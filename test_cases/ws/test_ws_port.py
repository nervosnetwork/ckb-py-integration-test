import time


from framework.basic import CkbTest


class TestWs(CkbTest):

    @classmethod
    def setup_class(cls):
        nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"cluster1/node{i}",
                8114 + i,
                8225 + i,
            )
            for i in range(3)
        ]

        cls.cluster = cls.Cluster(nodes)
        for i, node in enumerate(nodes):
            node.prepare(
                other_ckb_config={
                    "ckb_network_listen_addresses": [
                        f"/ip4/0.0.0.0/tcp/{8225 + i}/ws",
                        f"/ip4/0.0.0.0/tcp/{8225 + i}",
                    ],
                    "ckb_logger_filter": "debug",
                }
            )

        cls.cluster.start_all_nodes()
        source, *followers = nodes
        cls.Miner.make_tip_height_number(source, 10)
        source.start_miner()
        for follower in followers:
            follower.connected_ws(source)
        cls.Node.wait_cluster_height(cls.cluster, 10, 1000)

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestClass1")
        cls.cluster.stop_all_nodes()
        cls.cluster.clean_all_nodes()

    def test_node_info(self):
        """
        get node info
            - ws address is in addresses
        Returns:

        """
        node_info = self.cluster.ckb_nodes[0].getClient().local_node_info()
        print("node_info:", node_info)
        assert "ws" in node_info["addresses"][1]["address"]

    def test_get_peers(self):
        """
        get peers
            - ws address is in addresses
        Returns:

        """
        peers = self.cluster.ckb_nodes[0].getClient().get_peers()
        assert "ws" in str(peers)

        peers = self.cluster.ckb_nodes[1].getClient().get_peers()
        assert "ws" in str(peers)

    def test_send_tx(self):
        """
            1. node2 send tx
            2. node0 miner tx
            3. node0 send tx
            4. node2 miner tx
        Returns:
        """
        account = self.Ckb_cli.util_key_info_by_private_key(
            account_private=self.Config.ACCOUNT_PRIVATE_2
        )
        # 1. node2 send tx
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            140,
            self.cluster.ckb_nodes[2].getClient().url,
        )
        # 2. node0 miner tx
        self.Miner.miner_until_tx_committed(self.cluster.ckb_nodes[0], tx_hash, True)
        self.cluster.ckb_nodes[0].stop_miner()

        # 3. node0 send tx
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            140,
            self.cluster.ckb_nodes[0].getClient().url,
        )
        # 4. node2 miner tx
        self.Miner.miner_until_tx_committed(self.cluster.ckb_nodes[2], tx_hash, True)
        tip_number = self.cluster.ckb_nodes[2].getClient().get_tip_block_number()
        self.Node.wait_node_height(self.cluster.ckb_nodes[0], tip_number, 1000)
        self.cluster.ckb_nodes[0].start_miner()

    def test_link_tcp(self):
        """
        1. new_node start with tcp
        2. new_node link node0 tcp
        3. new_node send tx
        4. node0 miner tx
        5. node0 send tx
        6. new_node miner tx
        Returns:

        """

        # 1. new_node start with tcp
        new_node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST, "cluster1/node_new1", 8134, 8236
        )
        new_node.prepare(
            other_ckb_config={
                "ckb_network_listen_addresses": [
                    f"/ip4/0.0.0.0/tcp/8229",
                    f"/ip4/0.0.0.0/tcp/8299/ws",
                ],
                "ckb_logger_filter": "debug",
            }
        )
        new_node.start()
        self.cluster.ckb_nodes.append(new_node)

        # 2. new_node link node0 tcp
        peer_id = self.cluster.ckb_nodes[0].get_peer_id()
        node_info = self.cluster.ckb_nodes[0].client.local_node_info()
        peer_address = node_info["addresses"][-1]["address"].replace(
            "0.0.0.0", "127.0.0.1"
        )
        print(
            "add node response:", new_node.getClient().add_node(peer_id, peer_address)
        )
        tip_number = self.cluster.ckb_nodes[0].getClient().get_tip_block_number()
        self.Node.wait_node_height(new_node, tip_number, 1000)

        # 3. new_node send tx
        account = self.Ckb_cli.util_key_info_by_private_key(
            account_private=self.Config.ACCOUNT_PRIVATE_2
        )
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            140,
            new_node.getClient().url,
        )

        # 4. node0 miner tx
        self.Miner.miner_until_tx_committed(self.cluster.ckb_nodes[0], tx_hash, True)
        self.cluster.ckb_nodes[0].stop_miner()

        # 5. node0 send tx
        tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1,
            account["address"]["testnet"],
            140,
            self.cluster.ckb_nodes[0].getClient().url,
        )

        # 6. new_node miner tx
        self.Miner.miner_until_tx_committed(new_node, tx_hash, True)
        tip_number = new_node.getClient().get_tip_block_number()
        self.Node.wait_node_height(self.cluster.ckb_nodes[0], tip_number, 1000)
        self.cluster.ckb_nodes[0].start_miner()

    def test_remove_node(self):
        """
        1. node1 remove node0
            remove success
        2. get_peers
            return []
        Returns:

        """
        # 1. node1 remove node0
        self.cluster.ckb_nodes[1].getClient().remove_node(
            self.cluster.ckb_nodes[0].get_peer_id()
        )
        time.sleep(3)
        tip_number1 = self.cluster.ckb_nodes[1].getClient().get_tip_block_number()
        time.sleep(5)
        tip_number2 = self.cluster.ckb_nodes[1].getClient().get_tip_block_number()
        assert tip_number1 == tip_number2

        # 2. get_peers
        peers = self.cluster.ckb_nodes[1].getClient().get_peers()
        assert peers == []

    def test_ckb_network_reuse_tcp_with_ws(self):
        # 1. new_node start with tcp
        new_node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST, "cluster1/node_new", 8134, 8236
        )
        new_node.prepare(
            other_ckb_config={
                "ckb_network_listen_addresses": [
                    f"/ip4/0.0.0.0/tcp/8299/ws",
                ],
                "ckb_network_reuse_tcp_with_ws": "true",
            }
        )
        new_node.start()
        self.cluster.ckb_nodes.append(new_node)
        local_node_info = new_node.getClient().local_node_info()
        assert "/ip4/0.0.0.0/tcp/8299/ws" in local_node_info["addresses"][0]["address"]
        assert len(local_node_info["addresses"]) == 1
