"""PR #5316: Python/RPC oracle for package ancestry in block packing."""

import copy
import time
from pathlib import Path

import toml

from framework.basic import CkbTest


class TestPackingDag(CkbTest):
    MAX_BLOCK_CYCLES = 7_500_000
    MAX_BLOCK_BYTES = 2_600

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_packing_dag/node1",
            8496,
            8497,
        )
        cls.node.prepare()
        spec_path = Path(cls.node.ckb_specs_config_path)
        spec = toml.loads(spec_path.read_text())
        spec["params"]["max_block_cycles"] = cls.MAX_BLOCK_CYCLES
        spec["params"]["max_block_bytes"] = cls.MAX_BLOCK_BYTES
        spec_path.write_text(toml.dumps(spec))
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)
        client = cls.node.getClient()
        tip_height = client.get_tip_block_number()
        empty_block = client.get_block_by_number(hex(tip_height))
        assert len(empty_block["transactions"]) == 1
        assert empty_block["proposals"] == [] and empty_block["uncles"] == []
        cls.empty_base_bytes = len(
            bytes.fromhex(client.get_block_by_number(hex(tip_height), "0x0")[2:])
        )

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # The base copier uses a shared report path; pytest retains this run's log.
        pass

    def _pool_entries(self):
        raw = self.node.getClient().get_raw_tx_pool(True)
        return {**raw["pending"], **raw["proposed"]}

    def _assert_independent_shape(self, shape, competitor_fee=10_000):
        client = self.node.getClient()
        client.clear_tx_pool()
        assert self._pool_entries() == {}
        key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        parent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            output_count=3 if shape == "fanout" else 1,
            fee=1000,
            api_url=client.url,
        )
        if shape == "chain":
            middle = self.Tx.send_transfer_self_tx_with_input(
                [parent],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            near_leaf = self.Tx.send_transfer_self_tx_with_input(
                [middle],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            leaf = self.Tx.send_transfer_self_tx_with_input(
                [near_leaf],
                ["0x0"],
                key,
                fee=100_000_000,
                api_url=client.url,
            )
            order = [parent, middle, near_leaf, leaf]
            ancestors = {
                parent: {parent},
                middle: {parent, middle},
                near_leaf: {parent, middle, near_leaf},
                leaf: set(order),
            }
        else:
            assert shape == "fanout"
            children = [
                self.Tx.send_transfer_self_tx_with_input(
                    [parent],
                    [hex(index)],
                    key,
                    fee=50_000_000,
                    api_url=client.url,
                )
                for index in range(3)
            ]
            order = [parent, *children]
            ancestors = {parent: {parent}}
            ancestors.update({child: {parent, child} for child in children})
        competitor = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=competitor_fee,
            api_url=client.url,
        )
        graph = set(order)
        entries = self._pool_entries()
        assert set(entries) == graph | {competitor}
        raw_sizes = {}
        for tx_hash in graph | {competitor}:
            raw_tx = client.get_transaction(tx_hash, "0x0")["transaction"]
            raw_sizes[tx_hash] = len(bytes.fromhex(raw_tx[2:])) + 4
            assert int(entries[tx_hash]["size"], 16) == raw_sizes[tx_hash]
            # PackageOrderKey uses max(size, cycles * 0.0001705714).
            assert int(entries[tx_hash]["cycles"], 16) * 1_705_714 < (
                raw_sizes[tx_hash] * 10_000_000_000
            )
        for tx_hash, expected in ancestors.items():
            assert int(entries[tx_hash]["ancestors_count"], 16) == len(expected)
            assert int(entries[tx_hash]["ancestors_size"], 16) == sum(
                int(entries[ancestor]["size"], 16) for ancestor in expected
            )
            assert int(entries[tx_hash]["ancestors_cycles"], 16) == sum(
                int(entries[ancestor]["cycles"], 16) for ancestor in expected
            )
        graph_cycles = sum(int(entries[tx_hash]["cycles"], 16) for tx_hash in graph)
        competitor_cycles = int(entries[competitor]["cycles"], 16)
        assert graph_cycles <= self.MAX_BLOCK_CYCLES
        assert competitor_cycles <= self.MAX_BLOCK_CYCLES
        assert graph_cycles + competitor_cycles > self.MAX_BLOCK_CYCLES
        graph_size = sum(int(entries[tx_hash]["size"], 16) for tx_hash in graph)
        graph_fee = sum(int(entries[tx_hash]["fee"], 16) for tx_hash in graph)
        competitor_size = int(entries[competitor]["size"], 16)
        competitor_fee = int(entries[competitor]["fee"], 16)
        assert int(entries[parent]["fee"], 16) * competitor_size < (
            competitor_fee * int(entries[parent]["size"], 16)
        )
        if competitor_fee == 10_000:
            assert graph_fee * competitor_size > competitor_fee * graph_size
        else:
            assert shape == "chain"
            leaf = order[-1]
            assert int(entries[leaf]["fee"], 16) * competitor_size > (
                competitor_fee * int(entries[leaf]["size"], 16)
            )
            assert competitor_fee * graph_size > graph_fee * competitor_size

        short_ids = {tx_hash[:22] for tx_hash in graph | {competitor}}
        assert len(short_ids) == 5
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if short_ids <= set(client.get_block_template()["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError(f"{shape} proposals did not all appear")
        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        template = client.get_block_template()
        assert int(template["bytes_limit"], 16) == self.MAX_BLOCK_BYTES
        assert int(template["cycles_limit"], 16) == self.MAX_BLOCK_CYCLES
        selected = [entry["hash"] for entry in template["transactions"]]
        if competitor_fee == 10_000:
            assert set(selected) == graph, (shape, selected, graph)
            assert selected[0] == parent
            if shape == "chain":
                assert selected == order
            else:
                assert set(selected[1:]) == set(order[1:])
        else:
            assert competitor in selected and order[-1] not in selected
            assert set(selected) <= graph | {competitor}
            assert selected.index(competitor) == 0
            assert [tx_hash for tx_hash in selected if tx_hash in graph] == order[
                : len(selected) - 1
            ]
        block = self.Miner.block_template_transfer_to_submit_block(
            copy.deepcopy(template)
        )
        block_hash = client.submit_block(template["work_id"], block)
        committed = client.get_block(block_hash)
        assert [tx["hash"] for tx in committed["transactions"][1:]] == selected
        assert committed["proposals"] == [] and committed["uncles"] == []
        packed = client.get_block(block_hash, "0x0")
        serialized_bytes = len(bytes.fromhex(packed[2:]))
        assert serialized_bytes == self.empty_base_bytes + sum(
            raw_sizes[tx_hash] for tx_hash in selected
        )
        assert serialized_bytes <= self.MAX_BLOCK_BYTES
        for tx_hash in selected:
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "committed"
        if competitor_fee == 10_000:
            assert competitor in self._pool_entries()
        else:
            assert order[-1] in self._pool_entries()
        print(
            f"packing-{shape}: order={selected}, cycles={graph_cycles}, "
            f"serialized_bytes={serialized_bytes}, competitor={competitor}, "
            f"competitor_fee={competitor_fee}"
        )

    # TEST-MAP: NTD-27
    def test_high_fee_chain_packs_all_ancestors_before_descendant(self):
        self._assert_independent_shape("chain")
        self.did_pass = True

    # TEST-MAP: NTD-27
    def test_high_fee_fanout_packs_parent_once_before_children(self):
        self._assert_independent_shape("fanout")
        self.did_pass = True

    # TEST-MAP: NTD-27
    def test_chain_package_score_defers_leaf_behind_mid_rate_competitor(self):
        self._assert_independent_shape("chain", competitor_fee=50_000_000)
        self.did_pass = True

    # TEST-MAP: NTD-27
    def test_high_fee_diamond_packs_unique_ancestors_in_topological_order(self):
        client = self.node.getClient()
        client.clear_tx_pool()
        assert self._pool_entries() == {}
        consensus = client.get_consensus()
        assert int(consensus["max_block_cycles"], 16) == self.MAX_BLOCK_CYCLES
        assert int(consensus["max_block_bytes"], 16) == self.MAX_BLOCK_BYTES
        base_bytes = self.empty_base_bytes

        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2
        )
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        parent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        left = self.Tx.send_transfer_self_tx_with_input(
            [parent],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        right = self.Tx.send_transfer_self_tx_with_input(
            [parent],
            ["0x1"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        diamond = self.Tx.send_transfer_self_tx_with_input(
            [left, right],
            ["0x0", "0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=100_000_000,
            api_url=client.url,
        )
        competitor = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=10_000,
            api_url=client.url,
        )
        graph = {parent, left, right, diamond}
        entries = self._pool_entries()
        assert set(entries) == graph | {competitor}

        # Recompute ancestry from the explicit transaction graph, counting the
        # shared parent once rather than adding each child's aggregate twice.
        for tx_hash, ancestors in {
            parent: {parent},
            left: {parent, left},
            right: {parent, right},
            diamond: graph,
        }.items():
            assert int(entries[tx_hash]["ancestors_count"], 16) == len(ancestors)
            assert int(entries[tx_hash]["ancestors_size"], 16) == sum(
                int(entries[ancestor]["size"], 16) for ancestor in ancestors
            )
            assert int(entries[tx_hash]["ancestors_cycles"], 16) == sum(
                int(entries[ancestor]["cycles"], 16) for ancestor in ancestors
            )

        graph_cycles = sum(int(entries[tx_hash]["cycles"], 16) for tx_hash in graph)
        competitor_cycles = int(entries[competitor]["cycles"], 16)
        assert graph_cycles <= self.MAX_BLOCK_CYCLES
        assert competitor_cycles <= self.MAX_BLOCK_CYCLES
        assert graph_cycles + competitor_cycles > self.MAX_BLOCK_CYCLES

        # In this fixture serialized bytes dominate cycle-derived weight.
        # Compare integer cross-products, not rounded fee-rate floats: the
        # shared-ancestor diamond package outranks the standalone competitor.
        graph_size = sum(int(entries[tx_hash]["size"], 16) for tx_hash in graph)
        graph_fee = sum(int(entries[tx_hash]["fee"], 16) for tx_hash in graph)
        competitor_size = int(entries[competitor]["size"], 16)
        competitor_fee = int(entries[competitor]["fee"], 16)
        assert graph_cycles * 1_705_714 < graph_size * 10_000_000_000
        assert competitor_cycles * 1_705_714 < competitor_size * 10_000_000_000
        assert graph_fee * competitor_size > competitor_fee * graph_size
        assert int(entries[diamond]["fee"], 16) * graph_size >= graph_fee * int(
            entries[diamond]["size"], 16
        )

        short_ids = {tx_hash[:22] for tx_hash in graph | {competitor}}
        assert len(short_ids) == 5  # This fixture does not force a short-ID collision.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            proposal = client.get_block_template()
            if short_ids <= set(proposal["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("all DAG proposals did not appear in the template")

        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        template = client.get_block_template()
        assert int(template["bytes_limit"], 16) == self.MAX_BLOCK_BYTES
        selected = [entry["hash"] for entry in template["transactions"]]
        assert set(selected) == graph
        assert len(selected) == len(graph)
        assert selected.index(parent) < selected.index(left) < selected.index(diamond)
        assert selected.index(parent) < selected.index(right) < selected.index(diamond)
        assert (
            sum(int(entries[tx_hash]["cycles"], 16) for tx_hash in selected)
            <= self.MAX_BLOCK_CYCLES
        )
        assert sum(int(entries[tx_hash]["size"], 16) for tx_hash in selected) < int(
            template["bytes_limit"], 16
        )

        block = self.Miner.block_template_transfer_to_submit_block(
            copy.deepcopy(template)
        )
        block_hash = client.submit_block(template["work_id"], block)
        assert block_hash.startswith("0x")
        committed_block = client.get_block(block_hash)
        assert [tx["hash"] for tx in committed_block["transactions"][1:]] == selected
        assert committed_block["proposals"] == [] and committed_block["uncles"] == []
        packed_block = client.get_block(block_hash, "0x0")
        serialized_bytes = len(bytes.fromhex(packed_block[2:]))
        selected_bytes = 0
        for tx_hash in selected:
            raw_tx = client.get_transaction(tx_hash, "0x0")["transaction"]
            tx_bytes = len(bytes.fromhex(raw_tx[2:])) + 4
            assert int(entries[tx_hash]["size"], 16) == tx_bytes
            selected_bytes += tx_bytes
        expected_bytes = base_bytes + selected_bytes
        assert serialized_bytes == expected_bytes
        assert serialized_bytes <= self.MAX_BLOCK_BYTES
        assert client.get_transaction(diamond)["tx_status"]["status"] == "committed"
        assert competitor in self._pool_entries()
        print(
            f"packing-diamond: base_bytes={base_bytes}, selected_bytes={selected_bytes}, "
            f"serialized_bytes={serialized_bytes}, limit={self.MAX_BLOCK_BYTES}"
        )
        self.did_pass = True
