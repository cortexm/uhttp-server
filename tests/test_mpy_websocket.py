"""MicroPython WebSocket integration tests

Run on PC, WebSocket server runs on ESP32 via mpytool.
Uses raw sockets for WebSocket client (no external dependencies).

Configuration: same as test_mpy_integration.py
"""

import hashlib
import binascii
import json
import os
import socket
import time
import unittest
from pathlib import Path


def _load_config():
    """Load configuration from env vars and config files"""
    config = {
        'port': os.environ.get('MPY_TEST_PORT'),
        'ssid': os.environ.get('MPY_WIFI_SSID'),
        'password': os.environ.get('MPY_WIFI_PASSWORD'),
    }

    home = Path.home()
    wifi_paths = [
        home / '.config' / 'uhttp' / 'wifi.json',
        home / 'actions-runner' / '.config' / 'uhttp' / 'wifi.json',
    ]
    port_paths = [
        home / '.config' / 'mpytool' / 'ESP32',
        home / 'actions-runner' / '.config' / 'mpytool' / 'ESP32',
    ]

    if not config['ssid']:
        for path in wifi_paths:
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    config['ssid'] = data.get('ssid')
                    config['password'] = data.get('password', '')
                    break
                except (json.JSONDecodeError, IOError):
                    pass

    if not config['port']:
        for path in port_paths:
            if path.exists():
                try:
                    config['port'] = path.read_text().strip()
                    break
                except IOError:
                    pass

    if config['password'] is None:
        config['password'] = ''

    return config


_config = _load_config()
PORT = _config['port']
WIFI_SSID = _config['ssid']
WIFI_PASSWORD = _config['password']

ESP32_SERVER_PORT = 8081  # Different from test_mpy_integration

_WS_MAGIC = b'258EAFA5-E914-47DA-95CA-5AB9141B3175'

WS_OPCODE_TEXT = 0x1
WS_OPCODE_BINARY = 0x2
WS_OPCODE_CLOSE = 0x8
WS_OPCODE_PING = 0x9
WS_OPCODE_PONG = 0xA


def requires_device(cls):
    """Skip tests if device not configured"""
    if not PORT:
        return unittest.skip("MPY_TEST_PORT not set")(cls)
    if not WIFI_SSID:
        return unittest.skip("MPY_WIFI_SSID not set")(cls)
    return cls


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


def recv_frame(sock, timeout=10):
    """Receive and parse a WebSocket frame"""
    sock.settimeout(timeout)
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
    """Perform WebSocket handshake"""
    key = 'dGhlIHNhbXBsZSBub25jZQ=='
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: test\r\n"
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


