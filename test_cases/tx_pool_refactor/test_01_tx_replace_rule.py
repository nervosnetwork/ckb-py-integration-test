import json
import time

import pytest
from framework.basic import CkbTest


class TestTxReplaceRule(CkbTest):

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "tx_pool/node1", 8120, 8225
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    def setup_method(self, method):
        """
        clean tx pool
        :param method:
        :return:
        """
        self.node.getClient().clear_tx_pool()
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def test_transaction_replacement_with_unconfirmed_inputs_failure(self):
        """
        replace Tx contains unconfirmed inputs, replace failed
        1. a->b ,c->d => a,d -> b
            ERROR :RBF rejected: new Tx contains unconfirmed inputs
        :return:
        """
        TEST_PRIVATE_1 = (
            "0x98400f6a67af07025f5959af35ed653d649f745b8f54bf3f07bef9bd605ee941"
        )

        account = self.Ckb_cli.util_key_info_by_private_key(TEST_PRIVATE_1)
        cell_a_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )

        cell_c_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )
        tx_a_to_b = self.Tx.send_transfer_self_tx_with_input(
            [cell_a_hash],
            ["0x0"],
            TEST_PRIVATE_1,
            data="0x",
            fee=5000,
            output_count=1,
            api_url=self.node.getClient().url,
        )
        tx_c_to_d = self.Tx.send_transfer_self_tx_with_input(
            [cell_c_hash],
            ["0x0"],
            TEST_PRIVATE_1,
            data="0x",
            fee=5000,
            output_count=1,
            api_url=self.node.getClient().url,
        )

        with pytest.raises(Exception) as exc_info:
            tx_ad_to_b = self.Tx.send_transfer_self_tx_with_input(
                [cell_a_hash, tx_c_to_d],
                ["0x0", "0x0"],
                TEST_PRIVATE_1,
                data="0x",
                fee=5000,
                output_count=1,
                api_url=self.node.getClient().url,
            )
        expected_error_message = "RBF rejected: new Tx contains unconfirmed inputs`"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_transaction_replacement_with_confirmed_inputs_successful(self):
        """
        replace Tx contains confirmed inputs, replace failed
        1. a->b ,c->d => a,c -> b
            ERROR :RBF rejected: new Tx contains unconfirmed inputs
        :return:
        """
        TEST_PRIVATE_1 = (
            "0x98400f6a67af07025f5959af35ed653d649f745b8f54bf3f07bef9bd605ee941"
        )

        account = self.Ckb_cli.util_key_info_by_private_key(TEST_PRIVATE_1)
        cell_a_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )

        cell_c_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )
        tx_a_to_b = self.Tx.send_transfer_self_tx_with_input(
            [cell_a_hash],
            ["0x0"],
            TEST_PRIVATE_1,
            data="0x",
            fee=5000,
            output_count=1,
            api_url=self.node.getClient().url,
        )
        tx_c_to_d = self.Tx.send_transfer_self_tx_with_input(
            [cell_c_hash],
            ["0x0"],
            TEST_PRIVATE_1,
            data="0x",
            fee=5000,
            output_count=1,
            api_url=self.node.getClient().url,
        )

        tx_ac_to_b = self.Tx.send_transfer_self_tx_with_input(
            [cell_a_hash, cell_c_hash],
            ["0x0", "0x0"],
            TEST_PRIVATE_1,
            data="0x",
            fee=15000,
            output_count=1,
            api_url=self.node.getClient().url,
        )

        tx_a_to_b_response = self.node.getClient().get_transaction(tx_a_to_b)
        assert tx_a_to_b_response["tx_status"]["status"] == "rejected"
        assert "RBFRejected" in tx_a_to_b_response["tx_status"]["reason"]

        tx_c_to_d_response = self.node.getClient().get_transaction(tx_c_to_d)
        assert tx_c_to_d_response["tx_status"]["status"] == "rejected"
        assert "RBFRejected" in tx_c_to_d_response["tx_status"]["reason"]

        tx_ac_to_b_response = self.node.getClient().get_transaction(tx_ac_to_b)
        assert tx_ac_to_b_response["tx_status"]["status"] == "pending"

    def test_transaction_fee_equal_to_old_fee(self):
        """
         replace tx fee  ==  old tx fee

             1. send tx that tx fee == old tx fee
                ERROR : PoolRejectedRBF

             2. get_transaction
                min_fee_rate in PoolRejectedRBF
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "5000",
        )
        self.node.getClient().get_raw_tx_pool(True)
        with pytest.raises(Exception) as exc_info:
            # 1. send tx that tx fee == old tx fee
            self.Ckb_cli.wallet_transfer_by_private_key(
                self.Config.MINER_PRIVATE_1,
                account["address"]["testnet"],
                101,
                self.node.getClient().url,
                "5000",
            )
            # 2. get_transaction, min_fee_rate in PoolRejectedRBF
            self.node.getClient().get_raw_tx_pool(True)

        expected_error_message = "PoolRejectedRBF"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        hash_list = list(tx_pool["pending"].keys())
        assert tx_pool["pending"][hash_list[0]]["fee"] == "0x910"
        assert (
            f"{int(self.node.getClient().get_transaction(hash)['min_replace_fee'], 16)}"
            in exc_info.value.args[0]
        )

    def test_transaction_replacement_higher_fee(self):
        """
        Submitting multiple transactions using the same input cell,
            the fee is higher should replace successful
        Steps:
        1. send transaction A, sending input cell to address B
             send successful
        2. send transaction B, sending the same input cell to address B and fee > A(fee)
            send successful
        3. send transaction C, sending the same input cell to address B and fee > B(fee)
            send successful
        4. query transaction (A,B,C) status
              A status : rejected  ; reason : RBFRejected
              B status : rejected  ; reason : RBFRejected
              C status : pending   ;
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        # 1. send transaction A, sending input cell to address B, send successful
        tx_hash1 = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )
        # 2. send transaction B, sending the same input cell to address B and fee > A(fee), send successful
        tx_hash2 = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            200,
            self.node.getClient().url,
            "3000",
        )

        # 3. send transaction C, sending the same input cell to address B and fee > B(fee), send successful
        tx_hash3 = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            300,
            self.node.getClient().url,
            "6000",
        )
        # 4. query transaction (A,B,C) status
        #       A status : rejected  ; reason : RBFRejected
        #       B status : rejected  ; reason : RBFRejected
        #       C status : pending   ;
        tx1_response = self.node.getClient().get_transaction(tx_hash1)
        tx2_response = self.node.getClient().get_transaction(tx_hash2)
        tx3_response = self.node.getClient().get_transaction(tx_hash3)
        assert tx1_response["tx_status"]["status"] == "rejected"
        assert tx2_response["tx_status"]["status"] == "rejected"
        assert tx3_response["tx_status"]["status"] == "pending"
        assert "RBFRejected" in tx1_response["tx_status"]["reason"]
        assert "RBFRejected" in tx2_response["tx_status"]["reason"]

    def test_transaction_replacement_min_replace_fee(self):
        """
        replace tx use min_replace_fee ,replace successful
        Steps:
        1. send transaction A
             send successful
        2. send transaction B use A.min_replace_fee
            send successful
        3. query transaction (A,B) status
              A status : rejected  ; reason : RBFRejected
              B status : pending   ;
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        # 1. send transaction A, send successful
        tx_hash1 = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )

        # 2. send transaction B use A.min_replace_fee, send successful
        tx_hash2 = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash1],
            ["0x0"],
            self.Config.MINER_PRIVATE_1,
            fee=1500,
            api_url=self.node.getClient().url,
        )
        transaction = self.node.getClient().get_transaction(tx_hash2)
        tx_hash3 = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash1],
            ["0x0"],
            self.Config.MINER_PRIVATE_1,
            fee=int(transaction["min_replace_fee"], 16),
            api_url=self.node.getClient().url,
        )

        # 3. query transaction (A,B) status
        #       A status : rejected  ; reason : RBFRejected
        #       B status : pending   ;
        tx2_response = self.node.getClient().get_transaction(tx_hash2)
        tx3_response = self.node.getClient().get_transaction(tx_hash3)
        assert tx2_response["tx_status"]["status"] == "rejected"
        assert tx3_response["tx_status"]["status"] == "pending"
        assert "RBFRejected" in tx2_response["tx_status"]["reason"]

    def test_tx_conflict_too_many_txs(self):
        """
        if the replaced transaction affects more than 100 transactions, the replacement will fail.

            1. send tx A
                send tx successful
            2. send A linked tx 100
                send tx successful
            3. replace A tx
                Error : PoolRejectedRBF
            4. replace first linked tx
                replace successful

            5. query tx pool
                pending tx = 2
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        # 1. send tx A, send tx successful
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            360000,
            self.node.getClient().url,
            "2800",
        )
        first_hash = tx_hash
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        tx_list = []
        # 2. send A linked tx 100, send tx successful
        for i in range(100):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                ["0x0"],
                self.Config.ACCOUNT_PRIVATE_1,
                fee=1000,
                api_url=self.node.getClient().url,
            )
            tx_list.append(tx_hash)
            self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        # 3. replace A tx, Error : PoolRejectedRBF
        first_tx = self.node.getClient().get_transaction(first_hash)
        with pytest.raises(Exception) as exc_info:
            self.Ckb_cli.wallet_transfer_by_private_key(
                self.Config.ACCOUNT_PRIVATE_1,
                account["address"]["testnet"],
                360000,
                self.node.getClient().url,
                int(first_tx["min_replace_fee"], 16),
            )
        expected_error_message = "RBF rejected: Tx conflict with too many txs, conflict txs count: 101, expect <= 100"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # 4. replace first linked tx, replace successful
        second_tx = self.node.getClient().get_transaction(tx_list[0])
        self.Tx.send_transfer_self_tx_with_input(
            [first_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            fee=int(second_tx["min_replace_fee"], 16),
            api_url=self.node.getClient().url,
        )
        # 5. query tx pool, pending tx = 2
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert len(tx_pool["pending"].keys()) == 2

    def test_replace_pending_transaction_successful(self):
        """
        replace the pending transaction,replacement successful
            1. send transaction: A
                return tx_hash_a
            2. query transaction tx_hash_a
                A status: pending
            3. replace B=>A
                return tx_hash_b
            4. query transaction tx_hash_a ，query transaction tx_hash_b
                tx_hash_a status: rejected ,reason:RBFRejected
                tx_hash_b status: pending
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)

        # 1. send transaction: A, return tx_hash_a
        tx_a = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )
        # 2. query transaction tx_hash_a
        tx_a_response = self.node.getClient().get_transaction(tx_a)
        assert tx_a_response["tx_status"]["status"] == "pending"
        # 3. replace B=>A, return tx_hash_b
        tx_b = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            200,
            self.node.getClient().url,
            "12000",
        )

        # 4. query transaction tx_hash_b, tx_hash_b status: pending
        tx_b_response = self.node.getClient().get_transaction(tx_b)
        assert tx_b_response["tx_status"]["status"] == "pending"

        # query transaction tx_hash_a, tx_hash_a status: rejected, reason:RBFRejected
        tx_a_response = self.node.getClient().get_transaction(tx_a)
        assert tx_a_response["tx_status"]["status"] == "rejected"
        assert "RBFRejected" in tx_a_response["tx_status"]["reason"]

    def test_replace_proposal_transaction_successful(self):
        """
        Replacing the transaction for the proposal, replacement failed.
        1. Send a transaction and submit it to the proposal.
                Query the transaction status as 'proposal.'
        2. Replace the transaction for that proposal.
            successful
        3. get_block_template
            contains proposal that is not removed
        4.  generate empty block
            successful
        5. get_block_template
            cant contains proposal that is removed
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2
        )
        # 1. Send a transaction and submit it to the proposal. Query the transaction status as 'proposal.'
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account["address"]["testnet"],
            360000,
            api_url=self.node.getClient().url,
            fee_rate="1000",
        )

        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            output_count=1,
            fee=1000,
            api_url=self.node.getClient().url,
        )
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        tx_list = [tx_hash]
        for i in range(50):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                ["0x0"],
                self.Config.ACCOUNT_PRIVATE_2,
                output_count=1,
                fee=1000,
                api_url=self.node.getClient().url,
            )
            tx_list.append(tx_hash)

        self.Miner.miner_with_version(self.node, "0x0")
        self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(5)
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        for tx in tx_pool["proposed"].keys():
            print(tx)
        proposal_txs = list(tx_pool["proposed"].keys())
        assert len(proposal_txs) > 0
        for tx in proposal_txs:
            tx_response = self.node.getClient().get_transaction(tx)
            assert tx_response["tx_status"]["status"] == "proposed"
        tx_response = self.node.getClient().get_transaction(proposal_txs[0])
        # 2. Replace the transaction for that proposal. successful
        replace_proposal_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_response["transaction"]["inputs"][0]["previous_output"]["tx_hash"]],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_2,
            output_count=1,
            fee=1000000,
            api_url=self.node.getClient().url,
        )

        time.sleep(5)
        # 3. get_block_template, contains proposal that is not removed
        block_template = self.node.getClient().get_block_template()
        print(block_template)
        tx_response = self.node.getClient().get_transaction(proposal_txs[0])
        assert "RBFRejected" in tx_response["tx_status"]["reason"]
        # 4. generate empty block, successful
        self.node.getClient().generate_block()
        # 5. get_block_template, cant contains proposal that is removed
        block_template = self.node.getClient().get_block_template()
        assert not proposal_txs[0] in json.dumps(block_template)

    def test_send_transaction_duplicate_input_with_son_tx(self):
        """
        Replacing the transaction will also remove the child transactions.
        1. send a->b ,b->c, c->d
            successful
        2. Replace a->b, a->d
            successful
        3. query get_tx_pool
            return replace tx: a->d
        4. query old txs status
            status : rejected ,reason:RBFRejected
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        # 1. send a->b ,b->c, c->d, successful
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            360000,
            api_url=self.node.getClient().url,
            fee_rate="1000",
        )
        first_tx_hash = tx_hash
        tx_list = [first_tx_hash]
        self.Miner.miner_until_tx_committed(self.node, first_tx_hash)
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=1000,
            api_url=self.node.getClient().url,
        )
        tx_list.append(tx_hash)
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        for i in range(10):
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [tx_hash],
                ["0x0"],
                self.Config.ACCOUNT_PRIVATE_1,
                output_count=1,
                fee=1000,
                api_url=self.node.getClient().url,
            )
            tx_list.append(tx_hash)

        # 2. Replace a->b, a->d, successful
        replace_tx = self.node.getClient().get_transaction(tx_list[1])
        replace_tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [first_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=int(replace_tx["min_replace_fee"], 16),
            api_url=self.node.getClient().url,
        )

        # 3. query get_tx_pool, return replace tx: a->d
        tx_pool = self.node.getClient().get_raw_tx_pool(True)
        assert len(tx_pool["pending"]) == 1
        assert replace_tx_hash in list(tx_pool["pending"])
        for tx in tx_list[1:]:
            # 4. query old txs status, status : rejected ,reason:RBFRejected
            tx_response = self.node.getClient().get_transaction(tx)
            assert tx_response["tx_status"]["status"] == "rejected"
            assert "RBFRejected" in tx_response["tx_status"]["reason"]

    def test_min_replace_fee_changed_with_child_tx(self):
        """
        based on transaction A,
        send a child transaction. The 'min_replace_fee' of transaction A will not change,
        and it can be successfully replaced.

        1. Send transaction A.
            successful
        2. Query the 'min_replace_fee' of transaction A.

        3. Send a child transaction of transaction A.
            successful
        4. Query the updated 'min_replace_fee' of transaction A.
            min_replace_fee changed
        5.Send B to replace A.
            replace successful
        :return:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        # 1. Send transaction A. successful
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            360000,
            self.node.getClient().url,
            "2800",
        )
        first_hash = tx_hash
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        tx_list = []
        tx_hash1 = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            fee=1000,
            api_url=self.node.getClient().url,
        )
        # 2. Query the 'min_replace_fee' of transaction A.
        transaction1 = self.node.getClient().get_transaction(tx_hash1)
        # 3. Send a child transaction of transaction A. successful
        self.Tx.send_transfer_self_tx_with_input(
            [tx_hash1],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            fee=1000,
            api_url=self.node.getClient().url,
        )
        # 4. Query the updated 'min_replace_fee' of transaction A. min_replace_fee changed
        after_transaction1 = self.node.getClient().get_transaction(tx_hash1)
        assert after_transaction1["min_replace_fee"] != transaction1["min_replace_fee"]
        # 5. Send B to replace A. replace successful
        replace_tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            fee=int(after_transaction1["min_replace_fee"], 16),
            api_url=self.node.getClient().url,
        )
        transaction = self.node.getClient().get_transaction(replace_tx_hash)
        assert transaction["tx_status"]["status"] == "pending"

    def test_min_replace_fee_exceeds_1_ckb(self):
        """
            min_replace_fee exceeds 1 CKB.
            1. send tx(fee=0.9999ckb)
            2. query transaction
                tx.min_replace_fee > 1CKB
        :return:
        """
        account = self.Tx.util_key_info_by_private_key(self.Config.ACCOUNT_PRIVATE_1)
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            360000,
            self.node.getClient().url,
            "2800",
        )
        first_hash = tx_hash
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")
        tx_list = []
        # 1. send tx(fee=0.9999ckb)
        tx_hash1 = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            fee=99999999,
            api_url=self.node.getClient().url,
        )
        # 2. query transaction, tx.min_replace_fee > 1CKB
        transaction = self.node.getClient().get_transaction(tx_hash1)
        assert int(transaction["min_replace_fee"], 16) > 99999999
