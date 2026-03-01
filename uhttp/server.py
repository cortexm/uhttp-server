"""uHttp - Micro HTTP Server
python or micropython
(c) 2022-2024 Pavel Revak <pavelrevak@gmail.com>
"""

import os as _os
import errno
import socket as _socket
import select as _select
import json as _json
import time as _time

KB = 2 ** 10
MB = 2 ** 20
GB = 2 ** 30

LISTEN_SOCKETS = 2
MAX_WAITING_CLIENTS = 5
MAX_HEADERS_LENGTH = 4 * KB
MAX_CONTENT_LENGTH = 512 * KB
FILE_CHUNK_SIZE = 4 * KB  # bytes - chunk size for streaming file responses
KEEP_ALIVE_TIMEOUT = 15  # seconds
KEEP_ALIVE_MAX_REQUESTS = 100  # max requests per connection

HEADERS_DELIMITERS = (b'\n\r\n', b'\n\n')
BOUNDARY = 'frame'
CONTENT_LENGTH = 'content-length'
CONTENT_TYPE = 'content-type'
CONTENT_TYPE_XFORMDATA = 'application/x-www-form-urlencoded'
CONTENT_TYPE_HTML_UTF8 = 'text/html; charset=UTF-8'
CONTENT_TYPE_JSON = 'application/json'
CONTENT_TYPE_OCTET_STREAM = 'application/octet-stream'
CONTENT_TYPE_MULTIPART_REPLACE = (
    'multipart/x-mixed-replace; boundary=' + BOUNDARY)
CACHE_CONTROL = 'cache-control'
CACHE_CONTROL_NO_CACHE = 'no-cache'
LOCATION = 'Location'
CONNECTION = 'connection'
CONNECTION_CLOSE = 'close'
CONNECTION_KEEP_ALIVE = 'keep-alive'
COOKIE = 'cookie'
SET_COOKIE = 'set-cookie'
HOST = 'host'
EXPECT = 'expect'
EXPECT_100_CONTINUE = '100-continue'
CONTENT_TYPE_MAP = {
    'html': CONTENT_TYPE_HTML_UTF8,
    'htm': CONTENT_TYPE_HTML_UTF8,
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'svg': 'image/svg+xml',
    'webp': 'image/webp',
    'ico': 'image/x-icon',
    'bmp': 'image/bmp',
}
METHODS = (
    'CONNECT', 'DELETE', 'GET', 'HEAD', 'OPTIONS', 'PATCH', 'POST',
    'PUT', 'TRACE')
PROTOCOLS = ('HTTP/1.0', 'HTTP/1.1')

# Event mode constants
EVENT_REQUEST = 0   # Complete request (headers + body)
EVENT_HEADERS = 1   # Headers received, waiting for accept_body()
EVENT_DATA = 2      # Data available in buffer, call read_buffer()
EVENT_COMPLETE = 3  # Body fully received
EVENT_ERROR = 4     # Error occurred (timeout, disconnect)
STATUS_CODES = {
    100: "Continue",
    200: "OK",
    201: "Created",
    202: "Accepted",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content",
    300: "Multiple Choices",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    408: "Request Timeout",
    410: "Gone",
    411: "Length Required",
    413: "Payload Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    416: "Range Not Satisfiable",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    503: "Service Unavailable",
    505: "HTTP Version Not Supported",
    507: "Insufficient Storage",
}


class ClientError(Exception):
    """Server error"""


class HttpError(ClientError):
    """uHttp error"""


class HttpDisconnected(HttpError):
    """uHttp error"""


class HttpErrorWithResponse(HttpError):
    """uHttp errpr with result"""

    def __init__(self, status=500, message=None):
        msg = str(status)
        if status in STATUS_CODES:
            msg += " " + STATUS_CODES[status]
        if message:
            msg += ": " + message
        super().__init__(msg)
        self._status = status

    @property
    def status(self):
        """Result status code"""
        return self._status


def decode_percent_encoding(data):
    """Decode percent encoded data (bytes)"""
    if b'%' not in data:
        return data.replace(b'+', b' ')
    res = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == 37 and i + 2 < n:  # '%'
            try:
                res.append(int(bytes(data[i+1:i+3]), 16))
                i += 3
                continue
            except ValueError:
                pass
        res.append(32 if b == 43 else b)  # '+' -> ' '
        i += 1
    return bytes(res)


def split_iter(data, sep):
    """Split data by separator, yielding parts without allocating full list"""
    start = 0
    while True:
        pos = data.find(sep, start)
        if pos == -1:
            yield data[start:]
            break
        yield data[start:pos]
        start = pos + len(sep)


