#!/usr/bin/env python3
"""Test strict percent-encoding validation"""
import unittest
import socket
import time
import threading
import json
from uhttp import server as uhttp_server


class TestPercentEncoding(unittest.TestCase):
    """Test that invalid percent-encoding in URL path is rejected"""

    server = None
    server_thread = None
    PORT = 9967

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        client.respond({'path': client.path})
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def get_status(self, path_bytes):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(
            b"GET " + path_bytes + b" HTTP/1.1\r\n"
            b"Host: localhost\r\n\r\n")
        response = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response:
                    break
        except (TimeoutError, OSError):
            pass
        sock.close()
        return response

    def test_valid_percent_encoding(self):
        """Valid percent-encoding like %20 should work"""
        response = self.get_status(b'/hello%20world')
        self.assertIn(b'200', response)

    def test_valid_hex_uppercase(self):
        """%2F (/) should decode correctly"""
        response = self.get_status(b'/test%2Fvalue')
        self.assertIn(b'200', response)

    def test_valid_hex_lowercase(self):
        """%2f (/) lowercase should decode correctly"""
        response = self.get_status(b'/test%2fvalue')
        self.assertIn(b'200', response)

    def test_invalid_percent_encoding_rejected(self):
        """%ZZ is not valid hex — should return 400"""
        response = self.get_status(b'/test%ZZvalue')
        self.assertIn(b'400', response)

    def test_invalid_partial_hex_rejected(self):
        """%2G is not valid hex — should return 400"""
        response = self.get_status(b'/test%2Gvalue')
        self.assertIn(b'400', response)

    def test_truncated_percent_at_end(self):
        """% at end of path without two hex chars — should return 400"""
        response = self.get_status(b'/test%')
        self.assertIn(b'400', response)

    def test_truncated_percent_one_char(self):
        """%2 at end — should return 400"""
        response = self.get_status(b'/test%2')
        self.assertIn(b'400', response)

    def test_no_percent_works(self):
        """Path without percent-encoding should work fine"""
        response = self.get_status(b'/simple/path')
        self.assertIn(b'200', response)


if __name__ == '__main__':
    unittest.main()
