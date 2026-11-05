"""Ordinary peers under costly, duplicate, conflicting and churned traffic."""

import copy
import json
import os
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import toml
import requests

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import Transactions, size_in_block
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash
from test_cases.tx_pool_refactor.observations import resources, save_observations
from test_cases.tx_pool_refactor.tcp_response_gate import ResponseGate
from test_cases.tx_pool_refactor.test_capacity_metrics import (
    _metric,
    _metrics,
)


class TestPeerResilience(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        cls.ports = {}
        try:
            for name in ("target", "primary", "backup", "honest"):
                rpc, p2p, metrics = workload.unused_ports(3)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/peer_resilience/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                cls.nodes.append(node)
                cls.ports[name] = (p2p, metrics)
                node.prepare()
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                config["network"]["connect_outbound_interval_secs"] = 0
                config["tx_pool"].update(
                    max_tx_verify_cycles=1_000_000_000_000,
                    max_tx_verify_workers=2,
                    min_rbf_rate=2000,
                )
                config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
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
                spec["params"]["max_block_cycles"] = 1_000_000_000_000
                path.write_text(toml.dumps(spec))
                node.start()
            cls.target, cls.primary, cls.backup, cls.honest = cls.nodes
            cls.Miner.make_tip_height_number(cls.honest, 30)
            for node in cls.nodes[:-1]:
                workload.sync_blocks(cls.honest, node, 1, 30)
            cls.base_hash = cls.honest.getClient().get_tip_header()["hash"]
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
            for peer in client.get_peers():
                client.remove_node(peer["node_id"])
        for node in self.nodes:
            workload.wait_for(
                lambda: node.get_connected_count() == 0, "all peers partitioned"
            )
            client = node.getClient()
            if client.get_tip_header()["hash"] != self.base_hash:
                client.truncate(self.base_hash)
            client.clear_tx_pool()

    def teardown_method(self, method):
        pass

    # TEST-MAP: NTD-53
    def test_primary_disconnect_after_actual_request_uses_backup(self):
        """
        1. 用透明 TCP 延迟链路连接主来源，预先在备用来源保存同一合法交易。
        2. 主来源日志证实收到目标请求后阻断下行，证明响应字节滞留且目标未入池。
        3. 接入备用来源并断开主来源，要求备用收到真实请求、原 body 入池且独立交易继续。
        """
        # 1. 只转发真实节点字节；延迟给请求观察和阻断留出确定窗口。
        tx = workload.build_transaction(self.primary, cell=0)
        tx_hash = transaction_hash(tx)
        assert self.backup.getClient().send_transaction(tx) == tx_hash
        gate = ResponseGate(self.ports["primary"][0], delay=1.0)
        client = self.target.getClient()
        try:
            client.add_node(
                self.primary.get_peer_id(), f"/ip4/127.0.0.1/tcp/{gate.port}"
            )
            workload.wait_for(
                lambda: self.primary.get_peer_id()
                in {p["node_id"] for p in client.get_peers()},
                "proxied real peer",
                30,
            )
            assert self.primary.getClient().send_transaction(tx) == tx_hash
            # 2. 请求归属来自响应端 hash 日志；没有真正拦住响应就按夹具失败。
            workload.wait_for(
                lambda: workload.transaction_requests(self.primary, tx_hash),
                "primary received target request",
                30,
            )
            gate.held.set()
            workload.wait_for(
                lambda: gate.pending_bytes > 0, "response bytes held by proxy", 5
            )
            assert tx_hash not in workload.pool_transactions(
                client
            ), "fixture not reached: primary body already admitted"
            assert gate.errors == []
            self.backup.connected(self.target)
            workload.wait_for(
                lambda: self.backup.get_peer_id()
                in {p["node_id"] for p in client.get_peers()},
                "backup connected",
            )
            assert tx_hash not in workload.pool_transactions(
                client
            ), "fixture not reached: response completed before disconnect"
            # 3. 原主请求已发生，后备请求也必须实际发生；不以已持有同一 hash 代替。
            client.remove_node(self.primary.get_peer_id())
            gate.close()
            workload.wait_for(
                lambda: workload.transaction_requests(self.backup, tx_hash),
                "backup received target request",
                60,
            )
            workload.assert_pool_transaction(
                self.target, tx_hash, tx, workload.FAST_CYCLES
            )
            marker = workload.build_transaction(self.backup, cell=7)
            marker_hash = self.backup.getClient().send_transaction(marker)
            workload.assert_pool_transaction(
                self.target, marker_hash, marker, workload.FAST_CYCLES
            )
            print(
                {
                    "primary_requests": workload.transaction_requests(
                        self.primary, tx_hash
                    ),
                    "backup_requests": workload.transaction_requests(
                        self.backup, tx_hash
                    ),
                }
            )
        finally:
            gate.close()
        self.did_pass = True

    # TEST-MAP: TMN-01
    # TEST-MAP: TPF-02
    def test_costly_remote_load_preserves_honest_progress_and_settles(self):
        """
        1. 记录无压力本地/只读延迟，预先接收独立 Accepted 并连接三个真实 peer。
        2. 两来源本地验证后广播昂贵合法交易，在目标 active 期间提交诚实标记和有效块。
        3. 核对逐 hash 预算拒绝、无误 ban、原 Accepted 保持和停止负载后排空，保存诊断时序。
        """
        client = self.target.getClient()
        samples = []
        # 1. 容量充分；无压力对照与繁忙样本在相同节点上观测。
        control = workload.build_transaction(self.target, cell=6)
        started = time.monotonic()
        control_hash = client.send_transaction(control)
        client.get_tip_header()
        samples.append(
            {"phase": "idle", "local_and_read_seconds": time.monotonic() - started}
        )
        for node in (self.primary, self.backup, self.honest):
            node.connected(self.target)
        workload.wait_for(
            lambda: self.target.get_connected_count() == 3, "three real peers connected"
        )
        slow = [
            workload.build_budget_transaction(node, cell=index)
            for index, node in enumerate((self.primary, self.backup))
        ]
        executor = ThreadPoolExecutor(max_workers=2)

        def submit_source(node, tx):
            tx_hash = transaction_hash(tx)
            response = requests.post(
                node.rpcUrl,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "send_transaction",
                    "params": [tx, "passthrough"],
                },
                timeout=workload.BUDGET_RPC_TIMEOUT,
            )
            assert response.status_code in (200, 408), (
                response.status_code,
                response.text,
            )
            if response.status_code == 200:
                assert response.json().get("result") == tx_hash, response.text
            # A service timeout does not cancel local verification; never resubmit.
            workload.wait_for(
                lambda: tx_hash in workload.pool_transactions(node.getClient()),
                "source original request admitted",
                workload.BUDGET_RPC_TIMEOUT,
            )
            assert node.getClient().get_transaction(tx_hash)["transaction"] == {
                **tx,
                "hash": tx_hash,
            }
            print(
                {
                    "source": node.rpcUrl,
                    "hash": tx_hash,
                    "http_status": response.status_code,
                }
            )
            return tx_hash

        futures = [
            executor.submit(submit_source, node, tx)
            for node, tx in zip((self.primary, self.backup), slow)
        ]
        metrics_port = self.ports["target"][1]
        try:
            # 2. 只有公开 active 确认繁忙后才计入公平性观察。
            workload.wait_for(
                lambda: _metric(
                    _metrics(metrics_port),
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "active_work",
                )
                > 0,
                "remote load actually executing",
                workload.BUDGET_RPC_TIMEOUT,
            )
            started = time.monotonic()
            local = workload.build_transaction(self.target, cell=4)
            local_hash = client.send_transaction(local)
            assert client.get_transaction(local_hash)["transaction"] == {
                **local,
                "hash": local_hash,
            }
            samples.append(
                {"phase": "busy", "local_seconds": time.monotonic() - started}
            )
            started = time.monotonic()
            assert (
                client.call("get_tip_header", [], try_count=1, timeout=10)["hash"]
                == self.base_hash
            )
            samples.append(
                {"phase": "busy", "read_seconds": time.monotonic() - started}
            )
            marker = workload.build_transaction(self.honest, cell=5)
            marker_hash = self.honest.getClient().send_transaction(marker)
            workload.assert_pool_transaction(
                self.target, marker_hash, marker, workload.FAST_CYCLES
            )
            template = self.honest.getClient().get_block_template()
            template["transactions"] = []
            next_tip = self.honest.getClient().generate_block_with_template(template)
            workload.wait_for(
                lambda: client.get_tip_header()["hash"] == next_tip,
                "honest chain progress under load",
                60,
            )
            # 3. 默认 8 秒是 CPU 上限；拒绝证据是目标 hash/source，非墙钟计时。
            for tx, future in zip(slow, futures):
                assert future.result(
                    timeout=workload.BUDGET_RPC_TIMEOUT
                ) == transaction_hash(tx)
                workload.assert_remote_budget_refusal(self.target, transaction_hash(tx))
            workload.wait_for(
                lambda: _metric(
                    _metrics(metrics_port),
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "active_work",
                )
                == 0,
                "remote work drained",
                60,
            )
            workload.wait_for(
                lambda: _metric(
                    _metrics(metrics_port),
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "total_entries",
                )
                == 0,
                "pipeline residency drained",
                30,
            )
            workload.assert_pool_transaction(
                self.target, control_hash, control, workload.FAST_CYCLES
            )
            assert client.get_banned_addresses() == []
            samples.append(
                {
                    "phase": "settled",
                    "resources": resources(self.target),
                    "metrics": _metrics(metrics_port),
                }
            )
            save_observations(
                self.target,
                "busy-fairness",
                samples,
                settings={
                    "workers": 2,
                    "cpu_cap": "default",
                    "performance_thresholds": None,
                },
            )
        finally:
            executor.shutdown(wait=True)
        self.did_pass = True

    # TEST-MAP: TMN-03
    def test_valid_conflicting_peers_obey_rbf_and_preserve_honest_transaction(self):
        """
        1. 接收第一来源的合法低费交易及诚实独立交易。
        2. 第二来源广播不合格替换和合格替换，检查实际 Remote 拒绝与最终赢家。
        3. 并发来源再发送同输入交易，池与最终区块均保持输入唯一，诚实交易可确认。
        """
        # 1. 同链输入、正确脚本；拒绝只能来自池策略而非签名或 resolve。
        txs = Transactions(self.target, 3)
        a = txs.build([0], fee=1000)
        honest = txs.build([7])
        for source in (self.primary, self.honest):
            source.connected(self.target)
        self.primary.getClient().send_transaction(a)
        self.honest.getClient().send_transaction(honest)
        workload.assert_pool_transaction(
            self.target, transaction_hash(a), a, workload.FAST_CYCLES
        )
        workload.assert_pool_transaction(
            self.target, transaction_hash(honest), honest, workload.FAST_CYCLES
        )
        # 2. 第二来源自己的池未占用该输入，低费候选能够真正从网络到达目标。
        low, high = txs.build([0], fee=1100), txs.build([0], fee=10000)
        assert self.backup.get_connected_count() == 0
        self.backup.getClient().send_transaction(low)
        self.backup.connected(self.target)
        target_log = Path(self.target.ckb_dir) / "node.log"

        def rejected_remotely():
            for line in target_log.read_text().splitlines():
                start = line.find("{")
                if start < 0:
                    continue
                try:
                    value = json.loads(line[start:])
                except json.JSONDecodeError:
                    continue
                if (
                    value.get("hash") == transaction_hash(low)[2:]
                    and value.get("source") == "remote"
                    and value.get("reason", "").startswith("RBFRejected(")
                ):
                    return value
            return None

        workload.wait_for(rejected_remotely, "actual remote RBF rejection", 60)
        assert transaction_hash(a) in workload.pool_transactions(
            self.target.getClient()
        )
        self.backup.getClient().send_transaction(high)
        workload.assert_pool_transaction(
            self.target, transaction_hash(high), high, workload.FAST_CYCLES
        )
        assert transaction_hash(a) not in workload.pool_transactions(
            self.target.getClient()
        )
        # 3. 同时发送的两个有效变体允许策略内赢家，但绝不允许双花同时存在。
        concurrent = [txs.build([2], fee=5000, nonce=index) for index in (1, 2)]
        for source in (self.primary, self.backup):
            source.getClient().remove_node(self.target.get_peer_id())
            workload.wait_for(
                lambda: source.get_connected_count() == 0,
                "independent concurrent source",
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(source.getClient().send_transaction, tx)
                for source, tx in zip((self.primary, self.backup), concurrent)
            ]
            for tx, future in zip(concurrent, futures):
                assert future.result(timeout=30) == transaction_hash(tx)
            connections = [
                executor.submit(source.connected, self.target)
                for source in (self.primary, self.backup)
            ]
            for connection in connections:
                connection.result(timeout=30)
        for source, tx in zip((self.primary, self.backup), concurrent):
            workload.wait_for(
                lambda: workload.transaction_requests(source, transaction_hash(tx)),
                "concurrent body actually requested",
                60,
            )
        candidates = set(map(transaction_hash, concurrent))
        workload.wait_for(
            lambda: bool(
                candidates & set(workload.pool_transactions(self.target.getClient()))
            ),
            "one concurrent winner",
        )
        pool = workload.pool_transactions(self.target.getClient())
        assert len(candidates & set(pool)) == 1
        committed = self.Miner.miner_until_tx_committed(
            self.target, transaction_hash(honest)
        )
        block = self.target.getClient().get_block(committed["tx_status"]["block_hash"])
        inputs = [
            (i["previous_output"]["tx_hash"], i["previous_output"]["index"])
            for tx in block["transactions"][1:]
            for i in tx["inputs"]
        ]
        assert len(inputs) == len(set(inputs))
        assert self.target.getClient().get_banned_addresses() == []
        self.did_pass = True

    # TEST-MAP: TMN-04
    def test_connection_churn_preserves_honest_relay_and_releases_peers(self):
        """
        1. 固定诚实连接，记录资源和实际 peer 集合。
        2. 两扰动来源按固定间隔轮流连接、传播、断开，同时检查诚实链/RPC 进展。
        3. 停止扰动，验证只剩诚实 peer、队列排空和新交易传播，记录资源趋势。
        """
        # 1. 连接数最多三条，明显低于默认上限，不测试连接上限政策。
        self.honest.connected(self.target)
        workload.wait_for(
            lambda: self.target.get_connected_count() == 1, "honest peer ready"
        )
        before = resources(self.target)
        txs = Transactions(self.target, 4)
        samples = [before]
        client = self.target.getClient()
        # 2. 每轮使用不同 raw hash，避免 known-hash 过滤伪装连接健康。
        for round_number in range(6):
            source = (self.primary, self.backup)[round_number % 2]
            source.connected(self.target)
            workload.wait_for(
                lambda: source.get_peer_id()
                in {p["node_id"] for p in client.get_peers()},
                "churn peer connected",
            )
            tx = txs.build([0], fee=1000, nonce=round_number)
            tx_hash = source.getClient().send_transaction(tx)
            workload.assert_pool_transaction(
                self.target, tx_hash, tx, workload.FAST_CYCLES
            )
            client.remove_node(source.get_peer_id())
            workload.wait_for(
                lambda: {p["node_id"] for p in client.get_peers()}
                == {self.honest.get_peer_id()},
                "churn peer removed",
            )
            # Retire the prior same-input workload on every connected copy.
            for node in self.nodes:
                if tx_hash in workload.pool_transactions(node.getClient()):
                    assert node.getClient().remove_transaction(tx_hash) is True
            assert client.call("get_tip_header", [], try_count=1, timeout=5)
            template = self.honest.getClient().get_block_template()
            template["transactions"] = []
            tip = self.honest.getClient().generate_block_with_template(template)
            workload.wait_for(
                lambda: client.get_tip_header()["hash"] == tip,
                "honest chain advances during churn",
            )
            samples.append(resources(self.target))
            time.sleep(0.25)
        # 3. 资源只记录诊断值；未冻结 FD/RSS 增幅阈值，不伪报性能达标。
        marker = txs.build([7])
        marker_hash = self.honest.getClient().send_transaction(marker)
        workload.assert_pool_transaction(
            self.target, marker_hash, marker, workload.FAST_CYCLES
        )
        assert {p["node_id"] for p in client.get_peers()} == {self.honest.get_peer_id()}
        workload.wait_for(
            lambda: int(client.tx_pool_info()["orphan"], 16) == 0,
            "churn orphan queue empty",
        )
        time.sleep(1)
        samples.append(resources(self.target))
        save_observations(
            self.target,
            "connection-churn",
            samples,
            settings={"rounds": 6, "interval_seconds": 0.25},
        )
        self.did_pass = True

    # TEST-MAP: TCH-04
    def test_partition_reorg_converges_on_more_work_with_valid_pool(self):
        """
        1. 用实际 peer 集合证明分区，两边分别确认同输入的合法冲突交易。
        2. 构造累计工作量更高的分支并恢复连接，等待链、池、indexer 的 hash 收敛。
        3. 核对 canonical 输入唯一、旧冲突退出，新独立交易可确认。
        """
        # 1. 默认 dummy 链恒定难度，仍按 compact target 独立计算累计工作。
        assert all(node.get_connected_count() == 0 for node in self.nodes)
        txs = Transactions(self.target, 404)
        left, right = txs.build([0], fee=1000), txs.build([0], fee=2000)
        self.target.getClient().send_transaction(left)
        self.primary.getClient().send_transaction(right)
        self.Miner.miner_until_tx_committed(self.target, transaction_hash(left))
        self.Miner.miner_until_tx_committed(self.primary, transaction_hash(right))
        self.Miner.make_tip_height_number(
            self.primary, self.target.getClient().get_tip_block_number() + 2
        )

        def work(node):
            total = 0
            for height in range(31, node.getClient().get_tip_block_number() + 1):
                header = node.getClient().get_header_by_number(hex(height), "0x1")
                target, overflow = self.Miner.compact_to_target(
                    int(header["compact_target"], 16)
                )
                assert not overflow and target > 0
                total += (1 << 256) // (target + 1)
            return total

        assert work(self.primary) > work(self.target)
        # 2. 恢复真实连接，不用逐块 RPC 代替网络分区恢复。
        tip = self.primary.getClient().get_tip_header()
        self.target.connected(self.primary)
        for node in (self.target, self.primary):
            client = node.getClient()
            workload.wait_for(
                lambda: client.get_tip_header()["hash"] == tip["hash"],
                "chain converged after partition",
                60,
            )
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == tip["hash"],
                "pool follows winning chain",
            )
            workload.wait_for(
                lambda: client.get_indexer_tip()
                == {"block_hash": tip["hash"], "block_number": tip["number"]},
                "indexer follows winning hash",
                60,
            )
            assert transaction_hash(left) not in workload.pool_transactions(client)
            assert (
                client.get_transaction(transaction_hash(right))["tx_status"]["status"]
                == "committed"
            )
        # 3. 全部新 canonical 块的非 cellbase 输入去重，新交易可正常处理。
        inputs = []
        for height in range(31, int(tip["number"], 16) + 1):
            for tx in self.target.getClient().get_block_by_number(hex(height))[
                "transactions"
            ][1:]:
                inputs.extend(
                    (i["previous_output"]["tx_hash"], i["previous_output"]["index"])
                    for i in tx["inputs"]
                )
        assert len(inputs) == len(set(inputs))
        marker = txs.build([7])
        marker_hash = self.target.getClient().send_transaction(marker)
        assert (
            self.Miner.miner_until_tx_committed(self.target, marker_hash)["tx_status"][
                "status"
            ]
            == "committed"
        )
        self.did_pass = True

    # TEST-MAP: TCH-05
    def test_sigstop_resume_follows_chain_and_accepts_new_transactions(self):
        """
        1. 记录本轮 PID 并 SIGSTOP，以进程状态确认实际暂停。
        2. 其他真实节点继续出块和接收交易，暂停节点不要求响应。
        3. finally 中 SIGCONT，恢复连接后核对链/池追平和新交易完成，记录恢复时间。
        """
        # 1. PID 通过 RPC 端口和 cwd 验证，只操作本轮实例。
        pid = workload._node_process_id(self.target)
        assert pid is not None
        os.kill(pid, signal.SIGSTOP)
        try:
            state = subprocess.run(
                ["ps", "-o", "state=", "-p", str(pid)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            assert "T" in state, state
            # 2. 停止窗口内独立节点仍推进链；不向暂停的 RPC 发送长等待请求。
            self.Miner.make_tip_height_number(self.honest, 33)
            marker = workload.build_transaction(self.honest, cell=6)
            marker_hash = self.honest.getClient().send_transaction(marker)
            time.sleep(1)
        finally:
            os.kill(pid, signal.SIGCONT)
        # 3. 恢复耗时是诊断值，不从墙钟暂停推导 CPU 预算性质。
        started = time.monotonic()
        self.honest.connected(self.target)
        wanted = self.honest.getClient().get_tip_header()["hash"]
        workload.wait_for(
            lambda: self.target.getClient().get_tip_header()["hash"] == wanted,
            "resumed node catches up",
            60,
        )
        workload.assert_pool_transaction(
            self.target, marker_hash, marker, workload.FAST_CYCLES
        )
        local = workload.build_transaction(self.target, cell=7)
        local_hash = self.target.getClient().send_transaction(local)
        workload.assert_pool_transaction(
            self.target, local_hash, local, workload.FAST_CYCLES
        )
        assert workload._node_process_id(self.target) == pid
        print(
            {
                "resumed_pid": pid,
                "recovery_seconds": time.monotonic() - started,
                "tip": wanted,
            }
        )
        self.did_pass = True
