"""Runner input checks only: these tests start no CKB processes."""

import json
import os
from pathlib import Path

import pytest
from suites.network_verification import runtime
from suites.network_verification.tests.run_selected import PYTHON_CASES, execute


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    paths = []
    for name in ("test", "production", "cli"):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n# " + name + "\nexit 0\n")
        path.chmod(0o755)
        paths.append(path)
    testdata = tmp_path / "testdata"
    testdata.mkdir()
    script = testdata / "always_success"
    script.write_bytes(b"fixed-fixture")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"script_sha256": {script.name: runtime.sha256(script)}})
    )
    monkeypatch.setattr(runtime, "BASELINE", baseline)
    runtime.target.cache_clear()
    yield (*paths, testdata, tmp_path / "run")
    runtime.target.cache_clear()


def test_explicit_inputs_are_snapshotted_without_modifying_originals(
    inputs, monkeypatch
):
    before = {str(p): runtime.sha256(p) for p in inputs[:3]}
    manifest = runtime.prepare_target(*inputs)
    data = json.loads(manifest.read_text())
    monkeypatch.setenv("CKB_TEST_TARGET_MANIFEST", str(manifest))
    assert runtime.target() == data
    for kind, original in zip(("test", "production"), inputs):
        staged = runtime.ROOT / runtime.binary_dir(kind)
        assert (staged / "ckb").resolve() == original
        assert (staged / "ckb-cli").resolve() == inputs[2]
    assert runtime.script_path("always_success") == inputs[3] / "always_success"
    assert before == {str(p): runtime.sha256(p) for p in inputs[:3]}


def test_same_binary_for_both_feature_modes_is_rejected(inputs):
    with pytest.raises(ValueError, match="distinct"):
        runtime.prepare_target(inputs[0], inputs[0], *inputs[2:])
    assert not inputs[-1].exists()


@pytest.mark.parametrize("change", ["missing", "modified"])
def test_fixture_mismatch_fails_before_staging(inputs, change):
    fixture = inputs[3] / "always_success"
    if change == "missing":
        fixture.unlink()
    else:
        fixture.write_bytes(b"different")
    with pytest.raises(ValueError, match="Fixture missing or changed"):
        runtime.prepare_target(*inputs)
    assert not inputs[-1].exists()


def test_executable_permission_is_required(inputs):
    inputs[0].chmod(0o644)
    with pytest.raises(ValueError, match="executable"):
        runtime.prepare_target(*inputs)


def test_modified_binary_is_detected_when_loading_target(inputs, monkeypatch):
    manifest = runtime.prepare_target(*inputs)
    monkeypatch.setenv("CKB_TEST_TARGET_MANIFEST", str(manifest))
    inputs[0].write_bytes(b"changed")
    with pytest.raises(AssertionError):
        runtime.target()


def test_missing_binary_is_an_error(inputs):
    inputs[0].unlink()
    with pytest.raises(FileNotFoundError):
        runtime.prepare_target(*inputs)


def test_selection_is_exactly_seventeen_unique_existing_methods():
    import ast

    assert len(PYTHON_CASES) == len(set(PYTHON_CASES)) == 17
    for selector in PYTHON_CASES:
        file, cls, method = selector.split("::")
        tree = ast.parse((runtime.ROOT / file).read_text())
        klass = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == cls
        )
        assert any(
            isinstance(node, ast.FunctionDef) and node.name == method
            for node in klass.body
        )


def test_runner_records_nonzero_status_and_literal_output(tmp_path):
    import sys

    status = execute(
        [sys.executable, "-c", "print('intentional-failure'); raise SystemExit(7)"],
        tmp_path,
        {},
        tmp_path,
        "failure",
        10,
    )
    receipt = json.loads((tmp_path / "failure.json").read_text())
    assert status == receipt["exit_status"] == 7
    assert receipt["timed_out"] is False
    assert (tmp_path / "failure.log").read_text() == "intentional-failure\n"
    assert receipt["output_sha256"] == runtime.sha256(tmp_path / "failure.log")


def test_runner_watchdog_returns_timeout_not_a_pass(tmp_path):
    import sys

    status = execute(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        {},
        tmp_path,
        "timeout",
        0.1,
    )
    receipt = json.loads((tmp_path / "timeout.json").read_text())
    assert status == receipt["exit_status"] == 124
    assert receipt["timed_out"] is True


@pytest.mark.parametrize("name", ["run with spaces", "run;echo", "run$(echo)"])
def test_legacy_shell_unsafe_output_path_fails_preflight(inputs, name):
    output = inputs[-1].with_name(name)
    with pytest.raises(ValueError, match="shell-safe"):
        runtime.prepare_target(*inputs[:-1], output)
    assert not output.exists()


def test_restart_requires_old_process_exit_before_start(monkeypatch):
    from suites.network_verification import workload as w

    events = []

    class Node:
        def stop(self):
            events.append("stop")

        def start(self):
            events.append("start")

    monkeypatch.setattr(w, "_node_process_id", lambda node: 42)
    monkeypatch.setattr(w, "_process_exists", lambda pid: True)

    def immediate_check(predicate, description, timeout):
        assert predicate(), description

    monkeypatch.setattr(w, "wait_for", immediate_check)
    with pytest.raises(AssertionError, match="old node PID 42 exited"):
        w.restart(Node())
    assert events == ["stop"]


def test_restart_requires_a_different_listening_pid(monkeypatch):
    from suites.network_verification import workload as w
    from types import SimpleNamespace

    pids = iter([42, None, 42])
    monkeypatch.setattr(w, "_node_process_id", lambda node: next(pids))
    monkeypatch.setattr(w, "_process_exists", lambda pid: False)

    def immediate_check(predicate, description, timeout):
        result = predicate()
        assert result, description
        return result

    monkeypatch.setattr(w, "wait_for", immediate_check)
    node = SimpleNamespace(stop=lambda: None, start=lambda: None)
    with pytest.raises(AssertionError):
        w.restart(node)


def test_alternate_checkout_name_fails_preflight(inputs, monkeypatch):
    monkeypatch.setattr(runtime, "ROOT", inputs[-1].parent / "renamed-checkout")
    with pytest.raises(ValueError, match="checkout directory name"):
        runtime.prepare_target(*inputs)
    assert not inputs[-1].exists()
