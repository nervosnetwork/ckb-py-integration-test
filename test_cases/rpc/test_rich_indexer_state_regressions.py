"""Rich-indexer regression coverage for CKB commits 1f4a9a and 13c96b0."""

from decimal import Decimal
import time

import toml

from framework.basic import CkbTest
from framework.helper.contract import (
    deploy_ckb_contract,
    get_ckb_contract_codehash,
    invoke_ckb_contract,
)
from framework.helper.node import wait_cluster_height
from framework.util import get_project_root


class TestRichIndexerStateRegressions(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"rpc/rich_indexer_state_regressions/node{i}",
                18416 + i,
                18527 + i,
            )
            for i in range(2)
        ]
        cls.full_node = cls.nodes[0]
        cls.rich_node = cls.nodes[1]
        cls.cluster = cls.Cluster(cls.nodes)
        cls.cluster.clean_all_nodes()
        cls.cluster.prepare_all_nodes()

        config = toml.load(cls.rich_node.ckb_toml_path)
        config.setdefault("indexer_v2", {})["index_tx_pool"] = True
        with open(cls.rich_node.ckb_toml_path, "w") as config_file:
            toml.dump(config, config_file)

        cls.full_node.start()
        cls.rich_node.startWithRichIndexer()
        cls.cluster.connected_all_nodes()
        cls.Miner.make_tip_height_number(cls.full_node, 30)
        wait_cluster_height(cls.cluster, 30, 60)
        cls._wait_for_indexer_tip(cls.full_node.getClient().get_tip_header()["hash"])

        cls.deploy_tx = deploy_ckb_contract(
            cls.Config.ACCOUNT_PRIVATE_1,
            f"{get_project_root()}/source/contract/always_success",
            enable_type_id=True,
            api_url=cls.full_node.getClient().url,
        )
        cls.Miner.miner_until_tx_committed(cls.full_node, cls.deploy_tx)
        wait_cluster_height(
            cls.cluster, cls.full_node.getClient().get_tip_block_number(), 60
        )
        cls._wait_for_indexer_tip(cls.full_node.getClient().get_tip_header()["hash"])
        cls.code_hash = get_ckb_contract_codehash(
            cls.deploy_tx,
            0,
            enable_type_id=True,
            api_url=cls.full_node.getClient().url,
        )
        cls.account = cls.Ckb_cli.util_key_info_by_private_key(
            cls.Config.ACCOUNT_PRIVATE_1
        )
        funding_cell = cls._account_plain_live_cell(
            min_capacity=4 * 100 * 100_000_000 + 4_000
        )
        cls.funding_tx = cls.Tx.send_transfer_self_tx_with_input(
            [funding_cell["tx_hash"]],
            [hex(funding_cell["output_index"])],
            cls.Config.ACCOUNT_PRIVATE_1,
            fee=4_000,
            output_count=4,
            api_url=cls.full_node.getClient().url,
        )
        cls.Miner.miner_until_tx_committed(cls.full_node, cls.funding_tx)
        assert (
            len(
                cls.full_node.getClient().get_transaction(cls.funding_tx)[
                    "transaction"
                ]["outputs"]
            )
            >= 4
        )
        wait_cluster_height(
            cls.cluster, cls.full_node.getClient().get_tip_block_number(), 60
        )
        cls._wait_for_indexer_tip(cls.full_node.getClient().get_tip_header()["hash"])

    @classmethod
    def teardown_class(cls):
        cls.cluster.stop_all_nodes()
        cls.cluster.clean_all_nodes()

    def test_01_rollback_keeps_type_script_referenced_by_earlier_live_cell(self):
        """TP-INTEGRATION-RICH-INDEXER-ROLLBACK-001."""
        full_client = self.full_node.getClient()
        rich_client = self.rich_node.getClient()
        type_args = "0x1f4a9a"

        earlier_tx = invoke_ckb_contract(
            account_private=self.Config.ACCOUNT_PRIVATE_1,
            contract_out_point_tx_hash=self.deploy_tx,
            contract_out_point_tx_index=0,
            type_script_arg=type_args,
            data="0x01",
            hash_type="type",
            api_url=full_client.url,
            input_cells=[{"tx_hash": self.funding_tx, "index": 0}],
            output_lock_arg=self.account["lock_arg"],
        )
        self.Miner.miner_until_tx_committed(self.full_node, earlier_tx)
        wait_cluster_height(self.cluster, full_client.get_tip_block_number(), 60)
        self._wait_for_indexer_tip(full_client.get_tip_header()["hash"])

        rich_client.set_network_active(False)
        try:
            rolled_back_tx = invoke_ckb_contract(
                account_private=self.Config.ACCOUNT_PRIVATE_1,
                contract_out_point_tx_hash=self.deploy_tx,
                contract_out_point_tx_index=0,
                type_script_arg=type_args,
                data="0x02",
                hash_type="type",
                api_url=rich_client.url,
                input_cells=[{"tx_hash": self.funding_tx, "index": 1}],
                output_lock_arg=self.account["lock_arg"],
            )
            self.Miner.miner_until_tx_committed(self.rich_node, rolled_back_tx)
            self._wait_for_indexer_tip(rich_client.get_tip_header()["hash"])

            assert self._query_type_cells(type_args) == [
                {"tx_hash": earlier_tx, "index": "0x0"},
                {"tx_hash": rolled_back_tx, "index": "0x0"},
            ]

            self.Miner.make_tip_height_number(
                self.full_node, rich_client.get_tip_block_number() + 1
            )
            full_tip_hash = full_client.get_tip_header()["hash"]
        finally:
            rich_client.set_network_active(True)
            self.full_node.connected(self.rich_node)
            self.rich_node.connected(self.full_node)

        self._wait_for_node_tip(self.rich_node, full_tip_hash)
        self._wait_for_indexer_tip(full_tip_hash)

        assert self._query_type_cells(type_args) == [
            {"tx_hash": earlier_tx, "index": "0x0"}
        ]

    def test_02_pool_overlay_binds_dead_cells_before_output_data_filter(self):
        """TP-INTEGRATION-RICH-INDEXER-POOL-OVERLAY-001."""
        client = self.rich_node.getClient()
        type_args = "0x13c96b"
        source_tx = invoke_ckb_contract(
            account_private=self.Config.ACCOUNT_PRIVATE_1,
            contract_out_point_tx_hash=self.deploy_tx,
            contract_out_point_tx_index=0,
            type_script_arg=type_args,
            data="0x02",
            hash_type="type",
            api_url=client.url,
            input_cells=[{"tx_hash": self.funding_tx, "index": 2}],
            output_lock_arg=self.account["lock_arg"],
        )
        self.Miner.miner_until_tx_committed(self.rich_node, source_tx)
        assert self._query_type_cells(type_args) == [
            {"tx_hash": source_tx, "index": "0x0"}
        ]

        pending_tx = self.Tx.send_transfer_self_tx_with_input(
            [source_tx],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            fee=1_000,
            api_url=client.url,
            dep_cells=[{"tx_hash": self.deploy_tx, "index_hex": "0x0"}],
        )
        self.Node.wait_get_transaction(self.rich_node, pending_tx, "pending")

        try:
            assert self._query_type_cells(type_args) == []

            filtered_search_key = self._type_search_key(type_args)
            filtered_search_key["filter"] = {
                "output_data": "0x00",
                "output_data_filter_mode": "prefix",
            }
            cells = client.get_cells(filtered_search_key, "asc", "0xff", None)
            capacity = client.get_cells_capacity(filtered_search_key)

            assert cells["objects"] == []
            assert capacity is None
            assert (
                client.get_transaction(pending_tx)["tx_status"]["status"] == "pending"
            )
        finally:
            client.remove_transaction(pending_tx)

    def _query_type_cells(self, type_args):
        cells = self.rich_node.getClient().get_cells(
            self._type_search_key(type_args), "asc", "0xff", None
        )
        return [cell["out_point"] for cell in cells["objects"]]

    def _type_search_key(self, type_args):
        return {
            "script": {
                "code_hash": self.code_hash,
                "hash_type": "type",
                "args": type_args,
            },
            "script_type": "type",
            "script_search_mode": "exact",
        }

    @classmethod
    def _account_plain_live_cell(cls, min_capacity=0):
        live_cells = cls.Ckb_cli.wallet_get_live_cells(
            cls.account["address"]["testnet"],
            api_url=cls.full_node.getClient().url,
        )["live_cells"]
        for live_cell in live_cells:
            if live_cell["tx_hash"] == cls.deploy_tx and live_cell["output_index"] == 0:
                continue
            if live_cell.get("type_hashes") is None:
                if cls._live_cell_capacity(live_cell) >= min_capacity:
                    return live_cell
        raise AssertionError("account has no plain live cell for rich-indexer funding")

    @staticmethod
    def _live_cell_capacity(live_cell):
        return int(
            Decimal(live_cell["capacity"].replace("(CKB)", "").strip()) * 100_000_000
        )

    @classmethod
    def _wait_for_indexer_tip(cls, expected_hash, timeout=60):
        deadline = time.monotonic() + timeout
        last_tip = None
        while time.monotonic() < deadline:
            last_tip = cls.rich_node.getClient().get_indexer_tip()
            if last_tip is not None and last_tip["block_hash"] == expected_hash:
                return
            time.sleep(0.5)
        raise TimeoutError(
            f"rich-indexer did not reach block {expected_hash}; last tip: {last_tip}"
        )

    @staticmethod
    def _wait_for_node_tip(node, expected_hash, timeout=60):
        deadline = time.monotonic() + timeout
        last_tip = None
        while time.monotonic() < deadline:
            last_tip = node.getClient().get_tip_header()["hash"]
            if last_tip == expected_hash:
                return
            time.sleep(0.5)
        raise TimeoutError(
            f"node did not reach block {expected_hash}; last tip: {last_tip}"
        )
