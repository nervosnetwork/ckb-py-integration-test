import time

from framework.basic import CkbTest


class TestV210NetworkAndRpcRegressions(CkbTest):
    """Binary regressions for CKB PRs 5309, 5314, and 5324."""

    @classmethod
    def setup_class(cls):
        nodes = [
            cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                "v210/network_rpc_regressions/node{}".format(index),
                21014 + index,
                21025 + index,
            )
            for index in range(2)
        ]
        cls.cluster = cls.Cluster(nodes)
        cls.cluster.prepare_all_nodes()
        cls.cluster.start_all_nodes()

        source, follower = cls.cluster.ckb_nodes
        cls.Miner.make_tip_height_number(source, 24)
        follower.connected(source)
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
            if all(node.get_connected_count() >= 1 for node in cls.cluster.ckb_nodes):
                return
            time.sleep(1)
        raise AssertionError("timed out waiting for the V210 nodes to connect")

    def test_header_sync_and_http_rpc_remain_healthy(self):
        """Exercise valid GetHeaders synchronization and the HTTP RPC stack."""
        source, follower = self.cluster.ckb_nodes
        self.Node.wait_node_height(follower, 24, 60)

        assert source.getClient().get_tip_block_number() == 24
        assert follower.getClient().get_tip_block_number() == 24

        for node in self.cluster.ckb_nodes:
            for _ in range(10):
                info = node.getClient().local_node_info()
                assert int(info["connections"], 16) >= 1
                assert node.getClient().get_tip_block_number() == 24

        sync_state = follower.getClient().sync_state()
        assert sync_state["assume_valid_target_reached"] is True
        assert sync_state["min_chain_work_reached"] is True
        self.did_pass = True

    def test_unsupported_quic_v1_does_not_break_tcp_peer(self):
        """Keep the established TCP path healthy after an invalid QUIC request."""
        source, follower = self.cluster.ckb_nodes
        peer_id = follower.get_peer_id()
        peer_address = follower.get_peer_address()
        peer_port = peer_address.split("/tcp/")[-1].split("/")[0]
        quic_v1_address = "/ip4/127.0.0.1/udp/{}/quic-v1".format(peer_port)

        try:
            source.getClient().add_node(peer_id, quic_v1_address)
        except Exception as error:
            message = str(error).lower()
            assert "quic" in message or "multiaddr" in message

        assert source.get_connected_count() >= 1
        source.getClient().ping_peers()
        assert source.getClient().get_tip_block_number() == 24
        self.did_pass = True
