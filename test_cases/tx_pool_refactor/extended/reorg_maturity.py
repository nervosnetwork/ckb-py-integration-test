"""Full-block and rollback checks after pool verification and template assembly."""

import copy
import re
import shlex
import time
from pathlib import Path
from fractions import Fraction

import toml

from framework.basic import CkbTest
from framework.util import run_command
from test_cases.network_verification import workload
from test_cases.tx_pool_refactor.admission_fixture import (
    Transactions,
    propose,
    rpc_result,
)
from test_cases.tx_pool_refactor.dao_precheck.support import (
    fixvec,
    outpoint_bytes,
    transaction_hash,
)


class TestReorgTimeContext(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.nodes = []
        cls.genesis_time = int(time.time() * 1000) - 8 * 60 * 60 * 1000
        try:
            for name in ("long", "short"):
                rpc, p2p = workload.unused_ports(2)
                node = cls.CkbNode.init_dev_by_port(
                    cls.CkbNodeConfigPath.CURRENT_TEST,
                    f"tx_pool/reorg_time/{name}_{rpc}",
                    rpc,
                    p2p,
                )
                cls.nodes.append(node)
                node.prepare()
                workload.add_genesis_test_cells(node)
                path = Path(node.ckb_toml_path)
                config = toml.loads(path.read_text())
                config["network"]["connect_outbound_interval_secs"] = 0
                config["block_assembler"].update(
                    code_hash=workload.script_hash(
                        workload.script_path("always_success")
                    ),
                    hash_type="data",
                    args="0x",
                )
                path.write_text(toml.dumps(config))
                path = Path(node.ckb_specs_config_path)
                spec = toml.loads(path.read_text())
                spec["params"].update(
                    cellbase_maturity=(20 << 40) | (1 << 24) | 1,
                    genesis_epoch_length=10,
                    epoch_duration_target=14400,
                    permanent_difficulty_in_dummy=False,
                )
                spec["genesis"].update(
                    timestamp=cls.genesis_time, compact_target=0x20010000
                )
                path.write_text(toml.dumps(spec))
                node.ckb_pid = run_command(
                    f"cd {shlex.quote(node.ckb_dir)} && ./ckb run --indexer "
                    "--ba-advanced --skip-spec-check > node.log 2>&1 &"
                )
                time.sleep(3)
            cls.long, cls.short = cls.nodes
        except Exception:
            cls.teardown_class()
            raise

    @classmethod
    def teardown_class(cls):
        for node in reversed(cls.nodes):
            node.stop()
            workload.save_node_logs(node)
            node.clean()

    def teardown_method(self, method):
        pass

    def mine_to(self, node, height, interval_ms):
        client = node.getClient()
        while client.get_tip_block_number() < height:
            template = client.get_block_template()
            number = int(template["number"], 16)
            template["current_time"] = hex(self.genesis_time + number * interval_ms)
            tip = client.generate_block_with_template(template)
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == tip,
                "generated tip installed",
            )

    def total_work(self, node):
        client = node.getClient()
        total = 0
        for height in range(client.get_tip_block_number() + 1):
            header = client.get_header_by_number(hex(height), "0x1")
            target, overflow = self.Miner.compact_to_target(
                int(header["compact_target"], 16)
            )
            assert not overflow and target > 0
            total += (1 << 256) // target
        return total

    @staticmethod
    def epoch(value):
        value = int(value, 16)
        return Fraction(value & 0xFFFFFF) + Fraction(
            (value >> 24) & 0xFFFF, value >> 40
        )

    # TEST-MAP: NTD-106
    def test_shorter_reorg_revalidates_since_and_cellbase_maturity_with_descendants(
        self,
    ):
        """
        1. 动态难度链建立共同输入，生成较长低工作量链和较短高工作量合法分叉。
        2. 在长链接收 absolute/relative since、cellbase input/展开 dep 及后代和独立项。
        3. 实际切到短链，失效根及后代退出而独立项保留；条件再成熟后原交易可重交。
        """
        # 1. 使用正常验块生成 RPC；独立验证高度、工作量及两边的成熟度窗口。
        node, client = self.long, self.long.getClient()
        # Rewards are paid after the proposal window; height 1 has no cellbase output.
        self.mine_to(node, 12, 1000)
        txs = Transactions(node, 106)
        cellbase = client.get_block_by_number("0xc")["transactions"][0]
        assert cellbase["outputs"][0]["lock"]["code_hash"] == txs.lock_hash
        txs.transactions[cellbase["hash"]] = cellbase
        maturity_point = {"tx_hash": cellbase["hash"], "index": "0x0"}
        anchor, group = txs.build([0]), txs.build([4])
        group["outputs_data"] = ["0x" + fixvec([outpoint_bytes(maturity_point)]).hex()]
        txs.transactions[transaction_hash(group)] = group
        for tx in (anchor, group):
            assert client.send_transaction(tx) == transaction_hash(tx)
        self.mine_to(node, 16, 1000)
        for tx in (anchor, group):
            assert (
                client.get_transaction(transaction_hash(tx))["tx_status"]["status"]
                == "committed"
            )
        workload.sync_blocks(node, self.short, 1, 16)
        # Ten-minute spacing crosses the hash-rate bounding interval; one-minute
        # spacing would leave both branches at the same capped difficulty.
        self.mine_to(node, 36, 600000)
        self.mine_to(self.short, 33, 1000)
        short_client = self.short.getClient()
        wanted = short_client.get_tip_header()
        high = client.get_tip_block_number()
        assert int(wanted["number"], 16) < high
        assert self.total_work(self.short) > self.total_work(node)
        mature_epoch = self.epoch(
            client.get_header_by_number("0xc", "0x1")["epoch"]
        ) + Fraction(21, 20)
        assert self.epoch(short_client.get_block_template()["epoch"]) < mature_epoch
        assert self.epoch(client.get_block_template()["epoch"]) >= mature_epoch

        # 2. 所有相关 cell 均在共同前缀，唯一失效原因是切链后的时间上下文。
        absolute = txs.build([1])
        absolute["inputs"][0]["since"] = hex(high)
        relative = txs.build([txs.point(anchor)])
        anchor_height = int(
            client.get_transaction(transaction_hash(anchor))["tx_status"][
                "block_number"
            ],
            16,
        )
        relative["inputs"][0]["since"] = hex((1 << 63) | (high - anchor_height))
        dep = txs.build([3])
        dep["cell_deps"].append(
            {"out_point": txs.point(group), "dep_type": "dep_group"}
        )
        roots = [absolute, relative, dep, txs.build([maturity_point])]
        children = []
        for tx in roots:
            tx_hash = transaction_hash(tx)
            txs.transactions[tx_hash] = tx
            assert client.send_transaction(tx) == tx_hash
            child = txs.build([txs.point(tx)])
            assert client.send_transaction(child) == transaction_hash(child)
            children.append(child)
        independent = txs.build([7])
        independent_hash = client.send_transaction(independent)
        all_hashes = set(map(transaction_hash, roots + children))
        assert set(workload.pool_transactions(client)) == all_hashes | {
            independent_hash
        }

        # 3. submit_block 正常验块触发 reorg；truncate RPC 会主动清池。
        workload.sync_blocks(self.short, node, 17, 33)
        workload.wait_for(
            lambda: client.get_tip_header()["hash"] == wanted["hash"],
            "shorter stronger chain selected",
            60,
        )
        workload.wait_for(
            lambda: client.tx_pool_info()["tip_hash"] == wanted["hash"],
            "pool reorg installed",
        )
        workload.wait_for(
            lambda: set(workload.pool_transactions(client)) == {independent_hash},
            "only time-independent transaction remains",
        )
        assert all_hashes.isdisjoint(
            tx["hash"] for tx in client.get_block_template()["transactions"]
        )
        assert (
            client.get_block_by_number("0xc")["transactions"][0]["hash"]
            == cellbase["hash"]
        )
        while client.get_tip_block_number() < high:
            template = client.get_block_template()
            template["transactions"] = []
            template["proposals"] = []
            tip = client.generate_block_with_template(template)
            workload.wait_for(
                lambda: client.tx_pool_info()["tip_hash"] == tip,
                "maturity tip installed",
            )
        for root, child in zip(roots, children):
            for tx in (root, child):
                assert client.send_transaction(tx) == transaction_hash(tx)
        assert set(workload.pool_transactions(client)) == all_hashes | {
            independent_hash
        }
        self.did_pass = True
