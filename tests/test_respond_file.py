#!/usr/bin/env python3
"""
Tests for respond_file() with async streaming
"""
import unittest
import socket
import time
import threading
import tempfile
import os
from uhttp import server as uhttp_server


class TestRespondFile(unittest.TestCase):
    """Test suite for respond_file() method"""

    server = None
    server_thread = None
    PORT = 9990
    test_files = {}

    @classmethod
    def setUpClass(cls):
        """Start server and create test files"""
        # Create test files
        cls.temp_dir = tempfile.mkdtemp()

        # Small text file
        cls.test_files['small.txt'] = os.path.join(cls.temp_dir, 'small.txt')
        with open(cls.test_files['small.txt'], 'w') as f:
            f.write("Hello, World!")

        # Medium file (10KB)
        cls.test_files['medium.txt'] = os.path.join(cls.temp_dir, 'medium.txt')
        with open(cls.test_files['medium.txt'], 'w') as f:
            f.write("x" * 10240)

        # Large file (50KB) - tests chunking
        cls.test_files['large.bin'] = os.path.join(cls.temp_dir, 'large.bin')
        with open(cls.test_files['large.bin'], 'wb') as f:
            f.write(b'\x00\x01\x02\x03' * (50 * 1024 // 4))

        # HTML file for content-type test
        cls.test_files['test.html'] = os.path.join(cls.temp_dir, 'test.html')
        with open(cls.test_files['test.html'], 'w') as f:
            f.write("<html><body>Test</body></html>")

        # JPG file for content-type test
        cls.test_files['test.jpg'] = os.path.join(cls.temp_dir, 'test.jpg')
        with open(cls.test_files['test.jpg'], 'wb') as f:
            f.write(b'\xff\xd8\xff\xe0' + b'\x00' * 100)  # Fake JPEG header

        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)
                    if client:
                        if client.path == '/small':
                            client.respond_file(cls.test_files['small.txt'])
                        elif client.path == '/medium':
                            client.respond_file(cls.test_files['medium.txt'])
                        elif client.path == '/large':
                            client.respond_file(cls.test_files['large.bin'])
                        elif client.path == '/html':
                            client.respond_file(cls.test_files['test.html'])
                        elif client.path == '/jpg':
                            client.respond_file(cls.test_files['test.jpg'])
                        elif client.path == '/notfound':
                            client.respond_file('/nonexistent/file.txt')
                        elif client.path == '/keepalive':
                            client.respond_file(cls.test_files['small.txt'])
                        elif client.path == '/close':
                            client.respond_file(cls.test_files['small.txt'], headers={'connection': 'close'})
                        else:
                            client.respond({'error': 'unknown path'})
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """Stop server and cleanup test files"""
        if cls.server:
            cls.server.close()
            cls.server = None

        # Cleanup test files
        for file_path in cls.test_files.values():
            try:
                os.remove(file_path)
            except OSError:
                pass
        try:
            os.rmdir(cls.temp_dir)
        except OSError:
            pass

    def _recv_full_response(self, sock):
        """Helper to receive full HTTP response"""
        response = b''
        content_length = None
        body_start = None

        while True:
            try:
                chunk = sock.recv(4096)
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

        return response

    def test_small_file(self):
        """Test sending small text file"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            request = b"GET /small HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)
            response_str = response.decode()

            # Verify response
            self.assertIn("200 OK", response_str)
            self.assertIn("content-length: 13", response_str)
            self.assertIn("Hello, World!", response_str)

        finally:
            sock.close()

    def test_medium_file(self):
        """Test sending medium file (10KB)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            request = b"GET /medium HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)
            response_str = response.decode()

            # Verify response
            self.assertIn("200 OK", response_str)
            self.assertIn("content-length: 10240", response_str)

            # Extract body
            body_start = response.index(b'\r\n\r\n') + 4
            body = response[body_start:]
            self.assertEqual(len(body), 10240)
            self.assertTrue(all(c == ord('x') for c in body))

        finally:
            sock.close()

    def test_large_file(self):
        """Test sending large file (50KB) - tests chunking"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3.0)

            request = b"GET /large HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)

            # Verify response
            response_header = response.split(b'\r\n\r\n')[0].decode()
            self.assertIn("200 OK", response_header)
            self.assertIn("content-length: 51200", response_header)

            # Extract and verify body
            body_start = response.index(b'\r\n\r\n') + 4
            body = response[body_start:]
            self.assertEqual(len(body), 51200)

            # Verify content pattern
            for i in range(0, len(body), 4):
                expected = bytes([i % 256 for i in range(4)])
                self.assertEqual(body[i:i+4], b'\x00\x01\x02\x03')

        finally:
            sock.close()

    def test_file_not_found(self):
        """Test requesting non-existent file returns 404"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            request = b"GET /notfound HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)
            response_str = response.decode()

            # Verify 404 response
            self.assertIn("404 Not Found", response_str)
            self.assertIn("File not found", response_str)

        finally:
            sock.close()

    def test_content_type_html(self):
        """Test HTML file gets correct content-type"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            request = b"GET /html HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)
            response_str = response.decode()

            # Verify content-type
            self.assertIn("200 OK", response_str)
            self.assertIn("content-type: text/html; charset=utf-8", response_str.lower())

        finally:
            sock.close()

    def test_content_type_jpg(self):
        """Test JPG file gets correct content-type"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            request = b"GET /jpg HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)
            response_str = response.decode(errors='ignore')

            # Verify content-type
            self.assertIn("200 OK", response_str)
            self.assertIn("content-type: image/jpeg", response_str.lower())

        finally:
            sock.close()

    def test_keep_alive(self):
        """Test file response with keep-alive"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # First request
            request = b"GET /keepalive HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)
            response_str = response.decode()

            self.assertIn("200 OK", response_str)
            self.assertIn("connection: keep-alive", response_str.lower())
            self.assertIn("Hello, World!", response_str)

            # Second request on same connection
            time.sleep(0.1)
            request = b"GET /keepalive HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request)

            response2 = self._recv_full_response(sock)
            response2_str = response2.decode()

            self.assertIn("200 OK", response2_str)
            self.assertIn("Hello, World!", response2_str)

        finally:
            sock.close()

    def test_connection_close(self):
        """Test file response with Connection: close"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            request = b"GET /close HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request)

            response = self._recv_full_response(sock)
            response_str = response.decode()

            self.assertIn("200 OK", response_str)
            self.assertIn("connection: close", response_str.lower())

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
