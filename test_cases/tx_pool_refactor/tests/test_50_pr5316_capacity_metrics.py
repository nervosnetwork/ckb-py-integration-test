"""PR #5316: capacity-rejection diagnostics and settled pipeline gauges."""

import json
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests import (
    test_45_pr5316_time_budget_retry as budget_helpers,
)


def _three_ports():
    sockets = [socket.socket() for _ in range(3)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def _metric(metrics, name, label, value):
    pattern = re.compile(
        rf'^{re.escape(name)}\{{{label}="{value}"\}}\s+(\d+)(?:\.0+)?$',
        re.MULTILINE,
    )
    match = pattern.search(metrics)
    assert match is not None, f'missing {name}{{{label}="{value}"}}'
    return int(match.group(1))


def _metrics(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=2) as response:
        return response.read().decode("utf-8")


def _rejection(log_path, tx_hash):
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
        ):
            return event
    return None


class TestCapacityMetrics(CkbTest):
    def teardown_method(self, method):
        # Keep pytest's focused execution trace instead of copying a shared report.
        pass

    # TEST-MAP: NTD-74
    def test_capacity_refusal_has_bounded_diagnostics_and_settled_gauges(self):
        binary = budget_helpers._fixed_test_binary(None)
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "60b8e34" in version, version
        print(f"fixed test binary: {version}; sha256={budget_helpers._sha256(binary)}")

        rpc_port, p2p_port, metrics_port = _three_ports()
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_capacity_metrics/{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        shutil.copy2(binary, node.ckb_bin_path)
        budget_helpers.TestTimeBudgetRetry._set_budget(node, 1)
        with Path(node.ckb_toml_path).open("a", encoding="utf-8") as config:
            config.write(
                "\n[metrics.exporter.prometheus]\n"
                'target = { type = "prometheus", '
                f'listen_address = "127.0.0.1:{metrics_port}" }}\n'
            )
        started = False
        try:
            node.start()
            started = True
            client = node.getClient()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    _metrics(metrics_port)
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise AssertionError("metrics exporter did not start")

            self.Miner.make_tip_height_number(node, 30)
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
            self.Miner.miner_until_tx_committed(node, fund)
            candidate = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )

            baseline = _metrics(metrics_port)
            before_capacity = _metric(
                baseline,
                "ckb_tx_pool_pipeline_rejections",
                "class",
                "capacity",
            )
            before_active = _metric(
                baseline,
                "ckb_tx_pool_pipeline_residency",
                "resource",
                "active_work",
            )
            before_entries = _metric(
                baseline,
                "ckb_tx_pool_pipeline_residency",
                "resource",
                "total_entries",
            )
            assert before_active == 0 and before_entries == 0

            tx_hash = client.notify_transaction(candidate)
            log_path = Path(node.ckb_dir) / "node.log"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                event = _rejection(log_path, tx_hash)
                if event is not None:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("proposal capacity rejection was not logged")
            assert event["reason"] == "ExcessiveVerifyTime", event
            assert event["stage"] == "verify" and event["source"] == "proposal", event
            assert event["resource_observation"] == "rejection_preparation", event
            assert event["schema"] == 1
            assert isinstance(event["accounts"], list) and len(event["accounts"]) <= 5
            for account in event["accounts"]:
                assert account["account"] in {
                    "accepted",
                    "pipeline",
                    "remote",
                    "peer",
                    "history",
                }, account
                assert set(account) == {"account", "usage", "limit"}, account
                assert set(account["usage"]) == {
                    "items",
                    "bytes",
                    "edges",
                    "serialized",
                    "cycles",
                }, account
            assert tx_hash not in client.get_raw_tx_pool(False)["pending"]
            assert tx_hash not in client.get_raw_tx_pool(False)["proposed"]

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                after = _metrics(metrics_port)
                capacity = _metric(
                    after,
                    "ckb_tx_pool_pipeline_rejections",
                    "class",
                    "capacity",
                )
                active = _metric(
                    after,
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "active_work",
                )
                entries = _metric(
                    after,
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "total_entries",
                )
                if (
                    capacity >= before_capacity + 1
                    and active == before_active
                    and entries == before_entries
                ):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    f"metrics did not settle: capacity={capacity}, "
                    f"active={active}, entries={entries}"
                )
            assert capacity == before_capacity + 1
            for line in after.splitlines():
                if line.startswith("ckb_tx_pool_pipeline_") and "{" in line:
                    assert tx_hash[2:] not in line, line
            print(
                f"capacity_rejections={before_capacity}->{capacity}; "
                f"active_work={before_active}->{active}; "
                f"total_entries={before_entries}->{entries}; "
                f"stage={event['stage']}; reason={event['reason']}"
            )
            self.did_pass = True
        finally:
            if started:
                node.stop()
            node.clean()
