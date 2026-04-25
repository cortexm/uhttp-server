#!/usr/bin/env python3
"""
Test NDJSON streaming response (server -> client)
"""
import unittest
import socket
import time
import json
import threading
from uhttp import server as uhttp_server


class TestNDJSON(unittest.TestCase):
    """Test suite for NDJSON streaming responses"""

    server = None
    server_thread = None
    nd_clients = []
    PORT = 9986

    @classmethod
    def setUpClass(cls):
        cls.server = uhttp_server.HttpServer(port=cls.PORT)

        def run_server():
            try:
                while cls.server:
                    client = cls.server.wait(timeout=0.1)

                    if client:
                        if client.path == '/stream':
                            if client.response_ndjson():
                                cls.nd_clients.append({
                                    'client': client,
                                    'counter': 0,
                                    'last_send': time.time(),
                                    'mode': 'dict',
                                })
                        elif client.path == '/stream-mixed':
                            if client.response_ndjson():
                                cls.nd_clients.append({
                                    'client': client,
                                    'counter': 0,
                                    'last_send': time.time(),
                                    'mode': 'mixed',
                                })
                        elif client.path == '/stream-headers':
                            if client.response_ndjson(
                                    headers={'X-Stream': 'ndjson'}):
                                cls.nd_clients.append({
                                    'client': client,
                                    'counter': 0,
                                    'last_send': time.time(),
                                    'mode': 'dict',
                                })
                        else:
                            client.respond("Not found", status=404)

                    for sc in list(cls.nd_clients):
                        if time.time() - sc['last_send'] > 0.05:
                            sc['counter'] += 1
                            mode = sc['mode']

                            if sc['counter'] >= 4:
                                sc['client'].response_stream_end()
                                cls.nd_clients.remove(sc)
                            elif mode == 'dict':
                                sc['client'].send_ndjson(
                                    {'n': sc['counter'], 'msg': 'hello'})
                                sc['last_send'] = time.time()
                            elif mode == 'mixed':
                                # rotate through different JSON types
                                values = [
                                    {'n': sc['counter']},
                                    [1, 2, sc['counter']],
                                    f'string {sc["counter"]}',
                                    sc['counter']]
                                sc['client'].send_ndjson(
                                    values[(sc['counter'] - 1) % len(values)])
                                sc['last_send'] = time.time()

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

    def setUp(self):
        TestNDJSON.nd_clients = []

    def _recv_all(self, sock, timeout=3.0):
        sock.settimeout(timeout)
        all_data = b""
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                all_data += chunk
        except socket.timeout:
            pass
        return all_data

    def _make_request(self, path):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(('localhost', self.PORT))
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode()
        sock.sendall(request)
        return sock

    def _split_response(self, data):
        """Split raw HTTP response into (headers_text, body_bytes)"""
        sep = data.find(b"\r\n\r\n")
        self.assertGreater(sep, 0, "no header/body separator")
        return data[:sep].decode(), data[sep + 4:]

    def test_stream_headers(self):
        """NDJSON response has correct content-type and cache-control"""
        sock = self._make_request('/stream')
        try:
            data = self._recv_all(sock)
            headers, _ = self._split_response(data)
            self.assertIn("200 OK", headers)
            self.assertIn("content-type: application/x-ndjson", headers)
            self.assertIn("cache-control: no-cache", headers)
        finally:
            sock.close()

    def test_custom_headers_passthrough(self):
        """Custom headers passed to response_ndjson appear in response"""
        sock = self._make_request('/stream-headers')
        try:
            data = self._recv_all(sock)
            headers, _ = self._split_response(data)
            self.assertIn("X-Stream: ndjson", headers)
            self.assertIn("content-type: application/x-ndjson", headers)
        finally:
            sock.close()

    def test_ndjson_lines_parse(self):
        """Each body line is a valid JSON object terminated by \\n"""
        sock = self._make_request('/stream')
        try:
            data = self._recv_all(sock)
            _, body = self._split_response(data)

            # body must end with \n on the last record
            self.assertTrue(body.endswith(b"\n"))

            lines = body.split(b"\n")
            # last element is empty string after trailing \n
            self.assertEqual(lines[-1], b"")
            records = [json.loads(l) for l in lines[:-1]]

            self.assertEqual(len(records), 3)
            for i, rec in enumerate(records, start=1):
                self.assertEqual(rec, {'n': i, 'msg': 'hello'})
        finally:
            sock.close()

    def test_no_embedded_newlines(self):
        """Each NDJSON line contains exactly one record (no embedded \\n)"""
        sock = self._make_request('/stream')
        try:
            data = self._recv_all(sock)
            _, body = self._split_response(data)

            for line in body.split(b"\n")[:-1]:
                self.assertNotIn(b"\n", line)
                # must be parseable on its own
                json.loads(line)
        finally:
            sock.close()

    def test_mixed_json_types(self):
        """send_ndjson accepts dict/list/str/int per-record"""
        sock = self._make_request('/stream-mixed')
        try:
            data = self._recv_all(sock)
            _, body = self._split_response(data)
            lines = body.split(b"\n")[:-1]
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[0]), {'n': 1})
            self.assertEqual(json.loads(lines[1]), [1, 2, 2])
            self.assertEqual(json.loads(lines[2]), 'string 3')
        finally:
            sock.close()

    def test_stream_end_closes_connection(self):
        """response_stream_end closes the socket after final record"""
        sock = self._make_request('/stream')
        try:
            data = self._recv_all(sock, timeout=3.0)
            _, body = self._split_response(data)
            self.assertTrue(len(body) > 0)
            remaining = sock.recv(1024)
            self.assertEqual(remaining, b"")
        finally:
            sock.close()


if __name__ == '__main__':
    unittest.main()
