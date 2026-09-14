#!/usr/bin/env python3
"""Regression tests for the selectors-based event loop (v3)."""
import selectors
import socket
import time
import unittest
from uhttp import server as uhttp_server
from uhttp.server import HttpError
from uhttp.server import (
    EVENT_COMPLETE, EVENT_HEADERS, EVENT_WS_CLOSE, EVENT_WS_MESSAGE,
    EVENT_WS_REQUEST, HttpConnection)

WS_UPGRADE = (
    b"GET /ws HTTP/1.1\r\nHost: localhost\r\n"
    b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
    b"Sec-WebSocket-Version: 13\r\n\r\n")


def _frame(opcode, payload, rsv=0):
    """Masked client frame; payload < 126 bytes."""
    mask = b'\x37\xfa\x21\x3d'
    frame = bytearray([0x80 | rsv | opcode, 0x80 | len(payload)])
    frame.extend(mask)
    frame.extend(b ^ mask[i & 3] for i, b in enumerate(payload))
    return bytes(frame)


def _connect(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', port))
    return sock


def _drive(server, attempts=20, timeout=0.05):
    """Call wait() until it yields a connection."""
    for _ in range(attempts):
        client = server.wait(timeout)
        if client is not None:
            return client
    return None


class TestNextErrorMapping(unittest.TestCase):
    PORT = 9980

    def test_protocol_error_in_next_becomes_ws_close(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock = _connect(self.PORT)
        try:
            sock.sendall(WS_UPGRADE)
            client = _drive(server)
            self.assertEqual(client.event, EVENT_WS_REQUEST)
            client.accept_websocket()
            # Valid frame followed by one with RSV bits set, in one segment.
            sock.sendall(_frame(0x1, b'a') + _frame(0x1, b'b', rsv=0x70))
            client = _drive(server)
            self.assertEqual(client.event, EVENT_WS_MESSAGE)
            resumed = server.wait(0.1)
            self.assertIs(resumed, client)
            self.assertEqual(client.event, EVENT_WS_CLOSE)
        finally:
            sock.close()
            server.close()


class TestNextAfterHandoff(unittest.TestCase):
    PORT = 9981

    def test_next_is_inert_after_non_event_ws_handoff(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = _connect(self.PORT)
        try:
            sock.sendall(WS_UPGRADE + _frame(0x1, b'a'))
            client = _drive(server)
            self.assertTrue(client.is_websocket_request)
            ws = client.accept_websocket()
            self.assertIsNotNone(ws)
            self.assertFalse(client.next())
            self.assertIsNone(
                client.handle_event(client.socket, selectors.EVENT_READ))
        finally:
            sock.close()
            server.close()


class TestWaitOnSharedSelector(unittest.TestCase):
    PORT = 9982

    def test_wait_ignores_foreign_keys(self):
        sel = selectors.DefaultSelector()
        server = uhttp_server.HttpServer(port=self.PORT, selector=sel)
        a, b = socket.socketpair()
        calls = []
        try:
            sel.register(a, selectors.EVENT_READ, lambda *args: calls.append(1))
            b.send(b'x')
            self.assertIsNone(server.wait(0.1))
            self.assertEqual(calls, [])
            self.assertIn(a, [k.fileobj for k in sel.get_map().values()])
        finally:
            a.close()
            b.close()
            server.close()
            sel.close()


class TestClose(unittest.TestCase):
    PORT = 9983

    def test_close_closes_connections_and_wait_sleeps(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = _connect(self.PORT)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client = _drive(server)
            self.assertIsNotNone(client)
            server.close()
            self.assertIsNone(client.socket)
            self.assertEqual(server._waiting_connections, [])
            start = time.time()
            self.assertIsNone(server.wait(0.2))
            self.assertGreaterEqual(time.time() - start, 0.15)
        finally:
            sock.close()

    def test_handle_event_on_closed_server(self):
        server = uhttp_server.HttpServer(port=self.PORT + 1)
        listener = server.socket
        server.close()
        self.assertIsNone(server.handle_event(listener, selectors.EVENT_READ))


class TestEvictionLeak(unittest.TestCase):
    PORT = 9985

    def test_evicted_connection_with_stalled_408_is_closed(self):
        original = uhttp_server.HttpConnection._flush_send_buffer
        uhttp_server.HttpConnection._flush_send_buffer = lambda self: False
        server = uhttp_server.HttpServer(
            port=self.PORT, max_waiting_clients=1)
        socks = []
        try:
            socks.append(_connect(self.PORT))
            time.sleep(0.05)
            server.wait(0.1)
            self.assertEqual(len(server._waiting_connections), 1)
            evicted = server._waiting_connections[0]
            socks.append(_connect(self.PORT))
            time.sleep(0.05)
            server.wait(0.1)
            self.assertNotIn(evicted, server._waiting_connections)
            self.assertIsNone(evicted.socket)
            registered = [k.data for k in server.selector.get_map().values()]
            self.assertNotIn(evicted, registered)
        finally:
            uhttp_server.HttpConnection._flush_send_buffer = original
            for sock in socks:
                sock.close()
            server.close()


class TestNextBodyComplete(unittest.TestCase):
    PORT = 9986

    def test_next_does_not_reemit_event_complete(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock = _connect(self.PORT)
        try:
            sock.sendall(
                b"POST / HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 5\r\n\r\n")
            client = _drive(server)
            self.assertEqual(client.event, EVENT_HEADERS)
            client.accept_body()
            sock.sendall(b"hello")
            client = _drive(server)
            self.assertEqual(client.event, EVENT_COMPLETE)
            self.assertFalse(client.next())
            self.assertIsNone(server.wait(0.05))
        finally:
            sock.close()
            server.close()


class TestHandoffKeepsUnsentTail(unittest.TestCase):
    PORT = 9987

    def test_websocket_takes_over_unflushed_101(self):
        original = uhttp_server.HttpConnection._flush_send_buffer
        uhttp_server.HttpConnection._flush_send_buffer = lambda self: False
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = _connect(self.PORT)
        try:
            sock.sendall(WS_UPGRADE)
            client = _drive(server)
            ws = client.accept_websocket()  # 101 stays buffered (stalled)
            self.assertIn(
                b'101 Switching Protocols', bytes(client._send_buffer))
            self.assertTrue(ws.send_pending)
            uhttp_server.HttpConnection._flush_send_buffer = original
            client.try_send()
            sock.settimeout(1)
            self.assertIn(b'101 Switching Protocols', sock.recv(4096))
        finally:
            uhttp_server.HttpConnection._flush_send_buffer = original
            sock.close()
            server.close()


class TestInterestModifyFailure(unittest.TestCase):
    PORT = 9988

    def test_connection_closed_when_modify_fails(self):
        original = uhttp_server.HttpConnection._flush_send_buffer
        uhttp_server.HttpConnection._flush_send_buffer = lambda self: False
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = _connect(self.PORT)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client = _drive(server)

            def boom(*args, **kwargs):
                raise OSError("modify failed")
            server.selector.modify = boom
            client.respond(b'x' * 16)  # stalled -> arms WRITE -> modify fails
            self.assertIsNone(client.socket)
            self.assertNotIn(client, server._waiting_connections)
        finally:
            uhttp_server.HttpConnection._flush_send_buffer = original
            sock.close()
            server.close()


class TestRegistrationOwnership(unittest.TestCase):
    """The connection owns its selector registration (_interest)."""
    PORT = 9990

    def _registered(self, server, client):
        return client.socket in [
            k.fileobj for k in server.selector.get_map().values()]

    def test_close_unregisters(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = _connect(self.PORT)
        try:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            client = _drive(server)
            self.assertEqual(client._interest, selectors.EVENT_READ)
            self.assertTrue(self._registered(server, client))
            client.close()
            self.assertIsNone(client._interest)
            self.assertEqual(len(server.selector.get_map()), 1)  # listener
        finally:
            sock.close()
            server.close()

    def test_handoff_unregisters(self):
        server = uhttp_server.HttpServer(port=self.PORT + 1)
        sock = _connect(self.PORT + 1)
        try:
            sock.sendall(WS_UPGRADE)
            client = _drive(server)
            client.accept_websocket()
            self.assertIsNone(client._interest)
            self.assertFalse(self._registered(server, client))
        finally:
            sock.close()
            server.close()


class TestMaintenanceRateLimit(unittest.TestCase):
    PORT = 9989

    def test_scan_is_rate_limited(self):
        class Counting(list):
            scans = 0

            def __iter__(self):
                Counting.scans += 1
                return super().__iter__()

        server = uhttp_server.HttpServer(port=self.PORT, keep_alive_timeout=1)
        server._waiting_connections = Counting()
        try:
            server.maintenance()
            server.maintenance()
            self.assertEqual(Counting.scans, 1)
            time.sleep(0.6)
            server.maintenance()
            self.assertEqual(Counting.scans, 2)
        finally:
            server.close()


class TestWebSocketFacade(unittest.TestCase):
    """The non-event WebSocket speaks the v3 owner protocol.

    Before v3 it kept its own select.select() loop and its own send path,
    so it could not join a shared selector and had no send buffer cap.
    """

    PORT = 9991

    def _upgrade(self, server, sock, selector=None):
        sock.sendall(WS_UPGRADE)
        client = _drive(server)
        self.assertTrue(client.is_websocket_request)
        return client.accept_websocket(selector=selector)

    def test_v2_select_api_is_gone(self):
        server = uhttp_server.HttpServer(port=self.PORT)
        sock = _connect(self.PORT)
        try:
            ws = self._upgrade(server, sock)
            for name in ('read_sockets', 'write_sockets', 'process_events'):
                self.assertFalse(hasattr(ws, name), f"{name} must be removed")
            self.assertIsNotNone(ws.selector)
        finally:
            sock.close()
            server.close()

    def test_joins_a_shared_selector(self):
        sel = selectors.DefaultSelector()
        port = self.PORT + 1
        server = uhttp_server.HttpServer(port=port, selector=sel)
        sock = _connect(port)
        try:
            sock.sendall(WS_UPGRADE)
            conn = None
            deadline = time.time() + 2
            while conn is None and time.time() < deadline:
                for key, mask in sel.select(0.05):
                    ready = key.data.handle_event(key.fileobj, mask)
                    if isinstance(ready, HttpConnection):
                        conn = ready
            self.assertIsNotNone(conn)
            ws = conn.accept_websocket(selector=sel)
            self.assertIs(ws.selector, sel)

            sock.sendall(_frame(0x1, b'hi'))
            got = None
            deadline = time.time() + 2
            while got is None and time.time() < deadline:
                for key, mask in sel.select(0.05):
                    ready = key.data.handle_event(key.fileobj, mask)
                    if ready is ws and ws.event == EVENT_WS_MESSAGE:
                        got = ws.read_buffer()
            self.assertEqual(got, b'hi')
        finally:
            sock.close()
            server.close()
            sel.close()

    def test_wait_refuses_a_shared_selector(self):
        sel = selectors.DefaultSelector()
        port = self.PORT + 2
        server = uhttp_server.HttpServer(port=port, selector=sel)
        sock = _connect(port)
        try:
            sock.sendall(WS_UPGRADE)
            conn = None
            deadline = time.time() + 2
            while conn is None and time.time() < deadline:
                for key, mask in sel.select(0.05):
                    ready = key.data.handle_event(key.fileobj, mask)
                    if isinstance(ready, HttpConnection):
                        conn = ready
            ws = conn.accept_websocket(selector=sel)
            with self.assertRaises(HttpError):
                ws.wait(0.1)
        finally:
            sock.close()
            server.close()
            sel.close()

    def test_send_is_capped_by_the_send_buffer(self):
        original = uhttp_server.HttpConnection._flush_send_buffer
        uhttp_server.HttpConnection._flush_send_buffer = lambda self: False
        server = uhttp_server.HttpServer(
            port=self.PORT + 3, max_send_buffer_size=4096)
        sock = _connect(self.PORT + 3)
        try:
            ws = self._upgrade(server, sock)
            sent = True
            for _ in range(200):
                sent = ws.send(b'x' * 256)
                if not sent:
                    break
            self.assertFalse(sent, "send() must report the buffer cap")
        finally:
            uhttp_server.HttpConnection._flush_send_buffer = original
            sock.close()
            server.close()

    def test_next_drains_frames_from_one_recv(self):
        server = uhttp_server.HttpServer(port=self.PORT + 4)
        sock = _connect(self.PORT + 4)
        try:
            ws = self._upgrade(server, sock)
            sock.sendall(_frame(0x1, b'a') + _frame(0x1, b'b'))
            self.assertEqual(ws.wait(1.0), EVENT_WS_MESSAGE)
            self.assertEqual(ws.read_buffer(), b'a')
            # The second frame came in the same recv: select() will not
            # report it again, so next() must yield it.
            self.assertTrue(ws.next())
            self.assertEqual(ws.event, EVENT_WS_MESSAGE)
            self.assertEqual(ws.read_buffer(), b'b')
            self.assertFalse(ws.next())
        finally:
            sock.close()
            server.close()

    def test_blocking_wait_still_works(self):
        server = uhttp_server.HttpServer(port=self.PORT + 5)
        sock = _connect(self.PORT + 5)
        try:
            ws = self._upgrade(server, sock)
            sock.sendall(_frame(0x1, b'ping-me'))
            self.assertEqual(ws.wait(1.0), EVENT_WS_MESSAGE)
            self.assertTrue(ws.ws_is_text)
            self.assertEqual(ws.read_buffer(), b'ping-me')
            self.assertTrue(ws.send('pong'))
            self.assertFalse(ws.is_closed)
            ws.close()
            self.assertTrue(ws.is_closed)
        finally:
            sock.close()
            server.close()


if __name__ == '__main__':
    unittest.main()
