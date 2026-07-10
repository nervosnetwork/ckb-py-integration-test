import time
from typing import Union

import requests

import json


class RPCClient:
    def __init__(self, url):
        self.url = url

    def get_tip_block_number(self):
        return int(self.call("get_tip_block_number", []), 16)

    def get_block_economic_state(self, block_hash):
        return self.call("get_block_economic_state", [block_hash])

    def get_block_filter(self, block_hash):
        return self.call("get_block_filter", [block_hash])

    def get_banned_addresses(self):
        return self.call("get_banned_addresses", [])

    def set_ban(self, address, command, ban_time, absolute, reason):
        return self.call("set_ban", [address, command, ban_time, absolute, reason])

    def get_current_epoch(self):
        return self.call("get_current_epoch", [])

    def get_epoch_by_number(self, epoch_number):
        return self.call("get_epoch_by_number", [epoch_number])

    def get_fork_block(self, block_hash, verbosity):
        return self.call("get_fork_block", [block_hash, verbosity])

    def get_header_by_number(self, block_number, verbosity):
        return self.call("get_header_by_number", [block_number])
        # , verbosity])

        # return self.call("get_header_by_number", [block_number, verbosity])
        # return self.call("get_header_by_number",[block_number, verbosity])

    def get_indexer_tip(self):
        return self.call("get_indexer_tip", [])

    def local_node_info(self):
        return self.call("local_node_info", [])

    def ping_peers(self):
        return self.call("ping_peers", [])

    def remove_node(self, peer_id):
        return self.call("remove_node", [peer_id])

    # //"QmUsZHPbjjzU627UZFt4k8j6ycEcNvXRnVGxCPKqwbAfQS",
    # "/ip4/192.168.2.100/tcp/8114"
    def add_node(self, peer_id, peer_address):
        return self.call("add_node", [peer_id, peer_address])

    def get_block_hash(self, block_number_hex):
        return self.call("get_block_hash", [block_number_hex])

    def get_block_median_time(self, block_hash):
        return self.call("get_block_median_time", [block_hash])

    def get_block(self, block_hash, verbosity=None, with_cycles=None):
        return self.call("get_block", [block_hash, verbosity, with_cycles])

    def get_block_by_number(self, block_number, verbosity=None, with_cycles=None):
        """
        {
          "id": 42,
          "jsonrpc": "2.0",
          "method": "get_block_by_number",
          "params": [
            "0x0"
          ]
        }
        :return:
        """
        return self.call("get_block_by_number", [block_number, verbosity, with_cycles])

    def get_transaction_and_witness_proof(self, tx_hashes, block_hash=None):
        return self.call("get_transaction_and_witness_proof", [tx_hashes, block_hash])

    def sync_state(self):
        return self.call("sync_state", [])

    def truncate(self, block_hash):
        return self.call("truncate", [block_hash])

    def get_consensus(self):
        return self.call("get_consensus", [])

    def get_fee_rate_statics(self, target=None):
        return self.call("get_fee_rate_statics", [target])

    def estimate_fee_rate(self, estimate_mode=None, enable_fallback=None):
        return self.call("estimate_fee_rate", [estimate_mode, enable_fallback])

    def generate_epochs(self, epoch):
        return self.call("generate_epochs", [epoch])

    def generate_block(self):
        return self.call("generate_block", [])

    def get_deployments_info(self):
        return self.call("get_deployments_info", [])

    def get_pool_tx_detail_info(self, tx_hash):
        return self.call("get_pool_tx_detail_info", [tx_hash])

    def get_blockchain_info(self):
        return self.call("get_blockchain_info", [])

    def get_cells(self, search_key, order, limit, after):
        return self.call("get_cells", [search_key, order, limit, after])

    def get_block_template(
        self, bytes_limit=None, proposals_limit=None, max_version=None
    ):
        return self.call(
            "get_block_template",
            [
                # bytes_limit, proposals_limit, max_version
            ],
        )

    def calculate_dao_field(self, block_template):
        return self.call("calculate_dao_field", [block_template])

    def generate_block_with_template(self, block_template):
        return self.call("generate_block_with_template", [block_template])

    def calculate_dao_maximum_withdraw(self, out_point, kind):
        return self.call("calculate_dao_maximum_withdraw", [out_point, kind])

    def clear_banned_addresses(self):
        return self.call("clear_banned_addresses", [])

    def tx_pool_info(self):
        return self.call("tx_pool_info", [])

    def tx_pool_ready(self):
        return self.call("tx_pool_ready", [])

    def get_tip_header(self, verbosity=None):
        return self.call("get_tip_header", [verbosity])

    def verify_transaction_proof(self, tx_proof):
        return self.call("verify_transaction_proof", [tx_proof])

    def get_transaction(self, tx_hash, verbosity=None, only_committed=None):
        if verbosity is None and only_committed is None:
            return self.call("get_transaction", [tx_hash])
        return self.call("get_transaction", [tx_hash, verbosity, only_committed])

    def get_transactions(self, search_key, order, limit, after):
        return self.call("get_transactions", [search_key, order, limit, after])

    def dry_run_transaction(self, tx):
        return self.call("dry_run_transaction", [tx])

    def estimate_cycles(self, tx):
        return self.call("estimate_cycles", [tx])

    def get_transaction_proof(self, tx_hash, block_hash):
        return self.call("get_transaction_proof", [tx_hash, block_hash])

    def send_transaction(self, tx, outputs_validator="passthrough"):
        return self.call("send_transaction", [tx, outputs_validator])

    def send_test_transaction(self, tx, outputs_validator="passthrough"):
        return self.call("send_test_transaction", [tx, outputs_validator])

    def notify_transaction(self, tx):
        return self.call("notify_transaction", [tx])

    def get_raw_tx_pool(self, verbose=None):
        return self.call("get_raw_tx_pool", [verbose])

    def clear_tx_pool(self):
        return self.call("clear_tx_pool", [])

    def clear_tx_verify_queue(self):
        return self.call("clear_tx_verify_queue", [])

    def generate_block(self):
        return self.call("generate_block", [])

    def get_peers(self):
        return self.call("get_peers", [])

    def set_network_active(self, state):
        return self.call("set_network_active", [state])

    def remove_transaction(self, tx_hash):
        return self.call("remove_transaction", [tx_hash])

    def get_live_cell_with_include_tx_pool(
        self, index, tx_hash, with_data=True, include_tx_pool: Union[bool, None] = None
    ):
        """
        over ckb v116.1 version
        https://github.com/nervosnetwork/ckb/blob/bb677558efdc3e5f0759556720b62169469b555d/rpc/README.md#chain-get_live_cell
        Args:
            index:
            tx_hash:
            with_data:boolean
            include_tx_pool:boolean | null

        Returns:CellWithStatus

        """
        return self.call(
            "get_live_cell",
            [{"index": index, "tx_hash": tx_hash}, with_data, include_tx_pool],
        )

    def get_live_cell(self, index, tx_hash, with_data=True):
        """
        under ckb v116.1 version
        https://github.com/nervosnetwork/ckb/blob/bb677558efdc3e5f0759556720b62169469b555d/rpc/README.md#chain-get_live_cell
        Args:
            index:
            tx_hash:
            with_data:boolean

        Returns:CellWithStatus

        """
        return self.call(
            "get_live_cell", [{"index": index, "tx_hash": tx_hash}, with_data]
        )

    def submit_block(self, work_id, block):
        return self.call("submit_block", [work_id, block])

    def subscribe(self, topic):
        return self.call("subscribe", [topic])

    def get_cells_capacity(self, script):
        return self.call("get_cells_capacity", [script])

    def get_current_epoch(self):
        return self.call("get_current_epoch", [])

    def test_tx_pool_accept(self, tx, outputs_validator):
        return self.call("test_tx_pool_accept", [tx, outputs_validator])

    def call(self, method, params, try_count=15):

        headers = {"content-type": "application/json"}
        data = {"id": 42, "jsonrpc": "2.0", "method": method, "params": params}
        print(f"request:url:{self.url},data:\n{json.dumps(data)}")
        for i in range(try_count):
            try:
                response = requests.post(
                    self.url, data=json.dumps(data), headers=headers
                ).json()
                print(f"response:\n{json.dumps(response)}")
                if "error" in response.keys():
                    error_message = response["error"].get("message", "Unknown error")
                    raise Exception(f"Error: {error_message}")

                return response.get("result", None)
            except requests.exceptions.ConnectionError as e:
                print(e)
                print("request too quickly, wait 2s")
                time.sleep(2)
                continue
            except Exception as e:
                print("Exception:", e)
                raise e
        raise Exception("request time out")


if __name__ == "__main__":
    client = RPCClient("https://testnet.ckb.dev/")
    number = client.subscribe("new_tip_header")
    print(number)
