#!/usr/bin/env python3
"""
Simple test for persistent connections
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestKeepAliveSimple(unittest.TestCase):
    """Simple test suite for keep-alive connections"""

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
                            'message': f'Response #{client._requests_count}',
                            'path': client.path,
                            'request_number': client._requests_count,
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
        TestKeepAliveSimple.request_count = 0

    def test_keep_alive_http11(self):
        """Test keep-alive with HTTP/1.1 (3 sequential requests)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(0.5)

            responses = []

            for i in range(3):
                request = f"GET /test{i} HTTP/1.1\r\nHost: localhost\r\n\r\n"
                sock.send(request.encode())

                # Receive response with proper Content-Length handling
                response = b''
                content_length = None
                body_start = None

                while True:
                    try:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        response += chunk

                        if b'\r\n\r\n' in response and content_length is None:
                            # Parse headers to get Content-Length
                            headers = response.split(b'\r\n\r\n')[0].decode()
                            if 'Content-Length:' in headers:
                                content_length_line = [l for l in headers.split('\r\n')
                                                       if 'Content-Length' in l][0]
                                content_length = int(content_length_line.split(':')[1].strip())
                                body_start = response.index(b'\r\n\r\n') + 4

                        # Check if we have complete response
                        if content_length is not None and body_start is not None:
                            if len(response) >= body_start + content_length:
                                break
                    except socket.timeout:
                        break

                response_str = response.decode()
                responses.append(response_str)

                # Verify response
                self.assertIn("200 OK", response_str)
                self.assertIn(f"/test{i}", response_str)
                self.assertIn(f'"request_number": {i+1}', response_str)

                # Check for keep-alive header (except possibly the last one)
                if i < 2:
                    self.assertIn("keep-alive", response_str.lower())

                time.sleep(0.2)

            # Verify all 3 responses received
            self.assertEqual(len(responses), 3)

        finally:
            sock.close()

    def test_connection_header_keep_alive(self):
        """Test that Connection: keep-alive header is present in responses"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(0.5)

            request = b"GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request)

            # Receive response
            response = b''
            content_length = None
            body_start = None

            while True:
                try:
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
                    break

            response_str = response.decode()

            # Verify Connection: keep-alive header is present
            self.assertIn("200 OK", response_str)
            connection_header = [l for l in response_str.split('\r\n')
                                 if l.lower().startswith('connection:')]
            self.assertTrue(len(connection_header) > 0)
            self.assertIn('keep-alive', connection_header[0].lower())

        finally:
            sock.close()

    def test_protocol_version(self):
        """Test that server correctly identifies HTTP/1.1 protocol"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(0.5)

            request = b"GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            # Receive response
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

            # Verify protocol is HTTP/1.1
            self.assertIn("200 OK", response_str)
            self.assertIn('"protocol": "HTTP/1.1"', response_str)

        finally:
            sock.close()

    def test_multiple_requests_increment_counter(self):
        """Test that request_number increments for multiple requests on same connection"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(0.5)

            request_numbers = []

            for i in range(3):
                request = f"GET /req{i} HTTP/1.1\r\nHost: localhost\r\n\r\n"
                sock.send(request.encode())

                # Receive response
                response = b''
                content_length = None
                body_start = None

                while True:
                    try:
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
                        break

                response_str = response.decode()

                # Extract request_number from JSON response
                import json
                body_start = response_str.index('\r\n\r\n') + 4
                body = response_str[body_start:]
                data = json.loads(body)
                request_numbers.append(data['request_number'])

                time.sleep(0.1)

            # Verify request numbers increment: [1, 2, 3]
            self.assertEqual(request_numbers, [1, 2, 3])

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
