#!/usr/bin/env python3
"""
Tests for EAGAIN handling in non-blocking sockets
"""
import unittest
import socket
import errno
import time
import select
from uhttp import server as uhttp_server


class TestEAGAIN(unittest.TestCase):
    """Test EAGAIN handling"""

    PORT = 9995

    def test_eagain_on_read_no_data(self):
        """
        Test EAGAIN when reading from socket with no data available.
        Non-blocking socket should raise EAGAIN, not block.
        """
        server = uhttp_server.HttpServer(port=self.PORT)

        try:
            # Connect client but don't send anything
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', self.PORT))
            client_sock.setblocking(False)

            # Accept connection on server
            time.sleep(0.1)
            r, _, _ = select.select(server.read_sockets, [], [], 0.5)
            self.assertIn(server.socket, r)

            # This should accept the connection
            server.event_read(r)

            # Now there's a connection but no data sent
            # Try to read - should get EAGAIN (handled internally)
            self.assertEqual(len(server._waiting_connections), 1)
            connection = server._waiting_connections[0]

            # Connection socket is non-blocking, reading should trigger EAGAIN
            # which is handled in _recv_to_buffer by returning early
            # This should NOT raise an exception
            try:
                connection.process_request()
            except uhttp_server.HttpDisconnected:
                self.fail("Should not raise HttpDisconnected for EAGAIN")

            # Connection should still be open
            self.assertIsNotNone(connection.socket)

        finally:
            client_sock.close()
            server.close()

    def test_eagain_on_write_buffer_full(self):
        """
        Test EAGAIN when writing to socket with full buffer.
        This requires sending large amount of data quickly.
        """
        server = uhttp_server.HttpServer(port=self.PORT + 1, file_chunk_size=64*1024)

        try:
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', self.PORT + 1))
            # Keep client blocking but with small receive buffer
            client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            client_sock.setblocking(False)

            # Send request
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
            client_sock.send(request)
            time.sleep(0.1)

            # Process request
            connection = None
            for _ in range(10):
                r, _, _ = select.select(server.read_sockets, [], [], 0.1)
                if r:
                    connection = server.event_read(r)
                    if connection:
                        break

            self.assertIsNotNone(connection)

            # Send large response - should eventually hit EAGAIN
            # Create large data (1MB)
            large_data = b'X' * (1024 * 1024)
            connection.respond(large_data)

            # Connection should have data to send (buffer not fully flushed)
            # Due to EAGAIN, not all data was sent immediately
            # This tests that EAGAIN is handled gracefully

            # Try to send more - should handle EAGAIN
            for _ in range(10):
                if not connection.has_data_to_send:
                    break
                connection.try_send()
                time.sleep(0.01)

            # Connection should still be valid
            self.assertIsNotNone(connection.socket)

        finally:
            client_sock.close()
            server.close()

    def test_multiple_eagain_cycles(self):
        """
        Test multiple read attempts with EAGAIN between data arrivals.
        Simulates slow client sending data in chunks.
        """
        server = uhttp_server.HttpServer(port=self.PORT + 2)

        try:
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', self.PORT + 2))
            client_sock.setblocking(False)

            time.sleep(0.1)

            # Accept connection
            r, _, _ = select.select(server.read_sockets, [], [], 0.5)
            server.event_read(r)

            connection = server._waiting_connections[0]

            # Send partial request
            client_sock.send(b"GET / HTTP/1.1\r\n")
            time.sleep(0.05)

            # Try to process - should get partial data, EAGAIN on second read
            r, _, _ = select.select(server.read_sockets, [], [], 0.1)
            if r:
                result = server.event_read(r)
                # Should not be loaded yet (incomplete headers)
                self.assertIsNone(result)

            # Send rest of headers
            client_sock.send(b"Host: localhost\r\n\r\n")
            time.sleep(0.05)

            # Now should complete
            r, _, _ = select.select(server.read_sockets, [], [], 0.1)
            if r:
                result = server.event_read(r)
                self.assertIsNotNone(result)
                self.assertTrue(result.is_loaded)

        finally:
            client_sock.close()
            server.close()


class TestEAGAINWithMock(unittest.TestCase):
    """Test EAGAIN handling with mocked socket"""

    def test_recv_eagain_returns_none(self):
        """Test that _recv_to_buffer handles EAGAIN by returning early"""
        import io

        # Create a mock socket that raises EAGAIN
        class MockSocket:
            def __init__(self):
                self.fileno_val = 999

            def fileno(self):
                return self.fileno_val

            def recv(self, size):
                err = OSError()
                err.errno = errno.EAGAIN
                raise err

            def send(self, data):
                return len(data)

            def setblocking(self, val):
                pass

            def close(self):
                self.fileno_val = -1

        # Create minimal server mock
        class MockServer:
            def __init__(self):
                self._ssl_context = None

            def remove_connection(self, conn):
                pass

        mock_server = MockServer()
        mock_socket = MockSocket()

        # Create connection with mock socket
        connection = uhttp_server.HttpConnection(
            mock_server, mock_socket, ('127.0.0.1', 12345))

        # _recv_to_buffer should handle EAGAIN gracefully
        # It should return without raising exception
        try:
            connection._recv_to_buffer(1024)
        except uhttp_server.HttpDisconnected:
            self.fail("Should not raise HttpDisconnected for EAGAIN")

        # Buffer should be empty (no data received)
        self.assertEqual(len(connection._buffer), 0)

    def test_send_eagain_returns_false(self):
        """Test that try_send handles EAGAIN by returning False"""

        class MockSocket:
            def __init__(self):
                self.fileno_val = 999

            def fileno(self):
                return self.fileno_val

            def recv(self, size):
                return b''

            def send(self, data):
                err = OSError()
                err.errno = errno.EAGAIN
                raise err

            def setblocking(self, val):
                pass

            def close(self):
                self.fileno_val = -1

        class MockServer:
            def __init__(self):
                self._ssl_context = None

            def remove_connection(self, conn):
                pass

        mock_server = MockServer()
        mock_socket = MockSocket()

        connection = uhttp_server.HttpConnection(
            mock_server, mock_socket, ('127.0.0.1', 12345))

        # Add data to send buffer
        connection._send_buffer.extend(b'test data')

        # try_send should return False on EAGAIN (not all sent)
        result = connection.try_send()
        self.assertFalse(result)

        # Data should still be in buffer
        self.assertEqual(len(connection._send_buffer), 9)

        # Socket should still be open
        self.assertIsNotNone(connection._socket)


if __name__ == '__main__':
    unittest.main()
