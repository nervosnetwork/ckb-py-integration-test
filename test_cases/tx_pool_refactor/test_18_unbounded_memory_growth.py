import hashlib
import http.client
import json
import re
import shutil
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from framework.basic import CkbTest
from framework.util import get_project_root, run_command


class TestUnboundedMemoryGrowth(CkbTest):
    """Relay mailbox lifecycle and bounded logger control under transaction load."""

    LOGGER_LOAD_TRANSACTIONS = 10_000
    REQUEST_BATCH_SIZE = 256
    SUBMIT_TIMEOUT_SECONDS = 240
    INITIAL_FEE = 1_000_000
    RBF_FEE_STEP = 1_000
    METRICS_PORT = 8452
    IBD_RPC_PORT = 8460
    IBD_P2P_PORT = 8461
    IBD_METRICS_PORT = 8462
    METRIC_NAME = "ckb_relay_tx_verify_result_queue_size"
    CAPACITY_METRIC_NAME = "ckb_relay_tx_verify_result_queue_capacity"
    ALWAYS_SUCCESS_CODE_HASH = (
        "0x28e83a1277d48add8e72fadaa9248559e1b632bab2bd60b27955ebc4c03800a5"
    )
    SPEC_PATH = "tmp/pr5292_unbounded_memory_growth_dev.toml"
    IBD_SPEC_PATH = "tmp/pr5292_unbounded_memory_growth_ibd_dev.toml"

    @classmethod
    def setup_class(cls):
        node_path = cls._create_always_success_node_path()
        cls.node = cls.CkbNode.init_dev_by_port(
            node_path,
            "tx_pool/pr5292_unbounded_memory_growth",
            8450,
            8451,
        )
        try:
            cls.node.clean()
            cls.node.prepare(
                other_ckb_config={
                    "ckb_logger_filter": (
                        "info,ckb-rpc=debug,ckb-relay=trace,ckb-tx-pool=debug"
                    ),
                    "ckb_rpc_batch_limit": cls.REQUEST_BATCH_SIZE,
                    "ckb_rpc_max_request_body_size": 16 * 1024 * 1024,
                    "ckb_tx_pool_min_fee_rate": "1_000",
                    "ckb_tx_pool_min_rbf_rate": "1_500",
                    "ckb_block_assembler_code_hash": cls.ALWAYS_SUCCESS_CODE_HASH,
                    "ckb_block_assembler_args": "0x",
                    "ckb_block_assembler_hash_type": "data",
                    "ckb_block_assembler_message": "0x",
                }
            )
            cls._copy_always_success_cell()
            cls._enable_prometheus_exporter()
            cls._start_node_with_advanced_block_assembler()
            cls._wait_for_metrics_endpoint()
            cls.Miner.make_tip_height_number(cls.node, 30)
            cls.always_success_dep = cls._find_always_success_dep()
            cls.rbf_source = cls._cellbase_source(12)
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
        self.node.getClient().clear_tx_verify_queue()

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

    def test_relay_mailbox_drains_without_peers(self):
        """Committed relay effects are consumed even when no peer can receive them."""
        assert self.node.getClient().sync_state()["ibd"] is False
        self._exercise_relay_mailbox(self.node, self.METRICS_PORT, stream_id=1)
        self.did_pass = True

    def test_relay_mailbox_drains_during_initial_block_download(self):
        """IBD gates broadcasting while committed relay effects keep being consumed."""
        node_path = self._create_always_success_node_path(
            spec_path=self.IBD_SPEC_PATH,
            issue_always_success_cell=True,
        )
        ibd_node = self.CkbNode.init_dev_by_port(
            node_path,
            "tx_pool/pr5292_unbounded_memory_growth_ibd",
            self.IBD_RPC_PORT,
            self.IBD_P2P_PORT,
        )
        started = False
        try:
            ibd_node.clean()
            ibd_node.prepare(
                other_ckb_config={
                    "ckb_logger_filter": "info,ckb-relay=trace,ckb-tx-pool=debug",
                    "ckb_tx_pool_min_fee_rate": "1_000",
                    "ckb_tx_pool_min_rbf_rate": "1_500",
                    "ckb_block_assembler_code_hash": self.ALWAYS_SUCCESS_CODE_HASH,
                    "ckb_block_assembler_args": "0x",
                    "ckb_block_assembler_hash_type": "data",
                    "ckb_block_assembler_message": "0x",
                }
            )
            self._copy_always_success_cell(ibd_node)
            self._enable_prometheus_exporter(ibd_node, self.IBD_METRICS_PORT)
            self._start_node_with_advanced_block_assembler(ibd_node)
            started = True
            self._wait_for_metrics_endpoint(self.IBD_METRICS_PORT)

            assert ibd_node.getClient().sync_state()["ibd"] is True
            self._exercise_relay_mailbox(
                ibd_node,
                self.IBD_METRICS_PORT,
                stream_id=3,
                rbf_source=self._genesis_always_success_source(ibd_node),
                always_success_dep=self._find_always_success_dep(ibd_node),
            )
            self.did_pass = True
        finally:
            if started:
                ibd_node.stop()
            ibd_node.clean()

    def test_logger_control_remains_responsive_under_transaction_log_load(self):
        """
        TP-INT-LOGGER-MEM-5292-002 [P1]: runtime logger control requests made
        during sustained log production return promptly; a full bounded logger
        channel may reject a request, but it must not block the RPC worker.
        """
        assert self.node.get_connected_count() == 0

        stop_logger_probes = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as executor:
            probes = [
                executor.submit(self._probe_logger_control, stop_logger_probes, index)
                for index in range(2)
            ]
            try:
                submitted, _ = self._submit_rbf_transactions(
                    self.LOGGER_LOAD_TRANSACTIONS,
                    stream_id=2,
                )
            finally:
                stop_logger_probes.set()
            probe_results = [probe.result(timeout=15) for probe in probes]

        assert submitted == self.LOGGER_LOAD_TRANSACTIONS
        assert sum(result["calls"] for result in probe_results) > 0
        assert max(result["max_elapsed"] for result in probe_results) < 5

        # Logger record/control load must not make the node or RPC worker unresponsive.
        assert self.node.getClient().get_tip_header()["hash"].startswith("0x")
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
        assert len(self.node.getClient().get_raw_tx_pool()["pending"]) == 1
        self.did_pass = True

    def _exercise_relay_mailbox(
        self, node, metrics_port, stream_id, rbf_source=None, always_success_dep=None
    ):
        client = node.getClient()
        ibd = client.sync_state()["ibd"]
        assert node.get_connected_count() == 0
        _, capacity = self._relay_mailbox_state(metrics_port)
        per_round = 2 * capacity

        # Each round publishes more outcomes than the mailbox can retain. Its
        # consumer must keep progressing, including after the preceding drain.
        for round_index in range(3):
            submitted, last_hash = self._submit_rbf_transactions(
                per_round,
                stream_id,
                node=node,
                rbf_source=rbf_source,
                always_success_dep=always_success_dep,
                metrics_port=metrics_port,
                start_index=round_index * per_round,
            )
            assert submitted == per_round
            self._wait_for_relay_result_queue_drain(metrics_port)
            assert self._relay_mailbox_state(metrics_port)[1] == capacity
            assert client.get_raw_tx_pool()["pending"] == [last_hash]
            assert client.tx_pool_info()["verify_queue_size"] == "0x0"
            assert client.sync_state()["ibd"] is ibd
            assert node.get_connected_count() == 0

    @classmethod
    def _create_always_success_node_path(
        cls, spec_path=None, issue_always_success_cell=False
    ):
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

        if issue_always_success_cell:
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

        relative_spec_path = spec_path or cls.SPEC_PATH
        generated_spec_path = root / relative_spec_path
        generated_spec_path.parent.mkdir(parents=True, exist_ok=True)
        generated_spec_path.write_text(spec_text, encoding="utf-8")
        return cls.CkbNodeConfigPath(
            current.ckb_config_path,
            current.ckb_miner_config_path,
            relative_spec_path,
            current.ckb_bin_path,
        )

    @classmethod
    def _copy_always_success_cell(cls, node=None):
        target_node = node or cls.node
        cells_dir = Path(target_node.ckb_dir) / "specs" / "cells"
        cells_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(
            Path(get_project_root()) / "source" / "contract" / "always_success",
            cells_dir / "always_success",
        )

    @classmethod
    def _start_node_with_advanced_block_assembler(cls, node=None):
        target_node = node or cls.node
        target_node.ckb_pid = run_command(
            "cd {ckb_dir} && ./ckb run --indexer --ba-advanced "
            "--skip-spec-check > node.log 2>&1 &".format(ckb_dir=target_node.ckb_dir)
        )
        time.sleep(3)

    @classmethod
    def _find_always_success_dep(cls, node=None):
        target_node = node or cls.node
        genesis = target_node.getClient().get_block_by_number("0x0")
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
    def _cellbase_source(cls, block_number):
        block = cls.node.getClient().get_block_by_number(hex(block_number))
        cellbase = block["transactions"][0]
        output = cellbase["outputs"][0]
        assert output["lock"] == cls._always_success_lock()
        return {
            "out_point": {"tx_hash": cellbase["hash"], "index": "0x0"},
            "capacity": int(output["capacity"], 16),
        }

    @classmethod
    def _always_success_lock(cls):
        return {
            "code_hash": cls.ALWAYS_SUCCESS_CODE_HASH,
            "hash_type": "data",
            "args": "0x",
        }

    @classmethod
    def _enable_prometheus_exporter(cls, node=None, metrics_port=None):
        target_node = node or cls.node
        target_port = metrics_port or cls.METRICS_PORT
        with open(target_node.ckb_toml_path, "a", encoding="utf-8") as config_file:
            config_file.write(
                "\n[metrics.exporter.prometheus]\n"
                'target = { type = "prometheus", '
                f'listen_address = "127.0.0.1:{target_port}" }}\n'
            )

    @classmethod
    def _wait_for_metrics_endpoint(cls, metrics_port=None):
        target_port = metrics_port or cls.METRICS_PORT
        last_error = None
        for _ in range(50):
            try:
                cls._metrics_text(target_port)
                return
            except Exception as error:
                last_error = error
                time.sleep(0.2)
        raise AssertionError(f"metrics endpoint did not start: {last_error}")

    def _submit_rbf_transactions(
        self,
        target_count,
        stream_id,
        node=None,
        rbf_source=None,
        always_success_dep=None,
        metrics_port=None,
        start_index=0,
    ):
        target_node = node or self.node
        target_source = rbf_source or self.rbf_source
        target_dep = always_success_dep or self.always_success_dep
        submitted = 0
        last_hash = None
        deadline = time.monotonic() + self.SUBMIT_TIMEOUT_SECONDS
        rpc_url = urlparse(target_node.getClient().url)
        connection = http.client.HTTPConnection(
            rpc_url.hostname, rpc_url.port, timeout=30
        )
        try:
            for index in range(start_index, start_index + target_count):
                if time.monotonic() >= deadline:
                    raise AssertionError(
                        f"submitted only {submitted}/{target_count} transactions "
                        "before timeout"
                    )

                transaction = self._build_rbf_transaction(
                    index,
                    stream_id,
                    rbf_source=target_source,
                    always_success_dep=target_dep,
                )
                request = {
                    "id": index,
                    "jsonrpc": "2.0",
                    "method": "send_transaction",
                    "params": [transaction, "passthrough"],
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
                assert "result" in response, {"index": index, "response": response}
                submitted += 1
                last_hash = response["result"]
                if metrics_port is not None and submitted % 64 == 0:
                    self._relay_mailbox_state(metrics_port)
        finally:
            connection.close()
        return submitted, last_hash

    def _build_rbf_transaction(
        self, index, stream_id, rbf_source=None, always_success_dep=None
    ):
        target_source = rbf_source or self.rbf_source
        target_dep = always_success_dep or self.always_success_dep
        # The sub-KB transaction needs less than this 1,000-shannon increment
        # at the configured 1,500 shannons/KB replacement rate.
        output_capacity = (
            target_source["capacity"] - self.INITIAL_FEE - index * self.RBF_FEE_STEP
        )
        assert output_capacity > 4_900_000_000
        return {
            "version": "0x0",
            "cell_deps": [target_dep],
            "header_deps": [],
            "inputs": [{"previous_output": target_source["out_point"], "since": "0x0"}],
            "outputs": [
                {
                    "capacity": hex(output_capacity),
                    "lock": self._always_success_lock(),
                }
            ],
            "outputs_data": ["0x" + stream_id.to_bytes(8, "little").hex()],
            "witnesses": [],
        }

    def _relay_mailbox_state(self, metrics_port):
        metrics = self._metrics_text(metrics_port)
        capacity = self._metric_value(metrics, self.CAPACITY_METRIC_NAME)
        size = self._metric_value(metrics, self.METRIC_NAME)
        assert (
            capacity is not None and capacity > 0
        ), f"relay mailbox capacity missing or invalid: {capacity}"
        assert (
            size is not None and 0 <= size <= capacity
        ), f"relay mailbox occupancy outside its bound: size={size}, capacity={capacity}"
        return size, capacity

    def _wait_for_relay_result_queue_drain(self, metrics_port):
        last_value = None
        for _ in range(100):
            last_value, _ = self._relay_mailbox_state(metrics_port)
            if last_value == 0:
                return
            time.sleep(0.1)
        raise AssertionError(
            f"{self.METRIC_NAME} did not drain after submission: {last_value}"
        )

    def _probe_logger_control(self, stop_event, probe_index):
        calls = 0
        full_errors = 0
        max_elapsed = 0.0

        while not stop_event.is_set() or calls < 10:
            started_at = time.monotonic()
            response = self._post_json(
                {
                    "id": f"logger-{probe_index}-{calls}",
                    "jsonrpc": "2.0",
                    "method": "update_main_logger",
                    "params": [
                        {
                            "filter": None,
                            "to_stdout": None,
                            "to_file": None,
                            "color": bool(calls % 2),
                        }
                    ],
                },
                timeout=5,
            )
            elapsed = time.monotonic() - started_at
            max_elapsed = max(max_elapsed, elapsed)

            if "error" in response:
                message = response["error"].get("message", "")
                assert (
                    "logger service" in message or "full channel" in message
                ), response
                full_errors += 1
            else:
                assert response.get("result") is None, response

            calls += 1
            time.sleep(0.01)

        return {
            "calls": calls,
            "full_errors": full_errors,
            "max_elapsed": max_elapsed,
        }

    @classmethod
    def _metrics_text(cls, metrics_port=None):
        target_port = metrics_port or cls.METRICS_PORT
        with urllib.request.urlopen(
            f"http://127.0.0.1:{target_port}", timeout=5
        ) as response:
            return response.read().decode("utf-8")

    @staticmethod
    def _metric_value(metrics_text, metric_name):
        match = re.search(
            rf"^{re.escape(metric_name)}(?:\{{[^}}]*\}})?\s+(-?\d+(?:\.\d+)?)$",
            metrics_text,
            re.MULTILINE,
        )
        return None if match is None else int(float(match.group(1)))

    def _post_json(self, data, timeout):
        request = urllib.request.Request(
            self.node.getClient().url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
