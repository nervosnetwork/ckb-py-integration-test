"""Network pool time limits must not reject a valid full block."""

from framework.basic import CkbTest
from test_cases.network_verification.runtime import requires_target


@requires_target
class TestBudgetConsensusIsolation(CkbTest):
    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-08
    # TEST-EVIDENCE-BEGIN: VT2-08
    # Evidence | Case: VT2-08 (covered)
    # Evidence | Setup: Independent source admits slow transaction; low-cap receiver sees remote refusal only, never local successful validation of target.
    # Evidence | Trigger: Disconnect receiver; mine target on source; deliver full blocks through ordinary submit_block, not process_block_without_verify.
    # Evidence | Observe: All block hashes match, canonical tips converge, and committed transaction body/block hash equals independently mined source.
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
    # Evidence | Assert: assert receiver.submit_block("0x0", block) == expected_hash
    # Evidence | Assert: raise AssertionError(f"block receiver did not reach {expected_tip}")
    # Evidence | Assert: assert observed["tx_status"]["status"] == "committed"
    # Evidence | Assert: assert observed["transaction"] == committed["transaction"]
    # Evidence | Assert: assert (
    # Evidence |                 observed["tx_status"]["block_hash"]
    # Evidence |                 == committed["tx_status"]["block_hash"]
    # Evidence |             )
    # Evidence | Assert: assert (
    # Evidence |                 receiver.getClient().get_tip_header()["hash"]
    # Evidence |                 == miner.getClient().get_tip_header()["hash"]
    # Evidence |             )
    # Evidence | Limit: Full-block RPC instead of P2P block transport, with ordinary consensus validation still enabled at receiver.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-08
    def test_pool_timeout_does_not_reject_independently_mined_valid_block(self):
        from test_cases.network_verification import workload as w

        with w.nodes(12_000, 1) as (miner, receiver):
            tx = w.transaction(miner, slow=True)
            tx_hash = w.relay_slow(miner, receiver, tx)
            receiver.getClient().remove_node(miner.get_peer_id())
            w.wait_for(
                lambda: receiver.get_connected_count() == 0,
                "isolate full-block receiver",
            )
            before = miner.getClient().get_tip_block_number()
            committed = self.Miner.miner_until_tx_committed(miner, tx_hash)
            w.sync_blocks(
                miner, receiver, before + 1, miner.getClient().get_tip_block_number()
            )
            observed = receiver.getClient().get_transaction(tx_hash)
            assert observed["tx_status"]["status"] == "committed"
            assert observed["transaction"] == committed["transaction"]
            assert (
                observed["tx_status"]["block_hash"]
                == committed["tx_status"]["block_hash"]
            )
            assert (
                receiver.getClient().get_tip_header()["hash"]
                == miner.getClient().get_tip_header()["hash"]
            )
        self.did_pass = True
