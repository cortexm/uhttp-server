"""uHttp - Micro HTTP Server
python or micropython
(c) 2022-2024 Pavel Revak <pavelrevak@gmail.com>
"""

import os as _os
import errno
import socket as _socket
import selectors as _selectors
import json as _json
import time as _time
import hashlib as _hashlib
import binascii as _binascii

KB = 2 ** 10
MB = 2 ** 20
GB = 2 ** 30

# would-block errnos: EAGAIN, plus Windows-only EWOULDBLOCK when it differs
_WOULDBLOCK = (errno.EAGAIN,)
if getattr(errno, 'EWOULDBLOCK', errno.EAGAIN) != errno.EAGAIN:
    _WOULDBLOCK += (errno.EWOULDBLOCK,)

LISTEN_SOCKETS = 8
MAX_WAITING_CLIENTS = 32
MAX_HEADERS_LENGTH = 4 * KB
MAX_CONTENT_LENGTH = 512 * KB
FILE_CHUNK_SIZE = 4 * KB  # bytes - chunk size for streaming file responses
MAX_WS_MESSAGE_LENGTH = 64 * KB  # max WebSocket message size before chunking
MAX_SEND_BUFFER_SIZE = 64 * KB  # max pending bytes in send buffer (backpressure)
KEEP_ALIVE_TIMEOUT = 15  # seconds
KEEP_ALIVE_MAX_REQUESTS = 100  # max requests per connection
REQUEST_TIMEOUT = 5  # seconds - max time from first byte to complete headers

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
CONTENT_TYPE_EVENT_STREAM = 'text/event-stream'
CONTENT_TYPE_NDJSON = 'application/x-ndjson'
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
UPGRADE = 'upgrade'
SEC_WEBSOCKET_KEY = 'sec-websocket-key'
SEC_WEBSOCKET_ACCEPT = 'Sec-WebSocket-Accept'
SEC_WEBSOCKET_VERSION = 'sec-websocket-version'
WS_VERSION = '13'
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
    'js': 'application/javascript',
    'css': 'text/css',
    'json': 'application/json',
    'xml': 'text/xml',
    'txt': 'text/plain',
    'woff': 'font/woff',
    'woff2': 'font/woff2',
    'ttf': 'font/ttf',
    'mp3': 'audio/mpeg',
    'mp4': 'video/mp4',
    'wav': 'audio/wav',
    'pdf': 'application/pdf',
    'zip': 'application/zip',
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

# WebSocket event constants
EVENT_WS_MESSAGE = 5        # Complete WebSocket message
EVENT_WS_CHUNK_FIRST = 6    # First chunk of large message
EVENT_WS_CHUNK_NEXT = 7     # Next chunk of large message
EVENT_WS_CHUNK_LAST = 8     # Last chunk of large message
EVENT_WS_PING = 9           # Ping received (pong sent automatically)
EVENT_WS_CLOSE = 10         # WebSocket closed
EVENT_WS_REQUEST = 11       # WebSocket upgrade request received

# WebSocket opcodes
WS_OPCODE_CONTINUATION = 0x0
WS_OPCODE_TEXT = 0x1
WS_OPCODE_BINARY = 0x2
WS_OPCODE_CLOSE = 0x8
WS_OPCODE_PING = 0x9
WS_OPCODE_PONG = 0xA

_WS_MAGIC = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

