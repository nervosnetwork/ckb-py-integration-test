"""PR #5316: header and extension syscalls see only declared, committed origins."""

import copy
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from framework.basic import CkbTest
from test_cases.tx_pool_refactor.tests.test_45_pr5316_time_budget_retry import (
    _fixed_test_binary,
    _sha256,
    _two_ports,
)


# Witness bytes: expected syscall status, syscall (header/extension), source,
# index. The lock fails with exit 42 if the observed status differs. Keeping
# the probe in this Python integration test avoids a product-code or Rust-test
# change; clang emits a temporary RISC-V ELF used as ordinary on-chain data.
PROBE_SOURCE = r"""
.global _start
_start:
    la sp, scratch_end
    addi sp, sp, -32
    li t0, 4
    sd t0, 0(sp)
    addi a0, sp, 8
    mv a1, sp
    li a2, 0
    li a3, 0
    li a4, 1
    li a7, 2074
    ecall
    bnez a0, exit
    lbu s1, 8(sp)
    lbu s2, 9(sp)
    lbu a4, 10(sp)
    lbu a3, 11(sp)
    sd zero, 0(sp)
    addi a0, sp, 8
    mv a1, sp
    li a2, 0
    beqz s2, header
    li a7, 2104
    j probe
header:
    li a7, 2072
probe:
    ecall
    beq a0, s1, success
    li a0, 42
    j exit
success:
    li a0, 0
exit:
    li a7, 93
    ecall
.section .data
scratch:
    .space 64
scratch_end:
"""

# VM0 must reject the extension ecall before any memory access. If the gate
# unexpectedly opens, this tiny lock exits successfully and the test fails.
GATE_SOURCE = r"""
.global _start
_start:
    li a3, 0
    li a4, 1
    li a7, 2072
    ecall
    li t0, 2
    bne a0, t0, failed
    li a7, 2104
    ecall
    li a0, 0
    j exit
failed:
    li a0, 42
exit:
    li a7, 93
    ecall
"""


