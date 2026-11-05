"""Public-interface checks for removal of the legacy CKB Alert feature.

Expired alerts use real old CKB nodes and delayed TCP streams, never a raw
P2P peer. The old receiver must execute its hook before a negative result on
the new node is accepted. See reviews/ckb-pr-5304-alert-removal.md for binary selection.
"""

from contextlib import ExitStack
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from parameterized import parameterized
import requests
import toml

from framework.basic import CkbTest
from framework.helper.miner import compact_to_target
from framework.rpc import RPCClient
from framework.util import get_project_root
from test_cases.feature.alert_message import (
    alert_signature_config,
    signed_alert,
)
from test_cases.feature.tcp_delay import TcpDelayProxy


ROOT = Path(get_project_root())
ALERT_ID = 110
MODES = [("default",), ("legacy",)]
WAIT_SECONDS = 45
QUIET_SECONDS = 2
ALERT_VALIDITY_SECONDS = 30
PROXY_PAUSE_SECONDS = 60


class AlertRPC(RPCClient):
    def response(self, method, params):
        response = requests.post(
            self.url,
            json={"jsonrpc": "2.0", "id": 5304, "method": method, "params": params},
            timeout=5,
        )
        response.raise_for_status()
        body = response.json()
        assert body["jsonrpc"] == "2.0" and body["id"] == 5304, body
        return body

    def call(self, method, params, try_count=1):
        body = self.response(method, params)
        assert "error" not in body, f"{method}: {body.get('error')}"
        return body["result"]


@lru_cache(maxsize=4)
def binary_identity(path):
    binary = Path(path)
    assert binary.is_file() and os.access(
        binary, os.X_OK
    ), f"CKB binary missing: {binary}"
    version = subprocess.check_output(
        [str(binary), "--version"], text=True, timeout=15
    ).strip()
    return version, hashlib.sha256(binary.read_bytes()).hexdigest()


