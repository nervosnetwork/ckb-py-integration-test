"""Pure transaction encoding helpers; node lifecycle comes from CkbTest."""

import copy
import hashlib
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CKB = 100_000_000
FEE = 100_000
ZERO_DAO = "0x0000000000000000"


def ckb_hash(data):
    return (
        "0x"
        + hashlib.blake2b(data, digest_size=32, person=b"ckb-default-hash").hexdigest()
    )


def blob(value):
    return bytes.fromhex(value.removeprefix("0x"))


def uint(value, width=4):
    return (int(value, 16) if isinstance(value, str) else value).to_bytes(
        width, "little"
    )


def table(fields):
    if not fields:
        return uint(4)
    offset = 4 * (len(fields) + 1)
    offsets = []
    for field in fields:
        offsets.append(uint(offset))
        offset += len(field)
    return uint(offset) + b"".join(offsets + fields)


def byte_string(data):
    return uint(len(data)) + data


def fixvec(items):
    return uint(len(items)) + b"".join(items)


def script_bytes(script):
    return table(
        [
            blob(script["code_hash"]),
            bytes(
                [{"data": 0, "type": 1, "data1": 2, "data2": 4}[script["hash_type"]]]
            ),
            byte_string(blob(script["args"])),
        ]
    )


def outpoint_bytes(point):
    return blob(point["tx_hash"]) + uint(point["index"])


def transaction_hash(tx):
    """CKB raw transaction hash (witnesses excluded); checked against live RPC."""
    deps = [
        outpoint_bytes(d["out_point"])
        + bytes([{"code": 0, "dep_group": 1}[d["dep_type"]]])
        for d in tx["cell_deps"]
    ]
    inputs = [
        uint(i["since"], 8) + outpoint_bytes(i["previous_output"]) for i in tx["inputs"]
    ]
    outputs = [
        table(
            [
                uint(o["capacity"], 8),
                script_bytes(o["lock"]),
                script_bytes(o["type"]) if o.get("type") else b"",
            ]
        )
        for o in tx["outputs"]
    ]
    raw = table(
        [
            uint(tx["version"]),
            fixvec(deps),
            fixvec([blob(h) for h in tx["header_deps"]]),
            fixvec(inputs),
            table(outputs),
            table([byte_string(blob(d)) for d in tx["outputs_data"]]),
        ]
    )
    return ckb_hash(raw)


def witness(header_index=0):
    return "0x" + table([b"", byte_string(uint(header_index, 8)), b""]).hex()


def transaction(inputs, outputs, data, deps, headers=None, since="0x0"):
    return {
        "version": "0x0",
        "cell_deps": copy.deepcopy(deps),
        "header_deps": headers or [],
        "inputs": [{"previous_output": p, "since": since} for p in inputs],
        "outputs": copy.deepcopy(outputs),
        "outputs_data": data,
        "witnesses": [witness() for _ in inputs],
    }


def wait_for(predicate, label, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"timeout waiting for {label}; last={last!r}")


def assert_dao_error(reason, kind, index=0):
    text = str(reason)
    phrases = {
        "lock": ("DaoLockSizeMismatch", "lock script size of deposit cell"),
        "data": ("DaoOutputDataMismatch", "DAO output data"),
    }
    assert any(s in text for s in phrases[kind]), text
    assert re.search(rf"index[\s:\\]+{index}\b", text), text


def assert_script_error(reason):
    text = str(reason)
    assert "ValidationFailure" in text and "error code -2" in text, text
    assert (
        "DaoLockSizeMismatch" not in text and "DaoOutputDataMismatch" not in text
    ), text
