"""A network time-budget refusal must permit later verification."""

from framework.basic import CkbTest
from pathlib import Path
import toml
from test_cases.network_verification import workload


class TestTimeBudgetRetry(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 发送节点：保持默认 CPU 上限，通过本地提交完整验证并转发慢交易。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.source = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestTimeBudgetRetry/source_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.source)
            cls.source.prepare()
            config_path = Path(cls.source.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_cycles=1_000_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(
                cls.source, program_names=("cpu_budget_stress_1b",)
            )
            # 仅放宽本用例 dev 链的 cycles；CPU 时间上限保持默认值。
            spec_path = Path(cls.source.ckb_specs_config_path)
            spec = toml.loads(spec_path.read_text())
            spec["params"]["max_block_cycles"] = 1_000_000_000_000
            spec_path.write_text(toml.dumps(spec))
            cls.source.start()
            cls.started_nodes.append(cls.source)
            assert cls.source.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：保持默认 8 秒 CPU 上限；以目标 hash 的拒绝日志证明预算耗尽。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestTimeBudgetRetry/receiver_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.receiver)
            cls.receiver.prepare()
            config_path = Path(cls.receiver.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_cycles=1_000_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(
                cls.receiver, program_names=("cpu_budget_stress_1b",)
            )
            # 仅放宽本用例 dev 链的 cycles；CPU 时间上限保持默认值。
            spec_path = Path(cls.receiver.ckb_specs_config_path)
            spec = toml.loads(spec_path.read_text())
            spec["params"]["max_block_cycles"] = 1_000_000_000_000
            spec_path.write_text(toml.dumps(spec))
            cls.receiver.start()
            cls.started_nodes.append(cls.receiver)
            assert cls.receiver.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 第二个转发节点：默认配置，本地接受后重新广播同一交易。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.other = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestTimeBudgetRetry/other_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.other)
            cls.other.prepare()
            config_path = Path(cls.other.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_cycles=1_000_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(
                cls.other, program_names=("cpu_budget_stress_1b",)
            )
            # 仅放宽本用例 dev 链的 cycles；CPU 时间上限保持默认值。
            spec_path = Path(cls.other.ckb_specs_config_path)
            spec = toml.loads(spec_path.read_text())
            spec["params"]["max_block_cycles"] = 1_000_000_000_000
            spec_path.write_text(toml.dumps(spec))
            cls.other.start()
            cls.started_nodes.append(cls.other)
            assert cls.other.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.source, 30)
            workload.sync_blocks(cls.source, cls.receiver, 1, 30)
            workload.sync_blocks(cls.source, cls.other, 1, 30)
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.started_nodes):
            node.stop()
        for node in cls.nodes:
            workload.save_node_logs(node)
            node.clean()

    # TEST-MAP: EVT-04
    def test_default_network_budget_allows_remote_retry_and_local_submission(self):
        """
        1. 默认预算下转发慢交易，检查接收端实际请求并因预算耗尽拒绝。
        2. 经另一节点重发相同字节，检查新增请求及第二次预算拒绝。
        3. 隔离所有对端，本地提交同一交易，核对 hash、交易体和 cycles。
        """
        source, receiver, other = self.source, self.receiver, self.other

        # 1. 所有节点保持默认 CPU 上限，先证明目标交易走到真实网络验证。
        tx = workload.build_budget_transaction(source)
        source.connected(receiver)
        workload.wait_for(lambda: receiver.get_connected_count() == 1, "peer connected")
        tx_hash = source.getClient().call(
            "send_transaction",
            [tx, "passthrough"],
            try_count=1,
            timeout=workload.BUDGET_RPC_TIMEOUT,
        )
        cycles = int(
            workload.pool_transactions(source.getClient())[tx_hash]["cycles"], 16
        )
        assert 0 < cycles < 1_000_000_000_000
        workload.assert_pool_transaction(source, tx_hash, tx, cycles)
        workload.assert_remote_budget_refusal(receiver, tx_hash)
        assert workload.transaction_requests(source, tx_hash)

        # 2. 不重启、不改预算；新来源只连接 receiver，排除已有请求日志。
        refusals = len(workload.remote_refusals(receiver, tx_hash))
        requests_before = len(workload.transaction_requests(other, tx_hash))
        other.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == other.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "second peer connected",
        )
        assert (
            other.getClient().call(
                "send_transaction",
                [tx, "passthrough"],
                try_count=1,
                timeout=workload.BUDGET_RPC_TIMEOUT,
            )
            == tx_hash
        )
        workload.assert_pool_transaction(other, tx_hash, tx, cycles)
        workload.wait_for(
            lambda: len(workload.transaction_requests(other, tx_hash))
            > requests_before,
            f"fresh GetRelayTransactions for {tx_hash}",
            45,
        )
        workload.assert_remote_budget_refusal(receiver, tx_hash, refusals + 1)

        # 3. 断开两个真实对端，仅用本地入口提交完全相同的交易。
        client = receiver.getClient()
        for peer in (source, other):
            client.remove_node(peer.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0, "isolate local retry"
        )
        assert (
            client.call(
                "send_transaction",
                [tx, "passthrough"],
                try_count=1,
                timeout=workload.BUDGET_RPC_TIMEOUT,
            )
            == tx_hash
        )
        workload.assert_pool_transaction(receiver, tx_hash, tx, cycles)
        assert receiver.get_connected_count() == 0
        assert client.get_banned_addresses() == []
        self.did_pass = True
