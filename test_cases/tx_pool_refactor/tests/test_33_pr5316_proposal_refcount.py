"""PR #5316: repeated proposal heights retain the public pool phase."""

import time

from framework.basic import CkbTest


class TestProposalRefcount(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_proposal_refcount/node1",
            8532,
            8533,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # Preserve the pytest log instead of the base class's shared report copy.
        pass

    def _mine_without_committing(self, proposal_ids):
        client = self.node.getClient()
        template = client.get_block_template()
        height = int(template["number"], 16)
        template["transactions"] = []
        template["proposals"] = list(proposal_ids)
        template["uncles"] = []
        block_hash = client.generate_block_with_template(template)
        assert client.get_block_hash(hex(height)) == block_hash
        block = client.get_block_by_number(hex(height))
        assert block["proposals"] == list(proposal_ids)
        assert len(block["transactions"]) == 1  # Cellbase only; target stays in pool.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if int(client.tx_pool_info()["tip_number"], 16) == height:
                return height
            time.sleep(0.1)
        raise AssertionError(f"tx-pool did not reach proposal block {height}")

    def _assert_phase(self, tx_hash, detail_status, public_status):
        client = self.node.getClient()
        detail = client.get_pool_tx_detail_info(tx_hash)
        public = client.get_transaction(tx_hash)
        raw = client.get_raw_tx_pool(False)
        assert detail["entry_status"] == detail_status
        assert public["tx_status"]["status"] == public_status
        assert public["transaction"]["hash"] == tx_hash
        expected_bucket = "proposed" if detail_status == "proposed" else "pending"
        other_bucket = "pending" if expected_bucket == "proposed" else "proposed"
        assert tx_hash in raw[expected_bucket]
        assert tx_hash not in raw[other_bucket]

    def _assert_height_oracle(self, tx_hash, proposal_heights, closest, farthest):
        next_height = self.node.getClient().get_tip_block_number() + 1
        ages = [next_height - height for height in proposal_heights]
        if any(closest <= age <= farthest for age in ages):
            self._assert_phase(tx_hash, "proposed", "proposed")
        elif any(0 < age < closest for age in ages):
            self._assert_phase(tx_hash, "gap", "pending")
        else:
            self._assert_phase(tx_hash, "pending", "pending")

    # TEST-MAP: NTD-24
    def test_second_proposal_survives_first_expiry_then_final_expiry_is_pending(self):
        client = self.node.getClient()
        window = client.get_consensus()["tx_proposal_window"]
        closest = int(window["closest"], 16)
        farthest = int(window["farthest"], 16)
        assert 1 < closest < farthest

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
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        short_id = tx_hash[:22]
        self._assert_phase(tx_hash, "pending", "pending")

        first = self._mine_without_committing([short_id])
        self._assert_height_oracle(tx_hash, [first], closest, farthest)
        second = self._mine_without_committing([short_id])
        assert second == first + 1
        self._assert_height_oracle(tx_hash, [first, second], closest, farthest)

        # The next-block eligibility window is [tip+1-farthest,
        # tip+1-closest]. At first expiry, only the later height survives.
        while client.get_tip_block_number() < first + farthest:
            self._mine_without_committing([])
            self._assert_height_oracle(tx_hash, [first, second], closest, farthest)
        assert client.get_tip_block_number() == first + farthest
        self._assert_phase(tx_hash, "proposed", "proposed")

        # At the next tip the second proposal expires, while a fresh proposal
        # at that height is only Gap. Proposed must not mask that Gap.
        third = self._mine_without_committing([short_id])
        assert third == second + farthest
        self._assert_height_oracle(tx_hash, [first, second, third], closest, farthest)
        self._assert_phase(tx_hash, "gap", "pending")

        self._mine_without_committing([])
        self._assert_height_oracle(tx_hash, [first, second, third], closest, farthest)
        self._assert_phase(tx_hash, "proposed", "proposed")
        while client.get_tip_block_number() < third + farthest:
            self._mine_without_committing([])
            self._assert_height_oracle(
                tx_hash, [first, second, third], closest, farthest
            )
        self._assert_phase(tx_hash, "pending", "pending")
        print(
            f"proposal heights={first},{second},{third}; "
            f"first-expiry tip={first + farthest}; "
            f"gap-tail tip={third}; final-pending tip={third + farthest}"
        )
        self.did_pass = True

    # TEST-MAP: NTD-25
    def test_replacing_a_proposal_height_rebuilds_public_phases(self):
        client = self.node.getClient()
        client.clear_tx_pool()
        window = client.get_consensus()["tx_proposal_window"]
        closest = int(window["closest"], 16)
        farthest = int(window["farthest"], 16)
        assert 1 < closest and closest + 2 <= farthest

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
        common_tip = client.get_tip_header()["hash"]
        common_height = client.get_tip_block_number()

        peer = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_proposal_refcount/fork_peer",
            8560,
            8561,
        )
        peer.prepare()
        peer.start()
        try:
            peer_client = peer.getClient()
            assert client.get_block_hash("0x0") == peer_client.get_block_hash("0x0")
            for height in range(1, common_height + 1):
                block = client.get_block_by_number(hex(height))
                block["header"].pop("hash", None)
                for tx in block["transactions"]:
                    tx.pop("hash", None)
                for uncle in block["uncles"]:
                    uncle["header"].pop("hash", None)
                peer_client.submit_block("0x0", block)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (
                    peer_client.get_tip_header()["hash"] == common_tip
                    and peer_client.tx_pool_info()["tip_hash"] == common_tip
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("peer did not install the common chain")

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
                fee=1000,
                api_url=client.url,
            )
            first = client.send_transaction(first_tx)
            second = client.send_transaction(second_tx)
            first_id, second_id = first[:22], second[:22]
            assert first_id != second_id

            old_height = self._mine_without_committing([first_id])
            old_hash = client.get_tip_header()["hash"]
            assert old_height == common_height + 1
            self._assert_height_oracle(first, [old_height], closest, farthest)
            self._assert_phase(second, "pending", "pending")
            for _ in range(closest):
                self._mine_without_committing([])
            assert client.get_tip_block_number() == old_height + closest
            self._assert_phase(first, "proposed", "proposed")
            self._assert_phase(second, "pending", "pending")

            # The competing fork replaces the old height's proposal and moves
            # first's proposal to its new tip. The longer fork must change
            # first from Proposed to Gap without clearing or resubmitting it.
            peer_proposals = [[second_id]] + [[] for _ in range(closest)] + [[first_id]]
            for proposal_ids in peer_proposals:
                template = peer_client.get_block_template()
                template["transactions"] = []
                template["proposals"] = proposal_ids
                template["uncles"] = []
                peer_client.generate_block_with_template(template)
            replacement_hash = peer_client.get_block_hash(hex(old_height))
            winning_hash = peer_client.get_tip_header()["hash"]
            assert replacement_hash != old_hash
            assert peer_client.get_tip_block_number() == old_height + closest + 1
            assert peer_client.get_block_by_number(hex(old_height))["proposals"] == [
                second_id
            ]
            assert peer_client.get_block_by_number(hex(old_height + closest + 1))[
                "proposals"
            ] == [first_id]

            for height in range(old_height, old_height + closest + 2):
                block = peer_client.get_block_by_number(hex(height))
                block["header"].pop("hash", None)
                for tx in block["transactions"]:
                    tx.pop("hash", None)
                for uncle in block["uncles"]:
                    uncle["header"].pop("hash", None)
                client.submit_block("0x0", block)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (
                    client.get_tip_header()["hash"] == winning_hash
                    and client.tx_pool_info()["tip_hash"] == winning_hash
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("local node did not adopt the longer fork")

            assert client.get_block_hash(hex(old_height)) == replacement_hash
            assert client.get_block_hash(hex(old_height + closest + 1)) == winning_hash
            assert client.get_block_by_number(hex(old_height))["proposals"] == [
                second_id
            ]
            assert client.get_block_by_number(hex(old_height + closest + 1))[
                "proposals"
            ] == [first_id]
            self._assert_height_oracle(
                first,
                [old_height + closest + 1],
                closest,
                farthest,
            )
            self._assert_phase(first, "gap", "pending")
            self._assert_height_oracle(second, [old_height], closest, farthest)
            self._assert_phase(second, "proposed", "proposed")
            self.did_pass = True
        finally:
            peer.stop()
            peer.clean()

    # TEST-MAP: NTD-25
    def test_reorg_removes_old_proposal_without_replacement(self):
        client = self.node.getClient()
        client.clear_tx_pool()
        window = client.get_consensus()["tx_proposal_window"]
        closest = int(window["closest"], 16)
        farthest = int(window["farthest"], 16)
        assert 1 < closest and closest + 2 <= farthest

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
        target = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        self._assert_phase(target, "pending", "pending")
        common_tip = client.get_tip_header()["hash"]
        common_height = client.get_tip_block_number()

        peer = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_proposal_refcount/remove_peer",
            8562,
            8563,
        )
        peer.prepare()
        peer.start()
        try:
            peer_client = peer.getClient()
            assert client.get_block_hash("0x0") == peer_client.get_block_hash("0x0")
            for height in range(1, common_height + 1):
                block = client.get_block_by_number(hex(height))
                block["header"].pop("hash", None)
                for tx in block["transactions"]:
                    tx.pop("hash", None)
                for uncle in block["uncles"]:
                    uncle["header"].pop("hash", None)
                peer_client.submit_block("0x0", block)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (
                    peer_client.get_tip_header()["hash"] == common_tip
                    and peer_client.tx_pool_info()["tip_hash"] == common_tip
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("remove peer did not install the common chain")

            proposal_height = self._mine_without_committing([target[:22]])
            assert proposal_height == common_height + 1
            old_proposal_hash = client.get_block_hash(hex(proposal_height))
            for _ in range(closest):
                self._mine_without_committing([])
            self._assert_height_oracle(
                target,
                [proposal_height],
                closest,
                farthest,
            )
            self._assert_phase(target, "proposed", "proposed")
            old_tip = client.get_tip_header()["hash"]

            # This fork has no occurrence of the target proposal. Its extra
            # block forces removal of the old canonical proposal height.
            for _ in range(closest + 2):
                template = peer_client.get_block_template()
                template["transactions"] = []
                template["proposals"] = []
                template["uncles"] = []
                peer_client.generate_block_with_template(template)
            winning_tip = peer_client.get_tip_header()["hash"]
            assert winning_tip != old_tip
            assert peer_client.get_tip_block_number() == proposal_height + closest + 1
            for height in range(proposal_height, proposal_height + closest + 2):
                block = peer_client.get_block_by_number(hex(height))
                assert block["proposals"] == []
                block["header"].pop("hash", None)
                for tx in block["transactions"]:
                    tx.pop("hash", None)
                for uncle in block["uncles"]:
                    uncle["header"].pop("hash", None)
                client.submit_block("0x0", block)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (
                    client.get_tip_header()["hash"] == winning_tip
                    and client.tx_pool_info()["tip_hash"] == winning_tip
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("local node did not adopt the empty-proposal fork")

            assert client.get_block_hash(hex(proposal_height)) != old_proposal_hash
            assert client.get_block_by_number(hex(proposal_height))["proposals"] == []
            self._assert_height_oracle(target, [], closest, farthest)
            self._assert_phase(target, "pending", "pending")
            print(
                f"removed proposal height={proposal_height}; "
                f"old_tip={old_tip}; winning_tip={winning_tip}"
            )
            self.did_pass = True
        finally:
            peer.stop()
            peer.clean()
