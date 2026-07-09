# v208 @Xcodes-chain PR Test Report

## Scope

Issue: https://github.com/nervosnetwork/acceptance-internal/issues/1505

Assigned PRs / items:

- nervosnetwork/ckb#5063: migrate CI to Blacksmith.
- nervosnetwork/ckb#5267: Blacksmith desktop runners, PR quick checks, full checks on rc/develop/master.
- nervosnetwork/ckb#5266: develop CI fix, including `ckb import` chain service lifecycle.
- nervosnetwork/ckb#5241: integration-test chunk failures must fail required aggregate checks.
- nervosnetwork/ckb#5247: Rust supply-chain check hardening.
- nervosnetwork/ckb#5251: scheduled audit app token permission restriction.
- nervosnetwork/ckb#5274: Windows release CRT static link and package artifact smoke test.
- nervosnetwork/ckb#5244: OpenSSL dependency upgrade to 0.10.81.
- nervosnetwork/ckb#5276: crossbeam-epoch RustSec upgrade.
- nervosnetwork/ckb#5277: tentacle, tentacle-multiaddr, tentacle-secio upgrade; discovery, identify, hole punching, QuicV1 handling.
- nervosnetwork/ckb#5221: bounded TTL hole punching forward limiter.
- nervosnetwork/ckb#5272: `ckb-test --keep-tmp-data` keeps temporary directories after panic.
- compare items: release signing key, assume-valid-target, v0.208.0-rc0 bump, import/export bats regression.

## New Local Test Cases

- `test_5266_5274_export_import_smoke.py`
  - `test_release_package_init_and_export_genesis_smoke`
    - Covers package smoke path from PR 5274: `ckb init` followed by `ckb export` from a fresh DB.
    - Also indirectly covers OpenSSL/crossbeam dependency upgrades by executing the packaged binary against store initialization and export.
  - `test_import_sequential_ranges_after_chain_service_scope_fix`
    - Covers PR 5266: repeated `ckb import` into the same target DB after exporting block ranges from v0.208.0-rc0.

- `test_5277_upgrade_tentacle.py`
  - `test_tcp_peer_connection_and_sync_state_after_tentacle_upgrade`
    - Covers tentacle upgrade regression risk: local TCP peer connection, block sync, and `sync_state` availability.
  - `test_quic_v1_multiaddr_is_rejected_without_crashing_node`
    - Covers the QuicV1 variant handling surface from PR 5277: unsupported `quic-v1` multiaddr submitted through `add_node` must not crash the node or break existing TCP peers.

## Local Validation

Environment:

- OS: macOS Darwin arm64.
- CKB binary: `ckb 0.208.0-rc0 (d732095 2026-07-09)`.
- Release asset: `ckb_v0.208.0-rc0_aarch64-apple-darwin-portable.zip`.

Commands:

```bash
venv/bin/python -m black test_cases/v208
venv/bin/python -m pytest --collect-only -q test_cases/v208
venv/bin/python -m pytest -q test_cases/v208
```

Result:

```text
4 passed, 1 warning in 91.63s
```

## Remote / CI Regression Items

These items are primarily GitHub Actions, workflow policy, or CKB source-repo checks and should be verified on the remote CKB repository / release workflow rather than as Python node integration tests:

- PR 5063 / PR 5267: Blacksmith runner migration, fork fallback, PR quick checks, full checks on protected branches.
- PR 5241: intentionally fail one integration-test chunk and verify aggregate required check fails instead of being skipped/successful.
- PR 5247 / PR 5276: `make security-audit`, `make check-crates`, `make check-licenses`, and cargo-deny guardrails.
- PR 5251: scheduled audit workflow token only grants issues/checks permissions needed by the audit action.
- PR 5272: run CKB source `ckb-test --keep-tmp-data` with a panic spec and verify `/tmp/ckb-it-*` directories remain.
- Release signing key / rc bump: verify package workflow signs artifacts only on push and release tag resolves to `v0.208.0-rc0`.

## Issues Found

- No CKB functional issue found in the new local v208 Python integration tests.
- Environment note: pytest emitted `urllib3` `NotOpenSSLWarning` because the local Python ssl module is built with LibreSSL 2.8.3. This did not affect test execution.
