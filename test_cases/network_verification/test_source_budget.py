"""The same signed/packed bytes retain their local admission semantics."""

import copy


from framework.basic import CkbTest
from pathlib import Path
import toml
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash


class TestSourceBudget(CkbTest):
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
                f"network_verification/TestSourceBudget/source_{rpc_port}",
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
                f"network_verification/TestSourceBudget/receiver_{rpc_port}",
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

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.source, 30)
            workload.sync_blocks(cls.source, cls.receiver, 1, 30)
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

    # TEST-MAP: VT2-06
    # TEST-MAP: VT2-07
    def test_network_refusal_does_not_block_dry_run_or_local_submission(self):
        """
        1. 两个节点都保留同一未确认父交易，构造花费父输出的慢交易。
        2. dry-run 返回固定 cycles/手续费且不改变池状态，随后网络转发仍超时。
        3. 本地提交完全相同的交易，检查入池并最终打包。
        """
        source = self.source
        receiver = self.receiver
        client = receiver.getClient()
        # 1. 两个节点都保留未确认父交易，让慢交易从池中的父输出解析输入。
        parent_tx = workload.build_transaction(source)
        parent_tx["inputs"].extend(workload.build_transaction(source, cell=7)["inputs"])
        change = copy.deepcopy(parent_tx["outputs"][0])
        parent_tx["outputs"][0]["capacity"] = hex(6500 * workload.CKB)
        parent_tx["outputs"].append(change)
        parent_tx["outputs_data"].append("0x")
        parent = source.getClient().send_transaction(parent_tx)
        assert client.send_transaction(parent_tx) == parent
        for node in (source, receiver):
            status = node.getClient().get_transaction(parent)["tx_status"]
            assert status["status"] == "pending" and status["block_hash"] is None
        tx = workload.build_transaction(source, slow=True)
        tx["inputs"][0]["previous_output"] = {"tx_hash": parent, "index": "0x0"}
        # 2. 先预检 cycles、手续费和池状态，再实际转发，确认网络路径仍然超时。
        # 提前计算原始交易 hash，随后与发送节点返回的 hash 对照。
        tx_hash = transaction_hash(tx)
        assert receiver.get_connected_count() == 0
        before = client.get_raw_tx_pool(False)
        preview = client.test_tx_pool_accept(tx, "passthrough")
        assert int(preview["cycles"], 16) == workload.BUDGET_CYCLES
        assert int(preview["fee"], 16) == 100 * workload.CKB
        assert client.get_raw_tx_pool(False) == before
        assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
        assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
        source.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == source.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        relayed_hash = source.getClient().send_transaction(tx)
        workload.assert_pool_transaction(
            source, relayed_hash, tx, workload.BUDGET_CYCLES
        )
        workload.assert_remote_budget_refusal(receiver, relayed_hash)
        assert relayed_hash == tx_hash
        assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
        # 后续真实网络超时说明预检没有使该交易绕过网络预算。
        assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"

        # 3. 本地提交完全相同的交易，检查固定 cycles、入池和最终确认状态。
        assert client.send_transaction(tx) == tx_hash
        workload.assert_pool_transaction(receiver, tx_hash, tx, workload.BUDGET_CYCLES)
        committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
        assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True


class TestPersistedBudget(CkbTest):
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
                f"network_verification/TestPersistedBudget/source_{rpc_port}",
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
                f"network_verification/TestPersistedBudget/receiver_{rpc_port}",
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

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.source, 30)
            workload.sync_blocks(cls.source, cls.receiver, 1, 30)
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

    # TEST-MAP: VT2-09
    def test_persisted_slow_transaction_reloads_without_network_budget(self):
        """
        1. 网络慢交易超时后，通过本地提交使其入池并保存交易体/cycles。
        2. 断开对端并冷重启同一节点目录，不重发或重新提交交易。
        3. 检查持久化恢复后的交易体/cycles 不变，并能重新打包。
        """
        source = self.source
        receiver = self.receiver

        # 1. 网络慢交易超时后本地提交，记录入池交易体和 cycles。
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
        client = receiver.getClient()
        assert client.send_transaction(tx) == tx_hash
        before = workload.assert_pool_transaction(
            receiver, tx_hash, tx, workload.BUDGET_CYCLES
        )

        # 2. 断开对端并冷重启同一节点目录，不重新提交或转发该交易。
        client.remove_node(source.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0, "isolate persisted pool"
        )
        workload.restart_and_assert_new_process(receiver)
        assert receiver.get_connected_count() == 0

        # 3. 检查持久化恢复后的交易体和 cycles 不变，并将恢复交易打包确认。
        after = workload.assert_pool_transaction(
            receiver, tx_hash, tx, workload.BUDGET_CYCLES
        )
        assert after["transaction"] == before["transaction"]
        assert after["cycles"] == before["cycles"]
        committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
        assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True
