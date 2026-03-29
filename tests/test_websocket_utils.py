"""Unit tests for WebSocket helper functions and frame parsing"""
import unittest
import hashlib
import binascii
from uhttp import server as uhttp_server
from uhttp.server import (
    _ws_accept_key, _ws_build_frame, _WS_MAGIC,
    WS_OPCODE_TEXT, WS_OPCODE_BINARY, WS_OPCODE_CLOSE,
    WS_OPCODE_PING, WS_OPCODE_PONG, WS_OPCODE_CONTINUATION,
)


class TestWsAcceptKey(unittest.TestCase):
    """Test Sec-WebSocket-Accept key computation"""

    def test_rfc_example(self):
        """Test with RFC 6455 example key"""
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        expected = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        self.assertEqual(_ws_accept_key(key), expected)

    def test_known_key(self):
        """Test accept key matches manual computation"""
        key = "x3JJHMbDL1EzLkh9GBhXDw=="
        digest = hashlib.sha1(
            key.encode('ascii') + _WS_MAGIC).digest()
        expected = binascii.b2a_base64(digest).strip().decode('ascii')
        self.assertEqual(_ws_accept_key(key), expected)


class TestWsBuildFrame(unittest.TestCase):
    """Test WebSocket frame building"""

    def test_empty_frame(self):
        """Test empty payload frame"""
        frame = _ws_build_frame(WS_OPCODE_TEXT, b'')
        self.assertEqual(frame, bytes([0x81, 0x00]))

    def test_small_text_frame(self):
        """Test small text frame (payload < 126 bytes)"""
        payload = b'Hello'
        frame = _ws_build_frame(WS_OPCODE_TEXT, payload)
        self.assertEqual(frame[0], 0x81)  # FIN + TEXT
        self.assertEqual(frame[1], 5)  # length
        self.assertEqual(frame[2:], payload)

    def test_binary_frame(self):
        """Test binary frame opcode"""
        frame = _ws_build_frame(WS_OPCODE_BINARY, b'\x00\x01\x02')
        self.assertEqual(frame[0], 0x82)  # FIN + BINARY

    def test_no_fin_flag(self):
        """Test frame without FIN flag"""
        frame = _ws_build_frame(WS_OPCODE_TEXT, b'Hi', fin=False)
        self.assertEqual(frame[0], 0x01)  # no FIN + TEXT

    def test_medium_payload(self):
        """Test 126-byte extended length encoding"""
        payload = b'x' * 200
        frame = _ws_build_frame(WS_OPCODE_BINARY, payload)
        self.assertEqual(frame[0], 0x82)
        self.assertEqual(frame[1], 126)  # extended length marker
        length = (frame[2] << 8) | frame[3]
        self.assertEqual(length, 200)
        self.assertEqual(frame[4:], payload)

    def test_large_payload(self):
        """Test 64-bit extended length encoding"""
        payload = b'x' * 70000
        frame = _ws_build_frame(WS_OPCODE_BINARY, payload)
        self.assertEqual(frame[0], 0x82)
        self.assertEqual(frame[1], 127)  # 64-bit length marker
        length = 0
        for i in range(8):
            length = (length << 8) | frame[2 + i]
        self.assertEqual(length, 70000)
        self.assertEqual(frame[10:], payload)

    def test_str_payload(self):
        """Test string payload auto-encoding"""
        frame = _ws_build_frame(WS_OPCODE_TEXT, 'Hello')
        self.assertEqual(frame[2:], b'Hello')

    def test_ping_frame(self):
        """Test ping frame"""
        frame = _ws_build_frame(WS_OPCODE_PING, b'')
        self.assertEqual(frame, bytes([0x89, 0x00]))

    def test_pong_frame(self):
        """Test pong frame"""
        frame = _ws_build_frame(WS_OPCODE_PONG, b'data')
        self.assertEqual(frame[0], 0x8A)
        self.assertEqual(frame[2:], b'data')

    def test_close_frame(self):
        """Test close frame with status code"""
        payload = b'\x03\xe8'  # 1000
        frame = _ws_build_frame(WS_OPCODE_CLOSE, payload)
        self.assertEqual(frame[0], 0x88)
        self.assertEqual(frame[2:], payload)

    def test_continuation_frame(self):
        """Test continuation frame"""
        frame = _ws_build_frame(WS_OPCODE_CONTINUATION, b'data', fin=True)
        self.assertEqual(frame[0], 0x80)  # FIN + CONTINUATION

    def test_125_byte_boundary(self):
        """Test payload exactly at 125 bytes (max for 7-bit length)"""
        payload = b'x' * 125
        frame = _ws_build_frame(WS_OPCODE_TEXT, payload)
        self.assertEqual(frame[1], 125)
        self.assertEqual(len(frame), 2 + 125)

    def test_126_byte_boundary(self):
        """Test payload at 126 bytes (first extended length)"""
        payload = b'x' * 126
        frame = _ws_build_frame(WS_OPCODE_TEXT, payload)
        self.assertEqual(frame[1], 126)
        self.assertEqual(len(frame), 4 + 126)

    def test_65535_byte_boundary(self):
        """Test payload at 65535 bytes (max for 16-bit length)"""
        payload = b'x' * 65535
        frame = _ws_build_frame(WS_OPCODE_TEXT, payload)
        self.assertEqual(frame[1], 126)
        length = (frame[2] << 8) | frame[3]
        self.assertEqual(length, 65535)

    def test_65536_byte_boundary(self):
        """Test payload at 65536 bytes (first 64-bit length)"""
        payload = b'x' * 65536
        frame = _ws_build_frame(WS_OPCODE_TEXT, payload)
        self.assertEqual(frame[1], 127)


