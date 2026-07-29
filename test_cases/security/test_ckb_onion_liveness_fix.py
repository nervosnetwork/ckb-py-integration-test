import os
import subprocess
from pathlib import Path

import pytest

from framework.util import get_project_root


FIX_BRANCH_TESTS = [
    "wait_for_disconnect_drains_lines_until_close",
    "wait_for_disconnect_detects_bare_close",
]

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CKB_ONION_LIVENESS_FIX_REGRESSION") != "1",
    reason=(
        "Requires a CKB checkout with PR #5302 applied. The V209 integration-test "
        "binary does not include this fix yet."
    ),
)


def _candidate_ckb_repos():
    env_path = os.getenv("CKB_REPO_PATH")
    if env_path:
        yield Path(env_path).expanduser()

    project_root = Path(get_project_root())
    yield project_root / "ckb"
    yield project_root.parent / "ckb"
    yield Path("/Users/xue/nervosnetwork/ckb")


def _find_ckb_repo():
    for repo in _candidate_ckb_repos():
        if (repo / "Cargo.toml").is_file() and (
            repo / "util/onion/src/tor_connection.rs"
        ).is_file():
            return repo
    pytest.skip(
        "CKB source checkout not found. Set CKB_REPO_PATH to a checkout of "
        "nervosnetwork/ckb with PR #5302 applied."
    )


def _run(cmd, cwd):
    return subprocess.run(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def _assert_deployed_binary_matches_repo(repo):
    project_root = Path(get_project_root())
    binary = Path(os.getenv("CKB_BINARY_PATH", project_root / "download/current/ckb"))
    if not binary.is_file():
        pytest.skip(f"CKB binary not found: {binary}")

    head = _run(["git", "rev-parse", "--short=7", "HEAD"], repo)
    assert head.returncode == 0, head.stdout

    version = _run([str(binary), "--version"], project_root)
    assert version.returncode == 0, version.stdout
    assert head.stdout.strip() in version.stdout, (
        f"{binary} was not built from {repo} HEAD {head.stdout.strip()}:\n"
        f"{version.stdout}"
    )


def test_onion_liveness_wait_drains_bounded_control_lines():
    """
    Verifies PR #5302 for the Tor control-line buffering issue.

    The fix should bound the completed-line channel and keep draining
    unsolicited lines while waiting for disconnect, so a controller that sends
    more lines than the channel capacity cannot block the reader before EOF.
    """
    repo = _find_ckb_repo()
    _assert_deployed_binary_matches_repo(repo)

    tor_connection = repo / "util/onion/src/tor_connection.rs"
    source = tor_connection.read_text(encoding="utf-8")

    required_snippets = [
        "const CONTROL_LINE_CHANNEL_CAPACITY",
        "mpsc::channel(CONTROL_LINE_CHANNEL_CAPACITY)",
        "pub async fn wait_for_disconnect(mut self) -> bool",
        "Some(_) = self.line_rx.recv()",
        "Discard the unsolicited control line",
        "0..=CONTROL_LINE_CHANNEL_CAPACITY",
    ]
    for snippet in required_snippets:
        assert snippet in source, f"missing PR #5302 fix snippet: {snippet}"

    for test_name in FIX_BRANCH_TESTS:
        assert test_name in source, f"missing PR #5302 regression test: {test_name}"

    try:
        result = subprocess.run(
            [
                "cargo",
                "test",
                "-p",
                "ckb-onion",
                "wait_for_disconnect",
                "--",
                "--nocapture",
                "--test-threads=1",
            ],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        pytest.fail(f"ckb-onion wait_for_disconnect tests timed out:\n{output}")

    assert result.returncode == 0, result.stdout
    for test_name in FIX_BRANCH_TESTS:
        assert test_name in result.stdout, result.stdout
