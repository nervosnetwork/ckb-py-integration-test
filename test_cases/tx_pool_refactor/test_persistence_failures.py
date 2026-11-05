"""PR #5316: persistence failures retain the committed snapshot boundary."""

import struct
import os
from pathlib import Path

from framework.basic import CkbTest
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.test_persistence_replay import (
    _transactions,
)


class TestPersistenceFailures(CkbTest):
    def setup_method(self, method):
        port = 8540 if method.__name__.endswith("temp_open_failure") else 8542
        self.node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_persistence_failures/{method.__name__}",
            port,
            port + 1,
        )
        self.node.prepare()
        self.node.start()
        self.Miner.make_tip_height_number(self.node, 30)

    def teardown_method(self, method):
        if self.node.ckb_pid != -1:
            self.node.stop()
        workload.save_node_logs(self.node)
        self.node.clean()

    @staticmethod
    def _v2(node):
        paths = list(Path(node.ckb_dir).rglob("persisted_data.v2"))
        assert len(paths) == 1, paths
        return paths[0]

    def _split(self):
        client = self.node.getClient()
        key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        split = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            output_count=2,
            fee=1000,
            api_url=client.url,
        )
        self.Miner.miner_until_tx_committed(self.node, split)
        return split

    def _send(self, split, index):
        return self.Tx.send_transfer_self_tx_with_input(
            [split],
            [hex(index)],
            self.Config.ACCOUNT_PRIVATE_2,
            fee=1000,
            api_url=self.node.getClient().url,
        )

    def _pool(self):
        raw = self.node.getClient().get_raw_tx_pool(False)
        return set(raw["pending"]) | set(raw["proposed"])

    def _stop_and_log(self):
        workload.stop_and_assert_process_exit(self.node)
        log = (Path(self.node.ckb_dir) / "node.log").read_text()
        self._shutdown_number = getattr(self, "_shutdown_number", 0) + 1
        if os.environ.get("CKB_TEST_NODE_LOG_DIR"):
            directory = (
                Path(os.environ["CKB_TEST_NODE_LOG_DIR"]) / Path(self.node.ckb_dir).name
            )
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"shutdown-{self._shutdown_number}.log").write_text(log)
        assert "CKB shutdown" in log, "node shutdown did not finish"
        return log

    # TEST-MAP: NTD-64
    # TEST-MAP: TCH-03
    def test_prior_v2_survives_temp_open_failure(self):
        """
        1. 保存首笔交易的有效 v2 快照。
        2. 重启后再提交第二笔交易。
        3. 用目录占位使临时文件打开失败，核对旧 v2 未被覆盖。
        4. 撤销故障并重启，确认仅旧快照交易恢复及实际 I/O 错误。
        5. 重交未保存交易，再正常保存并重启，确认新快照有效且两笔恢复。
        """

        # 1. 保存首笔交易的有效 v2 快照。
        split = self._split()
        first = self._send(split, 0)
        assert self._pool() == {first}
        self._stop_and_log()
        v2 = self._v2(self.node)
        original = v2.read_bytes()
        assert original[:8] == b"CKBTPV2\0"

        # 2. 重启后再提交第二笔交易。
        self.node.start()
        assert self._pool() == {first}
        second = self._send(split, 1)
        assert self._pool() == {first, second}

        # 3. 目录占位对普通用户和 root 均能产生实际文件打开失败。
        tmp = v2.with_suffix(".v2.tmp")
        tmp.mkdir()
        try:
            log = self._stop_and_log()
            assert v2.read_bytes() == original
            assert tmp.is_dir()
        finally:
            tmp.rmdir()

        # 4. 再次启动，确认仅旧快照交易恢复并记录错误。
        self.node.start()
        workload.wait_for(lambda: self._pool() == {first}, "old snapshot restored")
        print(
            f"temp-open-error: original_v2_bytes={len(original)}; "
            "temp_removed=True; prior_pool_restored=True"
        )
        assert "failed to save tx-pool" in log and "Failed to open temp file" in log

        # 5. 故障撤销后必须证明后续保存和恢复正常，避免只验证一次读旧文件。
        assert self._send(split, 1) == second
        saved_bodies = {
            h: self.node.getClient().get_transaction(h)["transaction"]
            for h in (first, second)
        }
        clean_log = self._stop_and_log()
        assert "failed to save tx-pool" not in clean_log
        updated = v2.read_bytes()
        assert updated[:8] == b"CKBTPV2\0" and updated != original
        end = 16 + struct.unpack_from("<Q", updated, 8)[0]
        assert len(_transactions(updated[16:end])) == 2
        assert _transactions(updated[end:]) == []
        self.node.start()
        workload.wait_for(
            lambda: self._pool() == {first, second}, "new snapshot restored"
        )
        for tx_hash, body in saved_bodies.items():
            assert self.node.getClient().get_transaction(tx_hash)["transaction"] == body

    # TEST-MAP: NTD-65
    def test_v1_cleanup_failure_keeps_new_v2_readable(self):
        """
        1. 保存首笔交易的有效 v2 快照。
        2. 重启后提交第二笔交易。
        3. 让旧 v1 清理失败，确认新的 v2 已写成且临时文件消失。
        4. 再次启动，确认两笔交易从新 v2 恢复并检查错误日志。
        """

        # 1. 保存首笔交易的有效 v2 快照。
        split = self._split()
        first = self._send(split, 0)
        assert self._pool() == {first}
        self._stop_and_log()
        v2 = self._v2(self.node)
        original = v2.read_bytes()
        assert original[:8] == b"CKBTPV2\0"

        # 2. 重启后提交第二笔交易。
        self.node.start()
        assert self._pool() == {first}
        second = self._send(split, 1)
        assert self._pool() == {first, second}

        # A directory at the legacy path makes remove_file(v1) fail *after*
        # the normal temporary v2 has been renamed into place.

        # 3. 让旧 v1 清理失败，确认新的 v2 已写成且临时文件消失。
        v1 = v2.with_suffix(".v1")
        v1.mkdir()
        marker = v1 / "keep"
        marker.write_text("legacy removal must fail")
        log = self._stop_and_log()
        assert v1.is_dir() and marker.is_file()
        assert v2.is_file() and v2.read_bytes() != original
        assert not v2.with_suffix(".v2.tmp").exists()

        # If startup mistakenly selected the legacy directory, it would fail
        # to load; v2 must win and carry the new accepted transaction.

        # 4. 再次启动，确认两笔交易从新 v2 恢复并检查错误日志。
        self.node.start()
        assert self._pool() == {first, second}
        assert (
            self.node.getClient().get_transaction(second)["transaction"]["hash"]
            == second
        )
        marker.unlink()
        v1.rmdir()
        print(
            f"legacy-remove-error: new_v2_bytes={v2.stat().st_size}; "
            "new_pool_restored=True"
        )
        assert "failed to save tx-pool" in log
        assert "Failed to remove migrated tx-pool file" in log
