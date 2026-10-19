"""Fixed fixture-input and cold-restart checks; no independent test runner."""

import hashlib
import json

import pytest

from test_cases.network_verification import runtime


@pytest.fixture
def script_input(tmp_path, monkeypatch):
    script = tmp_path / "always_success"
    script.write_bytes(b"fixed-fixture")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "script_sha256": {
                    script.name: hashlib.sha256(script.read_bytes()).hexdigest()
                }
            }
        )
    )
    monkeypatch.setattr(runtime, "BASELINE", baseline)
    monkeypatch.setenv("CKB_SCRIPT_TESTDATA", str(tmp_path))
    return script


def test_fixed_script_is_used_without_modification(script_input):
    before = script_input.read_bytes()
    assert runtime.script_path("always_success") == script_input
    assert script_input.read_bytes() == before


def test_modified_script_is_rejected(script_input):
    script_input.write_bytes(b"changed")
    with pytest.raises(AssertionError, match="always_success"):
        runtime.script_path("always_success")


def test_missing_script_is_an_error(script_input):
    script_input.unlink()
    with pytest.raises(FileNotFoundError):
        runtime.script_path("always_success")


def test_unknown_script_is_rejected(script_input):
    with pytest.raises(AssertionError, match="unknown"):
        runtime.script_path("unknown")


def test_default_script_directory_uses_project_source(script_input, monkeypatch):
    monkeypatch.delenv("CKB_SCRIPT_TESTDATA")
    monkeypatch.setattr(runtime, "ROOT", script_input.parent / "root")
    destination = runtime.ROOT / "source/ckb-script-testdata/always_success"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(script_input.read_bytes())
    assert runtime.script_path("always_success") == destination


def test_relative_script_directory_becomes_absolute(script_input, monkeypatch):
    monkeypatch.chdir(script_input.parent.parent)
    monkeypatch.setenv("CKB_SCRIPT_TESTDATA", script_input.parent.name)
    assert runtime.script_path("always_success") == script_input.resolve()


def test_restart_requires_old_process_exit_before_start(monkeypatch):
    from test_cases.network_verification import workload

    events = []

    class Node:
        def stop(self):
            events.append("stop")

        def start(self):
            events.append("start")

    monkeypatch.setattr(workload, "_node_process_id", lambda node: 42)
    monkeypatch.setattr(workload, "_process_exists", lambda pid: True)

    def immediate_check(predicate, description, timeout):
        assert predicate(), description

    monkeypatch.setattr(workload, "wait_for", immediate_check)
    with pytest.raises(AssertionError, match="old node PID 42 exited"):
        workload.restart(Node())
    assert events == ["stop"]


def test_restart_requires_a_different_listening_pid(monkeypatch):
    from test_cases.network_verification import workload
    from types import SimpleNamespace

    pids = iter([42, None, 42])
    monkeypatch.setattr(workload, "_node_process_id", lambda node: next(pids))
    monkeypatch.setattr(workload, "_process_exists", lambda pid: False)

    def immediate_check(predicate, description, timeout):
        result = predicate()
        assert result, description
        return result

    monkeypatch.setattr(workload, "wait_for", immediate_check)
    node = SimpleNamespace(stop=lambda: None, start=lambda: None)
    with pytest.raises(AssertionError):
        workload.restart(node)
