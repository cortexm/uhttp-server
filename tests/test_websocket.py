"""Integration tests for WebSocket support

Tests use real sockets with server running in a thread.
"""
import unittest
import socket
import time
import struct
import hashlib
import binascii
import threading
from uhttp import server as uhttp_server
from uhttp.server import (
    EVENT_REQUEST, EVENT_WS_REQUEST, EVENT_WS_MESSAGE,
    EVENT_WS_CHUNK_FIRST, EVENT_WS_CHUNK_NEXT, EVENT_WS_CHUNK_LAST,
    EVENT_WS_PING, EVENT_WS_CLOSE,
    WS_OPCODE_TEXT, WS_OPCODE_BINARY, WS_OPCODE_CLOSE,
    WS_OPCODE_PING, WS_OPCODE_PONG, WS_OPCODE_CONTINUATION,
    _WS_MAGIC, _ws_build_frame,
)


def build_masked_frame(opcode, payload, fin=True, mask=b'\x37\xfa\x21\x3d'):
    """Build a masked WebSocket frame (client-side)"""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    frame = bytearray()
    frame.append((0x80 if fin else 0) | opcode)
    length = len(payload)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.append((length >> 8) & 0xFF)
        frame.append(length & 0xFF)
    else:
        frame.append(0x80 | 127)
        for i in range(7, -1, -1):
            frame.append((length >> (8 * i)) & 0xFF)
    frame.extend(mask)
    masked = bytearray(payload)
    for i in range(len(masked)):
        masked[i] ^= mask[i & 3]
    frame.extend(masked)
    return bytes(frame)


