"""Transparent local TCP delay/gate; never parses or constructs P2P messages."""

import collections
import select
import socket
import threading
import time


class ResponseGate:
    def __init__(self, upstream_port, delay=1.0):
        self.upstream_port = upstream_port
        self.delay = delay
        self.held = threading.Event()
        self.stopped = threading.Event()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.pending_bytes = 0
        self.errors = []
        self.sockets = [self.listener]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            self.listener.settimeout(0.2)
            while not self.stopped.is_set():
                try:
                    downstream, _ = self.listener.accept()
                    break
                except socket.timeout:
                    continue
            else:
                return
            upstream = socket.create_connection(
                ("127.0.0.1", self.upstream_port), timeout=5
            )
            self.sockets.extend([downstream, upstream])
            self.forward(downstream, upstream)
        except OSError as error:
            if not self.stopped.is_set():
                self.errors.append(str(error))
        finally:
            for connection in self.sockets:
                connection.close()

    def forward(self, downstream, upstream):
        queue = collections.deque()
        try:
            for connection in (downstream, upstream):
                connection.settimeout(5)
            while not self.stopped.is_set():
                readable = select.select([downstream, upstream], [], [], 0.02)[0]
                for connection in readable:
                    data = connection.recv(65536)
                    if not data:
                        return
                    if connection is downstream:
                        upstream.sendall(data)
                    else:
                        queue.append((time.monotonic() + self.delay, data))
                        self.pending_bytes += len(data)
                while (
                    queue and queue[0][0] <= time.monotonic() and not self.held.is_set()
                ):
                    _, data = queue.popleft()
                    downstream.sendall(data)
                    self.pending_bytes -= len(data)
        except (OSError, ValueError) as error:
            if not self.stopped.is_set():
                self.errors.append(str(error))

    def close(self):
        self.stopped.set()
        for connection in self.sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self.thread.join(timeout=6)
        assert not self.thread.is_alive(), "TCP gate thread did not stop"
