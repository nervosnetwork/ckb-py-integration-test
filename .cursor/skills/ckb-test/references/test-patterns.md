# CKB Test Patterns & Examples

## Table of Contents

1. [Transaction Pool / RBF Testing](#transaction-pool--rbf-testing)
2. [RPC API Testing](#rpc-api-testing)
3. [Contract Deployment & Invocation](#contract-deployment--invocation)
4. [Multi-Node Sync Testing](#multi-node-sync-testing)
5. [Hardfork Feature Testing](#hardfork-feature-testing)
6. [Error Assertion Patterns](#error-assertion-patterns)
7. [Mining Patterns](#mining-patterns)
8. [Transaction Chain Building](#transaction-chain-building)
9. [WebSocket Subscription Testing](#websocket-subscription-testing)
10. [Node Configuration Testing](#node-configuration-testing)

---

## Transaction Pool / RBF Testing

CKB supports Replace-By-Fee (RBF) similar to Bitcoin Core's BIP 125.

### Basic RBF: Higher Fee Replaces Lower Fee

```python
class TestRBF(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "rbf/node1", 8120, 8225
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    def setup_method(self, method):
        self.node.getClient().clear_tx_pool()
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")

    def test_rbf_higher_fee(self):
        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)

        # Send tx A with low fee
        tx_a = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"], 100,
            self.node.getClient().url, "1500"
        )

        # Send tx B with higher fee (same input) -- replaces A
        tx_b = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"], 200,
            self.node.getClient().url, "6000"
        )

        # Verify: A rejected, B pending
        assert self.node.getClient().get_transaction(tx_a)["tx_status"]["status"] == "rejected"
        assert self.node.getClient().get_transaction(tx_b)["tx_status"]["status"] == "pending"
```

### Using min_replace_fee

```python
def test_min_replace_fee(self):
    tx_a = self.Ckb_cli.wallet_transfer_by_private_key(
        self.Config.MINER_PRIVATE_1, address, 100, url, "1500"
    )
    # Query min_replace_fee from tx A
    tx_info = self.node.getClient().get_transaction(tx_a)
    min_fee = int(tx_info["min_replace_fee"], 16)

    # Replace using exact min_replace_fee
    tx_b = self.Tx.send_transfer_self_tx_with_input(
        [tx_a], ["0x0"], self.Config.MINER_PRIVATE_1,
        fee=min_fee, api_url=self.node.getClient().url
    )
    assert self.node.getClient().get_transaction(tx_b)["tx_status"]["status"] == "pending"
```

### RBF Conflict Limit (max 100 descendant transactions)

```python
def test_rbf_conflict_limit(self):
    tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(...)
    first_hash = tx_hash

    # Build chain of 100 descendants
    for i in range(100):
        tx_hash = self.Tx.send_transfer_self_tx_with_input(
            [tx_hash], ["0x0"], private_key,
            fee=1000, api_url=self.node.getClient().url
        )
        self.Node.wait_get_transaction(self.node, tx_hash, "pending")

    # Replacing root fails: too many conflicts (101 > 100)
    with pytest.raises(Exception) as exc_info:
        self.Ckb_cli.wallet_transfer_by_private_key(...)
    assert "conflict txs count: 101, expect <= 100" in exc_info.value.args[0]
```

---

## RPC API Testing

### Indexer: get_cells with Filters

```python
def test_get_cells_with_filter(self, cluster):
    # Deploy contract, invoke multiple times with different data
    deploy_hash = deploy_ckb_contract(MINER_PRIVATE_1, contract_path, ...)
    miner_until_tx_committed(cluster.ckb_nodes[0], deploy_hash)

    # Query cells with output_data filter
    result = node.getClient().get_cells(
        {
            "script": {"code_hash": codehash, "hash_type": "type", "args": "0x02"},
            "script_type": "type",
            "filter": {
                "output_data": "0x01",
                "output_data_filter_mode": "prefix"  # prefix | exact | partial
            },
        },
        "asc", "0xff", None
    )
    assert result["objects"][0]["output_data"] == "0x0101"
```

### Transaction Status Queries

```python
def test_transaction_lifecycle(self):
    tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(...)

    # Check pending
    tx = self.node.getClient().get_transaction(tx_hash)
    assert tx["tx_status"]["status"] == "pending"

    # Mine to propose
    self.Miner.miner_with_version(self.node, "0x0")
    self.Miner.miner_with_version(self.node, "0x0")

    # Check proposed
    tx = self.node.getClient().get_transaction(tx_hash)
    assert tx["tx_status"]["status"] == "proposed"

    # Mine to commit
    self.Miner.miner_until_tx_committed(self.node, tx_hash)

    # Check committed
    tx = self.node.getClient().get_transaction(tx_hash)
    assert tx["tx_status"]["status"] == "committed"
```

### Raw TX Pool Inspection

```python
def test_raw_tx_pool(self):
    self.Ckb_cli.wallet_transfer_by_private_key(...)
    tx_pool = self.node.getClient().get_raw_tx_pool(True)

    # Structure: {"pending": {hash: info, ...}, "proposed": {hash: info, ...}}
    pending_hashes = list(tx_pool["pending"].keys())
    assert len(pending_hashes) == 1

    # Each entry has fee, cycles, size, etc.
    info = tx_pool["pending"][pending_hashes[0]]
    assert "fee" in info
```

---

## Contract Deployment & Invocation

### Deploy and Invoke Always-Success Contract

```python
def test_deploy_contract(self):
    deploy_hash = self.Contract.deploy_ckb_contract(
        self.Config.MINER_PRIVATE_1,
        self.Config.ALWAYS_SUCCESS_CONTRACT_PATH,
        enable_type_id=True,
        api_url=self.node.getClient().url
    )
    self.Miner.miner_until_tx_committed(self.node, deploy_hash)

    # Get code hash for future invocations
    codehash = self.Contract.get_ckb_contract_codehash(
        deploy_hash, 0, enable_type_id=True,
        api_url=self.node.getClient().url
    )

    # Invoke contract
    invoke_hash = self.Contract.invoke_ckb_contract(
        account_private=self.Config.MINER_PRIVATE_1,
        contract_out_point_tx_hash=deploy_hash,
        contract_out_point_tx_index=0,
        type_script_arg="0x01",
        data="0xdeadbeef",
        hash_type="type",
        api_url=self.node.getClient().url
    )
    self.Miner.miner_until_tx_committed(self.node, invoke_hash)
```

---

## Multi-Node Sync Testing

### Two-Node Sync Verification

```python
class TestSync(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node1 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "sync/node1", 8120, 8225
        )
        cls.node2 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "sync/node2", 8121, 8226
        )
        cls.node1.prepare()
        cls.node2.prepare()
        cls.node1.start()
        cls.node2.start()

    def test_block_sync(self):
        # Mine blocks on node1
        self.Miner.make_tip_height_number(self.node1, 50)

        # Connect nodes
        self.node1.connected(self.node2)

        # Wait for node2 to sync
        import time
        for _ in range(60):
            tip1 = int(self.node1.getClient().get_tip_block_number(), 16)
            tip2 = int(self.node2.getClient().get_tip_block_number(), 16)
            if tip1 == tip2:
                break
            time.sleep(1)

        assert tip1 == tip2
```

---

## Hardfork Feature Testing

### CKB2023 Features (Spawn/Exec Syscalls)

```python
class TestCkb2023Spawn(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb2023/node1", 8120, 8225
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    def test_spawn_contract(self):
        # Deploy spawn contract
        deploy_hash = self.Contract.deploy_ckb_contract(
            self.Config.MINER_PRIVATE_1,
            self.Config.SPAWN_CONTRACT_PATH,
            enable_type_id=True,
            api_url=self.node.getClient().url
        )
        self.Miner.miner_until_tx_committed(self.node, deploy_hash)
        # ... invoke and verify spawn behavior
```

### Version-Aware Mining for Soft Fork Activation

```python
def test_soft_fork_activation(self):
    # Mine blocks with version 0x0 (pre-fork)
    for i in range(10):
        self.Miner.miner_with_version(self.node, "0x0")

    # Mine blocks with version 0x1 (post-fork signaling)
    for i in range(10):
        self.Miner.miner_with_version(self.node, "0x1")
```

---

## Error Assertion Patterns

### pytest.raises with Message Matching

```python
# Pattern 1: Assert error message substring
with pytest.raises(Exception) as exc_info:
    self.node.getClient().send_transaction(invalid_tx, "passthrough")
assert "expected_error" in exc_info.value.args[0]

# Pattern 2: Assert specific RPC error
with pytest.raises(Exception) as exc_info:
    self.Ckb_cli.wallet_transfer_by_private_key(...)
expected = "PoolRejectedRBF"
assert expected in exc_info.value.args[0], \
    f"Expected '{expected}' not found in '{exc_info.value.args[0]}'"
```

### Rejected Transaction Status

```python
# After RBF, old tx gets "rejected" status with reason
tx_response = self.node.getClient().get_transaction(old_tx_hash)
assert tx_response["tx_status"]["status"] == "rejected"
assert "RBFRejected" in tx_response["tx_status"]["reason"]
```

---

## Mining Patterns

### Mine to Exact Height

```python
# Set tip to height 100 (mines from current to 100)
self.Miner.make_tip_height_number(self.node, 100)
```

### Mine Until Transaction Committed

```python
tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(...)
self.Miner.miner_until_tx_committed(self.node, tx_hash)
# Transaction is now in committed state (confirmed)
```

### Manual Block Template Mining

```python
# Get block template, modify, submit
template = self.node.getClient().get_block_template()
# ... modify template (e.g., add proposals, change version)
block = self.Miner.block_template_transfer_to_submit_block(template)
self.node.getClient().submit_block("0x0", block)
```

---

## Transaction Chain Building

Build linked transactions for testing pool behavior:

```python
def build_tx_chain(self, root_hash, private_key, length):
    """Build a chain of N linked transactions."""
    tx_hashes = [root_hash]
    current = root_hash
    for i in range(length):
        current = self.Tx.send_transfer_self_tx_with_input(
            [current], ["0x0"], private_key,
            fee=1000, output_count=1,
            api_url=self.node.getClient().url
        )
        tx_hashes.append(current)
        self.Node.wait_get_transaction(self.node, current, "pending")
    return tx_hashes
```

---

## WebSocket Subscription Testing

```python
class TestWebSocket(CkbTest):
    def test_new_tip_header_subscription(self):
        # Subscribe to new tip headers via WebSocket
        result = self.node.subscribe_websocket("new_tip_header")
        # Mine a block to trigger notification
        self.Miner.miner_with_version(self.node, "0x0")
        # Verify notification received
        assert result is not None
```

---

## Node Configuration Testing

### Custom RPC Batch Limits

```python
class TestRpcBatchLimit(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(...)
        # Custom config can be applied before prepare()
        cls.node.prepare()
        cls.node.start()

    def test_batch_limit_exceeded(self):
        # Send batch request exceeding limit
        with pytest.raises(Exception):
            # ... batch RPC call exceeding configured limit
            pass
```

### Listen Address Configuration

```python
def test_custom_listen_address(self):
    node = self.CkbNode.init_dev_by_port(
        self.CkbNodeConfigPath.CURRENT_TEST, "config/node1", 8130, 8235
    )
    node.prepare()
    node.start()
    info = node.getClient().local_node_info()
    assert "8235" in str(info["addresses"])
    node.stop()
    node.clean()
```

---

## Test File Naming Convention

- `test_XX_description.py` where XX is a numeric ordering prefix
- Example: `test_01_tx_replace_rule.py`, `test_02_tx_pool_limit.py`
- Tests within a file follow `test_description_of_scenario` naming

## Key Test Design Principles

1. **Isolate state**: Use `setup_method` to clean tx pool between tests
2. **Mine sufficient blocks**: Always mine enough blocks for cellbase maturity (typically 30+)
3. **Wait for state**: Use `wait_get_transaction` or polling loops for async operations
4. **Clean up**: Always `stop()` and `clean()` nodes in `teardown_class`
5. **Unique ports**: Each test class must use unique RPC/P2P port pairs
6. **Descriptive docstrings**: Include step-by-step scenario description in test docstrings
