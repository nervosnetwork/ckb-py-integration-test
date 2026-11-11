"""真实 ckb-cli API 服务的认证与资金安全回归。"""

import json
import socket
import subprocess
import time

import pytest
import requests

from suites.ckb_cli.tests.cli_api_support import (
    ADDRESS,
    AUTH_TOKEN,
    SENDER_ADDRESS,
    confirm_transaction,
    dev_node,
    rpc,
    server_process,
    wait_for_rpc,
)


GOOD_HEADERS = {"Authorization": f"Bearer {AUTH_TOKEN}"}
AMOUNT = 6100000000
TRANSFER = {
    "capacity": AMOUNT,
    "fee_rate": 1000,
    "to_address": ADDRESS,
    "force_small_change_as_fee": "0.001",
}


def capacity(api_url, address, request_id):
    response = rpc(
        api_url,
        "get_capacity_by_address",
        [address],
        request_id,
        headers=GOOD_HEADERS,
    )
    assert "error" not in response, response
    result = response["result"]
    assert set(result) == {"total", "immature", "dao"}, result
    assert all(type(value) is int and value >= 0 for value in result.values()), result
    return result


def empty_pool(node_url, request_id):
    response = rpc(node_url, "tx_pool_info", [], request_id)
    assert "error" not in response, response
    pool = response["result"]
    sizes = {
        field: int(pool[field], 16)
        for field in ("pending", "proposed", "verify_queue_size")
    }
    assert sizes == {"pending": 0, "proposed": 0, "verify_queue_size": 0}, pool
    return sizes


def mine_and_sync(node_url, count=5):
    deadline = time.monotonic() + 30
    last_tip = None
    for block_index in range(count):
        block = rpc(node_url, "generate_block", [], 600 + block_index)
        assert "error" not in block, block
        last_tip = block["result"]
        while time.monotonic() < deadline:
            pool = rpc(node_url, "tx_pool_info", [], 650 + block_index)
            assert "error" not in pool, pool
            if pool["result"]["tip_hash"] == last_tip:
                break
            time.sleep(0.1)
        else:
            raise AssertionError(f"Node did not reach generated block {last_tip}")
    assert last_tip is not None
    tip_number = int(pool["result"]["tip_number"], 16)
    while time.monotonic() < deadline:
        indexer = rpc(node_url, "get_indexer_tip", [], 700)
        assert "error" not in indexer, indexer
        result = indexer.get("result")
        if result and int(result["block_number"], 16) >= tip_number:
            return
        time.sleep(0.1)
    raise AssertionError("Indexer did not reach generated blocks")


def transfer_payload(batch=False):
    call = {"jsonrpc": "2.0", "id": 51, "method": "transfer", "params": [TRANSFER]}
    if batch:
        return [
            call,
            {
                "jsonrpc": "2.0",
                "id": 52,
                "method": "get_capacity_by_address",
                "params": [ADDRESS],
            },
        ]
    return call


def transaction_hash(response, batch=False):
    assert response.status_code == 200, response.text
    body = response.json()
    if batch:
        assert isinstance(body, list) and len(body) == 2, body
        by_id = {item["id"]: item for item in body}
        assert set(by_id) == {51, 52}, body
        assert "error" not in by_id[52], body
        response_body = by_id[51]
    else:
        assert isinstance(body, dict), body
        response_body = body
    assert response_body.get("jsonrpc") == "2.0", response_body
    assert response_body.get("id") == 51, response_body
    assert "error" not in response_body, response_body
    value = response_body.get("result")
    assert isinstance(value, str) and value.startswith("0x") and len(value) == 66, body
    return value


def rejects_unauthorized(response):
    assert response.status_code == 401, (response.status_code, response.text)
    assert response.headers.get("WWW-Authenticate") == "Bearer", response.headers
    assert AUTH_TOKEN not in response.text, response.text
    assert AUTH_TOKEN not in str(response.headers), response.headers


