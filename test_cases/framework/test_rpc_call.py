import json
from unittest.mock import Mock

import pytest
import requests

from framework.rpc import RPCClient


@pytest.fixture
def rpc(monkeypatch):
    post = Mock(return_value=Mock(json=lambda: {"result": "ok"}))
    sleep = Mock()
    monkeypatch.setattr("framework.rpc.requests.post", post)
    monkeypatch.setattr("framework.rpc.time.sleep", sleep)
    return RPCClient("http://127.0.0.1:8114"), post, sleep


def test_default_call_keeps_logging_and_request_format(rpc, capsys):
    client, post, _ = rpc
    assert client.call("tx_pool_info", []) == "ok"
    kwargs = post.call_args.kwargs
    assert "timeout" not in kwargs
    assert json.loads(kwargs["data"]) == {
        "id": 42,
        "jsonrpc": "2.0",
        "method": "tx_pool_info",
        "params": [],
    }
    output = capsys.readouterr().out
    assert "request:url:" in output
    assert "response:" in output


def test_default_connection_retry_is_preserved(rpc):
    client, post, sleep = rpc
    response = post.return_value
    post.side_effect = [requests.ConnectionError("busy"), response]
    assert client.call("tx_pool_info", []) == "ok"
    assert post.call_count == 2
    sleep.assert_called_once_with(2)


def test_timeout_and_quiet_mode_are_forwarded(rpc, capsys):
    client, post, sleep = rpc
    assert client.call("tx_pool_info", [], timeout=0.25, verbose=False) == "ok"
    assert post.call_args.kwargs["timeout"] == 0.25
    assert capsys.readouterr().out == ""
    sleep.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectionError("offline"),
        requests.ConnectTimeout("connect timeout"),
        requests.ReadTimeout("read timeout"),
    ],
)
def test_single_attempt_propagates_network_error_without_sleep(rpc, capsys, error):
    client, post, sleep = rpc
    post.side_effect = error
    with pytest.raises(type(error)) as caught:
        client.call("tx_pool_info", [], try_count=1, timeout=1, verbose=False)
    assert caught.value is error
    assert post.call_count == 1
    sleep.assert_not_called()
    assert capsys.readouterr().out == ""


def test_quiet_mode_still_raises_rpc_error(rpc, capsys):
    client, post, sleep = rpc
    post.return_value = Mock(json=lambda: {"error": {"message": "rejected"}})
    with pytest.raises(Exception, match="Error: rejected"):
        client.call("send_transaction", [{}], timeout=1, verbose=False)
    assert post.call_count == 1
    sleep.assert_not_called()
    assert capsys.readouterr().out == ""


def test_quiet_mode_preserves_explicit_retry_count(rpc, capsys):
    client, post, sleep = rpc
    post.side_effect = requests.ConnectionError("offline")
    with pytest.raises(Exception, match="request time out"):
        client.call("tx_pool_info", [], try_count=3, verbose=False)
    assert post.call_count == 3
    assert sleep.call_count == 3
    assert capsys.readouterr().out == ""
