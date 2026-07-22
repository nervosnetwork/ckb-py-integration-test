import time

from framework.basic import CkbTest


class TestPipelineReorg(CkbTest):
    """End-to-end recovery checks for dependent transactions after a reorg."""

    @classmethod
    def setup_class(cls):
        # Do not use 8114 here: another local test project may run a long-lived
        # ckb miner against that RPC endpoint.
        cls.node1 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pipeline_reorg/node1",
            8124,
            8125,
        )
        cls.node2 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pipeline_reorg/node2",
            8126,
            8127,
        )
        try:
            cls.node1.prepare()
            cls.node2.prepare()
            cls.node1.start()
            cls.node2.start()
            cls.Miner.make_tip_height_number(cls.node1, 30)
            cls.node1.connected(cls.node2)
            cls.Node.wait_node_height(cls.node2, 30, 60)

            cls.node2.getClient().set_network_active(False)
        except Exception:
            cls.node1.stop()
            cls.node2.stop()
            cls.node1.clean()
            cls.node2.clean()
            raise

    @classmethod
    def teardown_class(cls):
        cls.node1.stop()
        cls.node2.stop()
        cls.node1.clean()
        cls.node2.clean()

    def setup_method(self, method):
        self.node1.getClient().clear_tx_pool()
        self.node2.getClient().clear_tx_pool()

    @staticmethod
    def _wait_for_pool_tip(node):
        for _ in range(60):
            pool_info = node.getClient().tx_pool_info()
            if int(pool_info["tip_number"], 16) == int(
                node.getClient().get_tip_block_number()
            ):
                return pool_info
            time.sleep(1)
        raise AssertionError("tx-pool did not catch up with the chain tip")

    @staticmethod
    def _wait_for_tip_hash(node, tip_hash):
        for _ in range(60):
            if node.getClient().get_tip_header()["hash"] == tip_hash:
                return
            time.sleep(1)
        raise AssertionError("node did not switch to the competing chain")

    def test_reorg_recovers_dependent_pending_tree(self):
        """
        A confirmed parent -> child -> grandchild chain must be restored to
        pending in dependency order after a competing longer chain wins.
        """
        account_private = self.Config.ACCOUNT_PRIVATE_1
        account = self.Ckb_cli.util_key_info_by_private_key(account_private)

        parent_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            account_private,
            account["address"]["testnet"],
            1_000_000,
            self.node1.getClient().url,
            "1500000",
        )
        child_hash = self.Tx.send_transfer_self_tx_with_input(
            [parent_hash],
            ["0x0"],
            account_private,
            output_count=2,
            fee=1090,
            api_url=self.node1.getClient().url,
        )
        grandchild_hash = self.Tx.send_transfer_self_tx_with_input(
            [child_hash],
            ["0x0"],
            account_private,
            output_count=2,
            fee=1090,
            api_url=self.node1.getClient().url,
        )
        tx_hashes = [parent_hash, child_hash, grandchild_hash]

        self.Node.wait_tx_pool(self.node1, "pending", len(tx_hashes))
        assert self.node1.getClient().tx_pool_info()["pending"] == "0x3"

        self.Miner.miner_until_tx_committed(self.node1, grandchild_hash)
        for tx_hash in tx_hashes:
            self.Node.wait_get_transaction(self.node1, tx_hash, "committed")

        while int(self.node2.getClient().get_tip_block_number()) <= int(
            self.node1.getClient().get_tip_block_number()
        ):
            self.Miner.miner_with_version(self.node2, "0x0")

        winning_tip_hash = self.node2.getClient().get_tip_header()["hash"]
        self.node2.getClient().set_network_active(True)
        self.node1.connected(self.node2)
        self._wait_for_tip_hash(self.node1, winning_tip_hash)
        self._wait_for_pool_tip(self.node1)

        self.Node.wait_tx_pool(self.node1, "pending", len(tx_hashes))
        pool_info = self.node1.getClient().tx_pool_info()
        assert pool_info["pending"] == "0x3"
        assert pool_info["orphan"] == "0x0"
        for tx_hash in tx_hashes:
            self.Node.wait_get_transaction(self.node1, tx_hash, "pending")

        self.Miner.miner_until_tx_committed(self.node1, grandchild_hash)
        for tx_hash in tx_hashes:
            self.Node.wait_get_transaction(self.node1, tx_hash, "committed")

        self.did_pass = True