def recv_frame(sock):
    """Receive and parse a WebSocket frame from socket"""
    header = b''
    while len(header) < 2:
        header += sock.recv(2 - len(header))
    b0, b1 = header[0], header[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        ext = b''
        while len(ext) < 2:
            ext += sock.recv(2 - len(ext))
        length = (ext[0] << 8) | ext[1]
    elif length == 127:
        ext = b''
        while len(ext) < 8:
            ext += sock.recv(8 - len(ext))
        length = 0
        for i in range(8):
            length = (length << 8) | ext[i]
    if masked:
        mask = b''
        while len(mask) < 4:
            mask += sock.recv(4 - len(mask))
    payload = b''
    while len(payload) < length:
        payload += sock.recv(length - len(payload))
    if masked:
        payload = bytearray(payload)
        for i in range(length):
            payload[i] ^= mask[i & 3]
        payload = bytes(payload)
    return fin, opcode, payload


def ws_upgrade(sock, path='/ws'):
    """Perform WebSocket handshake on connected socket"""
    key = 'dGhlIHNhbXBsZSBub25jZQ=='
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: localhost\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()
    sock.sendall(request)
    response = b''
    while b'\r\n\r\n' not in response:
        response += sock.recv(1024)
    assert b'101' in response, f"Expected 101, got: {response}"
    expected_accept = binascii.b2a_base64(
        hashlib.sha1(key.encode() + _WS_MAGIC).digest()
    ).strip().decode()
    assert expected_accept.encode() in response, "Invalid accept key"
    return response


# --- Event mode tests ---

class TestWebSocketEventMode(unittest.TestCase):
    """Test WebSocket in event mode"""

    PORT = 9970
    server = None
    server_thread = None
    ws_events = []
    ws_connections = {}
    pending_actions = []

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, event_mode=True,
            max_ws_message_length=1024)

        def run_server():
            try:
                while True:
                    srv = cls.server
                    if srv is None:
                        break

                    # Execute pending actions in server thread
                    while cls.pending_actions:
                        action = cls.pending_actions.pop(0)
                        action()

                    client = srv.wait(timeout=0.5)
                    if not client:
                        continue

                    if client.event == EVENT_WS_REQUEST:
                        client.accept_websocket()
                        cls.ws_connections[id(client)] = client

                    elif client.event == EVENT_REQUEST:
                        client.respond({'status': 'ok'})

                    elif client.event == EVENT_WS_MESSAGE:
                        cls.ws_events.append({
                            'event': 'message',
                            'data': client.ws_message,
                        })
                        # Echo back
                        client.ws_send(client.ws_message)

                    elif client.event == EVENT_WS_CHUNK_FIRST:
                        cls.ws_events.append({
                            'event': 'message_first',
                            'data': client.ws_message,
                        })

                    elif client.event == EVENT_WS_CHUNK_NEXT:
                        cls.ws_events.append({
                            'event': 'message_next',
                            'data': client.ws_message,
                        })

                    elif client.event == EVENT_WS_CHUNK_LAST:
                        cls.ws_events.append({
                            'event': 'message_last',
                            'data': client.ws_message,
                        })
                        # Echo total size
                        total = sum(
                            len(e['data']) for e in cls.ws_events
                            if e['event'].startswith('message_'))
                        client.ws_send(f"received:{total}")

                    elif client.event == EVENT_WS_PING:
                        cls.ws_events.append({
                            'event': 'ping',
                            'data': client.ws_message,
                        })

                    elif client.event == EVENT_WS_CLOSE:
                        cls.ws_events.append({
                            'event': 'close',
                            'data': client.ws_message,
                        })
                        cls.ws_connections.pop(id(client), None)
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def setUp(self):
        TestWebSocketEventMode.ws_events = []
        TestWebSocketEventMode.ws_connections = {}
        TestWebSocketEventMode.pending_actions = []

    def _connect_ws(self):
        """Connect and upgrade to WebSocket"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))
        sock.settimeout(3)
        ws_upgrade(sock)
        return sock

    def test_upgrade_handshake(self):
        """Test WebSocket upgrade returns 101"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3)
            response = ws_upgrade(sock)
            self.assertIn(b'101 Switching Protocols', response)
            self.assertIn(b'Upgrade: websocket', response)
        finally:
            sock.close()

    def test_text_echo(self):
        """Test sending and receiving text message"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'Hello WS'))
            fin, opcode, payload = recv_frame(sock)
            self.assertTrue(fin)
            self.assertEqual(opcode, WS_OPCODE_TEXT)
            self.assertEqual(payload, b'Hello WS')
        finally:
            sock.close()

    def test_binary_echo(self):
        """Test sending and receiving binary message"""
        sock = self._connect_ws()
        try:
            data = bytes(range(256))
            sock.sendall(build_masked_frame(WS_OPCODE_BINARY, data))
            fin, opcode, payload = recv_frame(sock)
            self.assertTrue(fin)
            self.assertEqual(opcode, WS_OPCODE_BINARY)
            self.assertEqual(payload, data)
        finally:
            sock.close()

    def test_multiple_messages(self):
        """Test multiple messages on same connection"""
        sock = self._connect_ws()
        try:
            for i in range(5):
                msg = f'message {i}'
                sock.sendall(build_masked_frame(WS_OPCODE_TEXT, msg))
                fin, opcode, payload = recv_frame(sock)
                self.assertEqual(payload.decode(), msg)
        finally:
            sock.close()

    def test_ping_auto_pong(self):
        """Test that server responds to ping with pong"""
        sock = self._connect_ws()
        try:
            ping_data = b'ping123'
            sock.sendall(build_masked_frame(WS_OPCODE_PING, ping_data))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_PONG)
            self.assertEqual(payload, ping_data)
            time.sleep(0.2)
            self.assertTrue(
                any(e['event'] == 'ping' for e in self.ws_events))
        finally:
            sock.close()

    def test_close_handshake(self):
        """Test WebSocket close handshake"""
        sock = self._connect_ws()
        try:
            close_payload = b'\x03\xe8'  # code 1000
            sock.sendall(build_masked_frame(
                WS_OPCODE_CLOSE, close_payload))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_CLOSE)
            time.sleep(0.2)
            self.assertTrue(
                any(e['event'] == 'close' for e in self.ws_events))
        finally:
            sock.close()

    def test_event_ws_message_type(self):
        """Test that EVENT_WS_MESSAGE is generated for small messages"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'small'))
            recv_frame(sock)
            time.sleep(0.2)
            self.assertTrue(
                any(e['event'] == 'message' for e in self.ws_events))
        finally:
            sock.close()

    def test_text_message_is_str(self):
        """Test that text frame ws_message is str"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'čau'))
            recv_frame(sock)
            time.sleep(0.2)
            msg_events = [
                e for e in self.ws_events if e['event'] == 'message']
            self.assertEqual(len(msg_events), 1)
            self.assertIsInstance(msg_events[0]['data'], str)
            self.assertEqual(msg_events[0]['data'], 'čau')
        finally:
            sock.close()

    def test_binary_message_is_bytes(self):
        """Test that binary frame ws_message is bytes"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(
                WS_OPCODE_BINARY, b'\x00\x01\x02'))
            recv_frame(sock)
            time.sleep(0.2)
            msg_events = [
                e for e in self.ws_events if e['event'] == 'message']
            self.assertEqual(len(msg_events), 1)
            self.assertIsInstance(msg_events[0]['data'], bytes)
        finally:
            sock.close()

    def test_fragmented_message(self):
        """Test fragmented message (multiple frames, one logical message)"""
        sock = self._connect_ws()
        try:
            # First fragment
            sock.sendall(build_masked_frame(
                WS_OPCODE_TEXT, 'Hello ', fin=False))
            time.sleep(0.1)
            # Final fragment
            sock.sendall(build_masked_frame(
                WS_OPCODE_CONTINUATION, 'World!', fin=True))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(payload.decode(), 'Hello World!')
        finally:
            sock.close()

    def test_server_ws_ping(self):
        """Test server sending ping to client"""
        sock = self._connect_ws()
        try:
            # Send a message to get a reference to the connection
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'init'))
            recv_frame(sock)
            time.sleep(0.3)

            # Schedule ping in server thread to avoid race conditions
            for conn in list(self.ws_connections.values()):
                self.pending_actions.append(
                    lambda c=conn: c.ws_ping(b'server-ping'))
                break

            sock.settimeout(5)
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_PING)
            self.assertEqual(payload, b'server-ping')
        finally:
            sock.close()

    def test_server_ws_close(self):
        """Test server initiating close"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'init'))
            recv_frame(sock)
            time.sleep(0.3)

            for conn in list(self.ws_connections.values()):
                self.pending_actions.append(
                    lambda c=conn: c.ws_close(1000, 'bye'))
                break

            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_CLOSE)
            code = (payload[0] << 8) | payload[1]
            self.assertEqual(code, 1000)
        finally:
            sock.close()

    def test_disconnect_generates_close_event(self):
        """Test that abrupt disconnect generates EVENT_WS_CLOSE"""
        sock = self._connect_ws()
        sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'init'))
        recv_frame(sock)
        time.sleep(0.2)

        self.ws_events.clear()
        sock.close()
        time.sleep(1)
        # Server should detect disconnect and fire close event
        close_events = [
            e for e in self.ws_events if e['event'] == 'close']
        self.assertEqual(len(close_events), 1)
        self.assertIsNone(close_events[0]['data'])

    def test_http_still_works_alongside_ws(self):
        """Test that HTTP requests work while WS connection is active"""
        ws_sock = self._connect_ws()
        try:
            # Make regular HTTP request
            http_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                http_sock.connect(('localhost', self.PORT))
                http_sock.settimeout(3)
                http_sock.sendall(
                    b"GET /test HTTP/1.1\r\n"
                    b"Host: localhost\r\n"
                    b"Connection: close\r\n\r\n")
                response = b''
                try:
                    while True:
                        chunk = http_sock.recv(1024)
                        if not chunk:
                            break
                        response += chunk
                except socket.timeout:
                    pass
                self.assertIn(b'200 OK', response)
            finally:
                http_sock.close()

            # WS should still work
            ws_sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'after http'))
            fin, opcode, payload = recv_frame(ws_sock)
            self.assertEqual(payload.decode(), 'after http')
        finally:
            ws_sock.close()

    def test_non_websocket_get_still_works(self):
        """Test that non-upgrade GET requests still work normally"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3)
            sock.sendall(
                b"GET /test HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n\r\n")
            response = b''
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass
            self.assertIn(b'200 OK', response)
        finally:
            sock.close()

    def test_empty_ping(self):
        """Test ping with empty payload"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_PING, b''))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_PONG)
            self.assertEqual(payload, b'')
        finally:
            sock.close()

    def test_ping_between_fragments(self):
        """Test control frame interjected between data fragments"""
        sock = self._connect_ws()
        try:
            # First fragment
            sock.sendall(build_masked_frame(
                WS_OPCODE_TEXT, 'part1', fin=False))
            time.sleep(0.1)
            # Ping in the middle
            sock.sendall(build_masked_frame(WS_OPCODE_PING, b'mid'))
            # Should get pong back
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_PONG)
            self.assertEqual(payload, b'mid')
            # Final fragment
            sock.sendall(build_masked_frame(
                WS_OPCODE_CONTINUATION, 'part2', fin=True))
            # Should get complete message
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(payload.decode(), 'part1part2')
        finally:
            sock.close()


# --- Non-event mode tests ---

class TestWebSocketNonEventMode(unittest.TestCase):
    """Test WebSocket in non-event mode (blocking)"""

    PORT = 9971
    server = None
    server_thread = None

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while True:
                    srv = cls.server
                    if srv is None:
                        break
                    client = srv.wait(timeout=0.5)
                    if not client:
                        continue
                    if client.is_websocket_request:
                        ws = client.accept_websocket()
                        # Echo loop in thread
                        def echo_loop(ws_conn):
                            while not ws_conn.is_closed:
                                msg = ws_conn.recv(timeout=5)
                                if msg is None:
                                    break
                                ws_conn.send(msg)
                        t = threading.Thread(
                            target=echo_loop, args=(ws,), daemon=True)
                        t.start()
                    else:
                        client.respond({'status': 'ok'})
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def _connect_ws(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))
        sock.settimeout(3)
        ws_upgrade(sock)
        return sock

    def test_text_echo(self):
        """Test text echo in non-event mode"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'Hello'))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_TEXT)
            self.assertEqual(payload, b'Hello')
        finally:
            sock.close()

    def test_binary_echo(self):
        """Test binary echo in non-event mode"""
        sock = self._connect_ws()
        try:
            data = b'\x00\x01\x02\xff'
            sock.sendall(build_masked_frame(WS_OPCODE_BINARY, data))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_BINARY)
            self.assertEqual(payload, data)
        finally:
            sock.close()

    def test_multiple_messages(self):
        """Test multiple messages"""
        sock = self._connect_ws()
        try:
            for i in range(10):
                msg = f'msg-{i}'
                sock.sendall(build_masked_frame(WS_OPCODE_TEXT, msg))
                fin, opcode, payload = recv_frame(sock)
                self.assertEqual(payload.decode(), msg)
        finally:
            sock.close()

    def test_close_from_client(self):
        """Test client-initiated close"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(
                WS_OPCODE_CLOSE, b'\x03\xe8'))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_CLOSE)
        finally:
            sock.close()

    def test_ping_pong(self):
        """Test ping-pong in non-event mode"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_PING, b'test'))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_PONG)
            self.assertEqual(payload, b'test')
        finally:
            sock.close()

    def test_fragmented_message(self):
        """Test fragmented message reassembly"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(
                WS_OPCODE_TEXT, 'Hello ', fin=False))
            time.sleep(0.05)
            sock.sendall(build_masked_frame(
                WS_OPCODE_CONTINUATION, 'World', fin=True))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(payload.decode(), 'Hello World')
        finally:
            sock.close()

    def test_http_not_affected(self):
        """Test that normal HTTP still works"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect(('localhost', self.PORT))
            sock.settimeout(3)
            sock.sendall(
                b"GET /test HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Connection: close\r\n\r\n")
            response = b''
            try:
                while True:
                    chunk = sock.recv(1024)
                    if not chunk:
                        break
                    response += chunk
            except socket.timeout:
                pass
            self.assertIn(b'200 OK', response)
        finally:
            sock.close()

    def test_websocket_object_properties(self):
        """Test WebSocket object is_closed property"""
        sock = self._connect_ws()
        try:
            # Send and receive to verify connection works
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'test'))
            recv_frame(sock)
        finally:
            sock.close()


