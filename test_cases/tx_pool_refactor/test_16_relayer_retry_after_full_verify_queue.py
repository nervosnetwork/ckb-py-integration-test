import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from framework.basic import CkbTest


class TestRelayerRetryAfterFullVerifyQueue(CkbTest):
    VERIFY_QUEUE_SIZE_LIMIT = 1_000_000
    FILLER_BATCH_COUNT = 24
    FILLER_WORKERS = 4
    FILLER_SEND_INTERVAL_SECONDS = 0.01
    FULL_QUEUE_MIN_TX_COUNT = 10
    LARGE_FILLER_COUNT = 3
    LARGE_FILLER_WITNESS_BYTES = 450_000
    MEDIUM_FILLER_WITNESS_BYTES = 10_000
    FILLER_WITNESS_SIZES = (
        [LARGE_FILLER_WITNESS_BYTES] * LARGE_FILLER_COUNT
        + [MEDIUM_FILLER_WITNESS_BYTES] * 10
        + [5_000, 2_000, 1_000, 1, 1]
    ) * FILLER_BATCH_COUNT
    FILLER_TX_COUNT = len(FILLER_WITNESS_SIZES)
    FILLER_FEE = 1_000_000

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

        filler_txs = self._build_filler_transactions()
        target_tx = self._build_target_transaction()
        flood = self._start_receiver_verify_queue_flood(filler_txs)
        try:
            # max_tx_verify_workers is clamped to at least one by CKB. Keep
            # filling through the public test RPC instead of relying on zero
            # workers to freeze the queue.
            self._wait_verify_queue_size_at_least(
                self.receiver, self.FULL_QUEUE_MIN_TX_COUNT, timeout=30
            )
            target_hash = self.sender.getClient().send_transaction(target_tx)
            self.Node.wait_get_transaction(self.sender, target_hash, "pending")
            receiver_target_status = self._wait_rejected_transaction(
                self.receiver, target_hash, timeout=30
            )
            assert receiver_target_status["status"] == "rejected"
            assert '"type":"Full"' in receiver_target_status["reason"]
        finally:
            self._stop_receiver_verify_queue_flood(flood)

        # Some fillers can finish verification before the queue is cleared;
        # remove them from the pool as well before testing the retry.
        self.receiver.getClient().clear_tx_pool()
        self._clear_receiver_verify_queue()

        assert self.sender.getClient().remove_transaction(target_hash)
        assert self.sender.getClient().send_transaction(target_tx) == target_hash
        self.Node.wait_get_transaction(self.receiver, target_hash, "pending")
        self.did_pass = True

    def _build_target_transaction(self):
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
        return self.Tx.build_send_transfer_self_tx_with_input(
            [fund_tx_hash],
            ["0x0"],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=1000,
            api_url=self.sender.getClient().url,
        )

    def _build_filler_transactions(self):
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
            output_count=self.FILLER_TX_COUNT,
            fee=self.FILLER_FEE,
            api_url=self.sender.getClient().url,
        )
        split_tx_hash = self.sender.getClient().send_transaction(split_tx)
        self.Miner.miner_until_tx_committed(self.sender, split_tx_hash)
        self.Node.wait_node_height(
            self.receiver, self.sender.getClient().get_tip_block_number(), 60
        )

        filler_txs = []
        for index, witness_bytes in enumerate(self.FILLER_WITNESS_SIZES):
            filler_txs.append(
                (
                    self._build_filler_transaction(
                        split_tx_hash, index, witness_bytes
                    ),
                    witness_bytes,
                )
            )
        return filler_txs

    def _build_filler_transaction(self, tx_hash, output_index, witness_bytes):
        tx = self.Tx.build_send_transfer_self_tx_with_input(
            [tx_hash],
            [hex(output_index)],
            self.Config.ACCOUNT_PRIVATE_1,
            output_count=1,
            fee=self.FILLER_FEE,
            api_url=self.sender.getClient().url,
        )
        # Keep the valid signature witness intact. The extra witness increases
        # the serialized size while remaining irrelevant to the lock script.
        tx["witnesses"].append("0x" + "00" * witness_bytes)
        return tx

    def _notify_filler_transaction(self, tx):
        self._call_rpc_quiet(self.receiver, "notify_transaction", [tx])

    def _start_receiver_verify_queue_flood(self, filler_txs):
        stop_event = threading.Event()
        errors = []
        errors_lock = threading.Lock()

        def submit_filler_txs(txs):
            for tx, _ in txs:
                if stop_event.is_set():
                    return
                try:
                    self._notify_filler_transaction(tx)
                except Exception as err:
                    # The tx-pool mailbox is bounded. A full mailbox is an
                    # expected side effect of the intentional flood.
                    if "TrySendError" not in str(err):
                        with errors_lock:
                            errors.append(err)
                stop_event.wait(self.FILLER_SEND_INTERVAL_SECONDS)

        executor = ThreadPoolExecutor(max_workers=self.FILLER_WORKERS)
        futures = [
            executor.submit(
                submit_filler_txs, filler_txs[index :: self.FILLER_WORKERS]
            )
            for index in range(self.FILLER_WORKERS)
        ]
        return stop_event, executor, futures, errors

    @staticmethod
    def _stop_receiver_verify_queue_flood(flood):
        stop_event, executor, futures, errors = flood
        stop_event.set()
        for future in futures:
            future.result()
        executor.shutdown(wait=True)
        assert not errors, errors

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
        for _ in range(timeout * 5):
            current_size = self._verify_queue_size(node)
            if current_size == expected_size:
                return
            time.sleep(0.2)
        raise AssertionError(
            f"expected verify_queue_size {expected_size}, got {current_size}"
        )

    def _wait_verify_queue_size_at_least(self, node, expected_size, timeout=10):
        for _ in range(timeout * 5):
            current_size = self._verify_queue_size(node)
            if current_size >= expected_size:
                return
            time.sleep(0.2)
        raise AssertionError(
            f"expected verify_queue_size >= {expected_size}, got {current_size}"
        )

    def _wait_rejected_transaction(self, node, tx_hash, timeout):
        last_status = None
        for _ in range(timeout * 5):
            result = node.getClient().get_transaction(tx_hash)
            last_status = result["tx_status"]
            if last_status["status"] == "rejected":
                return last_status
            time.sleep(0.2)
        raise AssertionError(f"transaction was not rejected: {last_status}")

    def _clear_receiver_verify_queue(self):
        last_error = None
        for _ in range(30):
            try:
                self.receiver.getClient().clear_tx_verify_queue()
                self._wait_verify_queue_size(self.receiver, 0)
                return
            except Exception as err:
                last_error = err
                time.sleep(1)
        raise AssertionError(f"failed to clear receiver verify queue: {last_error}")

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
