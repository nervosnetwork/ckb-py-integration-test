import pytest
from framework.basic import CkbTest


class TestTxPoolLimit(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "tx_pool/node1", 8120, 8225
        )
        cls.node.prepare(other_ckb_config={"ckb_tx_pool_max_tx_pool_size": "46400000"})
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    def setup_method(self, method):
        """
        clean tx pool
        :param method:
        :return:
        """
        self.node.getClient().clear_tx_pool()

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def test_max_linked_transactions(self):
        """
        https://github.com/nervosnetwork/ckb/blob/develop/util/app-config/src/legacy/tx_pool.rs#L15
        test linked tx limit
        1. keep sending linked transactions until an error occurs
            ERROR :PoolRejectedTransactionByMaxAncestorsCountLimit
        2. query max max_ancestors_count
         max_ancestors_count == 2000
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            360000,
            self.node.getClient().url,
            "2800",
        )
        num = 0
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        # 1. keep sending linked transactions until an error occurs
        with pytest.raises(Exception) as exc_info:

            while True:
                tx_hash = self.Tx.send_transfer_self_tx_with_input(
                    [tx_hash],
                    ["0x0"],
                    self.Config.ACCOUNT_PRIVATE_1,
                    fee=1000,
                    api_url=self.node.getClient().url,
                )
                self.Node.wait_get_transaction(self.node, tx_hash, "pending")
                num += 1
        print(exc_info)
        expected_error_message = "PoolRejectedTransactionByMaxAncestorsCountLimit"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}'"
            f" not found in actual string '{exc_info.value.args[0]}'"
        )
        raw_tx_pools = self.node.getClient().get_raw_tx_pool(True)
        self.node.getClient().clear_tx_pool()
        # 2. query max max_ancestors_count
        max_ancestors_count = 0
        for tx_hash in raw_tx_pools["pending"].keys():
            max_ancestors_count = max(
                max_ancestors_count,
                int(raw_tx_pools["pending"][tx_hash]["ancestors_count"], 16),
            )
        assert max_ancestors_count == 1000

    def test_sub_tx(self):
        """
        The sub transactions will not trigger an error until reaching 2000
        1. keep sending linked transactions until transaction len > 2000
            pass
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2
        )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            360000,
            api_url=self.node.getClient().url,
            fee_rate="8000000",
        )

        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            output_count=1000,
            fee=10009246,
            api_url=self.node.getClient().url,
        )
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        for i in range(130):
            self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                [hex(i)],
                self.Config.ACCOUNT_PRIVATE_2,
                output_count=1,
                fee=(1000),
                api_url=self.node.getClient().url,
            )
        self.node.getClient().clear_tx_pool()