class MpyWebSocketTestCase(unittest.TestCase):
    """Base class for MicroPython WebSocket server tests"""

    mpy = None
    conn = None
    esp32_ip = None
    server_running = False
    _mount_handler = None

    @classmethod
    def setUpClass(cls):
        import mpytool
        from mpytool.mpy_cross import MpyCross

        cls.conn = mpytool.ConnSerial(port=PORT, baudrate=115200)
        cls.mpy = mpytool.Mpy(cls.conn)

        # Soft reset to clear any previous state
        cls.mpy.stop()
        try:
            cls.conn.write(b'\x03\x03\x04')
            time.sleep(2)
            cls.conn.read_all()
        except Exception:
            pass
        cls.mpy.stop()

        # Mount server module with mpy-cross compilation
        server_dir = Path(__file__).parent.parent / 'uhttp'
        mpy_cross = MpyCross()
        mpy_cross.init(cls.mpy.platform())
        cls._mount_handler = cls.mpy.mount(
            str(server_dir), mount_point='/lib/uhttp', mpy_cross=mpy_cross)

        # Connect WiFi and get IP
        cls.esp32_ip = cls._connect_wifi()

        # Start WebSocket server and wait for it
        cls._start_server()
        cls._wait_for_server()

    @classmethod
    def tearDownClass(cls):
        if cls.server_running:
            try:
                cls.mpy.stop()
            except Exception:
                pass
            cls.server_running = False
        if cls.conn:
            cls.conn.close()

    @classmethod
    def _connect_wifi(cls):
        """Connect ESP32 to WiFi and return IP address"""
        code = f"""
import network
import time

wlan = network.WLAN(network.STA_IF)
wlan.active(True)

if not wlan.isconnected():
    wlan.connect({repr(WIFI_SSID)}, {repr(WIFI_PASSWORD)})
    for _ in range(30):
        if wlan.isconnected():
            break
        time.sleep(0.5)

if wlan.isconnected():
    print('IP:', wlan.ifconfig()[0])
else:
    print('WIFI_FAIL')
"""
        result = cls.mpy.comm.exec(code, timeout=20).decode('utf-8')
        if 'WIFI_FAIL' in result:
            raise RuntimeError("WiFi connection failed")

        for line in result.strip().split('\n'):
            if line.startswith('IP:'):
                return line.split(':')[1].strip()

        raise RuntimeError(f"Could not get IP address: {result}")

    @classmethod
    def _start_server(cls):
        """Start WebSocket echo server on ESP32"""
        # Verify import works
        test_result = cls.mpy.comm.exec(
            "import sys; sys.path.insert(0, '/lib'); "
            "from uhttp.server import HttpServer, EVENT_REQUEST, "
            "EVENT_WS_MESSAGE, EVENT_WS_CLOSE; print('OK')",
            timeout=10
        ).decode('utf-8')
        if 'OK' not in test_result:
            raise RuntimeError(
                f"Failed to import uhttp.server: {test_result}")

        # Start WS echo server (fire-and-forget)
        code = f"""
import sys
sys.path.insert(0, '/lib')
from uhttp.server import (
    HttpServer, EVENT_REQUEST, EVENT_WS_REQUEST, EVENT_WS_MESSAGE,
    EVENT_WS_CLOSE, EVENT_WS_PING)

server = HttpServer(port={ESP32_SERVER_PORT}, event_mode=True)

while True:
    client = server.wait(timeout=1)
    if client:
        if client.event == EVENT_WS_REQUEST:
            client.accept_websocket()
        elif client.event == EVENT_REQUEST:
            if client.path == '/health':
                client.respond({{'status': 'ok'}})
            else:
                client.respond({{'path': client.path}})
        elif client.event == EVENT_WS_MESSAGE:
            client.ws_send(client.ws_message)
        elif client.event == EVENT_WS_PING:
            pass
        elif client.event == EVENT_WS_CLOSE:
            pass
"""
        cls.mpy.comm.exec(code, timeout=0)
        cls.server_running = True
        time.sleep(0.5)

    @classmethod
    def _wait_for_server(cls, max_attempts=10):
        """Wait for server to respond to health check"""
        for _ in range(max_attempts):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                sock.connect((cls.esp32_ip, ESP32_SERVER_PORT))
                sock.sendall(
                    b'GET /health HTTP/1.0\r\nHost: test\r\n\r\n')
                if b'200' in sock.recv(1024):
                    sock.close()
                    return
                sock.close()
            except (OSError, socket.timeout):
                pass
            time.sleep(1)
        raise RuntimeError(
            f"Server not ready on {cls.esp32_ip}:{ESP32_SERVER_PORT}")

    def _connect_ws(self, path='/ws', retries=3):
        """Connect and upgrade to WebSocket with retries"""
        last_error = None
        for _ in range(retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((self.esp32_ip, ESP32_SERVER_PORT))
                ws_upgrade(sock, path)
                return sock
            except (OSError, AssertionError) as e:
                last_error = e
                try:
                    sock.close()
                except Exception:
                    pass
                time.sleep(0.5)
        raise last_error


@requires_device
class TestMpyWebSocket(MpyWebSocketTestCase):
    """Test WebSocket on MicroPython device"""

    def test_upgrade_handshake(self):
        """Test WebSocket upgrade returns 101"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(10)
            sock.connect((self.esp32_ip, ESP32_SERVER_PORT))
            response = ws_upgrade(sock)
            self.assertIn(b'101 Switching Protocols', response)
            self.assertIn(b'Upgrade: websocket', response)
        finally:
            sock.close()

    def test_text_echo(self):
        """Test text message echo"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, 'Hello ESP32'))
            fin, opcode, payload = recv_frame(sock)
            self.assertTrue(fin)
            self.assertEqual(opcode, WS_OPCODE_TEXT)
            self.assertEqual(payload, b'Hello ESP32')
        finally:
            sock.close()

    def test_binary_echo(self):
        """Test binary message echo"""
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

    def test_ping_pong(self):
        """Test ping-pong"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(
                WS_OPCODE_PING, b'ping-test'))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_PONG)
            self.assertEqual(payload, b'ping-test')
        finally:
            sock.close()

    def test_close_handshake(self):
        """Test WebSocket close handshake"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(
                WS_OPCODE_CLOSE, b'\x03\xe8'))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(opcode, WS_OPCODE_CLOSE)
        finally:
            sock.close()

    def test_utf8_message(self):
        """Test UTF-8 text message"""
        sock = self._connect_ws()
        try:
            msg = 'Ahoj svet! čau 🌍'
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, msg))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(payload.decode('utf-8'), msg)
        finally:
            sock.close()

    def test_empty_message(self):
        """Test empty text message"""
        sock = self._connect_ws()
        try:
            sock.sendall(build_masked_frame(WS_OPCODE_TEXT, ''))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(payload, b'')
        finally:
            sock.close()

    def test_medium_message(self):
        """Test medium-sized message (1KB)"""
        sock = self._connect_ws()
        try:
            data = b'x' * 1024
            sock.sendall(build_masked_frame(WS_OPCODE_BINARY, data))
            fin, opcode, payload = recv_frame(sock)
            self.assertEqual(len(payload), 1024)
            self.assertEqual(payload, data)
        finally:
            sock.close()

    def test_http_still_works(self):
        """Test that HTTP requests work alongside WebSocket"""
        # First make a WS connection
        ws_sock = self._connect_ws()
        try:
            # Make HTTP request
            http_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                http_sock.settimeout(10)
                http_sock.connect((self.esp32_ip, ESP32_SERVER_PORT))
                http_sock.sendall(
                    b'GET /test HTTP/1.0\r\nHost: test\r\n\r\n')
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
            ws_sock.sendall(build_masked_frame(
                WS_OPCODE_TEXT, 'still alive'))
            fin, opcode, payload = recv_frame(ws_sock)
            self.assertEqual(payload, b'still alive')
        finally:
            ws_sock.close()


if __name__ == '__main__':
    unittest.main()
