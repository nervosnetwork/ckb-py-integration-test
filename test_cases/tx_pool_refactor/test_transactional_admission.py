"""Atomic RBF and capacity admission through ordinary public RPC."""

import hashlib
import time
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import (
    Transactions,
    assert_pool,
    rpc_result,
    size_in_block,
)
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash


class TestTransactionalAdmission(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        try:
            for name in ("target", "observer"):
                rpc, p2p = workload.unused_ports(2)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/transactional_admission/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                cls.nodes.append(node)
                node.prepare()
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                config["tx_pool"].update(
                    max_tx_pool_size=20_000_000,
                    max_ancestors_count=200,
                    min_fee_rate=1000,
                    min_rbf_rate=2000,
                    keep_rejected_tx_hashes_days=1,
                    keep_rejected_tx_hashes_count=10000,
                )
                config["network"]["connect_outbound_interval_secs"] = 0
                path.write_text(toml.dumps(config))
                workload.add_genesis_test_cells(node)
                path = Path(node.ckb_specs_config_path)
                spec = toml.loads(path.read_text())
                cell = spec["genesis"]["issued_cells"][-1]
                spec["genesis"]["issued_cells"].extend(
                    [cell.copy() for _ in range(120)]
                )
                path.write_text(toml.dumps(spec))
                node.start()
            cls.node, cls.observer = cls.nodes
            cls.Miner.make_tip_height_number(cls.node, 30)
            workload.sync_blocks(cls.node, cls.observer, 1, 30)
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
            for peer in node.getClient().get_peers():
                node.getClient().remove_node(peer["node_id"])
            workload.wait_for(lambda: node.get_connected_count() == 0, "isolated pool")
            node.getClient().clear_tx_pool()
        self.set_capacity(20_000_000)
        tag = int.from_bytes(
            hashlib.sha256(method.__name__.encode()).digest()[:4], "little"
        )
        self.txs = Transactions(self.node, tag)
        self.client = self.node.getClient()

    def teardown_method(self, method):
        pass

    def set_capacity(self, size):
        # A real public configuration change, applied at a verified process boundary.
        workload.stop_and_assert_process_exit(self.node)
        path = Path(self.node.ckb_toml_path)
        config = toml.loads(path.read_text())
        config["tx_pool"]["max_tx_pool_size"] = size
        path.write_text(toml.dumps(config))
        self.node.start()
        workload.wait_for(
            lambda: self.node.getClient().tx_pool_info()["tip_number"] == "0x1e",
            "pool ready",
        )

    def admit(self, *bodies):
        for tx in bodies:
            assert self.client.send_transaction(tx) == transaction_hash(tx)

    def rejected(self, tx, code, method="send_transaction"):
        result = rpc_result(self.client, method, [tx, "passthrough"])
        assert result["error"]["code"] == code, result
        return result["error"]

    # TEST-MAP: TPA-01
    # TEST-MAP: TPA-08
    def test_preview_then_replace_whole_descendant_closure(self):
        """
        1. 接收 A→C→D 和独立 I，连接观察节点并确认正常传播。
        2. 预检合格 B，验证 fee/cycles、原池及两节点无传播副作用。
        3. 真实提交 B，核对 A/C/D 全部退出、I 保留及总量。
        """
        # 1. 每笔交易由真实 RPC 接收，连接上的独立交易作传播正对照。
        txs = self.txs
        a = txs.build([0])
        c = txs.build([txs.point(a)])
        d = txs.build([txs.point(c)])
        independent = txs.build([1])
        self.node.connected(self.observer)
        self.admit(a, c, d, independent)
        for tx in (a, c, d, independent):
            workload.assert_pool_transaction(
                self.observer, transaction_hash(tx), tx, workload.FAST_CYCLES
            )
        assert_pool(self.node, txs, [a, c, d, independent])
        b = txs.build([0], fee=10000)
        before = [node.getClient().get_raw_tx_pool(True) for node in self.nodes]

        # 2. 固定 tip 的 preview 不写 Accepted、history 或 recent reject。
        result = self.client.test_tx_pool_accept(b, "passthrough")
        assert int(result["fee"], 16) == 10000
        assert int(result["cycles"], 16) == workload.FAST_CYCLES
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            for node, expected in zip(self.nodes, before):
                assert node.getClient().get_raw_tx_pool(True) == expected
                assert (
                    node.getClient().get_transaction(transaction_hash(b))["tx_status"][
                        "status"
                    ]
                    == "unknown"
                )
            time.sleep(0.1)

        # 3. 实际提交才改变最终集合；费用和字节由输入夹具独立计算。
        self.admit(b)
        assert_pool(self.node, txs, [b, independent])
        self.did_pass = True

    # TEST-MAP: TPA-02
    def test_replacement_rejects_new_unconfirmed_input_atomically(self):
        """
        1. 接收 A、其后代 C 和独立未确认父 P。
        2. 提交高费 B，同时冲突 A 并新增 P 输入。
        3. 核对 -1111、原成员和账本完全保持。
        """
        # 1. 独立 P 的输入没有出现在原冲突根中。
        txs = self.txs
        a, p = txs.build([0]), txs.build([1])
        c = txs.build([txs.point(a)])
        self.admit(a, c, p)
        before = self.client.get_raw_tx_pool(True)
        # 2. 费用充分，拒绝原因限定为新增未确认输入。
        b = txs.build([0, txs.point(p)], fee=100000)
        error = self.rejected(b, -1111)
        assert "unconfirmed inputs" in str(error)
        # 3. 失败没有提交任何裁剪。
        assert self.client.get_raw_tx_pool(True) == before
        assert_pool(self.node, txs, [a, c, p])
        self.did_pass = True

    # TEST-MAP: TPA-03
    # TEST-MAP: TPA-23
    def test_shared_descendant_fee_boundary_counts_each_victim_once(self):
        """
        1. 接收两个根 A/D 及共享后代 C，独立计算最低替换费 F。
        2. 真实提交 F−1，核对 -1111 和原池保持。
        3. 提交恰好 F 的 B，检查共享后代只计一次且账本正确。
        """
        # 1. 两条冲突路径共享 C，固定费用不从 min_replace_fee 读取。
        txs = self.txs
        a, d = txs.build([0]), txs.build([1])
        c = txs.build([txs.point(a), txs.point(d)])
        self.admit(a, d, c)
        shape = txs.build([0, 1], fee=10000)
        required = 3000 + 2000 * size_in_block(shape) // 1000
        before = self.client.get_raw_tx_pool(True)
        # 2. 少一 shannon 必须原子失败。
        self.rejected(txs.build([0, 1], fee=required - 1), -1111)
        assert self.client.get_raw_tx_pool(True) == before
        assert_pool(self.node, txs, [a, d, c])
        # 3. 等号只支付去重的三笔原费用。
        b = txs.build([0, 1], fee=required)
        self.admit(b)
        assert_pool(self.node, txs, [b])
        self.did_pass = True

    # TEST-MAP: TPA-04
    # TEST-MAP: TPA-24
    def test_replacement_mutation_limit(self):
        """
        1. 逐笔接收 100/101 项链式后代及独立 I，确认容量充分。
        2. 以充分费用替换根输入，分别要求成功或 -1111。
        3. 核对成功的完整替换或失败的原集合及总量保持。
        """
        for count in (100, 101):
            self.client.clear_tx_pool()
            # 1. 每个祖先真实入池；配置允许 200 个祖先。
            txs = self.txs
            chain = [txs.build([0], nonce=count)]
            for _ in range(count - 1):
                chain.append(txs.build([txs.point(chain[-1])]))
            independent = txs.build([1], fee=1000000)
            self.admit(*chain, independent)
            assert_pool(self.node, txs, chain + [independent])
            before = self.client.get_raw_tx_pool(True)
            # 2. 变化只有冲突闭包数量，容量不参与裁剪。
            b = txs.build([0], fee=10000000, nonce=count)
            if count == 100:
                self.admit(b)
            else:
                self.rejected(b, -1111)
            # 3. 101 分支验证完整旧集合，而不是只检查根交易。
            if count == 100:
                assert_pool(self.node, txs, [b, independent])
            else:
                assert self.client.get_raw_tx_pool(True) == before
                assert_pool(self.node, txs, chain + [independent])
        self.did_pass = True

    # TEST-MAP: TPA-05
    # TEST-MAP: TPA-25
    def test_capacity_self_eviction_rolls_back_all_tentative_victims(self):
        """
        1. 小容量池装满低、中、高费三个独立项，构造较大中间费率 B。
        2. preview 返回 Full，原池、历史及 B 拒绝记录保持。
        3. 真实提交仍 Full，出现拒绝记录但原池和账本完整保持。
        """
        # 1. B 占整个池；即使移除两个低费项仍容不下，最高费 I 应受保护。
        txs = self.txs
        a, e, i = (
            txs.build([index], fee=fee)
            for index, fee in enumerate((1000, 2000, 1000000))
        )
        size = size_in_block(a)
        self.set_capacity(3 * size)
        self.admit(a, e, i)
        b = txs.pad_to(txs.build([3], fee=12000), 3 * size)
        assert 2000 / size < 12000 / size_in_block(b) < 1000000 / size
        before = self.client.get_raw_tx_pool(True)
        # 2. 预检失败不得写 recent reject 或保留裁剪。
        self.rejected(b, -1106, "test_tx_pool_accept")
        assert (
            self.client.get_transaction(transaction_hash(b))["tx_status"]["status"]
            == "unknown"
        )
        assert self.client.get_raw_tx_pool(True) == before
        # 3. 真实拒绝保留所有原成员，仅增加候选拒绝记录。
        self.rejected(b, -1106)
        assert (
            "Full"
            in self.client.get_transaction(transaction_hash(b))["tx_status"]["reason"]
        )
        assert self.client.get_raw_tx_pool(True) == before
        assert_pool(self.node, txs, [a, e, i])
        self.did_pass = True

    # TEST-MAP: TPA-06
    def test_capacity_evicts_descendant_closure_at_exact_size(self):
        """
        1. 建立低费 P→C 和独立高费 I，容量恰为三笔。
        2. 提交占两笔空间的高费 B，保证 P 比 C 的裁剪费率更低。
        3. 核对 P/C 闭包退出、I/B 保留且总字节恰等上限。
        """
        # 1. P 自身及后代包均低于 C，保证先选择 P 的闭包。
        txs = self.txs
        p = txs.build([0], fee=1000)
        c = txs.build([txs.point(p)], fee=2000)
        i = txs.build([1], fee=1000000)
        size = size_in_block(p)
        self.set_capacity(3 * size)
        self.admit(p, c, i)
        # 2. B 不与原输入冲突，只能走容量裁剪。
        b = txs.pad_to(txs.build([2], fee=100000), 2 * size)
        self.admit(b)
        # 3. 包含 block offset 的独立序列化恰等容量。
        assert_pool(self.node, txs, [b, i])
        assert int(self.client.tx_pool_info()["total_tx_size"], 16) == 3 * size
        self.did_pass = True

    # TEST-MAP: TPA-07
    def test_capacity_cannot_remove_candidates_own_producer(self):
        """
        1. 小容量池接收 P 和独立 I。
        2. B 通过 input/cell_dep 引用 P，且 P+B 本身已超容量。
        3. 核对 Full 和 P/I 的集合、字节、cycles 保持。
        """
        for edge in ("input", "cell_dep"):
            self.client.clear_tx_pool()
            # 1. 两笔原成员大小相同。
            txs = self.txs
            p, i = txs.build([0]), txs.build([1], fee=100000)
            size = size_in_block(p)
            self.set_capacity(2 * size)
            self.admit(p, i)
            before = self.client.get_raw_tx_pool(True)
            # 2. 依赖 P 是合法存在的普通 cell，脚本仍只执行 always_success。
            if edge == "input":
                b = txs.build([txs.point(p)], fee=1000000)
            else:
                b = txs.build([2], deps=[txs.point(p)], fee=1000000)
            b = txs.pad_to(b, 2 * size)
            self.rejected(b, -1106)
            # 3. 没有通过先删 P 再接受失效 B 来满足容量。
            assert self.client.get_raw_tx_pool(True) == before
            assert_pool(self.node, txs, [p, i])
        self.did_pass = True

    # TEST-MAP: TPA-09
    # TEST-MAP: TPA-15
    def test_preview_rejection_and_successful_replacement_history_are_distinct(self):
        """
        1. 接收 A→C，按完整闭包独立计算最低替换费 F。
        2. preview/submit F−1，分别核对无拒绝记录/有 RBFRejected，旧池保持。
        3. 提交合格 D，A 进入 conflicted，失败 B 不进入且历史不计 Accepted 总量。
        """
        # 1. A 的链上输入提供有效的替换历史恢复键。
        txs = self.txs
        a = txs.build([0])
        c = txs.build([txs.point(a)])
        self.admit(a, c)
        required = 2000 + 2000 * size_in_block(a) // 1000
        b = txs.build([0], fee=required - 1)
        before = self.client.get_raw_tx_pool(True)
        # 2. 同样的 -1111，在 preview 与真实提交之间保留副作用边界。
        self.rejected(b, -1111, "test_tx_pool_accept")
        assert (
            self.client.get_transaction(transaction_hash(b))["tx_status"]["status"]
            == "unknown"
        )
        assert self.client.get_raw_tx_pool(True) == before
        self.rejected(b, -1111)
        assert (
            "RBFRejected"
            in self.client.get_transaction(transaction_hash(b))["tx_status"]["reason"]
        )
        assert (
            transaction_hash(b) not in self.client.get_raw_tx_pool(True)["conflicted"]
        )
        assert_pool(self.node, txs, [a, c])
        # 3. 成功替换才产生原 Accepted 的历史，不把历史计入池账本。
        d = txs.build([0], fee=required)
        self.admit(d)
        history = self.client.get_raw_tx_pool(True)["conflicted"]
        assert transaction_hash(a) in history and transaction_hash(b) not in history
        assert_pool(self.node, txs, [d])
        self.did_pass = True

    # TEST-MAP: TPA-17
    def test_replacement_and_extra_capacity_eviction_commit_together(self):
        """
        1. 装满 A/E/I，准备替换 A 且额外占一笔空间的 B。
        2. 提交 B，同时触发 RBF 与容量裁剪。
        3. 核对 A/E 退出、B/I 保留及恰等容量的账本。
        """
        # 1. E 费率低于 B，I 高于 B，额外裁剪集合唯一。
        txs = self.txs
        a, e, i = (
            txs.build([index], fee=fee)
            for index, fee in enumerate((1000, 2000, 1000000))
        )
        size = size_in_block(a)
        self.set_capacity(3 * size)
        self.admit(a, e, i)
        # 2. 替换费覆盖 A 及 B 大小的增量费。
        b = txs.pad_to(txs.build([0], fee=100000), 2 * size)
        self.admit(b)
        # 3. 两类移除在同一次准入的最终集合中同时体现。
        assert_pool(self.node, txs, [b, i])
        self.did_pass = True

    # TEST-MAP: TPA-20
    def test_capacity_reranks_ancestor_after_first_descendant_eviction(self):
        """
        1. 构造 A 的低费 B→C 和高费 D 分支，以及独立 E。
        2. 新候选要求先移除 B/C，再多腾出 E 的空间。
        3. 核对重算后 A/D 保留、B/C/E 退出及祖先统计。
        """
        # 1. 用实际序列化大小证明重算前后费率跨越 E，避免费率并列。
        txs = self.txs
        a = txs.build([0], fee=1000, outputs=2)
        b = txs.build([txs.point(a)], fee=800)
        c = txs.build([txs.point(b)], fee=1200)
        d = txs.build([txs.point(a, 1)], fee=15000)
        e = txs.build([1], fee=5000)
        size = size_in_block(e)
        total = sum(map(size_in_block, [a, b, c, d, e]))
        assert 2000 / (2 * size) < 18000 / (size_in_block(a) + 3 * size) < 5000 / size
        assert 5000 / size < 16000 / (size_in_block(a) + size)
        self.set_capacity(total)
        self.admit(a, b, c, d, e)
        # 2. 必须腾出三笔空间，第一轮 B/C 后仍需第二轮。
        candidate = txs.pad_to(txs.build([2], fee=1000000), 3 * size)
        self.admit(candidate)
        # 3. 剩余 A/D 的父子关系和独立账本保持正确。
        assert_pool(self.node, txs, [a, d, candidate])
        entries = workload.pool_transactions(self.client)
        assert int(entries[transaction_hash(d)]["ancestors_count"], 16) == 2
        assert int(entries[transaction_hash(d)]["ancestors_size"], 16) == size_in_block(
            a
        ) + size_in_block(d)
        self.did_pass = True

    # TEST-MAP: TPA-26
    # TEST-MAP: TPA-27
    def test_combined_mutation_union_boundary(self):
        """
        1. 接收共享后代 RBF 闭包、97/98 个低费独立项和高费 I。
        2. 独立设置候选大小，保证完整移除并集后才恰好可容纳。
        3. 并集 100 时接受，101 时 Full 且原池完全保持。
        """
        for victims in (100, 101):
            self.client.clear_tx_pool()
            # 1. 两个冲突根共享一笔后代，闭包去重后固定为三项。
            txs = self.txs
            a, d = txs.build([0], nonce=victims), txs.build([1], nonce=victims)
            c = txs.build([txs.point(a), txs.point(d)])
            extras = [
                txs.build([index], nonce=victims) for index in range(2, victims - 1)
            ]
            independent = txs.build([120], fee=100000000, nonce=victims)
            originals = [a, d, c, *extras, independent]
            capacity = sum(map(size_in_block, originals))
            self.set_capacity(capacity)
            self.admit(*originals)
            assert len(originals) == victims + 1
            assert_pool(self.node, txs, originals)
            before = self.client.get_raw_tx_pool(True)
            # 2. 少移除任意一笔都会超过容量，I 费率高于候选且保留。
            b = txs.pad_to(
                txs.build([0, 1], fee=10000000, nonce=victims),
                capacity - size_in_block(independent),
            )
            assert len(extras) + 3 == victims
            assert size_in_block(b) + size_in_block(independent) == capacity
            assert (
                1000 / size_in_block(a)
                < 10000000 / size_in_block(b)
                < 100000000 / size_in_block(independent)
            )
            # 3. 超总修改上限为 Full；不与 RBF 自身 101 的 -1111 混淆。
            if victims == 100:
                self.admit(b)
                assert_pool(self.node, txs, [b, independent])
            else:
                self.rejected(b, -1106)
                assert self.client.get_raw_tx_pool(True) == before
                assert_pool(self.node, txs, originals)
        self.did_pass = True
