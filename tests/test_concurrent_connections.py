#!/usr/bin/env python3
"""
Test concurrent connections - multiple clients connecting simultaneously
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestConcurrentConnections(unittest.TestCase):
    """Test suite for concurrent connections"""

    server = None
    server_thread = None
    request_count = 0
    connections_count = 0
    lock = threading.Lock()
    PORT = 9988

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT, max_waiting_clients=10)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        with cls.lock:
                            cls.request_count += 1
                            req_num = cls.request_count

                        # Simulate some processing time
                        time.sleep(0.05)

                        client.respond({
                            'request_number': req_num,
                            'path': client.path,
                            'client_addr': str(client.addr)
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

    def setUp(self):
        """Reset counters before each test"""
        with self.lock:
            TestConcurrentConnections.request_count = 0
            TestConcurrentConnections.connections_count = 0

    def client_thread(self, client_id, num_requests):
        """Client that makes multiple requests"""
        with self.lock:
            TestConcurrentConnections.connections_count += 1

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect(('localhost', self.PORT))

            results = []
            for i in range(num_requests):
                request = (
                    f"GET /client{client_id}/request{i} HTTP/1.1\r\n"
                    f"Host: localhost\r\n"
                    f"Connection: keep-alive\r\n"
                    f"\r\n"
                ).encode()

                sock.sendall(request)
                response = sock.recv(4096).decode()

                if "200 OK" in response:
                    results.append(True)
                else:
                    results.append(False)

                time.sleep(0.01)

            sock.close()
            return results

        except Exception:
            return []

    def test_concurrent_clients(self):
        """Test multiple clients connecting at the same time (5 clients, 3 requests each)"""
        initial_count = self.request_count
        num_clients = 5
        requests_per_client = 3

        # Start all clients at once
        threads = []
        for i in range(num_clients):
            t = threading.Thread(target=self.client_thread, args=(i, requests_per_client))
            t.start()
            threads.append(t)

        # Wait for all to complete
        for t in threads:
            t.join()

        time.sleep(0.5)

        expected_requests = num_clients * requests_per_client
        actual_requests = self.request_count - initial_count

        self.assertEqual(actual_requests, expected_requests)

    def test_rapid_connections(self):
        """Test rapid connection and disconnection (10 quick sequential connections)"""
        initial_count = self.request_count
        num_connections = 10
        successful = 0

        for i in range(num_connections):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(('localhost', self.PORT))

                request = (
                    f"GET /rapid{i} HTTP/1.1\r\n"
                    f"Host: localhost\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode()

                sock.sendall(request)
                response = sock.recv(4096)

                if response and b"200 OK" in response:
                    successful += 1

                sock.close()

            except Exception:
                pass

        time.sleep(0.3)

        self.assertEqual(successful, num_connections)

    def test_max_waiting_clients(self):
        """Test server max waiting clients limit"""
        # Connect many clients but don't send requests yet
        sockets = []
        max_clients = 15  # More than server's max_waiting_clients (10)

        for i in range(max_clients):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect(('localhost', self.PORT))
                sockets.append(sock)
                time.sleep(0.02)
            except Exception:
                break

        # Now send requests on all sockets
        responses = []
        for i, sock in enumerate(sockets):
            try:
                request = (
                    f"GET /test{i} HTTP/1.1\r\n"
                    f"Host: localhost\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode()

                sock.sendall(request)
                sock.settimeout(1.0)
                response = sock.recv(4096)
                responses.append(response)
            except Exception:
                responses.append(None)

        # Close all sockets
        for sock in sockets:
            sock.close()

        time.sleep(0.5)

        successful = sum(1 for r in responses if r and b"200 OK" in r)
        timeouts = sum(1 for r in responses if r and b"408" in r)

        # Server should handle up to max_waiting_clients, excess get 408
        self.assertGreater(successful, 0)

    def test_slow_client(self):
        """Test client that is slow to send data"""
        initial_count = self.request_count

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))

        # Send headers
        headers = (
            b"POST /slow HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 20\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        sock.sendall(headers)

        # Wait before sending body
        time.sleep(0.5)

        # Send body slowly
        body = b'{"slow_client":true}'
        sock.sendall(body)

        sock.settimeout(2.0)
        try:
            response = sock.recv(4096).decode()
            self.assertIn("200 OK", response)
        except socket.timeout:
            self.fail("Server timed out waiting for data")

        sock.close()


if __name__ == '__main__':
    unittest.main()
