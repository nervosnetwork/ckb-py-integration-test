"""复用已有 100 CKB 转账用例，检查合法容量参数兼容性。"""

from contextlib import ExitStack
from pathlib import Path
import socket

import pytest

from framework.util import get_project_root
from suites.ckb_cli.tests.test_api_server_transfer import running, wait_for_rpc
from test_cases.framework import test_01_helper_ckb_cli as existing_cases


@pytest.fixture
def wallet_case(tmp_path):
    root = Path(__file__).resolve().parents[3]
    assert (
        Path(get_project_root()) == root
    ), "Run from a checkout named ckb-py-integration-test"
    case = existing_cases.TestCkbCli("test_06_account1_transfer_account2")
    with ExitStack() as reservations:
        ports = [reservations.enter_context(socket.socket()) for _ in range(2)]
        for port in ports:
            port.bind(("127.0.0.1", 0))
        rpc_port, p2p_port = [port.getsockname()[1] for port in ports]
    node = case.CkbNode.init_dev_by_port(
        case.CkbNodeConfigPath.CURRENT_TEST,
        f"ckb_cli_valid_fee/{tmp_path.name}",
        rpc_port,
        p2p_port,
    )
    case.node = node
    log = tmp_path / "node.log"
    try:
        node.prepare()
        with running(
            [node.ckb_bin_path, "run", "--indexer"], node.ckb_dir, log
        ) as process:
            wait_for_rpc(process, node.getClient().url, "get_indexer_tip", [], log)
            case.Miner.make_tip_height_number(node, 20)
            yield case
    finally:
        node.clean()


# TEST-MAP: CLI-TRANSFER-691-01
@pytest.mark.parametrize(
    "max_tx_fee", [None, "0.001"], ids=["default", "max_fee_0_001"]
)
def test_wallet_transfer_fee_capacity(wallet_case, max_tx_fee):
    """
    1. 复用原用例的 dev 节点和已注资账户，记录实际 CLI 版本。
    2. 复用原 100 CKB 转账流程，省略参数或带 --max-tx-fee 0.001，检查交易确认及收款增量。
    """
    # 1. 记录原 helper 实际调用的 CLI 版本。
    assert "ckb-cli" in wallet_case.Ckb_cli.version()

    # 2. 原用例内部等待 committed，并断言收款余额增加恰好 100 CKB。
    wallet_case.test_06_account1_transfer_account2(max_tx_fee=max_tx_fee)
