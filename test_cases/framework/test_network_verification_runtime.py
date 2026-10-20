"""Fixed fixture-input and cold-restart checks; no independent test runner."""

import hashlib
import json

import pytest

from test_cases.network_verification import runtime


@pytest.fixture(params=("always_success", "current_cycles"))
def script_input(request, tmp_path, monkeypatch):
    script = tmp_path / "source/contract" / request.param
    script.parent.mkdir(parents=True)
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
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime, "BASELINE", baseline)
    return script


def test_fixed_script_is_used_without_modification(script_input):
    before = script_input.read_bytes()
    assert runtime.script_path(script_input.name) == script_input
    assert script_input.read_bytes() == before


def test_modified_script_is_rejected(script_input):
    script_input.write_bytes(b"changed")
    with pytest.raises(AssertionError, match=script_input.name):
        runtime.script_path(script_input.name)


def test_missing_script_is_an_error(script_input):
    script_input.unlink()
    with pytest.raises(FileNotFoundError):
        runtime.script_path(script_input.name)


def test_unknown_script_is_rejected(script_input):
    with pytest.raises(AssertionError, match="unknown"):
        runtime.script_path("unknown")


def test_script_directory_uses_project_contract(script_input):
    assert runtime.script_path(script_input.name) == (
        runtime.ROOT / "source/contract" / script_input.name
    )


def test_script_path_is_independent_of_working_directory(script_input, monkeypatch):
    monkeypatch.chdir(script_input.parent)
    assert runtime.script_path(script_input.name) == script_input


def test_all_fixed_scripts_are_available_in_project_contracts():
    for name in json.loads(runtime.BASELINE.read_text())["script_sha256"]:
        assert runtime.script_path(name) == runtime.ROOT / "source/contract" / name


def test_setup_failure_prints_node_log_before_cleanup(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from test_cases.network_verification import workload
    from test_cases.network_verification.test_budget_rbf import (
        TestLocalConflictRecovery,
    )

    log_path = tmp_path / "node.log"
    log_path.write_text("Listen failed: Address already in use\n")
    node = SimpleNamespace(
        ckb_dir=str(tmp_path),
        prepare=Mock(side_effect=RuntimeError("startup failed")),
        clean=Mock(side_effect=log_path.unlink),
    )
    monkeypatch.setattr(
        TestLocalConflictRecovery,
        "CkbNode",
        SimpleNamespace(init_dev_by_port=Mock(return_value=node)),
    )
    monkeypatch.setattr(workload, "unused_ports", lambda count: [8101, 8102, 8103])

    with pytest.raises(RuntimeError, match="startup failed"):
        TestLocalConflictRecovery.setup_class()

    output = capsys.readouterr().out
    assert str(log_path) in output
    assert "Listen failed: Address already in use" in output
    node.clean.assert_called_once_with()
    assert not log_path.exists()


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
        workload.restart_and_assert_new_process(Node())
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
        workload.restart_and_assert_new_process(node)


def test_tcp_reader_retains_coalesced_messages():
    import socket
    from test_cases.network_verification.test_budget_rbf import _read_json_line

    reader, writer = socket.socketpair()
    try:
        writer.sendall(b'{"first": 1}\n{"second": 2}\n')
        first, buffer = _read_json_line(reader, b"", 1)
        assert first == {"first": 1}
        second, buffer = _read_json_line(reader, buffer, 1)
        assert second == {"second": 2}
        assert buffer == b""
    finally:
        reader.close()
        writer.close()


def test_tcp_reader_preserves_partial_message_on_timeout():
    import socket
    from test_cases.network_verification.test_budget_rbf import _read_json_line

    reader, writer = socket.socketpair()
    try:
        writer.sendall(b'{"value":')
        value, buffer = _read_json_line(reader, b"", 0.01)
        assert value is None
        assert buffer == b'{"value":'
        writer.sendall(b" 3}\n")
        value, buffer = _read_json_line(reader, buffer, 1)
        assert value == {"value": 3}
        assert buffer == b""
    finally:
        reader.close()
        writer.close()


def test_tcp_reader_rejects_closed_stream():
    import socket
    from test_cases.network_verification.test_budget_rbf import _read_json_line

    reader, writer = socket.socketpair()
    writer.close()
    try:
        with pytest.raises(AssertionError, match="rejected_transaction stream closed"):
            _read_json_line(reader, b"", 1)
    finally:
        reader.close()
