"""Node-free checks of wire inputs and external-observation helpers."""

import json
import socket
import struct
import threading
import time
from types import SimpleNamespace

from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import (
    Transactions,
    packed_transaction,
    size_in_block,
)
from test_cases.tx_pool_refactor.dao_precheck.support import ckb_hash, transaction_hash
from test_cases.tx_pool_refactor.relay_v3_codec import raw_transaction_hash
from test_cases.tx_pool_refactor import test_witness_revalidation as public_pool

independent_size = public_pool.TestPublicPoolRpc._serialized_size_in_block
from test_cases.tx_pool_refactor.subscription_client import SubscriptionClient
from test_cases.tx_pool_refactor.tcp_response_gate import ResponseGate
from test_cases.tx_pool_refactor.extended.performance import percentiles


def fixture_transactions():
    code = workload.script_path("always_success")
    lock = {"code_hash": workload.script_hash(code), "hash_type": "data", "args": "0x"}
    genesis = {
        "hash": "0x" + "11" * 32,
        "outputs": [{"capacity": hex(100 * workload.CKB), "lock": lock, "type": None}]
        + [
            {"capacity": hex(6500 * workload.CKB), "lock": lock, "type": None}
            for _ in range(128)
        ],
        "outputs_data": ["0x" + code.read_bytes().hex()] + ["0x"] * 128,
    }
    client = SimpleNamespace(get_block_by_number=lambda _: {"transactions": [genesis]})
    return Transactions(SimpleNamespace(getClient=lambda: client), 5316)


def test_packed_transaction_matches_independent_parser_size_and_raw_hash():
    txs = fixture_transactions()
    parent = txs.build([0, 1], outputs=2)
    child = txs.build([txs.point(parent)], deps=[txs.point(parent, 1)], padding=300)
    for tx in (parent, child):
        packed = packed_transaction(tx)
        assert "0x" + raw_transaction_hash(packed).hex() == transaction_hash(tx)
        total, raw_start, witnesses = struct.unpack_from("<III", packed)
        assert total == len(packed) and raw_start == 12
        assert ckb_hash(packed[raw_start:witnesses]) == transaction_hash(tx)
        assert size_in_block(tx) == independent_size(tx)


def test_witness_padding_keeps_hash_and_fee_but_changes_real_serialized_size():
    txs = fixture_transactions()
    tx = txs.build([0], fee=2000)
    padded = txs.pad_to(tx, 100 * size_in_block(tx))
    assert transaction_hash(padded) == transaction_hash(tx)
    assert packed_transaction(padded) != packed_transaction(tx)
    assert padded["outputs"] == tx["outputs"]
    assert independent_size(padded) == 100 * size_in_block(tx)
    assert txs.fees[transaction_hash(padded)] == 2000


def test_diamond_fees_and_capacity_boundary_are_independent_of_pool_rpc():
    txs = fixture_transactions()
    a, d = txs.build([0]), txs.build([1])
    child = txs.build([txs.point(a), txs.point(d)])
    extras = [txs.build([index]) for index in range(2, 99)]
    victims = [a, d, child, *extras]
    assert len({transaction_hash(tx) for tx in victims}) == 100
    assert sum(txs.fees[transaction_hash(tx)] for tx in (a, d, child)) == 3000
    replacement = txs.pad_to(
        txs.build([0, 1], fee=10000000), sum(map(size_in_block, victims))
    )
    assert size_in_block(replacement) == sum(independent_size(tx) for tx in victims)
    assert all(
        size_in_block(replacement)
        > sum(size_in_block(tx) for tx in victims if tx is not omitted)
        for omitted in victims
    )


def test_sparse_latency_samples_do_not_publish_p99():
    assert percentiles([]) == {
        "n": 0,
        "p50": None,
        "p95": None,
        "p99": None,
        "p99_status": "insufficient_samples",
    }
    small = percentiles([3, 1, 2])
    assert small["p50"] == 2 and small["p99"] is None
    enough = percentiles(list(range(100)))
    assert enough["p99"] == 98 and enough["p99_status"] == "available"


def test_response_gate_holds_stream_bytes_and_cleans_up():
    client, downstream = socket.socketpair()
    upstream, server_connection = socket.socketpair()
    received = []

    def server():
        try:
            received.append(server_connection.recv(100))
            server_connection.sendall(b"real response")
            server_connection.recv(1)
        finally:
            server_connection.close()

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    gate = ResponseGate.__new__(ResponseGate)
    gate.delay, gate.pending_bytes, gate.errors = 0.02, 0, []
    gate.held, gate.stopped = threading.Event(), threading.Event()
    gate.sockets = [downstream, upstream]
    gate.held.set()
    gate.thread = threading.Thread(
        target=gate.forward, args=(downstream, upstream), daemon=True
    )
    gate.thread.start()
    try:
        client.sendall(b"actual request")
        workload.wait_for(
            lambda: gate.pending_bytes == len(b"real response"),
            "fixture response queued",
            2,
        )
        assert received == [b"actual request"]
        client.settimeout(0.05)
        try:
            client.recv(100)
        except socket.timeout:
            pass
        else:
            raise AssertionError("held downstream data escaped gate")
        gate.held.clear()
        client.settimeout(2)
        assert client.recv(100) == b"real response"
        assert not gate.errors
    finally:
        gate.close()
        client.close()
        thread.join(timeout=2)
    assert not thread.is_alive()


def test_subscription_preserves_event_before_ack_and_fragmented_frame():
    reader, writer = socket.socketpair()
    client = SubscriptionClient.__new__(SubscriptionClient)
    client.socket, client.buffer, client.events, client.request_id = reader, b"", [], 0
    tx = {"hash": "0x" + "22" * 32}
    event = {
        "method": "subscribe",
        "params": {"subscription": "0x1", "result": json.dumps({"transaction": tx})},
    }
    ack = {"id": 1, "jsonrpc": "2.0", "result": "0x1"}

    def server():
        writer.recv(4096)
        data = (json.dumps(event) + "\n" + json.dumps(ack) + "\n").encode()
        writer.sendall(data[:7])
        time.sleep(0.01)
        writer.sendall(data[7:])

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    try:
        assert client.call("subscribe", ["new_transaction"]) == "0x1"
        assert client.collect(0.02) == [event]
        assert client.transaction(event) == tx
    finally:
        reader.close()
        writer.close()
        thread.join(timeout=2)
