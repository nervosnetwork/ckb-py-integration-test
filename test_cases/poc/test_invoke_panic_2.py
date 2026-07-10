import time

from framework.basic import CkbTest
from framework.util import get_project_root


class TestCkbJsVm(CkbTest):
    cluster: CkbTest.Cluster
    ckb_js_vm_deploy_hash: str
    ckb_js_vm_codeHash: str

    @classmethod
    def setup_class(cls):
        """
        1. star 4 node in tmp/cluster/hardFork dir
        2. link ckb node each other
        3. deploy contract
        4. miner 1000 block
        5. deploy ckb-js-vm
        Returns:

        """

        # 1. star 4 node in tmp/cluster/hardFork dir
        nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                "cluster1/hardFork/node{i}".format(i=i),
                8114 + i,
                8225 + i,
            )
            for i in range(4)
        ]
        cls.cluster = cls.Cluster(nodes)
        cls.cluster.prepare_all_nodes(
            other_ckb_config={"ckb_logger_filter": "info,ckb_script=debug"}
        )
        cls.cluster.start_all_nodes()

        # 2. link ckb node each other
        cls.cluster.connected_all_nodes()

        # 4. miner 1000 block
        cls.Miner.make_tip_height_number(cls.cluster.ckb_nodes[0], 1000)
        cls.Node.wait_cluster_height(cls.cluster, 1000, 100)

        # 5. deploy ckb-js-vm
        cls.ckb_js_vm_deploy_hash = cls.Contract.deploy_ckb_contract(
            cls.Config.MINER_PRIVATE_1,
            f"{get_project_root()}/source/contract/js_vm/ckb-js-vm",
            enable_type_id=False,
            api_url=cls.cluster.ckb_nodes[0].getClient().url,
        )
        cls.Miner.miner_until_tx_committed(
            cls.cluster.ckb_nodes[0], cls.ckb_js_vm_deploy_hash, with_unknown=True
        )
        ckb_js_vm_codeHash = cls.Contract.get_ckb_contract_codehash(
            cls.ckb_js_vm_deploy_hash,
            0,
            False,
            cls.cluster.ckb_nodes[0].getClient().url,
        )
        print("ckb_js_vm_codeHash:", ckb_js_vm_codeHash)

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestClass1")
        cls.cluster.stop_all_nodes()
        cls.cluster.clean_all_nodes()

    def test_01_deploy(self):
        self.node_new_2 = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.v206,
            "cluster1/hardFork/node{i}".format(i=6),
            8114 + 6,
            8225 + 6,
        )
        self.node_new_2.prepare(
            other_ckb_config={"ckb_logger_filter": "info,ckb_script=debug"}
        )
        self.node_new_2.start()
        self.node_new_2.connected(self.cluster.ckb_nodes[0])
        self.Node.wait_node_height(
            self.node_new_2,
            self.cluster.ckb_nodes[0].getClient().get_tip_block_number(),
            100,
        )

        account = self.Config.ACCOUNT_PRIVATE_1
        code_path = f"{get_project_root()}/source/contract/js_vm/panic-code"
        js_code_deploy_hash = self.Contract.deploy_ckb_contract(
            account,
            code_path,
            enable_type_id=True,
            api_url=self.cluster.ckb_nodes[0].getClient().url,
        )
        self.Miner.miner_until_tx_committed(
            self.cluster.ckb_nodes[0], js_code_deploy_hash, with_unknown=True
        )
        js_code_hash = self.Contract.get_ckb_contract_codehash(
            js_code_deploy_hash, 0, True, self.cluster.ckb_nodes[0].getClient().url
        )
        account1 = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.MINER_PRIVATE_1
        )
        pending_tx = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_2,
            account1["address"]["testnet"],
            100,
            self.cluster.ckb_nodes[0].getClient().url,
            "1500",
        )

        js_code_hash = js_code_hash.replace("0x", "")
        tx_hash = self.Contract.invoke_ckb_contract(
            account_private=self.Config.ACCOUNT_PRIVATE_1,
            contract_out_point_tx_hash=self.ckb_js_vm_deploy_hash,
            contract_out_point_tx_index=0,
            type_script_arg=f"0x0000{js_code_hash}01",
            data="0x1234",
            hash_type="data2",
            api_url=self.cluster.ckb_nodes[0].getClient().url,
            cell_deps=[
                {"tx_hash": js_code_deploy_hash, "index": "0x0"},
                {"tx_hash": pending_tx, "index": "0x0"},
            ],
        )
        print("pending_tx:", pending_tx)
        print("send tx:", tx_hash)
        self.cluster.ckb_nodes[0].getClient().get_transaction(tx_hash)
        time.sleep(10)
        self.Miner.miner_until_tx_committed(
            self.cluster.ckb_nodes[0], tx_hash, with_unknown=True
        )
        self.Node.wait_cluster_height(
            self.cluster,
            self.cluster.ckb_nodes[0].getClient().get_tip_block_number(),
            100,
        )
        self.node_new = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.v206,
            "cluster1/hardFork/node{i}".format(i=5),
            8114 + 5,
            8225 + 5,
        )
        self.node_new.prepare(
            other_ckb_config={"ckb_logger_filter": "info,ckb_script=debug"}
        )
        self.node_new.start()
        self.node_new.connected(self.cluster.ckb_nodes[0])
        self.Node.wait_node_height(
            self.node_new,
            self.cluster.ckb_nodes[0].getClient().get_tip_block_number(),
            100,
        )
