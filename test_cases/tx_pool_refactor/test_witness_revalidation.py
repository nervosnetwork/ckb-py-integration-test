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

    # TEST-MAP: NTD-39
    def test_changed_witness_rechecks_same_raw_transaction_hash(self):
        """
        1. 提交原交易后移出池，保留相同的原始交易体。
        2. 仅修改见证中的签名，确认交易体保持一致。
        3. 重新提交并核对脚本错误与池中无该交易。
        """

        # 1. 提交原交易后移出池，保留相同的原始交易体。
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

        # 2. 仅修改见证中的签名，确认交易体保持一致。
        changed = copy.deepcopy(original)
        signature = bytearray.fromhex(changed["witnesses"][0][2:])
        assert len(signature) > 32
        signature[-10] ^= 1  # Retain WitnessArgs structure; invalidate the signature.
        changed["witnesses"][0] = "0x" + signature.hex()
        assert changed["witnesses"] != original["witnesses"]
        assert {k: v for k, v in changed.items() if k != "witnesses"} == {
            k: v for k, v in original.items() if k != "witnesses"
        }

        # 3. 重新提交并核对脚本错误与池中无该交易。
        with pytest.raises(Exception) as rejected:
            client.send_transaction(changed)
        assert "script" in str(rejected.value).lower(), str(rejected.value)
        assert tx_hash not in self._pool_hashes()
        self.did_pass = True
