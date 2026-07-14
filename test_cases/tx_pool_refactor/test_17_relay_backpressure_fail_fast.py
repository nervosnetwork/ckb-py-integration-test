import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from framework.basic import CkbTest


class TestRelayBackpressureFailFast(CkbTest):
    REQUESTS_PER_WAVE = 256
    MAX_WAVES = 2
    MAX_WORKERS = 128
    WITNESS_BYTES = 500_000

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5239_relay_backpressure_fail_fast",
            8440,
            8441,
        )
        cls.node.prepare(
            other_ckb_config={
                "ckb_rpc_batch_limit": 1000,
                "ckb_rpc_max_request_body_size": 64 * 1024 * 1024,
                "ckb_tx_pool_max_tx_verify_workers": 0,
            }
        )
        cls.node.start()

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def setup_method(self, method):
        self.did_pass = None
        self.node.getClient().clear_tx_pool()
        self.node.getClient().clear_tx_verify_queue()

    def test_tx_pool_backpressure_requests_do_not_hang(self):
        """
        PR #5239: relay async tx-pool controller calls should fail fast when
        the tx-pool service mailbox is under backpressure.
        """
        started_at = time.monotonic()
        errors = []
        response_count = 0
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as executor:
            for wave in range(self.MAX_WAVES):
                start_index = wave * self.REQUESTS_PER_WAVE
                futures = [
                    executor.submit(
                        self._post_rpc,
                        self._rpc_request(
                            request_id=index,
                            method="notify_transaction",
                            params=[self._unknown_transaction(index)],
                        ),
                    )
                    for index in range(
                        start_index, start_index + self.REQUESTS_PER_WAVE
                    )
                ]
                futures.append(
                    executor.submit(
                        self._post_rpc,
                        self._rpc_request(
                            start_index + self.REQUESTS_PER_WAVE,
                            "tx_pool_info",
                            [],
                        ),
                    )
                )

                for future in as_completed(futures):
                    response = future.result()
                    response_count += 1
                    if "error" in response:
                        errors.append(response["error"]["message"])
        elapsed = time.monotonic() - started_at

        assert response_count == (self.REQUESTS_PER_WAVE + 1) * self.MAX_WAVES
        assert not [
            message for message in errors if "TrySendError" not in message
        ], errors
        assert elapsed < 60

        self._wait_tx_pool_rpc_recovers()
        self.did_pass = True

    def _unknown_transaction(self, index):
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
            "witnesses": ["0x" + "00" * self.WITNESS_BYTES],
        }

    def _wait_tx_pool_rpc_recovers(self):
        last_error = None
        for _ in range(30):
            try:
                self.node.getClient().clear_tx_verify_queue()
                self.node.getClient().tx_pool_info()
                return
            except Exception as err:
                last_error = err
                time.sleep(1)
        raise AssertionError(f"tx-pool RPC did not recover: {last_error}")

    def _rpc_request(self, request_id, method, params):
        return {
            "id": request_id,
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

    def _post_rpc(self, data):
        request = urllib.request.Request(
            self.node.getClient().url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
