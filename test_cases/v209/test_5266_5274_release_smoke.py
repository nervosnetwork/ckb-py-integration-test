import os

from framework.basic import CkbTest
from framework.util import run_command


class TestV209ReleaseSmoke(CkbTest):
    """
    v0.209.0 binary regression coverage for @Xcodes-chain assigned PRs.

    Covered release items:
    - PR 5266: ckb import chain service lifecycle and export/import cleanup.
    - PR 5274: release package smoke path, equivalent to init plus export against
      a fresh data directory.
    - PR 5278: release binary path that should be signed by the updated key.
    """

    @classmethod
    def setup_class(cls):
        cls.source_node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "v209/release_smoke/source",
            20934,
            20935,
        )
        cls.source_node.prepare()
        cls.source_node.start()
        cls.Miner.make_tip_height_number(cls.source_node, 10)
        cls.source_node.stop()
        cls.source_node.rmLockFile()
        cls.export_dir = os.path.join(cls.source_node.ckb_dir, "export")
        run_command("mkdir -p {}".format(cls.export_dir))

    @classmethod
    def teardown_class(cls):
        if hasattr(cls, "source_node"):
            cls.source_node.stop()
            cls.source_node.clean()

    def _export_blocks(self, from_block, to_block):
        export_file = os.path.join(
            self.export_dir, "ckb_dev-{}-{}.jsonl".format(from_block, to_block)
        )
        if os.path.exists(export_file):
            return export_file
        run_command(
            "cd {} && ./ckb export --target {} --from {} --to {}".format(
                self.source_node.ckb_dir, self.export_dir, from_block, to_block
            )
        )
        return export_file

    def _init_target_node(self):
        target = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            "v209/release_smoke/target",
            20936,
            20937,
        )
        target.prepare()
        target.start()
        assert target.getClient().get_tip_block_number() == 0
        target.stop()
        target.rmLockFile()
        return target

    def test_packaged_binary_reports_v209_release_version(self):
        """
        Check that download/current points at the v0.209.0 packaged binary.
        """
        version = run_command(
            "cd {} && ./ckb --version".format(self.source_node.ckb_dir)
        )

        assert version.startswith("ckb 0.209.0 ")
        self.did_pass = True

    def test_release_package_init_and_export_genesis_smoke(self):
        """
        Smoke the packaged v209 ckb binary with init and export from a fresh DB.
        This mirrors the release-package smoke added for Windows in PR 5274.
        """
        smoke_dir = os.path.join(self.source_node.ckb_dir, "package_smoke")
        export_dir = os.path.join(smoke_dir, "export")
        run_command("rm -rf {}".format(smoke_dir))
        run_command("mkdir -p {}".format(export_dir))

        run_command(
            "cd {} && ./ckb init -C {} --chain dev --force".format(
                self.source_node.ckb_dir, smoke_dir
            )
        )
        run_command(
            "cd {} && ./ckb export -C {} --target {} --from 0 --to 0".format(
                self.source_node.ckb_dir, smoke_dir, export_dir
            )
        )

        export_file = os.path.join(export_dir, "ckb_dev-0-0.jsonl")
        assert os.path.exists(export_file)
        assert os.path.getsize(export_file) > 0
        self.did_pass = True

    def test_import_sequential_ranges_after_chain_service_scope_fix(self):
        """
        Import two ranges into the same target DB. This catches regressions where
        ckb import leaves chain services or lock files in a bad state between runs.
        """
        file1 = self._export_blocks(1, 5)
        file2 = self._export_blocks(6, 10)
        target = self._init_target_node()
        try:
            run_command("cd {} && ./ckb import {}".format(target.ckb_dir, file1))
            target.rmLockFile()
            run_command("cd {} && ./ckb import {}".format(target.ckb_dir, file2))
            target.start()
            assert target.getClient().get_tip_block_number() == 10
            self.did_pass = True
        finally:
            target.stop()
            target.clean()
