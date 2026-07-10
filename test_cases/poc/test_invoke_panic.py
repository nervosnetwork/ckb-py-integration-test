# from framework.basic import CkbTest
# from framework.util import get_project_root
#
#
# class TestCkbJsVm(CkbTest):
#     cluster: CkbTest.Cluster
#     ckb_js_vm_deploy_hash: str
#     ckb_js_vm_codeHash: str
#     js_code_deploy_hash: str
#
#     @classmethod
#     def setup_class(cls):
#         """
#         1. star 4 node in tmp/cluster/hardFork dir
#         2. link ckb node each other
#         3. deploy contract
#         4. miner 1000 block
#         5. deploy ckb-js-vm
#         Returns:
#
#         """
#
#         # 1. star 4 node in tmp/cluster/hardFork dir
#         nodes = [
#             cls.CkbNode.init_dev_by_port(
#                 cls.CkbNodeConfigPath.CURRENT_TEST,
#                 "cluster1/hardFork/node{i}".format(i=i),
#                 8114 + i,
#                 8225 + i,
#             )
#             for i in range(1)
#         ]
#         cls.cluster = cls.Cluster(nodes)
#         cls.cluster.prepare_all_nodes(
#             other_ckb_config={"ckb_logger_filter": "info,ckb_script=debug"}
#         )
#         cls.cluster.start_all_nodes()
#
#         # 2. link ckb node each other
#         cls.cluster.connected_all_nodes()
#
#     @classmethod
#     def teardown_class(cls):
#         print("\nTeardown TestClass1")
#         cls.cluster.stop_all_nodes()
#         cls.cluster.clean_all_nodes()
#
#     def test_01_deploy_panic(self):
#         deploy_hash = self.Contract.deploy_ckb_contract(
#             self.Config.ACCOUNT_PRIVATE_2,
#             "/Users/guopenglin/demo4/ckb-integration-test/ckb-contract-tests/demo/contracts/panic-repro/target/riscv64imac-unknown-none-elf/release/panic-load-header",
#             enable_type_id=False,
#             api_url=self.cluster.ckb_nodes[0].getClient().url,
#         )
#         self.Miner.miner_until_tx_committed(
#             self.cluster.ckb_nodes[0], deploy_hash, with_unknown=True
#         )
#         account_args = self.Ckb_cli.util_key_info_by_private_key(
#             self.Config.ACCOUNT_PRIVATE_1
#         )["lock_arg"]
#         # invoke load_block_extension
#         account = self.Ckb_cli.util_key_info_by_private_key(self.Config.MINER_PRIVATE_1)
#         pending_tx = self.Ckb_cli.wallet_transfer_by_private_key(
#             self.Config.ACCOUNT_PRIVATE_2,
#             account["address"]["testnet"],
#             100,
#             self.cluster.ckb_nodes[0].getClient().url,
#             "1500",
#         )
#
#         invoke_hash = self.Contract.invoke_ckb_contract(
#             self.Config.ACCOUNT_PRIVATE_1,
#             contract_out_point_tx_hash=deploy_hash,
#             contract_out_point_tx_index=0,
#             type_script_arg=f"0x000000",
#             data="0x1234",
#             hash_type="data2",
#             api_url=self.cluster.ckb_nodes[0].getClient().url,
#             output_lock_arg=account_args,
#             cell_deps=[
#                 {
#                     "tx_hash": pending_tx,
#                     "index": "0x0",
#                 }
#             ],
#         )
#         # self.Miner.miner_until_tx_committed(self.cluster.ckb_nodes[0], invoke_hash)
#         # self.Tx.send_transfer_self_tx_with_input( [invoke_hash],
#         #     ["0x0"],
#         #     self.Config.ACCOUNT_PRIVATE_1,
#         #     output_count=1,
#         #     fee=1000,
#         #     api_url=self.cluster.ckb_nodes[0].getClient().url,
#         #     dep_cells=[
#         #         {"tx_hash": deploy_hash, "index_hex": "0x0"}
#         #     ],
#         # )
