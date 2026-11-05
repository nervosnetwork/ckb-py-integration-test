"""PR #5316: saved transactions are revalidated during v1/v2 pool replay."""

import struct
import re
from pathlib import Path

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.dao_precheck.support import ckb_hash


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
        accepted_len = struct.unpack_from("<Q", data, 8)[0]
        start = 16
        end = start + accepted_len
        assert end + 4 <= len(data)
        _transactions(data[end:])  # Independently check the Recovery vector too.
        return _transactions(data[start:end])

    @staticmethod
    def _spends(raw, tx_hash, index):
        return bytes.fromhex(tx_hash[2:]) + struct.pack("<I", index) in raw

    def _assert_replayed(self, expected, stale, label, rejected_count):
        client = self.node.getClient()
        workload.wait_for(
            lambda: "Persistent tx-pool data loaded:"
            in (Path(self.node.ckb_dir) / "node.log").read_text(),
            f"{label} replay completed",
        )
        raw = client.get_raw_tx_pool(False)
        actual = set(raw["pending"]) | set(raw["proposed"])
        assert actual == expected, (label, actual, expected)
        for tx_hash in expected:
            detail = client.get_transaction(tx_hash)
            assert detail["transaction"]["hash"] == tx_hash, (label, tx_hash)
            assert detail["transaction"] == self.expected_bodies[tx_hash], (
                label,
                tx_hash,
            )
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
        """
        1. 准备已失效交易和仍有效的父子及独立交易。
        2. 构造同一输入的替代花费并保存原始字节。
        3. 写入子交易在前且含失效交易的 v1 快照，验证回放重排与重验。
        4. 构造含重复哈希及冲突花费的 v2 双向量快照，核对 Accepted 优先和 Recovery 顺序。
        5. 再次恢复最后一个有效快照，将有效父子和独立交易打包确认。
        """

        # 1. 准备已失效交易和仍有效的父子及独立交易。
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
        workload.stop_and_assert_process_exit(self.node)
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
        self.expected_bodies = {
            key: client.get_transaction(key)["transaction"] for key in expected
        }
        workload.stop_and_assert_process_exit(self.node)
        saved = self._accepted(v2)
        assert len(saved) == 3
        parent_raw = next(raw for raw in saved if self._spends(raw, split, 1))
        child_raw = next(raw for raw in saved if self._spends(raw, parent, 0))
        independent_raw = next(raw for raw in saved if self._spends(raw, split, 2))
        assert len({parent_raw, child_raw, independent_raw}) == 3

        # Capture an alternative spend of the same output. A one-shannon fee
        # difference is below the replacement increment, so recovery order is
        # observable as the winner rather than being hidden by RBF.

        # 2. 构造同一输入的替代花费并保存原始字节。
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
        self.expected_bodies[alternate] = client.get_transaction(alternate)[
            "transaction"
        ]
        workload.stop_and_assert_process_exit(self.node)
        alternate_raw = next(
            raw for raw in self._accepted(v2) if self._spends(raw, split, 2)
        )
        assert alternate_raw != independent_raw

        # v1 has no status flags: child-first bytes must still be sorted and
        # every item must pass live chain-state admission.

        # 3. 写入子交易在前且含失效交易的 v1 快照，验证回放重排与重验。
        v1 = v2.with_suffix(".v1")
        v2.unlink()
        v1.write_bytes(
            _transaction_vec([child_raw, stale_raw, independent_raw, parent_raw])
        )
        self.node.start()
        try:
            self._assert_replayed(expected, stale, "v1", rejected_count=1)
        finally:
            workload.stop_and_assert_process_exit(self.node)

        # Accepted 是 writer 已排好序的前缀；只对 Recovery 构造子在父前的输入。
        # 冲突项同为 ready 时保留物理顺序，不再写 session/ordinal 元数据。

        # 4. 双向量快照检查去重、冲突先到者及 Recovery 依赖重排。
        for label, accepted_items, recovery_items, winner, loser in (
            (
                "v2-recovery-independent-first",
                [parent_raw, child_raw, stale_raw],
                [
                    independent_raw,
                    alternate_raw,
                    parent_raw,
                    child_raw,
                    independent_raw,
                ],
                independent,
                alternate,
            ),
            (
                "v2-recovery-alternate-first",
                [parent_raw, child_raw, stale_raw],
                [alternate_raw, independent_raw, parent_raw, child_raw, alternate_raw],
                alternate,
                independent,
            ),
            (
                "v2-accepted-wins-recovery-parent-first",
                [independent_raw, stale_raw],
                [child_raw, parent_raw, independent_raw, alternate_raw],
                independent,
                alternate,
            ),
        ):
            accepted = _transaction_vec(accepted_items)
            recovery = _transaction_vec(recovery_items)
            v2.write_bytes(
                b"CKBTPV2\0" + struct.pack("<Q", len(accepted)) + accepted + recovery
            )
            assert self._accepted(v2) == accepted_items
            self.node.start()
            try:
                self._assert_replayed(
                    {parent, child, winner}, stale, label, rejected_count=2
                )
                pool = client.get_raw_tx_pool(False)
                assert loser not in set(pool["pending"]) | set(pool["proposed"])
                assert self.node.get_connected_count() == 0
            finally:
                workload.stop_and_assert_process_exit(self.node)

        # 5. 最后一个有效快照再次真实恢复后打包，避免只证明文件可解码。
        self.node.start()
        workload.wait_for(
            lambda: set(client.get_raw_tx_pool(False)["pending"]) == expected,
            "final v2 replay before mining",
        )
        for tx_hash in (parent, child, independent):
            committed = self.Miner.miner_until_tx_committed(self.node, tx_hash)
            assert committed["tx_status"]["status"] == "committed"
            assert committed["transaction"] == self.expected_bodies[tx_hash]
        self.did_pass = True

    # TEST-MAP: NTD-61
    def test_accepted_snapshot_preserves_reader_before_spender(self):
        """
        1. 构造 P→A 和 B；A 将链上 X 作为 cell_dep，B 花费 X。
        2. 正常停止并独立解析实际 v2，确认 Accepted 顺序为 P、A、B。
        3. 无 peer 重启，核对三笔交易及完整 body；不重新提交任何交易。
        4. 将恢复交易打包，检查同块中 P、A、B 的顺序及确认结果。
        """
        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2

        # 1. 复用已签名交易夹具，read-before-spend 关系不等同于普通父子依赖。
        address = self.Ckb_cli.util_key_info_by_private_key(key)["address"]["testnet"]
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            address,
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
            fee=1000,
            api_url=client.url,
        )
        reader = self.Tx.send_transfer_self_tx_with_input(
            [parent],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
            dep_cells=[{"tx_hash": split, "index_hex": "0x1"}],
        )
        spender = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=100_000,
            api_url=client.url,
        )
        expected = {parent, reader, spender}
        bodies = {
            tx_hash: client.get_transaction(tx_hash)["transaction"]
            for tx_hash in expected
        }
        assert set(workload.pool_transactions(client)) == expected

        # 2. Transaction 是含 RawTransaction 和 witnesses 的 Molecule table。
        workload.stop_and_assert_process_exit(self.node)
        path = self._v2(self.node)
        saved = path.read_bytes()
        packed = self._accepted(path)
        hashes = []
        for raw in packed:
            total, raw_start, witnesses_start = struct.unpack_from("<III", raw)
            assert total == len(raw) and raw_start == 12 < witnesses_start <= total
            hashes.append(ckb_hash(raw[raw_start:witnesses_start]))
        assert set(hashes) == expected and len(hashes) == 3
        assert hashes.index(parent) < hashes.index(reader) < hashes.index(spender)

        # 3. 原文件原顺序重启，避免测试自己重排 Accepted 而掩盖 writer 错误。
        self.node.start()
        workload.wait_for(
            lambda: set(workload.pool_transactions(client)) == expected,
            "reader/spender replay",
        )
        assert self.node.get_connected_count() == 0
        assert path.read_bytes() == saved
        for tx_hash in expected:
            assert client.get_transaction(tx_hash)["transaction"] == bodies[tx_hash]

        # 4. 最终由完整验块约束证明依赖读取仍有效。
        committed = self.Miner.miner_until_tx_committed(self.node, reader)
        block_hash = committed["tx_status"]["block_hash"]
        block = client.get_block(block_hash)
        order = [tx["hash"] for tx in block["transactions"]]
        assert order.index(parent) < order.index(reader) < order.index(spender)
        for tx_hash in expected:
            detail = client.get_transaction(tx_hash)
            assert detail["tx_status"]["status"] == "committed"
            assert detail["tx_status"]["block_hash"] == block_hash
            assert detail["transaction"] == bodies[tx_hash]
        self.did_pass = True
