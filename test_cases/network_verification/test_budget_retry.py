"""A network time-budget refusal must permit later verification."""

from framework.basic import CkbTest
from test_cases.network_verification.runtime import requires_target


@requires_target
class TestTimeBudgetRetry(CkbTest):
    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-33
    # TEST-EVIDENCE-BEGIN: VT2-33
    # Evidence | Case: VT2-33 (covered)
    # Evidence | Setup: Valid fixed-cycle slow transaction; initial low-cap receiver has real hash-specific refusal and unknown/no-ban state.
    # Evidence | Trigger: Use workload.restart(cap=12000): old owned PID must disappear before updating cap; a distinct owned listener is required before identical transaction bytes are relayed through another ordinary peer.
    # Evidence | Observe: At1ms refused; at12000ms same hash/body/cycles admitted over network with no ban.
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
    # Evidence | Assert: assert other.getClient().send_transaction(tx) == tx_hash
    # Evidence | Assert: assert receiver.getClient().get_banned_addresses() == []
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Assert: w.restart(receiver, cap=12_000)
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
    # Evidence | Limit: Both selector methods must run; neither alone completes the Case.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # Evidence | Limit: The budget-change control now uses the same verified cold process boundary as persistence/reorg tests.
    # TEST-EVIDENCE-END: VT2-33
    def test_network_budget_accepts_same_transaction_after_limit_increase(self):
        from test_cases.network_verification import workload as w

        with w.nodes(12_000, 1, 12_000) as (source, receiver, other):
            tx = w.transaction(source, slow=True)
            tx_hash = w.relay_slow(source, receiver, tx)
            w.restart(receiver, cap=12_000)
            w.connect(other, receiver)
            assert other.getClient().send_transaction(tx) == tx_hash
            w.accepted(receiver, tx_hash, tx, w.BUDGET_CYCLES)
            assert receiver.getClient().get_banned_addresses() == []
        self.did_pass = True
