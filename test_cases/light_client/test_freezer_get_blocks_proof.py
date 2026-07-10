import os
import time

from framework.basic import CkbTest


class TestLightClientFreezerGetBlocksProof(CkbTest):
    CKB_BIN_PATH_ENV = "CKB_FREEZER_GET_BLOCKS_PROOF_CKB_BIN"
    MATCH_TX_START_BLOCK_NUMBER = 10
    TIP_BLOCK_NUMBER = 150
    NODE_RPC_PORT = 8331
    NODE_P2P_PORT = 8332
    LIGHT_CLIENT_RPC_PORT = 8333
    EXPECTED_MATCHED_BLOCKS = 1

    @classmethod
    def setup_class(cls):
        """
        Regression for CKB light-client protocol server proof requests over freezer data.

        The full node first commits a transaction matching a non-miner lock, then moves that
        exact block into freezer. The light client filters from that block so it must request
        GetBlocksProof for a single frozen matched block. Vulnerable CKB versions panic while
        serving it. The uncles cache is disabled because a warm cache can mask the missing
        freezer-pruned column. The node is restarted without block assembler after freezer
        completion so the light-client protocol server handles the request with a fresh
        post-freezer snapshot without hitting reward calculation first.
        """
        cls.ckb_light_node = None
        cls.node = cls.CkbNode.init_dev_by_port(
            cls._short_epoch_config(),
            "light_client_freezer/full_node",
            cls.NODE_RPC_PORT,
            cls.NODE_P2P_PORT,
        )
        cls.node.stop()
        cls.node.clean()
        cls.node.prepare(
            other_ckb_config={
                "ckb_logger_filter": "trace",
                "ckb_store_block_extensions_cache_size": "30",
                "ckb_store_block_uncles_cache_size": "0",
                "ckb_store_freezer_enable": "true",
            },
            other_ckb_spec_config={
                "ckb_params_genesis_epoch_length": "1",
                "ckb_name": "ckb_dev",
                "ckb_params_genesis_compact_target": "0x2020000",
                "ckb_params_epoch_duration_target": "8",
            },
        )
        cls.node.start()
        client = cls.node.getClient()

        cls.Miner.make_tip_height_number(cls.node, cls.MATCH_TX_START_BLOCK_NUMBER)
        cls.matched_account = cls.Ckb_cli.util_key_info_by_private_key(
            cls.Config.ACCOUNT_PRIVATE_1
        )
        cls.matched_tx_hash = cls.Ckb_cli.wallet_transfer_by_private_key(
            cls.Config.MINER_PRIVATE_1,
            cls.matched_account["address"]["testnet"],
            100,
            client.url,
            "1500",
        )
        tx_response = cls.Miner.miner_until_tx_committed(cls.node, cls.matched_tx_hash)
        cls.frozen_block_hash = tx_response["tx_status"]["block_hash"]
        cls.frozen_block_number = cls._find_block_number_by_hash(
            client,
            cls.frozen_block_hash,
            cls.MATCH_TX_START_BLOCK_NUMBER,
            client.get_tip_block_number(),
        )

        remaining_blocks = max(cls.TIP_BLOCK_NUMBER - client.get_tip_block_number(), 0)
        if remaining_blocks:
            client.generate_epochs(hex(remaining_blocks))
        cls.Node.wait_node_height(cls.node, cls.TIP_BLOCK_NUMBER, 30)
        cls._wait_freezer_append(cls.frozen_block_number, timeout=120)
        cls._disable_block_assembler_for_restart()
        cls.node.stop()
        cls.node.start()
        cls.Node.wait_node_height(cls.node, cls.TIP_BLOCK_NUMBER, 30)

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestLightClientFreezerGetBlocksProof")
        if getattr(cls, "ckb_light_node", None) is not None:
            try:
                cls.ckb_light_node.stop()
            except Exception as err:
                print(f"ignore light client stop error: {err}")
            try:
                cls.ckb_light_node.clean()
            except Exception as err:
                print(f"ignore light client clean error: {err}")
        if getattr(cls, "node", None) is not None:
            cls.node.stop()
            cls.node.clean()

    def test_get_blocks_proof_for_frozen_matched_block_does_not_panic(self):
        client = self.node.getClient()
        assert int(client.get_current_epoch()["number"], 16) > 2
        assert client.get_block_hash(hex(self.frozen_block_number)) == self.frozen_block_hash

        self.ckb_light_node = self.CkbLightClientNode.init_by_nodes(
            self.CkbLightClientConfigPath.CURRENT_TEST,
            [self.node],
            "light_client_freezer/light_client",
            self.LIGHT_CLIENT_RPC_PORT,
        )
        self.__class__.ckb_light_node = self.ckb_light_node
        self.ckb_light_node.clean()
        self.ckb_light_node.prepare()
        self.ckb_light_node.start()

        self.ckb_light_node.getClient().set_scripts(
            [
                {
                    "script": {
                        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                        "hash_type": "type",
                        "args": self.matched_account["lock_arg"],
                    },
                    "script_type": "lock",
                    "block_number": hex(self.frozen_block_number - 1),
                }
            ]
        )
        self._wait_light_client_get_blocks_proof(timeout=120)
        self._wait_light_client_downloaded_matched_block(timeout=120)

        self.did_pass = True

    @classmethod
    def _find_block_number_by_hash(cls, client, block_hash, start_number, end_number):
        for number in range(start_number, end_number + 1):
            if client.get_block_hash(hex(number)) == block_hash:
                return number
        raise AssertionError(
            f"unable to find committed tx block {block_hash} "
            f"between {start_number} and {end_number}"
        )

    @classmethod
    def _short_epoch_config(cls):
        ckb_bin_path = os.getenv(
            cls.CKB_BIN_PATH_ENV, cls.CkbNodeConfigPath.CURRENT_TEST.ckb_bin_path
        )
        return cls.CkbNodeConfigPath(
            cls.CkbNodeConfigPath.CURRENT_TEST.ckb_config_path,
            cls.CkbNodeConfigPath.CURRENT_TEST.ckb_miner_config_path,
            cls.CkbNodeConfigPath.MAINNET_SPEC_PATH,
            ckb_bin_path,
        )

    @classmethod
    def _wait_freezer_append(cls, block_number, timeout):
        marker = f"Freezer block append {block_number}"
        completed_marker = "Freezer completed"
        deadline = time.time() + timeout
        log_path = cls._full_node_log_path()
        saw_append = False
        while time.time() < deadline:
            log = cls._read_file(log_path)
            if marker in log:
                saw_append = True
            if saw_append and completed_marker in log:
                return
            if "Freezer error" in log:
                raise AssertionError(f"freezer failed, see {log_path}")
            time.sleep(1)
        raise AssertionError(
            f"timeout waiting for '{marker}' followed by '{completed_marker}' "
            f"in {log_path}; the test did not create a fully frozen block"
        )

    @classmethod
    def _disable_block_assembler_for_restart(cls):
        ckb_toml_path = cls.node.ckb_toml_path
        lines = cls._read_file(ckb_toml_path).splitlines(keepends=True)
        filtered = []
        skipping_block_assembler = False
        removed = False
        for line in lines:
            stripped = line.strip()
            if stripped == "[block_assembler]":
                skipping_block_assembler = True
                removed = True
                continue
            if (
                skipping_block_assembler
                and stripped.startswith("[")
                and not stripped.startswith("#")
            ):
                skipping_block_assembler = False
            if not skipping_block_assembler:
                filtered.append(line)

        if not removed:
            return
        with open(ckb_toml_path, "w") as file:
            file.writelines(filtered)

    def _wait_light_client_get_blocks_proof(self, timeout):
        deadline = time.time() + timeout
        log_path = self._light_client_log_path()
        matched_marker = f"matched blocks: {self.EXPECTED_MATCHED_BLOCKS}"
        proof_marker = f"count={self.EXPECTED_MATCHED_BLOCKS}"
        while time.time() < deadline:
            log = self._read_file(log_path)
            if matched_marker in log and "send get blocks proof request" in log and proof_marker in log:
                return
            time.sleep(1)
        raise AssertionError(
            "timeout waiting light-client to send single-block GetBlocksProof for "
            f"frozen block #{self.frozen_block_number} {self.frozen_block_hash}; "
            f"light-client log tail: {self._tail(log_path)}"
        )

    def _wait_light_client_downloaded_matched_block(self, timeout):
        deadline = time.time() + timeout
        full_node_log_path = self._full_node_log_path()
        light_client_log_path = self._light_client_log_path()
        download_marker = (
            f"all matched blocks downloaded, start_number={self.frozen_block_number},"
        )
        matched_count_marker = f"matched_count={self.EXPECTED_MATCHED_BLOCKS}"
        while time.time() < deadline:
            full_node_log = self._read_file(full_node_log_path)
            if "block uncles must be stored" in full_node_log:
                raise AssertionError(
                    "CKB full node panicked while serving GetBlocksProof for "
                    f"frozen block #{self.frozen_block_number} "
                    f"{self.frozen_block_hash}; full-node panic excerpt: "
                    f"{self._panic_excerpt(full_node_log_path)}"
                )
            if "panicked at" in full_node_log or "Stack backtrace" in full_node_log:
                raise AssertionError(
                    "CKB full node panicked while serving light-client proof; "
                    f"full-node panic excerpt: {self._panic_excerpt(full_node_log_path)}"
                )

            light_client_log = self._read_file(light_client_log_path)
            if download_marker in light_client_log and matched_count_marker in light_client_log:
                return
            time.sleep(1)
        raise AssertionError(
            "timeout waiting light-client to download matched frozen block after "
            "GetBlocksProof for "
            f"frozen block #{self.frozen_block_number} {self.frozen_block_hash}; "
            f"light-client log tail: {self._tail(light_client_log_path)}; "
            f"full-node log tail: {self._tail(full_node_log_path)}"
        )

    @classmethod
    def _tail(cls, path, limit=4000):
        return cls._read_file(path)[-limit:]

    @classmethod
    def _panic_excerpt(cls, path, limit=4000):
        log = cls._read_file(path)
        for marker in ("block uncles must be stored", "panicked at", "Stack backtrace"):
            marker_index = log.find(marker)
            if marker_index != -1:
                start = max(marker_index - limit // 2, 0)
                end = min(marker_index + limit // 2, len(log))
                return log[start:end]
        return log[-limit:]

    @classmethod
    def _full_node_log_path(cls):
        return f"{cls.node.ckb_dir}/node.log"

    def _light_client_log_path(self):
        return f"{self.ckb_light_node.tmp_path}/node.log"

    @staticmethod
    def _read_file(path):
        if not os.path.exists(path):
            return ""
        with open(path, "r", errors="ignore") as file:
            return file.read()
