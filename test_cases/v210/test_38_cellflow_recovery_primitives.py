import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.dao_precheck.support import (
    transaction_hash,
    wait_for,
)


class _DroppedResponseProxy:
    """Forward one JSON-RPC request, then close before returning its response."""

    def __init__(self, upstream_url):
        self.upstream_url = upstream_url
        self.forwarded = threading.Event()
        self.upstream_status = None
        self.upstream_body = None
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                request_body = self.rfile.read(length)
                try:
                    response = requests.post(
                        proxy.upstream_url,
                        data=request_body,
                        headers={"Content-Type": "application/json"},
                        timeout=10,
                    )
                    proxy.upstream_status = response.status_code
                    proxy.upstream_body = response.json()
                finally:
                    proxy.forwarded.set()
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()

            def log_message(self, _format, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class TestCellFlowRecoveryPrimitives(CkbTest):
    """CKBuilder issue #38 primitives that belong at the CKB integration layer."""

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.v210,
            "v210/cellflow_recovery/node0",
            21038,
            21039,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        if hasattr(cls, "node"):
            cls.node.stop()
            cls.node.clean()

    def test_dropped_broadcast_response_recovers_by_precomputed_hash(self):
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        funding_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            1_000,
            self.node.getClient().url,
            "1500",
        )
        self.Miner.miner_until_tx_committed(self.node, funding_hash)

        output_data = "0x63656c6c666c6f77"
        transaction = self.Tx.build_send_transfer_self_tx_with_input(
            [funding_hash],
            ["0x0"],
            self.Config.MINER_PRIVATE_1,
            data=output_data,
            fee=1_000,
            api_url=self.node.getClient().url,
        )
        expected_hash = transaction_hash(transaction)
        rpc_request = {
            "id": 38,
            "jsonrpc": "2.0",
            "method": "send_transaction",
            "params": [transaction, "passthrough"],
        }

        with _DroppedResponseProxy(self.node.getClient().url) as proxy:
            with pytest.raises(requests.ConnectionError):
                requests.post(proxy.url, json=rpc_request, timeout=10)
            assert proxy.forwarded.wait(timeout=10)
            assert proxy.upstream_status == 200
            assert proxy.upstream_body == {
                "id": 38,
                "jsonrpc": "2.0",
                "result": expected_hash,
            }

        observed = wait_for(
            lambda: self._observable_transaction(expected_hash),
            "transaction recovery by precomputed hash",
        )
        assert observed["tx_status"]["status"] in {"pending", "proposed"}

        committed = self.Miner.miner_until_tx_committed(self.node, expected_hash)
        commit_hash = committed["tx_status"]["block_hash"]
        commit_number = int(committed["tx_status"]["block_number"], 16)
        self.Miner.make_tip_height_number(self.node, commit_number + 3)

        assert self.node.getClient().get_block_hash(hex(commit_number)) == commit_hash
        assert self.node.getClient().get_tip_block_number() - commit_number + 1 >= 4

        live_cell = self.node.getClient().get_live_cell("0x0", expected_hash)
        assert live_cell["status"] == "live"
        assert live_cell["block_hash"] == commit_hash
        assert live_cell["cell"]["data"]["content"] == output_data
        assert live_cell["cell"]["output"] == transaction["outputs"][0]

        spending_hash = self.Tx.send_transfer_self_tx_with_input(
            [expected_hash],
            ["0x0"],
            self.Config.MINER_PRIVATE_1,
            fee=1_000,
            api_url=self.node.getClient().url,
        )
        self.Miner.miner_until_tx_committed(self.node, spending_hash)

        spent_cell = self.node.getClient().get_live_cell("0x0", expected_hash)
        assert spent_cell["status"] == "unknown"
        assert spent_cell["cell"] is None
        self.did_pass = True

    def _observable_transaction(self, tx_hash):
        response = self.node.getClient().get_transaction(tx_hash)
        if response["tx_status"]["status"] in {"pending", "proposed", "committed"}:
            return response
        return None
