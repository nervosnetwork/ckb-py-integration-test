# https://github.com/nervosnetwork/ckb/pull/4111
import time


from framework.basic import CkbTest
from framework.util import get_project_root


class Test4111(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. start node in issue/node1
        2. miner 400 block
        Returns:

        """

        # 1. start node in issue/node1
        node1 = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "issue/node1", 8914, 8927
        )
        cls.node = node1
        node1.prepare(other_ckb_config={"ckb_logger_filter": "debug"})
        node1.start()

        # 2. miner 400 block
        cls.Miner.make_tip_height_number(cls.node, 400)

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestClass1")
        cls.node.stop()
        cls.node.clean()

    # TEST-MAP: VT2-53
    def test_output_capacity_exceeding_inputs_is_rejected(self):
        """
        1. 创建并确认容量为 99 CKB 的输入。
        2. 构造输出 100 CKB 的超额交易，检查两个 RPC 都拒绝且交易池不变。
        3. 用相同输入构造输出 98 CKB 的合法交易，检查预检与入池 cycles 一致。
        """
        import requests

        # 1. 准备已确认的 99 CKB 输入，独立核对实际输出容量。
        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        address = self.Ckb_cli.util_key_info_by_private_key(key)["address"]["testnet"]
        funding = self.Ckb_cli.wallet_transfer_by_private_key(
            key, address, 99, api_url=client.url, fee_rate="1000"
        )
        self.Miner.miner_until_tx_committed(self.node, funding)
        assert (
            int(
                client.get_transaction(funding)["transaction"]["outputs"][0][
                    "capacity"
                ],
                16,
            )
            == 99 * 100_000_000
        )

        # 2. 输出容量超过输入，检查预检和提交均返回 Malformed/Overflow。
        invalid = self.Tx.build_send_transfer_self_tx_with_input(
            [funding], ["0x0"], key, fee=-100_000_000, api_url=client.url
        )
        assert int(invalid["outputs"][0]["capacity"], 16) == 100 * 100_000_000
        before = client.get_raw_tx_pool(False)
        for method in ("test_tx_pool_accept", "send_transaction"):
            response = requests.post(
                client.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": [invalid, "passthrough"],
                },
                timeout=30,
            )
            response.raise_for_status()
            error = response.json()["error"]
            assert error["code"] == -1108, error
            assert "Malformed" in error["data"] and "Overflow" in error["data"], error
            assert client.get_raw_tx_pool(False) == before
        # 3. 使用同一输入、正常正手续费和有效签名，核对合法交易的 cycles 和入池状态。
        valid = self.Tx.build_send_transfer_self_tx_with_input(
            [funding], ["0x0"], key, fee=100_000_000, api_url=client.url
        )
        assert int(valid["outputs"][0]["capacity"], 16) == 98 * 100_000_000
        preview = client.test_tx_pool_accept(valid, "passthrough")
        tx_hash = client.send_transaction(valid)
        result = client.get_transaction(tx_hash)
        assert result["tx_status"]["status"] in ("pending", "proposed")
        assert int(result["cycles"], 16) == int(preview["cycles"], 16) > 0
        assert tx_hash in client.get_raw_tx_pool(False)["pending"]
        self.did_pass = True

    def deploy_and_invoke(self, account, path, node, try_count=5):
        if try_count < 0:
            raise Exception("try out of times")
        try:
            deploy_hash = self.Contract.deploy_ckb_contract(
                account, path, enable_type_id=True, api_url=node.getClient().url
            )
            self.Miner.miner_until_tx_committed(node, deploy_hash)
            time.sleep(1)
            invoke_hash = self.Contract.invoke_ckb_contract(
                account_private=account,
                contract_out_point_tx_hash=deploy_hash,
                contract_out_point_tx_index=0,
                type_script_arg="0x02",
                data="0x1234",
                hash_type="type",
                api_url=node.getClient().url,
                fee=-100,
            )
            return invoke_hash
        except Exception as e:
            if "Resolve failed Dead" in e.args[0]:
                try_count -= 1
                time.sleep(3)
                return self.deploy_and_invoke(account, path, node, try_count)
            raise Exception(e.args[0])
