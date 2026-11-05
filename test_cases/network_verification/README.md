# Default verification budget integration

These tests use the ordinary `CURRENT_TEST` binary with the default **8-second
CPU cap**. Normal setup writes no budget TOML field or budget environment variable.
Remote verification may get a shorter calibrated budget from declared cycles;
a target-hash rejection proves exhaustion, not eight wall-clock seconds.

```sh
python3 -m pytest test_cases/network_verification
```

Three native methods cover default startup/removed configuration fields,
remote retry and peer usefulness followed by identical-body local admission,
and a valid full block after pool-budget rejection. Proposal/notify metrics and
Local/cold Recovery are in `../tx_pool_refactor/test_capacity_metrics.py`.
The configuration negative case deliberately supplies removed fields and checks
`unknown field`; no test-feature CKB build is needed.

CPU-stop diagnostics moved to `../tx_pool_refactor/extended/cpu_stop.py` and require
explicit file selection. Other performance, process and slow-network commands are
in the [tx-pool README](../tx_pool_refactor/README.md). There are no empty migration
wrappers or source/feature compilation probes in this suite.

Use Python 3.10–3.12, project dependencies, Linux/macOS, `lsof`, configured CKB and
`source/ckb-cli`, and serial execution with the normal checkout name and shell-safe
paths. Tests do not stage binaries or download/build contracts. `runtime.py` checks
fixed input hashes in `fixtures/vm-cycle-baseline.json`; `workload.py` supplies
ordinary node/RPC helpers. The retained `current_cycles` and `cpu_budget_stress_1b`
contracts have [sources and a manual build recipe](../../source/contract/README.vm-fixtures.md).
The stress loop hash is pinned; local/remote cycles equality is a consistency
check, not an independent VM-cycle oracle.

[Network design](../../reviews/network-verification-budget.md) retains deferred
cases with unchecked boxes, including VM/exec/spawn matrix VT2-24, recovery after
RBF VT2-45, missing DAO header VT2-54 and RPC-preview CPU interruption VT2-59.
No product-version skips or runtime expectation regeneration are used.
