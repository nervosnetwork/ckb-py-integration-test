"""PR #5316: committed relay results drain with no connected peers."""

import json
import os
import re
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
from test_cases.tx_pool_refactor.tests.test_50_pr5316_capacity_metrics import _metrics


def _five_ports():
    sockets = [socket.socket() for _ in range(5)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _relay_queue(metrics):
    values = {}
    for kind in ("size", "capacity"):
        match = re.search(
            rf"^ckb_relay_tx_verify_result_queue_{kind}\s+(\d+)(?:\.0+)?$",
            metrics,
            re.MULTILINE,
        )
        assert match is not None, f"missing relay queue {kind} gauge"
        values[kind] = int(match.group(1))
    return values


class TestNoPeerRelayDrain(CkbTest):
    def teardown_method(self, method):
        # Preserve the focused pytest trace and optional node logs.
        pass

    @staticmethod
    def _accepted(client, tx_hash):
        raw = client.get_raw_tx_pool(True)
        return {**raw["pending"], **raw["proposed"]}.get(tx_hash)

    # TEST-MAP: NTD-55
    def test_accepted_results_drain_without_peer_then_relay_after_connection(self):
        log_dir = os.environ.get("CKB_PR5316_RELAY_DRAIN_LOG_DIR")
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

        source_rpc, source_p2p, receiver_rpc, receiver_p2p, metrics_port = _five_ports()
        source = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_no_peer_relay_drain/source_{source_rpc}",
            source_rpc,
            source_p2p,
        )
        receiver = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_no_peer_relay_drain/receiver_{receiver_rpc}",
            receiver_rpc,
            receiver_p2p,
        )
        started = []
        try:
            for node in (source, receiver):
                node.prepare()
                shutil.copy2(binary, node.ckb_bin_path)
            with Path(source.ckb_toml_path).open("a", encoding="utf-8") as config:
                config.write(
                    "\n[metrics.exporter.prometheus]\n"
                    'target = { type = "prometheus", '
                    f'listen_address = "127.0.0.1:{metrics_port}" }}\n'
                )
            for node in (source, receiver):
                node.start()
                started.append(node)
                assert (
                    consensus_helpers.TestBudgetConsensusIsolation._ready(node)[
                        "tip_number"
                    ]
                    == "0x0"
                )
            sender, target = source.getClient(), receiver.getClient()
            assert source.get_connected_count() == receiver.get_connected_count() == 0
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    before_queue = _relay_queue(_metrics(metrics_port))
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise AssertionError("relay queue metrics did not start")
            assert before_queue["size"] == 0 and before_queue["capacity"] > 0

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
            split = self.Tx.send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                output_count=2,
                fee=1000,
                api_url=sender.url,
            )
            self.Miner.miner_until_tx_committed(source, split)
            consensus_helpers.TestBudgetConsensusIsolation._sync_blocks(
                source,
                receiver,
                1,
                sender.get_tip_block_number(),
            )
            assert sender.get_tip_header()["hash"] == target.get_tip_header()["hash"]
            assert source.get_connected_count() == receiver.get_connected_count() == 0
            assert target.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

            transactions = [
                self.Tx.build_send_transfer_self_tx_with_input(
                    [split],
                    [index],
                    key,
                    fee=1000,
                    api_url=sender.url,
                )
                for index in ("0x0", "0x1")
            ]
            hashes = [sender.send_transaction(tx) for tx in transactions]
            assert hashes[0] != hashes[1]
            sender_cycles = {}
            for tx_hash in hashes:
                entry = self._accepted(sender, tx_hash)
                assert entry is not None and int(entry["cycles"], 16) > 0
                sender_cycles[tx_hash] = int(entry["cycles"], 16)
            assert source.get_connected_count() == 0
            assert target.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                queue = _relay_queue(_metrics(metrics_port))
                if queue["size"] == 0:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    f"relay results stayed queued with no peer: {queue}"
                )
            assert queue["capacity"] == before_queue["capacity"]
            assert source.get_connected_count() == 0

            source.connected(receiver)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if (
                    source.get_connected_count() >= 1
                    and receiver.get_connected_count() >= 1
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("relay peers did not connect")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                received = {
                    tx_hash: self._accepted(target, tx_hash) for tx_hash in hashes
                }
                if all(entry is not None for entry in received.values()):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    f"previously accepted transactions not relayed: {received}"
                )
            for tx_hash, entry in received.items():
                assert int(entry["cycles"], 16) == sender_cycles[tx_hash]
                assert target.get_transaction(tx_hash)["tx_status"]["status"] in (
                    "pending",
                    "proposed",
                )
            assert _relay_queue(_metrics(metrics_port))["size"] == 0
            print(
                json.dumps(
                    {
                        "no_peer_drain": queue,
                        "connected_after": source.get_connected_count(),
                        "relayed": hashes,
                        "cycles": sender_cycles,
                    },
                    sort_keys=True,
                )
            )
            self.did_pass = True
        finally:
            for node, name in (
                (source, "source-final.log"),
                (receiver, "receiver-final.log"),
            ):
                consensus_helpers.TestBudgetConsensusIsolation._save_log(
                    node, log_dir, name
                )
            for node in reversed(started):
                node.stop()
            for node in (source, receiver):
                if Path(node.ckb_dir).exists():
                    node.clean()
