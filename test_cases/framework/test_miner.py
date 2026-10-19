import pytest

from framework.helper import miner

COMMITTED_RESPONSE = {"tx_status": {"status": "committed"}}


class FakeClient:
    def __init__(self, indexer_tips):
        self._indexer_tips = list(indexer_tips)
        self._last_indexer_tip = self._indexer_tips[-1] if self._indexer_tips else None
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


@pytest.mark.parametrize("with_uncles", [False, True], ids=["empty", "uncle-proposals"])
def test_block_template_preserves_uncle_proposals(monkeypatch, with_uncles):
    monkeypatch.setattr(miner, "get_hex_timestamp", lambda: "0x1234")
    expected_uncles = (
        [
            {"header": {"number": "0x1"}, "proposals": ["0x" + "11" * 10]},
            {"header": {"number": "0x2"}, "proposals": []},
        ]
        if with_uncles
        else []
    )
    cellbase = {"witnesses": ["0x00"]}
    transaction = {"witnesses": ["0x01"]}
    template = {
        "compact_target": "0x20010000",
        "dao": "0x" + "00" * 32,
        "epoch": "0x0",
        "number": "0x3",
        "parent_hash": "0x" + "22" * 32,
        "extension": "0x1234",
        "cellbase": {"data": cellbase},
        "transactions": [{"data": transaction}],
        # The assembler omits IDs already included in the selected uncles.
        "proposals": [],
        "uncles": [
            {**uncle, "hash": "0x" + "33" * 32, "required": False}
            for uncle in expected_uncles
        ],
    }

    block = miner.block_template_transfer_to_submit_block(template, "0x1")

    assert block["uncles"] == expected_uncles
    assert block["proposals"] == []
    assert block["transactions"] == [cellbase, transaction]
    assert block["extension"] == "0x1234"
    assert block["header"]["version"] == "0x1"
    assert block["header"]["timestamp"] == "0x1234"
