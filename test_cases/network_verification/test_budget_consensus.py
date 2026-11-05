"""Network pool time limits must not reject a valid full block."""

from framework.basic import CkbTest
from pathlib import Path
import toml
from test_cases.network_verification import workload


class TestBudgetConsensusIsolation(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 出块节点：默认 CPU 上限，本地验证交易后生成有效区块。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.miner = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestBudgetConsensusIsolation/miner_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.miner)
            cls.miner.prepare()
            config_path = Path(cls.miner.ckb_toml_path)
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
                cls.miner, program_names=("cpu_budget_stress_1b",)
            )
            # 仅放宽本用例 dev 链的 cycles；CPU 时间上限保持默认值。
            spec_path = Path(cls.miner.ckb_specs_config_path)
            spec = toml.loads(spec_path.read_text())
            spec["params"]["max_block_cycles"] = 1_000_000_000_000
            spec_path.write_text(toml.dumps(spec))
            cls.miner.start()
            cls.started_nodes.append(cls.miner)
            assert cls.miner.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：保持默认 8 秒 CPU 上限；以目标 hash 的拒绝日志证明预算耗尽。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestBudgetConsensusIsolation/receiver_{rpc_port}",
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
            cls.Miner.make_tip_height_number(cls.miner, 30)
            workload.sync_blocks(cls.miner, cls.receiver, 1, 30)
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

    # TEST-MAP: EVT-06
    # TEST-MAP: VT2-08
    def test_pool_timeout_does_not_reject_independently_mined_valid_block(self):
        """
        1. 接收节点先因网络预算拒绝慢交易。
        2. 断开交易转发，由发送方独立打包包含该交易的有效区块。
        3. 仅同步完整区块，检查接收节点仍正常验块并确认该交易。
        """
        miner = self.miner
        receiver = self.receiver

        # 1. 转发慢交易，确认发送方已入池而接收方出现网络验证超时。
        tx = workload.build_budget_transaction(miner)
        miner.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == miner.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        tx_hash = miner.getClient().call(
            "send_transaction",
            [tx, "passthrough"],
            try_count=1,
            timeout=workload.BUDGET_RPC_TIMEOUT,
        )
        # 本地 cycles 仅作跨入口一致性对照；不作为独立 VM 基准。
        cycles = int(
            workload.pool_transactions(miner.getClient())[tx_hash]["cycles"], 16
        )
        assert 0 < cycles < 1_000_000_000_000
        workload.assert_pool_transaction(miner, tx_hash, tx, cycles)
        workload.assert_remote_budget_refusal(receiver, tx_hash)

        # 2. 断开 P2P 连接，由发送方独立挖出包含该交易的有效区块。
        receiver.getClient().remove_node(miner.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0,
            "isolate full-block receiver",
        )
        before = miner.getClient().get_tip_block_number()
        committed = self.Miner.miner_until_tx_committed(miner, tx_hash)

        # 3. 只向接收方提交完整区块，核对确认状态、交易体、所在区块和链尖端。
        workload.sync_blocks(
            miner, receiver, before + 1, miner.getClient().get_tip_block_number()
        )
        observed = receiver.getClient().get_transaction(tx_hash)
        assert observed["tx_status"]["status"] == "committed"
        assert observed["transaction"] == committed["transaction"]
        assert (
            observed["tx_status"]["block_hash"] == committed["tx_status"]["block_hash"]
        )
        assert (
            receiver.getClient().get_tip_header()["hash"]
            == miner.getClient().get_tip_header()["hash"]
        )
        self.did_pass = True
