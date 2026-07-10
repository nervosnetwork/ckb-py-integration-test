import time

from framework.basic import CkbTest


class TestVerifyQueueUserBehavior(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5237_5238_user_behavior/node1",
            8414,
            8415,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def setup_method(self, method):
        self.did_pass = None
        self.node.getClient().clear_tx_pool()

    def test_transaction_reaches_proposed_and_committed_after_block_verification(self):
        """
        PR #5237/#5238 user behavior: sending a normal transaction, proposing it,
        and verifying new blocks should not leave the transaction or verify queue stuck.
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2
        )
        fund_tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            360000,
            api_url=self.node.getClient().url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund_tx_hash)

        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            output_count=1,
            fee=1000,
            api_url=self.node.getClient().url,
        )
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"

        tx_short_id = tx_hash[:22]
        for _ in range(20):
            block_template = self.node.getClient().get_block_template()
            if tx_short_id in block_template["proposals"]:
                break
            time.sleep(1)
        assert tx_short_id in block_template["proposals"]

        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        self.Node.wait_get_transaction(self.node, tx_hash, "proposed")
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert tx_hash in tx_pool["proposed"]
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"

        tx_response = self.Miner.miner_until_tx_committed(self.node, tx_hash)
        assert tx_response["tx_status"]["status"] == "committed"
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert tx_hash not in tx_pool["pending"]
        assert tx_hash not in tx_pool["proposed"]
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
        self.did_pass = True
