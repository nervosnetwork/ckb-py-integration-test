"""Production and test-feature network budget configuration boundaries."""

import socket
import subprocess
import time
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload


class TestTxPoolConfigCompatibility(CkbTest):

    # TEST-MAP: VT2-32
    # TEST-EVIDENCE-BEGIN: VT2-32
    # Evidence | Case: VT2-32 (covered)
    # Evidence | Setup: The caller configures CURRENT_TEST with a production build before running this method. Use its existing template in a fresh directory with unused RPC/P2P ports; assert all three internal fields are absent from ordinary tx_pool config. For normal starts, node.start() owns version reporting, background launch and node.log. The test begins its retained 20-second RPC-readiness deadline after start() returns. Finally node.stop() then node.clean() use the framework lifecycle.
    # Evidence | Trigger: Start ordinary config with node.start() and query tx_pool_info; independently inject each of max_tx_verify_time_ms, min_tx_verify_time_ms and cycles_per_ms and run the unchanged subprocess.run rejection path with timeout=10.
    # Evidence | Observe: Ordinary RPC returns genesis tip 0x0. Each invalid production config exits nonzero with its exact unknown-field diagnostic; this proves rejected-process startup, not a separately sampled RPC-port closure.
    # Evidence | Assert: assert info["tip_number"] == "0x0"
    # Evidence | Assert: raise AssertionError(
    # Evidence |                     "node did not expose tx_pool_info within 20 seconds"
    # Evidence |                 )
    # Evidence | Assert: assert field not in config["tx_pool"]
    # Evidence | Assert: assert result.returncode != 0, f"{field} unexpectedly started node"
    # Evidence | Assert: assert f"unknown field `{field}`" in output, output[-1500:]
    # Evidence | Limit: This changes failure handling: custom process.poll(), early exit/log-tail errors, terminate()/wait(10)/kill()/wait(10), and config-run.log are removed. Native start sleeps 1 second and 3 seconds plus run_command's 1-second background-launch sleep; native stop kills the RPC-port listener and sleeps 3 seconds without a process-exit assertion, then clean removes the node directory. The readiness deadline excludes start(). A stop exception can prevent the subsequent clean call; shutdown equivalence is not claimed.
    # Evidence | Limit: Production/test build provenance and binary distinctness are caller inputs, not test assertions. This review assumes DOCKER is unset and the supplied build directories contain the intended local binaries. Unknown-field rows assert exit/diagnostic, not an additional RPC socket check. Fields are loop iterations, not separately parameterized selectors.
    # Evidence | Limit: Static semantic review plus three-selector collection only; no real-node execution or per-Case pass result was produced by this review. Reviewer isolation is isolation_unverified.
    # TEST-EVIDENCE-END: VT2-32
    def test_production_rejects_test_only_budget_fields(self):
        # 1. 准备生产构建的默认配置；每个配置场景独立使用节点目录。
        rpc_port, p2p_port = workload.unused_ports(2)
        base = self.CkbNodeConfigPath.CURRENT_TEST
        node = self.CkbNode.init_dev_by_port(
            base,
            f"tx_pool/budget_config/production_defaults_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        path = Path(node.ckb_toml_path)
        config = toml.loads(path.read_text())
        path.write_text(toml.dumps(config))
        try:
            node.start()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
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
            # 默认配置不含测试专用字段。
            for field in (
                "max_tx_verify_time_ms",
                "min_tx_verify_time_ms",
                "cycles_per_ms",
            ):
                assert field not in config["tx_pool"]
        finally:
            node.stop()
            node.clean()
        for field in (
            "max_tx_verify_time_ms",
            "min_tx_verify_time_ms",
            "cycles_per_ms",
        ):
            rpc_port, p2p_port = workload.unused_ports(2)
            base = self.CkbNodeConfigPath.CURRENT_TEST
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
    # Evidence | Setup: The caller configures CURRENT_TEST with a test-feature build before running this method. Use fresh directories/ports for default config and max_tx_verify_time_ms=1/12000. For normal starts, node.start() owns version reporting, background launch and node.log. The test begins its retained 20-second RPC-readiness deadline after start() returns. Finally node.stop() then node.clean() use the framework lifecycle.
    # Evidence | Trigger: Start each allowed configuration via node.start(); preserve subprocess.run for 0, -1, 4294967296 (15-second timeout) and unsupported min_tx_verify_time_ms/cycles_per_ms (10-second timeout).
    # Evidence | Observe: Allowed values expose tx_pool_info at genesis and an empty pending pool. Invalid values exit nonzero, identify max_tx_verify_time_ms and range/nonzero failure, and leave the RPC socket closed. Unsupported fields exit nonzero with the exact unknown-field diagnostic.
    # Evidence | Assert: assert info["tip_number"] == "0x0"
    # Evidence | Assert: raise AssertionError(
    # Evidence |                         "node did not expose tx_pool_info within 20 seconds"
    # Evidence |                     )
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
    # Evidence | Limit: This changes failure handling: custom process.poll(), early exit/log-tail errors, terminate()/wait(10)/kill()/wait(10), and config-run.log are removed. Native start sleeps 1 second and 3 seconds plus run_command's 1-second background-launch sleep; native stop kills the RPC-port listener and sleeps 3 seconds without a process-exit assertion, then clean removes the node directory. The readiness deadline excludes start(). A stop exception can prevent the subsequent clean call; shutdown equivalence is not claimed.
    # Evidence | Limit: Both configuration and retry selectors jointly establish this Case; neither alone covers it. Loop values are not individual pytest parameter selectors. The unchanged retry binding retains its owned-PID cold-process verification; that verification is not present in normal configuration startup/cleanup.
    # Evidence | Limit: This finite workload demonstrates the 1ms rejection/12000ms acceptance contrast; it does not measure the exact CPU boundary, all positive u32 values, or the 8000ms default. Test-feature binary provenance remains a caller input, and DOCKER must be unset for the reviewed local-build path. The low-cap helper samples state after the matching refusal log; it does not establish indefinite later state.
    # Evidence | Limit: Static semantic review plus three-selector collection only; no real-node execution or per-Case pass result was produced by this review. Reviewer isolation is isolation_unverified.
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
            path = Path(node.ckb_toml_path)
            config = toml.loads(path.read_text())
            config["tx_pool"].update(fields)
            path.write_text(toml.dumps(config))
            try:
                node.start()
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
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
                node.stop()
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
