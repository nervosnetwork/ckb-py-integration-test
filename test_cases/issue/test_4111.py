# https://github.com/nervosnetwork/ckb/pull/4111
import time

import pytest

from framework.basic import CkbTest
from framework.util import get_project_root


class Test4111(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. start node in issue/node1
        2. miner 400 block
        Returns:

        """

        # 1. start node in issue/node1
        node1 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "issue/node1", 8914, 8927
        )
        cls.node = node1
        node1.prepare(other_ckb_config={"ckb_logger_filter": "debug"})
        node1.start()

        # 2. miner 400 block
        cls.Miner.make_tip_height_number(cls.node, 400)

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestClass1")
        cls.node.stop()
        cls.node.clean()

    # TEST-MAP: VT2-53
    # TEST-EVIDENCE-BEGIN: VT2-53
    # Evidence | Case: VT2-53 (covered)
    # Evidence | Setup: Confirmed99CKB ordinary input and signed100CKB single output; valid98CKB control reuses same input with correct new signature.
    # Evidence | Trigger: Both RPC endpoints receive invalid negative-fee candidate, then positive-fee control is previewed/submitted.
    # Evidence | Observe: Actual99/100 capacities, -1108 Malformed/Overflow, unchanged pool; legal98CKB control admitted with matching positive cycles.
    # Evidence | Assert: assert (
    # Evidence |             int(
    # Evidence |                 client.get_transaction(funding)["transaction"]["outputs"][0][
    # Evidence |                     "capacity"
    # Evidence |                 ],
    # Evidence |                 16,
    # Evidence |             )
    # Evidence |             == 99 * 100_000_000
    # Evidence |         )
    # Evidence | Assert: assert int(invalid["outputs"][0]["capacity"], 16) == 100 * 100_000_000
    # Evidence | Assert: assert error["code"] == -1108, error
    # Evidence | Assert: assert "Malformed" in error["data"] and "Overflow" in error["data"], error
    # Evidence | Assert: assert client.get_raw_tx_pool(False) == before
    # Evidence | Assert: assert int(valid["outputs"][0]["capacity"], 16) == 98 * 100_000_000
    # Evidence | Assert: assert result["tx_status"]["status"] in ("pending", "proposed")
    # Evidence | Assert: assert int(result["cycles"], 16) == int(preview["cycles"], 16) > 0
    # Evidence | Assert: assert tx_hash in client.get_raw_tx_pool(False)["pending"]
    # Evidence | Limit: Intended public fee precheck, not later contextual OutputsSumOverflow.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-53
    def test_output_capacity_exceeding_inputs_is_rejected(self):
        import requests

        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        address = self.Ckb_cli.util_key_info_by_private_key(key)["address"]["testnet"]
        funding = self.Ckb_cli.wallet_transfer_by_private_key(
            key, address, 99, api_url=client.url, fee_rate="1000"
        )
        self.Miner.miner_until_tx_committed(self.node, funding)
        assert (
            int(
                client.get_transaction(funding)["transaction"]["outputs"][0][
                    "capacity"
                ],
                16,
            )
            == 99 * 100_000_000
        )
        invalid = self.Tx.build_send_transfer_self_tx_with_input(
            [funding], ["0x0"], key, fee=-100_000_000, api_url=client.url
        )
        assert int(invalid["outputs"][0]["capacity"], 16) == 100 * 100_000_000
        before = client.get_raw_tx_pool(False)
        for method in ("test_tx_pool_accept", "send_transaction"):
            response = requests.post(
                client.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": [invalid, "passthrough"],
                },
                timeout=30,
            )
            response.raise_for_status()
            error = response.json()["error"]
            assert error["code"] == -1108, error
            assert "Malformed" in error["data"] and "Overflow" in error["data"], error
            assert client.get_raw_tx_pool(False) == before
        # Same confirmed input, ordinary positive fee and a valid signature.
        valid = self.Tx.build_send_transfer_self_tx_with_input(
            [funding], ["0x0"], key, fee=100_000_000, api_url=client.url
        )
        assert int(valid["outputs"][0]["capacity"], 16) == 98 * 100_000_000
        preview = client.test_tx_pool_accept(valid, "passthrough")
        tx_hash = client.send_transaction(valid)
        result = client.get_transaction(tx_hash)
        assert result["tx_status"]["status"] in ("pending", "proposed")
        assert int(result["cycles"], 16) == int(preview["cycles"], 16) > 0
        assert tx_hash in client.get_raw_tx_pool(False)["pending"]
        self.did_pass = True

    def deploy_and_invoke(self, account, path, node, try_count=5):
        if try_count < 0:
            raise Exception("try out of times")
        try:
            deploy_hash = self.Contract.deploy_ckb_contract(
                account, path, enable_type_id=True, api_url=node.getClient().url
            )
            self.Miner.miner_until_tx_committed(node, deploy_hash)
            time.sleep(1)
            invoke_hash = self.Contract.invoke_ckb_contract(
                account_private=account,
                contract_out_point_tx_hash=deploy_hash,
                contract_out_point_tx_index=0,
                type_script_arg="0x02",
                data="0x1234",
                hash_type="type",
                api_url=node.getClient().url,
                fee=-100,
            )
            return invoke_hash
        except Exception as e:
            if "Resolve failed Dead" in e.args[0]:
                try_count -= 1
                time.sleep(3)
                return self.deploy_and_invoke(account, path, node, try_count)
            raise Exception(e.args[0])
