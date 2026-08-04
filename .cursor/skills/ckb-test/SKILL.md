---
name: ckb-test
description: Comprehensive CKB blockchain integration test skill covering both Python (pytest) and Rust test frameworks. Use when writing, reviewing, debugging, or extending CKB integration tests, transaction pool tests, RPC tests, mining tests, P2P sync tests, DAO tests, hardfork tests, contract tests, or any CKB node behavior tests. Also use when asked about CKB testing patterns, framework architecture, or Bitcoin Core design parallels in the CKB context.
---

# CKB Integration Test Skill

## Overview

CKB (Nervos Common Knowledge Base) is a UTXO-based (Cell model) blockchain. This skill covers two testing layers:

1. **Python integration tests** (`test_cases/`) -- high-level end-to-end tests using pytest
2. **Rust integration tests** (`ckb/test/`) -- lower-level node behavior tests with P2P simulation

Both mirror Bitcoin Core's functional test design: spawn real node processes, interact via RPC, verify blockchain state.

## Architecture Quick Reference

```
ckb-py-integration-test/
├── framework/                   # Python test framework
│   ├── basic.py                 # CkbTest base class (extends unittest.TestCase + ABC)
│   ├── test_node.py             # CkbNode: node lifecycle (start/stop/restart/clean)
│   ├── test_cluster.py          # Cluster: multi-node management
│   ├── test_light_client.py     # Light client node management
│   ├── rpc.py                   # RPCClient: JSON-RPC wrapper with retry
│   ├── config.py                # Config: keys, paths, default settings
│   └── helper/
│       ├── miner.py             # Mining: make_tip_height_number, miner_until_tx_committed
│       ├── tx.py                # Tx: send_transfer_self_tx_with_input, build_tx_info
│       ├── ckb_cli.py           # CLI: wallet_transfer_by_private_key, deploy contracts
│       ├── contract.py          # Contract: deploy_ckb_contract, invoke_ckb_contract
│       └── node.py              # Node: wait_get_transaction, utility helpers
├── test_cases/                  # Test suites by domain
│   ├── tx_pool_refactor/        # Transaction pool: RBF, fee rules, conflicts
│   ├── rpc/                     # RPC API: get_cells, get_transaction, indexer
│   ├── ckb_cli/                 # CLI tool integration
│   ├── contracts/               # Smart contract deployment/invocation
│   ├── ckb2023/                 # CKB2023 hardfork: spawn, exec, new opcodes
│   ├── soft_fork/               # Soft fork activation tests
│   ├── feature/                 # Feature tests (headers-first sync, etc.)
│   ├── light_client/            # Light client protocol tests
│   ├── config/                  # Node configuration tests
│   ├── miner/                   # Mining behavior tests
│   ├── node_compatible/         # Version compatibility tests
│   ├── issue/                   # Regression tests for specific issues
│   ├── memory/                  # Memory limit tests
│   └── ws/                      # WebSocket subscription tests
├── ckb/test/                    # Rust integration tests
│   └── src/specs/               # Test specs by domain
│       ├── sync/                # Block sync, fork resolution, orphan blocks
│       ├── tx_pool/             # Mempool behavior, RBF, fee rates, cycles
│       ├── mining/              # Block template, proposal/commit lifecycle
│       ├── relay/               # Transaction/block relay via P2P
│       ├── rpc/                 # RPC endpoint correctness
│       ├── dao/                 # NervosDAO deposit/withdraw
│       ├── p2p/                 # P2P protocol behavior
│       └── hardfork/            # Hard fork feature activation
└── source/                      # Binaries, contracts, templates
    ├── contract/                # Pre-compiled test contracts
    └── template/                # Node config templates (Jinja2)
```

## Writing Python Tests

### Test Class Pattern

Every test inherits from `CkbTest` which provides module aliases:

```python
from framework.basic import CkbTest

class TestMyFeature(CkbTest):

    @classmethod
    def setup_class(cls):
        # Initialize node(s)
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "my_test/node1", 8120, 8225
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    def setup_method(self, method):
        # Clean state before each test
        self.node.getClient().clear_tx_pool()

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def test_something(self):
        # Test logic using self.node, self.Ckb_cli, self.Tx, etc.
        result = self.node.getClient().get_tip_block_number()
        assert int(result, 16) > 0
```

### Available Module Aliases on CkbTest

