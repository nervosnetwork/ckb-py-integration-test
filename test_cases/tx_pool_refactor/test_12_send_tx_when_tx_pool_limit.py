import pytest
from framework.basic import CkbTest


class TestSendTxWhenPoolLimit(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "tx_pool/node1", 8120, 8225
        )
        cls.node.prepare(other_ckb_config={"ckb_tx_pool_max_tx_pool_size": "4640"})
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    def setup_method(self, method):
        """
        clean tx pool
        :param method:
        :return:
        """
        self.node.getClient().clear_tx_pool()
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")
        pool = self.node.getClient().tx_pool_info()
        print("pool:", pool)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def test_replace_same_father_link_tx_when_tx_pool_full(self):
        """
        Can replace child transactions generated from other outputs of the parent transaction.
        The transaction with a cellDep has a very low fee; child transactions will only replace other transactions, not those used as cellDeps by higher-fee transactions.
        The parent transaction has the lowest fee; child transactions will replace other transactions but not the parent transaction.

        Returns:

        """
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

        tx_hash_2 = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            100000,
            self.node.getClient().url,
            "1000",
        )

        tx_list = [father_tx_hash, tx_hash_2]
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [father_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=15,
            fee=15000,
            api_url=self.node.getClient().url,
        )
        tx_list.append(tx_hash)
        for i in range(0, 15):
            print("current i:", i)
            tx_hash1 = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                [hex(i)],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=10000 * 2**i,
                dep_cells=[{"tx_hash": tx_hash_2, "index_hex": "0x0"}],
                api_url=self.node.getClient().url,
            )
            tx_list.append(tx_hash1)
        response = self.node.getClient().get_transaction(tx_hash_2)
        assert response["tx_status"]["status"] != "rejected"
        response = self.node.getClient().get_transaction(father_tx_hash)
        assert response["tx_status"]["status"] != "rejected"
        rejected = 0
        for tx_hash in tx_list:
            response = self.node.getClient().get_transaction(tx_hash)
            print(response)
            if response["tx_status"]["status"] == "rejected" or response["tx_status"]["status"] == "unknown":
                rejected += 1
        assert rejected == 11

    def test_replace_normal_tx_when_tx_pool_full(self):
        """
        When the transaction pool is full of standard transactions, sending a transaction with a higher fee will succeed, and transactions with lower fees will be removed.

        1. send 10 normal tx
        2. send 12 tx that  fee > 10 normal tx
        3. 12 tx status == pending
        4. 8 normal tx status == rejected
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            1000000,
            self.node.getClient().url,
            "1500000",
        )
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=10,
            fee=1500000,
            api_url=self.node.getClient().url,
        )
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        # 1. send 10 normal tx
        tx_list = []
        for i in range(0, 10):
            print("current i:", i)
            tx_hash1 = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                [hex(i)],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=1090 + i,
                api_url=self.node.getClient().url,
            )
            tx_list.append(tx_hash1)

        # 2. send 12 tx that  fee > 10 normal tx
        tx_hash_a = tx_hash
        tx_hash_a_list = []
        for i in range(12):
            print("tx_hash_a_list current i:", i)
            tx_hash_a = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash_a],
                [hex(0)],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=11090 + i * 1000,
                api_url=self.node.getClient().url,
            )
            tx_hash_a_list.append(tx_hash_a)
        # 4. 8 normal tx status == rejected
        rejected_status_size = 0
        for tx_hash in tx_list:
            tx = self.node.getClient().get_transaction(tx_hash)
            if tx["tx_status"]["status"] == "rejected" or tx["tx_status"]["status"] == "unknown":
                rejected_status_size += 1
        for tx_hash_a in tx_hash_a_list:
            tx = self.node.getClient().get_transaction(tx_hash_a)
            assert tx["tx_status"]["status"] != "rejected"
        assert rejected_status_size == 9

    def test_replace_link_tx_when_ckb_tx_pool_full(self):
        """
        Transaction xxxx(fee = 500000000)
        Chain transactions:a(fee=1000)-> b(fee=2000)- ->c(fee=3000)- -> d(fee=4000) -> e(fee=5000)
        Inserting tx(fee=500000) will result in the removal of transactions a, b, c, d, and e.
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            1000000,
            self.node.getClient().url,
            "1500000",
        )
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=10,
            fee=1500000,
            api_url=self.node.getClient().url,
        )
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        tx_hash_a = tx_hash
        tx_hash_a_list = []
        for i in range(12):
            print("tx_hash_a_list current i:", i)
            tx_hash_a = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash_a],
                [hex(0)],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=11090 + i * 1000,
                api_url=self.node.getClient().url,
            )
            tx_hash_a_list.append(tx_hash_a)

        tx_hash_b = tx_hash
        tx_hash_b_list = []
        tx_hash_b = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash_b],
            [hex(1)],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=1100090 + i * 1000,
            api_url=self.node.getClient().url,
        )
        tx_hash_b_list.append(tx_hash_b)
        for i in range(3):
            print("tx_hash_b_list current i:", i)

            tx_hash_b = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash_b],
                [hex(0)],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=110000000 + i * 10000000,
                api_url=self.node.getClient().url,
            )
            tx_hash_b_list.append(tx_hash_b)
        for tx_hash_a in tx_hash_a_list:
            tx = self.node.getClient().get_transaction(tx_hash_a)
            assert tx["tx_status"]["status"] == "rejected" or tx["tx_status"]["status"] == "unknown"
        for tx_hash_b in tx_hash_b_list:
            self.node.getClient().get_transaction(tx_hash_b)

    def test_cant_replace_father_tx_when_ckb_tx_pool_full(self):
        """
        If the transaction pool is full of parent transactions, even if a child transaction has a higher fee, it will still report 'pool is full'.

        Returns:

        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            1000000,
            self.node.getClient().url,
            "1500000",
        )
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=10,
            fee=1500000,
            api_url=self.node.getClient().url,
        )
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        with pytest.raises(Exception) as exc_info:
            for i in range(22):
                tx_hash = self.Tx.send_transfer_self_tx_with_input(
                    [tx_hash],
                    [hex(0)],
                    self.Config.ACCOUNT_PRIVATE_1,
                    output_count=1,
                    fee=11090 + i * 1000,
                    api_url=self.node.getClient().url,
                )

        expected_error_message = "PoolIsFull"
        print("exc_info.value.args[0]:", exc_info.value.args[0])
        assert (
            expected_error_message in exc_info.value.args[0]
        ), f"Expected substring '{expected_error_message}' not found in actual string '{exc_info.value.args[0]}'"
