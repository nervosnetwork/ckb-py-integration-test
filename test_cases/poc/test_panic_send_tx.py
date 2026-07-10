from framework.basic import CkbTest


class PanicSendTx(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "poc/PanicSendTx/node1",
            8114,
            8225,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 200)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def setup_method(self, method):
        self.node.getClient().clear_tx_pool()

    def test_send_tx_with_since_0x40ff_ffff_ffff_ffff(self):
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        father_tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            100000,
            self.node.getClient().url,
            "1500000",
        )
        for i in range(5):
            tx = self.Tx.build_send_transfer_self_tx_with_input(
                [father_tx_hash],
                ["0x0"],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=15000 + i,
                api_url=self.node.getClient().url,
            )
            tx["inputs"][0]["since"] = "0x40ffffffffffffff"

            # The PoC focuses on constructing/sending this `since` value; both
            # accepted and rejected responses are valid as long as there is no panic trace.
            try:
                tx_hash = self.node.getClient().send_transaction(tx)
                assert tx_hash.startswith("0x")
            except Exception as err:
                assert "Stack backtrace" not in err.args[0]

    def test_0001(self):
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        # 2. get live cells and cell is spend
        father_tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            100000,
            self.node.getClient().url,
            "1500000",
        )
        self.Miner.miner_until_tx_committed(self.node, father_tx_hash)
