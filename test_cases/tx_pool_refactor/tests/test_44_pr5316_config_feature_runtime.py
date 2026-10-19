"""PR #5316: Python startup checks for production/test configuration isolation."""

import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests.test_43_pr5316_feature_matrix import _source


def _two_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return first.getsockname()[1], second.getsockname()[1]


# TEST-MAP: NTD-72
def test_unit_ci_runs_production_parser_before_feature_unified_workspace():
    source = _source()
    makefile = (source / "Makefile").read_text()
    assert "test: test-production-config" in makefile
    assert "quick-test: test-production-config" in makefile
    production_rule = makefile.split("\ntest-production-config:", 1)[1].split(
        "\n.PHONY:", 1
    )[0]
    assert "cargo nextest run" in production_rule
    assert "-p ckb-app-config --lib --no-default-features" in production_rule
    assert "--features" not in production_rule
    for platform in ("ubuntu", "macos", "windows"):
        workflow = (
            source / f".github/workflows/ci_unit_tests_{platform}.yaml"
        ).read_text()
        assert "run: make test" in workflow


class TestConfigFeatureRuntime(CkbTest):
    def teardown_method(self, method):
        # Pytest and optional build logs retain this run's output.
        pass

    @staticmethod
    def _build(source, target, features, label, log_dir):
        command = [
            "cargo",
            "build",
            "--locked",
            "--offline",
            "--package",
            "ckb",
            "--bin",
            "ckb",
            "--message-format",
            "short",
        ]
        if features:
            command += ["--features", features]
        env = os.environ.copy()
        env.pop("RUSTFLAGS", None)
        env.update(
            CARGO_TARGET_DIR=str(target),
            CARGO_INCREMENTAL="0",
            CARGO_PROFILE_DEV_DEBUG="0",
            CARGO_BUILD_JOBS="4",
            CARGO_NET_OFFLINE="true",
        )
        result = subprocess.run(
            command,
            cwd=source,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        output = result.stdout + result.stderr
        if log_dir:
            Path(log_dir, f"{label}-build.log").write_text(output)
        assert result.returncode == 0, f"{label} build: {output[-4000:]}"
        binary = target / "debug/ckb"
        assert binary.is_file() and os.access(binary, os.X_OK)
        print(f"{label} build: exit={result.returncode}")
        return binary

    def _startup(self, binary, label, override, expected_ready, log_dir):
        rpc_port, p2p_port = _two_ports()
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_feature_config/{label}_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        process = None
        try:
            node.prepare()
            shutil.copy2(binary, node.ckb_bin_path)
            config_path = Path(node.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            if override:
                config["tx_pool"]["max_tx_verify_time_ms"] = 12_000
            else:
                assert "max_tx_verify_time_ms" not in config["tx_pool"]
            config_path.write_text(toml.dumps(config))
            path = Path(node.ckb_dir) / "feature-config-startup.log"
            with path.open("w") as stream:
                process = subprocess.Popen(
                    [
                        node.ckb_bin_path,
                        "run",
                        "-C",
                        node.ckb_dir,
                        "--indexer",
                        "--skip-spec-check",
                    ],
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
                deadline = time.monotonic() + 30
                ready = False
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
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
                    ready = info["tip_number"] == "0x0"
                    if ready:
                        break
                output = path.read_text(errors="replace")
                if log_dir:
                    Path(log_dir, f"{label}-startup.log").write_text(output)
                if expected_ready:
                    assert ready and process.poll() is None, output[-2500:]
                    print(f"{label} startup: ready=True; override={override}")
                else:
                    assert process.poll() is not None and not ready, output[-2500:]
                    assert (
                        "unknown field" in output and "max_tx_verify_time_ms" in output
                    ), output[-2500:]
                    print(
                        f"{label} startup: exit={process.returncode}; unknown override rejected"
                    )
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            node.clean()

    # TEST-MAP: NTD-72
    def test_fixed_head_production_rejects_test_budget_but_test_build_starts(self):
        source = _source()
        workflow = (
            source / ".github/workflows/ci_integration_tests_ubuntu.yaml"
        ).read_text()
        makefile = (source / "Makefile").read_text()
        assert '--release --features "test,deadlock_detection"' in workflow
        assert "integration-release: submodule-init" in makefile
        assert '--release --features "test,deadlock_detection"' in makefile
        assert "test-production-config:" in makefile
        assert "-p ckb-app-config --lib --no-default-features" in makefile

        log_dir = os.environ.get("CKB_PR5316_CONFIG_LOG_DIR")
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pr5316-config-feature-") as directory:
            target = Path(directory) / "target"
            production = self._build(source, target, "", "production", log_dir)
            self._startup(production, "production_default", False, True, log_dir)
            self._startup(production, "production_reject", True, False, log_dir)
            integration = self._build(source, target, "test", "test", log_dir)
            self._startup(integration, "test_override", True, True, log_dir)
        self.did_pass = True
