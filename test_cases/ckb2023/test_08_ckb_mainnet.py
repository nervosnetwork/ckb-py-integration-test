import time

from framework.basic import CkbTest
from framework.helper.spawn_contract import SpawnContract

# https://github.com/nervosnetwork/ckb/pull/4807
# pub const CKB2023_START_EPOCH: u64 = 12_293;


class CKBTestnet(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. star 4 node in tmp/cluster/hardFork dir
        2. link ckb node each other
        Returns:

        """

        # 1. star 4 node in tmp/cluster/hardFork dir
        nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_MAIN,
                "cluster/hardFork/node{i}".format(i=i),
                8114 + i,
                8225 + i,
            )
            for i in range(1, 5)
        ]
        cls.cluster = cls.Cluster(nodes)
        cls.cluster.prepare_all_nodes(
            other_ckb_spec_config={
                "ckb_params_genesis_epoch_length": "1",
                "ckb_name": "ckb",
            }
        )
        cls.cluster.start_all_nodes()

        # 2. link ckb node each other
        cls.cluster.connected_all_nodes()

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestClass1")
        cls.cluster.stop_all_nodes()
        cls.cluster.clean_all_nodes()

    def test_02(self):
        """
        1. generate_epochs(hex(12_293))
        2. get_consensus(0048) == 12_293
        3. get_consensus(0049) == 12_293
        4. miner with 0x1
        5. transfer data2
        6. invoke spawn
        Returns:

        """
        self.cluster.ckb_nodes[0].getClient().get_consensus()
        self.Miner.make_tip_height_number(self.cluster.ckb_nodes[0], 12_293)
        # generate_epochs will cause HeadersIsInvalid
        # self.cluster.ckb_nodes[0].client.generate_epochs(hex(12_293))

        tip_number = self.cluster.ckb_nodes[0].client.get_tip_block_number()
        print("tip number:", tip_number)
        consensus = self.cluster.ckb_nodes[0].getClient().get_consensus()
        res = get_epoch_number_by_consensus_response(consensus, "0048")
        assert res == 12_293
        res = get_epoch_number_by_consensus_response(consensus, "0049")
        assert res == 12_293
        time.sleep(5)
        # 0048 miner with other version block
        for i in range(20):
            self.Miner.miner_with_version(self.cluster.ckb_nodes[0], "0x1")

        # transfer data2
        # 2. send tx contains data2
        # send account 1 transfer data2
        # @ckb-lumos/helpers.encodeToAddress(
        #     {
        #         hashType:"data2",
        #         args:"0x",
        #         codeHash:"0x69c80d6a8104994bddc132bb568c953d60fae0ac928ad887c96de8434ca2a790"
        #     }
        # )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            "ckb1qp5usrt2syzfjj7acyetk45vj57kp7hq4jfg4ky8e9k7ss6v52neqpq2cmcdt",
            140,
            self.cluster.ckb_nodes[0].client.url,
        )

        # 3. miner until tx committed
        self.Miner.miner_with_version(self.cluster.ckb_nodes[0], "0x0")
        self.Miner.miner_until_tx_committed(self.cluster.ckb_nodes[0], tx_hash)
        # spawn
        spawn = SpawnContract()
        spawn.deploy(self.Config.MINER_PRIVATE_1, self.cluster.ckb_nodes[0])
        code_tx_hash, code_tx_index = spawn.get_deploy_hash_and_index()
        invoke_arg, invoke_data = spawn.get_arg_and_data("demo")
        tx_hash = self.Contract.invoke_ckb_contract(
            self.Config.MINER_PRIVATE_1,
            code_tx_hash,
            code_tx_index,
            invoke_arg,
            "data2",
            invoke_data,
            api_url=self.cluster.ckb_nodes[0].getClient().url,
        )
        self.Miner.miner_until_tx_committed(self.cluster.ckb_nodes[0], tx_hash)
        tip_number = self.cluster.ckb_nodes[0].getClient().get_tip_block_number()
        self.Node.wait_cluster_height(self.cluster, tip_number, 250)


def get_epoch_number_by_consensus_response(consensus_response, rfc_name):
    """
    get ckb epoch number
    "hardfork_features": [
            { "rfc": "0028", "epoch_number": "0x1526" },
         ]
    Example:
    get_epoch_number_by_consensus_response(consensus_response,"0028")
    return int(0x1526,16)
    :param consensus_response:  rpc get_consensus response
    :param rfc_name: example : 0048
    :return:
    """
    hardfork_features = consensus_response["hardfork_features"]
    return int(
        list(filter(lambda obj: rfc_name in obj["rfc"], hardfork_features))[0][
            "epoch_number"
        ].replace("0x", ""),
        16,
    )
