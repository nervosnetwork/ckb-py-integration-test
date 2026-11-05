"""External process faults with explicit snapshots, request outcomes and cleanup."""

import json
import os
import random
import select
import signal
import socket
import struct
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
    packed_transaction,
)
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash
from test_cases.tx_pool_refactor.test_pool_rpc_progress import pending_submission
from test_cases.tx_pool_refactor.tests.test_29_pr5316_persistence_replay import (
    _transactions,
)
from test_cases.tx_pool_refactor.tests.test_38_pr5316_template_notify import (
    _HoldingServer,
)


def read_snapshot(node):
    paths = list(Path(node.ckb_dir).rglob("persisted_data.v2"))
    assert len(paths) == 1, paths
    data = paths[0].read_bytes()
    assert data[:8] == b"CKBTPV2\0" and len(data) >= 24
    end = 16 + struct.unpack_from("<Q", data, 8)[0]
    assert end + 4 <= len(data)
    return paths[0], data, _transactions(data[16:end]), _transactions(data[end:])


class TestProcessResilience(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        try:
            for name in ("subject", "peer"):
                rpc, p2p = workload.unused_ports(2)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/process_resilience/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                cls.nodes.append(node)
                node.prepare()
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                config["network"]["connect_outbound_interval_secs"] = 0
                config["tx_pool"]["max_tx_verify_cycles"] = 1_000_000_000_000
                config["logger"]["filter"] = "info,ckb_script=debug,ckb_tx_pool=debug"
                path.write_text(toml.dumps(config))
                workload.add_genesis_test_cells(
                    node, program_names=("cpu_budget_stress_1b",)
                )
                path = Path(node.ckb_specs_config_path)
                spec = toml.loads(path.read_text())
                spec["params"]["max_block_cycles"] = 1_000_000_000_000
                path.write_text(toml.dumps(spec))
                node.start()
            cls.node, cls.peer = cls.nodes
            cls.Miner.make_tip_height_number(cls.peer, 30)
            workload.sync_blocks(cls.peer, cls.node, 1, 30)
            cls.base_hash = cls.peer.getClient().get_tip_header()["hash"]
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
            workload.wait_for(
                lambda: node.get_connected_count() == 0, "isolated process test"
            )
            if client.get_tip_header()["hash"] != self.base_hash:
                client.truncate(self.base_hash)
            client.clear_tx_pool()

    def teardown_method(self, method):
        pass

    # TEST-MAP: TCH-01
    def test_graceful_stop_reconciles_concurrent_requests_and_held_notify(self):
        """
        1. 启用真实阻塞通知端，确认已接收交易和通知请求在途。
        2. 并发提交独立交易时 SIGTERM，记录每个 HTTP 结局并确认旧 PID 退出。
        3. 独立解码快照；无 peer 重启，所有已返回成功交易恢复且 body 一致。
        4. 逐 hash 记录未完成请求结果，释放通知故障后接受新交易。
        """
        # 1. 新通知端只服务本用例，故障撤销和节点清理均有 finally。
        server = _HoldingServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config_path = Path(self.node.ckb_toml_path)
        original_config = config_path.read_text()
        executor = ThreadPoolExecutor(max_workers=4)
        try:
            workload.stop_and_assert_process_exit(self.node)
            config = toml.loads(original_config)
            config["block_assembler"]["notify"] = [
                f"http://127.0.0.1:{server.server_address[1]}/template"
            ]
            config["block_assembler"]["notify_timeout_millis"] = 15000
            config_path.write_text(toml.dumps(config))
            self.node.start()
            client = self.node.getClient()
            retained = workload.build_transaction(self.node, cell=0)
            retained_hash = client.send_transaction(retained)
            client.get_block_template()
            workload.wait_for(
                lambda: server.snapshot()[1] > 0, "template notification genuinely held"
            )
            txs = [
                workload.build_transaction(self.node, cell=index)
                for index in range(1, 5)
            ]
            barrier = threading.Barrier(5)

            def submit(tx):
                barrier.wait(timeout=10)
                started = time.monotonic()
                try:
                    response = requests.post(
                        client.url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "send_transaction",
                            "params": [tx, "passthrough"],
                        },
                        timeout=45,
                    )
                    return {
                        "status": response.status_code,
                        "body": response.text,
                        "started": started,
                        "finished": time.monotonic(),
                    }
                except requests.RequestException as error:
                    return {
                        "transport": type(error).__name__,
                        "started": started,
                        "finished": time.monotonic(),
                    }

            # 2. 提交工作与 SIGTERM 重叠；每个请求保留成功/拒绝/断连原始结果。
            futures = [executor.submit(submit, tx) for tx in txs]
            barrier.wait(timeout=10)
            previous = workload.stop_and_assert_process_exit(self.node)
            outcomes = [future.result(timeout=50) for future in futures]
            log = (Path(self.node.ckb_dir) / "node.log").read_text()
            assert "CKB shutdown" in log
            for failure in (
                "failed to save tx-pool",
                "skipping persistence",
                "tx-pool task failed while joining",
                "tx-pool publisher failed while draining",
                "tx-pool publisher exited before drain",
            ):
                assert failure not in log, log[-8000:]
            # 3. 快照必须包含已同步返回成功的交易，未完成请求不擅自记为成功。
            _, _, accepted, recovery = read_snapshot(self.node)
            completed = {retained_hash: retained}
            for tx, outcome in zip(txs, outcomes):
                if outcome.get("status") == 200:
                    body = json.loads(outcome["body"])
                    if "result" in body:
                        assert body["result"] == transaction_hash(tx)
                        completed[transaction_hash(tx)] = tx
            assert set(map(packed_transaction, completed.values())) <= set(accepted)
            assert recovery == []
            server.release.set()
            self.node.start()
            assert workload._node_process_id(self.node) != previous
            assert self.node.get_connected_count() == 0
            for tx_hash, tx in completed.items():
                workload.assert_pool_transaction(
                    self.node, tx_hash, tx, workload.FAST_CYCLES
                )
            # 4. 没收到成功响应的请求以恢复状态对账，新输入证明服务可继续使用。
            for tx, outcome in zip(txs, outcomes):
                status = client.get_transaction(transaction_hash(tx))
                if status["transaction"] is not None:
                    assert status["transaction"] == {**tx, "hash": transaction_hash(tx)}
                print(
                    json.dumps(
                        {
                            "hash": transaction_hash(tx),
                            "request": outcome,
                            "recovered": status,
                        }
                    )
                )
            marker = workload.build_transaction(self.node, cell=7)
            assert client.send_transaction(marker) == transaction_hash(marker)
            workload.assert_pool_transaction(
                self.node, transaction_hash(marker), marker, workload.FAST_CYCLES
            )
        finally:
            server.release.set()
            executor.shutdown(wait=True)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            config_path.write_text(original_config)
            # Apply removal of notify URLs before another method uses the node.
            workload.restart_and_assert_new_process(self.node)
        self.did_pass = True

    # TEST-MAP: TCH-02
    def test_sigkill_preserves_verified_prior_snapshot_boundary(self):
        """
        1. 正常保存并独立验证快照 S0，无 peer 重启确认其恢复。
        2. 接收内存新交易 S1 并发起慢请求，在真实执行标记后 SIGKILL。
        3. 重启核对 S0、数据库和所有可见 body，不要求 S1 的崩溃持久性。
        4. 原 S0 可确认，新独立交易仍能接受。
        """
        # 1. S0 的字节在故障前已保存且独立解码，不依赖退出码判断。
        client = self.node.getClient()
        s0 = workload.build_transaction(self.node, cell=0)
        s0_hash = client.send_transaction(s0)
        workload.stop_and_assert_process_exit(self.node)
        path, saved, accepted, recovery = read_snapshot(self.node)
        assert accepted == [packed_transaction(s0)] and recovery == []
        self.node.start()
        workload.assert_pool_transaction(self.node, s0_hash, s0, workload.FAST_CYCLES)
        # 2. 成功返回的 S1 只存在内存；慢 VM 日志证明杀进程发生于后续负载中。
        s1 = workload.build_transaction(self.node, cell=1)
        s1_hash = client.send_transaction(s1)
        slow = workload.build_budget_transaction(self.node, cell=2)
        log_path = Path(self.node.ckb_dir) / "node.log"
        offset = log_path.stat().st_size
        connection = pending_submission(self.node, slow)
        try:
            workload.wait_for(
                lambda: "CPU_STRESS_BEGIN_1B" in log_path.read_text()[offset:],
                "VM actually executing",
                15,
            )
            assert not select.select([connection], [], [], 0)[0]
            pid = workload._node_process_id(self.node)
            assert pid is not None
            assert path.read_bytes() == saved
            os.kill(pid, signal.SIGKILL)
            workload.wait_for(
                lambda: not workload._process_exists(pid), "SIGKILL target exited", 20
            )
            self.node.ckb_pid = -1
        finally:
            connection.close()
        # 3. 只有 S0 属于既有快照保证；所有可能恢复的交易必须是原字节。
        self.node.start()
        assert workload._node_process_id(self.node) != pid
        assert self.node.get_connected_count() == 0
        assert client.get_tip_header()["hash"] == self.base_hash
        workload.assert_pool_transaction(self.node, s0_hash, s0, workload.FAST_CYCLES)
        expected = {s0_hash: s0, s1_hash: s1, transaction_hash(slow): slow}
        pool = workload.pool_transactions(client)
        assert set(pool) <= set(expected)
        for tx_hash in pool:
            assert client.get_transaction(tx_hash)["transaction"] == {
                **expected[tx_hash],
                "hash": tx_hash,
            }
        print(
            {"S0": s0_hash, "S1": s1_hash, "restored": sorted(pool), "killed_pid": pid}
        )
        # 4. 数据库恢复可读还不够，恢复交易必须能通过完整出块路径。
        assert (
            self.Miner.miner_until_tx_committed(self.node, s0_hash)["tx_status"][
                "status"
            ]
            == "committed"
        )
        marker = workload.build_transaction(self.node, cell=7)
        marker_hash = client.send_transaction(marker)
        workload.assert_pool_transaction(
            self.node, marker_hash, marker, workload.FAST_CYCLES
        )
        self.did_pass = True

    # TEST-MAP: TCH-06
    def test_seeded_external_fault_sequence_reconciles_hashes(self):
        """
        1. 固定种子和操作集合，记录每个已完成外部步骤及请求结果。
        2. 交错客户端断连、清队列、移除、正常重启和真实分区/reorg。
        3. 每步核对集合，恢复全部故障后新标记交易可确认；失败保留首个失败前缀。
        """
        # 1. 固定每个操作的相对顺序，种子决定快交易输入与输出 nonce。
        rng = random.Random(5316)
        client = self.node.getClient()
        txs = Transactions(self.node, 600)
        trace = []
        path = Path(self.node.ckb_dir) / "fault-sequence.json"
        accepted = {}
        try:
            # 2. 序列包含确定可复现的故障；不把客户端未收到响应当成产品拒绝。
            for operation in (
                "disconnect",
                "clear_queue",
                "remove",
                "restart",
                "partition_reorg",
                "restart",
            ):
                event = {"operation": operation, "at": time.monotonic()}
                trace.append(event)
                if operation == "disconnect":
                    tx = txs.build(
                        [rng.choice([0, 1])], nonce=rng.randrange(1, 1 << 20)
                    )
                    connection = pending_submission(self.node, tx)
                    tx_hash = transaction_hash(tx)
                    # Never consume the HTTP response; observe enqueue/admission by hash
                    # before closing, so an unsent request is not called a cancellation.
                    try:
                        workload.wait_for(
                            lambda: tx_hash in workload.pool_transactions(client),
                            "request processed before discarding its response",
                            30,
                        )
                    finally:
                        # Connection: close permits the server to close first.
                        connection.close()
                    workload.wait_for(
                        lambda: tx_hash in workload.pool_transactions(client),
                        "disconnected request reconciled",
                        30,
                    )
                    accepted[tx_hash] = tx
                    event["hash"] = tx_hash
                elif operation == "clear_queue":
                    client.clear_tx_verify_queue()
                elif operation == "remove":
                    tx_hash = next(iter(accepted))
                    assert client.remove_transaction(tx_hash) is True
                    accepted.pop(tx_hash)
                    event["removed"] = tx_hash
                elif operation == "restart":
                    workload.restart_and_assert_new_process(self.node)
                    workload.wait_for(
                        lambda: set(workload.pool_transactions(client))
                        == set(accepted),
                        "exact restart set",
                    )
                else:
                    assert (
                        self.node.get_connected_count()
                        == self.peer.get_connected_count()
                        == 0
                    )
                    old = txs.build([2], fee=1000)
                    new = txs.build([2], fee=2000)
                    client.send_transaction(old)
                    self.Miner.miner_until_tx_committed(
                        self.node, transaction_hash(old)
                    )
                    other = self.peer.getClient()
                    other.send_transaction(new)
                    self.Miner.miner_until_tx_committed(
                        self.peer, transaction_hash(new)
                    )
                    self.Miner.make_tip_height_number(
                        self.peer, client.get_tip_block_number() + 2
                    )
                    wanted = other.get_tip_header()["hash"]
                    self.node.connected(self.peer)
                    workload.wait_for(
                        lambda: client.get_tip_header()["hash"] == wanted,
                        "reorg after partition",
                        60,
                    )
                    workload.wait_for(
                        lambda: client.tx_pool_info()["tip_hash"] == wanted,
                        "pool reconciled after reorg",
                        30,
                    )
                    assert (
                        client.get_transaction(transaction_hash(new))["tx_status"][
                            "status"
                        ]
                        == "committed"
                    )
                    assert transaction_hash(old) not in workload.pool_transactions(
                        client
                    )
                    client.remove_node(self.peer.get_peer_id())
                    workload.wait_for(
                        lambda: self.node.get_connected_count() == 0,
                        "partition fixture removed",
                    )
                    event["canonical"] = wanted
                # 3. 每步保存最早失败前缀，收据包含请求 hash 和最终状态。
                assert set(workload.pool_transactions(client)) == set(accepted)
                event["pool"] = sorted(accepted)
                path.write_text(json.dumps({"seed": 5316, "trace": trace}, indent=2))
            marker = txs.build([7], nonce=rng.randrange(1, 1 << 20))
            marker_hash = client.send_transaction(marker)
            assert (
                self.Miner.miner_until_tx_committed(self.node, marker_hash)[
                    "tx_status"
                ]["status"]
                == "committed"
            )
        finally:
            path.write_text(
                json.dumps({"seed": 5316, "first_failure_prefix": trace}, indent=2)
            )
            print(path.read_text())
            for peer in client.get_peers():
                client.remove_node(peer["node_id"])
        self.did_pass = True
