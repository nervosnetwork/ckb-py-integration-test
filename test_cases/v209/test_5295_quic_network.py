import os
import shutil
import socket
import time

from framework.basic import CkbTest
from framework.test_node import CkbNodeConfigPath
from framework.util import get_ckb_configs, get_project_root


PR_BINARY = os.getenv(
    "CKB_PR5295_BINARY", "/Users/xue/nervosnetwork/ckb/target/debug/ckb"
)


def _unused_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _unused_tcp_ports(count):
    ports = set()
    while len(ports) < count:
        ports.add(_unused_tcp_port())
    return list(ports)


def _install_pr_binary():
    root = get_project_root()
    target_dir = os.path.join(root, "download", "pr5295")
    os.makedirs(target_dir, exist_ok=True)
    shutil.copy2(PR_BINARY, os.path.join(target_dir, "ckb"))
    shutil.copy2(
        os.path.join(root, "download", "current", "ckb-cli"),
        os.path.join(target_dir, "ckb-cli"),
    )
    return CkbNodeConfigPath(
        "source/template/ckb/v209/ckb.toml.j2",
        "source/template/ckb/v209/ckb-miner.toml.j2",
        "source/template/ckb/v209/specs/dev.toml",
        "download/pr5295",
    )


def _quic_address(node):
    info = node.getClient().local_node_info()
    for item in info["addresses"]:
        address = item["address"].replace("0.0.0.0", "127.0.0.1")
        if "/udp/" in address and "/quic-v1" in address:
            return address
    raise AssertionError("node did not advertise a quic-v1 address: {}".format(info))


def _tcp_address(node):
    info = node.getClient().local_node_info()
    for item in info["addresses"]:
        address = item["address"].replace("0.0.0.0", "127.0.0.1")
        if "/tcp/" in address and "/ws" not in address:
            return address
    raise AssertionError("node did not advertise a tcp address: {}".format(info))


def _wait_connected(node, expected_peer_id, timeout=30):
    started_at = time.time()
    last_peers = []
    while time.time() - started_at < timeout:
        last_peers = node.getClient().get_peers()
        if any(peer["node_id"] == expected_peer_id for peer in last_peers):
            return last_peers
        time.sleep(1)
    raise AssertionError(
        "timeout waiting for {} to connect; last peers: {}".format(
            expected_peer_id, last_peers
        )
    )


def _wait_peer_address_seen(observer, expected_peer_id, address_fragment, timeout=60):
    started_at = time.time()
    last_peers = []
    while time.time() - started_at < timeout:
        last_peers = observer.getClient().get_peers()
        for peer in last_peers:
            if peer["node_id"] != expected_peer_id:
                continue
            advertised_addresses = [
                item["address"] for item in peer.get("addresses", [])
            ]
            if any(address_fragment in address for address in advertised_addresses):
                return peer
        time.sleep(1)
    raise AssertionError(
        "timeout waiting for {} to advertise {}; last peers: {}".format(
            expected_peer_id, address_fragment, last_peers
        )
    )


def _wait_discovered_connection(dialer, expected_peer_id, address_fragment, timeout=90):
    started_at = time.time()
    last_peers = []
    while time.time() - started_at < timeout:
        last_peers = dialer.getClient().get_peers()
        for peer in last_peers:
            if peer["node_id"] != expected_peer_id:
                continue
            connected_addresses = [
                item["address"] for item in peer.get("addresses", [])
            ]
            if any(address_fragment in address for address in connected_addresses):
                return peer
        time.sleep(1)
    raise AssertionError(
        "timeout waiting for {} to be discovered over {}; last peers: {}".format(
            expected_peer_id, address_fragment, last_peers
        )
    )


def _connect_by_quic(dialer, target):
    target_peer_id = target.get_peer_id()
    target_quic_addr = _quic_address(target)
    dialer.getClient().add_node(target_peer_id, target_quic_addr)
    return _wait_connected(dialer, target_peer_id)


def _connect_by_tcp(dialer, target):
    target_peer_id = target.get_peer_id()
    target_tcp_addr = _tcp_address(target)
    dialer.getClient().add_node(target_peer_id, target_tcp_addr)
    return _wait_connected(dialer, target_peer_id)


def _mine_and_wait(source, followers, target_height, timeout=60):
    CkbTest.Miner.make_tip_height_number(source, target_height)
    for follower in followers:
        CkbTest.Node.wait_node_height(follower, target_height, timeout)


