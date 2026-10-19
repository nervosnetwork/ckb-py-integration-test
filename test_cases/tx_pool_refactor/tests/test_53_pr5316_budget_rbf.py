"""PR #5316: a timed-out proposal must not partially replace accepted owners."""

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


class TestBudgetReplacement(CkbTest):
    def teardown_method(self, method):
        # Retain focused pytest output instead of the shared base report.
        pass

    @staticmethod
    def _owners(client):
        raw = client.get_raw_tx_pool(True)
        return {**raw["pending"], **raw["proposed"]}

    # TEST-MAP: EVT-01
    def test_proposal_timeout_preserves_parent_child_until_same_candidate_local_rbf(
        self,
    ):
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
            f"tx_pool/pr5316_budget_rbf/{rpc_port}",
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

            parent_tx = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            parent = client.send_transaction(parent_tx)
            child_tx = self.Tx.build_send_transfer_self_tx_with_input(
                [parent],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            child = client.send_transaction(child_tx)
            assert parent != child
            assert child_tx["inputs"][0]["previous_output"]["tx_hash"] == parent
            before = self._owners(client)
            assert set(before) == {parent, child}
            before_bodies = {
                tx_hash: client.get_transaction(tx_hash)["transaction"]
                for tx_hash in (parent, child)
            }
            tip = client.get_tip_header()["hash"]

            # Fee exceeds the two incumbents plus the RBF increment. The
            # post-timeout Local replay below proves this exact B is replaceable.
            replacement_tx = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=100_000_000,
                api_url=client.url,
            )
            proposal_hash = client.notify_transaction(replacement_tx)
            log_path = Path(node.ckb_dir) / "node.log"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                refusal = metric_helpers._rejection(log_path, proposal_hash)
                if refusal is not None:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    "replacement Proposal budget refusal did not settle"
                )
            assert refusal["reason"] == "ExcessiveVerifyTime", refusal
            assert (
                refusal["source"] == "proposal" and refusal["stage"] == "verify"
            ), refusal
            assert client.get_tip_header()["hash"] == tip
            assert self._owners(client) == before
            for tx_hash in (parent, child):
                result = client.get_transaction(tx_hash)
                assert result["transaction"] == before_bodies[tx_hash]
                assert result["tx_status"]["status"] in ("pending", "proposed")
            assert (
                client.get_transaction(proposal_hash)["tx_status"]["status"]
                == "unknown"
            )

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                active = metric_helpers._metric(
                    metric_helpers._metrics(metric_port),
                    "ckb_tx_pool_pipeline_residency",
                    "resource",
                    "active_work",
                )
                if active == 0:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(
                    f"timed-out replacement retained active work: {active}"
                )

            # Same bytes through Local prove B really is a valid RBF of A and
            # its dependent C, rather than a candidate rejected for another
            # reason before it could reach the replacement transition.
            local_hash = client.send_transaction(replacement_tx)
            assert local_hash == proposal_hash
            after = self._owners(client)
            assert set(after) == {local_hash}, after
            assert int(after[local_hash]["cycles"], 16) > 0
            assert client.get_tip_header()["hash"] == tip
            print(
                json.dumps(
                    {
                        "before": sorted(before),
                        "after": sorted(after),
                        "proposal_reason": refusal["reason"],
                        "proposal_source": refusal["source"],
                        "same_replacement_hash": local_hash == proposal_hash,
                        "active_after_refusal": active,
                    },
                    sort_keys=True,
                )
            )
            self.did_pass = True
        finally:
            if started:
                node.stop()
            node.clean()
