"""PR #5316 public live-cell query regression."""

from framework.basic import CkbTest


class TestLiveCellOverlay(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_live_cell_overlay/node1",
            8436,
            8437,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 100)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # The base copier uses a fixed report path; pytest captures this run's log.
        pass

    # TEST-MAP: NTD-75
    def test_pool_overlay_precedes_chain_and_with_data_only_changes_data(self):
        client = self.node.getClient()
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        fund_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            client.url,
            "1500",
        )
        self.Miner.miner_until_tx_committed(self.node, fund_hash)
        parent_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            self.Config.MINER_PRIVATE_1,
            data="0x01",
            fee=1000,
            api_url=client.url,
        )
        parent = client.get_transaction(parent_hash)
        assert parent["tx_status"]["status"] in ("pending", "proposed")
        previous_output = parent["transaction"]["inputs"][0]["previous_output"]

        # A chain cell already spent by an Accepted pool transaction is not live
        # when the caller asks for the pool overlay.
        chain_only = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            include_tx_pool=False,
        )
        chain_only_without_data = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            with_data=False,
            include_tx_pool=False,
        )
        with_pool_without_data = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            with_data=False,
            include_tx_pool=True,
        )
        with_pool = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            include_tx_pool=True,
        )
        assert chain_only["status"] == "live"
        assert chain_only_without_data["status"] == "live"
        assert chain_only_without_data["cell"]["output"] == chain_only["cell"]["output"]
        assert chain_only_without_data["cell"]["data"] is None
        assert with_pool["status"] == "unknown"
        assert with_pool["cell"] is None
        assert with_pool_without_data["status"] == "unknown"
        assert with_pool_without_data["cell"] is None

        # The Accepted output is visible only through the overlay. Omitting data
        # must not change its status or output fields.
        parent_with_data = client.get_live_cell_with_include_tx_pool(
            "0x0", parent_hash, with_data=True, include_tx_pool=True
        )
        parent_without_data = client.get_live_cell_with_include_tx_pool(
            "0x0", parent_hash, with_data=False, include_tx_pool=True
        )
        print("parent_without_data:", parent_without_data)
        assert client.get_live_cell("0x0", parent_hash)["status"] == "unknown"
        assert parent_with_data["status"] == "live"
        assert parent_without_data["status"] == "live"
        assert (
            parent_with_data["cell"]["output"] == parent_without_data["cell"]["output"]
        )
        assert parent_with_data["cell"]["data"]["content"] == "0x01"

        child_hash = self.Tx.send_transfer_self_tx_with_input(
            [parent_hash],
            ["0x0"],
            self.Config.MINER_PRIVATE_1,
            data="0x02",
            output_count=1,
            fee=1000,
            api_url=client.url,
        )
        assert client.get_transaction(child_hash)["tx_status"]["status"] in (
            "pending",
            "proposed",
        )
        spent_pool_output = client.get_live_cell_with_include_tx_pool(
            "0x0", parent_hash, include_tx_pool=True
        )
        spent_pool_without_data = client.get_live_cell_with_include_tx_pool(
            "0x0", parent_hash, with_data=False, include_tx_pool=True
        )
        assert spent_pool_output["status"] == "unknown"
        assert spent_pool_output["cell"] is None
        assert spent_pool_without_data["status"] == "unknown"
        assert spent_pool_without_data["cell"] is None

        self.Miner.miner_until_tx_committed(self.node, child_hash)
        chain_live = client.get_live_cell_with_include_tx_pool(
            "0x0", child_hash, include_tx_pool=False
        )
        chain_live_without_data = client.get_live_cell_with_include_tx_pool(
            "0x0", child_hash, with_data=False, include_tx_pool=False
        )
        committed_output = client.get_live_cell_with_include_tx_pool(
            "0x0", child_hash, include_tx_pool=True
        )
        committed_without_data = client.get_live_cell_with_include_tx_pool(
            "0x0", child_hash, with_data=False, include_tx_pool=True
        )
        spent_chain_output = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            include_tx_pool=True,
        )
        spent_chain_without_data = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            with_data=False,
            include_tx_pool=True,
        )
        chain_dead = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            include_tx_pool=False,
        )
        chain_dead_without_data = client.get_live_cell_with_include_tx_pool(
            previous_output["index"],
            previous_output["tx_hash"],
            with_data=False,
            include_tx_pool=False,
        )
        unknown_output = client.get_live_cell_with_include_tx_pool(
            "0xffff", child_hash, include_tx_pool=True
        )
        unknown_without_data = client.get_live_cell_with_include_tx_pool(
            "0xffff", child_hash, with_data=False, include_tx_pool=True
        )
        assert chain_live["status"] == "live"
        assert chain_live_without_data["status"] == "live"
        assert chain_live["cell"]["output"] == chain_live_without_data["cell"]["output"]
        assert chain_live["cell"]["data"]["content"] == "0x02"
        assert chain_live_without_data["cell"]["data"] is None
        assert committed_output["status"] == "live"
        assert committed_without_data["status"] == "live"
        assert committed_output["cell"]["output"] == chain_live["cell"]["output"]
        assert committed_without_data["cell"]["output"] == chain_live["cell"]["output"]
        assert committed_output["cell"]["data"]["content"] == "0x02"
        assert committed_without_data["cell"]["data"] is None
        assert chain_dead["status"] == "unknown"
        assert chain_dead["cell"] is None
        assert chain_dead_without_data["status"] == "unknown"
        assert chain_dead_without_data["cell"] is None
        assert spent_chain_output["status"] == "unknown"
        assert spent_chain_output["cell"] is None
        assert spent_chain_without_data["status"] == "unknown"
        assert spent_chain_without_data["cell"] is None
        assert unknown_output["status"] == "unknown"
        assert unknown_output["cell"] is None
        assert unknown_without_data["status"] == "unknown"
        assert unknown_without_data["cell"] is None
        # Regression: https://github.com/nervosnetwork/ckb/issues/5335
        # A pool-backed live cell should omit data when with_data=False.
        assert parent_without_data["cell"]["data"] is None
        self.did_pass = True
