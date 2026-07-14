# Wait for Indexer Design

## Context

`miner_until_tx_committed` currently returns as soon as CKB reports the
transaction as committed. The built-in indexer can still lag behind the chain,
so callers may query indexed cells before the committed block is available
through indexer RPCs.

## Decision

Add a reusable `wait_for_indexer` helper to `framework/helper/miner.py`.
`miner_until_tx_committed` will capture the node tip height once, immediately
after observing the committed transaction, and pass that fixed target to the
helper. A fixed snapshot avoids chasing a moving target if the node continues
to receive blocks.

## Helper Behavior

The helper accepts a node, a target height, and a timeout. It polls
`get_indexer_tip()` until the returned `block_number` is greater than or equal
to the target. A `None` tip or a lower block number means the indexer is still
syncing. Polls are separated by a short sleep.

If the indexer does not reach the target before the timeout, the helper raises
`TimeoutError` with the target height and most recently observed indexer tip.
It does not mine blocks or change the chain.

## Integration Flow

1. `miner_until_tx_committed` continues its existing transaction-status and
   mining loop.
2. When the transaction becomes committed, read the node tip height once.
3. Call `wait_for_indexer` with that height.
4. Return the original transaction response only after the helper succeeds.

Rejected, unknown, pending, and proposed transaction handling remains
unchanged.

## Testing

Add focused tests with a fake node/client and patched sleep behavior:

- Verify a committed transaction does not return while the indexer is behind,
  and returns when the indexer reaches the captured node-tip height.
- Verify `wait_for_indexer` raises `TimeoutError` when the indexer remains
  behind through the timeout.
- Run the focused tests and Python syntax compilation for the changed files.

