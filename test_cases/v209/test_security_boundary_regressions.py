"""Regression coverage for public security hardening from CKB PR #5219."""

from pathlib import Path

import pytest

from framework.basic import CkbTest
from framework.util import get_project_root


U64_MAX = "0xffffffffffffffff"


class TestNetworkAndHeaderSecurityBoundaries(CkbTest):
    """Binary-level coverage for network-ban and header-sync boundaries."""

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "v209/security_boundary/network_and_header",
            20940,
            20941,
        )
        cls.node.clean()
        cls.node.prepare()
        cls.node.start()
        cls.sync_node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "v209/security_boundary/header_sync",
            20944,
            20945,
        )
        cls.sync_node.clean()
        cls.sync_node.prepare()
        cls.sync_node.start()

    @classmethod
    def teardown_class(cls):
        for node in (getattr(cls, "node", None), getattr(cls, "sync_node", None)):
            if node is not None:
                node.stop()
                node.clean()

    @staticmethod
    def _find_ban(client, address):
        network = "{}/32".format(address)
        return next(
            (
                entry
                for entry in client.get_banned_addresses()
                if entry["address"] in {address, network}
            ),
            None,
        )

    @staticmethod
    def _delete_ban(client, address):
        client.set_ban(address, "delete", None, None, None)

    def test_tp_net_ban_001_relative_ban_time_overflow_is_rejected(self):
        """TP-NET-BAN-001: relative u64::MAX must not overflow or mutate bans."""
        client = self.node.getClient()
        address = "192.0.2.1"
        before = client.get_banned_addresses()

        with pytest.raises(Exception, match="relative.*ban_time.*overflows"):
            client.set_ban(
                address,
                "insert",
                U64_MAX,
                False,
                "relative ban overflow regression",
            )

        assert client.get_banned_addresses() == before
        assert client.local_node_info()["node_id"]
        self.did_pass = True

    def test_tp_net_ban_002_absolute_u64_max_ban_time_still_succeeds(self):
        """TP-NET-BAN-002: the overflow guard must not reject absolute time."""
        client = self.node.getClient()
        address = "192.0.2.2"
        try:
            client.set_ban(
                address,
                "insert",
                U64_MAX,
                True,
                "absolute-time compatibility",
            )
            ban = self._find_ban(client, address)

            assert ban is not None
            assert int(ban["ban_until"], 16) == int(U64_MAX, 16)
            self.did_pass = True
        finally:
            self._delete_ban(client, address)

    def test_tp_net_ban_003_normal_relative_ban_time_still_succeeds(self):
        """TP-NET-BAN-003: ordinary relative bans retain their old behavior."""
        client = self.node.getClient()
        address = "192.0.2.3"
        duration_ms = 60_000
        try:
            client.set_ban(
                address,
                "insert",
                hex(duration_ms),
                False,
                "normal relative-time compatibility",
            )
            ban = self._find_ban(client, address)

            assert ban is not None
            assert ban["ban_reason"] == "normal relative-time compatibility"
            assert int(ban["ban_until"], 16) > int(ban["created_at"], 16)
            self.did_pass = True
        finally:
            self._delete_ban(client, address)

    def test_tp_consensus_header_001_nonzero_version_remains_accepted(self):
        """TP-CONSENSUS-HEADER-001: header version remains non-reserved."""
        client = self.node.getClient()
        previous_tip = client.get_tip_block_number()

        self.Miner.miner_with_version(self.node, "0x1")

        tip = client.get_tip_header()
        assert int(tip["number"], 16) == previous_tip + 1
        assert tip["version"] == "0x1"

        self.sync_node.connected(self.node)
        self.Node.wait_node_height(self.sync_node, previous_tip + 1, 60)
        synced_tip = self.sync_node.getClient().get_tip_header()
        assert synced_tip["hash"] == tip["hash"]
        assert synced_tip["version"] == "0x1"
        self.did_pass = True


class TestFarFutureRewardBoundary(CkbTest):
    """Accelerate the 64-halving boundary that occurs centuries in production."""

    @classmethod
    def setup_class(cls):
        root = Path(get_project_root())
        source_spec = root / "source/template/ckb/v200/specs/dev.toml"
        cls.spec_path = root / "tmp/v209/fast_halving_security_boundary.toml"
        cls.spec_path.parent.mkdir(parents=True, exist_ok=True)

        spec = source_spec.read_text()
        replacements = {
            "primary_epoch_reward_halving_interval = 8760": (
                "primary_epoch_reward_halving_interval = 1"
            ),
            "epoch_duration_target = 14400": "epoch_duration_target = 8",
            "genesis_epoch_length = 1000": "genesis_epoch_length = 10",
        }
        for original, replacement in replacements.items():
            assert original in spec
            spec = spec.replace(original, replacement)
        cls.spec_path.write_text(spec)

        node_path = cls.CkbNodeConfigPath(
            "source/template/ckb/v200/ckb.toml.j2",
            "source/template/ckb/v200/ckb-miner.toml.j2",
            str(cls.spec_path.relative_to(root)),
            "download/current",
        )
        cls.node = cls.CkbNode.init_dev_by_port(
            node_path,
            "v209/security_boundary/far_future_reward",
            20942,
            20943,
        )
        cls.node.clean()
        cls.node.prepare()
        cls.node.start()

    @classmethod
    def teardown_class(cls):
        if hasattr(cls, "node"):
            cls.node.stop()
            cls.node.clean()
        if hasattr(cls, "spec_path"):
            cls.spec_path.unlink(missing_ok=True)

    def test_tp_consensus_reward_001_is_safe_after_64_halvings(self):
        """TP-CONSENSUS-REWARD-001: reward survives a shift count above 63."""
        client = self.node.getClient()

        client.generate_epochs("0x41")

        current_epoch = client.get_current_epoch()
        block_template = client.get_block_template()
        assert int(current_epoch["number"], 16) >= 65
        assert (int(block_template["epoch"], 16) & 0xFFFFFF) >= 65
        assert client.local_node_info()["active"] is True
        self.did_pass = True
