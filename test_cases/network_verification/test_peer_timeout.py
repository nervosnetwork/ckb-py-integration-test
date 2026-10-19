"""Real node relay: retryable budget refusal and subsequent peer usefulness."""

from framework.basic import CkbTest
from test_cases.network_verification.runtime import requires_target
from test_cases.network_verification import workload as w


@requires_target
class TestRemoteTimeBudgetPeer(CkbTest):
    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-01
    # TEST-MAP: VT2-02
    # TEST-EVIDENCE-BEGIN: VT2-01
    # Evidence | Case: VT2-01 (covered)
    # Evidence | Setup: Fresh cap=1 receiver on common 30-block dev chain; independent fast genesis input cell=1 has never been validated by receiver.
    # Evidence | Trigger: Original slow sender sends a distinct fast transaction through ordinary real-node relay after the slow refusal/re-request.
    # Evidence | Observe: Receiver pending/proposed with exact full body and fixed537 cycles in both RPC and verbose pool; original source peer remains connected.
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Assert: assert any(
    # Evidence |                 peer["node_id"] == source.get_peer_id()
    # Evidence |                 for peer in receiver.getClient().get_peers()
    # Evidence |             )
    # Evidence | Assert: assert receiver.getClient().get_banned_addresses() == []
    # Evidence | Limit: Honest node relay supplies truthful cycles; no malformed packet or exact CPU-time threshold is claimed.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-01
    # TEST-EVIDENCE-BEGIN: VT2-02
    # Evidence | Case: VT2-02 (covered)
    # Evidence | Setup: Valid64-group slow transaction at fixed135598681 cycles; below1e9 configured verification cycles and1e10 block cycles. Fresh low-cap receiver.
    # Evidence | Trigger: Original source locally admits and relays slow bytes; independent ordinary peer reannounces same hash/bytes; original source subsequently relays independent fast input.
    # Evidence | Observe: Hash-specific CPU-budget log, increased real GetRelayTransactions count, repeated unknown/nonmember/no-ban state, and successful original-peer fast admission.
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
    # Evidence | Assert: assert refusals >= 1
    # Evidence | Assert: assert source.getClient().send_transaction(tx) == tx_hash
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(transaction_requests(source, tx_hash)) > requests_before,
    # Evidence |         f"fresh GetRelayTransactions for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Assert: assert any(
    # Evidence |                 peer["node_id"] == source.get_peer_id()
    # Evidence |                 for peer in receiver.getClient().get_peers()
    # Evidence |             )
    # Evidence | Assert: assert receiver.getClient().get_banned_addresses() == []
    # Evidence | Assert: assert (
    # Evidence |                 receiver.getClient().get_transaction(tx_hash)["tx_status"]["status"]
    # Evidence |                 == "unknown"
    # Evidence |             )
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Limit: Request proof comes from sender protocol log, not custom packet capture. Reannouncement uses another peer; fast control uses original sender.
    # Evidence | Limit: Second refusal log precedes some cleanup; the new request already proves release following the first refusal.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-02
    def test_remote_timeout_keeps_peer_connected_for_next_valid_transaction(self):
        with w.nodes(12_000, 1, 12_000) as (source, receiver, other):
            slow = w.transaction(source, slow=True)
            tx_hash = w.relay_slow(source, receiver, slow)
            w.retry_slow(other, receiver, slow, tx_hash)
            # This new hash uses an independent input and the original peer.
            fast = w.transaction(source, cell=1)
            fast_hash = source.getClient().send_transaction(fast)
            w.accepted(receiver, fast_hash, fast, w.FAST_CYCLES)
            assert any(
                peer["node_id"] == source.get_peer_id()
                for peer in receiver.getClient().get_peers()
            )
            assert receiver.getClient().get_banned_addresses() == []
            assert (
                receiver.getClient().get_transaction(tx_hash)["tx_status"]["status"]
                == "unknown"
            )
        self.did_pass = True
