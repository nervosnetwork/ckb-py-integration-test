"""PR #5316: public recovery of detached producers and accepted descendants."""

import json
import tempfile
import time
from pathlib import Path

from framework.basic import CkbTest


class TestReorgRecovery(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_reorg_recovery/node1",
            8534,
            8535,
        )
        cls.peer = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_reorg_recovery/node2",
            8536,
            8537,
        )
        for node in (cls.node, cls.peer):
            node.prepare()
            node.start()
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
        # Keep the pytest log; the base copier uses a shared report path.
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
        raw = self.node.getClient().get_raw_tx_pool(False)
        return set(raw["pending"]) | set(raw["proposed"])

    # TEST-MAP: NTD-20
    # TEST-MAP: NTD-22
    def test_reorg_readmits_detached_parent_and_preserves_valid_pool_children(self):
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
            output_count=6,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)
        self._sync_common_chain()
        common_tip = client.get_tip_header()["hash"]

        parent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            output_count=3,
            fee=1000,
            api_url=client.url,
        )
        short_id = parent[:22]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if short_id in client.get_block_template()["proposals"]:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("parent proposal did not enter the template")
        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        assert client.get_transaction(parent)["tx_status"]["status"] == "proposed"

        # These are resolved while the producer has no committed block origin.
        child = self.Tx.send_transfer_self_tx_with_input(
            [parent],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        dep_reader = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x3"],
            key,
            dep_cells=[{"tx_hash": parent, "index_hex": "0x1"}],
            fee=1000,
            api_url=client.url,
        )
        # The group's only member is parent:2, distinct from the direct code
        # dep at parent:1. It was resolved while parent had no block origin.
        group_data = (
            "0x"
            + (1).to_bytes(4, "little").hex()
            + parent[2:]
            + (2).to_bytes(4, "little").hex()
        )
        group_container = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x4"],
            key,
            data=group_data,
            fee=1000,
            api_url=client.url,
        )
        with tempfile.TemporaryDirectory(prefix="pr5316-reorg-depgroup-") as directory:
            tx_file = str(Path(directory) / "reader.json")
            self.Ckb_cli.tx_init(tx_file, client.url)
            self.Ckb_cli.tx_add_multisig_config(
                account["address"]["testnet"],
                tx_file,
                client.url,
            )
            self.Ckb_cli.tx_add_input(split, 5, tx_file, client.url)
            output = client.get_transaction(split)["transaction"]["outputs"][5]
            self.Ckb_cli.tx_add_type_out_put(
                output["lock"]["code_hash"],
                output["lock"]["hash_type"],
                output["lock"]["args"],
                hex(int(output["capacity"], 16) - 1000),
                "0x",
                tx_file,
                False,
            )
            self.Ckb_cli.tx_add_cell_dep(group_container, "0x0", tx_file)
            with open(tx_file, encoding="utf-8") as stream:
                tx_document = json.load(stream)
            group_dep = next(
                dep
                for dep in tx_document["transaction"]["cell_deps"]
                if dep["out_point"]["tx_hash"] == group_container
            )
            group_dep["dep_type"] = "dep_group"
            with open(tx_file, "w", encoding="utf-8") as stream:
                json.dump(tx_document, stream)
            signature = self.Ckb_cli.tx_sign_inputs(key, tx_file, client.url)[0]
            self.Ckb_cli.tx_add_signature(
                signature["lock-arg"],
                signature["signature"],
                tx_file,
                client.url,
            )
            group_reader_tx = self.Tx.build_tx_info(tx_file)
        assert {
            "out_point": {"tx_hash": group_container, "index": "0x0"},
            "dep_type": "dep_group",
        } in group_reader_tx["cell_deps"]
        group_reader = client.send_transaction(group_reader_tx)
        assert (
            client.get_transaction(group_container)["transaction"]["outputs_data"][0]
            == group_data
        )
        assert {
            parent,
            child,
            dep_reader,
            group_container,
            group_reader,
        } <= self._pool_hashes()
        self.Miner.miner_until_tx_committed(self.node, parent)
        assert client.get_transaction(parent)["tx_status"]["status"] == "committed"
        assert {child, dep_reader, group_container, group_reader} <= self._pool_hashes()

        unrelated = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=1000,
            api_url=client.url,
        )
        attached_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x2"],
            key,
            fee=1000,
            api_url=client.url,
        )
        attached = client.send_transaction(attached_tx)
        assert peer_client.send_transaction(attached_tx) == attached
        old_pool = {
            child,
            dep_reader,
            group_container,
            group_reader,
            unrelated,
            attached,
        }
        assert self._pool_hashes() == old_pool

        self.Miner.miner_until_tx_committed(self.peer, attached)
        self.Miner.make_tip_height_number(self.peer, client.get_tip_block_number() + 2)
        old_tip = client.get_tip_header()["hash"]
        new_tip = peer_client.get_tip_header()["hash"]
        assert new_tip != old_tip
        assert peer_client.get_tip_block_number() > client.get_tip_block_number()
        assert peer_client.get_transaction(parent)["tx_status"]["status"] == "unknown"
        self.node.connected(self.peer)

        # The independently known old Accepted set plus detached non-cellbase
        # parent, minus the transaction already attached on the winning fork.
        expected = (old_pool | {parent}) - {attached}
        assert expected == {
            parent,
            child,
            dep_reader,
            group_container,
            group_reader,
            unrelated,
        }
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            tip = client.get_tip_header()["hash"]
            pool_tip = client.tx_pool_info()["tip_hash"]
            pool = self._pool_hashes()
            if tip == new_tip and pool_tip == new_tip and pool == expected:
                break
            time.sleep(0.5)
        else:
            raise AssertionError(
                f"reorg recovery unsettled: tip={tip}, pool_tip={pool_tip}, "
                f"pool={pool}, expected={expected}"
            )

        assert client.get_transaction(attached)["tx_status"]["status"] == "committed"
        for tx_hash in expected:
            result = client.get_transaction(tx_hash)
            assert result["transaction"]["hash"] == tx_hash
            assert result["tx_status"]["status"] in ("pending", "proposed")
        assert (
            client.get_transaction(group_container)["transaction"]["outputs_data"][0]
            == group_data
        )
        assert (
            client.get_transaction(group_reader)["transaction"]["cell_deps"]
            == group_reader_tx["cell_deps"]
        )
        assert (
            client.get_live_cell_with_include_tx_pool(
                "0x2",
                parent,
                include_tx_pool=False,
            )["status"]
            == "unknown"
        )
        assert (
            client.get_live_cell_with_include_tx_pool(
                "0x2",
                parent,
                include_tx_pool=True,
            )["status"]
            == "live"
        )
        assert client.get_transaction(parent)["tx_status"]["block_hash"] is None
        print(
            f"common_tip={common_tip}; old_tip={old_tip}; "
            f"new_tip={new_tip}; retained={sorted(expected)}"
        )
        self.did_pass = True