def parse_header_parameters(value):
    """Parse parameters/directives from header value, returns dict"""
    directives = {}
    for part in split_iter(value, ';'):
        if '=' in part:
            key, val = part.split('=', 1)
            directives[key.strip()] = val.strip().strip('"')
        elif part:
            directives[part.strip()] = None
    return directives


def parse_query(raw_query, query=None):
    """Parse raw_query from URL, append it to existing query, returns dict"""
    if query is None:
        query = {}
    for query_part in split_iter(raw_query, b'&'):
        if query_part:
            try:
                if b'=' in query_part:
                    key, val = query_part.split(b'=', 1)
                    key = decode_percent_encoding(key).decode('utf-8')
                    val = decode_percent_encoding(val).decode('utf-8')
                else:
                    key = decode_percent_encoding(query_part).decode('utf-8')
                    val = None
            except (UnicodeError, ValueError) as err:
                raise HttpErrorWithResponse(
                    400, "Invalid query string encoding") from err
            if key not in query:
                query[key] = val
            elif isinstance(query[key], list):
                query[key].append(val)
            else:
                query[key] = [query[key], val]
    return query


def parse_url(url):
    """Parse URL to path and query"""
    query = None
    if b'?' in url:
        path, raw_query = url.split(b'?', 1)
        query = parse_query(raw_query, query)
    else:
        path = url
    try:
        path = decode_percent_encoding(path).decode('utf-8')
    except (UnicodeError, ValueError) as err:
        raise HttpErrorWithResponse(
            400, "Invalid URL path encoding") from err
    return path, query


def parse_header_line(line):
    """Parse header line to key and value"""
    try:
        line = line.decode('ascii')
    except ValueError as err:
        readable = line.decode('utf-8', errors='replace')
        raise HttpErrorWithResponse(
            400, f"Invalid non-ASCII characters in header: {readable}") from err
    if ':' not in line:
        raise HttpErrorWithResponse(400, f"Wrong header format {line}")
    key, val = line.split(':', 1)
    return key.strip().lower(), val.strip()


def encode_response_data(headers, data):
    """encode response data by its type"""
    if isinstance(data, (dict, list, tuple, int, float)):
        data = _json.dumps(data).encode('ascii')
        if CONTENT_TYPE not in headers:
            headers[CONTENT_TYPE] = CONTENT_TYPE_JSON
    elif isinstance(data, str):
        data = data.encode('utf-8')
        if CONTENT_TYPE not in headers:
            headers[CONTENT_TYPE] = CONTENT_TYPE_HTML_UTF8
    elif isinstance(data, (bytes, bytearray, memoryview)):
        if CONTENT_TYPE not in headers:
            headers[CONTENT_TYPE] = CONTENT_TYPE_OCTET_STREAM
    else:
        raise HttpErrorWithResponse(415, f"Unsupported data type: {type(data).__name__}")
    headers[CONTENT_LENGTH] = len(data)
    return data


