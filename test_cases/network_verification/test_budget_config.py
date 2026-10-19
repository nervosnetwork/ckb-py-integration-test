"""Production and test-feature network budget configuration boundaries."""

import socket
import subprocess
import time
from pathlib import Path

import toml
import pytest

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.network_verification.runtime import (
    PRODUCTION_BIN_DIR,
    requires_test_feature,
)


@pytest.mark.network_verification
@requires_test_feature
class TestTxPoolConfigCompatibility(CkbTest):

    # TEST-MAP: VT2-32
    # TEST-EVIDENCE-BEGIN: VT2-32
    # Evidence | Case: VT2-32 (covered)
    # Evidence | Setup: Use PRODUCTION_BIN_DIR (default project-relative download/production) with its production CKB/CLI; normal configuration has no budget fields. No manifest or executable-hash comparison is performed. Preparation, process launch, RPC readiness and try/finally cleanup are now explicit in this test method; no custom contextmanager is involved.
    # Evidence | Trigger: Start ordinary config and query tx_pool_info; separately run production with each of three internal fields.
    # Evidence | Observe: Baseline RPC tip0 available; each field causes nonzero process exit and exact unknown-field diagnostic.
    # Evidence | Assert: assert info["tip_number"] == "0x0"
    # Evidence | Assert: raise AssertionError(
    # Evidence |                             "node did not expose tx_pool_info within 20 seconds"
    # Evidence |                         )
    # Evidence | Assert: assert field not in config["tx_pool"]
    # Evidence | Assert: assert result.returncode != 0, f"{field} unexpectedly started node"
    # Evidence | Assert: assert f"unknown field `{field}`" in output, output[-1500:]
    # Evidence | Limit: Fields loop within one method. Binary hash/distinctness/preflight and source-revision attestation are no longer suite checks; caller supplies the two builds and their provenance. Actual production/test configuration behavior remains asserted.
    # Evidence | Limit: Static coverage only; this readability review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-32
    def test_production_rejects_test_only_budget_fields(self):
        production = PRODUCTION_BIN_DIR
        # 1. 准备生产构建的默认配置；每个配置场景独立使用节点目录。
        rpc_port, p2p_port = workload.unused_ports(2)
        base = self.CkbNodeConfigPath.CURRENT_TEST
        base = self.CkbNodeConfigPath(
            base.ckb_config_path,
            base.ckb_miner_config_path,
            base.ckb_spec_path,
            production,
        )
        node = self.CkbNode.init_dev_by_port(
            base,
            f"tx_pool/budget_config/production_defaults_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        version = subprocess.run(
            [node.ckb_bin_path, "--version"], text=True, capture_output=True, check=True
        ).stdout.strip()
        print(
            f"config case production_defaults: binary_dir={base.ckb_bin_path}, {version}"
        )
        path = Path(node.ckb_toml_path)
        config = toml.loads(path.read_text())
        path.write_text(toml.dumps(config))
        log_path = Path(node.ckb_dir) / "config-run.log"
        try:
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise AssertionError(
                                f"node exited {process.returncode}: "
                                f"{log_path.read_text(errors='replace')[-1500:]}"
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
                            time.sleep(0.25)
                            continue
                        assert info["tip_number"] == "0x0"
                        break
                    else:
                        raise AssertionError(
                            "node did not expose tx_pool_info within 20 seconds"
                        )
                    # 2. 启动并等待 RPC 就绪后，验证生产默认配置不含测试字段。
                    for field in (
                        "max_tx_verify_time_ms",
                        "min_tx_verify_time_ms",
                        "cycles_per_ms",
                    ):
                        assert field not in config["tx_pool"]
                finally:
                    # 3. 无论断言是否通过，先结束进程，再关闭日志并清理目录。
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
        finally:
            node.clean()
        for field in (
            "max_tx_verify_time_ms",
            "min_tx_verify_time_ms",
            "cycles_per_ms",
        ):
            rpc_port, p2p_port = workload.unused_ports(2)
            base = self.CkbNodeConfigPath.CURRENT_TEST
            base = self.CkbNodeConfigPath(
                base.ckb_config_path,
                base.ckb_miner_config_path,
                base.ckb_spec_path,
                production,
            )
            node = self.CkbNode.init_dev_by_port(
                base,
                f"tx_pool/budget_config/invalid_{field}_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            node.prepare()
            version = subprocess.run(
                [node.ckb_bin_path, "--version"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            print(
                f"config case invalid_{field}: binary_dir={base.ckb_bin_path}, {version}"
            )
            path = Path(node.ckb_toml_path)
            config = toml.loads(path.read_text())
            config["tx_pool"].update({field: 1})
            path.write_text(toml.dumps(config))
            try:
                result = subprocess.run(
                    [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                output = result.stdout + result.stderr
                assert result.returncode != 0, f"{field} unexpectedly started node"
                assert f"unknown field `{field}`" in output, output[-1500:]
                print(f"production rejects {field}: exit={result.returncode}")
            finally:
                node.clean()
        self.did_pass = True

    # TEST-MAP: VT2-33
    # TEST-EVIDENCE-BEGIN: VT2-33
    # Evidence | Case: VT2-33 (covered)
    # Evidence | Setup: Existing CURRENT_TEST selects download/current test-feature CKB/CLI; separate node directories/ports for default,1,12000,0,-1,4294967296 plus two unexposed fields. Preparation, process launch, RPC readiness and try/finally cleanup are now explicit in this test method; no custom contextmanager is involved.
    # Evidence | Trigger: Actual startup for allowed values; bounded subprocess rejection for invalid values and fields.
    # Evidence | Observe: Allowed RPC readiness; invalid nonzero exits identify field/range and closed RPC port; unsupported fields report unknown-field.
    # Evidence | Assert: assert info["tip_number"] == "0x0"
    # Evidence | Assert: assert node.getClient().tx_pool_info()["pending"] == "0x0"
    # Evidence | Assert: assert result.returncode != 0, output
    # Evidence | Assert: assert "max_tx_verify_time_ms" in output, output
    # Evidence | Assert: assert any(
    # Evidence |                     word in output
    # Evidence |                     for word in ("invalid value", "out of range", "nonzero")
    # Evidence |                 ), output
    # Evidence | Assert: assert (
    # Evidence |                         sock.connect_ex(
    # Evidence |                             ("127.0.0.1", int(node.rpcUrl.rsplit(":", 1)[1]))
    # Evidence |                         )
    # Evidence |                         != 0
    # Evidence |                     )
    # Evidence | Assert: assert result.returncode != 0, f"{field} unexpectedly started node"
    # Evidence | Assert: assert f"unknown field `{field}`" in output, output[-1500:]
    # Evidence | Limit: Both selector methods must run; neither alone completes the Case.
    # Evidence | Limit: Static coverage only; this readability review does not establish current real-node per-Case outcomes.
    # Evidence | Limit: The budget-change control now uses the same verified cold process boundary as persistence/reorg tests.
    # TEST-EVIDENCE-END: VT2-33
    def test_budget_configuration_bounds(self):
        """Real startup matrix; test_budget_retry checks the 1ms/12000ms workload."""
        for label, fields in (
            ("default_budget", {}),
            ("minimum_budget", {"max_tx_verify_time_ms": 1}),
            ("large_budget", {"max_tx_verify_time_ms": 12_000}),
        ):
            # 每个允许值都重新准备节点，执行后立即停止和清理。
            rpc_port, p2p_port = workload.unused_ports(2)
            base = self.CkbNodeConfigPath.CURRENT_TEST
            node = self.CkbNode.init_dev_by_port(
                base, f"tx_pool/budget_config/{label}_{rpc_port}", rpc_port, p2p_port
            )
            node.prepare()
            version = subprocess.run(
                [node.ckb_bin_path, "--version"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            print(f"config case {label}: binary_dir={base.ckb_bin_path}, {version}")
            path = Path(node.ckb_toml_path)
            config = toml.loads(path.read_text())
            config["tx_pool"].update(fields)
            path.write_text(toml.dumps(config))
            log_path = Path(node.ckb_dir) / "config-run.log"
            try:
                with log_path.open("w") as log:
                    process = subprocess.Popen(
                        [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                        cwd=node.ckb_dir,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    try:
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            if process.poll() is not None:
                                raise AssertionError(
                                    f"node exited {process.returncode}: "
                                    f"{log_path.read_text(errors='replace')[-1500:]}"
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
                                time.sleep(0.25)
                                continue
                            assert info["tip_number"] == "0x0"
                            break
                        else:
                            raise AssertionError(
                                "node did not expose tx_pool_info within 20 seconds"
                            )
                        assert node.getClient().tx_pool_info()["pending"] == "0x0"
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=10)
            finally:
                node.clean()
        for value in (0, -1, 4_294_967_296):
            rpc_port, p2p_port = workload.unused_ports(2)
            base = self.CkbNodeConfigPath.CURRENT_TEST
            node = self.CkbNode.init_dev_by_port(
                base,
                f"tx_pool/budget_config/invalid_budget_{value}_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            node.prepare()
            version = subprocess.run(
                [node.ckb_bin_path, "--version"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            print(
                f"config case invalid_budget_{value}: binary_dir={base.ckb_bin_path}, {version}"
            )
            path = Path(node.ckb_toml_path)
            config = toml.loads(path.read_text())
            config["tx_pool"].update({"max_tx_verify_time_ms": value})
            path.write_text(toml.dumps(config))
            try:
                result = subprocess.run(
                    [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    text=True,
                    capture_output=True,
                    timeout=15,
                )
                output = result.stdout + result.stderr
                assert result.returncode != 0, output
                assert "max_tx_verify_time_ms" in output, output
                assert any(
                    word in output
                    for word in ("invalid value", "out of range", "nonzero")
                ), output
                with socket.socket() as sock:
                    sock.settimeout(1)
                    assert (
                        sock.connect_ex(
                            ("127.0.0.1", int(node.rpcUrl.rsplit(":", 1)[1]))
                        )
                        != 0
                    )
            finally:
                node.clean()
        for field in ("min_tx_verify_time_ms", "cycles_per_ms"):
            rpc_port, p2p_port = workload.unused_ports(2)
            base = self.CkbNodeConfigPath.CURRENT_TEST
            base = self.CkbNodeConfigPath(
                base.ckb_config_path,
                base.ckb_miner_config_path,
                base.ckb_spec_path,
                self.CkbNodeConfigPath.CURRENT_TEST.ckb_bin_path,
            )
            node = self.CkbNode.init_dev_by_port(
                base,
                f"tx_pool/budget_config/invalid_{field}_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            node.prepare()
            version = subprocess.run(
                [node.ckb_bin_path, "--version"],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            print(
                f"config case invalid_{field}: binary_dir={base.ckb_bin_path}, {version}"
            )
            path = Path(node.ckb_toml_path)
            config = toml.loads(path.read_text())
            config["tx_pool"].update({field: 1})
            path.write_text(toml.dumps(config))
            try:
                result = subprocess.run(
                    [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    text=True,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                output = result.stdout + result.stderr
                assert result.returncode != 0, f"{field} unexpectedly started node"
                assert f"unknown field `{field}`" in output, output[-1500:]
                print(f"production rejects {field}: exit={result.returncode}")
            finally:
                node.clean()
        self.did_pass = True
