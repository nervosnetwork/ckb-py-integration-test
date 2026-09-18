"""Offline regressions for the single-sighash CLI transaction exporters."""

import json

import pytest

from framework.helper import contract, tx

SIGNATURE = "0x" + "11" * 65
SIGNED_WITNESS = "0x5500000010000000550000005500000041000000" + SIGNATURE[2:]


@pytest.fixture(params=[contract.build_tx_info, tx.build_tx_info])
def export(request, tmp_path):
    def run(inputs, witnesses, signatures=None):
        payload = {
            "transaction": {
                "inputs": [{} for _ in range(inputs)],
                "witnesses": witnesses,
            },
            "signatures": signatures or {"lock-arg": [SIGNATURE]},
        }
        path = tmp_path / "transaction.json"
        path.write_text(json.dumps(payload))
        result = request.param(str(path))
        assert json.loads(path.read_text()) == payload
        return result["witnesses"]

    return run


@pytest.mark.parametrize("inputs", [1, 2, 3])
def test_empty_witnesses_are_padded_to_input_count(export, inputs):
    assert export(inputs, []) == [SIGNED_WITNESS] + ["0x"] * (inputs - 1)


def test_existing_and_extra_witnesses_are_preserved(export):
    assert export(2, ["0x", "0x1234", "0xabcd"]) == [SIGNED_WITNESS, "0x1234", "0xabcd"]


def test_first_witness_type_fields_are_preserved(export):
    # WitnessArgs: absent lock, input_type=0xaa, output_type=0xbbcc.
    original = "0x1b00000010000000100000001500000001000000aa02000000bbcc"
    expected = (
        "0x6000000010000000550000005a00000041000000"
        + SIGNATURE[2:]
        + "01000000aa02000000bbcc"
    )
    assert export(2, [original]) == [expected, "0x"]


def test_multiple_signing_groups_are_not_silently_truncated(export):
    with pytest.raises(ValueError, match="single sighash"):
        export(2, [], {"a": [SIGNATURE], "b": [SIGNATURE]})


def test_invalid_first_witness_is_rejected(export):
    with pytest.raises(ValueError, match="WitnessArgs"):
        export(1, ["0x1234"])
