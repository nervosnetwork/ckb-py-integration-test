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
