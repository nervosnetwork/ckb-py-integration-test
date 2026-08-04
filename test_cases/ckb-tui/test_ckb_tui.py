import json
import os
import signal
import subprocess
import time

import pytest
from framework.basic import CkbTest
from framework.util import get_project_root

CKB_TUI_BIN = f"{get_project_root()}/source/ckb-tui/ckb-tui"
TCP_PORT = 18314


class TestCkbTuiRpcDeps(CkbTest):
    """
    Verify all RPC endpoints that CKB-TUI depends on work correctly under CKB 0.205.0.
    CKB-TUI connects via HTTP JSON-RPC and optionally TCP subscriptions.

    RPC dependencies:
      - Chain: get_blockchain_info, get_tip_header, get_header_by_number, get_consensus, sync_state
      - Pool: tx_pool_info, get_fee_rate_statics
      - Network: local_node_info, get_peers
      - Indexer: get_cells
      - Terminal (0.205.0): get_overview
    """

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_tui/node", 8314, 8315
        )
        cls.node.prepare(
            other_ckb_config={
                "ckb_rpc_modules": [
                    "Net",
                    "Pool",
                    "Miner",
                    "Chain",
                    "Stats",
                    "Subscription",
                    "Experiment",
                    "Debug",
                    "IntegrationTest",
                    "Terminal",
                ],
                "ckb_tcp_listen_address": f"0.0.0.0:{TCP_PORT}",
            }
        )
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    # ── Chain RPCs ──────────────────────────────────────────────

    def test_get_blockchain_info(self):
        info = self.node.getClient().get_blockchain_info()
        assert info is not None
        assert info["chain"] == "ckb_dev"
        assert "epoch" in info
        assert "difficulty" in info

    def test_get_tip_header(self):
        header = self.node.getClient().get_tip_header()
        assert header is not None
        assert int(header["number"], 16) >= 30
        for field in ["hash", "epoch", "timestamp", "compact_target"]:
            assert field in header

    def test_get_header_by_number(self):
        header = self.node.getClient().get_header_by_number("0x1", None)
        assert header is not None
        assert header["number"] == "0x1"

    def test_get_consensus(self):
        consensus = self.node.getClient().get_consensus()
        assert consensus is not None
        assert "genesis_hash" in consensus
        assert "block_version" in consensus
        assert "epoch_duration_target" in consensus

    def test_sync_state(self):
        state = self.node.getClient().sync_state()
        assert state is not None
        assert "best_known_block_number" in state

    # ── Pool RPCs ───────────────────────────────────────────────

    def test_tx_pool_info(self):
        pool_info = self.node.getClient().tx_pool_info()
        assert pool_info is not None
        for field in ["pending", "proposed", "orphan", "min_fee_rate"]:
            assert field in pool_info

    def test_get_fee_rate_statics(self):
        stats = self.node.getClient().get_fee_rate_statics()
        assert stats is not None

    # ── Network RPCs ────────────────────────────────────────────

    def test_local_node_info(self):
        info = self.node.getClient().local_node_info()
        assert info is not None
        assert "node_id" in info
        assert "version" in info
        assert "addresses" in info
        assert len(info["addresses"]) > 0

    def test_get_peers(self):
        peers = self.node.getClient().get_peers()
        assert peers is not None
        assert isinstance(peers, list)

    # ── Terminal Module RPCs (CKB 0.205.0) ──────────────────────

    def test_get_overview_returns_all_sections(self):
        """
        get_overview is provided by the Terminal module (CKB PR #4989).
        Without it, CKB-TUI shows N/A for CPU, RAM, disk, difficulty,
        hash rate, pool counts, cells info, and network data.
        """
        overview = self.node.getClient().get_overview()
        assert overview is not None
        for section in ["sys", "mining", "pool", "cells", "network", "version"]:
            assert section in overview, f"Missing '{section}' in overview"

    def test_get_overview_sys_info(self):
        overview = self.node.getClient().get_overview()
        sys_info = overview.get("sys")
        assert sys_info is not None

    def test_get_overview_mining_info(self):
        overview = self.node.getClient().get_overview()
        mining_info = overview.get("mining")
        assert mining_info is not None

    def test_get_overview_pool_info(self):
        overview = self.node.getClient().get_overview()
        pool_info = overview.get("pool")
        assert pool_info is not None

    def test_get_overview_cells_info(self):
        overview = self.node.getClient().get_overview()
        cells_info = overview.get("cells")
        assert cells_info is not None

    def test_get_overview_network_info(self):
        overview = self.node.getClient().get_overview()
        network_info = overview.get("network")
        assert network_info is not None

    def test_get_overview_with_refresh_flags(self):
        """
        Refresh bit flags: 0x1=SYS, 0x2=MINING, 0x4=TX_POOL,
        0x8=CELLS, 0x10=NETWORK, 0x1f=ALL
        """
        client = self.node.getClient()
        overview_all = client.get_overview("0x1f")
        assert overview_all is not None

        overview_sys = client.get_overview("0x1")
        assert overview_sys is not None

        overview_pool = client.get_overview("0x4")
        assert overview_pool is not None

    def test_get_overview_pool_reflects_tx_pool(self):
        """Verify pool section updates after sending a transaction."""
        client = self.node.getClient()
        client.clear_tx_pool()
        self.Miner.miner_with_version(self.node, "0x0")

        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            client.url,
            "1500",
        )

        overview = client.get_overview("0x4")
        assert overview is not None
        assert overview.get("pool") is not None

    # ── Indexer RPCs (Live Cells Searcher) ──────────────────────

    def test_get_cells_by_lock_script(self):
        """CKB-TUI Live Cells Searcher queries cells by lock script args."""
        cells = self.node.getClient().get_cells(
            {
                "script": {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": "0x8883a512ee2383c01574a328f60eeccbb4d78240",
                },
                "script_type": "lock",
            },
            "asc",
            "0x10",
            None,
        )
        assert cells is not None
        assert "objects" in cells
        assert len(cells["objects"]) > 0


