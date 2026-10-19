from pathlib import Path

import pytest

from framework.basic import CkbTest
import toml
from test_cases.network_verification import workload


class TestCkbCliRpc117(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. start 1 ckb node in tmp/ckb_cli/node dir
        2. miner 20 block
        Returns:

        """
        # 1. start 1 ckb node in tmp/ckb_cli/node dir
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_cli/node", 8314, 8315
        )
        cls.node.prepare()
        cls.node.start()
        # 2. miner 20 block
        cls.Miner.make_tip_height_number(cls.node, 20)

    @classmethod
    def teardown_class(cls):
        """
        1. stop ckb node
        2. clean ckb node  tmp dir
        Returns:

        """
        print("stop node and clean")
        cls.node.stop()
        cls.node.clean()

    def test_01_sync_state(self):
        """
        #1.The assume_valid_target specified by ckb, if no assume_valid_target, this will be all zero.
        #2.If no assume_valid_target, this will be true.
        #3. min_chain_work on dev chain = 0x0
        #4. min_chain_work_reached on dev chain is 0x0,so will be reached return true
        Returns:

        """
        self.Ckb_cli.version()
        sync_state = self.Ckb_cli.sync_state(api_url=self.node.getClient().url)
        # 1.The assume_valid_target specified by ckb, if no assume_valid_target, this will be all zero.
        assert (
            sync_state["assume_valid_target"]
            == "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
        # 2.If no assume_valid_target, this will be true.
        assert sync_state["assume_valid_target_reached"] is True
        # 3. min_chain_work on dev chain = 0x0
        assert sync_state["min_chain_work"] == "0x0"
        # 4. min_chain_work_reached on dev chain is 0x0,so will be reached return true
        assert sync_state["min_chain_work_reached"] is True

    def test_02_remove_orphan_blocks_size(self):
        """
        #1. remove orphan_blocks_size
        Returns:

        """
        # 1. remove orphan_blocks_size
        self.Ckb_cli.version()
        sync_state = self.Ckb_cli.sync_state(api_url=self.node.getClient().url)
        assert "orphan_blocks_size" not in sync_state


from test_cases.network_verification.runtime import requires_target


@requires_target
class TestSyncProgress(CkbTest):
    @classmethod
    def setup_class(cls):
        # 每个测试类使用独立节点；失败时也执行清理。
        cls.nodes = []
        cls.started_nodes = []
        try:
            # 无效区块来源：触发接收节点的验块失败。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.bad = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestSyncProgress/bad_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.bad)
            cls.bad.prepare()
            config_path = Path(cls.bad.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.bad)
            cls.bad.start()
            cls.started_nodes.append(cls.bad)
            assert cls.bad.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 接收节点：12 秒上限，正常执行网络验证。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.receiver = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestSyncProgress/receiver_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.receiver)
            cls.receiver.prepare()
            config_path = Path(cls.receiver.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.receiver)
            cls.receiver.start()
            cls.started_nodes.append(cls.receiver)
            assert cls.receiver.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 健康来源：随后提供有效区块，验证同步继续。预算单位为毫秒，不是 VM cycles。
            rpc_port, p2p_port, tcp_port = workload.unused_ports(3)
            cls.healthy = cls.CkbNode.init_dev_by_port(
                cls.CkbNodeConfigPath.CURRENT_TEST,
                f"network_verification/TestSyncProgress/healthy_{rpc_port}",
                rpc_port,
                p2p_port,
            )
            cls.nodes.append(cls.healthy)
            cls.healthy.prepare()
            config_path = Path(cls.healthy.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["tx_pool"].update(
                max_tx_verify_time_ms=12_000,
                max_tx_verify_cycles=1_000_000_000,
                keep_rejected_tx_hashes_days=1,
                keep_rejected_tx_hashes_count=1000,
            )
            config["rpc"]["tcp_listen_address"] = f"127.0.0.1:{tcp_port}"
            if "Subscription" not in config["rpc"]["modules"]:
                config["rpc"]["modules"].append("Subscription")
            config["logger"]["filter"] = "info,ckb_tx_pool=debug,ckb_relay=trace"
            config["network"]["connect_outbound_interval_secs"] = 0
            config_path.write_text(toml.dumps(config))
            # 创世块加入固定脚本和 8 个 6500 CKB 测试输入。
            workload.add_genesis_test_cells(cls.healthy)
            cls.healthy.start()
            cls.started_nodes.append(cls.healthy)
            assert cls.healthy.getClient().tx_pool_info()["tip_number"] == "0x0"

            # 显式建立共同的 30 块链；此处只传区块，不建立 P2P 连接。
            cls.Miner.make_tip_height_number(cls.bad, 30)
            workload.sync_blocks(cls.bad, cls.receiver, 1, 30)
            workload.sync_blocks(cls.bad, cls.healthy, 1, 30)
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.started_nodes):
            node.stop()
        for node in cls.nodes:
            workload.save_node_logs(node)
            node.clean()

    def teardown_method(self, method):
        pass

    # TEST-MAP: VT2-39
    # TEST-EVIDENCE-BEGIN: VT2-39
    # Evidence | Case: VT2-39 (covered)
    # Evidence | Setup: Three actual nodes share common chain. Only bad source installs DAO-zero block through test-only bypass; receiver has normal block synchronization. Fresh class-owned nodes are explicitly configured and started in TestSyncProgress.setup_class; each class has one independent scenario.
    # Evidence | Trigger: Deliver bad block over peer, wait for ban and exact InvalidDAO hash; disconnect/clear loopback ban; healthy separate source supplies valid extension and stops mining.
    # Evidence | Observe: Bad height31 absent/common tip unchanged, valid canonical/pool tip convergence, three stable samples with unverified number>=tip and known header at claimed height.
    # Evidence | Assert: assert invalid_hash and bad_rpc.get_tip_header()["hash"] == invalid_hash
    # Evidence | Assert: workload.wait_for(
    # Evidence |             lambda: client.get_banned_addresses(), "invalid body source banned", 45
    # Evidence |         )
    # Evidence | Assert: assert client.get_tip_header()["hash"] == common
    # Evidence | Assert: assert client.get_block_by_number("0x1f") is None
    # Evidence | Assert: assert any(
    # Evidence |             invalid_hash in line and "InvalidDAO" in line for line in log.splitlines()
    # Evidence |         )
    # Evidence | Assert: workload.wait_for(
    # Evidence |             lambda: client.get_tip_header()["hash"] == expected_tip
    # Evidence |             and client.tx_pool_info()["tip_hash"] == expected_tip,
    # Evidence |             "valid tip/pool convergence",
    # Evidence |             45,
    # Evidence |         )
    # Evidence | Assert: assert expected_tip != invalid_hash
    # Evidence | Assert: assert tip["hash"] == expected_tip
    # Evidence | Assert: assert int(state["unverified_tip_number"], 16) >= int(
    # Evidence |                 tip["number"], 16
    # Evidence |             ), state
    # Evidence | Assert: assert (
    # Evidence |                 header is not None
    # Evidence |                 and header["number"] == state["unverified_tip_number"]
    # Evidence |             ), (state, header)
    # Evidence | Limit: Sequential public progress regression, not deterministic private callback/reset race.
    # Evidence | Limit: Three stable samples span finite0.2s; no continued source mining after convergence.
    # Evidence | Limit: Coverage is static; this B lifecycle review does not establish current real-node per-Case execution outcomes.
    # Evidence | Limit: Initial full execution stopped this selector in setup with missing module-global Path. The import-only correction resolves that name; focused/full post-fix execution remains a separate gate.
    # TEST-EVIDENCE-END: VT2-39
    def test_invalid_block_then_valid_sync_preserves_progress_floor(self):
        """
        1. 坏来源发送会进入体验证的无效区块，确认实际失败证据。
        2. 独立健康来源继续提供有效区块，等待接收节点追上有效链。
        3. 在稳定链高度检查同步状态，避免把瞬时跨 RPC 状态当成断言。
        """
        import copy
        import time

        bad = self.bad
        receiver = self.receiver
        healthy = self.healthy
        bad_rpc, client = bad.getClient(), receiver.getClient()
        common = bad_rpc.get_tip_header()["hash"]
        self.Miner.make_tip_height_number(bad, 31)
        invalid = copy.deepcopy(bad_rpc.get_block_by_number("0x1f"))
        invalid["header"].pop("hash")
        for transaction in invalid["transactions"]:
            transaction.pop("hash", None)
        # All header/body roots remain correct. DAO is checked during full
        # block verification, not by the header synchronization precheck.
        invalid["header"]["dao"] = "0x" + "00" * 32
        bad_rpc.truncate(common)
        invalid_hash = bad_rpc.call("process_block_without_verify", [invalid, False])
        assert invalid_hash and bad_rpc.get_tip_header()["hash"] == invalid_hash
        # The invalid block may cause a ban before a connection poll sees
        # this peer. The subsequent ban AND exact InvalidDAO hash prove
        # delivery; do not require an observable intermediate connection.
        bad.connected(receiver)
        workload.wait_for(
            lambda: client.get_banned_addresses(), "invalid body source banned", 45
        )
        assert client.get_tip_header()["hash"] == common
        assert client.get_block_by_number("0x1f") is None

        log = (Path(receiver.ckb_dir) / "node.log").read_text(errors="replace")
        assert any(
            invalid_hash in line and "InvalidDAO" in line for line in log.splitlines()
        )
        # Both fixtures use loopback. Disconnect the bad source, then clear
        # its IP ban so it does not also block the independent healthy node.
        client.remove_node(bad.get_peer_id())
        for ban in client.get_banned_addresses():
            client.set_ban(ban["address"], "delete", None, None, None)
        # A distinct healthy source resumes ordinary synchronization.
        self.Miner.make_tip_height_number(healthy, 32)
        expected_tip = healthy.getClient().get_tip_header()["hash"]
        workload.connect(healthy, receiver)
        workload.wait_for(
            lambda: client.get_tip_header()["hash"] == expected_tip
            and client.tx_pool_info()["tip_hash"] == expected_tip,
            "valid tip/pool convergence",
            45,
        )
        assert expected_tip != invalid_hash
        for _ in range(3):
            tip = client.get_tip_header()
            state = client.call("sync_state", [])
            assert tip["hash"] == expected_tip
            assert int(state["unverified_tip_number"], 16) >= int(
                tip["number"], 16
            ), state
            header = client.call("get_header", [state["unverified_tip_hash"]])
            assert (
                header is not None
                and header["number"] == state["unverified_tip_number"]
            ), (state, header)
            time.sleep(0.1)
        self.did_pass = True
