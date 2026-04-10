#!/usr/bin/env python3
"""Test trusted_proxies configuration for X-Forwarded-For handling"""
import unittest
import socket
import time
import threading
import json
from uhttp import server as uhttp_server


class TestTrustedProxiesDisabled(unittest.TestCase):
    """Test default behavior — X-Forwarded-For is ignored"""

    server = None
    server_thread = None
    last_request = None
    PORT = 9960

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        cls.last_request = {
                            'remote_address': client.remote_address,
                            'remote_addresses': client.remote_addresses,
                            'socket_address': client.socket_address,
                        }
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

    def send_request(self, request_bytes):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(request_bytes)
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                break
        sock.close()
        return response

    def test_forwarded_for_ignored_by_default(self):
        """X-Forwarded-For should be ignored when trusted_proxies not set"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '127.0.0.1:' + self.last_request['remote_address'].split(':')[1])
        self.assertNotIn('10.0.0.1', self.last_request['remote_address'])

    def test_socket_address_always_returns_socket_ip(self):
        """socket_address should always return socket IP"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertTrue(self.last_request['socket_address'].startswith('127.0.0.1:'))

    def test_remote_addresses_ignored_by_default(self):
        """remote_addresses should ignore X-Forwarded-For without trusted_proxies"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1, 10.0.0.2\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertNotIn('10.0.0.1', self.last_request['remote_addresses'])


class TestTrustedProxiesEnabled(unittest.TestCase):
    """Test with trusted_proxies=['127.0.0.1'] — localhost is trusted"""

    server = None
    server_thread = None
    last_request = None
    PORT = 9961

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, trusted_proxies=['127.0.0.1'])

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        cls.last_request = {
                            'remote_address': client.remote_address,
                            'remote_addresses': client.remote_addresses,
                            'socket_address': client.socket_address,
                        }
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

    def send_request(self, request_bytes):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(request_bytes)
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                break
        sock.close()
        return response

    def test_forwarded_for_used_when_trusted(self):
        """X-Forwarded-For should be used when connection is from trusted proxy"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.50')

    def test_forwarded_for_first_ip(self):
        """Should return first IP from X-Forwarded-For chain"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50, 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.50')

    def test_remote_addresses_returns_full_chain(self):
        """remote_addresses should return full X-Forwarded-For value"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50, 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(
            self.last_request['remote_addresses'], '203.0.113.50, 10.0.0.1')

    def test_no_forwarded_header_falls_back_to_socket(self):
        """Without X-Forwarded-For, should return socket address"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertTrue(self.last_request['remote_address'].startswith('127.0.0.1:'))

    def test_socket_address_unaffected_by_trusted_proxies(self):
        """socket_address should always return socket IP regardless of config"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertTrue(self.last_request['socket_address'].startswith('127.0.0.1:'))


class TestTrustedProxiesUntrustedSource(unittest.TestCase):
    """Test that X-Forwarded-For is ignored from non-trusted source"""

    server = None
    server_thread = None
    last_request = None
    PORT = 9962

    @classmethod
    def setUpClass(cls):
        # Trust only 10.0.0.1 — localhost is NOT trusted
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, trusted_proxies=['10.0.0.1'])

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        cls.last_request = {
                            'remote_address': client.remote_address,
                            'remote_addresses': client.remote_addresses,
                            'socket_address': client.socket_address,
                        }
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

    def send_request(self, request_bytes):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(request_bytes)
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                break
        sock.close()
        return response

    def test_forwarded_for_ignored_from_untrusted_source(self):
        """X-Forwarded-For from untrusted source (127.0.0.1) should be ignored"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertTrue(self.last_request['remote_address'].startswith('127.0.0.1:'))
        self.assertNotIn('203.0.113.50', self.last_request['remote_address'])


if __name__ == '__main__':
    unittest.main()