class TestCkbTuiTcpSubscription(CkbTest):
    """
    Verify TCP subscription endpoints that CKB-TUI uses for streaming data.
    CKB-TUI subscribes to: new_tip_block, new_transaction, rejected_transaction.
    """

    @classmethod
    def setup_class(cls):
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_tui/tcp_node", 8316, 8317
        )
        cls.node.prepare(
            other_ckb_config={
                "ckb_rpc_modules": [
                    "Net",
                    "Pool",
                    "Miner",
                    "Chain",
                    "Stats",
                    "Subscription",
                    "Experiment",
                    "Debug",
                    "IntegrationTest",
                ],
                "ckb_tcp_listen_address": f"0.0.0.0:{TCP_PORT + 1}",
            }
        )
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 30)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    @staticmethod
    def _tcp_subscribe(port, topic):
        import telnetlib

        tn = telnetlib.Telnet("127.0.0.1", port)
        msg = json.dumps(
            {"id": 2, "jsonrpc": "2.0", "method": "subscribe", "params": [topic]}
        )
        tn.write(msg.encode("utf-8") + b"\n")
        data = tn.read_until(b"}\n", timeout=5)
        response = json.loads(data.decode("utf-8"))
        return tn, response

    def test_subscribe_new_tip_block(self):
        tn, response = self._tcp_subscribe(TCP_PORT + 1, "new_tip_block")
        assert "result" in response

        self.Miner.miner_with_version(self.node, "0x0")
        notification = tn.read_until(b"}\n", timeout=10)
        assert notification is not None
        assert len(notification) > 0

        tn.close()

    def test_subscribe_new_transaction(self):
        tn, response = self._tcp_subscribe(TCP_PORT + 1, "new_transaction")
        assert "result" in response

        account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
        self.Ckb_cli.wallet_transfer_by_private_key(
            self.Config.MINER_PRIVATE_1,
            account["address"]["testnet"],
            100,
            self.node.getClient().url,
            "1500",
        )

        notification = tn.read_until(b"}\n", timeout=10)
        assert notification is not None
        assert len(notification) > 0

        tn.close()

    def test_subscribe_rejected_transaction(self):
        tn, response = self._tcp_subscribe(TCP_PORT + 1, "rejected_transaction")
        assert "result" in response
        tn.close()


class TestCkbTuiBinary(CkbTest):
    """
    Verify the CKB-TUI binary can start, connect to a CKB node, and run.
    """

    @classmethod
    def setup_class(cls):
        if not os.path.isfile(CKB_TUI_BIN):
            pytest.skip("ckb-tui binary not found, run ckb_tui_prepare.sh first")

        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_tui/bin_node", 8318, 8319
        )
        cls.node.prepare(
            other_ckb_config={
                "ckb_rpc_modules": [
                    "Net",
                    "Pool",
                    "Miner",
                    "Chain",
                    "Stats",
                    "Subscription",
                    "Experiment",
                    "Debug",
                    "IntegrationTest",
                    "Terminal",
                ],
                "ckb_tcp_listen_address": f"0.0.0.0:{TCP_PORT + 2}",
            }
        )
        cls.node.start()
        cls.Miner.make_tip_height_number(cls.node, 10)

    @classmethod
    def teardown_class(cls):
        cls.node.stop()
        cls.node.clean()

    def test_binary_exists_and_executable(self):
        assert os.path.isfile(CKB_TUI_BIN)
        assert os.access(CKB_TUI_BIN, os.X_OK)

    def test_start_with_rpc_only(self):
        """CKB-TUI should start and stay alive when given a valid RPC endpoint."""
        proc = subprocess.Popen(
            [CKB_TUI_BIN, "-r", self.node.getClient().url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
        time.sleep(3)
        poll = proc.poll()
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
        assert poll is None, f"ckb-tui exited prematurely with code {poll}"

    def test_start_with_rpc_and_tcp(self):
        """CKB-TUI should start with both RPC and TCP endpoints."""
        proc = subprocess.Popen(
            [
                CKB_TUI_BIN,
                "-r",
                self.node.getClient().url,
                "-t",
                f"127.0.0.1:{TCP_PORT + 2}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
        time.sleep(3)
        poll = proc.poll()
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
        assert poll is None, f"ckb-tui exited prematurely with code {poll}"

    def test_start_with_invalid_rpc_exits(self):
        """CKB-TUI should exit when given an unreachable RPC endpoint."""
        proc = subprocess.Popen(
            [CKB_TUI_BIN, "-r", "http://127.0.0.1:19999"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            preexec_fn=os.setsid,
        )
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
            pytest.fail("ckb-tui should have exited on unreachable RPC")
        assert proc.returncode != 0
