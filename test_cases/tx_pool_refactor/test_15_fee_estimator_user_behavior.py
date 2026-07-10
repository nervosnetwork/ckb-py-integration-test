from framework.basic import CkbTest


class TestFeeEstimatorUserBehavior(CkbTest):
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

    def test_high_fee_transaction_keeps_fee_estimator_available(self):
        """
        PR #5248 user behavior: a high fee-rate transaction should not break
        WeightUnitsFlow fee estimation or normal tx-pool lifecycle.
        """
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

        high_fee = 10_000_000_000_000
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=high_fee,
            api_url=self.node.getClient().url,
        )
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")

        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert tx_hash in tx_pool["pending"]
        assert int(tx_pool["pending"][tx_hash]["fee"], 16) == high_fee

        fee_rate = self.node.getClient().estimate_fee_rate("high_priority", False)
        fee_rate_value = int(fee_rate, 16)
        assert 1000 <= fee_rate_value <= 2_000_000
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"

        tx_response = self.Miner.miner_until_tx_committed(self.node, tx_hash)
        assert tx_response["tx_status"]["status"] == "committed"
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert tx_hash not in tx_pool["pending"]
        assert tx_hash not in tx_pool["proposed"]
        self.did_pass = True
