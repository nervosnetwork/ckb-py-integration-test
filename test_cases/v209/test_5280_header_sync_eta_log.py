import http.client
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from framework.basic import CkbTest


class TestHeaderSyncEtaLog(CkbTest):
    """End-to-end log-format regression coverage for CKB PR #5280."""

    HEADER_HEIGHT = 10_000
    OLD_CHAIN_AGE_SECONDS = 7 * 24 * 60 * 60
    UNKNOWN_ASSUME_VALID_TARGET = "0x" + "11" * 32
    ETA_PATTERN = re.compile(
        r"ETA: (?:almost synced|\d+ seconds|\d+ minutes|\d+ hours \d+ minutes)\."
    )

    @classmethod
    def setup_class(cls):
        cls.source = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "v209/pr5280_header_sync_eta/source",
            8492,
            8493,
        )
        cls.follower = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST,
            "v209/pr5280_header_sync_eta/follower",
            8494,
            8495,
        )
        cls.follower_process = None
        cls.follower_log_file = None
        try:
            cls.source.clean()
            cls.follower.clean()
            cls.source.prepare()
            cls.follower.prepare()
            cls.source.start()
            cls._generate_blocks_quietly(cls.source, cls.HEADER_HEIGHT)
            cls._start_follower_with_assume_valid_target()
            cls._connect_follower_to_source()
        except Exception:
            cls._stop_follower_process()
            cls.source.stop()
            cls.source.clean()
            cls.follower.clean()
            raise

    @classmethod
    def teardown_class(cls):
        cls._stop_follower_process()
        cls.source.stop()
        cls.source.clean()
        cls.follower.clean()

    def setup_method(self, method):
        self.did_pass = None

    def test_assume_valid_header_sync_uses_concise_eta_log(self):
        """
        Start a fresh node with an unknown assume-valid target and let it sync
        10,000 headers. The progress line emitted at that boundary must use the
        concise ETA wording introduced by PR #5280.
        """
        progress_line = self._wait_for_eta_progress_line(timeout=120)

        assert "best known header 10000-" in progress_line
        assert "CKB is syncing to latest Header" in progress_line
        assert self.ETA_PATTERN.search(progress_line), progress_line
        assert "Please wait." not in progress_line
        assert "Need " not in progress_line
        assert self.follower_process.poll() is None
        assert self.follower.getClient().get_tip_header()["hash"].startswith("0x")
        self.did_pass = True

    @classmethod
    def _generate_blocks_quietly(cls, node, target_height):
        rpc_url = urlparse(node.getClient().url)
        connection = http.client.HTTPConnection(
            rpc_url.hostname,
            rpc_url.port,
            timeout=30,
        )
        try:
            first_timestamp = int((time.time() - cls.OLD_CHAIN_AGE_SECONDS) * 1_000)
            for height in range(1, target_height + 1):
                template_deadline = time.monotonic() + 10
                while True:
                    block_template = cls._rpc_call(
                        connection,
                        "get_block_template",
                        [],
                        request_id=height * 2 - 1,
                    )
                    template_height = int(block_template["number"], 16)
                    if template_height == height:
                        break
                    assert template_height < height
                    assert time.monotonic() < template_deadline
                    time.sleep(0.01)

                # Keep the whole synthetic chain more than 24 hours behind the
                # wall clock. Otherwise CKB intentionally abandons an unknown
                # assume-valid target before BlockFetchCMD logs its ETA.
                block_template["current_time"] = hex(first_timestamp + height)
                result = cls._rpc_call(
                    connection,
                    "generate_block_with_template",
                    [block_template],
                    request_id=height * 2,
                )
                assert result.startswith("0x")

            target_tip_hash = cls._rpc_call(
                connection,
                "get_block_hash",
                [hex(target_height)],
                request_id=target_height * 2 + 1,
            )

            # A node whose own tip is older than 24 hours is also in IBD and
            # will refuse to serve headers. Briefly append a current-time block
            # so the source leaves IBD, then truncate it. CKB deliberately
            # remembers that IBD has finished for the lifetime of the process.
            current_template = cls._wait_for_template_height(
                connection,
                target_height + 1,
                request_id=target_height * 2 + 2,
            )
            current_template["current_time"] = hex(int(time.time() * 1_000))
            cls._rpc_call(
                connection,
                "generate_block_with_template",
                [current_template],
                request_id=target_height * 2 + 3,
            )
            chain_info = cls._rpc_call(
                connection,
                "get_blockchain_info",
                [],
                request_id=target_height * 2 + 4,
            )
            assert chain_info["is_initial_block_download"] is False
            cls._rpc_call(
                connection,
                "truncate",
                [target_tip_hash],
                request_id=target_height * 2 + 5,
            )
            chain_info = cls._rpc_call(
                connection,
                "get_blockchain_info",
                [],
                request_id=target_height * 2 + 6,
            )
            assert chain_info["is_initial_block_download"] is False
        finally:
            connection.close()

        current_height = node.getClient().get_tip_block_number()
        assert current_height == target_height

    @classmethod
    def _wait_for_template_height(cls, connection, expected_height, request_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            block_template = cls._rpc_call(
                connection,
                "get_block_template",
                [],
                request_id=request_id,
            )
            if int(block_template["number"], 16) == expected_height:
                return block_template
            time.sleep(0.01)
        raise AssertionError(f"block template did not reach height {expected_height}")

    @classmethod
    def _start_follower_with_assume_valid_target(cls):
        log_path = Path(cls.follower.ckb_dir) / "node.log"
        cls.follower_log_file = log_path.open("w", encoding="utf-8")
        cls.follower_process = subprocess.Popen(
            [
                str(Path(cls.follower.ckb_dir) / "ckb"),
                "run",
                "--indexer",
                "--skip-spec-check",
                "--assume-valid-target",
                cls.UNKNOWN_ASSUME_VALID_TARGET,
            ],
            cwd=cls.follower.ckb_dir,
            stdout=cls.follower_log_file,
            stderr=subprocess.STDOUT,
        )
        cls._wait_for_rpc(cls.follower)

    @classmethod
    def _connect_follower_to_source(cls):
        peer_id = cls.source.get_peer_id()
        peer_address = cls.source.get_peer_address()
        cls.follower.getClient().add_node(peer_id, peer_address)

    @classmethod
    def _wait_for_rpc(cls, node, timeout=30):
        rpc_url = urlparse(node.getClient().url)
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            connection = http.client.HTTPConnection(
                rpc_url.hostname,
                rpc_url.port,
                timeout=1,
            )
            try:
                cls._rpc_call(connection, "get_tip_block_number", [])
                return
            except Exception as error:
                last_error = error
                time.sleep(0.1)
            finally:
                connection.close()
        raise AssertionError(f"follower RPC did not start: {last_error}")

    @classmethod
    def _wait_for_eta_progress_line(cls, timeout):
        log_path = Path(cls.follower.ckb_dir) / "node.log"
        deadline = time.monotonic() + timeout
        last_log = ""
        while time.monotonic() < deadline:
            if cls.follower_process.poll() is not None:
                raise AssertionError(
                    f"follower exited with {cls.follower_process.returncode}:\n{last_log}"
                )
            if log_path.is_file():
                last_log = log_path.read_text(encoding="utf-8", errors="replace")
                for line in last_log.splitlines():
                    if "CKB is syncing to latest Header" in line and "ETA:" in line:
                        return line
            time.sleep(0.2)
        raise AssertionError(f"ETA progress log was not emitted:\n{last_log[-4000:]}")

    @classmethod
    def _stop_follower_process(cls):
        if cls.follower_process is not None and cls.follower_process.poll() is None:
            cls.follower_process.terminate()
            try:
                cls.follower_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.follower_process.kill()
                cls.follower_process.wait(timeout=5)
        if cls.follower_log_file is not None:
            cls.follower_log_file.close()

    @staticmethod
    def _rpc_call(connection, method, params, request_id=1):
        request = {
            "id": request_id,
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        connection.request(
            "POST",
            "/",
            body=json.dumps(request),
            headers={"Content-Type": "application/json"},
        )
        http_response = connection.getresponse()
        response = json.loads(http_response.read().decode("utf-8"))
        assert http_response.status == 200, response
        assert "result" in response, response
        return response["result"]
