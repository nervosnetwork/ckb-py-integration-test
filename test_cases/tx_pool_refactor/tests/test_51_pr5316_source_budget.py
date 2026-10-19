"""PR #5316: a network time-budget refusal does not bar local submission."""

import json
import shutil
import subprocess
import time
from pathlib import Path

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests import (
    test_45_pr5316_time_budget_retry as budget_helpers,
)
from test_cases.tx_pool_refactor.tests import (
    test_50_pr5316_capacity_metrics as metric_helpers,
)


class TestSourceBudget(CkbTest):
    def teardown_method(self, method):
        # Preserve the focused pytest output instead of the shared base report.
        pass

    # TEST-MAP: EVT-01
    def test_proposal_budget_refusal_does_not_block_same_hash_local_submission(self):
        binary = budget_helpers._fixed_test_binary(None)
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "60b8e34" in version, version
        print(f"fixed test binary: {version}; sha256={budget_helpers._sha256(binary)}")

        rpc_port, p2p_port, metric_port = metric_helpers._three_ports()
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_source_budget/{rpc_port}",
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
                f'listen_address = "127.0.0.1:{metric_port}" }}\n'
            )
        started = False
        try:
            node.start()
            started = True
            client = node.getClient()
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

            proposal_hash = client.notify_transaction(candidate)
            log_path = Path(node.ckb_dir) / "node.log"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                event = metric_helpers._rejection(log_path, proposal_hash)
                if event is not None:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("proposal budget refusal did not settle")
            assert event["reason"] == "ExcessiveVerifyTime", event
            assert event["source"] == "proposal" and event["stage"] == "verify", event
            assert proposal_hash not in {
                *client.get_raw_tx_pool(False)["pending"],
                *client.get_raw_tx_pool(False)["proposed"],
            }
            assert (
                client.get_transaction(proposal_hash)["tx_status"]["status"]
                == "unknown"
            )
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                before_local = metric_helpers._metric(
                    metric_helpers._metrics(metric_port),
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "active_work",
                )
                if before_local == 0:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    f"proposal active_work did not settle: {before_local}"
                )

            # The exact signed bytes are submitted locally in the same running
            # generation. A cached failure or a source-blind 1 ms cap would fail.
            local_hash = client.send_transaction(candidate)
            assert local_hash == proposal_hash
            accepted = client.get_transaction(local_hash)
            assert accepted["tx_status"]["status"] in ("pending", "proposed"), accepted
            assert accepted["transaction"]["hash"] == local_hash
            assert int(accepted["cycles"], 16) > 0
            raw = client.get_raw_tx_pool(False)
            assert local_hash in set(raw["pending"]) | set(raw["proposed"])

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                metrics = metric_helpers._metrics(metric_port)
                active = metric_helpers._metric(
                    metrics,
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "active_work",
                )
                if active == 0:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(f"active_work did not settle: {active}")
            print(
                json.dumps(
                    {
                        "proposal_reason": event["reason"],
                        "proposal_source": event["source"],
                        "local_status": accepted["tx_status"]["status"],
                        "local_cycles": accepted["cycles"],
                        "active_after_proposal": before_local,
                        "active_after_local": active,
                        "same_hash": local_hash == proposal_hash,
                    },
                    sort_keys=True,
                )
            )
            self.did_pass = True
        finally:
            if started:
                node.stop()
            node.clean()
