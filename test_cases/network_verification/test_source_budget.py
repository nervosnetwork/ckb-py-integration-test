"""The same signed/packed bytes retain their local admission semantics."""

import copy
import time


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
            # 发送节点：保持默认 CPU 上限，通过本地提交完整验证并转发慢交易。
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
                f"network_verification/TestSourceBudget/receiver_{rpc_port}",
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
    # TEST-MAP: EVT-01
    def test_network_refusal_does_not_block_preview_or_local_submission(self):
        """
        1. 两节点保留相同未确认父交易，连接后用独立快交易验证传播链路。
        2. 预检快速子交易，检查固定 cycles/fee、不入池、不广播；再提交同一字节。
        3. 另一慢子交易经真实 relay 触发默认网络预算拒绝，父交易仍在池中。
        4. 隔离对端，本地提交相同慢交易并打包确认。
        """
        source, receiver = self.source, self.receiver
        client = receiver.getClient()

        # 1. 父交易两个输出分别用于慢子交易和快速预检，避免相互花费冲突。
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
        source.connected(receiver)
        workload.wait_for(lambda: receiver.get_connected_count() == 1, "peer connected")
        control = workload.build_transaction(receiver, cell=6)
        control_hash = client.send_transaction(control)
        workload.assert_pool_transaction(
            source, control_hash, control, workload.FAST_CYCLES
        )

        # 2. 预检使用预算内的快速脚本，兼容预检本身受默认 CPU 上限约束的实现。
        fast = workload.build_transaction(source, output_capacity=6498 * workload.CKB)
        fast["inputs"][0]["previous_output"] = {"tx_hash": parent, "index": "0x1"}
        fast_hash = transaction_hash(fast)
        before = [node.getClient().get_raw_tx_pool(True) for node in (source, receiver)]
        preview = client.call(
            "test_tx_pool_accept",
            [fast, "passthrough"],
            try_count=1,
            timeout=30,
        )
        assert int(preview["cycles"], 16) == workload.FAST_CYCLES
        assert int(preview["fee"], 16) == workload.CKB
        # 有限观察窗口；快交易正对照已证明同一连接具备传播能力。
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            for node, pool in zip((source, receiver), before):
                assert node.getClient().get_raw_tx_pool(True) == pool
                assert (
                    node.getClient().get_transaction(fast_hash)["tx_status"]["status"]
                    == "unknown"
                )
            assert not workload.transaction_requests(receiver, fast_hash)
            time.sleep(0.1)
        assert client.send_transaction(fast) == fast_hash
        for node in (source, receiver):
            workload.assert_pool_transaction(
                node, fast_hash, fast, workload.FAST_CYCLES
            )

        # 3. 预检快交易不预热慢脚本；以目标 hash 的拒绝而非等待时长判定预算耗尽。
        tx = workload.build_budget_transaction(source)
        tx["inputs"][0]["previous_output"] = {"tx_hash": parent, "index": "0x0"}
        tx_hash = transaction_hash(tx)
        assert (
            source.getClient().call(
                "send_transaction",
                [tx, "passthrough"],
                try_count=1,
                timeout=workload.BUDGET_RPC_TIMEOUT,
            )
            == tx_hash
        )
        cycles = int(
            workload.pool_transactions(source.getClient())[tx_hash]["cycles"], 16
        )
        assert 0 < cycles < 1_000_000_000_000
        workload.assert_pool_transaction(source, tx_hash, tx, cycles)
        workload.assert_remote_budget_refusal(receiver, tx_hash)
        assert client.get_transaction(parent)["tx_status"]["status"] == "pending"

        # 4. 隔离网络后仍使用默认配置，本地验证不受网络时间预算限制。
        client.remove_node(source.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0, "isolate local submission"
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
            # 发送节点：保持默认 CPU 上限，通过本地提交完整验证并转发慢交易。
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
                f"network_verification/TestPersistedBudget/receiver_{rpc_port}",
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
    # TEST-MAP: EVT-01
    def test_persisted_slow_transaction_reloads_without_network_budget(self):
        """
        1. 网络慢交易超时后，通过本地提交使其入池并保存交易体/cycles。
        2. 断开对端并冷重启同一节点目录，不重发或重新提交交易。
        3. 检查持久化恢复后的交易体/cycles 不变，并能重新打包。
        """
        source = self.source
        receiver = self.receiver

        # 1. 网络慢交易超时后本地提交，记录入池交易体和 cycles。
        tx = workload.build_budget_transaction(source)
        source.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == source.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        tx_hash = source.getClient().call(
            "send_transaction",
            [tx, "passthrough"],
            try_count=1,
            timeout=workload.BUDGET_RPC_TIMEOUT,
        )
        # 本地 cycles 仅作跨入口一致性对照；不作为独立 VM 基准。
        cycles = int(
            workload.pool_transactions(source.getClient())[tx_hash]["cycles"], 16
        )
        assert 0 < cycles < 1_000_000_000_000
        workload.assert_pool_transaction(source, tx_hash, tx, cycles)
        workload.assert_remote_budget_refusal(receiver, tx_hash)
        client = receiver.getClient()
        assert (
            client.call(
                "send_transaction",
                [tx, "passthrough"],
                try_count=1,
                timeout=workload.BUDGET_RPC_TIMEOUT,
            )
            == tx_hash
        )
        before = workload.assert_pool_transaction(receiver, tx_hash, tx, cycles)

        # 2. 断开对端并冷重启同一节点目录，不重新提交或转发该交易。
        client.remove_node(source.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0, "isolate persisted pool"
        )
        workload.restart_and_assert_new_process(receiver)
        assert receiver.get_connected_count() == 0

        # 3. 检查持久化恢复后的交易体和 cycles 不变，并将恢复交易打包确认。
        after = workload.assert_pool_transaction(
            receiver, tx_hash, tx, cycles, timeout=workload.BUDGET_RPC_TIMEOUT
        )
        assert after["transaction"] == before["transaction"]
        assert after["cycles"] == before["cycles"]
        committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
        assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True
