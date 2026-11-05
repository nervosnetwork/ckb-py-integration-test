# CKB v0.210 Xcodes-chain Regression Report

## Scope

- [ckb#5309](https://github.com/nervosnetwork/ckb/pull/5309): upgrade `h2` to 0.4.17.
- [ckb#5314](https://github.com/nervosnetwork/ckb/pull/5314): reject oversized GetHeaders locators before allocation.
- [ckb#5324](https://github.com/nervosnetwork/ckb/pull/5324): prevent timestamp underflow in hole punching.

The tested CKB source tree was:

`/Users/xue/Downloads/ckb-ghsa-mqh2-6xxg-fpm3-dependabot-cargo-deps-bumps-rebase`

The archive does not contain Git metadata. The resulting binary reports
`ckb 0.209.0 ( )`; its SHA-256 is
`db29470eca513bed8d40863018093f5ed5788ff79f286b0a6526ac6b8c8ae2b1`.

## Source Validation

- `cargo test -p ckb-network --lib elapsed_millis_saturates_when_clock_moves_backwards -- --test-threads=1`
  - Result: 1 passed.
- `cargo test -p ckb-sync get_headers_rejects_oversized_locator_before_processing -- --nocapture --test-threads=1`
  - Result: 1 passed.
- `cargo test -p ckb-sync tests::synchronizer::functions -- --nocapture --test-threads=1`
  - Result: 14 passed.
- `cargo build --release --locked --bin ckb`
  - Result: passed; the locked build downloaded and compiled `h2 v0.4.17`.

## Binary Integration Validation

The locally built binary replaced `download/current/ckb` for all Python runs.

- New V210 regression case:
  - 24-block header synchronization from an established TCP peer.
  - Repeated HTTP RPC calls on both nodes after synchronization.
  - Node and TCP peer survival after an unsupported `quic-v1` add-node request.
  - Result: 2 passed.
- Existing V209, TCP/QUIC, and RPC regression suites:
  - Result: 37 passed, 5 skipped.
  - One Tor test was skipped because the opt-in environment variable was not set.
  - Four ckb-cli TUI tests remain disabled until ckb-cli#683 is merged.

## Coverage Boundary

The Python framework has no raw CKB P2P protocol peer and cannot inject a
backward system clock into the running node. Therefore, the oversized locator
and clock-rollback conditions are validated by the exact CKB source regression
tests. The Python case provides end-to-end coverage for the valid header-sync,
TCP network, and HTTP RPC paths around those changes.

## Findings

No regression or new issue was found in this validation.
