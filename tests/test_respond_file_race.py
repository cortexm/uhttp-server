#!/usr/bin/env python3
"""
Test for race condition in respond_file() with keep-alive

Bug: When streaming file response, if client sends next request
before streaming is complete, server may return the same connection
as "loaded" again, causing "Response already sent" error.
"""
import unittest
import socket
import time
import tempfile
import os
import select
from uhttp import server as uhttp_server


class TestRespondFileRace(unittest.TestCase):
    """Test race condition in respond_file with keep-alive"""

    PORT = 9997

    def test_is_loaded_true_while_response_pending(self):
        """
        Direct test: after respond_file(), is_loaded should be False
        until response is fully sent and connection is reset.
        """
        temp_dir = tempfile.mkdtemp()
        test_file = os.path.join(temp_dir, 'test.txt')

        with open(test_file, 'w') as f:
            f.write("Hello World")

        server = uhttp_server.HttpServer(port=self.PORT)

        try:
            # Connect client
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', self.PORT))
            client_sock.setblocking(False)

            # Send request
            request = b"GET /file HTTP/1.1\r\nHost: localhost\r\n\r\n"
            client_sock.send(request)

            # Wait for server to receive
            time.sleep(0.1)

            # Process until we get a loaded connection
            connection = None
            for _ in range(10):
                r, _, _ = select.select(server.read_sockets, [], [], 0.1)
                if r:
                    connection = server.event_read(r)
                    if connection:
                        break

            self.assertIsNotNone(connection, "Should have received request")
            self.assertTrue(connection.is_loaded, "Connection should be loaded")

            # Respond with file - this sets _response_started = True
            connection.respond_file(test_file)

            # NOW: is_loaded should be False because response is pending
            # This is the bug - is_loaded is still True!
            self.assertFalse(
                connection.is_loaded,
                "is_loaded should be False while response is pending"
            )

        finally:
            client_sock.close()
            server.close()
            os.remove(test_file)
            os.rmdir(temp_dir)

    def test_process_request_returns_false_while_response_pending(self):
        """
        After respond_file(), process_request() should NOT return the
        same connection again until response is complete and reset.
        """
        temp_dir = tempfile.mkdtemp()
        large_file = os.path.join(temp_dir, 'large.bin')

        # Large file to ensure streaming takes time
        file_size = 100 * 1024
        with open(large_file, 'wb') as f:
            f.write(b'X' * file_size)

        # Small chunk size to slow down streaming
        server = uhttp_server.HttpServer(port=self.PORT + 1, file_chunk_size=512)

        try:
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_sock.connect(('localhost', self.PORT + 1))
            client_sock.setblocking(False)

            # Send first request
            request = b"GET /file HTTP/1.1\r\nHost: localhost\r\n\r\n"
            client_sock.send(request)
            time.sleep(0.1)

            # Get the connection
            connection = None
            for _ in range(10):
                r, _, _ = select.select(server.read_sockets, [], [], 0.1)
                if r:
                    connection = server.event_read(r)
                    if connection:
                        break

            self.assertIsNotNone(connection)

            # Start streaming file response
            connection.respond_file(large_file)

            # Connection should still have data to send
            self.assertTrue(connection.has_data_to_send)

            # Send second request immediately (keep-alive)
            client_sock.send(request)
            time.sleep(0.1)

            # Try to process again - should NOT return the same connection
            # because response is still pending
            r, _, _ = select.select(server.read_sockets, [], [], 0.1)
            if r:
                # This is where the bug manifests:
                # process_request() returns True because is_loaded is True
                # But _response_started is also True!
                new_connection = server.event_read(r)

                if new_connection is connection:
                    # Bug! We got the same connection while response is pending
                    # Now if we try to respond, we get "Response already sent"
                    with self.assertRaises(uhttp_server.HttpError) as ctx:
                        new_connection.respond("This should fail")
                    self.assertIn("already sent", str(ctx.exception))
                    self.fail(
                        "Bug reproduced: server returned connection while "
                        "response was still pending"
                    )

        finally:
            client_sock.close()
            server.close()
            os.remove(large_file)
            os.rmdir(temp_dir)


if __name__ == '__main__':
    unittest.main()
