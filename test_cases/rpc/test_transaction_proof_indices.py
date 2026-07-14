import copy

import pytest

from framework.basic import CkbTest


@pytest.fixture(scope="module")
def proof_node():
    node = CkbTest.CkbNode.init_dev_by_port(
        CkbTest.CkbNodeConfigPath.CURRENT_TEST,
        "rpc/transaction_proof_indices",
        18415,
        18526,
    )
    node.prepare()
    try:
        node.start()
        CkbTest.Miner.make_tip_height_number(node, 1100)
        yield node
    finally:
        node.stop()
        node.clean()


class TestTransactionProofIndices:
    def test_verify_transaction_proof_rejects_empty_duplicate_and_oversized_indices(
        self, proof_node
    ):
        client, tx_hash, block = self._committed_transfer_context(proof_node)
        proof = client.get_transaction_proof([tx_hash], block["header"]["hash"])
        tx_count = len(block["transactions"])
        tx_index = proof["proof"]["indices"][0]

        self._assert_invalid_params(
            lambda: client.verify_transaction_proof(
                self._with_indices(proof, ["proof"], [])
            )
        )
        self._assert_invalid_params(
            lambda: client.verify_transaction_proof(
                self._with_indices(proof, ["proof"], [tx_index, tx_index])
            )
        )
        self._assert_invalid_params(
            lambda: client.verify_transaction_proof(
                self._with_indices(proof, ["proof"], [tx_index] * (tx_count + 1))
            )
        )

    def test_verify_transaction_and_witness_proof_rejects_empty_duplicate_and_oversized_indices(
        self, proof_node
    ):
        client, tx_hash, block = self._committed_transfer_context(proof_node)
        proof = client.get_transaction_and_witness_proof(
            [tx_hash], block["header"]["hash"]
        )
        tx_count = len(block["transactions"])
        tx_index = proof["transactions_proof"]["indices"][0]
        witness_index = proof["witnesses_proof"]["indices"][0]

        for proof_key in ["transactions_proof", "witnesses_proof"]:
            self._assert_invalid_params(
                lambda proof_key=proof_key: client.call(
                    "verify_transaction_and_witness_proof",
                    [self._with_indices(proof, [proof_key], [])],
                )
            )

        self._assert_invalid_params(
            lambda: client.call(
                "verify_transaction_and_witness_proof",
                [
                    self._with_indices(
                        proof, ["transactions_proof"], [tx_index, tx_index]
                    )
                ],
            )
        )
        self._assert_invalid_params(
            lambda: client.call(
                "verify_transaction_and_witness_proof",
                [
                    self._with_indices(
                        proof, ["witnesses_proof"], [witness_index, witness_index]
                    )
                ],
            )
        )

        self._assert_invalid_params(
            lambda: client.call(
                "verify_transaction_and_witness_proof",
                [
                    self._with_indices(
                        proof, ["transactions_proof"], [tx_index] * (tx_count + 1)
                    )
                ],
            )
        )
        self._assert_invalid_params(
            lambda: client.call(
                "verify_transaction_and_witness_proof",
                [
                    self._with_indices(
                        proof, ["witnesses_proof"], [witness_index] * (tx_count + 1)
                    )
                ],
            )
        )

    @staticmethod
    def _committed_transfer_context(node):
        client = node.getClient()
        account = CkbTest.Ckb_cli.util_key_info_by_private_key(
            CkbTest.Config.ACCOUNT_PRIVATE_1
        )
        tx_hash = CkbTest.Ckb_cli.wallet_transfer_by_private_key(
            CkbTest.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            140,
            client.url,
        )
        CkbTest.Miner.miner_until_tx_committed(node, tx_hash)
        tx = client.get_transaction(tx_hash)
        block = client.get_block(tx["tx_status"]["block_hash"])

        assert len(block["transactions"]) > 1
        return client, tx_hash, block

    @staticmethod
    def _with_indices(proof, proof_path, indices):
        modified = copy.deepcopy(proof)
        target = modified
        for key in proof_path:
            target = target[key]
        target["indices"] = indices
        return modified

    @staticmethod
    def _assert_invalid_params(rpc_call):
        with pytest.raises(Exception) as exc_info:
            rpc_call()
        message = str(exc_info.value)
        assert "InvalidParams" in message or "Invalid params" in message
