"""PR #5316: production node configuration remains compatible with old files."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests.test_43_pr5316_feature_matrix import _source


ROOT = Path(__file__).resolve().parents[3]
PRODUCTION_DIR = ".ai-test-agent/current/ckb-pr-5316-current/bin-production"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fixed_production_binary_dir():
    """Build only for NTD-02, then reuse the pinned source's production binary."""
    source = _source()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    directory = ROOT / PRODUCTION_DIR
    binary = directory / "ckb"
    metadata_path = directory / "metadata.json"
    recorded = (
        json.loads(metadata_path.read_text()) if metadata_path.is_file() else None
    )
    if not (
        binary.is_file()
        and recorded
        == {
            "head": head,
            "features": "production",
            "sha256": _sha256(binary),
        }
    ):
        directory.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.pop("RUSTFLAGS", None)
        env.update(
            CARGO_TARGET_DIR=str(
                ROOT / ".ai-test-agent/current/ckb-pr-5316-current/target-production"
            ),
            CARGO_INCREMENTAL="0",
            CARGO_PROFILE_DEV_DEBUG="0",
            CARGO_BUILD_JOBS="4",
            CARGO_NET_OFFLINE="true",
        )
        command = [
            "cargo",
            "build",
            "--locked",
            "--offline",
            "--package",
            "ckb",
            "--bin",
            "ckb",
            "--message-format",
            "short",
        ]
        result = subprocess.run(
            command,
            cwd=source,
            env=env,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        assert result.returncode == 0, (result.stdout + result.stderr)[-4000:]
        built = Path(env["CARGO_TARGET_DIR"]) / "debug/ckb"
        assert built.is_file() and os.access(built, os.X_OK), built
        candidate = directory / "ckb.new"
        shutil.copy2(built, candidate)
        candidate.replace(binary)
        metadata_path.write_text(
            json.dumps(
                {
                    "head": head,
                    "features": "production",
                    "sha256": _sha256(binary),
                },
                sort_keys=True,
            )
        )
        print(f"fixed production build: exit={result.returncode}")

    cli = ROOT / "source/ckb-cli"
    assert cli.is_file() and os.access(cli, os.X_OK), cli
    if not (directory / "ckb-cli").is_file() or _sha256(
        directory / "ckb-cli"
    ) != _sha256(cli):
        shutil.copy2(cli, directory / "ckb-cli")
    assert _sha256(directory / "ckb-cli") == _sha256(cli)
    version = subprocess.run(
        [str(binary), "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert head[:7] in version, version
    print(f"fixed production binary: {version}; sha256={_sha256(binary)}")
    return PRODUCTION_DIR


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestTxPoolConfigCompatibility(CkbTest):
    def teardown_method(self, method):
        # Node logs are captured by pytest; avoid the base shared report copy.
        pass

    def _prepared_node(self, name, tx_pool_fields, binary_dir=None):
        rpc_port, p2p_port = _unused_port(), _unused_port()
        base = self.CkbNodeConfigPath.CURRENT_TEST
        binary_dir = binary_dir or os.environ.get("CKB_PR5316_BIN_DIR")
        if binary_dir is not None:
            base = self.CkbNodeConfigPath(
                base.ckb_config_path,
                base.ckb_miner_config_path,
                base.ckb_spec_path,
                binary_dir,
            )
        node = self.CkbNode.init_dev_by_port(
            base,
            f"tx_pool/pr5316_config_compat/{name}_{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        version = subprocess.run(
            [node.ckb_bin_path, "--version"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        print(f"config case {name}: binary_dir={base.ckb_bin_path}, {version}")
        path = Path(node.ckb_toml_path)
        config = toml.loads(path.read_text())
        config["tx_pool"].update(tx_pool_fields)
        path.write_text(toml.dumps(config))
        return node, config

    @contextmanager
    def _running_node(self, name, tx_pool_fields, binary_dir=None):
        node, config = self._prepared_node(name, tx_pool_fields, binary_dir)
        log_path = Path(node.ckb_dir) / "config-run.log"
        try:
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise AssertionError(
                                f"node exited {process.returncode}: "
                                f"{log_path.read_text(errors='replace')[-1500:]}"
                            )
                        try:
                            info = node.getClient().call(
                                "tx_pool_info",
                                [],
                                try_count=1,
                                timeout=1,
                                verbose=False,
                            )
                        except Exception:
                            time.sleep(0.25)
                            continue
                        assert info["tip_number"] == "0x0"
                        yield node, config
                        return
                    raise AssertionError(
                        "node did not expose tx_pool_info within 20 seconds"
                    )
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
        finally:
            node.clean()

    def _rejected_config(self, field, binary_dir):
        node, _ = self._prepared_node(
            f"invalid_{field}",
            {field: 1},
            binary_dir,
        )
        try:
            result = subprocess.run(
                [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                cwd=node.ckb_dir,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            output = result.stdout + result.stderr
            assert result.returncode != 0, f"{field} unexpectedly started node"
            assert f"unknown field `{field}`" in output, output[-1500:]
            print(f"production rejects {field}: exit={result.returncode}")
        finally:
            node.clean()

    def _rejected_bound(self, name, tx_pool_fields, reason):
        node, _ = self._prepared_node(name, tx_pool_fields)
        log_path = Path(node.ckb_dir) / "config-error.log"
        try:
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        output = log_path.read_text(errors="replace")
                        if (
                            "build tx-pool service" in output
                            or process.poll() is not None
                        ):
                            break
                        time.sleep(0.1)
                    assert "build tx-pool service" in output, output[-1500:]
                    assert reason in output, output[-1500:]
                    assert "panicked at" not in output.lower(), output[-1500:]
                    rpc_port = int(node.rpcUrl.rsplit(":", 1)[1])
                    with socket.socket() as sock:
                        sock.settimeout(1)
                        assert sock.connect_ex(("127.0.0.1", rpc_port)) != 0
                    error_line = next(
                        line
                        for line in output.splitlines()
                        if "build tx-pool service" in line
                    )
                    print(
                        f"{name}: {error_line}; rpc=closed; "
                        f"process_exit={process.poll()}"
                    )
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
        finally:
            node.clean()

    # TEST-MAP: NTD-01
    def test_released_and_obsolete_fields_keep_pool_usable(self):
        released = {
            "max_tx_pool_size": 180_000_000,
            "min_fee_rate": 1_000,
            "min_rbf_rate": 1_500,
            "max_tx_verify_cycles": 70_000_000,
            "max_ancestors_count": 25,
        }
        with self._running_node("released", released) as (node, config):
            assert "verify_ordering" not in config["tx_pool"]
            assert node.getClient().tx_pool_info()["pending"] == "0x0"
        obsolete = {
            **released,
            "max_mem_size": 17,
            "max_cycles": 23,
            "max_verify_cache_size": 31,
            "max_conflict_cache_size": 37,
            "max_committed_txs_hash_cache_size": 41,
        }
        with self._running_node("obsolete", obsolete) as (node, _):
            self.Miner.make_tip_height_number(node, 30)
            client = node.getClient()
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
            tx_hash = self.Tx.send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=5000,
                api_url=client.url,
            )
            accepted = client.get_raw_tx_pool(True)["pending"][tx_hash]
            assert int(accepted["size"], 16) > obsolete["max_mem_size"]
            assert int(accepted["cycles"], 16) > obsolete["max_cycles"]
            assert client.get_transaction(tx_hash)["tx_status"]["status"] == "pending"
            print(
                f"obsolete limits ignored: size={accepted['size']}, "
                f"cycles={accepted['cycles']}"
            )
        self.did_pass = True

    # TEST-MAP: NTD-01
    def test_legacy_ancestor_floor_and_explicit_ordering(self):
        with self._running_node("ancestor_25", {"max_ancestors_count": 25}) as (
            node,
            _,
        ):
            self.Miner.make_tip_height_number(node, 30)
            client = node.getClient()
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            parent = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=client.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(node, parent)
            for _ in range(26):
                parent = self.Tx.send_transfer_self_tx_with_input(
                    [parent],
                    ["0x0"],
                    key,
                    fee=1000,
                    api_url=client.url,
                )
            assert int(client.tx_pool_info()["pending"], 16) == 26
            assert parent in client.get_raw_tx_pool(False)["pending"]
            print("legacy ancestors=25: accepted 26-deep uncommitted chain")
        with self._running_node(
            "ancestor_1001",
            {"max_ancestors_count": 1001, "verify_ordering": "fee_rate"},
        ) as (node, _):
            assert node.getClient().tx_pool_info()["pending"] == "0x0"
            print("legacy ancestors=1001 and explicit fee_rate: node ready")
        self.did_pass = True

    # TEST-MAP: NTD-02
    def test_production_rejects_test_only_budget_fields(self):
        binary_dir = _fixed_production_binary_dir()
        with self._running_node(
            "production_defaults",
            {},
            binary_dir,
        ) as (_, config):
            for field in (
                "max_tx_verify_time_ms",
                "min_tx_verify_time_ms",
                "cycles_per_ms",
            ):
                assert field not in config["tx_pool"]
        for field in (
            "max_tx_verify_time_ms",
            "min_tx_verify_time_ms",
            "cycles_per_ms",
        ):
            self._rejected_config(field, binary_dir)
        self.did_pass = True

    # TEST-MAP: NTD-03
    def test_oversized_worker_slots_fail_cleanly(self):
        # TOML integer values stop at signed 64-bit; this is the largest
        # configuration-reachable worker count, not an internal usize::MAX fixture.
        self._rejected_bound(
            "worker_slots_overflow",
            {"max_tx_verify_workers": sys.maxsize},
            "resource",
        )
        self.did_pass = True

    # TEST-MAP: NTD-03
    def test_oversized_residency_fails_cleanly(self):
        self._rejected_bound(
            "residency_overflow",
            {"max_tx_pool_size": sys.maxsize},
            "residency bound is not representable",
        )
        self.did_pass = True

    # TEST-MAP: NTD-03
    def test_explicit_zero_workers_still_starts_pool(self):
        with self._running_node(
            "workers_zero",
            {"max_tx_verify_workers": 0},
        ) as (node, config):
            assert config["tx_pool"]["max_tx_verify_workers"] == 0
            assert (
                node.getClient().call(
                    "tx_pool_ready",
                    [],
                    try_count=1,
                    timeout=5,
                    verbose=False,
                )
                is True
            )
            print("workers_zero: configured=0, tx_pool_ready=true")
        self.did_pass = True
