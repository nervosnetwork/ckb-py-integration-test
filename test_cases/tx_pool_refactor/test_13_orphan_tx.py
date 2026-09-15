import time

import pytest

from framework.basic import CkbTest


class TestOrphanTx(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node1 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "node/node1", 8114, 8115
        )

        cls.node2 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "node/node2", 8116, 8117
        )
        cls.node3 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "node/node3", 8118, 8119
        )

        cls.node1.prepare()
        cls.node1.start()

        cls.node2.prepare()
        cls.node2.start()

        cls.node3.prepare()
        cls.node3.start()

        cls.Miner.make_tip_height_number(cls.node1, 300)

        cls.node2.connected(cls.node1)
        cls.Node.wait_node_height(cls.node2, 300, 1500)
        cls.node3.connected(cls.node2)
        cls.Node.wait_node_height(cls.node3, 300, 1500)

    @classmethod
    def teardown_class(cls):
        cls.node1.stop()
        cls.node1.clean()

        cls.node2.stop()
        cls.node2.clean()

        cls.node3.stop()
        cls.node3.clean()

    def setup_method(self, method):
        for i in range(10):
            self.Miner.miner_with_version(self.node1, "0x0")
        node1_pool = self.node1.getClient().tx_pool_info()
        assert node1_pool["pending"] == "0x0"
        height = self.node1.getClient().get_tip_block_number()
        self.Node.wait_node_height(self.node2, height, 1000)

    def test_broadcast_tx_that_inputs_contains_miss_pending_tx(self):
        """
        A transaction has all pending inputs, and some of the input transactions are lost. When the transaction is broadcast, it can enter the orphan pool. After the missing input transactions are broadcast to the pending pool, it will be transferred from the orphan pool to the pending pool.
        0. Send parent transaction and wait for it to be on-chain
        1. Node 1 sends n child transactions
        2. After node 3 synchronizes, delete half of the child transactions on node 3
        3. Send a child transaction aggregation on node 1 (n child input -> new output)
        4. Query that the orphan pool of node 3 is 1
        5. Resend txs that delete on node 3
        6. The orphan pool of node 3 is 0, and the tx_pool of node 3 is the same as that of node 2
        Returns:
        """
        n = 10

        # 0. Send parent transaction and wait for it to be on-chain
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

        tx_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=n,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )

        self.Miner.miner_until_tx_committed(self.node1, tx_father_hash)

        # 1. Node 1 sends n child transactions
        tx_n_hash_list = []
        tx_n_index_list = []
        for i in range(0, n):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 2. After node 3 synchronizes, delete half of the child transactions on node 3
        self.Node.wait_get_transaction(self.node3, tx_n_hash_list[-1], "pending")
        remove_tx_list = []
        for i in range(0, n, 2):
            remove_tx = self.node3.getClient().get_transaction(tx_n_hash_list[i])
            self.node3.getClient().remove_transaction(tx_n_hash_list[i])
            remove_tx_list.append(remove_tx)

        # 3. Send a child transaction aggregation on node 1 (n child input -> new output)
        tx_child_hash = self.Tx.send_transfer_self_tx_with_input(
            tx_n_hash_list,
            tx_n_index_list,
            account_private,
            output_count=1,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )
        print(tx_child_hash)
        self.node1.getClient().get_transaction(tx_child_hash)
        time.sleep(5)

        # 4. Query that the orphan pool of node 3 is 1
        before_tx_pool_info = self.node3.getClient().tx_pool_info()
        assert before_tx_pool_info["orphan"] == "0x1"

        # 5. Resend txs that delete on node 3
        for remove_tx in remove_tx_list:
            del remove_tx["transaction"]["hash"]
            self.node3.getClient().send_transaction(remove_tx["transaction"])

        # 6. The orphan pool of node 3 is 0, and the tx_pool of node 3 is the same as that of node 2
        time.sleep(3)
        node2_info = self.node2.getClient().tx_pool_info()
        tx_pool_info = self.node3.getClient().tx_pool_info()
        print("node2 tx info:", node2_info)
        print("node3 before tx pool:", before_tx_pool_info)
        print("node3 send tx :pool:", tx_pool_info)
        assert tx_pool_info["pending"] == node2_info["pending"]
        assert tx_pool_info["orphan"] == "0x0"
        self.Miner.miner_until_tx_committed(self.node3, tx_child_hash)

    def test_broadcast_tx_that_inputs_contains_miss_pending_tx_and_commit_tx(self):
        """
        A transaction has some pending inputs, some committed inputs, and some pending inputs are missing. When the transaction is broadcast, it can enter the orphan pool. After the missing input transactions are synchronized to the pending pool, it will be transferred from the orphan pool to the pending pool.
            1. Node 1 sends 10 child transactions
            2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
            3. After node 3 synchronizes, delete half of the child transactions on node 3
            4. Send a child transaction aggregation on node 1 (n child input -> new output)
            5. Query that the orphan pool of node 3 is 1
            6. Resend txs that delete on node 3
            7. The orphan pool of node 3 is 0, and the tx_pool of node 2 is the same as that of node 1
        """

        n = 20
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

        tx_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=n,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )

        self.Miner.miner_until_tx_committed(self.node1, tx_father_hash)

        # 1. Node 1 sends 10 child transactions
        tx_n_hash_list = []
        tx_n_index_list = []
        tx_hash = ""

        for i in range(10):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
        self.Miner.miner_until_tx_committed(self.node1, tx_hash, True)
        for i in range(10, n):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 3. After node 3 synchronizes, delete half of the child transactions on node 3
        self.Node.wait_get_transaction(self.node3, tx_n_hash_list[-1], "pending")
        remove_tx_list = []
        for i in range(10, n, 2):
            remove_tx = self.node3.getClient().get_transaction(tx_n_hash_list[i])
            self.node3.getClient().remove_transaction(tx_n_hash_list[i])
            remove_tx_list.append(remove_tx)

        # 4. Send a child transaction aggregation on node 1 (n child input -> new output)
        tx_child_hash = self.Tx.send_transfer_self_tx_with_input(
            tx_n_hash_list,
            tx_n_index_list,
            account_private,
            output_count=1,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )
        print(tx_child_hash)
        self.node1.getClient().get_transaction(tx_child_hash)
        time.sleep(5)

        # 5. Query that the orphan pool of node 3 is 1
        before_tx_pool_info = self.node3.getClient().tx_pool_info()
        assert before_tx_pool_info["orphan"] == "0x1"

        # 6. Resend txs that delete on node 3
        for remove_tx in remove_tx_list:
            del remove_tx["transaction"]["hash"]
            self.node3.getClient().send_transaction(remove_tx["transaction"])

        # 7. The orphan pool of node 3 is 0, and the tx_pool of node 2 is the same as that of node 1
        time.sleep(3)
        node2_info = self.node2.getClient().tx_pool_info()
        tx_pool_info = self.node3.getClient().tx_pool_info()

        print("node2 tx info:", node2_info)
        print("node3 before tx pool:", before_tx_pool_info)
        print("node3 send tx :pool:", tx_pool_info)
        assert tx_pool_info["pending"] == node2_info["pending"]
        assert tx_pool_info["orphan"] == "0x0"
        self.Miner.miner_until_tx_committed(self.node3, tx_child_hash)

    @pytest.mark.skip
    def test_broadcast_tx_that_cellDep_contains_miss_tx(self):
        """
        A transactions have a cellDep, cellDep contains many tx_hashes that are miss when broadcast
            0. Send parent transaction and wait for it to be on-chain
            1. Node 1 sends n child transactions
            2. After node 3 synchronizes, delete half of the child transactions on node 3
            3. Send a exit child transaction aggregation on node 1 that cellDep contains miss tx
            4. Query that the orphan pool of node 3 is 1
            5. Resend txs that delete on node 3
            6. The orphan pool of node 3 is 0, and the tx_pool of node 3 is the same as that of node 2
        Returns:
        """
        n = 10

        # 0. Send parent transaction and wait for it to be on-chain
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

        tx_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=n,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )

        self.Miner.miner_until_tx_committed(self.node1, tx_father_hash)

        #  1. Node 1 sends n child transactions
        tx_n_hash_list = []
        tx_n_index_list = []
        for i in range(0, n):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 2. After node 3 synchronizes, delete half of the child transactions on node 3
        self.Node.wait_get_transaction(self.node3, tx_n_hash_list[-1], "pending")
        remove_tx_list = []
        for i in range(0, n, 2):
            remove_tx = self.node3.getClient().get_transaction(tx_n_hash_list[i])
            self.node3.getClient().remove_transaction(tx_n_hash_list[i])
            remove_tx_list.append(remove_tx)

        remove_cell_dep_hash_list = []
        for remove_tx in remove_tx_list:
            index = tx_n_hash_list.index(remove_tx["transaction"]["hash"])
            del tx_n_hash_list[index]
            remove_index = tx_n_index_list[index]
            del tx_n_index_list[index]
            remove_cell_dep_hash_list.append(
                {"tx_hash": remove_tx["transaction"]["hash"], "index_hex": remove_index}
            )

        # 3. Send a exit child transaction aggregation on node 1 that cellDep contains miss tx
        tx_child_hash = self.Tx.send_transfer_self_tx_with_input(
            tx_n_hash_list,
            tx_n_index_list,
            account_private,
            output_count=1,
            fee=1090000,
            api_url=self.node1.getClient().url,
            dep_cells=remove_cell_dep_hash_list,
        )
        print(tx_child_hash)
        tx_child_tx = self.node1.getClient().get_transaction(tx_child_hash)
        time.sleep(5)

        # 4. Query that the orphan pool of node 3 is 1
        before_tx_pool_info = self.node3.getClient().tx_pool_info()
        assert before_tx_pool_info["orphan"] == "0x1"

        # 5. Resend txs that delete on node 3
        for remove_tx in remove_tx_list:
            del remove_tx["transaction"]["hash"]
            self.node3.getClient().send_transaction(remove_tx["transaction"])

        time.sleep(3)
        # 6. The orphan pool of node 3 is 0, and the tx_pool of node 3 is the same as that of node 2
        node2_info = self.node2.getClient().tx_pool_info()
        tx_pool_info = self.node3.getClient().tx_pool_info()

        print("node2 tx info:", node2_info)
        print("node3 before tx pool:", before_tx_pool_info)
        print("node3 send tx :pool:", tx_pool_info)
        assert tx_pool_info["pending"] == node2_info["pending"]
        assert tx_pool_info["orphan"] == "0x0"
        self.Miner.miner_until_tx_committed(self.node3, tx_child_hash)

    @pytest.mark.skip
    def test_broadcast_tx_that_cellDep_contains_miss_tx_and_commit_tx(self):
        """
        A transactions have a cellDep, cellDep contains many tx_hashes that are miss when broadcast,and some txs are committed
            1. Node 1 sends 10 child transactions
            2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
            3. After node 3 synchronizes, delete half of the child transactions on node 3
            4. Send exist  child transaction aggregation on node 1 (n child input -> new output) ,that cell deps contains miss tx
            5. Query that the orphan pool of node 3 is 1
            6. Resend txs that delete on node 3
            7. The orphan pool of node 3 is 0, and the tx_pool of node 2 is the same as that of node 1
         Returns:
        """
        n = 20
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

        tx_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=n,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )

        self.Miner.miner_until_tx_committed(self.node1, tx_father_hash)

        #  1. Node 1 sends 10 child transactions
        tx_n_hash_list = []
        tx_n_index_list = []
        tx_hash = ""
        for i in range(10):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
        self.Miner.miner_until_tx_committed(self.node1, tx_hash, True)
        for i in range(10, n):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 3. After node 3 synchronizes, delete half of the child transactions on node 3
        self.Node.wait_get_transaction(self.node3, tx_n_hash_list[-1], "pending")
        remove_tx_list = []
        input_and_cell_dep_list = []
        for i in range(0, n, 2):
            remove_tx = self.node3.getClient().get_transaction(tx_n_hash_list[i])
            if self.node3.getClient().remove_transaction(tx_n_hash_list[i]):
                remove_tx_list.append(remove_tx)
            input_and_cell_dep_list.append(remove_tx)

        remove_cell_dep_hash_list = []
        for remove_tx in input_and_cell_dep_list:
            index = tx_n_hash_list.index(remove_tx["transaction"]["hash"])
            del tx_n_hash_list[index]
            remove_index = tx_n_index_list[index]
            del tx_n_index_list[index]
            remove_cell_dep_hash_list.append(
                {"tx_hash": remove_tx["transaction"]["hash"], "index_hex": remove_index}
            )

        # 4. Send exist  child transaction aggregation on node 1 (n child input -> new output) ,that cell deps contains miss tx and committed
        tx_child_hash = self.Tx.send_transfer_self_tx_with_input(
            tx_n_hash_list,
            tx_n_index_list,
            account_private,
            output_count=1,
            fee=1090000,
            api_url=self.node1.getClient().url,
            dep_cells=remove_cell_dep_hash_list,
        )
        print(tx_child_hash)
        self.node1.getClient().get_transaction(tx_child_hash)
        time.sleep(5)

        # 5. Query that the orphan pool of node 3 is 1
        before_tx_pool_info = self.node3.getClient().tx_pool_info()
        assert before_tx_pool_info["orphan"] == "0x1"

        # 6. Resend txs that delete on node 3
        for remove_tx in remove_tx_list:
            del remove_tx["transaction"]["hash"]
            self.node3.getClient().send_transaction(remove_tx["transaction"])

        time.sleep(3)

        # 7. The orphan pool of node 3 is 0, and the tx_pool of node 2 is the same as that of node 1
        node2_info = self.node2.getClient().tx_pool_info()
        tx_pool_info = self.node3.getClient().tx_pool_info()
        print("node2 tx info:", node2_info)
        print("node3 before tx pool:", before_tx_pool_info)
        print("node3 send tx :pool:", tx_pool_info)
        print("remove_tx_list len:", len(remove_tx_list))
        assert tx_pool_info["pending"] == node2_info["pending"]
        assert tx_pool_info["orphan"] == "0x0"
        self.Miner.miner_until_tx_committed(self.node3, tx_child_hash)

    @pytest.mark.skip
    def test_broadcast_tx_that_cellDep_and_input_are_contains_miss_tx_and_commit_tx(
        self,
    ):
        """
        A transactions have  cellDep and inputs, cellDep and inputs contains many tx_hashes that are miss when broadcast,and some txs are committed
            1. Node 1 sends 10 child transactions
            2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
            3. After node 3 synchronizes, delete half of the child transactions on node 3
            4. Send  child transaction aggregation on node 1 (n child input -> new output) ,that cell deps and inputs contains miss tx
            5. Query that the orphan pool of node 3 is 1
            6. Resend txs that delete on node 3
            7. The orphan pool of node 3 is 0, and the tx_pool of node 2 is the same as that of node 1
        Returns:
        """
        n = 20
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

        tx_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=n,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )

        self.Miner.miner_until_tx_committed(self.node1, tx_father_hash)

        # 1. Node 1 sends 10 child transactions
        tx_n_hash_list = []
        tx_n_index_list = []
        tx_hash = ""
        for i in range(10):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
        self.Miner.miner_until_tx_committed(self.node1, tx_hash, True)
        for i in range(10, n):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 3. After node 3 synchronizes, delete half of the child transactions on node 3
        self.Node.wait_get_transaction(self.node3, tx_n_hash_list[-1], "pending")
        remove_tx_list = []
        input_and_cell_dep_tx_list = []
        for i in range(0, n, 2):
            remove_tx = self.node3.getClient().get_transaction(tx_n_hash_list[i])
            self.node3.getClient().remove_transaction(tx_n_hash_list[i])
            if i > 10:
                remove_tx_list.append(remove_tx)
            input_and_cell_dep_tx_list.append(remove_tx)

        remove_cell_dep_hash_list = []
        num = 0
        for remove_tx in input_and_cell_dep_tx_list:
            num += 1
            if num % 2 == 1:
                index = tx_n_hash_list.index(remove_tx["transaction"]["hash"])
                del tx_n_hash_list[index]
                remove_index = tx_n_index_list[index]
                del tx_n_index_list[index]
                remove_cell_dep_hash_list.append(
                    {
                        "tx_hash": remove_tx["transaction"]["hash"],
                        "index_hex": remove_index,
                    }
                )

        # 4. Send  child transaction aggregation on node 1 (n child input -> new output) ,that cell deps and inputs contains miss tx
        tx_child_hash = self.Tx.send_transfer_self_tx_with_input(
            tx_n_hash_list,
            tx_n_index_list,
            account_private,
            output_count=1,
            fee=1090000,
            api_url=self.node1.getClient().url,
            dep_cells=remove_cell_dep_hash_list,
        )
        print(tx_child_hash)
        self.node1.getClient().get_transaction(tx_child_hash)
        time.sleep(5)

        # 5. Query that the orphan pool of node 3 is 1
        before_tx_pool_info = self.node3.getClient().tx_pool_info()
        assert before_tx_pool_info["orphan"] == "0x1"

        # 6. Resend txs that delete on node 3
        for remove_tx in remove_tx_list:
            del remove_tx["transaction"]["hash"]
            self.node3.getClient().send_transaction(remove_tx["transaction"])
        time.sleep(3)

        # 7. The orphan pool of node 3 is 0, and the tx_pool of node 2 is the same as that of node 1
        node2_info = self.node2.getClient().tx_pool_info()
        tx_pool_info = self.node3.getClient().tx_pool_info()
        print("node2 tx info:", node2_info)
        print("node3 before tx pool:", before_tx_pool_info)
        print("node3 send tx :pool:", tx_pool_info)
        assert tx_pool_info["pending"] == node2_info["pending"]
        assert tx_pool_info["orphan"] == "0x0"
        self.Miner.miner_until_tx_committed(self.node3, tx_child_hash)

    def test_linked_orphan_tx(self):
        """
        A transaction's parent transaction is in the orphan pool. When the parent transaction is recovered from the orphan pool, a series of child transactions will also be transferred from the orphan pool to the pending pool.
            1. Node 1 sends 10 child transactions
            2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
            3. After node 3 synchronizes, delete half of the child transactions on node 3
            4. Send a child transaction aggregation on node 1, where the inputs include the deleted transaction and the cell dep includes the deleted transaction
            5. Query that the orphan pool of node 3 is 1
            6. Send child transactions for 3 orphan transactions
            7. The number of orphan pool transactions is 4
            8. Resend the deleted transaction on node 3
            9. The orphan pool of node 3 is 0, and the tx_pool of node 3 is the same as that of node 2.
        Returns:
        """
        n = 20
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

        tx_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=n,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )

        self.Miner.miner_until_tx_committed(self.node1, tx_father_hash)

        #  1. Node 1 sends 10 child transactions
        tx_n_hash_list = []
        tx_n_index_list = []
        tx_hash = ""
        for i in range(10):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 2. Mine on node 1 until all 10 child transactions are on-chain, and then continue to send (n-10) child transactions
        self.Miner.miner_until_tx_committed(self.node1, tx_hash, True)
        for i in range(10, n):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 3. After node 3 synchronizes, delete half of the child transactions on node 3
        self.Node.wait_get_transaction(self.node3, tx_n_hash_list[-1], "pending")
        remove_tx_list = []
        for i in range(10, n, 2):
            remove_tx = self.node3.getClient().get_transaction(tx_n_hash_list[i])
            self.node3.getClient().remove_transaction(tx_n_hash_list[i])
            remove_tx_list.append(remove_tx)

        remove_cell_dep_hash_list = []
        num = 0
        for remove_tx in remove_tx_list:
            num += 1
            if num % 2 == 1:
                index = tx_n_hash_list.index(remove_tx["transaction"]["hash"])
                del tx_n_hash_list[index]
                remove_index = tx_n_index_list[index]
                del tx_n_index_list[index]
                remove_cell_dep_hash_list.append(
                    {
                        "tx_hash": remove_tx["transaction"]["hash"],
                        "index_hex": remove_index,
                    }
                )

        # 4. Send a child transaction aggregation on node 1, where the inputs include the deleted transaction and the cell dep includes the deleted transaction
        tx_child_hash = self.Tx.send_transfer_self_tx_with_input(
            tx_n_hash_list,
            tx_n_index_list,
            account_private,
            output_count=1,
            fee=1090000,
            api_url=self.node1.getClient().url,
            # dep_cells=remove_cell_dep_hash_list
        )
        print(tx_child_hash)
        self.node1.getClient().get_transaction(tx_child_hash)
        time.sleep(5)

        # 5. Query that the orphan pool of node 3 is 1
        before_tx_pool_info = self.node3.getClient().tx_pool_info()
        assert before_tx_pool_info["orphan"] == "0x1"

        # 6. Send child transactions for 3 orphan transactions
        tx_child_child_list = []
        tx_child_child_hash = tx_child_hash
        for i in range(3):
            tx_child_child_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_child_child_hash],
                ["0x0"],
                account_private,
                output_count=2,
                fee=1090000,
                api_url=self.node1.getClient().url,
                # dep_cells=remove_cell_dep_hash_list
            )
            tx_child_child_list.append(tx_child_child_hash)
        time.sleep(3)
        # 7. The number of orphan pool transactions is 4
        tx_pool_after_send_child_list = self.node3.getClient().tx_pool_info()
        assert tx_pool_after_send_child_list["orphan"] == "0x4"

        # 8. Resend the deleted transaction on node 3
        for remove_tx in remove_tx_list:
            del remove_tx["transaction"]["hash"]
            self.node3.getClient().send_transaction(remove_tx["transaction"])
        time.sleep(3)

        # 9. The orphan pool of node 3 is 0, and the tx_pool of node 3 is the same as that of node 2.
        node2_info = self.node2.getClient().tx_pool_info()
        tx_pool_info = self.node3.getClient().tx_pool_info()
        print("node2 tx info:", node2_info)
        print("node3 before tx pool:", before_tx_pool_info)
        print("node3 send tx :pool:", tx_pool_info)
        assert tx_pool_info["pending"] == node2_info["pending"]
        assert tx_pool_info["orphan"] == "0x0"
        # self.Miner.miner_until_tx_committed(self.node3, tx_child_hash)

    def test_orphan_with_rbf(self):
        """
        RBF Consideration in Orphan Pool to Pending Pool Transition
            1. Node 1 sends N transactions
            2. After node 3 synchronizes, delete half of the child transactions on node 3
            3. On node 1, send a child transaction aggregation with a fee of 1090000
            4. Query that the orphan pool of node 3 is 1
            5. On node 3, send a conflicting transaction with the aggregation transaction with a fee of 1000
            6. Resend the deleted transaction
            7. Query the orphan pool
            8. Query the status of the aggregation transaction and the conflicting transaction
        Returns:
        """
        n = 10
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

        tx_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=n,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )

        self.Miner.miner_until_tx_committed(self.node1, tx_father_hash)

        # 1. Node 1 sends N transactions
        tx_n_hash_list = []
        tx_n_index_list = []
        for i in range(0, 1):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")
        self.Miner.miner_until_tx_committed(self.node2, tx_n_hash_list[-1], True)
        for i in range(1, n):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_father_hash],
                [hex(i)],
                account_private,
                output_count=1,
                fee=1090000,
                api_url=self.node1.getClient().url,
            )
            tx_n_hash_list.append(tx_hash)
            tx_n_index_list.append("0x0")

        # 2. After node 3 synchronizes, delete half of the child transactions on node 3
        self.Node.wait_get_transaction(self.node2, tx_n_hash_list[-1], "pending")
        remove_tx_list = []
        for i in range(2, n, 2):
            remove_tx = self.node2.getClient().get_transaction(tx_n_hash_list[i])
            self.node2.getClient().remove_transaction(tx_n_hash_list[i])
            remove_tx_list.append(remove_tx)

        # 3. On node 1, send a child transaction aggregation with a fee of 1090000
        tx_child_hash = self.Tx.send_transfer_self_tx_with_input(
            tx_n_hash_list,
            tx_n_index_list,
            account_private,
            output_count=1,
            fee=1090000,
            api_url=self.node1.getClient().url,
        )
        print(tx_child_hash)
        self.node1.getClient().get_transaction(tx_child_hash)
        time.sleep(5)

        # 4. Query that the orphan pool of node 3 is 1
        node1_tx_pool_info = self.node1.getClient().tx_pool_info()
        before_tx_pool_info = self.node2.getClient().tx_pool_info()
        print("remove size:", len(remove_tx_list))
        assert before_tx_pool_info["orphan"] == "0x1"

        # 5. On node 3, send a conflicting transaction with the aggregation transaction with a fee of 1000
        conflict_tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_n_hash_list[0]],
            [tx_n_index_list[0]],
            account_private,
            output_count=1,
            fee=1000,
            api_url=self.node2.getClient().url,
        )
        before_tx_pool_info = self.node2.getClient().tx_pool_info()

        # 6. Resend the deleted transaction
        for remove_tx in remove_tx_list:
            del remove_tx["transaction"]["hash"]
            self.node2.getClient().send_transaction(remove_tx["transaction"])

        # 7. Query the orphan pool
        time.sleep(3)
        node2_info = self.node2.getClient().tx_pool_info()

        # 8. Query the status of the aggregation transaction and the conflicting transaction
        conflict_tx = self.node2.getClient().get_transaction(conflict_tx_hash)
        orphan_tx = self.node2.getClient().get_transaction(tx_child_hash)
        assert orphan_tx["tx_status"]["status"] == "rejected"
