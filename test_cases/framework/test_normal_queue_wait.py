"""Clock-controlled tests; no nodes or sockets are started."""

import pytest

from test_cases.tx_pool_refactor import test_20_verify_queue_regressions as queue_module


@pytest.fixture
def waiter(monkeypatch):
    case = queue_module.TestVerifyQueueRegressions()
    case.receiver = object()
    now = [0.0]
    monkeypatch.setattr(queue_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        queue_module.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    calls = []

    def configure(snapshots):
        index = [0]

        def rpc(node, method, timeout):
            calls.append((method, timeout))
            pending, queue_size, proposed, tip = snapshots[
                min(index[0], len(snapshots) - 1)
            ]
            if method == "get_raw_tx_pool":
                return {"pending": pending, "proposed": proposed}
            index[0] += 1
            return {
                "verify_queue_size": hex(queue_size),
                "proposed": hex(len(proposed)),
                "tip_hash": tip,
            }

        monkeypatch.setattr(case, "_call_rpc_quietly", rpc)

    return case, configure, calls, now


@pytest.mark.parametrize("peak", [0, 87, 278])
def test_any_observed_peak_is_valid_if_all_transactions_arrive(waiter, peak):
    case, configure, calls, _ = waiter
    configure([([], peak, [], "tip"), (["a", "b"], 0, [], "tip")])
    result = case._wait_for_normal_queue_drain(["a", "b"], "tip", timeout=1)
    assert result["received"] == 2
    assert result["observed_drain_peak"] == peak
    assert all(0 < timeout <= 1 for _, timeout in calls)


@pytest.mark.parametrize("pending,size,missing", [(["a"], 0, 1), (["a", "b"], 1, 0)])
def test_missing_transaction_or_stuck_queue_still_fails(waiter, pending, size, missing):
    case, configure, _, now = waiter
    configure([(pending, size, [], "tip")])
    with pytest.raises(AssertionError, match=f"missing={missing}, queue_size={size}"):
        case._wait_for_normal_queue_drain(["a", "b"], "tip", timeout=0.25)
    assert now[0] == 0.25


@pytest.mark.parametrize(
    "proposed,tip,error", [(["x"], "tip", "proposed"), ([], "new-tip", "tip changed")]
)
def test_proposals_or_new_blocks_invalidate_normal_only_workload(
    waiter, proposed, tip, error
):
    case, configure, _, _ = waiter
    configure([(["a"], 0, proposed, tip)])
    with pytest.raises(AssertionError, match=error):
        case._wait_for_normal_queue_drain(["a"], "tip", timeout=1)


def test_rpc_errors_are_not_silently_ignored(waiter, monkeypatch):
    case, _, _, _ = waiter

    def error(*args, **kwargs):
        raise TimeoutError("RPC timeout")

    monkeypatch.setattr(case, "_call_rpc_quietly", error)
    with pytest.raises(TimeoutError, match="RPC timeout"):
        case._wait_for_normal_queue_drain(["a"], "tip", timeout=1)


def test_rpc_result_after_deadline_is_not_a_pass(waiter, monkeypatch):
    case, configure, _, now = waiter
    configure([(["a"], 0, [], "tip")])
    rpc = case._call_rpc_quietly

    def delayed(*args, **kwargs):
        result = rpc(*args, **kwargs)
        now[0] += 0.6
        return result

    monkeypatch.setattr(case, "_call_rpc_quietly", delayed)
    with pytest.raises(AssertionError, match="did not drain"):
        case._wait_for_normal_queue_drain(["a"], "tip", timeout=1)
