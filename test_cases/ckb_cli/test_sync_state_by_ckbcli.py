import pytest

from framework.basic import CkbTest


class TestCkbCliRpc117(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. start 1 ckb node in tmp/ckb_cli/node dir
        2. miner 20 block
        Returns:

        """
        # 1. start 1 ckb node in tmp/ckb_cli/node dir
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_cli/node", 8314, 8315
        )
        cls.node.prepare()
        cls.node.start()
        # 2. miner 20 block
        cls.Miner.make_tip_height_number(cls.node, 20)

    @classmethod
    def teardown_class(cls):
        """
        1. stop ckb node
        2. clean ckb node  tmp dir
        Returns:

        """
        print("stop node and clean")
        cls.node.stop()
        cls.node.clean()

    def test_01_sync_state(self):
        """
        #1.The assume_valid_target specified by ckb, if no assume_valid_target, this will be all zero.
        #2.If no assume_valid_target, this will be true.
        #3. min_chain_work on dev chain = 0x0
        #4. min_chain_work_reached on dev chain is 0x0,so will be reached return true
        Returns:

        """
        self.Ckb_cli.version()
        sync_state = self.Ckb_cli.sync_state(api_url=self.node.getClient().url)
        # 1.The assume_valid_target specified by ckb, if no assume_valid_target, this will be all zero.
        assert (
            sync_state["assume_valid_target"]
            == "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
        # 2.If no assume_valid_target, this will be true.
        assert sync_state["assume_valid_target_reached"] is True
        # 3. min_chain_work on dev chain = 0x0
        assert sync_state["min_chain_work"] == "0x0"
        # 4. min_chain_work_reached on dev chain is 0x0,so will be reached return true
        assert sync_state["min_chain_work_reached"] is True

    def test_02_remove_orphan_blocks_size(self):
        """
        #1. remove orphan_blocks_size
        Returns:

        """
        # 1. remove orphan_blocks_size
        self.Ckb_cli.version()
        sync_state = self.Ckb_cli.sync_state(api_url=self.node.getClient().url)
        assert "orphan_blocks_size" not in sync_state


from suites.network_verification.runtime import requires_target


@requires_target
class TestSyncProgress(CkbTest):
    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-39
    # TEST-EVIDENCE-BEGIN: VT2-39
    # Evidence | Case: VT2-39 (covered)
    # Evidence | Setup: Three actual nodes share common chain. Only bad source installs DAO-zero block through test-only bypass; receiver has normal block synchronization.
    # Evidence | Trigger: Deliver bad block over peer, wait for ban and exact InvalidDAO hash; disconnect/clear loopback ban; healthy separate source supplies valid extension and stops mining.
    # Evidence | Observe: Bad height31 absent/common tip unchanged, valid canonical/pool tip convergence, three stable samples with unverified number>=tip and known header at claimed height.
    # Evidence | Assert: assert invalid_hash and bad_rpc.get_tip_header()["hash"] == invalid_hash
    # Evidence | Assert: w.wait_for(
    # Evidence |                 lambda: client.get_banned_addresses(), "invalid body source banned", 45
    # Evidence |             )
    # Evidence | Assert: assert client.get_tip_header()["hash"] == common
    # Evidence | Assert: assert client.get_block_by_number("0x1f") is None
    # Evidence | Assert: assert any(
    # Evidence |                 invalid_hash in line and "InvalidDAO" in line
    # Evidence |                 for line in log.splitlines()
    # Evidence |             )
    # Evidence | Assert: w.wait_for(
    # Evidence |                 lambda: client.get_tip_header()["hash"] == expected_tip
    # Evidence |                 and client.tx_pool_info()["tip_hash"] == expected_tip,
    # Evidence |                 "valid tip/pool convergence",
    # Evidence |                 45,
    # Evidence |             )
    # Evidence | Assert: assert expected_tip != invalid_hash
    # Evidence | Assert: assert tip["hash"] == expected_tip
    # Evidence | Assert: assert int(state["unverified_tip_number"], 16) >= int(
    # Evidence |                     tip["number"], 16
    # Evidence |                 ), state
    # Evidence | Assert: assert (
    # Evidence |                     header is not None
    # Evidence |                     and header["number"] == state["unverified_tip_number"]
    # Evidence |                 ), (state, header)
    # Evidence | Limit: Sequential public progress regression, not deterministic private callback/reset race.
    # Evidence | Limit: Three stable samples span finite0.2s; no continued source mining after convergence.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-39
    def test_invalid_block_then_valid_sync_preserves_progress_floor(self):
        import copy
        import time
        from suites.network_verification import workload as w

        with w.nodes(12_000, 12_000, 12_000) as (bad, receiver, healthy):
            bad_rpc, client = bad.getClient(), receiver.getClient()
            common = bad_rpc.get_tip_header()["hash"]
            self.Miner.make_tip_height_number(bad, 31)
            invalid = copy.deepcopy(bad_rpc.get_block_by_number("0x1f"))
            invalid["header"].pop("hash")
            for transaction in invalid["transactions"]:
                transaction.pop("hash", None)
            # All header/body roots remain correct. DAO is checked during full
            # block verification, not by the header synchronization precheck.
            invalid["header"]["dao"] = "0x" + "00" * 32
            bad_rpc.truncate(common)
            invalid_hash = bad_rpc.call(
                "process_block_without_verify", [invalid, False]
            )
            assert invalid_hash and bad_rpc.get_tip_header()["hash"] == invalid_hash
            # The invalid block may cause a ban before a connection poll sees
            # this peer. The subsequent ban AND exact InvalidDAO hash prove
            # delivery; do not require an observable intermediate connection.
            bad.connected(receiver)
            w.wait_for(
                lambda: client.get_banned_addresses(), "invalid body source banned", 45
            )
            assert client.get_tip_header()["hash"] == common
            assert client.get_block_by_number("0x1f") is None
            from pathlib import Path

            log = (Path(receiver.ckb_dir) / "node.log").read_text(errors="replace")
            assert any(
                invalid_hash in line and "InvalidDAO" in line
                for line in log.splitlines()
            )
            # Both fixtures use loopback. Disconnect the bad source, then clear
            # its IP ban so it does not also block the independent healthy node.
            client.remove_node(bad.get_peer_id())
            for ban in client.get_banned_addresses():
                client.set_ban(ban["address"], "delete", None, None, None)
            # A distinct healthy source resumes ordinary synchronization.
            self.Miner.make_tip_height_number(healthy, 32)
            expected_tip = healthy.getClient().get_tip_header()["hash"]
            w.connect(healthy, receiver)
            w.wait_for(
                lambda: client.get_tip_header()["hash"] == expected_tip
                and client.tx_pool_info()["tip_hash"] == expected_tip,
                "valid tip/pool convergence",
                45,
            )
            assert expected_tip != invalid_hash
            for _ in range(3):
                tip = client.get_tip_header()
                state = client.call("sync_state", [])
                assert tip["hash"] == expected_tip
                assert int(state["unverified_tip_number"], 16) >= int(
                    tip["number"], 16
                ), state
                header = client.call("get_header", [state["unverified_tip_hash"]])
                assert (
                    header is not None
                    and header["number"] == state["unverified_tip_number"]
                ), (state, header)
                time.sleep(0.1)
        self.did_pass = True
