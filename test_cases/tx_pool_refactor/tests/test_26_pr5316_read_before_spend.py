"""PR #5316: a cell-dep reader must be packed before its spender."""

import copy
import time

from framework.basic import CkbTest


class TestReadBeforeSpend(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_read_before_spend/node1",
            8498,
            8499,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # Keep the pytest log; CkbTest's report copier uses a shared path.
        pass

    # TEST-MAP: NTD-28
    def test_reader_and_its_parent_precede_spender_in_valid_block(self):
        client = self.node.getClient()
        private_key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(private_key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            private_key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            private_key,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        # P -> A is an input dependency. A reads X as a cell dep, while B
        # spends X. The read-before-spend edge A -> B is not fee ancestry.
        parent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            private_key,
            fee=1000,
            api_url=client.url,
        )
        reader = self.Tx.send_transfer_self_tx_with_input(
            [parent],
            ["0x0"],
            private_key,
            dep_cells=[{"tx_hash": split, "index_hex": "0x1"}],
            fee=1000,
            api_url=client.url,
        )
        spender = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            private_key,
            fee=100_000,
            api_url=client.url,
        )
        expected = {parent, reader, spender}
        raw = client.get_raw_tx_pool(True)
        entries = {**raw["pending"], **raw["proposed"]}
        assert set(entries) == expected
        assert int(entries[reader]["ancestors_count"], 16) == 2
        assert int(entries[spender]["ancestors_count"], 16) == 1

        short_ids = {tx_hash[:22] for tx_hash in expected}
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if short_ids <= set(client.get_block_template()["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("read-before-spend transactions were not proposed")

        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        tip_hash = client.get_tip_header()["hash"]
        for _ in range(3):
            # Repeated public reads can return one current template; they
            # check visible ordering, not the internal liveness memo.
            template = client.get_block_template()
            assert template["parent_hash"] == tip_hash
            selected = [tx["hash"] for tx in template["transactions"]]
            assert set(selected) == expected
            assert (
                selected.index(parent)
                < selected.index(reader)
                < selected.index(spender)
            )

        block = self.Miner.block_template_transfer_to_submit_block(
            copy.deepcopy(template)
        )
        block_hash = client.submit_block(template["work_id"], block)
        assert block_hash.startswith("0x")
        for tx_hash in expected:
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "committed"
        next_template = client.get_block_template()
        assert next_template["parent_hash"] == block_hash
        assert expected.isdisjoint(tx["hash"] for tx in next_template["transactions"])
        self.did_pass = True

    # TEST-MAP: NTD-32
    def test_same_count_rbf_and_phase_change_match_cold_graph_pack(self):
        client = self.node.getClient()
        client.clear_tx_pool()
        key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        first_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        second_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=1_000_000,
            api_url=client.url,
        )
        first = client.send_transaction(first_tx)
        second = client.send_transaction(second_tx)
        assert first != second
        common_tip = client.get_tip_header()["hash"]
        old_ids = {first[:22], second[:22]}
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            template = client.get_block_template()
            if old_ids <= set(template["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("first two owners did not enter template proposals")
        old_count = len(client.get_raw_tx_pool(False)["pending"])
        assert old_count == 2

        replacement_fee = max(
            int(client.get_transaction(second)["min_replace_fee"], 16) + 1000,
            2_000_000,
        )
        replacement_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            dep_cells=[{"tx_hash": first, "index_hex": "0x0"}],
            fee=replacement_fee,
            api_url=client.url,
        )
        replacement = client.send_transaction(replacement_tx)
        assert replacement not in {first, second}
        assert client.get_tip_header()["hash"] == common_tip
        pool = client.get_raw_tx_pool(False)
        assert set(pool["pending"]) == {first, replacement}
        assert pool["proposed"] == []
        assert len(pool["pending"]) == old_count
        assert client.get_transaction(second)["tx_status"]["status"] == "rejected"
        details = client.get_raw_tx_pool(True)["pending"]
        assert int(details[replacement]["fee"], 16) > int(details[first]["fee"], 16)
        assert int(details[first]["ancestors_count"], 16) == 1
        assert int(details[replacement]["ancestors_count"], 16) == 2
        assert any(
            dep["out_point"] == {"tx_hash": first, "index": "0x0"}
            for dep in replacement_tx["cell_deps"]
        )
        current_ids = {first[:22], replacement[:22]}
        assert len(current_ids) == 2 and second[:22] not in current_ids
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            refreshed = client.get_block_template()
            if current_ids <= set(refreshed["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError(
                "same-count owner replacement did not refresh proposals"
            )
        assert second[:22] not in refreshed["proposals"]
        assert refreshed["parent_hash"] == common_tip

        # Same owner pair crosses Pending -> Gap -> Proposed while the graph's
        # causal population remains unchanged. Pack must use current phase.
        for proposal_ids in (sorted(current_ids), []):
            template = client.get_block_template()
            template["transactions"] = []
            template["proposals"] = proposal_ids
            template["uncles"] = []
            block_hash = client.generate_block_with_template(template)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if client.tx_pool_info()["tip_hash"] == block_hash:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("pool did not follow proposal-phase block")
            if proposal_ids:
                for tx_hash in (first, replacement):
                    assert (
                        client.get_pool_tx_detail_info(tx_hash)["entry_status"] == "gap"
                    )
                gap_template = client.get_block_template()
                assert {first, replacement}.isdisjoint(
                    tx["hash"] for tx in gap_template["transactions"]
                )
        for tx_hash in (first, replacement):
            assert client.get_pool_tx_detail_info(tx_hash)["entry_status"] == "proposed"
        warm_template = client.get_block_template()
        warm_selected = [item["hash"] for item in warm_template["transactions"]]
        assert warm_selected == [first, replacement]

        # A fresh second node sees the identical chain and signed transactions
        # but has never compiled the warm node's graph cache.
        peer = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_read_before_spend/cold_peer",
            8590,
            8591,
        )
        peer.prepare()
        peer.start()
        try:
            cold = peer.getClient()
            assert cold.get_block_hash("0x0") == client.get_block_hash("0x0")
            for height in range(1, client.get_tip_block_number() + 1):
                block = client.get_block_by_number(hex(height))
                block["header"].pop("hash", None)
                for tx in block["transactions"]:
                    tx.pop("hash", None)
                for uncle in block["uncles"]:
                    uncle["header"].pop("hash", None)
                cold.submit_block("0x0", block)
            warm_tip = client.get_tip_header()["hash"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (
                    cold.get_tip_header()["hash"] == warm_tip
                    and cold.tx_pool_info()["tip_hash"] == warm_tip
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("cold peer did not install warm chain")
            for tx_hash in (first, replacement):
                tx = dict(client.get_transaction(tx_hash)["transaction"])
                tx.pop("hash")
                assert cold.send_transaction(tx) == tx_hash
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                cold_template = cold.get_block_template()
                cold_selected = [item["hash"] for item in cold_template["transactions"]]
                if set(cold_selected) == {first, replacement}:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("cold peer did not pack both accepted owners")
            assert cold_selected == warm_selected
            assert cold_template["parent_hash"] == warm_template["parent_hash"]
        finally:
            peer.stop()
            peer.clean()

        block = self.Miner.block_template_transfer_to_submit_block(
            copy.deepcopy(warm_template)
        )
        committed_hash = client.submit_block(warm_template["work_id"], block)
        assert [
            tx["hash"] for tx in client.get_block(committed_hash)["transactions"][1:]
        ] == warm_selected
        self.did_pass = True
