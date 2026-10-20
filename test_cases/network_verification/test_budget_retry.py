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
            # 发送节点：12 秒上限，正常接受并转发慢交易。预算单位为毫秒，不是 VM cycles。
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
                f"network_verification/TestTimeBudgetRetry/receiver_{rpc_port}",
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
                f"network_verification/TestTimeBudgetRetry/other_{rpc_port}",
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

    # TEST-MAP: VT2-33
    def test_network_budget_accepts_same_transaction_after_limit_increase(self):
        """
        1. 低预算接收节点拒绝网络慢交易。
        2. 冷重启接收节点，将上限从 1ms 提高到 12000ms。
        3. 另一节点转发完全相同的交易，检查交易体和 cycles 正确入池。
        """
        source = self.source
        receiver = self.receiver
        other = self.other

        # 1. 转发慢交易，确认接收方在 1ms 网络预算下超时。
        tx = workload.build_transaction(source, slow=True)
        source.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == source.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        tx_hash = source.getClient().send_transaction(tx)
        workload.assert_pool_transaction(source, tx_hash, tx, workload.BUDGET_CYCLES)
        workload.assert_remote_budget_refusal(receiver, tx_hash)

        # 2. 将预算提高到 12000ms，确认旧进程退出后启动新进程。
        workload.restart_and_assert_new_process(receiver, cap=12_000)

        # 3. 由另一节点重新转发同一交易，检查 hash、交易体、cycles 和封禁状态。
        other.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == other.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        assert other.getClient().send_transaction(tx) == tx_hash
        workload.assert_pool_transaction(receiver, tx_hash, tx, workload.BUDGET_CYCLES)
        assert receiver.getClient().get_banned_addresses() == []
        self.did_pass = True
