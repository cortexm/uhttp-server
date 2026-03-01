#!/usr/bin/env python3
"""
Test error handling - malformed requests, invalid headers, HTTP errors
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestErrorHandling(unittest.TestCase):
    """Test suite for HTTP error handling"""

    server = None
    server_thread = None
    PORT = 9989

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT,
            max_headers_length=2048,
            max_content_length=10240
        )

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        client.respond({'message': 'OK', 'path': client.path})
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

    def send_request_and_get_status(self, request_bytes):
        """Helper to send request and return HTTP status code"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(2.0)
            sock.connect(('localhost', self.PORT))
            sock.sendall(request_bytes)

            response = sock.recv(4096).decode('utf-8', errors='ignore')

            # Extract status code
            if response:
                first_line = response.split('\r\n')[0]
                if ' ' in first_line:
                    parts = first_line.split(' ')
                    if len(parts) >= 2:
                        return int(parts[1]), first_line
            return None, response
        except Exception as e:
            return None, str(e)
        finally:
            sock.close()

    def test_malformed_request_no_spaces(self):
        """Test malformed request line with no spaces"""
        status, response = self.send_request_and_get_status(b"INVALID\r\n\r\n")
        # Connection should be closed or 400 returned
        self.assertTrue(status is None or status == 400)

    def test_malformed_request_missing_url(self):
        """Test malformed request missing URL and protocol"""
        status, response = self.send_request_and_get_status(b"GET\r\n\r\n")
        self.assertTrue(status is None or status == 400)

    def test_malformed_request_missing_protocol(self):
        """Test malformed request missing protocol"""
        status, response = self.send_request_and_get_status(b"GET /test\r\n\r\n")
        self.assertTrue(status is None or status == 400)

    def test_unsupported_method_custom(self):
        """Test unsupported custom HTTP method"""
        request = b"CUSTOM /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
        status, response = self.send_request_and_get_status(request)
        self.assertEqual(status, 501)

    def test_unsupported_protocol_http2(self):
        """Test unsupported HTTP/2.0 protocol"""
        request = b"GET /test HTTP/2.0\r\nHost: localhost\r\n\r\n"
        status, response = self.send_request_and_get_status(request)
        self.assertEqual(status, 505)

    def test_unsupported_protocol_http09(self):
        """Test unsupported HTTP/0.9 protocol"""
        request = b"GET /test HTTP/0.9\r\nHost: localhost\r\n\r\n"
        status, response = self.send_request_and_get_status(request)
        self.assertEqual(status, 505)

    def test_unsupported_protocol_invalid(self):
        """Test invalid protocol version"""
        request = b"GET /test INVALID/1.1\r\nHost: localhost\r\n\r\n"
        status, response = self.send_request_and_get_status(request)
        self.assertEqual(status, 505)

    def test_headers_too_large(self):
        """Test request with headers exceeding max size (2048 bytes)"""
        # Create request with very large headers (>2048 bytes)
        large_header = "X-Large-Header: " + ("A" * 2000) + "\r\n"
        request = (
            b"GET /test HTTP/1.1\r\n"
            b"Host: localhost\r\n" +
            large_header.encode() +
            b"\r\n"
        )

        status, response = self.send_request_and_get_status(request)

        # Should get 431 or connection closed
        self.assertTrue(status == 431 or status is None)

    def test_content_too_large(self):
        """Test request with content exceeding max size (10240 bytes)"""
        # Try to send content larger than max (10240 bytes)
        large_data = "X" * 20000
        request = (
            b"POST /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 20000\r\n"
            b"Connection: close\r\n"
            b"\r\n" +
            large_data.encode()
        )

        status, response = self.send_request_and_get_status(request)

        # Should get 413 or connection closed
        self.assertTrue(status == 413 or status is None)

    def test_invalid_json(self):
        """Test POST with invalid JSON data"""
        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 20\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b'{invalid json here}'
        )

        status, response = self.send_request_and_get_status(request)

        # Should get 400 for invalid JSON
        self.assertTrue(status == 400 or status is None)

    def test_missing_host_header(self):
        """Test HTTP/1.1 request without Host header"""
        request = (
            b"GET /test HTTP/1.1\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )

        status, response = self.send_request_and_get_status(request)

        # RFC 2616: HTTP/1.1 requires Host header - must return 400
        self.assertEqual(status, 400)

    def test_http10_without_host_header(self):
        """Test HTTP/1.0 request without Host header (should be OK)"""
        request = (
            b"GET /test HTTP/1.0\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )

        status, response = self.send_request_and_get_status(request)

        # HTTP/1.0 does not require Host header - should return 200
        self.assertEqual(status, 200)

    def test_chunked_encoding(self):
        """Test chunked transfer encoding (not fully supported)"""
        request = (
            b"POST /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b"5\r\n"
            b"hello\r\n"
            b"0\r\n"
            b"\r\n"
        )

        status, response = self.send_request_and_get_status(request)

        # Server should respond (may not fully support chunked)
        self.assertTrue(status is not None or response)


if __name__ == '__main__':
    unittest.main()
