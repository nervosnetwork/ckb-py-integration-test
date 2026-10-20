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
    def test_production_rejects_test_only_budget_fields(self):
        """
        1. 使用未启用 test feature 的构建，以默认配置启动节点并检查 RPC 可用。
        2. 检查默认配置不包含测试专用字段，随后停止并清理节点。
        3. 逐项加入测试专用字段，检查启动失败且错误明确指出未知字段。
        """
        # 1. 准备生产构建的默认配置，启动节点并等待 tx_pool_info 可用。
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
            # 2. 默认配置不含测试专用字段；finally 中停止并清理节点。
            for field in (
                "max_tx_verify_time_ms",
                "min_tx_verify_time_ms",
                "cycles_per_ms",
            ):
                assert field not in config["tx_pool"]
        finally:
            node.stop()
            node.clean()

        # 3. 每个字段使用独立节点目录，检查非零退出码和对应的 unknown field。
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
    def test_budget_configuration_bounds(self):
        """
        1. 使用 test feature 构建，检查默认值、1ms 和 12000ms 配置均可启动。
        2. 检查 0、负数和超出 u32 范围的预算被拒绝，且 RPC 端口未监听。
        3. 检查不支持的 min_tx_verify_time_ms、cycles_per_ms 字段被拒绝。
        """
        # 1. 逐个启动允许的配置，检查创世高度和空交易池，再停止并清理。
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

        # 2. 非法预算应在启动时退出，并返回范围或非零值错误。
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

        # 3. 不支持的字段应返回对应的 unknown field，而不是启动节点。
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
