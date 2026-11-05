# Tx-pool integration regressions for CKB PR #5316

The node scenarios use native pytest/CkbTest and the project’s existing
`CkbNodeConfigPath.CURRENT_TEST`. Select the prepared CKB/ckb-cli paths in that
configuration before running. There is no suite-wide opt-in, binary substitution,
or product-version skip. The network budget cases use ordinary binaries and the
default 8-second CPU cap; they do not require a custom test-feature build.

Run from a checkout named `ckb-py-integration-test`, with Python 3.10–3.12,
the project dependencies, and `lsof`. Run node scenarios serially. The bundled
contract inputs are checked against fixed hashes. See the
[network verification guide](../../network_verification/README.md) and
[integration design](../../../reviews/ckb-pr-5316-non-test.md).

The expanded functional, resilience, and performance subset can be run with:

```sh
python -m pytest \
  test_cases/tx_pool_refactor/test_transactional_admission.py \
  test_cases/tx_pool_refactor/test_pool_rpc_progress.py \
  test_cases/tx_pool_refactor/test_indexer_pool_projection.py \
  test_cases/tx_pool_refactor/test_packing_context.py \
  test_cases/tx_pool_refactor/test_process_resilience.py \
  test_cases/tx_pool_refactor/test_peer_resilience.py \
  test_cases/tx_pool_refactor/test_pool_performance.py \
  test_cases/tx_pool_refactor/tests/test_29_pr5316_persistence_replay.py \
  test_cases/tx_pool_refactor/tests/test_36_pr5316_persistence_failures.py \
  test_cases/tx_pool_refactor/tests/test_50_pr5316_capacity_metrics.py
```

Performance probes use `CURRENT_MAIN` only for the baseline binary; both sides
use the candidate’s dev-chain specification. Configure distinct prepared binaries
for a real comparison. JSON observations are written under `report/pool_performance`.
These short diagnostic samples have no frozen regression thresholds. RBF admission
does not guarantee that the replacement confirms: template refresh is observed for
10 seconds, and either conflict may win. Canonical input exclusivity, losing-family
cleanup, and confirmation of a new independent transaction remain hard assertions.

The files `test_45`, `46`, `47`, `51`, `52`, and `53` retain old import locations as
non-collecting documentation pointers; the actual budget tests live in
`test_cases/network_verification`.

## Separate source and toolchain probes

The existing `test_41_pr5316_diagnostics_startup.py`,
`test_43_pr5316_feature_matrix.py`, and the source-CI check in
`test_44_pr5316_config_feature_runtime.py` retain their own pinned-source contract
(`60b8e349470bdffe57e9e92c3e0961b91c326b3a`). They read `CKB_PR5316_SOURCE_DIR`
or the documented local `.ai-test-agent` source checkout. The diagnostic and
feature-matrix probes build isolated executables with cached, locked Cargo inputs.
Select these explicitly after preparing that source/toolchain; they are not part
of the node-only command above or its execution claims. Recursive pytest over the
whole tx-pool directory, and `make test`, still collect these source probes; only
the explicit file selection above is node-only.

`test_48_pr5316_visible_origin.py` also requires a RISC-V-capable Clang and LLD
for its syscall-probe contract; `CKB_PR5316_RISCV_CLANG` selects the compiler.
This builds a test contract, not a CPU-budget variant of CKB.

## Recorded limits

The latest executed results for the 41-case subset combine focused runs:
38 pass and 3 fail; this is not a single full-suite run. The remaining failures
are the two persistence error-log assertions and the arrival-rate matrix interrupted
by node exit signals of unidentified origin. The revised graph case passed all
12 internal scenarios: six old-binary scenarios confirmed the original transaction,
and six candidate scenarios confirmed the replacement. These are different
outcomes, not a matched performance ranking. The full source/toolchain matrix has
not been rerun for this submission. Active failures remain assertions.
