import time

import pytest

from framework.basic import CkbTest


class TestIssue4363(CkbTest):
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
        cls.node2.connected(cls.node1)
        cls.Miner.make_tip_height_number(cls.node1, 300)
        cls.Node.wait_node_height(cls.node2, 300, 300)

    @classmethod
    def teardown_class(cls):
        cls.node1.stop()
        cls.node1.clean()
        cls.node2.stop()
        cls.node2.clean()

    def test_01_4363(self):
        """
        https://github.com/nervosnetwork/ckb/blob/develop/util/app-config/src/legacy/tx_pool.rs#L123
        DEFAULT_MAX_ANCESTORS_COUNT = 1000
        https://github.com/nervosnetwork/ckb/pull/4363/files
        When inserting a cellDep, if the chain is too long, excess transactions in the chain will be deleted.
        0. Generate 250 live cells and cell=a
        1. Send 200 transactions tx1(cellDep=a)
        2. Send tx2(input = low-fee tx1.output(1 || 2 || 3 || 4 || 5))
        3. Send tx3(cellDep = low-fee tx1.output(1 || 2 || 3 || 4 || 5))
        4. Send tx4(consuming a)
        5. Query low-fee tx1 reports PoolRejectedInvalidated
        6. Query tx2 reports PoolRejectedInvalidated
        7. Query tx3 reports PoolRejectedInvalidated
        """
        # 0. Generate 250 live cells and cell=a
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        account_private = self.Config.ACCOUNT_PRIVATE_1
        tx1_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            account_private,
            account["address"]["testnet"],
            10000000,
            self.node1.getClient().url,
            "15000000",
        )
        tx_live_cell_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx1_hash],
            ["0x0"],
            account_private,
            output_count=2005,
            fee=1000090,
            api_url=self.node1.getClient().url,
        )
        self.Miner.miner_until_tx_committed(self.node1, tx_live_cell_hash)
        tx2_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "15000000",
        )
        tx_3_father_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx2_hash],
            ["0x0"],
            account_private,
            output_count=5,
            fee=1000090,
            api_url=self.node1.getClient().url,
        )
        self.Miner.miner_until_tx_committed(self.node1, tx_3_father_hash)

        # 0. Generate cell a
        tx_a_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "1500000",
        )
        self.Miner.miner_until_tx_committed(self.node1, tx_a_hash)
        # 1. Send 200 transactions tx1(cellDep=a)
        tx1_list = []
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_live_cell_hash],
            [hex(0)],
            account_private,
            output_count=3,
            fee=3090,
            api_url=self.node1.getClient().url,
            dep_cells=[{"tx_hash": tx_a_hash, "index_hex": "0x0"}],
        )
        tx1_list.append(tx_hash)
        for i in range(1, 1005):
            print("current i:", i)
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_live_cell_hash],
                [hex(i)],
                account_private,
                output_count=3,
                fee=100090 + i * 1000,
                api_url=self.node1.getClient().url,
                dep_cells=[{"tx_hash": tx_a_hash, "index_hex": "0x0"}],
            )
            tx1_list.append(tx_hash)

        # 2. Send tx2(input = low-fee tx1.output(1 || 2 || 3 || 4 || 5))
        tx2_list = []
        tx22_list = []
        for i in range(3):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx1_list[0]],
                [hex(i)],
                account_private,
                output_count=2,
                fee=3090 + i * 1000,
                api_url=self.node1.getClient().url,
            )
            tx2_list.append(tx_hash)
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                [hex(1)],
                account_private,
                output_count=1,
                fee=3090 + i * 1000,
                api_url=self.node1.getClient().url,
            )
            tx22_list.append(tx_hash)

        # 3. Send tx3(cellDep = low-fee tx1.output(1 || 2 || 3 || 4 || 5))
        tx3_list = []
        for i in range(3):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_3_father_hash],
                [hex(i)],
                account_private,
                output_count=2,
                fee=3090 + i * 1000,
                api_url=self.node1.getClient().url,
                dep_cells=[{"tx_hash": tx1_list[1], "index_hex": "0x0"}],
            )
            tx3_list.append(tx_hash)

        # 4. Send tx4(consuming a)
        tx_a_cost_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_a_hash],
            ["0x0"],
            account_private,
            output_count=1,
            fee=100090,
            api_url=self.node1.getClient().url,
        )
        # TODO remove sleep
        time.sleep(10)
        # 5. Query low-fee tx1 reports PoolRejectedInvalidated
        print("---- tx1_list------")
        pending_status = 0
        rejected_status = 0
        for tx_hash in tx1_list:
            response = self.node1.getClient().get_transaction(tx_hash)
            response2 = self.node2.getClient().get_transaction(tx_hash)
            assert response["tx_status"]["status"] == response2["tx_status"]["status"]
            if response["tx_status"]["status"] == "pending":
                pending_status += 1
            if response["tx_status"]["status"] == "rejected":
                rejected_status += 1
        assert pending_status == 999
        assert rejected_status == 6
        # 6. Query tx2 reports PoolRejectedInvalidated
        print("---- tx2_hash------")
        for tx_hash in tx2_list:
            response = self.node1.getClient().get_transaction(tx_hash)
            response2 = self.node2.getClient().get_transaction(tx_hash)
            assert response["tx_status"]["status"] == response2["tx_status"]["status"]
            assert response["tx_status"]["status"] == "rejected"

        print("---- tx22_list------")
        for tx_hash in tx22_list:
            response = self.node1.getClient().get_transaction(tx_hash)
            response2 = self.node2.getClient().get_transaction(tx_hash)
            # unknown: the transaction (tx) did not broadcast to node 2.
            assert (
                response2["tx_status"]["status"] == "rejected"
                or response2["tx_status"]["status"] == "unknown"
            )
            assert response["tx_status"]["status"] == "rejected"

        # 7. Query tx3 reports PoolRejectedInvalidated
        print("---- tx3_list------")
        for tx_hash in tx3_list:
            response = self.node1.getClient().get_transaction(tx_hash)
            response2 = self.node2.getClient().get_transaction(tx_hash)
            # unknown: the transaction (tx) did not broadcast to node 2.
            assert (
                response2["tx_status"]["status"] == "rejected"
                or response2["tx_status"]["status"] == "unknown"
            )
            assert response["tx_status"]["status"] == "rejected"
