"""PR #5316: persistence failures retain the committed snapshot boundary."""

import os
from pathlib import Path

from framework.basic import CkbTest


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
        self.node.stop()
        log = (Path(self.node.ckb_dir) / "node.log").read_text()
        assert "CKB shutdown" in log, "node shutdown did not finish"
        return log

    # TEST-MAP: NTD-64
    def test_prior_v2_survives_temp_open_failure(self):
        split = self._split()
        first = self._send(split, 0)
        assert self._pool() == {first}
        self._stop_and_log()
        v2 = self._v2(self.node)
        original = v2.read_bytes()
        assert original[:8] == b"CKBTPV2\0"

        self.node.start()
        assert self._pool() == {first}
        second = self._send(split, 1)
        assert self._pool() == {first, second}

        # The writer follows this symlink when opening v2.tmp. Its target is
        # a local, read-only sentinel, forcing an open error without touching
        # the pre-existing v2. Cleanup should unlink only the temporary path.
        tmp = v2.with_suffix(".v2.tmp")
        sentinel = v2.with_name("write-denied-sentinel")
        sentinel.write_bytes(b"preserve sentinel")
        sentinel.chmod(0o444)
        assert not os.access(sentinel, os.W_OK)
        tmp.symlink_to(sentinel)
        log = self._stop_and_log()
        assert v2.read_bytes() == original
        assert not tmp.exists() and not tmp.is_symlink()
        assert sentinel.read_bytes() == b"preserve sentinel"

        self.node.start()
        assert self._pool() == {first}
        print(
            f"temp-open-error: original_v2_bytes={len(original)}; "
            "temp_removed=True; prior_pool_restored=True"
        )
        assert "failed to save tx-pool" in log and "Failed to open temp file" in log

    # TEST-MAP: NTD-65
    def test_v1_cleanup_failure_keeps_new_v2_readable(self):
        split = self._split()
        first = self._send(split, 0)
        assert self._pool() == {first}
        self._stop_and_log()
        v2 = self._v2(self.node)
        original = v2.read_bytes()
        assert original[:8] == b"CKBTPV2\0"

        self.node.start()
        assert self._pool() == {first}
        second = self._send(split, 1)
        assert self._pool() == {first, second}

        # A directory at the legacy path makes remove_file(v1) fail *after*
        # the normal temporary v2 has been renamed into place.
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
