# Network verification — Python regression

These 17 pytest methods cover 19 mapped integration test points using ordinary
CKB nodes, RPC and node-to-node relay. Existing DAO, Type ID, capacity, VM and
sync methods are marked in place rather than copied or listed in a custom runner.

## Existing project configuration

Use the same setup as other `test_cases` groups:

- `pip install -r requirements.txt`, Python 3.10–3.12, Linux/macOS and `lsof`.
- `CkbNodeConfigPath.CURRENT_TEST` selects `download/current/ckb` and
  `download/current/ckb-cli`; CLI helpers use `source/ckb-cli`. Prepare these
  paths with compatible executables, as for the rest of the project.
- For this optional regression only, the CKB in `download/current` must be a
  **test-feature build** supporting `max_tx_verify_time_ms`. The default version
  in `download.py` and `framework/test_node.py` is unchanged; updating it belongs
  in a later PR. Do not replace a release binary in a shared checkout while
  another test run is using it; use a separate checkout for this feature build.
- Put a production build without the test feature from the **same revision** in
  `download/production/ckb`, plus a compatible `download/production/ckb-cli`.
  `CKB_PRODUCTION_BIN_DIR` optionally overrides this project-relative directory.
  Build provenance is supplied by the caller; tests check actual config behavior,
  not whether two executables came from the same source revision.
- Put the matching CKB `script/testdata` contents in `source/ckb-script-testdata`,
  or set `CKB_SCRIPT_TESTDATA` to that directory. Each requested ELF is checked
  against the fixed hashes in `fixtures/vm-cycle-baseline.json`. Expected cycles
  are never regenerated from the node under test.
- Preserve the legacy framework's checkout name `ckb-py-integration-test` and
  shell-safe paths (ASCII letters/digits, `/`, `_`, `-`, `.`). Run serially,
  without Docker or xdist; existing CLI helpers use shared temporary key paths.
  There is no new path-staging or binary-selection layer.

## Native pytest entry point

From the repository root, enable the test-feature cases and use ordinary pytest:

```sh
export CKB_NETWORK_VERIFICATION=1
python3 -m pytest -m network_verification \
  test_cases/network_verification \
  test_cases/tx_pool_refactor/test_21_dao_pre_script_verification.py \
  test_cases/ckb_cli/test_ckbcli_dep_groups_enable_type_id_203.py \
  test_cases/issue/test_4111.py \
  test_cases/v209/test_5254_5262_vm_scheduler.py \
  test_cases/ckb_cli/test_sync_state_by_ckbcli.py
```

This selects exactly the existing 17 methods, not the whole repository suite.
Use native `--collect-only`, `-k`, or `path.py::Class::test_method` for inspection
or a subset. `pytest.ini` keeps the existing HTML report settings; optionally
pass `--junitxml=report/network-verification.xml` for CI. No `run_selected.py`,
manifest, custom pytest plugin, copied binary directory, or selector registry is
needed. `runtime.py` only holds the opt-in guard and fixture/path helpers.

`test_cases/network_verification` is also included in the existing Makefile
case-group loop. Without `CKB_NETWORK_VERIFICATION=1`, its 11 methods plus the
two new VM/sync methods skip, leaving the ordinary release-binary run unchanged.
The four reused DAO/Type ID/capacity methods continue to run normally. Enabling
the flag with missing/incompatible inputs results in ordinary pytest failures;
it is not a missing-input skip. The test-feature flag does not change assertions.

## What is asserted

- Hash-specific real-node CPU-budget refusal, fresh transaction request barriers,
  absent pool/rejected state, connected peers, no bans and subsequent success.
- Local/dry-run admission, persistence, cold reorg recovery and re-mining;
  RBF parent/child preservation and rejected-transaction subscription controls.
- Production/test configuration boundaries; DAO, Type ID, since and capacity
  validation; seven fixed VM/syscall cycle combinations and cold block verification;
  valid sync progress after a genuinely invalid block.
- Mining retains uncle headers **and proposals**. Dropping template uncles can
  strand a recovered transaction in pending by omitting its proposal from the chain.

See [the unchanged case contracts](../../reviews/network-verification-budget.md).
VT2-03/04/05/30/55/57 (controlled proposal/orphan/malformed-relay packets) remain
unimplemented. Ordinary relay or RPC notification is not credited as those paths.
Mappings, semantic review and actual execution results are separate evidence.
Single-platform success is not a cross-platform guarantee. Persistence, reorg and budget-change restarts verify the owning directory and
wait for the old PID to exit, then require a different listening PID. Other
existing node helpers use startup sleeps and port-based shutdown; ordinary RPCs
have no per-call timeout,
so use the existing CI/job timeout when a hard overall deadline is required.
There is no suite-owned subprocess, watchdog, environment reset or report format.

## Test readability and lifecycle

Each stateful scenario owns fresh nodes in its class's `setup_class` and stops
and cleans them in `teardown_class`, following the existing project style.
Node roles, `max_tx_verify_time_ms` (milliseconds), cycle limits, startup, and
the common 30-block chain are written directly in each class. Independent
persistence and local-RBF scenarios use separate classes so their pools and
chains cannot leak into one another. Test methods keep the original transaction
steps and assertions; `workload` retains only shared transaction/assertion,
fixed-genesis-data, log-copy and verified-restart utilities, not a node-lifecycle
context manager.
