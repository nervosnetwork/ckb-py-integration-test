# CKB vs Bitcoin Core: Test Framework Design Comparison

## Table of Contents

1. [Data Model Comparison](#data-model-comparison)
2. [Test Framework Architecture](#test-framework-architecture)
3. [Node Management](#node-management)
4. [RPC Interface](#rpc-interface)
5. [Mining Patterns](#mining-patterns)
6. [Transaction Pool (Mempool)](#transaction-pool-mempool)
7. [Replace-By-Fee (RBF)](#replace-by-fee-rbf)
8. [Block Relay & P2P](#block-relay--p2p)
9. [Fork & Reorg Handling](#fork--reorg-handling)
10. [Key Differences & CKB-Unique Concepts](#key-differences--ckb-unique-concepts)
11. [Design Patterns CKB Can Adopt from Bitcoin Core](#design-patterns-ckb-can-adopt)

---

## Data Model Comparison

| Concept | Bitcoin Core | CKB |
|---------|-------------|-----|
| Unspent output | UTXO (TxOut) | Cell (CellOutput + data) |
| Output reference | OutPoint (txid + vout) | OutPoint (tx_hash + index) |
| Locking script | scriptPubKey | lock_script (code_hash + args + hash_type) |
| Spending proof | scriptSig / witness | witnesses |
| Smart contract | Bitcoin Script (limited) | RISC-V VM (Turing-complete) |
| Unique ID | -- | type_script (enables unique cell identification) |
| Cell data | -- | Arbitrary data attached to each cell |
| Coinbase | Coinbase transaction | Cellbase transaction |

**CKB's Cell model** is a generalized UTXO. Each Cell has:
- `capacity` (like Bitcoin's value, but also sets storage limit)
- `lock_script` (who can spend it)
- `type_script` (optional: what rules govern the cell)
- `data` (arbitrary bytes, limited by capacity)

---

## Test Framework Architecture

| Aspect | Bitcoin Core | CKB Python | CKB Rust |
|--------|-------------|------------|----------|
| Language | Python | Python | Rust |
| Base class | `BitcoinTestFramework` | `CkbTest(ABC, TestCase)` | `Spec` trait |
| Required methods | `set_test_params()`, `run_test()` | `setup_class()`, test methods | `run()` |
| Enforcement | Metaclass | ABC | Trait |
| Test runner | Custom `test_runner.py` | pytest | Custom worker pool |
| Parallel execution | Yes (multiprocess) | Sequential (Makefile) | Yes (N workers) |

### Bitcoin Core's `BitcoinTestFramework`

```python
class MyTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 2
        self.setup_clean_chain = True

    def run_test(self):
        self.nodes[0].generate(100)
        self.sync_all()
```

### CKB's `CkbTest`

```python
class MyTest(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(...)
        cls.node.prepare()
        cls.node.start()

    def test_something(self):
        self.Miner.make_tip_height_number(self.node, 30)
```

---

## Node Management

| Feature | Bitcoin Core | CKB |
|---------|-------------|-----|
| Node class | `TestNode` | `CkbNode` (Python), `Node` (Rust) |
| RPC dispatch | Magic `__getattr__` (transparent) | `node.getClient().method()` (explicit) |
| Process mgmt | Auto start/stop | Manual `prepare()` → `start()` → `stop()` → `clean()` |
| Config template | Auto from args | Jinja2 templates |
| Port allocation | Framework auto-assigns | Developer specifies ports |
| Mock time | `setmocktime()` | Not available |
| Debug log assert | `assert_debug_log()` context mgr | Manual log inspection |
| Docker support | No | Yes (`DOCKER=true` env var) |

**Bitcoin Core RPC** (transparent):
```python
self.nodes[0].getblockchaininfo()  # Direct method call
```

**CKB RPC** (explicit):
```python
self.node.getClient().get_tip_block_number()  # Via RPCClient
```

---

## Mining Patterns

| Pattern | Bitcoin Core | CKB |
|---------|-------------|-----|
| Mine N blocks | `self.generate(node, N)` | `Miner.make_tip_height_number(node, N)` |
| Mine from template | `create_block()` + `block.solve()` | `get_block_template()` + `submit_block()` |
| Coinbase maturity | 100 blocks | 4 epochs (~16 blocks in dev) |
| Block cache | 200-block pre-mined cache | None (mine from genesis each test) |

**CKB-unique: Proposal/Commit 2-step process**

Unlike Bitcoin where transactions go directly from mempool to block, CKB requires:

1. **Propose**: Transaction ID appears in a block's proposal zone
2. **Gap**: Wait N blocks (proposal window)
3. **Commit**: Transaction can be included in commitment zone

```
Block N:   [proposals: tx_id_A]        ← tx proposed
Block N+1: [gap]
Block N+2: [commitments: tx_A]         ← tx committed (confirmed)
```

This is why CKB tests often mine multiple blocks:
```python
self.Miner.miner_with_version(self.node, "0x0")  # Propose
self.Miner.miner_with_version(self.node, "0x0")  # Gap
self.Miner.miner_with_version(self.node, "0x0")  # Commit
```

Or use the helper:
```python
self.Miner.miner_until_tx_committed(self.node, tx_hash)
```

---

## Transaction Pool (Mempool)

| Feature | Bitcoin Core | CKB |
|---------|-------------|-----|
| Name | mempool | tx_pool |
| States | unconfirmed → confirmed | pending → proposed → committed |
| Query | `getrawmempool()` | `get_raw_tx_pool(verbose)` |
| Clear | -- | `clear_tx_pool()` (debug RPC) |
| Fee rate | `getmempoolinfo()` | `tx_pool_info()`, `get_fee_rate_statistics()` |

CKB's tx_pool has two visible sections:
- **pending**: Valid txs waiting to be proposed
- **proposed**: Txs that have been proposed and are in the gap/commit window

```python
tx_pool = self.node.getClient().get_raw_tx_pool(True)
pending_txs = tx_pool["pending"]     # {hash: {fee, cycles, size, ...}}
proposed_txs = tx_pool["proposed"]   # {hash: {fee, cycles, size, ...}}
```

---

## Replace-By-Fee (RBF)

Both Bitcoin Core and CKB implement RBF with similar rules:

| Rule | Bitcoin (BIP 125) | CKB |
|------|-------------------|-----|
| Signal required | nSequence < 0xfffffffe | All txs are replaceable by default |
| Fee must increase | Yes | Yes (`min_replace_fee` field on tx) |
| No new unconfirmed inputs | Yes | Yes ("new Tx contains unconfirmed inputs") |
| Conflict limit | 100 descendants max | 100 descendants max |
| Children evicted | Yes | Yes (all descendants rejected) |

CKB provides `min_replace_fee` on each transaction for precise replacement:
```python
tx_info = self.node.getClient().get_transaction(tx_hash)
min_fee = int(tx_info["min_replace_fee"], 16)  # Exact fee needed to replace
```

---

## Block Relay & P2P

| Feature | Bitcoin Core | CKB |
|---------|-------------|-----|
| Python P2P layer | `P2PInterface` with message hooks | Not available |
| Rust P2P simulation | -- | `Net` struct in Rust tests |
| Compact blocks | BIP 152 | CKB compact block relay |
| Headers-first sync | Yes | Yes |
| Protocol hooks | `on_block()`, `on_tx()`, etc. | N/A (Python), `send()`/`receive()` (Rust) |

**Bitcoin Core P2P testing** (Python):
```python
class MyP2P(P2PInterface):
    def on_block(self, message):
        self.blocks.append(message.block)

peer = self.nodes[0].add_p2p_connection(MyP2P())
peer.send_message(msg_block(block))
peer.wait_for_getdata()
```

**CKB P2P testing** (Rust only):
```rust
let net = Net::new(/* ... */);
net.connect(&node);
net.send(&node, "Sync", build_header(tip));
let response = net.receive_timeout("Sync", Duration::from_secs(10));
```

CKB Python tests lack P2P simulation; they test node-to-node behavior by connecting real nodes via `node.connected(other_node)`.

---

## Fork & Reorg Handling

Both use longest-chain-wins rule:

**Bitcoin Core**:
```python
self.split_network()              # Disconnect [0,1] from [2,3]
self.generate(self.nodes[0], 10)  # Mine on partition A
self.generate(self.nodes[2], 20)  # Mine more on partition B
self.join_network()               # Reconnect → reorg to B's chain
```

**CKB** (Rust):
```rust
// ChainFork tests create competing chains
node0.mine(10);
node1.mine(20);
node0.connect(&node1);
// node0 reorgs to node1's chain
wait_until(|| node0.get_tip_block_number() == 20);
```

CKB Python tests don't have `split_network()`/`join_network()` helpers; use manual connect/disconnect.

---

## Key Differences & CKB-Unique Concepts

### 1. Cell Model vs UTXO Model
- Cells have `type_script` and `data` fields (UTXOs don't)
- Cells carry their own storage cost (`capacity` = value + storage rent)
- Type scripts enable programmable validation beyond spending conditions

### 2. Proposal/Commit Two-Phase Commit
- Unique to CKB; Bitcoin has single-step inclusion
- Tests must account for proposal window when verifying tx confirmation

### 3. Epochs vs Block Height
- CKB uses epochs for time-related calculations (DAO, difficulty adjustment)
- Each epoch has a target number of blocks
- `since` field in transaction inputs uses epoch-based locking

### 4. NervosDAO
- No equivalent in Bitcoin
- Deposit → lock → prepare withdrawal → withdraw
- Compensation for inflation (secondary issuance)
- Tests verify correct epoch calculation for withdrawal

### 5. Script Groups and Dep Cells
- Transactions reference "cell deps" (shared library cells)
- `dep_group` bundles multiple deps
- Type ID for deterministic contract addressing

### 6. Cellbase Maturity
- CKB cellbase matures after epochs (not fixed block count like Bitcoin's 100)
- Dev chain configured for faster maturity

---

## Design Patterns CKB Can Adopt

These patterns from Bitcoin Core could improve CKB's test framework:

### 1. Magic RPC Dispatch
Bitcoin: `node.getblockchaininfo()` → CKB could: `node.get_tip_block_number()`
Instead of: `node.getClient().get_tip_block_number()`

### 2. Built-in Sync Primitives
Bitcoin: `self.sync_all()`, `self.sync_blocks()`, `self.sync_mempools()`
CKB: Currently uses manual polling loops

### 3. Blockchain Cache
Bitcoin pre-mines 200 blocks and reuses the datadir.
CKB mines from genesis every test class, adding startup time.

### 4. MiniWallet Pattern
Bitcoin's `MiniWallet` creates/signs transactions without full wallet.
CKB relies on `ckb-cli` subprocess calls, which are slower.

### 5. Context Manager Assertions
Bitcoin: `with node.assert_debug_log(["expected message"]):`
CKB: Manual log file inspection

### 6. Structured Logging
Bitcoin: `self.log.info("Testing scenario X")`
CKB: `print()` statements

### 7. Python P2P Simulation
Bitcoin's `P2PInterface` allows acting as a network peer in Python tests.
CKB only has this capability in Rust tests via `Net`.
