#!/usr/bin/env python3
"""Test CRLF injection prevention in response headers and cookies"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestCrlfInjection(unittest.TestCase):
    """Test that CRLF in response headers/cookies is rejected"""

    server = None
    server_thread = None
    last_error = None
    PORT = 9963

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        cls.last_error = None
                        try:
                            path = client.path
                            if path == '/header-crlf':
                                client.respond(
                                    'ok',
                                    headers={'X-User': 'a\r\nInjected: true'})
                            elif path == '/header-lf':
                                client.respond(
                                    'ok',
                                    headers={'X-User': 'a\nInjected: true'})
                            elif path == '/header-cr':
                                client.respond(
                                    'ok',
                                    headers={'X-User': 'a\rInjected: true'})
                            elif path == '/cookie-crlf':
                                client.respond(
                                    'ok',
                                    cookies={'session': 'a\r\nInjected: true'})
                            elif path == '/cookie-key-crlf':
                                client.respond(
                                    'ok',
                                    cookies={'bad\r\nkey': 'value'})
                            elif path == '/redirect-crlf':
                                client.respond_redirect(
                                    'http://evil.com\r\nInjected: true')
                            elif path == '/clean':
                                client.respond(
                                    'ok',
                                    headers={'X-Safe': 'normal-value'},
                                    cookies={'session': 'abc123'})
                            else:
                                client.respond('ok')
                        except Exception as e:
                            cls.last_error = e
                            try:
                                client.respond('error', status=500)
                            except Exception:
                                pass
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def get_response(self, path):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode())
        response = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        except (TimeoutError, OSError):
            pass
        sock.close()
        return response.decode('utf-8', errors='replace')

    def test_header_value_with_crlf_rejected(self):
        """Header value containing CRLF must be rejected"""
        response = self.get_response('/header-crlf')
        self.assertNotIn('Injected', response)

    def test_header_value_with_lf_rejected(self):
        """Header value containing bare LF must be rejected"""
        response = self.get_response('/header-lf')
        self.assertNotIn('Injected', response)

    def test_header_value_with_cr_rejected(self):
        """Header value containing bare CR must be rejected"""
        response = self.get_response('/header-cr')
        self.assertNotIn('Injected', response)

    def test_cookie_value_with_crlf_rejected(self):
        """Cookie value containing CRLF must be rejected"""
        response = self.get_response('/cookie-crlf')
        self.assertNotIn('Injected', response)

    def test_cookie_key_with_crlf_rejected(self):
        """Cookie key containing CRLF must be rejected"""
        response = self.get_response('/cookie-key-crlf')
        self.assertNotIn('Injected', response)

    def test_redirect_url_with_crlf_rejected(self):
        """Redirect URL containing CRLF must be rejected"""
        response = self.get_response('/redirect-crlf')
        self.assertNotIn('Injected', response)

    def test_clean_headers_and_cookies_work(self):
        """Normal headers and cookies without CRLF must work fine"""
        response = self.get_response('/clean')
        self.assertIn('200', response)
        self.assertIn('X-Safe: normal-value', response)
        self.assertIn('set-cookie: session=abc123', response)


if __name__ == '__main__':
    unittest.main()
