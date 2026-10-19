"""The same signed/packed bytes retain their local admission semantics."""

import copy

from framework.basic import CkbTest
from test_cases.network_verification.runtime import requires_target
from test_cases.network_verification import workload as w
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash


@requires_target
class TestSourceBudget(CkbTest):
    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-06
    # TEST-MAP: VT2-07
    # TEST-EVIDENCE-BEGIN: VT2-06
    # Evidence | Case: VT2-06 (covered)
    # Evidence | Setup: Cap=1 receiver; child resolves common uncommitted parent. Same child is dry-run valid then actually remotely budget-refused.
    # Evidence | Trigger: Local send_transaction of exactly the same tx object after hash-specific refusal, without increasing cap.
    # Evidence | Observe: Same independently encoded hash returned, exact body/fixed cycles in pool, then committed.
    # Evidence | Assert: assert w.relay_slow(source, receiver, tx) == tx_hash
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
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-06
    # TEST-EVIDENCE-BEGIN: VT2-07
    # Evidence | Case: VT2-07 (covered)
    # Evidence | Setup: Both nodes hold same pending parent; receiver has no successful prior child validation. Child hash is computed without node verification.
    # Evidence | Trigger: Dry-run child, compare state, relay the same bytes at cap1, then submit locally.
    # Evidence | Observe: Fixed135598681 cycles,100CKB fee, unchanged pool/unknown child, pending parent, actual subsequent remote refusal and eventual same-hash local success.
    # Evidence | Assert: assert status["status"] == "pending" and status["block_hash"] is None
    # Evidence | Assert: assert receiver.get_connected_count() == 0
    # Evidence | Assert: assert int(preview["cycles"], 16) == w.BUDGET_CYCLES
    # Evidence | Assert: assert int(preview["fee"], 16) == 100 * w.CKB
    # Evidence | Assert: assert client.get_raw_tx_pool(False) == before
    # Evidence | Assert: assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
    # Evidence | Assert: assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
    # Evidence | Assert: assert w.relay_slow(source, receiver, tx) == tx_hash
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
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-07
    def test_network_refusal_does_not_block_dry_run_or_local_submission(self):
        with w.nodes(12_000, 1) as (source, receiver):
            client = receiver.getClient()
            # Both nodes know a still-uncommitted parent; the child resolves
            # its input from the pool rather than a confirmed genesis cell.
            parent_tx = w.transaction(source)
            parent_tx["inputs"].extend(w.transaction(source, cell=7)["inputs"])
            change = copy.deepcopy(parent_tx["outputs"][0])
            parent_tx["outputs"][0]["capacity"] = hex(6500 * w.CKB)
            parent_tx["outputs"].append(change)
            parent_tx["outputs_data"].append("0x")
            parent = source.getClient().send_transaction(parent_tx)
            assert client.send_transaction(parent_tx) == parent
            for node in (source, receiver):
                status = node.getClient().get_transaction(parent)["tx_status"]
                assert status["status"] == "pending" and status["block_hash"] is None
            tx = w.transaction(source, slow=True)
            tx["inputs"][0]["previous_output"] = {"tx_hash": parent, "index": "0x0"}
            # Reuse the existing packed raw-transaction hash helper. No node
            # has yet validated this child; actual sender RPC must match it.
            tx_hash = transaction_hash(tx)
            assert receiver.get_connected_count() == 0
            before = client.get_raw_tx_pool(False)
            preview = client.test_tx_pool_accept(tx, "passthrough")
            assert int(preview["cycles"], 16) == w.BUDGET_CYCLES
            assert int(preview["fee"], 16) == 100 * w.CKB
            assert client.get_raw_tx_pool(False) == before
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
            assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
            assert w.relay_slow(source, receiver, tx) == tx_hash
            assert client.get_transaction(parent)["tx_status"]["status"] == "pending"
            # Actual subsequent remote refusal shows the dry-run did not warm
            # a success cache that bypasses the cap.
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
            assert client.send_transaction(tx) == tx_hash
            w.accepted(receiver, tx_hash, tx, w.BUDGET_CYCLES)
            committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
            assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True

    # TEST-MAP: VT2-09
    # TEST-EVIDENCE-BEGIN: VT2-09
    # Evidence | Case: VT2-09 (covered)
    # Evidence | Setup: Slow transaction remotely refused under cap1, then locally admitted; full body/cycles saved; source peer disconnected.
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
    # Evidence | Assert: w.restart(receiver)
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
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-09
    def test_persisted_slow_transaction_reloads_without_network_budget(self):
        with w.nodes(12_000, 1) as (source, receiver):
            tx = w.transaction(source, slow=True)
            tx_hash = w.relay_slow(source, receiver, tx)
            client = receiver.getClient()
            assert client.send_transaction(tx) == tx_hash
            before = w.accepted(receiver, tx_hash, tx, w.BUDGET_CYCLES)
            client.remove_node(source.get_peer_id())
            w.wait_for(
                lambda: receiver.get_connected_count() == 0, "isolate persisted pool"
            )
            w.restart(receiver)
            assert receiver.get_connected_count() == 0
            after = w.accepted(receiver, tx_hash, tx, w.BUDGET_CYCLES)
            assert after["transaction"] == before["transaction"]
            assert after["cycles"] == before["cycles"]
            committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
            assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True
