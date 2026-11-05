"""Black-box rich-indexer rollback, with identical PostgreSQL/SQLite oracles.

Run both backends when PostgreSQL is configured; otherwise skip that backend.
See reviews/ckb-pr-5300-rich-indexer-rollback.md for binary selection and database credentials.
"""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import uuid

from parameterized import parameterized
import requests
import toml

from framework.basic import CkbTest
from framework.rpc import RPCClient
from framework.util import get_project_root


ROOT = Path(get_project_root())
BACKENDS = [("postgres",), ("sqlite",)]
TIMEOUT = 60
FEE = 10_000
CELL_CAPACITY = 1_000 * 100_000_000


class BoundedRPCClient(RPCClient):
    """Bound each request as well as the polling loops around it."""

    def call(self, method, params, try_count=1):
        response = requests.post(
            self.url,
            json={"id": 1, "jsonrpc": "2.0", "method": method, "params": params},
            timeout=5,
        )
        response.raise_for_status()
        body = response.json()
        assert "error" not in body, f"{method}: {body.get('error')}"
        return body["result"]


@unittest.skipIf(CkbTest.skip_docker(), "Requires native CKB processes; unset DOCKER")
class TestRichIndexerRollback(CkbTest):
    def setup_method(self, method):
        self.did_pass = None
        self.resources = ExitStack()
        self.case_dir = None
        self.processes = {}

    def teardown_method(self, method):
        # Stop only processes owned by this case, then drop only its database.
        # Avoid CkbTest's global tmp copy and CkbNode.stop's port-based kill.
        cleanup_succeeded = False
        try:
            self.resources.close()
            cleanup_succeeded = True
        finally:
            if self.case_dir is not None:
                if self.did_pass and cleanup_succeeded:
                    shutil.rmtree(self.case_dir)
                else:
                    # 公共失败报告也可能打包整个 tmp，因此先脱敏原始配置。
                    for config_path in self.case_dir.glob("node*/ckb.toml"):
                        try:
                            config = toml.load(config_path)
                            rich = config.get("indexer_v2", {}).get("rich_indexer", {})
                            if "db_password" in rich:
                                rich["db_password"] = "<redacted>"
                                config_path.write_text(toml.dumps(config))
                        except Exception:
                            # 不保留无法安全脱敏的配置，也不打印可能含密码的异常。
                            config_path.unlink(missing_ok=True)
                            print(
                                f"Omitted unreadable or unredactable config: {config_path}"
                            )
                    report = ROOT / "report/rich_indexer_rollback" / self.case_dir.name
                    shutil.copytree(self.case_dir, report)
                    print(f"Saved failing node data and logs: {report}")

    # filtered 表示索引不收录区块交易；区块本身仍合法，节点也已接受它。
    # S 始终不花费；X 用于核对保留/恢复；Y 是旧分叉花费 X 时创建的输出。
    # W 是新主链的输出；Z 是回滚后再次花费 X 创建的输出。

    # TEST-MAP: RICH-ROLLBACK-01
    # 准备：在指定数据库后端建立分叉，让旧分叉的一个区块被索引过滤规则排除。
    # 触发：先确认该区块已处理但交易未入索引，再切换到累计工作量更大的新链。
    # 观察：节点和索引的 tip，以及 cell、交易明细和容量的公开查询结果。
    # 断言：空交易列表的回滚能完成，S、X 保留，新链输出入索引，过滤交易不出现。
    @parameterized.expand(BACKENDS)
    def test_single_filtered_block(self, backend):
        self._prepare(backend, ["filtered"])
        self._mine_old_fork()
        self._assert_indexed_state([self.s, self.x], {self.genesis_hash})

        self._switch_to_new_chain()
        self._assert_restored_state()

    # TEST-MAP: RICH-ROLLBACK-02
    # 准备：旧分叉连续三个区块都被索引过滤，且索引 tip 确实追到旧链末端。
    # 触发：让节点采用更强的新链，连续撤销这三个没有索引交易的旧区块。
    # 观察：索引是否追到新链的确切高度和 hash，以及完整查询结果。
    # 断言：连续空记录回滚不中断；S、X 和新链输出、交易明细、容量均正确。
    @parameterized.expand(BACKENDS)
    def test_three_consecutive_filtered_blocks(self, backend):
        self._prepare(backend, ["filtered"] * 3)
        self._mine_old_fork()
        self._assert_indexed_state([self.s, self.x], {self.genesis_hash})

        self._switch_to_new_chain()
        self._assert_restored_state()

    # TEST-MAP: RICH-ROLLBACK-03
    # 准备：旧分叉收录一笔正常交易，花费 X 并创建 Y，先验证旧链索引状态。
    # 触发：切换到另一条更强的链；新链花费辅助输入，使旧交易无法再次上链。
    # 观察：公开索引查询中的 live cell、交易明细及容量，并核对节点端 X 的状态。
    # 断言：X 恢复可花费，Y 和旧交易退出索引查询；S 保留且新链输出被收录。
    @parameterized.expand(BACKENDS)
    def test_normal_spent_cell_rollback(self, backend):
        self._prepare(backend, ["indexed"])
        self._mine_old_fork()
        self._assert_indexed_state(
            [self.s, self.y], {self.genesis_hash, self.old_tx["hash"]}
        )

        self._switch_to_new_chain()
        self._assert_restored_state()

    # TEST-MAP: RICH-ROLLBACK-04
    # 准备：旧分叉同时包含正常索引区块和过滤区块，交换排列以覆盖两种回滚顺序。
    # 触发：分别执行先撤销过滤区块、先撤销正常区块的主链切换。
    # 观察：两种顺序下的最终索引 tip、cell、交易明细和容量。
    # 断言：两种顺序均恢复 X、移除 Y 和旧交易的索引记录，并正确收录新链输出。
    @parameterized.expand(
        [
            (backend, order)
            for backend in ("postgres", "sqlite")
            for order in ("filtered_first", "indexed_first")
        ]
    )
    def test_mixed_rollback_in_both_orders(self, backend, rollback_order):
        # Layout is ascending height; rollback visits it in reverse.
        layout = (
            ["indexed", "filtered"]
            if rollback_order == "filtered_first"
            else ["filtered", "indexed"]
        )
        self._prepare(backend, layout)
        self._mine_old_fork()
        self._assert_indexed_state(
            [self.s, self.y], {self.genesis_hash, self.old_tx["hash"]}
        )

        self._switch_to_new_chain()
        self._assert_restored_state()

    # TEST-MAP: RICH-ROLLBACK-05
    # 准备：完成混合区块回滚，先确认 X 已恢复且当前索引结果正确。
    # 触发：在新主链上再次花费 X，打包新交易并等待节点和索引同步。
    # 观察：花费后的 live cell、匹配查询锁的完整输入/输出类型和序号，以及容量。
    # 断言：X 不再是 live cell，花费交易及其输出 Z 被收录，S 和原新链输出保留。
    @parameterized.expand(BACKENDS)
    def test_spend_restored_cell_and_continue_indexing(self, backend):
        self._prepare(backend, ["indexed", "filtered"])
        self._mine_old_fork()
        self._switch_to_new_chain()
        self._assert_restored_state()

        self._spend_restored_x()
        self._assert_indexed_state(
            [self.s, self.w, self.z],
            {self.genesis_hash, self.new_tx["hash"], self.spend_tx["hash"]},
        )

    # TEST-MAP: RICH-ROLLBACK-06
    # 准备：完成混合回滚并保存查询快照，保留原节点目录、数据库和配置。
    # 触发：停止并重启 rich-indexer 节点，再连接对端并花费已恢复的 X。
    # 观察：重启前后配置和查询快照，以及重启后新交易的索引结果。
    # 断言：重启后状态完全一致；之后仍能更新 X、Z 的 live 状态、交易明细和容量。
    @parameterized.expand(BACKENDS)
    def test_restart_preserves_rollback_and_continues_indexing(self, backend):
        self._prepare(backend, ["indexed", "filtered"])
        self._mine_old_fork()
        self._switch_to_new_chain()
        self._assert_restored_state()
        before = self._snapshot()
        config_before = Path(self.rich_node.ckb_toml_path).read_bytes()

        self._stop(self.rich_node)
        self._start(self.rich_node, rich=True)
        self._wait_for_indexer_tip(self.new_tip)
        # 仍逐字节比较，但失败时不让 pytest 展开含数据库密码的配置。
        if Path(self.rich_node.ckb_toml_path).read_bytes() != config_before:
            raise AssertionError("Restart changed the node configuration")
        assert self._snapshot() == before, "Restart changed persisted indexer results"
        self._assert_restored_state()

        self._connect()
        self._spend_restored_x()
        self._assert_indexed_state(
            [self.s, self.w, self.z],
            {self.genesis_hash, self.new_tx["hash"], self.spend_tx["hash"]},
        )

    def _prepare(self, backend, layout):
        binary = Path(
            os.environ.get("CKB_TEST_BINARY", ROOT / "download/current/ckb")
        ).resolve()
        assert binary.is_file() and os.access(
            binary, os.X_OK
        ), f"CKB binary missing: {binary}"
        version = subprocess.check_output(
            [str(binary), "--version"], text=True, timeout=15
        ).strip()
        checksum = hashlib.sha256(binary.read_bytes()).hexdigest()
        expected_checksum = os.environ.get("CKB_TEST_BINARY_SHA256")
        if expected_checksum:
            assert checksum == expected_checksum, "CKB binary SHA-256 mismatch"
        print(
            f"CKB binary: {binary}\nVersion: {version}\nSHA-256: {checksum}\nBackend: {backend}"
        )
        db_config = self._database_config(backend)
        parent = ROOT / "tmp/rich_indexer_rollback"
        parent.mkdir(parents=True, exist_ok=True)
        self.case_dir = Path(tempfile.mkdtemp(prefix=f"{backend}_", dir=parent))
        self.layout = layout
        self.first_fork_height = 33
        self.filtered_heights = {
            self.first_fork_height + offset
            for offset, kind in enumerate(layout)
            if kind == "filtered"
        }

        contract = ROOT / "source/contract/always_success"
        code_hash = (
            "0x"
            + hashlib.blake2b(
                contract.read_bytes(), digest_size=32, person=b"ckb-default-hash"
            ).hexdigest()
        )
        self.lock = {"code_hash": code_hash, "hash_type": "data", "args": "0x5300"}
        auxiliary_lock = dict(self.lock, args="0x5301")
        spec = toml.load(ROOT / self.CkbNodeConfigPath.CURRENT_TEST.ckb_spec_path)
        assert spec["pow"]["func"] == "Dummy"
        assert spec["params"]["permanent_difficulty_in_dummy"] is True
        spec["genesis"]["system_cells"].append(
            {
                "file": {"file": "specs/cells/always_success"},
                "create_type_id": False,
            }
        )
        # Different capacities identify S, X, G, F0..F2, N unambiguously.
        for index in range(7):
            spec["genesis"]["issued_cells"].append(
                {
                    "capacity": CELL_CAPACITY + index,
                    "lock": self.lock if index < 2 else auxiliary_lock,
                }
            )

        reservations = []
        for _ in range(4):
            reservation = socket.socket()
            reservation.bind(("127.0.0.1", 0))
            self.resources.callback(reservation.close)
            reservations.append(reservation)
        ports = [sock.getsockname()[1] for sock in reservations]
        self.nodes = []
        for index in range(2):
            relative_dir = (self.case_dir / f"node{index}").relative_to(ROOT / "tmp")
            node = self.CkbNode.init_dev_by_port(
                self.CkbNodeConfigPath.CURRENT_TEST,
                str(relative_dir),
                ports[index * 2],
                ports[index * 2 + 1],
            )
            node.prepare()
            shutil.copy2(binary, node.ckb_bin_path)
            cells_dir = Path(node.ckb_dir) / "specs/cells"
            cells_dir.mkdir(parents=True)
            shutil.copy2(contract, cells_dir / "always_success")
            with open(node.ckb_specs_config_path, "w") as stream:
                toml.dump(spec, stream)
            config = toml.load(node.ckb_toml_path)
            config["rpc"]["listen_address"] = f"127.0.0.1:{ports[index * 2]}"
            config["network"]["listen_addresses"] = [
                f"/ip4/127.0.0.1/tcp/{ports[index * 2 + 1]}"
            ]
            config["block_assembler"]["message"] = f"0x530{index}"
            config["indexer_v2"] = {"index_tx_pool": False}
            if index == 1:
                config["indexer_v2"]["rich_indexer"] = db_config
                if self.filtered_heights:
                    config["indexer_v2"]["block_filter"] = " && ".join(
                        f'block.header.number.to_uint() != "{hex(height)}".to_uint()'
                        for height in sorted(self.filtered_heights)
                    )
            with open(node.ckb_toml_path, "w") as stream:
                toml.dump(config, stream)
            node.client = BoundedRPCClient(node.rpcUrl)
            self.nodes.append(node)
            self.resources.callback(self._stop, node)
        self.full_node, self.rich_node = self.nodes
        for reservation in reservations:
            reservation.close()
        self._start(self.full_node, rich=False)
        self._start(self.rich_node, rich=True)

        genesis = self.full_node.client.get_block_by_number("0x0")
        self.genesis_hash = genesis["transactions"][0]["hash"]
        issued = {}
        for transaction in genesis["transactions"]:
            for index, output in enumerate(transaction["outputs"]):
                point = {"tx_hash": transaction["hash"], "index": hex(index)}
                data = transaction["outputs_data"][index]
                if data == "0x" + contract.read_bytes().hex():
                    self.dep = {"out_point": point, "dep_type": "code"}
                if output["lock"] in (self.lock, auxiliary_lock):
                    issued[int(output["capacity"], 16) - CELL_CAPACITY] = {
                        "out_point": point,
                        "output": output,
                        "output_data": data,
                    }
        assert set(issued) == set(range(7)), "Genesis fixture cells missing"
        self.s, self.x, self.g = [issued[index] for index in range(3)]
        for _ in range(30):
            self._mine_block(self.full_node)

        self.old_tx = self._register_tx([self.x, self.g], self.lock, "0x01")
        self.filtered_txs = [
            self._register_tx([issued[index]], self.lock, f"0x{index:02x}")
            for index in range(3, 6)
        ]
        # The winning branch consumes G and all F inputs: detached old
        # transactions cannot become valid again, while X remains spendable.
        self.blocker_tx = self._register_tx(
            [issued[index] for index in range(2, 6)], auxiliary_lock, "0x02"
        )
        self.new_tx = self._register_tx([issued[6]], self.lock, "0x03")
        self.y = self._output_cell(self.old_tx)
        self.w = self._output_cell(self.new_tx)
        assert all(
            cell["out_point"]["tx_hash"] == self.genesis_hash
            for cell in (self.s, self.x)
        ), "Expected S and X in the same genesis transaction"
        # Grouped transaction cells are [input/output, index] pairs. Genesis
        # outputs remain in transaction history even after X is spent.
        self.expected_transaction_cells = {
            self.genesis_hash: [
                ["output", self.s["out_point"]["index"]],
                ["output", self.x["out_point"]["index"]],
            ],
            # X is input 0; auxiliary input G must not match the queried lock.
            self.old_tx["hash"]: [["input", "0x0"], ["output", "0x0"]],
            # N has the auxiliary lock, so only the new output matches.
            self.new_tx["hash"]: [["output", "0x0"]],
        }
        proposal_txs = [self.old_tx, *self.filtered_txs, self.blocker_tx, self.new_tx]
        window = self.full_node.client.get_consensus()["tx_proposal_window"]
        assert (
            int(window["closest"], 16) == 2
        ), f"Unexpected devnet proposal window: {window}"
        assert int(window["farthest"], 16) >= len(layout) + 2
        self._mine_block(
            self.full_node, proposals=[tx["hash"][:22] for tx in proposal_txs]
        )
        self.ancestor = self._mine_block(self.full_node)
        assert int(self.ancestor["number"], 16) == self.first_fork_height - 1
        # Prepare conflicting proposals before connecting: only the mined
        # common blocks are shared, not transient transaction-pool fixtures.
        self._connect()
        self._wait_for_node_tip(self.rich_node, self.ancestor)
        self._wait_for_indexer_tip(self.ancestor)
        self._assert_indexed_state([self.s, self.x], {self.genesis_hash})
        self.rich_node.client.set_network_active(False)
        self.full_node.client.set_network_active(False)
        # set_network_active only discards incoming messages; it does not
        # close sessions. Disable both ends, then explicitly remove the peer.
        self.rich_node.client.remove_node(self.full_node.get_peer_id())
        self._wait(
            lambda: not self.rich_node.client.get_peers()
            and not self.full_node.client.get_peers(),
            "disconnect before fork",
        )

    def _database_config(self, backend):
        if backend == "sqlite":
            return {"db_type": "sqlite"}
        required = ("CKB_TEST_PG_HOST", "CKB_TEST_PG_USER", "CKB_TEST_PG_PASSWORD")
        if not any(name in os.environ for name in (*required, "CKB_TEST_PG_PORT")):
            self.skipTest(
                "PostgreSQL is not configured; full #5300 coverage requires it"
            )
        missing = [name for name in required if name not in os.environ]
        assert (
            not missing
        ), f"PostgreSQL prerequisite missing: {', '.join(missing)}; see reviews/ckb-pr-5300-rich-indexer-rollback.md"
        import psycopg
        from psycopg import sql

        config = {
            "db_type": "postgres",
            "db_name": "ckb_5300_" + uuid.uuid4().hex,
            "db_host": os.environ[required[0]],
            "db_port": int(os.environ.get("CKB_TEST_PG_PORT", "5432")),
            "db_user": os.environ[required[1]],
            "db_password": os.environ[required[2]],
        }
        admin = psycopg.connect(
            host=config["db_host"],
            port=config["db_port"],
            user=config["db_user"],
            password=config["db_password"],
            dbname="postgres",
            connect_timeout=5,
            autocommit=True,
        )
        self.resources.callback(admin.close)
        admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config["db_name"]))
        )
        self.resources.callback(
            admin.execute,
            sql.SQL("DROP DATABASE {}").format(sql.Identifier(config["db_name"])),
        )
        return config

    def _start(self, node, rich):
        log = open(Path(node.ckb_dir) / "node.log", "ab")
        try:
            self.processes[node] = subprocess.Popen(
                [
                    node.ckb_bin_path,
                    "run",
                    "--rich-indexer" if rich else "--indexer",
                    "--skip-spec-check",
                ],
                cwd=node.ckb_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            log.close()

        def ready():
            assert (
                self.processes[node].poll() is None
            ), f"CKB exited; inspect {node.ckb_dir}/node.log"
            try:
                return node.client.get_tip_header() is not None
            except requests.RequestException:
                return False

        self._wait(ready, f"RPC startup: {node.rpcUrl}")

    def _stop(self, node):
        process = self.processes.pop(node, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise AssertionError(f"CKB did not stop gracefully: {node.ckb_dir}")

    def _connect(self):
        self.rich_node.connected(self.full_node)
        self._wait(lambda: bool(self.rich_node.client.get_peers()), "peer connection")

    @staticmethod
    def _wait(predicate, description):
        deadline = time.monotonic() + TIMEOUT
        last = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(0.1)
        raise AssertionError(
            f"Timed out after {TIMEOUT}s: {description}; last result: {last}"
        )

    def _wait_for_node_tip(self, node, header):
        self._wait(
            lambda: node.client.get_tip_header()["hash"] == header["hash"],
            f"node tip {header['number']} / {header['hash']}",
        )

    def _wait_for_indexer_tip(self, header):
        expected = {"block_hash": header["hash"], "block_number": header["number"]}
        self._wait(
            lambda: self.rich_node.client.get_indexer_tip() == expected,
            f"rich-indexer tip {expected}; logs: {self.rich_node.ckb_dir}/node.log",
        )

    def _register_tx(self, inputs, lock, data):
        transaction = {
            "version": "0x0",
            "cell_deps": [self.dep],
            "header_deps": [],
            "inputs": [
                {"previous_output": cell["out_point"], "since": "0x0"}
                for cell in inputs
            ],
            "outputs": [
                {
                    "capacity": hex(
                        sum(int(cell["output"]["capacity"], 16) for cell in inputs)
                        - FEE
                    ),
                    "lock": lock,
                    "type": None,
                }
            ],
            "outputs_data": [data],
            "witnesses": [],
        }
        tx_hash = self.full_node.client.send_transaction(transaction)
        assert self.full_node.client.remove_transaction(
            tx_hash
        ), f"Could not remove proposal fixture {tx_hash}"
        return {
            "hash": tx_hash,
            "data": transaction,
            "cycles": None,
            "depends": None,
            "required": False,
        }

    @staticmethod
    def _output_cell(tx):
        return {
            "out_point": {"tx_hash": tx["hash"], "index": "0x0"},
            "output": tx["data"]["outputs"][0],
            "output_data": tx["data"]["outputs_data"][0],
        }

    def _mine_block(self, node, transactions=(), proposals=()):
        parent = node.client.get_tip_header()

        def current_template():
            template = node.client.get_block_template()
            return template if template["parent_hash"] == parent["hash"] else None

        template = self._wait(current_template, "block template catches up to parent")
        template["transactions"] = deepcopy(list(transactions))
        template["proposals"] = list(proposals)
        template["uncles"] = []
        template["current_time"] = hex(
            max(int(template["current_time"], 16), int(parent["timestamp"], 16) + 1)
        )
        block_hash = node.client.generate_block_with_template(template)
        block = node.client.get_block(block_hash)
        assert [tx["hash"] for tx in block["transactions"][1:]] == [
            tx["hash"] for tx in transactions
        ]
        assert block["header"]["parent_hash"] == parent["hash"]
        assert int(block["header"]["number"], 16) == int(parent["number"], 16) + 1
        return block["header"]

    def _mine_old_fork(self):
        self.old_blocks = []
        self.filtered_in_old_fork = []
        for offset, kind in enumerate(self.layout):
            tx = self.old_tx if kind == "indexed" else self.filtered_txs[offset]
            header = self._mine_block(self.rich_node, [tx])
            assert int(header["number"], 16) == self.first_fork_height + offset
            self._wait_for_indexer_tip(header)
            self.old_blocks.append(header)
            if kind == "filtered":
                self.filtered_in_old_fork.append(tx["hash"])
                assert tx["hash"] not in {
                    row["tx_hash"] for row in self._query_all("get_transactions")
                }
        self.old_tip = self.old_blocks[-1]
        expected = [self.s, self.y] if "indexed" in self.layout else [self.s, self.x]
        hashes = (
            {self.genesis_hash, self.old_tx["hash"]}
            if "indexed" in self.layout
            else {self.genesis_hash}
        )
        self._assert_indexed_state(expected, hashes)

    def _switch_to_new_chain(self):
        assert (
            self.full_node.client.get_tip_header()["hash"] == self.ancestor["hash"]
        ), "Old fork leaked into the competing node"
        first_new = self._mine_block(self.full_node, [self.blocker_tx])
        assert first_new["hash"] != self.old_blocks[0]["hash"]
        for _ in range(len(self.layout) - 1):
            self._mine_block(self.full_node)
        self.new_tip = self._mine_block(self.full_node, [self.new_tx])
        assert int(self.new_tip["number"], 16) not in self.filtered_heights
        assert int(self.new_tip["number"], 16) > int(self.old_tip["number"], 16)
        assert (
            self.new_tip["compact_target"] == self.old_tip["compact_target"]
        ), "Expected fixed difficulty"
        self.rich_node.client.set_network_active(True)
        self.full_node.client.set_network_active(True)
        self._connect()
        self._wait_for_node_tip(self.rich_node, self.new_tip)
        self._wait_for_indexer_tip(self.new_tip)

    def _assert_restored_state(self):
        self._assert_indexed_state(
            [self.s, self.x, self.w], {self.genesis_hash, self.new_tx["hash"]}
        )
        # This is node state, separate from the indexer's restored-live oracle.
        assert (
            self.rich_node.client.get_live_cell(
                self.x["out_point"]["index"], self.x["out_point"]["tx_hash"]
            )["status"]
            == "live"
        )
        assert self.rich_node.client.get_live_cell(
            self.g["out_point"]["index"], self.g["out_point"]["tx_hash"]
        )["status"] in ("dead", "unknown")
        assert (
            self.rich_node.client.get_transaction(self.blocker_tx["hash"])["tx_status"][
                "status"
            ]
            == "committed"
        )

    def _spend_restored_x(self):
        self.spend_tx = self._register_tx([self.x], self.lock, "0x04")
        self.z = self._output_cell(self.spend_tx)
        self.expected_transaction_cells[self.spend_tx["hash"]] = [
            ["input", "0x0"],
            ["output", "0x0"],
        ]
        self._mine_block(self.full_node, proposals=[self.spend_tx["hash"][:22]])
        self._mine_block(self.full_node)
        self._mine_block(self.full_node, [self.spend_tx])
        # Ordinary later templates must not re-include detached transactions.
        template = self.full_node.client.get_block_template()
        forbidden = {self.old_tx["hash"], *self.filtered_in_old_fork}
        assert not forbidden.intersection(tx["hash"] for tx in template["transactions"])
        self.new_tip = self._mine_block(self.full_node)
        self._wait_for_node_tip(self.rich_node, self.new_tip)
        self._wait_for_indexer_tip(self.new_tip)

    def _query_all(self, method):
        key = {
            "script": self.lock,
            "script_type": "lock",
            "script_search_mode": "exact",
        }
        if method == "get_transactions":
            # Group all matching input/output pairs under each transaction.
            # Grouping also avoids splitting one transaction across
            # the rich-indexer's ungrouped cursor (whose offset can repeat).
            key["group_by_transaction"] = True
        cursor = None
        rows = []
        for _ in range(100):
            page = getattr(self.rich_node.client, method)(key, "asc", "0x2", cursor)
            if not page["objects"]:
                return rows
            if method == "get_transactions":
                # SQL aggregation does not promise the order of IO pairs.
                # Preserve every pair for exact assertions and restart equality.
                for transaction in page["objects"]:
                    transaction["cells"].sort()
            rows.extend(page["objects"])
            assert page["last_cursor"] != cursor, f"{method}: cursor did not advance"
            cursor = page["last_cursor"]
        raise AssertionError(f"{method}: pagination exceeded fixture limit")

    def _snapshot(self):
        key = {
            "script": self.lock,
            "script_type": "lock",
            "script_search_mode": "exact",
        }
        return {
            "tip": self.rich_node.client.get_indexer_tip(),
            "cells": self._query_all("get_cells"),
            "transactions": self._query_all("get_transactions"),
            "capacity": self.rich_node.client.get_cells_capacity(key),
        }

    def _assert_indexed_state(self, expected_cells, expected_hashes):
        snapshot = self._snapshot()
        point = lambda cell: (cell["out_point"]["tx_hash"], cell["out_point"]["index"])
        actual = {point(cell): cell for cell in snapshot["cells"]}
        expected = {point(cell): cell for cell in expected_cells}
        assert len(actual) == len(snapshot["cells"]), "Duplicate indexed live cells"
        assert (
            actual.keys() == expected.keys()
        ), f"Indexed live cells differ: {actual.keys()} != {expected.keys()}"
        for out_point, cell in expected.items():
            assert actual[out_point]["output"] == cell["output"]
            assert actual[out_point]["output_data"] == cell["output_data"]
        transactions = {row["tx_hash"]: row for row in snapshot["transactions"]}
        assert len(transactions) == len(
            snapshot["transactions"]
        ), "Duplicate indexed transactions"
        assert (
            transactions.keys() == expected_hashes
        ), f"Indexed transactions differ: {transactions.keys()} != {expected_hashes}"
        for tx_hash in expected_hashes:
            expected_pairs = sorted(self.expected_transaction_cells[tx_hash])
            assert transactions[tx_hash]["cells"] == expected_pairs, (
                f"Transaction {tx_hash} input/output pairs differ: "
                f"{transactions[tx_hash]['cells']} != {expected_pairs}"
            )
        capacity = snapshot["capacity"]
        assert capacity is not None, "Sentinel S must keep tracked capacity nonempty"
        assert int(capacity["capacity"], 16) == sum(
            int(cell["output"]["capacity"], 16) for cell in expected_cells
        )
        assert {
            key: capacity[key] for key in ("block_hash", "block_number")
        } == snapshot["tip"]
