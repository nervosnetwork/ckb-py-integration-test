# PR #5316 fixed-source Python regressions

These tests implement the mapped Python portion of
[`reviews/ckb-pr-5316-non-test.md`](../../../reviews/ckb-pr-5316-non-test.md):
49 of 96 review IDs have a `TEST-MAP` somewhere in this directory. A mapping is
not a claim that every branch of the row is covered or that the test passes.

The suite targets CKB commit `60b8e349470bdffe57e9e92c3e0961b91c326b3a`, not the
moving release selected by `CkbNodeConfigPath.CURRENT_TEST`. It uses the existing
v209 templates, the repository's node/RPC/miner helpers, and real local nodes.
It includes query/overlay, waiting/chain conflicts, packing, reorg/recovery,
persistence, notification, configuration/build-feature and VM-budget checks.
No product-source changes, binaries, generated HTML reports or local caches are
part of this submission.

## Running

Prerequisites:

- Prepare the normal Python project dependencies and `source/ckb-cli` as described
  in the repository README. Use Python 3.12 (the shared framework imports
  `telnetlib`). The CLI used by the node fixture is the same CLI used by shared
  transaction helpers; its SHA-256 is printed in the run log.
- A local, non-root process with localhost socket access; Docker is not supported
  by this fixed-source fixture. Run the suite serially because some cases use
  fixed local ports and the existing shared node helpers.
- The pinned CKB source checkout, its submodules, native build prerequisites and
  Rust toolchain. The checkout must be clean. Build helpers use locked, offline
  Cargo inputs, so fetch dependencies before testing.
- `test_48_pr5316_visible_origin.py` also needs a Clang build with the
  `riscv64-unknown-elf` target and the LLD linker (`-fuse-ld=lld`). Set
  `CKB_PR5316_RISCV_CLANG=/absolute/path/to/clang` when the default `clang`
  lacks that target/linker. These tools compile the small syscall probes used
  by the test; ordinary host-only CKB build tools are insufficient.

One-time source preparation, from the Python repository root:

```sh
export CKB_PR5316_SOURCE_DIR="$PWD/.ai-test-agent/current/ckb-pr-5316-current/product"
git clone https://github.com/nervosnetwork/ckb.git "$CKB_PR5316_SOURCE_DIR"
git -C "$CKB_PR5316_SOURCE_DIR" checkout 60b8e349470bdffe57e9e92c3e0961b91c326b3a
git -C "$CKB_PR5316_SOURCE_DIR" submodule update --init --recursive
(cd "$CKB_PR5316_SOURCE_DIR" && cargo fetch --locked)
```

If the pinned checkout already exists, reuse it instead of cloning again. The
first opted-in run builds the `test` feature binary and writes source/feature/hash
metadata. Feature-specific cases also build production, profiling and tokio-trace
variants. No platform-specific executable digest is hard-coded.

```sh
# Inventory only; no binaries or source checkout are required.
python -m pytest -o addopts='' --collect-only -q test_cases/tx_pool_refactor/tests

# Full fixed-source suite (includes intentionally retained failing assertions).
python -m pytest -o addopts='' --run-pr5316 -q --tb=short \
  test_cases/tx_pool_refactor/tests

# A focused selection uses the same explicit opt-in.
python -m pytest -o addopts='' --run-pr5316 -q \
  test_cases/tx_pool_refactor/tests/test_41_pr5316_diagnostics_startup.py
```

Without `--run-pr5316`, these tests skip before loading/building a binary; normal
release-oriented CI is not silently redirected to the historical source pin.
Skipped tests are not counted as passing fixed-source verification.

## Known failures and evidence limits

The following expectations remain active rather than being removed or changed
into passing assertions:

- `test_pool_overlay_precedes_chain_and_with_data_only_changes_data`: the
  pool-backed live-cell response retains data despite `with_data=False`.
- `test_prior_v2_survives_temp_open_failure`: snapshot preservation and restart
  restoration passed in the recorded run, but the expected shutdown error log
  was absent.
- `test_v1_cleanup_failure_keeps_new_v2_readable`: the newly committed v2 remained
  readable and restored both transactions, but the expected v1-cleanup error
  log was absent.

The user-requested `test_unavailable_console_bind_is_isolated_from_node` removal
is preserved. The three other diagnostic tests remain.

The October 19 exported full-run header reported 7 failures out of 60, but its
body was truncated and contained only four failure records (including the
removed console-bind case). A separate run of the missing final 17 cases passed.
Those observations do not establish the other three original failure causes,
nor a current full-suite pass. The PR description records checks on the actual
submitted snapshot separately from these historical runs.

## Relationship to later network-budget work

Some of these files were subsequently reused and expanded for the independent
network-verification suite in PR #148. This PR packages their saved PR5316
versions, before that unrelated work introduced new runtime/workload dependencies.
It has no dependency on `suites.network_verification` or the later suite's target
manifest. There is intentional scenario overlap; follow-up consolidation should
preserve review mappings and assertions instead of silently treating the two
source/version contracts as identical.