class HttpConnection():
    """Simple HTTP client connection"""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, server, sock, addr, **kwargs):
        """sock - client socket, addr - tuple (ip, port)"""
        self._server = server
        self._addr = addr
        self._socket = sock
        self._buffer = bytearray()
        self._send_buffer = bytearray()
        self._rx_bytes_counter = 0
        self._method = None
        self._url = None
        self._protocol = None
        self._headers = None
        self._data = None
        self._path = None
        self._query = None
        self._content_length = None
        self._cookies = None
        self._is_multipart = False
        self._response_started = False
        self._response_keep_alive = False
        self._file_handle = None
        self._last_activity = _time.time()
        self._requests_count = 0
        # Event mode attributes
        self.context = None
        self._event = None
        self._bytes_received = 0
        self._error = None
        self._streaming_body = False
        self._streaming_events = False
        self._body_complete = False
        self._body_file_handle = None
        self._to_file = None
        self._expect_continue = False
        # Config from kwargs
        self._max_headers_length = kwargs.get(
            'max_headers_length', MAX_HEADERS_LENGTH)
        self._max_content_length = kwargs.get(
            'max_content_length', MAX_CONTENT_LENGTH)
        self._file_chunk_size = kwargs.get(
            'file_chunk_size', FILE_CHUNK_SIZE)
        self._keep_alive_timeout = kwargs.get(
            'keep_alive_timeout', KEEP_ALIVE_TIMEOUT)
        self._keep_alive_max_requests = kwargs.get(
            'keep_alive_max_requests', KEEP_ALIVE_MAX_REQUESTS)

    def __del__(self):
        self.close()

    def __repr__(self):
        result = f"HttpConnection: [{self.remote_address}] {self.method}"
        result += f" http://{self.full_url}"
        return result

    @property
    def addr(self):
        """Client address"""
        return self._addr

    @property
    def remote_address(self):
        """Return client address"""
        forwarded = self.headers_get_attribute('x-forwarded-for')
        if forwarded:
            return forwarded.split(',')[0]
        addr = self._addr[0]
        if addr.startswith('::ffff:'):
            addr = addr[7:]  # Remove IPv4-mapped prefix
        return f"{addr}:{self._addr[1]}"

    @property
    def remote_addresses(self):
        """Return client address"""
        forwarded = self.headers_get_attribute('x-forwarded-for')
        if forwarded:
            return forwarded
        return f"{self._addr[0]}:{self._addr[1]}"

    @property
    def is_secure(self):
        """Return True if connection is using SSL/TLS"""
        return self._server.is_secure

    @property
    def method(self):
        """HTTP method"""
        return self._method

    @property
    def url(self):
        """URL address"""
        return self._url

    @property
    def host(self):
        """URL address"""
        return self.headers_get_attribute(HOST, '')

    @property
    def full_url(self):
        """URL address"""
        return f"{self.host}{self.url}"

    @property
    def protocol(self):
        """Protocol"""
        return self._protocol

    @property
    def headers(self):
        """headers dict"""
        return self._headers

    @property
    def data(self):
        """Content data"""
        return self._data

    @property
    def path(self):
        """Path"""
        return self._path

    @property
    def query(self):
        """Query dict"""
        return self._query

    @property
    def cookies(self):
        """Cookies dict"""
        if self._cookies is None:
            self._cookies = {}
            raw_cookies = self.headers_get_attribute(COOKIE)
            if raw_cookies:
                for cookie_param in split_iter(raw_cookies, ';'):
                    if '=' in cookie_param:
                        key, val = cookie_param.split('=')
                        key = key.strip()
                        if key:
                            self._cookies[key] = val.strip()
        return self._cookies

    @property
    def socket(self):
        """This socket"""
        return self._socket

    @property
    def rx_bytes_counter(self):
        """Read bytes counter"""
        return self._rx_bytes_counter

    @property
    def is_loaded(self):
        """True when request is fully loaded and ready for response"""
        if self._response_started:
            return False
        return self._method and (not self.content_length or self._data)

    @property
    def is_timed_out(self):
        """True when connection has been idle too long"""
        return (_time.time() - self._last_activity) > self._keep_alive_timeout

    @property
    def is_max_requests_reached(self):
        """True when connection reached max requests limit"""
        return self._requests_count >= self._keep_alive_max_requests

    @property
    def has_data_to_send(self):
        """True when there is data waiting to be sent or file being streamed"""
        return len(self._send_buffer) > 0 or self._file_handle is not None

    @property
    def send_buffer_size(self):
        """Size of pending send buffer in bytes"""
        return len(self._send_buffer)

    @property
    def event(self):
        """Current event type (EVENT_REQUEST, EVENT_HEADERS, etc.)"""
        return self._event

    @property
    def bytes_received(self):
        """Number of body bytes received so far"""
        return self._bytes_received

    @property
    def error(self):
        """Error message if event is EVENT_ERROR"""
        return self._error

    @property
    def content_type(self):
        """Content type"""
        return self.headers_get_attribute(CONTENT_TYPE, '')

    @property
    def content_length(self):
        """Content length"""
        if self._headers is None:
            return None
        if self._content_length is None:
            content_length = self.headers_get_attribute(CONTENT_LENGTH)
            if content_length is None:
                self._content_length = False
            elif content_length.isdigit():
                self._content_length = int(content_length)
            else:
                raise HttpErrorWithResponse(
                    400, f"Wrong content length {content_length}")
        return self._content_length

    def headers_get_attribute(self, key, default=None):
        """Return headers value"""
        if self._headers:
            return self._headers.get(key, default)
        return default

    def _recv_to_buffer(self, size):
        try:
            buffer = self._socket.recv(size - len(self._buffer))
        except OSError as err:
            if err.errno in (errno.EAGAIN, errno.ENOENT):
                # EAGAIN: no data available (non-blocking)
                # ENOENT: SSL handshake in progress (CPython)
                return
            raise HttpDisconnected(f"{err}: {self.addr}") from err
        except MemoryError as err:
            raise HttpErrorWithResponse(413) from err
        if buffer is None:
            # MicroPython SSL: handshake in progress
            return
        if not buffer:
            raise HttpDisconnected(f"Lost connection from client {self.addr}")
        self._rx_bytes_counter += len(buffer)
        self._buffer.extend(buffer)
        self.update_activity()

    def _parse_http_request(self, line):
        if line.count(b' ') != 2:
            readable = line.decode('utf-8', errors='replace')
            raise HttpError(f"Malformed request line: {readable}")
        method, url, protocol = line.strip().split(b' ')
        try:
            self._method = method.decode('ascii')
            self._url = url.decode('ascii')
            self._protocol = protocol.decode('ascii')
        except ValueError as err:
            readable = line.decode('utf-8', errors='replace')
            raise HttpErrorWithResponse(
                400, f"Invalid characters in request line: {readable}") from err
        if self._method not in METHODS:
            raise HttpErrorWithResponse(501)
        if self._protocol not in PROTOCOLS:
            raise HttpErrorWithResponse(505)
        self._path, self._query = parse_url(url)

    def _process_data(self):
        if len(self._buffer) < self.content_length:
            return

        if len(self._buffer) > self.content_length:
            raise HttpErrorWithResponse(400, "Unexpected data after body")

        content_type_parts = parse_header_parameters(self.content_type)
        if CONTENT_TYPE_XFORMDATA in content_type_parts:
            self._data = parse_query(self._buffer)
        elif CONTENT_TYPE_JSON in content_type_parts:
            try:
                self._data = _json.loads(self._buffer)
            except ValueError as err:
                raise HttpErrorWithResponse(
                    400, f"JSON decode error: {err}") from err
        else:
            self._data = self._buffer
        self._buffer = bytearray()

    def _process_headers(self, header_lines):
        self._headers = {}
        while header_lines:
            line = header_lines.pop(0)
            if not line:
                break
            if self._method is None:
                self._parse_http_request(line)
            else:
                key, val = parse_header_line(line)
                self._headers[key] = val

        # RFC 2616: HTTP/1.1 requires Host header
        if self._protocol == 'HTTP/1.1' and 'host' not in self._headers:
            raise HttpErrorWithResponse(
                400, "Host header is required for HTTP/1.1")

        # Handle Expect: 100-continue
        expect = self.headers_get_attribute(EXPECT, '').lower()
        if expect == EXPECT_100_CONTINUE and self.content_length:
            self._expect_continue = True
            if not self._server.event_mode:
                # Non-event mode: send 100 Continue immediately
                self._send_100_continue()

        if self.content_length:
            if self.content_length > self._max_content_length:
                raise HttpErrorWithResponse(413)
            self._process_data()

    def _read_headers(self):
        self._recv_to_buffer(self._max_headers_length)
        for delimiter in HEADERS_DELIMITERS:
            if delimiter in self._buffer:
                end_index = self._buffer.index(delimiter) + len(delimiter)
                header_lines = self._buffer[:end_index].splitlines()
                self._buffer = self._buffer[end_index:]
                self._process_headers(header_lines)
                return
        if len(self._buffer) >= self._max_headers_length:
            raise HttpErrorWithResponse(
                431,
                f"Headers too large: {len(self._buffer)} bytes (max {self._max_headers_length})")

    def _send(self, data):
        """Add data to send buffer for async sending"""
        if self._socket is None:
            return
        if isinstance(data, str):
            data = data.encode('ascii')
        self._send_buffer.extend(data)
        self.try_send()

    def _send_100_continue(self):
        """Send 100 Continue response if client expects it"""
        if not self._expect_continue:
            return
        self._expect_continue = False
        self._send('HTTP/1.1 100 Continue\r\n\r\n')

    def _close_file_handle(self):
        """Close file handle safely"""
        if self._file_handle:
            try:
                self._file_handle.close()
            except OSError:
                pass
            self._file_handle = None

    def _refill_from_file(self):
        """Read next chunk from file into send buffer.
        Returns False if error occurred and connection was closed."""
        if not self._file_handle:
            return True
        if len(self._send_buffer) >= self._file_chunk_size:
            return True
        try:
            chunk = self._file_handle.read(self._file_chunk_size)
            if chunk:
                self._send_buffer.extend(chunk)
            else:
                self._close_file_handle()
        except OSError:
            self._close_file_handle()
            self.close()
            return False
        return True

    def _flush_send_buffer(self):
        """Try to send data from buffer.
        Returns True if buffer is empty."""
        if not self._send_buffer:
            return True
        try:
            sent = self._socket.send(self._send_buffer)
            # MicroPython SSL may return None when buffer full
            if sent is None:
                return False
            if sent > 0:
                self._send_buffer = self._send_buffer[sent:]
            return len(self._send_buffer) == 0
        except OSError as err:
            if err.errno == errno.EAGAIN:
                return False
            self.close()
            return False

    def try_send(self):
        """Try to send data, finalize when complete"""
        if self._socket is None:
            return

        if not self._refill_from_file():
            return

        if self._flush_send_buffer() and self._file_handle is None:
            self._finalize_sent_response()

    def update_activity(self):
        """Update last activity timestamp"""
        self._last_activity = _time.time()

    def _should_keep_alive(self, response_headers=None):
        """Determine if connection should be kept alive

        Args:
            response_headers: Optional dict of response headers
                    to check for explicit Connection header

        Returns:
            bool: True if connection should be kept alive
        """
        if response_headers and CONNECTION in response_headers:
            return response_headers[CONNECTION].lower() == CONNECTION_KEEP_ALIVE

        req_connection = self.headers_get_attribute(CONNECTION, '').lower()

        if self._protocol == 'HTTP/1.1':
            keep_alive = req_connection != CONNECTION_CLOSE
        else:
            keep_alive = req_connection == CONNECTION_KEEP_ALIVE

        if keep_alive and self.is_max_requests_reached:
            keep_alive = False

        return keep_alive

    def _finalize_sent_response(self):
        """Finalize connection after response fully sent (no buffered data)"""
        if not self._response_started:
            return

        if self._is_multipart:
            return

        if self._response_keep_alive:
            self.reset()
        else:
            self.close()

    def reset(self):
        """Reset connection for next request (keep-alive)"""
        self._close_file_handle()
        self._close_body_file()
        self._method = None
        self._url = None
        self._protocol = None
        self._headers = None
        self._data = None
        self._path = None
        self._query = None
        self._content_length = None
        self._cookies = None
        self._is_multipart = False
        self._response_started = False
        self._response_keep_alive = False
        # Reset event mode attributes
        self.context = None
        self._event = None
        self._bytes_received = 0
        self._error = None
        self._streaming_body = False
        self._streaming_events = False
        self._body_complete = False
        self._to_file = None
        self._expect_continue = False
        self.update_activity()

    def close(self):
        """Close connection"""
        self._close_file_handle()
        self._close_body_file(delete=True)
        self._server.remove_connection(self)
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
            self._send_buffer[:] = b''

    def headers_get(self, key, default=None):
        """Return value from headers by key, or default if key not found"""
        return self._headers.get(key.lower(), default)

    def process_request(self):
        """Process HTTP request when read event on client socket"""
        if self._socket is None:
            return None
        if self._is_multipart:
            return False
        try:
            if self._method is None:
                self._read_headers()
            elif self.content_length:
                self._recv_to_buffer(self.content_length)
                self._process_data()
            if self.is_loaded:
                self._requests_count += 1
            return self.is_loaded
        except HttpErrorWithResponse as err:
            self.respond(
                data=str(err), status=err.status,
                headers={CONNECTION: CONNECTION_CLOSE})
        except ClientError:
            self.close()
        return None

    def process_request_event(self):
        """Process HTTP request in event mode.

        Returns True if event is ready, False if waiting, None on error.
        """
        if self._socket is None:
            return None
        if self._is_multipart:
            return False

        try:
            return self._process_event()
        except HttpErrorWithResponse as err:
            self._error = str(err)
            self._event = EVENT_ERROR
            return True
        except ClientError as err:
            # Client disconnect on keep-alive while waiting for next request
            # is normal - just close silently
            if self._requests_count > 0 and self._method is None:
                self.close()
                return None
            self._error = str(err)
            self._event = EVENT_ERROR
            return True

    def _process_event(self):
        """Internal event processing logic"""
        # Phase 1: Reading headers
        if self._method is None:
            self._read_headers()
            if self._method is None:
                return False  # Headers not complete yet
            return self._handle_headers_complete()

        # Phase 2: Streaming body
        if self._streaming_body:
            return self._handle_streaming_body()

        # Phase 3: Waiting for accept_body() call
        return False

    def _handle_headers_complete(self):
        """Handle completed headers, decide event type"""
        if not self.content_length:
            # No body - complete request
            self._event = EVENT_REQUEST
            self._requests_count += 1
            return True

        # Check if small body already arrived with headers
        # _data may be set by _process_headers() or buffer may have the data
        if self._data is not None or len(self._buffer) >= self.content_length:
            if self._data is None:
                self._process_data()
            self._event = EVENT_REQUEST
            self._requests_count += 1
            return True

        # Body expected but not complete - notify headers ready
        self._event = EVENT_HEADERS
        return True

    def _handle_streaming_body(self):
        """Handle streaming body data"""
        self._recv_to_buffer(self._max_content_length)

        if not self._buffer:
            return False  # No new data

        # Write to file if in file mode
        if self._body_file_handle:
            self._write_buffer_to_file()
            if self._event == EVENT_ERROR:
                return True

        # Check if body is complete
        total = self._bytes_received + len(self._buffer)
        if self.content_length and total >= self.content_length:
            self._body_complete = True
            self._event = EVENT_COMPLETE
            self._requests_count += 1
            return True

        # If not streaming events, keep buffering until complete
        if not self._streaming_events:
            return False

        self._event = EVENT_DATA
        return True

    def _write_buffer_to_file(self):
        """Write buffer to body file handle"""
        try:
            self._body_file_handle.write(self._buffer)
            self._bytes_received += len(self._buffer)
            self._buffer = bytearray()
        except OSError as err:
            self._close_body_file(delete=True)
            self._error = f"Failed to write file: {err}"
            self._event = EVENT_ERROR

    def _build_response_header(self, status=200, headers=None, cookies=None):
        """Build HTTP response header string

        Connection header is added automatically based on keep-alive decision if not explicitly set.
        To force connection close, set headers['connection'] = 'close'.
        """
        parts = [f'{PROTOCOLS[-1]} {status} {STATUS_CODES[status]}']

        if headers:
            for key, val in headers.items():
                parts.append(f'{key}: {val}')

        if cookies:
            for key, val in cookies.items():
                if val is None:
                    val = '; Max-Age=0'
                parts.append(f'{SET_COOKIE}: {key}={val}')

        parts.append('\r\n')
        return '\r\n'.join(parts)

    def _prepare_response(self, headers=None, is_multipart=False):
        """Common response preparation, returns headers dict"""
        if self._response_started:
            raise HttpError("Response already sent for this request")
        self._response_started = True
        self._is_multipart = is_multipart

        if headers is None:
            headers = {}

        if not is_multipart:
            keep_alive = self._should_keep_alive(headers)
            if CONNECTION not in headers:
                headers[CONNECTION] = (
                    CONNECTION_KEEP_ALIVE if keep_alive else CONNECTION_CLOSE)
            self._response_keep_alive = keep_alive

        return headers

    def _accept_body_common(self):
        """Common setup for accept_body methods.

        Returns:
            int: Number of bytes already waiting in buffer.

        Raises:
            HttpError: If called outside of EVENT_HEADERS state.
        """
        if self._event != EVENT_HEADERS:
            raise HttpError("accept_body() can only be called after EVENT_HEADERS")
        self._streaming_body = True
        self._send_100_continue()
        return len(self._buffer)

    def accept_body(self):
        """Accept incoming body data, buffer all and receive EVENT_COMPLETE.

        Must be called after receiving EVENT_HEADERS to start receiving body.
        All data is buffered internally. When complete, EVENT_COMPLETE is emitted
        and data can be read with read_buffer().

        Returns:
            int: Number of bytes already waiting in buffer.
        """
        return self._accept_body_common()

    def accept_body_streaming(self):
        """Accept incoming body data with streaming events.

        Must be called after receiving EVENT_HEADERS to start receiving body.
        Emits EVENT_DATA for each chunk received. Call read_buffer() to get data.
        When complete, EVENT_COMPLETE is emitted.

        Returns:
            int: Number of bytes already waiting in buffer.
        """
        pending = self._accept_body_common()
        self._streaming_events = True
        return pending

    def accept_body_to_file(self, path):
        """Accept incoming body data and save directly to file.

        Must be called after receiving EVENT_HEADERS to start receiving body.
        Data is written to file as it arrives. When complete, EVENT_COMPLETE
        is emitted. No EVENT_DATA events are sent.

        Args:
            path: Path to file where body will be saved.

        Returns:
            int: Number of bytes already waiting in buffer.
        """
        pending = self._accept_body_common()
        self._to_file = path

        try:
            self._body_file_handle = open(path, 'wb')
        except OSError as err:
            self._error = f"Failed to open file: {err}"
            self._event = EVENT_ERROR
            return 0

        return pending

    def read_buffer(self):
        """Read available data from buffer.

        Returns:
            bytes or None: Data from buffer, or None if no data available.
        """
        if not self._buffer:
            return None
        chunk = bytes(self._buffer)
        self._bytes_received += len(chunk)
        self._buffer = bytearray()
        return chunk

    def _close_body_file(self, delete=False):
        """Close body file handle safely"""
        if hasattr(self, '_body_file_handle') and self._body_file_handle:
            try:
                self._body_file_handle.close()
            except OSError:
                pass
            self._body_file_handle = None
            if delete and hasattr(self, '_to_file') and self._to_file:
                try:
                    _os.remove(self._to_file)
                except OSError:
                    pass

    def respond(self, data=None, status=200, headers=None, cookies=None):
        """Create general respond with data, status and headers as dict

        To force connection close, set headers['connection'] = 'close'.
        By default, HTTP/1.1 uses keep-alive, HTTP/1.0 closes connection.
        """
        if self._socket is None:
            return
        headers = self._prepare_response(headers)
        if data:
            data = encode_response_data(headers, data)

        header = self._build_response_header(status, headers=headers, cookies=cookies)
        try:
            if data:
                header_bytes = header.encode('ascii') if isinstance(header, str) else header
                self._send(header_bytes + data)
            else:
                self._send(header)
            if not self.has_data_to_send:
                self._finalize_sent_response()
        except OSError:
            self.close()

    def respond_file(self, file_name, headers=None):
        """Respond with file content, streaming asynchronously to minimize memory usage

        To force connection close, set headers['connection'] = 'close'.
        """
        try:
            file_size = _os.stat(file_name)[6]  # st_size
        except (OSError, ImportError, AttributeError):
            self.respond(data=f'File not found: {file_name}', status=404)
            return

        headers = self._prepare_response(headers)

        if CONTENT_TYPE not in headers:
            ext = file_name.lower().split('.')[-1] if '.' in file_name else ''
            headers[CONTENT_TYPE] = CONTENT_TYPE_MAP.get(ext, CONTENT_TYPE_OCTET_STREAM)
        headers[CONTENT_LENGTH] = file_size

        header = self._build_response_header(200, headers=headers)

        try:
            self._file_handle = open(file_name, 'rb')
            self._send(header)
        except OSError:
            self._close_file_handle()
            self.close()

    def response_multipart(self, headers=None):
        """Create multipart respond with headers as dict"""
        if self._socket is None:
            return False
        headers = self._prepare_response(headers, is_multipart=True)

        if CONTENT_TYPE not in headers:
            headers[CONTENT_TYPE] = CONTENT_TYPE_MULTIPART_REPLACE

        header = self._build_response_header(200, headers=headers)
        try:
            self._send(header)
        except OSError:
            self.close()
            return False
        return True

    def response_multipart_frame(self, data, headers=None, boundary=None):
        """Create multipart frame respond with data and headers as dict"""
        if self._socket is None:
            return False
        if not data:
            self.response_multipart_end()
            return False
        if not boundary:
            boundary = BOUNDARY
        if headers is None:
            headers = {}
        data = encode_response_data(headers, data)
        parts = [f'--{boundary}']
        for key, val in headers.items():
            parts.append(f'{key}: {val}')
        parts.append('\r\n')
        header = '\r\n'.join(parts)
        try:
            self._send(header)
            self._send(data)
            self._send('\r\n')
        except OSError:
            self.close()
            return False
        return True

    def response_multipart_end(self, boundary=None):
        """Finish multipart stream"""
        if not boundary:
            boundary = BOUNDARY
        self._is_multipart = False

        # Determine keep-alive behavior (multipart was started without Connection header)
        # Use default protocol behavior
        keep_alive = self._should_keep_alive()
        self._response_keep_alive = keep_alive

        try:
            self._send(f'--{boundary}--\r\n')
            if not self.has_data_to_send:
                self._finalize_sent_response()
        except OSError:
            self.close()

    def respond_redirect(self, url, status=302, cookies=None):
        """Create redirect respond to URL"""
        self.respond(status=status, headers={LOCATION: url}, cookies=cookies)


