#!/usr/bin/env python3
"""
Test Content-Length security - ensures server respects Content-Length header
and prevents HTTP request smuggling attacks
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestContentLengthSecurity(unittest.TestCase):
    """Test suite for Content-Length security"""

    server = None
    server_thread = None
    request_count = 0
    last_request_data = None  # pylint: disable=unsubscriptable-object
    PORT = 9987

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
                        cls.last_request_data = {
                            'method': client.method,
                            'path': client.path,
                            'data': client.data,
                            'headers': client._headers
                        }
                        client.respond({'ok': True, 'request_number': cls.request_count})
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
        """Reset before each test"""
        TestContentLengthSecurity.request_count = 0
        TestContentLengthSecurity.last_request_data = None

    def test_content_length_smaller_than_data(self):
        """
        Test: Content-Length is 10 but actual data is 30 bytes
        Expected: Server rejects with 400 error (pipelining not supported)
        """
        time.sleep(0.3)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        # Send POST with Content-Length: 10 but 30 bytes of data
        request = (
            b"POST /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 10\r\n"
            b"Connection: keep-alive\r\n"
            b"\r\n"
            b'{"id":123}EXTRA_DATA_20_BYTES!'  # First 10 bytes: {"id":123}
        )

        sock.sendall(request)
        response = sock.recv(4096).decode()
        sock.close()

        time.sleep(0.2)

        # Server should reject with 400 error due to extra data
        self.assertIn("400", response)
        self.assertIsNone(self.last_request_data)

    def test_content_length_with_pipelined_request(self):
        """
        Test: POST with exact Content-Length followed by another request in same packet
        Expected: Server rejects with 400 error (pipelining not supported)
        """
        initial_count = self.request_count

        time.sleep(0.3)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        # POST with exact Content-Length + pipelined GET
        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 13\r\n"
            b"Connection: keep-alive\r\n"
            b"\r\n"
            b'{"test":true}'  # Exactly 13 bytes
            b"GET /smuggled HTTP/1.1\r\n"  # This is pipelining attempt
            b"Host: localhost\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )

        sock.sendall(request)

        # Read response - server should reject with 400 (pipelining not supported)
        response1 = sock.recv(4096).decode()

        sock.close()
        time.sleep(0.3)

        # Server should reject with 400 error, no request processed
        self.assertEqual(self.request_count - initial_count, 0)
        self.assertIn("400", response1)

    def test_invalid_content_length(self):
        """
        Test: Content-Length with invalid value
        Expected: Server returns 400 Bad Request
        """
        time.sleep(0.3)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        request = (
            b"POST /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: invalid\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )

        sock.sendall(request)
        response = sock.recv(4096).decode()
        sock.close()

        self.assertIn("400", response)

    def test_zero_content_length(self):
        """
        Test: POST with Content-Length: 0
        Expected: Server accepts request with no body
        """
        initial_count = self.request_count

        time.sleep(0.3)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        request = (
            b"POST /empty HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )

        sock.sendall(request)
        response = sock.recv(4096).decode()
        sock.close()

        self.assertIn("200", response)
        self.assertGreater(self.request_count, initial_count)

    def test_multiple_content_lengths(self):
        """
        Test: Multiple Content-Length headers (potential smuggling attack)
        Expected: Server should handle this safely
        """
        time.sleep(0.3)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        # Some servers might use first, some last - both are potential vulnerabilities
        request = (
            b"POST /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 10\r\n"
            b"Content-Length: 5\r\n"  # Conflicting header
            b"Connection: close\r\n"
            b"\r\n"
            b"12345678901234567890"
        )

        sock.sendall(request)
        response = sock.recv(4096).decode()
        sock.close()

        time.sleep(0.2)

        # Both behaviors are acceptable - using first or returning error
        self.assertTrue("200" in response or "400" in response)

    def test_content_length_larger_than_available(self):
        """
        Test: Content-Length says 100 but client only sends 20 bytes
        Expected: Server waits for more data or times out
        """
        time.sleep(0.3)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        # Send headers with Content-Length: 100
        request_headers = (
            b"POST /incomplete HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 100\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )

        sock.sendall(request_headers)
        # Only send 20 bytes instead of promised 100
        sock.sendall(b"Only 20 bytes here!!")

        # Set short timeout to see if server responds immediately (it shouldn't)
        sock.settimeout(1.0)
        try:
            response = sock.recv(4096)
            # If we get immediate response, server didn't wait for complete data
            if response:
                self.fail("Server responded immediately with partial data")
        except socket.timeout:
            # Expected - server waiting for complete data
            pass

        sock.close()


if __name__ == '__main__':
    unittest.main()
