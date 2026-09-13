#!/usr/bin/env python3
"""
Test UTF-8 encoding of responses (bodies, header/cookie values, SSE).

Why UTF-8 and not ASCII on every response path:
- json.dumps() on MicroPython does NOT escape non-ASCII (no ensure_ascii), so
  an accented value in a JSON body would raise UnicodeError. On CPython
  ensure_ascii hides it, so this bug is MicroPython-only for JSON bodies.
- _send() handles UTF-8 text: SSE event fields and, for empty-body responses,
  the whole header block (non-ASCII header/cookie values).
- respond() with a body encodes the header block on a separate branch, which
  must match _send() or non-ASCII header/cookie values crash there instead.

UnicodeError is not an OSError, so the streaming send_*() guards do not catch
it -- it escapes into the application. These tests lock UTF-8 on every path.
Encoding sites in uhttp/server.py: encode_response_data(), _send(), respond().
"""
import unittest
import socket
import time
import json
import threading
from uhttp import server as uhttp_server

# Slovak text with diacritics, exercises non-ASCII on every path
TEXT = 'Košice ďáčé'


class TestUtf8Response(unittest.TestCase):
    """Response paths must encode non-ASCII content as UTF-8, not crash"""

    server = None
    server_thread = None
    PORT = 9971

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            while cls.server:
                try:
                    client = cls.server.wait(timeout=0.1)
                    if not client:
                        continue
                    path = client.path
                    if path == '/json':
                        client.respond({'mesto': TEXT})
                    elif path == '/str':
                        client.respond(TEXT)
                    elif path == '/header':
                        # body present -> exercises respond() header-with-body
                        client.respond(
                            data=b'ok', headers={'X-Label': TEXT})
                    elif path == '/header-nobody':
                        client.respond(
                            status=204, headers={'X-Label': TEXT})
                    elif path == '/cookie':
                        client.respond(
                            data=b'ok', cookies={'label': TEXT})
                    elif path == '/sse':
                        if client.response_stream():
                            client.send_event(data=TEXT)
                            client.response_stream_end()
                    else:
                        client.respond('not found', status=404)
                except Exception:
                    # one crashing handler must not kill the server loop
                    pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def get_raw(self, path):
        """Send GET and return the full raw response bytes"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        sock.sendall(
            f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode('ascii'))
        response = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        except socket.timeout:
            pass
        sock.close()
        return response

    def split(self, raw):
        """Return (headers_bytes, body_bytes)"""
        parts = raw.split(b"\r\n\r\n", 1)
        return parts[0], (parts[1] if len(parts) > 1 else b"")

    def test_json_body_utf8(self):
        """respond(dict) with diacritics -> UTF-8 JSON body"""
        _, body = self.split(self.get_raw('/json'))
        self.assertEqual(json.loads(body.decode('utf-8')), {'mesto': TEXT})

    def test_str_body_utf8(self):
        """respond(str) with diacritics -> UTF-8 body"""
        _, body = self.split(self.get_raw('/str'))
        self.assertEqual(body.decode('utf-8'), TEXT)

    def test_header_value_utf8_with_body(self):
        """respond(data=..., headers={non-ASCII}) must not crash the response"""
        headers, body = self.split(self.get_raw('/header'))
        self.assertIn(b'200', headers)
        self.assertIn(TEXT.encode('utf-8'), headers)
        self.assertEqual(body, b'ok')

    def test_header_value_utf8_no_body(self):
        """respond(status, headers={non-ASCII}) without body -> UTF-8 header"""
        headers, _ = self.split(self.get_raw('/header-nobody'))
        self.assertIn(b'204', headers)
        self.assertIn(TEXT.encode('utf-8'), headers)

    def test_cookie_value_utf8(self):
        """respond(cookies={non-ASCII}) with body -> UTF-8 Set-Cookie"""
        headers, body = self.split(self.get_raw('/cookie'))
        self.assertIn(b'200', headers)
        self.assertIn(TEXT.encode('utf-8'), headers)
        self.assertEqual(body, b'ok')

    def test_sse_event_utf8(self):
        """send_event(data=non-ASCII) -> UTF-8 in the stream"""
        raw = self.get_raw('/sse')
        self.assertIn(b'200', raw)
        self.assertIn(b'data: ' + TEXT.encode('utf-8'), raw)


if __name__ == '__main__':
    unittest.main()
