"""PR #5316: exact byte/cycle packing boundaries through public templates."""

import copy
import time
from contextlib import contextmanager
from pathlib import Path

import toml

from framework.basic import CkbTest


class TestPackingExactBudget(CkbTest):
    def teardown_method(self, method):
        # The base copier uses a shared report path; pytest retains this log.
        pass

    @contextmanager
    def _node(self, name, port, max_bytes=None, max_cycles=None):
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_packing_exact_budget/{name}",
            port,
            port + 1,
        )
        node.prepare()
        if max_bytes is not None or max_cycles is not None:
            spec_path = Path(node.ckb_specs_config_path)
            spec = toml.loads(spec_path.read_text())
            if max_bytes is not None:
                spec["params"]["max_block_bytes"] = max_bytes
            if max_cycles is not None:
                spec["params"]["max_block_cycles"] = max_cycles
            spec_path.write_text(toml.dumps(spec))
        try:
            node.start()
            self.Miner.make_tip_height_number(node, 30)
            yield node
        finally:
            if node.ckb_pid != -1:
                node.stop()
            node.clean()

    @staticmethod
    def _empty_block_bytes(client):
        height = client.get_tip_block_number()
        block = client.get_block_by_number(hex(height))
        assert block["proposals"] == [] and block["uncles"] == []
        assert len(block["transactions"]) == 1  # Cellbase only.
        packed = client.get_block_by_number(hex(height), "0x0")
        assert isinstance(packed, str) and packed.startswith("0x")
        return len(bytes.fromhex(packed[2:]))

    def _split(self, node):
        client = node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=4,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(node, split)
        return split

    def _candidates(self, node, split):
        client = node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        big = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            data="0x" + "ab" * 128,
            fee=100_000_000,
            api_url=client.url,
        )
        medium = self.Tx.send_transfer_self_tx_with_input(
            [split, split],
            ["0x1", "0x2"],
            key,
            data="0x" + "cd" * 512,
            fee=10_000_000,
            api_url=client.url,
        )
        tiny = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x3"],
            key,
            fee=1000,
            api_url=client.url,
        )
        raw = client.get_raw_tx_pool(True)
        entries = {**raw["pending"], **raw["proposed"]}
        assert set(entries) == {big, medium, tiny}
        measured = {}
        for name, tx_hash in (("big", big), ("medium", medium), ("tiny", tiny)):
            size = int(entries[tx_hash]["size"], 16)
            cycles = int(entries[tx_hash]["cycles"], 16)
            fee = int(entries[tx_hash]["fee"], 16)
            packed = client.get_transaction(tx_hash, "0x0")["transaction"]
            assert size == len(bytes.fromhex(packed[2:])) + 4
            measured[name] = (size, cycles, fee)
        assert measured["medium"][0] > measured["tiny"][0]
        assert measured["medium"][1] > measured["tiny"][1]
        big_size, big_cycles, big_fee = measured["big"]
        medium_size, medium_cycles, medium_fee = measured["medium"]
        tiny_size, tiny_cycles, tiny_fee = measured["tiny"]
        # Both byte and cycle fee rates dominate, hence so does fee/weight
        # regardless of which dimension dominates the packed score.
        assert big_fee * medium_size > medium_fee * big_size
        assert big_fee * medium_cycles > medium_fee * big_cycles
        assert medium_fee * tiny_size > tiny_fee * medium_size
        assert medium_fee * tiny_cycles > tiny_fee * medium_cycles
        return {"big": big, "medium": medium, "tiny": tiny}, measured

    def _prepared_template(self, node, hashes):
        client = node.getClient()
        short_ids = {tx_hash[:22] for tx_hash in hashes.values()}
        assert len(short_ids) == 3
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            template = client.get_block_template()
            if short_ids <= set(template["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("candidate proposals did not enter template")
        self.Miner.miner_with_version(node, "0x0")
        self.Miner.miner_with_version(node, "0x0")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            template = client.get_block_template()
            if (
                template["proposals"] == []
                and template["uncles"] == []
                and client.tx_pool_info()["tip_hash"] == client.get_tip_header()["hash"]
            ):
                for tx_hash in hashes.values():
                    assert (
                        client.get_transaction(tx_hash)["tx_status"]["status"]
                        == "proposed"
                    )
                return template
            time.sleep(0.2)
        raise AssertionError("empty optional-content template did not settle")

    # TEST-MAP: NTD-30
    def test_equal_budget_includes_candidate_and_one_less_skips_it(self):
        """
        1. 先测量候选交易的实际字节与周期消耗。
        2. 对等于预算和少一单位的节点逐项核对模板选择。
        """

        # 1. 先测量候选交易的实际字节与周期消耗。
        with self._node("probe", 8544) as probe:
            base_bytes = self._empty_block_bytes(probe.getClient())
            hashes, measured = self._candidates(probe, self._split(probe))
            assert set(hashes) == {"big", "medium", "tiny"}

        big_size, big_cycles, _ = measured["big"]
        medium_size, medium_cycles, _ = measured["medium"]
        byte_equal = base_bytes + big_size + medium_size
        cycle_equal = big_cycles + medium_cycles
        assert byte_equal > base_bytes and cycle_equal > big_cycles

        # 2. 对等于预算和少一单位的节点逐项核对模板选择。
        for name, port, max_bytes, max_cycles, is_equal in (
            ("byte_equal", 8546, byte_equal, None, True),
            ("byte_minus_one", 8548, byte_equal - 1, None, False),
            ("cycle_equal", 8550, None, cycle_equal, True),
            ("cycle_minus_one", 8552, None, cycle_equal - 1, False),
        ):
            with self._node(name, port, max_bytes, max_cycles) as node:
                client = node.getClient()
                assert self._empty_block_bytes(client) == base_bytes
                consensus = client.get_consensus()
                if max_bytes is not None:
                    assert int(consensus["max_block_bytes"], 16) == max_bytes
                if max_cycles is not None:
                    assert int(consensus["max_block_cycles"], 16) == max_cycles
                current, sizes = self._candidates(node, self._split(node))
                assert sizes == measured, (name, sizes, measured)
                template = self._prepared_template(node, current)
                selected = [item["hash"] for item in template["transactions"]]
                expected = (
                    [current["big"], current["medium"]]
                    if is_equal
                    else [current["big"], current["tiny"]]
                )
                assert selected == expected, (name, selected, expected)
                selected_bytes = base_bytes + sum(
                    sizes[label][0]
                    for label in (("big", "medium") if is_equal else ("big", "tiny"))
                )
                selected_cycles = sum(
                    sizes[label][1]
                    for label in (("big", "medium") if is_equal else ("big", "tiny"))
                )
                assert selected_bytes <= int(template["bytes_limit"], 16)
                assert selected_cycles <= int(template["cycles_limit"], 16)
                if max_bytes is not None:
                    assert selected_cycles < int(template["cycles_limit"], 16)
                if max_cycles is not None:
                    assert selected_bytes < int(template["bytes_limit"], 16)
                if name == "byte_equal":
                    assert selected_bytes == max_bytes
                if name == "cycle_equal":
                    assert selected_cycles == max_cycles
                block = self.Miner.block_template_transfer_to_submit_block(
                    copy.deepcopy(template)
                )
                block_hash = client.submit_block(template["work_id"], block)
                packed_block = client.get_block(block_hash, "0x0")
                packed_size = len(bytes.fromhex(packed_block[2:]))
                assert (
                    packed_size
                    - sum(
                        sizes[label][0]
                        for label in (
                            ("big", "medium") if is_equal else ("big", "tiny")
                        )
                    )
                    == base_bytes
                )
                assert packed_size == selected_bytes
                for tx_hash in expected:
                    assert (
                        client.get_transaction(tx_hash)["tx_status"]["status"]
                        == "committed"
                    )
                print(
                    f"{name}: limit_bytes={max_bytes}; limit_cycles={max_cycles}; "
                    f"selected={selected}; serialized_bytes={selected_bytes}; "
                    f"cycles={selected_cycles}"
                )
        self.did_pass = True
