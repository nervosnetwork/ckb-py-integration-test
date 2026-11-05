import framework.helper.contract as contract


class FakeRPCClient:
    def __init__(self, api_url):
        self.api_url = api_url

    def get_consensus(self):
        return {"id": "ckb_dev"}

    def get_transaction(self, tx_hash):
        return {"tx_status": {"block_hash": "0xblock"}}


def test_contract_cell_dep_is_not_selected_as_an_input(monkeypatch):
    contract_hash = "0xcontract"
    funding_hash = "0xfunding"
    added_inputs = []
    added_cell_deps = []

    monkeypatch.setattr(contract, "RPCClient", FakeRPCClient)
    monkeypatch.setattr(
        contract, "get_ckb_contract_codehash", lambda *args, **kwargs: "0xcode"
    )
    monkeypatch.setattr(
        contract,
        "util_key_info_by_private_key",
        lambda private_key: {"address": {"testnet": "ckt1test"}},
    )
    monkeypatch.setattr(
        contract,
        "wallet_get_live_cells",
        lambda address, api_url: {
            "live_cells": [
                {
                    "tx_hash": contract_hash,
                    "output_index": 0,
                    "capacity": "200.0 (CKB)",
                },
                {
                    "tx_hash": funding_hash,
                    "output_index": 1,
                    "capacity": "200.0 (CKB)",
                },
            ]
        },
    )
    monkeypatch.setattr(contract, "tx_init", lambda *args: None)
    monkeypatch.setattr(contract, "tx_add_multisig_config", lambda *args: None)
    monkeypatch.setattr(
        contract,
        "tx_add_input",
        lambda tx_hash, index, *args: added_inputs.append((tx_hash, index)),
    )
    monkeypatch.setattr(contract, "tx_add_header_dep", lambda *args: None)
    monkeypatch.setattr(contract, "tx_add_type_out_put", lambda *args: None)
    monkeypatch.setattr(
        contract,
        "tx_add_cell_dep",
        lambda tx_hash, index, *args: added_cell_deps.append((tx_hash, index)),
    )
    monkeypatch.setattr(
        contract,
        "tx_sign_inputs",
        lambda *args: [{"lock-arg": "0xlock", "signature": "0xsig"}],
    )
    monkeypatch.setattr(contract, "tx_add_signature", lambda *args: None)
    monkeypatch.setattr(contract, "tx_info", lambda *args: None)
    monkeypatch.setattr(contract, "build_tx_info", lambda *args: {"hash": "0xtx"})

    tx = contract.build_invoke_ckb_contract(
        account_private="0xprivate",
        contract_out_point_tx_hash=contract_hash,
        contract_out_point_tx_index=0,
        type_script_arg="0x02",
    )

    assert tx == {"hash": "0xtx"}
    assert added_inputs == [(funding_hash, 1)]
    assert added_cell_deps == [(contract_hash, "0x0")]
