# CKB Python Test Framework Architecture

## Core Components

### CkbNode (`framework/test_node.py`)

Manages a CKB node process lifecycle. Each test creates one or more nodes.

```python
# Initialize with unique ports
node = CkbNode.init_dev_by_port(
    CkbNodeConfigPath.CURRENT_TEST,  # Config template path
    "test_name/node1",               # Working directory name
    8120,                             # RPC port
    8225                              # P2P port
)

# Lifecycle
node.prepare()          # Generate config files from templates
node.start()            # Start CKB process
node.startWithRichIndexer()  # Start with rich indexer support
node.stop()             # Stop CKB + miner processes
node.restart()          # Restart (optionally clean data)
node.clean()            # Remove working directory

# RPC access
client = node.getClient()  # Returns RPCClient instance

# Peer management
node.connected(other_node)          # Connect via P2P
node.connected_all_address(other_node)  # Connect all addresses

# Miner control
node.start_miner()
node.stop_miner()

# Subscriptions
node.subscribe_telnet(topic)    # TCP subscription
node.subscribe_websocket(topic) # WebSocket subscription
```

**Config paths** (`CkbNodeConfigPath`):
- `CURRENT_TEST` -- Current version dev chain (most common)
- `TESTNET` -- Testnet config
- `PREVIEW_DUMMY` -- Preview network
- `v200`, `v201`, `v202` -- Specific version configs (for compatibility tests)

**Docker support**: Set `DOCKER=true` env var to run nodes in Docker containers. CkbNode handles Docker commands transparently.

### RPCClient (`framework/rpc.py`)

JSON-RPC 2.0 client with automatic retry (15 attempts).

```python
client = node.getClient()

# Chain RPCs
client.get_tip_block_number()           # Returns hex string
client.get_block(block_hash)
client.get_block_by_number(number)
client.get_header_by_number(number)
client.get_transaction(tx_hash)         # Returns tx + status + min_replace_fee

# Pool RPCs
client.send_transaction(tx, "passthrough")  # output_validator options
client.tx_pool_info()
client.get_raw_tx_pool(verbose=True)    # Returns pending/proposed maps
client.clear_tx_pool()

# Mining RPCs
client.generate_block()
client.generate_block_with_template(template)
client.get_block_template()

# Indexer RPCs
client.get_cells(search_key, order, limit, after_cursor)
client.get_transactions(search_key, order, limit, after_cursor)
client.get_cells_capacity(search_key)

# Network RPCs
client.add_node(peer_id, address)
client.remove_node(peer_id)
client.local_node_info()
client.get_peers()

# DAO RPCs
client.calculate_dao_field(block_template)
client.calculate_dao_maximum_withdraw(out_point, block_hash)

# Utility RPCs
client.estimate_cycles(tx)
client.dry_run_transaction(tx)
client.get_live_cell(out_point, with_data=True)
client.get_consensus()
client.get_fee_rate_statistics(target=None)
```

### Cluster (`framework/test_cluster.py`)

Manages multiple CKB nodes as a group.

```python
cluster = Cluster([node1, node2, node3])
cluster.start()
cluster.stop()
cluster.clean()
cluster.ckb_nodes  # Access list of CkbNode instances
```

### CkbTest (`framework/basic.py`)

Abstract base class. Provides module aliases and test lifecycle hooks.

```python
class CkbTest(ABC, unittest.TestCase):
    # Module aliases (class attributes)
    Miner = framework.helper.miner
    Ckb_cli = framework.helper.ckb_cli
    Contract = framework.helper.contract
    Node = framework.helper.node
    Tx = framework.helper.tx
    Cluster = framework.test_cluster.Cluster
    CkbNode = framework.test_node.CkbNode
    Config = framework.config
    # ... etc.
```

**Failure artifact collection**: On test failure, `teardown_method()` copies `tmp/` directory to `report/{method_name}/` for debugging.

## Helper Modules

### Miner (`framework/helper/miner.py`)

```python
# Set chain tip to exact height (mines or truncates)
make_tip_height_number(node, target_height)

# Mine until a specific transaction is committed (max 100 blocks)
miner_until_tx_committed(node, tx_hash)

# Mine a block with specific version (for hardfork testing)
miner_with_version(node, "0x0")   # Pre-CKB2023
miner_with_version(node, "0x1")   # CKB2023

# Convert block template to submit_block format
block_template_transfer_to_submit_block(template)

# Difficulty utilities
compact_to_target(compact)
target_to_compact(target)
```

