"""Canonical cells and Accepted-only overlays on both public indexers."""

import time
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import Transactions
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash


class TestIndexerPoolProjection(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        try:
            for name, rich, overlay in (
                ("regular", False, True),
                ("rich", True, True),
                ("chain_only", False, False),
            ):
                rpc, p2p = workload.unused_ports(2)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/indexer_projection/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                cls.nodes.append(node)
                node.prepare()
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                config.setdefault("indexer_v2", {})["index_tx_pool"] = overlay
                config["tx_pool"].update(min_fee_rate=1000, min_rbf_rate=2000)
                config["network"]["connect_outbound_interval_secs"] = 0
                path.write_text(toml.dumps(config))
                workload.add_genesis_test_cells(node)
                if rich:
                    node.startWithRichIndexer()
                else:
                    node.start()
            cls.regular, cls.rich, cls.chain_only = cls.nodes
            cls.Miner.make_tip_height_number(cls.chain_only, 30)
            for node in (cls.regular, cls.rich):
                workload.sync_blocks(cls.chain_only, node, 1, 30)
            cls.base_hash = cls.chain_only.getClient().get_tip_header()["hash"]
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.nodes):
            node.stop()
            workload.save_node_logs(node)
            node.clean()

    def setup_method(self, method):
        self.did_pass = None
        for node in self.nodes:
            client = node.getClient()
            if client.get_tip_header()["hash"] != self.base_hash:
                client.truncate(self.base_hash)
            client.clear_tx_pool()
            self.indexed(node)

    def teardown_method(self, method):
        pass

    @staticmethod
    def indexed(node):
        client = node.getClient()
        tip = client.get_tip_header()
        workload.wait_for(
            lambda: client.get_indexer_tip()
            == {
                "block_hash": tip["hash"],
                "block_number": tip["number"],
            },
            "indexer follows exact canonical hash and height",
            60,
        )

    @staticmethod
    def view(node, lock):
        client = node.getClient()
        search = {"script": lock, "script_type": "lock", "script_search_mode": "exact"}
        result = client.get_cells(search, "asc", "0x64", None)
        cells = {
            (item["out_point"]["tx_hash"], item["out_point"]["index"]): int(
                item["output"]["capacity"], 16
            )
            for item in result["objects"]
        }
        capacity = client.get_cells_capacity(search)
        return cells, int(capacity["capacity"], 16)

    def expect_cells(self, node, lock, expected):
        workload.wait_for(
            lambda: self.view(node, lock) == (expected, sum(expected.values())),
            "exact indexed cells and capacity",
            30,
        )

    # TEST-MAP: NTD-91
    def test_overlay_tracks_accepted_but_ignores_waiting_and_replaced_inputs(self):
        """
        1. 普通/rich 开启 overlay，第三节点禁用；核对完整链上输入及 capacity。
        2. 接收 A 后删除，分别验证输入隐藏和恢复，禁用对照始终只看链。
        3. 准备 Waiting 和有独立释放输入的 Replaced，验证只有当前 Accepted 消费被隐藏。
        """
        # 1. 预期仅来自八个已知创世 issued cells。
        txs = Transactions(self.regular, 91)
        a = txs.build([0, 1], fee=2000)
        lock = a["outputs"][0]["lock"]
        original = {(p["tx_hash"], p["index"]): 6500 * workload.CKB for p in txs.cells}
        for node in self.nodes:
            self.expect_cells(node, lock, original)
        # 2. 两种公开索引均须刷新成员变化；禁用 overlay 的结果不随池变化。
        for node in self.nodes:
            client = node.getClient()
            assert client.send_transaction(a) == transaction_hash(a)
            hidden = {
                key: value
                for key, value in original.items()
                if key not in {(p["tx_hash"], p["index"]) for p in txs.cells[:2]}
            }
            self.expect_cells(
                node, lock, original if node is self.chain_only else hidden
            )
            assert client.remove_transaction(transaction_hash(a)) is True
            self.expect_cells(node, lock, original)
        # 3. Waiting 的输入 2、Replaced A 释放的输入 1 均应继续可见。
        missing = txs.build([3])
        waiter = txs.build([2, txs.point(missing)])
        replacement = txs.build([0], fee=100000)
        for node in self.nodes:
            client = node.getClient()
            assert client.send_transaction(a) == transaction_hash(a)
            assert client.send_transaction(replacement) == transaction_hash(replacement)
            assert transaction_hash(a) in client.get_raw_tx_pool(True)["conflicted"]
            assert client.notify_transaction(waiter) == transaction_hash(waiter)
            workload.wait_for(
                lambda: int(client.tx_pool_info()["orphan"], 16) == 1,
                "independent waiter retained",
            )
            assert transaction_hash(waiter) not in workload.pool_transactions(client)
            consumed = (txs.cells[0]["tx_hash"], txs.cells[0]["index"])
            expected = {
                key: value for key, value in original.items() if key != consumed
            }
            self.expect_cells(
                node, lock, original if node is self.chain_only else expected
            )
        self.did_pass = True

    # TEST-MAP: NTD-93
    def test_truncate_then_equal_height_branch_replaces_indexed_cells(self):
        """
        1. 两种 indexer 追平含旧分支独有 cell 的链，核对 hash/height 和精确集合。
        2. 实际 truncate 到共同块并停止出块，检查索引退回和旧 cell 消失。
        3. 构建另一合法分支至相同高度，索引按新 hash 和 canonical cell 集合收敛。
        """
        # 1. 链上差异来自花费同一输入的两个有效交易，不依赖池 overlay。
        source = self.chain_only
        client = source.getClient()
        txs = Transactions(source, 93)
        old = txs.build([0], fee=1000, nonce=1)
        lock = old["outputs"][0]["lock"]
        original = {(p["tx_hash"], p["index"]): 6500 * workload.CKB for p in txs.cells}
        common = {
            key: value
            for key, value in original.items()
            if key != (txs.cells[0]["tx_hash"], txs.cells[0]["index"])
        }
        old_hash = client.send_transaction(old)
        self.Miner.miner_until_tx_committed(source, old_hash)
        old_height, old_tip = (
            client.get_tip_block_number(),
            client.get_tip_header()["hash"],
        )
        for node in (self.regular, self.rich):
            workload.sync_blocks(source, node, 31, old_height)
        for node in self.nodes:
            self.indexed(node)
            self.expect_cells(
                node,
                lock,
                {**common, (old_hash, "0x0"): int(old["outputs"][0]["capacity"], 16)},
            )
        # 2. 等待精确 hash，不以高度相等误判 indexer 已完成切换。
        for node in self.nodes:
            node.getClient().truncate(self.base_hash)
            node.getClient().clear_tx_pool()
            self.indexed(node)
            self.expect_cells(node, lock, original)
        # 3. 停在旧 tip 的相同高度，强制观察同高度不同 hash。
        new = txs.build([0], fee=2000, nonce=2)
        new_hash = client.send_transaction(new)
        self.Miner.miner_until_tx_committed(source, new_hash)
        assert client.get_tip_block_number() <= old_height
        self.Miner.make_tip_height_number(source, old_height)
        new_tip = client.get_tip_header()["hash"]
        assert new_tip != old_tip
        start = time.monotonic()
        for node in (self.regular, self.rich):
            workload.sync_blocks(source, node, 31, old_height)
            self.indexed(node)
            self.expect_cells(
                node,
                lock,
                {**common, (new_hash, "0x0"): int(new["outputs"][0]["capacity"], 16)},
            )
            assert node.getClient().get_indexer_tip()["block_hash"] == new_tip
        print(
            {
                "same_height": old_height,
                "old_hash": old_tip,
                "new_hash": new_tip,
                "both_indexers_seconds": time.monotonic() - start,
            }
        )
        self.did_pass = True
