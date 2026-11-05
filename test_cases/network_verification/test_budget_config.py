"""Ordinary CKB binaries accept defaults and reject internal budget fields."""

import socket
import subprocess
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload


class TestTxPoolConfigCompatibility(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.nodes):
            node.stop()
            workload.save_node_logs(node)
            node.clean()

    # TEST-MAP: VT2-32
    # TEST-MAP: VT2-33
    def test_default_budget_rejects_removed_configuration_fields(self):
        """
        1. 普通二进制使用缺省配置启动，检查 RPC、空池及三个预算字段均未配置。
        2. 分别写入旧预算字段及原数值边界，检查均因 unknown field 非零退出。
        3. 检查每个失败配置的 RPC 端口未监听，避免把启动成功误判为配置拒绝。
        """
        # 1. 使用项目统一二进制；不通过版本判断跳过，也不设置预算环境变量。
        rpc_port, p2p_port = workload.unused_ports(2)
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"network_verification/budget_config/default_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        self.nodes.append(node)
        node.prepare()
        config = toml.loads(Path(node.ckb_toml_path).read_text())
        for field in (
            "max_tx_verify_time_ms",
            "min_tx_verify_time_ms",
            "cycles_per_ms",
        ):
            assert field not in config["tx_pool"]
        node.start()
        info = node.getClient().call("tx_pool_info", [], try_count=1, timeout=10)
        assert info["tip_number"] == "0x0"
        assert info["pending"] == info["proposed"] == "0x0"
        workload.stop_and_assert_process_exit(node)

        # 2. 正值、零、负值及 u32 越界值全部是未知字段，不再按旧数值范围区分。
        fields = [
            ("max_tx_verify_time_ms", value)
            for value in (1, 12_000, 0, -1, 4_294_967_296)
        ]
        fields += [("min_tx_verify_time_ms", 1), ("cycles_per_ms", 1)]
        for field, value in fields:
            rpc_port, p2p_port = workload.unused_ports(2)
            invalid = self.CkbNode.init_dev_by_port(
                self.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/budget_config/{field}_{value}_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            self.nodes.append(invalid)
            invalid.prepare()
            config_path = Path(invalid.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"][field] = value
            config_path.write_text(toml.dumps(config))
            result = subprocess.run(
                [invalid.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                cwd=invalid.ckb_dir,
                text=True,
                capture_output=True,
                timeout=15,
            )
            output = result.stdout + result.stderr
            assert result.returncode != 0, f"{field}={value} unexpectedly started"
            assert f"unknown field `{field}`" in output, output
            print(f"Rejected {field}={value}: exit={result.returncode}; {output}")

            # 3. 每个配置负例都核对端口未监听；以 unknown field 而非启动等待超时归因。
            with socket.socket() as connection:
                connection.settimeout(1)
                assert connection.connect_ex(("127.0.0.1", rpc_port)) != 0
        self.did_pass = True
