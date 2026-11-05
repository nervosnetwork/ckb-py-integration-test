"""PR #5316: a chain-attached spend invalidates pool input/dep readers."""

import json
import time
from unittest.mock import patch

from framework.basic import CkbTest


class TestChainConflict(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_chain_conflict/node1",
            8492,
            8493,
        )
        cls.peer = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_chain_conflict/node2",
            8494,
            8495,
        )
        cls.node.prepare()
        cls.peer.prepare()
        cls.node.start()
        cls.peer.start()
        assert cls.node.getClient().get_block_hash(
            "0x0"
        ) == cls.peer.getClient().get_block_hash("0x0")
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        for node in (cls.node, cls.peer):
            node.stop()
            node.clean()

    def teardown_method(self, method):
        # The base copier uses a shared report path; pytest retains this run's log.
        pass

    def _sync_common_chain(self):
        source = self.node.getClient()
        destination = self.peer.getClient()
        for height in range(1, source.get_tip_block_number() + 1):
            block = source.get_block_by_number(hex(height))
            block["header"].pop("hash", None)
            for tx in block["transactions"]:
                tx.pop("hash", None)
            for uncle in block["uncles"]:
                uncle["header"].pop("hash", None)
            destination.submit_block("0x0", block)
        expected = source.get_tip_header()["hash"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if (
                destination.get_tip_header()["hash"] == expected
                and destination.tx_pool_info()["tip_hash"] == expected
            ):
                return
            time.sleep(0.2)
        raise AssertionError("peer did not install the common chain")

    def _pool_hashes(self):
        pool = self.node.getClient().get_raw_tx_pool(True)
        return set(pool["pending"]) | set(pool["proposed"])

    # TEST-MAP: NTD-19
    def test_attached_spend_evicts_input_and_dep_readers_with_descendants(self):
        """
        1. 准备两个节点共享的已上链输入。
        2. 构造输入消费者、依赖读取者、各自后代及无关交易。
        3. 在竞争链上花费冲突输入并同步较长链。
        4. 确认冲突分支被逐出且无关交易保留。
        5. 再验证已上链父交易的有效池内子交易不会被误删。
        """

        # 1. 准备两个节点共享的已上链输入。
        client = self.node.getClient()
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2
        )
        fund_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund_hash)
        split_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            output_count=4,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split_hash)
        self._sync_common_chain()  # Both nodes share the committed cells, then diverge.

        # 2. 构造输入消费者、依赖读取者、各自后代及无关交易。
        input_consumer = self.Tx.send_transfer_self_tx_with_input(
            [split_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        input_child = self.Tx.send_transfer_self_tx_with_input(
            [input_consumer],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        dep_reader = self.Tx.send_transfer_self_tx_with_input(
            [split_hash],
            ["0x2"],
            self.Config.ACCOUNT_PRIVATE_2,
            dep_cells=[{"tx_hash": split_hash, "index_hex": "0x1"}],
            fee=1000,
            api_url=client.url,
        )
        dep_child = self.Tx.send_transfer_self_tx_with_input(
            [dep_reader],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        unrelated = self.Tx.send_transfer_self_tx_with_input(
            [split_hash],
            ["0x3"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        invalidated = {input_consumer, input_child, dep_reader, dep_child}
        assert invalidated | {unrelated} <= self._pool_hashes()

        # 3. 在竞争链上花费冲突输入并同步较长链。
        peer_client = self.peer.getClient()
        attached_spend = self.Tx.send_transfer_self_tx_with_input(
            [split_hash, split_hash],
            ["0x0", "0x1"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=2000,
            api_url=peer_client.url,
        )
        assert (
            self.Miner.miner_until_tx_committed(self.peer, attached_spend)["tx_status"][
                "status"
            ]
            == "committed"
        )
        peer_tip = peer_client.get_tip_header()["hash"]
        self.node.connected(self.peer)

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            tip = client.get_tip_header()["hash"]
            pool = self._pool_hashes()
            if (
                tip == peer_tip
                and client.tx_pool_info()["tip_hash"] == peer_tip
                and invalidated.isdisjoint(pool)
                and unrelated in pool
            ):
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                f"chain conflict not settled: tip={tip}, peer={peer_tip}, "
                f"invalidated_in_pool={invalidated & pool}, unrelated_in_pool={unrelated in pool}"
            )

        # 4. 确认冲突分支被逐出且无关交易保留。
        assert (
            client.get_transaction(attached_spend)["tx_status"]["status"] == "committed"
        )
        assert self._pool_hashes() == {unrelated}
        info = client.tx_pool_info()
        assert int(info["pending"], 16) + int(info["proposed"], 16) == 1
        for tx_hash in invalidated:
            response = client.get_transaction(tx_hash)
            assert response["tx_status"]["status"] not in ("pending", "proposed")

        # Separate branch: an Accepted transaction itself enters the chain.
        # Its child is submitted only after the parent reaches Proposed, so
        # the next block can commit the parent while the child remains in pool.

        # 5. 再验证已上链父交易的有效池内子交易不会被误删。
        attached_parent = self.Tx.send_transfer_self_tx_with_input(
            [split_hash],
            ["0x2"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        assert attached_parent in self._pool_hashes()
        short_id = attached_parent[:22]
        for _ in range(10):
            if short_id in client.get_block_template()["proposals"]:
                break
            self.Miner.miner_with_version(self.node, "0x0")
        else:
            raise AssertionError("attached parent never appeared in proposals")
        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        self.Node.wait_get_transaction(self.node, attached_parent, "proposed")
        attached_child = self.Tx.send_transfer_self_tx_with_input(
            [attached_parent],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        assert attached_child in self._pool_hashes()
        committed = self.Miner.miner_until_tx_committed(self.node, attached_parent)
        assert committed["tx_status"]["status"] == "committed"
        assert attached_parent not in self._pool_hashes()
        assert attached_child in self._pool_hashes()
        assert client.get_transaction(attached_child)["tx_status"]["status"] in (
            "pending",
            "proposed",
        )
        self.did_pass = True
