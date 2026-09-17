#!/usr/bin/env python3
"""Backpressure: pause_reading()/resume_reading() stop and resume socket reads.

Not reading a socket lets its kernel buffer fill so TCP stalls the peer. These
tests drive the observable state machine (selector interest, event delivery,
the stuck-consumer guard) rather than TCP window internals.
"""
import socket
import time
import unittest
from uhttp import server as uhttp_server
from uhttp.server import (
    EVENT_COMPLETE, EVENT_DATA, EVENT_HEADERS, EVENT_WS_MESSAGE)

WS_UPGRADE = (
    b"GET /ws HTTP/1.1\r\nHost: localhost\r\n"
    b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
    b"Sec-WebSocket-Version: 13\r\n\r\n")


def _frame(opcode, payload):
    """Masked client text/binary frame; payload < 126 bytes."""
    mask = b'\x37\xfa\x21\x3d'
    frame = bytearray([0x80 | opcode, 0x80 | len(payload)])
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


def _silent(server, attempts=4, timeout=0.05):
    """True if wait() yields nothing across attempts (delivery is paused)."""
    for _ in range(attempts):
        if server.wait(timeout) is not None:
            return False
    return True


class TestPauseWebSocket(unittest.TestCase):
    PORT = 9974

    def _accept_ws(self, server):
        sock = _connect(self.PORT)
        sock.sendall(WS_UPGRADE)
        client = _drive(server)
        client.accept_websocket()
        return sock, client

    def test_pause_gates_delivery_resume_releases_it(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock, client = self._accept_ws(server)
        try:
            sock.sendall(_frame(0x1, b'one'))
            self.assertIs(_drive(server), client)
            self.assertEqual(client.event, EVENT_WS_MESSAGE)
            self.assertEqual(client.read_buffer(), b'one')

            client.pause_reading()
            self.assertIsNone(client._interest)  # unregistered from selector

            sock.sendall(_frame(0x1, b'two'))
            self.assertTrue(_silent(server))  # not read while paused

            client.resume_reading()
            self.assertIs(_drive(server), client)
            self.assertEqual(client.event, EVENT_WS_MESSAGE)
            self.assertEqual(client.read_buffer(), b'two')
        finally:
            sock.close()
            server.close()

    def test_send_still_works_while_read_paused(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock, client = self._accept_ws(server)
        try:
            sock.settimeout(1.0)
            resp = b""
            while b"\r\n\r\n" not in resp:            # drain the 101 handshake
                resp += sock.recv(256)
            frame = resp.partition(b"\r\n\r\n")[2]

            client.pause_reading()
            self.assertTrue(client.ws_send('hi'))     # egress works while paused
            server.wait(0.05)                          # flush any buffered write
            while len(frame) < 4:
                frame += sock.recv(64)
            self.assertEqual(frame[0], 0x81)          # FIN | text
            self.assertIn(b'hi', frame)
        finally:
            sock.close()
            server.close()

    def test_guard_closes_paused_connection_past_deadline(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock, client = self._accept_ws(server)
        try:
            client.pause_reading(timeout=0.05)
            time.sleep(0.12)
            server._last_maintenance = 0  # bypass the scan interval
            server.maintenance()
            self.assertIsNone(client._socket)
            self.assertNotIn(client, server._waiting_connections)
        finally:
            sock.close()
            server.close()

    def test_zero_timeout_disables_the_guard(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock, client = self._accept_ws(server)
        try:
            client.pause_reading(timeout=0)
            client._last_activity = time.time() - 3600  # ancient
            server._last_maintenance = 0
            server.maintenance()
            self.assertIsNotNone(client._socket)  # app owns the lifecycle
        finally:
            sock.close()
            server.close()


class TestPauseUpload(unittest.TestCase):
    PORT = 9975

    def test_pause_gates_body_resume_completes(self):
        server = uhttp_server.HttpServer(port=self.PORT, event_mode=True)
        sock = _connect(self.PORT)
        try:
            sock.sendall(
                b"POST /u HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 10\r\n\r\nabc")
            client = _drive(server)
            self.assertEqual(client.event, EVENT_HEADERS)
            client.accept_body_streaming()

            body = bytearray()
            self.assertIs(_drive(server), client)
            self.assertEqual(client.event, EVENT_DATA)
            body += client.read_buffer()

            client.pause_reading()
            self.assertIsNone(client._interest)
            sock.sendall(b"defghij")           # the remaining 7 bytes
            self.assertTrue(_silent(server))    # buffered by TCP, not read

            client.resume_reading()
            while client.event != EVENT_COMPLETE:
                self.assertIs(_drive(server), client)
                chunk = client.read_buffer()
                if chunk:
                    body += chunk
            self.assertEqual(bytes(body), b'abcdefghij')
        finally:
            sock.close()
            server.close()


if __name__ == '__main__':
    unittest.main()
