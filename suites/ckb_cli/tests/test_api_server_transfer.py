"""真实 CLI HTTP 接口的容量参数回归；选版与运行说明见对应 review。"""

from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

from jinja2 import Environment, FileSystemLoader
import pytest
import requests

from framework.config import ACCOUNT_PRIVATE_1
from framework.util import get_ckb_configs


ROOT = Path(__file__).resolve().parents[3]
ADDRESS = (
    "ckt1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xws"
    "qw6vjzy9kahx3lyvlgap8dp8ewd8g80pcgcexzrj"
)


def rpc(url, method, params, request_id, timeout=5):
    response = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        timeout=timeout,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict), body
    assert body.get("jsonrpc") == "2.0" and body.get("id") == request_id, body
    print(f"{method} response: {body}")
    return body


@contextmanager
def running(arguments, directory, log_path, env=None):
    with log_path.open("w") as output:
        process = subprocess.Popen(
            [str(arg) for arg in arguments],
            cwd=directory,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def wait_for_rpc(process, url, method, params, log_path):
    deadline = time.monotonic() + 30
    last_response = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        assert process.poll() is None, f"Service exited; see {log_path}"
        try:
            last_response = rpc(url, method, params, 0, timeout=min(5, remaining))
            if "error" not in last_response and last_response.get("result") is not None:
                return
        except requests.RequestException as error:
            last_response = str(error)
        time.sleep(0.1)
    pytest.fail(f"Service not ready within 30s: {last_response}; see {log_path}")


def confirm_transaction(node_url, tx_hash):
    deadline = time.monotonic() + 30

    def call_node(method, params, request_id):
        remaining = deadline - time.monotonic()
        assert remaining > 0, "Transaction confirmation or indexer sync timed out"
        return rpc(node_url, method, params, request_id, timeout=min(5, remaining))

    transaction = None
    for attempt in range(21):
        response = call_node("get_transaction", [tx_hash], 100 + attempt)
        assert "error" not in response, response
        transaction = response.get("result")
        assert isinstance(transaction, dict), response
        status = transaction["tx_status"]
        if status["status"] == "committed":
            break
        assert status["status"] in {"pending", "proposed"}, transaction
        if attempt == 20:
            break
        block = call_node("generate_block", [], 200 + attempt)
        assert "error" not in block, block
        while time.monotonic() < deadline:
            pool = call_node("tx_pool_info", [], 300 + attempt)
            assert "error" not in pool, pool
            if pool["result"]["tip_hash"] == block["result"]:
                break
            time.sleep(0.1)
    assert transaction["tx_status"]["status"] == "committed", transaction
    height = int(transaction["tx_status"]["block_number"], 16)
    while time.monotonic() < deadline:
        tip = call_node("get_indexer_tip", [], 400)
        assert "error" not in tip, tip
        if tip.get("result") and int(tip["result"]["block_number"], 16) >= height:
            return transaction
        time.sleep(0.1)
    raise AssertionError("Indexer did not reach the transaction's confirmation block")


@pytest.fixture
def api_server(tmp_path):
    cli = Path(os.environ.get("CKB_CLI_BIN", ROOT / "source/ckb-cli")).resolve()
    node = Path(
        os.environ.get("CKB_TEST_BINARY", ROOT / "download/current/ckb")
    ).resolve()
    for binary in (cli, node):
        assert binary.is_file() and os.access(
            binary, os.X_OK
        ), f"Missing binary: {binary}"

    with ExitStack() as reservations:
        ports = [reservations.enter_context(socket.socket()) for _ in range(3)]
        for port in ports:
            port.bind(("127.0.0.1", 0))
        rpc_port, p2p_port, api_port = [port.getsockname()[1] for port in ports]

    # 显式定位本 worktree，复用真实 dev 模板，避免框架按仓库名字匹配到主工作区。
    config, _, _ = get_ckb_configs(p2p_port, rpc_port)
    config["ckb_rpc_listen_address"] = f"127.0.0.1:{rpc_port}"
    config["ckb_network_listen_addresses"] = [f"/ip4/127.0.0.1/tcp/{p2p_port}"]
    config["ckb_network_max_outbound_peers"] = 0
    environment = Environment(loader=FileSystemLoader(ROOT))
    environment.filters["to_json"] = json.dumps
    template = environment.get_template("source/template/ckb/v210/ckb.toml.j2")
    (tmp_path / "ckb.toml").write_text(template.render(**config))
    shutil.copyfile(
        ROOT / "source/template/ckb/v210/specs/dev.toml", tmp_path / "dev.toml"
    )
    shutil.copyfile(
        ROOT / "source/template/ckb/default.db-options", tmp_path / "default.db-options"
    )
    key = tmp_path / "test.key"
    key.write_text(ACCOUNT_PRIVATE_1 + "\n")  # 公开 dev genesis 测试账户，已预置容量。
    key.chmod(0o600)
    env = {**os.environ, "CKB_CLI_HOME": str(tmp_path / "cli-home")}
    for binary in (node, cli):
        version = subprocess.check_output(
            [str(binary), "--version"], env=env, text=True, timeout=15
        ).strip()
        print(f"Binary: {binary}; version: {version}")

    node_url = f"http://127.0.0.1:{rpc_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    node_log, api_log = tmp_path / "node.log", tmp_path / "api-server.log"
    print(f"Service logs: {node_log}, {api_log}")
    with running([node, "run", "--indexer"], tmp_path, node_log) as node_process:
        wait_for_rpc(node_process, node_url, "get_indexer_tip", [], node_log)
        wait_for_rpc(node_process, node_url, "tx_pool_info", [], node_log)
        arguments = [
            cli,
            "--url",
            node_url,
            "server",
            "--listen",
            f"127.0.0.1:{api_port}",
            "--privkey-path",
            key,
        ]
        with running(arguments, tmp_path, api_log, env) as api_process:
            wait_for_rpc(
                api_process, api_url, "get_capacity_by_address", [ADDRESS], api_log
            )
            yield api_url, node_url, api_process


# TEST-MAP: CLI-API-691-01
def test_transfer_rejects_invalid_fee_capacity(api_server):
    """
    1. 通过真实余额查询确认钱包 HTTP 服务与节点正常。
    2. 发送其他字段合法、最高手续费容量为非法字符串的转账请求。
    3. 检查返回完整的 JSON-RPC 参数错误，错误码为 -32602。
    """
    # 1. 查询余额，检查成功响应及非负整数容量字段。
    api_url, _, _ = api_server
    baseline = rpc(api_url, "get_capacity_by_address", [ADDRESS], 1)
    assert "error" not in baseline, baseline
    capacity = baseline["result"]
    assert set(capacity) == {"total", "immature", "dao"}, capacity
    assert all(
        type(value) is int and value >= 0 for value in capacity.values()
    ), capacity

    # 2. 保持同一服务，只有最高手续费容量字符串非法。
    response = rpc(
        api_url,
        "transfer",
        [
            {
                "capacity": 6100000000,
                "fee_rate": 1000,
                "to_address": ADDRESS,
                "force_small_change_as_fee": "NOT_A_CAPACITY",
            }
        ],
        2,
    )

    # 3. 检查参数错误；断连、超时和其他错误码均不能通过。
    assert "result" not in response, response
    assert response["error"]["code"] == -32602, response


# TEST-MAP: CLI-API-691-02
def test_wallet_requests_survive_invalid_fee_capacity(api_server):
    """
    1. 查询健康服务的余额，建立未发生链状态变化时的基线。
    2. 发送非法容量转账，检查参数错误；异常时保留失败并继续探测。
    3. 不重启服务，再次查询余额，检查成功响应且结果不变。
    4. 再发相同非法转账，检查仍返回完整的 -32602 响应。
    5. 不重启服务，将同一参数改为合法的 0.001，提交一笔 61 CKB 转账。
    6. 检查交易 committed、indexer 同步及收款余额增加 61 CKB。
    7. 汇总失败并检查 API 进程未变，任何异常或不符预期都使本例失败。
    """
    # 1. 健康基线必须成功，前置错误不能算旧版复现。
    api_url, node_url, api_process = api_server
    api_pid = api_process.pid
    print(f"API recovery test PID: {api_pid}")
    baseline = rpc(api_url, "get_capacity_by_address", [ADDRESS], 1)
    assert "error" not in baseline, baseline
    capacity = baseline["result"]
    assert set(capacity) == {"total", "immature", "dao"}, capacity
    assert all(
        type(value) is int and value >= 0 for value in capacity.values()
    ), capacity
    arguments = {
        "capacity": 6100000000,
        "fee_rate": 1000,
        "to_address": ADDRESS,
        "force_small_change_as_fee": "NOT_A_CAPACITY",
    }
    failures = []

    # 2. 延后报告首次请求的失败，保证后续请求确实被探测。
    try:
        first = rpc(api_url, "transfer", [arguments], 2)
        assert "result" not in first, first
        assert isinstance(first.get("error"), dict), first
        assert first["error"].get("code") == -32602, first
    except (requests.RequestException, AssertionError) as error:
        failures.append(f"首次非法转账: {error!r}")

    # 3. 同一服务上的新余额请求必须成功，且不改变容量结果。
    try:
        after = rpc(api_url, "get_capacity_by_address", [ADDRESS], 3)
        assert "error" not in after, after
        assert isinstance(after.get("result"), dict), after
        assert all(type(value) is int for value in after["result"].values()), after
        assert after["result"] == capacity, after
    except (requests.RequestException, AssertionError) as error:
        failures.append(f"非法请求后的余额查询: {error!r}")

    # 4. 继续使用同一服务，重复请求也必须返回参数错误。
    try:
        repeated = rpc(api_url, "transfer", [arguments], 4)
        assert "result" not in repeated, repeated
        assert isinstance(repeated.get("error"), dict), repeated
        assert repeated["error"].get("code") == -32602, repeated
    except (requests.RequestException, AssertionError) as error:
        failures.append(f"重复非法转账: {error!r}")

    # 5. 保持原 API 进程，仅将非法容量字符串改为合法值。
    tx_hash = None
    try:
        valid = rpc(
            api_url,
            "transfer",
            [{**arguments, "force_small_change_as_fee": "0.001"}],
            5,
        )
        assert "error" not in valid, valid
        candidate = valid.get("result")
        assert (
            isinstance(candidate, str)
            and candidate.startswith("0x")
            and len(candidate) == 66
        ), valid
        tx_hash = candidate
    except (requests.RequestException, AssertionError) as error:
        failures.append(f"合法转账失败，未执行交易确认及余额变化检查: {error!r}")

    # 6. 使用真实节点确认交易，并通过同一 API 服务重新查询收款余额。
    if tx_hash is not None:
        try:
            transaction = confirm_transaction(node_url, tx_hash)
            assert transaction["tx_status"]["status"] == "committed", transaction
            final = rpc(api_url, "get_capacity_by_address", [ADDRESS], 6)
            assert "error" not in final, final
            expected = {**capacity, "total": capacity["total"] + 6100000000}
            assert final.get("result") == expected, final
            assert all(type(value) is int for value in final["result"].values()), final
            print(
                f"Same API PID {api_pid}: recipient balance {capacity['total']} -> {expected['total']}"
            )
        except (requests.RequestException, AssertionError) as error:
            failures.append(f"合法转账确认或余额检查失败: {error!r}")

    # 7. 不能因捕获异常而通过；保留所有后续接口的失败诊断。
    if api_process.pid != api_pid or api_process.poll() is not None:
        failures.append("API 服务进程发生变化或退出")
    assert not failures, "\n".join(failures)
