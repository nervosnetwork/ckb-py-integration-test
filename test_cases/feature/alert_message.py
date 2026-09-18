"""Signed legacy Alert inputs for isolated devnet RPC tests.

The public test key below comes from CKB's former
util/network-alert/src/tests/generate_alert_signature.rs. Never fund this key.
Only the RawAlert signing input is encoded here; nodes handle P2P messages.
"""

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile


TEST_PRIVATE_KEY = "3b3b6f014ceb07c8a14dc2d5bb08f3ee6975811bbdd0d8d53fe2c9e69ef4e498"
TEST_PUBLIC_KEY = "0x0329e3889cae7b1788836ff8841646174aeb7828a4f997c7f8e54c7257c9ff21a3"


def alert_signature_config() -> dict:
    """Return the value of [alert_signature] for the public test key."""
    return {"signatures_threshold": 1, "public_keys": [TEST_PUBLIC_KEY]}


def _raw_alert_bytes(*, alert_id: int, message: str, notice_until: int) -> bytes:
    """Encode the seven RawAlert fields in their Molecule schema order.

    Source: CKB 17d7db5, util/gen-types/schemas/extensions.mol (RawAlert).
    Integers and table offsets are little endian. Bytes contains its byte
    count, while an absent BytesOpt has no bytes at all.
    """
    message_bytes = message.encode("utf-8")
    fields = [
        struct.pack("<Q", notice_until),
        struct.pack("<I", alert_id),
        struct.pack("<I", 0),  # cancel
        struct.pack("<I", 1),  # priority
        struct.pack("<I", len(message_bytes)) + message_bytes,
        b"",  # min_version = None
        b"",  # max_version = None
    ]
    total_size = 4 * (len(fields) + 1)
    offsets = []
    for field in fields:
        offsets.append(total_size)
        total_size += len(field)
    return struct.pack("<8I", total_size, *offsets) + b"".join(fields)


def signed_alert(
    ckb_cli: str | Path,
    *,
    alert_id: int,
    message: str,
    notice_until: int,
) -> dict:
    """Build send_alert JSON, with notice_until expressed in Unix milliseconds.

    The exact JSON (including its timestamp and signature) should be reused
    for every replay. min_version/max_version are absent so the fixture is
    applicable to all old node versions. Expired timestamps are allowed here;
    the old send_alert RPC still rejects them at submission time.
    """
    raw_alert = _raw_alert_bytes(
        alert_id=alert_id, message=message, notice_until=notice_until
    )
    # RawAlert::calc_alert_hash hashes only the serialized RawAlert table.
    digest = hashlib.blake2b(
        raw_alert, digest_size=32, person=b"ckb-default-hash"
    ).hexdigest()
    cli_path = Path(ckb_cli).expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="ckb-alert-sign-") as temporary_dir:
        work_dir = Path(temporary_dir)
        key_path = work_dir / "public-test-key"
        key_path.write_text(TEST_PRIVATE_KEY + "\n", encoding="ascii")
        key_path.chmod(0o600)
        result = subprocess.run(
            [
                str(cli_path),
                "--local-only",
                "--no-color",
                "--output-format",
                "json",
                "util",
                "sign-message",
                "--recoverable",
                "--privkey-path",
                str(key_path),
                "--message",
                "0x" + digest,
            ],
            env={**os.environ, "CKB_CLI_HOME": str(work_dir / "cli")},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
    output = json.loads(result.stdout)
    signature = output["signature"]
    if not output.get("recoverable") or not signature.startswith("0x"):
        raise ValueError(f"ckb-cli did not return a recoverable signature: {output}")
    signature_bytes = bytes.fromhex(signature[2:])
    if len(signature_bytes) != 65 or signature_bytes[-1] > 3:
        raise ValueError("ckb-cli returned an invalid recoverable signature encoding")
    return {
        "id": hex(alert_id),
        "cancel": "0x0",
        "min_version": None,
        "max_version": None,
        "priority": "0x1",
        "notice_until": hex(notice_until),
        "message": message,
        "signatures": [signature],
    }
