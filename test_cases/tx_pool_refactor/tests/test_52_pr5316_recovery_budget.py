"""PR #5316: detached transaction recovery bypasses the network VM time cap."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests import (
    test_45_pr5316_time_budget_retry as budget_helpers,
)
from test_cases.tx_pool_refactor.tests import (
    test_46_pr5316_budget_consensus as consensus_helpers,
)
from test_cases.tx_pool_refactor.tests import (
    test_50_pr5316_capacity_metrics as metric_helpers,
)


class TestRecoveryBudget(CkbTest):
    def teardown_method(self, method):
        # Retain the focused pytest output instead of the shared base report.
        pass

    # TEST-MAP: EVT-01
    def test_detached_transaction_recovers_after_restart_under_one_ms_cap(self):
        binary = budget_helpers._fixed_test_binary(None)
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "60b8e34" in version, version
        print(f"fixed test binary: {version}; sha256={budget_helpers._sha256(binary)}")

        low_rpc, low_p2p, fork_rpc, fork_p2p = consensus_helpers._four_ports()
        low = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_recovery_budget/low_{low_rpc}",
            low_rpc,
            low_p2p,
        )
        fork = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_recovery_budget/fork_{fork_rpc}",
            fork_rpc,
            fork_p2p,
        )
        started = []
        try:
            for node, cap in ((low, 1), (fork, 12_000)):
                node.prepare()
                shutil.copy2(binary, node.ckb_bin_path)
                budget_helpers.TestTimeBudgetRetry._set_budget(node, cap)
                node.start()
                started.append(node)
                assert (
                    consensus_helpers.TestBudgetConsensusIsolation._ready(node)[
                        "tip_number"
                    ]
                    == "0x0"
                )
            client = low.getClient()
            other = fork.getClient()
            assert client.get_block_hash("0x0") == other.get_block_hash("0x0")

            self.Miner.make_tip_height_number(low, 30)
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=client.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(low, fund)
            common_height = client.get_tip_block_number()
            consensus_helpers.TestBudgetConsensusIsolation._sync_blocks(
                low,
                fork,
                1,
                common_height,
            )
            assert other.get_live_cell("0x0", fund)["status"] == "live"

            candidate = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            # Prove this exact candidate exceeds the network-source cap before
            # it is committed; Local then admits the same bytes for mining.
            proposal_hash = client.notify_transaction(candidate)
            log_path = Path(low.ckb_dir) / "node.log"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                refusal = metric_helpers._rejection(log_path, proposal_hash)
                if refusal is not None:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("network-cap control did not reject candidate")
            assert refusal["reason"] == "ExcessiveVerifyTime", refusal
            assert (
                refusal["source"] == "proposal" and refusal["stage"] == "verify"
            ), refusal
            assert proposal_hash not in {
                *client.get_raw_tx_pool(False)["pending"],
                *client.get_raw_tx_pool(False)["proposed"],
            }
            target = client.send_transaction(candidate)
            assert target == proposal_hash
            initial_entry = client.get_raw_tx_pool(True)["pending"][target]
            assert int(initial_entry["cycles"], 16) > 0
            committed = self.Miner.miner_until_tx_committed(low, target)
            assert committed["tx_status"]["status"] == "committed"
            old_tip = client.get_tip_header()["hash"]
            old_height = client.get_tip_block_number()
            assert old_height > common_height
            assert other.get_tip_block_number() == common_height
            assert other.get_transaction(target)["tx_status"]["status"] == "unknown"

            # Before connection, the competing branch does not see the target
            # and overtakes the old committed chain. Restart clears in-memory
            # verification state before detached blocks enter Recovery.
            self.Miner.make_tip_height_number(fork, old_height + 2)
            new_tip = other.get_tip_header()["hash"]
            assert new_tip != old_tip
            assert other.get_transaction(target)["tx_status"]["status"] == "unknown"
            assert target not in {
                *other.get_raw_tx_pool(False)["pending"],
                *other.get_raw_tx_pool(False)["proposed"],
            }, "competing branch must not have the candidate before peers connect"
            low.stop()
            started.remove(low)
            low.start()
            started.append(low)
            assert (
                consensus_helpers.TestBudgetConsensusIsolation._ready(low)["tip_hash"]
                == old_tip
            )
            assert client.get_transaction(target)["tx_status"]["status"] == "committed"
            assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

            low.connected(fork)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                tip = client.get_tip_header()["hash"]
                pool_tip = client.tx_pool_info()["tip_hash"]
                raw = client.get_raw_tx_pool(False)
                hashes = set(raw["pending"]) | set(raw["proposed"])
                if tip == new_tip and pool_tip == new_tip and target in hashes:
                    break
                time.sleep(0.5)
            else:
                raise AssertionError(
                    f"Recovery did not settle: tip={tip}, pool_tip={pool_tip}, "
                    f"target={target}, pool={hashes}"
                )
            # Once peers connect, relay can legitimately give the fork node
            # this transaction; branch isolation was checked before connect.
            result = client.get_transaction(target)
            assert result["transaction"]["hash"] == target
            assert result["tx_status"]["status"] in ("pending", "proposed"), result
            assert result["tx_status"]["block_hash"] is None
            assert int(result["cycles"], 16) > 0
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                info = client.tx_pool_info()
                if int(info["verify_queue_size"], 16) == 0:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(f"Recovery verify queue did not settle: {info}")
            print(
                json.dumps(
                    {
                        "old_height": old_height,
                        "new_height": other.get_tip_block_number(),
                        "recovered_status": result["tx_status"]["status"],
                        "recovered_cycles": result["cycles"],
                        "same_hash": result["transaction"]["hash"] == target,
                        "network_cap_ms": 1,
                        "network_control": refusal["reason"],
                        "verify_queue_size": info["verify_queue_size"],
                    },
                    sort_keys=True,
                )
            )
            self.did_pass = True
        finally:
            log_dir = os.environ.get("CKB_PR5316_RECOVERY_LOG_DIR")
            if log_dir:
                Path(log_dir).mkdir(parents=True, exist_ok=True)
                for node, name in ((low, "low-node.log"), (fork, "fork-node.log")):
                    source = Path(node.ckb_dir) / "node.log"
                    if source.is_file():
                        shutil.copy2(source, Path(log_dir) / name)
            for node in reversed(started):
                node.stop()
            for node in (low, fork):
                if Path(node.ckb_dir).exists():
                    node.clean()
