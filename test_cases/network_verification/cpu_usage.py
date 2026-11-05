"""Read cumulative process CPU time, not a sampled or decaying CPU percentage."""

import ctypes
import os
import sys
from functools import lru_cache
from pathlib import Path


class _DarwinUsage(ctypes.Structure):
    # macOS SDK sys/resource.h: rusage_info_v0.
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64)
        for name in (
            "user",
            "system",
            "idle_wakeups",
            "interrupt_wakeups",
            "pageins",
            "wired",
            "resident",
            "footprint",
            "start",
            "exit",
        )
    ]


@lru_cache(maxsize=1)
def _darwin_clock():
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pid_rusage.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    library.proc_pid_rusage.restype = ctypes.c_int
    timebase = (ctypes.c_uint32 * 2)()
    system = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    assert system.mach_timebase_info(ctypes.byref(timebase)) == 0
    assert timebase[1] != 0
    # Mach ticks are not nanoseconds on Apple Silicon (e.g. numer/denom=125/3).
    return library, timebase[0] / timebase[1] / 1_000_000_000


def process_cpu_seconds(pid):
    """Sum user/system CPU for a live node; failed reads must fail the test."""
    if sys.platform.startswith("linux"):
        # comm can contain spaces and ')'; fields after its last ')' start at state.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    if sys.platform == "darwin":
        library, seconds_per_tick = _darwin_clock()
        usage = _DarwinUsage()
        if library.proc_pid_rusage(pid, 0, ctypes.byref(usage)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(pid))
        return (usage.user + usage.system) * seconds_per_tick
    raise NotImplementedError(f"process CPU measurement on {sys.platform}")
