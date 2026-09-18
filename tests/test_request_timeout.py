#!/usr/bin/env python3
"""Test request_timeout — slow header protection"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server
from tests.testutils import wait_until_listening


class TestRequestTimeout(unittest.TestCase):
    """Test that slow headers are timed out"""

    server = None
    server_thread = None
    PORT = 9966
    TIMEOUT = 0.5  # minimal timeout for fast tests

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, request_timeout=cls.TIMEOUT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        client.respond({'status': 'ok'})
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        wait_until_listening(cls.PORT)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def test_fast_request_succeeds(self):
        """Normal request within timeout should succeed"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        response = sock.recv(4096)
        sock.close()
        self.assertIn(b'200', response)

    def _trigger_maintenance(self):
        """Idle cleanup is traffic-driven, so give the loop an event to ride on

        Without this a lone stalled client is never timed out and the test
        only waits out its own socket timeout.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(b"GET /trigger HTTP/1.1\r\nHost: localhost\r\n\r\n")
        sock.recv(4096)
        sock.close()

    def test_slow_header_gets_408(self):
        """Headers that never complete must be answered with 408"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(b"GET / HTTP/1.1\r\n")
        time.sleep(self.TIMEOUT + 0.2)
        self._trigger_maintenance()
        try:
            response = sock.recv(4096)
        finally:
            sock.close()
        self.assertIn(b'408', response)

    def test_slow_header_does_not_block_others(self):
        """Slow client should not prevent fast clients from being served"""
        # Start slow client
        slow = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        slow.settimeout(5.0)
        slow.connect(('localhost', self.PORT))
        slow.sendall(b"GET / HTTP/1.1\r\n")  # Partial headers

        time.sleep(0.3)

        # Fast client should still work
        fast = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        fast.settimeout(3.0)
        fast.connect(('localhost', self.PORT))
        fast.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        response = fast.recv(4096)
        fast.close()
        self.assertIn(b'200', response)

        slow.close()


if __name__ == '__main__':
    unittest.main()
