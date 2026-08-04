# CKB Rust Integration Test Framework

## Overview

Located at `ckb/test/`, the Rust test framework provides lower-level testing with P2P protocol simulation. Tests run against real CKB node processes with fine-grained control over block construction, network messages, and chain state.

## Spec Trait

Every Rust test implements the `Spec` trait:

```rust
pub trait Spec: Send {
    // Auto-derived from type name (e.g. "BlockSyncFromOne")
    fn name(&self) -> &str { ... }

    // Configure number of nodes and retry behavior
    fn setup(&self) -> Setup {
        Setup { num_nodes: 1, retry_failed: 0 }
    }

    // Creates and starts nodes (auto-applies modify_app_config/modify_chain_spec)
    fn before_run(&self) -> Vec<Node> { ... }

    // Main test logic -- receives mutable node list
    fn run(&self, nodes: &mut Vec<Node>);

    // Optional: customize node configuration before start
    fn modify_app_config(&self, config: &mut CKBAppConfig) { ... }
    fn modify_chain_spec(&self, spec: &mut ChainSpec) { ... }
}
```

### Example Spec

```rust
pub struct MyTest;

impl Spec for MyTest {
    fn setup(&self) -> Setup {
        Setup { num_nodes: 2, retry_failed: 0 }
    }

    fn run(&self, nodes: &mut Vec<Node>) {
        let node0 = &nodes[0];
        let node1 = &nodes[1];

        // Mine blocks on node0
        node0.mine(10);

        // Connect nodes
        node0.connect(node1);

        // Wait for sync
        let target = node0.get_tip_block_number();
        node1.wait_until(|| node1.get_tip_block_number() == target);

        // Verify
        assert_eq!(node0.get_tip_block(), node1.get_tip_block());
    }
}
```

## Node Struct (`src/node.rs`)

Manages a CKB node process with RPC and P2P capabilities.

```rust
// Key fields
pub struct Node {
    working_dir: String,           // Temp directory
    consensus: Consensus,          // Chain parameters
    rpc_client: RpcClient,         // JSON-RPC client
    p2p_listen: String,            // P2P listen address
    rpc_listen: String,            // RPC listen address
    guard: Option<ProcessGuard>,   // Child process handle
    node_id: Option<String>,       // P2P node ID
}

// Key methods
impl Node {
    fn new(/* ... */) -> Self;     // Create with temp dir, allocate ports
    fn start(&mut self);           // Spawn CKB process
    fn stop(&mut self);            // Kill process

    // Mining
    fn mine(&self, count: u64);    // Mine N blocks via get_block_template + submit_block
    fn mine_until_bool<F>(&self, predicate: F);  // Mine until condition
    fn mine_until_transaction_confirm(&self, tx_hash: &H256);

    // P2P
    fn connect(&self, other: &Node);
    fn disconnect(&self, other: &Node);

    // Chain state
    fn get_tip_block(&self) -> Block;
    fn get_tip_block_number(&self) -> u64;
    fn submit_transaction(&self, tx: &Transaction) -> H256;
    fn wait_for_tx_pool(&self);

    // Utility
    fn wait_until<F>(&self, predicate: F);  // Poll until true (with timeout)
}
```

## P2P Simulation (`src/net.rs`)

The `Net` struct simulates a P2P peer for protocol-level testing:

```rust
pub struct Net {
    // Simulates peer connections with custom message handling
}

impl Net {
    fn connect(&self, node: &Node);
    fn send(&self, node: &Node, protocol: &str, message: Bytes);
    fn receive_timeout(&self, protocol: &str, timeout: Duration) -> Option<Bytes>;
}
```

Used for testing block relay, transaction relay, and compact blocks at the protocol level.

## Test Utility Functions

### Mining (`src/util/mining.rs`)

```rust
mine(node, count);                           // Mine N blocks
mine_until_bool(node, predicate);            // Mine until condition
mine_until_transaction_confirm(node, hash);  // Mine until tx committed
mine_until_out_ibd_mode(node);              // Exit initial block download
mine_until_out_bootstrap_period(node);      // Exit bootstrap
mine_with_blocking(node, condition);         // Mine with custom blocking
```

### Transaction (`src/util/transaction.rs`)

```rust
always_success_transaction(node, input);  // Create tx with always-success lock
send_tx(node, tx);                        // Send via RPC
relay_tx(net, node, tx);                  // Send via P2P relay protocol
get_tx_pool_conflicts(node);              // Get conflicting txs
```

### TXO Abstraction (`src/txo.rs`)

```rust
// TXO = Unspent Transaction Output (CKB's Cell)
pub struct TXO {
    out_point: OutPoint,    // tx_hash + index
    cell_output: CellOutput // capacity + lock + type + data
}

pub struct TXOSet {
    txos: Vec<TXO>
}

impl TXOSet {
    fn boom(&self) -> Vec<Transaction>;  // Split large TXOs into smaller ones
    fn bang(&self) -> Transaction;       // Create equal-capacity transaction
    fn bang_random_fee(&self) -> Transaction;  // Create tx with random fee
}
```

### General Utilities (`src/utils.rs`)

```rust
wait_until(timeout, predicate);        // Poll with timeout
build_block(tip, transactions);         // Construct block for P2P
build_header(tip);                      // Construct header for P2P
build_compact_block(block);             // Create compact block message
generate_utxo_set(node, count);         // Create spendable UTXOs
temp_path();                            // Create temp directory
```

## Test Domains

### Sync Tests (`specs/sync/`)

- `BlockSyncFromOne` -- Basic block sync between nodes
- `BlockSyncForks` -- Fork resolution (longest chain wins)
- `BlockSyncOrphanBlocks` -- Orphan block handling
- `ChainFork1-7` -- Various fork scenarios

### TX Pool Tests (`specs/tx_pool/`)

- `SendSecpTxUseDepGroup` -- SECP256K1 transaction with dep groups
- Orphan transaction handling
- RBF (Replace-By-Fee) rules
- Fee rate filtering
- Cycles limits
- Pool reconciliation after reorgs

### Mining Tests (`specs/mining/`)

- `MiningBasic` -- Proposal → commit lifecycle
- `BlockTemplates` -- Template updates on tip change

### DAO Tests (`specs/dao/`)

- `WithdrawDAO` -- Deposit → prepare → withdraw with epoch-based `since`
- `WithdrawDAOWithOverflowCapacity` -- Overflow protection

### Relay Tests (`specs/relay/`)

- Transaction relay via P2P
- Compact block relay
- Block propagation

## Running Rust Tests

```bash
cd ckb

# Run all integration tests
make integration

# Run specific spec
cargo run -p ckb-test -- --bin target/release/ckb sync::block_sync

# Run with options
cargo run -p ckb-test -- \
    --bin target/release/ckb \
    --concurrent 4 \
    --max-time 600 \
    --log-file test.log
```

## Test Runner Architecture

The Rust test runner uses a worker pool model:

1. Parse CLI args (binary path, spec filter, concurrency)
2. Register all spec implementations
3. Filter specs by name pattern
4. Spawn N worker threads
5. Each worker pulls specs from shared queue
6. Workers report results via channels (`Notify::Start/Done/Error/Panick`)
7. Sequential specs run after parallel workers finish
8. Support retry on failure, fail-fast mode, timeout handling
