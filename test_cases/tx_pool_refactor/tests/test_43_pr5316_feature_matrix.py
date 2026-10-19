"""PR #5316: compile the production feature matrix at the pinned source HEAD.

This is a Python integration check of Cargo's executable build, not a Rust
unit test. It uses an isolated target directory and offline locked inputs.
"""

import os
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXPECTED_HEAD = "60b8e349470bdffe57e9e92c3e0961b91c326b3a"


def _source():
    source = Path(
        os.environ.get(
            "CKB_PR5316_SOURCE_DIR",
            ROOT / ".ai-test-agent/current/ckb-pr-5316-current/product",
        )
    ).resolve()
    assert (source / "Cargo.toml").is_file(), source
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert head == EXPECTED_HEAD, (head, EXPECTED_HEAD)
    return source


# TEST-MAP: NTD-72
def test_pinned_executable_feature_build_matrix():
    source = _source()
    log_dir = os.environ.get("CKB_PR5316_MATRIX_LOG_DIR")
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
    cases = (
        ("default", (), None, True),
        ("profiling", ("profiling",), None, True),
        ("trace_without_unstable", ("tokio-trace",), None, False),
        ("trace", ("tokio-trace",), "--cfg tokio_unstable", True),
        ("profile_trace", ("profiling", "tokio-trace"), "--cfg tokio_unstable", True),
        ("integration_test", ("test",), None, True),
    )
    with tempfile.TemporaryDirectory(prefix="pr5316-cargo-matrix-") as target:
        for name, features, rustflags, should_compile in cases:
            operation = "build" if should_compile else "check"
            command = [
                "cargo",
                operation,
                "--locked",
                "--offline",
                "--package",
                "ckb",
                "--bin",
                "ckb",
                "--message-format",
                "short",
            ]
            if features:
                command += ["--features", ",".join(features)]
            env = os.environ.copy()
            env.pop("RUSTFLAGS", None)
            if rustflags is not None:
                env["RUSTFLAGS"] = rustflags
            env.update(
                CARGO_TARGET_DIR=target,
                CARGO_INCREMENTAL="0",
                CARGO_PROFILE_DEV_DEBUG="0",
                CARGO_BUILD_JOBS="4",
                CARGO_NET_OFFLINE="true",
            )
            result = subprocess.run(
                command,
                cwd=source,
                env=env,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            output = result.stdout + result.stderr
            if log_dir:
                Path(log_dir, f"{name}.log").write_text(output)
            print(f"{name}: exit={result.returncode}; command={' '.join(command)}")
            if should_compile:
                assert result.returncode == 0, f"{name}: {output[-4000:]}"
                if operation == "build":
                    binary = Path(target) / "debug/ckb"
                    assert binary.is_file() and os.access(binary, os.X_OK), binary
                    version = subprocess.run(
                        [str(binary), "--version"],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=10,
                    ).stdout.strip()
                    assert EXPECTED_HEAD[:7] in version, version
                    print(
                        f"{name}: executable={binary}; bytes={binary.stat().st_size}; "
                        f"version={version}"
                    )
            else:
                assert result.returncode != 0, f"{name} unexpectedly compiled"
                assert (
                    "the `tokio-trace` feature requires "
                    '`RUSTFLAGS="--cfg tokio_unstable"`'
                ) in output, output[-4000:]
