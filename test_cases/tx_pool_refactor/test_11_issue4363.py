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

    def test_earlier_readers_commit_and_later_readers_are_rejected(self):
        """
        Issue #4363 protects a cell from being pinned by continuing dep readers.
        Admission order fixes the reader set: preserve earlier readers, reject
        later readers, and commit the spender after the retained readers even
        when they do not fit in one block. Readers are not causal ancestors.
        """
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

        # Commit the shared dependency cell.
        tx_a_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            1000000,
            self.node1.getClient().url,
            "1500000",
        )
        self.Miner.miner_until_tx_committed(self.node1, tx_a_hash)
        # Exceed the legacy 1,000-ancestor threshold with independent readers.
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

        # Keep input descendants and dependency descendants of the cheapest readers.
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

        earlier = set(tx1_list + tx2_list + tx22_list + tx3_list)
        for node in (self.node1, self.node2):
            self._wait_pool(node, earlier)

        # Establish that fresh, distinct readers are valid before the spend.
        later = [
            self.Tx.build_send_transfer_self_tx_with_input(
                [tx_live_cell_hash],
                [hex(index)],
                account_private,
                output_count=3,
                fee=1_000_090,
                api_url=self.node1.getClient().url,
                dep_cells=[{"tx_hash": tx_a_hash, "index_hex": "0x0"}],
            )
            for index in range(1005, 1008)
        ]
        for node in (self.node1, self.node2):
            for transaction in later:
                node.getClient().test_tx_pool_accept(transaction, "passthrough")

        tx_a_cost_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_a_hash],
            ["0x0"],
            account_private,
            output_count=1,
            fee=100090,
            api_url=self.node1.getClient().url,
        )
        expected = earlier | {tx_a_cost_hash}
        for node in (self.node1, self.node2):
            self._wait_pool(node, expected)
            self._assert_late_readers_rejected(node, later)
            self._wait_pool(node, expected)

        # Keep attempting reads while mining. A full block may postpone the
        # spender, but must not let it invalidate readers accepted before it.
        committed = set()
        readers = set(tx1_list)
        for _ in range(10):
            if tx_a_cost_hash not in committed:
                self._assert_late_readers_rejected(self.node1, later)
            self.Miner.miner_with_version(self.node1, "0x0")
            header = self.node1.getClient().get_tip_header()
            block = self.node1.getClient().get_block(header["hash"])
            for transaction in block["transactions"]:
                tx_hash = transaction["hash"]
                if tx_hash == tx_a_cost_hash:
                    assert (
                        readers <= committed
                    ), "the spender overtook an earlier reader"
                committed.add(tx_hash)
            self.Node.wait_node_height(self.node2, int(header["number"], 16), 30)
            assert (
                self.node2.getClient().get_block_hash(header["number"])
                == header["hash"]
            )
            if expected <= committed:
                break
        assert expected <= committed, "finite earlier readers must not pin the dep cell"
        for node in (self.node1, self.node2):
            status = node.getClient().get_transaction(tx_a_cost_hash)["tx_status"]
            assert status["status"] == "committed"
            self._wait_pool(node, set())
        self.did_pass = True

    @staticmethod
    def _assert_late_readers_rejected(node, transactions):
        for transaction in transactions:
            with pytest.raises(Exception, match=r"TransactionFailedToResolve.*Dead"):
                node.getClient().send_transaction(transaction)

    @staticmethod
    def _wait_pool(node, expected):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            pool = node.getClient().get_raw_tx_pool()
            actual = set(pool["pending"]) | set(pool["proposed"])
            if actual == expected:
                return
            time.sleep(0.1)
        raise AssertionError(
            f"pool mismatch: missing={expected - actual}, unexpected={actual - expected}"
        )
