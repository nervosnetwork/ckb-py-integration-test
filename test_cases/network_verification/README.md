# Network verification — Python regression

Run the completed 17 pytest methods (19 mapped integration test points) with
ordinary CKB nodes, RPC and node-to-node relay. Existing DAO, Type ID, capacity,
VM and sync tests are reused in place. Additional budget tests use descriptive
names directly under `test_cases/network_verification/`; `workload.py` only shares node/transaction helpers. No
custom Python P2P stack, Rust tests or automatic product builds are included.

## Prepare once

- Linux or macOS, Python 3.10–3.12, `lsof`, and `pip install -r requirements.txt`.
- Two CKB executables **from the same revision**: one normal production build,
  one separately built with the `test` feature. The tested revision must support
  network CPU-budget verification and the `max_tx_verify_time_ms` test field.
  Do not use the same executable for both options. The runner records hashes;
  it does not prove that independently supplied executables share a source revision.
- A compatible `ckb-cli` executable (the local verification used 0.210.1).
- The CKB checkout's `script/testdata` directory. The nine required ELF fixtures
  are listed with SHA-256 in `fixtures/vm-cycle-baseline.json`; a mismatch fails
  before any node starts. Obtain them with the matching CKB source checkout.
  The fixed expected cycles must not be regenerated from the node under test.
- Keep the checkout directory named `ckb-py-integration-test`; legacy framework
  root discovery depends on that name. Other names fail preflight.
- Checkout and output-directory paths must use ASCII letters, digits, `/`, `_`,
  `-` or `.` only (no spaces or shell metacharacters). The legacy node helpers
  use shell commands; unsupported paths fail preflight instead of being misquoted.
- Run serially, without Docker or pytest-xdist. Existing CLI helpers use shared
  temporary key-file paths. Keys and funds in these dev-chain fixtures are test-only.

## Run

From this repository (replace the four input paths):

```sh
python3 test_cases/network_verification/run_selected.py \
  --test-binary /path/to/test-build/ckb \
  --production-binary /path/to/production-build/ckb \
  --ckb-cli /path/to/ckb-cli \
  --testdata /path/to/ckb/script/testdata
```

This is the full selected regression, not the entire repository test suite.
Use `--collect-only` to confirm all 17 selectors without starting nodes.
Use `--selector 'file.py::Class::test_method'` (repeatable) for a subset; exact
choices are in `run_selected.py`. No `--selector` always means all 17 methods.

Each invocation prints a unique directory below `report/network-verification/`
containing `target.json` (binary/fixture hashes), `collected.json`,
`regression.xml`, `regression.log`, `regression.json` (command/status/duration),
and retained workload-node logs under `nodes/`. Use `--output` to change the
parent directory. The default watchdog is 900 seconds; `--timeout` changes only
the overall run deadline, never the 1ms/12000ms node CPU budgets.

The opt-in plugin binds executable paths in memory and restores them at session
end; it does not overwrite `source/ckb`, `source/ckb-cli` or default node config.
This suite fails if inputs are missing or incorrect; the wrapper does not skip
missing prerequisites. The two additional classes in the legacy VM/sync files
are skipped during the ordinary release-binary run unless this wrapper selects
the explicit target. Standard existing tests remain in the ordinary suite.

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
so the outer process-group watchdog remains the hard bound.

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
