import hashlib
import http.client
import json
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from framework.basic import CkbTest
from framework.util import get_project_root


class TestStaleParentAncestorEviction(CkbTest):
    """End-to-end regression coverage for CKB PR #5293 and #5294."""

    ALWAYS_SUCCESS_CODE_HASH = (
        "0x28e83a1277d48add8e72fadaa9248559e1b632bab2bd60b27955ebc4c03800a5"
    )
    SPEC_PATH = "tmp/pr5293_stale_parent_ancestor_eviction_dev.toml"
    MAX_ANCESTORS_COUNT = 1_000
    CHAIN_LENGTH = MAX_ANCESTORS_COUNT
    TX_FEE = 1_000
    CELL_A_CAPACITY = 10_000_000_000

    @classmethod
    def setup_class(cls):
        node_path = cls._create_always_success_node_path()
        cls.node = cls.CkbNode.init_dev_by_port(
            node_path,
            "tx_pool/pr5293_stale_parent_ancestor_eviction",
            8470,
            8471,
        )
        try:
            cls.node.clean()
            cls.node.prepare(
                other_ckb_config={
                    "ckb_tx_pool_max_ancestors_count": cls.MAX_ANCESTORS_COUNT,
                }
            )
            cls._copy_always_success_cell()
            cls.node.start()
            cls.Miner.make_tip_height_number(cls.node, 30)
            cls.always_success_dep = cls._find_always_success_dep()
            cls.genesis_source = cls._genesis_always_success_source()
        except Exception:
            cls.node.stop()
            cls.node.clean()
            raise

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def setup_method(self, method):
        self.did_pass = None
        self.node.getClient().clear_tx_pool()

    def teardown_method(self, method):
        if self.did_pass:
            return
        report_dir = (
            Path(get_project_root())
            / "report"
            / f"{self.__class__.__name__}_{method.__name__}"
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        node_log = Path(self.node.ckb_dir) / "node.log"
        if node_log.is_file():
            shutil.copy2(node_log, report_dir / "node.log")

    def test_evicting_cell_dep_parent_removes_stale_descendants(self):
        """
        TP-INT-TXPOOL-5293-001 [P0]: build a default-limit ancestor pressure
        chain (1,000 entries), then submit a transaction that spends both the
        chain tail and a cell referenced as tx1's cell dep. Inserting the new
        transaction must evict tx1 and all 999 descendants, remove every stale
        parent id, and leave the node alive with only the new transaction.
        """
        split_tx, cell_b_capacity = self._create_committed_cells_a_and_b()

        rpc_url = urlparse(self.node.getClient().url)
        connection = http.client.HTTPConnection(
            rpc_url.hostname, rpc_url.port, timeout=30
        )
        try:
            tx1_hash, tail_hash, tail_capacity = self._submit_ancestor_chain(
                connection,
                split_tx,
                cell_b_capacity,
            )

            pool_before = self._rpc_call(connection, "get_raw_tx_pool", [True])
            assert len(pool_before["pending"]) == self.CHAIN_LENGTH
            assert pool_before["proposed"] == {}
            assert (
                int(pool_before["pending"][tail_hash]["ancestors_count"], 16)
                == self.MAX_ANCESTORS_COUNT
            )

            replacement = self._build_transaction(
                inputs=[
                    {"tx_hash": split_tx, "index": "0x0"},
                    {"tx_hash": tail_hash, "index": "0x0"},
                ],
                output_capacity=(
                    self.CELL_A_CAPACITY + tail_capacity - 10 * self.TX_FEE
                ),
                cell_deps=[self.always_success_dep],
            )
            replacement_hash = self._rpc_call(
                connection,
                "send_transaction",
                [replacement, "passthrough"],
            )

            pool_after = self._rpc_call(connection, "get_raw_tx_pool", [True])
        finally:
            connection.close()

        assert tx1_hash not in pool_after["pending"]
        assert tail_hash not in pool_after["pending"]
        assert set(pool_after["pending"]) == {replacement_hash}
        assert pool_after["proposed"] == {}
        assert int(pool_after["pending"][replacement_hash]["ancestors_count"], 16) == 1

        pool_info = self.node.getClient().tx_pool_info()
        assert pool_info["pending"] == "0x1"
        assert pool_info["proposed"] == "0x0"
        assert self.node.getClient().get_tip_header()["hash"].startswith("0x")
        self.did_pass = True

    def _create_committed_cells_a_and_b(self):
        source_capacity = self.genesis_source["capacity"]
        cell_b_capacity = source_capacity - self.CELL_A_CAPACITY - self.TX_FEE
        assert cell_b_capacity > self.CELL_A_CAPACITY

        split_transaction = {
            "version": "0x0",
            "cell_deps": [self.always_success_dep],
            "header_deps": [],
            "inputs": [
                {
                    "previous_output": self.genesis_source["out_point"],
                    "since": "0x0",
                }
            ],
            "outputs": [
                {
                    "capacity": hex(self.CELL_A_CAPACITY),
                    "lock": self._always_success_lock(),
                },
                {
                    "capacity": hex(cell_b_capacity),
                    "lock": self._always_success_lock(),
                },
            ],
            "outputs_data": ["0x", "0x"],
            "witnesses": [],
        }
        split_tx = self.node.getClient().send_transaction(split_transaction)
        self.Miner.miner_until_tx_committed(self.node, split_tx)
        return split_tx, cell_b_capacity

    def _submit_ancestor_chain(self, connection, split_tx, capacity):
        previous_out_point = {"tx_hash": split_tx, "index": "0x1"}
        tx1_hash = None
        tail_hash = None
        deadline = time.monotonic() + 180

        for index in range(self.CHAIN_LENGTH):
            assert (
                time.monotonic() < deadline
            ), f"submitted only {index}/{self.CHAIN_LENGTH} ancestor transactions"
            capacity -= self.TX_FEE
            cell_deps = [self.always_success_dep]
            if index == 0:
                cell_deps.append(
                    {
                        "out_point": {"tx_hash": split_tx, "index": "0x0"},
                        "dep_type": "code",
                    }
                )

            transaction = self._build_transaction(
                inputs=[previous_out_point],
                output_capacity=capacity,
                cell_deps=cell_deps,
            )
            tail_hash = self._rpc_call(
                connection,
                "send_transaction",
                [transaction, "passthrough"],
                request_id=index,
            )
            if tx1_hash is None:
                tx1_hash = tail_hash
            previous_out_point = {"tx_hash": tail_hash, "index": "0x0"}

        return tx1_hash, tail_hash, capacity

    @classmethod
    def _build_transaction(cls, inputs, output_capacity, cell_deps):
        return {
            "version": "0x0",
            "cell_deps": cell_deps,
            "header_deps": [],
            "inputs": [
                {"previous_output": out_point, "since": "0x0"} for out_point in inputs
            ],
            "outputs": [
                {
                    "capacity": hex(output_capacity),
                    "lock": cls._always_success_lock(),
                }
            ],
            "outputs_data": ["0x"],
            "witnesses": [],
        }

    @staticmethod
    def _rpc_call(connection, method, params, request_id=1):
        request = {
            "id": request_id,
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        connection.request(
            "POST",
            "/",
            body=json.dumps(request),
            headers={"Content-Type": "application/json"},
        )
        http_response = connection.getresponse()
        response = json.loads(http_response.read().decode("utf-8"))
        assert http_response.status == 200, response
        assert "result" in response, response
        return response["result"]

    @classmethod
    def _create_always_success_node_path(cls):
        current = cls.CkbNodeConfigPath.CURRENT_TEST
        root = Path(get_project_root())
        spec_text = (root / current.ckb_spec_path).read_text(encoding="utf-8")

        system_cells_lock = "\n[genesis.system_cells_lock]"
        assert system_cells_lock in spec_text
        spec_text = spec_text.replace(
            system_cells_lock,
            "\n[[genesis.system_cells]]\n"
            'file = { file = "specs/cells/always_success" }\n'
            "create_type_id = false\n" + system_cells_lock,
            1,
        )

        bootstrap_lock = (
            "[genesis.bootstrap_lock]\n"
            'code_hash = "0x0000000000000000000000000000000000000000000000000000000000000000"\n'
            'args = "0x"\n'
            'hash_type = "type"'
        )
        assert bootstrap_lock in spec_text
        spec_text = spec_text.replace(
            bootstrap_lock,
            "[genesis.bootstrap_lock]\n"
            f'code_hash = "{cls.ALWAYS_SUCCESS_CODE_HASH}"\n'
            'args = "0x"\n'
            'hash_type = "data"',
            1,
        )

        issued_cell_lock = (
            'lock.code_hash = "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8"\n'
            'lock.args = "0xc8328aabcd9b9e8e64fbc566c4385c3bdeb219d7"\n'
            'lock.hash_type = "type"'
        )
        assert issued_cell_lock in spec_text
        spec_text = spec_text.replace(
            issued_cell_lock,
            f'lock.code_hash = "{cls.ALWAYS_SUCCESS_CODE_HASH}"\n'
            'lock.args = "0x"\n'
            'lock.hash_type = "data"',
            1,
        )

        generated_spec_path = root / cls.SPEC_PATH
        generated_spec_path.parent.mkdir(parents=True, exist_ok=True)
        generated_spec_path.write_text(spec_text, encoding="utf-8")
        return cls.CkbNodeConfigPath(
            current.ckb_config_path,
            current.ckb_miner_config_path,
            cls.SPEC_PATH,
            current.ckb_bin_path,
        )

    @classmethod
    def _copy_always_success_cell(cls):
        cells_dir = Path(cls.node.ckb_dir) / "specs" / "cells"
        cells_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(
            Path(get_project_root()) / "source" / "contract" / "always_success",
            cells_dir / "always_success",
        )

    @classmethod
    def _find_always_success_dep(cls):
        genesis = cls.node.getClient().get_block_by_number("0x0")
        for transaction in genesis["transactions"]:
            for index, output_data in enumerate(transaction["outputs_data"]):
                digest = hashlib.blake2b(
                    bytes.fromhex(output_data[2:]),
                    digest_size=32,
                    person=b"ckb-default-hash",
                ).hexdigest()
                if "0x" + digest == cls.ALWAYS_SUCCESS_CODE_HASH:
                    return {
                        "out_point": {
                            "tx_hash": transaction["hash"],
                            "index": hex(index),
                        },
                        "dep_type": "code",
                    }
        raise AssertionError("always-success system cell not found in genesis")

    @classmethod
    def _genesis_always_success_source(cls):
        genesis = cls.node.getClient().get_block_by_number("0x0")
        candidates = []
        for transaction in genesis["transactions"]:
            for index, output in enumerate(transaction["outputs"]):
                if output["lock"] == cls._always_success_lock():
                    candidates.append(
                        {
                            "out_point": {
                                "tx_hash": transaction["hash"],
                                "index": hex(index),
                            },
                            "capacity": int(output["capacity"], 16),
                        }
                    )
        assert candidates, "always-success issued cell not found in genesis"
        return max(candidates, key=lambda candidate: candidate["capacity"])

    @classmethod
    def _always_success_lock(cls):
        return {
            "code_hash": cls.ALWAYS_SUCCESS_CODE_HASH,
            "hash_type": "data",
            "args": "0x",
        }
