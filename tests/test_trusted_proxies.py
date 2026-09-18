#!/usr/bin/env python3
"""Test trusted_proxies configuration for X-Forwarded-For handling"""
import unittest
import socket
import time
import threading
import json
from uhttp import server as uhttp_server
from tests.testutils import wait_until_listening


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
        wait_until_listening(cls.PORT)

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
        self.assertEqual(self.last_request['remote_address'], '127.0.0.1')

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
        self.assertEqual(self.last_request['remote_addresses'], ['127.0.0.1'])


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
        wait_until_listening(cls.PORT)

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

    def test_untrusted_hop_wins_over_spoofed_prefix(self):
        """Client-written prefix must not shadow the hop a proxy appended

        With $proxy_add_x_forwarded_for the proxy appends the peer IP, so
        anything left of it is attacker-controlled.
        """
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50, 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '10.0.0.1')

    def test_remote_addresses_returns_full_chain(self):
        """remote_addresses should return the whole X-Forwarded-For chain"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50, 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(
            self.last_request['remote_addresses'], ['203.0.113.50', '10.0.0.1'])

    def test_empty_chain_entries_are_dropped(self):
        """Empty items must not be taken for an untrusted hop"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50, , \r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.50')
        self.assertEqual(
            self.last_request['remote_addresses'], ['203.0.113.50'])

    def test_chain_of_only_empty_entries_falls_back_to_socket(self):
        """A header with nothing usable must not produce an empty chain"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: ,\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '127.0.0.1')
        self.assertEqual(self.last_request['remote_addresses'], ['127.0.0.1'])

    def test_chain_entry_with_port_loses_the_port(self):
        """Some proxies write $remote_addr:$remote_port into the header"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.9:51234\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.9')

    def test_bracketed_ipv6_chain_entry_is_unwrapped(self):
        """RFC 3986 form, with and without a port"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: [2001:db8::9]:443\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '2001:db8::9')

    def test_bare_ipv6_chain_entry_is_kept(self):
        """An unbracketed IPv6 must not lose its last group to port stripping"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 2001:db8::9\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '2001:db8::9')

    def test_ipv4_mapped_chain_entry_is_normalized(self):
        """An IPv4-mapped entry must match trusted_proxies and read plainly"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: ::ffff:203.0.113.50\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.50')

    def test_no_forwarded_header_falls_back_to_socket(self):
        """Without X-Forwarded-For, should return socket IP"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '127.0.0.1')
        self.assertEqual(self.last_request['remote_addresses'], ['127.0.0.1'])

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
        wait_until_listening(cls.PORT)

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
        self.assertEqual(self.last_request['remote_address'], '127.0.0.1')
        self.assertEqual(self.last_request['remote_addresses'], ['127.0.0.1'])


class TestParseIp(unittest.TestCase):
    """Chain entries carrying a port or brackets must reduce to a bare IP"""

    def test_forms(self):
        cases = (
            ('203.0.113.9', '203.0.113.9'),
            ('203.0.113.9:51234', '203.0.113.9'),
            ('2001:db8::9', '2001:db8::9'),
            ('[2001:db8::9]', '2001:db8::9'),
            ('[2001:db8::9]:443', '2001:db8::9'),
            ('::1', '::1'),
            ('::ffff:192.0.2.1', '192.0.2.1'),
            ('[::ffff:192.0.2.1]:80', '192.0.2.1'),
            ('unknown', 'unknown'),
            ('[', ''),
        )
        for value, expected in cases:
            self.assertEqual(uhttp_server.parse_ip(value), expected, value)


class TestTrustedProxiesValidation(unittest.TestCase):
    """A str config would silently turn membership into substring matching"""

    PORT = 9964

    def test_string_is_rejected(self):
        with self.assertRaises(ValueError):
            uhttp_server.HttpServer(
                port=self.PORT, trusted_proxies='192.168.1.10')

    def test_cidr_is_rejected(self):
        """A range would never match, and it would fail silently"""
        with self.assertRaises(ValueError):
            uhttp_server.HttpServer(
                port=self.PORT, trusted_proxies=['10.0.0.0/8'])

    def test_config_entries_are_normalized(self):
        """Config and chain must be compared in the same form"""
        server = uhttp_server.HttpServer(
            port=self.PORT,
            trusted_proxies=['[::ffff:10.0.0.1]', '192.0.2.1:8080'])
        try:
            self.assertEqual(
                server._trusted_proxies, {'10.0.0.1', '192.0.2.1'})
        finally:
            server.close()

    def test_any_iterable_is_accepted(self):
        for proxies in (
                ['127.0.0.1'], ('127.0.0.1',), {'127.0.0.1'}):
            server = uhttp_server.HttpServer(
                port=self.PORT, trusted_proxies=proxies)
            try:
                self.assertEqual(server._trusted_proxies, {'127.0.0.1'})
            finally:
                server.close()

    def test_empty_config_disables_forwarded_for(self):
        server = uhttp_server.HttpServer(port=self.PORT, trusted_proxies=[])
        try:
            self.assertIsNone(server._trusted_proxies)
        finally:
            server.close()


class TestTrustedProxyChain(unittest.TestCase):
    """Test right-to-left walk over a chain of several trusted proxies"""

    server = None
    server_thread = None
    last_request = None
    PORT = 9963

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, trusted_proxies=['127.0.0.1', '10.0.0.1'])

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
        wait_until_listening(cls.PORT)

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

    def test_trusted_hops_are_skipped(self):
        """First hop that is not a trusted proxy is the client"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50, 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.50')

    def test_spoofed_prefix_is_ignored(self):
        """Entries the client prepended stay left of the real hop"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 1.2.3.4, 203.0.113.50, 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.50')
        self.assertEqual(
            self.last_request['remote_addresses'],
            ['1.2.3.4', '203.0.113.50', '10.0.0.1'])

    def test_trusted_hop_written_with_a_port_still_matches(self):
        """A port on a trusted hop must not stop the walk on that proxy"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 203.0.113.50, 10.0.0.1:4711\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '203.0.113.50')

    def test_all_hops_trusted_falls_back_to_nearest(self):
        """With no untrusted hop the nearest one is returned, never nothing"""
        self.send_request(
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1\r\n"
            b"\r\n")
        time.sleep(0.2)
        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['remote_address'], '127.0.0.1')


if __name__ == '__main__':
    unittest.main()
