import threading
import time
from pathlib import Path

import framework.helper.ckb_cli as ckb_cli_helper
import framework.helper.contract as contract_helper

from framework.basic import CkbTest
import toml
from test_cases.network_verification import workload
from framework.util import get_project_root


class TestVmSchedulerRegressions(CkbTest):
    """Binary-level regression coverage for CKB PR #5254 and #5262."""

    CONTRACT_PATH = (
        Path(get_project_root())
        / "source"
        / "contract"
        / "test_cases"
        / "spawn_stop_16_spawn_create_17_spawn"
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

        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "v209/pr5254_5262_vm_scheduler",
            8490,
            8491,
        )
        try:
            cls.node.clean()
            cls.node.prepare(other_ckb_config={"ckb_tx_pool_max_tx_verify_workers": 1})
            cls.node.start()
            cls.Miner.make_tip_height_number(cls.node, 30)
            cls.deploy_hash = cls.Contract.deploy_ckb_contract(
                cls.Config.MINER_PRIVATE_1,
                str(cls.CONTRACT_PATH),
                enable_type_id=True,
                api_url=cls.node.getClient().url,
            )
            cls.Miner.miner_until_tx_committed(cls.node, cls.deploy_hash)
        except Exception:
            cls.node.stop()
            cls.node.clean()
            cls._restore_cli_path()
            raise

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()
        cls._restore_cli_path()

    @classmethod
    def _restore_cli_path(cls):
        ckb_cli_helper.cli_path = cls._original_ckb_cli_path
        contract_helper.cli_path = cls._original_contract_cli_path

    def setup_method(self, method):
        self.did_pass = None

    def test_terminated_vm_cleanup_keeps_spawn_scheduler_usable(self):
        """
        PR #5254: execute a contract that fills all 16 child-VM slots, stops
        those children, and creates another child. Successful verification and
        commitment prove that wait/exit cleanup removed terminated VM entries
        without corrupting the scheduler.
        """
        tx = self._build_scheduler_transaction()
        tx_hash = self.node.getClient().notify_transaction(tx)

        self._wait_for_accepted_status(tx_hash)
        response = self.Miner.miner_until_tx_committed(self.node, tx_hash)

        assert response["tx_status"]["status"] == "committed"
        assert self.node.getClient().get_tip_header()["hash"].startswith("0x")
        self.did_pass = True

    def test_async_verification_survives_concurrent_block_processing(self):
        """
        PR #5262: enqueue the same multi-VM contract through the asynchronous
        verify queue while blocks are being processed. The transaction must
        finish verification and commit using the remaining live scheduler path
        after the unused fully-suspended scheduler state was removed.
        """
        tx = self._build_scheduler_transaction()
        stop_mining = threading.Event()
        mined_blocks = []
        mining_errors = []

        def churn_blocks():
            while not stop_mining.is_set() and len(mined_blocks) < 20:
                try:
                    mined_blocks.append(self.node.getClient().generate_block())
                except Exception as error:
                    mining_errors.append(error)
                    return

        miner = threading.Thread(target=churn_blocks, daemon=True)
        miner.start()
        try:
            tx_hash = self.node.getClient().notify_transaction(tx)
            self._wait_for_accepted_status(tx_hash)
        finally:
            stop_mining.set()
            miner.join(timeout=10)

        assert not miner.is_alive(), "block churn thread did not stop"
        assert not mining_errors, mining_errors
        assert mined_blocks, "no block was processed during asynchronous verification"

        response = self.node.getClient().get_transaction(tx_hash)
        if response["tx_status"]["status"] != "committed":
            response = self.Miner.miner_until_tx_committed(self.node, tx_hash)
        assert response["tx_status"]["status"] == "committed"
        assert self.node.getClient().tx_pool_info()["verify_queue_size"] == "0x0"
        self.did_pass = True

    def _build_scheduler_transaction(self):
        return self.Contract.build_invoke_ckb_contract(
            account_private=self.Config.MINER_PRIVATE_1,
            contract_out_point_tx_hash=self.deploy_hash,
            contract_out_point_tx_index=0,
            type_script_arg="0x02",
            data="0x1234",
            hash_type="type",
            api_url=self.node.getClient().url,
        )

    def _wait_for_accepted_status(self, tx_hash, timeout=60):
        deadline = time.monotonic() + timeout
        last_status = "unknown"
        while time.monotonic() < deadline:
            response = self.node.getClient().get_transaction(tx_hash)
            last_status = response["tx_status"]["status"]
            if last_status in {"pending", "proposed", "committed"}:
                return response
            if last_status == "rejected":
                raise AssertionError(response["tx_status"].get("reason"))
            time.sleep(0.1)
        raise AssertionError(
            f"transaction {tx_hash} was not accepted; last status: {last_status}"
        )


