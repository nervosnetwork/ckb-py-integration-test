"""The same signed/packed bytes retain their local admission semantics."""

import copy

import pytest

from framework.basic import CkbTest
from pathlib import Path
import toml
from test_cases.network_verification.runtime import requires_test_feature
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash


@pytest.mark.network_verification
@requires_test_feature
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

    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-06
    # TEST-MAP: VT2-07
    # TEST-EVIDENCE-BEGIN: VT2-06
    # Evidence | Case: VT2-06 (covered)
    # Evidence | Setup: Cap=1 receiver; child resolves common uncommitted parent. Same child is dry-run valid then actually remotely budget-refused. Fresh class-owned nodes are explicitly configured and started in TestSourceBudget.setup_class; each class has one independent scenario.
    # Evidence | Trigger: Local send_transaction of exactly the same tx object after hash-specific refusal, without increasing cap.
    # Evidence | Observe: Same independently encoded hash returned, exact body/fixed cycles in pool, then committed.
    # Evidence | Assert: assert workload.relay_slow(source, receiver, tx) == tx_hash
    # Evidence | Assert: assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
    # Evidence | Assert: assert client.send_transaction(tx) == tx_hash
    # Evidence | Assert: assert committed["tx_status"]["status"] == "committed"
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(remote_refusals(node, tx_hash)) >= count,
    # Evidence |         f"processed remote CPU-budget refusal {count} for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Assert: assert tx_hash not in owners(client)
    # Evidence | Assert: assert result["tx_status"]["status"] == "unknown", result
    # Evidence | Assert: assert result["transaction"] is None
    # Evidence | Assert: assert client.get_banned_addresses() == []
    # Evidence | Assert: assert node.get_connected_count() >= 1
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Limit: Combined with VT2-07 setup; preceding dry-run does not invalidate the oracle because subsequent remote refusal explicitly occurs.
    # Evidence | Limit: Coverage is static; this native-pytest review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-06
    # TEST-EVIDENCE-BEGIN: VT2-07
    # Evidence | Case: VT2-07 (covered)
    # Evidence | Setup: Both nodes hold same pending parent; receiver has no successful prior child validation. Child hash is computed without node verification. Fresh class-owned nodes are explicitly configured and started in TestSourceBudget.setup_class; each class has one independent scenario.
    # Evidence | Trigger: Dry-run child, compare state, relay the same bytes at cap1, then submit locally.
    # Evidence | Observe: Fixed135598681 cycles,100CKB fee, unchanged pool/unknown child, pending parent, actual subsequent remote refusal and eventual same-hash local success.
    # Evidence | Assert: assert status["status"] == "pending" and status["block_hash"] is None
    # Evidence | Assert: assert receiver.get_connected_count() == 0
    # Evidence | Assert: assert int(preview["cycles"], 16) == workload.BUDGET_CYCLES
    # Evidence | Assert: assert int(preview["fee"], 16) == 100 * workload.CKB
    # Evidence | Assert: assert client.get_raw_tx_pool(False) == before
    # Evidence | Assert: assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
    # Evidence | Assert: assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
    # Evidence | Assert: assert workload.relay_slow(source, receiver, tx) == tx_hash
    # Evidence | Assert: assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
    # Evidence | Assert: assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
    # Evidence | Assert: assert client.send_transaction(tx) == tx_hash
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(remote_refusals(node, tx_hash)) >= count,
    # Evidence |         f"processed remote CPU-budget refusal {count} for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Assert: assert tx_hash not in owners(client)
    # Evidence | Assert: assert result["tx_status"]["status"] == "unknown", result
    # Evidence | Assert: assert result["transaction"] is None
    # Evidence | Assert: assert client.get_banned_addresses() == []
    # Evidence | Assert: assert node.get_connected_count() >= 1
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Limit: Proves no observable dry-run cache bypass, not private VM-call/cache counters.
    # Evidence | Limit: Coverage is static; this native-pytest review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-07
    def test_network_refusal_does_not_block_dry_run_or_local_submission(self):
        """
        1. 两个节点都保留同一未确认父交易，构造花费父输出的慢交易。
        2. dry-run 返回固定 cycles/手续费且不改变池状态，随后网络转发仍超时。
        3. 本地提交完全相同的交易，检查入池并最终打包。
        """
        source = self.source
        receiver = self.receiver
        client = receiver.getClient()
        # Both nodes know a still-uncommitted parent; the child resolves
        # its input from the pool rather than a confirmed genesis cell.
        parent_tx = workload.transaction(source)
        parent_tx["inputs"].extend(workload.transaction(source, cell=7)["inputs"])
        change = copy.deepcopy(parent_tx["outputs"][0])
        parent_tx["outputs"][0]["capacity"] = hex(6500 * workload.CKB)
        parent_tx["outputs"].append(change)
        parent_tx["outputs_data"].append("0x")
        parent = source.getClient().send_transaction(parent_tx)
        assert client.send_transaction(parent_tx) == parent
        for node in (source, receiver):
            status = node.getClient().get_transaction(parent)["tx_status"]
            assert status["status"] == "pending" and status["block_hash"] is None
        tx = workload.transaction(source, slow=True)
        tx["inputs"][0]["previous_output"] = {"tx_hash": parent, "index": "0x0"}
        # Reuse the existing packed raw-transaction hash helper. No node
        # has yet validated this child; actual sender RPC must match it.
        tx_hash = transaction_hash(tx)
        assert receiver.get_connected_count() == 0
        before = client.get_raw_tx_pool(False)
        preview = client.test_tx_pool_accept(tx, "passthrough")
        assert int(preview["cycles"], 16) == workload.BUDGET_CYCLES
        assert int(preview["fee"], 16) == 100 * workload.CKB
        assert client.get_raw_tx_pool(False) == before
        assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
        assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
        assert workload.relay_slow(source, receiver, tx) == tx_hash
        assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
        # Actual subsequent remote refusal shows the dry-run did not warm
        # a success cache that bypasses the cap.
        assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
        assert client.send_transaction(tx) == tx_hash
        workload.accepted(receiver, tx_hash, tx, workload.BUDGET_CYCLES)
        committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
        assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True


