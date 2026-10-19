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
            # 出块节点：先本地验证交易，再生成有效区块。预算单位为毫秒，不是 VM cycles。
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
            workload.add_genesis_test_cells(cls.miner)
            cls.miner.start()
            cls.started_nodes.append(cls.miner)
            assert cls.miner.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：1ms 上限，触发网络慢交易超时。预算单位为毫秒，不是 VM cycles。
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

    # TEST-MAP: VT2-08
    # TEST-EVIDENCE-BEGIN: VT2-08
    # Evidence | Case: VT2-08 (covered)
    # Evidence | Setup: Independent source admits slow transaction; low-cap receiver sees remote refusal only, never local successful validation of target. Fresh class-owned nodes are explicitly configured and started in TestBudgetConsensusIsolation.setup_class; each class has one independent scenario.
    # Evidence | Trigger: Disconnect receiver; mine target on source; deliver full blocks through ordinary submit_block, not process_block_without_verify.
    # Evidence | Observe: All block hashes match, canonical tips converge, and committed transaction body/block hash equals independently mined source.
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(remote_refusals(node, tx_hash)) >= count,
    # Evidence |         f"processed remote CPU-budget refusal {count} for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Assert: assert tx_hash not in pool_transactions(client)
    # Evidence | Assert: assert result["tx_status"]["status"] == "unknown", result
    # Evidence | Assert: assert result["transaction"] is None
    # Evidence | Assert: assert client.get_banned_addresses() == []
    # Evidence | Assert: assert node.get_connected_count() >= 1
    # Evidence | Assert: assert receiver.submit_block("0x0", block) == expected_hash
    # Evidence | Assert: raise AssertionError(f"block receiver did not reach {expected_tip}")
    # Evidence | Assert: assert observed["tx_status"]["status"] == "committed"
    # Evidence | Assert: assert observed["transaction"] == committed["transaction"]
    # Evidence | Assert: assert (
    # Evidence |             observed["tx_status"]["block_hash"] == committed["tx_status"]["block_hash"]
    # Evidence |         )
    # Evidence | Assert: assert (
    # Evidence |             receiver.getClient().get_tip_header()["hash"]
    # Evidence |             == miner.getClient().get_tip_header()["hash"]
    # Evidence |         )
    # Evidence | Limit: Full-block RPC instead of P2P block transport, with ordinary consensus validation still enabled at receiver.
    # Evidence | Limit: Static coverage only; this readability review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-08
    def test_pool_timeout_does_not_reject_independently_mined_valid_block(self):
        """
        1. 接收节点先因网络预算拒绝慢交易。
        2. 断开交易转发，由发送方独立打包包含该交易的有效区块。
        3. 仅同步完整区块，检查低预算节点仍正常验块并确认该交易。
        """
        miner = self.miner
        receiver = self.receiver
        tx = workload.build_transaction(miner, slow=True)
        miner.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == miner.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        tx_hash = miner.getClient().send_transaction(tx)
        workload.assert_pool_transaction(miner, tx_hash, tx, workload.BUDGET_CYCLES)
        workload.assert_remote_budget_refusal(receiver, tx_hash)
        receiver.getClient().remove_node(miner.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0,
            "isolate full-block receiver",
        )
        before = miner.getClient().get_tip_block_number()
        committed = self.Miner.miner_until_tx_committed(miner, tx_hash)
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
