import pytest

from framework.basic import CkbTest


class TestCkbCliRpc117(CkbTest):

    @classmethod
    def setup_class(cls):
        """
        1. start 1 ckb node in tmp/ckb_cli/node dir
        2. miner 20 block
        Returns:

        """
        # 1. start 1 ckb node in tmp/ckb_cli/node dir
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "ckb_cli/node", 8314, 8315
        )
        cls.node.prepare()
        cls.node.start()
        # 2. miner 20 block
        cls.Miner.make_tip_height_number(cls.node, 20)

    @classmethod
    def teardown_class(cls):
        """
        1. stop ckb node
        2. clean ckb node  tmp dir
        Returns:

        """
        print("stop node and clean")
        cls.node.stop()
        cls.node.clean()

    def test_01_sync_state(self):
        """
        #1.The assume_valid_target specified by ckb, if no assume_valid_target, this will be all zero.
        #2.If no assume_valid_target, this will be true.
        #3. min_chain_work on dev chain = 0x0
        #4. min_chain_work_reached on dev chain is 0x0,so will be reached return true
        Returns:

        """
        self.Ckb_cli.version()
        sync_state = self.Ckb_cli.sync_state(api_url=self.node.getClient().url)
        # 1.The assume_valid_target specified by ckb, if no assume_valid_target, this will be all zero.
        assert (
            sync_state["assume_valid_target"]
            == "0x0000000000000000000000000000000000000000000000000000000000000000"
        )
        # 2.If no assume_valid_target, this will be true.
        assert sync_state["assume_valid_target_reached"] is True
        # 3. min_chain_work on dev chain = 0x0
        assert sync_state["min_chain_work"] == "0x0"
        # 4. min_chain_work_reached on dev chain is 0x0,so will be reached return true
        assert sync_state["min_chain_work_reached"] is True

    def test_02_remove_orphan_blocks_size(self):
        """
        #1. remove orphan_blocks_size
        Returns:

        """
        # 1. remove orphan_blocks_size
        self.Ckb_cli.version()
        sync_state = self.Ckb_cli.sync_state(api_url=self.node.getClient().url)
        assert "orphan_blocks_size" not in sync_state
