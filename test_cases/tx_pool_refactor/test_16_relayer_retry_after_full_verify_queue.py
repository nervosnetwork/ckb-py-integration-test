import json
import time
import urllib.request

from framework.basic import CkbTest


class TestRelayerRetryAfterFullVerifyQueue(CkbTest):
    LARGE_FILLER_BATCH_SIZE = 10
    LARGE_FILLER_WITNESS_BYTES = 500_000
    MAX_LARGE_FILLER_TXS = 540
    SMALL_FILLER_BATCH_SIZE = 100
    SMALL_FILLER_WITNESS_BYTES = 0
    MAX_SMALL_FILLER_TXS = 5000

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
                other_ckb_config={"ckb_tx_pool_max_tx_verify_workers": 0}
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

        target_tx = self._build_target_transaction()
        full_queue_size = self._fill_receiver_verify_queue()

        target_hash = self.sender.getClient().send_transaction(target_tx)
        self.Node.wait_get_transaction(self.sender, target_hash, "pending")
        time.sleep(5)
        assert self._verify_queue_size(self.receiver) == full_queue_size
        assert (
            self.receiver.getClient().get_transaction(target_hash)["tx_status"][
                "status"
            ]
            == "unknown"
        )

        self.receiver.getClient().clear_tx_verify_queue()
        self._wait_verify_queue_size(self.receiver, 0)

        assert self.sender.getClient().remove_transaction(target_hash)
        assert self.sender.getClient().send_transaction(target_tx) == target_hash
        self._wait_verify_queue_size_at_least(self.receiver, 1, timeout=30)
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

    def _fill_receiver_verify_queue(self):
        previous_size = self._verify_queue_size(self.receiver)
        previous_size = self._fill_with_filler_transactions(
            previous_size,
            start_index=0,
            max_txs=self.MAX_LARGE_FILLER_TXS,
            batch_size=self.LARGE_FILLER_BATCH_SIZE,
            witness_bytes=self.LARGE_FILLER_WITNESS_BYTES,
        )
        return self._fill_with_filler_transactions(
            previous_size,
            start_index=self.MAX_LARGE_FILLER_TXS,
            max_txs=self.MAX_SMALL_FILLER_TXS,
            batch_size=self.SMALL_FILLER_BATCH_SIZE,
            witness_bytes=self.SMALL_FILLER_WITNESS_BYTES,
        )

    def _fill_with_filler_transactions(
        self, previous_size, start_index, max_txs, batch_size, witness_bytes
    ):
        for batch_start in range(start_index, start_index + max_txs, batch_size):
            for index in range(batch_start, batch_start + batch_size):
                tx = self._unknown_transaction(index, witness_bytes)
                self._notify_filler_transaction(tx)
            expected_size = previous_size + batch_size
            current_size = self._wait_verify_queue_progress(
                previous_size, expected_size
            )
            if current_size == previous_size:
                assert previous_size > 0
                return previous_size
            previous_size = current_size

        raise AssertionError("verify queue did not become full")

    def _unknown_transaction(self, index, witness_bytes):
        lock_arg = f"0x{index + 1:040x}"
        unknown_input = f"0x{index + 1:064x}"
        return {
            "version": "0x0",
            "cell_deps": [],
            "header_deps": [],
            "inputs": [
                {
                    "previous_output": {"tx_hash": unknown_input, "index": "0x0"},
                    "since": "0x0",
                }
            ],
            "outputs": [
                {
                    "capacity": "0x2540be400",
                    "lock": {
                        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                        "hash_type": "type",
                        "args": lock_arg,
                    },
                    "type": None,
                }
            ],
            "outputs_data": ["0x"],
            "witnesses": ["0x" + "00" * witness_bytes],
        }

    def _notify_filler_transaction(self, tx):
        self._call_rpc_quiet(self.receiver, "notify_transaction", [tx])

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

    def _wait_verify_queue_progress(self, previous_size, expected_size):
        for _ in range(50):
            current_size = self._verify_queue_size(self.receiver)
            if current_size != previous_size or current_size >= expected_size:
                return current_size
            time.sleep(0.2)
        return self._verify_queue_size(self.receiver)

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
