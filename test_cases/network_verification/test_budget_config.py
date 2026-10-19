"""Production and test-feature network budget configuration boundaries."""

import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import toml
import pytest

from framework.basic import CkbTest
from test_cases.network_verification.runtime import (
    PRODUCTION_BIN_DIR,
    requires_test_feature,
)


def _unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.network_verification
@requires_test_feature
class TestTxPoolConfigCompatibility(CkbTest):
    def teardown_method(self, method):
        # Node logs are captured by pytest; avoid the base shared report copy.
        pass

    def _prepared_node(self, name, tx_pool_fields, binary_dir=None):
        rpc_port, p2p_port = _unused_port(), _unused_port()
        base = self.CkbNodeConfigPath.CURRENT_TEST
        if binary_dir is not None:
            base = self.CkbNodeConfigPath(
                base.ckb_config_path,
                base.ckb_miner_config_path,
                base.ckb_spec_path,
                binary_dir,
            )
        node = self.CkbNode.init_dev_by_port(
            base,
            f"tx_pool/budget_config/{name}_{rpc_port}",
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

    # TEST-MAP: VT2-32
    # TEST-EVIDENCE-BEGIN: VT2-32
    # Evidence | Case: VT2-32 (covered)
    # Evidence | Setup: Use PRODUCTION_BIN_DIR (default project-relative download/production) with its production CKB/CLI; normal configuration has no budget fields. No manifest or executable-hash comparison is performed.
    # Evidence | Trigger: Start ordinary config and query tx_pool_info; separately run production with each of three internal fields.
    # Evidence | Observe: Baseline RPC tip0 available; each field causes nonzero process exit and exact unknown-field diagnostic.
    # Evidence | Assert: assert info["tip_number"] == "0x0"
    # Evidence | Assert: raise AssertionError(
    # Evidence |                         "node did not expose tx_pool_info within 20 seconds"
    # Evidence |                     )
    # Evidence | Assert: assert field not in config["tx_pool"]
    # Evidence | Assert: assert result.returncode != 0, f"{field} unexpectedly started node"
    # Evidence | Assert: assert f"unknown field `{field}`" in output, output[-1500:]
    # Evidence | Limit: Fields loop within one method. Binary hash/distinctness/preflight and source-revision attestation are no longer suite checks; caller supplies the two builds and their provenance. Actual production/test configuration behavior remains asserted.
    # Evidence | Limit: Coverage is static; this native-pytest review does not establish current real-node per-Case outcomes.
    # TEST-EVIDENCE-END: VT2-32
    def test_production_rejects_test_only_budget_fields(self):
        production = PRODUCTION_BIN_DIR
        with self._running_node(
            "production_defaults",
            {},
            production,
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
            self._rejected_config(field, production)
        self.did_pass = True

    # TEST-MAP: VT2-33
    # TEST-EVIDENCE-BEGIN: VT2-33
    # Evidence | Case: VT2-33 (covered)
    # Evidence | Setup: Existing CURRENT_TEST selects download/current test-feature CKB/CLI; separate node directories/ports for default,1,12000,0,-1,4294967296 plus two unexposed fields.
    # Evidence | Trigger: Actual startup for allowed values; bounded subprocess rejection for invalid values and fields.
    # Evidence | Observe: Allowed RPC readiness; invalid nonzero exits identify field/range and closed RPC port; unsupported fields report unknown-field.
    # Evidence | Assert: assert info["tip_number"] == "0x0"
    # Evidence | Assert: assert node.getClient().tx_pool_info()["pending"] == "0x0"
    # Evidence | Assert: assert result.returncode != 0, output
    # Evidence | Assert: assert "max_tx_verify_time_ms" in output, output
    # Evidence | Assert: assert any(
    # Evidence |                     word in output
    # Evidence |                     for word in ("invalid value", "out of range", "nonzero")
    # Evidence |                 ), output
    # Evidence | Assert: assert (
    # Evidence |                         sock.connect_ex(
    # Evidence |                             ("127.0.0.1", int(node.rpcUrl.rsplit(":", 1)[1]))
    # Evidence |                         )
    # Evidence |                         != 0
    # Evidence |                     )
    # Evidence | Assert: assert result.returncode != 0, f"{field} unexpectedly started node"
    # Evidence | Assert: assert f"unknown field `{field}`" in output, output[-1500:]
    # Evidence | Limit: Both selector methods must run; neither alone completes the Case.
    # Evidence | Limit: Coverage is static; this native-pytest review does not establish current real-node per-Case outcomes.
    # Evidence | Limit: The budget-change control now uses the same verified cold process boundary as persistence/reorg tests.
    # TEST-EVIDENCE-END: VT2-33
    def test_budget_configuration_bounds(self):
        """Real startup matrix; test_budget_retry checks the 1ms/12000ms workload."""
        for label, fields in (
            ("default_budget", {}),
            ("minimum_budget", {"max_tx_verify_time_ms": 1}),
            ("large_budget", {"max_tx_verify_time_ms": 12_000}),
        ):
            with self._running_node(label, fields) as (node, _):
                assert node.getClient().tx_pool_info()["pending"] == "0x0"
        for value in (0, -1, 4_294_967_296):
            node, _ = self._prepared_node(
                f"invalid_budget_{value}", {"max_tx_verify_time_ms": value}
            )
            try:
                result = subprocess.run(
                    [node.ckb_bin_path, "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    text=True,
                    capture_output=True,
                    timeout=15,
                )
                output = result.stdout + result.stderr
                assert result.returncode != 0, output
                assert "max_tx_verify_time_ms" in output, output
                assert any(
                    word in output
                    for word in ("invalid value", "out of range", "nonzero")
                ), output
                with socket.socket() as sock:
                    sock.settimeout(1)
                    assert (
                        sock.connect_ex(
                            ("127.0.0.1", int(node.rpcUrl.rsplit(":", 1)[1]))
                        )
                        != 0
                    )
            finally:
                node.clean()
        for field in ("min_tx_verify_time_ms", "cycles_per_ms"):
            self._rejected_config(
                field, self.CkbNodeConfigPath.CURRENT_TEST.ckb_bin_path
            )
        self.did_pass = True
