#!/usr/bin/env python3
"""Lossless access to repeated header field lines (3.x)."""
import socket
import unittest

from uhttp import server as uhttp_server


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


class HeadersAllTestCase(unittest.TestCase):
    PORT = 9760

    def request(self, raw, port=None):
        """Send a raw request, return the loaded connection and its socket."""
        port = self.PORT if port is None else port
        server = uhttp_server.HttpServer(port=port)
        sock = connect(port)
        self.addCleanup(server.close)
        self.addCleanup(sock.close)
        sock.sendall(raw)
        client = drive(server)
        self.assertIsNotNone(client)
        return client, sock


class TestHeadersAll(HeadersAllTestCase):

    def test_repeated_header_keeps_every_value(self):
        client, _ = self.request(
            b"GET / HTTP/1.1\r\nHost: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1\r\n"
            b"X-Forwarded-For: 10.0.0.2\r\n\r\n")
        self.assertEqual(
            client.headers_all('X-Forwarded-For'), ['10.0.0.1', '10.0.0.2'])

    def test_combined_value_stays_a_string(self):
        client, _ = self.request(
            b"GET / HTTP/1.1\r\nHost: localhost\r\n"
            b"X-Forwarded-For: 10.0.0.1\r\n"
            b"X-Forwarded-For: 10.0.0.2\r\n\r\n", port=self.PORT + 1)
        self.assertEqual(
            client.headers_get_attribute('x-forwarded-for'),
            '10.0.0.1, 10.0.0.2')

    def test_single_header_is_a_one_item_list(self):
        client, _ = self.request(
            b"GET / HTTP/1.1\r\nHost: localhost\r\n"
            b"X-Token: abc\r\n\r\n", port=self.PORT + 2)
        self.assertEqual(client.headers_all('x-token'), ['abc'])

    def test_missing_header_is_an_empty_list(self):
        client, _ = self.request(
            b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n",
            port=self.PORT + 3)
        self.assertEqual(client.headers_all('x-nothing'), [])

    def test_value_containing_a_comma_is_not_split(self):
        client, _ = self.request(
            b"GET / HTTP/1.1\r\nHost: localhost\r\n"
            b"Accept: text/html, application/xml\r\n"
            b"Accept: text/plain\r\n\r\n", port=self.PORT + 4)
        self.assertEqual(
            client.headers_all('accept'),
            ['text/html, application/xml', 'text/plain'])

    def test_repeated_cookie_lines_are_separate(self):
        client, _ = self.request(
            b"GET / HTTP/1.1\r\nHost: localhost\r\n"
            b"Cookie: a=1\r\nCookie: b=2\r\n\r\n", port=self.PORT + 5)
        self.assertEqual(client.headers_all('cookie'), ['a=1', 'b=2'])
        self.assertEqual(client.cookies, {'a': '1', 'b': '2'})

    def test_values_do_not_leak_into_the_next_keepalive_request(self):
        client, sock = self.request(
            b"GET /1 HTTP/1.1\r\nHost: localhost\r\n"
            b"X-Trace: one\r\nX-Trace: two\r\n\r\n", port=self.PORT + 6)
        self.assertEqual(client.headers_all('x-trace'), ['one', 'two'])
        client.respond('ok')
        sock.sendall(b"GET /2 HTTP/1.1\r\nHost: localhost\r\n\r\n")
        server = client._server
        second = drive(server)
        self.assertIsNotNone(second)
        self.assertEqual(second.path, '/2')
        self.assertEqual(second.headers_all('x-trace'), [])


if __name__ == '__main__':
    unittest.main()
