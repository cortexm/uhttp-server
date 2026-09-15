#!/usr/bin/env python3
"""Regression tests for the 2026-07-20 code review findings.

Driven through server.wait() only, so they stay valid on the selectors
branch as well.
"""
import os
import re
import select
import selectors
import socket
import tempfile
import time
import unittest

from uhttp import server as uhttp_server
from uhttp.server import EVENT_COMPLETE, EVENT_ERROR, EVENT_HEADERS


def connect(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', port))
    return sock


def drive(server, attempts=20, timeout=0.05):
    for _ in range(attempts):
        client = server.wait(timeout)
        if client is not None:
            return client
    return None


def read_interest_armed(server, connection):
    """True while the loop still wakes on this socket being readable.

    2.x drains the unwanted bytes instead, and has no selector - it is
    always watching, so the caller's readable check decides there.
    """
    selector = getattr(server, 'selector', None)
    if selector is None:
        return True
    for key in selector.get_map().values():
        if key.data is connection:
            return bool(key.events & selectors.EVENT_READ)
    return False
class TestResponseInProgressDrain(unittest.TestCase):
    """Bytes sent while a response is going out must not spin the loop."""

    PORT = 9700

    def test_unread_client_bytes_are_drained(self):
        temp_dir = tempfile.mkdtemp()
        path = os.path.join(temp_dir, 'big.bin')
        with open(path, 'wb') as handle:
            handle.write(b'X' * 200_000)
        server = uhttp_server.HttpServer(port=self.PORT, file_chunk_size=512)
        sock = connect(self.PORT)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client = drive(server)
            self.assertIsNotNone(client)
            client.respond_file(path)

            sock.sendall(b'garbage' * 100)
            time.sleep(0.1)
            for _ in range(10):
                server.wait(0.02)

            readable, _, _ = select.select([client.socket], [], [], 0)
            self.assertFalse(
                readable and read_interest_armed(server, client),
                "client bytes stay pending and READ stays armed -"
                " the loop busy-spins")
        finally:
            sock.close()
            server.close()
            try:
                os.unlink(path)
                os.rmdir(temp_dir)
            except OSError:
                pass


class TestAcceptLimit(unittest.TestCase):
    PORT = 9702

    def test_waiting_connections_never_exceed_the_limit(self):
        server = uhttp_server.HttpServer(port=self.PORT, max_waiting_clients=2)
        socks = []
        try:
            for _ in range(5):
                socks.append(connect(self.PORT))
                time.sleep(0.02)
                server.wait(0.05)
                self.assertLessEqual(
                    len(server._waiting_connections), 2,
                    "max_waiting_clients must be a hard limit")
        finally:
            for sock in socks:
                sock.close()
            server.close()


class TestPipelinedBodyIsAnEvent(unittest.TestCase):
    """Extra bytes after the body must not raise out of client.data."""

    PORT = 9703

    def test_extra_body_bytes_surface_as_event_error(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock = connect(self.PORT)
        try:
            sock.sendall(
                b"POST / HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 5\r\n\r\n")
            client = drive(server)
            self.assertEqual(client.event, EVENT_HEADERS)
            client.accept_body()
            sock.sendall(b"helloEXTRA")
            client = drive(server)
            self.assertEqual(client.event, EVENT_ERROR)
            self.assertIsNotNone(client.error)
        finally:
            sock.close()
            server.close()


class TestWebSocketHandshakeValidation(unittest.TestCase):
    PORT = 9704

    def _upgrade(self, port, method=b'GET', version=b'13'):
        sock = connect(port)
        sock.sendall(
            method + b" /ws HTTP/1.1\r\nHost: localhost\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: " + version + b"\r\n\r\n")
        return sock

    def test_wrong_version_is_rejected(self):
        # Same contract as the missing Sec-WebSocket-Key: the app decides
        # how to answer, so accept_websocket() raises.
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = self._upgrade(self.PORT, version=b'8')
        try:
            client = drive(server)
            self.assertIsNotNone(client)
            with self.assertRaises(uhttp_server.HttpErrorWithResponse):
                client.accept_websocket()
        finally:
            sock.close()
            server.close()

    def test_non_get_method_is_rejected(self):
        server = uhttp_server.HttpServer(port=self.PORT + 1)
        sock = self._upgrade(self.PORT + 1, method=b'POST')
        try:
            client = drive(server)
            self.assertIsNotNone(client)
            with self.assertRaises(uhttp_server.HttpErrorWithResponse):
                client.accept_websocket()
        finally:
            sock.close()
            server.close()


class TestForwardedForJoin(unittest.TestCase):
    PORT = 9706

    def test_duplicate_forwarded_for_headers_are_joined(self):
        server = uhttp_server.HttpServer(
            port=self.PORT, trusted_proxies=['127.0.0.1'])
        sock = connect(self.PORT)
        try:
            sock.sendall(
                b"GET / HTTP/1.1\r\nHost: localhost\r\n"
                b"X-Forwarded-For: 10.0.0.1\r\n"
                b"X-Forwarded-For: 10.0.0.2\r\n\r\n")
            client = drive(server)
            self.assertIsNotNone(client)
            self.assertIn('10.0.0.1', client.remote_addresses)
            self.assertIn('10.0.0.2', client.remote_addresses)
            self.assertEqual(client.remote_address, '10.0.0.1')
        finally:
            sock.close()
            server.close()


class TestDuplicateCookieHeaders(unittest.TestCase):
    """Cookies use '; ', so combining them with a comma corrupts them."""

    PORT = 9711

    def test_repeated_cookie_headers_stay_parsable(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = connect(self.PORT)
        try:
            sock.sendall(
                b"GET / HTTP/1.1\r\nHost: localhost\r\n"
                b"Cookie: a=1\r\nCookie: b=2\r\n\r\n")
            client = drive(server)
            self.assertIsNotNone(client)
            self.assertEqual(client.cookies, {'a': '1', 'b': '2'})
        finally:
            sock.close()
            server.close()


class TestObsFold(unittest.TestCase):
    PORT = 9707

    def test_folded_header_line_is_rejected(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = connect(self.PORT)
        try:
            # A folded line containing a colon is silently taken as a new
            # header; RFC 7230 wants a deterministic 400 either way.
            sock.sendall(
                b"GET / HTTP/1.1\r\nHost: localhost\r\n"
                b"X-Long: first\r\n  x-smuggled: yes\r\n\r\n")
            drive(server)
            sock.settimeout(1)
            self.assertIn(b'400', sock.recv(4096))
        finally:
            sock.close()
            server.close()


class TestContentTypeCase(unittest.TestCase):
    PORT = 9708

    def test_uppercase_json_content_type_is_parsed(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = connect(self.PORT)
        try:
            body = b'{"a": 1}'
            sock.sendall(
                b"POST / HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: Application/JSON\r\n"
                b"Content-Length: %d\r\n\r\n" % len(body) + body)
            client = drive(server)
            self.assertEqual(client.data, {'a': 1})
        finally:
            sock.close()
            server.close()


class TestIsLoadedIsBool(unittest.TestCase):
    PORT = 9709

    def test_is_loaded_is_a_bool(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = connect(self.PORT)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client = drive(server)
            self.assertIs(client.is_loaded, True)
            client.respond('ok')
            self.assertIs(client.is_loaded, False)
        finally:
            sock.close()
            server.close()

    def test_is_loaded_is_false_before_the_request_line(self):
        server = uhttp_server.HttpServer(port=self.PORT + 1)
        sock = connect(self.PORT + 1)
        try:
            time.sleep(0.05)
            server.wait(0.05)  # accepts, nothing sent yet
            self.assertTrue(server._waiting_connections)
            # Returned None instead of False: `_method and (...)`
            self.assertIs(server._waiting_connections[0].is_loaded, False)
        finally:
            sock.close()
            server.close()


class TestPartialInitClose(unittest.TestCase):
    def test_close_on_partially_built_connection(self):
        connection = object.__new__(uhttp_server.HttpConnection)
        connection.close()  # must not raise AttributeError


class TestWebSocketSendCap(unittest.TestCase):
    """A stalled peer must not grow the WebSocket send buffer without limit."""

    PORT = 9710

    STALLS = (
        ('HttpConnection', '_flush_send_buffer', lambda self: False),
        ('WebSocket', '_try_flush_send', lambda self: None))

    def _stall_sending(self):
        """Freeze whichever flush path this version uses."""
        saved = []
        for name, attr, stub in self.STALLS:
            owner = getattr(uhttp_server, name)
            if attr in vars(owner):
                saved.append((owner, attr, getattr(owner, attr)))
                setattr(owner, attr, stub)
        self.assertTrue(saved, "no flush path to stall")
        return saved

    def test_send_reports_the_buffer_cap(self):
        saved = self._stall_sending()
        server = uhttp_server.HttpServer(
            port=self.PORT, max_send_buffer_size=4096)
        sock = connect(self.PORT)
        try:
            sock.sendall(
                b"GET /ws HTTP/1.1\r\nHost: localhost\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                b"Sec-WebSocket-Version: 13\r\n\r\n")
            client = drive(server)
            ws = client.accept_websocket()
            results = []
            for _ in range(200):
                results.append(ws.send(b'x' * 256))
                if results[-1] is False:
                    break
            self.assertIn(True, results, "send() must report success")
            self.assertIs(
                results[-1], False, "send() must report the buffer cap")
            self.assertLess(
                len(results), 200, "send() never tripped the cap")
        finally:
            for owner, attr, value in saved:
                setattr(owner, attr, value)
            sock.close()
            server.close()


if __name__ == '__main__':
    unittest.main()


class SlowSocket:
    """Accepts at most `limit` bytes per send, forcing partial writes."""

    def __init__(self, sock, limit=1024):
        self._sock = sock
        self._limit = limit

    def send(self, data):
        return self._sock.send(bytes(data)[:self._limit])

    def __getattr__(self, name):
        return getattr(self._sock, name)


class TestSendBufferDoesNotReallocate(unittest.TestCase):
    """A partial send must consume the buffer, not rebuild it."""

    PORT = 9712

    def test_partial_sends_reuse_one_buffer(self):
        payload = b'y' * 60_000
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = connect(self.PORT)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client = drive(server)
            held = client._send_buffer          # before anything is queued
            client._socket = SlowSocket(client._socket)
            client.respond(payload)

            received = bytearray()
            sock.settimeout(0.2)
            deadline = time.time() + 20
            while len(received) < len(payload) and time.time() < deadline:
                server.wait(0.01)
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    continue
                if not chunk:
                    break
                received.extend(chunk)

            body = len(received) - received.index(b'\r\n\r\n') - 4
            self.assertEqual(body, len(payload), "body was truncated")
            self.assertIs(
                client._send_buffer, held,
                "the send buffer object was rebuilt on a partial send")
        finally:
            sock.close()
            server.close()


class TestRespondLargerThanTheCap(unittest.TestCase):
    """respond() writes header+body as one initial write, so the cap
    must not reject a body bigger than it."""

    PORT = 9713

    def test_body_over_the_cap_is_accepted(self):
        payload = b'z' * 50_000
        server = uhttp_server.HttpServer(
            port=self.PORT, max_send_buffer_size=4096)
        sock = connect(self.PORT)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client = drive(server)
            client.respond(payload)  # must not raise
            received = bytearray()
            sock.settimeout(0.2)
            deadline = time.time() + 10
            while len(received) < len(payload) and time.time() < deadline:
                server.wait(0.01)
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    continue
                if not chunk:
                    break
                received.extend(chunk)
            self.assertEqual(
                len(received) - received.index(b'\r\n\r\n') - 4, len(payload))
        finally:
            sock.close()
            server.close()


class TestMicroPythonPortability(unittest.TestCase):
    """Source-level guards for constructs CPython accepts and MicroPython does not.

    A behavioural test cannot catch these: the code runs fine here and only
    fails on-device, where the exception lands *after* the bytes are already
    on the wire - so a client still sees a valid response and only the next
    request reveals that the server loop died.
    """

    def test_no_bytearray_slice_deletion(self):
        # MicroPython: "'bytearray' object doesn't support item deletion".
        # Use `buf[:] = buf[n:]` instead - it shrinks in place and keeps
        # the object, verified on an ESP32-C6.
        with open(uhttp_server.__file__) as handle:
            source = handle.read()
        offenders = [
            line.strip() for line in source.splitlines()
            if re.search(r'\bdel\s+[\w.]+\[[^\]]*:', line)]
        self.assertEqual(
            offenders, [], "slice deletion is not supported on MicroPython")
