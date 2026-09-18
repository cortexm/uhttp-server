#!/usr/bin/env python3
"""Shared helpers for the test suite"""
import socket
import time


def wait_until_listening(port, host='localhost', timeout=5.0):
    """Block until the server accepts a TCP connection on port

    Replaces a fixed sleep after starting a server thread. The listening
    socket already exists when HttpServer() returns, so this is near instant,
    and it still covers fixtures that build the server inside the thread.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def content_length(head):
    """Return the content-length of a response head, or None"""
    for line in head.decode('latin-1').split('\r\n')[1:]:
        key, _, value = line.partition(':')
        if key.strip().lower() == 'content-length':
            return int(value.strip())
    return None


def read_response(sock):
    """Read one complete HTTP response and return it as raw bytes

    The match on content-length is case-insensitive: the server sends header
    names lowercase, so a 'Content-Length:' match never completes and the
    caller ends up waiting out its socket timeout on every single response.
    """
    response = b''
    header_end = -1
    length = None
    while True:
        if header_end < 0:
            header_end = response.find(b'\r\n\r\n')
            if header_end >= 0:
                length = content_length(response[:header_end])
        if header_end >= 0:
            if length is None:
                break
            if len(response) - header_end - 4 >= length:
                break
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    return response
