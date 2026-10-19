"""PR #5316: local resolution must reject terminal missing dependencies."""

import copy
import json
import tempfile
import time
from pathlib import Path

import pytest

from framework.basic import CkbTest


class TestResolutionRejection(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_resolution_rejection/node1",
            8530,
            8531,
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

    def _assert_rejected_without_pool_owner(self, transaction, reason, detail=None):
        client = self.node.getClient()
        with pytest.raises(Exception) as failure:
            client.send_transaction(transaction)
        assert "TransactionFailedToResolve" in str(failure.value)
        assert reason.lower() in str(failure.value).lower(), str(failure.value)
        if detail is not None:
            assert detail.lower() in str(failure.value).lower(), str(failure.value)
        pool = client.get_raw_tx_pool(False)
        assert pool["pending"] == []
        assert pool["proposed"] == []
        info = client.tx_pool_info()
        assert int(info["orphan"], 16) == 0
        assert int(info["verify_queue_size"], 16) == 0

    # TEST-MAP: NTD-13
    # TEST-MAP: NTD-15
    def test_unknown_input_does_not_mask_invalid_dep_group_or_header(self):
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
        base = self.Tx.build_send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        assert client.get_transaction(fund)["tx_status"]["status"] == "committed"
        assert client.get_live_cell("0x0", fund)["status"] == "live"

        unknown_input = copy.deepcopy(base)
        unknown_input["inputs"][0]["previous_output"]["tx_hash"] = "0x" + "00" * 32
        self._assert_rejected_without_pool_owner(unknown_input, "unknown", "00" * 32)

        out_of_range = copy.deepcopy(base)
        outputs = client.get_transaction(fund)["transaction"]["outputs"]
        out_of_range["inputs"][0]["previous_output"]["index"] = hex(len(outputs))
        self._assert_rejected_without_pool_owner(out_of_range, "unknown", fund[2:])

        invalid_dep_group = copy.deepcopy(unknown_input)
        invalid_dep_group["cell_deps"].append(
            {
                "out_point": {"tx_hash": fund, "index": "0x0"},
                "dep_type": "dep_group",
            }
        )
        self._assert_rejected_without_pool_owner(
            invalid_dep_group,
            "invaliddepgroup",
            fund[2:],
        )

        invalid_header = copy.deepcopy(unknown_input)
        invalid_header["header_deps"].append("0x" + "ff" * 32)
        self._assert_rejected_without_pool_owner(
            invalid_header,
            "invalidheader",
            "ff" * 32,
        )

        accepted = client.send_transaction(base)
        assert client.get_transaction(accepted)["transaction"]["hash"] == accepted
        pool = client.get_raw_tx_pool(False)
        assert set(pool["pending"]) | set(pool["proposed"]) == {accepted}

        # A well-formed Molecule OutPointVec with zero members is terminal too,
        # even when the same transaction also has an unknown input.
        empty_group_data = "0x" + (0).to_bytes(4, "little").hex()
        empty_group = self.Tx.send_transfer_self_tx_with_input(
            [accepted],
            ["0x0"],
            key,
            data=empty_group_data,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, empty_group)
        group_result = client.get_transaction(empty_group)
        assert group_result["tx_status"]["status"] == "committed"
        assert group_result["transaction"]["outputs_data"][0] == empty_group_data
        assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

        empty_dep_group = copy.deepcopy(unknown_input)
        empty_dep_group["cell_deps"].append(
            {
                "out_point": {"tx_hash": empty_group, "index": "0x0"},
                "dep_type": "dep_group",
            }
        )
        self._assert_rejected_without_pool_owner(
            empty_dep_group,
            "invaliddepgroup",
            empty_group[2:],
        )

        # The same unknown input with an actual canonical header is Unknown,
        # not InvalidHeader: only an invalid header masks it.
        canonical_hash = client.get_tip_header()["hash"]
        valid_header = copy.deepcopy(unknown_input)
        valid_header["header_deps"].append(canonical_hash)
        self._assert_rejected_without_pool_owner(
            valid_header,
            "unknown",
            "00" * 32,
        )

        # Sign with the canonical header already present, so a live input can
        # verify with that header as context rather than just pass resolution.
        with tempfile.TemporaryDirectory(prefix="pr5316-valid-header-") as directory:
            tx_file = str(Path(directory) / "valid-header.json")
            self.Ckb_cli.tx_init(tx_file, client.url)
            self.Ckb_cli.tx_add_multisig_config(
                account["address"]["testnet"],
                tx_file,
                client.url,
            )
            self.Ckb_cli.tx_add_input(empty_group, 0, tx_file, client.url)
            output = group_result["transaction"]["outputs"][0]
            self.Ckb_cli.tx_add_type_out_put(
                output["lock"]["code_hash"],
                output["lock"]["hash_type"],
                output["lock"]["args"],
                hex(int(output["capacity"], 16) - 1000),
                "0x",
                tx_file,
                False,
            )
            self.Ckb_cli.tx_add_header_dep(canonical_hash, tx_file)
            signature = self.Ckb_cli.tx_sign_inputs(key, tx_file, client.url)[0]
            self.Ckb_cli.tx_add_signature(
                signature["lock-arg"],
                signature["signature"],
                tx_file,
                client.url,
            )
            valid_context_tx = self.Tx.build_tx_info(tx_file)
        assert valid_context_tx["header_deps"] == [canonical_hash]
        valid_context_hash = client.send_transaction(valid_context_tx)
        assert (
            client.get_transaction(valid_context_hash)["tx_status"]["status"]
            == "pending"
        )
        self.Miner.miner_until_tx_committed(self.node, valid_context_hash)
        assert (
            client.get_transaction(valid_context_hash)["tx_status"]["status"]
            == "committed"
        )
        self.did_pass = True

    def _wait_for_frontier(self, transaction_hash, expected_orphans, accepted):
        client = self.node.getClient()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            pool = client.get_raw_tx_pool(False)
            present = transaction_hash in set(pool["pending"]) | set(pool["proposed"])
            orphan_count = int(client.tx_pool_info()["orphan"], 16)
            if orphan_count == expected_orphans and present == accepted:
                return
            time.sleep(0.2)
        raise AssertionError(
            f"frontier did not settle: orphan={expected_orphans}, "
            f"accepted={accepted}, tx={transaction_hash}; last orphan={orphan_count}, "
            f"last pool={pool}"
        )

    # TEST-MAP: NTD-14
    def test_missing_input_and_expanded_dep_group_member_wait_for_both(self):
        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            account["address"]["testnet"],
            600000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            fee=1000,
            output_count=3,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        input_parent_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        member_parent_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=1000,
            api_url=client.url,
        )
        member_parent = client.send_transaction(member_parent_tx)
        assert client.remove_transaction(member_parent) is True

        # Molecule OutPointVec is a fixed vector: count + one 36-byte OutPoint.
        group_data = (
            "0x"
            + (1).to_bytes(4, "little").hex()
            + member_parent[2:]
            + (0).to_bytes(4, "little").hex()
        )
        group_parent_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split],
            ["0x2"],
            key,
            data=group_data,
            fee=1000,
            api_url=client.url,
        )
        group_parent = client.send_transaction(group_parent_tx)
        self.Miner.miner_until_tx_committed(self.node, group_parent)
        assert (
            client.get_transaction(group_parent)["transaction"]["outputs_data"][0]
            == group_data
        )
        input_parent = client.send_transaction(input_parent_tx)
        assert client.send_transaction(member_parent_tx) == member_parent

        # Sign while both producers exist, then remove them before proposal
        # admission; local send_transaction rejects Unknown dependencies.
        with tempfile.TemporaryDirectory(prefix="pr5316-depgroup-") as directory:
            tx_file = str(Path(directory) / "waiter.json")
            self.Ckb_cli.tx_init(tx_file, client.url)
            self.Ckb_cli.tx_add_multisig_config(
                account["address"]["testnet"],
                tx_file,
                client.url,
            )
            self.Ckb_cli.tx_add_input(input_parent, 0, tx_file, client.url)
            output = client.get_transaction(input_parent)["transaction"]["outputs"][0]
            self.Ckb_cli.tx_add_type_out_put(
                output["lock"]["code_hash"],
                output["lock"]["hash_type"],
                output["lock"]["args"],
                hex(int(output["capacity"], 16) - 1000),
                "0x",
                tx_file,
                False,
            )
            self.Ckb_cli.tx_add_cell_dep(group_parent, "0x0", tx_file)
            with open(tx_file, encoding="utf-8") as stream:
                tx_document = json.load(stream)
            group_dep = next(
                dep
                for dep in tx_document["transaction"]["cell_deps"]
                if dep["out_point"]["tx_hash"] == group_parent
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
            waiter_tx = self.Tx.build_tx_info(tx_file)

        assert {
            "out_point": {"tx_hash": group_parent, "index": "0x0"},
            "dep_type": "dep_group",
        } in waiter_tx["cell_deps"]
        assert waiter_tx["inputs"][0]["previous_output"]["tx_hash"] == input_parent
        for first_parent, second_parent, first_tx, second_tx in (
            (input_parent, member_parent, input_parent_tx, member_parent_tx),
            (member_parent, input_parent, member_parent_tx, input_parent_tx),
        ):
            client.clear_tx_pool()
            assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}
            waiter = client.notify_transaction(waiter_tx)
            self._wait_for_frontier(waiter, 1, False)
            assert client.send_transaction(first_tx) == first_parent
            self._wait_for_frontier(waiter, 1, False)
            assert client.get_transaction(waiter)["transaction"] is None
            assert client.send_transaction(second_tx) == second_parent
            self._wait_for_frontier(waiter, 0, True)
            assert client.get_transaction(waiter)["transaction"] is not None
        self.did_pass = True
