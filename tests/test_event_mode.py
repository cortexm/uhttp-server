#!/usr/bin/env python3
"""
Tests for event mode streaming functionality
"""
import unittest
import socket
import time
import threading
import tempfile
import os
from uhttp import server as uhttp_server
from uhttp.server import (
    EVENT_REQUEST, EVENT_HEADERS, EVENT_DATA, EVENT_COMPLETE, EVENT_ERROR
)


class TestEventModeBasic(unittest.TestCase):
    """Test basic event mode functionality"""

    server = None
    server_thread = None
    PORT = 9990
    events_received = []

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
                        cls.events_received.append({
                            'event': client.event,
                            'path': client.path,
                            'method': client.method,
                            'content_length': client.content_length,
                        })

                        if client.event == EVENT_REQUEST:
                            client.respond({'status': 'ok', 'event': 'request'})
                        elif client.event == EVENT_HEADERS:
                            client.accept_body()
                            data = client.read_buffer()
                            if data:
                                client.context = {'chunks': [data]}
                            else:
                                client.context = {'chunks': []}
                        elif client.event == EVENT_DATA:
                            data = client.read_buffer()
                            if data and client.context:
                                client.context['chunks'].append(data)
                        elif client.event == EVENT_COMPLETE:
                            data = client.read_buffer()
                            if data and client.context:
                                client.context['chunks'].append(data)
                            total = sum(
                                len(c) for c in client.context.get('chunks', []))
                            client.respond({
                                'status': 'ok',
                                'event': 'complete',
                                'total_bytes': total,
                                'chunks_count': len(client.context.get('chunks', []))
                            })
                        elif client.event == EVENT_ERROR:
                            pass  # Connection will be closed
            except Exception:
                pass

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
        TestEventModeBasic.events_received = []

    def test_simple_get_returns_event_request(self):
        """Test that simple GET without body returns EVENT_REQUEST"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2)

            request = b"GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
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
            self.assertIn('"event": "request"', response_str)

            # Verify event was EVENT_REQUEST
            self.assertTrue(len(self.events_received) > 0)
            self.assertEqual(self.events_received[0]['event'], EVENT_REQUEST)

        finally:
            sock.close()

    def test_small_post_single_packet_returns_event_request(self):
        """Test that small POST sent in one packet returns EVENT_REQUEST"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3)

            body = b'{"test": "data"}'
            request = (
                f"POST /test HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode() + body

            # Send headers + body in single packet
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
            # Since headers + body arrived together, expect EVENT_REQUEST
            self.assertIn('"event": "request"', response_str)

            # Verify event was EVENT_REQUEST (0)
            self.assertTrue(len(self.events_received) > 0)
            self.assertEqual(self.events_received[0]['event'], EVENT_REQUEST)

        finally:
            sock.close()

    def test_small_post_split_returns_event_complete(self):
        """Test that small POST with split headers/body returns EVENT_COMPLETE"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3)

            body = b'{"test": "data"}'
            headers = (
                f"POST /test HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()

            # Send headers first, then body separately to force EVENT_HEADERS
            sock.send(headers)
            time.sleep(0.2)
            sock.send(body)

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
            # Since we sent headers and body separately, expect EVENT_COMPLETE
            self.assertIn('"event": "complete"', response_str)

        finally:
            sock.close()


class TestEventModeStreaming(unittest.TestCase):
    """Test streaming functionality in event mode"""

    server = None
    server_thread = None
    PORT = 9991
    received_data = []

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
                            client.context = {'total': 0}
                            pending = client.accept_body_streaming()
                            cls.received_data.append(
                                ('headers', client.path, pending))
                            data = client.read_buffer()
                            if data:
                                client.context['total'] += len(data)
                        elif client.event == EVENT_DATA:
                            data = client.read_buffer()
                            if data:
                                client.context['total'] += len(data)
                                cls.received_data.append(('data', len(data)))
                        elif client.event == EVENT_COMPLETE:
                            data = client.read_buffer()
                            if data:
                                client.context['total'] += len(data)
                            cls.received_data.append(
                                ('complete', client.context['total']))
                            client.respond({
                                'total': client.context['total'],
                                'bytes_received': client.bytes_received
                            })
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
        TestEventModeStreaming.received_data = []

    def test_large_post_triggers_headers_event(self):
        """Test that large POST triggers EVENT_HEADERS first"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(5)

            # Send headers first, then body in chunks
            body_size = 10000
            headers = (
                f"POST /upload HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Length: {body_size}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()
            sock.send(headers)
            time.sleep(0.1)

            # Send body in chunks
            body = b'X' * body_size
            chunk_size = 1000
            for i in range(0, len(body), chunk_size):
                sock.send(body[i:i+chunk_size])
                time.sleep(0.05)

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
            self.assertIn(f'"total": {body_size}', response_str)

            # Verify we got headers event
            headers_events = [e for e in self.received_data if e[0] == 'headers']
            self.assertTrue(len(headers_events) > 0)

            # Verify we got complete event
            complete_events = [e for e in self.received_data if e[0] == 'complete']
            self.assertTrue(len(complete_events) > 0)
            self.assertEqual(complete_events[0][1], body_size)

        finally:
            sock.close()


class TestEventModeFileUpload(unittest.TestCase):
    """Test file upload functionality in event mode"""

    server = None
    server_thread = None
    PORT = 9992
    upload_path = None
    upload_result = None

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
                            # Create temp file for upload
                            fd, cls.upload_path = tempfile.mkstemp()
                            os.close(fd)
                            client.accept_body_to_file(cls.upload_path)
                        elif client.event == EVENT_DATA:
                            pass  # File mode handles data internally
                        elif client.event == EVENT_COMPLETE:
                            # Check file size
                            if cls.upload_path and os.path.exists(cls.upload_path):
                                size = os.path.getsize(cls.upload_path)
                                cls.upload_result = {'size': size}
                                client.respond({'uploaded': size})
                            else:
                                client.respond({'error': 'file not found'})
                        elif client.event == EVENT_ERROR:
                            cls.upload_result = {'error': client.error}
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
        # Cleanup temp file
        if cls.upload_path and os.path.exists(cls.upload_path):
            os.unlink(cls.upload_path)

    def setUp(self):
        """Reset before each test"""
        TestEventModeFileUpload.upload_result = None
        if TestEventModeFileUpload.upload_path:
            if os.path.exists(TestEventModeFileUpload.upload_path):
                os.unlink(TestEventModeFileUpload.upload_path)
            TestEventModeFileUpload.upload_path = None

    def test_file_upload(self):
        """Test uploading data to file"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(5)

            body_size = 5000
            headers = (
                f"POST /upload HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Length: {body_size}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            ).encode()
            sock.send(headers)
            time.sleep(0.1)

            # Send body
            body = b'Y' * body_size
            sock.send(body)

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
            self.assertIn(f'"uploaded": {body_size}', response_str)

            # Verify file was created with correct size
            self.assertIsNotNone(self.upload_result)
            self.assertEqual(self.upload_result.get('size'), body_size)

        finally:
            sock.close()


class TestEventModeBackwardsCompatibility(unittest.TestCase):
    """Test that event_mode=False still works as before"""

    server = None
    server_thread = None
    PORT = 9993

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
                        # In non-event mode, event should be None
                        client.respond({
                            'status': 'ok',
                            'event': client.event,
                            'data_len': len(client.data) if client.data else 0
                        })
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        """Stop server after all tests"""
        if cls.server:
            cls.server.close()
            cls.server = None

    def test_non_event_mode_works(self):
        """Test that non-event mode returns complete request"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2)

            body = b'test data here'
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
            # In non-event mode, event should be None
            self.assertIn('"event": null', response_str)
            self.assertIn(f'"data_len": {len(body)}', response_str)

        finally:
            sock.close()


class TestEventModeConstants(unittest.TestCase):
    """Test event mode constants are exported correctly"""

    def test_constants_exist(self):
        """Test that all event constants are defined"""
        self.assertEqual(EVENT_REQUEST, 0)
        self.assertEqual(EVENT_HEADERS, 1)
        self.assertEqual(EVENT_DATA, 2)
        self.assertEqual(EVENT_COMPLETE, 3)
        self.assertEqual(EVENT_ERROR, 4)

    def test_constants_importable(self):
        """Test that constants can be imported from uhttp.server"""
        from uhttp.server import (
            EVENT_REQUEST, EVENT_HEADERS, EVENT_DATA,
            EVENT_COMPLETE, EVENT_ERROR
        )
        self.assertIsNotNone(EVENT_REQUEST)


class TestEventModeContext(unittest.TestCase):
    """Test context attribute functionality"""

    server = None
    server_thread = None
    PORT = 9994
    context_values = []

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
                            # Test setting and reading context
                            client.context = {'test': 'value', 'count': 42}
                            cls.context_values.append(client.context)
                            client.respond({'context_set': True})
                        elif client.event == EVENT_HEADERS:
                            client.context = {'streaming': True}
                            client.accept_body()
                        elif client.event == EVENT_COMPLETE:
                            cls.context_values.append(client.context)
                            client.respond({
                                'context': client.context.get('streaming')
                            })
            except Exception:
                pass

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
        TestEventModeContext.context_values = []

    def test_context_can_store_data(self):
        """Test that context can store arbitrary data"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2)

            request = b"GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
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

            # Verify context was set
            self.assertTrue(len(self.context_values) > 0)
            self.assertEqual(self.context_values[0]['test'], 'value')
            self.assertEqual(self.context_values[0]['count'], 42)

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
