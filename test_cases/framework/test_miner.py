import pytest

from framework.helper import miner


COMMITTED_RESPONSE = {"tx_status": {"status": "committed"}}


class FakeClient:
    def __init__(self, indexer_tips):
        self._indexer_tips = list(indexer_tips)
        self._last_indexer_tip = (
            self._indexer_tips[-1] if self._indexer_tips else None
        )
        self.indexer_tip_calls = 0
        self.tip_block_number_calls = 0

    def get_transaction(self, tx_hash):
        return COMMITTED_RESPONSE

    def get_tip_block_number(self):
        self.tip_block_number_calls += 1
        return 12

    def get_indexer_tip(self):
        self.indexer_tip_calls += 1
        if self._indexer_tips:
            self._last_indexer_tip = self._indexer_tips.pop(0)
        return self._last_indexer_tip


class FakeNode:
    def __init__(self, client):
        self._client = client

    def getClient(self):
        return self._client


def test_miner_until_tx_committed_waits_for_indexer_tip(monkeypatch):
    client = FakeClient(
        [
            {"block_number": "0xb"},
            {"block_number": "0xc"},
        ]
    )
    monkeypatch.setattr(miner.time, "sleep", lambda seconds: None)

    response = miner.miner_until_tx_committed(FakeNode(client), "0xtx")

    assert response is COMMITTED_RESPONSE
    assert client.tip_block_number_calls == 1
    assert client.indexer_tip_calls == 2


def test_wait_for_indexer_reports_last_tip_on_timeout(monkeypatch):
    client = FakeClient([{"block_number": "0xb"}])
    clock = iter([0, 0, 1])
    monkeypatch.setattr(miner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(miner.time, "sleep", lambda seconds: None)

    with pytest.raises(
        TimeoutError,
        match="target height 12.*last indexer tip: 0xb",
    ):
        miner.wait_for_indexer(FakeNode(client), 12, timeout=1)
