"""Export CLI transactions whose inputs share one secp256k1 sighash lock."""

import json
import struct


def _signed_witness(witness, signature):
    fields = [b"", b"", b""]
    if witness != "0x":
        raw = bytes.fromhex(witness[2:])
        if len(raw) < 16:
            raise ValueError("invalid WitnessArgs header")
        size, *offsets = struct.unpack_from("<4I", raw)
        if (
            size != len(raw)
            or offsets[0] != 16
            or offsets != sorted(offsets)
            or offsets[-1] > size
        ):
            raise ValueError("invalid WitnessArgs offsets")
        ends = offsets[1:] + [size]
        fields = [raw[start:end] for start, end in zip(offsets, ends)]
        for field in fields:
            if field and (
                len(field) < 4 or int.from_bytes(field[:4], "little") != len(field) - 4
            ):
                raise ValueError("invalid WitnessArgs field")
    fields[0] = struct.pack("<I", len(signature)) + signature
    offsets = [16, 16 + len(fields[0]), 16 + len(fields[0]) + len(fields[1])]
    size = 16 + sum(map(len, fields))
    return "0x" + (struct.pack("<4I", size, *offsets) + b"".join(fields)).hex()


def build_tx_info(tx_file):
    """Retain type/extra witnesses and the CLI's per-input empty witnesses.

    The signing CLI pads missing input witnesses before hashing. Dropping those
    empty witnesses changes the sighash message even though the tx hash is the
    same. Multiple lock groups/multisig require a group-aware exporter instead.
    """
    with open(tx_file) as stream:
        info = json.load(stream)
    groups = list(info["signatures"].values())
    if len(groups) != 1 or len(groups[0]) != 1:
        raise ValueError("expected a single sighash signing group")
    signature = bytes.fromhex(groups[0][0][2:])
    if len(signature) != 65:
        raise ValueError("expected a 65-byte sighash signature")
    transaction = info["transaction"]
    if not transaction["inputs"]:
        raise ValueError("expected at least one input")
    witnesses = list(transaction.get("witnesses", []))
    witnesses.extend(["0x"] * max(0, len(transaction["inputs"]) - len(witnesses)))
    witnesses[0] = _signed_witness(witnesses[0], signature)
    transaction["witnesses"] = witnesses
    return transaction
