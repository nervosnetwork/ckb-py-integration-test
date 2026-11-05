"""PR #5236: Python-only integration tests against download/current/ckb.

Uses CkbTest/CkbNode, ordinary RPC and normal node-to-node transaction relay.
No custom P2P messages, Rust code, internal cache injection or VM probes.
Historical base comparison and malformed remote-message tests are not run.
"""

import copy
import time
from pathlib import Path

import toml
import requests


from framework.basic import CkbTest
from test_cases.tx_pool_refactor.dao_precheck.support import (
    ROOT,
    CKB,
    FEE,
    ZERO_DAO,
    assert_dao_error,
    assert_script_error,
    blob,
    ckb_hash,
    outpoint_bytes,
    script_bytes,
    transaction,
    transaction_hash,
    uint,
    wait_for,
)


class TestDaoPreScriptVerification(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "tx_pool/dao_precheck/node1", 8120, 8225
        )
        cls.node.prepare()

        contract = ROOT / "source/contract/always_success"
        cls.lock = {
            "code_hash": ckb_hash(contract.read_bytes()),
            "hash_type": "data",
            "args": "0x" + "11" * 20,
        }
        cls.secp_lock = {
            "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
            "hash_type": "type",
            "args": "0x" + "11" * 20,
        }
        spec = toml.load(cls.node.ckb_specs_config_path)
        spec["genesis"]["timestamp"] = int(time.time() * 1000) - 60_000
        spec["params"].update(
            epoch_duration_target=8,
            genesis_epoch_length=10,
            starting_block_limiting_dao_withdrawing_lock=0,
        )
        spec["genesis"]["system_cells"].append(
            {"file": {"file": str(contract)}, "create_type_id": False}
        )
        spec["genesis"]["issued_cells"] += [
            {"capacity": 100_000 * CKB, "lock": cls.lock} for _ in range(32)
        ]
        cls.spec = spec
        Path(cls.node.ckb_specs_config_path).write_text(toml.dumps(spec))
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)
        cls.client = cls.node.getClient()
        cls.dao = {
            "code_hash": cls.client.get_consensus()["dao_type_hash"],
            "hash_type": "type",
            "args": "0x",
        }
        cls.deps, funds = cls._genesis_cells()
        cls.funding_cells = iter(funds)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def setup_method(self, method):
        self.did_pass = False
        self.peer = None
        # Three independent inputs per test; no shared Cell is spent twice.
        self.funds = [next(self.funding_cells) for _ in range(3)]

    def teardown_method(self, method):
        if self.peer is not None:
            self.peer.stop()
            self.peer.clean()

    def _start_peer(self):
        self.peer = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/dao_precheck/node2",
            8121,
            8226,
        )
        self.peer.prepare()
        Path(self.peer.ckb_specs_config_path).write_text(toml.dumps(self.spec))
        self.peer.start()
        return self.peer

    @classmethod
    def _genesis_cells(cls):
        genesis = cls.client.get_block_by_number("0x0")
        deps, funds, secp_point = [], [], None
        for tx in genesis["transactions"]:
            for index, output in enumerate(tx["outputs"]):
                point = {"tx_hash": tx["hash"], "index": hex(index)}
                type_hash = (
                    ckb_hash(script_bytes(output["type"]))
                    if output.get("type")
                    else None
                )
                if (
                    ckb_hash(blob(tx["outputs_data"][index])) == cls.lock["code_hash"]
                    or type_hash == cls.dao["code_hash"]
                ):
                    deps.append({"out_point": point, "dep_type": "code"})
                if type_hash == cls.secp_lock["code_hash"]:
                    secp_point = point
                if output["lock"] == cls.lock and not output.get("type"):
                    funds.append((point, int(output["capacity"], 16)))
        assert secp_point is not None
        for tx in genesis["transactions"]:
            for index, data in enumerate(tx["outputs_data"]):
                if outpoint_bytes(secp_point).hex() in data[2:]:
                    deps.append(
                        {
                            "out_point": {"tx_hash": tx["hash"], "index": hex(index)},
                            "dep_type": "dep_group",
                        }
                    )
        assert len(deps) == 3 and len(funds) == 32
        return deps, funds

    def _mine(self, node, count=1):
        client = node.getClient()
        for _ in range(count):
            client.generate_block()
            wait_for(
                lambda: client.tx_pool_info()["tip_hash"]
                == client.get_tip_header()["hash"],
                "pool tip",
            )

    def _commit(self, tx_hash):
        for _ in range(20):
            result = self.client.get_transaction(tx_hash)
            if result["tx_status"]["status"] == "committed":
                return result["tx_status"]["block_hash"]
            self._mine(self.node)
        self.fail(f"transaction not committed: {tx_hash}")

    def _sync(self, peer):
        client = peer.getClient()
        for height in range(
            client.get_tip_block_number() + 1, self.client.get_tip_block_number() + 1
        ):
            block = self.client.get_block_by_number(hex(height))
            block["header"].pop("hash", None)
            for tx in block["transactions"]:
                tx.pop("hash", None)
            for uncle in block["uncles"]:
                uncle["header"].pop("hash", None)
            client.submit_block("0x0", block)
        wait_for(
            lambda: client.tx_pool_info()["tip_hash"]
            == self.client.get_tip_header()["hash"],
            "peer chain sync",
        )

    def _wait_pool(self, node, tx_hash):
        def entry():
            pool = node.getClient().get_raw_tx_pool(True)
            return pool["pending"].get(tx_hash) or pool["proposed"].get(tx_hash)

        return wait_for(entry, f"accepted {tx_hash}")

    def _assert_absent(self, tx, node=None):
        client = (node or self.node).getClient()
        tx_hash = transaction_hash(tx)
        pool = client.get_raw_tx_pool(True)
        assert tx_hash not in pool["pending"] and tx_hash not in pool["proposed"]
        assert all(
            item["hash"] != tx_hash
            for item in client.get_block_template()["transactions"]
        )

    def _funding_transaction(self, kinds=(True,), failing=False, fund=0):
        point, capacity = self.funds[fund]
        lock = self.secp_lock if failing else self.lock
        outputs = [
            {
                "capacity": hex(1000 * CKB),
                "lock": copy.deepcopy(lock),
                "type": self.dao if dao else None,
            }
            for dao in kinds
        ]
        outputs.append(
            {
                "capacity": hex(capacity - len(kinds) * 1000 * CKB - FEE),
                "lock": self.lock,
                "type": None,
            }
        )
        return transaction(
            [point],
            outputs,
            [ZERO_DAO if dao else "0x" for dao in kinds] + ["0x"],
            self.deps,
        )

    def _create_cells(self, kinds=(True,), failing=False, fund=0):
        tx = self._funding_transaction(kinds, failing, fund)
        tx_hash = self.client.send_transaction(tx)
        assert tx_hash == transaction_hash(tx)
        block_hash = self._commit(tx_hash)
        return {
            "tx": tx,
            "hash": tx_hash,
            "header": self.client.call("get_header", [block_hash]),
            "kinds": kinds,
        }

    def _prepare_withdraw(self, cells):
        # Preserve DAO capacity; the ordinary change cell pays the fee.
        count = len(cells["tx"]["outputs"])
        inputs = [{"tx_hash": cells["hash"], "index": hex(i)} for i in range(count)]
        outputs = copy.deepcopy(cells["tx"]["outputs"][:count])
        outputs[-1]["capacity"] = hex(int(outputs[-1]["capacity"], 16) - FEE)
        data = [
            "0x" + uint(cells["header"]["number"], 8).hex() if dao else "0x"
            for dao in (*cells["kinds"], False)
        ]
        return transaction(inputs, outputs, data, self.deps, [cells["header"]["hash"]])

    def _bad_and_control(self, kind, failing=True, fund=0):
        cells = self._create_cells((kind == "lock",), failing, fund)
        control = self._prepare_withdraw(cells)
        bad = copy.deepcopy(control)
        if kind == "lock":
            bad["outputs"][0]["lock"]["args"] += "00"
        else:
            bad["outputs"][0]["type"] = self.dao
            bad["outputs_data"][0] = "0x" + uint(cells["header"]["number"], 8).hex()
            control = copy.deepcopy(bad)
            control["outputs_data"][0] = ZERO_DAO
        return bad, control

    def _final_withdraw(self, cells, prepare, prepare_hash, prepare_block):
        deposit_header = cells["header"]
        epoch = int(deposit_header["epoch"], 16)
        deposit_epoch = epoch & 0xFFFFFF
        prepare_epoch = (
            int(self.client.call("get_header", [prepare_block])["epoch"], 16) & 0xFFFFFF
        )
        target = deposit_epoch + ((prepare_epoch - deposit_epoch) // 180 + 1) * 180
        current = int(self.client.get_tip_header()["epoch"], 16) & 0xFFFFFF
        if current <= target:
            self.client.generate_epochs(hex(target + 1 - current))
        maximum = int(
            self.client.calculate_dao_maximum_withdraw(
                {"tx_hash": cells["hash"], "index": "0x0"}, prepare_block
            ),
            16,
        )
        since = hex((0x20 << 56) | (epoch & ~0xFFFFFF) | target)
        output = {
            "capacity": hex(maximum - FEE),
            "lock": prepare["outputs"][0]["lock"],
            "type": None,
        }
        return transaction(
            [{"tx_hash": prepare_hash, "index": "0x0"}],
            [output],
            ["0x"],
            self.deps,
            [deposit_header["hash"], prepare_block],
            since,
        )

    # TEST-MAP: DAOQ-01
    def test_lock_size_error_precedes_script_error(self):
        bad, control = self._bad_and_control("lock")
        for method in ("send_transaction", "test_tx_pool_accept"):
            with self.subTest(method=method):
                with self.assertRaises(Exception) as error:
                    self.client.call(method, [bad, "passthrough"])
                assert_dao_error(error.exception, "lock")
                self._assert_absent(bad)
                with self.assertRaises(Exception) as error:
                    self.client.call(method, [control, "passthrough"])
                assert_script_error(error.exception)
        self.did_pass = True

    # TEST-MAP: DAOQ-02
    def test_output_data_error_precedes_script_error(self):
        bad, control = self._bad_and_control("data")
        for method in ("send_transaction", "test_tx_pool_accept"):
            with self.subTest(method=method):
                with self.assertRaises(Exception) as error:
                    self.client.call(method, [bad, "passthrough"])
                assert_dao_error(error.exception, "data")
                self._assert_absent(bad)
                with self.assertRaises(Exception) as error:
                    self.client.call(method, [control, "passthrough"])
                assert_script_error(error.exception)
        self.did_pass = True

    def _legal_lifecycle(self, peer=None):
        def admit(tx):
            expected = self.client.test_tx_pool_accept(tx, "passthrough")
            assert int(expected["cycles"], 16) > 0 and int(expected["fee"], 16) == FEE
            if peer:
                wait_for(
                    lambda: peer.getClient().tx_pool_info()["tip_hash"]
                    == self.client.get_tip_header()["hash"],
                    "connected peer chain sync",
                )
                assert (
                    peer.getClient().test_tx_pool_accept(tx, "passthrough") == expected
                )
            tx_hash = self.client.send_transaction(tx)
            assert tx_hash == transaction_hash(tx)
            if peer:
                entry = self._wait_pool(
                    peer, tx_hash
                )  # before mining: actual normal relay
                assert (
                    entry["cycles"] == expected["cycles"]
                    and entry["fee"] == expected["fee"]
                )
            block = self._commit(tx_hash)
            if peer:
                self.Node.wait_get_transaction(peer, tx_hash, "committed")
            return tx_hash, block

        deposit = self._funding_transaction()
        tx_hash, block = admit(deposit)
        cells = {
            "tx": deposit,
            "hash": tx_hash,
            "header": self.client.call("get_header", [block]),
            "kinds": (True,),
        }
        prepare = self._prepare_withdraw(cells)
        prepare_hash, prepare_block = admit(prepare)
        admit(self._final_withdraw(cells, prepare, prepare_hash, prepare_block))
        admit(self._funding_transaction((False,), fund=1))

    # TEST-MAP: DAOQ-04
    def test_legal_dao_lifecycle_local(self):
        self._legal_lifecycle()
        self.did_pass = True

    # TEST-MAP: DAOQ-04
    def test_legal_dao_lifecycle_normal_relay(self):
        peer = self._start_peer()
        self._sync(peer)
        peer.connected(self.node)
        self._legal_lifecycle(peer)
        self.did_pass = True

    # TEST-MAP: DAOQ-05
    def test_same_length_different_lock(self):
        cells = self._create_cells()
        prepare = self._prepare_withdraw(cells)
        prepare["outputs"][0]["lock"]["args"] = "0x" + "22" * 20
        prepare_hash = self.client.send_transaction(prepare)
        block = self._commit(prepare_hash)
        withdraw = self._final_withdraw(cells, prepare, prepare_hash, block)
        self._commit(self.client.send_transaction(withdraw))
        self.did_pass = True

    # TEST-MAP: DAOQ-06
    # TEST-MAP: VT2-26
    def test_lock_size_boundary_and_index(self):
        """
        1. 交错准备 DAO 和普通 cell，DAO cell 位于索引 0、2、4。
        2. 将各 DAO 输出的 lock args 长度改为 19 或 21 字节，核对错误字段和索引。
        3. 用正确的 lock args 构造对照交易，检查预检通过并成功上链。
        """
        # 1. 交错创建 DAO 与普通 cell，覆盖 0、2、4 三个 DAO 索引。
        cells = self._create_cells((True, False, True, False, True))

        # 2. 分别破坏 DAO lock 长度，检查两个 RPC 拒绝且交易没有进入池。
        for index in (0, 2, 4):
            for delta in (-1, 1):
                with self.subTest(index=index, delta=delta):
                    tx = self._prepare_withdraw(cells)
                    tx["outputs"][index]["lock"]["args"] = "0x" + "11" * (20 + delta)
                    for method in ("test_tx_pool_accept", "send_transaction"):
                        with self.assertRaises(Exception) as error:
                            self.client.call(method, [tx, "passthrough"])
                        assert_dao_error(error.exception, "lock", index)
                        self._assert_absent(tx)

        # 3. 恢复正确的 lock 长度，检查合法准备提取交易可以确认。
        control = self._prepare_withdraw(cells)
        self.client.test_tx_pool_accept(control, "passthrough")
        self._commit(self.client.send_transaction(control))
        self.did_pass = True

    # TEST-MAP: DAOQ-10
    def test_valid_dao_still_checks_witness(self):
        cells = self._create_cells(failing=True)
        tx = self._prepare_withdraw(cells)
        for method in ("test_tx_pool_accept", "send_transaction"):
            with self.assertRaises(Exception) as error:
                self.client.call(method, [tx, "passthrough"])
            assert_script_error(error.exception)
        self._assert_absent(tx)
        self.did_pass = True

    # TEST-MAP: DAOQ-11
    def test_dry_run_has_no_admission_or_rejection_record(self):
        cases = []
        for fund, kind in enumerate(("lock", "data")):
            bad, _ = self._bad_and_control(kind, failing=False, fund=fund)
            cases.append((kind, bad))
        cases.append(("valid", self._funding_transaction((False,), fund=2)))
        peer = self._start_peer()
        self._sync(peer)
        peer.connected(self.node)
        for kind, tx in cases:
            with self.subTest(kind=kind):
                if kind == "valid":
                    result = self.client.test_tx_pool_accept(tx, "passthrough")
                    assert (
                        int(result["cycles"], 16) > 0 and int(result["fee"], 16) == FEE
                    )
                else:
                    with self.assertRaises(Exception) as error:
                        self.client.test_tx_pool_accept(tx, "passthrough")
                    assert_dao_error(error.exception, kind)
                for node in (self.node, peer):
                    self._assert_absent(tx, node)
                    response = node.getClient().get_transaction(transaction_hash(tx))
                    status = (response or {}).get("tx_status", {})
                    assert status.get("status") != "rejected" and not status.get(
                        "reason"
                    ), status
        # Prove the observer connection actually relays accepted transactions.
        self._wait_pool(peer, self.client.send_transaction(cases[-1][1]))
        for kind, tx in cases[:2]:
            with self.assertRaises(Exception) as error:
                self.client.send_transaction(tx)
            assert_dao_error(error.exception, kind)
            reason = wait_for(
                lambda: (self.client.get_transaction(transaction_hash(tx)) or {})
                .get("tx_status", {})
                .get("reason"),
                "rejection record",
            )
            assert_dao_error(reason, kind)
        self.did_pass = True

    # TEST-MAP: DAOQ-14
    def test_reorg_readmits_dao_prepare(self):
        cells = self._create_cells()
        fork = self._start_peer()
        self._sync(fork)  # Same ancestor, but nodes remain disconnected.
        common_height = self.client.get_tip_block_number()
        prepare = self._prepare_withdraw(cells)
        expected = self.client.test_tx_pool_accept(prepare, "passthrough")
        tx_hash = self.client.send_transaction(prepare)
        old_block = self._commit(tx_hash)
        old_header = self.client.call("get_header", [old_block])
        self._mine(fork, self.client.get_tip_block_number() - common_height + 4)
        fork_tip = fork.getClient().get_tip_header()["hash"]
        self.node.connected(fork)
        wait_for(
            lambda: self.client.get_tip_header()["hash"] == fork_tip,
            "higher-work fork selected",
        )
        entry = self._wait_pool(self.node, tx_hash)
        assert entry["cycles"] == expected["cycles"] and entry["fee"] == expected["fee"]
        assert self.client.get_block_hash(old_header["number"]) != old_block
        assert self._commit(tx_hash) != old_block
        self.did_pass = True

    # TEST-MAP: VT2-54
    def test_dao_withdraw_requires_withdrawing_header(self):
        """
        1. 完成 DAO 准备提取并构造包含完整 header_deps 的最终提取交易。
        2. 删除准备提取区块头，检查两个 RPC 返回 -1108 和 InvalidOutPoint。
        3. 使用保留完整区块头依赖的对照交易，检查预检通过并成功上链。
        """
        # 1. 确认准备提取交易，取得所在区块头并构造有效的最终提取交易。
        cells = self._create_cells()
        prepare = self._prepare_withdraw(cells)
        prepare_hash = self.client.send_transaction(prepare)
        prepare_block = self._commit(prepare_hash)
        valid = self._final_withdraw(cells, prepare, prepare_hash, prepare_block)

        # 2. 仅删除准备提取所在区块的 header_dep，核对错误码、错误类型和未入池状态。
        invalid = copy.deepcopy(valid)
        assert invalid["header_deps"] == [cells["header"]["hash"], prepare_block]
        invalid["header_deps"].remove(prepare_block)
        for method in ("test_tx_pool_accept", "send_transaction"):
            # 直接检查公开 RPC 响应，保留通用 RPC 异常封装会丢失的数值错误码。
            response = requests.post(
                self.client.url,
                json={
                    "id": 1,
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": [invalid, "passthrough"],
                },
                timeout=30,
            )
            response.raise_for_status()
            error = response.json()["error"]
            assert error["code"] == -1108, error
            assert (
                "Malformed" in error["message"]
                and "InvalidOutPoint" in error["message"]
            ), error
            self._assert_absent(invalid)

        # 3. 完整 header_deps 的对照交易应通过预检，并成功提交和确认。
        self.client.test_tx_pool_accept(valid, "passthrough")
        self._commit(self.client.send_transaction(valid))
        self.did_pass = True
