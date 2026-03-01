#!/usr/bin/env python3
"""
Tests for SSL/TLS support
"""
import unittest
import socket
import ssl
import time
import threading
import json
import subprocess
import os
from uhttp import server as uhttp_server


def ensure_test_certificates():
    """Create self-signed test certificates if they don't exist"""
    cert_file = 'cert.pem'
    key_file = 'key.pem'

    if os.path.exists(cert_file) and os.path.exists(key_file):
        return  # Certificates already exist

    print("\nGenerating test SSL certificates...")
    try:
        subprocess.run([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048',
            '-keyout', key_file, '-out', cert_file,
            '-days', '365', '-nodes',
            '-subj', '/CN=localhost'
        ], check=True, capture_output=True)
        print(f"Created {cert_file} and {key_file}")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to generate SSL certificates: {e}\n"
            f"Please install OpenSSL or create certificates manually:\n"
            f"  openssl req -x509 -newkey rsa:2048 -keyout key.pem "
            f"-out cert.pem -days 365 -nodes -subj '/CN=localhost'"
        ) from e
    except FileNotFoundError:
        raise RuntimeError(
            "OpenSSL not found. Please install OpenSSL or create certificates manually:\n"
            "  openssl req -x509 -newkey rsa:2048 -keyout key.pem "
            "-out cert.pem -days 365 -nodes -subj '/CN=localhost'"
        )


