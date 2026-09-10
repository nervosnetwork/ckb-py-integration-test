from framework.basic import CkbTest


class TestFeeEstimatorUserBehavior(CkbTest):
    MAX_BUCKET_FEE_RATE = 2_000_000
    HIGH_FEE = 10_000_000_000_000

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5248_fee_estimator_user_behavior/node1",
            8424,
            8425,
        )
        cls.node.prepare(
            other_ckb_config={"ckb_fee_estimator_algorithm": "WeightUnitsFlow"}
        )
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def setup_method(self, method):
        self.did_pass = None
        self.node.getClient().clear_tx_pool()

    def _submit_high_fee_transaction(self):
        """Fund and submit a valid transaction whose fee rate exceeds the cap."""
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        fund_tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            360000,
            api_url=self.node.getClient().url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund_tx_hash)

        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=self.HIGH_FEE,
            api_url=self.node.getClient().url,
        )
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")

        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert tx_hash in tx_pool["pending"]
        entry = tx_pool["pending"][tx_hash]
        assert int(entry["fee"], 16) == self.HIGH_FEE

        # CKB weight is max(serialized size, cycles * 0.0001705714), so it is
        # no greater than max(size, cycles). This conservative lower bound is
        # enough to prove that the transaction reaches the capped code path.
        weight_upper_bound = max(int(entry["size"], 16), int(entry["cycles"], 16))
        fee_rate_lower_bound = self.HIGH_FEE * 1000 // weight_upper_bound
        assert fee_rate_lower_bound > self.MAX_BUCKET_FEE_RATE
        return tx_hash

    def _assert_bounded_estimate(self, estimate_mode):
        fee_rate = self.node.getClient().estimate_fee_rate(estimate_mode, False)
        fee_rate_value = int(fee_rate, 16)
        assert 1000 <= fee_rate_value <= self.MAX_BUCKET_FEE_RATE
        return fee_rate_value

    def test_high_fee_transaction_keeps_fee_estimator_available(self):
        """
        TP-INT-TXPOOL-FEE-5248-001 [P0]

        PR #5248 user behavior: a high fee-rate transaction should not break
        WeightUnitsFlow fee estimation or normal tx-pool lifecycle.
        """
        tx_hash = self._submit_high_fee_transaction()
        self._assert_bounded_estimate("high_priority")
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"

        tx_response = self.Miner.miner_until_tx_committed(self.node, tx_hash)
        assert tx_response["tx_status"]["status"] == "committed"
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert tx_hash not in tx_pool["pending"]
        assert tx_hash not in tx_pool["proposed"]
        self.did_pass = True

    def test_repeated_high_priority_estimates_remain_bounded_and_read_only(self):
        """
        TP-INT-TXPOOL-FEE-5248-002 [P1]

        Repeated estimator calls must stay bounded and deterministic without
        changing the chain or evicting the high-fee transaction.
        """
        tx_hash = self._submit_high_fee_transaction()
        tip_hash = self.node.getClient().get_tip_header()["hash"]

        estimates = [self._assert_bounded_estimate("high_priority") for _ in range(3)]
        assert len(set(estimates)) == 1

        assert self.node.getClient().get_tip_header()["hash"] == tip_hash
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert tx_hash in tx_pool["pending"]
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
        self.did_pass = True
