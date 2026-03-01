#!/usr/bin/env python3
"""
Test keep-alive limits (max requests and timeout)
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestKeepAliveMaxRequests(unittest.TestCase):
    """Test suite for keep-alive max requests limit"""

    server = None
    server_thread = None
    PORT = 9984

    @classmethod
    def setUpClass(cls):
        """Start server with max_requests=3"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT, keep_alive_max_requests=3)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)
                    if client:
                        client.respond({'request': client._requests_count})
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

    def test_max_requests_limit(self):
        """Test that connection closes after max requests (3)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            responses = []

            for i in range(4):
                try:
                    request = f"GET /test{i} HTTP/1.1\r\nHost: localhost\r\n\r\n"
                    sock.send(request.encode())

                    response = b''
                    content_length = None
                    body_start = None

                    while True:
                        try:
                            chunk = sock.recv(1024)
                            if not chunk:
                                # Connection closed
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

                    if response:
                        response_str = response.decode()
                        responses.append(response_str)

                        # Check connection header
                        conn_header = [l for l in response_str.split('\r\n')
                                       if l.lower().startswith('connection:')]

                        if i < 2:  # First 2 requests should keep-alive
                            self.assertIn("200 OK", response_str)
                            self.assertTrue(len(conn_header) > 0)
                            self.assertIn('keep-alive', conn_header[0].lower())
                        elif i == 2:  # 3rd request should close
                            self.assertIn("200 OK", response_str)
                            self.assertTrue(len(conn_header) > 0)
                            self.assertIn('close', conn_header[0].lower())
                        else:  # 4th request should fail
                            # Connection should be closed already
                            pass

                    time.sleep(0.1)

                except (BrokenPipeError, ConnectionResetError):
                    # Expected after max_requests reached
                    if i == 3:  # 4th request should fail
                        break
                    else:
                        raise

            # Should have received 3 successful responses
            self.assertEqual(len(responses), 3)

        finally:
            sock.close()

    def test_max_requests_enforced(self):
        """Test that 4th request on same connection fails"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            # Send 3 requests successfully
            for i in range(3):
                request = f"GET /req{i} HTTP/1.1\r\nHost: localhost\r\n\r\n"
                sock.send(request.encode())

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

                self.assertIn(b"200 OK", response)
                time.sleep(0.1)

            # 4th request should fail (connection closed)
            request4 = b"GET /req4 HTTP/1.1\r\nHost: localhost\r\n\r\n"

            try:
                sock.send(request4)
                response4 = sock.recv(1024)
                # Either empty response or connection was closed
                self.assertEqual(len(response4), 0)
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Expected - connection was closed
                pass

        finally:
            sock.close()


class TestKeepAliveTimeout(unittest.TestCase):
    """Test suite for keep-alive timeout"""

    server = None
    server_thread = None
    PORT = 9985

    @classmethod
    def setUpClass(cls):
        """Start server with timeout=2s"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT, keep_alive_timeout=2)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)
                    if client:
                        client.respond({'message': 'ok'})
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

    def test_idle_timeout(self):
        """Test that idle connections timeout after 2s"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3.0)

            # First request
            request = b"GET /test1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request)

            response = b''
            content_length = None
            body_start = None

            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response += chunk

                if b'\r\n\r\n' in response and content_length is None:
                    headers = response.split(b'\r\n\r\n')[0].decode()
                    headers_lower = headers.lower()
                    if 'content-length:' in headers_lower:
                        content_length_line = [l for l in headers.split('\r\n')
                                               if 'content-length' in l.lower()][0]
                        content_length = int(content_length_line.split(':')[1].strip())
                        body_start = response.index(b'\r\n\r\n') + 4

                if content_length is not None and body_start is not None:
                    if len(response) >= body_start + content_length:
                        break

            self.assertIn(b"200 OK", response)

            # Wait for timeout (2s) + margin
            time.sleep(2.5)

            # Trigger a read event with a new connection to cause cleanup
            # (idle connection cleanup happens at the end of event_read)
            trigger_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            trigger_sock.connect(('localhost', self.PORT))
            trigger_sock.send(b"GET /trigger HTTP/1.1\r\nHost: localhost\r\n\r\n")
            trigger_sock.recv(1024)
            trigger_sock.close()

            # Try second request - should fail or get 408
            request2 = b"GET /test2 HTTP/1.1\r\nHost: localhost\r\n\r\n"

            connection_closed = False
            received_408 = False

            try:
                sock.send(request2)
                response2 = sock.recv(1024)

                if response2:
                    if b'408' in response2:
                        received_408 = True
                else:
                    connection_closed = True
            except (BrokenPipeError, ConnectionResetError, OSError):
                connection_closed = True

            # Either connection closed or received 408
            self.assertTrue(connection_closed or received_408)

        finally:
            sock.close()

    def test_keep_alive_before_timeout(self):
        """Test that connection stays alive if requests come before timeout"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(1.0)

            # Send 3 requests, each within the timeout window (2s)
            for i in range(3):
                request = f"GET /test{i} HTTP/1.1\r\nHost: localhost\r\n\r\n"
                sock.send(request.encode())

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

                self.assertIn(b"200 OK", response)

                # Wait 1s between requests (less than 2s timeout)
                time.sleep(1.0)

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
