#!/usr/bin/env python3
"""
Test HTTP/1.0 keep-alive behavior
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestHTTP10KeepAlive(unittest.TestCase):
    """Test suite for HTTP/1.0 keep-alive behavior"""

    server = None
    server_thread = None
    PORT = 9983

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)
                    if client:
                        client.respond({
                            'request': client._requests_count,
                            'protocol': client.protocol,
                            'path': client.path
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

    def test_http10_without_keepalive(self):
        """Test HTTP/1.0 without Connection header (should close)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            request = b"GET /test HTTP/1.0\r\nHost: localhost\r\n\r\n"
            sock.send(request)

            # Read response
            response = b''
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass

            response_str = response.decode()

            # Verify response
            self.assertIn("200 OK", response_str)

            # Check connection header - should be "close" for HTTP/1.0 without keep-alive
            conn_header = [l for l in response_str.split('\r\n')
                          if l.lower().startswith('connection:')]

            self.assertTrue(len(conn_header) > 0)
            self.assertIn('close', conn_header[0].lower())

        finally:
            sock.close()

    def test_http10_with_keepalive(self):
        """Test HTTP/1.0 with explicit Connection: keep-alive"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            responses = []

            # Send 2 requests on same connection
            for i in range(2):
                request = f"GET /test{i} HTTP/1.0\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
                sock.send(request.encode())

                # Read response
                response = b''
                content_length = None
                body_start = None

                try:
                    while True:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        response += chunk

                        if b'\r\n\r\n' in response and content_length is None:
                            headers = response.split(b'\r\n\r\n')[0].decode()
                            if 'Content-Length:' in headers:
                                content_length_line = [l for l in headers.split('\r\n')
                                                       if 'Content-Length' in l][0]
                                content_length = int(content_length_line.split(':')[1].strip())
                                body_start = response.index(b'\r\n\r\n') + 4

                        if content_length is not None and body_start is not None:
                            if len(response) >= body_start + content_length:
                                break
                except socket.timeout:
                    pass

                response_str = response.decode()
                responses.append(response_str)

                # Verify response
                self.assertIn("200 OK", response_str)
                self.assertIn(f"/test{i}", response_str)

                # Check connection header - should be "keep-alive"
                conn_header = [l for l in response_str.split('\r\n')
                              if l.lower().startswith('connection:')]

                self.assertTrue(len(conn_header) > 0)
                self.assertIn('keep-alive', conn_header[0].lower())

                time.sleep(0.2)

            # Verify both responses received
            self.assertEqual(len(responses), 2)

        finally:
            sock.close()

    def test_http10_protocol_detection(self):
        """Test that server correctly identifies HTTP/1.0"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            request = b"GET /test HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            # Read response
            response = b''
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass

            response_str = response.decode()

            # Verify protocol is HTTP/1.0
            self.assertIn("200 OK", response_str)
            self.assertIn('"protocol": "HTTP/1.0"', response_str)

        finally:
            sock.close()

    def test_http10_close_after_first_request(self):
        """Test that HTTP/1.0 without keep-alive closes after first request"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            # First request without keep-alive
            request1 = b"GET /test1 HTTP/1.0\r\nHost: localhost\r\n\r\n"
            sock.send(request1)

            # Read response
            response1 = b''
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response1 += chunk
            except socket.timeout:
                pass

            self.assertIn(b"200 OK", response1)

            # Try second request - should fail (connection closed)
            request2 = b"GET /test2 HTTP/1.0\r\nHost: localhost\r\n\r\n"

            connection_closed = False
            try:
                sock.send(request2)
                response2 = sock.recv(1024)
                # Either empty response or connection was closed
                if len(response2) == 0:
                    connection_closed = True
            except (BrokenPipeError, ConnectionResetError, OSError):
                connection_closed = True

            # Connection should be closed
            self.assertTrue(connection_closed)

        finally:
            sock.close()

    def test_http10_keepalive_multiple_requests(self):
        """Test multiple requests on HTTP/1.0 keep-alive connection"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            # Send 3 requests with keep-alive
            for i in range(3):
                request = f"GET /req{i} HTTP/1.0\r\nHost: localhost\r\nConnection: keep-alive\r\n\r\n"
                sock.send(request.encode())

                # Read response
                response = b''
                content_length = None
                body_start = None

                try:
                    while True:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        response += chunk

                        if b'\r\n\r\n' in response and content_length is None:
                            headers = response.split(b'\r\n\r\n')[0].decode()
                            if 'Content-Length:' in headers:
                                content_length_line = [l for l in headers.split('\r\n')
                                                       if 'Content-Length' in l][0]
                                content_length = int(content_length_line.split(':')[1].strip())
                                body_start = response.index(b'\r\n\r\n') + 4

                        if content_length is not None and body_start is not None:
                            if len(response) >= body_start + content_length:
                                break
                except socket.timeout:
                    pass

                response_str = response.decode()

                # Verify response
                self.assertIn("200 OK", response_str)
                self.assertIn(f"/req{i}", response_str)
                self.assertIn(f'"request": {i+1}', response_str)

                time.sleep(0.1)

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
