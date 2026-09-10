"""Regression test for https://github.com/nervosnetwork/ckb/pull/5245.

A custom cell filter can leave some previous outputs absent from rich-indexer.
When one transaction mixes indexed and unindexed inputs, the input loop must
skip each missing output and continue processing later inputs. The old `break`
left later indexed outputs incorrectly visible as live cells.
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

    def test_continues_after_unindexed_inputs_at_start_and_middle(self):
        """Missing inputs at the start or middle must not hide later spends."""
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

        # Two independent input-order regressions share this source transaction:
        #
        #   A: [unindexed 0, indexed 1]
        #   B: [indexed 2, unindexed 3, indexed 4]
        #
        # The second group proves that `continue` also works after the loop has
        # already processed an indexed input, not only on its first iteration.
        type_args_by_output = ("0x00", "0x01", "0x01", "0x00", "0x01")
        output_capacity = (
            int(funding_output["capacity"], 16) - SOURCE_TX_FEE_RESERVE
        ) // len(type_args_by_output)
        source_tx_file = f"/tmp/rich-indexer-mixed-inputs-{time.time_ns()}.json"
        try:
            self.Ckb_cli.tx_init(source_tx_file, api_url)
            self.Ckb_cli.tx_add_multisig_config(
                account["address"]["testnet"], source_tx_file, api_url
            )
            self.Ckb_cli.tx_add_input(
                funding_tx, funding_index, source_tx_file, api_url
            )

            # Type args 0x00 are excluded by the filter; 0x01 are retained.
            for type_args in type_args_by_output:
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

        def indexed_out_points():
            cells = rich_client.get_cells(search_key, "asc", "0xff", None)
            return [cell["out_point"] for cell in cells["objects"]]

        expected_indexed = [
            {"tx_hash": source_tx, "index": index} for index in ("0x1", "0x2", "0x4")
        ]
        assert indexed_out_points() == expected_indexed

        # Querying by the shared lock proves the filter excluded outputs 0 and
        # 3, instead of merely proving the other outputs match the type search.
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
        assert [
            cell["out_point"] for cell in cells_for_lock["objects"]
        ] == expected_indexed

        def spend_source_outputs(indexes):
            index_hexes = [hex(index) for index in indexes]
            mixed_inputs_tx = self.Tx.send_transfer_self_tx_with_input(
                [source_tx] * len(indexes),
                index_hexes,
                account_private,
                data="0x00",
                fee=1000,
                api_url=api_url,
                dep_cells=[{"tx_hash": deploy_tx, "index_hex": "0x0"}],
            )
            self.Miner.miner_until_tx_committed(full_node, mixed_inputs_tx)

            mixed_inputs = client.get_transaction(mixed_inputs_tx)["transaction"][
                "inputs"
            ]
            assert [
                (
                    cell_input["previous_output"]["tx_hash"],
                    cell_input["previous_output"]["index"],
                )
                for cell_input in mixed_inputs
            ] == [(source_tx, index_hex) for index_hex in index_hexes]

            wait_cluster_height(
                self.cluster, full_node.getClient().get_tip_block_number(), 60
            )
            self.wait_for_indexer(rich_node)

        # The unindexed output is first. With the old `break`, input[1] is not
        # marked spent and remains alongside the untouched second group.
        spend_source_outputs([0, 1])
        assert (
            indexed_out_points() == expected_indexed[1:]
        ), "rich-indexer must continue after an unindexed first input"

        # The unindexed output is now in the middle. Input[2] is processed
        # before the gap; `continue` must then reach input[4]. The helper's
        # lock-only change outputs cannot match the 0x01 type search.
        spend_source_outputs([2, 3, 4])
        assert (
            indexed_out_points() == []
        ), "rich-indexer must continue after an unindexed middle input"
