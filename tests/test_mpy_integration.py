"""MicroPython integration tests for uhttp-server

Run on PC, server runs on ESP32 via mpytool.

Configuration (in order of priority):
    1. Environment variables:
        MPY_TEST_PORT      - Serial port (e.g., /dev/ttyUSB0)
        MPY_WIFI_SSID      - WiFi SSID
        MPY_WIFI_PASSWORD  - WiFi password

    2. Config files:
        ~/.config/uhttp/wifi.json       - local config
        ~/actions-runner/.config/uhttp/wifi.json  - CI runner

        Format: {"ssid": "MyWiFi", "password": "secret"}

    3. Port from mpytool config:
        ~/.config/mpytool/ESP32
        ~/actions-runner/.config/mpytool/ESP32

Run tests:
    MPY_TEST_PORT=/dev/ttyUSB0 MPY_WIFI_SSID=MyWiFi python -m unittest tests.test_mpy_integration -v
"""

import json
import os
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

# Server port on ESP32
ESP32_SERVER_PORT = 8080


def requires_device(cls):
    """Skip tests if device not configured"""
    if not PORT:
        return unittest.skip("MPY_TEST_PORT not set")(cls)
    if not WIFI_SSID:
        return unittest.skip("MPY_WIFI_SSID not set")(cls)
    return cls


# Minimum free RAM for server tests (server.py is ~44KB)
MIN_RAM_FOR_SERVER = 200000


class MpyServerTestCase(unittest.TestCase):
    """Base class for MicroPython server tests"""

    mpy = None
    conn = None
    esp32_ip = None
    server_running = False
    _server_uploaded = False

    @classmethod
    def setUpClass(cls):
        import mpytool

        cls.conn = mpytool.ConnSerial(port=PORT, baudrate=115200)
        cls.mpy = mpytool.Mpy(cls.conn)
        cls.mpy.stop()

        # Check RAM before uploading large server module
        free_mem = cls.get_free_memory()
        if free_mem < MIN_RAM_FOR_SERVER:
            raise unittest.SkipTest(
                f"Not enough RAM for server: {free_mem} < {MIN_RAM_FOR_SERVER}")

        # Upload server module once
        if not cls._server_uploaded:
            cls._upload_server()
            cls._server_uploaded = True

        # Connect WiFi and get IP
        cls.esp32_ip = cls._connect_wifi()

        # Start server on ESP32
        cls._start_server()

    @classmethod
    def get_free_memory(cls):
        """Get free memory on device"""
        result = cls.mpy.comm.exec_raw_paste(
            "import gc; gc.collect(); print(gc.mem_free())", timeout=5)
        return int(result.strip())

    @classmethod
    def tearDownClass(cls):
        cls._stop_server()
        if cls.conn:
            cls.conn.close()

    @classmethod
    def _upload_server(cls):
        """Upload server.py to ESP32"""
        server_file = Path(__file__).parent.parent / 'uhttp' / 'server.py'
        # Create /lib/uhttp directory
        try:
            cls.mpy.mkdir('/lib')
        except Exception:
            pass
        try:
            cls.mpy.mkdir('/lib/uhttp')
        except Exception:
            pass
        # Upload server.py
        cls.mpy.put(server_file.read_bytes(), '/lib/uhttp/server.py')

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
        """Start HTTP server on ESP32 (fire-and-forget)"""
        code = f"""
import sys
sys.path.insert(0, '/lib')

from uhttp.server import HttpServer

server = HttpServer(port={ESP32_SERVER_PORT})
print('SERVER_STARTED')

while True:
    client = server.wait(timeout=1)
    if client:
        if client.path == '/json':
            client.respond({{'method': client.method, 'path': client.path}})
        elif client.path == '/echo':
            client.respond({{'data': client.data, 'method': client.method}})
        elif client.path == '/headers':
            client.respond({{'headers': dict(client.headers)}})
        elif client.path == '/status/404':
            client.respond({{'error': 'not found'}}, status=404)
        elif client.path == '/status/500':
            client.respond({{'error': 'server error'}}, status=500)
        else:
            client.respond({{'path': client.path, 'method': client.method}})
"""
        # Start server with timeout=0 (fire-and-forget)
        cls.mpy.comm.exec(code, timeout=0)
        cls.server_running = True
        time.sleep(1)  # Give server time to start

    @classmethod
    def _stop_server(cls):
        """Stop HTTP server on ESP32"""
        if cls.server_running:
            try:
                cls.mpy.stop()
                time.sleep(0.5)
            except Exception:
                pass
            cls.server_running = False


@requires_device
class TestHTTPServer(MpyServerTestCase):
    """Test HTTP server basic functionality"""

    def _request(self, method, path, **kwargs):
        """Make HTTP request to ESP32 server"""
        from uhttp.client import HttpClient
        url = f"http://{self.esp32_ip}:{ESP32_SERVER_PORT}"
        client = HttpClient(url)
        try:
            response = getattr(client, method.lower())(path, **kwargs).wait()
            return response
        finally:
            client.close()

    def test_get_request(self):
        """Test basic GET request"""
        response = self._request('GET', '/test')
        self.assertEqual(response.status, 200)
        data = response.json()
        self.assertEqual(data['path'], '/test')
        self.assertEqual(data['method'], 'GET')

    def test_post_json(self):
        """Test POST with JSON data"""
        response = self._request('POST', '/echo', json={'name': 'test'})
        self.assertEqual(response.status, 200)
        data = response.json()
        self.assertEqual(data['method'], 'POST')

    def test_json_response(self):
        """Test JSON response"""
        response = self._request('GET', '/json')
        self.assertEqual(response.status, 200)
        data = response.json()
        self.assertEqual(data['method'], 'GET')
        self.assertEqual(data['path'], '/json')

    def test_status_404(self):
        """Test 404 status code"""
        response = self._request('GET', '/status/404')
        self.assertEqual(response.status, 404)

    def test_status_500(self):
        """Test 500 status code"""
        response = self._request('GET', '/status/500')
        self.assertEqual(response.status, 500)

    def test_put_request(self):
        """Test PUT request"""
        response = self._request('PUT', '/echo', json={'update': True})
        self.assertEqual(response.status, 200)
        data = response.json()
        self.assertEqual(data['method'], 'PUT')

    def test_delete_request(self):
        """Test DELETE request"""
        response = self._request('DELETE', '/test')
        self.assertEqual(response.status, 200)
        data = response.json()
        self.assertEqual(data['method'], 'DELETE')

    def test_keep_alive(self):
        """Test multiple requests on same connection"""
        from uhttp.client import HttpClient
        url = f"http://{self.esp32_ip}:{ESP32_SERVER_PORT}"
        client = HttpClient(url)
        try:
            r1 = client.get('/test1').wait()
            r2 = client.get('/test2').wait()
            r3 = client.get('/test3').wait()

            self.assertEqual(r1.status, 200)
            self.assertEqual(r2.status, 200)
            self.assertEqual(r3.status, 200)

            self.assertEqual(r1.json()['path'], '/test1')
            self.assertEqual(r2.json()['path'], '/test2')
            self.assertEqual(r3.json()['path'], '/test3')
        finally:
            client.close()


if __name__ == '__main__':
    unittest.main()