def build_masked_frame(opcode, payload, fin=True, mask=b'\x01\x02\x03\x04'):
    """Build a masked WebSocket frame (simulating client-side)"""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    frame = bytearray()
    frame.append((0x80 if fin else 0) | opcode)
    length = len(payload)
    if length < 126:
        frame.append(0x80 | length)  # mask bit set
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


class TestWsFrameHeaderParsing(unittest.TestCase):
    """Test WebSocket frame header parsing via HttpConnection"""

    def _make_connection(self, **kwargs):
        """Create a minimal HttpConnection for testing"""
        import socket
        server = type('MockServer', (), {
            'is_secure': False,
            'event_mode': True,
            'remove_connection': lambda self, c: None,
        })()
        # Use a real socket pair for testing
        s1, s2 = socket.socketpair()
        s1.setblocking(False)
        s2.setblocking(False)
        conn = uhttp_server.HttpConnection(
            server, s1, ('127.0.0.1', 12345), **kwargs)
        return conn, s2

    def tearDown(self):
        """Clean up any connections"""
        pass

    def test_parse_small_frame_header(self):
        """Test parsing header of small frame"""
        conn, peer = self._make_connection()
        try:
            frame = build_masked_frame(WS_OPCODE_TEXT, b'Hello')
            conn._buffer = bytearray(frame)
            conn._ws_mode = True
            result = conn._ws_parse_frame_header()
            self.assertTrue(result)
            self.assertTrue(conn._ws_frame_fin)
            self.assertEqual(conn._ws_frame_opcode, WS_OPCODE_TEXT)
            self.assertEqual(conn._ws_frame_remaining, 5)
            self.assertIsNotNone(conn._ws_frame_mask)
        finally:
            conn._socket.close()
            peer.close()

    def test_parse_incomplete_header(self):
        """Test that incomplete header returns False"""
        conn, peer = self._make_connection()
        try:
            conn._buffer = bytearray(b'\x81')  # only 1 byte
            result = conn._ws_parse_frame_header()
            self.assertFalse(result)
        finally:
            conn._socket.close()
            peer.close()

    def test_parse_medium_length_header(self):
        """Test parsing 16-bit extended length"""
        conn, peer = self._make_connection()
        try:
            frame = build_masked_frame(WS_OPCODE_BINARY, b'x' * 200)
            conn._buffer = bytearray(frame)
            result = conn._ws_parse_frame_header()
            self.assertTrue(result)
            self.assertEqual(conn._ws_frame_remaining, 200)
        finally:
            conn._socket.close()
            peer.close()

    def test_parse_unmasked_frame(self):
        """Test parsing unmasked frame (server-to-server or lenient)"""
        conn, peer = self._make_connection()
        try:
            frame = _ws_build_frame(WS_OPCODE_TEXT, b'Hello')
            conn._buffer = bytearray(frame)
            result = conn._ws_parse_frame_header()
            self.assertTrue(result)
            self.assertIsNone(conn._ws_frame_mask)
            self.assertEqual(conn._ws_frame_remaining, 5)
        finally:
            conn._socket.close()
            peer.close()

    def test_message_opcode_tracking(self):
        """Test that message opcode is set for data frames"""
        conn, peer = self._make_connection()
        try:
            frame = build_masked_frame(WS_OPCODE_BINARY, b'data')
            conn._buffer = bytearray(frame)
            conn._ws_parse_frame_header()
            self.assertEqual(conn._ws_message_opcode, WS_OPCODE_BINARY)
        finally:
            conn._socket.close()
            peer.close()

    def test_continuation_preserves_message_opcode(self):
        """Test that continuation frame doesn't override message opcode"""
        conn, peer = self._make_connection()
        try:
            conn._ws_message_opcode = WS_OPCODE_TEXT
            frame = build_masked_frame(
                WS_OPCODE_CONTINUATION, b'more', fin=True)
            conn._buffer = bytearray(frame)
            conn._ws_parse_frame_header()
            self.assertEqual(conn._ws_message_opcode, WS_OPCODE_TEXT)
        finally:
            conn._socket.close()
            peer.close()


