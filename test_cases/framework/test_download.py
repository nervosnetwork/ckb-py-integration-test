import importlib
import sys

import pytest
import requests


def test_import_does_not_start_download(monkeypatch):
    def unexpected_download(*args, **kwargs):
        raise AssertionError("download started during import")

    monkeypatch.setattr(requests, "get", unexpected_download)
    sys.modules.pop("download", None)

    importlib.import_module("download")


def test_sync_current_version_replaces_stale_directory(tmp_path):
    download = importlib.import_module("download")
    source = tmp_path / "0.208.0"
    current = tmp_path / "current"
    (source / "docs").mkdir(parents=True)
    (source / "ckb").write_text("new ckb")
    (source / "ckb-cli").write_text("new cli")
    (source / "docs" / "rpc.md").write_text("new docs")
    current.mkdir()
    (current / "stale").write_text("old")

    download.sync_current_version(str(source), str(current))

    assert (current / "ckb").read_text() == "new ckb"
    assert (current / "ckb-cli").read_text() == "new cli"
    assert (current / "docs" / "rpc.md").read_text() == "new docs"
    assert not (current / "stale").exists()


def test_main_downloads_all_versions_then_syncs_latest(monkeypatch):
    download = importlib.import_module("download")
    events = []
    monkeypatch.setattr(download, "versions", ["0.206.0", "0.208.0-rc0"])
    monkeypatch.setattr(download, "DOWNLOAD_DIR", "test-download")
    monkeypatch.setattr(
        download,
        "download_ckb",
        lambda version: events.append(("download", version)),
    )
    monkeypatch.setattr(
        download,
        "sync_current_version",
        lambda source, current: events.append(("sync", source, current)),
    )

    download.main()

    assert events == [
        ("download", "0.206.0"),
        ("download", "0.208.0-rc0"),
        ("sync", "test-download/0.208.0", "test-download/current"),
    ]


def test_main_rejects_empty_versions(monkeypatch):
    download = importlib.import_module("download")
    monkeypatch.setattr(download, "versions", [])

    with pytest.raises(ValueError, match="versions must not be empty"):
        download.main()


def test_current_node_configs_use_stable_download_path():
    from framework.test_node import CkbNodeConfigPath

    current_configs = [
        CkbNodeConfigPath.CURRENT_TEST,
        CkbNodeConfigPath.TESTNET,
        CkbNodeConfigPath.CURRENT_MAIN,
        CkbNodeConfigPath.PREVIEW_DUMMY,
    ]

    assert {config.ckb_bin_path for config in current_configs} == {"download/current"}
    assert CkbNodeConfigPath.v206.ckb_bin_path == "download/0.206.0"
