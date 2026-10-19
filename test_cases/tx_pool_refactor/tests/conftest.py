"""Opt-in Python integration tests against the fixed PR #5316 source."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from framework.test_node import CkbNodeConfigPath, DOCKER


ROOT = Path(__file__).resolve().parents[3]
RELATIVE_BIN_DIR = ".ai-test-agent/current/ckb-pr-5316-current/bin-suite"
EXPECTED_HEAD = "60b8e349470bdffe57e9e92c3e0961b91c326b3a"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _link_once(link, target):
    if not link.is_symlink() and not link.exists():
        try:
            link.symlink_to(os.path.relpath(target, link.parent))
        except FileExistsError:
            pass  # Another pytest worker may have installed the same link.
    assert link.is_symlink() and link.resolve() == target.resolve(), link


def pytest_addoption(parser):
    parser.addoption(
        "--run-pr5316",
        action="store_true",
        default=False,
        help="run the pinned PR5316 source/build regression suite (see its README)",
    )


@pytest.fixture(scope="session")
def pr5316_binary_dir(request):
    if not request.config.getoption("--run-pr5316"):
        pytest.skip("pinned source suite: opt in with --run-pr5316")
    assert not DOCKER, "This PR fixture requires a local pinned CKB process"
    source = Path(
        os.environ.get(
            "CKB_PR5316_SOURCE_DIR",
            ROOT / ".ai-test-agent/current/ckb-pr-5316-current/product",
        )
    ).resolve()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert head == EXPECTED_HEAD, (head, EXPECTED_HEAD)
    changed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert not changed, f"Pinned source checkout has changes: {changed}"

    from test_cases.tx_pool_refactor.tests.test_45_pr5316_time_budget_retry import (
        _fixed_test_binary,
    )

    binary = _fixed_test_binary(None)
    metadata = json.loads((binary.parent / "metadata.json").read_text())
    assert metadata == {"head": head, "features": "test", "sha256": _sha256(binary)}
    version = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert head[:7] in version, version

    # Use the same prepared CLI as framework.helper.ckb_cli; hashes are recorded
    # rather than tied to one developer's operating system and architecture.
    cli = ROOT / "source/ckb-cli"
    assert cli.is_file() and os.access(cli, os.X_OK), cli
    suite_dir = ROOT / RELATIVE_BIN_DIR
    suite_dir.mkdir(parents=True, exist_ok=True)
    _link_once(suite_dir / "ckb", binary)
    _link_once(suite_dir / "ckb-cli", cli)
    print(f"PR5316 test binary: {version}; sha256={metadata['sha256']}")
    print(f"PR5316 ckb-cli: sha256={_sha256(cli)}")
    return RELATIVE_BIN_DIR


@pytest.fixture(scope="module", autouse=True)
def use_pr5316_binary(pr5316_binary_dir):
    config = CkbNodeConfigPath.CURRENT_TEST
    fields = (
        "ckb_config_path",
        "ckb_miner_config_path",
        "ckb_spec_path",
        "ckb_bin_path",
    )
    previous = {name: getattr(config, name) for name in fields}
    previous_env = os.environ.get("CKB_PR5316_BIN_DIR")
    # Keep the template generation of the original fixed-source regression run,
    # independently of whichever release CURRENT_TEST targets in the parent suite.
    config.ckb_config_path = "source/template/ckb/v209/ckb.toml.j2"
    config.ckb_miner_config_path = "source/template/ckb/v209/ckb-miner.toml.j2"
    config.ckb_spec_path = "source/template/ckb/v209/specs/dev.toml"
    config.ckb_bin_path = pr5316_binary_dir
    os.environ["CKB_PR5316_BIN_DIR"] = pr5316_binary_dir
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(config, name, value)
        if previous_env is None:
            os.environ.pop("CKB_PR5316_BIN_DIR", None)
        else:
            os.environ["CKB_PR5316_BIN_DIR"] = previous_env