class TestWsDemask(unittest.TestCase):
    """Test WebSocket demasking"""

    def _make_connection(self):
        import socket
        server = type('MockServer', (), {
            'is_secure': False,
            'event_mode': True,
            'remove_connection': lambda self, c: None,
        })()
        s1, s2 = socket.socketpair()
        s1.setblocking(False)
        s2.setblocking(False)
        conn = uhttp_server.HttpConnection(
            server, s1, ('127.0.0.1', 12345))
        return conn, s2

    def test_demask_basic(self):
        """Test basic demasking"""
        conn, peer = self._make_connection()
        try:
            mask = b'\x01\x02\x03\x04'
            payload = b'Hello'
            masked = bytearray(payload)
            for i in range(len(masked)):
                masked[i] ^= mask[i & 3]
            conn._buffer = bytearray(masked)
            conn._ws_frame_mask = mask
            conn._ws_frame_mask_offset = 0
            result = conn._ws_demask(len(payload))
            self.assertEqual(result, payload)
        finally:
            conn._socket.close()
            peer.close()

    def test_demask_incremental(self):
        """Test demasking in chunks preserves mask offset"""
        conn, peer = self._make_connection()
        try:
            mask = b'\x01\x02\x03\x04'
            payload = b'HelloWorld!'
            masked = bytearray(payload)
            for i in range(len(masked)):
                masked[i] ^= mask[i & 3]

            conn._ws_frame_mask = mask
            conn._ws_frame_mask_offset = 0

            # Demask first 5 bytes
            conn._buffer = bytearray(masked[:5])
            part1 = conn._ws_demask(5)
            self.assertEqual(part1, b'Hello')
            self.assertEqual(conn._ws_frame_mask_offset, 5 & 3)

            # Demask remaining 6 bytes
            conn._buffer = bytearray(masked[5:])
            part2 = conn._ws_demask(6)
            self.assertEqual(part2, b'World!')
        finally:
            conn._socket.close()
            peer.close()

    def test_demask_no_mask(self):
        """Test demask with no mask (returns data as-is)"""
        conn, peer = self._make_connection()
        try:
            conn._buffer = bytearray(b'Hello')
            conn._ws_frame_mask = None
            conn._ws_frame_mask_offset = 0
            result = conn._ws_demask(5)
            self.assertEqual(result, b'Hello')
        finally:
            conn._socket.close()
            peer.close()


if __name__ == '__main__':
    unittest.main()
