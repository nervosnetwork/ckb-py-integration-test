"""
Tests for PR #4924: Enhanced ckb export/import subcommand
https://github.com/nervosnetwork/ckb/pull/4924

Features covered:
  Export:
    - --from / --to block range selection (both inclusive)
    - JSONL output format (breaking change from .json)
    - Export to stdout via --target -
  Import:
    - --skip-script-verify flag
    - --skip-all-verify flag
    - --num-threads N for parallel processing
    - Import from stdin via ckb import -
    - Parent block existence validation
"""

import json
import os
import time

from framework.basic import CkbTest
from framework.util import run_command


class TestCkbExportImport(CkbTest):

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_command/node1", 8514, 8515
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 100)
        cls.node.stop()
        cls.node.rmLockFile()
        cls.export_dir = os.path.join(cls.node.ckb_dir, "export")
        run_command(f"mkdir -p {cls.export_dir}")

    @classmethod
    def teardown_class(cls):
        print("stop node and clean")
        cls.node.stop()
        cls.node.clean()

    def _init_target_node(self, dec_dir, rpc_port, p2p_port):
        """Create a fresh target node with genesis block initialized in DB."""
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST, dec_dir, rpc_port, p2p_port
        )
        node.prepare()
        node.start()
        tip = node.getClient().get_tip_block_number()
        assert tip == 0, f"Target node genesis check failed, tip={tip}"
        node.stop()
        node.rmLockFile()
        return node

    def _export_blocks(self, from_block, to_block):
        """Export a range from the source node, return the output file path."""
        expected = os.path.join(
            self.export_dir, f"ckb_dev-{from_block}-{to_block}.jsonl"
        )
        if os.path.exists(expected):
            return expected
        run_command(
            f"cd {self.node.ckb_dir} && ./ckb export "
            f"--target {self.export_dir} --from {from_block} --to {to_block}"
        )
        return expected

    # ------------------------------------------------------------------ export

    def test_01_export_with_range(self):
        """Export blocks 1-50 with --from/--to, verify .jsonl file is created."""
        export_file = self._export_blocks(1, 50)
        assert os.path.exists(export_file), f"File not found: {export_file}"
        assert os.path.getsize(export_file) > 0

    def test_02_export_jsonl_format(self):
        """Exported file must be valid JSONL with block header data."""
        export_file = self._export_blocks(1, 10)
        line_count = 0
        with open(export_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                block = json.loads(line)
                assert "header" in block, "Block missing 'header'"
                assert "number" in block["header"], "Header missing 'number'"
                line_count += 1
        assert line_count >= 10, f"Expected >= 10 blocks, got {line_count}"

    def test_03_export_range_inclusive(self):
        """--from and --to are both inclusive boundaries."""
        export_file = self._export_blocks(10, 20)
        block_numbers = []
        with open(export_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                block = json.loads(line)
                block_numbers.append(int(block["header"]["number"], 16))
        assert 10 in block_numbers, "Start block (10) must be included"
        assert 20 in block_numbers, "End block (20) must be included"

    def test_04_export_invalid_range(self):
        """Export with from > to should fail."""
        result = run_command(
            f"cd {self.node.ckb_dir} && ./ckb export "
            f"--target {self.export_dir} --from 50 --to 10",
            check_exit_code=False,
        )
        assert (
            isinstance(result, int) and result != 0
        ), "Export should fail when from > to"

    # ------------------------------------------------------------------ import

    def test_05_import_basic(self):
        """Import blocks 1-50, start node, verify tip == 50."""
        export_file = self._export_blocks(1, 50)
        target = self._init_target_node("ckb_command/import_basic", 8516, 8517)
        try:
            run_command(f"cd {target.ckb_dir} && ./ckb import {export_file}")
            target.start()
            tip = target.getClient().get_tip_block_number()
            assert tip == 50, f"Expected tip 50, got {tip}"
            target.stop()
        finally:
            target.stop()
            target.clean()

    def test_06_import_parent_not_found(self):
        """Import blocks 50-100 into a fresh node (only genesis) should fail."""
        export_file = self._export_blocks(50, 100)
        target = self._init_target_node("ckb_command/import_parent", 8518, 8519)
        try:
            result = run_command(
                f"cd {target.ckb_dir} && ./ckb import {export_file}",
                check_exit_code=False,
            )
            assert (
                isinstance(result, int) and result != 0
            ), "Import should fail when parent block is missing"
        finally:
            target.clean()

    def test_07_import_sequential_ranges(self):
        """Import two non-overlapping ranges (1-50, 51-100), verify tip == 100."""
        file1 = self._export_blocks(1, 50)
        file2 = self._export_blocks(51, 100)
        target = self._init_target_node("ckb_command/import_seq", 8520, 8521)
        try:
            run_command(f"cd {target.ckb_dir} && ./ckb import {file1}")
            target.rmLockFile()
            run_command(f"cd {target.ckb_dir} && ./ckb import {file2}")
            target.start()
            tip = target.getClient().get_tip_block_number()
            assert tip == 100, f"Expected tip 100, got {tip}"
            target.stop()
        finally:
            target.stop()
            target.clean()

    def test_08_import_skip_script_verify(self):
        """Import with --skip-script-verify for faster trusted import."""
        export_file = self._export_blocks(1, 50)
        target = self._init_target_node("ckb_command/skip_script", 8522, 8523)
        try:
            run_command(
                f"cd {target.ckb_dir} && ./ckb import "
                f"--skip-script-verify {export_file}"
            )
            target.start()
            tip = target.getClient().get_tip_block_number()
            assert tip == 50
            target.stop()
        finally:
            target.stop()
            target.clean()

    def test_09_import_skip_all_verify(self):
        """Import with --skip-all-verify for maximum speed trusted import."""
        export_file = self._export_blocks(1, 50)
        target = self._init_target_node("ckb_command/skip_all", 8524, 8525)
        try:
            run_command(
                f"cd {target.ckb_dir} && ./ckb import "
                f"--skip-all-verify {export_file}"
            )
            target.start()
            tip = target.getClient().get_tip_block_number()
            assert tip == 50
            target.stop()
        finally:
            target.stop()
            target.clean()

    def test_10_import_num_threads(self):
        """Import with --num-threads 2 for parallel processing."""
        export_file = self._export_blocks(1, 50)
        target = self._init_target_node("ckb_command/num_threads", 8526, 8527)
        try:
            run_command(
                f"cd {target.ckb_dir} && ./ckb import " f"--num-threads 2 {export_file}"
            )
            target.start()
            tip = target.getClient().get_tip_block_number()
            assert tip == 50
            target.stop()
        finally:
            target.stop()
            target.clean()

    # ------------------------------------------------------------------ pipe

    def test_11_export_stdout_import_stdin(self):
        """Pipe: ckb export --target - | ckb import - (stdout/stdin)."""
        target = self._init_target_node("ckb_command/pipe", 8528, 8529)
        try:
            run_command(
                f"cd {self.node.ckb_dir} && ./ckb export --target - --from 1 --to 50 "
                f"| (cd {target.ckb_dir} && ./ckb import -)"
            )
            target.start()
            tip = target.getClient().get_tip_block_number()
            assert tip == 50, f"Expected tip 50, got {tip}"
            target.stop()
        finally:
            target.stop()
            target.clean()