class TestWebSocketLargeMessages(unittest.TestCase):
    """Test WebSocket chunking for large messages (event mode)"""

    PORT = 9972
    server = None
    server_thread = None
    ws_events = []

    @classmethod
    def setUpClass(cls):
        # Small limit to trigger chunking easily
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, event_mode=True,
            max_ws_message_length=100)

        def run_server():
            try:
                while True:
                    srv = cls.server
                    if srv is None:
                        break
                    client = srv.wait(timeout=0.5)
                    if not client:
                        continue

                    if client.event == EVENT_WS_REQUEST:
                        client.accept_websocket()
                    elif client.event == EVENT_REQUEST:
                        client.respond({'status': 'ok'})
                    elif client.event in (
                            EVENT_WS_MESSAGE, EVENT_WS_CHUNK_FIRST,
                            EVENT_WS_CHUNK_NEXT, EVENT_WS_CHUNK_LAST):
                        cls.ws_events.append({
                            'event': client.event,
                            'data': client.ws_message,
                            'len': len(client.ws_message),
                        })
                        if client.event == EVENT_WS_MESSAGE:
                            client.ws_send(f"ok:{len(client.ws_message)}")
                        elif client.event == EVENT_WS_CHUNK_LAST:
                            total = sum(
                                e['len'] for e in cls.ws_events
                                if e['event'] in (
                                    EVENT_WS_CHUNK_FIRST,
                                    EVENT_WS_CHUNK_NEXT,
                                    EVENT_WS_CHUNK_LAST))
                            client.ws_send(f"chunked:{total}")
                    elif client.event == EVENT_WS_CLOSE:
                        pass
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def setUp(self):
        TestWebSocketLargeMessages.ws_events = []

    def _connect_ws(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))
        sock.settimeout(3)
        ws_upgrade(sock)
        return sock

    def test_small_message_single_event(self):
        """Test message under limit produces EVENT_WS_MESSAGE"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'x' * 50))
            fin, opcode, payload = recv_frame(sock)
            self.assertIn(b'ok:50', payload)
            time.sleep(0.2)
            events = [
                e for e in self.ws_events
                if e['event'] == EVENT_WS_MESSAGE]
            self.assertEqual(len(events), 1)
        finally:
            sock.close()

    def test_large_message_chunked_events(self):
        """Test message over limit produces FIRST/NEXT/LAST events"""
        sock = self._connect_ws()
        try:
            # Send 500 bytes (limit is 100)
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'x' * 500))
            fin, opcode, payload = recv_frame(sock)
            self.assertIn(b'chunked:500', payload)
            time.sleep(0.2)
            event_types = [e['event'] for e in self.ws_events]
            self.assertIn(EVENT_WS_CHUNK_FIRST, event_types)
            self.assertIn(EVENT_WS_CHUNK_LAST, event_types)
            # Total data should be 500
            total = sum(e['len'] for e in self.ws_events)
            self.assertEqual(total, 500)
        finally:
            sock.close()

    def test_large_fragmented_message(self):
        """Test large message sent as WS fragments"""
        sock = self._connect_ws()
        try:
            # Send 300 bytes in 3 fragments of 100
            sock.sendall(build_masked_frame(
                WS_OPCODE_TEXT, 'a' * 100, fin=False))
            time.sleep(0.05)
            sock.sendall(build_masked_frame(
                WS_OPCODE_CONTINUATION, 'b' * 100, fin=False))
            time.sleep(0.05)
            sock.sendall(build_masked_frame(
                WS_OPCODE_CONTINUATION, 'c' * 100, fin=True))
            fin, opcode, payload = recv_frame(sock)
            self.assertIn(b'chunked:300', payload)
            time.sleep(0.2)
            total = sum(e['len'] for e in self.ws_events)
            self.assertEqual(total, 300)
        finally:
            sock.close()

    def test_exactly_at_limit(self):
        """Test message exactly at limit produces single EVENT_WS_MESSAGE"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'x' * 100))
            fin, opcode, payload = recv_frame(sock)
            self.assertIn(b'ok:100', payload)
            time.sleep(0.2)
            events = [
                e for e in self.ws_events
                if e['event'] == EVENT_WS_MESSAGE]
            self.assertEqual(len(events), 1)
        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
