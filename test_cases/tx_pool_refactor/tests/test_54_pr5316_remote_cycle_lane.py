"""PR #5316: the remote cycle threshold schedules work, not VM validity."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests import (
    test_45_pr5316_time_budget_retry as budget_helpers,
)
from test_cases.tx_pool_refactor.tests import (
    test_46_pr5316_budget_consensus as consensus_helpers,
)
from test_cases.tx_pool_refactor.tests.test_47_pr5316_peer_timeout import _six_ports


class TestRemoteCycleLane(CkbTest):
    def teardown_method(self, method):
        # The focused pytest log and optional node logs are the test record.
        pass

    @staticmethod
    def _accepted(client, tx_hash):
        raw = client.get_raw_tx_pool(True)
        return {**raw["pending"], **raw["proposed"]}.get(tx_hash)

    @staticmethod
    def _set_cycle_threshold(node, threshold):
        path = Path(node.ckb_toml_path)
        config = toml.loads(path.read_text())
        config["tx_pool"]["max_tx_verify_cycles"] = threshold
        path.write_text(toml.dumps(config))
        assert (
            toml.loads(path.read_text())["tx_pool"]["max_tx_verify_cycles"] == threshold
        )

    # TEST-MAP: NTD-09
    def test_exact_and_plus_one_declared_cycles_both_verify_remotely(self):
        log_dir = os.environ.get("CKB_PR5316_CYCLE_LANE_LOG_DIR")
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

        source_rpc, source_p2p, exact_rpc, exact_p2p, plus_rpc, plus_p2p = _six_ports()
        source = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_remote_cycle_lane/source_{source_rpc}",
            source_rpc,
            source_p2p,
        )
        exact = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_remote_cycle_lane/exact_{exact_rpc}",
            exact_rpc,
            exact_p2p,
        )
        plus_one = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_remote_cycle_lane/plus_{plus_rpc}",
            plus_rpc,
            plus_p2p,
        )
        started = []
        try:
            source.prepare()
            shutil.copy2(binary, source.ckb_bin_path)
            source.start()
            started.append(source)
            assert (
                consensus_helpers.TestBudgetConsensusIsolation._ready(source)[
                    "tip_number"
                ]
                == "0x0"
            )
            sender = source.getClient()

            self.Miner.make_tip_height_number(source, 30)
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=sender.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(source, fund)
            candidate = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=sender.url,
            )
            preview = sender.test_tx_pool_accept(candidate, "passthrough")
            cycles = int(preview["cycles"], 16)
            consensus_max = int(sender.get_consensus()["max_block_cycles"], 16)
            assert 1 < cycles < consensus_max
            assert sender.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

            # The same relay announcement carries the sender's actual cycles.
            # Changing only each receiver's local threshold puts that one
            # declaration exactly at the boundary or one cycle above it.
            for node, threshold in ((exact, cycles), (plus_one, cycles - 1)):
                node.prepare()
                shutil.copy2(binary, node.ckb_bin_path)
                self._set_cycle_threshold(node, threshold)
                node.start()
                started.append(node)
                assert (
                    consensus_helpers.TestBudgetConsensusIsolation._ready(node)[
                        "tip_number"
                    ]
                    == "0x0"
                )
                receiver = node.getClient()
                assert receiver.get_block_hash("0x0") == sender.get_block_hash("0x0")
                consensus_helpers.TestBudgetConsensusIsolation._sync_blocks(
                    source,
                    node,
                    1,
                    sender.get_tip_block_number(),
                )
                assert receiver.get_live_cell("0x0", fund)["status"] == "live"
                assert receiver.get_raw_tx_pool(False) == {
                    "pending": [],
                    "proposed": [],
                }
                source.connected(node)

            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if source.get_connected_count() >= 2:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    "source did not connect to both cycle-threshold receivers"
                )

            tx_hash = sender.send_transaction(candidate)
            sender_entry = self._accepted(sender, tx_hash)
            assert sender_entry is not None
            assert int(sender_entry["cycles"], 16) == cycles
            observed = {}
            for name, node in (("exact", exact), ("plus_one", plus_one)):
                client = node.getClient()
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    entry = self._accepted(client, tx_hash)
                    if entry is not None:
                        break
                    time.sleep(0.2)
                else:
                    raise AssertionError(
                        f"{name} receiver did not remotely accept {tx_hash}"
                    )
                assert int(entry["cycles"], 16) == cycles
                assert client.get_transaction(tx_hash)["tx_status"]["status"] in (
                    "pending",
                    "proposed",
                )
                assert client.get_banned_addresses() == []
                observed[name] = int(entry["cycles"], 16)

            assert observed == {"exact": cycles, "plus_one": cycles}
            print(
                json.dumps(
                    {
                        "tx_hash": tx_hash,
                        "declared_and_actual_cycles": cycles,
                        "thresholds": {"exact": cycles, "plus_one": cycles - 1},
                        "consensus_max": consensus_max,
                        "accepted_cycles": observed,
                    },
                    sort_keys=True,
                )
            )
            self.did_pass = True
        finally:
            for node, name in (
                (source, "source-final.log"),
                (exact, "exact-final.log"),
                (plus_one, "plus-one-final.log"),
            ):
                consensus_helpers.TestBudgetConsensusIsolation._save_log(
                    node, log_dir, name
                )
            for node in reversed(started):
                node.stop()
            for node in (source, exact, plus_one):
                if Path(node.ckb_dir).exists():
                    node.clean()
