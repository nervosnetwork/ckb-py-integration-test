from framework.basic import CkbTest


class TestGetLiveCell(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. start 1 ckb node in tmp/livecell/node1 node dir
        2. miner 100block
        Returns:

        """
        # 1. start 1 ckb node in tmp/feature/gene_rate_epochs node dir
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "livecell/node1", 8120, 8225
        )
        cls.node.prepare(other_ckb_config={"ckb_tx_pool_max_tx_pool_size": "180_000"})
        cls.node.start()
        # 2. miner 100block
        cls.Miner.make_tip_height_number(cls.node, 100)

    @classmethod
    def teardown_class(cls):
        """
        1. stop ckb node
        2. clean ckb node  tmp dir
        Returns:

        """
        cls.node.stop()
        cls.node.clean()

    def test_get_live_cell_with_unspend(self):
        """
        1. get cells and tx is pending and with unspend
        2. query cell status will be live
        Returns:

        """
        # 1. get cells and tx is pending and with unspend
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )
        print(f"txHash:{tx_hash}")
        # 2. query cell status will be live
        transaction = self.node.getClient().get_transaction(tx_hash)
        result = self.node.getClient().get_live_cell_with_include_tx_pool(
            transaction["transaction"]["inputs"][0]["previous_output"]["index"],
            transaction["transaction"]["inputs"][0]["previous_output"]["tx_hash"],
        )
        assert result["status"] == "live"

    def test_get_live_cell_with_spend(self):
        """
        1. generate account and build normal tx
        2. get live cells and cell is spend
        3. send link tx and test_tx_pool_accept check success
        4. query cell status will be unknown
        Returns:

        """
        # 1. generate account and build normal tx
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
        tx_hash = father_tx_hash
        # 3. send link tx and test_tx_pool_accept check success
        for i in range(3):
            tx = self.Tx.build_send_transfer_self_tx_with_input(
                [tx_hash],
                [hex(0)],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=1100090 + i * 1000,
                api_url=self.node.getClient().url,
            )
            self.node.getClient().test_tx_pool_accept(tx, "passthrough")
            self.node.getClient().send_transaction(tx)
            transaction = self.node.getClient().get_transaction(father_tx_hash)
            result = self.node.getClient().get_live_cell_with_include_tx_pool(
                transaction["transaction"]["inputs"][0]["previous_output"]["index"],
                transaction["transaction"]["inputs"][0]["previous_output"]["tx_hash"],
                include_tx_pool=True,
            )
            # 4. query cell status will be unknown
            assert result["status"] == "unknown"
            assert result["block_hash"] is None

    def test_get_live_cell_block_hash(self):
        """Verify CKB #5269 block_hash semantics across a cell lifecycle."""
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )
        transaction = self.node.getClient().get_transaction(tx_hash)
        previous_output = transaction["transaction"]["inputs"][0]["previous_output"]

        # TP-RPC-LIVE-CELL-001: a pool-only cell is unknown unless the pool is included.
        pending_output_without_pool = self.node.getClient().get_live_cell(
            "0x0", tx_hash
        )
        assert pending_output_without_pool["status"] == "unknown"
        assert pending_output_without_pool["cell"] is None
        assert pending_output_without_pool["block_hash"] is None

        # TP-RPC-LIVE-CELL-002: an uncommitted live cell has no containing block.
        pending_output = self.node.getClient().get_live_cell_with_include_tx_pool(
            "0x0", tx_hash, include_tx_pool=True
        )
        assert pending_output["status"] == "live"
        assert pending_output["cell"] is not None
        assert pending_output["block_hash"] is None

        # TP-RPC-LIVE-CELL-003: a committed live cell reports its creation block.
        committed = self.Miner.miner_until_tx_committed(self.node, tx_hash)
        creation_block_hash = committed["tx_status"]["block_hash"]
        committed_output = self.node.getClient().get_live_cell("0x0", tx_hash)
        assert committed_output["status"] == "live"
        assert committed_output["cell"] is not None
        assert committed_output["block_hash"] == creation_block_hash

        # TP-RPC-LIVE-CELL-004: omitting cell data must not omit block_hash.
        committed_output_without_data = self.node.getClient().get_live_cell(
            "0x0", tx_hash, with_data=False
        )
        assert committed_output_without_data["status"] == "live"
        assert committed_output_without_data["cell"] is not None
        assert committed_output_without_data["cell"]["data"] is None
        assert committed_output_without_data["block_hash"] == creation_block_hash

        # The returned hash remains the creation block after the chain tip advances.
        self.Miner.miner_with_version(self.node, "0x0")
        assert self.node.getClient().get_tip_header()["hash"] != creation_block_hash
        assert (
            self.node.getClient().get_live_cell("0x0", tx_hash)["block_hash"]
            == creation_block_hash
        )

        # TP-RPC-LIVE-CELL-005: spent and nonexistent cells have no block_hash.
        spent_input = self.node.getClient().get_live_cell(
            previous_output["index"], previous_output["tx_hash"]
        )
        assert spent_input["status"] == "unknown"
        assert spent_input["cell"] is None
        assert spent_input["block_hash"] is None

        unknown_output = self.node.getClient().get_live_cell("0xffff", tx_hash)
        assert unknown_output["status"] == "unknown"
        assert unknown_output["cell"] is None
        assert unknown_output["block_hash"] is None
