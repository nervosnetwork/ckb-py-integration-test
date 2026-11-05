"""PR #5316: sequential recent-reject overwrites keep one counted key."""

import copy
import json
import os
import shutil
import time
from pathlib import Path

from framework.basic import CkbTest
import toml
from test_cases.network_verification import workload


def _rejection_count(log_path, tx_hash):
    count = 0
    for line in log_path.read_text(errors="replace").splitlines():
        start = line.find('{"accounts":')
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if (
            event.get("event") == "committed_rejection"
            and event.get("hash") == tx_hash[2:]
            and "InvalidHeader" in event.get("reason", "")
        ):
            count += 1
    return count


class TestRecentRejectOverwrite(CkbTest):
    def teardown_method(self, method):
        # Keep the focused pytest output, not the base shared report copier.
        pass

    @staticmethod
    def _wait_for_rejection(client, log_path, tx_hash, events, recent):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = client.get_transaction(tx_hash)["tx_status"]
            count = int(
                client.call("get_overview", [4], try_count=1, timeout=20)["pool"][
                    "total_recent_reject_num"
                ],
                16,
            )
            logged = _rejection_count(log_path, tx_hash)
            if (
                response["status"] == "rejected"
                and "invalidheader" in response["reason"].lower()
                and count == recent
                and logged >= events
            ):
                return
            time.sleep(0.2)
        raise AssertionError(
            f"rejection did not settle: tx={tx_hash}, "
            f"status={response}, recent={count}, events={logged}/{events}"
        )

    @staticmethod
    def _save_log(node, name):
        directory = os.environ.get("CKB_PR5316_RECENT_LOG_DIR")
        if directory:
            target = Path(directory)
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(Path(node.ckb_dir) / "node.log", target / name)

    # TEST-MAP: NTD-81
    def test_repeated_reject_same_hash_does_not_inflate_recent_count(self):
        """
        1. 准备节点和两个仅区块头依赖不同的拒绝交易。
        2. 重复拒绝同一哈希，确认最近拒绝数不被重复累加。
        3. 拒绝另一哈希并重启，确认两个拒绝条目仍可查询。
        """

        # 1. 准备节点和两个仅区块头依赖不同的拒绝交易。
        rpc_port, p2p_port = workload.unused_ports(2)
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_recent_reject_overwrite/{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        config_path = Path(node.ckb_toml_path)
        config = toml.loads(config_path.read_text())
        config["tx_pool"]["recent_reject"] = str(
            Path(node.ckb_dir) / "data/recent-reject"
        )
        config["logger"]["filter"] = "info,ckb_tx_pool::rejection=debug"
        if "Terminal" not in config["rpc"]["modules"]:
            config["rpc"]["modules"].append("Terminal")
        config_path.write_text(toml.dumps(config))
        started = False
        try:
            node.start()
            started = True
            client = node.getClient()
            assert (
                int(
                    client.call("get_overview", [4], try_count=1, timeout=20)["pool"][
                        "total_recent_reject_num"
                    ],
                    16,
                )
                == 0
            )
            self.Miner.make_tip_height_number(node, 30)
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
            base = self.Tx.build_send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                fee=1000,
                api_url=client.url,
            )
            same = copy.deepcopy(base)
            same["header_deps"] = ["0x" + "ff" * 32]
            distinct = copy.deepcopy(base)
            distinct["header_deps"] = ["0x" + "ee" * 32]
            log_path = Path(node.ckb_dir) / "node.log"

            # 2. 重复拒绝同一哈希，确认最近拒绝数不被重复累加。
            for expected_events in (1, 2, 3):
                tx_hash = client.notify_transaction(same)
                self._wait_for_rejection(
                    client,
                    log_path,
                    tx_hash,
                    expected_events,
                    1,
                )
                assert _rejection_count(log_path, tx_hash) == expected_events
                print(f"same hash rejection={expected_events}, recent=1, tx={tx_hash}")

            # 3. 拒绝另一哈希并重启，确认两个拒绝条目仍可查询。
            distinct_hash = client.notify_transaction(distinct)
            assert distinct_hash != tx_hash
            self._wait_for_rejection(client, log_path, distinct_hash, 1, 2)
            diagnostics = log_path.read_text(errors="replace")
            assert "recent_reject write failed" not in diagnostics
            assert "new writes omitted for one second" not in diagnostics
            print(f"distinct hash rejection=1, recent=2, tx={distinct_hash}")
            self._save_log(node, "before-restart.log")

            node.stop()
            started = False
            node.start()
            started = True
            # DB 的近似计数重启后不要求精确为 2；逐 hash 检查持久化结果。
            for rejected_hash in (tx_hash, distinct_hash):
                status = client.get_transaction(rejected_hash)["tx_status"]
                assert status["status"] == "rejected"
                assert "invalidheader" in status["reason"].lower()
            self._save_log(node, "after-restart.log")
            print("restart: both rejected hashes persisted")
            self.did_pass = True
        finally:
            if started:
                self._save_log(node, "final-node.log")
                node.stop()
            node.clean()
