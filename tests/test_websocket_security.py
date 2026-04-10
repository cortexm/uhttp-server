"""WebSocket security tests — RFC 6455 compliance

Tests for:
- Control frame payload size limit (≤125 bytes, RFC 6455 §5.5)
- Control frame fragmentation forbidden (FIN must be 1, RFC 6455 §5.5)
- Client frames must be masked (RFC 6455 §5.1)
- Reserved bits must be zero (RFC 6455 §5.2)
"""
import unittest
import socket
import time
import threading
from uhttp import server as uhttp_server
from uhttp.server import (
    EVENT_REQUEST, EVENT_WS_REQUEST, EVENT_WS_MESSAGE,
    EVENT_WS_PING, EVENT_WS_CLOSE,
    WS_OPCODE_TEXT, WS_OPCODE_PING, WS_OPCODE_CLOSE, WS_OPCODE_PONG,
    _WS_MAGIC,
)
from tests.test_websocket import (
    build_masked_frame, recv_frame, ws_upgrade,
)


def build_unmasked_frame(opcode, payload, fin=True):
    """Build an unmasked WebSocket frame (violates RFC 6455 §5.1)"""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    frame = bytearray()
    frame.append((0x80 if fin else 0) | opcode)
    length = len(payload)
    if length < 126:
        frame.append(length)  # No mask bit
    elif length < 65536:
        frame.append(126)
        frame.append((length >> 8) & 0xFF)
        frame.append(length & 0xFF)
    else:
        frame.append(127)
        for i in range(7, -1, -1):
            frame.append((length >> (8 * i)) & 0xFF)
    frame.extend(payload)
    return bytes(frame)


def build_masked_frame_rsv(opcode, payload, rsv=0x70, fin=True,
        mask=b'\x37\xfa\x21\x3d'):
    """Build a masked frame with RSV bits set"""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    frame = bytearray()
    frame.append((0x80 if fin else 0) | rsv | opcode)
    length = len(payload)
    frame.append(0x80 | length)
    frame.extend(mask)
    masked = bytearray(payload)
    for i in range(len(masked)):
        masked[i] ^= mask[i & 3]
    frame.extend(masked)
    return bytes(frame)


