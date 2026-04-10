#!/usr/bin/env python3
"""Test that error responses don't reflect user input as HTML (XSS prevention)"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestErrorReflection(unittest.TestCase):
    """Error responses must use text/plain and not reflect raw user input"""

    server = None
    server_thread = None
    PORT = 9965

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        client.respond({'status': 'ok'})
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

    def get_response(self, request_bytes):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(request_bytes)
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
        return response

    def test_error_response_is_text_plain(self):
        """Error responses must use text/plain, not text/html"""
        response = self.get_response(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: abc\r\n"
            b"\r\n")
        self.assertIn(b'text/plain', response)
        self.assertNotIn(b'text/html', response)

    def test_script_not_reflected_in_header_error(self):
        """Script tags in malformed headers must not be reflected as HTML"""
        response = self.get_response(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: <script>alert(1)</script>\r\n"
            b"\r\n")
        self.assertIn(b'text/plain', response)

    def test_json_error_no_parser_details(self):
        """JSON parse errors should not leak parser internals"""
        response = self.get_response(
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 3\r\n"
            b"\r\n"
            b"{x}")
        # Should not contain Python-specific error details
        self.assertNotIn(b'column', response)
        self.assertNotIn(b'line 1', response)
        self.assertNotIn(b'char ', response)


if __name__ == '__main__':
    unittest.main()
