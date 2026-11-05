"""Small real-node fixtures shared by the existing budget integration tests.

Only ordinary node RPC and node-to-node relay are used, not a synthetic P2P peer.
"""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import toml

from test_cases.network_verification.runtime import script_path

CKB = 100_000_000
BUDGET_CYCLES = 135_598_681  # Independent pre-change reference, same VM scripts/groups.
FAST_CYCLES = 537
BUDGET_RPC_TIMEOUT = 240  # Wall-clock watchdog for full local/recovery execution.


def wait_for(predicate, description, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"{description}; last observation: {last!r}")


def unused_ports(count):
    sockets = [socket.socket() for _ in range(count)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def script_hash(path):
    return (
        "0x"
        + hashlib.blake2b(
            Path(path).read_bytes(), digest_size=32, person=b"ckb-default-hash"
        ).hexdigest()
    )


def _node_process_id(node):
    port = node.rpcUrl.rsplit(":", 1)[1]
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode in (0, 1), result.stderr
    pids = set(result.stdout.split())
    if not pids:
        return None
    assert len(pids) == 1, pids
    pid = int(pids.pop())
    cwd = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout
    paths = [
        Path(line[1:]).resolve() for line in cwd.splitlines() if line.startswith("n")
    ]
    assert paths == [Path(node.ckb_dir).resolve()], (pid, paths, node.ckb_dir)
    return pid


def _process_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def stop_and_assert_process_exit(node):
    """Wait for the owning process to exit before inspecting persistence files."""
    previous = _node_process_id(node)
    assert previous is not None
    node.stop()
    wait_for(
        lambda: not _process_exists(previous), f"old node PID {previous} exited", 20
    )
    assert _node_process_id(node) is None
    return previous


def restart_and_assert_new_process(node):
    """Prove a cold process boundary with the same default budget/configuration."""
    previous = stop_and_assert_process_exit(node)
    node.start()
    current = wait_for(lambda: _node_process_id(node), "restarted node listening", 20)
    assert current != previous, (previous, current)
    print(f"cold restart: old PID {previous} exited; new PID {current}")


def add_genesis_test_cells(node, *, program_names=()):
    """Only add fixed scripts and eight 6500-CKB inputs; never start/configure nodes."""
    programs = {
        name: script_path(name)
        for name in ("always_success", "current_cycles", *program_names)
    }
    spec_path = Path(node.ckb_specs_config_path)
    spec = toml.loads(spec_path.read_text())
    for path in programs.values():
        spec["genesis"]["system_cells"].append(
            {"file": {"file": str(path)}, "create_type_id": False}
        )
    lock = {
        "code_hash": script_hash(programs["always_success"]),
        "hash_type": "data",
        "args": "0x",
    }
    spec["genesis"]["issued_cells"].extend(
        [{"capacity": 6500 * CKB, "lock": lock.copy()} for _ in range(8)]
    )
    spec_path.write_text(toml.dumps(spec))


def save_node_logs(node):
    """Copy logs/configs only; callers explicitly stop and clean their nodes."""
    if not os.environ.get("CKB_TEST_NODE_LOG_DIR"):
        return
    directory = Path(os.environ["CKB_TEST_NODE_LOG_DIR"]) / Path(node.ckb_dir).name
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("node.log", "ckb.toml", "dev.toml"):
        path = Path(node.ckb_dir) / name
        if path.is_file():
            shutil.copyfile(path, directory / name)


def sync_blocks(source, destination, first_height, last_height):
    sender = source.getClient()
    receiver = destination.getClient()
    for height in range(first_height, last_height + 1):
        block = sender.get_block_by_number(hex(height))
        expected_hash = block["header"].pop("hash")
        for tx in block["transactions"]:
            tx.pop("hash", None)
        for uncle in block["uncles"]:
            uncle["header"].pop("hash", None)
        assert (
            receiver.call(
                "submit_block",
                ["0x0", block],
                try_count=1,
                timeout=BUDGET_RPC_TIMEOUT,
            )
            == expected_hash
        )
    expected_tip = sender.get_block_hash(hex(last_height))
    # Cold reorg recovery can fully execute the stress VM before the pool tip settles.
    deadline = time.monotonic() + BUDGET_RPC_TIMEOUT
    while time.monotonic() < deadline:
        if (
            receiver.get_tip_header()["hash"] == expected_tip
            and receiver.tx_pool_info()["tip_hash"] == expected_tip
        ):
            return
        time.sleep(0.2)
    raise AssertionError(f"block receiver did not reach {expected_tip}")


def build_transaction(node, *, cell=0, slow=False, output_capacity=None):
    client = node.getClient()
    genesis = client.get_block_by_number("0x0")["transactions"][0]
    fast_hash, slow_hash = (
        script_hash(script_path(name)) for name in ("always_success", "current_cycles")
    )
    indices = [
        i
        for i, output in enumerate(genesis["outputs"])
        if output["lock"]["code_hash"] == fast_hash
        and int(output["capacity"], 16) == 6500 * CKB
    ]
    assert len(indices) == 8
    deps = []
    for code_hash in [fast_hash, slow_hash] if slow else [fast_hash]:
        index = next(
            i
            for i, data in enumerate(genesis["outputs_data"])
            if "0x"
            + hashlib.blake2b(
                bytes.fromhex(data[2:]), digest_size=32, person=b"ckb-default-hash"
            ).hexdigest()
            == code_hash
        )
        deps.append(
            {
                "out_point": {"tx_hash": genesis["hash"], "index": hex(index)},
                "dep_type": "code",
            }
        )
    lock = {"code_hash": fast_hash, "hash_type": "data", "args": "0x"}
    outputs = [
        {
            "capacity": hex(
                output_capacity
                if output_capacity is not None
                else (100 if slow else 6499) * CKB
            ),
            "lock": lock.copy(),
            "type": (
                {"code_hash": slow_hash, "hash_type": "data1", "args": f"0x{group:02x}"}
                if slow
                else None
            ),
        }
        for group in range(64 if slow else 1)
    ]
    return {
        "version": "0x0",
        "cell_deps": deps,
        "header_deps": [],
        "inputs": [
            {
                "previous_output": {
                    "tx_hash": genesis["hash"],
                    "index": hex(indices[cell]),
                },
                "since": "0x0",
            }
        ],
        "outputs": outputs,
        "outputs_data": ["0x"] * len(outputs),
        "witnesses": [],
    }


def build_budget_transaction(node, *, cell=0):
    """Finite single-VM stress fixture, with 100 CKB fee on a 6500 CKB input.

    Uses the checked-in ELF without building CKB or overriding its CPU budget.
    Its cycles are compared across admission/recovery paths, not presented as an
    independent VM cycles baseline. A real hash-specific refusal is required.
    """
    tx = build_transaction(node, cell=cell, output_capacity=6400 * CKB)
    program = script_path("cpu_budget_stress_1b")
    genesis = node.getClient().get_block_by_number("0x0")["transactions"][0]
    index = genesis["outputs_data"].index("0x" + program.read_bytes().hex())
    tx["cell_deps"].append(
        {
            "out_point": {"tx_hash": genesis["hash"], "index": hex(index)},
            "dep_type": "code",
        }
    )
    tx["outputs"][0]["type"] = {
        "code_hash": script_hash(program),
        "hash_type": "data1",
        "args": "0x",
    }
    return tx


def pool_transactions(client):
    raw = client.get_raw_tx_pool(True)
    return {**raw["pending"], **raw["proposed"]}


def assert_pool_transaction(node, tx_hash, tx, cycles, *, timeout=30):
    client = node.getClient()
    wait_for(
        lambda: tx_hash in pool_transactions(client),
        f"pool admission of {tx_hash}",
        timeout,
    )
    response = client.get_transaction(tx_hash)
    assert response["tx_status"]["status"] in ("pending", "proposed"), response
    body = dict(response["transaction"])
    assert body.pop("hash") == tx_hash
    assert body == tx
    assert int(response["cycles"], 16) == cycles
    assert int(pool_transactions(client)[tx_hash]["cycles"], 16) == cycles
    return response


def budget_rejection(line, tx_hash, source):
    """Match the actual target hash, verification stage and ingress source."""
    start = line.find("{")
    if start >= 0:
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            return None
        if (
            event.get("event") == "committed_rejection"
            and event.get("hash") == tx_hash.removeprefix("0x")
            and event.get("reason") == "ExcessiveVerifyTime"
            and event.get("stage") == "verify"
            and event.get("source") == source
        ):
            return event
        return None
    # Preserve the same observable contract on ordinary binaries with text logs.
    if (
        source == "remote"
        and tx_hash in line
        and "remote reject:" in line
        and "Transaction verification exceeded the local tx-pool time limit" in line
    ):
        return {"hash": tx_hash[2:], "reason": "ExcessiveVerifyTime", "source": source}
    return None


def budget_refusals(node, tx_hash, source):
    return [
        line
        for line in (Path(node.ckb_dir) / "node.log")
        .read_text(errors="replace")
        .splitlines()
        if budget_rejection(line, tx_hash, source)
    ]


def remote_refusals(node, tx_hash):
    return budget_refusals(node, tx_hash, "remote")


def assert_remote_budget_refusal(node, tx_hash, count=1):
    wait_for(
        lambda: len(remote_refusals(node, tx_hash)) >= count,
        f"processed remote CPU-budget refusal {count} for {tx_hash}",
        45,
    )
    client = node.getClient()
    assert tx_hash not in pool_transactions(client)
    result = client.get_transaction(tx_hash)
    assert result["tx_status"]["status"] == "unknown", result
    assert result["transaction"] is None
    assert client.get_banned_addresses() == []
    assert node.get_connected_count() >= 1


def transaction_requests(node, tx_hash):
    # Emitted by the real sender when it handles GetRelayTransactions, not by
    # this Python harness and not merely by a hash announcement.
    return [
        line
        for line in (Path(node.ckb_dir) / "node.log")
        .read_text(errors="replace")
        .splitlines()
        if "request transactions(" in line and tx_hash[2:] in line
    ]
