#!/usr/bin/env python3
"""
Test that connections with in-progress response are not timed out with 408.

When a connection is sending a large response (e.g., file streaming),
the keep-alive timeout should not interrupt it with a 408 response.
"""
import unittest
import socket
import time
import tempfile
import os
from uhttp import server as uhttp_server


class TestTimeoutDuringResponse(unittest.TestCase):
    """Test that _cleanup_idle_connections skips connections with _response_started"""

    PORT = 9998
    FILE_SIZE = 200_000

    def test_no_408_while_response_in_progress(self):
        """Connection streaming file response should not get 408 timeout"""
        temp_dir = tempfile.mkdtemp()
        large_file = os.path.join(temp_dir, 'large.bin')
        with open(large_file, 'wb') as f:
            f.write(b'X' * self.FILE_SIZE)

        server = uhttp_server.HttpServer(
            port=self.PORT, keep_alive_timeout=0.3, file_chunk_size=512)
        trigger_sock = None

        try:
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', self.PORT))
            client_sock.setblocking(False)
            client_sock.send(
                b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            time.sleep(0.1)

            connection = None
            for _ in range(10):
                connection = server.wait(0.1)
                if connection:
                    break
            self.assertIsNotNone(connection)

            connection.respond_file(large_file)
            self.assertTrue(connection._response_started)

            # Wait longer than keep_alive_timeout
            time.sleep(0.5)

            # Trigger cleanup via new connection
            trigger_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            trigger_sock.connect(('localhost', self.PORT))
            time.sleep(0.1)

            # wait() accepts the trigger (activity) and runs maintenance,
            # which must skip the streaming connection (_response_started).
            server.wait(0.2)

            self.assertIsNotNone(
                connection.socket,
                "Connection should not be closed while response is in progress")

            # Drain the response (wait() flushes the write-ready connection)
            response = b""
            deadline = time.time() + 5
            while (connection.has_data_to_send or len(response) < self.FILE_SIZE) \
                    and time.time() < deadline:
                server.wait(0.05)
                try:
                    chunk = client_sock.recv(65536)
                    if not chunk:
                        break
                    response += chunk
                except BlockingIOError:
                    pass

            self.assertIn(b'200 OK', response)
            self.assertIn(b'X' * 100, response)

        finally:
            client_sock.close()
            if trigger_sock:
                trigger_sock.close()
            # Close server first so file handles are released (Windows)
            server.close()
            time.sleep(0.1)
            try:
                os.unlink(large_file)
                os.rmdir(temp_dir)
            except OSError:
                pass

    def test_idle_keepalive_still_gets_408(self):
        """Idle keep-alive connection (no response started) should still get 408"""
        server = uhttp_server.HttpServer(
            port=self.PORT + 1, keep_alive_timeout=0.3)
        trigger_sock = None

        try:
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', self.PORT + 1))
            client_sock.setblocking(False)

            client_sock.send(
                b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            time.sleep(0.1)

            # Process the request and respond
            connection = None
            for _ in range(10):
                connection = server.wait(0.1)
                if connection:
                    connection.respond('ok')
                    break
            self.assertIsNotNone(connection)

            # Flush send buffer
            deadline = time.time() + 2
            while connection.has_data_to_send and time.time() < deadline:
                server.wait(0.05)

            # Drain the first response
            try:
                while True:
                    client_sock.recv(4096)
            except BlockingIOError:
                pass

            # Wait for keep-alive timeout
            time.sleep(0.5)

            # Trigger cleanup
            trigger_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            trigger_sock.connect(('localhost', self.PORT + 1))
            time.sleep(0.1)

            # wait() accepts the trigger (activity) and runs maintenance,
            # which 408s the idle keep-alive connection.
            server.wait(0.2)

            # Flush 408 response
            deadline = time.time() + 2
            while connection.has_data_to_send and time.time() < deadline:
                server.wait(0.05)

            # Read the 408 response
            time.sleep(0.1)
            response = b""
            try:
                for _ in range(10):
                    chunk = client_sock.recv(4096)
                    if chunk:
                        response += chunk
                    else:
                        break
            except (BlockingIOError, ConnectionResetError, BrokenPipeError):
                pass

            self.assertIn(b'408', response)

        finally:
            client_sock.close()
            if trigger_sock:
                trigger_sock.close()
            server.close()


if __name__ == '__main__':
    unittest.main()
