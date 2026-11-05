"""Unit checks for the external CPU counter used by the long-VM regression."""

import ctypes
import errno
import os
import sys
import time
from types import SimpleNamespace

import pytest

from test_cases.network_verification import cpu_usage


def test_linux_counter_handles_spaces_and_parentheses_in_process_name(monkeypatch):
    fields = ["R"] + ["0"] * 10 + ["120", "30"] + ["0"] * 30
    stat = "42 (ckb (worker) process) " + " ".join(fields)
    monkeypatch.setattr(cpu_usage.sys, "platform", "linux")
    monkeypatch.setattr(cpu_usage.Path, "read_text", lambda self: stat)
    monkeypatch.setattr(cpu_usage.os, "sysconf", lambda name: 100)
    assert cpu_usage.process_cpu_seconds(42) == 1.5


def test_darwin_counter_converts_mach_ticks(monkeypatch):
    def read_usage(pid, flavor, pointer):
        assert (pid, flavor) == (42, 0)
        pointer._obj.user = 24_000_000
        pointer._obj.system = 12_000_000
        return 0

    monkeypatch.setattr(cpu_usage.sys, "platform", "darwin")
    monkeypatch.setattr(
        cpu_usage,
        "_darwin_clock",
        lambda: (SimpleNamespace(proc_pid_rusage=read_usage), 125 / 3 / 1e9),
    )
    assert cpu_usage.process_cpu_seconds(42) == pytest.approx(1.5)


def test_darwin_counter_failure_is_not_reported_as_zero_cpu(monkeypatch):
    def missing_process(*args):
        ctypes.set_errno(errno.ESRCH)
        return -1

    monkeypatch.setattr(cpu_usage.sys, "platform", "darwin")
    monkeypatch.setattr(
        cpu_usage,
        "_darwin_clock",
        lambda: (SimpleNamespace(proc_pid_rusage=missing_process), 1e-9),
    )
    with pytest.raises(ProcessLookupError):
        cpu_usage.process_cpu_seconds(42)


def test_native_counter_agrees_with_python_process_clock():
    if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        pytest.skip("CPU counter supports Linux and macOS")
    before = cpu_usage.process_cpu_seconds(os.getpid())
    started = time.process_time()
    while time.process_time() - started < 0.1:
        pass
    expected = time.process_time() - started
    measured = cpu_usage.process_cpu_seconds(os.getpid()) - before
    assert measured == pytest.approx(expected, abs=0.03)