class TestVmVerificationPaths(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 出块节点：先本地验证交易，再生成有效区块。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.miner = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestVmVerificationPaths/miner_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.miner)
            cls.miner.prepare()
            config_path = Path(cls.miner.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 相同创世块装入 VM/syscall 样本，供后续七组验证共用。
            workload.add_genesis_test_cells(
                cls.miner,
                program_names=(
                    "exec_callee",
                    "exec_caller_from_cell_data",
                    "spawn_callee_current_cycles",
                    "spawn_callee_exec_callee",
                    "spawn_callee_exec_caller",
                    "spawn_caller_current_cycles",
                    "spawn_caller_exec",
                ),
            )
            cls.miner.start()
            cls.started_nodes.append(cls.miner)
            assert cls.miner.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：12 秒上限，正常执行网络验证。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestVmVerificationPaths/receiver_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.receiver)
            cls.receiver.prepare()
            config_path = Path(cls.receiver.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 相同创世块装入 VM/syscall 样本，供后续七组验证共用。
            workload.add_genesis_test_cells(
                cls.receiver,
                program_names=(
                    "exec_callee",
                    "exec_caller_from_cell_data",
                    "spawn_callee_current_cycles",
                    "spawn_callee_exec_callee",
                    "spawn_callee_exec_caller",
                    "spawn_caller_current_cycles",
                    "spawn_caller_exec",
                ),
            )
            cls.receiver.start()
            cls.started_nodes.append(cls.receiver)
            assert cls.receiver.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 冷验块节点：不接收交易转发，只验证完整区块。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.cold = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestVmVerificationPaths/cold_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.cold)
            cls.cold.prepare()
            config_path = Path(cls.cold.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 相同创世块装入 VM/syscall 样本，供后续七组验证共用。
            workload.add_genesis_test_cells(
                cls.cold,
                program_names=(
                    "exec_callee",
                    "exec_caller_from_cell_data",
                    "spawn_callee_current_cycles",
                    "spawn_callee_exec_callee",
                    "spawn_callee_exec_caller",
                    "spawn_caller_current_cycles",
                    "spawn_caller_exec",
                ),
            )
            cls.cold.start()
            cls.started_nodes.append(cls.cold)
            assert cls.cold.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.miner, 30)
            workload.sync_blocks(cls.miner, cls.receiver, 1, 30)
            workload.sync_blocks(cls.miner, cls.cold, 1, 30)
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.started_nodes):
            node.stop()
        for node in cls.nodes:
            workload.save_node_logs(node)
            node.clean()

    """VM-version/syscall fixtures through RPC, real relay and cold full blocks."""

    # TEST-MAP: VT2-24
    # TEST-EVIDENCE-BEGIN: VT2-24
    # Evidence | Case: VT2-24 (covered)
    # Evidence | Setup: Each of the nine requested script inputs is checked against its fixed SHA-256 as script_path is called during setup; there is no suite-wide manifest preflight; fixed cycle JSON read without regeneration. Seven VM/version and applicable exec/spawn fixtures use independent genesis cells and cap12000. Fresh class-owned nodes are explicitly configured and started in TestVmVerificationPaths.setup_class; each class has one independent scenario.
    # Evidence | Trigger: Each fixture: source RPC dry-run, real relay to second node, mining, then full-block submit to isolated third node whose target status is unknown.
    # Evidence | Observe: RPC and verbose pool cycles match fixed baseline; cold node body/block hash committed matches source; full-block helper requires canonical tip convergence.
    # Evidence | Assert: assert int(preview["cycles"], 16) == expected[label], (label, preview)
    # Evidence | Assert: assert cold.get_connected_count() == 0
    # Evidence | Assert: assert (
    # Evidence |                 cold.getClient().get_transaction(tx_hash)["tx_status"]["status"]
    # Evidence |                 == "unknown"
    # Evidence |             )
    # Evidence | Assert: assert result["tx_status"]["status"] == "committed", label
    # Evidence | Assert: assert result["transaction"] == committed["transaction"], label
    # Evidence | Assert: assert (
    # Evidence |                 result["tx_status"]["block_hash"]
    # Evidence |                 == committed["tx_status"]["block_hash"]
    # Evidence |             ), label
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(pool_transactions(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Assert: assert receiver.submit_block("0x0", block) == expected_hash
    # Evidence | Assert: raise AssertionError(f"block receiver did not reach {expected_tip}")
    # Evidence | Limit: Seven fixture labels are an in-method loop, not seven independent pytest parameter selectors.
    # Evidence | Limit: Baseline JSON fixes values/hashes but lacks original pre-change commit/binary/measurement receipt; historical provenance is unverified.
    # Evidence | Limit: Static coverage only; this readability review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-24
    def test_vm_cycles_match_across_rpc_relay_and_block_verification(self):
        """
        1. 遍历固定的 VM0/1/2、exec 和 spawn 七组样本。
        2. 分别检查 dry-run 与真实网络入池 cycles 等于独立固定基线。
        3. 第三个节点仅接收完整区块，检查冷验块与最终确认结果。
        """
        import json
        from test_cases.network_verification.runtime import ROOT, script_path

        fixtures = [
            ("vm0", "data", []),
            ("vm1", "data1", []),
            ("vm2", "data2", []),
            ("exec-vm1", "data1", ["exec_caller_from_cell_data", "exec_callee"]),
            ("exec-vm2", "data2", ["exec_caller_from_cell_data", "exec_callee"]),
            (
                "spawn-vm2",
                "data2",
                ["spawn_caller_current_cycles", "spawn_callee_current_cycles"],
            ),
            (
                "spawn-exec-vm2",
                "data2",
                [
                    "spawn_caller_exec",
                    "spawn_callee_exec_caller",
                    "spawn_callee_exec_callee",
                ],
            ),
        ]
        expected = json.loads(
            (
                ROOT / "test_cases/network_verification/fixtures/vm-cycle-baseline.json"
            ).read_text()
        )["values"]
        miner = self.miner
        receiver = self.receiver
        cold = self.cold
        miner.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == miner.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        genesis = miner.getClient().get_block_by_number("0x0")["transactions"][0]
        for cell, (label, hash_type, names) in enumerate(fixtures):
            tx = workload.build_transaction(
                miner, cell=cell, output_capacity=200 * workload.CKB
            )
            deps, code_hash = [], tx["outputs"][0]["lock"]["code_hash"]
            for position, name in enumerate(names):
                path = script_path(name)
                code = "0x" + path.read_bytes().hex()
                index = genesis["outputs_data"].index(code)
                deps.append(
                    {
                        "out_point": {
                            "tx_hash": genesis["hash"],
                            "index": hex(index),
                        },
                        "dep_type": "code",
                    }
                )
                if position == 0:
                    code_hash = workload.script_hash(path)
            # The syscall fixtures address callee deps by index, so keep
            # caller/callee order and append the input-lock dependency last.
            tx["cell_deps"] = deps + tx["cell_deps"]
            tx["outputs"][0]["type"] = {
                "code_hash": code_hash,
                "hash_type": hash_type,
                "args": "0x",
            }
            preview = miner.getClient().test_tx_pool_accept(tx, "passthrough")
            assert int(preview["cycles"], 16) == expected[label], (label, preview)
            tx_hash = miner.getClient().send_transaction(tx)
            workload.assert_pool_transaction(receiver, tx_hash, tx, expected[label])
            assert cold.get_connected_count() == 0
            assert (
                cold.getClient().get_transaction(tx_hash)["tx_status"]["status"]
                == "unknown"
            )
            first = cold.getClient().get_tip_block_number() + 1
            committed = self.Miner.miner_until_tx_committed(miner, tx_hash)
            workload.sync_blocks(
                miner, cold, first, miner.getClient().get_tip_block_number()
            )
            result = cold.getClient().get_transaction(tx_hash)
            assert result["tx_status"]["status"] == "committed", label
            assert result["transaction"] == committed["transaction"], label
            assert (
                result["tx_status"]["block_hash"]
                == committed["tx_status"]["block_hash"]
            ), label
            print(f"{label}: RPC/relay cycles={expected[label]}, cold block committed")
        self.did_pass = True
