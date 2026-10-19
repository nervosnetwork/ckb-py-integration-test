"""PR #5316: cached pool verification must not waive block cycle totals."""

import time
from contextlib import contextmanager
from pathlib import Path
import socket

import pytest
import toml

from framework.basic import CkbTest


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestCachedBlockCycles(CkbTest):
    def teardown_method(self, method):
        # The base copier uses a shared report path; pytest retains this log.
        pass

    @contextmanager
    def _node(self, name, max_cycles=None):
        rpc_port, p2p_port = _unused_port(), _unused_port()
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_cached_block_cycles/{name}_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        if max_cycles is not None:
            spec_path = Path(node.ckb_specs_config_path)
            spec = toml.loads(spec_path.read_text())
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
    def _rpc(client, method, params):
        return client.call(method, params, try_count=1, timeout=35, verbose=False)

    def _mine_without_transactions(self, node, proposals=()):
        client = node.getClient()
        template = self._rpc(client, "get_block_template", [])
        template["transactions"] = []
        template["proposals"] = list(proposals)
        block_hash = self._rpc(client, "generate_block_with_template", [template])
        height = int(template["number"], 16)
        assert client.get_block_hash(hex(height)) == block_hash
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            info = self._rpc(client, "tx_pool_info", [])
            if int(info["tip_number"], 16) == height:
                return block_hash
            time.sleep(0.1)
        raise AssertionError(f"pool did not reach block {height}")

    def _fund_and_submit(self, node):
        client = node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        address = self.Ckb_cli.util_key_info_by_private_key(key)["address"]["testnet"]
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            address,
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=3,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(node, split)
        hashes = [
            self.Tx.send_transfer_self_tx_with_input(
                [split],
                ["0x0"],
                key,
                fee=2000,
                api_url=client.url,
            ),
            self.Tx.send_transfer_self_tx_with_input(
                [split, split],
                ["0x1", "0x2"],
                key,
                data="0x" + "ab" * 128,
                fee=5000,
                api_url=client.url,
            ),
        ]
        entries = self._rpc(client, "get_raw_tx_pool", [True])
        accepted = {**entries["pending"], **entries["proposed"]}
        assert set(accepted) == set(hashes)
        split_tx = client.get_transaction(split)["transaction"]
        expected_fee = 0
        cycles = []
        for tx_hash, inputs in zip(hashes, ((0,), (1, 2))):
            entry = accepted[tx_hash]
            response = client.get_transaction(tx_hash)
            assert response["tx_status"]["status"] in ("pending", "proposed")
            tx = response["transaction"]
            input_capacity = sum(
                int(split_tx["outputs"][index]["capacity"], 16) for index in inputs
            )
            output_capacity = sum(
                int(output["capacity"], 16) for output in tx["outputs"]
            )
            fee = input_capacity - output_capacity
            assert fee > 0
            assert int(entry["fee"], 16) == fee
            assert int(response["fee"], 16) == fee
            expected_fee += fee
            cycles.append(int(entry["cycles"], 16))
        return hashes, cycles, expected_fee

    def _prepare_commit_window(self, node, hashes):
        client = node.getClient()
        short_ids = [tx_hash[:22] for tx_hash in hashes]
        assert len(set(short_ids)) == len(hashes)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            template = self._rpc(client, "get_block_template", [])
            if set(short_ids) <= set(template["proposals"]):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("pool transactions did not enter proposals")
        self._mine_without_transactions(node, short_ids)
        self._mine_without_transactions(node)
        for tx_hash in hashes:
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "proposed"

    def _both_transaction_template(self, node, hashes, cycles):
        client = node.getClient()
        template = self._rpc(client, "get_block_template", [])
        transactions = []
        for tx_hash, cycle_count in zip(hashes, cycles):
            data = dict(client.get_transaction(tx_hash)["transaction"])
            data.pop("hash")  # RPC view has a hash; template data is the raw tx.
            transactions.append(
                {
                    "hash": tx_hash,
                    "required": False,
                    "cycles": hex(cycle_count),
                    "depends": None,
                    "data": data,
                }
            )
        template["transactions"] = transactions
        template["proposals"] = []
        return template

    # TEST-MAP: NTD-42
    def test_cached_proofs_still_obey_aggregate_cycle_limit_and_block_fee(self):
        with self._node("probe") as probe:
            _, measured_cycles, _ = self._fund_and_submit(probe)
        exact = sum(measured_cycles)
        assert min(measured_cycles) > 0 and max(measured_cycles) < exact - 1

        for name, limit, should_accept in (
            ("over", exact - 1, False),
            ("equal", exact, True),
        ):
            with self._node(name, limit) as node:
                client = node.getClient()
                assert int(client.get_consensus()["max_block_cycles"], 16) == limit
                hashes, cycles, expected_fee = self._fund_and_submit(node)
                assert cycles == measured_cycles
                assert all(cycle < limit for cycle in cycles)
                self._prepare_commit_window(node, hashes)
                template = self._both_transaction_template(node, hashes, cycles)
                height_before = client.get_tip_block_number()
                if not should_accept:
                    with pytest.raises(Exception) as rejected:
                        self._rpc(client, "generate_block_with_template", [template])
                    assert "ExceededMaximumCycles" in str(rejected.value)
                    assert client.get_tip_block_number() == height_before
                    print(
                        f"over: cycles={sum(cycles)}, limit={limit}, error={rejected.value}"
                    )
                    continue

                block_hash = self._rpc(
                    client, "generate_block_with_template", [template]
                )
                block = client.get_block(block_hash)
                assert [tx["hash"] for tx in block["transactions"][1:]] == hashes
                assert client.get_tip_block_number() == height_before + 1
                for tx_hash in hashes:
                    assert (
                        client.get_transaction(tx_hash)["tx_status"]["status"]
                        == "committed"
                    )
                for _ in range(50):
                    economics = self._rpc(
                        client, "get_block_economic_state", [block_hash]
                    )
                    if economics is not None:
                        break
                    self._mine_without_transactions(node)
                else:
                    raise AssertionError(
                        "accepted block did not reach economic finality"
                    )
                assert int(economics["txs_fee"], 16) == expected_fee
                print(
                    f"equal: cycles={sum(cycles)}, limit={limit}, "
                    f"txs_fee={expected_fee}, block={block_hash}"
                )
        self.did_pass = True
