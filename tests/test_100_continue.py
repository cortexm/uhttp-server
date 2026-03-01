#!/usr/bin/env python3
"""
Tests for Expect: 100-continue support
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server
from uhttp.server import EVENT_REQUEST, EVENT_HEADERS, EVENT_COMPLETE


class Test100ContinueNonEventMode(unittest.TestCase):
    """Test 100-continue in non-event mode"""

    server = None
    server_thread = None
    PORT = 9980
    received_data = []

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT, event_mode=False)

        def run_server():
            try:
                while True:
                    server = cls.server
                    if server is None:
                        break
                    client = server.wait(timeout=0.5)
                    if client:
                        cls.received_data.append({
                            'path': client.path,
                            'data_len': len(client.data) if client.data else 0,
                        })
                        client.respond({'status': 'ok', 'received': len(client.data) if client.data else 0})
            except Exception as e:
                print(f"Server error: {e}")

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        """Stop server after all tests"""
        if cls.server:
            cls.server.close()
            cls.server = None

    def setUp(self):
        """Reset before each test"""
        Test100ContinueNonEventMode.received_data = []

    def test_100_continue_auto_response(self):
        """Test that server automatically sends 100 Continue in non-event mode"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(5)

            body = b'test data for 100 continue'
            headers = (
                f"POST /upload HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Expect: 100-continue\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()

            # Send headers only
            sock.send(headers)

            # Wait for 100 Continue
            sock.settimeout(2)
            response_100 = b''
            try:
                while b'\r\n\r\n' not in response_100:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response_100 += chunk
            except socket.timeout:
                self.fail("Did not receive 100 Continue response")

            # Verify we got 100 Continue
            self.assertIn(b'100 Continue', response_100)

            # Now send the body
            sock.send(body)

            # Get final response
            sock.settimeout(3)
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
            self.assertIn("200 OK", response_str)
            self.assertIn(f'"received": {len(body)}', response_str)

        finally:
            sock.close()

    def test_without_expect_header(self):
        """Test normal POST without Expect header still works"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3)

            body = b'normal post data'
            request = (
                f"POST /test HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode() + body

            sock.send(request)

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
            self.assertIn("200 OK", response_str)
            self.assertIn(f'"received": {len(body)}', response_str)

        finally:
            sock.close()


class Test100ContinueEventMode(unittest.TestCase):
    """Test 100-continue in event mode"""

    server = None
    server_thread = None
    PORT = 9981
    received_events = []

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT, event_mode=True)

        def run_server():
            try:
                while True:
                    server = cls.server
                    if server is None:
                        break
                    client = server.wait(timeout=0.5)
                    if client:
                        cls.received_events.append(client.event)

                        if client.event == EVENT_REQUEST:
                            client.respond({'status': 'ok', 'event': 'request'})
                        elif client.event == EVENT_HEADERS:
                            client.accept_body()
                        elif client.event == EVENT_COMPLETE:
                            data = client.read_buffer()
                            size = len(data) if data else 0
                            client.respond({'status': 'ok', 'received': size})
            except Exception as e:
                print(f"Server error: {e}")

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        """Stop server after all tests"""
        if cls.server:
            cls.server.close()
            cls.server = None

    def setUp(self):
        """Reset before each test"""
        Test100ContinueEventMode.received_events = []

    def test_100_continue_after_accept_body(self):
        """Test that 100 Continue is sent after accept_body() in event mode"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(5)

            body = b'event mode 100 continue test data'
            headers = (
                f"POST /upload HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Expect: 100-continue\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()

            # Send headers only
            sock.send(headers)

            # Wait for 100 Continue
            sock.settimeout(2)
            response_100 = b''
            try:
                while b'\r\n\r\n' not in response_100:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response_100 += chunk
            except socket.timeout:
                self.fail("Did not receive 100 Continue response")

            # Verify we got 100 Continue
            self.assertIn(b'100 Continue', response_100)

            # Now send the body
            sock.send(body)

            # Get final response
            sock.settimeout(3)
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
            self.assertIn("200 OK", response_str)
            self.assertIn(f'"received": {len(body)}', response_str)

            # Verify event sequence
            self.assertIn(EVENT_HEADERS, self.received_events)
            self.assertIn(EVENT_COMPLETE, self.received_events)

        finally:
            sock.close()


class Test100ContinueEventModeReject(unittest.TestCase):
    """Test rejecting request in event mode without sending 100 Continue"""

    server = None
    server_thread = None
    PORT = 9982

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT, event_mode=True)

        def run_server():
            try:
                while True:
                    server = cls.server
                    if server is None:
                        break
                    client = server.wait(timeout=0.5)
                    if client:
                        if client.event == EVENT_REQUEST:
                            client.respond({'status': 'ok'})
                        elif client.event == EVENT_HEADERS:
                            # Reject the upload - don't call accept_body()
                            # This should NOT send 100 Continue
                            client.respond(
                                {'error': 'rejected'}, status=413,
                                headers={'connection': 'close'})
            except Exception as e:
                print(f"Server error: {e}")

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        """Stop server after all tests"""
        if cls.server:
            cls.server.close()
            cls.server = None

    def test_reject_without_100_continue(self):
        """Test that rejecting request does not send 100 Continue"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(5)

            body = b'this data should not be accepted'
            headers = (
                f"POST /upload HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Expect: 100-continue\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()

            # Send headers only
            sock.send(headers)

            # Wait for response (should be 413, not 100 Continue)
            sock.settimeout(2)
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

            # Should NOT contain 100 Continue
            self.assertNotIn('100 Continue', response_str)

            # Should contain 413 rejection
            self.assertIn('413', response_str)
            self.assertIn('rejected', response_str)

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