def port_is_closed(dev_node):
    with pytest.raises((ConnectionRefusedError, TimeoutError, OSError)):
        with socket.create_connection(("127.0.0.1", dev_node["api_port"]), timeout=1):
            pass


# TEST-MAP: CLI-API-690-01
@pytest.mark.parametrize("with_private_key", [True, False], ids=["wallet", "read_only"])
def test_server_requires_token_for_both_modes(dev_node, with_private_key):
    """
    1. 先用有效 token 启动同一配置，证明节点和服务可用。
    2. 不提供 token 再启动，检查因缺必需参数在限时内退出。
    3. 检查对应端口未提供 API 服务。
    """
    # 1. 有效配置先成功查询，排除节点或私钥无效造成的假阳性。
    with server_process(dev_node, with_private_key=with_private_key) as (
        api_url,
        process,
        _,
        log_path,
    ):
        wait_for_rpc(
            process,
            api_url,
            "get_capacity_by_address",
            [ADDRESS],
            log_path,
            headers=GOOD_HEADERS,
        )
        assert capacity(api_url, ADDRESS, 1) == {"total": 0, "immature": 0, "dao": 0}

    # 2. 省略 token；旧版服务保持运行应使本例失败，不能算正确拒绝。
    with server_process(
        dev_node, with_private_key=with_private_key, send_token=False
    ) as (_, process, _, log_path):
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            exit_code = None
        message = log_path.read_text(errors="replace")
        assert exit_code is not None and exit_code != 0, message
        assert "--auth-token" in message and "required" in message.lower(), message

        # 3. 即使进程退出，也不能已有 API 在目标端口服务。
        port_is_closed(dev_node)


# TEST-MAP: CLI-API-690-02
@pytest.mark.parametrize(
    "bad_token",
    ["", "with space", "line\nbreak", "测试", "\x7f"],
    ids=["empty", "space", "line_break", "non_ascii", "delete"],
)
def test_server_rejects_invalid_token(dev_node, bad_token):
    """
    1. 图形 ASCII 边界字符组成的正常 token 可启动并认证查询。
    2. 对同一有效配置指定非法 token，检查参数校验错误来源。
    3. 检查非法配置没有开放 API 端口。
    """
    # 1. 正常 token 包含 ! 和 ~，证明健康服务及请求可用。
    assert AUTH_TOKEN.startswith("!") and AUTH_TOKEN.endswith("~")
    with server_process(dev_node) as (api_url, process, _, log_path):
        wait_for_rpc(
            process,
            api_url,
            "get_capacity_by_address",
            [ADDRESS],
            log_path,
            headers=GOOD_HEADERS,
        )
        assert capacity(api_url, ADDRESS, 1)["total"] == 0

    # 2. 必须是 auth-token 值的校验问题；旧版未知选项不能通过。
    with server_process(dev_node, token=bad_token, send_token=True) as (
        _,
        process,
        _,
        log_path,
    ):
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            exit_code = None
        message = log_path.read_text(errors="replace")
        assert exit_code is not None and exit_code != 0, message
        assert "--auth-token" in message, message
        if bad_token:
            assert (
                "auth-token must be non-empty printable ASCII without spaces" in message
            )
        else:
            assert "requires a value" in message, message

        # 3. 目标端口必须确实未开放。
        port_is_closed(dev_node)


