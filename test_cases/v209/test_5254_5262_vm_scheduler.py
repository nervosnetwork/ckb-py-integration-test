import threading
import time
from pathlib import Path

import framework.helper.ckb_cli as ckb_cli_helper
import framework.helper.contract as contract_helper
from framework.basic import CkbTest
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


from test_cases.network_verification.runtime import requires_target


@requires_target
class TestVmVerificationPaths(CkbTest):
    """VM-version/syscall fixtures through RPC, real relay and cold full blocks."""

    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-24
    # TEST-EVIDENCE-BEGIN: VT2-24
    # Evidence | Case: VT2-24 (covered)
    # Evidence | Setup: Nine script hashes prechecked; fixed cycle JSON read without regeneration. Seven VM/version and applicable exec/spawn fixtures use independent genesis cells and cap12000.
    # Evidence | Trigger: Each fixture: source RPC dry-run, real relay to second node, mining, then full-block submit to isolated third node whose target status is unknown.
    # Evidence | Observe: RPC and verbose pool cycles match fixed baseline; cold node body/block hash committed matches source; full-block helper requires canonical tip convergence.
    # Evidence | Assert: assert int(preview["cycles"], 16) == expected[label], (label, preview)
    # Evidence | Assert: assert cold.get_connected_count() == 0
    # Evidence | Assert: assert (
    # Evidence |                     cold.getClient().get_transaction(tx_hash)["tx_status"]["status"]
    # Evidence |                     == "unknown"
    # Evidence |                 )
    # Evidence | Assert: assert result["tx_status"]["status"] == "committed", label
    # Evidence | Assert: assert result["transaction"] == committed["transaction"], label
    # Evidence | Assert: assert (
    # Evidence |                     result["tx_status"]["block_hash"]
    # Evidence |                     == committed["tx_status"]["block_hash"]
    # Evidence |                 ), label
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Assert: assert receiver.submit_block("0x0", block) == expected_hash
    # Evidence | Assert: raise AssertionError(f"block receiver did not reach {expected_tip}")
    # Evidence | Limit: Seven fixture labels are an in-method loop, not seven independent pytest parameter selectors.
    # Evidence | Limit: Baseline JSON fixes values/hashes but lacks original pre-change commit/binary/measurement receipt; historical provenance is unverified.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-24
    def test_vm_cycles_match_across_rpc_relay_and_block_verification(self):
        import json
        from test_cases.network_verification import workload as w
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
        programs = sorted({name for _, _, names in fixtures for name in names})
        with w.nodes(12_000, 12_000, 12_000, program_names=programs) as (
            miner,
            receiver,
            cold,
        ):
            w.connect(miner, receiver)
            genesis = miner.getClient().get_block_by_number("0x0")["transactions"][0]
            for cell, (label, hash_type, names) in enumerate(fixtures):
                tx = w.transaction(miner, cell=cell, output_capacity=200 * w.CKB)
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
                        code_hash = w.script_hash(path)
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
                w.accepted(receiver, tx_hash, tx, expected[label])
                assert cold.get_connected_count() == 0
                assert (
                    cold.getClient().get_transaction(tx_hash)["tx_status"]["status"]
                    == "unknown"
                )
                first = cold.getClient().get_tip_block_number() + 1
                committed = self.Miner.miner_until_tx_committed(miner, tx_hash)
                w.sync_blocks(
                    miner, cold, first, miner.getClient().get_tip_block_number()
                )
                result = cold.getClient().get_transaction(tx_hash)
                assert result["tx_status"]["status"] == "committed", label
                assert result["transaction"] == committed["transaction"], label
                assert (
                    result["tx_status"]["block_hash"]
                    == committed["tx_status"]["block_hash"]
                ), label
                print(
                    f"{label}: RPC/relay cycles={expected[label]}, cold block committed"
                )
        self.did_pass = True
