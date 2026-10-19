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


class TestWaitingChainConflict(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_waiting_chain_conflict/node1",
            8554,
            8555,
        )
        cls.peer = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_waiting_chain_conflict/node2",
            8556,
            8557,
        )
        cls.node.prepare()
        cls.peer.prepare()
        cls.node.start()
        cls.peer.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        for node in (cls.node, cls.peer):
            node.stop()
            node.clean()

    def teardown_method(self, method):
        # Preserve this run's pytest log instead of copying a shared report.
        pass

    # TEST-MAP: NTD-21
    # TEST-MAP: NTD-45
    # TEST-MAP: NTD-46
    def test_attached_spend_removes_waiter_with_other_missing_input(self):
        client = self.node.getClient()
        peer_client = self.peer.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=3,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        # Give the peer exactly the committed chain without connecting the
        # nodes. Its later spend must arrive as an attached-block fact, not a
        # competing Accepted pool transaction on the waiter node.
        for height in range(1, client.get_tip_block_number() + 1):
            block = client.get_block_by_number(hex(height))
            block["header"].pop("hash", None)
            for tx in block["transactions"]:
                tx.pop("hash", None)
            for uncle in block["uncles"]:
                uncle["header"].pop("hash", None)
            peer_client.submit_block("0x0", block)
        assert peer_client.get_tip_header()["hash"] == client.get_tip_header()["hash"]

        parent_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        parent = client.send_transaction(parent_tx)
        waiter_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [parent, split],
            ["0x0", "0x1"],
            key,
            fee=2000,
            api_url=client.url,
        )
        assert waiter_tx["inputs"][0]["previous_output"]["tx_hash"] == parent
        assert waiter_tx["inputs"][1]["previous_output"] == {
            "tx_hash": split,
            "index": "0x1",
        }
        assert client.remove_transaction(parent) is True
        waiter = client.call("notify_transaction", [waiter_tx])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if int(client.tx_pool_info()["orphan"], 16) == 1:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("missing-parent transaction did not enter Waiting")
        assert waiter not in self._accepted_hashes()
        assert client.get_pool_tx_detail_info(waiter)["entry_status"] == "unknown"
        for verbosity in ("0x1", "0x2"):
            waiting_view = client.get_transaction(waiter, verbosity)
            assert waiting_view["transaction"] is None
            assert waiting_view["tx_status"]["status"] == "unknown"

        # Keep an unrelated Accepted owner alongside Waiting. Pool totals
        # must count only that owner, while orphan counts the waiter.
        control = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x2"],
            key,
            fee=1000,
            api_url=client.url,
        )
        raw = client.get_raw_tx_pool(True)
        info = client.tx_pool_info()
        assert set(raw["pending"]) == {control}
        assert raw["proposed"] == {}
        assert int(info["pending"], 16) == 1
        assert int(info["proposed"], 16) == 0
        assert int(info["orphan"], 16) == 1
        assert int(info["verify_queue_size"], 16) == 0
        assert int(info["total_tx_size"], 16) == int(
            raw["pending"][control]["size"], 16
        )
        assert int(info["total_tx_cycles"], 16) == int(
            raw["pending"][control]["cycles"], 16
        )
        assert int(client.get_pool_tx_detail_info(control)["rank_in_pending"], 16) == 1
        assert int(client.get_pool_tx_detail_info(waiter)["rank_in_pending"], 16) == 0

        # An unrelated attached block must leave the same missing-A waiter in
        # Waiting. This separates a B-spend rejection from generic tip churn.
        self.Miner.miner_with_version(self.peer, "0x0")
        control_tip = peer_client.get_tip_header()["hash"]
        control_block = peer_client.get_block(control_tip)
        control_block["header"].pop("hash", None)
        for tx in control_block["transactions"]:
            tx.pop("hash", None)
        for uncle in control_block["uncles"]:
            uncle["header"].pop("hash", None)
        assert client.submit_block("0x0", control_block) == control_tip
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            info = client.tx_pool_info()
            if info["tip_hash"] == control_tip and int(info["orphan"], 16) == 1:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("unrelated block changed the Waiting owner")

        attached_spend = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=1000,
            api_url=peer_client.url,
        )
        assert (
            self.Miner.miner_until_tx_committed(
                self.peer,
                attached_spend,
            )[
                "tx_status"
            ]["status"]
            == "committed"
        )
        peer_tip = peer_client.get_tip_header()["hash"]
        assert int(client.tx_pool_info()["orphan"], 16) == 1
        self.node.connected(self.peer)

        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if (
                client.get_tip_header()["hash"] == peer_tip
                and client.tx_pool_info()["tip_hash"] == peer_tip
                and int(client.tx_pool_info()["orphan"], 16) == 0
            ):
                break
            time.sleep(0.5)
        else:
            raise AssertionError("attached spend did not retire the Waiting owner")
        assert (
            client.get_transaction(attached_spend)["tx_status"]["status"] == "committed"
        )
        assert waiter not in self._accepted_hashes()
        assert client.get_pool_tx_detail_info(waiter)["entry_status"] == "unknown"
        for verbosity in ("0x1", "0x2"):
            outcome = client.get_transaction(waiter, verbosity)
            assert outcome["transaction"] is None
            assert outcome["tx_status"]["status"] == "rejected"
        reason = json.loads(outcome["tx_status"]["reason"])
        assert reason["type"] == "Resolve"
        assert "Dead(OutPoint(" in reason["description"]
        assert split[2:] + "01000000" in reason["description"]

        # Making A available later cannot revive a waiter whose B input was
        # already spent in the attached block.
        assert client.send_transaction(parent_tx) == parent
        assert parent in self._accepted_hashes()
        for _ in range(10):
            assert int(client.tx_pool_info()["orphan"], 16) == 0
            assert waiter not in self._accepted_hashes()
            assert (
                client.get_transaction(waiter)["tx_status"]["reason"]
                == outcome["tx_status"]["reason"]
            )
            time.sleep(0.2)
        print(
            f"waiting-chain-conflict: waiter={waiter}, parent={parent}, "
            f"spent={split}:1, tip={peer_tip}, orphan=1->0"
        )
        self.did_pass = True

    def _accepted_hashes(self):
        raw = self.node.getClient().get_raw_tx_pool(False)
        return set(raw["pending"]) | set(raw["proposed"])


