# Tx-pool integration tests

PR #5316 adds **30 default integration methods**, strengthens two existing RBF
methods in `test_01_tx_replace_rule.py`, and keeps **18 explicit extended methods**.
Existing tests in this directory remain part of normal collection. Case designs
and deferred coverage live in [one review](../../reviews/ckb-pr-5316-non-test.md).
A TEST-MAP association does not mean the case passed or every variation is covered.

## Default entry

```sh
python3 -m pytest test_cases/tx_pool_refactor test_cases/network_verification
```

Run serially with the configured ordinary `CURRENT_TEST` binary and the existing
`source/ckb-cli`. The default CPU cap stays at 8 seconds. Tests may raise the dev
chain's VM-cycle ceiling; that is separate from the CPU-time policy. No CKB source
checkout, compiler, budget override, custom runner or version skip is required.

| New core group | Methods | Files under `test_cases/` |
| --- | ---: | --- |
| Atomic admission, capacity and preview | 8 | `tx_pool_refactor/test_transactional_admission.py` |
| Chain conflict and detached recovery | 2 | `tx_pool_refactor/test_chain_conflict.py`, `test_reorg_recovery.py` |
| Packing, proposals and block cycles | 5 | `tx_pool_refactor/test_packing_dag.py`, `test_read_before_spend.py`, `test_packing_exact_budget.py`, `test_cached_block_cycles.py`, `test_proposal_expiry.py` |
| Witness, Waiting clear and subscriptions | 3 | `tx_pool_refactor/test_witness_revalidation.py`, `test_pool_rpc_progress.py` |
| Ordinary/rich indexer projection | 2 | `tx_pool_refactor/test_indexer_pool_projection.py` |
| Persistence, replay and filesystem failures | 6 | `tx_pool_refactor/test_persistence_*.py` |
| Default budget and entry isolation | 4 | `network_verification/test_budget_config.py`, `test_peer_timeout.py`, `test_budget_consensus.py`, `tx_pool_refactor/test_capacity_metrics.py` |

The existing RBF methods now check exact membership, body, size and cycles after
new-unconfirmed-input rejection and the 100/101 mutation boundary. The standalone
retry scenario is merged into the peer-timeout case, including identical-body
local admission after two remote refusals. Default execution still includes real
8-second refusals and local full verification; it is not a fast unit suite.

## Explicit extended entries

These ordinary pytest files deliberately do not start with `test_`; recursive
pytest and `make test` do not collect them. Select a file or method explicitly:

```sh
python3 -m pytest test_cases/tx_pool_refactor/extended/process_faults.py
python3 -m pytest test_cases/tx_pool_refactor/extended/peer_faults.py
python3 -m pytest test_cases/tx_pool_refactor/extended/slow_rpc.py
python3 -m pytest test_cases/tx_pool_refactor/extended/reorg_maturity.py
python3 -m pytest test_cases/tx_pool_refactor/extended/notifications.py
python3 -m pytest test_cases/tx_pool_refactor/extended/cpu_stop.py
python3 -m pytest test_cases/tx_pool_refactor/extended/performance.py
```

Counts are respectively 2, 6, 2, 1, 1, 1 and 5. Process-fault methods own fresh
nodes; SIGTERM follows a fresh VM BEGIN marker and a still-pending request.
SIGKILL checks only the previously verified snapshot, not all volatile admissions.
The shorter stronger-chain reorg retains the real maturity/descendant invariant.
Slow RPC cases require actual pending work and HTTP 408, without resubmitting the
original transaction. Four stress groups have a 4e12 dev-chain cycle ceiling.

Performance files produce diagnostics in `report/pool_performance/`. Timing
thresholds are not frozen. An original or its RBF replacement may confirm; hard
checks enforce conflict exclusivity, losing-family cleanup and new independent
transaction confirmation. Template refresh within 10 seconds is an observation.
Different winners do not establish a matched performance ranking.

## Evidence and deferred coverage

Earlier runs of the expanded 41-method selection ended at 38 pass / 3 fail; this
is historical evidence, not a full run of the streamlined commit. The two retained
persistence-fault tests reported missing error logs. The 144-round arrival matrix
was incomplete after all four nodes received exit signals of unknown origin.
Those expectations remain intact. The streamline validation ran six focused node
selectors (latest per selector: **5 pass / 1 fail**). The strengthened in-flight
SIGTERM case reproduced an empty Accepted snapshot despite five successful fast
submission responses; it fails before recovery assertions. This failure remains
active. Both default/extended collection and 31 helper checks pass; full default
execution and the 144-round performance matrix were not rerun.

Removed source/feature-build probes, empty migration wrappers and weaker duplicate
entry points are not part of this PR. Unique deferred cases retain their IDs and
unchecked rows in the review. In particular, exact-capacity equality TPA-06,
continuous-Accepted duplicate relay TMN-02 and fault interleaving TCH-06 are not
claimed as delivered. NTD-27 currently has the diamond representative; additional
chain/fanout package-score variations remain deferred.

Retained contract inputs have [sources and a manual build recipe](../../source/contract/README.vm-fixtures.md).