class TestWebSocketSecurity(unittest.TestCase):
    """WebSocket RFC 6455 security compliance tests"""

    PORT = 9964
    server = None
    server_thread = None

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(
            port=cls.PORT, event_mode=True)

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
                    elif client.event == EVENT_WS_MESSAGE:
                        data = client.read_buffer()
                        client.ws_send(data.decode('utf-8'))
                    elif client.event == EVENT_WS_PING:
                        pass  # Pong sent automatically
                    elif client.event == EVENT_WS_CLOSE:
                        pass
                    elif client.event == EVENT_REQUEST:
                        client.respond({'status': 'ok'})
            except Exception:
                pass

        cls.server_thread = threading.Thread(target=run_server, daemon=True)
        cls.server_thread.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.close()
            cls.server = None

    def ws_connect(self):
        """Create connected and upgraded WebSocket"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', self.PORT))
        ws_upgrade(sock)
        return sock

    # --- Control frame size (RFC 6455 §5.5) ---

    def test_ping_small_payload_accepted(self):
        """PING with ≤125 byte payload should work"""
        sock = self.ws_connect()
        sock.sendall(build_masked_frame(WS_OPCODE_PING, b'hello'))
        fin, opcode, payload = recv_frame(sock)
        self.assertEqual(opcode, WS_OPCODE_PONG)
        self.assertEqual(payload, b'hello')
        sock.close()

    def test_ping_125_bytes_accepted(self):
        """PING with exactly 125 bytes should work"""
        sock = self.ws_connect()
        data = b'x' * 125
        sock.sendall(build_masked_frame(WS_OPCODE_PING, data))
        fin, opcode, payload = recv_frame(sock)
        self.assertEqual(opcode, WS_OPCODE_PONG)
        self.assertEqual(payload, data)
        sock.close()

    def test_ping_126_bytes_rejected(self):
        """PING with 126 byte payload must be rejected (RFC 6455 §5.5)"""
        sock = self.ws_connect()
        data = b'x' * 126
        sock.sendall(build_masked_frame(WS_OPCODE_PING, data))
        # Connection should be closed — recv returns empty or close frame
        try:
            result = sock.recv(4096)
            # Either empty (closed) or close frame
            if result:
                self.assertEqual(result[0] & 0x0F, WS_OPCODE_CLOSE)
        except (ConnectionResetError, OSError):
            pass  # Connection reset is also acceptable
        sock.close()

    def test_close_large_payload_rejected(self):
        """CLOSE with >125 byte payload must be rejected"""
        sock = self.ws_connect()
        data = b'\x03\xe8' + b'x' * 200  # Status code + 200 bytes reason
        sock.sendall(build_masked_frame(WS_OPCODE_CLOSE, data))
        try:
            result = sock.recv(4096)
            if result:
                # Should get close frame back, not echo the huge payload
                self.assertLessEqual(len(result), 131)  # 2+4+125 max
        except (ConnectionResetError, OSError):
            pass
        sock.close()

    # --- Control frame fragmentation (RFC 6455 §5.5) ---

    def test_ping_fragmented_rejected(self):
        """PING with FIN=0 must be rejected (control frames cannot be fragmented)"""
        sock = self.ws_connect()
        sock.sendall(build_masked_frame(WS_OPCODE_PING, b'hello', fin=False))
        try:
            result = sock.recv(4096)
            if result:
                self.assertEqual(result[0] & 0x0F, WS_OPCODE_CLOSE)
        except (ConnectionResetError, OSError):
            pass
        sock.close()

    # --- Client frame masking (RFC 6455 §5.1) ---

    def test_unmasked_frame_rejected(self):
        """Unmasked client frame must be rejected (RFC 6455 §5.1)"""
        sock = self.ws_connect()
        sock.sendall(build_unmasked_frame(WS_OPCODE_TEXT, b'hello'))
        try:
            result = sock.recv(4096)
            if result:
                self.assertEqual(result[0] & 0x0F, WS_OPCODE_CLOSE)
        except (ConnectionResetError, OSError):
            pass
        sock.close()

    def test_masked_frame_accepted(self):
        """Masked client frame should work normally"""
        sock = self.ws_connect()
        sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'hello'))
        fin, opcode, payload = recv_frame(sock)
        self.assertEqual(opcode, WS_OPCODE_TEXT)
        self.assertEqual(payload, b'hello')
        sock.close()

    # --- Reserved bits (RFC 6455 §5.2) ---

    def test_rsv_bits_set_rejected(self):
        """Frames with RSV bits set must be rejected (no extensions negotiated)"""
        sock = self.ws_connect()
        sock.sendall(build_masked_frame_rsv(
            WS_OPCODE_TEXT, b'hello', rsv=0x40))  # RSV1 set
        try:
            result = sock.recv(4096)
            if result:
                self.assertEqual(result[0] & 0x0F, WS_OPCODE_CLOSE)
        except (ConnectionResetError, OSError):
            pass
        sock.close()

    def test_rsv2_set_rejected(self):
        """Frame with RSV2 set must be rejected"""
        sock = self.ws_connect()
        sock.sendall(build_masked_frame_rsv(
            WS_OPCODE_TEXT, b'hello', rsv=0x20))
        try:
            result = sock.recv(4096)
            if result:
                self.assertEqual(result[0] & 0x0F, WS_OPCODE_CLOSE)
        except (ConnectionResetError, OSError):
            pass
        sock.close()

    def test_rsv3_set_rejected(self):
        """Frame with RSV3 set must be rejected"""
        sock = self.ws_connect()
        sock.sendall(build_masked_frame_rsv(
            WS_OPCODE_TEXT, b'hello', rsv=0x10))
        try:
            result = sock.recv(4096)
            if result:
                self.assertEqual(result[0] & 0x0F, WS_OPCODE_CLOSE)
        except (ConnectionResetError, OSError):
            pass
        sock.close()


if __name__ == '__main__':
    unittest.main()
