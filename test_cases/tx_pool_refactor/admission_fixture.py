"""Independent inputs and accounting for real RPC admission tests."""

import copy

import requests

from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.dao_precheck.support import (
    blob,
    byte_string,
    fixvec,
    outpoint_bytes,
    script_bytes,
    table,
    transaction_hash,
    uint,
)


def packed_transaction(tx):
    raw = table(
        [
            uint(tx["version"]),
            fixvec(
                [
                    outpoint_bytes(dep["out_point"])
                    + bytes([{"code": 0, "dep_group": 1}[dep["dep_type"]]])
                    for dep in tx["cell_deps"]
                ]
            ),
            fixvec([blob(value) for value in tx["header_deps"]]),
            fixvec(
                [
                    uint(value["since"], 8) + outpoint_bytes(value["previous_output"])
                    for value in tx["inputs"]
                ]
            ),
            table(
                [
                    table(
                        [
                            uint(output["capacity"], 8),
                            script_bytes(output["lock"]),
                            script_bytes(output["type"]) if output.get("type") else b"",
                        ]
                    )
                    for output in tx["outputs"]
                ]
            ),
            table([byte_string(blob(value)) for value in tx["outputs_data"]]),
        ]
    )
    return table([raw, table([byte_string(blob(value)) for value in tx["witnesses"]])])


def size_in_block(tx):
    return len(packed_transaction(tx)) + 4


def rpc_result(client, method, params):
    """Keep error code/data intact; transport errors are test failures."""
    response = requests.post(
        client.url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        },
        timeout=30,
    )
    assert response.status_code == 200, (response.status_code, response.text)
    return response.json()


class Transactions:
    """One always-success lock group; fee comes from known cell capacities."""

    def __init__(self, node, tag):
        self.genesis = node.getClient().get_block_by_number("0x0")["transactions"][0]
        self.lock_hash = workload.script_hash(workload.script_path("always_success"))
        self.cells = [
            {"tx_hash": self.genesis["hash"], "index": hex(index)}
            for index, output in enumerate(self.genesis["outputs"])
            if output["lock"]["code_hash"] == self.lock_hash
            and int(output["capacity"], 16) == 6500 * workload.CKB
        ]
        code = "0x" + workload.script_path("always_success").read_bytes().hex()
        self.dep = {
            "out_point": {
                "tx_hash": self.genesis["hash"],
                "index": hex(self.genesis["outputs_data"].index(code)),
            },
            "dep_type": "code",
        }
        self.tag = tag
        self.transactions = {self.genesis["hash"]: self.genesis}
        self.fees = {}

    def point(self, tx, index=0):
        return {"tx_hash": transaction_hash(tx), "index": hex(index)}

    def build(self, inputs, *, fee=1000, outputs=1, deps=(), padding=0, nonce=0):
        points = [self.cells[p] if isinstance(p, int) else p for p in inputs]
        capacity = sum(
            int(
                self.transactions[p["tx_hash"]]["outputs"][int(p["index"], 16)][
                    "capacity"
                ],
                16,
            )
            for p in points
        )
        capacities = [(capacity - fee) // outputs] * outputs
        capacities[-1] += (capacity - fee) % outputs
        assert min(capacities) > 100 * workload.CKB
        tx = {
            "version": "0x0",
            "cell_deps": [copy.deepcopy(self.dep)]
            + [
                {"out_point": copy.deepcopy(point), "dep_type": "code"}
                for point in deps
            ],
            "header_deps": [],
            "inputs": [
                {"previous_output": copy.deepcopy(point), "since": "0x0"}
                for point in points
            ],
            "outputs": [
                {
                    "capacity": hex(value),
                    "lock": {
                        "code_hash": self.lock_hash,
                        "hash_type": "data",
                        "args": "0x",
                    },
                    "type": None,
                }
                for value in capacities
            ],
            # Equal-width tags prevent accidental cross-test recent-reject hits.
            "outputs_data": ["0x" + uint(self.tag, 4).hex() + uint(nonce, 4).hex()]
            * outputs,
            "witnesses": ["0x" + "00" * padding],
        }
        tx_hash = transaction_hash(tx)
        self.transactions[tx_hash] = tx
        self.fees[tx_hash] = fee
        return tx

    def pad_to(self, tx, size):
        tx = copy.deepcopy(tx)
        missing = size - size_in_block(tx)
        assert missing >= 0, (size, size_in_block(tx))
        tx["witnesses"][0] += "00" * missing
        assert size_in_block(tx) == size
        self.transactions[transaction_hash(tx)] = tx
        return tx


def assert_pool(node, transactions, bodies):
    """Exact Accepted membership, body and independently computed accounting."""
    client = node.getClient()
    expected = {transaction_hash(tx): tx for tx in bodies}
    entries = workload.pool_transactions(client)
    assert set(entries) == set(expected), (set(entries), set(expected))
    info = client.tx_pool_info()
    assert int(info["total_tx_size"], 16) == sum(size_in_block(tx) for tx in bodies)
    assert int(info["total_tx_cycles"], 16) == len(bodies) * workload.FAST_CYCLES
    for tx_hash, tx in expected.items():
        assert int(entries[tx_hash]["fee"], 16) == transactions.fees[tx_hash]
        assert int(entries[tx_hash]["size"], 16) == size_in_block(tx)
        workload.assert_pool_transaction(node, tx_hash, tx, workload.FAST_CYCLES)


def propose(node, miner, hashes):
    client = node.getClient()
    short_ids = {value[:22] for value in hashes}
    workload.wait_for(
        lambda: short_ids <= set(client.get_block_template()["proposals"]),
        "all intended transactions proposed",
    )
    for _ in range(2):
        template = client.get_block_template()
        template["transactions"] = []
        tip = client.generate_block_with_template(template)
        workload.wait_for(
            lambda: client.tx_pool_info()["tip_hash"] == tip, "proposal tip installed"
        )
