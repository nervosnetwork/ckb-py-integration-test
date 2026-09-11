import os
import shutil
import subprocess
from pathlib import Path

import pytest
from framework.basic import CkbTest
from framework.util import get_project_root


@pytest.mark.skipif(
    os.getenv("CKB_PR5325_ENABLE") != "1",
    reason=(
        "Disabled until nervosnetwork/ckb#5325 is merged into the default CKB "
        "binary; set CKB_PR5325_ENABLE=1 when validating a PR #5325 build"
    ),
)
class TestPr5325OverflowHardening(CkbTest):
    """Integration coverage for nervosnetwork/ckb#5325."""

    @classmethod
    def setup_class(cls):
        cls.root = Path(get_project_root())
        cls.source_spec = cls.root / "source/template/ckb/v209/specs/dev.toml"
        cls.spec_dir = cls.root / "tmp/v209/pr5325_overflow_specs"
        cls.spec_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def teardown_class(cls):
        shutil.rmtree(cls.spec_dir, ignore_errors=True)

    def test_invalid_zero_genesis_epoch_length_is_rejected(self):
        spec_path = self._write_spec(
            "invalid_zero_genesis_epoch_length.toml",
            {"genesis_epoch_length = 1000": "genesis_epoch_length = 0"},
        )

        output = self._run_invalid_spec_node(spec_path, 25325, 25326)

        assert "InvalidParams" in output
        assert "genesis_epoch_length must be non-zero" in output
        assert "panicked at" not in output
        self.did_pass = True

    def test_invalid_zero_epoch_duration_target_is_rejected(self):
        spec_path = self._write_spec(
            "invalid_zero_epoch_duration_target.toml",
            {"epoch_duration_target = 14400": "epoch_duration_target = 0"},
        )

        output = self._run_invalid_spec_node(spec_path, 25327, 25328)

        assert "InvalidParams" in output
        assert "epoch_duration_target must be non-zero" in output
        assert "panicked at" not in output
        self.did_pass = True

    def test_invalid_zero_orphan_rate_denominator_is_rejected(self):
        spec_path = self._write_spec(
            "invalid_zero_orphan_rate_denominator.toml",
            {
                "genesis_epoch_length = 1000": (
                    "genesis_epoch_length = 1000\norphan_rate_target = [1, 0]"
                )
            },
        )

        output = self._run_invalid_spec_node(spec_path, 25331, 25332)

        assert "InvalidParams" in output
        assert "orphan_rate_target denominator must be non-zero" in output
        assert "panicked at" not in output
        self.did_pass = True

    def test_invalid_genesis_orphan_count_overflow_is_rejected(self):
        spec_path = self._write_spec(
            "invalid_genesis_orphan_count_overflow.toml",
            {
                "genesis_epoch_length = 1000": (
                    "genesis_epoch_length = 9223372036854775807\n"
                    "orphan_rate_target = [4294967295, 1]"
                )
            },
        )

        output = self._run_invalid_spec_node(spec_path, 25333, 25334)

        assert "InvalidParams" in output
        assert "genesis_epoch_length * orphan_rate_target numerator overflows" in output
        assert "panicked at" not in output
        self.did_pass = True

    def test_minimal_valid_consensus_params_still_start_and_mine(self):
        spec_path = self._write_spec(
            "minimal_valid_params.toml",
            {
                "genesis_epoch_length = 1000": "genesis_epoch_length = 1",
                "epoch_duration_target = 14400": "epoch_duration_target = 1",
            },
        )
        node = self._prepare_node(spec_path, "valid_minimal", 25329, 25330)
        try:
            node.start()
            client = node.getClient()
            client.generate_block()
            tip = client.get_tip_header()

            assert int(tip["number"], 16) >= 1
            assert client.local_node_info()["active"] is True
            self.did_pass = True
        finally:
            node.stop()
            node.clean()

    def _write_spec(self, filename, replacements):
        spec = self.source_spec.read_text()
        for original, replacement in replacements.items():
            assert original in spec
            spec = spec.replace(original, replacement)
        spec_path = self.spec_dir / filename
        spec_path.write_text(spec)
        return spec_path

    def _run_invalid_spec_node(self, spec_path, rpc_port, p2p_port):
        node = self._prepare_node(spec_path, spec_path.stem, rpc_port, p2p_port)
        log_path = Path(node.ckb_dir) / "node.log"
        try:
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    ["./ckb", "run", "--indexer", "--skip-spec-check"],
                    cwd=node.ckb_dir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
                    output = log_path.read_text()
                    raise AssertionError(
                        "invalid consensus params were accepted; "
                        f"node kept running. log tail:\n{output[-2000:]}"
                    )

            output = log_path.read_text()
            assert process.returncode != 0, output[-2000:]
            return output
        finally:
            node.stop()
            node.clean()

    def _prepare_node(self, spec_path, name, rpc_port, p2p_port):
        node_path = self.CkbNodeConfigPath(
            "source/template/ckb/v209/ckb.toml.j2",
            "source/template/ckb/v209/ckb-miner.toml.j2",
            str(spec_path.relative_to(self.root)),
            "download/current",
        )
        node = self.CkbNode.init_dev_by_port(
            node_path,
            f"v209/pr5325_overflow_hardening/{name}",
            rpc_port,
            p2p_port,
        )
        node.clean()
        node.prepare()
        return node
