"""A successful script proof cannot waive fresh since checks."""

import json
import tempfile
from pathlib import Path

import pytest

from framework.basic import CkbTest
from test_cases.network_verification import workload


class TestCachedContext(CkbTest):
    @classmethod
    def setup_class(cls):
        # 单节点用例：先准备 30 块链，测试方法只处理 since 场景。
        rpc_port, p2p_port = workload.unused_ports(2)
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            f"network_verification/cached_context/{rpc_port}",
            rpc_port,
            p2p_port,
        )
        cls.started = False
        try:
            cls.node.prepare()
            cls.node.start()
            cls.started = True
            cls.Miner.make_tip_height_number(cls.node, 30)
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        if cls.started:
            cls.node.stop()
        workload.save_node_logs(cls.node)
        cls.node.clean()

    # TEST-MAP: VT2-52
    def test_cached_script_proof_does_not_skip_since_after_truncate(self):
        """
        1. 先在成熟高度接受带 since 的交易，记录 cycles 后将其移出池。
        2. 截断到输入已确认但 since 未成熟的高度，检查两个 RPC 都拒绝。
        3. 再次挖到成熟高度，检查相同交易的 hash/cycles 和入池结果。
        """
        # 1. 准备已确认输入，在 since 成熟高度接受交易，记录 cycles 后移出池。
        node = self.node
        client = node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        address = self.Ckb_cli.util_key_info_by_private_key(key)["address"]["testnet"]
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
        with tempfile.TemporaryDirectory(prefix="budget-cache-since-") as directory:
            tx_file = str(Path(directory) / "since.json")
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            self.Ckb_cli.tx_init(tx_file, client.url)
            self.Ckb_cli.tx_add_multisig_config(address, tx_file, client.url)
            self.Ckb_cli.tx_add_input(split, 0, tx_file, client.url)
            cell = client.get_transaction(split)["transaction"]["outputs"][0]
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
            document["transaction"]["inputs"][0]["since"] = hex(high_height)
            with open(tx_file, "w", encoding="utf-8") as stream:
                json.dump(document, stream)
            signature = self.Ckb_cli.tx_sign_inputs(key, tx_file, client.url)[0]
            self.Ckb_cli.tx_add_signature(
                signature["lock-arg"], signature["signature"], tx_file, client.url
            )
            tx = self.Tx.build_tx_info(tx_file)
            assert tx["inputs"][0]["since"] == hex(high_height)
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
        # 2. 保持交易及输入不变，仅截断链高度，检查预检和提交都返回 Immature。
        client.truncate(low_hash)
        assert client.get_tip_block_number() == low_height
        assert client.get_transaction(split)["tx_status"]["block_hash"] == low_hash
        with pytest.raises(Exception, match="Immature"):
            client.test_tx_pool_accept(tx, "passthrough")
        with pytest.raises(Exception, match="Immature"):
            client.send_transaction(tx)
        assert client.get_raw_tx_pool(False) == {"pending": [], "proposed": []}

        # 3. 再次达到成熟高度，检查同一交易的 cycles、hash 和入池状态。
        self.Miner.make_tip_height_number(node, high_height)
        assert client.get_tip_block_number() == high_height
        preview = client.test_tx_pool_accept(tx, "passthrough")
        assert int(preview["cycles"], 16) == high_cycles
        assert client.send_transaction(tx) == accepted_hash
        assert accepted_hash in set(client.get_raw_tx_pool(False)["pending"]) | set(
            client.get_raw_tx_pool(False)["proposed"]
        )
        self.did_pass = True
