import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import tempfile
import termios
import time
import fcntl

import pytest
from framework.basic import CkbTest
from framework.util import get_project_root


CKB_CLI_BIN = os.path.join(get_project_root(), "source", "ckb-cli")
TCP_PORT = 21914
MINER_LOCK_ARGS = "0x8883a512ee2383c01574a328f60eeccbb4d78240"
MINER_CODE_HASH = "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8"


class TuiProcess:
    def __init__(self, args):
        self.home = tempfile.mkdtemp(prefix="ckb-cli-tui-home-")
        self.master_fd, slave_fd = pty.openpty()
        window_size = struct.pack("HHHH", 40, 140, 0, 0)
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, window_size)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, window_size)
        env = os.environ.copy()
        env["CKB_CLI_HOME"] = self.home
        env["TERM"] = "xterm-256color"

        def _take_controlling_tty():
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen(
            [CKB_CLI_BIN, "--local-only", "tui"] + args,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            preexec_fn=_take_controlling_tty,
            close_fds=True,
        )
        os.close(slave_fd)

    def read_until(self, expected, timeout=10):
        deadline = time.time() + timeout
        output = ""
        while time.time() < deadline:
            readable, _, _ = select.select([self.master_fd], [], [], 0.2)
            if not readable:
                if self.proc.poll() is not None:
                    break
                continue
            try:
                chunk = os.read(self.master_fd, 8192).decode("utf-8", "ignore")
            except OSError:
                break
            output += chunk
            if expected in output:
                return output
        return output

    def write(self, value):
        os.write(self.master_fd, value.encode("utf-8"))

    def close(self):
        try:
            if self.proc.poll() is None:
                self.write("q")
                try:
                    self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            if self.proc.poll() is None:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                    self.proc.wait(timeout=5)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            shutil.rmtree(self.home, ignore_errors=True)


class TestV209CkbCliTuiIntegration(CkbTest):
    """
    Regression coverage for nervosnetwork/ckb-cli#683.

    The PR integrates ckb-tui into ckb-cli as `ckb-cli tui`. These cases run
    the integrated command through a real pseudo-TTY so the terminal UI path is
    exercised instead of only checking ordinary stdout/stderr commands.
    """

    @classmethod
    def setup_class(cls):
        if not os.path.isfile(CKB_CLI_BIN):
            pytest.skip("source/ckb-cli not found; run develop_prepare first")

        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.v209,
            "v209/ckb_cli_tui/node",
            21912,
            21913,
        )
        cls.node.prepare(
            other_ckb_config={
                "ckb_rpc_modules": [
                    "Net",
                    "Pool",
                    "Miner",
                    "Chain",
                    "Stats",
                    "Subscription",
                    "Experiment",
                    "Debug",
                    "IntegrationTest",
                    "Terminal",
                ],
                "ckb_tcp_listen_address": f"0.0.0.0:{TCP_PORT}",
            }
        )
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 12)

    @classmethod
    def teardown_class(cls):
        if hasattr(cls, "node"):
            cls.node.stop()
            cls.node.clean()

    def _run_cli(self, args):
        env = os.environ.copy()
        env["CKB_CLI_HOME"] = tempfile.mkdtemp(prefix="ckb-cli-tui-home-")
        try:
            return subprocess.run(
                [CKB_CLI_BIN, "--local-only", "tui"] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=15,
                env=env,
                check=False,
            )
        finally:
            shutil.rmtree(env["CKB_CLI_HOME"], ignore_errors=True)

    def test_tui_starts_with_rpc_and_tcp_urls(self):
        tui = TuiProcess(
            [
                "--rpc-url",
                self.node.getClient().url,
                "--tcp-url",
                f"127.0.0.1:{TCP_PORT}",
                "--refresh_interval",
                "100",
            ]
        )
        try:
            output = tui.read_until("CKB Node Monitor", timeout=10)
            assert "CKB Node Monitor" in output
            assert "Overview" in output

            tui.write("\t")
            output += tui.read_until("Blockchain", timeout=5)
            assert "Blockchain" in output
            assert tui.proc.poll() is None
            self.did_pass = True
        finally:
            tui.close()

    def test_tui_rejects_invalid_refresh_interval_without_panic(self):
        result = self._run_cli(["--refresh_interval", "abc"])

        assert result.returncode != 0
        assert "panicked at" not in result.stdout
        assert "invalid" in result.stdout.lower() or "error" in result.stdout.lower()
        self.did_pass = True

    def test_tui_rejects_missing_theme_file_without_panic(self):
        missing_theme_file = os.path.join(
            tempfile.gettempdir(), "missing-ckb-cli-tui-theme.toml"
        )
        result = self._run_cli(
            [
                "--rpc-url",
                self.node.getClient().url,
                "--theme-file",
                missing_theme_file,
            ]
        )

        assert result.returncode != 0
        assert "panicked at" not in result.stdout
        assert (
            "theme" in result.stdout.lower() or "no such file" in result.stdout.lower()
        )
        self.did_pass = True

    def test_live_cells_searcher_indexer_query_has_miner_cells(self):
        """
        Live Cells Searcher uses `get_cells` with lock args/code hash/hash type.
        With the node started through the framework's `--indexer` path, the
        same search key should return mined cells.
        """
        cells = self.node.getClient().get_cells(
            {
                "script": {
                    "code_hash": MINER_CODE_HASH,
                    "hash_type": "type",
                    "args": MINER_LOCK_ARGS,
                },
                "script_type": "lock",
                "group_by_transaction": False,
                "with_data": False,
            },
            "desc",
            "0x3",
            None,
        )

        assert cells is not None
        assert len(cells["objects"]) > 0
        first_cell = cells["objects"][0]
        assert first_cell["output"]["lock"]["args"] == MINER_LOCK_ARGS
        assert first_cell["output"]["lock"]["code_hash"] == MINER_CODE_HASH
        assert first_cell["output"]["lock"]["hash_type"] == "type"
        self.did_pass = True
