"""Explicit binaries and fixture inputs for the opt-in local-node regression."""

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "test_cases/network_verification/fixtures/vm-cycle-baseline.json"
requires_target = pytest.mark.skipif(
    not os.environ.get("CKB_TEST_TARGET_MANIFEST"),
    reason="Opt-in budget regression: use test_cases/network_verification/run_selected.py",
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_target(test_binary, production_binary, cli, testdata, directory):
    """Snapshot caller-supplied inputs; never build or modify product sources."""
    directory = Path(directory).resolve()
    if ROOT.name != "ckb-py-integration-test":
        raise ValueError(
            "Legacy framework requires checkout directory name ckb-py-integration-test"
        )
    # Legacy node helpers interpolate their working directory into shell commands.
    # Fail before preparation rather than misquoting or operating on another path.
    for path in (ROOT, directory):
        if not re.fullmatch(r"[/A-Za-z0-9_.-]+", str(path)):
            raise ValueError(
                f"Legacy node helpers require a shell-safe checkout/output path: {path}"
            )
    data = {"testdata": str(Path(testdata).resolve())}
    for kind, value in (
        ("test", test_binary),
        ("production", production_binary),
        ("cli", cli),
    ):
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"{kind} must name an executable file: {path}")
        data[kind] = {"path": str(path), "sha256": sha256(path)}
    if data["test"]["sha256"] == data["production"]["sha256"]:
        raise ValueError(
            "Supply distinct test-feature and production builds from the same revision"
        )
    expected = json.loads(BASELINE.read_text())["script_sha256"]
    for name, digest in expected.items():
        path = Path(data["testdata"]) / name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(
                f"Fixture missing or changed: {path}; expected SHA-256 {digest}"
            )
    data["script_sha256"] = expected
    directory.mkdir(parents=True, exist_ok=True)
    for kind in ("test", "production"):
        staged = directory / ("bin-" + kind)
        staged.mkdir()
        (staged / "ckb").symlink_to(data[kind]["path"])
        (staged / "ckb-cli").symlink_to(data["cli"]["path"])
        data[kind]["directory"] = os.path.relpath(staged, ROOT)
    manifest = directory / "target.json"
    manifest.write_text(json.dumps(data, indent=2) + "\n")
    return manifest


@lru_cache(maxsize=1)
def target():
    data = json.loads(Path(os.environ["CKB_TEST_TARGET_MANIFEST"]).read_text())
    for kind in ("test", "production", "cli"):
        assert sha256(data[kind]["path"]) == data[kind]["sha256"], kind
    expected = json.loads(BASELINE.read_text())["script_sha256"]
    assert data["script_sha256"] == expected
    for name, digest in expected.items():
        assert sha256(Path(data["testdata"]) / name) == digest, name
    return data


def binary_dir(kind="test"):
    data = target()
    directory = ROOT / data[kind]["directory"]
    assert sha256(directory / "ckb") == data[kind]["sha256"]
    assert sha256(directory / "ckb-cli") == data["cli"]["sha256"]
    return data[kind]["directory"]


def script_path(name):
    data = target()
    assert name in data["script_sha256"], name
    return Path(data["testdata"]) / name
