from unittest.mock import Mock, call

import pytest

from framework.test_cluster import Cluster


def disconnected_nodes(count):
    nodes = [Mock() for _ in range(count)]
    for node in nodes:
        node.getClient.return_value.get_peers.return_value = []
    return nodes


def test_connect_does_not_wait_for_ibd_peers_before_the_caller_can_mine():
    first, second = disconnected_nodes(2)
    Cluster([first, second]).connected_node(0, 1)
    first.connected.assert_called_once_with(second)
    second.connected.assert_not_called()
    for node in (first, second):
        node.getClient.return_value.get_peers.assert_not_called()


def test_connect_propagates_rpc_errors():
    first, second = disconnected_nodes(2)
    first.connected.side_effect = ConnectionError("RPC unavailable")
    with pytest.raises(ConnectionError, match="RPC unavailable"):
        Cluster([first, second]).connected_node(0, 1)


def test_full_mesh_requests_each_distinct_pair_once():
    first, second, third, fourth = disconnected_nodes(4)
    Cluster([first, second, third, fourth]).connected_all_nodes()
    assert first.connected.call_args_list == [call(second), call(third), call(fourth)]
    assert second.connected.call_args_list == [call(third), call(fourth)]
    third.connected.assert_called_once_with(fourth)
    fourth.connected.assert_not_called()


def test_adding_a_node_only_connects_it_to_existing_nodes():
    first, second, third = disconnected_nodes(3)
    cluster = Cluster([first, second])
    cluster.add_node(third)
    assert cluster.ckb_nodes == [first, second, third]
    first.connected.assert_called_once_with(third)
    second.connected.assert_called_once_with(third)
    third.connected.assert_not_called()


def test_restart_requests_the_topology_only_once():
    first, second = disconnected_nodes(2)
    Cluster([first, second]).restart_all_node(clean_data=True)
    first.restart.assert_called_once_with(clean_data=True)
    second.restart.assert_called_once_with(clean_data=True)
    first.connected.assert_called_once_with(second)
    second.connected.assert_not_called()
