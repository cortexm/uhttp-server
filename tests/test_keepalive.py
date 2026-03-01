#!/usr/bin/env python3
"""
Test keep-alive connection functionality
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestServerKeepAlive(unittest.TestCase):
    """Test suite for keep-alive connections"""

    server = None
    server_thread = None
    request_count = 0
    PORT = 9986

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT, keep_alive_max_requests=5)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)

                    if client:
                        cls.request_count += 1
                        client.respond({
                            'request_number': client._requests_count,
                            'path': client.path,
                            'total': cls.request_count,
                            'protocol': client.protocol
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
        """Reset before each test"""
        TestServerKeepAlive.request_count = 0

    def test_http11_keep_alive_default(self):
        """Test HTTP/1.1 keep-alive (default behavior)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # HTTP/1.1 should keep connection alive by default
            request = (
                b"GET /test1 HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"\r\n"
            )

            sock.sendall(request)

            # Read first response
            response = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
                # Check if we have complete response (headers + body)
                if b"\r\n\r\n" in response:
                    header_end = response.index(b"\r\n\r\n") + 4
                    # Check if JSON body is complete
                    body = response[header_end:]
                    if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                        break

            response_str = response.decode('utf-8', errors='ignore')

            self.assertIn("200 OK", response_str)
            self.assertIn("keep-alive", response_str.lower())
            self.assertIn("/test1", response_str)

            # Send second request on same connection
            request2 = (
                b"GET /test2 HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )

            sock.sendall(request2)

            # Read second response
            response2 = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response2 += chunk
                # Check if we have complete response (headers + body)
                if b"\r\n\r\n" in response2:
                    header_end = response2.index(b"\r\n\r\n") + 4
                    # Check if JSON body is complete
                    body = response2[header_end:]
                    if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                        break

            response2_str = response2.decode('utf-8', errors='ignore')

            self.assertIn("200 OK", response2_str)
            self.assertIn("/test2", response2_str)

        finally:
            sock.close()

    def test_http10_with_keep_alive(self):
        """Test HTTP/1.0 with explicit Connection: keep-alive header"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # HTTP/1.0 with keep-alive header
            request = (
                b"GET /test HTTP/1.0\r\n"
                b"Host: localhost\r\n"
                b"Connection: keep-alive\r\n"
                b"\r\n"
            )

            sock.sendall(request)

            # Read response
            response = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
                # Check if we have complete response (headers + body)
                if b"\r\n\r\n" in response:
                    header_end = response.index(b"\r\n\r\n") + 4
                    # Check if JSON body is complete
                    body = response[header_end:]
                    if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                        break

            response_str = response.decode('utf-8', errors='ignore')

            self.assertIn("200 OK", response_str)
            self.assertIn("keep-alive", response_str.lower())

        finally:
            sock.close()

    def test_http10_without_keep_alive(self):
        """Test HTTP/1.0 without keep-alive (connection closes)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # HTTP/1.0 without keep-alive header
            request = (
                b"GET /test HTTP/1.0\r\n"
                b"Host: localhost\r\n"
                b"\r\n"
            )

            sock.sendall(request)

            # Read response - connection should close
            response = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass

            response_str = response.decode('utf-8', errors='ignore')

            self.assertIn("200 OK", response_str)
            # Should have "Connection: close" or no keep-alive
            self.assertTrue("close" in response_str.lower() or "keep-alive" not in response_str.lower())

        finally:
            sock.close()

    def test_multiple_requests_on_connection(self):
        """Test multiple requests on same keep-alive connection"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            responses = []

            # Send 3 requests on same connection
            for i in range(1, 4):
                request = (
                    f"GET /test{i} HTTP/1.1\r\n"
                    f"Host: localhost\r\n"
                    f"\r\n"
                ).encode()

                sock.sendall(request)

                # Read response
                response = b""
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                    # Check if we have complete response (headers + body)
                    if b"\r\n\r\n" in response:
                        header_end = response.index(b"\r\n\r\n") + 4
                        # Check if JSON body is complete
                        body = response[header_end:]
                        if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                            break

                response_str = response.decode('utf-8', errors='ignore')
                responses.append(response_str)

                self.assertIn("200 OK", response_str)
                self.assertIn(f"/test{i}", response_str)

            # Verify all responses received
            self.assertEqual(len(responses), 3)

        finally:
            sock.close()

    def test_max_requests_limit(self):
        """Test that connection closes after max_requests (5)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # Send max_requests (5) requests
            for i in range(1, 6):
                request = (
                    f"GET /request{i} HTTP/1.1\r\n"
                    f"Host: localhost\r\n"
                    f"\r\n"
                ).encode()

                sock.sendall(request)

                # Read response
                response = b""
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                    # Check if we have complete response (headers + body)
                    if b"\r\n\r\n" in response:
                        header_end = response.index(b"\r\n\r\n") + 4
                        # Check if JSON body is complete
                        body = response[header_end:]
                        if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                            break

                response_str = response.decode('utf-8', errors='ignore')

                self.assertIn("200 OK", response_str)
                self.assertIn(f"/request{i}", response_str)

                # 5th response should have Connection: close
                if i == 5:
                    self.assertIn("close", response_str.lower())

            # Try 6th request - should fail or start new connection
            request6 = (
                b"GET /request6 HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"\r\n"
            )

            try:
                sock.sendall(request6)
                response6 = sock.recv(1024)
                # If we get response, connection was closed and this is new connection
                # or server accepted one more request
                self.assertTrue(len(response6) == 0 or b"200 OK" in response6)
            except (BrokenPipeError, ConnectionResetError):
                # Expected - connection was closed after 5 requests
                pass

        finally:
            sock.close()

    def test_connection_close_header(self):
        """Test explicit Connection: close header closes connection"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            request = (
                b"GET /test HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )

            sock.sendall(request)

            # Read response
            response = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass

            response_str = response.decode('utf-8', errors='ignore')

            self.assertIn("200 OK", response_str)
            self.assertIn("close", response_str.lower())

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
