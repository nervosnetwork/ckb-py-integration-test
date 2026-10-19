"""PR #5316: observable Accepted pool projections through public RPC.

Internal queued/active/Waiting/Replaced barriers and recent-reject fixtures are
separate gaps; this test deliberately does not infer those states from RPC timing.
"""

import time

from framework.basic import CkbTest


class TestPublicPoolProjection(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_public_projection/node1",
            8512,
            8513,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # The base copier uses a shared report path; pytest retains this log.
        pass

    def _assert_projection(self, hashes, expected_detail, unknown_hash):
        client = self.node.getClient()
        raw = client.get_raw_tx_pool(True)
        info = client.tx_pool_info()
        assert set(raw["pending"]) | set(raw["proposed"]) == set(hashes)
        assert not (set(raw["pending"]) & set(raw["proposed"]))
        assert int(info["pending"], 16) == len(raw["pending"])
        assert int(info["proposed"], 16) == len(raw["proposed"])
        assert int(info["orphan"], 16) == 0
        assert int(info["verify_queue_size"], 16) == 0
        entries = [*raw["pending"].values(), *raw["proposed"].values()]
        assert int(info["total_tx_size"], 16) == sum(
            int(x["size"], 16) for x in entries
        )
        assert int(info["total_tx_cycles"], 16) == sum(
            int(x["cycles"], 16) for x in entries
        )

        ranks = []
        for tx_hash in hashes:
            detail = client.get_pool_tx_detail_info(tx_hash)
            assert detail["entry_status"] == expected_detail
            assert int(detail["pending_count"], 16) == len(raw["pending"])
            assert int(detail["proposed_count"], 16) == len(raw["proposed"])
            rank = int(detail["rank_in_pending"], 16)
            if expected_detail == "proposed":
                assert rank == 0
            else:
                ranks.append(rank)

            status_only = client.get_transaction(tx_hash, "0x1")
            with_body = client.get_transaction(tx_hash, "0x2")
            public_status = "proposed" if expected_detail == "proposed" else "pending"
            assert status_only["transaction"] is None
            assert status_only["tx_status"]["status"] == public_status
            assert with_body["transaction"]["hash"] == tx_hash
            assert with_body["tx_status"]["status"] == public_status
            assert int(status_only["cycles"], 16) == int(
                raw["proposed" if expected_detail == "proposed" else "pending"][
                    tx_hash
                ]["cycles"],
                16,
            )

        if expected_detail != "proposed":
            assert sorted(ranks) == list(range(1, len(raw["pending"]) + 1))

        unknown = client.get_pool_tx_detail_info(unknown_hash)
        assert unknown["entry_status"] == "unknown"
        assert int(unknown["rank_in_pending"], 16) == 0
        for verbosity in ("0x1", "0x2"):
            response = client.get_transaction(unknown_hash, verbosity)
            assert response["transaction"] is None
            assert response["tx_status"]["status"] == "unknown"

    # TEST-MAP: NTD-45
    # TEST-MAP: NTD-46
    def test_pending_gap_proposed_public_status_and_pool_totals(self):
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
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        hashes = [
            self.Tx.send_transfer_self_tx_with_input(
                [split],
                [hex(index)],
                key,
                fee=1000,
                api_url=client.url,
            )
            for index in range(2)
        ]
        unknown_hash = "0x" + "00" * 32
        assert unknown_hash not in hashes
        self._assert_projection(hashes, "pending", unknown_hash)

        short_ids = {tx_hash[:22] for tx_hash in hashes}
        assert len(short_ids) == len(hashes)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            template = client.call(
                "get_block_template", [], try_count=1, timeout=35, verbose=False
            )
            if short_ids <= set(template["proposals"]):
                break
            time.sleep(0.2)
        else:
            raise AssertionError("fixture transactions not proposed")

        self.Miner.miner_with_version(self.node, "0x0")
        self._assert_projection(hashes, "gap", unknown_hash)
        self.Miner.miner_with_version(self.node, "0x0")
        self._assert_projection(hashes, "proposed", unknown_hash)
        self.did_pass = True
