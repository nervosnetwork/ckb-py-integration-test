"""Budget exhaustion preserves RBF state and does not leak rejection events."""

import copy
import json
import select
import socket
import time
from pathlib import Path

import requests
import toml


from framework.basic import CkbTest

from test_cases.network_verification import workload


def _read_json_line(connection, buffer, timeout):
    """Read one bounded TCP frame, retaining partial/coalesced data for the next read."""
    deadline = time.monotonic() + timeout
    while b"\n" not in buffer:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([connection], [], [], remaining)[0]:
            return None, buffer
        data = connection.recv(65536)
        assert data, "rejected_transaction stream closed"
        buffer += data
    line, buffer = buffer.split(b"\n", 1)
    return json.loads(line), buffer


class TestBudgetReplacement(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 发送节点：12 秒上限，正常接受并转发慢交易。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.source = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestBudgetReplacement/source_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.source)
            cls.source.prepare()
            config_path = Path(cls.source.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.source)
            cls.source.start()
            cls.started_nodes.append(cls.source)
            assert cls.source.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：1ms 上限，触发网络慢交易超时。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestBudgetReplacement/receiver_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.receiver)
            cls.receiver.prepare()
            config_path = Path(cls.receiver.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=1,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.receiver)
            cls.receiver.start()
            cls.started_nodes.append(cls.receiver)
            assert cls.receiver.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 第二个转发节点：重新发送同一交易，验证可重试。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.other = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestBudgetReplacement/other_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.other)
            cls.other.prepare()
            config_path = Path(cls.other.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.other)
            cls.other.start()
            cls.started_nodes.append(cls.other)
            assert cls.other.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.source, 30)
            workload.sync_blocks(cls.source, cls.receiver, 1, 30)
            workload.sync_blocks(cls.source, cls.other, 1, 30)
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.started_nodes):
            node.stop()
        for node in cls.nodes:
            workload.save_node_logs(node)
            node.clean()

    # TEST-MAP: VT2-48
    # TEST-MAP: VT2-29
    def test_network_timeout_preserves_parent_child_until_same_candidate_local_rbf(
        self,
    ):
        """
        1. 接收节点先保存父子交易，记录池、交易体和链尖端。
        2. 监听拒绝事件，再网络转发慢替换交易；检查超时不破坏父子状态。
        3. 用普通 RBF 事件校验订阅，并在有限观察窗口内排除慢交易的拒绝事件。
        4. 本地提交同一慢替换交易，检查父子交易被替换且链尖端不变。
        """
        source = self.source
        receiver = self.receiver
        other = self.other
        client = receiver.getClient()

        # 1. 建立父子交易，保存完整池状态、交易体和链尖端作为对照。
        parent_tx = workload.build_transaction(receiver)
        parent = client.send_transaction(parent_tx)
        child_tx = copy.deepcopy(parent_tx)
        child_tx["inputs"][0]["previous_output"] = {
            "tx_hash": parent,
            "index": "0x0",
        }
        child_tx["outputs"][0]["capacity"] = hex(6498 * workload.CKB)
        child = client.send_transaction(child_tx)
        before = client.get_raw_tx_pool(True)
        bodies = {key: client.get_transaction(key) for key in (parent, child)}
        tip = client.get_tip_header()["hash"]
        # 2. 建立拒绝事件订阅并校验 ACK，再转发慢替换交易并检查原父子状态。
        host, port = toml.loads(Path(receiver.ckb_toml_path).read_text())["rpc"][
            "tcp_listen_address"
        ].rsplit(":", 1)
        connection = socket.create_connection((host, int(port)), timeout=10)
        subscription_buffer = b""
        try:
            connection.sendall(
                json.dumps(
                    {
                        "id": 1,
                        "jsonrpc": "2.0",
                        "method": "subscribe",
                        "params": ["rejected_transaction"],
                    }
                ).encode()
                + b"\n"
            )
            ack, subscription_buffer = _read_json_line(
                connection, subscription_buffer, 10
            )
            assert ack["id"] == 1 and ack.get("result") is not None, ack
            subscription_id = ack["result"]
            candidate = workload.build_transaction(source, slow=True)
            source.connected(receiver)
            workload.wait_for(
                lambda: any(
                    peer["node_id"] == source.get_peer_id()
                    for peer in receiver.getClient().get_peers()
                ),
                "expected peer connection",
            )
            tx_hash = source.getClient().send_transaction(candidate)
            workload.assert_pool_transaction(
                source, tx_hash, candidate, workload.BUDGET_CYCLES
            )
            workload.assert_remote_budget_refusal(receiver, tx_hash)
            # 用另一节点重发，并观察新的 GetRelayTransactions 和再次超时。
            refusals = len(workload.remote_refusals(receiver, tx_hash))
            requests_before = len(workload.transaction_requests(other, tx_hash))
            assert refusals >= 1
            other.connected(receiver)
            workload.wait_for(
                lambda: any(
                    peer["node_id"] == other.get_peer_id()
                    for peer in receiver.getClient().get_peers()
                ),
                "expected peer connection",
            )
            assert other.getClient().send_transaction(candidate) == tx_hash
            workload.wait_for(
                lambda: len(workload.transaction_requests(other, tx_hash))
                > requests_before,
                f"fresh GetRelayTransactions for {tx_hash}",
                45,
            )
            workload.assert_remote_budget_refusal(receiver, tx_hash, refusals + 1)
            assert client.get_raw_tx_pool(True) == before
            assert {
                key: client.get_transaction(key) for key in (parent, child)
            } == bodies
            assert client.get_tip_header()["hash"] == tip

            # 3. 触发普通 RBF 拒绝事件，验证订阅有效，并检查没有慢交易拒绝事件。
            normal = workload.build_transaction(receiver, cell=2)
            normal_hash = client.send_transaction(normal)
            replacement = copy.deepcopy(normal)
            replacement["outputs"][0]["capacity"] = hex(6490 * workload.CKB)
            client.send_transaction(replacement)
            deadline, positive = time.monotonic() + 15, False
            while time.monotonic() < deadline and not positive:
                value, subscription_buffer = _read_json_line(
                    connection,
                    subscription_buffer,
                    max(0.01, deadline - time.monotonic()),
                )
                if value is None:
                    event = None
                else:
                    assert value["params"]["subscription"] == subscription_id
                    result = value["params"]["result"]
                    event = json.loads(result) if isinstance(result, str) else result
                assert event is not None, "ordinary RBF positive event missing"
                event_hash = event[0]["transaction"]["hash"]
                assert event_hash != tx_hash, event
                positive = (
                    event_hash == normal_hash and event[1]["type"] == "RBFRejected"
                )
            assert positive
            # 在同一订阅已收到正向事件后，再观察一个有限的负向窗口。
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                value, subscription_buffer = _read_json_line(
                    connection, subscription_buffer, deadline - time.monotonic()
                )
                if value is None:
                    event = None
                else:
                    assert value["params"]["subscription"] == subscription_id
                    result = value["params"]["result"]
                    event = json.loads(result) if isinstance(result, str) else result
                if event is None:
                    break
                assert event[0]["transaction"]["hash"] != tx_hash, event
            assert {
                key: client.get_transaction(key) for key in (parent, child)
            } == bodies

            # 4. 本地提交同一候选，检查原父子交易均因 RBF 被替换，链尖端不变。
            assert client.send_transaction(candidate) == tx_hash
            workload.assert_pool_transaction(
                receiver, tx_hash, candidate, workload.BUDGET_CYCLES
            )
            for old in (parent, child):
                status = client.get_transaction(old)["tx_status"]
                assert (
                    status["status"] == "rejected" and "RBFRejected" in status["reason"]
                )
            assert client.get_tip_header()["hash"] == tip
        finally:
            connection.close()
        self.did_pass = True


