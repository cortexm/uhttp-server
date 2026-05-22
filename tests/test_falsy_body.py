#!/usr/bin/env python3
"""
Regression tests for is_loaded falsy-body bug.

When a request body parses to a falsy Python value (JSON {}, [], null, 0,
false, ""; or an empty form-urlencoded body), HttpConnection.is_loaded
must still report True so the server hands the request off to the
application. Previously is_loaded did a truthy check on _data, which
made these requests hang until keep-alive timeout.
"""
import json
import socket
import threading
import time
import unittest

from uhttp import server as uhttp_server


class TestFalsyBody(unittest.TestCase):
    """Server must dispatch requests with falsy parsed bodies."""

    PORT = 9997
    # Short timeout so a regression of the bug fails the test quickly
    # (and so a passing test runs fast).
    KEEP_ALIVE_TIMEOUT = 1.0

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, keep_alive_timeout=cls.KEEP_ALIVE_TIMEOUT)
        cls.last_data = None
        cls.stop = False

        def run_server():
            while not cls.stop:
                client = cls.server.wait(timeout=0.05)
                if client:
                    cls.last_data = client.data
                    client.respond({'echo_type': type(client.data).__name__})

        cls.thread = threading.Thread(target=run_server, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.stop = True
        cls.thread.join(timeout=2.0)
        cls.server.close()

    def setUp(self):
        type(self).last_data = None

    def _send(self, body_bytes, content_type):
        """Send POST with given body; return (status_line, parsed_json or None).

        Uses a 2s socket timeout — well above KEEP_ALIVE_TIMEOUT — so that if
        the server hangs on is_loaded the test fails with empty response,
        not a hang.
        """
        request = (
            b"POST /api HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: " + content_type.encode() + b"\r\n"
            b"Content-Length: " + str(len(body_bytes)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n"
            + body_bytes
        )
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(request)

        response = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        finally:
            sock.close()

        if not response:
            return None, None
        status_line = response.split(b"\r\n", 1)[0]
        body = response.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in response else b""
        parsed = json.loads(body.decode()) if body else None
        return status_line, parsed

    # JSON falsy values
    def test_json_empty_object(self):
        start = time.time()
        status, parsed = self._send(b"{}", "application/json")
        elapsed = time.time() - start
        self.assertIsNotNone(status, "server did not respond (likely hung on is_loaded)")
        self.assertIn(b"200", status)
        self.assertEqual(parsed, {'echo_type': 'dict'})
        self.assertEqual(self.last_data, {})
        self.assertLess(elapsed, self.KEEP_ALIVE_TIMEOUT,
                        "response took longer than keep-alive timeout — request was stuck")

    def test_json_empty_array(self):
        status, parsed = self._send(b"[]", "application/json")
        self.assertIsNotNone(status)
        self.assertIn(b"200", status)
        self.assertEqual(parsed, {'echo_type': 'list'})
        self.assertEqual(self.last_data, [])

    def test_json_null(self):
        status, parsed = self._send(b"null", "application/json")
        self.assertIsNotNone(status)
        self.assertIn(b"200", status)
        self.assertEqual(parsed, {'echo_type': 'NoneType'})
        self.assertIsNone(self.last_data)

    def test_json_zero(self):
        status, parsed = self._send(b"0", "application/json")
        self.assertIsNotNone(status)
        self.assertIn(b"200", status)
        self.assertEqual(parsed, {'echo_type': 'int'})
        self.assertEqual(self.last_data, 0)

    def test_json_false(self):
        status, parsed = self._send(b"false", "application/json")
        self.assertIsNotNone(status)
        self.assertIn(b"200", status)
        self.assertEqual(parsed, {'echo_type': 'bool'})
        self.assertEqual(self.last_data, False)

    def test_json_empty_string(self):
        status, parsed = self._send(b'""', "application/json")
        self.assertIsNotNone(status)
        self.assertIn(b"200", status)
        self.assertEqual(parsed, {'echo_type': 'str'})
        self.assertEqual(self.last_data, "")


if __name__ == '__main__':
    unittest.main()
