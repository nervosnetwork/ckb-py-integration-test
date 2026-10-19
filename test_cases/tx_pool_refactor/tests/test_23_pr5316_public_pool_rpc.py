"""PR #5316: externally observable tx-pool RPC behavior.

Internal barrier/race branches remain separate Python-only coverage gaps.
"""

import copy
import tempfile
import time
from pathlib import Path

import pytest

from framework.basic import CkbTest


class TestPublicPoolRpc(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_public_pool_rpc/node1",
            8490,
            8491,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def setup_method(self, method):
        self.did_pass = None
        self.node.getClient().clear_tx_pool()

    def teardown_method(self, method):
        # The base copier uses a shared report path; pytest retains this run's log.
        pass

    def _committed_funding(self):
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
        return fund_hash

    def _pool_hashes(self):
        pool = self.node.getClient().get_raw_tx_pool(True)
        return set(pool["pending"]) | set(pool["proposed"])

    @staticmethod
    def _serialized_size_in_block(tx):
        """Molecule table/vector sizes plus a transaction's block offset."""

        def table(*fields):
            return 4 + 4 * len(fields) + sum(fields)

        def vector(items):
            return 4 + 4 * len(items) + sum(items)

        def byte_string(value):
            return 4 + (len(value) - 2) // 2

        outputs = []
        for output in tx["outputs"]:
            assert output["type"] is None
            lock = output["lock"]
            outputs.append(table(8, table(32, 1, byte_string(lock["args"])), 0))
        raw = table(
            4,
            4 + 37 * len(tx["cell_deps"]),
            4 + 32 * len(tx["header_deps"]),
            4 + 44 * len(tx["inputs"]),
            vector(outputs),
            vector([byte_string(data) for data in tx["outputs_data"]]),
        )
        return table(raw, vector([byte_string(w) for w in tx["witnesses"]])) + 4

    # TEST-MAP: NTD-82
    # NTD-82 expects (1) size == tx_size_limit to be admitted via
    # send_transaction, (2) the same body via notify_transaction, (3) size ==
    # tx_size_limit + 1 to be rejected by both entrances, and (4) rejection to
    # leave no pool entry or verify task. Remote/batch/ID/compact/full-queue
    # branches are not covered here.
    def test_direct_transaction_exact_size_bound_and_next_byte(self):
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
        fund_output = client.get_transaction(fund)["transaction"]["outputs"][0]
        limit = int(client.tx_pool_info()["tx_size_limit"], 16)
        assert limit == 512_000

        def build_transaction(output_data_bytes):
            with tempfile.TemporaryDirectory(prefix="pr5316-size-bound-") as directory:
                tx_file = str(Path(directory) / "transaction.json")
                self.Ckb_cli.tx_init(tx_file, client.url)
                self.Ckb_cli.tx_add_multisig_config(
                    account["address"]["testnet"],
                    tx_file,
                    client.url,
                )
                self.Ckb_cli.tx_add_input(fund, 0, tx_file, client.url)
                lock = fund_output["lock"]
                self.Ckb_cli.tx_add_type_out_put(
                    lock["code_hash"],
                    lock["hash_type"],
                    lock["args"],
                    hex(int(fund_output["capacity"], 16) - 1_000_000),
                    "0x" + "00" * output_data_bytes,
                    tx_file,
                    False,
                )
                signature = self.Ckb_cli.tx_sign_inputs(key, tx_file, client.url)[0]
                self.Ckb_cli.tx_add_signature(
                    signature["lock-arg"],
                    signature["signature"],
                    tx_file,
                    client.url,
                )
                return self.Tx.build_tx_info(tx_file)

        base = build_transaction(0)
        # Independently compute the in-block size used to pad the fixture and
        # to check the node's reported size.
        padding = limit - self._serialized_size_in_block(base)
        assert padding > 0
        # Pad only output_data; the two variants differ by exactly one byte.
        exact = build_transaction(padding)
        assert self._serialized_size_in_block(exact) == limit
        assert len(exact["witnesses"]) == 1

        # (1) Equal to the limit: send_transaction accepts it and reports the
        # same size.
        accepted = client.call(
            "send_transaction",
            [exact, "passthrough"],
            try_count=1,
            timeout=45,
            verbose=False,
        )
        assert accepted in self._pool_hashes()
        detail = client.get_raw_tx_pool(True)
        entry = detail["pending"].get(accepted) or detail["proposed"][accepted]
        assert int(entry["size"], 16) == limit
        assert client.remove_transaction(accepted) is True

        # (2) The same body through notify_transaction; poll for async admission.
        notified = client.call(
            "notify_transaction",
            [exact],
            try_count=1,
            timeout=45,
            verbose=False,
        )
        assert notified == accepted
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and accepted not in self._pool_hashes():
            time.sleep(0.2)
        assert accepted in self._pool_hashes()
        assert client.remove_transaction(accepted) is True

        # (3) One byte over: both entrances return the size error naming actual
        # and maximum. The unchanged witness means a size (not signature) error
        # confirms the size gate runs before script verification.
        oversized = copy.deepcopy(exact)
        oversized["outputs_data"][0] += "00"
        assert self._serialized_size_in_block(oversized) == limit + 1
        for method, params in (
            ("send_transaction", [oversized, "passthrough"]),
            ("notify_transaction", [oversized]),
        ):
            with pytest.raises(Exception) as rejected:
                client.call(method, params, try_count=1, timeout=45, verbose=False)
            assert str(limit + 1) in str(rejected.value)
            assert str(limit) in str(rejected.value)
        # (4) Rejection leaves no pool entry and no queued verify task.
        assert self._pool_hashes() == set()
        assert int(client.tx_pool_info()["verify_queue_size"], 16) == 0
        self.did_pass = True

    # TEST-MAP: NTD-44
    def test_successful_send_is_queryable_and_duplicate_is_rejected(self):
        client = self.node.getClient()
        fund_hash = self._committed_funding()
        transaction = self.Tx.build_send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )

        tx_hash = client.send_transaction(transaction)
        response = client.get_transaction(tx_hash)
        assert tx_hash in self._pool_hashes()
        assert response["transaction"]["hash"] == tx_hash
        assert response["tx_status"]["status"] in ("pending", "proposed")

        with pytest.raises(Exception) as duplicate:
            client.send_transaction(transaction)
        assert "duplicat" in str(duplicate.value).lower()
        assert tx_hash in self._pool_hashes()
        self.did_pass = True

    # TEST-MAP: NTD-39
    def test_changed_witness_rechecks_same_raw_transaction_hash(self):
        client = self.node.getClient()
        fund_hash = self._committed_funding()
        original = self.Tx.build_send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        tx_hash = client.send_transaction(original)
        assert tx_hash in self._pool_hashes()
        assert client.remove_transaction(tx_hash) is True
        assert tx_hash not in self._pool_hashes()

        changed = copy.deepcopy(original)
        signature = bytearray.fromhex(changed["witnesses"][0][2:])
        assert len(signature) > 32
        signature[-10] ^= 1  # Retain WitnessArgs structure; invalidate the signature.
        changed["witnesses"][0] = "0x" + signature.hex()
        assert changed["witnesses"] != original["witnesses"]
        assert {k: v for k, v in changed.items() if k != "witnesses"} == {
            k: v for k, v in original.items() if k != "witnesses"
        }

        with pytest.raises(Exception) as rejected:
            client.send_transaction(changed)
        assert "script" in str(rejected.value).lower(), str(rejected.value)
        assert tx_hash not in self._pool_hashes()
        self.did_pass = True

    # TEST-MAP: NTD-48
    def test_clear_verify_queue_keeps_accepted_transaction(self):
        client = self.node.getClient()
        fund_hash = self._committed_funding()
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        assert tx_hash in self._pool_hashes()

        client.clear_tx_verify_queue()

        response = client.get_transaction(tx_hash)
        assert tx_hash in self._pool_hashes()
        assert response["transaction"]["hash"] == tx_hash
        assert response["tx_status"]["status"] in ("pending", "proposed")
        self.did_pass = True

    # TEST-MAP: NTD-33
    def test_same_tip_clear_invalidates_selected_template_owner(self):
        client = self.node.getClient()
        fund_hash = self._committed_funding()
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        assert tx_hash in self._pool_hashes()

        short_id = tx_hash[:22]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if short_id in client.get_block_template()["proposals"]:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("transaction was not proposed")
        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            prepared = client.get_block_template()
            if tx_hash in {tx["hash"] for tx in prepared["transactions"]}:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("transaction was not selected for a template")
        tip_hash = client.get_tip_header()["hash"]
        assert prepared["parent_hash"] == tip_hash
        assert client.tx_pool_info()["tip_hash"] == tip_hash

        client.clear_tx_pool()
        assert self._pool_hashes() == set()
        assert client.get_tip_header()["hash"] == tip_hash
        refreshed = client.get_block_template()
        assert refreshed["parent_hash"] == tip_hash
        assert int(refreshed["work_id"], 16) > int(prepared["work_id"], 16)
        assert tx_hash not in {tx["hash"] for tx in refreshed["transactions"]}
        print(
            f"same-tip clear: tip={tip_hash}, selected_before={tx_hash}, "
            f"selected_after={len(refreshed['transactions'])}, "
            f"work_id={prepared['work_id']}->{refreshed['work_id']}"
        )
        self.did_pass = True

    # TEST-MAP: NTD-34
    def test_unselected_independent_transaction_keeps_prior_template_valid(self):
        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        fund_hash = self._committed_funding()
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            key,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)

        selected = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if selected[:22] in client.get_block_template()["proposals"]:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("selected transaction was not proposed")
        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            prepared = client.get_block_template()
            if selected in {tx["hash"] for tx in prepared["transactions"]}:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("selected transaction did not enter template")
        tip_hash = client.get_tip_header()["hash"]
        assert prepared["parent_hash"] == tip_hash

        independent = self.Tx.send_transfer_self_tx_with_input(
            [split],
            ["0x1"],
            key,
            fee=1000,
            api_url=client.url,
        )
        assert {selected, independent} <= self._pool_hashes()
        assert independent not in {tx["hash"] for tx in prepared["transactions"]}

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            refreshed = client.get_block_template()
            hashes = {tx["hash"] for tx in refreshed["transactions"]}
            if independent[:22] in refreshed["proposals"] and selected in hashes:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("independent proposal or selected owner was lost")
        assert refreshed["parent_hash"] == tip_hash
        assert refreshed["work_id"] != prepared["work_id"]
        assert independent not in hashes  # Not yet in its commit window.

        old_block = self.Miner.block_template_transfer_to_submit_block(
            copy.deepcopy(prepared)
        )
        block_hash = client.submit_block(prepared["work_id"], old_block)
        assert client.get_transaction(selected)["tx_status"]["status"] == "committed"
        assert independent in self._pool_hashes()
        following = client.get_block_template()
        assert following["parent_hash"] == block_hash
        assert independent[:22] in following["proposals"]
        print(
            f"unselected-add: tip={tip_hash}, old_work_id={prepared['work_id']}, "
            f"new_work_id={refreshed['work_id']}, independent={independent}"
        )
        self.did_pass = True

    # TEST-MAP: NTD-78
    def test_remove_local_removes_parent_and_descendant_once(self):
        client = self.node.getClient()
        fund_hash = self._committed_funding()
        parent_hash = self.Tx.send_transfer_self_tx_with_input(
            [fund_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        child_hash = self.Tx.send_transfer_self_tx_with_input(
            [parent_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=client.url,
        )
        assert {parent_hash, child_hash} <= self._pool_hashes()

        assert client.remove_transaction(parent_hash) is True
        assert {parent_hash, child_hash}.isdisjoint(self._pool_hashes())
        assert client.remove_transaction(parent_hash) is False
        self.did_pass = True
