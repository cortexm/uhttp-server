#!/usr/bin/env python3
"""
Test persistent connections (keep-alive) functionality
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestKeepAlive(unittest.TestCase):
    """Test suite for keep-alive connections"""

    server = None
    server_thread = None
    request_count = 0
    PORT = 9981

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
                            'message': f'Response to request #{client._requests_count}',
                            'path': client.path,
                            'total_requests': cls.request_count,
                            'connection_request_number': client._requests_count
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
        TestKeepAlive.request_count = 0

    def test_multiple_requests_same_connection(self):
        """Test client making multiple requests on same connection"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            responses = []

            # Send 3 requests on same connection
            for i in range(3):
                request = f"GET /test{i} HTTP/1.1\r\nHost: localhost\r\n\r\n"
                sock.send(request.encode())

                # Receive response
                response = b""
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
                    # Check if we have complete response
                    if b"\r\n\r\n" in response:
                        header_end = response.index(b"\r\n\r\n") + 4
                        body = response[header_end:]
                        if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                            break

                response_str = response.decode()
                responses.append(response_str)

                # Verify response
                self.assertIn("200 OK", response_str)
                self.assertIn(f"/test{i}", response_str)

                # First 2 responses should keep connection alive
                if i < 2:
                    self.assertIn("keep-alive", response_str.lower())

                time.sleep(0.1)

            # Verify all 3 responses received
            self.assertEqual(len(responses), 3)

        finally:
            sock.close()

    def test_connection_reuse(self):
        """Test that connection is reused (request counter increments)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # Send first request
            request1 = b"GET /first HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request1)

            # Read first response
            response1 = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response1 += chunk
                if b"\r\n\r\n" in response1:
                    header_end = response1.index(b"\r\n\r\n") + 4
                    body = response1[header_end:]
                    if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                        break

            response1_str = response1.decode()
            self.assertIn("connection_request_number", response1_str)
            self.assertIn('"connection_request_number": 1', response1_str)

            # Send second request on same connection
            request2 = b"GET /second HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request2)

            # Read second response
            response2 = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response2 += chunk
                if b"\r\n\r\n" in response2:
                    header_end = response2.index(b"\r\n\r\n") + 4
                    body = response2[header_end:]
                    if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                        break

            response2_str = response2.decode()
            self.assertIn("connection_request_number", response2_str)
            # Should be request #2 on this connection
            self.assertIn('"connection_request_number": 2', response2_str)

        finally:
            sock.close()

    def test_http11_default_keep_alive(self):
        """Test that HTTP/1.1 keeps connection alive by default"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # HTTP/1.1 request without Connection header
            request = b"GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request)

            # Read response
            response = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk
                if b"\r\n\r\n" in response:
                    header_end = response.index(b"\r\n\r\n") + 4
                    body = response[header_end:]
                    if body and body.count(b"{") > 0 and body.count(b"{") == body.count(b"}"):
                        break

            response_str = response.decode()

            # Should have keep-alive header
            self.assertIn("200 OK", response_str)
            self.assertIn("keep-alive", response_str.lower())

        finally:
            sock.close()

    def test_explicit_close(self):
        """Test that Connection: close header closes connection"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # Request with explicit Connection: close
            request = b"GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

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

            response_str = response.decode()

            # Should have Connection: close header
            self.assertIn("200 OK", response_str)
            self.assertIn("close", response_str.lower())

        finally:
            sock.close()

    def test_pipelined_requests_not_supported(self):
        """Test that pipelined requests are not supported - only first processed"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # Send 2 pipelined requests
            pipelined = (
                b"GET /pipe1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
                b"GET /pipe2 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            )
            sock.sendall(pipelined)

            # Read response
            all_data = b""
            try:
                while len(all_data) < 8192:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    all_data += chunk
            except socket.timeout:
                pass

            all_str = all_data.decode()

            # Only first request processed, connection closed
            self.assertIn("/pipe1", all_str)
            self.assertNotIn("/pipe2", all_str)
            self.assertEqual(all_str.count("200 OK"), 1)

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
