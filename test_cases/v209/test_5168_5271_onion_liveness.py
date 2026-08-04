import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
import toml

from framework.util import get_project_root


ONION_ADDRESS_PATTERN = re.compile(
    r"/onion3/[a-z2-7]{56}:\d+/p2p/[1-9A-HJ-NP-Za-km-z]+"
)
DISCONNECT_ARM_DELAY_SECONDS = 0.5
DISCONNECT_TIMEOUT_SECONDS = 1.0

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_CKB_TOR_ONION_LIVENESS") != "1",
    reason="Requires a real Tor daemon; set RUN_CKB_TOR_ONION_LIVENESS=1 to opt in.",
)


class _ManagedProcess:
    def __init__(self, command, cwd, log_path):
        self.command = [str(arg) for arg in command]
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = self.log_path.open("ab", buffering=0)
        self.process = subprocess.Popen(
            self.command,
            cwd=cwd,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def assert_running(self):
        return_code = self.process.poll()
        assert return_code is None, (
            f"process exited with code {return_code}: {' '.join(self.command)}\n"
            f"{_read_log(self.log_path)}"
        )

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if not self._log_file.closed:
            self._log_file.close()

    def kill(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        if not self._log_file.closed:
            self._log_file.close()


def _read_log(path, offset=0):
    path = Path(path)
    if not path.exists():
        return ""
    with path.open("rb") as log_file:
        log_file.seek(offset)
        return log_file.read().decode("utf-8", errors="replace")


def _wait_for_log(process, text, timeout, offset=0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if text in _read_log(process.log_path, offset):
            return time.monotonic()
        process.assert_running()
        time.sleep(0.1)
    pytest.fail(
        f"timed out after {timeout}s waiting for {text!r} in "
        f"{process.log_path}:\n{_read_log(process.log_path, offset)}"
    )


def _unused_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _unused_tcp_ports(count):
    ports = set()
    while len(ports) < count:
        ports.add(_unused_tcp_port())
    return list(ports)


def _find_tor_binary():
    configured = os.getenv("TOR_BINARY_PATH") or os.getenv("TOR_COMMAND_PATH")
    candidates = [
        configured,
        shutil.which("tor"),
        "/opt/homebrew/bin/tor",
        "/usr/local/bin/tor",
        "/usr/bin/tor",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    pytest.skip("Tor was not found; install it or set TOR_BINARY_PATH")


def _find_ckb_binary():
    configured = os.getenv("CKB_BINARY_PATH")
    binary = Path(
        configured or Path(get_project_root()) / "download" / "current" / "ckb"
    ).expanduser()
    if not binary.is_file():
        pytest.skip(f"CKB binary was not found: {binary}; set CKB_BINARY_PATH")
    return binary.resolve()


def _run(command, cwd=None):
    return subprocess.run(
        [str(arg) for arg in command],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


def _assert_ckb_v209_binary(ckb_binary):
    version = _run([ckb_binary, "--version"])
    assert version.returncode == 0, version.stdout
    assert version.stdout.startswith("ckb 0.209.0 "), version.stdout


def _write_tor_config(path, data_dir, socks_port, control_port):
    path.write_text(
        "\n".join(
            [
                f"DataDirectory {data_dir}",
                f"SocksPort 127.0.0.1:{socks_port}",
                f"ControlPort 127.0.0.1:{control_port}",
                "CookieAuthentication 1",
                "ClientOnly 1",
                "AvoidDiskWrites 1",
                "Log notice stdout",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _start_tor(tor_binary, tor_config, log_path):
    process = _ManagedProcess([tor_binary, "-f", tor_config], None, log_path)
    _wait_for_log(process, "Bootstrapped 100%", timeout=180)
    return process


def _initialize_ckb(ckb_binary, ckb_dir, rpc_port, p2p_port, socks_port, control_port):
    result = _run(
        [
            ckb_binary,
            "init",
            "--chain",
            "dev",
            "--force",
            "--log-to",
            "stdout",
            "--rpc-port",
            rpc_port,
            "--p2p-port",
            p2p_port,
            "-C",
            ckb_dir,
        ]
    )
    assert result.returncode == 0, result.stdout

    config_path = ckb_dir / "ckb.toml"
    config = toml.load(config_path)
    config["network"]["onion"] = {
        "listen_on_onion": True,
        "onion_server": f"127.0.0.1:{socks_port}",
        "p2p_listen_address": f"127.0.0.1:{p2p_port}",
        "tor_controller": f"127.0.0.1:{control_port}",
        "onion_private_key_path": str(ckb_dir / "onion_private_key"),
        "onion_external_port": p2p_port,
    }
    with config_path.open("w", encoding="utf-8") as config_file:
        toml.dump(config, config_file)


def _rpc_call(rpc_port, method):
    request = urllib.request.Request(
        f"http://127.0.0.1:{rpc_port}",
        data=json.dumps(
            {"id": 1, "jsonrpc": "2.0", "method": method, "params": []}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert "error" not in payload, payload
    return payload["result"]


def _last_onion_address(log_path):
    addresses = ONION_ADDRESS_PATTERN.findall(_read_log(log_path))
    assert addresses, f"no Onion address found in {_read_log(log_path)}"
    return addresses[-1]


def _assert_connection_stays_stable(tor, ckb, observation_seconds=5):
    offset = ckb.log_path.stat().st_size
    deadline = time.monotonic() + observation_seconds
    while time.monotonic() < deadline:
        tor.assert_running()
        ckb.assert_running()
        new_log = _read_log(ckb.log_path, offset)
        assert "OnionService disconnected, retrying..." not in new_log, new_log
        assert (
            "CKB has started listening on the onion hidden network" not in new_log
        ), f"unexpected Onion reconnect loop while Tor stayed alive:\n{new_log}"
        time.sleep(0.1)


def _wait_for_rpc_onion_address(rpc_port, expected_address, timeout=10):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            node_info = _rpc_call(rpc_port, "local_node_info")
            advertised_addresses = {
                address["address"] for address in node_info["addresses"]
            }
            if expected_address in advertised_addresses:
                return
        except Exception as error:
            last_error = error
        time.sleep(0.1)
    pytest.fail(
        f"{expected_address} was not advertised by local_node_info within "
        f"{timeout}s; last error: {last_error}"
    )


def test_real_tor_disconnect_is_detected_and_onion_service_recovers(tmp_path):
    """Real Tor regression for CKB PRs #5168 and #5271."""
    tor_binary = _find_tor_binary()
    ckb_binary = _find_ckb_binary()
    _assert_ckb_v209_binary(ckb_binary)

    socks_port, control_port, rpc_port, p2p_port = _unused_tcp_ports(4)

    tor_data_dir = tmp_path / "tor-data"
    tor_data_dir.mkdir()
    tor_config = tmp_path / "torrc"
    _write_tor_config(tor_config, tor_data_dir, socks_port, control_port)

    ckb_dir = tmp_path / "ckb"
    _initialize_ckb(
        ckb_binary,
        ckb_dir,
        rpc_port,
        p2p_port,
        socks_port,
        control_port,
    )

    tor = None
    ckb = None
    try:
        tor = _start_tor(tor_binary, tor_config, tmp_path / "tor-first.log")
        ckb = _ManagedProcess(
            [ckb_binary, "run", "--indexer", "--skip-spec-check"],
            ckb_dir,
            tmp_path / "ckb.log",
        )
        started_message = "CKB has started listening on the onion hidden network"
        _wait_for_log(ckb, started_message, timeout=45)
        first_onion_address = _last_onion_address(ckb.log_path)
        _wait_for_rpc_onion_address(rpc_port, first_onion_address)

        # Give the old polling implementation enough time to finish its first
        # immediate uptime check. Its next check would be roughly 3 seconds
        # later, while TcpStream-close detection should react immediately.
        time.sleep(DISCONNECT_ARM_DELAY_SECONDS)

        disconnect_offset = ckb.log_path.stat().st_size
        disconnect_started_at = time.monotonic()
        tor.kill()
        tor = None
        disconnected_at = _wait_for_log(
            ckb,
            "OnionService disconnected, retrying...",
            timeout=DISCONNECT_TIMEOUT_SECONDS,
            offset=disconnect_offset,
        )
        assert disconnected_at - disconnect_started_at < DISCONNECT_TIMEOUT_SECONDS
        ckb.assert_running()

        recovery_offset = ckb.log_path.stat().st_size
        tor = _start_tor(tor_binary, tor_config, tmp_path / "tor-second.log")
        _wait_for_log(ckb, started_message, timeout=45, offset=recovery_offset)
        recovered_onion_address = _last_onion_address(ckb.log_path)

        assert recovered_onion_address == first_onion_address
        _wait_for_rpc_onion_address(rpc_port, recovered_onion_address)
        assert (ckb_dir / "onion_private_key").is_file()
        _assert_connection_stays_stable(tor, ckb, observation_seconds=3)
    finally:
        if ckb is not None:
            ckb.stop()
        if tor is not None:
            tor.stop()
