"""Diagnostic samples, with no unfrozen performance pass/fail thresholds."""

import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

from test_cases.network_verification import workload
from test_cases.network_verification.cpu_usage import process_cpu_seconds
from framework.util import get_project_root


def resources(node):
    pid = workload._node_process_id(node)
    assert pid is not None
    rss = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.strip()
    descriptors = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-Ff"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.splitlines()
    fd_count = sum(line[1:].isdigit() for line in descriptors if line.startswith("f"))
    if platform.system() == "Linux":
        threads = len(list(Path(f"/proc/{pid}/task").iterdir()))
    else:
        result = subprocess.run(
            ["ps", "-M", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        threads = max(0, len(result.stdout.splitlines()) - 1)
    return {
        "at": time.monotonic(),
        "pid": pid,
        "cpu_seconds": process_cpu_seconds(pid),
        "rss_kib": int(rss),
        "fd_count": fd_count,
        "threads": threads,
        "pool": node.getClient().tx_pool_info(),
    }


def save_observations(node, name, observations, *, settings=None):
    result = {
        "conclusion": "diagnostic_only_no_frozen_regression_thresholds",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "binary_sha256": hashlib.sha256(
            Path(node.ckb_bin_path).read_bytes()
        ).hexdigest(),
        "config": toml_without_secrets(node.ckb_toml_path),
        "spec_sha256": hashlib.sha256(
            Path(node.ckb_specs_config_path).read_bytes()
        ).hexdigest(),
        "settings": settings or {},
        "observations": observations,
    }
    # Native pytest stdout survives node cleanup; the JSON is also convenient while running.
    directory = (
        Path(get_project_root())
        / "report"
        / "pool_performance"
        / Path(node.ckb_dir).name
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{time.time_ns()}.json"
    path.write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "diagnostic_record": str(path),
                "samples": len(observations),
                "conclusion": result["conclusion"],
            }
        )
    )
    return path


def toml_without_secrets(path):
    import toml

    config = toml.loads(Path(path).read_text())
    return {key: config.get(key) for key in ("tx_pool", "indexer_v2", "rpc")}
