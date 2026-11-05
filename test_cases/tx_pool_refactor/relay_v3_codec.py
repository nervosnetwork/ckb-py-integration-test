"""Minimal Molecule codec for the two PR #5316 RelayV3 fetch paths.

Wire layout follows the fixed product's ``extensions.mol`` and
``blockchain.mol``.  Transport, peer authentication and message framing are
deliberately separate; this module only handles RelayMessage payload bytes.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass


class MoleculeError(ValueError):
    """Malformed or unexpected RelayV3 Molecule payload."""


@dataclass(frozen=True)
class RelayTransaction:
    cycles: int
    transaction: bytes


@dataclass(frozen=True)
class RelayResponse:
    kind: str
    transactions: tuple[bytes | RelayTransaction, ...]


def _u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise MoleculeError(f"u32 out of range: {value}")
    return struct.pack("<I", value)


def _read_u32(data: bytes, offset: int = 0) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise MoleculeError("truncated u32")
    return struct.unpack_from("<I", data, offset)[0]


def _fixed(value: bytes, size: int, label: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != size:
        raise MoleculeError(f"{label} must be exactly {size} bytes")
    return value


def _fixvec(items: tuple[bytes, ...], item_size: int) -> bytes:
    if any(len(item) != item_size for item in items):
        raise MoleculeError(f"fixvec item must be {item_size} bytes")
    return _u32(len(items)) + b"".join(items)


def _table(*fields: bytes) -> bytes:
    offset = 4 + 4 * len(fields)
    offsets = []
    for field in fields:
        offsets.append(_u32(offset))
        offset += len(field)
    return _u32(offset) + b"".join(offsets) + b"".join(fields)


def _fields(data: bytes, count: int) -> tuple[bytes, ...]:
    if len(data) < 4 + 4 * count or _read_u32(data) != len(data):
        raise MoleculeError("invalid table size")
    offsets = [_read_u32(data, 4 + 4 * i) for i in range(count)]
    if offsets and offsets[0] != 4 + 4 * count:
        raise MoleculeError("invalid first table offset")
    if any(left > right for left, right in zip(offsets, offsets[1:])):
        raise MoleculeError("unordered table offsets")
    if any(offset > len(data) for offset in offsets):
        raise MoleculeError("table offset beyond end")
    return tuple(
        data[start:end] for start, end in zip(offsets, offsets[1:] + [len(data)])
    )


def _dynvec(data: bytes) -> tuple[bytes, ...]:
    if len(data) < 4 or _read_u32(data) != len(data):
        raise MoleculeError("invalid dynvec size")
    if len(data) == 4:
        return ()
    first = _read_u32(data, 4)
    if first < 8 or first > len(data) or (first - 4) % 4:
        raise MoleculeError("invalid dynvec header")
    count = (first - 4) // 4
    offsets = [_read_u32(data, 4 + 4 * i) for i in range(count)]
    if offsets[0] != first or any(a > b for a, b in zip(offsets, offsets[1:])):
        raise MoleculeError("unordered dynvec offsets")
    if any(offset > len(data) for offset in offsets):
        raise MoleculeError("dynvec offset beyond end")
    return tuple(
        data[start:end] for start, end in zip(offsets, offsets[1:] + [len(data)])
    )


def _check_fixvec(data: bytes, item_size: int) -> None:
    if len(data) < 4 or len(data) != 4 + _read_u32(data) * item_size:
        raise MoleculeError("invalid fixvec size")


def _check_script(data: bytes) -> None:
    code_hash, hash_type, args = _fields(data, 3)
    if len(code_hash) != 32 or len(hash_type) != 1:
        raise MoleculeError("invalid Script fixed fields")
    _check_fixvec(args, 1)


def _raw_transaction(transaction: bytes) -> bytes:
    raw, witnesses = _fields(transaction, 2)
    version, cell_deps, header_deps, inputs, outputs, outputs_data = _fields(raw, 6)
    if len(version) != 4:
        raise MoleculeError("invalid RawTransaction.version")
    _check_fixvec(cell_deps, 37)  # CellDep = OutPoint(36) + dep_type(1)
    _check_fixvec(header_deps, 32)
    _check_fixvec(inputs, 44)  # CellInput = since(8) + OutPoint(36)
    for output in _dynvec(outputs):
        capacity, lock, type_script = _fields(output, 3)
        if len(capacity) != 8:
            raise MoleculeError("invalid CellOutput.capacity")
        _check_script(lock)
        if type_script:  # Molecule ScriptOpt: empty bytes = None.
            _check_script(type_script)
    for item in _dynvec(outputs_data) + _dynvec(witnesses):
        _check_fixvec(item, 1)
    return raw


def encode_get_block_proposal(block_hash: bytes, short_ids: tuple[bytes, ...]) -> bytes:
    """RelayMessage tag 6: short-ID lookup plus the current block hash."""
    block_hash = _fixed(block_hash, 32, "block_hash")
    proposals = tuple(_fixed(item, 10, "proposal_short_id") for item in short_ids)
    return _u32(6) + _table(block_hash, _fixvec(proposals, 10))


def encode_get_relay_transactions(full_hashes: tuple[bytes, ...]) -> bytes:
    """RelayMessage tag 3: full 32-byte hash lookup."""
    hashes = tuple(_fixed(item, 32, "transaction_hash") for item in full_hashes)
    return _u32(3) + _table(_fixvec(hashes, 32))


def decode_fetch_response(data: bytes) -> RelayResponse:
    """Decode only BlockProposal (tag 7) or RelayTransactions (tag 1)."""
    if len(data) < 4:
        raise MoleculeError("truncated RelayMessage")
    tag, body = _read_u32(data), data[4:]
    if tag == 7:
        (items,) = _fields(body, 1)
        transactions = _dynvec(items)
        for transaction in transactions:
            _raw_transaction(transaction)
        return RelayResponse("BlockProposal", transactions)
    if tag == 1:
        (items,) = _fields(body, 1)
        transactions = []
        for item in _dynvec(items):
            cycles, transaction = _fields(item, 2)
            if len(cycles) != 8:
                raise MoleculeError("RelayTransaction.cycles must be u64")
            _raw_transaction(transaction)
            transactions.append(
                RelayTransaction(struct.unpack("<Q", cycles)[0], transaction)
            )
        return RelayResponse("RelayTransactions", tuple(transactions))
    raise MoleculeError(f"unexpected RelayMessage tag: {tag}")


def raw_transaction_hash(transaction: bytes) -> bytes:
    """Hash the packed Transaction.raw field, excluding witnesses."""
    raw = _raw_transaction(transaction)
    return hashlib.blake2b(raw, digest_size=32, person=b"ckb-default-hash").digest()


def encode_transaction(tx: dict) -> bytes:
    """Reuse the existing transaction encoder; add the witness vector only."""
    from test_cases.tx_pool_refactor.dao_precheck.support import (
        raw_transaction_bytes,
        table,
        byte_string,
        blob,
    )

    packed = table(
        [
            raw_transaction_bytes(tx),
            table([byte_string(blob(w)) for w in tx["witnesses"]]),
        ]
    )
    _raw_transaction(packed)  # Validate exact field/vector widths before sending.
    return packed


def encode_relay_hashes(tx_hash: str) -> bytes:
    return _u32(2) + _table(
        _fixvec((_fixed(bytes.fromhex(tx_hash[2:]), 32, "hash"),), 32)
    )


def encode_relay_transaction(tx: dict, cycles: int) -> bytes:
    if not 0 <= cycles <= 0xFFFFFFFFFFFFFFFF:
        raise MoleculeError("cycles must fit u64")
    item = _table(struct.pack("<Q", cycles), encode_transaction(tx))
    return _u32(1) + _table(_table(item))


def encode_block_proposal(tx: dict) -> bytes:
    return _u32(7) + _table(_table(encode_transaction(tx)))


def decode_transaction_request(message: bytes) -> tuple[bytes, ...]:
    if _read_u32(message) != 3:
        raise MoleculeError("not GetRelayTransactions")
    (hashes,) = _fields(message[4:], 1)
    _check_fixvec(hashes, 32)
    return tuple(hashes[i : i + 32] for i in range(4, len(hashes), 32))


def decode_proposal_request(message: bytes) -> tuple[bytes, tuple[bytes, ...]]:
    if _read_u32(message) != 6:
        raise MoleculeError("not GetBlockProposal")
    block_hash, short_ids = _fields(message[4:], 2)
    _fixed(block_hash, 32, "block_hash")
    _check_fixvec(short_ids, 10)
    return block_hash, tuple(
        short_ids[i : i + 10] for i in range(4, len(short_ids), 10)
    )


def encode_compact_block(block: dict) -> bytes:
    """Prefill all transactions of an already validated full block."""
    from test_cases.tx_pool_refactor.dao_precheck.support import uint, blob, ckb_hash

    h = block["header"]
    header = b"".join(
        [
            uint(h["version"]),
            uint(h["compact_target"]),
            *(uint(h[k], 8) for k in ("timestamp", "number", "epoch")),
            *(
                blob(h[k])
                for k in (
                    "parent_hash",
                    "transactions_root",
                    "proposals_hash",
                    "extra_hash",
                    "dao",
                )
            ),
            uint(h["nonce"], 16),
        ]
    )
    assert len(header) == 208 and ckb_hash(header) == h["hash"]
    prefilled = tuple(
        _table(_u32(i), encode_transaction(tx))
        for i, tx in enumerate(block["transactions"])
    )
    fields = [
        header,
        _fixvec((), 10),
        _table(*prefilled),
        _fixvec(tuple(blob(u["header"]["hash"]) for u in block["uncles"]), 32),
        _fixvec(tuple(blob(p) for p in block["proposals"]), 10),
    ]
    if block.get("extension") is not None:
        extension = blob(block["extension"])
        fields.append(_u32(len(extension)) + extension)
    return _u32(0) + _table(*fields)
