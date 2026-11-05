import time

from framework.basic import CkbTest
from framework.util import get_project_root


class TestCkbJsVm(CkbTest):
    @classmethod
    def setup_class(cls):
        """Prepare a current-version cluster with the JS VM contract."""
        nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"cluster1/hardFork/node{i}",
                8114 + i,
                8225 + i,
            )
            for i in range(4)
        ]
        cls.cluster = cls.Cluster(nodes)
        cls.source = nodes[0]
        cls.cluster.prepare_all_nodes(
            other_ckb_config={"ckb_logger_filter": "info,ckb_script=debug"}
        )
        cls.cluster.start_all_nodes()

        cls.cluster.connected_all_nodes()

        cls.Miner.make_tip_height_number(cls.source, 1000)
        cls.Node.wait_cluster_height(cls.cluster, 1000, 100)

        cls.vm_deploy_hash = cls.Contract.deploy_ckb_contract(
            cls.Config.MINER_PRIVATE_1,
            f"{get_project_root()}/source/contract/js_vm/ckb-js-vm",
            enable_type_id=False,
            api_url=cls.source.getClient().url,
        )
        cls.Miner.miner_until_tx_committed(
            cls.source, cls.vm_deploy_hash, with_unknown=True
        )
        vm_code_hash = cls.Contract.get_ckb_contract_codehash(
            cls.vm_deploy_hash,
            0,
            False,
            cls.source.getClient().url,
        )
        print("vm_code_hash:", vm_code_hash)

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestClass1")
        cls.cluster.stop_all_nodes()
        cls.cluster.clean_all_nodes()

    def test_01_deploy(self):
        """Connect legacy peers before and after invoking the panic contract."""
        client = self.source.getClient()
        self._start_legacy_follower(6)

        account = self.Config.ACCOUNT_PRIVATE_1
        code_path = f"{get_project_root()}/source/contract/js_vm/panic-code"
        panic_deploy_hash = self.Contract.deploy_ckb_contract(
            account,
            code_path,
            enable_type_id=True,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(
            self.source, panic_deploy_hash, with_unknown=True
        )
        panic_code_hash = self.Contract.get_ckb_contract_codehash(
            panic_deploy_hash, 0, True, client.url
        )
        miner_account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.MINER_PRIVATE_1
        )
        pending_dep_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            miner_account["address"]["testnet"],
            100,
            client.url,
            "1500",
        )

        panic_code_hash = panic_code_hash.replace("0x", "")
        tx_hash = self.Contract.invoke_ckb_contract(
            account_private=self.Config.ACCOUNT_PRIVATE_1,
            contract_out_point_tx_hash=self.vm_deploy_hash,
            contract_out_point_tx_index=0,
            type_script_arg=f"0x0000{panic_code_hash}01",
            data="0x1234",
            hash_type="data2",
            api_url=client.url,
            cell_deps=[
                {"tx_hash": panic_deploy_hash, "index": "0x0"},
                {"tx_hash": pending_dep_hash, "index": "0x0"},
            ],
        )
        print("pending_dep_hash:", pending_dep_hash)
        print("send tx:", tx_hash)
        client.get_transaction(tx_hash)
        time.sleep(10)
        self.Miner.miner_until_tx_committed(self.source, tx_hash, with_unknown=True)
        self.Node.wait_cluster_height(
            self.cluster,
            client.get_tip_block_number(),
            100,
        )
        self._start_legacy_follower(5)

    def _start_legacy_follower(self, index):
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.v206,
            f"cluster1/hardFork/node{index}",
            8114 + index,
            8225 + index,
        )
        # Keep logs until the framework has captured any test failure.
        self.addClassCleanup(node.clean)
        self.addCleanup(node.stop)
        node.prepare(other_ckb_config={"ckb_logger_filter": "info,ckb_script=debug"})
        node.start()
        node.connected(self.source)
        self.Node.wait_node_height(
            node, self.source.getClient().get_tip_block_number(), 100
        )