class TestSSL(unittest.TestCase):
    """Test suite for SSL/HTTPS connections"""

    server = None
    server_thread = None
    PORT = 9985

    @classmethod
    def setUpClass(cls):
        """Start HTTPS server once for all tests"""
        # Ensure test certificates exist
        ensure_test_certificates()

        # Create SSL context
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')

        cls.server = uhttp_server.HttpServer(
            port=cls.PORT,
            ssl_context=context,
            keep_alive_timeout=30
        )

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)
                    if client:
                        client.respond({
                            'message': 'Hello from HTTPS',
                            'secure': client.is_secure,
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

    def _create_ssl_socket(self):
        """Helper: create SSL socket connected to test server"""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssl_sock = context.wrap_socket(sock, server_hostname='localhost')
        ssl_sock.connect(('localhost', self.PORT))
        return ssl_sock

    def test_ssl_get_request(self):
        """Test basic SSL GET request"""
        ssl_sock = self._create_ssl_socket()
        try:
            # Send GET request
            request = b'GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
            ssl_sock.sendall(request)

            # Read full response
            response = b''
            while True:
                chunk = ssl_sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            response = response.decode()

            # Check response
            self.assertIn('HTTP/1.1 200 OK', response)
            self.assertIn('application/json', response)

            # Parse JSON body
            body_start = response.find('\r\n\r\n') + 4
            body = json.loads(response[body_start:])

            self.assertEqual(body['secure'], True)
            self.assertEqual(body['path'], '/test')
            self.assertEqual(body['method'], 'GET')
        finally:
            ssl_sock.close()

    def test_ssl_post_request(self):
        """Test SSL POST request with JSON data"""
        ssl_sock = self._create_ssl_socket()
        try:
            # Send POST request
            data = json.dumps({'test': 'data', 'number': 123})
            request = (
                f'POST /api/data HTTP/1.1\r\n'
                f'Host: localhost\r\n'
                f'Content-Type: application/json\r\n'
                f'Content-Length: {len(data)}\r\n'
                f'Connection: close\r\n'
                f'\r\n'
                f'{data}'
            ).encode()
            ssl_sock.sendall(request)

            # Read full response
            response = b''
            while True:
                chunk = ssl_sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            response = response.decode()

            # Check response
            self.assertIn('HTTP/1.1 200 OK', response)

            # Parse JSON body
            body_start = response.find('\r\n\r\n') + 4
            body = json.loads(response[body_start:])

            self.assertEqual(body['secure'], True)
            self.assertEqual(body['path'], '/api/data')
            self.assertEqual(body['method'], 'POST')
        finally:
            ssl_sock.close()

    def test_ssl_keep_alive(self):
        """Test SSL connection with keep-alive (multiple requests)"""
        ssl_sock = self._create_ssl_socket()
        try:
            # First request
            request1 = b'GET /first HTTP/1.1\r\nHost: localhost\r\n\r\n'
            ssl_sock.sendall(request1)

            # Read first response (need to parse Content-Length to know when to stop)
            response1 = b''
            while b'\r\n\r\n' not in response1:
                response1 += ssl_sock.recv(1)

            headers1 = response1.decode()
            content_length1 = 0
            for line in headers1.split('\r\n'):
                if line.lower().startswith('content-length:'):
                    content_length1 = int(line.split(':')[1].strip())

            body1_bytes = ssl_sock.recv(content_length1)
            full_response1 = response1.decode() + body1_bytes.decode()

            self.assertIn('HTTP/1.1 200 OK', full_response1)
            self.assertIn('keep-alive', full_response1.lower())

            # Second request on same connection
            request2 = b'GET /second HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
            ssl_sock.sendall(request2)

            # Read second response completely
            response2 = b''
            while True:
                chunk = ssl_sock.recv(4096)
                if not chunk:
                    break
                response2 += chunk
            full_response2 = response2.decode()

            self.assertIn('HTTP/1.1 200 OK', full_response2)

            # Parse bodies
            body1 = json.loads(body1_bytes.decode())
            body2 = json.loads(full_response2[full_response2.find('\r\n\r\n')+4:])

            self.assertEqual(body1['path'], '/first')
            self.assertEqual(body2['path'], '/second')
            self.assertTrue(body1['secure'])
            self.assertTrue(body2['secure'])
        finally:
            ssl_sock.close()

    def test_is_secure_property(self):
        """Test that is_secure property returns True for SSL connections"""
        ssl_sock = self._create_ssl_socket()
        try:
            request = b'GET /secure-check HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
            ssl_sock.sendall(request)

            # Read full response
            response = b''
            while True:
                chunk = ssl_sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            response = response.decode()

            body = json.loads(response[response.find('\r\n\r\n')+4:])
            self.assertTrue(body['secure'])
        finally:
            ssl_sock.close()


class TestHTTPtoHTTPSRedirect(unittest.TestCase):
    """Test HTTP to HTTPS redirect pattern"""

    http_server = None
    https_server = None
    server_thread = None
    HTTP_PORT = 9986
    HTTPS_PORT = 9987

    @classmethod
    def setUpClass(cls):
        """Start both HTTP and HTTPS servers"""
        # Ensure test certificates exist
        ensure_test_certificates()

        # Create SSL context
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')

        # HTTP server (for redirects)
        cls.http_server = uhttp_server.HttpServer(port=cls.HTTP_PORT)

        # HTTPS server (for actual content)
        cls.https_server = uhttp_server.HttpServer(
            port=cls.HTTPS_PORT,
            ssl_context=context
        )

        def run_servers():
            import select
            try:
                while cls.http_server and cls.https_server:
                    read_sockets = (cls.http_server.read_sockets +
                                    cls.https_server.read_sockets)
                    write_sockets = (cls.http_server.write_sockets +
                                     cls.https_server.write_sockets)

                    r, w, x = select.select(read_sockets, write_sockets, [], 0.5)

                    if w:
                        cls.http_server.event_write(w)
                        cls.https_server.event_write(w)

                    if r:
                        # HTTP server - redirect to HTTPS
                        http_client = cls.http_server.event_read(r)
                        if http_client:
                            https_url = (f"https://localhost:{cls.HTTPS_PORT}"
                                         f"{http_client.url}")
                            http_client.respond_redirect(https_url)

                        # HTTPS server - serve content
                        https_client = cls.https_server.event_read(r)
                        if https_client:
                            https_client.respond({
                                'message': 'Secure content',
                                'secure': https_client.is_secure
                            })
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_servers, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """Stop both servers"""
        if cls.http_server:
            cls.http_server.close()
            cls.http_server = None
        if cls.https_server:
            cls.https_server.close()
            cls.https_server = None

    def test_http_redirect_to_https(self):
        """Test that HTTP server redirects to HTTPS"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.HTTP_PORT))
            request = b'GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n'
            sock.sendall(request)

            response = sock.recv(4096).decode()

            # Check redirect response (respond_redirect uses 302 by default)
            self.assertIn('HTTP/1.1 302', response)
            self.assertIn(f'https://localhost:{self.HTTPS_PORT}/test', response)
        finally:
            sock.close()

    def test_https_serves_content(self):
        """Test that HTTPS server serves actual content"""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssl_sock = context.wrap_socket(sock, server_hostname='localhost')
        try:
            ssl_sock.connect(('localhost', self.HTTPS_PORT))
            request = b'GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
            ssl_sock.sendall(request)

            # Read full response
            response = b''
            while True:
                chunk = ssl_sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            response = response.decode()

            body = json.loads(response[response.find('\r\n\r\n')+4:])

            self.assertEqual(body['message'], 'Secure content')
            self.assertTrue(body['secure'])
        finally:
            ssl_sock.close()


if __name__ == '__main__':
    unittest.main()
