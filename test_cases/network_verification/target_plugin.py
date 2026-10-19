"""Session-only binary/CLI selection; no downloaded files are overwritten."""

import json
from pathlib import Path
import os
import shlex

import pytest
from framework import config as project_config
from framework.helper import ckb_cli
from framework.test_node import CkbNodeConfigPath, DOCKER
from test_cases.network_verification.runtime import ROOT, binary_dir, target


@pytest.fixture(scope="session", autouse=True)
def selected_node_binary():
    assert not DOCKER, "The budget regression uses local node processes"
    patch = pytest.MonkeyPatch()
    patch.setattr(CkbNodeConfigPath.CURRENT_TEST, "ckb_bin_path", binary_dir())
    patch.setattr(ckb_cli, "cli_path", shlex.quote(target()["cli"]["path"]))
    # Existing cases have fixed directory names; separate each wrapper invocation.
    patch.setattr(
        project_config,
        "TMP_PATH",
        os.path.relpath(
            Path(os.environ["CKB_TEST_TARGET_MANIFEST"]).parent / "tmp", ROOT
        ),
    )
    try:
        yield
    finally:
        patch.undo()


def pytest_sessionfinish(session, exitstatus):
    """Record actual selectors; collection/skips never count as passing cases."""
    path = Path(os.environ["CKB_TEST_TARGET_MANIFEST"]).parent / "collected.json"
    path.write_text(
        json.dumps([item.nodeid for item in session.items], indent=2) + "\n"
    )
