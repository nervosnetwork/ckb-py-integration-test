"""PR #5316: a cell-dep reader must be packed before its spender."""

import copy
import time

from framework.basic import CkbTest


class TestReadBeforeSpend(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_read_before_spend/node1",
            8498,
            8499,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # Keep the pytest log; CkbTest's report copier uses a shared path.
        pass

    # TEST-MAP: NTD-28
    def test_reader_and_its_parent_precede_spender_in_valid_block(self):
        """
        1. 准备资金并构造父交易、依赖读取者与花费者。
        2. 核对交易池中的依赖关系和祖先数量。
        3. 推进提案窗口并重复读取模板，检查读取者先于花费者。
        4. 提交区块，确认交易上链且后续模板不再包含它们。
        """

        # 1. 准备资金并构造父交易、依赖读取者与花费者。
        client = self.node.getClient()
        private_key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(private_key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            private_key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            private_key,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        # P -> A is an input dependency. A reads X as a cell dep, while B
        # spends X. The read-before-spend edge A -> B is not fee ancestry.
        parent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            private_key,
            fee=1000,
            api_url=client.url,
        )
        reader = self.Tx.send_transfer_self_tx_with_input(
            [parent],
            ["0x0"],
            private_key,
            dep_cells=[{"tx_hash": split, "index_hex": "0x1"}],
            fee=1000,
            api_url=client.url,
        )
        spender = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            private_key,
            fee=100_000,
            api_url=client.url,
        )

        # 2. 核对交易池中的依赖关系和祖先数量。
        expected = {parent, reader, spender}
        raw = client.get_raw_tx_pool(True)
        entries = {**raw["pending"], **raw["proposed"]}
        assert set(entries) == expected
        assert int(entries[reader]["ancestors_count"], 16) == 2
        assert int(entries[spender]["ancestors_count"], 16) == 1

        # 3. 推进提案窗口并重复读取模板，检查读取者先于花费者。
        short_ids = {tx_hash[:22] for tx_hash in expected}
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if short_ids <= set(client.get_block_template()["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("read-before-spend transactions were not proposed")

        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        tip_hash = client.get_tip_header()["hash"]
        for _ in range(3):
            # Repeated public reads can return one current template; they
            # check visible ordering, not the internal liveness memo.
            template = client.get_block_template()
            assert template["parent_hash"] == tip_hash
            selected = [tx["hash"] for tx in template["transactions"]]
            assert set(selected) == expected
            assert (
                selected.index(parent)
                < selected.index(reader)
                < selected.index(spender)
            )

        # 4. 提交区块，确认交易上链且后续模板不再包含它们。
        block = self.Miner.block_template_transfer_to_submit_block(
            copy.deepcopy(template)
        )
        block_hash = client.submit_block(template["work_id"], block)
        assert block_hash.startswith("0x")
        for tx_hash in expected:
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "committed"
        next_template = client.get_block_template()
        assert next_template["parent_hash"] == block_hash
        assert expected.isdisjoint(tx["hash"] for tx in next_template["transactions"])
        self.did_pass = True
