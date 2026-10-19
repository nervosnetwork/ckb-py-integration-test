"""PR #5316: local pool time limits must not reject a valid block."""

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests import (
    test_45_pr5316_time_budget_retry as budget_helpers,
)


def _four_ports():
    sockets = [socket.socket() for _ in range(4)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


class TestBudgetConsensusIsolation(CkbTest):
    def teardown_method(self, method):
        # Keep the focused pytest log instead of copying a shared report path.
        pass

    @staticmethod
    def _ready(node):
        client = node.getClient()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                return client.call(
                    "tx_pool_info",
                    [],
                    try_count=1,
                    timeout=1,
                    verbose=False,
                )
            except Exception:
                time.sleep(0.2)
        output = (Path(node.ckb_dir) / "node.log").read_text(errors="replace")
        raise AssertionError(f"node startup failed: {output[-3000:]}")

    @staticmethod
    def _sync_blocks(source, destination, first_height, last_height):
        sender = source.getClient()
        receiver = destination.getClient()
        for height in range(first_height, last_height + 1):
            block = sender.get_block_by_number(hex(height))
            expected_hash = block["header"].pop("hash")
            for tx in block["transactions"]:
                tx.pop("hash", None)
            for uncle in block["uncles"]:
                uncle["header"].pop("hash", None)
            assert receiver.submit_block("0x0", block) == expected_hash
        expected_tip = sender.get_tip_header()["hash"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if (
                receiver.get_tip_header()["hash"] == expected_tip
                and receiver.tx_pool_info()["tip_hash"] == expected_tip
            ):
                return
            time.sleep(0.2)
        raise AssertionError(f"block receiver did not reach {expected_tip}")

    @staticmethod
    def _save_log(node, log_dir, name):
        if log_dir:
            log_path = Path(node.ckb_dir) / "node.log"
            if log_path.is_file():
                Path(log_dir, name).write_bytes(log_path.read_bytes())

    # TEST-MAP: EVT-06
    def test_pool_timeout_does_not_reject_independently_mined_valid_block(self):
        log_dir = os.environ.get("CKB_PR5316_CONSENSUS_LOG_DIR")
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
        binary = budget_helpers._fixed_test_binary(log_dir)
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "60b8e34" in version, version
        print(f"fixed test binary: {version}; sha256={budget_helpers._sha256(binary)}")

        high_rpc, high_p2p, low_rpc, low_p2p = _four_ports()
        high = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_budget_consensus/high_{high_rpc}",
            high_rpc,
            high_p2p,
        )
        low = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_budget_consensus/low_{low_rpc}",
            low_rpc,
            low_p2p,
        )
        started = []
        try:
            for node, budget in ((high, 12_000), (low, 1)):
                node.prepare()
                shutil.copy2(binary, node.ckb_bin_path)
                budget_helpers.TestTimeBudgetRetry._set_budget(node, budget)
                node.start()
                started.append(node)
                assert self._ready(node)["tip_number"] == "0x0"
            high_client = high.getClient()
            low_client = low.getClient()
            assert high_client.get_block_hash("0x0") == low_client.get_block_hash("0x0")

            self.Miner.make_tip_height_number(high, 30)
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=high_client.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(high, fund)
            self._sync_blocks(high, low, 1, high_client.get_tip_block_number())
            assert low_client.get_live_cell("0x0", fund)["status"] == "live"

            transaction = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=high_client.url,
            )
            tx_hash = low_client.notify_transaction(transaction)
            low_log = Path(low.ckb_dir) / "node.log"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if budget_helpers.TestTimeBudgetRetry._has_budget_rejection(
                    low_log, tx_hash
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("low-budget node did not reject the proposal")
            assert (
                low_client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
            )
            assert tx_hash not in low_client.get_raw_tx_pool(False)["pending"]
            assert tx_hash not in low_client.get_raw_tx_pool(False)["proposed"]
            self._save_log(low, log_dir, "low-budget-before-block.log")

            height_before = high_client.get_tip_block_number()
            assert high_client.send_transaction(transaction) == tx_hash
            raw = high_client.get_raw_tx_pool(True)
            accepted = {**raw["pending"], **raw["proposed"]}
            cycles = int(accepted[tx_hash]["cycles"], 16)
            max_cycles = int(low_client.get_consensus()["max_block_cycles"], 16)
            assert 0 < cycles < max_cycles
            committed = self.Miner.miner_until_tx_committed(high, tx_hash)
            assert committed["tx_status"]["status"] == "committed"
            commit_height = int(committed["tx_status"]["block_number"], 16)
            assert commit_height > height_before
            short_id = tx_hash[:22]
            proposals = [
                height
                for height in range(height_before + 1, commit_height)
                if short_id in high_client.get_block_by_number(hex(height))["proposals"]
            ]
            assert proposals, "committed block had no prior proposal"
            self._sync_blocks(
                high, low, height_before + 1, high_client.get_tip_block_number()
            )

            observed = low_client.get_transaction(tx_hash)
            assert observed["tx_status"]["status"] == "committed"
            assert observed["transaction"]["hash"] == tx_hash
            assert (
                observed["tx_status"]["block_hash"]
                == committed["tx_status"]["block_hash"]
            )
            assert (
                low_client.get_tip_header()["hash"]
                == high_client.get_tip_header()["hash"]
            )
            self._save_log(low, log_dir, "low-budget-after-block.log")
            print(
                f"pool timeout then consensus commit: tx={tx_hash}, "
                f"cycles={cycles}/{max_cycles}, proposal_heights={proposals}, "
                f"commit_height={commit_height}"
            )
            self.did_pass = True
        finally:
            for node, name in ((low, "low-final.log"), (high, "high-final.log")):
                self._save_log(node, log_dir, name)
            for node in reversed(started):
                node.stop()
            for node in (low, high):
                if Path(node.ckb_dir).exists():
                    node.clean()