# TEST-MAP: CLI-API-690-03
@pytest.mark.parametrize(
    "attack",
    [
        "missing",
        "wrong_same_length",
        "suffix",
        "bare_token",
        "web_origin",
        "null_origin",
        "host_origin",
        "batch",
    ],
)
def test_unauthorized_transfer_cannot_move_funds(dev_node, attack):
    """
    1. 建立有资金的钱包、无资金收款地址及空交易池基线。
    2. 用不同未认证 HTTP 形态发送合法转账，检查 401 且无 token 泄露。
    3. 检查交易池未变化，出块并同步 indexer 后双方余额不变。
    4. 同一 API 进程仅补正确认证头，检查相同转账真实确认与余额变化。
    5. 汇总此前错误，检查进程一直存活且 PID 未变。
    """
    # 1. 有效签名账户与业务请求均真实可用，负请求前交易池为空。
    assert SENDER_ADDRESS != ADDRESS
    with server_process(dev_node) as (api_url, process, _, log_path):
        wait_for_rpc(
            process,
            api_url,
            "get_capacity_by_address",
            [ADDRESS],
            log_path,
            headers=GOOD_HEADERS,
        )
        pid = process.pid
        sender_before = capacity(api_url, SENDER_ADDRESS, 1)
        receiver_before = capacity(api_url, ADDRESS, 2)
        assert sender_before["total"] > AMOUNT, sender_before
        assert receiver_before == {"total": 0, "immature": 0, "dao": 0}
        empty_pool(dev_node["node_url"], 3)

        if attack == "wrong_same_length":
            attack_headers = {"Authorization": f"Bearer X{AUTH_TOKEN[1:]}"}
        elif attack == "suffix":
            attack_headers = {"Authorization": f"Bearer {AUTH_TOKEN}x"}
        elif attack == "bare_token":
            attack_headers = {"Authorization": AUTH_TOKEN}
        elif attack == "web_origin":
            attack_headers = {"Origin": "https://example.com"}
        elif attack == "null_origin":
            attack_headers = {"Origin": "null"}
        elif attack == "host_origin":
            hostname = f"attacker.example:{dev_node['api_port']}"
            attack_headers = {"Host": hostname, "Origin": f"http://{hostname}"}
        else:
            attack_headers = {}
        batch = attack == "batch"
        body = transfer_payload(batch)
        failures = []
        sender_after_denied = sender_before
        receiver_after_denied = receiver_before

        # 2. 即使旧版接受攻击请求，也继续检查实际资金状态，不把 HTTP 响应当唯一 oracle。
        try:
            denied = requests.post(
                api_url, json=body, headers=attack_headers, timeout=5
            )
            rejects_unauthorized(denied)
        except (requests.RequestException, AssertionError) as error:
            failures.append(f"未认证转账响应: {error!r}")

        # 3. 交易池、出块及余额从真实节点和同一 API 新请求取得。
        try:
            empty_pool(dev_node["node_url"], 4)
        except (requests.RequestException, AssertionError) as error:
            failures.append(f"未认证转账后的交易池: {error!r}")
        try:
            mine_and_sync(dev_node["node_url"])
        except (requests.RequestException, AssertionError) as error:
            failures.append(f"未认证转账后的出块和 indexer: {error!r}")
        try:
            sender_after_denied = capacity(api_url, SENDER_ADDRESS, 5)
            receiver_after_denied = capacity(api_url, ADDRESS, 6)
            assert sender_after_denied == sender_before, sender_after_denied
            assert receiver_after_denied == receiver_before, receiver_after_denied
        except (requests.RequestException, AssertionError) as error:
            failures.append(f"未认证转账后的资金状态: {error!r}")

        # 4. 保持业务 body、Origin 和 Host 不变，仅修正认证头；确认真实交易。
        try:
            authorized = requests.post(
                api_url,
                json=body,
                headers={**attack_headers, **GOOD_HEADERS},
                timeout=5,
            )
            tx_hash = transaction_hash(authorized, batch)
            transaction = confirm_transaction(dev_node["node_url"], tx_hash)
            assert transaction["tx_status"]["status"] == "committed", transaction
            sender_after_valid = capacity(api_url, SENDER_ADDRESS, 7)
            receiver_after_valid = capacity(api_url, ADDRESS, 8)
            assert (
                receiver_after_valid["total"] == receiver_after_denied["total"] + AMOUNT
            )
            assert receiver_after_valid["dao"] == receiver_after_denied["dao"]
            assert receiver_after_valid["immature"] == receiver_after_denied["immature"]
            assert sender_after_valid["total"] <= sender_after_denied["total"] - AMOUNT
        except (
            requests.RequestException,
            AssertionError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            failures.append(f"认证后真实转账: {error!r}")

        # 5. 捕获异常只为继续观察，不能将前面的失败算作通过。
        if process.pid != pid or process.poll() is not None:
            failures.append("原 API 进程已变化或退出")
        assert not failures, "\n".join(failures)


# TEST-MAP: CLI-API-690-04
@pytest.mark.parametrize(
    "mode",
    [
        ("wallet", True, "127.0.0.1"),
        ("read_only", False, "127.0.0.1"),
        ("read_only_wildcard", False, "0.0.0.0"),
    ],
    ids=lambda mode: mode[0],
)
def test_other_http_entries_and_read_only_mode_require_auth(dev_node, mode):
    """
    1. 在带或不带私钥的配置下，证明正确 token 能查询已知余额。
    2. 检查无 token、错 token 的余额请求先返回 401。
    3. 同一 GET 和坏 JSON POST 先验证认证后到达下游，再移除认证检查 401。
    4. 无私钥服务即使正确认证也不能转账，交易池和资金不变。
    """
    # 1. 无私钥的 wildcard 服务仍只经本机地址探测，不声称远端验证。
    _, with_private_key, bind_host = mode
    with server_process(
        dev_node, with_private_key=with_private_key, bind_host=bind_host
    ) as (api_url, process, _, log_path):
        wait_for_rpc(
            process,
            api_url,
            "get_capacity_by_address",
            [ADDRESS],
            log_path,
            headers=GOOD_HEADERS,
        )
        baseline = capacity(api_url, ADDRESS, 1)
        assert baseline == {"total": 0, "immature": 0, "dao": 0}, baseline

        # 2. 相同且有效的读请求，只有认证头缺失或不正确。
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "get_capacity_by_address",
            "params": [ADDRESS],
        }
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            denied = requests.post(api_url, json=request, headers=headers, timeout=5)
            rejects_unauthorized(denied)

        # 3. 证明 401 发生在 HTTP 方法和 JSON 解析错误之前。
        good_get = requests.get(api_url, headers=GOOD_HEADERS, timeout=5)
        assert good_get.status_code == 405, good_get.text
        bad_get = requests.get(api_url, timeout=5)
        rejects_unauthorized(bad_get)
        parse_headers = {**GOOD_HEADERS, "Content-Type": "application/json"}
        good_parse = requests.post(api_url, data="{", headers=parse_headers, timeout=5)
        assert good_parse.status_code == 200, good_parse.text
        parsed = good_parse.json()
        assert parsed.get("error", {}).get("code") == -32700, parsed
        bad_parse = requests.post(
            api_url,
            data="{",
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        rejects_unauthorized(bad_parse)

        # 4. 另一个权限边界是无私钥时永远不能签名；拒绝来源必须明确。
        if not with_private_key:
            sender_before = capacity(api_url, SENDER_ADDRESS, 3)
            assert sender_before["total"] > AMOUNT, sender_before
            empty_pool(dev_node["node_url"], 3)
            response = rpc(
                api_url,
                "transfer",
                [TRANSFER],
                4,
                headers=GOOD_HEADERS,
            )
            assert "result" not in response, response
            assert response.get("error", {}).get("code") == -32603, response
            assert "privkey-path" in response["error"]["message"], response
            empty_pool(dev_node["node_url"], 5)
            mine_and_sync(dev_node["node_url"])
            assert capacity(api_url, SENDER_ADDRESS, 7) == sender_before
            assert capacity(api_url, ADDRESS, 6) == baseline


# TEST-MAP: CLI-API-690-05
@pytest.mark.parametrize("origin", ["https://example.com", "null"], ids=["web", "null"])
def test_preflight_never_transfers_and_authorized_browser_post_works(dev_node, origin):
    """
    1. 建立健康有资金服务及空交易池基线。
    2. 不带认证发送普通及带转账 body 的 OPTIONS，检查预检响应和无转账结果。
    3. 出块且 indexer 同步后，检查双方余额未变化。
    4. 同一 Origin 携正确 token 发送 POST，检查 CORS 响应、确认及余额变化。
    """
    # 1. 先证明钱包与收款地址状态有效。
    with server_process(dev_node) as (api_url, process, _, log_path):
        wait_for_rpc(
            process,
            api_url,
            "get_capacity_by_address",
            [ADDRESS],
            log_path,
            headers=GOOD_HEADERS,
        )
        pid = process.pid
        sender_before = capacity(api_url, SENDER_ADDRESS, 1)
        receiver_before = capacity(api_url, ADDRESS, 2)
        assert sender_before["total"] > AMOUNT
        assert receiver_before == {"total": 0, "immature": 0, "dao": 0}
        empty_pool(dev_node["node_url"], 3)

        # 2. 浏览器真实空 body 和对抗性有效转账 body 均不得执行 RPC。
        preflight_headers = {
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type",
        }
        for content in (None, json.dumps(transfer_payload())):
            request_headers = dict(preflight_headers)
            if content is not None:
                request_headers["Content-Type"] = "application/json"
            preflight = requests.options(
                api_url, headers=request_headers, data=content, timeout=5
            )
            assert preflight.status_code == 200, preflight.text
            assert preflight.headers.get("Access-Control-Allow-Origin") == origin
            assert "POST" in preflight.headers.get("Access-Control-Allow-Methods", "")
            allowed_headers = preflight.headers.get(
                "Access-Control-Allow-Headers", ""
            ).lower()
            assert (
                "authorization" in allowed_headers and "content-type" in allowed_headers
            )
            assert not preflight.text.strip(), preflight.text
            empty_pool(dev_node["node_url"], 4)

        # 3. 正常出块后重新查询，防止只看瞬时空交易池。
        mine_and_sync(dev_node["node_url"])
        assert capacity(api_url, SENDER_ADDRESS, 5) == sender_before
        assert capacity(api_url, ADDRESS, 6) == receiver_before

        # 4. 浏览器的正式 POST 也必须有允许该 Origin 的响应头。
        authorized = requests.post(
            api_url,
            json=transfer_payload(),
            headers={**GOOD_HEADERS, "Origin": origin},
            timeout=5,
        )
        assert authorized.headers.get("Access-Control-Allow-Origin") == origin
        tx_hash = transaction_hash(authorized)
        transaction = confirm_transaction(dev_node["node_url"], tx_hash)
        assert transaction["tx_status"]["status"] == "committed", transaction
        sender_after = capacity(api_url, SENDER_ADDRESS, 7)
        receiver_after = capacity(api_url, ADDRESS, 8)
        assert receiver_after["total"] == receiver_before["total"] + AMOUNT
        assert receiver_after["immature"] == receiver_before["immature"]
        assert receiver_after["dao"] == receiver_before["dao"]
        assert sender_after["total"] <= sender_before["total"] - AMOUNT
        assert process.pid == pid and process.poll() is None


# TEST-MAP: CLI-API-690-06
def test_wallet_server_cannot_listen_on_all_interfaces(dev_node):
    """
    1. 用有效私钥和 token 从本机地址启动，证明环境及钱包配置正确。
    2. 同样配置只改监听到 0.0.0.0，检查明确因安全限制退出。
    3. 检查目标端口未开放，不能提供可签名的 API 服务。
    """
    # 1. 本机绑定可用，排除节点或密钥的前置失败。
    with server_process(dev_node) as (api_url, process, _, log_path):
        wait_for_rpc(
            process,
            api_url,
            "get_capacity_by_address",
            [ADDRESS],
            log_path,
            headers=GOOD_HEADERS,
        )
        assert capacity(api_url, ADDRESS, 1)["total"] == 0

    # 2. 只有绑定地址发生变化，旧、新两版都应给出同一安全限制。
    with server_process(dev_node, bind_host="0.0.0.0") as (
        _,
        process,
        _,
        log_path,
    ):
        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            exit_code = None
        message = log_path.read_text(errors="replace")
        assert exit_code is not None and exit_code != 0, message
        assert "listen ip MUST be 127.0.0.1" in message, message

        # 3. 不能已有外部监听端口提供服务。
        port_is_closed(dev_node)
