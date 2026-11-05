"""External process faults with explicit snapshots, request outcomes and cleanup."""

import http.client
import json
import os
import select
import signal
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import (
    packed_transaction,
)
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash
from test_cases.tx_pool_refactor.rpc_progress_fixture import pending_submission
from test_cases.tx_pool_refactor.test_persistence_replay import (
    _transactions,
)
from test_cases.tx_pool_refactor.extended.notifications import (
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


def process_identity(pid):
    """Match a live process by its start time and actual working directory."""
    if not workload._process_exists(pid):
        return None
    started = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
        timeout=5,
    )
    cwd = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    if not workload._process_exists(pid):
        return None
    assert started.returncode == cwd.returncode == 0, (started.stderr, cwd.stderr)
    directories = [
        str(Path(line[1:]).resolve())
        for line in cwd.stdout.splitlines()
        if line.startswith("n")
    ]
    assert len(directories) == 1 and started.stdout.strip(), (
        started.stdout,
        cwd.stdout,
    )
    return directories[0], started.stdout.strip()


def signal_owned_process(pid, identity, sig):
    if process_identity(pid) != identity:
        return
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


class TestProcessResilience(CkbTest):
    def setup_method(self, method):
        self.did_pass = None
        self.nodes = []
        self.processes = []
        try:
            for name in ("subject", "peer"):
                rpc, p2p = workload.unused_ports(2)
                node = self.CkbNode.init_dev_by_port(
                    self.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/process_resilience/{method.__name__}/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                self.nodes.append(node)
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
                self.record_process(node)
            self.node, self.peer = self.nodes
            self.Miner.make_tip_height_number(self.peer, 30)
            workload.sync_blocks(self.peer, self.node, 1, 30)
            self.base_hash = self.peer.getClient().get_tip_header()["hash"]
        except Exception:
            self.teardown_method(method)
            raise

    def record_process(self, node):
        pid = workload._node_process_id(node)
        assert pid is not None
        identity = process_identity(pid)
        assert identity is not None and identity[0] == str(Path(node.ckb_dir).resolve())
        self.processes.append((pid, identity))

    def teardown_method(self, method):
        # RPC 已关闭的进程也按启动时间和 cwd 识别；单个清理错误不打断其余节点。
        errors = []
        for pid, identity in self.processes:
            try:
                signal_owned_process(pid, identity, signal.SIGTERM)
                try:
                    workload.wait_for(
                        lambda: process_identity(pid) != identity,
                        "owned process exited",
                        workload.BUDGET_RPC_TIMEOUT,
                    )
                except AssertionError:
                    signal_owned_process(pid, identity, signal.SIGKILL)
                    workload.wait_for(
                        lambda: process_identity(pid) != identity,
                        "owned process killed",
                        20,
                    )
                    errors.append((pid, "cleanup required SIGKILL"))
            except Exception as error:
                errors.append((pid, repr(error)))
        for node in reversed(self.nodes):
            try:
                if workload._node_process_id(node) is not None:
                    workload.stop_and_assert_process_exit(node)
                workload.save_node_logs(node)
                node.clean()
            except Exception as error:
                errors.append((node.ckb_dir, repr(error)))
        assert not errors, errors

    # TEST-MAP: TCH-01
    def test_graceful_stop_reconciles_concurrent_requests_and_held_notify(self):
        """
        1. 启用真实阻塞通知端，确认已接收交易和通知请求在途。
        2. 确认慢交易在 VM 中执行且 HTTP 未返回，再并发提交独立交易并 SIGTERM。
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
        slow_connection = None
        try:
            workload.stop_and_assert_process_exit(self.node)
            config = toml.loads(original_config)
            config["block_assembler"]["notify"] = [
                f"http://127.0.0.1:{server.server_address[1]}/template"
            ]
            config["block_assembler"]["notify_timeout_millis"] = 15000
            config_path.write_text(toml.dumps(config))
            self.node.start()
            self.record_process(self.node)
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

            # 2. 新日志标记和未返回的 HTTP 证明服务端真实在途，再施加 SIGTERM。
            slow = workload.build_budget_transaction(self.node, cell=5)
            log_path = Path(self.node.ckb_dir) / "node.log"
            offset = log_path.stat().st_size
            slow_started = time.monotonic()
            slow_connection = pending_submission(self.node, slow)
            workload.wait_for(
                lambda: "CPU_STRESS_BEGIN_1B" in log_path.read_text()[offset:],
                "VM executing before SIGTERM",
                15,
            )
            assert not select.select([slow_connection], [], [], 0)[0]
            assert transaction_hash(slow) not in workload.pool_transactions(client)
            futures = [executor.submit(submit, tx) for tx in txs]
            barrier.wait(timeout=10)
            assert not select.select([slow_connection], [], [], 0)[0]
            assert server.snapshot()[1] > 0
            print(
                {
                    "vm_started_before_sigterm": transaction_hash(slow),
                    "http_pending": True,
                    "notify_held": True,
                }
            )
            previous = workload._node_process_id(self.node)
            assert previous is not None
            os.kill(previous, signal.SIGTERM)
            # 活跃 Local 验证可在停机时排空；使用同一有限工作负载的完成期限。
            workload.wait_for(
                lambda: not workload._process_exists(previous),
                "SIGTERM with in-flight VM completed",
                workload.BUDGET_RPC_TIMEOUT,
            )
            assert workload._node_process_id(self.node) is None
            outcomes = [future.result(timeout=50) for future in futures]
            # 旧 PID 已退出，读取在途请求的真实最终响应；成功响应也必须进入快照保证。
            response = http.client.HTTPResponse(slow_connection)
            try:
                response.begin()
                slow_outcome = {
                    "status": response.status,
                    "body": response.read().decode(),
                    "started": slow_started,
                    "finished": time.monotonic(),
                }
            except (OSError, http.client.HTTPException) as error:
                slow_outcome = {
                    "transport": type(error).__name__,
                    "started": slow_started,
                    "finished": time.monotonic(),
                }
            finally:
                response.close()
            txs.append(slow)
            outcomes.append(slow_outcome)
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
            print(
                {
                    "snapshot_accepted_count": len(accepted),
                    "snapshot_recovery_count": len(recovery),
                    "successful_response_hashes": sorted(completed),
                    "slow_request": slow_outcome,
                }
            )
            assert set(map(packed_transaction, completed.values())) <= set(accepted)
            assert recovery == []
            server.release.set()
            self.node.start()
            self.record_process(self.node)
            assert workload._node_process_id(self.node) != previous
            assert self.node.get_connected_count() == 0
            workload.wait_for(
                lambda: set(completed) <= set(workload.pool_transactions(client)),
                "all successful responses replayed after restart",
                workload.BUDGET_RPC_TIMEOUT,
            )
            for tx_hash, tx in completed.items():
                if tx_hash == transaction_hash(slow):
                    restored = client.get_transaction(tx_hash)
                    assert restored["tx_status"]["status"] in ("pending", "proposed")
                    assert restored["transaction"] == {**tx, "hash": tx_hash}
                    assert tx_hash in workload.pool_transactions(client)
                else:
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
            if slow_connection is not None:
                slow_connection.close()
            server.release.set()
            executor.shutdown(wait=True)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            config_path.write_text(original_config)
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
        self.record_process(self.node)
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
        self.record_process(self.node)
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
