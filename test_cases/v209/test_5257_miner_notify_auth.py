import http.client
import json
import queue
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from framework.basic import CkbTest


AUTH_TOKEN = "miner-notify-secret-5257"
REQUEST_TIMEOUT_SECONDS = 5
STARTUP_TIMEOUT_SECONDS = 15


class _NotifyRecorderHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        self.server.records.put(
            {
                "method": self.command,
                "path": self.path,
                "headers": {
                    name.lower(): value for name, value in self.headers.items()
                },
                "body": body,
            }
        )
        self.send_response(http.client.OK)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format, *_args):
        pass


class _NotifyRecorderServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address):
        super().__init__(server_address, _NotifyRecorderHandler)
        self.records = queue.Queue()


def _unused_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until(description, check, timeout=STARTUP_TIMEOUT_SECONDS):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except AssertionError:
            raise
        except Exception as error:
            last_error = error
        time.sleep(0.1)
    raise AssertionError(
        f"timed out waiting for {description}; last error: {last_error!r}"
    )


def _stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class TestV209MinerNotifyAuth(CkbTest):
    """Binary-level regression coverage for CKB PR #5257."""

    @classmethod
    def setup_class(cls):
        cls.node = None
        cls.node_process = None
        cls.miner_process = None
        cls.node_log = None
        cls.miner_log = None
        cls.recorder = _NotifyRecorderServer(("127.0.0.1", 0))
        cls.recorder_thread = threading.Thread(
            target=cls.recorder.serve_forever,
            name="miner-notify-recorder",
            daemon=True,
        )
        cls.recorder_thread.start()

        rpc_port = _unused_tcp_port()
        p2p_port = _unused_tcp_port()
        cls.miner_port = _unused_tcp_port()
        recorder_port = cls.recorder.server_address[1]

        try:
            cls.node = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.v209,
                "node/miner_notify_auth",
                rpc_port,
                p2p_port,
            )
            cls.node.prepare(
                other_ckb_config={
                    "ckb_block_assembler_notify": [
                        f"http://127.0.0.1:{recorder_port}/block-template",
                        f"http://127.0.0.1:{cls.miner_port}/",
                    ],
                    "ckb_block_assembler_notify_timeout_millis": 2_000,
                    "ckb_block_assembler_notify_auth_token": AUTH_TOKEN,
                },
                other_ckb_miner_config={
                    "ckb_miner_listen": f"127.0.0.1:{cls.miner_port}",
                    "ckb_miner_auth_token": AUTH_TOKEN,
                    "ckb_miner_block_on_submit": "false",
                },
            )

            cls.miner_log = open(cls.node.ckb_dir + "/miner-notify.log", "w")
            cls.miner_process = subprocess.Popen(
                [cls.node.ckb_bin_path, "miner", "-C", cls.node.ckb_dir],
                stdout=cls.miner_log,
                stderr=subprocess.STDOUT,
            )
            _wait_until("ckb-miner notify listener", cls._miner_is_ready)

            cls.node_log = open(cls.node.ckb_dir + "/node-notify.log", "w")
            cls.node_process = subprocess.Popen(
                [
                    cls.node.ckb_bin_path,
                    "run",
                    "-C",
                    cls.node.ckb_dir,
                    "--indexer",
                    "--skip-spec-check",
                ],
                stdout=cls.node_log,
                stderr=subprocess.STDOUT,
            )
            _wait_until("CKB RPC", cls._node_rpc_is_ready)
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        _stop_process(getattr(cls, "node_process", None))
        _stop_process(getattr(cls, "miner_process", None))

        for log_file in (
            getattr(cls, "node_log", None),
            getattr(cls, "miner_log", None),
        ):
            if log_file is not None:
                log_file.close()

        recorder = getattr(cls, "recorder", None)
        if recorder is not None:
            recorder.shutdown()
            recorder.server_close()
        recorder_thread = getattr(cls, "recorder_thread", None)
        if recorder_thread is not None:
            recorder_thread.join(timeout=5)

        node = getattr(cls, "node", None)
        if node is not None:
            node.clean()

    @classmethod
    def _node_rpc_is_ready(cls):
        if cls.node_process.poll() is not None:
            raise AssertionError(
                f"CKB exited during startup with code {cls.node_process.returncode}"
            )
        return cls.node.getClient().get_tip_header()

    @classmethod
    def _miner_is_ready(cls):
        if cls.miner_process.poll() is not None:
            raise AssertionError(
                "ckb-miner exited during startup with code "
                f"{cls.miner_process.returncode}"
            )
        try:
            status, headers, _body = cls._request_miner("GET")
        except OSError:
            return False
        return (
            status == http.client.METHOD_NOT_ALLOWED and headers.get("allow") == "POST"
        )

    @classmethod
    def _request_miner(cls, method, body=b"", authorization=None):
        headers = {"Content-Type": "application/json"}
        if authorization is not None:
            headers["Authorization"] = authorization
        connection = http.client.HTTPConnection(
            "127.0.0.1", cls.miner_port, timeout=REQUEST_TIMEOUT_SECONDS
        )
        try:
            connection.request(method, "/", body=body, headers=headers)
            response = connection.getresponse()
            response_headers = {
                name.lower(): value for name, value in response.getheaders()
            }
            return response.status, response_headers, response.read()
        finally:
            connection.close()

    def test_node_notify_sends_bearer_token(self):
        notify_path = "/block-template"

        def next_notify():
            while True:
                try:
                    record = self.recorder.records.get_nowait()
                except queue.Empty:
                    self.node.getClient().get_block_template()
                    return None
                if record["path"] == notify_path:
                    return record

        record = _wait_until("block-template notify request", next_notify)

        assert record["method"] == "POST"
        assert record["path"] == notify_path
        assert record["headers"]["authorization"] == f"Bearer {AUTH_TOKEN}"
        assert record["headers"]["content-type"] == "application/json"

        block_template = json.loads(record["body"])
        assert block_template["work_id"].startswith("0x")
        assert block_template["parent_hash"].startswith("0x")
        assert "cellbase" in block_template

    def test_node_notify_omits_auth_when_token_is_unconfigured(self):
        rpc_port = _unused_tcp_port()
        unsecured_node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.v209,
            f"node/unsecured_notify_{rpc_port}",
            rpc_port,
            _unused_tcp_port(),
        )
        recorder_port = self.recorder.server_address[1]
        notify_path = "/unsecured-block-template"
        unsecured_node.prepare(
            other_ckb_config={
                "ckb_block_assembler_notify": [
                    f"http://127.0.0.1:{recorder_port}{notify_path}"
                ],
                "ckb_block_assembler_notify_timeout_millis": 2_000,
            }
        )
        node_log = open(unsecured_node.ckb_dir + "/node-notify.log", "w")
        node_process = subprocess.Popen(
            [
                unsecured_node.ckb_bin_path,
                "run",
                "-C",
                unsecured_node.ckb_dir,
                "--skip-spec-check",
            ],
            stdout=node_log,
            stderr=subprocess.STDOUT,
        )

        def node_is_ready():
            if node_process.poll() is not None:
                raise AssertionError(
                    f"unsecured CKB exited with code {node_process.returncode}"
                )
            return unsecured_node.getClient().get_tip_header()

        def next_unsecured_notify():
            while True:
                try:
                    record = self.recorder.records.get_nowait()
                except queue.Empty:
                    return None
                if record["path"] == notify_path:
                    return record

        try:
            _wait_until("unsecured CKB RPC", node_is_ready)
            self.Miner.miner_with_version(unsecured_node, "0x0")
            record = _wait_until(
                "unauthenticated block-template notify", next_unsecured_notify
            )
        finally:
            _stop_process(node_process)
            node_log.close()
            unsecured_node.clean()

        assert "authorization" not in record["headers"]

    def test_miner_notify_rejects_missing_and_wrong_token(self):
        block_template = json.dumps(self.node.getClient().get_block_template()).encode()

        for authorization in (None, "Bearer wrong-secret"):
            status, headers, _body = self._request_miner(
                "POST", block_template, authorization
            )
            assert status == http.client.UNAUTHORIZED
            assert headers["www-authenticate"] == "Bearer"

    def test_miner_notify_rejects_non_post_method(self):
        status, headers, _body = self._request_miner(
            "GET", authorization=f"Bearer {AUTH_TOKEN}"
        )

        assert status == http.client.METHOD_NOT_ALLOWED
        assert headers["allow"] == "POST"

    def test_miner_notify_accepts_matching_token(self):
        block_template = json.dumps(self.node.getClient().get_block_template()).encode()
        status, _headers, _body = self._request_miner(
            "POST", block_template, f"Bearer {AUTH_TOKEN}"
        )

        assert status == http.client.OK

    def test_rejects_invalid_token_at_startup(self):
        cases = [
            (
                "ckb_miner_auth_token",
                "miner",
                "",
                "miner.client.auth_token must be non-empty",
            ),
            (
                "ckb_miner_auth_token",
                "miner",
                " token-with-leading-space",
                "miner.client.auth_token must be non-empty",
            ),
            (
                "ckb_block_assembler_notify_auth_token",
                "run",
                "token-with-trailing-space ",
                "block_assembler.notify_auth_token must be non-empty",
            ),
        ]
        for config_key, command, invalid_token, expected_message in cases:
            with self.subTest(config_key=config_key, invalid_token=invalid_token):
                self._assert_invalid_token_is_rejected(
                    config_key, command, invalid_token, expected_message
                )

    def _assert_invalid_token_is_rejected(
        self, config_key, command, invalid_token, expected_message
    ):
        rpc_port = _unused_tcp_port()
        p2p_port = _unused_tcp_port()
        invalid_node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.v209,
            f"node/invalid_{command}_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        miner_config = {}
        node_config = {}
        if config_key.startswith("ckb_miner_"):
            miner_config[config_key] = invalid_token
            miner_config["ckb_miner_listen"] = f"127.0.0.1:{_unused_tcp_port()}"
        else:
            node_config[config_key] = invalid_token

        try:
            invalid_node.prepare(
                other_ckb_config=node_config,
                other_ckb_miner_config=miner_config,
            )
            result = subprocess.run(
                [invalid_node.ckb_bin_path, command, "-C", invalid_node.ckb_dir],
                capture_output=True,
                text=True,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        finally:
            invalid_node.clean()

        assert result.returncode != 0
        assert expected_message in result.stderr