### Transaction (`framework/helper/tx.py`)

```python
# Send a self-transfer transaction using specific inputs
send_transfer_self_tx_with_input(
    tx_hash_list,         # List of input tx hashes
    tx_index_list,        # List of input indices (e.g. ["0x0"])
    private_key,
    data="0x",            # Optional cell data
    fee=1000,             # Fee in shannons
    output_count=1,       # Number of outputs
    api_url="..."         # Node RPC URL
)

# Build transaction without sending (returns tx dict)
build_send_transfer_self_tx_with_input(
    tx_hash_list, tx_index_list, private_key, ...
)

# Extract tx info from ckb-cli generated JSON
build_tx_info(tx_hash_list, tx_index_list, private_key, api_url)
```

### CKB CLI (`framework/helper/ckb_cli.py`)

```python
# Transfer CKB by private key
wallet_transfer_by_private_key(
    private_key, to_address, capacity,
    api_url, fee_rate="1000"
)

# Get account info from private key
util_key_info_by_private_key(private_key)
# Returns: {"address": {"testnet": "ckt1...", "mainnet": "ckb1..."}, ...}

# Query balance
wallet_get_capacity(address, api_url)

# Transaction building steps
tx_init(tx_file)
tx_add_input(tx_file, tx_hash, index, api_url)
tx_add_output(tx_file, address, capacity, api_url)
tx_sign_inputs(tx_file, private_key, api_url)
tx_send(tx_file, api_url)
```

### Contract (`framework/helper/contract.py`)

```python
# Deploy a contract binary
deploy_ckb_contract(
    private_key,
    contract_path,           # Path to compiled contract binary
    enable_type_id=True,     # Use type_id for unique identification
    api_url="..."
)

# Invoke a deployed contract
invoke_ckb_contract(
    account_private,
    contract_out_point_tx_hash,
    contract_out_point_tx_index,
    type_script_arg,
    data="0x",
    hash_type="type",
    api_url="..."
)

# Get contract code hash
get_ckb_contract_codehash(tx_hash, index, enable_type_id, api_url)
```

### Node Helpers (`framework/helper/node.py`)

```python
# Wait until transaction reaches expected status
wait_get_transaction(node, tx_hash, expected_status)
# expected_status: "pending", "proposed", "committed", "rejected"
```

## Configuration (`framework/config.py`)

```python
# Test accounts (pre-funded on dev chain)
MINER_PRIVATE_1 = "0x98400f6a67af07025f5959af35ed653d649f745b8f54bf3f07bef9bd605ee946"
ACCOUNT_PRIVATE_1 = "0xd00c06bfd800d27397002dca6fb0993d5ba6399b4238b2f29ee9deb97593d2bc"
ACCOUNT_PRIVATE_2 = "0x63d86723e08f0f813a36ce6aa123bb2289d90680ae1e99d4de8cdb334553f24d"

# Contract paths
ALWAYS_SUCCESS_CONTRACT_PATH = "{project_root}/source/contract/always_success"
SPAWN_CONTRACT_PATH = "{project_root}/source/contract/test_cases/spawn_demo"
UDT_CONTRACT_PATH = "{project_root}/source/contract/XUDTType"

# Default node config
CKB_DEFAULT_CONFIG = {
    "ckb_chain_spec": '{ file = "dev.toml" }',
    "ckb_rpc_modules": ["Net", "Pool", "Miner", "Chain", "Stats",
                         "Subscription", "Experiment", "Debug", "IntegrationTest"],
    # ... block assembler config
}
```

## Pytest Fixtures

Some test files use pytest fixtures for shared node setup (e.g., `test_cases/rpc/node_fixture.py`):

```python
@pytest.fixture(scope="module")
def get_cluster_indexer():
    # Setup cluster with indexer support
    cluster = Cluster([node1, node2])
    cluster.start()
    yield cluster
    cluster.stop()
    cluster.clean()
```

## File Templates

Node configuration uses Jinja2 templates at `source/template/`:
- `ckb.toml.j2` -- Main CKB config
- `specs/mainnet.toml.j2` -- Chain spec
- `ckb_light_client/config.toml.j2` -- Light client config
- `fiber/config.yml.j2` -- Fiber config

Templates are rendered with `CkbNode.prepare()` using config dicts from `framework/config.py`.
