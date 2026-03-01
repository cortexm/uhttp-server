#!/usr/bin/env python3
"""
Test data parsing - form data, JSON, query strings, cookies
"""
import unittest
import socket
import time
import threading
import json
from uhttp import server as uhttp_server


class TestDataParsing(unittest.TestCase):
    """Test suite for HTTP data parsing"""

    server = None
    server_thread = None
    last_request = None  # pylint: disable=unsubscriptable-object
    PORT = 9990

    @classmethod
    def setUpClass(cls):
        """Start server once for all tests"""
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)
                    if client:
                        # Store full request info for verification
                        cls.last_request = {
                            'method': client.method,
                            'path': client.path,
                            'query': client.query,
                            'data': client.data,
                            'headers': dict(client._headers) if client._headers else {},
                            'cookies': client.cookies
                        }
                        # Simple response (avoid sending bytes in JSON)
                        client.respond({'status': 'ok'})
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)  # Wait for server to start

    @classmethod
    def tearDownClass(cls):
        """Stop server after all tests"""
        if cls.server:
            cls.server.close()
            cls.server = None

    def setUp(self):
        """Reset before each test"""
        TestDataParsing.last_request = None

    def send_request(self, request_bytes):
        """Helper to send request and return parsed JSON response"""
        try:
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

            # Extract JSON from response
            if b"\r\n\r\n" in response:
                body = response.split(b"\r\n\r\n", 1)[1]
                if body:
                    return json.loads(body.decode())
            return None
        except Exception as e:
            self.fail(f"Request failed: {e}")
            return None

    # Query String Parsing Tests

    def test_query_string_simple(self):
        """Test simple query string parsing"""
        request = b"GET /test?a=1&b=2&c=3 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['query'], {'a': '1', 'b': '2', 'c': '3'})

    def test_query_string_plus_to_space(self):
        """Test plus sign conversion to space"""
        request = b"GET /test?name=John+Doe HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['query'], {'name': 'John Doe'})

    def test_query_string_url_encoding(self):
        """Test URL percent encoding"""
        request = b"GET /test?email=test%40example.com HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['query'], {'email': 'test@example.com'})

    def test_query_string_duplicate_keys(self):
        """Test duplicate query keys become list"""
        request = b"GET /test?list=1&list=2&list=3 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['query'], {'list': ['1', '2', '3']})

    def test_query_string_empty_value(self):
        """Test empty value with = sign"""
        request = b"GET /test?empty=&another=value HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['query'], {'empty': '', 'another': 'value'})

    def test_query_string_no_value(self):
        """Test parameter without = sign (flag)"""
        request = b"GET /test?novalue HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['query'], {'novalue': None})

    # JSON Parsing Tests

    def test_json_simple_object(self):
        """Test simple JSON object parsing"""
        data = {'id': 123, 'name': 'Test'}
        json_bytes = json.dumps(data).encode('utf-8')

        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n" +
            f"Content-Length: {len(json_bytes)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            json_bytes
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], data)

    def test_json_nested_object(self):
        """Test nested JSON object"""
        data = {'nested': {'key': 'value'}}
        json_bytes = json.dumps(data).encode('utf-8')

        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n" +
            f"Content-Length: {len(json_bytes)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            json_bytes
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], data)

    def test_json_array(self):
        """Test JSON array"""
        data = [1, 2, 3, 4, 5]
        json_bytes = json.dumps(data).encode('utf-8')

        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n" +
            f"Content-Length: {len(json_bytes)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            json_bytes
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], data)

    def test_json_unicode(self):
        """Test JSON with unicode characters"""
        data = {'unicode': 'Dobrý deň! 你好'}
        json_bytes = json.dumps(data).encode('utf-8')

        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n" +
            f"Content-Length: {len(json_bytes)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            json_bytes
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], data)

    def test_json_various_types(self):
        """Test JSON with various data types"""
        data = {'number': 42, 'float': 3.14, 'bool': True, 'null': None}
        json_bytes = json.dumps(data).encode('utf-8')

        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n" +
            f"Content-Length: {len(json_bytes)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            json_bytes
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], data)

    # Form Data Parsing Tests

    def test_form_data_simple(self):
        """Test simple form data"""
        form_data = b"username=john&password=secret123"

        request = (
            b"POST /form HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n" +
            f"Content-Length: {len(form_data)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            form_data
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], {'username': 'john', 'password': 'secret123'})

    def test_form_data_spaces(self):
        """Test form data with spaces (plus signs)"""
        form_data = b"name=John+Doe&city=New+York"

        request = (
            b"POST /form HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n" +
            f"Content-Length: {len(form_data)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            form_data
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], {'name': 'John Doe', 'city': 'New York'})

    def test_form_data_url_encoded(self):
        """Test form data with URL encoding"""
        form_data = b"email=test%40example.com&url=https%3A%2F%2Fexample.com"

        request = (
            b"POST /form HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n" +
            f"Content-Length: {len(form_data)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            form_data
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], {'email': 'test@example.com', 'url': 'https://example.com'})

    def test_form_data_multiple_values(self):
        """Test form data with duplicate keys"""
        form_data = b"tags=python&tags=http&tags=server"

        request = (
            b"POST /form HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n" +
            f"Content-Length: {len(form_data)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            form_data
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], {'tags': ['python', 'http', 'server']})

    # Cookie Parsing Tests

    def test_cookie_single(self):
        """Test single cookie parsing"""
        request = (
            b"GET /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Cookie: session=abc123\r\n"
            b"Connection: close\r\n\r\n"
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['cookies'], {'session': 'abc123'})

    def test_cookie_multiple(self):
        """Test multiple cookies"""
        request = (
            b"GET /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Cookie: user=john; token=xyz789\r\n"
            b"Connection: close\r\n\r\n"
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['cookies'], {'user': 'john', 'token': 'xyz789'})

    def test_cookie_multiple_values(self):
        """Test cookies with multiple values"""
        request = (
            b"GET /test HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Cookie: id=123; name=TestUser\r\n"
            b"Connection: close\r\n\r\n"
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['cookies'], {'id': '123', 'name': 'TestUser'})

    # Binary Data Tests

    def test_raw_binary_data(self):
        """Test raw binary data preservation"""
        binary_data = b'\x00\x01\x02\x03\x04\x05\xFF\xFE\xFD'

        request = (
            b"POST /upload HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/octet-stream\r\n" +
            f"Content-Length: {len(binary_data)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            binary_data
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['data'], binary_data)

    # Empty POST Test

    def test_empty_post(self):
        """Test POST with no body"""
        request = b"POST /empty HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertIn(self.last_request['data'], [None, b''])

    # Combined Query and Body Test

    def test_query_and_body(self):
        """Test request with both query string and POST body"""
        post_data = json.dumps({'name': 'Test', 'value': 123})
        post_bytes = post_data.encode()

        request = (
            b"POST /api?action=create&debug=true HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n" +
            f"Content-Length: {len(post_bytes)}\r\n".encode() +
            b"Connection: close\r\n\r\n" +
            post_bytes
        )

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['query'], {'action': 'create', 'debug': 'true'})
        self.assertEqual(self.last_request['data'], {'name': 'Test', 'value': 123})

    # URL Path Tests

    def test_path_numeric_id(self):
        """Test path with numeric ID"""
        request = b"GET /api/users/123 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['path'], '/api/users/123')

    def test_path_with_extension(self):
        """Test path with file extension"""
        request = b"GET /files/document.pdf HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['path'], '/files/document.pdf')

    def test_path_url_encoded_spaces(self):
        """Test path with URL encoded spaces"""
        request = b"GET /path%20with%20spaces HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['path'], '/path with spaces')

    def test_path_special_characters(self):
        """Test path with special characters"""
        request = b"GET /special/%21%40%23%24 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

        self.send_request(request)
        time.sleep(0.1)

        self.assertIsNotNone(self.last_request)
        self.assertEqual(self.last_request['path'], '/special/!@#$')


if __name__ == '__main__':
    unittest.main()
