# Network verification — Python regression

These 18 pytest methods cover 20 mapped integration test points using ordinary
CKB nodes, RPC and node-to-node relay. Existing DAO, Type ID, capacity, VM and
sync methods stay in their original files rather than being copied.

## Existing project configuration

All nodes use `CkbNodeConfigPath.CURRENT_TEST` directly. The caller chooses the
CKB/ckb-cli binaries by editing that existing configuration before a run;
tests do not select another binary directory or require an enabling flag.
CLI helpers continue to use the project's existing `source/ckb-cli` path.

Requirements: the project's Python dependencies, Python 3.10–3.12, Linux/macOS
and `lsof`. All fixed contract binaries are included in `source/contract` and
loaded directly from that directory; no script-path environment variable is used.
The original VM/syscall binaries match CKB's `script/testdata` inputs. Fixed ELF
hashes and expected VM cycles remain in `fixtures/vm-cycle-baseline.json`; tests
never regenerate expectations. The additional `cpu_budget_stress_1b` fixture is
a finite single-VM stress loop with its assembly source and linker script beside
the ELF; its hash is pinned, without claiming an independent cycles baseline.

Run serially with the framework's usual checkout name and shell-safe paths;
existing CLI helpers share temporary key paths. No binary staging or additional
configuration layer is maintained here.

## Native pytest entry point

Use ordinary file, class or method selectors. No custom marker or automatic
feature-based skip is installed. For example:

```sh
python3 -m pytest test_cases/network_verification/test_peer_timeout.py
```

The two configuration tests have different binary prerequisites. Configure
`CURRENT_TEST` with a production build without the test feature, then run:

```sh
python3 -m pytest test_cases/network_verification/test_budget_config.py::TestTxPoolConfigCompatibility::test_production_rejects_test_only_budget_fields
```

Configure `CURRENT_TEST` with a test-feature build supporting
`max_tx_verify_time_ms`, then run the test-feature configuration matrix:

```sh
python3 -m pytest test_cases/network_verification/test_budget_config.py::TestTxPoolConfigCompatibility::test_budget_configuration_bounds
```

The other budget regressions likewise require that test-feature build. Do not
run the production-only method and the test-feature methods together against
one binary. The caller selects the corresponding tests; an incompatible build
fails the original assertions instead of being silently skipped.

Use native `--collect-only`, `-k`, or node IDs for inspection and selection.
`pytest.ini` retains the existing HTML report settings, and the existing
Makefile case-group loop includes this test directory. `runtime.py` only checks
fixed script inputs; it neither selects binaries nor controls test execution.

### Long-running VM interruption (VT2-58)

```sh
python3 -m pytest test_cases/network_verification/test_budget_cpu_stop.py
```

This roughly two-minute test uses three fresh nodes and the bundled finite
billion-iteration syscall loop. Local submission and the 180000ms network
control must both consume at least 16 seconds of process CPU and finish. The
8000ms node must emit BEGIN and a budget refusal, but no END; its observed
process CPU between these events must be 7.7–8.6 seconds. After refusal, another
15 seconds of observation must return to the idle CPU allowance; a fast relay
must still succeed. All nodes are stopped and cleaned normally.

Only this test's isolated dev chain raises block and pool verification cycles to
`1_000_000_000_000`, so the cycle ceiling does not preempt the time budget. It
is not a mainnet-consensus workload. Run on an otherwise quiet machine: the
five-second idle baseline must use less than 5% of one CPU core. The post-refusal
CPU allowance is `max(0.25s, idle_rate * observation_seconds + 0.20s)`. A workload
that completes too quickly fails explicitly rather than falsely proving interruption.

Linux uses `/proc/<pid>/stat`; macOS converts `proc_pid_rusage` Mach ticks with
`mach_timebase_info`. No new dependency or environment variable is required.
Counters are tested against Python's process CPU clock. Results measure whole
process CPU, including background work, not exact execution-thread timing.
Unsupported operating systems skip this CPU-specific case before starting nodes.

The checked-in ELF is used directly, without a test-time build. To rebuild the
fixture manually with LLVM's RISC-V assembler and `ld.lld`:

```sh
clang --target=riscv64-unknown-elf -march=rv64imc -mabi=lp64 -nostdlib \
  -fuse-ld=lld -Wl,-T,source/contract/cpu_budget_stress_1b.ld \
  -Wl,--no-relax -Wl,-z,max-page-size=4096 \
  source/contract/cpu_budget_stress_1b.S -o source/contract/cpu_budget_stress_1b
```

Toolchain changes can alter ELF bytes; check the pinned input hash rather than
regenerating it automatically during a test run.

## What is asserted

- Hash-specific real-node CPU-budget refusal, fresh transaction request barriers,
  absent pool/rejected state, connected peers, no bans and subsequent success.
- Mid-execution interruption of a single long-running VM, high-budget relay
  completion, and process CPU returning to idle after refusal (VT2-58).
- Local/dry-run admission, persistence, cold reorg recovery and re-mining;
  RBF parent/child preservation and rejected-transaction subscription controls.
- Production/test configuration boundaries; DAO, Type ID, since and capacity
  validation; seven fixed VM/syscall cycle combinations and cold block verification;
  valid sync progress after a genuinely invalid block.
- Mining retains uncle headers **and proposals**. Dropping template uncles can
  strand a recovered transaction in pending by omitting its proposal from the chain.

See [the case contracts](../../reviews/network-verification-budget.md).
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
chains cannot leak into one another. Test methods show connect, submit, retry/request barriers and subscription ACK/event
checks directly; no combined relay/retry scenario helpers or custom subscription
class remain. Configuration preparation/rejection and the single-use since
transaction construction are also written at their use sites. Tests inherit the
project's normal method teardown instead of overriding it with empty methods.

`workload` retains reusable low-level tools rather than full scenarios:

- `build_transaction` and fixed genesis scripts/cells: shared immutable inputs.
- `assert_pool_transaction` / `assert_remote_budget_refusal`: shared exact-body,
  cycle, pool-state, peer and ban assertions; they do not submit or connect peers.
- `restart_and_assert_new_process`: the same PID-ownership/exit/replacement proof
  used by three cold-restart tests; not an unchecked stop/start shortcut.
- Bounded polling, free-port allocation, hash/log extraction, full-block delivery
  and log retention. The TCP test keeps only a buffered line reader so fragmented
  or coalesced messages and finite deadlines retain their original behavior.

Existing DAO/Type ID serialization helpers are reused unchanged. These helpers avoid duplicating protocol packing or
weakening shared invariants; scenario actions stay visible in each test.
