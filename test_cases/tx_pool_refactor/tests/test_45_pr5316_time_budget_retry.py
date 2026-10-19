"""PR #5316: a proposal time-budget refusal must permit later verification."""

import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests.test_43_pr5316_feature_matrix import _source


ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / ".ai-test-agent/current/ckb-pr-5316-current/bin-test"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _two_ports():
    with socket.socket() as rpc, socket.socket() as p2p:
        rpc.bind(("127.0.0.1", 0))
        p2p.bind(("127.0.0.1", 0))
        return rpc.getsockname()[1], p2p.getsockname()[1]


def _fixed_test_binary(log_dir):
    source = _source()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    binary = CACHE / "ckb"
    metadata = CACHE / "metadata.json"
    if binary.is_file() and metadata.is_file():
        recorded = json.loads(metadata.read_text())
        if recorded == {"head": head, "features": "test", "sha256": _sha256(binary)}:
            return binary

    CACHE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pr5316-budget-build-") as directory:
        target = Path(directory) / "target"
        command = [
            "cargo",
            "build",
            "--locked",
            "--offline",
            "--package",
            "ckb",
            "--bin",
            "ckb",
            "--features",
            "test",
            "--message-format",
            "short",
        ]
        env = os.environ.copy()
        env.pop("RUSTFLAGS", None)
        env.update(
            CARGO_TARGET_DIR=str(target),
            CARGO_INCREMENTAL="0",
            CARGO_PROFILE_DEV_DEBUG="0",
            CARGO_BUILD_JOBS="4",
            CARGO_NET_OFFLINE="true",
        )
        result = subprocess.run(
            command,
            cwd=source,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if log_dir:
            Path(log_dir, "test-feature-build.log").write_text(
                result.stdout + result.stderr
            )
        assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
        built = target / "debug/ckb"
        assert built.is_file() and os.access(built, os.X_OK)
        candidate = CACHE / "ckb.new"
        shutil.copy2(built, candidate)
        candidate.replace(binary)
    metadata.write_text(
        json.dumps(
            {
                "head": head,
                "features": "test",
                "sha256": _sha256(binary),
            },
            sort_keys=True,
        )
    )
    return binary


class TestTimeBudgetRetry(CkbTest):
    def teardown_method(self, method):
        # Keep the focused pytest log instead of copying a shared report path.
        pass

    @staticmethod
    def _recent_count(client):
        overview = client.call(
            "get_overview",
            [4],
            try_count=1,
            timeout=20,
            verbose=False,
        )
        return int(overview["pool"]["total_recent_reject_num"], 16)

    @staticmethod
    def _set_budget(node, milliseconds):
        path = Path(node.ckb_toml_path)
        config = toml.loads(path.read_text())
        config["tx_pool"]["max_tx_verify_time_ms"] = milliseconds
        config["tx_pool"]["recent_reject"] = str(
            Path(node.ckb_dir) / "data/recent-reject"
        )
        config["logger"]["filter"] = "info,ckb_tx_pool::rejection=debug"
        modules = config["rpc"]["modules"]
        if "Terminal" not in modules:
            modules.append("Terminal")
        path.write_text(toml.dumps(config))

    @staticmethod
    def _save_log(node, log_dir, name):
        if log_dir:
            Path(log_dir, name).write_bytes(
                (Path(node.ckb_dir) / "node.log").read_bytes()
            )

    @staticmethod
    def _has_budget_rejection(log_path, tx_hash):
        for line in log_path.read_text(errors="replace").splitlines():
            start = line.find('{"accounts":')
            if start < 0:
                continue
            try:
                event = json.loads(line[start:])
            except json.JSONDecodeError:
                continue
            if (
                event.get("event") == "committed_rejection"
                and event.get("hash") == tx_hash[2:]
                and event.get("reason") == "ExcessiveVerifyTime"
                and event.get("source") == "proposal"
                and event.get("stage") == "verify"
            ):
                return True
        return False

    # TEST-MAP: EVT-04
    def test_one_ms_proposal_rejection_allows_same_tx_after_restart(self):
        log_dir = os.environ.get("CKB_PR5316_TIME_LOG_DIR")
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
        binary = _fixed_test_binary(log_dir)
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "60b8e34" in version, version
        print(f"fixed test binary: {version}; sha256={_sha256(binary)}")

        rpc_port, p2p_port = _two_ports()
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_time_budget_retry/{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        shutil.copy2(binary, node.ckb_bin_path)
        self._set_budget(node, 1)
        started = False
        try:
            node.start()
            started = True
            client = node.getClient()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    info = client.call(
                        "tx_pool_info",
                        [],
                        try_count=1,
                        timeout=1,
                        verbose=False,
                    )
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                output = (Path(node.ckb_dir) / "node.log").read_text(errors="replace")
                raise AssertionError(f"CKB startup failed: {output[-3000:]}")
            assert info["tip_number"] == "0x0"
            recent_dir = Path(node.ckb_dir) / "data/recent-reject"
            assert recent_dir.is_dir(), "recent-reject endpoint did not open"
            self.Miner.make_tip_height_number(node, 30)
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=client.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(node, fund)
            candidate = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            # A terminal resolver error is a read/write control for the recent
            # endpoint; an absent endpoint would otherwise also report zero.
            control = copy.deepcopy(candidate)
            control["header_deps"] = ["0x" + "ff" * 32]
            control_hash = client.notify_transaction(control)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                control_status = client.get_transaction(control_hash)["tx_status"]
                if control_status["status"] == "rejected":
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("recordable resolver control was not rejected")
            assert "invalidheader" in control_status["reason"].lower()
            baseline_recent = self._recent_count(client)
            assert baseline_recent >= 1
            tx_hash = client.notify_transaction(candidate)
            log_path = Path(node.ckb_dir) / "node.log"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if self._has_budget_rejection(log_path, tx_hash):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("proposal did not log ExcessiveVerifyTime")
            self._save_log(node, log_dir, "low-budget-node.log")
            assert tx_hash not in client.get_raw_tx_pool(False)["pending"]
            assert tx_hash not in client.get_raw_tx_pool(False)["proposed"]
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "unknown"
            assert int(client.tx_pool_info()["verify_queue_size"], 16) == 0
            assert self._recent_count(client) == baseline_recent
            print(
                f"1 ms budget: tx={tx_hash}, rejected=ExcessiveVerifyTime, "
                f"recent={baseline_recent}"
            )

            node.stop()
            started = False
            self._set_budget(node, 12_000)
            node.start()
            started = True
            assert client.get_tip_block_number() >= 30
            assert self._recent_count(client) == baseline_recent
            assert client.notify_transaction(candidate) == tx_hash
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                response = client.get_transaction(tx_hash)
                if response["tx_status"]["status"] in ("pending", "proposed"):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError(f"same transaction not accepted: {response}")
            assert response["transaction"]["hash"] == tx_hash
            assert int(response["cycles"], 16) > 0
            assert self._recent_count(client) == baseline_recent
            self._save_log(node, log_dir, "raised-budget-node.log")
            print(f"raised budget: same tx accepted, cycles={response['cycles']}")
            self.did_pass = True
        finally:
            log_path = Path(node.ckb_dir) / "node.log"
            if log_dir and log_path.is_file():
                self._save_log(node, log_dir, "final-node.log")
            if started:
                node.stop()
            node.clean()
