import time


def make_tip_height_number(node, number):
    current_tip_number = node.getClient().get_tip_block_number()
    if current_tip_number == number:
        return
        # number < current_tip_number  cut block
    if number < current_tip_number:
        block_hash = node.getClient().get_block_hash(hex(number))
        node.getClient().truncate(block_hash)
        current_tip_number = node.getClient().get_tip_block_number()
        assert current_tip_number == number
        return
        # number >= current_tip_number miner block
    miner_num = number - current_tip_number
    # self.client.
    for i in range(miner_num):
        miner_with_version(node, "0x0")
    current_tip_number = node.getClient().get_tip_block_number()
    assert current_tip_number == number


def wait_for_indexer(node, target_height, timeout=100):
    deadline = time.monotonic() + timeout
    last_indexer_tip = None

    while time.monotonic() < deadline:
        last_indexer_tip = node.getClient().get_indexer_tip()
        if (
            last_indexer_tip is not None
            and int(last_indexer_tip["block_number"], 16) >= target_height
        ):
            return last_indexer_tip
        time.sleep(1)

    last_indexer_tip_number = (
        last_indexer_tip["block_number"] if last_indexer_tip is not None else None
    )
    raise TimeoutError(
        "indexer did not reach target height "
        f"{target_height} within {timeout}s; "
        f"last indexer tip: {last_indexer_tip_number}"
    )


def miner_until_tx_committed(node, tx_hash, with_unknown=False):
    for i in range(100):
        tx_response = node.getClient().get_transaction(tx_hash)
        if tx_response["tx_status"]["status"] == "committed":
            target_height = node.getClient().get_tip_block_number()
            wait_for_indexer(node, target_height)
            return tx_response
        if (
            tx_response["tx_status"]["status"] == "pending"
            or tx_response["tx_status"]["status"] == "proposed"
        ):
            miner_with_version(node, "0x0")
            time.sleep(1)
            continue
        if with_unknown and tx_response["tx_status"]["status"] == "unknown":
            miner_with_version(node, "0x0")
            time.sleep(1)
            continue

        if (
            tx_response["tx_status"]["status"] == "rejected"
            or tx_response["tx_status"]["status"] == "unknown"
        ):
            raise Exception(
                f"status:{tx_response['tx_status']['status']},reason:{tx_response['tx_status']['reason']}"
            )

    raise Exception(
        f"miner 100 block ,but tx_response always pending:{tx_hash}，tx_response:{tx_response}"
    )


# https://github.com/nervosnetwork/rfcs/pull/416
# support > 0x0 when ckb2023 active
def miner_with_version(node, version):
    # get_block_template
    block = node.getClient().get_block_template()
    node.getClient().submit_block(
        block["work_id"], block_template_transfer_to_submit_block(block, version)
    )
    pool = node.getClient().tx_pool_info()
    header = node.getClient().get_tip_header()
    print(
        "miner block num:{number}".format(
            number=int(block["number"].replace("0x", ""), 16)
        )
    )
    print(
        "pool num:{pool_number}, header num:{header_number}".format(
            pool_number=int(pool["tip_number"].replace("0x", ""), 16),
            header_number=int(header["number"].replace("0x", ""), 16),
        )
    )
    for i in range(100):
        pool_info = node.getClient().tx_pool_info()
        tip_number = node.getClient().get_tip_block_number()
        if int(pool_info["tip_number"], 16) == tip_number:
            return
        time.sleep(1)
    raise Exception("pool_info not eq tip number")


def block_template_transfer_to_submit_block(block, version="0x0"):
    block["transactions"].insert(0, block["cellbase"])
    block["transactions"] = [x["data"] for x in block["transactions"]]
    ret = {
        "header": {
            "compact_target": block["compact_target"],
            "dao": block["dao"],
            "epoch": block["epoch"],
            "extra_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "nonce": "0x0",
            "number": block["number"],
            "parent_hash": block["parent_hash"],
            "proposals_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "timestamp": get_hex_timestamp(),
            "transactions_root": "0x0000000000000000000000000000000000000000000000000000000000000000",
            "version": version,
        },
        "extension": block["extension"],
        "uncles": [],
        "transactions": block["transactions"],
        "proposals": block["proposals"],
    }
    return ret


def get_hex_timestamp():
    timestamp = int(time.time() * 1000)
    hex_timestamp = hex(timestamp)
    return hex_timestamp


def compact_to_target(compact):
    exponent = compact >> 24
    mantissa = compact & 0x00FFFFFF
    rtn = 0
    if exponent <= 3:
        mantissa >>= 8 * (3 - exponent)
        rtn = mantissa
    else:
        rtn = mantissa
        rtn <<= 8 * (exponent - 3)
    overflow = mantissa != 0 and (exponent > 32)
    return rtn, overflow


def target_to_compact(target):
    bits = (target).bit_length()
    exponent = (bits + 7) // 8
    compact = (
        target << (8 * (3 - exponent))
        if exponent <= 3
        else (target >> (8 * (exponent - 3)))
    )
    compact = compact | (exponent << 24)
    return compact
