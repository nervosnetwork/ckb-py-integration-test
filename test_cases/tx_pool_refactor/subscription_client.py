"""Line-delimited public subscription RPC, preserving events around ACKs."""

import json
import socket
import time


class SubscriptionClient:
    def __init__(self, port):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buffer = b""
        self.events = []
        self.request_id = 0

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.socket.settimeout(remaining)
            try:
                data = self.socket.recv(65536)
            except socket.timeout:
                return None
            assert data, "subscription closed unexpectedly"
            self.buffer += data
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def call(self, method, params):
        self.request_id += 1
        self.socket.sendall(
            json.dumps(
                {
                    "id": self.request_id,
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                }
            ).encode()
            + b"\n"
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = self.read(deadline - time.monotonic())
            assert message is not None, "subscription ACK deadline"
            if message.get("id") == self.request_id:
                assert "error" not in message, message
                return message["result"]
            self.events.append(message)
        raise AssertionError("subscription ACK missing")

    def collect(self, seconds):
        events, self.events = self.events, []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            message = self.read(deadline - time.monotonic())
            if message is None:
                break
            events.append(message)
        return events

    @staticmethod
    def transaction(message):
        value = message["params"]["result"]
        if isinstance(value, str):
            value = json.loads(value)
        return value["transaction"]

    def close(self):
        self.socket.close()
