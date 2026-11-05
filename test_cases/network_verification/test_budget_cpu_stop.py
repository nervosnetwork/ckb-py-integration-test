"""Single-VM interruption and CPU quiescence after a real network budget refusal."""

import sys
import threading
import time
from pathlib import Path
from unittest import SkipTest

import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.network_verification.cpu_usage import process_cpu_seconds
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash


class TestBudgetCpuStop(CkbTest):
    @classmethod
    def setup_class(cls):
        if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
            raise SkipTest("Process CPU observation requires Linux or macOS")
        cls.nodes = []
        cls.pids = {}
        try:
            # 两个独立节点均保持默认 CPU 上限；完整执行对照走本地入口。
            for role in ("source", "limited"):
                rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"network_verification/TestBudgetCpuStop/{role}_{rpc_port}",
                    rpc_port,
                    p2p_port,
                )
                cls.nodes.append(node)
                setattr(cls, role, node)
                node.prepare()
                config_path = Path(node.ckb_toml_path)
                config = toml.loads(config_path.read_text())
                config["tx_pool"].update(
                    max_tx_verify_cycles=1_000_000_000_000,
                    keep_rejected_tx_hashes_days=1,
                    keep_rejected_tx_hashes_count=1000,
                )
                config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
                config["logger"][
                    "filter"
                ] = "info,ckb_tx_pool=debug,ckb_relay=trace,ckb_script=debug"
                config["network"]["connect_outbound_interval_secs"] = 0
                config_path.write_text(toml.dumps(config))
                workload.add_genesis_test_cells(
                    node, program_names=("cpu_budget_stress_1b",)
                )
                # 仅本类 dev 链放宽 cycles，避免 cycles 耗尽冒充 CPU 预算中断。
                spec_path = Path(node.ckb_specs_config_path)
                spec = toml.loads(spec_path.read_text())
                spec["params"]["max_block_cycles"] = 1_000_000_000_000
                spec_path.write_text(toml.dumps(spec))
                node.start()
                assert (
                    node.getClient().call("tx_pool_info", [], try_count=1, timeout=5)[
                        "tip_number"
                    ]
                    == "0x0"
                )
                cls.pids[role] = workload._node_process_id(node)
                assert cls.pids[role] is not None
                process_cpu_seconds(cls.pids[role])

            cls.Miner.make_tip_height_number(cls.source, 30)
            workload.sync_blocks(cls.source, cls.limited, 1, 30)
        except Exception:
            for node in cls.nodes:
                log = Path(node.ckb_dir) / "node.log"
                if log.exists():
                    print(log.read_text(errors="replace")[-8000:])
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.nodes):
            node.stop()
            workload.save_node_logs(node)
            node.clean()
        for pid in cls.pids.values():
            workload.wait_for(
                lambda: not workload._process_exists(pid), f"PID {pid} exited", 20
            )

    # TEST-MAP: VT2-58
    def test_long_vm_stops_spending_cpu_after_network_budget_refusal(self):
        """
        1. 构造单 VM 长循环交易，连接两个默认配置节点并测量接收端空闲 CPU。
        2. 发送端本地完整执行，证明交易合法且至少消耗 16 秒进程 CPU，再真实转发。
        3. 接收端预算拒绝后观察 15 秒，检查 CPU 回到空闲、脚本未结束及快交易仍可入池。
        4. 隔离对端，在同一接收节点本地完整执行相同交易，核对结束标记、交易体和 cycles。
        """
        source, limited = self.source, self.limited
        pid = self.pids["limited"]
        log_path = Path(limited.ckb_dir) / "node.log"
        begin, end = "CPU_STRESS_BEGIN_1B", "CPU_STRESS_END_1B"
        refusal = "BUDGET_REFUSAL"

        # 1. 固定 ELF 只有一个慢 type 脚本组，排除脚本组之间停止的假象。
        slow = workload.build_budget_transaction(source)
        expected_hash = transaction_hash(slow)
        source.connected(limited)
        workload.wait_for(lambda: limited.get_connected_count() == 1, "peer connected")
        idle_start = time.monotonic()
        idle_before = process_cpu_seconds(pid)
        time.sleep(5)
        idle_cpu = process_cpu_seconds(pid) - idle_before
        idle_wall = time.monotonic() - idle_start
        idle_rate = idle_cpu / idle_wall
        print(f"CPU idle: {idle_cpu:.6f}s / {idle_wall:.3f}s wall")
        assert 0 <= idle_rate < 0.05, "Node is too busy for an idle CPU comparison"

        events, sampling_errors = [], []
        stop_sampling = threading.Event()

        def sample_cpu_at_script_events():
            # 进程 CPU 包含后台工作；50ms 采样只作中断观测，不声称精确线程计量。
            offset = log_path.stat().st_size
            try:
                while not stop_sampling.is_set():
                    cpu = process_cpu_seconds(pid)
                    with log_path.open() as log:
                        log.seek(offset)
                        lines = log.readlines()
                        offset = log.tell()
                    for line in lines:
                        for marker in (begin, end, refusal):
                            if marker in line:
                                events.append((marker, cpu))
                                print(f"CPU event: {cpu:.6f}s {line.strip()}")
                        if workload.budget_rejection(line, expected_hash, "remote"):
                            events.append((refusal, cpu))
                            print(f"CPU event: {cpu:.6f}s {line.strip()}")
                    stop_sampling.wait(0.05)
            except Exception as error:
                sampling_errors.append(error)

        sampler = threading.Thread(target=sample_cpu_at_script_events, daemon=True)
        sampler.start()
        try:
            # 2. 本地入口不受网络预算限制；真实 cycles 随目标交易经 relay 发送。
            local_before = process_cpu_seconds(self.pids["source"])
            tx_hash = source.getClient().call(
                "send_transaction",
                [slow, "passthrough"],
                try_count=1,
                timeout=workload.BUDGET_RPC_TIMEOUT,
                verbose=False,
            )
            assert tx_hash == expected_hash
            local_cpu = process_cpu_seconds(self.pids["source"]) - local_before
            print(f"CPU source full execution: {local_cpu:.6f}s")
            assert local_cpu >= 16, "Workload must take at least twice the 8s CPU cap"
            source_log = (Path(source.ckb_dir) / "node.log").read_text()
            assert source_log.count(begin) == source_log.count(end) == 1
            cycles = int(
                workload.pool_transactions(source.getClient())[tx_hash]["cycles"], 16
            )
            assert 0 < cycles < 1_000_000_000_000
            workload.assert_pool_transaction(source, tx_hash, slow, cycles)

            # 3. 以目标 hash 的真实预算拒绝为起点，保持进程存活并检查后台停止。
            workload.assert_remote_budget_refusal(limited, tx_hash)
            assert workload.transaction_requests(source, tx_hash)
            workload.wait_for(
                lambda: any(marker == refusal for marker, _ in events),
                "CPU sample at budget refusal",
                5,
            )
            after_start = time.monotonic()
            after_before = process_cpu_seconds(pid)
            time.sleep(15)
            after_cpu = process_cpu_seconds(pid) - after_before
            after_wall = time.monotonic() - after_start
            allowance = max(0.25, idle_rate * after_wall + 0.20)
            print(
                f"CPU after refusal: {after_cpu:.6f}s / {after_wall:.3f}s wall; limit={allowance:.6f}s"
            )
            assert 0 <= after_cpu < allowance, "VM kept consuming CPU after refusal"
            assert [marker for marker, _ in events] == [begin, refusal]
            limited_cpu = events[1][1] - events[0][1]
            print(f"CPU network execution: {limited_cpu:.6f}s; default cap=8000ms")
            # 声明 cycles 可使预算更短；不要求每笔 Remote 都恰好消耗 8 秒。
            assert 0 < limited_cpu <= 8.6, events
            assert limited_cpu < local_cpu * 0.6, (limited_cpu, local_cpu)
            assert len(workload.remote_refusals(limited, tx_hash)) == 1
            assert limited.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
            fast = workload.build_transaction(source, cell=1)
            fast_hash = source.getClient().send_transaction(fast)
            workload.assert_pool_transaction(
                limited, fast_hash, fast, workload.FAST_CYCLES
            )
            assert limited.getClient().get_banned_addresses() == []
            assert limited.get_connected_count() == 1
            log = log_path.read_text()
            assert log.count(begin) == 1 and end not in log
            assert (
                limited.getClient().get_transaction(tx_hash)["tx_status"]["status"]
                == "unknown"
            )
        finally:
            stop_sampling.set()
            sampler.join(timeout=5)
            assert not sampler.is_alive(), "CPU sampler did not stop"
            assert not sampling_errors, sampling_errors

        # 4. 同一接收节点做完整本地验证对照；隔离网络以排除重传或重复执行。
        limited.getClient().remove_node(source.get_peer_id())
        workload.wait_for(
            lambda: limited.get_connected_count() == 0, "isolate local control"
        )
        control_before = process_cpu_seconds(pid)
        assert (
            limited.getClient().call(
                "send_transaction",
                [slow, "passthrough"],
                try_count=1,
                timeout=workload.BUDGET_RPC_TIMEOUT,
                verbose=False,
            )
            == tx_hash
        )
        control_cpu = process_cpu_seconds(pid) - control_before
        print(f"CPU same-node full local control: {control_cpu:.6f}s")
        assert (
            control_cpu >= 16
        ), "Receiver's full execution must exceed the default cap"
        assert limited_cpu < control_cpu * 0.6
        log = log_path.read_text()
        assert log.count(begin) == 2 and log.count(end) == 1
        workload.assert_pool_transaction(limited, tx_hash, slow, cycles)
        self.did_pass = True
