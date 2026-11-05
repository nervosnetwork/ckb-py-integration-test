"""Native pytest performance probes; raw data only until thresholds are frozen.

CURRENT_MAIN contributes only its binary path. Both sides use CURRENT_TEST's
dev templates/spec, equal inputs, fixed schedules and ordinary 8s CPU defaults.
These short diagnostic rounds are not a statistically powered regression gate.
"""

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import (
    Transactions,
    assert_pool,
    packed_transaction,
    propose,
    size_in_block,
)
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash
from test_cases.tx_pool_refactor.observations import resources, save_observations
from test_cases.tx_pool_refactor.subscription_client import SubscriptionClient
from test_cases.tx_pool_refactor.test_process_resilience import read_snapshot
from test_cases.tx_pool_refactor.tests.test_29_pr5316_persistence_replay import (
    _transactions,
)
from test_cases.tx_pool_refactor.tests.test_38_pr5316_template_notify import (
    _HoldingServer,
)
from test_cases.tx_pool_refactor.tests.test_50_pr5316_capacity_metrics import _metrics


def percentiles(values):
    """Retain sparse-tail status; never report a p99 from a handful of completions."""
    values = sorted(values)

    def quantile(q):
        if not values:
            return None
        return values[min(len(values) - 1, int((len(values) - 1) * q))]

    return {
        "n": len(values),
        "p50": quantile(0.50),
        "p95": quantile(0.95),
        "p99": quantile(0.99) if len(values) >= 100 else None,
        "p99_status": "available" if len(values) >= 100 else "insufficient_samples",
    }


def arrival_window(target, source, transactions, *, rate, ingress, deadline=60):
    """Open-loop schedule plus a separate observer of actual target admission."""
    hashes = list(map(transaction_hash, transactions))
    assert len(set(hashes)) == len(hashes)
    start = time.monotonic()
    rows = [
        {"hash": h, "planned": start + index / rate, "transaction": tx}
        for index, (h, tx) in enumerate(zip(hashes, transactions))
    ]
    by_hash = {row["hash"]: row for row in rows}
    stop = threading.Event()
    observation_errors = []
    client = target.getClient()

    def observe():
        try:
            while not stop.is_set():
                entries = workload.pool_transactions(client)
                observed = time.monotonic()
                for tx_hash in entries.keys() & by_hash.keys():
                    by_hash[tx_hash].setdefault("accepted_at", observed)
                stop.wait(0.02)
        except Exception as error:
            observation_errors.append(repr(error))

    def send(row, sender):
        row["sent_at"] = time.monotonic()
        row["source"] = "local" if sender is target else "remote"
        try:
            response = requests.post(
                sender.rpcUrl,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "send_transaction",
                    "params": [row["transaction"], "passthrough"],
                },
                timeout=40,
            )
            row["http_status"] = response.status_code
            row["http_body"] = response.text
        except requests.RequestException as error:
            row["transport_error"] = type(error).__name__
        row["http_finished_at"] = time.monotonic()

    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    executor = ThreadPoolExecutor(max_workers=16)
    try:
        futures = []
        for index, row in enumerate(rows):
            time.sleep(max(0, row["planned"] - time.monotonic()))
            sender = (
                target
                if ingress == "local" or (ingress == "mixed" and index % 2 == 0)
                else source
            )
            futures.append(executor.submit(send, row, sender))
        for future in futures:
            future.result(timeout=45)
        until = time.monotonic() + deadline
        while time.monotonic() < until and any(
            "accepted_at" not in row for row in rows
        ):
            if observation_errors:
                break
            time.sleep(0.05)
    finally:
        stop.set()
        observer.join(timeout=5)
        executor.shutdown(wait=True)
    end = time.monotonic()
    assert not observer.is_alive()
    assert not observation_errors, observation_errors
    for row in rows:
        result = client.get_transaction(row["hash"])
        row["final_status"] = result["tx_status"]
        row["completed"] = "accepted_at" in row
        row["schedule_delay"] = row["sent_at"] - row["planned"]
        if row["completed"]:
            assert result["transaction"] == {**row["transaction"], "hash": row["hash"]}
            row["admission_latency"] = row["accepted_at"] - row["planned"]
        else:
            row["censored_at"] = end
            row["budget_refusals"] = workload.remote_refusals(target, row["hash"])
    functional_errors = []
    for row in rows:
        if row.get("http_status") == 200:
            try:
                reply = json.loads(row["http_body"])
            except json.JSONDecodeError:
                functional_errors.append(
                    {"hash": row["hash"], "error": "invalid_json_response"}
                )
                continue
            if reply.get("result") != row["hash"]:
                functional_errors.append({"hash": row["hash"], "error": reply})
        elif row.get("http_status") not in (None, 408):
            functional_errors.append({"hash": row["hash"], "error": row["http_status"]})
    completed = [row for row in rows if row["completed"]]
    summary = {
        "offered": len(rows),
        "actual_sent": len(rows),
        "accepted": len(completed),
        "unfinished": len(rows) - len(completed),
        "window_seconds": end - start,
        "throughput": len(completed) / (end - start),
        "completion_rate": len(completed) / len(rows),
        "functional_errors": functional_errors,
        "admission_seconds": percentiles(
            [row["admission_latency"] for row in completed]
        ),
        "schedule_delay_seconds": percentiles([row["schedule_delay"] for row in rows]),
    }
    return {"summary": summary, "requests": rows}


