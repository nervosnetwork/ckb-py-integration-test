#!/usr/bin/env python3
"""Run the 17 Python network-budget regressions with explicit local inputs."""

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from suites.network_verification.runtime import prepare_target, sha256

PYTHON_CASES = [
    # Each selector invokes the existing case in place; no copied test body.
    # TEST-MAP: VT2-32
    "suites/network_verification/tests/test_budget_config.py::TestTxPoolConfigCompatibility::test_production_rejects_test_only_budget_fields",
    # TEST-MAP: VT2-33
    "suites/network_verification/tests/test_budget_config.py::TestTxPoolConfigCompatibility::test_budget_configuration_bounds",
    # TEST-MAP: VT2-26
    "test_cases/tx_pool_refactor/test_21_dao_pre_script_verification.py::TestDaoPreScriptVerification::test_lock_size_boundary_and_index",
    # TEST-MAP: VT2-54
    "test_cases/tx_pool_refactor/test_21_dao_pre_script_verification.py::TestDaoPreScriptVerification::test_dao_withdraw_requires_withdrawing_header",
    # TEST-MAP: VT2-27
    "test_cases/ckb_cli/test_ckbcli_dep_groups_enable_type_id_203.py::TestCkbCliMultisig203::test_01_dep_groups_enable_type_id_true",
    "suites/network_verification/tests/test_budget_retry.py::TestTimeBudgetRetry::test_network_budget_accepts_same_transaction_after_limit_increase",
    "suites/network_verification/tests/test_budget_consensus.py::TestBudgetConsensusIsolation::test_pool_timeout_does_not_reject_independently_mined_valid_block",
    "suites/network_verification/tests/test_peer_timeout.py::TestRemoteTimeBudgetPeer::test_remote_timeout_keeps_peer_connected_for_next_valid_transaction",
    "suites/network_verification/tests/test_source_budget.py::TestSourceBudget::test_network_refusal_does_not_block_dry_run_or_local_submission",
    "suites/network_verification/tests/test_source_budget.py::TestSourceBudget::test_persisted_slow_transaction_reloads_without_network_budget",
    "suites/network_verification/tests/test_recovery_budget.py::TestRecoveryBudget::test_detached_transaction_recovers_after_restart_under_one_ms_cap",
    "suites/network_verification/tests/test_budget_rbf.py::TestBudgetReplacement::test_network_timeout_preserves_parent_child_until_same_candidate_local_rbf",
    "suites/network_verification/tests/test_budget_rbf.py::TestBudgetReplacement::test_locally_rejected_slow_transaction_recovers_when_conflict_is_replaced",
    "suites/network_verification/tests/test_cached_context.py::TestCachedContext::test_cached_script_proof_does_not_skip_since_after_truncate",
    # TEST-MAP: VT2-53
    "test_cases/issue/test_4111.py::Test4111::test_output_capacity_exceeding_inputs_is_rejected",
    # TEST-MAP: VT2-24
    "test_cases/v209/test_5254_5262_vm_scheduler.py::TestVmVerificationPaths::test_vm_cycles_match_across_rpc_relay_and_block_verification",
    # TEST-MAP: VT2-39
    "test_cases/ckb_cli/test_sync_state_by_ckbcli.py::TestSyncProgress::test_invalid_block_then_valid_sync_preserves_progress_floor",
]


def execute(command, cwd, env, directory, label, timeout):
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / f"{label}.log"
    started = time.time()
    timed_out = False
    with log.open("wb") as stream:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
            finally:
                # The pytest parent may exit before a node that ignored SIGTERM.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            code = 124
    receipt = {
        "command": [str(arg) for arg in command],
        "cwd": str(cwd),
        "started_unix": started,
        "duration_seconds": time.time() - started,
        "exit_status": code,
        "timed_out": timed_out,
        "environment_keys": sorted(env),
        "combined_stdout_stderr": str(log),
        "output_sha256": sha256(log),
    }
    (directory / f"{label}.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(log.read_text(errors="replace")[-12000:])
    print(f"{label}: exit={code}; receipt={directory / (label+'.json')}")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--test-binary", type=Path, required=True, help="ckb built with --features test"
    )
    parser.add_argument(
        "--production-binary",
        type=Path,
        required=True,
        help="separate ckb build without test feature",
    )
    parser.add_argument("--ckb-cli", type=Path, required=True)
    parser.add_argument(
        "--testdata", type=Path, required=True, help="CKB script/testdata directory"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "report/network-verification"
    )
    parser.add_argument("--label", default="regression")
    parser.add_argument("--selector", action="append", choices=PYTHON_CASES)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not args.label or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for c in args.label
    ):
        parser.error(
            "--label may contain only letters, digits, hyphens and underscores"
        )
    args.output.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=args.label + "-", dir=args.output.resolve()))
    try:
        manifest = prepare_target(
            args.test_binary, args.production_binary, args.ckb_cli, args.testdata, run
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER", "SYSTEMROOT")
        if key in os.environ
    }
    env.update(
        NO_PROXY="*",
        no_proxy="*",
        PYTHONPATH=str(ROOT),
        CKB_TEST_TARGET_MANIFEST=str(manifest),
        CKB_TEST_NODE_LOG_DIR=str(run / "nodes"),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
    )
    selected = args.selector or PYTHON_CASES
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-p",
        "suites.network_verification.tests.target_plugin",
        "-q",
        *selected,
        "--junitxml=" + str(run / "regression.xml"),
    ]
    if args.collect_only:
        command.append("--collect-only")
    print(f"Run directory: {run}", flush=True)
    return execute(command, ROOT, env, run, "regression", args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
