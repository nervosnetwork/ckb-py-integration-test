# v209 @Xcodes-chain PR Test Report

## Scope

Issue: https://github.com/nervosnetwork/acceptance-internal/issues/1546#issuecomment-5112605981

Assigned PRs:

- nervosnetwork/ckb#5063: migrate to Blacksmith CI.
- nervosnetwork/ckb#5246: harden `build_ckb_image` workflow against tag-name command injection.
- nervosnetwork/ckb#5247: harden Rust supply-chain checks.
- nervosnetwork/ckb#5251: limit scheduled audit app token permissions.
- nervosnetwork/ckb#5255: `cargo fmt --all`.
- nervosnetwork/ckb#5260: stabilize RPC test teardown.
- nervosnetwork/ckb#5266: fix develop CI failures, including `ckb import` lifecycle cleanup.
- nervosnetwork/ckb#5267: try Blacksmith desktop runners.
- nervosnetwork/ckb#5270: improve sync test robustness.
- nervosnetwork/ckb#5272: keep temporary test data on panic.
- nervosnetwork/ckb#5274: Windows release CRT dependency and package artifact smoke test.
- nervosnetwork/ckb#5278: update CKB release signing key.
- nervosnetwork/ckb#5281: enable additional Clippy lifetime lints.

## Test Analysis

Most assigned PRs are CI, workflow security, formatting, or source-repository test-fixture changes. They are not directly observable through the Python binary integration layer and should be reviewed or validated in the CKB repository workflows.

The binary integration suite can provide stable coverage for the release artifact and CLI/state-management surfaces:

- `ckb --version` confirms that the tested `download/current` binary is the packaged `v0.209.0` release.
- `ckb init` plus `ckb export` against a fresh data directory covers the release package smoke path from PR 5274.
- Sequential `ckb import` into the same target DB covers the PR 5266 lifecycle cleanup risk, where chain services or lock files could pollute repeated import runs.
- Running these paths through the packaged release binary also gives smoke coverage for the release signing-key/package path from PR 5278 once CI uses the `v0.209.0` release artifact.

## New Local Test Cases

- `test_5266_5274_release_smoke.py`
  - `test_packaged_binary_reports_v209_release_version`
  - `test_release_package_init_and_export_genesis_smoke`
  - `test_import_sequential_ranges_after_chain_service_scope_fix`

## Remote / CI Regression Items

These items should be verified in CKB source-repository workflows or by reviewing the workflow diff because they are not product behavior exposed by the packaged binary:

- PR 5063 / PR 5267: Blacksmith runner selection, fork fallback, PR quick checks, and protected-branch full checks.
- PR 5246: `build_ckb_image` tag-name shell injection hardening and quoted env-value handling.
- PR 5247: cargo-deny guardrails and workflow push-trigger restrictions.
- PR 5251: scheduled audit app token only requests issues/checks permissions.
- PR 5255 / PR 5281: formatting and Clippy-only source hygiene.
- PR 5260 / PR 5270: source test stability fixes already covered by CKB repository tests.
- PR 5272: `ckb-test --keep-tmp-data` panic behavior requires the CKB source test harness, which is not included in the release package.

## Issues Found

- No CKB functional issue found in the new local v209 Python integration tests.
- Environment note: pytest emitted `urllib3` `NotOpenSSLWarning` because the local Python ssl module is built with LibreSSL 2.8.3. This did not affect test execution.

## Local Validation

Environment:

- OS: macOS Darwin arm64.
- CKB binary: `ckb 0.209.0 (d166e28 2026-07-29)`.
- Release asset: `ckb_v0.209.0_aarch64-apple-darwin-portable.zip`.
- Related v0.209.0 binary PR for later validation reference: https://github.com/nervosnetwork/ckb-py-integration-test/pull/131

Commands:

```bash
venv/bin/python -m black test_cases/v209
venv/bin/python -m pytest --collect-only -q test_cases/v209
venv/bin/python -m pytest -q test_cases/v209
```

Result:

```text
3 passed, 1 warning in 31.58s
```
