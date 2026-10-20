from framework.basic import CkbTest
import copy
import tempfile
from pathlib import Path

from test_cases.tx_pool_refactor.dao_precheck.support import (
    ckb_hash,
    outpoint_bytes,
    transaction_hash,
    uint,
)


class TestCkbCliMultisig203(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. start 1 ckb node in tmp/ckb_cli/node dir
        2. miner 2 block
        Returns:

        """
        # 1. start 1 ckb node in tmp/ckb_cli/node dir
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_cli/node", 8314, 8315
        )
        cls.node.prepare()
        cls.node.start()
        # 2. miner 2 block
        cls.Miner.make_tip_height_number(cls.node, 2)

    @classmethod
    def teardown_class(cls):
        """
        1. stop ckb node
        2. clean ckb node tmp dir
        Returns:

        """
        print("stop node and clean")
        cls.node.stop()
        cls.node.clean()

    # TEST-MAP: VT2-27
    def test_01_dep_groups_enable_type_id_true(self):
        """
        1. 为合约和 dep_group 启用 TYPE_ID，部署并取得 dep_group 交易 hash。
        2. 挖矿确认部署交易，按首个输入和输出索引核对 TYPE_ID args。
        3. 换用另一已确认输入并复用原 TYPE_ID args，检查创建哈希不匹配的交易被拒绝。
        """
        # 1. 启用合约和 dep_group 的 TYPE_ID，返回 dep_group 部署交易 hash。
        dep_group_tx_hash = self.Contract.deploy_ckb_contract(
            self.Config.ACCOUNT_PRIVATE_2,
            self.Config.ALWAYS_SUCCESS_CONTRACT_PATH,
            enable_type_id=True,
            dep_groups_enable_type_id=True,
            return_tx="dep_group_tx",
            api_url=self.node.getClient().url,
        )
        # 2. 确认部署交易，检查类型脚本存在且 args 等于独立计算的 32 字节哈希。
        tx_response = self.Miner.miner_until_tx_committed(self.node, dep_group_tx_hash)
        output = tx_response["transaction"]["outputs"][0]
        output_type = output.get("type")
        assert (
            output_type is not None
        ), "Failed: Output does not contain a 'type' field."
        first_input = tx_response["transaction"]["inputs"][0]
        expected = ckb_hash(
            uint(first_input["since"], 8)
            + outpoint_bytes(first_input["previous_output"])
            + uint(0, 8)
        )
        assert output_type["args"] == expected
        assert len(bytes.fromhex(output_type["args"][2:])) == 32

        # 3. 使用另一输入复用原 TYPE_ID args，检查创建哈希不匹配的交易被拒绝。
        self._assert_wrong_creation_hash_rejected(output_type)
        self.did_pass = True

    def _assert_wrong_creation_hash_rejected(self, existing_type):
        """A separate confirmed input prevents spent-input checks hiding Type ID."""
        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        address = self.Ckb_cli.util_key_info_by_private_key(key)["address"]["testnet"]
        fund_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            address,
            1000,
            api_url=client.url,
            fee_rate="1000",
        )
        funding = self.Miner.miner_until_tx_committed(self.node, fund_hash)[
            "transaction"
        ]
        output = copy.deepcopy(funding["outputs"][0])
        output["capacity"] = hex(int(output["capacity"], 16) - 100_000)
        output["type"] = copy.deepcopy(existing_type)
        point = {"tx_hash": fund_hash, "index": "0x0"}
        correct_hash = ckb_hash(uint(0, 8) + outpoint_bytes(point) + uint(0, 8))
        assert output["type"]["args"] != correct_hash
        assert len(bytes.fromhex(output["type"]["args"][2:])) == 32
        with tempfile.TemporaryDirectory(prefix="type-id-creation-") as directory:
            tx_file = str(Path(directory) / "transaction.json")
            self.Ckb_cli.tx_init(tx_file, client.url)
            self.Ckb_cli.tx_add_input(fund_hash, 0, tx_file, client.url)
            self.Ckb_cli.tx_add_output(output, "0x", tx_file)
            signatures = self.Ckb_cli.tx_sign_inputs(key, tx_file, client.url)
            for signature in signatures:
                self.Ckb_cli.tx_add_signature(
                    signature["lock-arg"],
                    signature["signature"],
                    tx_file,
                    client.url,
                )
            invalid = self.Tx.build_tx_info(tx_file)
        for method in ("test_tx_pool_accept", "send_transaction"):
            with self.assertRaises(Exception) as error:
                client.call(method, [invalid, "passthrough"])
            reason = str(error.exception)
            assert "ValidationFailure" in reason and "error code -3" in reason, reason
            assert "Outputs[0].Type" in reason, reason
            pool = client.get_raw_tx_pool(False)
            tx_hash = transaction_hash(invalid)
            assert tx_hash not in pool["pending"] and tx_hash not in pool["proposed"]

    def test_02_dep_groups_enable_type_id_false(self):
        # 1. deploy contract return deploy tx hash
        dep_group_tx_hash = self.Contract.deploy_ckb_contract(
            self.Config.ACCOUNT_PRIVATE_2,
            self.Config.ALWAYS_SUCCESS_CONTRACT_PATH,
            enable_type_id=True,
            dep_groups_enable_type_id=False,
            return_tx="dep_group_tx",
            api_url=self.node.getClient().url,
        )
        # 2. miner until deploy contrct tx hash
        tx_response = self.Miner.miner_until_tx_committed(self.node, dep_group_tx_hash)
        output = tx_response["transaction"]["outputs"][0]
        # 3. check hash_type
        assert (
            output.get("type") is None
        ), f"Failed: type field should be None, got {output.get('type')}"

    def test_03_dep_groups_enable_type_id_old(self):
        # 1. deploy contract return deploy tx hash
        dep_group_tx_hash = self.Contract.deploy_ckb_contract(
            self.Config.ACCOUNT_PRIVATE_2,
            self.Config.ALWAYS_SUCCESS_CONTRACT_PATH,
            enable_type_id=True,
            dep_groups_enable_type_id="old",
            return_tx="dep_group_tx",
            api_url=self.node.getClient().url,
        )
        # 2. miner until deploy contrct tx hash
        tx_response = self.Miner.miner_until_tx_committed(self.node, dep_group_tx_hash)
        output = tx_response["transaction"]["outputs"][0]
        # 3. check hash_type
        assert (
            output.get("type") is None
        ), f"Failed: type field should be None, got {output.get('type')}"