| Alias | Module | Key Functions |
|-------|--------|---------------|
| `Miner` | `framework.helper.miner` | `make_tip_height_number()`, `miner_until_tx_committed()`, `miner_with_version()` |
| `Ckb_cli` | `framework.helper.ckb_cli` | `wallet_transfer_by_private_key()`, `wallet_get_capacity()`, `util_key_info_by_private_key()` |
| `Tx` | `framework.helper.tx` | `send_transfer_self_tx_with_input()`, `build_tx_info()` |
| `Contract` | `framework.helper.contract` | `deploy_ckb_contract()`, `invoke_ckb_contract()`, `get_ckb_contract_codehash()` |
| `Node` | `framework.helper.node` | `wait_get_transaction()` |
| `Cluster` | `framework.test_cluster.Cluster` | Multi-node cluster management |
| `CkbNode` | `framework.test_node.CkbNode` | `init_dev_by_port()`, `start()`, `stop()`, `prepare()`, `clean()` |
| `Config` | `framework.config` | `MINER_PRIVATE_1`, `ACCOUNT_PRIVATE_1`, `ACCOUNT_PRIVATE_2` |

### Core Workflows

**Send and confirm a transaction:**

```python
tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
    self.Config.MINER_PRIVATE_1,
    target_address, 100,
    self.node.getClient().url, "1500"
)
self.Miner.miner_until_tx_committed(self.node, tx_hash)
```

**Build chained transactions (for RBF/conflict testing):**

```python
tx1 = self.Tx.send_transfer_self_tx_with_input(
    [parent_hash], ["0x0"], private_key,
    fee=1000, output_count=1, api_url=self.node.getClient().url
)
tx2 = self.Tx.send_transfer_self_tx_with_input(
    [tx1], ["0x0"], private_key,
    fee=1000, output_count=1, api_url=self.node.getClient().url
)
```

**Test expected errors:**

```python
with pytest.raises(Exception) as exc_info:
    self.Ckb_cli.wallet_transfer_by_private_key(...)
assert "PoolRejectedRBF" in exc_info.value.args[0]
```

**Multi-node cluster test:**

```python
cluster = cls.Cluster([node1, node2])
cluster.start()
node1.connected(node2)
# ... test sync/relay behavior
cluster.stop()
cluster.clean()
```

### Test Configuration

- **Ports**: Each node needs unique RPC + P2P ports. Use `init_dev_by_port(config, name, rpc_port, p2p_port)`.
- **Private keys**: Use `Config.MINER_PRIVATE_1` (miner rewards), `Config.ACCOUNT_PRIVATE_1/2` (funded accounts).
- **Node configs**: `CkbNodeConfigPath.CURRENT_TEST` for dev chain, other paths for testnet/mainnet configs.
- **Contracts**: Pre-compiled at `source/contract/` -- always_success, spawn_demo, XUDTType, etc.

### Running Tests

```bash
make prepare              # Install deps, download CKB binaries
make test                 # Run all test suites
pytest test_cases/tx_pool_refactor/test_01_tx_replace_rule.py  # Single test file
pytest test_cases/rpc/ -k "test_get_cells"                     # Filter by name
```

## Writing Rust Tests

See [references/rust-test-framework.md](references/rust-test-framework.md) for the `Spec` trait, `Node` struct, and P2P simulation details.

## Test Domain Reference

| Domain | Python Dir | Rust Dir | Key Scenarios |
|--------|-----------|----------|---------------|
| TX Pool | `tx_pool_refactor/` | `specs/tx_pool/` | RBF, fee rules, conflicts, orphans, pool limits |
| RPC | `rpc/` | `specs/rpc/` | get_cells, get_transaction, indexer, block template |
| Mining | `miner/` | `specs/mining/` | Proposal/commit lifecycle, block template, versions |
| Sync | `feature/` | `specs/sync/` | Block sync, fork resolution, headers-first |
| DAO | -- | `specs/dao/` | Deposit, prepare, withdraw, epoch calculation |
| Contracts | `contracts/`, `ckb2023/` | -- | Deploy, invoke, spawn, exec syscalls |
| P2P | -- | `specs/p2p/`, `specs/relay/` | Compact blocks, tx relay, peer management |
| Hardfork | `soft_fork/`, `ckb2023/` | `specs/hardfork/` | Feature activation, version bits |

## Bitcoin Core Design Parallels

CKB's test design mirrors Bitcoin Core's functional test framework. For detailed comparison, see [references/bitcoin-core-comparison.md](references/bitcoin-core-comparison.md).

Key parallels:
- **Cell model = UTXO model**: CKB's Cells are generalized UTXOs with lock/type scripts
- **TX Pool = Mempool**: pending/proposed/committed states (vs Bitcoin's mempool/block)
- **RBF**: Both support Replace-By-Fee with min_replace_fee rules
- **Block template**: `get_block_template` + `submit_block` pattern (identical to Bitcoin's `getblocktemplate`)
- **Proposal window**: CKB adds a 2-step commit: propose first, then commit (unique to CKB)

## Additional References

- **Framework architecture**: [references/framework-architecture.md](references/framework-architecture.md)
- **Common test patterns**: [references/test-patterns.md](references/test-patterns.md)
- **Rust test framework**: [references/rust-test-framework.md](references/rust-test-framework.md)
- **Bitcoin Core comparison**: [references/bitcoin-core-comparison.md](references/bitcoin-core-comparison.md)
