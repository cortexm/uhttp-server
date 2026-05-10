#!/usr/bin/env python3
"""
Test send-buffer backpressure cap.

Slow consumers can leave bytes pending in HttpConnection._send_buffer.
Without a cap this grows unboundedly and OOMs on memory-tight devices
(ESP32, etc.). With max_send_buffer_size, _send() raises OSError once
the resulting buffer would exceed the cap, and streaming send_*()
callers translate that into a False return.

A single large initial write into an empty buffer is always accepted
(typical respond(data=big_blob) case).
"""
import socket
import threading
import time
import unittest

from uhttp import server as uhttp_server


class _SlowClientServer:
    """Streams NDJSON records as fast as possible until the client
    stops reading. Records iterations + last send_ndjson return value.
    """

    def __init__(self, port, max_send_buffer_size=None):
        kwargs = {}
        if max_send_buffer_size is not None:
            kwargs['max_send_buffer_size'] = max_send_buffer_size
        self.server = uhttp_server.HttpServer(port=port, **kwargs)
        self.port = port
        self._stop = False
        self._thread = None
        self.last_send_ok = None
        self.send_attempts = 0
        self.stream_clients = []

    def start(self):
        def run():
            # Big payload so kernel TCP buffers fill quickly even on
            # loopback where bytes flow nearly instantly.
            payload = {'msg': 'x' * 16384}  # ~16 KB JSON record
            while not self._stop and self.server:
                client = self.server.wait(timeout=0.05)
                if client and client.path == '/stream':
                    if client.response_ndjson():
                        self.stream_clients.append(client)
                elif client:
                    client.respond("Not found", status=404)
                for c in list(self.stream_clients):
                    self.send_attempts += 1
                    ok = c.send_ndjson(payload)
                    self.last_send_ok = ok
                    if not ok:
                        self.stream_clients.remove(c)
                        c.close()
            self.server.close()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        time.sleep(0.2)

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2)


class TestSendBufferCap(unittest.TestCase):
    """Send-buffer cap kicks in for slow consumers."""

    PORT = 9970

    def _slow_client(self, port, recv_bufsize=2048):
        """TCP client that sets a tiny receive buffer and reads only
        once (the HTTP response headers), then never reads again."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, recv_bufsize)
        sock.connect(('localhost', port))
        sock.sendall(
            b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n")
        sock.settimeout(2.0)
        # Drain just the response headers so the server gets to start
        # streaming, then stop reading.
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(256)
            if not chunk:
                break
            data += chunk
        return sock

    def test_slow_client_trips_cap(self):
        """Slow consumer: server eventually gets False from send_ndjson.

        Uses a monkey-patched _flush_send_buffer to simulate a TCP
        socket that never drains. Necessary because real kernel
        send/recv buffers vary by orders of magnitude across platforms
        (Linux auto-tunes tcp_rmem up to 6 MB), making any
        network-only version of this test non-deterministic.
        """
        original_flush = uhttp_server.HttpConnection._flush_send_buffer
        uhttp_server.HttpConnection._flush_send_buffer = (
            lambda self: False)
        try:
            srv = _SlowClientServer(
                port=self.PORT, max_send_buffer_size=4096)
            srv.start()
            try:
                sock = self._slow_client(srv.port, recv_bufsize=2048)
                try:
                    # Wait up to 3s for the server to hit the cap.
                    deadline = time.time() + 3.0
                    while time.time() < deadline:
                        if srv.last_send_ok is False:
                            break
                        time.sleep(0.05)
                    self.assertEqual(
                        srv.last_send_ok, False,
                        f"send_ndjson never returned False after "
                        f"{srv.send_attempts} attempts")
                    # Server cleaned up the connection.
                    self.assertEqual(len(srv.stream_clients), 0)
                finally:
                    sock.close()
            finally:
                srv.stop()
        finally:
            uhttp_server.HttpConnection._flush_send_buffer = original_flush

    def test_fast_client_does_not_trip_cap(self):
        """Healthy reader: send_ndjson keeps returning True."""
        srv = _SlowClientServer(
            port=self.PORT + 1, max_send_buffer_size=4096)
        srv.start()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('localhost', srv.port))
            sock.sendall(
                b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n")
            sock.settimeout(2.0)
            try:
                # Read continuously for ~0.5 s — server should never
                # see backpressure from a kernel that drains promptly.
                deadline = time.time() + 0.5
                while time.time() < deadline:
                    try:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                    except socket.timeout:
                        break
                # Server kept getting True back from send_ndjson.
                self.assertNotEqual(
                    srv.last_send_ok, False,
                    "fast reader still triggered the cap — bug?")
                self.assertGreater(srv.send_attempts, 10)
            finally:
                sock.close()
        finally:
            srv.stop()

    def test_initial_large_respond_allowed(self):
        """A single respond(data=blob) larger than the cap goes through
        because the buffer was empty when _send() was called."""
        cap = 4096
        big_payload = b"x" * (3 * cap)  # 12 KB
        delivered = {'value': None}

        server = uhttp_server.HttpServer(
            port=self.PORT + 2, max_send_buffer_size=cap)

        def run():
            while server._socket is not None:
                client = server.wait(timeout=0.1)
                if client and client.path == '/big':
                    client.respond(data=big_payload)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.2)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('localhost', self.PORT + 2))
            sock.sendall(
                b"GET /big HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n\r\n")
            sock.settimeout(2.0)
            data = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                data += chunk
            sock.close()
            sep = data.find(b"\r\n\r\n")
            self.assertGreater(sep, 0)
            body = data[sep + 4:]
            delivered['value'] = body
            self.assertEqual(
                len(body), len(big_payload),
                f"body length {len(body)} != expected {len(big_payload)}")
            self.assertEqual(body, big_payload)
        finally:
            server.close()
            t.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
