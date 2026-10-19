"""PR #5316: optional diagnostic failures do not overwrite files or stop CKB."""

import errno
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests.test_43_pr5316_feature_matrix import _source


ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / ".ai-test-agent/current/ckb-pr-5316-current"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestDiagnosticsStartup(CkbTest):
    def teardown_method(self, method):
        # Pytest retains process output; avoid the base shared report copier.
        pass

    @staticmethod
    def _feature_binary(feature):
        feature_name, rustflags = {
            "PROFILE": ("profiling", None),
            "TRACE": ("tokio-trace", "--cfg tokio_unstable"),
        }[feature]
        source = _source()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        directory = CACHE / f"bin-{feature.lower()}"
        binary = directory / "ckb"
        metadata_path = directory / "metadata.json"
        recorded = (
            json.loads(metadata_path.read_text()) if metadata_path.is_file() else None
        )
        if not (
            binary.is_file()
            and recorded
            == {
                "head": head,
                "feature": feature_name,
                "rustflags": rustflags,
                "sha256": _sha256(binary),
            }
        ):
            directory.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env.pop("RUSTFLAGS", None)
            if rustflags:
                env["RUSTFLAGS"] = rustflags
            env.update(
                CARGO_TARGET_DIR=str(CACHE / "target-production"),
                CARGO_INCREMENTAL="0",
                CARGO_PROFILE_DEV_DEBUG="0",
                CARGO_BUILD_JOBS="4",
                CARGO_NET_OFFLINE="true",
            )
            command = [
                "cargo",
                "build",
                "--locked",
                "--offline",
                "--package",
                "ckb",
                "--bin",
                "ckb",
                "--features",
                feature_name,
                "--message-format",
                "short",
            ]
            result = subprocess.run(
                command,
                cwd=source,
                env=env,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
            built = Path(env["CARGO_TARGET_DIR"]) / "debug/ckb"
            assert built.is_file() and os.access(built, os.X_OK), built
            candidate = directory / "ckb.new"
            shutil.copy2(built, candidate)
            candidate.replace(binary)
            metadata_path.write_text(
                json.dumps(
                    {
                        "head": head,
                        "feature": feature_name,
                        "rustflags": rustflags,
                        "sha256": _sha256(binary),
                    },
                    sort_keys=True,
                )
            )
            print(f"{feature} build: exit={result.returncode}")
        assert binary.is_file() and os.access(binary, os.X_OK), binary
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        assert head[:7] in version, version
        print(
            f"diagnostic binary: {version}; feature={feature}; sha256={_sha256(binary)}"
        )
        return binary.resolve()

    @contextmanager
    def _running_node(self, name, binary, extra_env):
        rpc_port, p2p_port = _unused_port(), _unused_port()
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_diagnostics/{name}_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        try:
            node.prepare()
            # The normal fixture supplies ckb-cli. Replace only this test
            # node's copied CKB with the requested feature build.
            shutil.copy2(binary, node.ckb_bin_path)
            log_path = Path(node.ckb_dir) / "diagnostic-startup.log"
            env = os.environ.copy()
            for key in (
                "TX_POOL_PROFILE_TRACE_PATH",
                "TOKIO_CONSOLE_BUFFER_CAPACITY",
                "TOKIO_CONSOLE_PUBLISH_INTERVAL",
                "TOKIO_CONSOLE_RECORD_PATH",
                "TOKIO_CONSOLE_BIND",
                "TOKIO_CONSOLE_RETENTION",
            ):
                env.pop(key, None)
            env.update(extra_env)
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    [
                        node.ckb_bin_path,
                        "run",
                        "-C",
                        node.ckb_dir,
                        "--indexer",
                        "--skip-spec-check",
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
                try:
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        assert process.poll() is None, (
                            f"node exited {process.returncode}: "
                            f"{log_path.read_text(errors='replace')[-2000:]}"
                        )
                        try:
                            info = node.getClient().call(
                                "tx_pool_info",
                                [],
                                try_count=1,
                                timeout=1,
                                verbose=False,
                            )
                        except Exception:
                            time.sleep(0.2)
                            continue
                        assert info["tip_number"] == "0x0"
                        yield log_path
                        assert process.poll() is None, (
                            f"node exited after startup: "
                            f"{log_path.read_text(errors='replace')[-2000:]}"
                        )
                        assert (
                            node.getClient().call(
                                "tx_pool_info",
                                [],
                                try_count=1,
                                timeout=1,
                                verbose=False,
                            )["tip_number"]
                            == "0x0"
                        )
                        return
                    raise AssertionError(
                        f"RPC did not start: {log_path.read_text(errors='replace')[-2000:]}"
                    )
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
        finally:
            node.clean()

    @staticmethod
    def _assert_log(log_path, *parts):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            output = log_path.read_text(errors="replace")
            match = next(
                (
                    line
                    for line in output.splitlines()
                    if all(part in line for part in parts)
                ),
                None,
            )
            if match is not None:
                print("diagnostic: " + match)
                return
            time.sleep(0.1)
        raise AssertionError(output[-2000:])

    # TEST-MAP: NTD-73
    def test_existing_profile_is_preserved_and_node_starts(self):
        binary = self._feature_binary("PROFILE")
        # Keep the sentinel inside this test's own temporary directory.
        with tempfile.TemporaryDirectory(prefix="pr5316-profile-") as directory:
            target = Path(directory) / "existing.trace"
            sentinel = b"existing-profile-must-remain"
            target.write_bytes(sentinel)
            with self._running_node(
                "profile_exists",
                binary,
                {"TX_POOL_PROFILE_TRACE_PATH": str(target)},
            ) as log_path:
                self._assert_log(
                    log_path,
                    "optional profiling initialization failed",
                    os.strerror(errno.EEXIST),
                )
                assert target.read_bytes() == sentinel
        self.did_pass = True

    # TEST-MAP: NTD-73
    def test_zero_console_buffer_and_publish_interval_are_diagnosed(self):
        binary = self._feature_binary("TRACE")
        for label, variable, value in (
            ("zero_buffer", "TOKIO_CONSOLE_BUFFER_CAPACITY", "0"),
            ("zero_interval", "TOKIO_CONSOLE_PUBLISH_INTERVAL", "0s"),
        ):
            env = {
                variable: value,
                "TOKIO_CONSOLE_BIND": f"127.0.0.1:{_unused_port()}",
            }
            with self._running_node(label, binary, env) as log_path:
                self._assert_log(
                    log_path,
                    "optional profiling initialization failed",
                    variable,
                    "must be greater than zero",
                )
        self.did_pass = True

    # TEST-MAP: NTD-73
    def test_console_record_path_is_rejected_without_creating_file(self):
        binary = self._feature_binary("TRACE")
        with tempfile.TemporaryDirectory(prefix="pr5316-console-") as directory:
            target = Path(directory) / "record.trace"
            env = {
                "TOKIO_CONSOLE_RECORD_PATH": str(target),
                "TOKIO_CONSOLE_BIND": f"127.0.0.1:{_unused_port()}",
            }
            with self._running_node("record_path", binary, env) as log_path:
                self._assert_log(
                    log_path,
                    "optional profiling initialization failed",
                    "TOKIO_CONSOLE_RECORD_PATH is disabled",
                )
                assert not target.exists()
        self.did_pass = True
