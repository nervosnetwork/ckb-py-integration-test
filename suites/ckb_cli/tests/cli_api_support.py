"""ckb-cli API 认证验收复用的本地 dev 节点与服务进程。"""

from contextlib import ExitStack, contextmanager
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
from suites.ckb_cli.tests.test_api_server_transfer import (
    ADDRESS,
    confirm_transaction,
    running,
)


ROOT = Path(__file__).resolve().parents[3]
SENDER_ADDRESS = (
    "ckt1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xws"
    "qwgx292hnvmn68xf779vmzrshpmm6epn4c0cgwga"
)
AUTH_TOKEN = "!ckb-cli-test-token-690~"
MINER_LOCK_ARG = "0x" + "42" * 20


def rpc(url, method, params, request_id, timeout=5, headers=None):
    response = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        timeout=timeout,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict), body
    assert body.get("jsonrpc") == "2.0" and body.get("id") == request_id, body
    print(f"{method} response: {body}")
    return body


def wait_for_rpc(process, url, method, params, log_path, headers=None):
    deadline = time.monotonic() + 30
    last_response = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        assert process.poll() is None, f"Service exited; see {log_path}"
        try:
            last_response = rpc(
                url, method, params, 0, timeout=min(5, remaining), headers=headers
            )
            if "error" not in last_response and last_response.get("result") is not None:
                return
        except requests.RequestException as error:
            last_response = str(error)
        time.sleep(0.1)
    pytest.fail(f"Service not ready within 30s: {last_response}; see {log_path}")


@pytest.fixture
def dev_node(tmp_path):
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

    # 每例独立真实 dev 节点，矿工地址须区别于发送与收款地址。
    config, _, _ = get_ckb_configs(p2p_port, rpc_port)
    config["ckb_rpc_listen_address"] = f"127.0.0.1:{rpc_port}"
    config["ckb_network_listen_addresses"] = [f"/ip4/127.0.0.1/tcp/{p2p_port}"]
    config["ckb_network_max_outbound_peers"] = 0
    config["ckb_block_assembler_args"] = MINER_LOCK_ARG
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
    key.write_text(ACCOUNT_PRIVATE_1 + "\n")  # 公开 dev genesis 测试账户。
    key.chmod(0o600)
    env = {**os.environ, "CKB_CLI_HOME": str(tmp_path / "cli-home")}

    for key_info_args, expected_address in (
        (["--privkey-path", key], SENDER_ADDRESS),
        (["--address", ADDRESS], ADDRESS),
    ):
        output = subprocess.check_output(
            [
                str(cli),
                "util",
                "key-info",
                *[str(value) for value in key_info_args],
                "--local-only",
                "--output-format",
                "json",
            ],
            env=env,
            text=True,
            timeout=15,
        )
        info = json.loads(output[output.index("{") :])
        assert info["address"]["testnet"] == expected_address, info
        assert info["lock_arg"] != MINER_LOCK_ARG, info

    for binary in (node, cli):
        version = subprocess.check_output(
            [str(binary), "--version"], env=env, text=True, timeout=15
        ).strip()
        print(f"Binary: {binary}; version: {version}")
    help_text = subprocess.check_output(
        [str(cli), "server", "--help"], env=env, text=True, timeout=15
    )
    supports_auth = "--auth-token" in help_text
    print(f"CLI server auth option supported: {supports_auth}")

    node_url = f"http://127.0.0.1:{rpc_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    node_log = tmp_path / "node.log"
    print(f"Node log: {node_log}")
    with running([node, "run", "--indexer"], tmp_path, node_log) as node_process:
        wait_for_rpc(node_process, node_url, "get_indexer_tip", [], node_log)
        wait_for_rpc(node_process, node_url, "tx_pool_info", [], node_log)
        yield {
            "cli": cli,
            "node_url": node_url,
            "api_url": api_url,
            "api_port": api_port,
            "key": key,
            "directory": tmp_path,
            "env": env,
            "node_process": node_process,
            "supports_auth": supports_auth,
        }


@contextmanager
def server_process(
    dev_node,
    *,
    with_private_key=True,
    token=AUTH_TOKEN,
    send_token=None,
    bind_host="127.0.0.1",
):
    if send_token is None:
        send_token = dev_node["supports_auth"]
    arguments = [
        dev_node["cli"],
        "--url",
        dev_node["node_url"],
        "server",
        "--listen",
        f"{bind_host}:{dev_node['api_port']}",
    ]
    if with_private_key:
        arguments.extend(["--privkey-path", dev_node["key"]])
    if send_token:
        arguments.extend(["--auth-token", token])
    log_path = dev_node["directory"] / f"api-server-{time.time_ns()}.log"
    headers = {"Authorization": f"Bearer {token}"} if send_token else {}
    print(f"API service log: {log_path}; listen={bind_host}:{dev_node['api_port']}")
    with running(
        arguments, dev_node["directory"], log_path, dev_node["env"]
    ) as process:
        yield dev_node["api_url"], process, headers, log_path