@pytest.mark.network_verification
@requires_test_feature
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

    def teardown_method(self, method):
        # 日志由 teardown_class 保留，避免复制项目共享 tmp。
        pass

    # TEST-MAP: VT2-09
    # TEST-EVIDENCE-BEGIN: VT2-09
    # Evidence | Case: VT2-09 (covered)
    # Evidence | Setup: Slow transaction remotely refused under cap1, then locally admitted; full body/cycles saved; source peer disconnected. Fresh class-owned nodes are explicitly configured and started in TestPersistedBudget.setup_class; each class has one independent scenario.
    # Evidence | Trigger: Call workload.restart on the same directory: identify the listener by PID and owning cwd, wait for old PID disappearance and no listener, then require a distinct new listening PID. No peer retransmission or manual resubmit; query restored pool and mine target.
    # Evidence | Observe: Old process disappearance and distinct owned new listener are required before pool reads. Same-directory restored transaction body/cycles equal pre-shutdown values, no peer is connected, and transaction finally commits.
    # Evidence | Assert: assert client.send_transaction(tx) == tx_hash
    # Evidence | Assert: assert receiver.get_connected_count() == 0
    # Evidence | Assert: assert after["transaction"] == before["transaction"]
    # Evidence | Assert: assert after["cycles"] == before["cycles"]
    # Evidence | Assert: assert committed["tx_status"]["status"] == "committed"
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Assert: workload.restart(receiver)
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
    # Evidence | Limit: Restart ownership uses lsof PID/cwd checks and a bounded disappearance wait, not a retained subprocess handle; failure to terminate or identify the process fails the test. Exact graceful-exit status is not recorded, but old in-memory state cannot satisfy the post-boundary pool oracle.
    # Evidence | Limit: Coverage is static; this native-pytest review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-09
    def test_persisted_slow_transaction_reloads_without_network_budget(self):
        """
        1. 网络慢交易超时后，通过本地提交使其入池并保存交易体/cycles。
        2. 断开对端并冷重启同一节点目录，不重发或重新提交交易。
        3. 检查持久化恢复后的交易体/cycles 不变，并能重新打包。
        """
        source = self.source
        receiver = self.receiver
        tx = workload.transaction(source, slow=True)
        tx_hash = workload.relay_slow(source, receiver, tx)
        client = receiver.getClient()
        assert client.send_transaction(tx) == tx_hash
        before = workload.accepted(receiver, tx_hash, tx, workload.BUDGET_CYCLES)
        client.remove_node(source.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0, "isolate persisted pool"
        )
        workload.restart(receiver)
        assert receiver.get_connected_count() == 0
        after = workload.accepted(receiver, tx_hash, tx, workload.BUDGET_CYCLES)
        assert after["transaction"] == before["transaction"]
        assert after["cycles"] == before["cycles"]
        committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
        assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True
