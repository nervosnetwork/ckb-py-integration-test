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
from test_cases.tx_pool_refactor.test_capacity_metrics import (
    _metric,
    _metrics,
)


from test_cases.tx_pool_refactor.rpc_progress_fixture import (
    RpcProgressFixture,
    pending_submission,
)


class TestPoolRpcProgress(RpcProgressFixture):
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
