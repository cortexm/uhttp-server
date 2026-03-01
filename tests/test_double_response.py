#!/usr/bin/env python3
"""
Test that multiple respond() calls are prevented
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server


class TestDoubleResponse(unittest.TestCase):
    """Test suite for preventing multiple responses"""

    server = None
    server_thread = None
    test_scenario = None
    PORT = 9979

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)

                    if client:
                        try:
                            if cls.test_scenario == 'double_respond':
                                client.respond({'message': 'First response'})
                                # Try to respond again - should raise HttpError
                                client.respond({'message': 'Second response'})

                            elif cls.test_scenario == 'double_respond_file':
                                client.respond({'message': 'First response'})
                                # Try to respond with file - should raise HttpError
                                client.respond_file('test.txt')

                            elif cls.test_scenario == 'double_multipart':
                                client.response_multipart()
                                # Try to start multipart again - should raise HttpError
                                client.response_multipart()

                            elif cls.test_scenario == 'respond_after_multipart':
                                client.response_multipart()
                                client.response_multipart_frame(b"Frame 1")
                                # Try to respond - should raise HttpError
                                client.respond({'message': 'Response'})

                            else:
                                # Normal response for connection test
                                client.respond({'message': 'OK'})

                        except uhttp_server.HttpError:
                            # Expected error - close connection
                            client.close()

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
        TestDoubleResponse.test_scenario = None

    def send_request_and_get_response(self):
        """Helper to send request and return response"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(2.0)
            sock.connect(('localhost', self.PORT))

            request = b"GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            sock.sendall(request)

            response = b""
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass

            return response.decode('utf-8', errors='ignore')
        except Exception as e:
            return str(e)
        finally:
            sock.close()

    def test_double_respond(self):
        """Test that calling respond() twice raises error"""
        TestDoubleResponse.test_scenario = 'double_respond'
        response = self.send_request_and_get_response()

        # Should only get first response, second should fail
        self.assertIn("First response", response)
        self.assertNotIn("Second response", response)

    def test_double_respond_file(self):
        """Test that calling respond() then respond_file() raises error"""
        TestDoubleResponse.test_scenario = 'double_respond_file'
        response = self.send_request_and_get_response()

        # Should only get first response
        self.assertIn("First response", response)

    def test_double_multipart(self):
        """Test that calling response_multipart() twice raises error"""
        TestDoubleResponse.test_scenario = 'double_multipart'
        response = self.send_request_and_get_response()

        # Should get multipart headers only once
        self.assertEqual(response.count("multipart/x-mixed-replace"), 1)

    def test_respond_after_multipart(self):
        """Test that calling respond() after response_multipart() raises error"""
        TestDoubleResponse.test_scenario = 'respond_after_multipart'
        response = self.send_request_and_get_response()

        # Should get multipart headers, not JSON response
        self.assertIn("multipart/x-mixed-replace", response)
        self.assertNotIn("message", response)

    def test_keepalive_resets_response_flag(self):
        """Test that keep-alive connection resets response flag"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(2.0)

            # First request
            request1 = b"GET /test1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request1)

            # Read first response
            response1 = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response1 += chunk
                if b"\r\n\r\n" in response1:
                    header_end = response1.index(b"\r\n\r\n") + 4
                    body = response1[header_end:]
                    if body and b"}" in body:
                        break

            response1_str = response1.decode()
            self.assertIn("200 OK", response1_str)

            # Second request on same connection
            request2 = b"GET /test2 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            sock.send(request2)

            # Read second response
            response2 = b""
            while True:
                chunk = sock.recv(1024)
                if not chunk:
                    break
                response2 += chunk
                if b"\r\n\r\n" in response2:
                    header_end = response2.index(b"\r\n\r\n") + 4
                    body = response2[header_end:]
                    if body and b"}" in body:
                        break

            response2_str = response2.decode()
            # Should get second response successfully (flag was reset)
            self.assertIn("200 OK", response2_str)

        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