class HttpServer():
    """HTTP server"""

    def __init__(
            self, address='0.0.0.0', port=80, ssl_context=None,
            event_mode=False, **kwargs):
        """IP address and port of listening interface for HTTP

        For IPv6 dual-stack (accepts both IPv4 and IPv6), use address='::'

        Args:
            event_mode: If True, enables streaming event mode where wait()
                returns clients at different stages (headers, data, complete).
                If False (default), wait() only returns fully loaded requests.
        """
        self._kwargs = kwargs
        self._ssl_context = ssl_context
        self._event_mode = event_mode
        if ':' in address:
            self._socket = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
            try:
                self._socket.setsockopt(
                    _socket.IPPROTO_IPV6, _socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                pass
        else:
            self._socket = _socket.socket()
        self._socket.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        self._socket.bind((address, port))
        self._socket.listen(kwargs.get('listen', LISTEN_SOCKETS))
        self._max_clients = kwargs.get(
            'max_waiting_clients', MAX_WAITING_CLIENTS)
        self._waiting_connections = []

    @property
    def socket(self):
        """Server socket"""
        return self._socket

    @property
    def is_secure(self):
        """Return True if server uses SSL/TLS"""
        return bool(self._ssl_context)

    @property
    def event_mode(self):
        """Return True if event mode is enabled"""
        return self._event_mode

    @property
    def read_sockets(self):
        """All sockets waiting for communication, used for select"""
        read_sockets = [
            con.socket for con in self._waiting_connections
            if con.socket is not None]
        if self._socket is not None:
            read_sockets.append(self._socket)
        return read_sockets

    @property
    def write_sockets(self):
        """All sockets with data to send, used for select"""
        return [
            con.socket for con in self._waiting_connections
            if con.socket is not None and con.has_data_to_send]

    def close(self):
        """Close HTTP server"""
        try:
            self._socket.close()
        except OSError:
            pass
        self._socket = None

    def remove_connection(self, connection):
        if connection in self._waiting_connections:
            self._waiting_connections.remove(connection)

    def _cleanup_idle_connections(self):
        """Remove timed out idle connections"""
        for connection in list(self._waiting_connections):
            if connection._is_multipart:
                continue
            if not connection.is_loaded and connection.is_timed_out:
                connection.respond(
                    'Request Timeout', status=408,
                    headers={CONNECTION: CONNECTION_CLOSE})

    def _accept(self):
        try:
            cl_socket, addr = self._socket.accept()
        except OSError:
            return

        try:
            cl_socket.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

        try:
            cl_socket.setblocking(False)
        except (OSError, AttributeError):
            pass

        if self._ssl_context:
            try:
                cl_socket = self._ssl_context.wrap_socket(
                    cl_socket, server_side=True, do_handshake_on_connect=False)
            except OSError:
                try:
                    cl_socket.close()
                except OSError:
                    pass
                return

        connection = HttpConnection(self, cl_socket, addr, **self._kwargs)
        while len(self._waiting_connections) > self._max_clients:
            connection_to_remove = self._waiting_connections.pop(0)
            if connection_to_remove._response_started:
                # Already responding (e.g., multipart stream) - just close
                connection_to_remove.close()
            else:
                connection_to_remove.respond(
                    'Request Timeout, too many requests', status=408,
                    headers={CONNECTION: CONNECTION_CLOSE})
        self._waiting_connections.append(connection)

    def event_read(self, sockets):
        """Process sockets with read_event,
        returns None or instance of HttpConnection with established connection"""
        result = None

        if self._socket in sockets:
            self._accept()
        else:
            for connection in list(self._waiting_connections):
                if connection.socket in sockets:
                    if self._event_mode:
                        if connection.process_request_event():
                            result = connection
                            break
                    elif connection.process_request():
                        result = connection
                        break

        self._cleanup_idle_connections()

        return result

    def _get_pending_connection(self):
        """Get connection with pending data in buffer (event mode only)"""
        if not self._event_mode:
            return None
        for connection in self._waiting_connections:
            if connection._streaming_body and connection._buffer:
                return connection
        return None

    def event_write(self, sockets):
        """Process sockets with write_event, send buffered data"""
        for connection in list(self._waiting_connections):
            if connection.socket in sockets:
                connection.try_send()

    def process_events(self, read_sockets, write_sockets):
        """Process select results, returns loaded connection or None

        This allows using external select with multiple servers/sockets:

        Example:
            server1 = HttpServer(port=80)
            server2 = HttpServer(port=443, ssl_context=ctx)

            read_all = server1.read_sockets + server2.read_sockets
            write_all = server1.write_sockets + server2.write_sockets
            r, w, _ = select.select(read_all, write_all, [], timeout)

            client = server1.process_events(r, w) or server2.process_events(r, w)
        """
        # Check pending connections first (event mode)
        pending = self._get_pending_connection()
        if pending:
            if pending._handle_streaming_body():
                return pending

        if write_sockets:
            self.event_write(write_sockets)
        if read_sockets:
            return self.event_read(read_sockets)
        return None

    def wait(self, timeout=1):
        """Wait for new clients with specified timeout,
        returns None or instance of HttpConnection with established connection"""
        # Check pending connections first (event mode)
        pending = self._get_pending_connection()
        if pending:
            if pending._handle_streaming_body():
                return pending

        self.event_write(self.write_sockets)
        try:
            read_sockets, write_sockets, _ = _select.select(
                self.read_sockets, self.write_sockets, [], timeout)
        except (OSError, ValueError) as err:
            # EBADF: socket closed concurrently
            # ValueError: socket fileno() is -1 (closed)
            if isinstance(err, ValueError) or err.errno == errno.EBADF:
                return None
            raise
        return self.process_events(read_sockets, write_sockets)
