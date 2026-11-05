"""Public RPC cancellation, concurrency and subscription boundaries."""

import copy
import http.client
import json
import select
import socket
import time
from pathlib import Path
from urllib.parse import urlsplit

import toml

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import Transactions
from test_cases.tx_pool_refactor.dao_precheck.support import transaction_hash
from test_cases.tx_pool_refactor.subscription_client import SubscriptionClient
from test_cases.tx_pool_refactor.tests.test_50_pr5316_capacity_metrics import (
    _metric,
    _metrics,
)


def pending_submission(node, tx):
    url = urlsplit(node.rpcUrl)
    connection = socket.create_connection((url.hostname, url.port), timeout=5)
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "send_transaction",
            "params": [tx, "passthrough"],
        }
    ).encode()
    connection.sendall(
        (
            f"POST / HTTP/1.1\r\nHost: {url.netloc}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        ).encode()
        + body
    )
    return connection


class TestPoolRpcProgress(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        try:
            for name in ("subject", "producer"):
                rpc, p2p, tcp, metrics = workload.unused_ports(4)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/rpc_progress/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                cls.nodes.append(node)
                node.prepare()
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                # Four finite stress groups need about 2.016e12 cycles in total.
                # This cycle ceiling is independent of the unchanged default CPU cap.
                config["tx_pool"]["max_tx_verify_cycles"] = 4_000_000_000_000
                config["tx_pool"]["max_tx_verify_workers"] = 2
                config["network"]["connect_outbound_interval_secs"] = 0
                config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp}"
                if "Subscription" not in config["rpc"]["modules"]:
                    config["rpc"]["modules"].append("Subscription")
                config.setdefault("metrics", {}).setdefault("exporter", {})[
                    "prometheus"
                ] = {
                    "target": {
                        "type": "prometheus",
                        "listen_address": f"127.0.0.1:{metrics}",
                    }
                }
                path.write_text(toml.dumps(config))
                workload.add_genesis_test_cells(
                    node, program_names=("cpu_budget_stress_1b",)
                )
                path = Path(node.ckb_specs_config_path)
                spec = toml.loads(path.read_text())
                spec["params"]["max_block_cycles"] = 4_000_000_000_000
                path.write_text(toml.dumps(spec))
                node.start()
                if name == "subject":
                    cls.metrics_port, cls.tcp_port = metrics, tcp
            cls.node, cls.producer = cls.nodes
            cls.Miner.make_tip_height_number(cls.producer, 30)
            workload.sync_blocks(cls.producer, cls.node, 1, 30)
            cls.base_hash = cls.node.getClient().get_tip_header()["hash"]
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.nodes):
            node.stop()
            workload.save_node_logs(node)
            node.clean()

    def setup_method(self, method):
        self.did_pass = None
        for node in self.nodes:
            client = node.getClient()
            if client.get_tip_header()["hash"] != self.base_hash:
                client.truncate(self.base_hash)
            client.clear_tx_pool()
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == self.base_hash,
                "base tip restored",
            )

    def teardown_method(self, method):
        pass

    def active(self):
        return _metric(
            _metrics(self.metrics_port),
            "ckb_tx_pool_pipeline_residency",
            "resource",
            "active_work",
        )

    def slow(self, cell, groups, tag):
        tx = workload.build_budget_transaction(self.node, cell=cell)
        output = tx["outputs"][0]
        tx["outputs"] = [copy.deepcopy(output) for _ in range(groups)]
        for index, output in enumerate(tx["outputs"]):
            output["capacity"] = hex(6400 * workload.CKB // groups)
            output["type"]["args"] = "0x" + bytes([tag, index]).hex()
        tx["outputs_data"] = ["0x"] * groups
        return tx

    # TEST-MAP: NTD-48
    def test_clear_verify_queue_removes_waiter_without_resurrecting_it(self):
        """
        1. 保留一笔 Accepted，notify 缺失父交易的合法子交易并确认 orphan=1。
        2. 清验证队列，检查 orphan 归零且原 Accepted/body 不变。
        3. 补齐父交易，等待正常准入；原 waiter 不复活且无公开 body。
        4. 显式重新 notify 子交易作为有效性正对照，确认它能正常接受。
        """
        # 1. 未知父交易与子交易都用无需签名的固定脚本，子交易确实合法。
        txs = Transactions(self.node, 48)
        client = self.node.getClient()
        accepted = txs.build([0])
        parent = txs.build([1])
        child = txs.build([txs.point(parent)])
        accepted_hash = client.send_transaction(accepted)
        child_hash = client.notify_transaction(child)
        workload.wait_for(
            lambda: int(client.tx_pool_info()["orphan"], 16) == 1,
            "valid missing-parent waiter",
        )
        # 2. 队列清理与 Accepted 成员边界分别检查。
        client.clear_tx_verify_queue()
        workload.wait_for(
            lambda: int(client.tx_pool_info()["orphan"], 16) == 0, "waiter cleared"
        )
        workload.assert_pool_transaction(
            self.node, accepted_hash, accepted, workload.FAST_CYCLES
        )
        # 3. 父交易完成后给真实唤醒窗口，原 waiter 始终不再出现。
        parent_hash = client.send_transaction(parent)
        workload.assert_pool_transaction(
            self.node, parent_hash, parent, workload.FAST_CYCLES
        )
        until = time.monotonic() + 1
        while time.monotonic() < until:
            assert set(workload.pool_transactions(client)) == {
                accepted_hash,
                parent_hash,
            }
            assert client.get_transaction(child_hash)["transaction"] is None
            assert int(client.tx_pool_info()["orphan"], 16) == 0
            time.sleep(0.1)
        # 4. 重新提交成功证明此前没有复活并非因为 waiter 本来无效。
        assert client.notify_transaction(child) == child_hash
        workload.assert_pool_transaction(
            self.node, child_hash, child, workload.FAST_CYCLES
        )
        self.did_pass = True

    # TEST-MAP: NTD-18
    # TEST-MAP: NTD-96
    def test_pending_local_verification_allows_reads_and_chain_progress(self):
        """
        1. 准备独立有效块和两个合法慢交易，以 active 和未返回 HTTP 确认并发处理中。
        2. 独立连接查询链/池并提交有效块，要求慢请求仍未完成时链顶前进。
        3. 不重交目标，逐 hash 核对最终 Accepted/body 和 HTTP 结局。
        """
        # 1. 预先产生块，避免把出块夹具耗时混入并发观察窗口。
        producer = self.producer.getClient()
        block_hash = producer.generate_block_with_template(
            producer.get_block_template()
        )
        block = producer.get_block(block_hash)
        block["header"].pop("hash")
        for tx in block["transactions"]:
            tx.pop("hash", None)
        txs = [self.slow(index, 2, 10) for index in range(2)]
        connections = [pending_submission(self.node, tx) for tx in txs]
        client = self.node.getClient()
        try:
            workload.wait_for(
                lambda: self.active() >= 2,
                "two local verifications actually active",
                15,
            )
            assert not select.select(connections, [], [], 0)[
                0
            ], "fixture not reached: HTTP already completed"
            # 2. 查询和链推进必须发生在两个慢请求仍未返回的窗口内。
            started = time.monotonic()
            assert (
                client.call("get_tip_header", [], try_count=1, timeout=5)["hash"]
                == self.base_hash
            )
            assert (
                client.call("tx_pool_info", [], try_count=1, timeout=5)["tip_hash"]
                == self.base_hash
            )
            assert (
                client.call("submit_block", ["0x0", block], try_count=1, timeout=10)
                == block_hash
            )
            workload.wait_for(
                lambda: client.get_tip_header()["hash"] == block_hash,
                "chain progressed during verification",
                10,
            )
            assert not select.select(connections, [], [], 0)[
                0
            ], "fixture not reached: slow HTTP ended before chain progress"
            assert self.active() >= 1
            print(
                json.dumps(
                    {
                        "read_and_chain_seconds": time.monotonic() - started,
                        "tip": block_hash,
                    }
                )
            )
            # 3. HTTP 200/408 均保留，成功准入只由原 hash/body 确认。
            for connection, tx in zip(connections, txs):
                connection.settimeout(40)
                response = http.client.HTTPResponse(connection)
                response.begin()
                body = response.read()
                assert response.status in (200, 408), (response.status, body)
                if response.status == 200:
                    assert json.loads(body)["result"] == transaction_hash(tx)
                workload.wait_for(
                    lambda: transaction_hash(tx) in workload.pool_transactions(client),
                    "original slow transaction settled",
                    600,
                )
                actual = client.get_transaction(transaction_hash(tx))
                assert actual["transaction"] == {**tx, "hash": transaction_hash(tx)}
                print(
                    json.dumps(
                        {
                            "hash": transaction_hash(tx),
                            "http_status": response.status,
                            "status": actual["tx_status"],
                        }
                    )
                )
            workload.wait_for(lambda: self.active() == 0, "all local work settled", 15)
        finally:
            for connection in connections:
                connection.close()
        self.did_pass = True

    # TEST-MAP: NTD-95
    def test_client_disconnect_and_service_408_do_not_cancel_admission(self):
        """
        1. 分别发起两个独立慢交易，确认 active 上升且 HTTP 尚未完成。
        2. 一项主动断开客户端；另一项等待真实 HTTP 408，结束时目标仍未入池。
        3. 不重交、不连接 peer，等待相同 hash/body 最终入池并清空 active。
        """
        client = self.node.getClient()
        for cell, mode in enumerate(("disconnect", "http408")):
            # 1. 多个有限 VM 组延长本地请求，不修改网络默认 CPU 上限。
            tx = self.slow(cell, 4, 20)
            tx_hash = transaction_hash(tx)
            connection = pending_submission(self.node, tx)
            started = time.monotonic()
            try:
                workload.wait_for(
                    lambda: self.active() > 0, "local request actually active", 15
                )
                assert not select.select([connection], [], [], 0)[
                    0
                ], "fixture not reached: request already ended"
                # 2. 408 必须来自服务端，客户端 read timeout 按失败保留。
                if mode == "disconnect":
                    connection.shutdown(socket.SHUT_RDWR)
                    connection.close()
                else:
                    connection.settimeout(45)
                    response = http.client.HTTPResponse(connection)
                    response.begin()
                    payload = response.read()
                    assert response.status == 408, (
                        "fixture not reached: expected real HTTP 408",
                        response.status,
                        payload,
                    )
                assert tx_hash not in workload.pool_transactions(
                    client
                ), "fixture not reached: already Accepted at HTTP end"
                assert self.active() > 0
                # 3. 仅查询原请求结果；不使用第二次提交让测试伪成功。
                workload.wait_for(
                    lambda: tx_hash in workload.pool_transactions(client),
                    f"admission survives {mode}",
                    600,
                )
                response = client.get_transaction(tx_hash)
                assert response["transaction"] == {**tx, "hash": tx_hash}
                assert response["tx_status"]["status"] in ("pending", "proposed")
                workload.wait_for(
                    lambda: self.active() == 0, "request resources settled", 15
                )
                assert self.node.get_connected_count() == 0
                print(
                    json.dumps(
                        {
                            "mode": mode,
                            "hash": tx_hash,
                            "seconds": time.monotonic() - started,
                        }
                    )
                )
            finally:
                connection.close()
        self.did_pass = True

    # TEST-MAP: NTD-98
    def test_subscriptions_have_no_history_and_resubscribe_sees_new_events(self):
        """
        1. 无订阅时产生旧交易，再建立两个订阅并确认 ACK。
        2. 提交新交易，两个订阅收到相同 hash/body，均没有旧事件重放。
        3. 取消第一订阅，产生中间事件；重新订阅后只收到新标记交易。
        """
        client = self.node.getClient()
        # 1. 旧事件真实产生于两个订阅建立前。
        old = workload.build_transaction(self.node, cell=0)
        old_hash = client.send_transaction(old)
        subscribers = [SubscriptionClient(self.tcp_port) for _ in range(2)]
        try:
            ids = [sub.call("subscribe", ["new_transaction"]) for sub in subscribers]
            for sub in subscribers:
                assert sub.collect(0.3) == [], "new subscription replayed history"
            # 2. 相同新事件必须在两个已确认健康订阅中完整可见。
            fresh = workload.build_transaction(self.node, cell=1)
            fresh_hash = client.send_transaction(fresh)
            for sub in subscribers:
                events = sub.collect(2)
                bodies = [sub.transaction(event) for event in events]
                assert bodies == [{**fresh, "hash": fresh_hash}], events
                assert all(body["hash"] != old_hash for body in bodies)
            # 3. unsubscribe ACK 定义边界，取消期间的事件也不补发。
            assert subscribers[0].call("unsubscribe", [ids[0]]) is True
            middle = workload.build_transaction(self.node, cell=2)
            middle_hash = client.send_transaction(middle)
            assert subscribers[0].collect(0.5) == []
            assert [
                subscribers[1].transaction(e)["hash"] for e in subscribers[1].collect(1)
            ] == [middle_hash]
            subscribers[0].call("subscribe", ["new_transaction"])
            assert subscribers[0].collect(0.5) == []
            marker = workload.build_transaction(self.node, cell=3)
            marker_hash = client.send_transaction(marker)
            for sub in subscribers:
                assert [sub.transaction(e) for e in sub.collect(2)] == [
                    {**marker, "hash": marker_hash}
                ]
        finally:
            for sub in subscribers:
                sub.close()
        self.did_pass = True

    # TEST-MAP: NTD-85
    def test_template_parameter_contract_observations(self):
        """
        1. 固定链和池，接收一笔可提案交易。
        2. 分别请求默认及三类模板参数，记录完整公开响应。
        3. 检查只读调用未改变池和链；参数上限语义保持待确认。
        """
        # 1. 避免以空模板掩盖 proposals_limit 的响应差异。
        client = self.node.getClient()
        tx_hash = client.send_transaction(workload.build_transaction(self.node, cell=5))
        workload.wait_for(
            lambda: tx_hash[:22] in client.get_block_template()["proposals"],
            "proposal available",
        )
        pool, tip = client.get_raw_tx_pool(True), client.get_tip_header()["hash"]
        # 2. 表征用例只记录现状；没有把当前忽略参数的实现冻结成需求。
        observations = []
        for params in ([], ["0x1"], [None, "0x0"], [None, None, "0x0"]):
            result = client.call("get_block_template", params, try_count=1, timeout=10)
            observations.append({"params": params, "response": result})
            assert result["parent_hash"] == tip
        path = Path(self.node.ckb_dir) / "template-parameter-observations.json"
        path.write_text(
            json.dumps(
                {
                    "contract_status": "pending_product_decision",
                    "observations": observations,
                },
                indent=2,
            )
        )
        print(path.read_text())
        # 3. 只读边界可独立判定，参数限制正确性不在此自报通过。
        assert client.get_raw_tx_pool(True) == pool
        assert client.get_tip_header()["hash"] == tip
        self.did_pass = True
