from unittest.mock import Mock, call, patch

import pytest

from framework.test_cluster import Cluster


def nodes_with_peers(*identities):
    nodes = [Mock() for _ in identities]
    for node, identity in zip(nodes, identities):
        node.get_peer_id.return_value = identity
        node.getClient.return_value.get_peers.return_value = [
            {"node_id": other} for other in identities if other != identity
        ]
    return nodes


def test_one_duplex_connection_waits_for_both_peer_identities():
    first, second = nodes_with_peers("first", "second")
    second.getClient.return_value.get_peers.side_effect = [
        [{"node_id": "unrelated"}],
        [{"node_id": "first"}],
    ]
    with patch("framework.test_cluster.time.sleep"):
        Cluster([first, second]).connected_node(0, 1)
    first.connected.assert_called_once_with(second)
    second.connected.assert_not_called()
    assert second.getClient.return_value.get_peers.call_count == 2


def test_an_unrelated_peer_does_not_complete_the_connection():
    first, second = nodes_with_peers("first", "second")
    first.getClient.return_value.get_peers.return_value = [{"node_id": "unrelated"}]
    with patch("framework.test_cluster.time.monotonic", side_effect=[0, 0, 11]), patch(
        "framework.test_cluster.time.sleep"
    ), pytest.raises(TimeoutError, match="Peer connection did not complete"):
        Cluster([first, second]).connected_node(0, 1)


def test_full_mesh_requests_each_distinct_pair_once():
    first, second, third = nodes_with_peers("first", "second", "third")
    Cluster([first, second, third]).connected_all_nodes()
    assert first.connected.call_args_list == [call(second), call(third)]
    second.connected.assert_called_once_with(third)
    third.connected.assert_not_called()
