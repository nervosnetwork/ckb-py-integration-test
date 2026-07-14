"""Regression test for https://github.com/nervosnetwork/ckb/pull/5245.

A custom cell filter can leave some previous outputs absent from rich-indexer.
When one transaction spends an unindexed output before an indexed output, the
input loop must skip the missing output and continue processing later inputs.
The old `break` left the indexed output incorrectly visible as a live cell.
"""

import os
import time

import toml

from framework.basic import CkbTest
from framework.helper.contract import (
    deploy_ckb_contract,
    get_ckb_contract_codehash,
)
from framework.helper.node import wait_cluster_height
from framework.util import get_project_root

SOURCE_TX_FEE_RESERVE = 2_000


class TestRichIndexerMixedInputs(CkbTest):

    @classmethod
    def setup_class(cls):
        nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"rich_indexer_mixed_inputs/node{i}",
                8138 + i,
                8238 + i,
            )
            for i in range(2)
        ]
        cls.cluster = cls.Cluster(nodes)
        nodes[0].prepare()
        nodes[1].prepare()

        # Only the rich-indexer node filters cells. The full node remains
        # unfiltered so it can fund, build, and submit the test transactions.
        rich_indexer_config = toml.load(nodes[1].ckb_toml_path)
        rich_indexer_config["indexer_v2"] = {
            "cell_filter": 'output.type?.args == "0x01"'
        }
        with open(nodes[1].ckb_toml_path, "w") as config_file:
            toml.dump(rich_indexer_config, config_file)
        nodes[0].start()
        nodes[1].startWithRichIndexer()
        cls.cluster.connected_all_nodes()
        cls.Miner.make_tip_height_number(nodes[0], 100)
        wait_cluster_height(cls.cluster, 100, 60)
        cls.wait_for_indexer(nodes[0])
        cls.wait_for_indexer(nodes[1])

    @classmethod
    def teardown_class(cls):
        cls.cluster.stop_all_nodes()
        cls.cluster.clean_all_nodes()

    @staticmethod
    def wait_for_indexer(node, timeout=60):
        target_number = node.getClient().get_tip_block_number()
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            indexer_tip = node.getClient().get_indexer_tip()
            if (
                indexer_tip is not None
                and int(indexer_tip["block_number"], 16) >= target_number
            ):
                return
            time.sleep(0.5)

        raise TimeoutError(
            f"rich-indexer did not reach block {target_number} within {timeout}s"
        )

    def test_continues_after_unindexed_input(self):
        """A missing first input must not hide a later indexed spend."""
        full_node = self.cluster.ckb_nodes[0]
        rich_node = self.cluster.ckb_nodes[1]
        client = full_node.getClient()
        rich_client = rich_node.getClient()
        api_url = client.url
        deploy_account_private = self.Config.ACCOUNT_PRIVATE_1
        account_private = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(account_private)

        deploy_tx = deploy_ckb_contract(
            deploy_account_private,
            f"{get_project_root()}/source/contract/always_success",
            enable_type_id=True,
            api_url=api_url,
        )
        self.Miner.miner_until_tx_committed(full_node, deploy_tx)
        self.wait_for_indexer(full_node)

        code_hash = get_ckb_contract_codehash(
            deploy_tx, 0, enable_type_id=True, api_url=api_url
        )
        genesis = client.get_block_by_number("0x0")
        funding_tx, funding_index, funding_output = next(
            (
                (tx["hash"], output_index, output)
                for tx in genesis["transactions"]
                for output_index, output in enumerate(tx["outputs"])
                if output["lock"]["args"] == account["lock_arg"]
            ),
            (None, None, None),
        )
        assert funding_output is not None, "genesis funding cell not found"

        output_capacity = (
            int(funding_output["capacity"], 16) - SOURCE_TX_FEE_RESERVE
        ) // 2
        source_tx_file = f"/tmp/rich-indexer-mixed-inputs-{time.time_ns()}.json"
        try:
            self.Ckb_cli.tx_init(source_tx_file, api_url)
            self.Ckb_cli.tx_add_multisig_config(
                account["address"]["testnet"], source_tx_file, api_url
            )
            self.Ckb_cli.tx_add_input(
                funding_tx, funding_index, source_tx_file, api_url
            )

            # output[0] has type args 0x00 and is excluded by the filter.
            # output[1] has type args 0x01 and is retained by rich-indexer.
            for type_args in ("0x00", "0x01"):
                self.Ckb_cli.tx_add_output(
                    {
                        "capacity": hex(output_capacity),
                        "lock": funding_output["lock"],
                        "type": {
                            "code_hash": code_hash,
                            "hash_type": "type",
                            "args": type_args,
                        },
                    },
                    "0x",
                    source_tx_file,
                )

            self.Ckb_cli.tx_add_cell_dep(deploy_tx, "0x0", source_tx_file)
            sign_data = self.Ckb_cli.tx_sign_inputs(
                account_private, source_tx_file, api_url
            )
            self.Ckb_cli.tx_add_signature(
                sign_data[0]["lock-arg"],
                sign_data[0]["signature"],
                source_tx_file,
                api_url,
            )
            source_tx = self.Ckb_cli.tx_send(source_tx_file, api_url).strip()
        finally:
            if os.path.exists(source_tx_file):
                os.remove(source_tx_file)

        self.Miner.miner_until_tx_committed(full_node, source_tx)

        wait_cluster_height(
            self.cluster, full_node.getClient().get_tip_block_number(), 60
        )
        self.wait_for_indexer(rich_node)

        search_key = {
            "script": {
                "code_hash": code_hash,
                "hash_type": "type",
                "args": "0x01",
            },
            "script_type": "type",
            "script_search_mode": "exact",
        }
        cells_before_spend = rich_client.get_cells(search_key, "asc", "0xff", None)
        assert [cell["out_point"] for cell in cells_before_spend["objects"]] == [
            {"tx_hash": source_tx, "index": "0x1"}
        ]

        # Querying by their shared lock proves the filter excluded output[0],
        # instead of merely proving that output[1] matches the type search.
        cells_for_lock = rich_client.get_cells(
            {
                "script": funding_output["lock"],
                "script_type": "lock",
                "script_search_mode": "exact",
            },
            "asc",
            "0xff",
            None,
        )
        assert [cell["out_point"] for cell in cells_for_lock["objects"]] == [
            {"tx_hash": source_tx, "index": "0x1"}
        ]

        # The unindexed output is deliberately first. With the old `break`,
        # rich-indexer never reaches the indexed output at input[1].
        # The helper creates a lock-only change output, so this transaction
        # cannot introduce another cell matching type args 0x01.
        mixed_inputs_tx = self.Tx.send_transfer_self_tx_with_input(
            [source_tx, source_tx],
            ["0x0", "0x1"],
            account_private,
            data="0x00",
            fee=1000,
            api_url=api_url,
            dep_cells=[{"tx_hash": deploy_tx, "index_hex": "0x0"}],
        )
        self.Miner.miner_until_tx_committed(full_node, mixed_inputs_tx)

        mixed_inputs = client.get_transaction(mixed_inputs_tx)["transaction"]["inputs"]
        assert [
            (
                cell_input["previous_output"]["tx_hash"],
                cell_input["previous_output"]["index"],
            )
            for cell_input in mixed_inputs
        ] == [(source_tx, "0x0"), (source_tx, "0x1")]

        wait_cluster_height(
            self.cluster, full_node.getClient().get_tip_block_number(), 60
        )
        self.wait_for_indexer(rich_node)
        cells_after_spend = rich_client.get_cells(search_key, "asc", "0xff", None)

        # `continue` processes input[1], so the previously indexed cell is no
        # longer returned as live. The old `break` leaves one object here.
        assert cells_after_spend["objects"] == [], (
            "rich-indexer must continue after an unindexed input and mark later "
            "indexed inputs as spent"
        )
