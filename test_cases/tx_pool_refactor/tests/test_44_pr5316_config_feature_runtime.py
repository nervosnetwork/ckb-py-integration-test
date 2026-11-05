"""PR #5316: default-binary startup and removed budget-field checks."""

import socket
import subprocess
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
    """
    1. 读取构建脚本，确认生产配置解析目标先于测试目标。
    2. 核对各平台 CI 工作流的构建规则。
    """

    # 1. 读取构建脚本，确认生产配置解析目标先于测试目标。
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

    # 2. 核对各平台 CI 工作流的构建规则。
    for platform in ("ubuntu", "macos", "windows"):
        workflow = (
            source / f".github/workflows/ci_unit_tests_{platform}.yaml"
        ).read_text()
        assert "run: make test" in workflow


class TestConfigFeatureRuntime(CkbTest):
    def teardown_method(self, method):
        # Pytest and optional build logs retain this run's output.
        pass

    def _startup(self, label, override, expected_ready):
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
            config_path = Path(node.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            if override:
                config["tx_pool"]["max_tx_verify_time_ms"] = 1
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
    def test_default_binary_starts_and_rejects_removed_budget_field(self):
        """
        1. 使用默认二进制与缺省配置启动，确认交易池就绪。
        2. 在相同二进制的独立节点加入旧预算字段，确认启动拒绝。
        """

        # 1. 使用默认二进制与缺省配置启动，确认交易池就绪。
        self._startup("default", False, True)

        # 2. 在相同二进制的独立节点加入旧预算字段，确认启动拒绝。
        self._startup("removed_budget", True, False)
        self.did_pass = True
