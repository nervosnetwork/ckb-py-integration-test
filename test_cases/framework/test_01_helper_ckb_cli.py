from framework.basic import CkbTest


class TestCkbCli(CkbTest):

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_cli/node", 8314, 8315
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 20)

    @classmethod
    def teardown_class(cls):
        print("stop node and clean")
        cls.node.stop()
        cls.node.clean()

    def test_01_version(self):
        ckb_cli_version = self.Ckb_cli.version()
        assert (
            "ckb-cli" in ckb_cli_version
        ), "{ckb_cli_version} not contains ckb-cli".format(
            ckb_cli_version=ckb_cli_version
        )

    def test_02_util_key_info_by_private_key(self):
        account1 = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        assert (
            "ckt1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xwsqwgx292hnvmn68xf779vmzrshpmm6epn4c0cgwga"
            == account1["address"]["testnet"]
        )

    def test_03_get_deploy_toml(self):
        config_toml_str = self.Ckb_cli.get_deploy_toml_config(
            self.Config.ACCOUNT_PRIVATE_2, "/tmp/path", True
        )
        assert "0x470dcdc5e44064909650113a274b3b36aecb6dc7" in config_toml_str

    def test_04_get_account_balance(self):
        account1 = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        ckb_address = account1["address"]["testnet"]
        account_capacity = self.Ckb_cli.wallet_get_capacity(
            ckb_address, self.node.getClient().url
        )
        assert account_capacity > 10000000000.0

    def test_05_get_account_live_cells(self):
        account1 = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        ckb_address = account1["address"]["testnet"]
        live_cells = self.Ckb_cli.wallet_get_live_cells(
            ckb_address, self.node.getClient().url
        )
        assert len(live_cells["live_cells"]) > 0

    def test_06_account1_transfer_account2(self, max_tx_fee=None):
        transfer_balance = 100

        account2 = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2
        )
        ckb_address = account2["address"]["testnet"]

        before_transfer_capacity = self.Ckb_cli.wallet_get_capacity(
            ckb_address, self.node.getClient().url
        )

        # account 1 transfer 100 to account 2
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            ckb_address,
            transfer_balance,
            api_url=self.node.getClient().url,
            max_tx_fee=max_tx_fee,
        )

        # wait transfer tx committed
        self.Miner.miner_until_tx_committed(self.node, tx_hash)

        # check balance is right
        after_transfer_capacity = self.Ckb_cli.wallet_get_capacity(
            ckb_address, self.node.getClient().url
        )
        assert after_transfer_capacity - transfer_balance == before_transfer_capacity
