"""Cold reorg recovery bypasses the network-only VM CPU cap."""

import pytest

from framework.basic import CkbTest
from pathlib import Path
import toml
from test_cases.network_verification.runtime import requires_test_feature
from test_cases.network_verification import workload


@pytest.mark.network_verification
@requires_test_feature
class TestRecoveryBudget(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 接收节点：1ms 上限，触发网络慢交易超时。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestRecoveryBudget/receiver_{rpc_port}",
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

            # 分叉节点：构造不含目标交易的更长链。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.fork = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestRecoveryBudget/fork_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.fork)
            cls.fork.prepare()
            config_path = Path(cls.fork.ckb_toml_path)
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
            workload.add_genesis_test_cells(cls.fork)
            cls.fork.start()
            cls.started_nodes.append(cls.fork)
            assert cls.fork.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 发送节点：12 秒上限，正常接受并转发慢交易。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.source = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestRecoveryBudget/source_{rpc_port}",
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

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.receiver, 30)
            workload.sync_blocks(cls.receiver, cls.fork, 1, 30)
            workload.sync_blocks(cls.receiver, cls.source, 1, 30)
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

    # TEST-MAP: VT2-10
    # TEST-EVIDENCE-BEGIN: VT2-10
    # Evidence | Case: VT2-10 (covered)
    # Evidence | Setup: Slow transaction first network-refused, then locally mined on old branch; separate valid fork excludes it and is two blocks longer; recovery cap1e9 exceeds transaction cycles. Fresh class-owned nodes are explicitly configured and started in TestRecoveryBudget.setup_class; each class has one independent scenario.
    # Evidence | Trigger: Use workload.restart_and_assert_new_process at cap1 and require verified old-PID disappearance/no listener followed by a distinct owned listening PID; then deliver the better fork as full blocks without a connected peer, observe detached transaction and re-mine.
    # Evidence | Observe: Verified cold process boundary, preserved old chain/empty pool/no peer, canonical switch to new fork, exact recovered transaction body/cycles and final commitment.
    # Evidence | Assert: assert client.send_transaction(tx) == tx_hash
    # Evidence | Assert: assert other.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
    # Evidence | Assert: assert new_tip != old_tip
    # Evidence | Assert: assert client.get_tip_header()["hash"] == old_tip
    # Evidence | Assert: assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}
    # Evidence | Assert: assert receiver.get_connected_count() == 0
    # Evidence | Assert: assert client.get_tip_header()["hash"] == new_tip
    # Evidence | Assert: assert receiver.get_connected_count() == 0
    # Evidence | Assert: assert committed["tx_status"]["status"] == "committed"
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(pool_transactions(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Assert: workload.restart_and_assert_new_process(receiver)
    # Evidence | Assert: assert result.returncode in (0, 1), result.stderr
    # Evidence | Assert: assert len(pids) == 1, pids
    # Evidence | Assert: assert paths == [Path(node.ckb_dir).resolve()], (pid, paths, node.ckb_dir)
    # Evidence | Assert: assert previous is not None
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: not _process_exists(previous), f"old node PID {previous} exited", 20
    # Evidence |     )
    # Evidence | Assert: assert _node_process_id(node) is None
    # Evidence | Assert: current = wait_for(lambda: _node_process_id(node), "restarted node listening", 20)
    # Evidence | Assert: assert current != previous, (previous, current)
    # Evidence | Limit: Coldness is established by old-PID disappearance and distinct owned new listener; no private VM/cache counter is claimed. lsof/OS process visibility is an explicit local prerequisite.
    # Evidence | Limit: Static coverage only; this readability review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-10
    def test_detached_transaction_recovers_after_restart_under_one_ms_cap(self):
        """
        1. 慢交易先网络超时，再本地入池并在旧分叉确认。
        2. 另一节点生成不含该交易的更长分叉；冷重启旧链节点。
        3. 只同步完整区块触发重组，检查交易自动回池且重新打包。
        """
        receiver = self.receiver
        fork = self.fork
        source = self.source
        client, other = receiver.getClient(), fork.getClient()
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
        client.remove_node(source.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0, "isolate reorg fixture"
        )
        assert client.send_transaction(tx) == tx_hash
        self.Miner.miner_until_tx_committed(receiver, tx_hash)
        old_height, old_tip = (
            client.get_tip_block_number(),
            client.get_tip_header()["hash"],
        )
        assert other.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
        self.Miner.make_tip_height_number(fork, old_height + 2)
        new_tip = other.get_tip_header()["hash"]
        assert new_tip != old_tip
        workload.restart_and_assert_new_process(receiver)
        assert client.get_tip_header()["hash"] == old_tip
        assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}
        assert receiver.get_connected_count() == 0
        # Full blocks only: no peer can inject the detached transaction.
        workload.sync_blocks(fork, receiver, 31, old_height + 2)
        assert client.get_tip_header()["hash"] == new_tip
        workload.assert_pool_transaction(receiver, tx_hash, tx, workload.BUDGET_CYCLES)
        assert receiver.get_connected_count() == 0
        committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
        assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True
