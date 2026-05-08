"""
Regression tests for https://github.com/nervosnetwork/ckb/pull/5166

PR 5166 fixes two bugs in rich-indexer's `get_binary_upper_boundary()`:

1. Leading-zero bug: When `script.args` starts with 0x00, `BigUint::to_bytes_be()`
   strips the leading zero, producing an incorrectly large upper bound in
   lexicographic (bytea) comparison. The `args >= $prefix AND args < $upper`
   range ends up matching far more rows than intended.

2. All-0xFF overflow: When args is all 0xFF, `BigUint + 1` becomes
   `[0x01, 0x00, ...]`, which is lexicographically smaller than `[0xFF, ...]`,
   causing the range query to match nothing.

These tests deploy a type-id contract on node[0] (plain indexer) and query
the rich-indexer on node[1] in prefix mode with:
  - a prefix starting with 0x00 (leading-zero case)
  - a prefix of all 0xFF (overflow case)

Without the fix, the first case over-matches (returns extra cells whose args
start with 0x01) and the second case returns zero cells.
"""

import pytest

from framework.helper.contract import (
    deploy_ckb_contract,
    invoke_ckb_contract,
    get_ckb_contract_codehash,
)
from framework.util import get_project_root
from framework.config import MINER_PRIVATE_1
from framework.helper.miner import miner_until_tx_committed
from test_cases.rpc.node_fixture import get_cluster_indexer


class TestRichIndexerPrefixBoundary:

    def test_prefix_upper_boundary(self, get_cluster_indexer):
        cluster = get_cluster_indexer
        node_full = cluster.ckb_nodes[0]
        node_rich = cluster.ckb_nodes[1]
        api_url = node_full.getClient().url

        deploy_hash = deploy_ckb_contract(
            MINER_PRIVATE_1,
            f"{get_project_root()}/source/contract/always_success",
            enable_type_id=True,
            api_url=api_url,
        )
        miner_until_tx_committed(node_full, deploy_hash)

        # args grouped so each group shares a common prefix we'll later query by.
        # - "0x00aabb" / "0x00aacc" : two real matches for prefix "0x00aa"
        # - "0x01aabb"              : poison cell; WITHOUT the fix, the buggy
        #                             upper bound for prefix "0x00aa" becomes
        #                             "0xab" (leading zero dropped), and
        #                             "0x01aabb" < "0xab" lexicographically,
        #                             so it would be over-matched.
        # - "0xffff" / "0xffffcc"   : two real matches for prefix "0xffff".
        #                             WITHOUT the fix, the all-0xFF upper
        #                             bound becomes [0x01,0x00,0x00] which is
        #                             lexicographically smaller than 0xffff,
        #                             so the range query returns nothing.
        # - "0xabcd"                : unrelated control cell.
        args_list = [
            "0x00aabb",
            "0x00aacc",
            "0x01aabb",
            "0xffff",
            "0xffffcc",
            "0xabcd",
        ]
        for arg in args_list:
            tx_hash = invoke_ckb_contract(
                account_private=MINER_PRIVATE_1,
                contract_out_point_tx_hash=deploy_hash,
                contract_out_point_tx_index=0,
                type_script_arg=arg,
                data="0x",
                hash_type="type",
                api_url=api_url,
            )
            miner_until_tx_committed(node_full, tx_hash)

        codehash = get_ckb_contract_codehash(
            deploy_hash, 0, enable_type_id=True, api_url=api_url
        )

        def query_prefix(prefix):
            """Query rich-indexer get_cells in prefix mode; return list of type.args."""
            ret = node_rich.getClient().get_cells(
                {
                    "script": {
                        "code_hash": codehash,
                        "hash_type": "type",
                        "args": prefix,
                    },
                    "script_type": "type",
                    "script_search_mode": "prefix",
                },
                "asc",
                "0xff",
                None,
            )
            return sorted(obj["output"]["type"]["args"] for obj in ret["objects"])

        # --- Case 1: leading-zero prefix ----------------------------------
        # Expected exactly two cells with args starting with 0x00aa.
        # Before PR 5166: "0x01aabb" would also be returned (over-match).
        got_00aa = query_prefix("0x00aa")
        assert got_00aa == ["0x00aabb", "0x00aacc"], (
            f"leading-zero prefix over/under-match. "
            f"expected=['0x00aabb', '0x00aacc'] got={got_00aa}"
        )

        # Also: querying the "0x01" prefix must not be affected by the poison
        # cell being matched under "0x00aa".
        got_01 = query_prefix("0x01")
        assert got_01 == [
            "0x01aabb"
        ], f"0x01 prefix: expected ['0x01aabb'] got={got_01}"

        # --- Case 2: all-0xFF prefix (overflow) ---------------------------
        # Expected two cells: "0xffff" and "0xffffcc".
        # Before PR 5166: empty result (upper bound wraps to 0x010000 which
        # is lexicographically smaller than 0xffff in bytea).
        got_ffff = query_prefix("0xffff")
        assert got_ffff == ["0xffff", "0xffffcc"], (
            f"all-0xFF prefix returned wrong set. "
            f"expected=['0xffff', '0xffffcc'] got={got_ffff}"
        )

        # Sanity: exact-mode queries must still work for these edge cases.
        def query_exact(arg):
            ret = node_rich.getClient().get_cells(
                {
                    "script": {
                        "code_hash": codehash,
                        "hash_type": "type",
                        "args": arg,
                    },
                    "script_type": "type",
                    "script_search_mode": "exact",
                },
                "asc",
                "0xff",
                None,
            )
            return [obj["output"]["type"]["args"] for obj in ret["objects"]]

        assert query_exact("0x00aabb") == ["0x00aabb"]
        assert query_exact("0xffff") == ["0xffff"]
