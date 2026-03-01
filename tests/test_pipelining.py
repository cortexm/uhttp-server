#!/usr/bin/env python3
"""
Test that HTTP pipelining is NOT supported - server processes only first request
and closes connection when pipelined data is detected.
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestPipeliningNotSupported(unittest.TestCase):
    """Test that pipelining is rejected"""

    server = None
    server_thread = None
    request_count = 0
    PORT = 9982

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)
                    if client:
                        cls.request_count += 1
                        client.respond({
                            'request_number': cls.request_count,
                            'path': client.path,
                            'method': client.method
                        })
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """Stop server after all tests"""
        if cls.server:
            cls.server.close()
            cls.server = None

    def setUp(self):
        """Reset request count before each test"""
        TestPipeliningNotSupported.request_count = 0

    def test_pipelining_only_first_request_processed(self):
        """Test that only first pipelined request is processed, connection closed"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        # Send two pipelined requests in one packet
        pipelined_requests = (
            b"GET /test1 HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: keep-alive\r\n"
            b"\r\n"
            b"GET /test2 HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: keep-alive\r\n"
            b"\r\n"
        )

        sock.sendall(pipelined_requests)

        # Read response
        sock.settimeout(1.0)
        all_data = b""
        try:
            while len(all_data) < 8192:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                all_data += chunk
        except socket.timeout:
            pass

        sock.close()
        time.sleep(0.2)

        # Only first request should be processed
        self.assertEqual(self.request_count, 1)
        self.assertIn(b"/test1", all_data)
        # Second request should NOT be in response
        self.assertNotIn(b"/test2", all_data)

    def test_pipelining_post_rejected_with_error(self):
        """Test pipelining with POST - rejected with 400 error"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        # POST with exact Content-Length + pipelined GET
        pipelined_requests = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 14\r\n"
            b"Connection: keep-alive\r\n"
            b"\r\n"
            b'{"id": 123456}'  # Exactly 14 bytes
            b"GET /status HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )

        sock.sendall(pipelined_requests)

        # Read response
        sock.settimeout(1.0)
        all_data = b""
        try:
            while len(all_data) < 8192:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                all_data += chunk
        except socket.timeout:
            pass

        sock.close()
        time.sleep(0.2)

        # Pipelining rejected with 400 error, no request processed
        self.assertEqual(self.request_count, 0)
        self.assertIn(b"400", all_data)


if __name__ == '__main__':
    unittest.main()