@unittest.skipIf(CkbTest.skip_docker(), "Requires native CKB processes; unset DOCKER")
class TestAlertRemoval(CkbTest):
    def setup_method(self, method):
        self.did_pass = None
        self.resources = ExitStack()
        parent = ROOT / "tmp/alert_removal"
        parent.mkdir(parents=True, exist_ok=True)
        self.case_dir = Path(tempfile.mkdtemp(prefix="case_", dir=parent))
        self.nodes = []
        self.processes = {}

    def teardown_method(self, method):
        cleaned = False
        try:
            self.resources.close()
            cleaned = True
        finally:
            if self.did_pass and cleaned:
                shutil.rmtree(self.case_dir)
            else:
                report = ROOT / "report/alert_removal" / self.case_dir.name
                shutil.copytree(self.case_dir, report)
                print(f"Saved Alert test data and logs: {report}")

    # TEST-MAP: ALERT-REMOVE-01
    # 准备：使用新版 ckb init 原样生成配置，并以该配置启动节点。
    # 触发：启动就绪后读取生成配置、协议列表和普通 RPC 响应。
    # 观察：默认配置中的 Alert 项、支持协议、alerts 字段及链头查询。
    # 断言：旧 Alert 配置字段及协议均不存在，alerts 为空，节点正常响应。
    def test_generated_defaults_start_without_alert(self):
        node = self._node(instrument=False)
        generated = node.generated_config
        assert "Alert" not in generated["network"]["support_protocols"]
        assert "Alert" not in generated["rpc"]["modules"]
        assert "alert_signature" not in generated
        assert "network_alert_notify_script" not in generated.get("notify", {})
        assert "notify_alert_timeout" not in generated.get("notify", {})
        # This node starts the generated file without any config mutation.
        assert Path(node.ckb_toml_path).read_bytes() == node.generated_bytes
        self._assert_new_node(node)

    # TEST-MAP: ALERT-REMOVE-02
    # 准备：分别加入四类合法旧 Alert 字段，再覆盖全部字段的组合。
    # 触发：先同步新区块，再保留数据重启并继续同步。
    # 观察：重启前后配置与链头、后续区块同步及 Alert 能力。
    # 断言：旧配置可加载，重启保留链头，继续同步正常且未恢复 Alert。
    @parameterized.expand(
        [(group,) for group in ("network", "rpc", "signature", "notify", "legacy")]
    )
    def test_legacy_config_loads_and_survives_restart(self, group):
        target = self._node(mode=group)
        source = self._node()
        self._prime([source, target])
        self._connect(target, source)
        self._advance(source, [target])
        before = target.client.get_tip_header()
        config = Path(target.ckb_toml_path).read_bytes()

        self._stop(target)
        self._start(target)
        assert Path(target.ckb_toml_path).read_bytes() == config
        assert target.client.get_tip_header()["hash"] == before["hash"]
        self._connect(target, source)
        self._advance(source, [target])
        self._assert_new_node(target)

    # TEST-MAP: ALERT-REMOVE-03
    # 准备：默认配置和保留旧 RPC Alert 项的配置，各准备三种告警参数。
    # 触发：分别向 send_alert 提交合法、过期及缺少必填字段的请求。
    # 观察：每次 JSON-RPC 错误码与消息，以及请求后的节点状态。
    # 断言：均返回 -32601 Method not found，Alert 仍关闭且普通 RPC 可用。
    @parameterized.expand([("default",), ("rpc",)])
    def test_send_alert_method_is_not_registered(self, mode):
        node = self._node(mode=mode)
        valid = self._alert(notice_until=int(time.time() * 1000) + 60_000)
        expired = self._alert(notice_until=int(time.time() * 1000) - 1_000)
        for alert in (valid, expired, {"id": "0x1"}):
            response = node.client.response("send_alert", [alert])
            assert response["error"]["code"] == -32601, response
            assert "Method not found" in response["error"]["message"], response
        self._assert_new_node(node)

    # TEST-MAP: ALERT-REMOVE-04
    # 准备：默认或完整旧配置的新版节点，分别搭配新版及兼容旧版 peer。
    # 触发：覆盖主动与被动连接，再由两端分别出块并向对端同步。
    # 观察：实际连接方向、本地与协商协议、两端同步后的区块 hash。
    # 断言：连接方向符合场景，双方均未协商 Alert，双向区块同步正常。
    @parameterized.expand(
        [
            (mode, peer_version, direction)
            for mode in ("default", "legacy")
            for peer_version in ("old", "new")
            for direction in ("outbound", "inbound")
        ]
    )
    def test_normal_peering_without_alert_protocol(self, mode, peer_version, direction):
        target = self._node(mode=mode)
        peer = self._node(
            old=peer_version == "old",
            mode="legacy" if peer_version == "old" else "default",
        )
        self._prime([target, peer])
        if direction == "outbound":
            self._connect(target, peer)
        else:
            self._connect(peer, target)
        self._assert_topology({target: [peer], peer: [target]})
        connection = self._peer(target, peer)
        assert connection["is_outbound"] == (direction == "outbound"), connection
        self._assert_new_node(target)
        assert ALERT_ID not in self._peer_protocols(peer, target)
        self._advance(peer, [target])
        self._advance(target, [peer])
        self._assert_new_node(target)

    # TEST-MAP: ALERT-REMOVE-05
    # 准备：分别使用默认配置及完整旧 Alert 配置启动新版节点。
    # 触发：在启动、同步新区块和保留数据重启后查询链信息。
    # 观察：alerts 类型与值，链名称、epoch、中位时间、难度及同步状态。
    # 断言：alerts 始终为空列表，其他字段符合节点状态，重启前后响应一致。
    @parameterized.expand(MODES)
    def test_alerts_field_stays_empty_across_sync_and_restart(self, mode):
        target = self._node(mode=mode)
        self._assert_chain_info(target)
        source = self._node()
        self._prime([source, target])
        self._connect(target, source)
        self._advance(source, [target])
        before = self._assert_chain_info(target)
        tip = target.client.get_tip_header()

        self._stop(target)
        self._start(target)
        assert target.client.get_tip_header()["hash"] == tip["hash"]
        assert self._assert_chain_info(target) == before

    # TEST-MAP: ALERT-REMOVE-06
    # 准备：分别建立旧中继正向对照和新版唯一中继的三节点拓扑。
    # 触发：由旧发送端提交未过期的合法告警，两组使用各自的独立消息。
    # 观察：中继及下游脚本记录、Alert 协议、实际 peer 列表和区块同步。
    # 断言：旧中继能传播；新版中继无 Alert 副作用，下游无告警且同步正常。
    @parameterized.expand(MODES)
    def test_unexpired_alert_cannot_cross_new_relay(self, mode):
        sender, relay, observer = self._relay_topology(old_relay=True)
        alert = self._alert(notice_until=int(time.time() * 1000) + 60_000)
        self._send_alert(sender, alert)
        self._wait_events(relay, alert["message"], 1)
        self._wait_events(observer, alert["message"], 1)
        self._assert_topology(
            {sender: [relay], relay: [sender, observer], observer: [relay]}
        )
        self._advance(sender, [relay, observer])
        for node in (sender, relay, observer):
            self._stop(node)

        sender, relay, observer = self._relay_topology(old_relay=False, mode=mode)
        alert = self._alert(notice_until=int(time.time() * 1000) + 60_000)
        self._send_alert(sender, alert)
        self._assert_topology(
            {sender: [relay], relay: [sender, observer], observer: [relay]}
        )
        self._assert_new_node(relay)
        self._observe_no_alert(relay, [observer])
        self._advance(sender, [relay, observer])
        self._observe_no_alert(relay, [observer])
        self._assert_topology(
            {sender: [relay], relay: [sender, observer], observer: [relay]}
        )

    # TEST-MAP: ALERT-REMOVE-07
    # 准备：旧发送端与新旧接收端预先建连，签名后暂停发送方向的转发。
    # 触发：在有效期内提交告警，等到过期后才首次放行连接中的数据。
    # 观察：旧接收脚本的消息及时间，新版协议、alerts、脚本和正常同步。
    # 断言：旧对照恰好处理一次过期告警，新版无 Alert 协议及告警副作用。
    # 边界：新版未协商 Alert，验证入口关闭，不要求告警实际进入新版处理器。
    @parameterized.expand(MODES)
    def test_expired_alert_first_delivery(self, mode):
        self._expired_delivery(mode, rounds=1)

    # TEST-MAP: ALERT-REMOVE-08
    # 准备：三个旧发送端各自建连，并在有效期内提交同一份签名告警。
    # 触发：过期后逐条放行，每轮旧脚本执行后清理过期记录，再放行下一条。
    # 观察：旧脚本逐轮新增记录及时间，原连接存活，以及新版协议与同步。
    # 断言：同一过期告警在旧端处理三次，新版持续无 Alert 协议及告警副作用。
    @parameterized.expand(MODES)
    def test_same_expired_alert_is_replayed_after_cleanup(self, mode):
        self._expired_delivery(mode, rounds=3)

    def _expired_delivery(self, mode, rounds):
        # 每轮使用独立发送端和预建会话，让相同告警可以在旧端清理记录后再次到达。
        # 旧端的实际脚本记录证明输入与投递有效；新版只检查协议移除及无副作用。
        old_receiver = self._node(old=True, mode="legacy")
        target = self._node(mode=mode)
        senders = [self._node(old=True, mode="legacy") for _ in range(rounds)]
        self._prime([old_receiver, target, *senders])
        gates = []
        for sender in senders:
            to_old = self._delayed_connect(sender, old_receiver)
            to_new = self._delayed_connect(sender, target)
            self._wait(
                lambda: ALERT_ID in self._peer_protocols(sender, old_receiver),
                "old Alert negotiation",
            )
            self._wait(
                lambda: ALERT_ID in self._peer_protocols(old_receiver, sender),
                "old receiver Alert negotiation",
            )
            self._assert_new_node(target)
            assert ALERT_ID not in self._peer_protocols(sender, target)
            gates.append((to_old, to_new))
        topology = {old_receiver: senders, target: senders}
        topology.update({sender: [old_receiver, target] for sender in senders})
        self._assert_topology(topology)
        self._advance(senders[0], [old_receiver, target, *senders[1:]])

        # The timestamp is signed and must never change afterwards. Allow
        # the CLI's 15s signing timeout plus time for all sender RPCs/hooks.
        expires = int(time.time() * 1000) + ALERT_VALIDITY_SECONDS * 1000
        alert = self._alert(notice_until=expires)
        send_seconds = (expires - int(time.time() * 1000)) / 1000 - 1
        assert send_seconds > 0, (
            "Alert signing exhausted the validity window before any sender "
            f"accepted it: notice_until={expires}, remaining={send_seconds:.2f}s"
        )
        send_deadline = time.monotonic() + send_seconds

        # Signing happens before pause, so waiting for expiry uses at most
        # 30.1s of the 60s pause limit. The rest is reserved for the three
        # sequential releases, their 2s quiet windows, and sync checks.
        paused_at = time.monotonic()
        for pair in gates:
            for gate in pair:
                gate.pause()
        for index, sender in enumerate(senders, 1):
            remaining = send_deadline - time.monotonic()
            assert remaining > 0, (
                f"Alert submission deadline expired before sender {index}/{rounds}: "
                f"notice_until={expires}, remaining={remaining:.2f}s"
            )
            assert sender.client.call("send_alert", [alert]) is None

        # One shared deadline covers all local hooks and gateway queues;
        # sequential 45s waits could otherwise outlive the signed timestamp.
        while True:
            remaining = send_deadline - time.monotonic()
            events = [self._events(sender) for sender in senders]
            for records in events:
                assert len(records) <= 1 and all(
                    event["message"] == alert["message"] for event in records
                ), f"Unexpected sender Alert hook records: {records}"
            states = [to_old.snapshot() for to_old, _ in gates]
            assert remaining > 0, (
                "Alert queueing deadline expired before its signed expiry: "
                f"sender_hook_counts={[len(records) for records in events]}, "
                f"old_gateway_states={states}, remaining={remaining:.2f}s"
            )
            for pair in gates:
                for gate in pair:
                    gate.assert_session_alive()
            if all(len(records) == 1 for records in events) and all(
                state.buffered_bytes > 0 for state in states
            ):
                break
            time.sleep(0.1)
        assert (
            self._events(old_receiver) == []
        ), "Old receiver got the alert before release"
        while int(time.time() * 1000) <= expires + 100:
            assert (
                self._events(old_receiver) == []
            ), "Old receiver got the alert before release"
            self._assert_new_node(target)
            for pair in gates:
                for gate in pair:
                    gate.assert_session_alive()
            time.sleep(0.1)

        # 一轮完成脚本取证、公开 RPC 清理和同步检查后，才进入下一轮放行。
        for index, (sender, pair) in enumerate(zip(senders, gates), 1):
            remaining = PROXY_PAUSE_SECONDS - (time.monotonic() - paused_at)
            assert remaining > 1, (
                f"Pause budget exhausted before release {index}/{rounds}: "
                f"remaining={remaining:.2f}s of {PROXY_PAUSE_SECONDS}s; "
                "previous delivery, observation or sync exceeded the reserved time"
            )
            old_gate, new_gate = pair
            forwarded = old_gate.snapshot().forwarded_bytes
            old_gate.resume()
            new_gate.resume()
            self._wait_events(old_receiver, alert["message"], index)
            events = self._events(old_receiver)
            assert all(event["time_ms"] > expires for event in events), events
            self._wait(
                lambda: old_gate.snapshot().buffered_bytes == 0,
                "drained original byte stream",
            )
            assert old_gate.snapshot().forwarded_bytes > forwarded
            # This public RPC clears the old dedup/notified records. Its
            # empty result alone would not prove the expired hook executed.
            assert old_receiver.client.get_blockchain_info()["alerts"] == []
            self._assert_topology(topology)
            self._observe_no_alert(target)
            current_tip = old_receiver.client.get_tip_header()["hash"]
            self._wait(
                lambda: sender.client.get_tip_header()["hash"] == current_tip,
                "released sender catches up before mining",
            )
            self._advance(sender, [target, old_receiver])
            for old_proxy, new_proxy in gates:
                old_proxy.assert_session_alive()
                new_proxy.assert_session_alive()
        self._observe_no_alert(target)
        self._advance(senders[-1], [old_receiver, target, *senders[:-1]])
        assert len(self._events(old_receiver)) == rounds

    def _node(self, old=False, mode="default", instrument=True):
        name = "CKB_TEST_OLD_BINARY" if old else "CKB_TEST_BINARY"
        fallback = ROOT / ("download/0.209.0/ckb" if old else "download/current/ckb")
        binary = Path(os.environ.get(name, fallback)).resolve()
        version, digest = binary_identity(str(binary))
        expected = os.environ.get(name + "_SHA256")
        if expected:
            assert digest == expected, f"{name} SHA-256 mismatch"
        print(
            f"{'Old control' if old else 'Target'}: {binary}; {version}; sha256={digest}"
        )
        reservations = [socket.socket() for _ in range(2)]
        for reservation in reservations:
            self.resources.callback(reservation.close)
            reservation.bind(("127.0.0.1", 0))
        rpc_port, p2p_port = [sock.getsockname()[1] for sock in reservations]
        relative_dir = (self.case_dir / f"node{len(self.nodes)}").relative_to(
            ROOT / "tmp"
        )
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST, str(relative_dir), rpc_port, p2p_port
        )
        Path(node.ckb_dir).mkdir(parents=True)
        node.ckb_bin_path = str(binary)
        node.p2p_port = p2p_port
        node.test_rpc_enabled = instrument
        node.client = AlertRPC(node.rpcUrl)
        node.alert_events = Path(node.ckb_dir) / "alert-events.jsonl"
        node.alert_script = Path(node.ckb_dir) / "record-alert"
        self.nodes.append(node)
        self.resources.callback(self._stop, node)

        command = [
            str(binary),
            "init",
            "--chain",
            "dev",
            "--rpc-port",
            str(rpc_port),
            "--p2p-port",
            str(p2p_port),
            "--log-to",
            "stdout",
        ]
        if instrument:
            command += [
                "--import-spec",
                str(ROOT / self.CkbNodeConfigPath.CURRENT_TEST.ckb_spec_path),
                "--ba-arg",
                "0x8883a512ee2383c01574a328f60eeccbb4d78240",
            ]
        subprocess.run(
            command,
            cwd=node.ckb_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        node.generated_bytes = Path(node.ckb_toml_path).read_bytes()
        node.generated_config = toml.loads(node.generated_bytes.decode())
        if instrument:
            config = deepcopy(node.generated_config)
            config["rpc"]["modules"].append("IntegrationTest")
            config["rpc"]["listen_address"] = f"127.0.0.1:{rpc_port}"
            network = config["network"]
            network["listen_addresses"] = [f"/ip4/127.0.0.1/tcp/{p2p_port}"]
            network["bootnodes"] = []
            network["dns_seeds"] = []
            network["connect_outbound_interval_secs"] = 0
            network["support_protocols"] = [
                p
                for p in network["support_protocols"]
                if p not in ("Discovery", "Feeler")
            ]
            node.alert_script.write_text(
                f"#!{sys.executable}\nimport json, sys, time\n"
                f"with open({str(node.alert_events)!r}, 'a') as stream:\n"
                "    stream.write(json.dumps({'message': sys.argv[1], 'time_ms': time.time_ns() // 1000000}) + '\\n')\n"
            )
            node.alert_script.chmod(0o700)
            self._legacy_config(config, mode, node)
            with open(node.ckb_toml_path, "w") as stream:
                toml.dump(config, stream)
        for reservation in reservations:
            reservation.close()
        self._start(node)
        if old:
            assert ALERT_ID in {
                int(protocol["id"], 16)
                for protocol in node.client.local_node_info()["protocols"]
            }, f"Old control does not support Alert: {binary}; {version}"
        return node

    @staticmethod
    def _legacy_config(config, mode, node):
        if (
            mode in ("network", "legacy")
            and "Alert" not in config["network"]["support_protocols"]
        ):
            config["network"]["support_protocols"].append("Alert")
        if mode in ("rpc", "legacy") and "Alert" not in config["rpc"]["modules"]:
            config["rpc"]["modules"].append("Alert")
        if mode in ("signature", "legacy"):
            config["alert_signature"] = alert_signature_config()
        if mode in ("notify", "legacy"):
            config["notify"] = {
                "network_alert_notify_script": str(node.alert_script),
                "notify_alert_timeout": 1_000,
                "script_timeout": 2_000,
            }

    def _start(self, node):
        with open(Path(node.ckb_dir) / "node.log", "ab") as log:
            self.processes[node] = subprocess.Popen(
                [node.ckb_bin_path, "run"],
                cwd=node.ckb_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
            )

        def ready():
            assert (
                self.processes[node].poll() is None
            ), f"CKB exited: {node.ckb_dir}/node.log"
            try:
                return node.client.get_tip_header()
            except requests.RequestException:
                return None

        self._wait(ready, f"RPC startup: {node.rpcUrl}")
        if node.test_rpc_enabled:
            # HTTP starts before the tx-pool service. Importing the first
            # block too early leaves old block assemblers at genesis.
            self._wait(
                lambda: node.client.tx_pool_ready() is True,
                f"tx-pool startup: {node.rpcUrl}",
            )

    def _stop(self, node):
        process = self.processes.pop(node, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise AssertionError(f"CKB did not stop gracefully: {node.ckb_dir}")

    @staticmethod
    def _wait(predicate, description):
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            if result := predicate():
                return result
            time.sleep(0.1)
        raise AssertionError(f"Timed out after {WAIT_SECONDS}s: {description}")

    def _prime(self, nodes):
        # Give every isolated node the same recent valid block before P2P
        # handshakes, so inbound-direction tests do not depend on IBD policy.
        genesis = nodes[0].client.get_block_hash("0x0")
        assert all(node.client.get_block_hash("0x0") == genesis for node in nodes)
        block_hash = nodes[0].client.generate_block()
        block = nodes[0].client.get_block(block_hash)
        block["header"].pop("hash", None)
        for tx in block["transactions"]:
            tx.pop("hash", None)
        for node in nodes[1:]:
            assert node.client.submit_block("0x0", block) == block_hash
        for node in nodes:
            assert node.client.get_tip_header()["hash"] == block_hash
            assert (
                node.client.get_blockchain_info()["is_initial_block_download"] is False
            )

    def _connect(self, dialer, receiver, port=None):
        dialer.client.add_node(
            receiver.get_peer_id(), f"/ip4/127.0.0.1/tcp/{port or receiver.p2p_port}"
        )
        self._wait(
            lambda: self._peer(dialer, receiver) and self._peer(receiver, dialer),
            "peer connection",
        )

    def _delayed_connect(self, sender, receiver):
        proxy = TcpDelayProxy(
            "127.0.0.1", receiver.p2p_port, max_pause_seconds=PROXY_PAUSE_SECONDS
        ).start()
        self.resources.callback(proxy.close)
        self._connect(sender, receiver, port=proxy.port)
        proxy.wait_connected()
        return proxy

    @staticmethod
    def _peer(node, other):
        other_id = other.get_peer_id()
        matches = [
            peer for peer in node.client.get_peers() if peer["node_id"] == other_id
        ]
        assert len(matches) <= 1, f"Duplicate peer sessions: {matches}"
        return matches[0] if matches else None

    def _peer_protocols(self, node, other):
        peer = self._peer(node, other)
        return (
            {int(protocol["id"], 16) for protocol in peer["protocols"]}
            if peer
            else set()
        )

    def _assert_topology(self, topology):
        for node, neighbors in topology.items():
            expected = {neighbor.get_peer_id() for neighbor in neighbors}
            actual = {peer["node_id"] for peer in node.client.get_peers()}
            assert (
                actual == expected
            ), f"Unexpected peers at {node.rpcUrl}: {actual} != {expected}"

    def _advance(self, source, receivers):
        parent = source.client.get_tip_header()["hash"]
        # Block assembler updates asynchronously after a block is imported.
        # Wait for a template extending the current tip before generating.
        self._wait(
            lambda: source.client.get_block_template()["parent_hash"] == parent,
            "block template catches up to the chain tip",
        )
        block_hash = source.client.generate_block()
        assert source.client.get_tip_header()["hash"] == block_hash
        for receiver in receivers:
            self._wait(
                lambda: receiver.client.get_tip_header()["hash"] == block_hash,
                f"P2P block sync to {receiver.rpcUrl}: {block_hash}",
            )
        return block_hash

    def _assert_new_node(self, node):
        local = node.client.local_node_info()
        assert "/ckb/alt" not in {protocol["name"] for protocol in local["protocols"]}
        assert ALERT_ID not in {
            int(protocol["id"], 16) for protocol in local["protocols"]
        }, local
        for peer in node.client.get_peers():
            assert ALERT_ID not in {
                int(protocol["id"], 16) for protocol in peer["protocols"]
            }, peer
        assert node.client.get_blockchain_info()["alerts"] == []
        assert (
            self._events(node) == []
        ), f"Removed Alert hook executed: {self._events(node)}"
        assert node.client.get_tip_header()["hash"]

    def _assert_chain_info(self, node):
        info = node.client.get_blockchain_info()
        assert isinstance(info["alerts"], list) and info["alerts"] == []
        assert info["chain"] == "ckb_dev"
        header = node.client.get_tip_header()
        if int(header["number"], 16) == 0:
            # Genesis header has epoch=0; chain info includes its epoch length.
            epoch = node.client.get_current_epoch()
            expected_epoch = int(epoch["number"], 16) | (int(epoch["length"], 16) << 40)
        else:
            expected_epoch = int(header["epoch"], 16)
        assert int(info["epoch"], 16) == expected_epoch
        assert info["median_time"] == node.client.get_block_median_time(header["hash"])
        target, overflow = compact_to_target(int(header["compact_target"], 16))
        assert not overflow and target > 1
        assert int(info["difficulty"], 16) == (1 << 256) // target
        assert isinstance(info["is_initial_block_download"], bool)
        # The fixture genesis is historical; mined blocks use current time.
        assert info["is_initial_block_download"] == (int(header["number"], 16) == 0)
        return info

    @staticmethod
    def _events(node):
        if not node.alert_events.exists():
            return []
        # A hook write can be in progress. Only complete JSONL records count.
        lines = node.alert_events.read_text().splitlines(keepends=True)
        return [json.loads(line) for line in lines if line.endswith("\n")]

    def _wait_events(self, node, message, count):
        def received():
            events = self._events(node)
            assert all(event["message"] == message for event in events), events
            assert (
                len(events) <= count
            ), f"Unexpected extra Alert hook invocation: {events}"
            return len(events) == count

        self._wait(received, f"{count} Alert hook call(s) at {node.rpcUrl}")

    def _alert(self, notice_until):
        cli = Path(
            os.environ.get("CKB_TEST_CLI", ROOT / "download/current/ckb-cli")
        ).resolve()
        identifier = uuid.uuid4().int & 0xFFFFFFFF
        return signed_alert(
            cli,
            alert_id=identifier,
            message=f"alert-removal-{identifier}",
            notice_until=notice_until,
        )

    def _send_alert(self, node, alert):
        assert node.client.call("send_alert", [alert]) is None
        self._wait_events(node, alert["message"], 1)

    def _observe_no_alert(self, target, observers=()):
        deadline = time.monotonic() + QUIET_SECONDS
        while time.monotonic() < deadline:
            self._assert_new_node(target)
            for observer in observers:
                assert self._events(observer) == [], "Alert crossed the new relay"
                assert observer.client.get_blockchain_info()["alerts"] == []
                assert observer.client.get_tip_header()["hash"]
            time.sleep(0.1)

    def _relay_topology(self, old_relay, mode="default"):
        sender = self._node(old=True, mode="legacy")
        relay = self._node(old=old_relay, mode="legacy" if old_relay else mode)
        observer = self._node(old=True, mode="legacy")
        self._prime([sender, relay, observer])
        self._connect(sender, relay)
        self._connect(observer, relay)
        self._assert_topology(
            {sender: [relay], relay: [sender, observer], observer: [relay]}
        )
        if old_relay:
            self._wait(
                lambda: ALERT_ID in self._peer_protocols(sender, relay)
                and ALERT_ID in self._peer_protocols(observer, relay),
                "old relay Alert negotiation",
            )
        self._advance(sender, [relay, observer])
        return sender, relay, observer