class TestWaitingWakePagination(CkbTest):
    WAITER_COUNT = 33  # One more than the product's WAKE_PAGE = 32.

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_waiting_wake_pagination/node1",
            8558,
            8559,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # Keep this run's pytest log rather than a shared copied report.
        pass

    # TEST-MAP: NTD-16
    def test_shared_trigger_wakes_waiters_across_page_boundary(self):
        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)

        split_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=self.WAITER_COUNT + 1,
            fee=20_000,
            api_url=client.url,
        )
        split = client.send_transaction(split_tx)
        assert len(split_tx["outputs"]) == self.WAITER_COUNT + 1
        self.Miner.miner_until_tx_committed(self.node, split)

        parent_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            [hex(self.WAITER_COUNT)],
            key,
            fee=1000,
            api_url=client.url,
        )
        parent = client.send_transaction(parent_tx)
        shared_dep = {"tx_hash": parent, "index_hex": "0x0"}
        waiters = []
        # Reuse the already resolved local account metadata. The helper's
        # repeated key-info CLI writes a global /tmp/tmp.data path, which is
        # unrelated to the pool behavior and races with other test processes.
        with patch.object(
            self.Tx, "util_key_info_by_private_key", return_value=account
        ):
            for index in range(self.WAITER_COUNT):
                tx = self.Tx.build_send_transfer_self_tx_with_input(
                    [split],
                    [hex(index)],
                    key,
                    fee=1000,
                    api_url=client.url,
                    dep_cells=[shared_dep],
                )
                assert tx["inputs"][0]["previous_output"] == {
                    "tx_hash": split,
                    "index": hex(index),
                }
                assert {
                    "out_point": {"tx_hash": parent, "index": "0x0"},
                    "dep_type": "code",
                } in tx["cell_deps"]
                waiters.append(tx)

        assert client.remove_transaction(parent) is True
        hashes = [
            client.call("notify_transaction", [tx], verbose=False) for tx in waiters
        ]
        assert len(set(hashes)) == self.WAITER_COUNT
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if int(client.tx_pool_info()["orphan"], 16) == self.WAITER_COUNT:
                break
            time.sleep(0.2)
        else:
            raise AssertionError(
                "33 shared-trigger transactions did not all reach Waiting"
            )
        raw = client.get_raw_tx_pool(False)
        assert set(hashes).isdisjoint(set(raw["pending"]) | set(raw["proposed"]))

        assert client.send_transaction(parent_tx) == parent
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            raw = client.get_raw_tx_pool(False)
            accepted = set(raw["pending"]) | set(raw["proposed"])
            if (set(hashes) | {parent}) <= accepted and int(
                client.tx_pool_info()["orphan"], 16
            ) == 0:
                break
            time.sleep(0.2)
        else:
            raise AssertionError(
                f"wake lost shared-trigger waiters: accepted={len(accepted & set(hashes))}/"
                f"{self.WAITER_COUNT}, orphan={client.tx_pool_info()['orphan']}"
            )
        assert len(accepted & set(hashes)) == self.WAITER_COUNT
        for tx_hash in hashes:
            response = client.get_transaction(tx_hash)
            assert response["transaction"]["hash"] == tx_hash
            assert response["tx_status"]["status"] in ("pending", "proposed")
        print(
            f"waiting-pagination: shared_dep={parent}:0, waiters={len(hashes)}, "
            "orphan=33->0, accepted=33"
        )
        self.did_pass = True
