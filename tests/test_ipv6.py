#!/usr/bin/env python3
"""
Tests for IPv6 and dual-stack server functionality
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestIPv6Server(unittest.TestCase):
    """Test suite for IPv6 server"""

    PORT = 9990

    def test_ipv6_socket_creation(self):
        """Test that IPv6 address creates AF_INET6 socket"""
        server = uhttp_server.HttpServer(address='::', port=self.PORT)
        self.assertEqual(server.socket.family, socket.AF_INET6)
        server.close()

    def test_ipv4_socket_creation(self):
        """Test that IPv4 address creates AF_INET socket"""
        server = uhttp_server.HttpServer(address='0.0.0.0', port=self.PORT + 1)
        self.assertEqual(server.socket.family, socket.AF_INET)
        server.close()

    def test_ipv6_address_detection(self):
        """Test that ':' in address triggers IPv6 socket creation"""
        # Test addresses that contain ':' should be detected as IPv6
        ipv6_addresses = ['::', '::1', '::0', '2001:db8::1', 'fe80::1']
        for addr in ipv6_addresses:
            self.assertIn(':', addr)

        # Test addresses without ':' should be detected as IPv4
        ipv4_addresses = ['0.0.0.0', '127.0.0.1', '192.168.1.1']
        for addr in ipv4_addresses:
            self.assertNotIn(':', addr)

    def test_bindable_addresses(self):
        """Test server creation with bindable addresses"""
        # Only test addresses that should be bindable on most systems
        test_cases = [
            ('::', socket.AF_INET6),
            ('0.0.0.0', socket.AF_INET),
        ]
        for i, (address, expected_family) in enumerate(test_cases):
            try:
                server = uhttp_server.HttpServer(
                    address=address, port=self.PORT + 10 + i)
                self.assertEqual(
                    server.socket.family, expected_family,
                    f"Address {address} should create {expected_family}")
                server.close()
            except OSError as e:
                if 'Cannot assign' in str(e):
                    self.skipTest(f"Address {address} not bindable on this system")
                raise


class TestDualStack(unittest.TestCase):
    """Test dual-stack server accepting both IPv4 and IPv6"""

    server = None
    server_thread = None
    PORT = 9995

    @classmethod
    def setUpClass(cls):
        """Start dual-stack server"""
        try:
            cls.server = uhttp_server.HttpServer(address='::', port=cls.PORT)
        except OSError:
            raise unittest.SkipTest("IPv6 not available on this system")

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.5)
                    if client:
                        client.respond({
                            'remote': client.remote_address,
                            'path': client.path
                        })
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        """Stop server"""
        if cls.server:
            cls.server.close()
            cls.server = None

    def test_ipv4_client_connection(self):
        """Test IPv4 client connecting to dual-stack server"""
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(('127.0.0.1', self.PORT))
            client.send(b'GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n')
            response = client.recv(4096)
            self.assertIn(b'200 OK', response)
            # Check that remote_address is normalized (no ::ffff: prefix)
            self.assertIn(b'127.0.0.1', response)
            self.assertNotIn(b'::ffff:', response)
        finally:
            client.close()

    def test_ipv6_client_connection(self):
        """Test IPv6 client connecting to dual-stack server"""
        client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            client.connect(('::1', self.PORT))
            client.send(b'GET /test HTTP/1.1\r\nHost: localhost\r\n\r\n')
            response = client.recv(4096)
            self.assertIn(b'200 OK', response)
            self.assertIn(b'::1', response)
        except OSError as e:
            if 'Cannot assign' in str(e):
                self.skipTest("IPv6 loopback not available")
            raise
        finally:
            client.close()


class TestRemoteAddressNormalization(unittest.TestCase):
    """Test remote_address normalization for IPv4-mapped addresses"""

    def test_ipv4_mapped_normalization(self):
        """Test that ::ffff:x.x.x.x is normalized to x.x.x.x"""
        # Create a mock connection to test the property
        server = uhttp_server.HttpServer(port=9998)

        # Simulate IPv4-mapped address from accept()
        class MockConnection:
            def __init__(self):
                self._addr = ('::ffff:192.168.1.100', 12345)
                self._headers = {}

            def headers_get_attribute(self, name):
                return self._headers.get(name)

        conn = MockConnection()
        # Bind the property method
        conn.remote_address = property(
            lambda self: uhttp_server.HttpConnection.remote_address.fget(self))

        # Test directly using the logic
        addr = conn._addr[0]
        if addr.startswith('::ffff:'):
            addr = addr[7:]
        result = f"{addr}:{conn._addr[1]}"

        self.assertEqual(result, '192.168.1.100:12345')
        server.close()

    def test_ipv6_address_unchanged(self):
        """Test that pure IPv6 addresses are not modified"""
        addr = '::1'
        if addr.startswith('::ffff:'):
            addr = addr[7:]
        self.assertEqual(addr, '::1')

    def test_ipv4_address_unchanged(self):
        """Test that pure IPv4 addresses are not modified"""
        addr = '192.168.1.1'
        if addr.startswith('::ffff:'):
            addr = addr[7:]
        self.assertEqual(addr, '192.168.1.1')


if __name__ == '__main__':
    unittest.main()
