# Network verification — Python regression

The budget regressions use ordinary CKB binaries with the default **8-second
CPU cap**. Normal setup writes neither a budget TOML field nor a budget environment
variable. Remote verification may receive a shorter calibrated budget based on
its declared cycles; a timeout is proved by a hash-specific rejection, not by
waiting eight wall-clock seconds. Mapping and collection are not passing runs.

Existing DAO, Type ID, capacity, VM and sync tests stay in their original files.
The former duplicate budget modules under `tx_pool_refactor/tests/test_45`,
`46`, `47`, `51`, `52`, and `53` now point to this directory and collect no tests.
Their Case IDs remain beside the native test methods here.

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
CLI helpers use isolated temporary key files. No binary staging or additional
configuration layer is maintained here.

## Native pytest entry point

Use ordinary file, class or method selectors. No custom marker or automatic
feature-based skip is installed. For example:

```sh
python3 -m pytest test_cases/network_verification/test_peer_timeout.py
```

Use the ordinary binary selected by `CURRENT_TEST` for the configuration matrix:

```sh
python3 -m pytest test_cases/network_verification/test_budget_config.py
```

It first starts a default node, then checks that `max_tx_verify_time_ms` values
1, 12000, 0, -1 and 4294967296, plus `min_tx_verify_time_ms` and `cycles_per_ms`,
are rejected as unknown fields. This is the only case that deliberately writes
those fields. No separate test-feature build is required.

For the PR #5316 network/local/recovery scope, select the existing methods:

```sh
python3 -m pytest test_cases/network_verification
```

RPC-preview interruption is a separate behavior contract outside this PR.
These node tests contain no product-version skips or binary switching.

Use native `--collect-only`, `-k`, or node IDs for inspection and selection.
`pytest.ini` supplies the project report/output settings, and the existing
Makefile case-group loop includes this test directory. `runtime.py` only checks
fixed script inputs; it neither selects binaries nor controls test execution.

### Long-running VM interruption (VT2-58)

```sh
python3 -m pytest test_cases/network_verification/test_budget_cpu_stop.py
```

This test uses two fresh default-configured nodes and the bundled finite
billion-iteration syscall loop. Source-side local submission must consume at
least 16 seconds of process CPU and finish. The receiver must emit BEGIN and
a hash-specific budget refusal, with no END; observed process CPU must be
positive and at most 8.6 seconds, allowing sampling/background overhead. There
is no 7.7-second lower bound because Remote's calibrated budget can be shorter.

After refusal, 15 seconds of observation must return to the idle CPU allowance,
and a fast relay must still succeed. The receiver is then isolated and locally
executes the identical slow transaction to completion, consuming at least 16
seconds of CPU. This same-node control checks the body and cycles as well as
BEGIN/END, without a high-budget network node or a configuration change.

All slow-budget cases use isolated dev chains with block and pool verification cycles set to
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
- Mid-execution interruption of a single long-running VM, same-node
  local completion, and process CPU returning to idle after refusal (VT2-58).
- Fast-child preview with the independent 537-cycle/fee baseline, slow local
  admission, persistence, cold reorg recovery and re-mining;
  RBF parent/child preservation and rejected-transaction subscription controls.
- Default/unknown-field configuration boundaries; DAO, Type ID, since and capacity
  validation; seven fixed VM/syscall cycle combinations and cold block verification;
  valid sync progress after a genuinely invalid block.
- Mining retains uncle headers **and proposals**. Dropping template uncles can
  strand a recovered transaction in pending by omitting its proposal from the chain.

See [the case contracts](../../reviews/network-verification-budget.md).
VT2-03/04/05/30/55/57 (controlled proposal/orphan/malformed-relay packets) remain
unimplemented. Ordinary relay or RPC notification is not credited as those paths.
Mappings, semantic review and actual execution results are separate evidence.
Single-platform success is not a cross-platform guarantee. Persistence and reorg restarts verify the owning directory and
wait for the old PID to exit, then require a different listening PID. Other
existing node helpers use startup sleeps and port-based shutdown; ordinary RPCs
have no per-call timeout,
so use the existing CI/job timeout when a hard overall deadline is required.
There is no suite-owned subprocess, watchdog, environment reset or report format.

## Test readability and lifecycle

Each stateful scenario owns fresh nodes in its class's `setup_class` and stops
and cleans them in `teardown_class`, following the existing project style.
Node roles, default CPU-cap expectations, cycle limits, startup, and
the common 30-block chain are written directly in each class. Independent
persistence and local-RBF scenarios use separate classes so their pools and
chains cannot leak into one another. Test methods show connect, submit, retry/request barriers and subscription ACK/event
checks directly; no combined relay/retry scenario helpers or custom subscription
class remain. Configuration preparation/rejection and the single-use since
transaction construction are also written at their use sites. Tests inherit the
project's normal method teardown instead of overriding it with empty methods.