STATUS_CODES = {
    100: "Continue",
    101: "Switching Protocols",
    200: "OK",
    201: "Created",
    202: "Accepted",
    203: "Non-Authoritative Information",
    204: "No Content",
    205: "Reset Content",
    206: "Partial Content",
    207: "Multi-Status",
    300: "Multiple Choices",
    301: "Moved Permanently",
    302: "Found",
    303: "See Other",
    304: "Not Modified",
    307: "Temporary Redirect",
    308: "Permanent Redirect",
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Payload Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    416: "Range Not Satisfiable",
    418: "I'm a Teapot",
    422: "Unprocessable Entity",
    426: "Upgrade Required",
    428: "Precondition Required",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    451: "Unavailable For Legal Reasons",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
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
    """Decode percent encoded data (bytes)

    Raises ValueError on invalid percent-encoding.
    """
    if b'%' not in data:
        return data.replace(b'+', b' ')
    res = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        if b == 37:  # '%'
            if i + 2 >= n:
                raise ValueError(f"Truncated percent-encoding at position {i}")
            try:
                res.append(int(bytes(data[i+1:i+3]), 16))
            except ValueError:
                raise ValueError(
                    f"Invalid percent-encoding: %{chr(data[i+1])}{chr(data[i+2])}")
            i += 3
            continue
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


def parse_cookies(raw_cookies):
    """Parse cookie string into dict"""
    cookies = {}
    for cookie_param in split_iter(raw_cookies, ';'):
        if '=' in cookie_param:
            key, val = cookie_param.split('=', 1)
            key = key.strip()
            if key:
                cookies[key] = val.strip()
    return cookies


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


def unmap_ipv4(address):
    """Strip IPv4-mapped IPv6 prefix: ::ffff:192.0.2.1 -> 192.0.2.1"""
    if address.startswith('::ffff:'):
        return address[7:]
    return address


def parse_ip(address):
    """Return the bare IP of an address, dropping brackets and port

    Accepts `ip`, `ipv4:port`, `[ipv6]` and `[ipv6]:port`. Every IPv6 holds
    at least two colons, so a single colon can only be a port separator.
    """
    if address.startswith('['):
        address = address[1:].split(']', 1)[0]
    elif address.count(':') == 1:
        address = address.split(':', 1)[0]
    return unmap_ipv4(address)


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
        data = _json.dumps(data).encode('utf-8')
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


def _ws_accept_key(key):
    """Compute Sec-WebSocket-Accept from Sec-WebSocket-Key"""
    digest = _hashlib.sha1(key.encode('ascii') + _WS_MAGIC).digest()
    return _binascii.b2a_base64(digest).strip().decode('ascii')


def _ws_build_frame(opcode, payload, fin=True):
    """Build WebSocket frame (server-side, unmasked)"""
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    frame = bytearray()
    frame.append((0x80 if fin else 0) | opcode)
    length = len(payload)
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.append((length >> 8) & 0xFF)
        frame.append(length & 0xFF)
    else:
        frame.append(127)
        for i in range(7, -1, -1):
            frame.append((length >> (8 * i)) & 0xFF)
    frame.extend(payload)
    return bytes(frame)


class _WsFrameMixin:
    """Shared WebSocket frame parsing methods"""

    def __init__(self):
        """Initialize WebSocket frame parsing state"""
        self._ws_frame_header_parsed = False
        self._ws_frame_opcode = None
        self._ws_frame_fin = False
        self._ws_frame_remaining = 0
        self._ws_frame_mask = None
        self._ws_frame_mask_offset = 0
        self._ws_message_opcode = None
        self._ws_fragment_buffer = bytearray()
        self._ws_control_buffer = bytearray()
        self._ws_chunked = False
        self._ws_message = None

    @property
    def ws_is_text(self):
        """True if current message is text frame"""
        return self._ws_message_opcode == WS_OPCODE_TEXT

    def _ws_process_buffer(self):
        """Process WebSocket frames from buffer.
        Returns True if event is ready.
        Requires subclass to provide: _ws_do_send(data), _ws_on_close()
        and attributes: _buffer, _event, _max_ws_message_length"""
        while True:
            if not self._ws_frame_header_parsed:
                if not self._ws_parse_frame_header():
                    return False

            available = min(len(self._buffer), self._ws_frame_remaining)
            if available == 0 and self._ws_frame_remaining > 0:
                return False

            # Cap demask size to detect limit crossing early
            if (available > 0
                    and not self._ws_chunked
                    and self._ws_frame_opcode < 0x8):
                space = (self._max_ws_message_length + 1
                         - len(self._ws_fragment_buffer))
                if space > 0:
                    available = min(available, space)

            if available > 0:
                chunk = self._ws_demask(available)
                self._ws_frame_remaining -= available

                if self._ws_frame_opcode >= 0x8:
                    self._ws_control_buffer.extend(chunk)
                else:
                    self._ws_fragment_buffer.extend(chunk)
                    if self._ws_chunked:
                        frame_done = self._ws_frame_remaining == 0
                        msg_done = frame_done and self._ws_frame_fin
                        if frame_done:
                            self._ws_frame_header_parsed = False
                        if msg_done:
                            self._ws_chunked = False
                            self._event = EVENT_WS_CHUNK_LAST
                        else:
                            self._event = EVENT_WS_CHUNK_NEXT
                        return True

            frame_done = self._ws_frame_remaining == 0
            if not frame_done:
                if (len(self._ws_fragment_buffer)
                        > self._max_ws_message_length):
                    self._ws_chunked = True
                    self._event = EVENT_WS_CHUNK_FIRST
                    return True
                return False

            self._ws_frame_header_parsed = False

            if self._ws_frame_opcode >= 0x8:
                if self._ws_frame_opcode == WS_OPCODE_PING:
                    # Auto-pong may raise OSError if the send buffer cap is
                    # hit (slow/dead consumer or ping flood). Treat as a
                    # close rather than letting it crash the event loop.
                    try:
                        self._ws_do_send(_ws_build_frame(
                            WS_OPCODE_PONG,
                            bytes(self._ws_control_buffer)))
                    except OSError:
                        self._ws_message = None
                        self._event = EVENT_WS_CLOSE
                        self._ws_control_buffer = bytearray()
                        self._ws_on_close()
                        return True
                    self._ws_message = bytes(self._ws_control_buffer)
                    self._event = EVENT_WS_PING
                    self._ws_control_buffer = bytearray()
                    return True
                if self._ws_frame_opcode == WS_OPCODE_CLOSE:
                    # Closing anyway: swallow a send buffer overflow.
                    try:
                        self._ws_do_send(_ws_build_frame(
                            WS_OPCODE_CLOSE,
                            bytes(self._ws_control_buffer)))
                    except OSError:
                        pass
                    self._ws_message = (
                        bytes(self._ws_control_buffer)
                        if self._ws_control_buffer else None)
                    self._event = EVENT_WS_CLOSE
                    self._ws_control_buffer = bytearray()
                    self._ws_on_close()
                    return True
                self._ws_control_buffer = bytearray()
                continue

            if (len(self._ws_fragment_buffer)
                    > self._max_ws_message_length):
                self._ws_chunked = True
                self._event = EVENT_WS_CHUNK_FIRST
                return True

            if self._ws_frame_fin:
                self._event = EVENT_WS_MESSAGE
                return True

            continue

    def _ws_protocol_error(self, reason):
        """Send close frame with 1002 (protocol error) and raise"""
        payload = (1002).to_bytes(2, 'big') + reason.encode('utf-8')
        try:
            self._ws_do_send(_ws_build_frame(WS_OPCODE_CLOSE, payload))
        except (OSError, AttributeError):
            pass
        raise ClientError(f"WebSocket protocol error: {reason}")

    def _ws_parse_frame_header(self):
        """Parse WebSocket frame header from buffer"""
        buf = self._buffer
        if len(buf) < 2:
            return False
        b0, b1 = buf[0], buf[1]
        # RFC 6455 §5.2: RSV bits must be 0 (no extensions negotiated)
        if b0 & 0x70:
            self._ws_protocol_error("reserved bits must be zero")
        self._ws_frame_fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        self._ws_frame_opcode = opcode
        if opcode != WS_OPCODE_CONTINUATION and opcode < 0x8:
            self._ws_message_opcode = opcode
            if self._ws_fragment_buffer:
                self._ws_fragment_buffer = bytearray()
        masked = bool(b1 & 0x80)
        # RFC 6455 §5.1: client frames must be masked
        if not masked:
            self._ws_protocol_error("client frame must be masked")
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            if len(buf) < 4:
                return False
            length = (buf[2] << 8) | buf[3]
            offset = 4
        elif length == 127:
            if len(buf) < 10:
                return False
            length = 0
            for i in range(8):
                length = (length << 8) | buf[2 + i]
            offset = 10
        # RFC 6455 §5.5: control frames must have payload ≤125 and FIN=1
        if opcode >= 0x8:
            if length > 125:
                self._ws_protocol_error("control frame too large")
            if not self._ws_frame_fin:
                self._ws_protocol_error("control frame must not be fragmented")
        if len(buf) < offset + 4:
            return False
        self._ws_frame_mask = bytes(buf[offset:offset + 4])
        offset += 4
        self._buffer = self._buffer[offset:]
        self._ws_frame_remaining = length
        self._ws_frame_mask_offset = 0
        self._ws_frame_header_parsed = True
        return True

    def _ws_demask(self, length):
        """Demask bytes from buffer"""
        data = bytearray(self._buffer[:length])
        self._buffer = self._buffer[length:]
        if self._ws_frame_mask:
            mask = self._ws_frame_mask
            offset = self._ws_frame_mask_offset
            for i in range(length):
                data[i] ^= mask[(offset + i) & 3]
            self._ws_frame_mask_offset = (offset + length) & 3
        return bytes(data)


class WebSocket:
    """Non-event-mode WebSocket: a facade over the upgraded HttpConnection.

    Speaks the same owner protocol as HttpServer and HttpConnection, so it
    is driven either by its own wait() or from a shared selector loop. All
    framing, buffering and sending live on the connection.
    """

    def __init__(self, connection, selector=None):
        self._connection = connection
        self._sock = connection._socket  # kept for unregister() after close
        self._owns_selector = selector is None
        self._selector = selector or _selectors.DefaultSelector()
        self._interest = None
        self._update_interest()

    @property
    def selector(self):
        """The selector this WebSocket registers its socket in"""
        return self._selector

    @property
    def is_closed(self):
        """True if WebSocket is closed"""
        return self._connection._socket is None

    @property
    def event(self):
        """Current event type"""
        return self._connection.event

    @property
    def ws_message(self):
        """Ping/close payload of the current event"""
        return self._connection.ws_message

    @property
    def ws_is_text(self):
        """True if current message is text frame"""
        return self._connection.ws_is_text

    @property
    def send_pending(self):
        """True if there is unsent data in send buffer"""
        return self._connection.has_data_to_send

    def read_buffer(self):
        """Read accumulated message/chunk data"""
        return self._connection.read_buffer()

    def send(self, data):
        """Send WebSocket frame.
        str -> text frame, bytes -> binary frame.

        Returns False if the socket is closed or the send buffer cap hit.
        """
        return self._sent(self._connection.ws_send(data))

    def ping(self, data=b''):
        """Send ping frame. Returns False if it could not be queued."""
        return self._sent(self._connection.ws_ping(data))

    def close(self, code=1000, reason=''):
        """Send close frame and close the connection"""
        sent = self._connection.ws_close(code, reason)
        self._unregister()
        if self._owns_selector:
            try:
                self._selector.close()
            except OSError:
                pass
        return sent

    def handle_event(self, fileobj, mask):
        """Owner dispatch: returns self when an event is ready, else None"""
        connection = self._connection
        if connection._socket is None or fileobj is not connection._socket:
            return None
        if mask & _selectors.EVENT_WRITE:
            connection.try_send()
        if mask & _selectors.EVENT_READ and connection._socket is not None:
            try:
                connection._ws_recv()
            except ClientError:
                return self._closed_event()
        return self._process()

    def next(self):
        """Process a frame already in the receive buffer; True while ready.

        One recv() can carry several frames that select() will not report
        again, so drain with this before blocking.
        """
        if self._connection._socket is None or not self._connection._buffer:
            return False
        return self._process() is not None

    def wait(self, timeout=None):
        """Wait for a WebSocket event, driving this WebSocket's selector.

        Returns the event type, or None on timeout. Requires an owned
        selector: a blocking wait cannot service a shared one's other keys.
        """
        if not self._owns_selector:
            raise HttpError(
                "wait() needs the WebSocket's own selector; drive a shared "
                "one with handle_event()")
        if self.is_closed:
            return None
        if self.next():
            return self.event
        try:
            events = self._selector.select(timeout)
        except (OSError, ValueError):
            self._unregister()
            return None
        for key, mask in events:
            if self.handle_event(key.fileobj, mask) is not None:
                return self.event
        return None

    def _process(self):
        """Parse buffered frames, then reconcile the selector interest"""
        try:
            ready = self._connection._ws_process_buffer()
        except ClientError:
            return self._closed_event()
        self._update_interest()
        return self if ready else None

    def _closed_event(self):
        connection = self._connection
        connection._event = EVENT_WS_CLOSE
        connection._ws_message = None
        connection.close()
        self._unregister()
        return self

    def _sent(self, ok):
        self._update_interest()
        return ok

    def pause_reading(self, timeout=None):
        """Stop reading inbound frames to stall the peer via TCP backpressure.

        See HttpConnection.pause_reading(). Outbound send() still works.
        A standalone WebSocket is application-driven, so timeout is stored
        but not enforced here — apply the deadline in the driving loop.
        """
        self._connection._read_paused = True
        self._connection._read_pause_timeout = timeout
        self._update_interest()

    def resume_reading(self):
        """Resume reading inbound frames after pause_reading()."""
        self._connection._read_paused = False
        self._connection._read_pause_timeout = None
        self._connection.update_activity()
        self._update_interest()

    def _update_interest(self):
        connection = self._connection
        if connection._socket is None:
            self._unregister()
            return
        want = 0
        if not connection._read_paused:
            want |= _selectors.EVENT_READ
        if connection.has_data_to_send:
            want |= _selectors.EVENT_WRITE
        if not want:
            self._unregister()  # paused with nothing to send: stop watching
            return
        if want == self._interest:
            return
        method = 'register' if self._interest is None else 'modify'
        try:
            getattr(self._selector, method)(self._sock, want, self)
        except (KeyError, ValueError, OSError):
            connection.close()  # nothing could wake it again
            self._interest = None
            return
        self._interest = want

    def _unregister(self):
        if self._interest is None:
            return
        try:
            self._selector.unregister(self._sock)
        except (KeyError, ValueError, OSError):
            pass
        self._interest = None


class HttpConnection(_WsFrameMixin):
    """Simple HTTP client connection"""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, server, sock, addr, **kwargs):
        """sock - client socket, addr - tuple (ip, port)"""
        self._server = server
        self._addr = addr
        self._socket = sock
        self._buffer = bytearray()
        self._send_buffer = bytearray()
        self._send_offset = 0
        self._interest = None  # selector mask; None = not registered
        self._detached = False  # handed off to a WebSocket object
        self._read_paused = False  # app backpressure: stop reading, stall TCP
        self._read_pause_timeout = None
        self._rx_bytes_counter = 0
        self._method = None
        self._url = None
        self._protocol = None
        self._headers = None
        self._headers_repeated = None
        self._data = None
        self._data_loaded = False
        self._path = None
        self._query = None
        self._content_length = None
        self._cookies = None
        self._is_streaming = False
        self._response_started = False
        self._response_keep_alive = False
        self._file_handle = None
        self._last_activity = _time.time()
        self._requests_count = 0
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
        _WsFrameMixin.__init__(self)
        self._ws_mode = False
        self._max_headers_length = kwargs.get(
            'max_headers_length', MAX_HEADERS_LENGTH)
        self._max_content_length = kwargs.get(
            'max_content_length', MAX_CONTENT_LENGTH)
        self._max_ws_message_length = kwargs.get(
            'max_ws_message_length', MAX_WS_MESSAGE_LENGTH)
        self._file_chunk_size = kwargs.get(
            'file_chunk_size', FILE_CHUNK_SIZE)
        self._max_send_buffer_size = kwargs.get(
            'max_send_buffer_size', MAX_SEND_BUFFER_SIZE)
        self._keep_alive_timeout = kwargs.get(
            'keep_alive_timeout', KEEP_ALIVE_TIMEOUT)
        self._keep_alive_max_requests = kwargs.get(
            'keep_alive_max_requests', KEEP_ALIVE_MAX_REQUESTS)
        self._request_timeout = kwargs.get(
            'request_timeout', REQUEST_TIMEOUT)
        self._request_start = None
        self._headers_scanned = 0

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
    def _socket_ip(self):
        return unmap_ipv4(self._addr[0])

    @property
    def socket_address(self):
        """Return socket address as `ip:port` (ignores X-Forwarded-For)

        IPv6 is bracketed per RFC 3986: `[2001:db8::1]:8080`
        """
        addr = self._socket_ip
        if ':' in addr:
            addr = f"[{addr}]"
        return f"{addr}:{self._addr[1]}"

    @property
    def remote_addresses(self):
        """Return the X-Forwarded-For chain, client first, last proxy last

        The header is used only when the socket peer is a trusted proxy,
        otherwise the result is just the socket IP. Only the part from
        `remote_address` rightwards is trustworthy — entries left of it come
        from a header the client may have written itself.
        """
        proxies = self._server._trusted_proxies
        if proxies and self._socket_ip in proxies:
            forwarded = self.headers_get_attribute('x-forwarded-for')
            if forwarded:
                addresses = []
                for address in forwarded.split(','):
                    address = parse_ip(address.strip())
                    if address:
                        addresses.append(address)
                if addresses:
                    return addresses
        return [self._socket_ip]

    @property
    def remote_address(self):
        """Return client IP address (no port)

        Walks the address chain from the socket peer leftwards and returns
        the first hop that is not a trusted proxy. Going right-to-left is
        what makes this spoof-proof: a client can prepend anything to
        X-Forwarded-For, but it cannot forge the hops a proxy appended
        after it.
        """
        proxies = self._server._trusted_proxies or ()
        for addr in reversed(self.remote_addresses):
            if addr not in proxies:
                return addr
        return self._socket_ip

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
        """Content data (parsed JSON/form or raw bytes)"""
        if not self._data_loaded and self._event == EVENT_COMPLETE and self._buffer:
            self._process_data()
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
            raw_cookies = self.headers_get_attribute(COOKIE)
            self._cookies = parse_cookies(raw_cookies) if raw_cookies else {}
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
        return bool(
            self._method
            and (not self.content_length or self._data_loaded))

    @property
    def is_timed_out(self):
        """True when connection has been idle too long.

        While reading is paused the deadline is the pause override if set
        (0 or less disables it, so the app owns the lifecycle), else the
        usual keep-alive timeout — a paused consumer that never drains ages
        out like any other idle connection.
        """
        limit = self._keep_alive_timeout
        if self._read_paused and self._read_pause_timeout is not None:
            if self._read_pause_timeout <= 0:
                return False
            limit = self._read_pause_timeout
        return (_time.time() - self._last_activity) > limit

    @property
    def is_max_requests_reached(self):
        """True when connection reached max requests limit"""
        return self._requests_count >= self._keep_alive_max_requests

    @property
    def has_data_to_send(self):
        """True when there is data waiting to be sent or file being streamed"""
        return self.send_buffer_size > 0 or self._file_handle is not None

    @property
    def send_buffer_size(self):
        """Size of pending send buffer in bytes"""
        return len(self._send_buffer) - self._send_offset

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

    @property
    def is_websocket_request(self):
        """True if request is a WebSocket upgrade"""
        if self._headers is None:
            return False
        return (
            self.headers_get_attribute(UPGRADE, '').lower() == 'websocket'
            and 'upgrade' in self.headers_get_attribute(
                CONNECTION, '').lower())

    @property
    def is_websocket(self):
        """True if connection is in WebSocket mode"""
        return self._ws_mode

    @property
    def ws_message(self):
        """Ping/close payload of the current event (EVENT_WS_PING/CLOSE)"""
        return self._ws_message

    def headers_get_attribute(self, key, default=None):
        """Return headers value"""
        if self._headers:
            return self._headers.get(key, default)
        return default

    def headers_all(self, key):
        """Every value of a repeated header field, in the order received.

        headers_get() combines repeated field lines into one string, which
        is lossy when a value may itself contain the separator. This keeps
        them apart. Returns [] for a header that was not sent.
        """
        key = key.lower()
        if not self._headers or key not in self._headers:
            return []
        if self._headers_repeated and key in self._headers_repeated:
            return list(self._headers_repeated[key])
        return [self._headers[key]]

    def _recv_to_buffer(self, size):
        try:
            buffer = self._socket.recv(size - len(self._buffer))
        except OSError as err:
            if err.errno in _WOULDBLOCK or err.errno == errno.ENOENT:
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

    def _probe_streaming_close(self):
        """Detect peer close on a streaming/multipart connection.

        Streaming responses (multipart, SSE, NDJSON, response_stream)
        are unidirectional: server -> client. A readable event on the
        socket therefore means either the peer closed its end (EOF) or
        sent unsolicited bytes which we silently discard. Raises
        HttpDisconnected on EOF or socket error so the caller can close
        the connection. Without this probe a half-closed fd keeps
        firing on select() and busy-spins the event loop.
        """
        try:
            data = self._socket.recv(self._file_chunk_size)
        except OSError as err:
            if err.errno in _WOULDBLOCK or err.errno == errno.ENOENT:
                return
            raise HttpDisconnected(f"{err}: {self.addr}") from err
        if data is None:
            # MicroPython SSL: handshake / would-block
            return
        if not data:
            raise HttpDisconnected(
                f"Streaming client closed: {self.addr}")
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

        content_type = self.content_type.split(';')[0].strip().lower()
        if content_type == CONTENT_TYPE_XFORMDATA:
            self._data = parse_query(self._buffer)
        elif content_type == CONTENT_TYPE_JSON:
            try:
                self._data = _json.loads(self._buffer)
            except (ValueError, RuntimeError) as err:
                # ValueError: malformed JSON. RuntimeError: deeply nested
                # JSON exhausts the recursion limit (RecursionError is a
                # subclass of RuntimeError on both CPython and MicroPython).
                # Without this an attacker crashes the loop with a tiny body.
                raise HttpErrorWithResponse(
                    400, "Invalid JSON body") from err
        else:
            self._data = self._buffer
        self._buffer = bytearray()
        self._data_loaded = True

    def _process_headers(self, header_lines):
        self._headers = {}
        self._headers_repeated = None
        for line in header_lines:
            if not line:
                break
            if self._method is None:
                self._parse_http_request(line)
            else:
                if line[:1] in (b' ', b'\t'):
                    raise HttpErrorWithResponse(
                        400, "Obsolete header line folding is not supported")
                key, val = parse_header_line(line)
                # RFC 7230: reject duplicate Content-Length or Host
                if key in (CONTENT_LENGTH, HOST) and key in self._headers:
                    raise HttpErrorWithResponse(
                        400, f"Duplicate {key} header")
                if key in self._headers:
                    # RFC 9110 5.3: repeated field lines combine with a
                    # comma. Cookie is the exception - RFC 6265 separates
                    # its pairs with '; ', so a comma would corrupt it.
                    if self._headers_repeated is None:
                        self._headers_repeated = {}
                    self._headers_repeated.setdefault(
                        key, [self._headers[key]]).append(val)
                    separator = '; ' if key == COOKIE else ', '
                    val = self._headers[key] + separator + val
                self._headers[key] = val

        if 'transfer-encoding' in self._headers:
            raise HttpErrorWithResponse(501, "Transfer-Encoding not supported")

        # RFC 2616: HTTP/1.1 requires Host header
        if self._protocol == 'HTTP/1.1' and 'host' not in self._headers:
            raise HttpErrorWithResponse(
                400, "Host header is required for HTTP/1.1")

        expect = self.headers_get_attribute(EXPECT, '').lower()
        if expect == EXPECT_100_CONTINUE and self.content_length:
            self._expect_continue = True
            if not self._server.event_mode:
                self._send_100_continue()

        if self.content_length:
            if self.content_length > self._max_content_length:
                raise HttpErrorWithResponse(413)
            self._process_data()

    def _read_headers(self):
        if self._request_start is None:
            self._request_start = _time.time()
        self._recv_to_buffer(self._max_headers_length)
        # Resume the scan a few bytes back so a delimiter split across two
        # reads is still found, instead of rescanning the whole buffer.
        start = max(0, self._headers_scanned - 3)
        for delimiter in HEADERS_DELIMITERS:
            found = self._buffer.find(delimiter, start)
            if found >= 0:
                end_index = found + len(delimiter)
                header_lines = self._buffer[:end_index].splitlines()
                self._buffer = self._buffer[end_index:]
                self._headers_scanned = 0
                self._process_headers(header_lines)
                return
        self._headers_scanned = len(self._buffer)
        if len(self._buffer) >= self._max_headers_length:
            raise HttpErrorWithResponse(
                431,
                f"Headers too large: {len(self._buffer)} bytes (max {self._max_headers_length})")

    def _send(self, *parts):
        """Add data to send buffer for async sending.

        Backpressure: if buffer already holds pending data and adding
        more would exceed _max_send_buffer_size, raises OSError. A
        single large initial write into an empty buffer is always
        accepted (typical respond(data=...) case) - parts are measured
        together, so a header plus an oversized body still counts as one.
        Slow consumers that accumulate buffered data will trip the cap and
        the streaming send_*() callers convert it to a False return.
        """
        if self._socket is None:
            return
        parts = [
            part.encode('utf-8') if isinstance(part, str) else part
            for part in parts]
        pending = self.send_buffer_size
        if pending and pending + sum(
                len(part) for part in parts) > self._max_send_buffer_size:
            raise OSError("Send buffer overflow")
        for part in parts:
            self._send_buffer.extend(part)
        self.try_send()

    def _send_100_continue(self):
        """Send 100 Continue response if client expects it"""
        if not self._expect_continue:
            return
        self._expect_continue = False
        self._send('HTTP/1.1 100 Continue\r\n\r\n')

    def _close_file_handle(self):
        """Close file handle safely"""
        if getattr(self, '_file_handle', None):
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
        if self.send_buffer_size >= self._file_chunk_size:
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

    def _consume_sent(self, sent):
        """Drop sent bytes without rebuilding the buffer.

        Compacts only once the consumed prefix outgrows the remainder, so
        draining a large response copies O(size) in total instead of
        reallocating the whole remainder on every partial send.
        """
        self._send_offset += sent
        remaining = len(self._send_buffer) - self._send_offset
        # MicroPython bytearrays support slice assignment but not deletion,
        # so shrink in place that way - it keeps the object either way.
        if not remaining:
            self._send_buffer[:] = b''
            self._send_offset = 0
        elif self._send_offset >= remaining:
            self._send_buffer[:] = self._send_buffer[self._send_offset:]
            self._send_offset = 0

    def _flush_send_buffer(self):
        """Try to send data from buffer.
        Returns True if buffer is empty."""
        if not self.send_buffer_size:
            return True
        try:
            sent = self._socket.send(
                memoryview(self._send_buffer)[self._send_offset:])
            # MicroPython SSL may return None when buffer full
            if sent is None:
                return False
            if sent > 0:
                self._consume_sent(sent)
            return self.send_buffer_size == 0
        except OSError as err:
            if err.errno in _WOULDBLOCK:
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
        self._update_interest()

    def _selector_call(self, method, *args):
        try:
            getattr(self._server._selector, method)(self._socket, *args)
        except (KeyError, ValueError, OSError):
            return False
        return True

    def _register(self):
        if self._selector_call('register', _selectors.EVENT_READ, self):
            self._interest = _selectors.EVENT_READ
            return True
        return False

    def _unregister(self):
        if self._interest is not None and self._socket is not None:
            self._selector_call('unregister')
        self._interest = None

    def _update_interest(self):
        """Arm WRITE only while sending, READ only while listening.

        Dropping READ while a plain response drains is what keeps a client
        that talks during its own download from spinning the loop: its
        bytes stay in the socket buffer until reset() re-arms READ. A
        streaming or WebSocket connection is bidirectional and keeps READ.

        pause_reading() also drops READ: with nothing left to watch the
        socket is unregistered so its kernel buffer fills and TCP stalls the
        peer (backpressure). While paused it stays re-registerable, because a
        send from application code still has to arm WRITE — otherwise those
        bytes would sit unsent until resume_reading(). Outside a pause,
        registration is owned by _accept(): an unregistered socket is skipped.
        """
        if self._socket is None or self._detached:
            return
        if self._interest is None and not self._read_paused:
            return
        want = 0
        read_ok = (not self._response_started
                   or self._is_streaming or self._ws_mode)
        if read_ok and not self._read_paused:
            want |= _selectors.EVENT_READ
        if self.has_data_to_send:
            want |= _selectors.EVENT_WRITE
        if not want:
            if self._read_paused:
                self._unregister()
                return
            want = _selectors.EVENT_READ  # a mask of 0 is not selectable
        if want == self._interest:
            return
        method = 'register' if self._interest is None else 'modify'
        if self._selector_call(method, want, self):
            self._interest = want
        else:
            self.close()  # can't be re-armed: it would hang forever

    def pause_reading(self, timeout=None):
        """Stop reading from the socket to slow the peer via TCP backpressure.

        Use when the application cannot keep up with an inbound WebSocket
        stream or request-body upload: the socket is left unread, its kernel
        buffer fills and TCP stalls the sender. Outbound sending is
        unaffected.

        Needs an inbound stream to throttle: WebSocket mode or a body
        accepted with accept_body*(). Pausing a one-way response (SSE,
        multipart) would only blind its peer-close probe, and pausing while
        the request is still arriving would trade the request timeout for the
        longer keep-alive one, so both raise.

        timeout: seconds the connection may stay paused before maintenance()
        closes it as a stuck consumer. None uses the keep-alive timeout; a
        value <= 0 disables the deadline (the application owns the lifecycle).
        A slow-but-alive consumer that resumes and drains periodically keeps
        the connection alive, since each read refreshes the activity clock.
        The deadline is only as precise as maintenance(), which scans at most
        once per half the shortest configured timeout.
        """
        if self._detached or not (self._ws_mode or self._streaming_body):
            raise HttpError(
                "pause_reading() needs an inbound stream: WebSocket mode or "
                "accept_body*(); a detached WebSocket pauses through its "
                "own object")
        self._read_paused = True
        self._read_pause_timeout = timeout
        self._update_interest()

    def resume_reading(self):
        """Resume reading after pause_reading() and re-arm the socket."""
        self._read_paused = False
        self._read_pause_timeout = None
        self.update_activity()
        if self._socket is None or self._detached:
            return
        if self._interest is None and not self._register():
            self.close()  # nothing could wake it again
            return
        self._update_interest()

    def handle_event(self, fileobj, mask):
        """Owner dispatch for a selector event.

        Returns this connection when a request/event is ready, else None.
        """
        if self._socket is None or self._detached:
            return None
        if mask & _selectors.EVENT_WRITE:
            self.try_send()
            if self._socket is None:
                return None
        if mask & _selectors.EVENT_READ:
            if self._server.event_mode:
                if self.process_request_event():
                    return self
            elif self.process_request():
                return self
        return None

    def next(self):
        """Process the next event already in the receive buffer.

        One recv() may carry several WebSocket frames or body chunks that
        select() will not report again. Returns True while another event
        is ready on this connection.
        """
        if self._socket is None or self._detached:
            return False
        try:
            if self._ws_mode and self._buffer:
                return self._ws_process_buffer()
            if (self._streaming_body and self._buffer
                    and not self._body_complete):
                return self._handle_streaming_body()
        except ClientError as err:
            if self._ws_mode:
                self._event = EVENT_WS_CLOSE
                self._ws_message = None
                self._ws_mode = False
            else:
                self._error = str(err)
                self._event = EVENT_ERROR
            return True
        return False

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

        if self._is_streaming or self._ws_mode:
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
        self._headers_repeated = None
        self._data = None
        self._data_loaded = False
        self._path = None
        self._query = None
        self._content_length = None
        self._cookies = None
        self._is_streaming = False
        self._response_started = False
        self._response_keep_alive = False
        self.context = None
        self._event = None
        self._bytes_received = 0
        self._error = None
        self._streaming_body = False
        self._streaming_events = False
        self._body_complete = False
        self._to_file = None
        self._expect_continue = False
        self._request_start = None
        self._headers_scanned = 0
        self._read_paused = False
        self._read_pause_timeout = None
        self.update_activity()

    def close(self):
        """Close connection (safe on a partially built instance)"""
        self._close_file_handle()
        self._close_body_file(delete=True)
        if getattr(self, '_interest', None) is not None:
            self._unregister()
        server = getattr(self, '_server', None)
        if server is not None:
            server.remove_connection(self)
        if getattr(self, '_socket', None):
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
            self._send_buffer = bytearray()
            self._send_offset = 0

    def headers_get(self, key, default=None):
        """Return value from headers by key, or default if key not found"""
        return self._headers.get(key.lower(), default)

    def process_request(self):
        """Process HTTP request when read event on client socket"""
        if self._socket is None:
            return None
        if self._is_streaming or self._response_started:
            try:
                self._probe_streaming_close()
            except ClientError:
                self.close()
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
                data=str(err).encode('utf-8'), status=err.status,
                headers={
                    CONNECTION: CONNECTION_CLOSE,
                    CONTENT_TYPE: 'text/plain; charset=UTF-8'})
        except ClientError:
            self.close()
        return None

    def process_request_event(self):
        """Process HTTP request in event mode.

        Returns True if event is ready, False if waiting, None on error.
        """
        if self._socket is None:
            return None

        if self._ws_mode:
            try:
                return self._process_ws_event()
            except ClientError:
                self._event = EVENT_WS_CLOSE
                self._ws_message = None
                self._ws_mode = False
                return True

        if self._is_streaming or self._response_started:
            try:
                self._probe_streaming_close()
            except ClientError:
                # Peer closed the streaming response (EOF). Close
                # silently; the application will learn about it on
                # its next send_*(). No EVENT_STREAM_CLOSE today.
                self.close()
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
                return False
            return self._handle_headers_complete()

        # Phase 2: Streaming body
        if self._streaming_body:
            return self._handle_streaming_body()

        # Phase 3: Waiting for accept_body() call
        return False

    def _handle_headers_complete(self):
        """Handle completed headers, decide event type"""
        if not self.content_length:
            if self.is_websocket_request:
                self._event = EVENT_WS_REQUEST
            else:
                self._event = EVENT_REQUEST
            self._requests_count += 1
            return True

        if self._data_loaded or len(self._buffer) >= self.content_length:
            if not self._data_loaded:
                self._process_data()
            self._event = EVENT_REQUEST
            self._requests_count += 1
            return True

        self._event = EVENT_HEADERS
        return True

    def _handle_streaming_body(self):
        """Handle streaming body data"""
        self._recv_to_buffer(self._max_content_length)

        if not self._buffer:
            return False

        if self._body_file_handle:
            self._write_buffer_to_file()
            if self._event == EVENT_ERROR:
                return True

        total = self._bytes_received + len(self._buffer)
        if self.content_length and total > self.content_length:
            self._close_body_file(delete=True)
            self._error = "Unexpected data after body"
            self._event = EVENT_ERROR
            return True
        if self.content_length and total >= self.content_length:
            self._close_body_file()  # Close file before EVENT_COMPLETE
            self._body_complete = True
            self._event = EVENT_COMPLETE
            self._requests_count += 1
            return True

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

    @staticmethod
    def _check_header_value(val):
        """Check header value for CRLF injection"""
        val = str(val)
        if '\r' in val or '\n' in val:
            raise HttpError(f"Header value contains CR/LF: {val!r}")
        return val

    def _build_response_header(self, status=200, headers=None, cookies=None):
        """Build HTTP response header string

        Connection header is added automatically based on keep-alive decision if not explicitly set.
        To force connection close, set headers['connection'] = 'close'.
        """
        # .get() tolerates custom/unknown status codes without KeyError
        parts = [f'{PROTOCOLS[-1]} {status} {STATUS_CODES.get(status, "")}']

        if headers:
            for key, val in headers.items():
                self._check_header_value(key)
                self._check_header_value(val)
                parts.append(f'{key}: {val}')

        if cookies:
            for key, val in cookies.items():
                self._check_header_value(key)
                if val is None:
                    val = '; Max-Age=0'
                else:
                    self._check_header_value(val)
                parts.append(f'{SET_COOKIE}: {key}={val}')

        parts.append('\r\n')
        return '\r\n'.join(parts)

    def _prepare_response(self, headers=None, is_streaming=False):
        """Common response preparation, returns headers dict"""
        if self._response_started:
            raise HttpError("Response already sent for this request")
        self._response_started = True
        self._is_streaming = is_streaming

        if headers is None:
            headers = {}

        if not is_streaming:
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
        In WS mode reads from fragment buffer, otherwise from receive buffer.

        Returns:
            bytes or None: Data from buffer, or None if no data available.
        """
        if self._ws_mode:
            if not self._ws_fragment_buffer:
                return None
            data = bytes(self._ws_fragment_buffer)
            self._ws_fragment_buffer = bytearray()
            return data
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
        if data is not None:
            data = encode_response_data(headers, data)

        header = self._build_response_header(status, headers=headers, cookies=cookies)
        try:
            if data is not None:
                self._send(header, data)
            else:
                self._send(header)
            if not self.has_data_to_send:
                self._finalize_sent_response()
        except OSError:
            self.close()

    def respond_file(self, file_name, headers=None):
        """Respond with file content, streaming asynchronously to minimize memory usage

        WARNING: Caller must validate file_name to prevent path traversal.
        This method does not restrict file access to any base directory.

        To force connection close, set headers['connection'] = 'close'.
        """
        try:
            file_size = _os.stat(file_name)[6]  # st_size
        except (OSError, ImportError, AttributeError):
            self.respond(data='File not found', status=404)
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
        headers = self._prepare_response(headers, is_streaming=True)

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
        self._is_streaming = False

        keep_alive = self._should_keep_alive()
        self._response_keep_alive = keep_alive

        try:
            self._send(f'--{boundary}--\r\n')
            if not self.has_data_to_send:
                self._finalize_sent_response()
        except OSError:
            self.close()

    def response_stream(self, content_type=None, headers=None, cookies=None):
        """Start streaming response without Content-Length

        Sends HTTP headers and keeps connection open for streaming data.
        Use send() to send raw data or send_event() for SSE events.
        Call response_stream_end() or close() when done.

        The connection becomes long-lived: the server stops processing
        further request events on it and the application owns its
        lifetime. Peer close (EOF) is detected automatically — the
        connection is closed and the next send_*() call returns False.

        Returns True on success, False if socket is closed.
        """
        if self._socket is None:
            return False
        headers = self._prepare_response(headers, is_streaming=True)

        if CONTENT_TYPE not in headers:
            headers[CONTENT_TYPE] = (
                content_type or CONTENT_TYPE_EVENT_STREAM)
        if CACHE_CONTROL not in headers:
            headers[CACHE_CONTROL] = CACHE_CONTROL_NO_CACHE

        header = self._build_response_header(200, headers=headers, cookies=cookies)
        try:
            self._send(header)
        except OSError:
            self.close()
            return False
        return True

    def send_chunk(self, data):
        """Send raw data chunk to stream

        Args:
            data: str or bytes to send

        Returns True on success. Returns False and closes the
        connection if the socket is gone or the send buffer cap was
        hit - a consumer too slow to drain what is already queued
        (max_send_buffer_size).
        """
        if self._socket is None:
            return False
        if isinstance(data, str):
            data = data.encode('utf-8')
        try:
            self._send(data)
        except OSError:
            self.close()
            return False
        return True

    def send_event(self, data=None, event=None, event_id=None, retry=None):
        """Send SSE event to stream

        Args:
            data: Event data (str or dict/list/tuple/int/float for JSON)
            event: Event type name
            event_id: Event ID for client reconnection
            retry: Reconnection time in milliseconds

        Returns True on success. Returns False and closes the
        connection if the socket is gone or the send buffer cap was
        hit - a consumer too slow to drain what is already queued
        (max_send_buffer_size).
        """
        if self._socket is None:
            return False
        try:
            if event_id is not None:
                self._send(f'id: {event_id}\n')
            if event is not None:
                self._send(f'event: {event}\n')
            if retry is not None:
                self._send(f'retry: {retry}\n')
            if data is not None:
                if isinstance(data, (dict, list, tuple, int, float)):
                    self._send(f'data: {_json.dumps(data)}\n')
                else:
                    start = 0
                    while True:
                        pos = data.find('\n', start)
                        if pos < 0:
                            self._send(f'data: {data[start:]}\n')
                            break
                        self._send(f'data: {data[start:pos]}\n')
                        start = pos + 1
            self._send('\n')
        except OSError:
            self.close()
            return False
        return True

    def response_ndjson(self, headers=None, cookies=None):
        """Start NDJSON streaming response (application/x-ndjson).

        Thin wrapper over response_stream() with NDJSON content-type.
        Use send_ndjson() to send objects, response_stream_end() to finish.

        Returns True on success, False if socket is closed.
        """
        return self.response_stream(
                content_type=CONTENT_TYPE_NDJSON,
                headers=headers, cookies=cookies)

    def send_ndjson(self, obj):
        """Send one JSON-serializable object as an NDJSON line.

        Args:
            obj: any JSON-serializable value (dict/list/str/int/float/bool/None)

        Returns True on success. Returns False and closes the
        connection if the socket is gone or the send buffer cap was
        hit - a consumer too slow to drain what is already queued
        (max_send_buffer_size).
        """
        if self._socket is None:
            return False
        try:
            # two _send() calls reuse _send_buffer so the line goes out as
            # a single TCP segment (same pattern as send_event)
            self._send(_json.dumps(obj))
            self._send('\n')
        except OSError:
            self.close()
            return False
        return True

    def response_stream_end(self):
        """End streaming response and close connection"""
        self._is_streaming = False
        self._response_keep_alive = False
        try:
            if not self.has_data_to_send:
                self._finalize_sent_response()
        except OSError:
            pass
        self.close()

    def respond_redirect(self, url, status=302, cookies=None):
        """Create redirect respond to URL"""
        self.respond(status=status, headers={LOCATION: url}, cookies=cookies)

    # -- WebSocket methods --

    def accept_websocket(self, selector=None):
        """Accept WebSocket upgrade request.

        In event mode: switches connection to WS mode, no return value.
        In non-event mode: returns a WebSocket. Pass selector= to drive it
        from a shared loop instead of its own wait().
        """
        if not self.is_websocket_request:
            raise HttpError("Not a WebSocket upgrade request")
        if self._method != 'GET':
            raise HttpErrorWithResponse(
                400, "WebSocket upgrade requires GET")
        if self.headers_get_attribute(SEC_WEBSOCKET_VERSION) != WS_VERSION:
            raise HttpErrorWithResponse(
                426, f"Unsupported WebSocket version, need {WS_VERSION}")
        key = self.headers_get_attribute(SEC_WEBSOCKET_KEY)
        if not key:
            raise HttpErrorWithResponse(400, "Missing Sec-WebSocket-Key")
        accept = _ws_accept_key(key)
        self._response_started = True
        # Must be set before _send to prevent _finalize_sent_response
        self._ws_mode = True
        self._send(
            'HTTP/1.1 101 Switching Protocols\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            f'{SEC_WEBSOCKET_ACCEPT}: {accept}\r\n'
            '\r\n')
        if not self._server.event_mode:
            self._detached = True
            self._unregister()
            self._server.remove_connection(self)
            return WebSocket(self, selector)

    def ws_send(self, data):
        """Send WebSocket message (event mode).
        str -> text frame, bytes -> binary frame.

        Returns True on success, False if socket is closed or the send
        buffer cap was hit (slow consumer)."""
        if self._socket is None:
            return False
        try:
            if isinstance(data, str):
                self._send(_ws_build_frame(
                    WS_OPCODE_TEXT, data.encode('utf-8')))
            else:
                self._send(_ws_build_frame(WS_OPCODE_BINARY, data))
            self.update_activity()
        except OSError:
            self.close()
            return False
        return True

    def ws_ping(self, data=b''):
        """Send WebSocket ping (event mode).

        Returns True on success, False if socket is closed or buffer
        cap was hit."""
        if self._socket is None:
            return False
        if isinstance(data, str):
            data = data.encode('utf-8')
        try:
            self._send(_ws_build_frame(WS_OPCODE_PING, data))
        except OSError:
            self.close()
            return False
        return True

    def ws_close(self, code=1000, reason=''):
        """Send WebSocket close and close connection (event mode).

        Always closes the connection; returns True if the close frame
        was queued, False if it could not be (socket already closed
        or buffer cap)."""
        if self._socket is None:
            return False
        payload = bytearray()
        payload.append((code >> 8) & 0xFF)
        payload.append(code & 0xFF)
        if reason:
            payload.extend(reason.encode('utf-8'))
        sent = True
        try:
            self._send(_ws_build_frame(WS_OPCODE_CLOSE, bytes(payload)))
        except OSError:
            sent = False
        self.close()
        return sent

    # -- WebSocket internal methods --

    def _ws_recv(self):
        """Read available data from socket for WebSocket"""
        try:
            data = self._socket.recv(self._file_chunk_size)
        except OSError as err:
            if err.errno in _WOULDBLOCK or err.errno == errno.ENOENT:
                return
            raise HttpDisconnected(f"{err}: {self.addr}") from err
        if data is None:
            return
        if not data:
            raise HttpDisconnected(
                f"WebSocket disconnected: {self.addr}")
        self._rx_bytes_counter += len(data)
        self._buffer.extend(data)
        self.update_activity()

    def _ws_do_send(self, data):
        """Send WebSocket frame data (hook for _WsFrameMixin)"""
        self._send(data)

    def _ws_on_close(self):
        """Handle WebSocket close (hook for _WsFrameMixin)"""
        self._ws_mode = False

    def _process_ws_event(self):
        """Process WebSocket data in event mode"""
        self._ws_recv()
        if not self._buffer:
            return False
        return self._ws_process_buffer()


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
        proxies = kwargs.pop('trusted_proxies', None)
        if isinstance(proxies, str):
            # a str would make the membership tests substring matches:
            # '92.168.1.1' in '192.168.1.10' is True, trust would be forged
            raise ValueError(
                "trusted_proxies must be a list of IP addresses, not a str")
        self._trusted_proxies = set(proxies) if proxies else None
        # Shared selector may be passed in; an owned one is closed in close().
        selector = kwargs.pop('selector', None)
        self._owns_selector = selector is None
        self._selector = selector or _selectors.DefaultSelector()
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
        self._resume = None  # last returned connection, drained via next()
        self._last_maintenance = 0
        self._maintenance_interval = min(
            kwargs.get('keep_alive_timeout', KEEP_ALIVE_TIMEOUT),
            kwargs.get('request_timeout', REQUEST_TIMEOUT)) / 2
        self._selector.register(self._socket, _selectors.EVENT_READ, self)

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
    def selector(self):
        """The selectors.BaseSelector this server registers its sockets in.

        Shared-selector loops read events from here and dispatch via
        key.data.handle_event(); see wait() for the single-server case.
        """
        return self._selector

    def close(self):
        """Close the server, its connections and (if owned) the selector"""
        for connection in list(self._waiting_connections):
            connection.close()
        if self._socket is not None:
            try:
                self._selector.unregister(self._socket)
            except (KeyError, ValueError, OSError):
                pass
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        if self._owns_selector:
            try:
                self._selector.close()
            except OSError:
                pass

    def remove_connection(self, connection):
        if connection is self._resume:
            self._resume = None
        if connection in self._waiting_connections:
            self._waiting_connections.remove(connection)

    def maintenance(self):
        """Close idle / timed-out connections.

        wait() calls this after each tick that returned events; call it once
        per iteration when driving a shared selector yourself. Scans at most
        once per half the shortest timeout.
        """
        now = _time.time()
        if now - self._last_maintenance < self._maintenance_interval:
            return
        self._last_maintenance = now
        for connection in list(self._waiting_connections):
            if connection._read_paused:
                if connection.is_timed_out:
                    connection.close()  # stuck consumer guard
                continue
            if connection._is_streaming:
                continue
            if connection._ws_mode:
                if connection.is_timed_out:
                    connection.close()
                continue
            if connection._response_started:
                continue
            if not connection.is_loaded and connection.is_timed_out:
                connection.respond(
                    'Request Timeout', status=408,
                    headers={CONNECTION: CONNECTION_CLOSE})
            elif (not connection.is_loaded
                    and connection._request_start
                    and now - connection._request_start
                    > connection._request_timeout):
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
        while len(self._waiting_connections) >= self._max_clients:
            connection_to_remove = self._waiting_connections.pop(0)
            if connection_to_remove._response_started:
                # Already responding (e.g., multipart stream) - just close
                connection_to_remove.close()
            else:
                connection_to_remove.respond(
                    'Request Timeout, too many requests', status=408,
                    headers={CONNECTION: CONNECTION_CLOSE})
                if connection_to_remove.has_data_to_send:
                    connection_to_remove.close()  # stalled 408: don't leak
        self._waiting_connections.append(connection)
        if not connection._register():
            connection.close()

    def handle_event(self, fileobj, mask):
        """Owner dispatch for the listening socket: accept, returns None"""
        if self._socket is not None:
            self._accept()
        return None

    def _owns(self, owner):
        return owner is self or (
            isinstance(owner, HttpConnection) and owner._server is self)

    def wait(self, timeout=1):
        """Wait for socket activity, returns a loaded HttpConnection or None.

        On a shared selector only this server's sockets are serviced. A
        returned connection is drained via next() on the following call.
        """
        if self._socket is None:
            if timeout:
                _time.sleep(timeout)
            return None
        resume = self._resume
        self._resume = None
        if resume is not None and resume.next():
            self._resume = resume
            return resume
        try:
            events = self._selector.select(timeout)
        except (OSError, ValueError) as err:
            # ValueError/EBADF: closed; EINVAL: invalid socket on Windows
            if isinstance(err, ValueError) or err.errno in (
                    errno.EBADF, errno.EINVAL):
                return None
            raise
        if not events:
            return None  # cleanup is traffic-driven, see maintenance()
        result = None
        for key, mask in events:
            if not self._owns(key.data):
                continue
            ready = key.data.handle_event(key.fileobj, mask)
            if ready is not None:
                result = ready
                self._resume = ready
                break
        self.maintenance()
        return result
