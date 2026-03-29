#!/usr/bin/env python3
"""
Test responding with falsy values (empty list, empty dict, 0, empty string, empty bytes)
"""
import unittest
import socket
import time
import threading
import json
from uhttp import server as uhttp_server


class TestRespondFalsy(unittest.TestCase):
    """Test that respond() correctly handles falsy but valid data values"""

    server = None
    server_thread = None
    response_data = None
    PORT = 9997

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        client.respond(cls.response_data)
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

    def get_response(self):
        """Send GET and return (headers_str, body_bytes)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(b"GET / HTTP/1.0\r\nHost: localhost\r\n\r\n")
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        sock.close()
        parts = response.split(b"\r\n\r\n", 1)
        headers = parts[0].decode()
        body = parts[1] if len(parts) > 1 else b""
        return headers, body

    def test_respond_empty_list(self):
        """respond([]) should return JSON '[]'"""
        TestRespondFalsy.response_data = []
        headers, body = self.get_response()
        self.assertIn('200', headers)
        self.assertEqual(json.loads(body), [])

    def test_respond_empty_dict(self):
        """respond({}) should return JSON '{}'"""
        TestRespondFalsy.response_data = {}
        headers, body = self.get_response()
        self.assertIn('200', headers)
        self.assertEqual(json.loads(body), {})

    def test_respond_zero(self):
        """respond(0) should return JSON '0'"""
        TestRespondFalsy.response_data = 0
        headers, body = self.get_response()
        self.assertIn('200', headers)
        self.assertEqual(json.loads(body), 0)

    def test_respond_empty_string(self):
        """respond('') should return empty body"""
        TestRespondFalsy.response_data = ''
        headers, body = self.get_response()
        self.assertIn('200', headers)
        self.assertEqual(body, b'')

    def test_respond_empty_bytes(self):
        """respond(b'') should return empty body"""
        TestRespondFalsy.response_data = b''
        headers, body = self.get_response()
        self.assertIn('200', headers)
        self.assertEqual(body, b'')

    def test_respond_none(self):
        """respond(None) should return no body"""
        TestRespondFalsy.response_data = None
        headers, body = self.get_response()
        self.assertIn('200', headers)
        self.assertEqual(body, b'')


if __name__ == '__main__':
    unittest.main()
