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
from test_cases.network_verification.runtime import requires_target
from test_cases.network_verification import workload as w


class RejectionSubscription:
    """Bounded TCP reader; an acknowledged subscription precedes every trigger."""

    def __init__(self, node):
        host, port = toml.loads(Path(node.ckb_toml_path).read_text())["rpc"][
            "tcp_listen_address"
        ].rsplit(":", 1)
        self.socket = socket.create_connection((host, int(port)), timeout=10)
        self.buffer = b""
        self.socket.sendall(
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
        ack = self.read(10)
        assert ack["id"] == 1 and ack.get("result") is not None, ack
        self.subscription = ack["result"]

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.socket], [], [], remaining)[0]:
                return None
            data = self.socket.recv(65536)
            assert data, "rejected_transaction stream closed"
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def event(self, timeout):
        value = self.read(timeout)
        if value is None:
            return None
        assert value["params"]["subscription"] == self.subscription
        result = value["params"]["result"]
        return json.loads(result) if isinstance(result, str) else result


@requires_target
class TestBudgetReplacement(CkbTest):
    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-48
    # TEST-MAP: VT2-29
    # TEST-EVIDENCE-BEGIN: VT2-29
    # Evidence | Case: VT2-29 (covered)
    # Evidence | Setup: Acknowledged rejected_transaction TCP subscription before trigger; receiver cap1; slow candidate plus independent normal RBF control.
    # Evidence | Trigger: Slow network refusal, fresh same-hash request, then normal high-fee replacement; consume same stream until explicit positive event and drain0.5s.
    # Evidence | Observe: Target slow hash absent from all observed events; positive normal_hash/RBFRejected event and live stream asserted.
    # Evidence | Assert: assert ack["id"] == 1 and ack.get("result") is not None, ack
    # Evidence | Assert: assert data, "rejected_transaction stream closed"
    # Evidence | Assert: assert value["params"]["subscription"] == self.subscription
    # Evidence | Assert: assert event is not None, "ordinary RBF positive event missing"
    # Evidence | Assert: assert event_hash != tx_hash, event
    # Evidence | Assert: assert positive
    # Evidence | Assert: assert event[0]["transaction"]["hash"] != tx_hash, event
    # Evidence | Assert: assert refusals >= 1
    # Evidence | Assert: assert source.getClient().send_transaction(tx) == tx_hash
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(transaction_requests(source, tx_hash)) > requests_before,
    # Evidence |         f"fresh GetRelayTransactions for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Limit: Finite negative window plus positive same-stream barrier; no proof of private TryFrom execution or arbitrarily delayed event absence.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-29
    # TEST-EVIDENCE-BEGIN: VT2-48
    # Evidence | Case: VT2-48 (covered)
    # Evidence | Setup: Accepted parentA/childC on fixed tip with complete verbose pool and response snapshots; slow B spends parent input with higher fee.
    # Evidence | Trigger: Relay B at cap1, fresh re-request, compare unchanged A/C, then locally submit exactly same B as successful replacement control.
    # Evidence | Observe: Full pool and bodies/status/cycles snapshots unchanged before local success; B unknown/nonmember; then B admitted and both A/C RBFRejected; tip fixed.
    # Evidence | Assert: assert client.get_raw_tx_pool(True) == before
    # Evidence | Assert: assert {
    # Evidence |                     key: client.get_transaction(key) for key in (parent, child)
    # Evidence |                 } == bodies
    # Evidence | Assert: assert client.get_tip_header()["hash"] == tip
    # Evidence | Assert: assert {
    # Evidence |                     key: client.get_transaction(key) for key in (parent, child)
    # Evidence |                 } == bodies
    # Evidence | Assert: assert client.send_transaction(candidate) == tx_hash
    # Evidence | Assert: assert (
    # Evidence |                         status["status"] == "rejected"
    # Evidence |                         and "RBFRejected" in status["reason"]
    # Evidence |                     )
    # Evidence | Assert: assert client.get_tip_header()["hash"] == tip
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(remote_refusals(node, tx_hash)) >= count,
    # Evidence |         f"processed remote CPU-budget refusal {count} for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Assert: assert tx_hash not in owners(client)
    # Evidence | Assert: assert result["tx_status"]["status"] == "unknown", result
    # Evidence | Assert: assert result["transaction"] is None
    # Evidence | Assert: assert client.get_banned_addresses() == []
    # Evidence | Assert: assert node.get_connected_count() >= 1
    # Evidence | Assert: assert refusals >= 1
    # Evidence | Assert: assert source.getClient().send_transaction(tx) == tx_hash
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(transaction_requests(source, tx_hash)) > requests_before,
    # Evidence |         f"fresh GetRelayTransactions for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Limit: Public pool-state protection, not private spend-owner map.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-48
    def test_network_timeout_preserves_parent_child_until_same_candidate_local_rbf(
        self,
    ):
        with w.nodes(12_000, 1, 12_000) as (source, receiver, other):
            client = receiver.getClient()
            parent_tx = w.transaction(receiver)
            parent = client.send_transaction(parent_tx)
            child_tx = copy.deepcopy(parent_tx)
            child_tx["inputs"][0]["previous_output"] = {
                "tx_hash": parent,
                "index": "0x0",
            }
            child_tx["outputs"][0]["capacity"] = hex(6498 * w.CKB)
            child = client.send_transaction(child_tx)
            before = client.get_raw_tx_pool(True)
            bodies = {key: client.get_transaction(key) for key in (parent, child)}
            tip = client.get_tip_header()["hash"]
            subscription = RejectionSubscription(receiver)
            try:
                candidate = w.transaction(source, slow=True)
                tx_hash = w.relay_slow(source, receiver, candidate)
                # A fresh real GetRelayTransactions confirms release of the
                # first attempt's known marker before state/event assertions.
                w.retry_slow(other, receiver, candidate, tx_hash)
                assert client.get_raw_tx_pool(True) == before
                assert {
                    key: client.get_transaction(key) for key in (parent, child)
                } == bodies
                assert client.get_tip_header()["hash"] == tip
                normal = w.transaction(receiver, cell=2)
                normal_hash = client.send_transaction(normal)
                replacement = copy.deepcopy(normal)
                replacement["outputs"][0]["capacity"] = hex(6490 * w.CKB)
                client.send_transaction(replacement)
                deadline, positive = time.monotonic() + 15, False
                while time.monotonic() < deadline and not positive:
                    event = subscription.event(max(0.01, deadline - time.monotonic()))
                    assert event is not None, "ordinary RBF positive event missing"
                    event_hash = event[0]["transaction"]["hash"]
                    assert event_hash != tx_hash, event
                    positive = (
                        event_hash == normal_hash and event[1]["type"] == "RBFRejected"
                    )
                assert positive
                # Finite negative window plus the same-stream positive barrier.
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline:
                    event = subscription.event(deadline - time.monotonic())
                    if event is None:
                        break
                    assert event[0]["transaction"]["hash"] != tx_hash, event
                assert {
                    key: client.get_transaction(key) for key in (parent, child)
                } == bodies
                assert client.send_transaction(candidate) == tx_hash
                w.accepted(receiver, tx_hash, candidate, w.BUDGET_CYCLES)
                for old in (parent, child):
                    status = client.get_transaction(old)["tx_status"]
                    assert (
                        status["status"] == "rejected"
                        and "RBFRejected" in status["reason"]
                    )
                assert client.get_tip_header()["hash"] == tip
            finally:
                subscription.socket.close()
        self.did_pass = True

    # TEST-MAP: VT2-45
    # TEST-EVIDENCE-BEGIN: VT2-45
    # Evidence | Case: VT2-45 (covered)
    # Evidence | Setup: A spendsX, slow and remotely refused at cap1; receiver then isolated. B spendsX/Y fee150CKB, A fee100, C spendsY fee200.
    # Evidence | Trigger: Submit A locally once, require RBFRejected; submit C; perform no further A submit/notify/relay while waiting for automatic readmission.
    # Evidence | Observe: A absent after local error; later exact body/cycles in pool, B gone/C present, no peers and both A/C committed.
    # Evidence | Assert: assert error["code"] == -1111 and "RBFRejected" in error["data"], error
    # Evidence | Assert: assert tx_hash not in w.owners(client)
    # Evidence | Assert: assert b_hash not in w.owners(client)
    # Evidence | Assert: assert c_hash in w.owners(client)
    # Evidence | Assert: assert receiver.get_connected_count() == 0
    # Evidence | Assert: assert client.get_transaction(tx_hash)["tx_status"]["status"] == "committed"
    # Evidence | Assert: assert client.get_transaction(c_hash)["tx_status"]["status"] == "committed"
    # Evidence | Assert: assert response["tx_status"]["status"] in ("pending", "proposed"), response
    # Evidence | Assert: assert body.pop("hash") == tx_hash
    # Evidence | Assert: assert body == tx
    # Evidence | Assert: assert int(response["cycles"], 16) == cycles
    # Evidence | Assert: assert int(owners(client)[tx_hash]["cycles"], 16) == cycles
    # Evidence | Assert: wait_for(
    # Evidence |         lambda: len(remote_refusals(node, tx_hash)) >= count,
    # Evidence |         f"processed remote CPU-budget refusal {count} for {tx_hash}",
    # Evidence |         45,
    # Evidence |     )
    # Evidence | Assert: assert tx_hash not in owners(client)
    # Evidence | Assert: assert result["tx_status"]["status"] == "unknown", result
    # Evidence | Assert: assert result["transaction"] is None
    # Evidence | Assert: assert client.get_banned_addresses() == []
    # Evidence | Assert: assert node.get_connected_count() >= 1
    # Evidence | Limit: Public automatic recovery with no-resubmit/no-peer control; private queue/cache internals not observed.
    # Evidence | Limit: Static coverage only; final frozen full run was not supplied and no per-Case pass is inferred.
    # TEST-EVIDENCE-END: VT2-45
    def test_locally_rejected_slow_transaction_recovers_when_conflict_is_replaced(self):
        with w.nodes(12_000, 1) as (source, receiver):
            client = receiver.getClient()
            candidate = w.transaction(source, slow=True)
            tx_hash = w.relay_slow(source, receiver, candidate)
            client.remove_node(source.get_peer_id())
            w.wait_for(
                lambda: receiver.get_connected_count() == 0, "isolate local retry"
            )
            # B spends X/Y, A only X. A's 100 CKB fee is valid but insufficient
            # to replace B (150 CKB). C replaces B using only Y (200 CKB).
            b = w.transaction(receiver)
            y = w.transaction(receiver, cell=1)
            b["inputs"].extend(y["inputs"])
            b["outputs"][0]["capacity"] = hex((13000 - 150) * w.CKB)
            b_hash = client.send_transaction(b)
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
            assert tx_hash not in w.owners(client)
            c = copy.deepcopy(y)
            c["outputs"][0]["capacity"] = hex((6500 - 200) * w.CKB)
            c_hash = client.send_transaction(c)
            # No further submit/notify/relay of A: this is the queued local
            # recovery route, not synchronous direct RPC admission.
            w.accepted(receiver, tx_hash, candidate, w.BUDGET_CYCLES)
            assert b_hash not in w.owners(client)
            assert c_hash in w.owners(client)
            assert receiver.get_connected_count() == 0
            self.Miner.miner_until_tx_committed(receiver, tx_hash)
            self.Miner.miner_until_tx_committed(receiver, c_hash)
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "committed"
            assert client.get_transaction(c_hash)["tx_status"]["status"] == "committed"
        self.did_pass = True
