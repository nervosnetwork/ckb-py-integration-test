"""PR #5316: a broken v2 pool snapshot must not revive valid v1 data."""

import struct
from pathlib import Path

from framework.basic import CkbTest
from test_cases.network_verification import workload


class TestPersistenceCorruption(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_persistence_corruption/node1",
            8506,
            8507,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # The base report copier uses a shared path; pytest retains this log.
        pass

    @staticmethod
    def _saved_v2(node):
        paths = list(Path(node.ckb_dir).rglob("persisted_data.v2"))
        assert len(paths) == 1, f"expected one saved v2 snapshot, found {paths}"
        return paths[0]

    # TEST-MAP: NTD-62
    def test_broken_v2_wins_over_valid_v1_and_starts_empty(self):
        """
        1. 保存含有效交易的 v2 快照并提取可加载的 v1 载荷。
        2. 先用 v1 载荷启动，确认它本身可以恢复交易。
        3. 构造越界或损坏的 v2 快照，逐项确认拒绝并以空池启动。
        """

        # 1. 保存含有效交易的 v2 快照并提取可加载的 v1 载荷。
        client = self.node.getClient()
        private_key = self.Config.ACCOUNT_PRIVATE_2
        account = self.Ckb_cli.util_key_info_by_private_key(private_key)
        fund = self.Ckb_cli.wallet_transfer_by_private_key(
            private_key,
            account["address"]["testnet"],
            360000,
            api_url=client.url,
            fee_rate="1000",
        )
        self.Miner.miner_until_tx_committed(self.node, fund)
        retained = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            private_key,
            fee=1000,
            api_url=client.url,
        )
        assert retained in client.get_raw_tx_pool(False)["pending"]
        pool_limit = int(client.tx_pool_info()["max_tx_pool_size"], 16)
        workload.stop_and_assert_process_exit(self.node)

        v2 = self._saved_v2(self.node)
        snapshot = v2.read_bytes()
        assert snapshot[:8] == b"CKBTPV2\0"
        accepted_len = struct.unpack_from("<Q", snapshot, 8)[0]
        accepted_start = 16
        legacy_payload = snapshot[accepted_start : accepted_start + accepted_len]
        assert accepted_len > 4
        assert struct.unpack_from("<I", legacy_payload)[0] == len(legacy_payload)
        assert snapshot[accepted_start + accepted_len :] == b"\x04\0\0\0"
        v1 = v2.with_suffix(".v1")

        # 先证明同一个当前 v2 可恢复，再证明派生 v1 可恢复，排除坏正对照。
        self.node.start()
        workload.wait_for(
            lambda: retained in self.node.getClient().get_raw_tx_pool(False)["pending"],
            "valid v2 replay",
        )
        workload.stop_and_assert_process_exit(self.node)

        # 2. 先用 v1 载荷启动，确认它本身可以恢复交易。
        v2.unlink()
        v1.write_bytes(legacy_payload)
        self.node.start()
        try:
            workload.wait_for(
                lambda: retained
                in self.node.getClient().get_raw_tx_pool(False)["pending"],
                "valid v1 replay",
            )
            print("v1-only: retained transaction restored")
        finally:
            workload.stop_and_assert_process_exit(self.node)

        # A sparse file crosses the derived read bound without allocating its
        # full logical length. The other fixtures exercise header/section and
        # independent TransactionVec validation after the bounded read.

        # 3. 构造越界或损坏的 v2 快照，逐项确认拒绝并以空池启动。
        scale = max(pool_limit, 180_000_000)
        pipeline = 384_000_000 * scale // 180_000_000
        read_bound = (pool_limit + pipeline) * 2 + 1024 * 1024
        empty_vec = b"\x04\0\0\0"
        header = b"CKBTPV2\0" + struct.pack("<Q", len(legacy_payload))
        cases = [
            ("bad magic", b"BROKENV2", "invalid v2 header"),
            ("truncated magic", b"CKBTPV2", "invalid v2 header"),
            ("truncated length", b"CKBTPV2\0" + b"\0" * 7, "missing accepted length"),
            (
                "declared length",
                b"CKBTPV2\0" + struct.pack("<Q", 9999) + empty_vec,
                "declared accepted section exceeds file length",
            ),
            (
                "broken Accepted",
                header + b"\0" * len(legacy_payload) + empty_vec,
                "TransactionVec",
            ),
            (
                "broken Recovery",
                header + legacy_payload + struct.pack("<I", 5),
                "TransactionVec",
            ),
            ("read bound", None, "exceeds bound"),
        ]
        for label, payload, error_text in cases:
            v1.write_bytes(legacy_payload)
            if payload is None:
                with v2.open("wb") as file:
                    file.truncate(read_bound + 1)
            else:
                v2.write_bytes(payload)
            assert v1.is_file() and v1.stat().st_size == len(legacy_payload)
            self.node.start()
            try:
                pool = self.node.getClient().get_raw_tx_pool(False)
                assert pool["pending"] == [] and pool["proposed"] == [], label
                assert (
                    self.node.getClient().get_transaction(retained)["tx_status"][
                        "status"
                    ]
                    == "unknown"
                ), label
                log = (Path(self.node.ckb_dir) / "node.log").read_text()
                errors = [
                    line
                    for line in log.splitlines()
                    if "failed to prepare tx-pool persistence" in line
                    and error_text in line
                ]
                assert errors, label
                if label == "read bound":
                    assert (
                        f"file size {read_bound + 1} exceeds bound {read_bound}"
                        in errors[-1]
                    )
                print(f"{label}: empty pool; unknown transaction; {errors[-1]}")
            finally:
                workload.stop_and_assert_process_exit(self.node)
        self.did_pass = True
