import hashlib
import json
import shutil
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import framework.helper.ckb_cli as ckb_cli_helper
import framework.helper.contract as contract_helper
from framework.basic import CkbTest
from framework.util import get_project_root


class TestVerifyQueueRegressions(CkbTest):
    """End-to-end regression coverage for CKB PR #5238 and #5249."""

    ALWAYS_SUCCESS_CODE_HASH = (
        "0x28e83a1277d48add8e72fadaa9248559e1b632bab2bd60b27955ebc4c03800a5"
    )
    SPEC_PATH = "tmp/pr5238_5249_verify_queue_dev.toml"
    EXPENSIVE_CONTRACT_PATH = (
        Path(get_project_root())
        / "source"
        / "contract"
        / "test_cases"
        / "spawn_stop_16_spawn_create_17_spawn"
    )

    NORMAL_TX_COUNT = 1_000
    EXPENSIVE_TX_COUNT = 24
    NORMAL_WITNESS_BYTES = 10_000
    RECEIVER_MAX_VERIFY_CYCLES = 5_000_000
    CELL_CAPACITY = 100_000_000_000
    TX_FEE = 10_000_000
    SPLIT_FEE = 10_000_000

    RENOTIFY_LOG = (
        "didn't got tx after pop_front, but tasks is not empty, "
        "notify other Workers now"
    )

    @classmethod
    def setup_class(cls):
        cls._original_ckb_cli_path = ckb_cli_helper.cli_path
        cls._original_contract_cli_path = contract_helper.cli_path
        current_cli = (
            Path(get_project_root())
            / cls.CkbNodeConfigPath.CURRENT_TEST.ckb_bin_path
            / "ckb-cli"
        )
        assert current_cli.is_file(), f"CKB CLI not found: {current_cli}"
        ckb_cli_helper.cli_path = str(current_cli)
        contract_helper.cli_path = str(current_cli)

        node_path = cls._create_always_success_node_path()
        cls.source = cls.CkbNode.init_dev_by_port(
            node_path,
            "tx_pool/pr5238_5249_verify_queue/source",
            8500,
            8501,
        )
        cls.receiver = cls.CkbNode.init_dev_by_port(
            node_path,
            "tx_pool/pr5238_5249_verify_queue/receiver",
            8502,
            8503,
        )
        try:
            cls.source.clean()
            cls.receiver.clean()
            cls.source.prepare(
                other_ckb_config={"ckb_tx_pool_max_tx_verify_workers": 8}
            )
            cls.receiver.prepare(
                other_ckb_config={
                    "ckb_logger_filter": "info,ckb_tx_pool=debug",
                    "ckb_tx_pool_max_tx_verify_workers": 2,
                    "ckb_tx_pool_max_tx_verify_cycles": (
                        cls.RECEIVER_MAX_VERIFY_CYCLES
                    ),
                }
            )
            cls._copy_always_success_cell(cls.source)
            cls._copy_always_success_cell(cls.receiver)

            cls.source.start()
            cls.Miner.make_tip_height_number(cls.source, 30)
            cls.receiver.start()
            cls.receiver.connected(cls.source)
            cls.Node.wait_node_height(cls.receiver, 30, 60)

            cls.always_success_dep = cls._find_always_success_dep(cls.source)
            genesis_source = cls._genesis_always_success_source(cls.source)
            cls.contract_out_point = cls._deploy_expensive_contract()
            cls.split_tx_hash = cls._create_workload_cells(genesis_source)
            cls.Node.wait_node_height(
                cls.receiver,
                cls.source.getClient().get_tip_block_number(),
                60,
            )
        except Exception:
            cls.source.stop()
            cls.receiver.stop()
            cls.source.clean()
            cls.receiver.clean()
            cls._restore_cli_path()
            raise

    @classmethod
    def teardown_class(cls):
        cls.source.stop()
        cls.receiver.stop()
        cls.source.clean()
        cls.receiver.clean()
        cls._restore_cli_path()

    @classmethod
    def _restore_cli_path(cls):
        ckb_cli_helper.cli_path = cls._original_ckb_cli_path
        contract_helper.cli_path = cls._original_contract_cli_path

    def setup_method(self, method):
        self.did_pass = None
        self.source.getClient().clear_tx_pool()
        self.receiver.getClient().clear_tx_pool()
        self.receiver.getClient().clear_tx_verify_queue()
        self._wait_verify_queue_empty(timeout=10)

    def teardown_method(self, method):
        if self.did_pass:
            return
        report_dir = (
            Path(get_project_root())
            / "report"
            / f"{self.__class__.__name__}_{method.__name__}"
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        for name, node in (("source", self.source), ("receiver", self.receiver)):
            node_log = Path(node.ckb_dir) / "node.log"
            if node_log.is_file():
                shutil.copy2(node_log, report_dir / f"{name}.log")

    def test_normal_only_queue_drains_under_remote_transaction_pressure(self):
        """
        PR #5238: create a real remote-only verify queue with no proposal
        transactions. The queue must build a measurable backlog and drain in
        bounded time while RPC remains responsive. This is the production path
        that previously rescanned the entire queue for every pop (O(N^2)).
        """
        transactions = [
            self._build_normal_transaction(index)
            for index in range(self.NORMAL_TX_COUNT)
        ]
        queue_samples, stop_sampling, sampler = self._sample_verify_queue()
        started_at = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=128) as executor:
                tx_hashes = list(
                    executor.map(self._send_transaction_quietly, transactions)
                )
            self._wait_for_pending_transactions(tx_hashes, timeout=60)
            self._wait_verify_queue_empty(timeout=60)
        finally:
            stop_sampling.set()
            sampler.join(timeout=5)
        elapsed = time.monotonic() - started_at

        assert not sampler.is_alive()
        assert len(set(tx_hashes)) == self.NORMAL_TX_COUNT
        assert max(queue_samples, default=0) >= 100, queue_samples[-20:]
        assert elapsed < 60
        assert self.receiver.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
        assert self.receiver.getClient().get_tip_header()["hash"].startswith("0x")
        self.did_pass = True

    def test_large_cycle_handoff_does_not_self_wake_from_stored_permit(self):
        """
        PR #5249: relay several transactions whose declared cycles exceed the
        receiver's small-cycle threshold. While the general worker verifies
        them, the small-only worker must hand work off without storing a permit
        that makes it repeatedly wake itself.
        """
        receiver_log = Path(self.receiver.ckb_dir) / "node.log"
        log_offset = receiver_log.stat().st_size
        transactions = [
            self._build_expensive_transaction(self.NORMAL_TX_COUNT + index)
            for index in range(self.EXPENSIVE_TX_COUNT)
        ]
        acceptance = self.source.getClient().test_tx_pool_accept(
            transactions[0], "passthrough"
        )
        assert int(acceptance["cycles"], 16) > self.RECEIVER_MAX_VERIFY_CYCLES
        queue_samples, stop_sampling, sampler = self._sample_verify_queue()
        try:
            with ThreadPoolExecutor(max_workers=self.EXPENSIVE_TX_COUNT) as executor:
                tx_hashes = list(
                    executor.map(self._send_transaction_quietly, transactions)
                )
            self._wait_for_source_pending_transactions(tx_hashes, timeout=60)
            self._wait_for_queue_activity(queue_samples, timeout=30)
            self._wait_verify_queue_empty(timeout=60)
        finally:
            stop_sampling.set()
            sampler.join(timeout=5)

        source_pool = self.source.getClient().get_raw_tx_pool(True)["pending"]
        declared_cycles = [
            int(source_pool[tx_hash]["cycles"], 16) for tx_hash in tx_hashes
        ]
        assert min(declared_cycles) > self.RECEIVER_MAX_VERIFY_CYCLES

        time.sleep(0.5)
        with receiver_log.open("rb") as log_file:
            log_file.seek(log_offset)
            new_log = log_file.read().decode("utf-8", errors="replace")
        renotify_count = new_log.count(self.RENOTIFY_LOG)

        assert max(queue_samples, default=0) >= 2, queue_samples
        assert 1 <= renotify_count <= self.EXPENSIVE_TX_COUNT * 2, renotify_count
        assert "verify worker panicked" not in new_log.lower()
        assert self.receiver.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
        assert self.receiver.getClient().get_tip_header()["hash"].startswith("0x")
        self.did_pass = True

    def _send_transaction_quietly(self, transaction):
        request = urllib.request.Request(
            self.source.getClient().url,
            data=json.dumps(
                {
                    "id": 42,
                    "jsonrpc": "2.0",
                    "method": "send_transaction",
                    "params": [transaction, "passthrough"],
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert "error" not in payload, payload["error"]
        tx_hash = payload.get("result")
        assert isinstance(tx_hash, str) and tx_hash.startswith("0x"), payload
        return tx_hash

    def _sample_verify_queue(self):
        samples = []
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    samples.append(self._verify_queue_size())
                except Exception:
                    pass
                time.sleep(0.01)

        thread = threading.Thread(target=sample, daemon=True)
        thread.start()
        return samples, stop, thread

    def _wait_for_pending_transactions(self, tx_hashes, timeout):
        expected = set(tx_hashes)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pool = self.receiver.getClient().get_raw_tx_pool()
            if expected.issubset(pool["pending"]):
                return
            time.sleep(0.1)
        missing = expected.difference(pool["pending"])
        raise AssertionError(f"receiver is missing {len(missing)} pending transactions")

    def _wait_for_source_pending_transactions(self, tx_hashes, timeout):
        expected = set(tx_hashes)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pool = self.source.getClient().get_raw_tx_pool()
            if expected.issubset(pool["pending"]):
                return
            time.sleep(0.1)
        missing = expected.difference(pool["pending"])
        raise AssertionError(f"source is missing {len(missing)} pending transactions")

    @staticmethod
    def _wait_for_queue_activity(queue_samples, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if max(queue_samples, default=0) >= 2:
                return
            time.sleep(0.01)
        raise AssertionError("receiver verify queue never contained two transactions")

    def _wait_verify_queue_empty(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._verify_queue_size() == 0:
                return
            time.sleep(0.05)
        raise AssertionError(
            f"verify queue did not drain; size={self._verify_queue_size()}"
        )

    def _verify_queue_size(self):
        return int(self.receiver.getClient().tx_pool_info()["verify_queue_size"], 16)

    def _build_normal_transaction(self, index):
        return {
            "version": "0x0",
            "cell_deps": [self.always_success_dep],
            "header_deps": [],
            "inputs": [
                {
                    "previous_output": {
                        "tx_hash": self.split_tx_hash,
                        "index": hex(index),
                    },
                    "since": "0x0",
                }
            ],
            "outputs": [
                {
                    "capacity": hex(self.CELL_CAPACITY - self.TX_FEE),
                    "lock": self._always_success_lock(),
                }
            ],
            "outputs_data": ["0x"],
            "witnesses": ["0x" + "00" * self.NORMAL_WITNESS_BYTES],
        }

    def _build_expensive_transaction(self, index):
        return {
            "version": "0x0",
            "cell_deps": [
                {"out_point": self.contract_out_point, "dep_type": "code"},
                self.always_success_dep,
            ],
            "header_deps": [self.expensive_contract_header_hash],
            "inputs": [
                {
                    "previous_output": {
                        "tx_hash": self.split_tx_hash,
                        "index": hex(index),
                    },
                    "since": "0x0",
                }
            ],
            "outputs": [
                {
                    "capacity": hex(self.CELL_CAPACITY - self.TX_FEE),
                    "lock": self._always_success_lock(),
                    "type": {
                        "code_hash": self.expensive_contract_code_hash,
                        "hash_type": "type",
                        "args": "0x02",
                    },
                }
            ],
            "outputs_data": ["0x1234"],
            "witnesses": [],
        }

    @classmethod
    def _deploy_expensive_contract(cls):
        deploy_hash = cls.Contract.deploy_ckb_contract(
            cls.Config.MINER_PRIVATE_1,
            str(cls.EXPENSIVE_CONTRACT_PATH),
            enable_type_id=True,
            api_url=cls.source.getClient().url,
        )
        cls.Miner.miner_until_tx_committed(cls.source, deploy_hash)
        cls.Node.wait_node_height(
            cls.receiver,
            cls.source.getClient().get_tip_block_number(),
            60,
        )
        cls.expensive_contract_code_hash = cls.Contract.get_ckb_contract_codehash(
            deploy_hash,
            0,
            True,
            cls.source.getClient().url,
        )
        cls.expensive_contract_header_hash = cls.source.getClient().get_transaction(
            deploy_hash
        )["tx_status"]["block_hash"]
        return {"tx_hash": deploy_hash, "index": "0x0"}

    @classmethod
    def _create_workload_cells(cls, source):
        workload_count = cls.NORMAL_TX_COUNT + cls.EXPENSIVE_TX_COUNT
        change_capacity = (
            source["capacity"] - cls.CELL_CAPACITY * workload_count - cls.SPLIT_FEE
        )
        assert change_capacity >= cls.CELL_CAPACITY
        outputs = [
            {
                "capacity": hex(cls.CELL_CAPACITY),
                "lock": cls._always_success_lock(),
            }
            for _ in range(workload_count)
        ]
        outputs.append(
            {
                "capacity": hex(change_capacity),
                "lock": cls._always_success_lock(),
            }
        )
        transaction = {
            "version": "0x0",
            "cell_deps": [cls.always_success_dep],
            "header_deps": [],
            "inputs": [{"previous_output": source["out_point"], "since": "0x0"}],
            "outputs": outputs,
            "outputs_data": ["0x"] * len(outputs),
            "witnesses": [],
        }
        split_hash = cls.source.getClient().send_transaction(transaction)
        cls.Miner.miner_until_tx_committed(cls.source, split_hash)
        return split_hash

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
    def _copy_always_success_cell(cls, node):
        cells_dir = Path(node.ckb_dir) / "specs" / "cells"
        cells_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(
            Path(get_project_root()) / "source" / "contract" / "always_success",
            cells_dir / "always_success",
        )

    @classmethod
    def _find_always_success_dep(cls, node):
        genesis = node.getClient().get_block_by_number("0x0")
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
    def _genesis_always_success_source(cls, node):
        genesis = node.getClient().get_block_by_number("0x0")
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
