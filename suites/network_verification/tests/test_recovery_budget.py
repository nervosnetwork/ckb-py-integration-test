"""Cold reorg recovery bypasses the network-only VM CPU cap."""

from framework.basic import CkbTest
from suites.network_verification.runtime import requires_target
from suites.network_verification import workload as w


@requires_target
class TestRecoveryBudget(CkbTest):
    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-10
    # TEST-EVIDENCE-BEGIN: VT2-10
    # Evidence | Case: VT2-10 (covered)
    # Evidence | Setup: Slow transaction first network-refused, then locally mined on old branch; separate valid fork excludes it and is two blocks longer; recovery cap1e9 exceeds transaction cycles.
    # Evidence | Trigger: Use workload.restart at cap1 and require verified old-PID disappearance/no listener followed by a distinct owned listening PID; then deliver the better fork as full blocks without a connected peer, observe detached transaction and re-mine.
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
    # Evidence | Limit: Coldness is established by old-PID disappearance and distinct owned new listener; no private VM/cache counter is claimed. lsof/OS process visibility is an explicit local prerequisite.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-10
    def test_detached_transaction_recovers_after_restart_under_one_ms_cap(self):
        with w.nodes(1, 12_000, 12_000) as (receiver, fork, source):
            client, other = receiver.getClient(), fork.getClient()
            tx = w.transaction(source, slow=True)
            tx_hash = w.relay_slow(source, receiver, tx)
            client.remove_node(source.get_peer_id())
            w.wait_for(
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
            w.restart(receiver)
            assert client.get_tip_header()["hash"] == old_tip
            assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}
            assert receiver.get_connected_count() == 0
            # Full blocks only: no peer can inject the detached transaction.
            w.sync_blocks(fork, receiver, 31, old_height + 2)
            assert client.get_tip_header()["hash"] == new_tip
            w.accepted(receiver, tx_hash, tx, w.BUDGET_CYCLES)
            assert receiver.get_connected_count() == 0
            committed = self.Miner.miner_until_tx_committed(receiver, tx_hash)
            assert committed["tx_status"]["status"] == "committed"
        self.did_pass = True
