import time

from framework.basic import CkbTest
from framework.util import run_command


class TestOrphanTx(CkbTest):

    @classmethod
    def setup_class(cls):
        cls.node1 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "node/node1", 8114, 8115
        )

        cls.node2 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "node/node2", 8116, 8117
        )

        cls.node1.prepare()
        cls.node1.start()

        cls.node2.prepare()
        cls.node2.start()

        cls.node1.connected(cls.node2)

        time.sleep(5)
        cls.Miner.make_tip_height_number(cls.node1, 300)

        cls.Node.wait_node_height(cls.node2, 300, 1500)

    @classmethod
    def teardown_class(cls):
        cls.node1.stop()
        cls.node1.clean()

        cls.node2.stop()
        cls.node2.clean()
        pass

    def setup_method(self, method):
        for i in range(10):
            self.Miner.miner_with_version(self.node1, "0x0")
        node1_pool = self.node1.getClient().tx_pool_info()
        assert node1_pool["pending"] == "0x0"
        height = self.node1.getClient().get_tip_block_number()
        self.Node.wait_node_height(self.node2, height, 1000)

    def test_orphan_transactions_return_to_pending_pool(self):
        """
        Orphan transactions can successfully return to the pending pool:

         1. Send parent transaction 1 to node 1.
                 send successful
         2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1.
                 remove parent transaction 1 successful
         3. Continue sending a tree-shaped structure of consecutive child transactions to node 1.
             send successful
         4. All child transactions received by node 2 go into the orphan pool.
             node2: pending pool is 0x0
             node1: pending pool > 0
         5. Node 2 calls miner; transactions in the orphan pool will not be included in the blockchain.
             tx not committed
         6. Send parent transaction 1 to node 2.
             send successful
         7. Query the transaction pool; orphan transactions are returned to the pending pool.
             node2 pool['orphan'] == 0
         8. Use node 2's miner; all transactions are added to the blockchain.
             all tx is committed
        """

        # 1. Send parent transaction 1 to node 1.
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        account_private = self.Config.ACCOUNT_PRIVATE_1
        tx1_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            account_private,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "1500000",
        )

        # 2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1.

        self.Node.wait_tx_pool(self.node2, "pending", 1)
        self.node2.getClient().remove_transaction(tx1_hash)
        assert self.node2.getClient().tx_pool_info()["pending"] == "0x0"

        # account2 = self.Ckb_cli.util_key_info_by_private_key(self.Config.ACCOUNT_PRIVATE_2)
        # tx2_hash = self.Ckb_cli.wallet_transfer_by_private_key(self.Config.ACCOUNT_PRIVATE_2,
        #                                                        account2["address"]["testnet"], 1000000,
        #                                                        self.node1.getClient().url, "1500000")

        # 3. Continue sending a tree-shaped structure of consecutive child transactions to node 1.
        #
        # for i in range
        #   send Cell1 --(tx1)-> Cell1-1,Cell1-2
        #                        |        ----(tx2)----->  Cell2-1,Cell2-2
        #                        |
        #                         ------(tx3)------->  Cell1-1,Cell1-2
        #
        #  ...

        account1_tx_hash = self.send_multiple_self_transactions(
            self.node1, tx1_hash, self.Config.ACCOUNT_PRIVATE_1, 5
        )
        # account2_tx_hash = self.send_multiple_self_transactions(self.node1, tx2_hash,
        #                                                         self.Config.ACCOUNT_PRIVATE_2, 5)

        # node2 txpool [orphan] > 0

        # 4. All child transactions received by node 2 go into the orphan pool.

        node1_pool = self.node1.getClient().tx_pool_info()
        assert node1_pool["pending"] == "0xb"
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] != "0x0"
        assert node2_pool["pending"] == "0x0"

        # 5. Node 2 calls miner; transactions in the orphan pool will not be included in the blockchain.
        for i in range(20):
            self.Miner.miner_with_version(self.node2, "0x0")
        node2_pool = self.node2.getClient().tx_pool_info()
        # assert node2_pool['orphan'] == "0xa"
        assert node2_pool["orphan"] != "0x0"
        assert node2_pool["pending"] == "0x0"

        # 6. Send parent transaction 1 to node 2.
        tx1 = self.node1.getClient().get_transaction(tx1_hash)
        del tx1["transaction"]["hash"]
        self.node2.getClient().send_transaction(tx1["transaction"])

        # 7. Query the transaction pool; orphan transactions are returned to the pending pool.
        self.Node.wait_tx_pool(self.node2, "pending", 1)
        self.Node.wait_tx_pool(self.node2, "pending", 11)
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] == "0x0"
        assert node2_pool["pending"] == "0xb"

        # 8. Use node 2's miner; all transactions are added to the blockchain.
        for i in range(10):
            self.Miner.miner_with_version(self.node2, "0x0")
        height = self.node2.getClient().get_tip_block_number()
        self.Node.wait_node_height(self.node1, height, 1000)
        node1_pool = self.node1.getClient().tx_pool_info()
        assert node1_pool["orphan"] == "0x0"
        assert node1_pool["pending"] == "0x0"
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] == "0x0"
        assert node2_pool["pending"] == "0x0"

    def test_clean_orphan_tx_when_node_restart(self):
        """
        1. Send parent transaction 1 to node 1.
                send successful
        2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1.
                remove parent transaction 1 successful
        3. Continue sending a tree-shaped structure of consecutive child transactions to node 1.
            send successful
        4. All child transactions received by node 2 go into the orphan pool.
            node2: pending pool is 0x0
            node1: pending pool > 0
        5. node2 restart
            restart successful
        6. pool orphan is empty

        """
        # 1. Send parent transaction 1 to node 1.
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        account_private = self.Config.ACCOUNT_PRIVATE_1
        tx1_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            account_private,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "1500000",
        )

        # 2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1.

        self.Node.wait_tx_pool(self.node2, "pending", 1)
        self.node2.getClient().remove_transaction(tx1_hash)
        assert self.node2.getClient().tx_pool_info()["pending"] == "0x0"

        # account2 = self.Ckb_cli.util_key_info_by_private_key(self.Config.ACCOUNT_PRIVATE_2)
        # tx2_hash = self.Ckb_cli.wallet_transfer_by_private_key(self.Config.ACCOUNT_PRIVATE_2,
        #                                                        account2["address"]["testnet"], 1000000,
        #                                                        self.node1.getClient().url, "1500000")

        # 3. Continue sending a tree-shaped structure of consecutive child transactions to node 1.
        #
        # for i in range
        #   send Cell1 --(tx1)-> Cell1-1,Cell1-2
        #                        |        ----(tx2)----->  Cell2-1,Cell2-2
        #                        |
        #                         ------(tx3)------->  Cell1-1,Cell1-2
        #
        #  ...

        account1_tx_hash = self.send_multiple_self_transactions(
            self.node1, tx1_hash, self.Config.ACCOUNT_PRIVATE_1, 5
        )
        # account2_tx_hash = self.send_multiple_self_transactions(self.node1, tx2_hash,
        #                                                         self.Config.ACCOUNT_PRIVATE_2, 5)

        # node2 txpool [orphan] > 0

        # 4. All child transactions received by node 2 go into the orphan pool.
        time.sleep(10)
        node1_pool = self.node1.getClient().tx_pool_info()
        assert node1_pool["pending"] == "0xb"
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] != "0x0"
        assert node2_pool["pending"] == "0x0"
        orphan_size = int(node2_pool["orphan"], 16)
        # 5. node2 restart
        self.node2.stop()
        run_command(f"cd {self.node2.ckb_dir} && rm -rf data/network")
        self.node2.start()

        # 6. orphan pool is empty
        time.sleep(10)
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] == "0x0"

        # 7. link node1 ,in order to not affect other case
        self.node2.connected(self.node1)
        for i in range(10):
            self.Miner.miner_with_version(self.node1, "0x0")
        height = self.node2.getClient().get_tip_block_number()
        self.Node.wait_node_height(self.node2, height, 1000)

    def test_clean_orphan_tx_timeout_gt_2min(self):
        """
        In the past 2 minutes, orphan transactions have not been removed yet.

         1. Send parent transaction 1 to node 1.
         2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1.
         3. Continue sending a tree-shaped structure of consecutive child transactions to node 1.
         4. All child transactions received by node 2 go into the orphan pool.
         5. orphan pool will empty until 120s pass
         6. query tx pool orphan size not removed
         7. Send parent transaction 1 to node 2.

        """
        # 1. Send parent transaction 1 to node 1.
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        account_private = self.Config.ACCOUNT_PRIVATE_1
        tx1_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            account_private,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "1500000",
        )

        # 2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1.

        self.Node.wait_tx_pool(self.node2, "pending", 1)
        self.node2.getClient().remove_transaction(tx1_hash)
        assert self.node2.getClient().tx_pool_info()["pending"] == "0x0"

        # account2 = self.Ckb_cli.util_key_info_by_private_key(self.Config.ACCOUNT_PRIVATE_2)
        # tx2_hash = self.Ckb_cli.wallet_transfer_by_private_key(self.Config.ACCOUNT_PRIVATE_2,
        #                                                        account2["address"]["testnet"], 1000000,
        #                                                        self.node1.getClient().url, "1500000")

        # 3. Continue sending a tree-shaped structure of consecutive child transactions to node 1.
        #
        # for i in range
        #   send Cell1 --(tx1)-> Cell1-1,Cell1-2
        #                        |        ----(tx2)----->  Cell2-1,Cell2-2
        #                        |
        #                         ------(tx3)------->  Cell1-1,Cell1-2
        #
        #  ...

        account1_tx_hash = self.send_multiple_self_transactions(
            self.node1, tx1_hash, self.Config.ACCOUNT_PRIVATE_1, 5
        )
        # account2_tx_hash = self.send_multiple_self_transactions(self.node1, tx2_hash,
        #                                                         self.Config.ACCOUNT_PRIVATE_2, 5)

        # node2 txpool [orphan] > 0

        # 4. All child transactions received by node 2 go into the orphan pool.
        time.sleep(10)
        node1_pool = self.node1.getClient().tx_pool_info()
        assert node1_pool["pending"] == "0xb"
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] != "0x0"
        assert node2_pool["pending"] == "0x0"

        # 5. orphan pool will empty until 120s pass
        time.sleep(150)
        account1_tx_hash = self.send_multiple_self_transactions(
            self.node1, account1_tx_hash, self.Config.ACCOUNT_PRIVATE_1, 5
        )
        time.sleep(10)

        # 6. query tx pool orphan size not removed

        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] == "0x14"

        # 7. Send parent transaction 1 to node 2.
        tx1 = self.node1.getClient().get_transaction(tx1_hash)
        del tx1["transaction"]["hash"]
        self.node2.getClient().send_transaction(tx1["transaction"])
        for i in range(10):
            self.Miner.miner_with_version(self.node2, "0x0")
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["pending"] == "0x0"

    def test_send_max_orphan_tx(self):
        """
        Removed orphan transactions can be resubmitted through synchronization.

        1. Send parent transaction 1,2 to node 1.
            send successful
        2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1,2.
            remove successful
        3. continue sending 60 transactions, which form a tree-shaped structure with consecutive child transactions for tx1 and tx2, to node 1
            send successful
        4. All child transactions received by node 2 go into the orphan pool. buy max orphan tx size is 100
            node2:orphan == 100
            node1: pending == 122

        5. resend parent tx1,tx2 to node2
            send successful
        6. resend all  tx to node1

        7. query tx pool info
            node2:orphan == 122
            node1: pending == 122

        """

        # 1. Send parent transaction 1 to node 1.
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        account_private = self.Config.ACCOUNT_PRIVATE_1
        tx1_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            account_private,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "1500000",
        )

        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2
        )
        account_private = self.Config.ACCOUNT_PRIVATE_2
        tx2_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            account_private,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "1500000",
        )

        # 2. Wait for node 2 to synchronize with node 1, then remove transaction parent 1.

        self.Node.wait_tx_pool(self.node2, "pending", 2)
        self.node2.getClient().remove_transaction(tx1_hash)
        self.node2.getClient().remove_transaction(tx2_hash)

        assert self.node2.getClient().tx_pool_info()["pending"] == "0x0"

        # 3. Continue sending a tree-shaped structure of consecutive child transactions to node 1.
        #
        # for i in range
        #   send Cell1 --(tx1)-> Cell1-1,Cell1-2
        #                        |        ----(tx2)----->  Cell2-1,Cell2-2
        #                        |
        #                         ------(tx3)------->  Cell1-1,Cell1-2
        #
        #  ...

        account1_tx_hash_list = self.send_multiple_linked_self_transactions(
            self.node1, tx1_hash, self.Config.ACCOUNT_PRIVATE_1, 60
        )
        account2_tx_hash_list = self.send_multiple_linked_self_transactions(
            self.node1, tx2_hash, self.Config.ACCOUNT_PRIVATE_2, 60
        )

        # 4. All child transactions received by node 2 go into the orphan pool.
        time.sleep(3)
        node1_pool = self.node1.getClient().tx_pool_info()
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["orphan"] != "0x0"
        assert node2_pool["pending"] == "0x0"

        # 5. resend parent tx1,tx2 to node2
        tx1 = self.node1.getClient().get_transaction(tx1_hash)
        del tx1["transaction"]["hash"]
        self.node2.getClient().send_transaction(tx1["transaction"])
        time.sleep(3)

        node2_pool = self.node2.getClient().tx_pool_info()
        # 6. resend all  tx to node1
        for i in range(2):
            for hash in account1_tx_hash_list:
                tx1 = self.node1.getClient().get_transaction(hash)
                del tx1["transaction"]["hash"]
                try:
                    self.node1.getClient().send_transaction(tx1["transaction"])
                except Exception:
                    pass
                time.sleep(0.5)
        time.sleep(3)

        node2_pool = self.node2.getClient().tx_pool_info()
        tx1 = self.node1.getClient().get_transaction(tx2_hash)
        del tx1["transaction"]["hash"]
        self.node2.getClient().send_transaction(tx1["transaction"])
        time.sleep(3)

        node2_pool = self.node2.getClient().tx_pool_info()
        #
        for i in range(2):
            for hash in account2_tx_hash_list:
                tx1 = self.node1.getClient().get_transaction(hash)
                del tx1["transaction"]["hash"]
                try:
                    self.node1.getClient().send_transaction(tx1["transaction"])
                except Exception:
                    pass
                time.sleep(0.5)
        time.sleep(3)
        # 6. query tx pool info
        node1_pool = self.node1.getClient().tx_pool_info()
        node2_pool = self.node2.getClient().tx_pool_info()
        print("tx1 list:", account1_tx_hash_list)
        print("tx2 list:", account2_tx_hash_list)
        assert node1_pool["pending"] == node2_pool["pending"]
        for i in range(20):
            self.Miner.miner_with_version(self.node2, "0x0")
        node2_pool = self.node2.getClient().tx_pool_info()
        assert node2_pool["pending"] == "0x0"

    def send_multiple_self_transactions(self, node, tx_hash, account_private, size):
        for i in range(size):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                ["0x0"],
                account_private,
                output_count=2,
                fee=1090,
                api_url=node.getClient().url,
            )
            self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                ["0x1"],
                account_private,
                output_count=2,
                fee=1090,
                api_url=node.getClient().url,
            )
        return tx_hash

    def send_multiple_linked_self_transactions(
        self, node, tx_hash, account_private, size
    ):
        tx_hash_list = [tx_hash]
        for i in range(size):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                ["0x0"],
                account_private,
                output_count=1,
                fee=1090,
                api_url=node.getClient().url,
            )
            tx_hash_list.append(tx_hash)
        return tx_hash_list
