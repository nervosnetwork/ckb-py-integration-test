"""PR #5316: bound miner notifications and release their owned children."""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import toml

from framework.basic import CkbTest


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for(label, predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {label}")


class _HoldingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        record = json.loads(body)
        record["closed_by_client"] = False
        with self.server.lock:
            self.server.active += 1
            self.server.maximum_active = max(
                self.server.maximum_active, self.server.active
            )
            self.server.received.append(record)
        try:
            # Detect cancellation without replying; this observes the HTTP
            # client's socket, not just the server's own handler lifetime.
            self.connection.settimeout(0.1)
            while not self.server.release.is_set():
                try:
                    if not self.connection.recv(1, socket.MSG_PEEK):
                        record["closed_by_client"] = True
                        return
                except TimeoutError:
                    pass
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            record["closed_by_client"] = True
        finally:
            with self.server.lock:
                self.server.active -= 1

    def log_message(self, _format, *_args):
        pass


class _HoldingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _HoldingHandler)
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.received = []
        self.active = 0
        self.maximum_active = 0

    def snapshot(self):
        with self.lock:
            return (
                [dict(record) for record in self.received],
                self.active,
                self.maximum_active,
            )


def _child_records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _child_pids(path):
    return [record["pid"] for record in _child_records(path)]


def _child_alive(pid, script):
    process = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat=", "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    parts = process.stdout.strip().split(None, 1)
    return (
        process.returncode == 0
        and len(parts) == 2
        and not parts[0].startswith("Z")
        and str(script) in parts[1]
    )


class TestTemplateNotifyLifecycle(CkbTest):
    def teardown_method(self, method):
        # The base copier uses a shared report path; pytest retains this log.
        pass

    # TEST-MAP: NTD-60
    def test_timeout_and_shutdown_release_bounded_notifications(self):
        server = _HoldingServer()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        node = None
        process = None
        node_log = None
        script = None
        child_log = None
        try:
            rpc_port, p2p_port = _unused_port(), _unused_port()
            node = self.CkbNode.init_dev_by_port(
                self.CkbNodeConfigPath.CURRENT_TEST,
                f"tx_pool/pr5316_template_notify/{rpc_port}",
                rpc_port,
                p2p_port,
            )
            node.prepare(
                other_ckb_config={
                    "ckb_block_assembler_notify": [
                        f"http://127.0.0.1:{server.server_address[1]}/template"
                    ],
                    "ckb_block_assembler_notify_timeout_millis": 15_000,
                }
            )
            script = Path(node.ckb_dir) / "hang_notify.py"
            child_log = Path(node.ckb_dir) / "notify-children.jsonl"
            script.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys, time\n"
                f"with open({str(child_log)!r}, 'a') as output:\n"
                "    output.write(json.dumps({'pid': os.getpid(), "
                "'number': json.loads(sys.argv[1])['number']}) + '\\n')\n"
                "    output.flush()\n"
                "while True: time.sleep(60)\n"
            )
            script.chmod(0o755)
            config_path = Path(node.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["block_assembler"]["notify_scripts"] = [str(script)]
            config_path.write_text(toml.dumps(config))

            node_log = open(Path(node.ckb_dir) / "node-notify.log", "w")
            process = subprocess.Popen(
                [
                    node.ckb_bin_path,
                    "run",
                    "-C",
                    node.ckb_dir,
                    "--indexer",
                    "--skip-spec-check",
                ],
                stdout=node_log,
                stderr=subprocess.STDOUT,
            )

            def ready():
                assert process.poll() is None, f"CKB exited: {process.returncode}"
                try:
                    return node.getClient().get_tip_header()
                except Exception:
                    return None

            _wait_for("CKB RPC", ready)
            client = node.getClient()
            first_template = client.get_block_template()
            _wait_for("first HTTP notify", lambda: server.snapshot()[0])
            first_pid = _wait_for(
                "first script notify", lambda: _child_pids(child_log)
            )[0]
            first_record = server.snapshot()[0][0]
            first_number = first_record["number"]
            for field in ("number", "parent_hash", "work_id"):
                assert first_record[field] == first_template[field]
            assert _child_records(child_log)[0]["number"] == first_number
            assert _child_alive(first_pid, script)

            # Multiple changed-tip publications must not create another batch
            # while the first HTTP/script pair still holds the notification lane.
            started = time.monotonic()
            for _ in range(3):
                self.Miner.miner_with_version(node, "0x0")
            assert time.monotonic() - started < 14, "updates exceeded notify timeout"
            current_template = client.get_block_template()
            current_number = current_template["number"]
            records, active, maximum = server.snapshot()
            assert len(records) == 1 and active == 1 and maximum == 1
            assert _child_pids(child_log) == [first_pid]

            _wait_for(
                "timed-out script exit",
                lambda: not _child_alive(first_pid, script),
                seconds=20,
            )
            _wait_for(
                "timed-out HTTP request cancellation",
                lambda: server.snapshot()[0][0]["closed_by_client"],
            )
            timeout_log = (Path(node.ckb_dir) / "node-notify.log").read_text()
            assert "timed out" in timeout_log and str(script) in timeout_log
            _wait_for(
                "current template notification",
                lambda: any(
                    record["number"] == current_number
                    for record in server.snapshot()[0]
                ),
            )
            latest_record = next(
                record
                for record in server.snapshot()[0]
                if record["number"] == current_number
            )
            for field in ("number", "parent_hash", "work_id"):
                assert latest_record[field] == current_template[field]
            second_pid = _wait_for(
                "next script notification",
                lambda: _child_pids(child_log)[1:] or None,
            )[0]
            assert _child_alive(second_pid, script)
            assert _child_records(child_log)[1]["number"] == current_number
            assert server.snapshot()[1] == 1  # HTTP request still in flight.

            process.terminate()
            process.wait(timeout=40)
            _wait_for(
                "shutdown script exit",
                lambda: not _child_alive(second_pid, script),
                seconds=5,
            )
            _wait_for(
                "shutdown HTTP cancellation",
                lambda: all(
                    record["closed_by_client"] for record in server.snapshot()[0]
                ),
            )
            _wait_for("HTTP handlers drained", lambda: server.snapshot()[1] == 0)
            count = len(server.snapshot()[0])
            time.sleep(0.3)
            assert len(server.snapshot()[0]) == count
            assert server.snapshot()[2] == 1
            assert all(not _child_alive(pid, script) for pid in _child_pids(child_log))
            print(
                f"notify: first={first_number}, latest={current_number}, "
                f"http_requests={count}, maximum_active={maximum}, "
                f"child_pids={_child_pids(child_log)}, shutdown_exit={process.returncode}"
            )
            self.did_pass = True
        finally:
            server.release.set()
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if script is not None and child_log is not None:
                for pid in _child_pids(child_log):
                    if _child_alive(pid, script):
                        os.kill(pid, signal.SIGKILL)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
            if node_log is not None:
                node_log.close()
            if node is not None:
                node.clean()
