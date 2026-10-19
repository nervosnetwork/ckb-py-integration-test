"""PR #5316: migrate a loadable v1 pool snapshot through a normal v2 save."""

import struct
from pathlib import Path

from framework.basic import CkbTest


class TestPersistenceMigration(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "tx_pool/pr5316_persistence_migration/node1",
            8538,
            8539,
        )
        cls.node.prepare()
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def teardown_method(self, method):
        # Keep the pytest log rather than copying the shared report path.
        pass

    @staticmethod
    def _v2(node):
        paths = list(Path(node.ckb_dir).rglob("persisted_data.v2"))
        assert len(paths) == 1, paths
        return paths[0]

    @staticmethod
    def _accepted_bytes(v2):
        data = v2.read_bytes()
        assert data[:8] == b"CKBTPV2\0"
        accepted_len, recovery_count = struct.unpack_from("<QI", data, 8)
        assert recovery_count == 0
        start = 20
        end = start + accepted_len
        assert end + 4 == len(data)
        accepted = data[start:end]
        assert len(accepted) > 4
        assert struct.unpack_from("<I", accepted)[0] == len(accepted)
        assert data[end:] == b"\x04\0\0\0"  # Empty recovery TransactionVec.
        return accepted

    # TEST-MAP: NTD-63
    def test_normal_save_migrates_v1_to_readable_v2_and_removes_legacy(self):
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
        retained = self.Tx.send_transfer_self_tx_with_input(
            [fund],
            ["0x0"],
            key,
            fee=1000,
            api_url=client.url,
        )
        assert set(client.get_raw_tx_pool(False)["pending"]) == {retained}

        # Derive a v1 fixture from a real v2 save, so the exact packed
        # transaction is known to be valid on this node's current chain.
        self.node.stop()
        v2 = self._v2(self.node)
        original_accepted = self._accepted_bytes(v2)
        v1 = v2.with_suffix(".v1")
        tmp = v2.with_suffix(".v2.tmp")
        assert not v1.exists() and not tmp.exists()
        v2.unlink()
        v1.write_bytes(original_accepted)

        self.node.start()
        assert v1.read_bytes() == original_accepted
        assert not v2.exists()
        assert set(client.get_raw_tx_pool(False)["pending"]) == {retained}
        assert client.get_transaction(retained)["transaction"]["hash"] == retained

        # Graceful stop invokes the normal writer; its successful return must
        # leave a complete v2 snapshot and retire the migration input.
        self.node.stop()
        assert v2.is_file() and not v1.exists() and not tmp.exists()
        assert self._accepted_bytes(v2) == original_accepted

        self.node.start()
        assert set(client.get_raw_tx_pool(False)["pending"]) == {retained}
        assert client.get_transaction(retained)["transaction"]["hash"] == retained
        print(
            f"migrated={retained}; v2_bytes={v2.stat().st_size}; "
            "legacy_absent=True; temp_absent=True; v2_reload=True"
        )
        self.did_pass = True
