"""Real node relay: retryable budget refusal and subsequent peer usefulness."""

from framework.basic import CkbTest
from pathlib import Path
import toml
from test_cases.network_verification import workload


class TestRemoteTimeBudgetPeer(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 发送节点：12 秒上限，正常接受并转发慢交易。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.source = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestRemoteTimeBudgetPeer/source_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.source)
            cls.source.prepare()
            config_path = Path(cls.source.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
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
            workload.add_genesis_test_cells(cls.source)
            cls.source.start()
            cls.started_nodes.append(cls.source)
            assert cls.source.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：1ms 上限，触发网络慢交易超时。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestRemoteTimeBudgetPeer/receiver_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.receiver)
            cls.receiver.prepare()
            config_path = Path(cls.receiver.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=1,
                max_tx_verify_cycles=1_000_000_000,
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
            workload.add_genesis_test_cells(cls.receiver)
            cls.receiver.start()
            cls.started_nodes.append(cls.receiver)
            assert cls.receiver.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 第二个转发节点：重新发送同一交易，验证可重试。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.other = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestRemoteTimeBudgetPeer/other_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.other)
            cls.other.prepare()
            config_path = Path(cls.other.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
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
            workload.add_genesis_test_cells(cls.other)
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

    # TEST-MAP: VT2-01
    # TEST-MAP: VT2-02
    def test_remote_timeout_keeps_peer_connected_for_next_valid_transaction(self):
        """
        1. 发送慢交易，让 1ms 接收节点触发网络验证超时。
        2. 另一节点重发相同交易，验证接收节点仍会重新请求并验证。
        3. 原发送节点继续转发快交易，检查成功入池、连接保留且没有封禁。
        """
        source = self.source
        receiver = self.receiver
        other = self.other

        # 1. 发送慢交易，检查低预算接收方的网络验证超时结果。
        slow = workload.build_transaction(source, slow=True)
        source.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == source.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        tx_hash = source.getClient().send_transaction(slow)
        workload.assert_pool_transaction(source, tx_hash, slow, workload.BUDGET_CYCLES)
        workload.assert_remote_budget_refusal(receiver, tx_hash)

        # 2. 另一节点重发相同交易，检查新增请求和第二次验证超时。
        refusals = len(workload.remote_refusals(receiver, tx_hash))
        requests_before = len(workload.transaction_requests(other, tx_hash))
        assert refusals >= 1
        other.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == other.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        assert other.getClient().send_transaction(slow) == tx_hash
        workload.wait_for(
            lambda: len(workload.transaction_requests(other, tx_hash))
            > requests_before,
            f"fresh GetRelayTransactions for {tx_hash}",
            45,
        )
        workload.assert_remote_budget_refusal(receiver, tx_hash, refusals + 1)
        # 3. 原节点用独立输入发送快交易，检查入池成功、连接保留且没有封禁。
        fast = workload.build_transaction(source, cell=1)
        fast_hash = source.getClient().send_transaction(fast)
        workload.assert_pool_transaction(
            receiver, fast_hash, fast, workload.FAST_CYCLES
        )
        assert any(
            peer["node_id"] == source.get_peer_id()
            for peer in receiver.getClient().get_peers()
        )
        assert receiver.getClient().get_banned_addresses() == []
        assert (
            receiver.getClient().get_transaction(tx_hash)["tx_status"]["status"]
            == "unknown"
        )
        self.did_pass = True
