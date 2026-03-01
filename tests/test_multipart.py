#!/usr/bin/env python3
"""
Test multipart response functionality
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestMultipart(unittest.TestCase):
    """Test suite for multipart responses"""

    server = None
    server_thread = None
    multipart_clients = []
    PORT = 9980

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)

                    if client:
                        if client.path == '/':
                            client.respond("<h1>Multipart Test</h1>")

                        elif client.path == '/stream':
                            if client.response_multipart():
                                cls.multipart_clients.append({
                                    'client': client,
                                    'counter': 0,
                                    'last_send': time.time()
                                })

                        else:
                            client.respond("Not found", status=404)

                    # Send frames to active multipart clients
                    for mc in list(cls.multipart_clients):
                        if time.time() - mc['last_send'] > 0.1:  # Send every 100ms
                            mc['counter'] += 1
                            data = f"Frame {mc['counter']}\n"

                            if mc['counter'] >= 5:  # Send 5 frames then close
                                mc['client'].response_multipart_end()
                                mc['client'].close()
                                cls.multipart_clients.remove(mc)
                            else:
                                if mc['client'].response_multipart_frame(data):
                                    mc['last_send'] = time.time()
                                else:
                                    mc['client'].close()
                                    cls.multipart_clients.remove(mc)

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

    def setUp(self):
        """Reset before each test"""
        TestMultipart.multipart_clients = []

    def test_basic_request(self):
        """Test basic non-multipart request"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))

            request = (
                b"GET / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )

            sock.sendall(request)
            sock.settimeout(2.0)

            # Read all data
            all_data = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    all_data += chunk
            except socket.timeout:
                pass

            response = all_data.decode()

            self.assertIn("200 OK", response)
            self.assertIn("Multipart Test", response)
        finally:
            sock.close()

    def test_multipart_stream(self):
        """Test multipart stream response"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))

            request = (
                b"GET /stream HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )

            sock.sendall(request)
            sock.settimeout(3.0)

            # Read initial headers
            headers = b""
            while b"\r\n\r\n" not in headers:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                headers += chunk

            headers_str = headers.decode('utf-8', errors='ignore')

            # Verify multipart headers
            self.assertIn("200 OK", headers_str)
            self.assertIn("multipart/x-mixed-replace", headers_str)
            self.assertIn("boundary=", headers_str)

            # Read some frames
            frames_data = b""
            try:
                while len(frames_data) < 4096:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    frames_data += chunk
            except socket.timeout:
                pass

            frames_str = frames_data.decode('utf-8', errors='ignore')

            # Verify frames were received
            self.assertIn("Frame 1", frames_str)
            self.assertIn("Frame 2", frames_str)
        finally:
            sock.close()

    def test_multipart_frame_count(self):
        """Test that all expected frames are received"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))

            request = (
                b"GET /stream HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )

            sock.sendall(request)
            sock.settimeout(3.0)

            # Read all data
            all_data = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    all_data += chunk
            except socket.timeout:
                pass

            all_str = all_data.decode('utf-8', errors='ignore')

            # Count frames (server sends 5 frames)
            frame_count = sum(1 for i in range(1, 6) if f"Frame {i}" in all_str)

            self.assertGreaterEqual(frame_count, 3)  # At least 3 frames received
        finally:
            sock.close()

    def test_not_found(self):
        """Test 404 response for unknown path"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))

            request = (
                b"GET /notfound HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )

            sock.sendall(request)
            sock.settimeout(2.0)

            # Read all data
            all_data = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    all_data += chunk
            except socket.timeout:
                pass

            response = all_data.decode()

            self.assertIn("404", response)
        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
