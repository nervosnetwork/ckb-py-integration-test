import json
import time
import urllib.request

from framework.basic import CkbTest
from framework.util import get_project_root


class TestRelayerRetryAfterFullVerifyQueue(CkbTest):
    # A normal transfer from the split transaction is 355 bytes. A 700-byte
    # queue therefore accepts one such filler, then rejects another transfer
    # with VerifyQueue::Full. The slower transaction below keeps the sole
    # verifier occupied while that filler remains queued.
    VERIFY_QUEUE_SIZE_LIMIT = 700
    SLOW_VERIFY_SCRIPT_ARG = "0x02"

    @classmethod
    def setup_class(cls):
        cls.sender = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5235_relayer_retry_after_full_queue/sender",
            8434,
            8435,
        )
        cls.receiver = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5235_relayer_retry_after_full_queue/receiver",
            8436,
            8437,
        )
        try:
            cls.sender.prepare()
            cls.receiver.prepare(
                other_ckb_config={
                    "ckb_tx_pool_max_tx_pool_size": cls.VERIFY_QUEUE_SIZE_LIMIT,
                    "ckb_tx_pool_max_verify_queue_tx_size": cls.VERIFY_QUEUE_SIZE_LIMIT,
                    "ckb_tx_pool_max_tx_verify_workers": 1,
                }
            )
            cls.sender.start()
            cls.receiver.start()
            cls._connect_until_ready(cls.sender, cls.receiver)
            cls.Miner.make_tip_height_number(cls.sender, 30)
            cls.Node.wait_node_height(cls.receiver, 30, 60)
            cls.slow_verify_contract_tx_hash = cls.Contract.deploy_ckb_contract(
                cls.Config.MINER_PRIVATE_1,
                f"{get_project_root()}/source/contract/test_cases/spawn_loop_times",
                enable_type_id=True,
                api_url=cls.sender.getClient().url,
            )
            cls.Miner.miner_until_tx_committed(
                cls.sender, cls.slow_verify_contract_tx_hash
            )
            cls.Node.wait_node_height(
                cls.receiver, cls.sender.getClient().get_tip_block_number(), 60
            )
        except Exception:
            cls.sender.stop()
            cls.receiver.stop()
            cls.sender.clean()
            cls.receiver.clean()
            raise

    @classmethod
    def teardown_class(cls):
        cls.sender.stop()
        cls.receiver.stop()
        cls.sender.clean()
        cls.receiver.clean()

    def setup_method(self, method):
        self.did_pass = None
        self.sender.getClient().clear_tx_pool()
        self.receiver.getClient().clear_tx_pool()
        self.receiver.getClient().clear_tx_verify_queue()

    def test_relayer_retries_same_transaction_after_full_verify_queue_reject(self):
        """
        PR #5235: when remote tx enqueue fails because verify queue is full,
        relayer should be notified so the same tx can be requested again later.
        """
        self._wait_connected(self.sender, self.receiver)
        self._wait_connected(self.receiver, self.sender)

        target_tx, filler_tx = self._build_target_and_filler_transactions()
        slow_filler_tx = self._build_slow_filler_transaction()

        self._notify_transaction(self.receiver, slow_filler_tx)
        # The loop script is rejected only after several seconds. Wait until
        # it has been picked up by the only worker before queuing the normal
        # filler behind it.
        time.sleep(0.2)
        self._wait_verify_queue_size(self.receiver, 0, timeout=2)
        self._notify_transaction(self.receiver, filler_tx)
        self._wait_verify_queue_size_at_least(self.receiver, 1, timeout=5)

        target_hash = self.sender.getClient().send_transaction(target_tx)
        self.Node.wait_get_transaction(self.sender, target_hash, "pending")
        # `Full` is transient backpressure. CKB notifies the relayer but does
        # not retain a rejected-transaction record, so the receiver reports
        # the transaction as unknown after the initial request has time to
        # arrive while the filler still occupies the verify queue.
        time.sleep(3)
        receiver_target_status = self.receiver.getClient().get_transaction(target_hash)[
            "tx_status"
        ]
        assert receiver_target_status["status"] == "unknown"

        # Let the slow verifier finish and the queued filler drain. Clearing
        # the pool releases only the filler; it deliberately does not restart
        # the receiver, so the relayer state from the Full notification stays
        # intact for the retry.
        self._wait_verify_queue_size(self.receiver, 0, timeout=20)
        self.receiver.getClient().clear_tx_pool()
        assert self.sender.getClient().remove_transaction(target_hash)
        assert self.sender.getClient().send_transaction(target_tx) == target_hash
        self.Node.wait_get_transaction(self.receiver, target_hash, "pending")
        self.did_pass = True

    def _build_target_and_filler_transactions(self):
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        fund_tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            360000,
            api_url=self.sender.getClient().url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.sender, fund_tx_hash)
        self.Node.wait_node_height(
            self.receiver, self.sender.getClient().get_tip_block_number(), 60
        )
        split_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [fund_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=2,
            fee=1000,
            api_url=self.sender.getClient().url,
        )
        split_tx_hash = self.sender.getClient().send_transaction(split_tx)
        self.Miner.miner_until_tx_committed(self.sender, split_tx_hash)
        self.Node.wait_node_height(
            self.receiver, self.sender.getClient().get_tip_block_number(), 60
        )
        target_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=1000,
            api_url=self.sender.getClient().url,
        )
        filler_tx = self.Tx.build_send_transfer_self_tx_with_input(
            [split_tx_hash],
            ["0x1"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=1000,
            api_url=self.sender.getClient().url,
        )
        return target_tx, filler_tx

    def _build_slow_filler_transaction(self):
        return self.Contract.build_invoke_ckb_contract(
            account_private=self.Config.MINER_PRIVATE_1,
            contract_out_point_tx_hash=self.slow_verify_contract_tx_hash,
            contract_out_point_tx_index=0,
            type_script_arg=self.SLOW_VERIFY_SCRIPT_ARG,
            data="0x1234",
            hash_type="type",
            api_url=self.sender.getClient().url,
        )

    def _notify_transaction(self, node, tx):
        self._call_rpc_quiet(node, "notify_transaction", [tx])

    @staticmethod
    def _connect_until_ready(node_a, node_b):
        for _ in range(12):
            node_a.connected(node_b)
            node_b.connected(node_a)
            if TestRelayerRetryAfterFullVerifyQueue._has_peer(
                node_a, node_b
            ) and TestRelayerRetryAfterFullVerifyQueue._has_peer(node_b, node_a):
                return
            time.sleep(5)
        raise AssertionError("nodes are not connected")

    @staticmethod
    def _has_peer(node, peer):
        peer_id = peer.get_peer_id()
        return any(p["node_id"] == peer_id for p in node.getClient().get_peers())

    @staticmethod
    def _wait_connected(node, peer):
        for _ in range(30):
            if TestRelayerRetryAfterFullVerifyQueue._has_peer(node, peer):
                return
            time.sleep(1)
        raise AssertionError("nodes are not connected")

    def _wait_verify_queue_size(self, node, expected_size, timeout=10):
        for _ in range(timeout * 10):
            current_size = self._verify_queue_size(node)
            if current_size == expected_size:
                return
            time.sleep(0.1)
        raise AssertionError(
            f"expected verify_queue_size {expected_size}, got {current_size}"
        )

    def _wait_verify_queue_size_at_least(self, node, expected_size, timeout=10):
        for _ in range(timeout * 10):
            current_size = self._verify_queue_size(node)
            if current_size >= expected_size:
                return
            time.sleep(0.1)
        raise AssertionError(
            f"expected verify_queue_size >= {expected_size}, got {current_size}"
        )

    def _verify_queue_size(self, node):
        info = self._call_rpc_quiet(node, "tx_pool_info", [])
        return int(info["verify_queue_size"], 16)

    def _call_rpc_quiet(self, node, method, params):
        data = {"id": 42, "jsonrpc": "2.0", "method": method, "params": params}
        request = urllib.request.Request(
            node.getClient().url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            response = json.loads(response.read().decode("utf-8"))
        if "error" in response:
            raise Exception(response["error"].get("message", "Unknown error"))
        return response.get("result")
