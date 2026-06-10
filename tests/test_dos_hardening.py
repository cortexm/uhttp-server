#!/usr/bin/env python3
"""DoS hardening regression tests.

Covers crashes that a single malicious request could trigger before the
hardening fixes:

1. Deeply nested JSON body -> RecursionError. json.loads() raises
   RecursionError (a RuntimeError subclass), which is neither ValueError
   nor ClientError, so it used to propagate out of wait() and crash the
   server loop. Now caught and turned into 400.
2. WebSocket auto-pong when the send buffer cap is hit -> OSError. The
   automatic pong reply lives deep inside _ws_process_buffer(); an OSError
   from the send-buffer cap (slow/dead consumer, ping flood) used to
   escape the event loop. Now it closes the connection instead.
3. Custom/unknown response status code -> KeyError in the status line.
   Now tolerated via STATUS_CODES.get().
"""
import errno
import socket
import threading
import time
import unittest

from uhttp import server as uhttp_server
from uhttp.server import (
    HttpConnection, EVENT_WS_CLOSE, EVENT_WS_PING, WS_OPCODE_PING,
)
from tests.test_websocket import build_masked_frame


class TestDeepJsonNoCrash(unittest.TestCase):
    """A deeply nested JSON body must yield 400, not crash the loop."""

    PORT = 9958
    crash = None

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)
        cls._stop = False

        def run():
            while not cls._stop and cls.server:
                try:
                    client = cls.server.wait(timeout=0.1)
                except Exception as err:  # pylint: disable=broad-except
                    # An unhandled exception here means the malicious
                    # request escaped request processing and killed the
                    # loop — exactly the bug under test.
                    cls.crash = repr(err)
                    break
                if client:
                    client.respond({'ok': True})

        cls._thread = threading.Thread(target=run, daemon=True)
        cls._thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls._stop = True
        if cls.server:
            cls.server.close()
        cls._thread.join(timeout=2)

    def _request(self, body, content_type=b'application/json'):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))
        sock.sendall(
            b"POST /x HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: " + content_type + b"\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n" + body)
        sock.settimeout(3.0)
        data = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
        sock.close()
        return data

    def test_deep_json_returns_400_and_survives(self):
        """~100k-deep JSON array: server answers 400 and stays alive."""
        depth = 100000
        body = b'[' * depth + b']' * depth  # valid but pathologically deep
        response = self._request(body)
        self.assertIn(b"400", response, "deep JSON should be rejected as 400")
        self.assertIsNone(
            self.crash, f"server loop crashed: {self.crash}")

        # Prove the loop is still serving requests after the attack.
        good = self._request(b'{"a":1}')
        self.assertIn(b"200", good, "server stopped serving after deep JSON")
        self.assertIsNone(self.crash, f"server loop crashed: {self.crash}")


class TestCustomStatusCode(unittest.TestCase):
    """respond() with a status not in STATUS_CODES must not raise."""

    PORT = 9959

    def test_unknown_status_code(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        stop = {'v': False}
        loop_error = {'v': None}

        def run():
            while not stop['v'] and server._socket is not None:
                try:
                    client = server.wait(timeout=0.1)
                except Exception as err:  # pylint: disable=broad-except
                    loop_error['v'] = repr(err)
                    break
                if client:
                    client.respond(data="custom", status=250)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.3)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('localhost', self.PORT))
            sock.sendall(
                b"GET / HTTP/1.1\r\nHost: localhost\r\n"
                b"Connection: close\r\n\r\n")
            sock.settimeout(2.0)
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            sock.close()
            # Status line present with the custom code, no KeyError crash.
            self.assertIn(b"250", data)
            self.assertTrue(data.startswith(b"HTTP/1.1 250"))
            self.assertIsNone(loop_error['v'], loop_error['v'])
        finally:
            stop['v'] = True
            server.close()
            thread.join(timeout=2)


class _StalledSocket:
    """Socket whose send never drains (EAGAIN), to force the send-buffer
    cap to trip deterministically without any real network."""

    def send(self, _data):
        raise OSError(errno.EAGAIN, "stalled")

    def recv(self, _n):
        raise OSError(errno.EAGAIN, "no data")

    def close(self):
        pass


class _FakeServer:
    """Minimal server stub for constructing a bare HttpConnection."""

    _trusted_proxies = None
    event_mode = True
    is_secure = False

    def remove_connection(self, connection):
        pass


class TestWsPingFloodNoCrash(unittest.TestCase):
    """Auto-pong hitting the send-buffer cap closes instead of crashing."""

    def test_ping_flood_send_cap_does_not_raise(self):
        conn = HttpConnection(
            _FakeServer(), _StalledSocket(), ('1.2.3.4', 1234),
            max_send_buffer_size=256)
        conn._ws_mode = True

        # Flood masked PING frames. Each auto-pong (~102 B) accumulates in
        # the (never-draining) send buffer; after a couple it exceeds the
        # 256 B cap and _send() raises OSError from inside the auto-pong.
        ping = build_masked_frame(WS_OPCODE_PING, b'x' * 100)
        conn._buffer.extend(ping * 20)

        events = []
        for _ in range(50):
            try:
                ready = conn._ws_process_buffer()
            except OSError:
                self.fail(
                    "auto-pong raised OSError — would crash the event loop")
            if not ready:
                break
            events.append(conn._event)
            if conn._event == EVENT_WS_CLOSE:
                break

        self.assertIn(EVENT_WS_PING, events, "expected some pings processed")
        self.assertIn(
            EVENT_WS_CLOSE, events,
            "send-cap overflow should close the WS, not crash")
        self.assertFalse(conn._ws_mode, "connection should leave WS mode")


if __name__ == '__main__':
    unittest.main()
