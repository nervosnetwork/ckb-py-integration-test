"""Public RPC cancellation, concurrency and subscription boundaries."""

import copy
import http.client
import json
import select
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit

import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import Transactions
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash
from test_cases.tx_pool_refactor.subscription_client import SubscriptionClient
from test_cases.tx_pool_refactor.test_capacity_metrics import (
    _metric,
    _metrics,
)


def pending_submission(node, tx):
    url = urlsplit(node.rpcUrl)
    connection = socket.create_connection((url.hostname, url.port), timeout=5)
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "send_transaction",
            "params": [tx, "passthrough"],
        }
    ).encode()
    connection.sendall(
        (
            f"POST / HTTP/1.1\r\nHost: {url.netloc}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode()
        + body
    )
    return connection


class RpcProgressFixture(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        try:
            for name in ("subject", "producer"):
                rpc, p2p, tcp, metrics = workload.unused_ports(4)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/rpc_progress/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                cls.nodes.append(node)
                node.prepare()
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                # Four finite stress groups need about 2.016e12 cycles in total.
                # This cycle ceiling is independent of the unchanged default CPU cap.
                config["tx_pool"]["max_tx_verify_cycles"] = 4_000_000_000_000
                config["tx_pool"]["max_tx_verify_workers"] = 2
                config["network"]["connect_outbound_interval_secs"] = 0
                config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp}"
                if "Subscription" not in config["rpc"]["modules"]:
                    config["rpc"]["modules"].append("Subscription")
                config.setdefault("metrics", {}).setdefault("exporter", {})[
                    "prometheus"
                ] = {
                    "target": {
                        "type": "prometheus",
                        "listen_address": f"127.0.0.1:{metrics}",
                    }
                }
                path.write_text(toml.dumps(config))
                workload.add_genesis_test_cells(
                    node, program_names=("cpu_budget_stress_1b",)
                )
                path = Path(node.ckb_specs_config_path)
                spec = toml.loads(path.read_text())
                spec["params"]["max_block_cycles"] = 4_000_000_000_000
                path.write_text(toml.dumps(spec))
                node.start()
                if name == "subject":
                    cls.metrics_port, cls.tcp_port = metrics, tcp
            cls.node, cls.producer = cls.nodes
            cls.Miner.make_tip_height_number(cls.producer, 30)
            workload.sync_blocks(cls.producer, cls.node, 1, 30)
            cls.base_hash = cls.node.getClient().get_tip_header()["hash"]
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
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == self.base_hash,
                "base tip restored",
            )

    def teardown_method(self, method):
        pass

    def active(self):
        return _metric(
            _metrics(self.metrics_port),
            "ckb_tx_pool_pipeline_residency",
            "resource",
            "active_work",
        )

    def slow(self, cell, groups, tag):
        tx = workload.build_budget_transaction(self.node, cell=cell)
        output = tx["outputs"][0]
        tx["outputs"] = [copy.deepcopy(output) for _ in range(groups)]
        for index, output in enumerate(tx["outputs"]):
            output["capacity"] = hex(6400 * workload.CKB // groups)
            output["type"]["args"] = "0x" + bytes([tag, index]).hex()
        tx["outputs_data"] = ["0x"] * groups
        return tx
