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
