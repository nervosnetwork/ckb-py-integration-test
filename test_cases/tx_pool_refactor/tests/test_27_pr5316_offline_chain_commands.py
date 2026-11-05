"""PR #5316: offline import and replay release chain-service ownership."""

import subprocess
from pathlib import Path

from framework.basic import CkbTest


class TestOfflineChainCommands(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.source = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_offline_chain/source",
            8502,
            8503,
        )
        cls.target = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_offline_chain/target",
            8504,
            8505,
        )
        cls.source.prepare()
        cls.source.start()
        cls.Miner.make_tip_height_number(cls.source, 10)
        cls.source_tip_hash = cls.source.getClient().get_tip_header()["hash"]
        cls.source.stop()
        cls.target.prepare()
        cls.target.start()
        assert cls.target.getClient().get_tip_block_number() == 0
        cls.target.stop()

    @classmethod
    def teardown_class(cls):
        for node in (cls.source, cls.target):
            node.stop()
            node.clean()

    def teardown_method(self, method):
        # CkbTest's report copier uses a shared path; pytest retains this log.
        pass

    @staticmethod
    def _run(node, *args):
        result = subprocess.run(
            ["./ckb", *args],
            cwd=node.ckb_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"ckb {' '.join(args)} exited {result.returncode}:\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
        return result.stdout + result.stderr

    # TEST-MAP: NTD-69
    def test_import_and_replay_release_chain_and_temporary_db(self):
        """
        1. 从源节点导出区块并确认导出文件非空。
        2. 离线导入并分别执行检查和分析回放，确认临时目录被释放。
        3. 启动目标节点，核对区块高度及链顶哈希。
        """

        # 1. 从源节点导出区块并确认导出文件非空。
        export_dir = Path(self.source.ckb_dir) / "export"
        export_dir.mkdir()
        self._run(
            self.source,
            "export",
            "--target",
            str(export_dir),
            "--from",
            "1",
            "--to",
            "10",
        )
        export_file = export_dir / "ckb_dev-1-10.jsonl"
        assert export_file.is_file() and export_file.stat().st_size > 0

        # No node, pool consumer or relay consumer runs during these commands.

        # 2. 离线导入并分别执行检查和分析回放，确认临时目录被释放。
        self._run(self.target, "import", str(export_file))
        replay_tmp = Path(self.target.ckb_dir) / "replay-tmp"
        replay_tmp.mkdir()
        before = set(replay_tmp.iterdir())
        sanity = self._run(
            self.target, "replay", "--tmp-target", str(replay_tmp), "--sanity-check"
        )
        assert "Sanity-check pass" in sanity, sanity[-2000:]
        assert set(replay_tmp.iterdir()) == before
        profile = self._run(
            self.target,
            "replay",
            "--tmp-target",
            str(replay_tmp),
            "--profile",
            "1",
            "10",
        )
        assert "End profiling" in profile
        assert set(replay_tmp.iterdir()) == before

        # A new node can open the same DB after every offline scope has exited.

        # 3. 启动目标节点，核对区块高度及链顶哈希。
        self.target.start()
        assert self.target.getClient().get_tip_block_number() == 10
        assert self.target.getClient().get_tip_header()["hash"] == self.source_tip_hash
        self.target.stop()
        self.did_pass = True
