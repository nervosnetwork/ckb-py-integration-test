"""PR #5316: capacity-rejection diagnostics and settled pipeline gauges."""

import json
import re
import socket
import time
import urllib.request
from pathlib import Path

import requests

from framework.basic import CkbTest
import toml
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _metric(metrics, name, label, value):
    pattern = re.compile(
        rf'^{re.escape(name)}\{{{label}="{value}"\}}\s+(\d+)(?:\.0+)?$',
        re.MULTILINE,
    )
    match = pattern.search(metrics)
    assert match is not None, f'missing {name}{{{label}="{value}"}}'
    return int(match.group(1))


def _metrics(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2) as response:
        return response.read().decode("utf-8")


def _rejection(log_path, tx_hash):
    for line in log_path.read_text(errors="replace").splitlines():
        start = line.find('{"accounts":')
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if (
            event.get("event") == "committed_rejection"
            and event.get("hash") == tx_hash[2:]
        ):
            return event
    return None


class TestCapacityMetrics(CkbTest):
    @classmethod
    def setup_class(cls):
        rpc_port, p2p_port, cls.metrics_port = workload.unused_ports(3)
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            f"network_verification/notify_budget/{rpc_port}",
            rpc_port,
            p2p_port,
        )
        try:
            cls.node.prepare()
            path = Path(cls.node.ckb_toml_path)
            config = toml.loads(path.read_text())
            config["tx_pool"].update(
                max_tx_verify_cycles=1_000_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_script=debug"
            config["network"]["connect_outbound_interval_secs"] = 0
            config.setdefault("metrics", {}).setdefault("exporter", {})[
                "prometheus"
            ] = {
                "target": {
                    "type": "prometheus",
                    "listen_address": f"127.0.0.1:{cls.metrics_port}",
                }
            }
            path.write_text(toml.dumps(config))
            workload.add_genesis_test_cells(
                cls.node, program_names=("cpu_budget_stress_1b",)
            )
            path = Path(cls.node.ckb_specs_config_path)
            spec = toml.loads(path.read_text())
            spec["params"]["max_block_cycles"] = 1_000_000_000_000
            path.write_text(toml.dumps(spec))
            cls.node.start()
            cls.Miner.make_tip_height_number(cls.node, 30)
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        workload.save_node_logs(cls.node)
        cls.node.clean()

    def teardown_method(self, method):
        workload.save_node_logs(self.node)

    # TEST-MAP: NTD-74
    # TEST-MAP: EVT-01
    def test_notify_budget_rejection_preserves_pool_and_allows_local_recovery(self):
        """
        1. 默认 CPU 上限下先接收独立快交易，记录池和指标基线。
        2. notify 慢交易，按目标 hash/source/stage 确认预算拒绝及原 Accepted 保持。
        3. 等待指标结算，检查拒绝归因、无高基数 label 和可再次执行同 hash。
        4. 本地提交相同慢交易，核对成功入池和原快交易保持。
        5. 无 peer 冷重启，检查两笔交易及 cycles 恢复，并正常打包。
        """
        node, port = self.node, self.metrics_port
        client = node.getClient()

        # 1. 先建立独立 Accepted，避免只对空池作不变断言。
        fast = workload.build_transaction(node, cell=1)
        fast_hash = client.send_transaction(fast)
        workload.assert_pool_transaction(node, fast_hash, fast, workload.FAST_CYCLES)
        before_pool = client.get_raw_tx_pool(True)
        before = _metrics(port)
        before_capacity = _metric(
            before, "ckb_tx_pool_pipeline_rejections", "class", "capacity"
        )
        before_active = _metric(
            before, "ckb_tx_pool_pipeline_residency", "resource", "active_work"
        )
        before_entries = _metric(
            before, "ckb_tx_pool_pipeline_residency", "resource", "total_entries"
        )
        assert before_active == 0
        candidate = workload.build_budget_transaction(node)
        tx_hash = transaction_hash(candidate)
        assert node.get_connected_count() == 0

        # 2. notify 返回 hash 仅说明入站完成；等待真实 Proposal 验证拒绝。
        assert (
            client.call("notify_transaction", [candidate], try_count=1, timeout=10)
            == tx_hash
        )
        workload.wait_for(
            lambda: workload.budget_refusals(node, tx_hash, "proposal"),
            "notify CPU budget refusal",
            60,
        )
        event = workload.budget_rejection(
            workload.budget_refusals(node, tx_hash, "proposal")[-1], tx_hash, "proposal"
        )
        assert (
            event["schema"] == 1
            and event["resource_observation"] == "rejection_preparation"
        )
        assert isinstance(event["accounts"], list) and len(event["accounts"]) <= 5
        for account in event["accounts"]:
            assert account["account"] in {
                "accepted",
                "pipeline",
                "remote",
                "peer",
                "history",
            }
            assert set(account) == {"account", "usage", "limit"}
            assert set(account["usage"]) == {
                "items",
                "bytes",
                "edges",
                "serialized",
                "cycles",
            }
        assert client.get_raw_tx_pool(True) == before_pool
        detail = client.get_transaction(tx_hash)
        assert (
            detail["tx_status"]["status"] == "unknown" and detail["transaction"] is None
        )
        assert client.get_banned_addresses() == []

        # 3. 原 hash 再次实际执行并拒绝；每次都等待公开指标回到基线。
        for attempt in (1, 2):
            if attempt == 2:
                assert (
                    client.call(
                        "notify_transaction", [candidate], try_count=1, timeout=10
                    )
                    == tx_hash
                )
            workload.wait_for(
                lambda: len(workload.budget_refusals(node, tx_hash, "proposal"))
                == attempt,
                f"notify attempt {attempt} completed",
                60,
            )

            def settled():
                metrics = _metrics(port)
                return (
                    _metric(
                        metrics, "ckb_tx_pool_pipeline_rejections", "class", "capacity"
                    )
                    == before_capacity + attempt
                    and _metric(
                        metrics,
                        "ckb_tx_pool_pipeline_residency",
                        "resource",
                        "active_work",
                    )
                    == before_active
                    and _metric(
                        metrics,
                        "ckb_tx_pool_pipeline_residency",
                        "resource",
                        "total_entries",
                    )
                    == before_entries
                )

            workload.wait_for(settled, "notify metrics settled", 20)
            assert client.get_raw_tx_pool(True) == before_pool
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
        for line in _metrics(port).splitlines():
            if line.startswith("ckb_tx_pool_pipeline_") and "{" in line:
                assert tx_hash[2:] not in line

        # 4. 同一字节经本地入口完整执行，网络拒绝没有污染本地准入。
        response = requests.post(
            client.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "send_transaction",
                "params": [candidate, "passthrough"],
            },
            timeout=workload.BUDGET_RPC_TIMEOUT,
        )
        assert response.status_code in (200, 408), (response.status_code, response.text)
        if response.status_code == 200:
            assert response.json().get("result") == tx_hash, response.text
        # Observe the original request after a real service timeout, without retrying it.
        workload.wait_for(
            lambda: tx_hash in workload.pool_transactions(client),
            "original local request admitted",
            workload.BUDGET_RPC_TIMEOUT,
        )
        print({"local_hash": tx_hash, "http_status": response.status_code})
        cycles = int(workload.pool_transactions(client)[tx_hash]["cycles"], 16)
        assert 0 < cycles < 1_000_000_000_000
        saved = workload.assert_pool_transaction(node, tx_hash, candidate, cycles)
        workload.assert_pool_transaction(node, fast_hash, fast, workload.FAST_CYCLES)

        # 5. 真实 PID 边界、无 peer、无重交，检验持久化恢复而非重复广播。
        workload.restart_and_assert_new_process(node)
        assert node.get_connected_count() == 0
        recovered = workload.assert_pool_transaction(
            node,
            tx_hash,
            candidate,
            cycles,
            timeout=workload.BUDGET_RPC_TIMEOUT,
        )
        assert recovered["transaction"] == saved["transaction"]
        assert recovered["cycles"] == saved["cycles"]
        workload.assert_pool_transaction(node, fast_hash, fast, workload.FAST_CYCLES)
        for target in (tx_hash, fast_hash):
            assert (
                self.Miner.miner_until_tx_committed(node, target)["tx_status"]["status"]
                == "committed"
            )
        self.did_pass = True
