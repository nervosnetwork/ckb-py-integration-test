"""PR #5316: a remote time-budget refusal must not penalize its peer."""

import copy
import json
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
from test_cases.tx_pool_refactor.tests import (
    test_46_pr5316_budget_consensus as consensus_helpers,
)


def _six_ports():
    sockets = [socket.socket() for _ in range(6)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


class TestRemoteTimeBudgetPeer(CkbTest):
    def teardown_method(self, method):
        # Retain the focused pytest log rather than copying a shared report path.
        pass

    @staticmethod
    def _budget_rejections(log_path, tx_hash):
        matches = []
        for line in log_path.read_text(errors="replace").splitlines():
            start = line.find('{"accounts":')
            if start < 0:
                continue
            try:
                event = json.loads(line[start:])
            except json.JSONDecodeError:
                continue
            if (
                event.get("event") == "committed_rejection"
                and event.get("hash") == tx_hash[2:]
                and event.get("reason") == "ExcessiveVerifyTime"
                and event.get("source") == "remote"
                and event.get("stage") == "verify"
            ):
                matches.append(event)
        return matches

    @staticmethod
    def _accepted(client, tx_hash):
        raw = client.get_raw_tx_pool(True)
        return {**raw["pending"], **raw["proposed"]}.get(tx_hash)

    # TEST-MAP: EVT-01
    # TEST-MAP: EVT-05
    def test_remote_timeout_keeps_peer_connected_for_next_valid_transaction(self):
        log_dir = os.environ.get("CKB_PR5316_PEER_LOG_DIR")
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

        high_rpc, high_p2p, low_rpc, low_p2p, other_rpc, other_p2p = _six_ports()
        high = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_peer_timeout/high_{high_rpc}",
            high_rpc,
            high_p2p,
        )
        low = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_peer_timeout/low_{low_rpc}",
            low_rpc,
            low_p2p,
        )
        other = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_peer_timeout/other_{other_rpc}",
            other_rpc,
            other_p2p,
        )
        started = []
        try:
            for node, budget in ((high, 12_000), (low, 1), (other, 12_000)):
                node.prepare()
                shutil.copy2(binary, node.ckb_bin_path)
                budget_helpers.TestTimeBudgetRetry._set_budget(node, budget)
                node.start()
                started.append(node)
                assert (
                    consensus_helpers.TestBudgetConsensusIsolation._ready(node)[
                        "tip_number"
                    ]
                    == "0x0"
                )
            source = high.getClient()
            target = low.getClient()
            assert source.get_block_hash("0x0") == target.get_block_hash("0x0")

            self.Miner.make_tip_height_number(high, 30)
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=source.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(high, fund)
            split = self.Tx.send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                output_count=2,
                fee=1000,
                api_url=source.url,
            )
            self.Miner.miner_until_tx_committed(high, split)
            consensus_helpers.TestBudgetConsensusIsolation._sync_blocks(
                high,
                low,
                1,
                source.get_tip_block_number(),
            )
            consensus_helpers.TestBudgetConsensusIsolation._sync_blocks(
                high,
                other,
                1,
                source.get_tip_block_number(),
            )
            for index in ("0x0", "0x1"):
                assert target.get_live_cell(index, split)["status"] == "live"

            slow_tx = self.Tx.build_send_transfer_self_tx_with_input(
                [split],
                ["0x0"],
                key,
                fee=1000,
                api_url=source.url,
            )
            fast_tx = self.Tx.build_send_transfer_self_tx_with_input(
                [split],
                ["0x1"],
                key,
                fee=1000,
                api_url=source.url,
            )
            # A local preview executes the fast script and stores its proof but
            # does not admit or relay the transaction on the target node.
            preview = target.test_tx_pool_accept(fast_tx, "passthrough")
            assert int(preview["cycles"], 16) > 0
            assert target.get_raw_tx_pool(False) == {"pending": [], "proposed": []}
            recent_dir = Path(low.ckb_dir) / "data/recent-reject"
            assert recent_dir.is_dir(), "recent-reject endpoint did not open"
            control = copy.deepcopy(slow_tx)
            control["header_deps"] = ["0x" + "ff" * 32]
            control_hash = target.notify_transaction(control)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                control_status = target.get_transaction(control_hash)["tx_status"]
                if control_status["status"] == "rejected":
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("recordable resolver control was not rejected")
            assert "invalidheader" in control_status["reason"].lower()
            recent_before = budget_helpers.TestTimeBudgetRetry._recent_count(target)
            assert recent_before >= 1

            high.connected(low)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if high.get_connected_count() >= 1 and low.get_connected_count() >= 1:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("test peers did not connect")
            assert target.get_tip_header()["hash"] == source.get_tip_header()["hash"]

            slow_hash = source.send_transaction(slow_tx)
            slow_entry = self._accepted(source, slow_hash)
            assert slow_entry is not None and int(slow_entry["cycles"], 16) > 0
            low_log = Path(low.ckb_dir) / "node.log"
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                rejections = self._budget_rejections(low_log, slow_hash)
                if rejections:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    "remote transaction did not reach the low-budget verifier"
                )
            rejection = rejections[0]
            assert rejection["peer"] is not None, rejection
            assert self._accepted(target, slow_hash) is None
            assert target.get_transaction(slow_hash)["tx_status"]["status"] == "unknown"
            assert (
                budget_helpers.TestTimeBudgetRetry._recent_count(target)
                == recent_before
            )
            assert target.get_banned_addresses() == []
            assert high.get_connected_count() >= 1 and low.get_connected_count() >= 1
            consensus_helpers.TestBudgetConsensusIsolation._save_log(
                low,
                log_dir,
                "low-after-remote-timeout.log",
            )

            # A separate peer announces the *same* hash. A second remote
            # verification proves that the earlier negative relay projection
            # did not retain this hash as permanently known/in-flight.
            other.connected(low)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if other.get_connected_count() >= 1 and low.get_connected_count() >= 2:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("second peer did not connect")
            assert other.getClient().send_transaction(slow_tx) == slow_hash
            assert self._accepted(other.getClient(), slow_hash) is not None
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                rejections = self._budget_rejections(low_log, slow_hash)
                later = [
                    event for event in rejections if event["peer"] != rejection["peer"]
                ]
                if later:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("same hash was not retried from the second peer")
            assert len(rejections) >= 2
            assert self._accepted(target, slow_hash) is None
            assert target.get_transaction(slow_hash)["tx_status"]["status"] == "unknown"
            assert (
                budget_helpers.TestTimeBudgetRetry._recent_count(target)
                == recent_before
            )
            consensus_helpers.TestBudgetConsensusIsolation._save_log(
                low,
                log_dir,
                "low-after-same-hash-retry.log",
            )

            fast_hash = source.send_transaction(fast_tx)
            fast_entry = self._accepted(source, fast_hash)
            assert fast_entry is not None
            assert int(fast_entry["cycles"], 16) == int(preview["cycles"], 16)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                accepted = self._accepted(target, fast_hash)
                if accepted is not None:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    "same peer's next valid transaction was not accepted"
                )
            assert int(accepted["cycles"], 16) == int(preview["cycles"], 16)
            assert target.get_transaction(fast_hash)["tx_status"]["status"] in (
                "pending",
                "proposed",
            )
            assert (
                budget_helpers.TestTimeBudgetRetry._recent_count(target)
                == recent_before
            )
            assert target.get_banned_addresses() == []
            assert high.get_connected_count() >= 1 and low.get_connected_count() >= 1
            consensus_helpers.TestBudgetConsensusIsolation._save_log(
                low,
                log_dir,
                "low-after-next-accepted.log",
            )
            print(
                f"remote timeout, same-hash retry, next accepted: "
                f"peers={rejection['peer']},{later[0]['peer']}, "
                f"slow={slow_hash}, fast={fast_hash}, "
                f"fast_cycles={int(accepted['cycles'], 16)}, "
                f"recent={recent_before}, connected={low.get_connected_count()}"
            )
            self.did_pass = True
        finally:
            for node, name in (
                (low, "low-final.log"),
                (high, "high-final.log"),
                (other, "other-final.log"),
            ):
                consensus_helpers.TestBudgetConsensusIsolation._save_log(
                    node, log_dir, name
                )
            for node in reversed(started):
                node.stop()
            for node in (low, high, other):
                if Path(node.ckb_dir).exists():
                    node.clean()