class TestLocalConflictRecovery(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 发送节点：12 秒上限，正常接受并转发慢交易。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.source = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestLocalConflictRecovery/source_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.source)
            cls.source.prepare()
            config_path = Path(cls.source.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.source)
            cls.source.start()
            cls.started_nodes.append(cls.source)
            assert cls.source.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：1ms 上限，触发网络慢交易超时。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestLocalConflictRecovery/receiver_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.receiver)
            cls.receiver.prepare()
            config_path = Path(cls.receiver.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=1,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.receiver)
            cls.receiver.start()
            cls.started_nodes.append(cls.receiver)
            assert cls.receiver.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.source, 30)
            workload.sync_blocks(cls.source, cls.receiver, 1, 30)
        except Exception:
            for node in cls.nodes:
                log_path = Path(node.ckb_dir) / "node.log"
                if log_path.is_file():
                    print(
                        f"\n--- startup failure: {log_path} ---\n"
                        f"{log_path.read_text(errors='replace')[-8000:]}"
                    )
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.started_nodes):
            node.stop()
        for node in cls.nodes:
            workload.save_node_logs(node)
            node.clean()

    # TEST-MAP: VT2-45
    def test_locally_rejected_slow_transaction_recovers_when_conflict_is_replaced(self):
        """
        1. 慢交易 A 网络超时后隔离对端；快速交易 B 占用 A 的输入。
        2. 本地提交 A，检查手续费不足以替换 B 而返回 RBFRejected。
        3. 交易 C 替换 B 后释放 A 的输入；不重交 A，检查它自动恢复并确认。
        """
        source = self.source
        receiver = self.receiver
        client = receiver.getClient()

        # 1. 慢交易 A 网络超时后隔离对端，再提交占用其输入的快速交易 B。
        candidate = workload.build_transaction(source, slow=True)
        source.connected(receiver)
        workload.wait_for(
            lambda: any(
                peer["node_id"] == source.get_peer_id()
                for peer in receiver.getClient().get_peers()
            ),
            "expected peer connection",
        )
        tx_hash = source.getClient().send_transaction(candidate)
        workload.assert_pool_transaction(
            source, tx_hash, candidate, workload.BUDGET_CYCLES
        )
        workload.assert_remote_budget_refusal(receiver, tx_hash)
        client.remove_node(source.get_peer_id())
        workload.wait_for(
            lambda: receiver.get_connected_count() == 0, "isolate local retry"
        )
        # A 花费 X、手续费 100 CKB；B 花费 X/Y、手续费 150 CKB。
        # 后续 C 只花费 Y、手续费 200 CKB，替换 B 后释放 X。
        b = workload.build_transaction(receiver)
        y = workload.build_transaction(receiver, cell=1)
        b["inputs"].extend(y["inputs"])
        b["outputs"][0]["capacity"] = hex((13000 - 150) * workload.CKB)
        b_hash = client.send_transaction(b)

        # 2. 本地提交 A，检查手续费不足以替换 B，返回 RBFRejected 且 A 未入池。
        response = requests.post(
            client.url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "send_transaction",
                "params": [candidate, "passthrough"],
            },
            timeout=30,
        )
        response.raise_for_status()
        error = response.json()["error"]
        assert error["code"] == -1111 and "RBFRejected" in error["data"], error
        assert tx_hash not in workload.pool_transactions(client)

        # 3. 用 C 替换 B，保持断连且不重交 A，检查 A 自动恢复并与 C 一起确认。
        c = copy.deepcopy(y)
        c["outputs"][0]["capacity"] = hex((6500 - 200) * workload.CKB)
        c_hash = client.send_transaction(c)
        # 不再提交、通知或转发 A，确保观察的是本地队列自动恢复。
        workload.assert_pool_transaction(
            receiver, tx_hash, candidate, workload.BUDGET_CYCLES
        )
        assert b_hash not in workload.pool_transactions(client)
        assert c_hash in workload.pool_transactions(client)
        assert receiver.get_connected_count() == 0
        self.Miner.miner_until_tx_committed(receiver, tx_hash)
        self.Miner.miner_until_tx_committed(receiver, c_hash)
        assert client.get_transaction(tx_hash)["tx_status"]["status"] == "committed"
        assert client.get_transaction(c_hash)["tx_status"]["status"] == "committed"
        self.did_pass = True