class TestVisibleCellOrigin(CkbTest):
    def teardown_method(self, method):
        # Preserve the focused pytest log, not a copy of the shared tmp tree.
        pass

    def _signed_cell(self, client, fund_hash, index, key, lock, data="0x"):
        with tempfile.TemporaryDirectory(prefix="pr5316-origin-parent-") as directory:
            tx_file = str(Path(directory) / "parent.json")
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            self.Ckb_cli.tx_init(tx_file, client.url)
            self.Ckb_cli.tx_add_multisig_config(address, tx_file, client.url)
            self.Ckb_cli.tx_add_input(fund_hash, index, tx_file, client.url)
            fund_cell = client.get_transaction(fund_hash)["transaction"]["outputs"][
                index
            ]
            self.Ckb_cli.tx_add_type_out_put(
                lock["code_hash"],
                lock["hash_type"],
                lock["args"],
                hex(int(fund_cell["capacity"], 16) - 1000),
                data,
                tx_file,
                False,
            )
            signature = self.Ckb_cli.tx_sign_inputs(key, tx_file, client.url)[0]
            self.Ckb_cli.tx_add_signature(
                signature["lock-arg"],
                signature["signature"],
                tx_file,
                client.url,
            )
            return self.Tx.build_tx_info(tx_file)

    @staticmethod
    def _child(
        client,
        input_hash,
        code_hash,
        code_dep_hash,
        normal_lock,
        expected,
        syscall,
        source,
        index,
        headers=(),
        extra_deps=(),
    ):
        cell = client.get_transaction(input_hash)["transaction"]["outputs"][0]
        assert cell["lock"]["code_hash"] == code_hash
        witness = "0x" + bytes((expected, syscall, source, index)).hex()
        return {
            "version": "0x0",
            "cell_deps": [
                {
                    "out_point": {"tx_hash": code_dep_hash, "index": "0x0"},
                    "dep_type": "code",
                },
                *extra_deps,
            ],
            "header_deps": list(headers),
            "inputs": [
                {
                    "previous_output": {"tx_hash": input_hash, "index": "0x0"},
                    "since": "0x0",
                }
            ],
            "outputs": [
                {
                    "capacity": hex(int(cell["capacity"], 16) - 1000),
                    "lock": normal_lock,
                    "type": None,
                }
            ],
            "outputs_data": ["0x"],
            "witnesses": [witness],
        }

    @staticmethod
    def _preview(client, tx):
        result = client.test_tx_pool_accept(tx, "passthrough")
        assert int(result["cycles"], 16) > 0, result
        return result

    # TEST-MAP: NTD-43
    def test_header_and_extension_visibility_for_inputs_and_expanded_deps(self):
        binary = _fixed_test_binary(None)
        version = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert "60b8e34" in version, version
        print(f"fixed test binary: {version}; sha256={_sha256(binary)}")

        rpc_port, p2p_port = _two_ports()
        node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST,
            f"tx_pool/pr5316_visible_origin/{rpc_port}",
            rpc_port,
            p2p_port,
        )
        node.prepare()
        shutil.copy2(binary, node.ckb_bin_path)
        started = False
        try:
            node.start()
            started = True
            client = node.getClient()
            self.Miner.make_tip_height_number(node, 30)
            key = self.Config.ACCOUNT_PRIVATE_2
            address = self.Ckb_cli.util_key_info_by_private_key(key)["address"][
                "testnet"
            ]
            with tempfile.TemporaryDirectory(prefix="pr5316-origin-elf-") as directory:
                source_path = Path(directory) / "probe.S"
                elf_path = Path(directory) / "probe"
                gate_source_path = Path(directory) / "gate.S"
                gate_elf_path = Path(directory) / "gate"
                source_path.write_text(PROBE_SOURCE)
                gate_source_path.write_text(GATE_SOURCE)
                compiler = os.environ.get("CKB_PR5316_RISCV_CLANG", "clang")
                for source_file, output_file in (
                    (source_path, elf_path),
                    (gate_source_path, gate_elf_path),
                ):
                    subprocess.run(
                        [
                            compiler,
                            "-target",
                            "riscv64-unknown-elf",
                            "-march=rv64imac",
                            "-mabi=lp64",
                            "-nostdlib",
                            "-fuse-ld=lld",
                            "-Wl,-e,_start",
                            "-Wl,--no-rosegment",
                            str(source_file),
                            "-o",
                            str(output_file),
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                assert elf_path.read_bytes()[:4] == b"\x7fELF"
                assert gate_elf_path.read_bytes()[:4] == b"\x7fELF"
                code_dep_hash = self.Contract.deploy_ckb_contract(
                    key,
                    str(elf_path),
                    enable_type_id=False,
                    api_url=client.url,
                )
                self.Miner.miner_until_tx_committed(node, code_dep_hash)
                gate_dep_hash = self.Contract.deploy_ckb_contract(
                    key,
                    str(gate_elf_path),
                    enable_type_id=False,
                    api_url=client.url,
                )
            self.Miner.miner_until_tx_committed(node, gate_dep_hash)
            code_hash = self.Contract.get_ckb_contract_codehash(
                code_dep_hash,
                0,
                enable_type_id=False,
                api_url=client.url,
            )
            gate_hash = self.Contract.get_ckb_contract_codehash(
                gate_dep_hash,
                0,
                enable_type_id=False,
                api_url=client.url,
            )
            assert code_hash.startswith("0x") and len(code_hash) == 66

            fund = self.Ckb_cli.wallet_transfer_by_private_key(
                key,
                address,
                360000,
                api_url=client.url,
                fee_rate="1000",
            )
            self.Miner.miner_until_tx_committed(node, fund)
            split = self.Tx.send_transfer_self_tx_with_input(
                [fund],
                ["0x0"],
                key,
                output_count=8,
                fee=2000,
                api_url=client.url,
            )
            self.Miner.miner_until_tx_committed(node, split)
            split_tx = client.get_transaction(split)["transaction"]
            assert len(split_tx["outputs"]) == 8
            normal_lock = split_tx["outputs"][0]["lock"]
            probe_lock = {"code_hash": code_hash, "hash_type": "data2", "args": "0x"}
            v0_lock = {"code_hash": gate_hash, "hash_type": "data", "args": "0x"}

            committed_tx = self._signed_cell(client, split, 0, key, probe_lock)
            committed = client.send_transaction(committed_tx)
            self.Miner.miner_until_tx_committed(node, committed)
            input_origin = client.get_transaction(committed)["tx_status"]["block_hash"]
            dep_origin = client.get_transaction(split)["tx_status"]["block_hash"]
            assert input_origin != dep_origin
            assert client.get_block(input_origin)["extension"] is not None
            assert client.get_block(dep_origin)["extension"] is not None

            dep = {"out_point": {"tx_hash": split, "index": "0x1"}, "dep_type": "code"}
            # Both syscalls must distinguish a declared committed origin from
            # an omitted one, for the input and a normal cell dep alike.
            for syscall in (0, 1):
                for source, index, origin, deps in (
                    (1, 0, input_origin, ()),
                    (3, 1, dep_origin, (dep,)),
                ):
                    visible = self._child(
                        client,
                        committed,
                        code_hash,
                        code_dep_hash,
                        normal_lock,
                        0,
                        syscall,
                        source,
                        index,
                        (origin,),
                        deps,
                    )
                    unrelated_origin = dep_origin if source == 1 else input_origin
                    assert unrelated_origin != origin
                    hidden = self._child(
                        client,
                        committed,
                        code_hash,
                        code_dep_hash,
                        normal_lock,
                        2,
                        syscall,
                        source,
                        index,
                        (unrelated_origin,),
                        deps,
                    )
                    self._preview(client, visible)
                    self._preview(client, hidden)
                    wrong = copy.deepcopy(hidden)
                    wrong["witnesses"][0] = (
                        "0x" + bytes((0, syscall, source, index)).hex()
                    )
                    with pytest.raises(Exception, match="42"):
                        client.test_tx_pool_accept(wrong, "passthrough")

            # A pool output has no transaction_info even if a header hash is
            # supplied. Exercise that condition once as input and once as dep.
            pending_tx = self._signed_cell(client, split, 2, key, probe_lock)
            pending = client.send_transaction(pending_tx)
            assert client.get_transaction(pending)["tx_status"]["status"] in {
                "pending",
                "proposed",
            }
            for syscall in (0, 1):
                for source, index, deps in (
                    (1, 0, ()),
                    (
                        3,
                        1,
                        (
                            {
                                "out_point": {"tx_hash": pending, "index": "0x0"},
                                "dep_type": "code",
                            },
                        ),
                    ),
                ):
                    input_hash = pending if source == 1 else committed
                    no_info = self._child(
                        client,
                        input_hash,
                        code_hash,
                        code_dep_hash,
                        normal_lock,
                        2,
                        syscall,
                        source,
                        index,
                        (input_origin,),
                        deps,
                    )
                    self._preview(client, no_info)

            # The dep-group container's block is not an origin of its expanded
            # member. The group cell is committed in a distinct later block.
            group_data = (
                "0x"
                + (1).to_bytes(4, "little").hex()
                + split[2:]
                + (3).to_bytes(4, "little").hex()
            )
            group_tx = self._signed_cell(client, split, 4, key, normal_lock, group_data)
            group = client.send_transaction(group_tx)
            self.Miner.miner_until_tx_committed(node, group)
            group_origin = client.get_transaction(group)["tx_status"]["block_hash"]
            assert group_origin not in {dep_origin, input_origin}
            group_dep = {
                "out_point": {"tx_hash": group, "index": "0x0"},
                "dep_type": "dep_group",
            }
            for syscall in (0, 1):
                hidden_member = self._child(
                    client,
                    committed,
                    code_hash,
                    code_dep_hash,
                    normal_lock,
                    2,
                    syscall,
                    3,
                    1,
                    (group_origin,),
                    (group_dep,),
                )
                visible_member = self._child(
                    client,
                    committed,
                    code_hash,
                    code_dep_hash,
                    normal_lock,
                    0,
                    syscall,
                    3,
                    1,
                    (dep_origin,),
                    (group_dep,),
                )
                self._preview(client, hidden_member)
                self._preview(client, visible_member)

            # In both old VM versions the gate first observes ITEM_MISSING
            # from load_header, then the absent extension ecall must fail.
            for fund_index, hash_type in ((5, "data"), (6, "data1")):
                old_lock = {**v0_lock, "hash_type": hash_type}
                old_tx = self._signed_cell(client, split, fund_index, key, old_lock)
                old_cell = client.send_transaction(old_tx)
                self.Miner.miner_until_tx_committed(node, old_cell)
                old_extension = self._child(
                    client,
                    old_cell,
                    gate_hash,
                    gate_dep_hash,
                    normal_lock,
                    0,
                    1,
                    1,
                    0,
                )
                with pytest.raises(Exception, match=r"InvalidEcall\(2104\)"):
                    client.test_tx_pool_accept(old_extension, "passthrough")

            # At least one positive path also reaches actual pool admission.
            accepted = client.send_transaction(
                self._child(
                    client,
                    committed,
                    code_hash,
                    code_dep_hash,
                    normal_lock,
                    0,
                    0,
                    1,
                    0,
                    (input_origin,),
                )
            )
            assert client.get_transaction(accepted)["tx_status"]["status"] in {
                "pending",
                "proposed",
            }
            assert accepted in set(client.get_raw_tx_pool(False)["pending"]) | set(
                client.get_raw_tx_pool(False)["proposed"]
            )
            self.did_pass = True
        finally:
            if started:
                node.stop()
            node.clean()
