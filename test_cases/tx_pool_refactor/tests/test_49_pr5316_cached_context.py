"""PR #5316: a successful script proof cannot waive fresh since checks."""

import json
import tempfile
from pathlib import Path

import pytest

from framework.basic import CkbTest
from test_cases.network_verification.workload import unused_ports


class TestCachedContext(CkbTest):
    def teardown_method(self, method):
        # The focused pytest log is the retained execution record.
        pass

    def _since_transaction(self, client, split_hash, key, since_height):
        with tempfile.TemporaryDirectory(prefix="pr5316-cache-since-") as directory:
            tx_file = str(Path(directory) / "since.json")
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            self.Ckb_cli.tx_init(tx_file, client.url)
            self.Ckb_cli.tx_add_multisig_config(address, tx_file, client.url)
            self.Ckb_cli.tx_add_input(split_hash, 0, tx_file, client.url)
            cell = client.get_transaction(split_hash)["transaction"]["outputs"][0]
            self.Ckb_cli.tx_add_type_out_put(
                cell["lock"]["code_hash"],
                cell["lock"]["hash_type"],
                cell["lock"]["args"],
                hex(int(cell["capacity"], 16) - 2000),
                "0x",
                tx_file,
                False,
            )
            with open(tx_file, encoding="utf-8") as stream:
                document = json.load(stream)
            document["transaction"]["inputs"][0]["since"] = hex(since_height)
            with open(tx_file, "w", encoding="utf-8") as stream:
                json.dump(document, stream)
            signature = self.Ckb_cli.tx_sign_inputs(key, tx_file, client.url)[0]
            self.Ckb_cli.tx_add_signature(
                signature["lock-arg"],
                signature["signature"],
                tx_file,
                client.url,
            )
            tx = self.Tx.build_tx_info(tx_file)
            assert tx["inputs"][0]["since"] == hex(since_height)
            return tx

    # TEST-MAP: NTD-40
    # TEST-MAP: VT2-52
    # TEST-EVIDENCE-BEGIN: VT2-52
    # Evidence | Case: VT2-52 (covered)
    # Evidence | Setup: CURRENT_TEST 默认二进制；确认split输入，low_height/hash；在high=low+6构造since=high签名tx，先入池获得cycles。
    # Evidence | Trigger: remove_transaction后truncate到low；同tx经两个RPC均应Immature；再挖到high、preview/submit同tx。
    # Evidence | Observe: 低高度pool空，两个RPC匹配Immature；成熟后cycles等原值，返回同hash并进入pending/proposed。
    # Evidence | Assert: assert client.get_block_hash(hex(low_height)) == low_hash
    # Evidence | Assert: assert client.get_tip_block_number() == high_height
    # Evidence | Assert: assert high_cycles > 0
    # Evidence | Assert: assert client.get_transaction(accepted_hash)["tx_status"]["status"] in {
    # Evidence |                 "pending", "proposed",
    # Evidence |             }
    # Evidence | Assert: assert client.remove_transaction(accepted_hash) is True
    # Evidence | Assert: assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}
    # Evidence | Assert: assert client.get_tip_block_number() == low_height
    # Evidence | Assert: assert client.get_transaction(split)["tx_status"]["block_hash"] == low_hash
    # Evidence | Assert: assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}
    # Evidence | Assert: assert client.get_tip_block_number() == high_height
    # Evidence | Assert: assert int(preview["cycles"], 16) == high_cycles
    # Evidence | Assert: assert client.send_transaction(tx) == accepted_hash
    # Evidence | Assert: assert accepted_hash in set(client.get_raw_tx_pool(False)["pending"]) | set(
    # Evidence |                 client.get_raw_tx_pool(False)["proposed"]
    # Evidence |             )
    # Evidence | Assert: with pytest.raises(Exception, match="Immature"):
    # Evidence |                 client.test_tx_pool_accept(tx, "passthrough")
    # Evidence | Assert: with pytest.raises(Exception, match="Immature"):
    # Evidence |                 client.send_transaction(tx)
    # Evidence | Limit: 仅评审冻结 Python 实现；没有将 Rust/native overlay 或历史进程退出码计为本 Case 的冻结后通过。
    # Evidence | Limit: 公开since回归，不证明内部缓存命中次数；未用remove_transaction声称清空成功缓存。
    # TEST-EVIDENCE-END: VT2-52
    def test_cached_script_proof_does_not_skip_since_after_truncate(self):
        """
        1. 准备节点和带绝对 since 条件的交易。
        2. 在较高链顶提交交易并记录验证周期。
        3. 回退链顶，用相同交易核对 Immature 拒绝和空池。
        4. 重新推进链顶，确认原交易可再次接受且周期一致。
        """

        # 1. 准备节点和带绝对 since 条件的交易。
        rpc_port, p2p_port = unused_ports(2)
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_cached_context/{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        started = False
        try:
            node.start()
            started = True
            client = node.getClient()
            self.Miner.make_tip_height_number(node, 30)
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=client.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(node, fund)
            split = self.Tx.send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                output_count=2,
                fee=2000,
                api_url=client.url,
            )
            self.Miner.miner_until_tx_committed(node, split)
            split_status = client.get_transaction(split)["tx_status"]
            low_height = int(split_status["block_number"], 16)
            low_hash = split_status["block_hash"]
            assert client.get_block_hash(hex(low_height)) == low_hash

            high_height = low_height + 6
            self.Miner.make_tip_height_number(node, high_height)
            assert client.get_tip_block_number() == high_height

            # 2. 在较高链顶提交交易并记录验证周期。
            tx = self._since_transaction(client, split, key, high_height)
            accepted_hash = client.send_transaction(tx)
            high_entry = client.get_raw_tx_pool(True)["pending"][accepted_hash]
            high_cycles = int(high_entry["cycles"], 16)
            assert high_cycles > 0
            assert client.get_transaction(accepted_hash)["tx_status"]["status"] in {
                "pending",
                "proposed",
            }
            assert client.remove_transaction(accepted_hash) is True
            assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

            # The same raw transaction and input outpoint retain the semantic
            # script key; only the tip-dependent absolute since result changes.

            # 3. 回退链顶，用相同交易核对 Immature 拒绝和空池。
            client.truncate(low_hash)
            assert client.get_tip_block_number() == low_height
            assert client.get_transaction(split)["tx_status"]["block_hash"] == low_hash
            with pytest.raises(Exception, match="Immature"):
                client.test_tx_pool_accept(tx, "passthrough")
            with pytest.raises(Exception, match="Immature"):
                client.send_transaction(tx)
            assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

            # 4. 重新推进链顶，确认原交易可再次接受且周期一致。
            self.Miner.make_tip_height_number(node, high_height)
            assert client.get_tip_block_number() == high_height
            preview = client.test_tx_pool_accept(tx, "passthrough")
            assert int(preview["cycles"], 16) == high_cycles
            assert client.send_transaction(tx) == accepted_hash
            assert accepted_hash in set(client.get_raw_tx_pool(False)["pending"]) | set(
                client.get_raw_tx_pool(False)["proposed"]
            )
            self.did_pass = True
        finally:
            if started:
                node.stop()
            node.clean()
