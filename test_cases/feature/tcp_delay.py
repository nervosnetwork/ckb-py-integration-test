"""Delay one direction of one TCP session without interpreting CKB traffic."""

from dataclasses import dataclass
import select
import socket
import threading
import time


@dataclass(frozen=True)
class ProxySnapshot:
    """Counters for this proxy; ``connected_at`` uses ``time.monotonic()``."""

    connections: int
    reconnections: int
    connected_at: float | None
    session_alive: bool
    paused: bool
    buffered_bytes: int
    forwarded_bytes: int
    reverse_forwarded_bytes: int
    error: str | None


class TcpDelayProxy:
    """A sender connects here; its bytes are forwarded to the real receiver.

    Call ``pause()`` only after the peers have negotiated their protocols.
    While paused, sender-to-receiver bytes accumulate in a bounded buffer;
    receiver-to-sender traffic continues. ``resume()`` releases the original
    bytes in order, once. A closed session is an error, never an invitation to
    reconnect. This fixture does not parse, modify, or replay encrypted bytes.
    """

    def __init__(
        self,
        receiver_host,
        receiver_port,
        *,
        listen_host="127.0.0.1",
        max_buffer_bytes=1_048_576,
        connect_timeout=5,
        accept_timeout=30,
        idle_timeout=120,
        max_pause_seconds=60,
    ):
        if (
            max_buffer_bytes <= 0
            or min(connect_timeout, accept_timeout, idle_timeout, max_pause_seconds)
            <= 0
        ):
            raise ValueError("Buffer limit and timeouts must be positive")
        self._target = (receiver_host, receiver_port)
        self._listen_host = listen_host
        self._limit = max_buffer_bytes
        self._connect_timeout = connect_timeout
        self._accept_timeout = accept_timeout
        self._idle_timeout = idle_timeout
        self._max_pause_seconds = max_pause_seconds
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._connected = threading.Event()
        self._thread = None
        self._listener = None
        self._sender = None
        self._receiver = None
        self._address = None
        self._connections = 0
        self._connected_at = None
        self._session_open = False
        self._paused_at = None
        self._pending = bytearray()
        self._reverse_pending = bytearray()
        self._forwarded = 0
        self._reverse_forwarded = 0
        self._error = None

    def start(self):
        with self._lock:
            if self._thread is not None or self._closed.is_set():
                raise RuntimeError("A TCP delay proxy can only be started once")
            self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self._listener.bind((self._listen_host, 0))
                self._listener.listen(2)
                self._listener.setblocking(False)
                self._address = self._listener.getsockname()
                self._thread = threading.Thread(
                    target=self._run, name=f"tcp-delay-{self.port}", daemon=True
                )
                self._thread.start()
            except BaseException:
                self._close_sockets()
                raise
        return self

    @property
    def address(self):
        if self._address is None:
            raise RuntimeError("Start the proxy before requesting its address")
        return self._address

    @property
    def port(self):
        return self.address[1]

    def snapshot(self):
        with self._lock:
            return ProxySnapshot(
                connections=self._connections,
                reconnections=max(0, self._connections - 1),
                connected_at=self._connected_at,
                session_alive=self._session_open and not self._closed.is_set(),
                paused=self._paused_at is not None,
                buffered_bytes=len(self._pending),
                forwarded_bytes=self._forwarded,
                reverse_forwarded_bytes=self._reverse_forwarded,
                error=self._error,
            )

    def wait_connected(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._connected.wait(min(0.05, max(0, deadline - time.monotonic()))):
                self.assert_session_alive()
                return
            state = self.snapshot()
            if state.error or self._closed.is_set():
                raise AssertionError(f"TCP proxy did not connect: {state}")
        raise AssertionError(f"TCP proxy connection timed out: {self.snapshot()}")

    def assert_session_alive(self):
        state = self.snapshot()
        assert (
            state.connections == 1
            and state.reconnections == 0
            and state.connected_at is not None
            and state.session_alive
            and state.error is None
        ), f"Original TCP session is not healthy: {state}"

    def pause(self):
        # Forwarding uses this same lock, so no forward send can begin after
        # pause() returns until resume() is called.
        with self._lock:
            self.assert_session_alive()
            if self._paused_at is not None:
                raise RuntimeError("TCP proxy is already paused")
            self._paused_at = time.monotonic()

    def resume(self):
        with self._lock:
            self.assert_session_alive()
            if self._paused_at is None:
                raise RuntimeError("TCP proxy is not paused")
            if time.monotonic() - self._paused_at > self._max_pause_seconds:
                raise AssertionError("TCP pause exceeded its allowed duration")
            self._paused_at = None

    def close(self):
        self._closed.set()
        self._close_sockets()
        if self._thread is not None:
            self._thread.join(self._connect_timeout + 1)
            if self._thread.is_alive():
                raise RuntimeError("TCP proxy worker did not stop within its timeout")

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _close_sockets(self):
        with self._lock:
            self._session_open = False
            for sock in (self._listener, self._sender, self._receiver):
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    sock.close()

    def _accept_sender(self):
        sender, _ = self._listener.accept()
        with self._lock:
            self._connections += 1
            if self._connections != 1:
                sender.close()
                raise RuntimeError("Unexpected second connection to TCP delay proxy")
            self._sender = sender
            self._receiver = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            receiver = self._receiver
        # close() can close this socket even while its bounded connect waits.
        receiver.settimeout(self._connect_timeout)
        receiver.connect(self._target)
        sender.setblocking(False)
        receiver.setblocking(False)
        with self._lock:
            self._connected_at = time.monotonic()
            self._session_open = True
            self._connected.set()

    def _receive(self, source, pending, direction):
        with self._lock:
            # One extra byte detects overflow rather than silently discarding
            # traffic or allocating an unbounded paused-session buffer.
            available = min(65536, self._limit - len(pending) + 1)
            try:
                data = source.recv(available)
            except BlockingIOError:
                return False
            if not data:
                raise ConnectionError(f"TCP {direction} stream closed")
            if len(pending) + len(data) > self._limit:
                raise BufferError(
                    f"TCP {direction} buffer exceeded {self._limit} bytes"
                )
            pending.extend(data)
        return True

    def _send(self, destination, pending, *, forward):
        with self._lock:
            if not pending or (forward and self._paused_at is not None):
                return False
            try:
                sent = destination.send(pending)
            except BlockingIOError:
                return False
            if sent == 0:
                raise ConnectionError("TCP send returned zero bytes")
            del pending[:sent]
            if forward:
                self._forwarded += sent
            else:
                self._reverse_forwarded += sent
        return True

    def _run(self):
        started = last_activity = time.monotonic()
        try:
            while not self._closed.is_set():
                with self._lock:
                    now = time.monotonic()
                    if (
                        self._connected_at is None
                        and now - started > self._accept_timeout
                    ):
                        raise TimeoutError("No sender connected before accept timeout")
                    if now - last_activity > self._idle_timeout:
                        raise TimeoutError("TCP delay session exceeded idle timeout")
                    if (
                        self._paused_at is not None
                        and now - self._paused_at > self._max_pause_seconds
                    ):
                        raise TimeoutError("TCP delay session exceeded pause timeout")
                    readable = [self._listener]
                    writable = []
                    if self._session_open:
                        readable.extend((self._sender, self._receiver))
                        if self._pending and self._paused_at is None:
                            writable.append(self._receiver)
                        if self._reverse_pending:
                            writable.append(self._sender)
                reads, writes, _ = select.select(readable, writable, [], 0.02)
                if self._listener in reads:
                    self._accept_sender()
                    last_activity = time.monotonic()
                if self._sender in reads:
                    if self._receive(self._sender, self._pending, "sender-to-receiver"):
                        last_activity = time.monotonic()
                if self._receiver in reads:
                    if self._receive(
                        self._receiver, self._reverse_pending, "receiver-to-sender"
                    ):
                        last_activity = time.monotonic()
                if self._receiver in writes:
                    if self._send(self._receiver, self._pending, forward=True):
                        last_activity = time.monotonic()
                if self._sender in writes:
                    if self._send(self._sender, self._reverse_pending, forward=False):
                        last_activity = time.monotonic()
        except Exception as exc:
            if not self._closed.is_set():
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
        finally:
            self._close_sockets()
