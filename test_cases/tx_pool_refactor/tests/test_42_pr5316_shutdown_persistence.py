"""PR #5316: a graceful stop retains settled pool state under notify pressure."""

import subprocess
import threading
from pathlib import Path

import toml

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests.test_38_pr5316_template_notify import (
    _HoldingServer,
    _unused_port,
    _wait_for,
)


class TestShutdownPersistence(CkbTest):
    def teardown_method(self, method):
        # Keep pytest output; the base copier uses a shared report path.
        pass

    # TEST-MAP: NTD-67
    def test_stop_with_pending_notify_saves_accepted_transaction(self):
        server = _HoldingServer()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        node = None
        process = None
        node_log = None
        restarted = False
        try:
            rpc_port, p2p_port = _unused_port(), _unused_port()
            node = self.CkbNode.init_dev_by_port(
                self.CkbNodeConfigPath.CURRENT_TEST,
                f"tx_pool/pr5316_shutdown_persistence/{rpc_port}",
                rpc_port,
                p2p_port,
            )
            node.prepare()
            node.start()
            self.Miner.make_tip_height_number(node, 30)
            client = node.getClient()
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=client.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(node, fund)
            node.stop()

            saved = list(Path(node.ckb_dir).rglob("persisted_data.v2"))
            assert len(saved) == 1, saved
            snapshot = saved[0]
            before = snapshot.read_bytes()
            assert before.startswith(b"CKBTPV2\0")

            config_path = Path(node.ckb_toml_path)
            config = toml.loads(config_path.read_text())
            config["block_assembler"]["notify"] = [
                f"http://127.0.0.1:{server.server_address[1]}/template"
            ]
            config["block_assembler"]["notify_timeout_millis"] = 15_000
            config_path.write_text(toml.dumps(config))

            node_log = open(Path(node.ckb_dir) / "shutdown-notify.log", "w")
            process = subprocess.Popen(
                [
                    node.ckb_bin_path,
                    "run",
                    "-C",
                    node.ckb_dir,
                    "--indexer",
                    "--skip-spec-check",
                ],
                stdout=node_log,
                stderr=subprocess.STDOUT,
            )

            def ready():
                assert process.poll() is None, f"CKB exited: {process.returncode}"
                try:
                    return client.get_tip_header()
                except Exception:
                    return None

            _wait_for("restarted CKB RPC", ready)
            client.get_block_template()
            _wait_for("held template notification", lambda: server.snapshot()[1] == 1)

            retained = self.Tx.send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            pool = client.get_raw_tx_pool(False)
            assert retained in set(pool["pending"]) | set(pool["proposed"])
            info = client.tx_pool_info()
            assert int(info["pending"], 16) + int(info["proposed"], 16) >= 1
            assert server.snapshot()[1] == 1

            process.terminate()
            # The service has two separate shutdown budgets; this bound is
            # deliberately not a claim that the whole stop fits in 30s.
            process.wait(timeout=90)
            _wait_for(
                "held notify cancellation",
                lambda: all(item["closed_by_client"] for item in server.snapshot()[0]),
                seconds=5,
            )
            _wait_for("notify handler drain", lambda: server.snapshot()[1] == 0)
            after = snapshot.read_bytes()
            assert after.startswith(b"CKBTPV2\0") and after != before
            assert (
                "CKB shutdown"
                in (Path(node.ckb_dir) / "shutdown-notify.log").read_text()
            )

            server.release.set()
            node.start()
            restarted = True

            def replayed():
                raw = node.getClient().get_raw_tx_pool(False)
                return retained in set(raw["pending"]) | set(raw["proposed"])

            _wait_for("accepted transaction replay", replayed, seconds=20)
            assert (
                node.getClient().get_transaction(retained)["transaction"]["hash"]
                == retained
            )
            print(
                f"shutdown: tx={retained}, saved_bytes={len(before)}->{len(after)}, "
                f"notify_count={len(server.snapshot()[0])}, replayed=True"
            )
            self.did_pass = True
        finally:
            server.release.set()
            if node is not None and (restarted or node.ckb_pid != -1):
                node.stop()
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
            if node_log is not None:
                node_log.close()
            if node is not None:
                node.clean()
