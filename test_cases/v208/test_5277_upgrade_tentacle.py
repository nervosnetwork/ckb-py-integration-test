import time

from framework.basic import CkbTest


class TestUpgradeTentacle5277(CkbTest):
    """
    https://github.com/nervosnetwork/ckb/pull/5277

    PR 5277 upgrades tentacle and teaches CKB's TCP-only hole punching filters
    about the new QuicV1 transport. The integration coverage focuses on local
    TCP networking still working after the dependency upgrade and on unsupported
    quic-v1 multiaddrs not taking the node down when they are submitted through
    the public network RPC.
    """

    @classmethod
    def setup_class(cls):
        nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                "v208/upgrade_tentacle_5277/node{i}".format(i=i),
                20814 + i,
                20825 + i,
            )
            for i in range(0, 2)
        ]
        cls.cluster = cls.Cluster(nodes)
        cls.cluster.prepare_all_nodes()
        cls.cluster.start_all_nodes()
        cls.cluster.connected_node(0, 1)
        cls._wait_peer_connected()

    @classmethod
    def teardown_class(cls):
        if hasattr(cls, "cluster"):
            cls.cluster.stop_all_nodes()
            cls.cluster.clean_all_nodes()

    @classmethod
    def _wait_peer_connected(cls, timeout=30):
        started_at = time.time()
        while time.time() - started_at < timeout:
            connected_counts = [
                node.get_connected_count() for node in cls.cluster.ckb_nodes
            ]
            if all(count >= 1 for count in connected_counts):
                return
            time.sleep(1)
        raise Exception("timeout waiting for v208 nodes to connect")

    def test_tcp_peer_connection_and_sync_state_after_tentacle_upgrade(self):
        """
        1. Start two v0.208.0-rc0 dev nodes.
        2. Connect them over their advertised TCP multiaddr.
        3. Generate blocks on node0 and wait for node1 to sync.
        4. Check sync_state remains available after the tentacle upgrade.
        """
        node0, node1 = self.cluster.ckb_nodes

        node0_info = node0.getClient().local_node_info()
        node1_info = node1.getClient().local_node_info()
        assert int(node0_info["connections"], 16) >= 1
        assert int(node1_info["connections"], 16) >= 1
        assert all("/tcp/" in item["address"] for item in node0_info["addresses"])
        assert all("/tcp/" in item["address"] for item in node1_info["addresses"])

        self.Miner.make_tip_height_number(node0, 5)
        self.Node.wait_node_height(node1, 5, 60)

        sync_state = node1.getClient().sync_state()
        assert sync_state["assume_valid_target_reached"] is True
        assert sync_state["min_chain_work_reached"] is True
        self.did_pass = True

    def test_quic_v1_multiaddr_is_rejected_without_crashing_node(self):
        """
        1. Submit a quic-v1 multiaddr to add_node.
        2. The address may be rejected because CKB hole punching is TCP-only.
        3. The node must stay alive and keep the existing TCP peer connection.
        """
        node0, node1 = self.cluster.ckb_nodes
        peer_id = node1.get_peer_id()
        peer_address = node1.get_peer_address()
        peer_port = peer_address.split("/tcp/")[-1].split("/")[0]
        quic_v1_address = "/ip4/127.0.0.1/udp/{}/quic-v1".format(peer_port)

        try:
            node0.getClient().add_node(peer_id, quic_v1_address)
        except Exception as error:
            assert "quic" in str(error).lower() or "multiaddr" in str(error).lower()

        node0_info = node0.getClient().local_node_info()
        assert int(node0_info["connections"], 16) >= 1
        node0.getClient().ping_peers()
        self.did_pass = True
