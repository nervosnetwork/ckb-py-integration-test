"""PR #5316: saved transactions are revalidated during v1/v2 pool replay."""

import struct
import re
from pathlib import Path

from framework.basic import CkbTest


def _transactions(data):
    """Read the offsets of a Molecule TransactionVec, retaining packed txs."""
    assert len(data) >= 4 and struct.unpack_from("<I", data)[0] == len(data)
    if len(data) == 4:
        return []
    first = struct.unpack_from("<I", data, 4)[0]
    assert first >= 8 and first % 4 == 0 and first <= len(data)
    count = first // 4 - 1
    offsets = list(struct.unpack_from(f"<{count}I", data, 4)) + [len(data)]
    assert offsets[0] == first and offsets == sorted(offsets)
    return [data[start:end] for start, end in zip(offsets, offsets[1:])]


def _transaction_vec(items):
    header = 4 * (len(items) + 1)
    size = header + sum(map(len, items))
    cursor = header
    offsets = []
    for item in items:
        offsets.append(cursor)
        cursor += len(item)
    result = struct.pack(f"<{len(offsets) + 1}I", size, *offsets) + b"".join(items)
    assert _transactions(result) == items
    return result


class TestPersistenceReplay(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_persistence_replay/node1",
            8508,
            8509,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # Keep pytest output rather than copying the shared report path.
        pass

    @staticmethod
    def _v2(node):
        paths = list(Path(node.ckb_dir).rglob("persisted_data.v2"))
        assert len(paths) == 1, paths
        return paths[0]

    @staticmethod
    def _accepted(path):
        data = path.read_bytes()
        assert data[:8] == b"CKBTPV2\0"
        accepted_len, recovery_count = struct.unpack_from("<QI", data, 8)
        start = 20 + recovery_count * 20
        end = start + accepted_len
        assert end <= len(data)
        return _transactions(data[start:end])

    @staticmethod
    def _spends(raw, tx_hash, index):
        return bytes.fromhex(tx_hash[2:]) + struct.pack("<I", index) in raw

    def _assert_replayed(self, expected, stale, label, rejected_count):
        client = self.node.getClient()
        raw = client.get_raw_tx_pool(False)
        actual = set(raw["pending"]) | set(raw["proposed"])
        assert actual == expected, (label, actual, expected)
        for tx_hash in expected:
            detail = client.get_transaction(tx_hash)
            assert detail["transaction"]["hash"] == tx_hash, (label, tx_hash)
        assert client.get_transaction(stale)["tx_status"]["status"] not in (
            "pending",
            "proposed",
            "committed",
        )
        log = (Path(self.node.ckb_dir) / "node.log").read_text()
        lines = [
            line
            for line in log.splitlines()
            if "Persistent tx-pool data loaded:" in line
        ]
        assert lines, label
        assert re.search(
            rf"Persistent tx-pool data loaded: {len(expected)} accepted, "
            rf"{rejected_count} (?:stale|rejected)",
            lines[-1],
        ), (label, lines[-1])
        print(f"{label}: {lines[-1]}; pool={sorted(actual)}")

    # TEST-MAP: NTD-61
    def test_v1_and_v2_replay_revalidate_sorted_and_duplicate_transactions(self):
        client = self.node.getClient()
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
            output_count=3,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        # Save a valid transaction, then let a different chain transaction
        # spend the same input so this saved one is genuinely uncommitted/stale.
        stale = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        self.node.stop()
        v2 = self._v2(self.node)
        stale_items = self._accepted(v2)
        assert len(stale_items) == 1 and self._spends(stale_items[0], split, 0)
        stale_raw = stale_items[0]
        self.node.start()
        assert client.remove_transaction(stale) is True
        chain_spend = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            fee=1001,
            api_url=client.url,
        )
        assert chain_spend != stale
        self.Miner.miner_until_tx_committed(self.node, chain_spend)
        assert client.get_transaction(chain_spend)["tx_status"]["status"] == "committed"

        # A parent/child and independent transaction remain valid on this tip.
        parent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=1000,
            api_url=client.url,
        )
        child = self.Tx.send_transfer_self_tx_with_input(
            [parent],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        independent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x2"],
            key,
            fee=1000,
            api_url=client.url,
        )
        expected = {parent, child, independent}
        assert set(client.get_raw_tx_pool(False)["pending"]) == expected
        self.node.stop()
        saved = self._accepted(v2)
        assert len(saved) == 3
        parent_raw = next(raw for raw in saved if self._spends(raw, split, 1))
        child_raw = next(raw for raw in saved if self._spends(raw, parent, 0))
        independent_raw = next(raw for raw in saved if self._spends(raw, split, 2))
        assert len({parent_raw, child_raw, independent_raw}) == 3

        # Capture an alternative spend of the same output. A one-shannon fee
        # difference is below the replacement increment, so recovery order is
        # observable as the winner rather than being hidden by RBF.
        self.node.start()
        assert client.remove_transaction(independent) is True
        alternate = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x2"],
            key,
            fee=1001,
            api_url=client.url,
        )
        assert alternate != independent
        self.node.stop()
        alternate_raw = next(
            raw for raw in self._accepted(v2) if self._spends(raw, split, 2)
        )
        assert alternate_raw != independent_raw

        # v1 has no status flags: child-first bytes must still be sorted and
        # every item must pass live chain-state admission.
        v1 = v2.with_suffix(".v1")
        v2.unlink()
        v1.write_bytes(
            _transaction_vec([child_raw, stale_raw, independent_raw, parent_raw])
        )
        self.node.start()
        try:
            self._assert_replayed(expected, stale, "v1", rejected_count=1)
        finally:
            self.node.stop()

        # v2 recovery repeats an Accepted hash. Two conflicting spends appear
        # in fixed physical order; flipping only metadata must flip the winner.
        accepted = _transaction_vec([child_raw, stale_raw, parent_raw])
        recovery = _transaction_vec([alternate_raw, independent_raw, parent_raw])
        for label, order_keys, winner, loser in (
            (
                "v2-ordinal-independent-first",
                ((0, 2), (0, 1), (0, 0)),
                independent,
                alternate,
            ),
            (
                "v2-ordinal-alternate-first",
                ((0, 1), (0, 2), (0, 0)),
                alternate,
                independent,
            ),
            (
                "v2-session-independent-first",
                ((1, 0), (0, 2), (0, 0)),
                independent,
                alternate,
            ),
        ):
            metadata = b"".join(
                session.to_bytes(16, "little") + struct.pack("<I", ordinal)
                for session, ordinal in order_keys
            )
            v2.write_bytes(
                b"CKBTPV2\0"
                + struct.pack("<QI", len(accepted), 3)
                + metadata
                + accepted
                + recovery
            )
            self.node.start()
            try:
                self._assert_replayed(
                    {parent, child, winner}, stale, label, rejected_count=2
                )
                pool = self.node.getClient().get_raw_tx_pool(False)
                assert loser not in set(pool["pending"]) | set(pool["proposed"])
            finally:
                self.node.stop()
        self.did_pass = True