`workload` retains reusable low-level tools rather than full scenarios:

- `build_transaction` retains the original fixed-cycle inputs.
- `build_budget_transaction` uses the existing hash-pinned stress ELF and a
  100-CKB fee. Stress cycles are compared across local/relay/recovery paths; they
  are not presented as an independent VM baseline. The original 135598681-cycle
  workload is not used to claim default-cap exhaustion.
- Slow local RPCs, full-block submission and recovery waits have a 240-second
  wall-clock watchdog, distinct from the node's CPU budget.
- `assert_pool_transaction` / `assert_remote_budget_refusal`: shared exact-body,
  cycle, pool-state, peer and ban assertions; they do not submit or connect peers.
- `restart_and_assert_new_process`: the same PID-ownership/exit/replacement proof
  used by cold-restart tests; it never changes the budget.
- `stop_and_assert_process_exit`: waits for the owning PID before persistence
  fixtures read or replace snapshot files.
- Bounded polling, free-port allocation, hash/log extraction, full-block delivery
  and log retention. The TCP test keeps only a buffered line reader so fragmented
  or coalesced messages and finite deadlines retain their original behavior.

Existing DAO/Type ID serialization helpers are reused unchanged. These helpers avoid duplicating protocol packing or
weakening shared invariants; scenario actions stay visible in each test.

## 补齐的池集成用例

PR #5316 的普通二进制集成补充沿用同一 pytest 入口；默认 8 秒 CPU 上限不配置。功能组可分别执行：

```sh
venv/bin/python -m pytest test_cases/tx_pool_refactor/test_transactional_admission.py
venv/bin/python -m pytest test_cases/tx_pool_refactor/test_pool_rpc_progress.py
venv/bin/python -m pytest test_cases/tx_pool_refactor/test_indexer_pool_projection.py
venv/bin/python -m pytest test_cases/tx_pool_refactor/test_packing_context.py
venv/bin/python -m pytest test_cases/tx_pool_refactor/test_process_resilience.py
venv/bin/python -m pytest test_cases/tx_pool_refactor/test_peer_resilience.py
```

`test_pool_rpc_progress.py` 的实际 HTTP 408 场景使用多个有限长 VM 组，测试链 cycles 容量为 `4_000_000_000_000`，覆盖四组约 `2.016e12` cycles；网络 CPU 上限仍用默认 8 秒。等待本地最终准入的 watchdog 为 600 秒；机器过快而未触发 408、或未进入并发区间时明确失败，不按跳过或通过处理。慢本地请求收到真实 HTTP 408 后，只查询原 hash 的最终准入，不重新提交。进程故障只针对由端口/cwd 核实的本轮 PID。持久化 I/O 故障扩展位于 `tests/test_36_pr5316_persistence_failures.py`。

`test_packing_context.py` 的成熟度回退通过动态难度下的较短强分叉触发正常 reorg，独立核对累计工作量和 epoch 成熟度窗口。`truncate` RPC 会主动清空整个池，不适用于“独立交易保留”的断言。

性能诊断单独选择，矩阵运行较长：

```sh
venv/bin/python -m pytest test_cases/tx_pool_refactor/test_pool_performance.py
```

基线只取项目 `CURRENT_MAIN` 的二进制，基线和候选都用 `CURRENT_TEST` 的 dev 模板/链规格；不额外编译。真实 HTTP/准入时序、未完成样本、资源和指标写入 `report/pool_performance/`。没有冻结回退阈值和正式重复轮次前，这些 JSON 只支持诊断，不代表性能达标。

TPF-03 允许旧交易或 RBF 替换交易最终确认；替换入池不保证替换交易上链。模板刷新仅观测10秒，超窗记录 `template_refresh_seconds=null`、实际观察时长和残留hash，继续核对实际区块的冲突互斥、失效交易清理及新交易确认。`graph-template-refresh` 保存刷新观测，`graph-cost` 保存实际赢家和完整样本；不同赢家的样本分别解释。

当前未映射的集成前提与恢复点见 [主测试文档](../../reviews/ckb-pr-5316-non-test.md)：NTD-29、NTD-36、NTD-59。NTD-85 已有响应表征入口，参数上限的产品预期仍待确认。末四项 P2P 专项仍单独保留，不用本地 notify 代替。静态收集和辅助测试结果不代表 CKB 运行结果。

Contract C sources, the pinned header dependency, and manual rebuild instructions
are in the [VM fixture guide](../../source/contract/README.vm-fixtures.md). Manual outputs are separate
from the hash-pinned inputs used by pytest.
