"""Integration regression coverage for nervosnetwork/ckb#5252."""

import os

from framework.basic import CkbTest
from framework.util import run_command


class TestFeeRateStatisticsMissingTxSizes(CkbTest):
    """Exercise fee statistics against imported BlockExt records without tx sizes."""

    LEGACY_BLOCK_COUNT = 5

    @classmethod
    def setup_class(cls):
        cls.source_node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "fee_rate_statistics/5252/source",
            25214,
            25215,
        )
        cls.target_node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "fee_rate_statistics/5252/target",
            25216,
            25217,
        )

        cls.source_node.prepare()
        cls.source_node.start()
        cls.Miner.make_tip_height_number(cls.source_node, cls.LEGACY_BLOCK_COUNT)
        cls.source_node.stop()
        cls.source_node.rmLockFile()

        export_dir = os.path.join(cls.source_node.ckb_dir, "export")
        run_command("mkdir -p {}".format(export_dir))
        run_command(
            "cd {} && ./ckb export --target {} --from 1 --to {}".format(
                cls.source_node.ckb_dir,
                export_dir,
                cls.LEGACY_BLOCK_COUNT,
            )
        )
        export_file = os.path.join(
            export_dir,
            "ckb_dev-1-{}.jsonl".format(cls.LEGACY_BLOCK_COUNT),
        )

        cls.target_node.prepare()
        cls.target_node.start()
        assert cls.target_node.getClient().get_tip_block_number() == 0
        cls.target_node.stop()
        cls.target_node.rmLockFile()

        # The skip-all verification path persists successful BlockExt records
        # with txs_sizes=None, matching the database state addressed by CKB#5252.
        run_command(
            "cd {} && ./ckb import --skip-all-verify {}".format(
                cls.target_node.ckb_dir,
                export_file,
            )
        )
        cls.target_node.start()
        assert (
            cls.target_node.getClient().get_tip_block_number() == cls.LEGACY_BLOCK_COUNT
        )

    @classmethod
    def teardown_class(cls):
        for node_name in ("target_node", "source_node"):
            node = getattr(cls, node_name, None)
            if node is not None:
                node.stop()
                node.clean()

    def test_01_all_missing_tx_sizes_return_null_without_panicking(self):
        """TP-INTEGRATION-RPC-FEE-STAT-5252-01."""
        result = self.target_node.getClient().get_fee_rate_statistics(
            self.LEGACY_BLOCK_COUNT
        )

        assert result is None
        assert (
            self.target_node.getClient().get_tip_block_number()
            == self.LEGACY_BLOCK_COUNT
        )
        self.did_pass = True

    def test_02_missing_tx_sizes_are_skipped_when_valid_data_exists(self):
        """TP-INTEGRATION-RPC-FEE-STAT-5252-02."""
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            140,
            self.target_node.client.url,
            fee_rate=1000,
        )
        self.Miner.miner_until_tx_committed(self.target_node, tx_hash)

        target = self.target_node.getClient().get_tip_block_number()
        result = self.target_node.getClient().get_fee_rate_statistics(target)

        assert result == {"mean": "0x3e8", "median": "0x3e8"}
        self.did_pass = True
