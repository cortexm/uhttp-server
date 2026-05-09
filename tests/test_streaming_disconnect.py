#!/usr/bin/env python3
"""
Test that streaming/multipart connections detect peer EOF and don't
busy-spin when the client closes its end of the TCP connection.

Regression: previously HttpConnection.process_request*() short-circuited
with `if self._is_multipart: return False` without calling recv().
After the client closed TCP the kernel kept signalling readable on the
half-closed fd, so select() returned immediately every iteration and the
event loop spun at 100% CPU until the stream connection went away.
"""
import socket
import threading
import time
import unittest

from uhttp import server as uhttp_server


class _StreamServer:
    """Helper: long-lived NDJSON stream server.

    Holds one stream client indefinitely (no automatic frames). Records
    the number of event-loop iterations so tests can assert that the
    server is NOT busy-spinning after a client disconnect.
    """

    def __init__(self, port, send_idle_data=False):
        self.port = port
        self.server = uhttp_server.HttpServer(port=port)
        self._stop = False
        self._thread = None
        self.iterations = 0
        self.stream_clients = []
        self.send_idle_data = send_idle_data

    def start(self):
        def run():
            while not self._stop and self.server:
                client = self.server.wait(timeout=0.1)
                self.iterations += 1
                if client and client.path == '/stream':
                    if client.response_ndjson():
                        self.stream_clients.append(client)
                elif client:
                    client.respond("Not found", status=404)
                # Optionally push a frame so send_ndjson() also gets a
                # chance to detect peer-close via send error.
                if self.send_idle_data:
                    for c in list(self.stream_clients):
                        if not c.send_ndjson({'tick': self.iterations}):
                            self.stream_clients.remove(c)
                            c.close()
            self.server.close()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        # Allow the listener to bind before tests connect.
        time.sleep(0.2)

    def stop(self):
        self._stop = True
        if self._thread:
            self._thread.join(timeout=2)


class TestStreamingDisconnect(unittest.TestCase):
    """Streaming responses must clean up after client TCP close."""

    PORT = 9994

    def _open_stream(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', port))
        sock.sendall(
            b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n")
        # Wait for response headers so we know the stream is established.
        sock.settimeout(2.0)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        self.assertIn(b"200 OK", data)
        return sock

    def _wait_for(self, predicate, timeout=2.0, interval=0.05):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return False

    def test_client_close_drops_connection(self):
        """After client closes TCP the server must drop the stream
        connection from _waiting_connections within one event tick."""
        srv = _StreamServer(port=self.PORT)
        srv.start()
        try:
            sock = self._open_stream(self.PORT)
            # Server-side bookkeeping: stream connection is in waiting list.
            self.assertTrue(self._wait_for(
                lambda: len(srv.server._waiting_connections) == 1))
            # Client closes its end — server must notice on next read event.
            sock.close()
            self.assertTrue(
                self._wait_for(
                    lambda: len(srv.server._waiting_connections) == 0,
                    timeout=2.0),
                "stream connection was not cleaned up after peer close")
        finally:
            srv.stop()

    def test_client_close_does_not_busy_spin(self):
        """After client closes TCP, wait(timeout=0.1) must keep
        respecting the timeout (not return immediately on the EOF-
        readable fd)."""
        srv = _StreamServer(port=self.PORT + 1)
        srv.start()
        try:
            sock = self._open_stream(srv.port)
            self.assertTrue(self._wait_for(
                lambda: len(srv.server._waiting_connections) == 1))
            # Snapshot iteration counter, drop client, then sample again.
            iters_before = srv.iterations
            sock.close()
            # Wait for cleanup to complete so the EOF-readable fd is
            # gone from the select set.
            self.assertTrue(self._wait_for(
                lambda: len(srv.server._waiting_connections) == 0,
                timeout=2.0))
            iters_at_cleanup = srv.iterations
            # Now measure idle iterations over 0.5 s. With timeout=0.1
            # we expect ~5 iterations. Allow generous slack but reject
            # a busy-spin (which would be hundreds or thousands).
            time.sleep(0.5)
            iters_after = srv.iterations
            idle = iters_after - iters_at_cleanup
            self.assertLessEqual(
                idle, 20,
                f"event loop appears to busy-spin "
                f"({idle} iterations in 0.5s, expected ~5)")
            # Sanity: we DID make progress, not zero.
            self.assertGreater(iters_at_cleanup, iters_before)
        finally:
            srv.stop()

    def test_simultaneous_disconnects(self):
        """Multiple streams closing at once must all be cleaned up."""
        srv = _StreamServer(port=self.PORT + 2)
        srv.start()
        try:
            socks = [self._open_stream(srv.port) for _ in range(5)]
            self.assertTrue(self._wait_for(
                lambda: len(srv.server._waiting_connections) == 5))
            for s in socks:
                s.close()
            self.assertTrue(
                self._wait_for(
                    lambda: len(srv.server._waiting_connections) == 0,
                    timeout=3.0),
                "not all stream connections cleaned up after peer close")
        finally:
            srv.stop()

    def test_idle_stream_stays_alive(self):
        """A streaming connection that has no traffic must NOT be
        garbage-collected by idle cleanup (legitimate long-lived
        SSE/NDJSON pattern)."""
        srv = _StreamServer(port=self.PORT + 3)
        srv.start()
        try:
            sock = self._open_stream(srv.port)
            self.assertTrue(self._wait_for(
                lambda: len(srv.server._waiting_connections) == 1))
            # Wait beyond default request timeout. Stream must survive.
            time.sleep(1.5)
            self.assertEqual(len(srv.server._waiting_connections), 1)
        finally:
            sock.close()
            srv.stop()

    def test_unexpected_client_data_is_discarded(self):
        """If a client sends bytes during a streaming response the
        server must discard them (not propagate to application) and
        keep the stream open."""
        srv = _StreamServer(port=self.PORT + 4)
        srv.start()
        try:
            sock = self._open_stream(srv.port)
            self.assertTrue(self._wait_for(
                lambda: len(srv.server._waiting_connections) == 1))
            # Send a stray request-shaped blob — server must drop it
            # silently while the stream remains established.
            sock.sendall(b"GARBAGE FROM CLIENT\r\n")
            time.sleep(0.4)
            self.assertEqual(
                len(srv.server._waiting_connections), 1,
                "stream connection was dropped after stray client data")
        finally:
            sock.close()
            srv.stop()


if __name__ == '__main__':
    unittest.main()
