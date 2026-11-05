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
        """
        1. 准备交易并核对初始 Pending 阶段。
        2. 在连续两个高度提案，核对引用计数及第一个提案到期后的阶段。
        3. 再次提案并推进窗口，核对 Gap、Proposed 和最终 Pending。
        """

        # 1. 准备交易并核对初始 Pending 阶段。
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

        # 2. 在连续两个高度提案，核对引用计数及第一个提案到期后的阶段。
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

        # 3. 再次提案并推进窗口，核对 Gap、Proposed 和最终 Pending。
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
