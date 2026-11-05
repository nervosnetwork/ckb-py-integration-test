import time

import pytest

from framework.basic import CkbTest
from framework.util import get_project_root


class TestExceededMaximumCycles(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "contract/node", 8114, 8115
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 2000)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def test_01(self):
        path = f"{get_project_root()}/source/contract/test_cases/spawn_loop_times"
        account = self.Config.MINER_PRIVATE_1
        node = self.node
        deploy_hash = self.Contract.deploy_ckb_contract(
            account, path, enable_type_id=True, api_url=node.getClient().url
        )
        self.Miner.miner_until_tx_committed(node, deploy_hash)
        time.sleep(1)
        tx = self.Contract.build_invoke_ckb_contract(
            account_private=account,
            contract_out_point_tx_hash=deploy_hash,
            contract_out_point_tx_index=0,
            type_script_arg="0x02",
            data="0x1234",
            hash_type="type",
            api_url=node.getClient().url,
            # Always cover the multi-input sighash path, independent of the
            # indexer's coin order and the first selected cell's capacity.
            min_input_count=2,
        )
        assert len(tx["inputs"]) >= 2
        assert len(tx["witnesses"]) == len(tx["inputs"])
        invoke_hash = self.node.getClient().send_test_transaction(tx, "passthrough")
        self.node.getClient().get_transaction(invoke_hash)
        self.Node.wait_get_transaction(self.node, invoke_hash, "rejected")

        rejected = self.node.getClient().get_transaction(invoke_hash)["tx_status"]
        assert "ExceededMaximumCycles" in (rejected["reason"] or ""), rejected

        with pytest.raises(Exception) as exc_info:
            invoke_hash = self.node.getClient().send_transaction(tx, "passthrough")

        expected_error_message = "ExceededMaximumCycles"
        assert (
            expected_error_message in exc_info.value.args[0]
        ), f"Expected substring '{expected_error_message}' not found in actual string '{exc_info.value.args[0]}'"