class TestQuicNetwork5295(CkbTest):
    """
    https://github.com/nervosnetwork/ckb/pull/5295

    PR 5295 enables tentacle QUIC for CKB. The regression coverage verifies
    that a quic-v1 multiaddr can establish a peer connection and sync, and that
    a TCP-only node still connects and receives relayed blocks in the same
    local dev network.
    """

    @classmethod
    def setup_class(cls):
        config_path = _install_pr_binary()
        rpc_a, p2p_a, rpc_b, p2p_b, rpc_c, p2p_c = _unused_tcp_ports(6)

        dual_config, dual_miner_config, dual_spec_config = get_ckb_configs(p2p_a, rpc_a)
        dual_config["ckb_network_listen_addresses"] = [
            "/ip4/0.0.0.0/tcp/{}".format(p2p_a),
            "/ip4/0.0.0.0/udp/{}/quic-v1".format(p2p_a),
        ]
        dual_config["ckb_network_public_addresses"] = [
            "/ip4/127.0.0.1/tcp/{}".format(p2p_a),
            "/ip4/127.0.0.1/udp/{}/quic-v1".format(p2p_a),
        ]

        quic_config, quic_miner_config, quic_spec_config = get_ckb_configs(p2p_b, rpc_b)
        quic_config["ckb_network_listen_addresses"] = [
            "/ip4/0.0.0.0/udp/{}/quic-v1".format(p2p_b)
        ]
        quic_config["ckb_network_public_addresses"] = [
            "/ip4/127.0.0.1/udp/{}/quic-v1".format(p2p_b)
        ]

        tcp_config, tcp_miner_config, tcp_spec_config = get_ckb_configs(p2p_c, rpc_c)
        tcp_config["ckb_network_listen_addresses"] = [
            "/ip4/0.0.0.0/tcp/{}".format(p2p_c)
        ]
        tcp_config["ckb_network_public_addresses"] = [
            "/ip4/127.0.0.1/tcp/{}".format(p2p_c)
        ]

        cls.source = cls.CkbNode(
            config_path,
            "v209/pr5295_quic_network/source",
            dual_config,
            dual_miner_config,
            dual_spec_config,
        )
        cls.quic_follower = cls.CkbNode(
            config_path,
            "v209/pr5295_quic_network/quic_follower",
            quic_config,
            quic_miner_config,
            quic_spec_config,
        )
        cls.tcp_follower = cls.CkbNode(
            config_path,
            "v209/pr5295_quic_network/tcp_follower",
            tcp_config,
            tcp_miner_config,
            tcp_spec_config,
        )
        cls.cluster = cls.Cluster([cls.source, cls.quic_follower, cls.tcp_follower])
        cls.cluster.prepare_all_nodes()
        cls.cluster.start_all_nodes()

    @classmethod
    def teardown_class(cls):
        if hasattr(cls, "cluster"):
            cls.cluster.stop_all_nodes()
            cls.cluster.clean_all_nodes()

    def test_quic_address_connects_and_tcp_only_node_still_syncs(self):
        source = self.source
        quic_follower = self.quic_follower
        tcp_follower = self.tcp_follower

        self.Miner.make_tip_height_number(source, 1)

        source_peer_id = source.get_peer_id()
        source_quic_addr = _quic_address(source)
        source_tcp_addr = _tcp_address(source)

        _connect_by_quic(quic_follower, source)
        _connect_by_tcp(tcp_follower, source)

        _mine_and_wait(source, [quic_follower, tcp_follower], 3)

        assert "/quic-v1" in source_quic_addr
        assert "/tcp/" in source_tcp_addr
        quic_addresses = quic_follower.getClient().local_node_info()["addresses"]
        assert any("/quic-v1" in item["address"] for item in quic_addresses)
        assert all(
            "/quic-v1" not in item["address"]
            for item in tcp_follower.getClient().local_node_info()["addresses"]
        )

        self.did_pass = True

    def test_quic_listen_addr_is_advertised_and_discovered_peer_connects(self):
        source = self.source
        quic_follower = self.quic_follower
        tcp_follower = self.tcp_follower

        self.Miner.make_tip_height_number(source, 1)

        _connect_by_quic(quic_follower, source)
        _connect_by_tcp(tcp_follower, source)

        quic_follower_peer_id = quic_follower.get_peer_id()
        source_peer_id = source.get_peer_id()

        source_observed_quic_peer = _wait_peer_address_seen(
            source, quic_follower_peer_id, "/quic-v1"
        )
        print(
            "source observed quic follower advertised addresses:",
            source_observed_quic_peer.get("addresses", []),
        )

        discovered_peer = _wait_discovered_connection(
            tcp_follower, quic_follower_peer_id, "/quic-v1"
        )
        print(
            "tcp follower discovered and connected quic peer:",
            discovered_peer.get("addresses", []),
        )

        assert any(
            peer["node_id"] == source_peer_id
            for peer in tcp_follower.getClient().get_peers()
        )
        assert any(
            "/ip4/127.0.0.1/udp/" in item["address"] and "/quic-v1" in item["address"]
            for item in discovered_peer.get("addresses", [])
        )

        target_height = source.getClient().get_tip_block_number() + 1
        _mine_and_wait(source, [quic_follower, tcp_follower], target_height)

        self.did_pass = True

    def test_quic_follower_restart_reconnects_and_resyncs(self):
        source = self.source
        quic_follower = self.quic_follower

        quic_follower.stop()
        quic_follower.start()

        restarted_info = quic_follower.getClient().local_node_info()
        assert any(
            "/quic-v1" in item["address"] for item in restarted_info["addresses"]
        )

        _connect_by_quic(quic_follower, source)

        target_height = source.getClient().get_tip_block_number() + 2
        _mine_and_wait(source, [quic_follower], target_height)

        assert quic_follower.get_connected_count() >= 1
        self.did_pass = True

    def test_quic_reconnect_sync_smoke_records_timings(self):
        source = self.source
        quic_follower = self.quic_follower
        measurements = []

        for round_index in range(3):
            quic_follower.stop()
            quic_follower.start()

            connect_started_at = time.time()
            _connect_by_quic(quic_follower, source)
            connect_seconds = time.time() - connect_started_at

            target_height = source.getClient().get_tip_block_number() + 1
            sync_started_at = time.time()
            _mine_and_wait(source, [quic_follower], target_height)
            sync_seconds = time.time() - sync_started_at

            measurements.append(
                {
                    "round": round_index + 1,
                    "connect_seconds": round(connect_seconds, 3),
                    "sync_seconds": round(sync_seconds, 3),
                    "target_height": target_height,
                }
            )

        print("PR5295 QUIC reconnect/sync smoke measurements:", measurements)

        assert all(item["connect_seconds"] < 30 for item in measurements)
        assert all(item["sync_seconds"] < 60 for item in measurements)
        self.did_pass = True