class TestPoolPerformance(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        cls.metrics_ports = {}
        cls.tcp_ports = {}
        try:
            candidate = cls.CkbNodeConfigPath.CURRENT_TEST
            baseline = cls.CkbNodeConfigPath(
                candidate.ckb_config_path,
                candidate.ckb_miner_config_path,
                candidate.ckb_spec_path,
                cls.CkbNodeConfigPath.CURRENT_MAIN.ckb_bin_path,
            )
            for name, configuration, rich in (
                ("baseline", baseline, False),
                ("candidate", candidate, False),
                ("relay", candidate, False),
                ("rich", candidate, True),
            ):
                rpc, p2p, tcp, metrics = workload.unused_ports(4)
                node = cls.CkbNode.init_dev_by_port(
                    configuration, f"tx_pool/performance/{name}_{rpc}", rpc, p2p
                )
                cls.nodes.append(node)
                setattr(cls, name, node)
                cls.metrics_ports[node] = metrics
                cls.tcp_ports[node] = tcp
                node.prepare()
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                config["network"]["connect_outbound_interval_secs"] = 0
                config["tx_pool"].update(
                    max_ancestors_count=200,
                    max_tx_pool_size=20_000_000,
                    min_rbf_rate=2000,
                    max_tx_verify_workers=2,
                )
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
                workload.add_genesis_test_cells(node)
                path = Path(node.ckb_specs_config_path)
                spec = toml.loads(path.read_text())
                spec["genesis"]["issued_cells"].extend(
                    [spec["genesis"]["issued_cells"][-1].copy() for _ in range(120)]
                )
                path.write_text(toml.dumps(spec))
                if rich:
                    node.startWithRichIndexer()
                else:
                    node.start()
            cls.Miner.make_tip_height_number(cls.relay, 30)
            for node in (cls.baseline, cls.candidate, cls.rich):
                workload.sync_blocks(cls.relay, node, 1, 30)
            cls.base_hash = cls.relay.getClient().get_tip_header()["hash"]
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
        self.reset()

    def teardown_method(self, method):
        pass

    def reset(self):
        for node in self.nodes:
            for peer in node.getClient().get_peers():
                node.getClient().remove_node(peer["node_id"])
        for node in self.nodes:
            workload.wait_for(
                lambda: node.get_connected_count() == 0, "performance topology reset"
            )
            client = node.getClient()
            if client.get_tip_header()["hash"] != self.base_hash:
                client.truncate(self.base_hash)
            client.clear_tx_pool()
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == self.base_hash,
                "performance initial chain",
            )

    # TEST-MAP: TPF-01
    def test_arrival_rate_matrix_records_complete_and_censored_requests(self):
        """
        1. 固定速率、输入、大小和快慢脚本矩阵，在同机执行 A/A 和 AB/BA 诊断顺序。
        2. 按开放式时间表提交 Local/Remote/混合负载，分别采集 HTTP 和真实准入时间。
        3. 保存全部 hash 结局、调度延迟、吞吐及未完成样本；不作未冻结门槛的达标判定。
        """
        # 1. 短诊断轮不是十轮统计检验；p99 样本不足明确标记。
        order = [
            self.baseline,
            self.baseline,
            self.candidate,
            self.candidate,
            self.baseline,
            self.candidate,
            self.candidate,
            self.baseline,
        ]
        for ingress_index, ingress in enumerate(("local", "remote", "mixed")):
            for variant, (slow, padding) in enumerate(
                ((False, 0), (False, 2048), (True, 0))
            ):
                for rate_index, rate in enumerate((2, 8)):
                    for round_number, node in enumerate(order):
                        self.reset()
                        # Fixed-width tags keep byte/cycle cost equal while avoiding
                        # known-hash filters retained between otherwise empty pools.
                        tag = (
                            10000
                            + ingress_index * 1000
                            + variant * 100
                            + rate_index * 10
                            + round_number
                        )
                        txs = Transactions(node, tag)
                        bodies = [
                            txs.build(
                                [index],
                                fee=100 * workload.CKB if slow else 10000,
                                padding=padding,
                            )
                            for index in range(8)
                        ]
                        if slow:
                            genesis = txs.genesis
                            code = workload.script_path("current_cycles")
                            dep = {
                                "out_point": {
                                    "tx_hash": genesis["hash"],
                                    "index": hex(
                                        genesis["outputs_data"].index(
                                            "0x" + code.read_bytes().hex()
                                        )
                                    ),
                                },
                                "dep_type": "code",
                            }
                            for tx in bodies:
                                output = tx["outputs"][0]
                                output["capacity"] = hex(100 * workload.CKB)
                                tx["outputs"] = [
                                    copy.deepcopy(output) for _ in range(64)
                                ]
                                for index, item in enumerate(tx["outputs"]):
                                    item["type"] = {
                                        "code_hash": workload.script_hash(code),
                                        "hash_type": "data1",
                                        "args": f"0x{index:02x}",
                                    }
                                tx["outputs_data"] *= 64
                                tx["cell_deps"].append(dep)
                        self.relay.connected(node)
                        workload.wait_for(
                            lambda: node.get_connected_count() == 1,
                            "performance relay connected",
                        )
                        # 2. 发送调度和入池观测使用不同线程，远端不拿源 RPC 返回冒充准入。
                        before = resources(node)
                        result = arrival_window(
                            node, self.relay, bodies, rate=rate, ingress=ingress
                        )
                        after = resources(node)
                        # 3. 未完成和 HTTP 错误保留为数据，正确 body 和无意外 ban 是硬断言。
                        assert node.getClient().get_banned_addresses() == []
                        save_observations(
                            node,
                            "arrival-matrix",
                            [{"before": before, "after": after, **result}],
                            settings={
                                "round": round_number,
                                "order": ["A", "A", "B", "B", "A", "B", "B", "A"],
                                "rate": rate,
                                "ingress": ingress,
                                "slow": slow,
                                "padding": padding,
                                "transaction_tag": tag,
                                "workers": 2,
                                "cpu_cap": "default",
                                "thresholds": None,
                            },
                        )
                        assert not result["summary"]["functional_errors"], result[
                            "summary"
                        ]
                        if not slow:
                            assert result["summary"]["accepted"] == len(bodies), result[
                                "summary"
                            ]
        self.did_pass = True

    # TEST-MAP: TPF-03
    def test_graph_scaling_records_admission_rbf_capacity_and_template_cost(self):
        """
        1. 两个二进制分别重建固定规模 chain/fanout/diamond 图，核对独立成员账本。
        2. 测量重复模板和合法 RBF，观测刷新窗口；未清除旧交易仅记录未完成。
        3. 用实际模板正常出块，允许任一冲突方确认，核对失效方及后代清理和新交易进展。
        4. 实际缩小池容量后测量高费置换，保存各规模 wall/CPU 样本和确认方。
        """
        for node in (self.baseline, self.candidate):
            for shape in ("chain", "fanout", "diamond"):
                for scale in (4, 16):
                    # 1. 交易图由固定 seed/tag 和输入构造；不同规模分别报告。
                    self.reset()
                    client = node.getClient()
                    txs = Transactions(node, scale)
                    root = txs.build(
                        [0], fee=10000, outputs=scale if shape != "chain" else 1
                    )
                    bodies = [root]
                    if shape == "chain":
                        for _ in range(scale - 1):
                            bodies.append(txs.build([txs.point(bodies[-1])]))
                    else:
                        leaves = [
                            txs.build([txs.point(root, index)])
                            for index in range(scale)
                        ]
                        bodies.extend(leaves)
                        if shape == "diamond":
                            bodies.append(
                                txs.build([txs.point(tx) for tx in leaves], fee=100000)
                            )
                    begin = resources(node)
                    started = time.monotonic()
                    for tx in bodies:
                        assert client.send_transaction(tx) == transaction_hash(tx)
                    admission_seconds = time.monotonic() - started
                    assert_pool(node, txs, bodies)
                    # 2. proposal 后按输入边核对顺序；10秒仅为模板刷新观察窗。
                    propose(node, self.Miner, list(map(transaction_hash, bodies)))
                    costs = []
                    for _ in range(10):
                        started = time.monotonic()
                        template = client.get_block_template()
                        costs.append(time.monotonic() - started)
                        selected = [item["hash"] for item in template["transactions"]]
                        assert set(selected) == set(map(transaction_hash, bodies))
                        for tx in bodies:
                            for value in tx["inputs"]:
                                parent = value["previous_output"]["tx_hash"]
                                if parent in selected:
                                    assert selected.index(parent) < selected.index(
                                        transaction_hash(tx)
                                    )
                    replacement = txs.build([0], fee=10000000)
                    started = time.monotonic()
                    assert client.send_transaction(replacement) == transaction_hash(
                        replacement
                    )
                    rbf_seconds = time.monotonic() - started
                    assert_pool(node, txs, [replacement])
                    refresh_started = time.monotonic()
                    old_hashes = set(map(transaction_hash, bodies))
                    while True:
                        template = client.get_block_template()
                        retained = old_hashes & {
                            item["hash"] for item in template["transactions"]
                        }
                        observed_seconds = time.monotonic() - refresh_started
                        if not retained or observed_seconds >= 10:
                            break
                        time.sleep(0.1)
                    sample = {
                        "shape": shape,
                        "scale": scale,
                        "admission_seconds": admission_seconds,
                        "template_seconds": costs,
                        "rbf_seconds": rbf_seconds,
                        "template_refresh_seconds": (
                            observed_seconds if not retained else None
                        ),
                        "template_refresh_observed": not retained,
                        "template_refresh_window_seconds": 10,
                        "template_observation_seconds": observed_seconds,
                        "retained_old_hashes": sorted(retained),
                        "before": begin,
                    }
                    save_observations(node, "graph-template-refresh", [sample])

                    # 3. 本地替换不保证最终确认；双方的后代均纳入失效清理检查。
                    replacement_hash = transaction_hash(replacement)
                    root_hash = transaction_hash(root)
                    replacement_child = txs.build([txs.point(replacement)])
                    assert client.send_transaction(
                        replacement_child
                    ) == transaction_hash(replacement_child)
                    assert_pool(node, txs, [replacement, replacement_child])
                    confirmed = set()
                    confirmation_started = time.monotonic()
                    for _ in range(6):
                        template = client.get_block_template()
                        block = self.Miner.block_template_transfer_to_submit_block(
                            copy.deepcopy(template)
                        )
                        block["header"]["timestamp"] = template["current_time"]
                        tip = client.submit_block(template["work_id"], block)
                        workload.wait_for(
                            lambda: client.tx_pool_info()["tip_hash"] == tip,
                            "confirmation block installed",
                        )
                        confirmed.update(
                            tx["hash"]
                            for tx in client.get_block(tip)["transactions"][1:]
                        )
                        if {root_hash, replacement_hash} & confirmed:
                            break
                    winners = {root_hash, replacement_hash} & confirmed
                    if len(winners) != 1:
                        sample.update(
                            confirmation_winner=None,
                            confirmation_status=(
                                "incomplete" if not winners else "conflicting"
                            ),
                            confirmation_seconds=time.monotonic()
                            - confirmation_started,
                            confirmed_hashes=sorted(confirmed),
                        )
                        save_observations(node, "graph-confirmation", [sample])
                    assert len(winners) == 1, (
                        "expected one conflict confirmed within six blocks",
                        confirmed,
                    )
                    replacement_won = replacement_hash in confirmed
                    losing_hashes = (
                        old_hashes
                        if replacement_won
                        else {
                            replacement_hash,
                            transaction_hash(replacement_child),
                        }
                    )
                    workload.wait_for(
                        lambda: losing_hashes.isdisjoint(
                            workload.pool_transactions(client)
                        ),
                        "losing conflict and descendants leave accepted pool",
                    )
                    winning_bodies = (
                        [replacement, replacement_child] if replacement_won else bodies
                    )
                    valid_remaining = {
                        transaction_hash(tx) for tx in winning_bodies
                    } - confirmed
                    remaining = set(workload.pool_transactions(client))
                    assert remaining <= valid_remaining, (remaining, valid_remaining)
                    assert_pool(
                        node,
                        txs,
                        [
                            tx
                            for tx in winning_bodies
                            if transaction_hash(tx) in remaining
                        ],
                    )
                    sample.update(
                        confirmation_winner=(
                            "replacement" if replacement_won else "original"
                        ),
                        confirmation_status="confirmed",
                        confirmation_seconds=time.monotonic() - confirmation_started,
                        losing_hashes=sorted(losing_hashes),
                    )
                    probe = txs.build([4], nonce=1)
                    probe_hash = transaction_hash(probe)
                    assert client.send_transaction(probe) == probe_hash
                    assert (
                        self.Miner.miner_until_tx_committed(node, probe_hash)[
                            "tx_status"
                        ]["status"]
                        == "committed"
                    )
                    canonical_hashes, spent = set(), set()
                    for height in range(31, client.get_tip_block_number() + 1):
                        for tx in client.get_block_by_number(hex(height))[
                            "transactions"
                        ][1:]:
                            canonical_hashes.add(tx["hash"])
                            for value in tx["inputs"]:
                                point = value["previous_output"]
                                key = (point["tx_hash"], point["index"])
                                assert key not in spent, key
                                spent.add(key)
                    assert len({root_hash, replacement_hash} & canonical_hashes) == 1
                    assert losing_hashes.isdisjoint(canonical_hashes)
                    assert probe_hash in canonical_hashes
                    assert losing_hashes.isdisjoint(workload.pool_transactions(client))
                    sample.update(
                        confirmed_hashes=sorted(canonical_hashes),
                        probe_hash=probe_hash,
                        after=resources(node),
                    )

                    # 4. 小容量配置真实生效，置换只针对两个独立低费交易。
                    self.reset()
                    low = [txs.build([index], fee=1000) for index in (1, 2)]
                    large = txs.pad_to(
                        txs.build([3], fee=100000), sum(map(size_in_block, low))
                    )
                    path = Path(node.ckb_toml_path)
                    original = path.read_text()
                    workload.stop_and_assert_process_exit(node)
                    config = toml.loads(original)
                    config["tx_pool"]["max_tx_pool_size"] = sum(map(size_in_block, low))
                    path.write_text(toml.dumps(config))
                    node.start()
                    try:
                        for tx in low:
                            client.send_transaction(tx)
                        started = time.monotonic()
                        client.send_transaction(large)
                        sample["capacity_seconds"] = time.monotonic() - started
                        assert_pool(node, txs, [large])
                        save_observations(node, "graph-cost", [sample])
                    finally:
                        workload.stop_and_assert_process_exit(node)
                        path.write_text(original)
                        node.start()
        self.did_pass = True

    # TEST-MAP: TPF-04
    def test_recovery_and_reorg_have_separate_readiness_milestones(self):
        """
        1. 固定池集合和快照字节量，正常停止后独立解码。
        2. 分别记录进程可查询、完整池恢复和 indexer 精确 hash 追平时间。
        3. 真实分叉重组后重新记录池及索引完成点，核对 canonical 交易与可恢复独立项。
        """
        for node in (self.baseline, self.candidate, self.rich):
            # 1. 三种目标都使用相同输入集合，rich 作为单独维度记录。
            self.reset()
            client = node.getClient()
            txs = Transactions(node, 400)
            bodies = [txs.build([index]) for index in range(16)]
            for tx in bodies:
                client.send_transaction(tx)
            expected = set(map(transaction_hash, bodies))
            workload.stop_and_assert_process_exit(node)
            if list(Path(node.ckb_dir).rglob("persisted_data.v2")):
                _, saved, accepted, recovery = read_snapshot(node)
                snapshot_format = "v2"
            else:
                # The baseline's on-disk format is observed, never selected by version.
                paths = [
                    p
                    for p in Path(node.ckb_dir).rglob("persisted_data*")
                    if p.is_file() and p.suffix not in (".v2", ".tmp")
                ]
                assert len(paths) == 1, paths
                saved = paths[0].read_bytes()
                accepted, recovery = _transactions(saved), []
                snapshot_format = "transaction_vec"
            assert len(accepted) == 16 and recovery == []
            assert set(accepted) == set(map(packed_transaction, bodies))
            # 2. start 的项目等待也计入观测，不把端口就绪当作池恢复完成。
            started = time.monotonic()
            if node is self.rich:
                node.startWithRichIndexer()
            else:
                node.start()
            assert client.get_tip_header()["hash"] == self.base_hash
            rpc_ready = time.monotonic() - started
            workload.wait_for(
                lambda: set(workload.pool_transactions(client)) == expected,
                "all snapshot transactions recovered",
                60,
            )
            pool_ready = time.monotonic() - started
            workload.wait_for(
                lambda: (client.get_indexer_tip() or {}).get("block_hash")
                == self.base_hash,
                "indexer exact canonical hash",
                60,
            )
            index_ready = time.monotonic() - started
            assert_pool(node, txs, bodies)
            sample = {
                "snapshot_bytes": len(saved),
                "snapshot_format": snapshot_format,
                "rpc_ready_seconds": rpc_ready,
                "pool_ready_seconds": pool_ready,
                "indexer_ready_seconds": index_ready,
            }
            # 3. 目标先确认输入 0，来源构造更长的合法冲突分支并通过普通 peer 同步。
            original = bodies[0]
            template = client.get_block_template()
            template["transactions"] = []
            template["proposals"] = [transaction_hash(original)[:22]]
            proposed_tip = client.generate_block_with_template(template)
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == proposed_tip,
                "recovery probe proposal tip",
            )
            template = client.get_block_template()
            template["transactions"] = []
            template["proposals"] = []
            gap_tip = client.generate_block_with_template(template)
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == gap_tip,
                "recovery probe gap tip",
            )
            self.Miner.miner_until_tx_committed(node, transaction_hash(original))
            replacement = txs.build([0], fee=100000)
            self.relay.getClient().send_transaction(replacement)
            self.Miner.miner_until_tx_committed(
                self.relay, transaction_hash(replacement)
            )
            self.Miner.make_tip_height_number(
                self.relay, client.get_tip_block_number() + 2
            )
            wanted = self.relay.getClient().get_tip_header()
            started = time.monotonic()
            self.relay.connected(node)
            workload.wait_for(
                lambda: client.get_tip_header()["hash"] == wanted["hash"],
                "reorg chain ready",
                60,
            )
            sample["reorg_chain_seconds"] = time.monotonic() - started
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == wanted["hash"]
                and transaction_hash(original)
                not in workload.pool_transactions(client),
                "reorg pool ready",
                60,
            )
            sample["reorg_pool_seconds"] = time.monotonic() - started
            workload.wait_for(
                lambda: client.get_indexer_tip()
                == {"block_hash": wanted["hash"], "block_number": wanted["number"]},
                "reorg indexer ready",
                60,
            )
            sample["reorg_indexer_seconds"] = time.monotonic() - started
            assert (
                client.get_transaction(transaction_hash(replacement))["tx_status"][
                    "status"
                ]
                == "committed"
            )
            for tx in bodies[1:]:
                workload.assert_pool_transaction(
                    node, transaction_hash(tx), tx, workload.FAST_CYCLES
                )
            save_observations(
                node,
                "recovery-milestones",
                [sample],
                settings={"indexer": "rich" if node is self.rich else "regular"},
            )
        self.did_pass = True

    # TEST-MAP: TPF-05
    def test_repeated_operation_cycles_record_stable_state_resources(self):
        """
        1. 固定轮次、输入和冷却窗口，记录初始空池资源。
        2. 每轮提交、RBF、移除、出块和重连，再回到相同链顶与空池。
        3. 采集累计 CPU、RSS、线程、FD 和公开队列，保留趋势而不把缓存平台当泄漏。
        """
        for node in (self.baseline, self.candidate):
            # 1. 固定十轮，不按观察到的结果提前停止或选择性丢掉峰值。
            self.reset()
            samples = [resources(node)]
            client = node.getClient()
            # 2. 每轮回到同一 canonical tip 和相同逻辑成员状态。
            for round_number in range(10):
                txs = Transactions(node, 500 + round_number)
                first, replacement = txs.build([0]), txs.build([0], fee=100000)
                client.send_transaction(first)
                client.send_transaction(replacement)
                assert_pool(node, txs, [replacement])
                assert client.remove_transaction(transaction_hash(replacement)) is True
                self.relay.connected(node)
                workload.wait_for(
                    lambda: node.get_connected_count() == 1, "cycle peer connected"
                )
                client.remove_node(self.relay.get_peer_id())
                workload.wait_for(
                    lambda: node.get_connected_count() == 0, "cycle peer released"
                )
                template = client.get_block_template()
                template["transactions"] = []
                client.generate_block_with_template(template)
                client.truncate(self.base_hash)
                client.clear_tx_pool()
                workload.wait_for(
                    lambda: client.tx_pool_info()["tip_hash"] == self.base_hash,
                    "stable canonical state",
                )
                assert workload.pool_transactions(client) == {}
                time.sleep(0.5)
                # 3. 原始稳定窗口值包含 allocator/DB 缓存变化，未附加假定 RSS 归零断言。
                samples.append(resources(node))
            save_observations(
                node,
                "resource-trend",
                samples,
                settings={"rounds": 10, "cooldown_seconds": 0.5, "seed": 500},
            )
        self.did_pass = True

    # TEST-MAP: TPF-06
    def test_subscription_and_notify_modes_record_overhead_and_drops(self):
        """
        1. 在相同输入与速率下配置无订阅、正常订阅、慢订阅和超时通知四种模式。
        2. 采集请求/准入时序、CPU 和实际公开 drop 指标，健康订阅同时读取事件。
        3. 释放慢端并验证标记事件与新交易进展，保存模式间诊断数据。
        """
        for node in (self.baseline, self.candidate):
            for mode in ("none", "healthy", "slow", "notify_timeout"):
                # 1. 一次只改变通知模式；节点配置变更通过真实重启生效。
                self.reset()
                server = _HoldingServer() if mode == "notify_timeout" else None
                server_thread = None
                config_path = Path(node.ckb_toml_path)
                original = config_path.read_text()
                subscribers = []
                reader_stop = threading.Event()
                received, reader_errors = [], []
                reader = None
                try:
                    if server is not None:
                        server_thread = threading.Thread(
                            target=server.serve_forever, daemon=True
                        )
                        server_thread.start()
                        workload.stop_and_assert_process_exit(node)
                        config = toml.loads(original)
                        config["block_assembler"]["notify"] = [
                            f"http://127.0.0.1:{server.server_address[1]}/template"
                        ]
                        config["block_assembler"]["notify_timeout_millis"] = 500
                        config_path.write_text(toml.dumps(config))
                        node.start()
                    if mode != "none":
                        healthy = SubscriptionClient(self.tcp_ports[node])
                        subscribers.append(healthy)
                        healthy.call("subscribe", ["new_transaction"])

                        def read_events():
                            try:
                                while not reader_stop.is_set():
                                    received.extend(healthy.collect(0.1))
                            except Exception as error:
                                reader_errors.append(repr(error))

                        reader = threading.Thread(target=read_events, daemon=True)
                        reader.start()
                    if mode == "slow":
                        slow = SubscriptionClient(self.tcp_ports[node])
                        subscribers.append(slow)
                        slow.call("subscribe", ["new_transaction"])
                        # Do not read this TCP connection during the fixed event load.
                    txs = Transactions(node, 606)
                    bodies = [
                        txs.build([index], fee=100000, padding=20000)
                        for index in range(64)
                    ]
                    before, metrics_before = resources(node), _metrics(
                        self.metrics_ports[node]
                    )
                    # 2. 大事件负载与发送计划在各模式一致，不从 CPU 数据反推编码次数。
                    result = arrival_window(
                        node, self.relay, bodies, rate=100, ingress="local"
                    )
                    after, metrics_after = resources(node), _metrics(
                        self.metrics_ports[node]
                    )
                    assert result["summary"]["accepted"] == 64, result["summary"]
                    if server is not None:
                        workload.wait_for(
                            lambda: len(server.snapshot()[0]) > 0,
                            "real notify requests reached held endpoint",
                        )
                        workload.wait_for(
                            lambda: any(
                                item["closed_by_client"]
                                for item in server.snapshot()[0]
                            ),
                            "notify request actually timed out",
                            10,
                        )
                        server.release.set()
                    # 3. 每种有订阅模式的健康端都必须收到最终标记，慢端退出不留下停顿。
                    if mode == "slow":
                        subscribers.pop().close()
                    marker = txs.build([100], fee=100000, nonce=1)
                    marker_hash = node.getClient().send_transaction(marker)
                    workload.assert_pool_transaction(
                        node, marker_hash, marker, workload.FAST_CYCLES
                    )
                    if reader is not None:
                        workload.wait_for(
                            lambda: any(
                                healthy.transaction(item)["hash"] == marker_hash
                                for item in list(received)
                            ),
                            "healthy subscriber marker",
                            15,
                        )
                        assert not reader_errors, reader_errors
                    drops = lambda text: [
                        line
                        for line in text.splitlines()
                        if line.startswith("ckb_notify_transaction_dropped")
                    ]
                    save_observations(
                        node,
                        "notification-overhead",
                        [
                            {
                                "before": before,
                                "after": after,
                                "drop_metrics_before": drops(metrics_before),
                                "drop_metrics_after": drops(metrics_after),
                                "drop_metric_present": bool(drops(metrics_after)),
                                **result,
                            }
                        ],
                        settings={"mode": mode, "rate": 100, "events": 64},
                    )
                    assert not result["summary"]["functional_errors"], result["summary"]
                finally:
                    reader_stop.set()
                    if reader is not None:
                        reader.join(timeout=5)
                    for subscriber in subscribers:
                        subscriber.close()
                    if server is not None:
                        server.release.set()
                        server.shutdown()
                        server.server_close()
                        server_thread.join(timeout=5)
                        workload.stop_and_assert_process_exit(node)
                        config_path.write_text(original)
                        node.start()
        self.did_pass = True
