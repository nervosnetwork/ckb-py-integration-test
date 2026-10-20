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
from test_cases.network_verification.runtime import script_path


class TestBudgetCpuStop(CkbTest):
    @classmethod
    def setup_class(cls):
        if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
            raise SkipTest("Process CPU observation requires Linux or macOS")
        cls.nodes = []
        cls.pids = {}
        try:
            # 三个独立节点：本地发送、高预算网络对照、8 秒预算网络接收。
            for role, cap_ms in (
                ("source", 180_000),
                ("control", 180_000),
                ("limited", 8_000),
            ):
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
                    max_tx_verify_time_ms=cap_ms,
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
            workload.sync_blocks(cls.source, cls.control, 1, 30)
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
        1. 构造单 VM 十亿次循环交易，连接两个冷节点并测量空闲 CPU。
        2. 本地完整执行证明交易合法且耗时；同一交易真实转发到两个节点。
        3. 检查 8000ms 节点中途拒绝，之后 15 秒 CPU 回到空闲且脚本未结束。
        4. 高预算节点完整执行同一交易并入池；低预算节点继续接收快速交易。
        """
        source, limited, control = self.source, self.limited, self.control

        # 1. 单个慢 type 脚本组，避免把脚本组之间停止误认为 VM 中途打断。
        program = script_path("cpu_budget_stress_1b")
        slow = workload.build_transaction(source)
        genesis = source.getClient().get_block_by_number("0x0")["transactions"][0]
        index = genesis["outputs_data"].index("0x" + program.read_bytes().hex())
        slow["cell_deps"].append(
            {
                "out_point": {"tx_hash": genesis["hash"], "index": hex(index)},
                "dep_type": "code",
            }
        )
        slow["outputs"][0]["type"] = {
            "code_hash": workload.script_hash(program),
            "hash_type": "data1",
            "args": "0x",
        }
        for node in (limited, control):
            source.connected(node)
            workload.wait_for(
                lambda: len(node.getClient().get_peers()) == 1, "peer connected"
            )
        idle_start = time.monotonic()
        idle_before = process_cpu_seconds(self.pids["limited"])
        time.sleep(5)
        idle_cpu = process_cpu_seconds(self.pids["limited"]) - idle_before
        idle_wall = time.monotonic() - idle_start
        print(f"CPU idle: {idle_cpu:.6f}s / {idle_wall:.3f}s wall")
        assert (
            idle_cpu / idle_wall < 0.05
        ), "Node is too busy for an idle CPU comparison"

        begin, end = "CPU_STRESS_BEGIN_1B", "CPU_STRESS_END_1B"
        refusal = "remote reject: Transaction verification exceeded the local tx-pool time limit"
        events = {"limited": [], "control": []}
        sampling_errors = []
        stop_sampling = threading.Event()

        def sample_cpu_at_script_events():
            # 只读取公开日志和进程 CPU；50ms 采样误差不作为精确线程预算测量。
            offsets = dict.fromkeys(events, 0)
            try:
                while not stop_sampling.is_set():
                    for role, node in (("limited", limited), ("control", control)):
                        cpu = process_cpu_seconds(self.pids[role])
                        with (Path(node.ckb_dir) / "node.log").open() as log:
                            log.seek(offsets[role])
                            lines = log.readlines()
                            offsets[role] = log.tell()
                        for line in lines:
                            for marker in (begin, end, refusal):
                                if marker in line:
                                    events[role].append((marker, cpu))
                                    print(
                                        f"CPU event: {role} {cpu:.6f}s {line.strip()}"
                                    )
                    stop_sampling.wait(0.05)
            except Exception as error:
                sampling_errors.append(error)

        sampler = threading.Thread(target=sample_cpu_at_script_events, daemon=True)
        sampler.start()
        try:
            # 2. 本地执行不限网络预算；真实 cycles 随原交易经 relay 发送。
            local_before = process_cpu_seconds(self.pids["source"])
            tx_hash = source.getClient().call(
                "send_transaction",
                [slow, "passthrough"],
                try_count=1,
                timeout=240,
                verbose=False,
            )
            local_cpu = process_cpu_seconds(self.pids["source"]) - local_before
            print(f"CPU local full execution: {local_cpu:.6f}s")
            assert local_cpu >= 16, "Workload must take at least twice the 8s CPU cap"
            source_log = (Path(source.ckb_dir) / "node.log").read_text()
            assert source_log.count(begin) == source_log.count(end) == 1
            cycles = int(
                workload.pool_transactions(source.getClient())[tx_hash]["cycles"], 16
            )
            assert cycles < 1_000_000_000_000
            workload.assert_pool_transaction(source, tx_hash, slow, cycles)

            # 3. 拒绝后不关节点、不重交交易；观测 CPU，排除后台继续跑长循环。
            workload.assert_remote_budget_refusal(limited, tx_hash)
            workload.wait_for(
                lambda: any(marker == refusal for marker, _ in events["limited"]),
                "CPU sample at budget refusal",
                5,
            )
            after_start = time.monotonic()
            after_before = process_cpu_seconds(self.pids["limited"])
            time.sleep(15)
            after_cpu = process_cpu_seconds(self.pids["limited"]) - after_before
            after_wall = time.monotonic() - after_start
            allowance = max(0.25, idle_cpu / idle_wall * after_wall + 0.20)
            print(
                f"CPU after refusal: {after_cpu:.6f}s / {after_wall:.3f}s wall; limit={allowance:.6f}s"
            )
            assert (
                after_cpu < allowance
            ), "VM kept consuming CPU after the budget refusal"
            assert [marker for marker, _ in events["limited"]] == [begin, refusal]
            limited_cpu = events["limited"][1][1] - events["limited"][0][1]
            print(f"CPU limited execution: {limited_cpu:.6f}s; cap=8000ms")
            assert 7.7 <= limited_cpu <= 8.6, events["limited"]
            assert len(workload.remote_refusals(limited, tx_hash)) == 1
            assert limited.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
            assert (
                limited.getClient().get_transaction(tx_hash)["tx_status"]["status"]
                == "unknown"
            )

            # 4. 高预算同交易执行到底；低预算同 peer 的快交易正常入池且不封禁。
            workload.wait_for(
                lambda: tx_hash in workload.pool_transactions(control.getClient()),
                "high-budget relay admission",
                200,
            )
            workload.assert_pool_transaction(control, tx_hash, slow, cycles)
            workload.wait_for(
                lambda: any(marker == end for marker, _ in events["control"]),
                "high-budget script completion",
                5,
            )
            assert [marker for marker, _ in events["control"]] == [begin, end]
            control_cpu = events["control"][1][1] - events["control"][0][1]
            print(f"CPU control full execution: {control_cpu:.6f}s; cap=180000ms")
            assert control_cpu >= 16
            fast = workload.build_transaction(source, cell=1)
            fast_hash = source.getClient().send_transaction(fast)
            workload.assert_pool_transaction(
                limited, fast_hash, fast, workload.FAST_CYCLES
            )
            assert limited.getClient().get_banned_addresses() == []
            assert len(limited.getClient().get_peers()) == 1
            # 再查一次起止日志，覆盖等待高预算对照期间的意外恢复。
            limited_log = (Path(limited.ckb_dir) / "node.log").read_text()
            assert limited_log.count(begin) == 1 and end not in limited_log
            assert (
                limited.getClient().get_transaction(tx_hash)["tx_status"]["status"]
                == "unknown"
            )
        finally:
            stop_sampling.set()
            sampler.join(timeout=5)
            assert not sampler.is_alive(), "CPU sampler did not stop"
            assert not sampling_errors, sampling_errors
        self.did_pass = True
