# uHTTP Server

Micro HTTP server for MicroPython and CPython.

## Features

- MicroPython and CPython compatible
- Low-level POSIX socket implementation
- Fully synchronous but handles multiple connections
- Delayed response support (hold client, reply later)
- Raw data (HTML, binary) and JSON support
- SSL/TLS for HTTPS connections
- IPv6 and dual-stack support
- Event mode for streaming large uploads
- WebSocket support (RFC 6455)
- Server-Sent Events (SSE) streaming
- NDJSON streaming responses (`application/x-ndjson`)
- Memory-efficient (~32KB RAM minimum)

## Installation

```bash
pip install uhttp-server
```

For MicroPython, copy `uhttp/server.py` to your device.


## Usage

```python
import uhttp.server

server = uhttp.server.HttpServer(port=9980)

while True:
    client = server.wait()
    if client:
        if client.path == '/':
            # result is html
            client.respond("<h1>hello</h1><p>uHTTP</p>")
        elif client.path == '/rpc':
            # result is json
            client.respond({'message': 'hello', 'success': True, 'headers': client.headers, 'query': client.query})
        else:
            client.respond("Not found", status=404)
```


## Migrating from 2.x to 3.0

3.0 replaces the internal `select.select()` loop with a
[`selectors`](https://docs.python.org/3/library/selectors.html) selector that
the server, its connections and its WebSockets **register themselves** in.
Dispatch goes through the owner stored in `key.data`, so one loop can drive
several servers, their connections and other subsystems (including
[uhttp-client](https://github.com/cortexm/uhttp-client) 3.x) together.

**Removed:** `read_sockets`, `write_sockets`, `process_events()`,
`event_read()`, `event_write()` — on `HttpServer` and on `WebSocket`.

`server.wait(timeout)` is unchanged, so single-server code needs no edits.

### External loop

```python
# 2.x
while True:
    r, w, _ = select.select(
        http.read_sockets + https.read_sockets,
        http.write_sockets + https.write_sockets, [], 1.0)
    client = http.process_events(r, w) or https.process_events(r, w)
    if client:
        serve(client)

# 3.0
selector = selectors.DefaultSelector()
http = HttpServer(port=80, selector=selector)
https = HttpServer(port=443, ssl_context=ctx, selector=selector)

while True:
    for key, mask in selector.select(1.0):
        client = key.data.handle_event(key.fileobj, mask)
        if isinstance(client, HttpConnection):
            serve(client)
            while client.next():   # drain events buffered from one recv
                serve(client)
    http.maintenance()
    https.maintenance()
```

Three rules carry the whole migration:

1. **`key.data.handle_event(key.fileobj, mask)`** replaces `process_events()`.
   It returns the connection when a request/event is ready, otherwise `None`.
   Anything you register yourself must expose the same method — register an
   owner object, not a bare callback.
2. **`while client.next():`** — one `recv()` can carry several WebSocket
   frames or body chunks, and `select()` will not report them again because the
   OS buffer is already drained. Drain them before blocking. `wait()` does this
   for you.
3. **`maintenance()` once per loop iteration** — keep-alive and header
   timeouts have no event to ride on. Call it every time round; it throttles
   itself to at most one scan per half the shortest timeout, so calling it
   often costs nothing. `wait()` calls it for you.

### Writing from application code

A send does not have to come from an event handler. `respond()`, `send_event()`,
`send_ndjson()`, `ws_send()` — called from a timer, another thread's queue, any
code that holds the connection — buffer the data and **arm write interest
themselves**, so the shared loop flushes it on the next `EVENT_WRITE` without
you touching the selector. A server-push stream driven from your own tick works
the same as one driven from a request.

What the loop still owes you is a turn: the data leaves on the next
`selector.select()` pass, so the timeout you pass there is also the worst-case
latency of a push.

### Registering your own sockets

Register the object that owns the socket, with the socket as the key:

```python
selector.register(my_sock, selectors.EVENT_READ, my_owner)
```

The contract for `my_owner` is:

- **`handle_event(fileobj, mask)`** is the only method the loop calls. Return
  value is yours — the loop above only unwraps `HttpConnection`.
- **You own registration.** Nothing in uhttp registers, modifies or unregisters
  a key it did not create: `wait()` and `maintenance()` skip keys whose
  `key.data` is not this server or one of its connections, and `server.close()`
  closes the selector only if the server created it. Unregister before you
  close your socket — a closed fd left in a selector raises on the next
  `select()`.
- **You call `modify()` yourself** to arm and disarm `EVENT_WRITE`. Arm it when
  a write buffers, disarm it when the buffer empties: leave it armed and the
  loop spins, forget to arm it and the write stalls.

```python
class Sender:
    def __init__(self, selector, sock):
        self._selector, self._sock = selector, sock
        self._out = bytearray()
        self._mask = selectors.EVENT_READ
        selector.register(sock, self._mask, self)

    def send(self, data):
        self._out.extend(data)
        self._want(selectors.EVENT_READ | selectors.EVENT_WRITE)

    def handle_event(self, fileobj, mask):
        if mask & selectors.EVENT_WRITE:
            sent = self._sock.send(self._out)
            self._out[:] = self._out[sent:]
            if not self._out:
                self._want(selectors.EVENT_READ)
        if mask & selectors.EVENT_READ:
            ...
        return None

    def _want(self, mask):
        if mask != self._mask:
            self._selector.modify(self._sock, mask, self)
            self._mask = mask

    def close(self):
        self._selector.unregister(self._sock)
        self._sock.close()
```

### WebSocket

Non-event mode still returns a `WebSocket` with the same `wait()`,
`read_buffer()`, `send()` and `is_closed`, but it is now a facade over the
connection — so it honours `max_send_buffer_size` and `send()`/`ping()` return
`False` when they cannot queue. To drive it from a shared loop, hand the
selector to the upgrade:

```python
ws = client.accept_websocket(selector=selector)   # joins the shared loop
ws = client.accept_websocket()                    # owns one, use ws.wait()
```

`wait()` refuses a shared selector: a blocking wait cannot service the other
owners' ready keys.

### Not yet on MicroPython

`selectors` is not in the MicroPython standard library, so 3.x is CPython-only
until a shim lands. Stay on 2.x on-device.

## Migrating from 3.1 to 3.2

The client address properties changed shape — a breaking change inside 3.x,
because the old ones could not express an IPv6 address unambiguously and the
`X-Forwarded-For` handling was spoofable. See
[Behind a reverse proxy](#behind-a-reverse-proxy) for the new rule.

| | 3.1 | 3.2 |
|---|---|---|
| `remote_address` | `'ip:port'`, or bare `'ip'` from `X-Forwarded-For` | always bare `'ip'` |
| `remote_addresses` | `'a, b'` string, or `'ip:port'` | `['a', 'b']`, or `[socket_ip]` |
| `socket_address` | `'2001:db8::1:8080'` | `'[2001:db8::1]:8080'` |

```python
# 3.1
ip = client.remote_address.rsplit(':', 1)[0]      # broken for IPv6
chain = client.remote_addresses.split(',')

# 3.2
ip = client.remote_address
chain = client.remote_addresses
port = client.addr[1]                             # if you really need it
```

`remote_address` can also return a **different** address than it did in 3.1:
with a multi-hop proxy chain it is now the rightmost untrusted hop, so list
every proxy in `trusted_proxies` or the walk stops at the one you omitted.

## SSL/HTTPS Support

uHTTP supports SSL/TLS encryption for HTTPS connections on both CPython and MicroPython.

### Basic HTTPS Server

```python
import ssl
import uhttp.server

# Create SSL context
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')

# Create HTTPS server
server = uhttp.server.HttpServer(port=443, ssl_context=context)

while True:
    client = server.wait()
    if client:
        # Check if connection is secure
        if client.is_secure:
            client.respond({'message': 'Secure HTTPS connection!'})
        else:
            client.respond({'message': 'Insecure HTTP connection'})
```

### Using Let's Encrypt / Certbot Certificates

[Certbot](https://certbot.eff.org/) creates certificates in `/etc/letsencrypt/live/your-domain/` with these files:

- `cert.pem` - Your domain certificate only
- `chain.pem` - Certificate authority chain
- **`fullchain.pem`** - Your certificate + CA chain (use this for `certfile`)
- **`privkey.pem`** - Private key (use this for `keyfile`)

**Important:** Always use `fullchain.pem` (not `cert.pem`) as the certificate file. Without the full chain, clients will get "certificate verification failed" errors.

#### Example with Certbot Certificates

```python
import ssl
import uhttp.server

# Create SSL context with Let's Encrypt certificates
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(
    certfile='/etc/letsencrypt/live/example.com/fullchain.pem',
    keyfile='/etc/letsencrypt/live/example.com/privkey.pem'
)

# Create HTTPS server
server = uhttp.server.HttpServer(
    address='0.0.0.0',
    port=443,
    ssl_context=context
)

while True:
    client = server.wait()
    if client:
        client.respond({'message': 'Hello from HTTPS!'})
```

#### Permissions Note

The `/etc/letsencrypt/` directory requires root access. You have two options:

1. **Run as root** (not recommended for production):
   ```bash
   sudo python3 your_server.py
   ```

2. **Copy certificates to accessible location** (recommended):
   ```bash
   # Copy certificates to your application directory
   sudo cp /etc/letsencrypt/live/example.com/fullchain.pem ~/myapp/
   sudo cp /etc/letsencrypt/live/example.com/privkey.pem ~/myapp/
   sudo chown youruser:youruser ~/myapp/*.pem
   sudo chmod 600 ~/myapp/privkey.pem
   ```

   Then use the copied files:
   ```python
   context.load_cert_chain(
       certfile='/home/youruser/myapp/fullchain.pem',
       keyfile='/home/youruser/myapp/privkey.pem'
   )
   ```

#### Certificate Renewal

Let's Encrypt certificates expire every 90 days. After renewal with `certbot renew`, restart your server to load the new certificates, or implement a reload mechanism:

```bash
# Renew certificates
sudo certbot renew

# Restart your application
sudo systemctl restart your-app
```

### HTTP to HTTPS Redirect

Run both HTTP and HTTPS servers to redirect HTTP traffic:

```python
import ssl
import selectors
import uhttp.server

# SSL context for HTTPS
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(
    certfile='/etc/letsencrypt/live/example.com/fullchain.pem',
    keyfile='/etc/letsencrypt/live/example.com/privkey.pem'
)

# One shared selector drives both servers. On a ready key,
# key.data.handle_event() dispatches to the owning server/connection.
selector = selectors.DefaultSelector()

# HTTP server (redirects)
http_server = uhttp.server.HttpServer(port=80, selector=selector)

# HTTPS server (serves content)
https_server = uhttp.server.HttpServer(
    port=443, ssl_context=context, selector=selector)

def serve(client):
    if client.is_secure:
        client.respond({'message': 'Secure content'})   # HTTPS content
    else:
        client.respond_redirect(f"https://{client.host}{client.url}")  # redirect

while True:
    for key, mask in selector.select(1.0):
        client = key.data.handle_event(key.fileobj, mask)
        if isinstance(client, uhttp.server.HttpConnection):
            serve(client)
            while client.next():   # drain events buffered from one recv (WS/stream)
                serve(client)
    http_server.maintenance()
    https_server.maintenance()
```

### Testing SSL Locally

For local development, create self-signed certificates:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj "/CN=localhost"
```

Then use them:
```python
context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
```

Test with curl (use `-k` to accept self-signed certificates):
```bash
curl -k https://localhost:8443/
```

See [examples/](../examples/) directory for complete working examples.


## API

### General methods:

**`import uhttp.server`**

**`uhttp.server.decode_percent_encoding(data)`**

- Decode percent encoded data (bytes)

**`uhttp.server.parse_header_parameters(value)`**

- Parse parameters/directives from header value, returns dict

**`uhttp.server.parse_cookies(raw_cookies)`**

- Parse cookie string into dict

**`uhttp.server.parse_query(raw_query, query=None)`**

- Parse raw_query from URL, append it to existing query, returns dict

**`uhttp.server.parse_url(url)`**

- Parse URL to path and query

**`uhttp.server.parse_header_line(line)`**

- Parse header line to key and value

**`uhttp.server.encode_response_data(headers, data)`**

- Encode response data by its type


### Class `HttpServer`:

**`HttpServer(address='0.0.0.0', port=80, ssl_context=None, event_mode=False, **kwargs)`**

Parameters:
- `address` - IP address to bind to (default: '0.0.0.0')
- `port` - Port to listen on (default: 80)
- `ssl_context` - Optional `ssl.SSLContext` for HTTPS connections (default: None)
- `event_mode` - Enable event mode for streaming uploads (default: False)
- `**kwargs` - Additional options:
  - `max_waiting_clients` - Maximum concurrent connections (default: 32)
  - `keep_alive_timeout` - Keep-alive timeout in seconds (default: 15)
  - `keep_alive_max_requests` - Max requests per connection (default: 100)
  - `request_timeout` - Max seconds from first byte to complete headers (default: 5)
  - `max_headers_length` - Maximum header size in bytes (default: 4KB)
  - `max_content_length` - Maximum body size in bytes (default: 512KB, only enforced when event_mode=False)
  - `max_send_buffer_size` - Maximum pending bytes in send buffer for backpressure (default: 64KB). When a slow client cannot drain TCP fast enough, `_send()` raises `OSError` instead of growing `_send_buffer` unbounded.
  - `max_ws_message_length` - Maximum WebSocket message size before chunking (default: 64KB)
  - `file_chunk_size` - Chunk size in bytes for streaming file responses (default: 4KB)
  - `listen` - Listening socket backlog (default: 8)
  - `trusted_proxies` - List of trusted proxy IP addresses (default: None). When set, `X-Forwarded-For` is honoured for connections from these IPs; when not set it is ignored entirely. List **every** proxy in the chain, not just the one the server talks to — see [Behind a reverse proxy](#behind-a-reverse-proxy).
  - `selector` - A `selectors.BaseSelector` to register sockets in (default: a `DefaultSelector` the server owns and closes). Pass the same instance to several servers (and register your own sockets in it) to drive them from one loop.

#### Properties:

**`socket(self)`**

- Server socket

**`selector(self)`**

- The `selectors.BaseSelector` the server registers its sockets in. Read events from it and dispatch via `key.data.handle_event()` for a shared-selector loop across several servers / your own sockets (register those with an owner object exposing `handle_event(fileobj, mask)`). Pass `selector=` to the constructor to share one; `wait()` on a shared selector services only this server's sockets.

**`is_secure(self)`**

- Returns `True` if server uses SSL/TLS, `False` otherwise

**`event_mode(self)`**

- Returns `True` if event mode is enabled

#### Methods:

**`wait(self, timeout=1)`**

- Wait for socket activity with the given timeout, returns None or an `HttpConnection` with an established request. Single-server convenience backed by the server's own selector; internally accepts connections, flushes writes, and runs `maintenance()`.

**`handle_event(self, fileobj, mask)`**

- Owner dispatch for the server's listening socket (stored as `key.data`). Accepts a new connection and returns None. Used by a shared-selector loop. (Connections stored as `key.data` expose the same method; theirs returns the connection when a request is ready.)

**`maintenance(self)`**

- Closes idle / timed-out connections — keep-alive and request-header timeouts have no event to ride on. Call it once per loop iteration when driving a shared selector yourself; `wait()` calls it for you. Calling it often is cheap: it throttles itself to at most one scan per half the shortest configured timeout, so the real cleanup rate is bounded by the timeouts, not by your loop.


### Class `HttpConnection`:

**`HttpConnection(server, sock, addr, **kwargs)`**

#### Properties:

**`addr(self)`**

- Client address tuple (ip, port)

**`socket_address(self)`**

- Client socket address as string `ip:port`, always the socket IP, ignores `X-Forwarded-For`. IPv6 is bracketed per RFC 3986: `[2001:db8::1]:8080`

**`remote_address(self)`**

- Client IP address as string, without port. The last hop in `remote_addresses` that is not in `trusted_proxies`, counted from the right. Falls back to the socket IP when every hop is trusted, so it is never empty.

**`remote_addresses(self)`**

- The `X-Forwarded-For` chain as a list of IP strings, client first and last proxy last — the same shape as Werkzeug's `access_route` or Express's `req.ips`. Falls back to `[socket_ip]` when the header is absent or the socket peer is not a trusted proxy. Entries left of `remote_address` are **not** trustworthy — use `remote_address` for anything that makes a decision.

**`method(self)`**

- HTTP method

**`url(self)`**

- URL address

**`host(self)`**

- URL address

**`full_url(self)`**

- URL address

**`protocol(self)`**

- Protocol

**`headers(self)`**

- headers dict

**`data(self)`**

- Content data

**`path(self)`**

- Path

**`query(self)`**

- Query dict

**`cookies(self)`**

- Cookies dict

**`is_secure(self)`**

- Returns `True` if connection is using SSL/TLS, `False` otherwise

**`socket(self)`**

- This socket

**`is_loaded(self)`**

- Returns `True` when request is fully loaded and ready for response

**`content_length(self)`**

- Content length

**`event(self)`** (event mode only)

- Current event type: `EVENT_REQUEST`, `EVENT_HEADERS`, `EVENT_DATA`, `EVENT_COMPLETE`, or `EVENT_ERROR`

**`bytes_received(self)`** (event mode only)

- Number of body bytes received so far

**`error(self)`** (event mode only)

- Error message when event is `EVENT_ERROR`

**`context`** (event mode only)

- Application storage attribute for request state (read-write)

**`is_websocket_request(self)`**

- True if request is a WebSocket upgrade request

**`is_websocket(self)`**

- True if connection is in WebSocket mode

**`ws_message(self)`**

- Last received WebSocket message (str for text frames, bytes for binary frames)

#### Methods:

**`headers_get(self, key, default=None)`**

- Return value from headers by key, or default if key not found
- Repeated field lines are combined into one value per RFC 9110 5.3 —
  with `, `, except `Cookie`, which RFC 6265 separates with `; `

**`headers_all(self, key)`**

- Return every value of a repeated header as a list, in the order received,
  or `[]` if the header was not sent. Use it when combining would be
  ambiguous, e.g. a value that may itself contain a comma:

```python
client.headers_get('accept')   # 'text/html, application/xml, text/plain'
client.headers_all('accept')   # ['text/html, application/xml', 'text/plain']
```

**`process_request(self)`**

- Process HTTP request when read event on client socket

**`handle_event(self, fileobj, mask)`**

- Owner dispatch for this connection (stored as `key.data`). Drives read/write for the given readiness `mask` and returns the connection when a request/event is ready, else None. Used by a shared-selector loop.

**`next(self)`**

- Process the next event already in the receive buffer, returning True while another event is ready on this same connection. One `recv()` can pull several WebSocket frames / body chunks that `select()` will not report again; call `next()` in a loop after handling an event to drain them. Returns False on a plain HTTP connection or after a non-event WebSocket handoff.

**`respond(self, data=None, status=200, headers=None, cookies=None)`**

- Create general response with data, status and headers as dict

**`respond_redirect(self, url, status=302, cookies=None)`**

- Create redirect response to URL

**`respond_file(self, file_name, headers=None)`**

- Respond with file content, streaming asynchronously to minimize memory usage
- **⚠️ Security:** this method does **not** restrict file access to any base
  directory and does **not** sanitize `file_name`. If you build `file_name`
  from request data (e.g. `client.path`), an attacker can use `..` to escape
  your web root (path traversal) and read arbitrary files. Always validate the
  path yourself first: reject any segment equal to `..`, then join onto a fixed
  base directory and verify the result stays inside it. Note `client.path` is
  already percent-decoded, so `%2e%2e` and `..` look identical to your check.

**`response_multipart(self, headers=None)`**

- Create multipart response with headers as dict (for MJPEG streams etc.)

**`response_multipart_frame(self, data, headers=None, boundary=None)`**

- Send multipart frame with data and headers

**`response_multipart_end(self, boundary=None)`**

- Finish multipart stream

**`response_stream(self, content_type=None, headers=None, cookies=None)`**

- Start streaming response without Content-Length
- Default content type: `text/event-stream`, sets `Cache-Control: no-cache`
- Returns `True` on success, `False` if socket is closed

**`send_chunk(self, data)`**

- Send raw data chunk (str or bytes) to stream
- Use for SSE comments (`: ping\n\n`), custom formats, or non-SSE streaming
- Returns `True` on success. Returns `False` **and closes the connection** if the socket is gone or the send buffer cap was hit — a consumer too slow to drain what is already queued (`max_send_buffer_size`)

**`send_event(self, data=None, event=None, event_id=None, retry=None)`**

- Send formatted SSE event
- `data` - str or dict/list/tuple/int/float (auto JSON serialized). Multi-line strings split into multiple `data:` lines
- `event` - Event type name (client listens via `addEventListener()`)
- `event_id` - Event ID for client reconnection (`Last-Event-ID` header)
- `retry` - Reconnection time in milliseconds
- Returns `True` on success. Returns `False` **and closes the connection** if the socket is gone or the send buffer cap was hit — a consumer too slow to drain what is already queued (`max_send_buffer_size`)

**`response_ndjson(self, headers=None, cookies=None)`**

- Start NDJSON streaming response (`application/x-ndjson`)
- Thin wrapper over `response_stream()` with NDJSON content type
- Returns `True` on success, `False` if socket is closed

**`send_ndjson(self, obj)`**

- Serialize one JSON-serializable value as a single NDJSON line (`json.dumps(obj) + '\n'`)
- `obj` - any JSON-serializable value (dict/list/str/int/float/bool/None)
- Returns `True` on success. Returns `False` **and closes the connection** if the socket is gone or the send buffer cap was hit — a consumer too slow to drain what is already queued (`max_send_buffer_size`)

**`response_stream_end(self)`**

- End stream and close connection (used for both SSE and NDJSON)

**`accept_body(self, streaming=False, to_file=None)`** (event mode only)

- Accept body after `EVENT_HEADERS`. Call this to start receiving body data.
- `streaming=False` (default) - Buffer all data, receive only `EVENT_COMPLETE`
- `streaming=True` - Receive `EVENT_DATA` for each chunk, read with `read_buffer()`
- `to_file="/path"` - Save body directly to file
- Returns: Number of bytes already waiting in buffer

**`read_buffer(self)`** (event mode only)

- Read available data from buffer
- Returns: bytes or None if no data available

**`accept_websocket(self)`**

- Accept WebSocket upgrade. Event mode: switches to WS mode. Non-event mode: returns `WebSocket` object.

**`ws_send(self, data)`** (event mode, WebSocket)

- Send WebSocket message. `str` → text frame, `bytes` → binary frame.

**`ws_ping(self, data=b'')`** (event mode, WebSocket)

- Send WebSocket ping frame

**`ws_close(self, code=1000, reason='')`** (event mode, WebSocket)

- Send WebSocket close frame and close connection

**`pause_reading(self, timeout=None)`**

- Stop reading from the socket so its kernel buffer fills and TCP stalls the peer (inbound backpressure). Use when the application cannot keep up with an inbound WebSocket stream or a request-body upload. Outbound sending is unaffected.
- `timeout` - seconds the connection may stay paused before `maintenance()` closes it as a stuck consumer. `None` (default) uses the keep-alive timeout; a value `<= 0` disables the deadline (the application owns the lifecycle). A slow-but-alive consumer that resumes and drains periodically stays alive, since each read refreshes the activity clock.

**`resume_reading(self)`**

- Resume reading after `pause_reading()` and re-arm the socket.


## Server-Sent Events (SSE)

SSE enables one-way server-to-client streaming over HTTP. The browser's `EventSource` API handles automatic reconnection.

### Basic SSE Stream

```python
import time
import uhttp.server

server = uhttp.server.HttpServer(port=8080)
sse_clients = []

while True:
    client = server.wait(timeout=0.1)

    if client:
        if client.path == '/events':
            if client.response_stream():
                sse_clients.append(client)
        else:
            client.respond("hello")

    # Send events to connected clients
    for sc in list(sse_clients):
        if not sc.send_event({'temp': 23.5}, event='sensor', event_id=1):
            sse_clients.remove(sc)  # Client disconnected
```

### SSE with Reconnection Support

When a client reconnects, the browser sends `Last-Event-ID` header automatically:

```python
if client.path == '/events':
    last_id = client.header('last-event-id')
    client.response_stream()
    if last_id:
        # Resend missed events from history
        for event in history.since(int(last_id)):
            client.send_event(event['data'], event_id=event['id'])
    sse_clients.append(client)
```

### Keep-alive

Send comments periodically to prevent proxy/load balancer timeouts:

```python
if time.time() - last_ping > 15:
    for sc in list(sse_clients):
        if not sc.send_chunk(': ping\n\n'):
            sse_clients.remove(sc)
```

### SSE Protocol Format

```
id: 1
event: sensor
retry: 5000
data: {"temp": 23.5}

```

- Each field on its own line, event terminated by empty line
- `data:` — event payload (required for client to receive data)
- `event:` — event type, client listens via `addEventListener()`
- `id:` — event ID, sent back as `Last-Event-ID` on reconnect
- `retry:` — reconnection delay in milliseconds
- `: comment` — ignored by client, used for keep-alive pings


## NDJSON Streaming

NDJSON (Newline-Delimited JSON, `application/x-ndjson`) is a minimal one-way server-to-client streaming format: one JSON value per line, separated by `\n`. Compared to SSE it has no field structure (`event:`/`id:`/`retry:`) and no browser-side reconnect API — just raw JSON records. Useful for bulk data exports, log tailing, internal APIs, and any non-browser HTTP client that consumes a stream of records.

### Basic NDJSON Stream

```python
import uhttp.server

server = uhttp.server.HttpServer(port=8080)

while True:
    client = server.wait(timeout=0.1)
    if not client:
        continue

    if client.path == '/export':
        if client.response_ndjson():
            for row in db.iter_rows():
                if not client.send_ndjson(row):
                    break  # client disconnected
            client.response_stream_end()
    else:
        client.respond("hello")
```

### Stream Termination

NDJSON has no in-band end-of-stream marker — the client detects end of stream by **TCP connection close** (EOF on `recv()`). Therefore `response_stream_end()` always closes the connection (no keep-alive).

If you need to signal *why* the stream ended (e.g. completion vs. server-initiated abort), send a sentinel record on the application level before closing:

```python
client.send_ndjson({'_end': True, 'reason': 'done'})
client.response_stream_end()
```

### Wire Format

```
{"id":1,"temp":23.5}
{"id":2,"temp":23.7}
{"id":3,"temp":23.6}
```

- One JSON value per line, terminated by `\n`
- No leading/trailing wrapping; concatenation of records is the body
- Each `send_ndjson()` call emits exactly one line
- `json.dumps()` escapes embedded newlines, so records cannot break the framing

### Client Example

```python
import requests, json
with requests.get('http://localhost:8080/export', stream=True) as r:
    for line in r.iter_lines():
        if line:
            record = json.loads(line)
            print(record)
```

### NDJSON vs SSE

| Aspect              | NDJSON                       | SSE                          |
|---------------------|------------------------------|------------------------------|
| Content type        | `application/x-ndjson`       | `text/event-stream`          |
| Per-record metadata | none (just JSON)             | `event:`, `id:`, `retry:`    |
| Browser API         | none (manual `fetch` stream) | `EventSource` w/ auto-reconnect |
| Multi-line payload  | not allowed (one line = one record) | `data:` repeated per line |
| End of stream       | TCP close                    | TCP close (or app-level event) |
| Typical use         | bulk export, logs, APIs      | live UI updates in browser   |


## WebSocket Support

uHTTP supports WebSocket connections (RFC 6455) in both event mode and non-event mode.

**Shared-selector loops:** both modes work. In event mode the connection stays
the owner; in non-event mode pass the selector to the upgrade —
`ws = client.accept_websocket(selector=sel)` — and the returned `WebSocket`
registers itself as `key.data`, dispatching through `handle_event(fileobj, mask)`
and `next()` like every other owner. Without a selector it owns one and you use
its blocking `wait(timeout)`.

**Security:** The server does not validate the `Origin` header during WebSocket upgrade. To prevent cross-site WebSocket hijacking, validate the `Origin` header in your application before calling `accept_websocket()`.

### Event Mode (non-blocking, multiple connections)

```python
from uhttp.server import (
    HttpServer, EVENT_REQUEST, EVENT_WS_REQUEST,
    EVENT_WS_MESSAGE, EVENT_WS_CHUNK_FIRST,
    EVENT_WS_CHUNK_NEXT, EVENT_WS_CHUNK_LAST,
    EVENT_WS_PING, EVENT_WS_CLOSE
)

server = HttpServer(port=8080, event_mode=True)

while True:
    client = server.wait()
    if not client:
        continue

    if client.event == EVENT_WS_REQUEST:
        client.accept_websocket()

    elif client.event == EVENT_REQUEST:
        client.respond({'status': 'ok'})

    elif client.event == EVENT_WS_MESSAGE:
        data = client.read_buffer()  # bytes
        if client.ws_is_text:
            client.ws_send(data.decode('utf-8'))  # echo as text
        else:
            client.ws_send(data)  # echo as binary

    elif client.event == EVENT_WS_CHUNK_FIRST:
        client.context = {'chunks': [client.read_buffer()]}

    elif client.event == EVENT_WS_CHUNK_NEXT:
        client.context['chunks'].append(client.read_buffer())

    elif client.event == EVENT_WS_CHUNK_LAST:
        client.context['chunks'].append(client.read_buffer())
        # process all chunks...
        client.ws_send('received')

    elif client.event == EVENT_WS_PING:
        pass  # pong sent automatically

    elif client.event == EVENT_WS_CLOSE:
        print("WebSocket closed")
```

### Non-Event Mode (blocking, simple)

```python
from uhttp.server import HttpServer

server = HttpServer(port=8080)

while True:
    client = server.wait()
    if client and client.is_websocket_request:
        ws = client.accept_websocket()  # returns WebSocket object
        while not ws.is_closed:
            msg = ws.recv(timeout=5)
            if msg is not None:
                ws.send(msg)  # echo
    elif client:
        client.respond("hello")
```

### WebSocket API

**HttpConnection properties:**
- `is_websocket_request` - True if request is a WebSocket upgrade
- `is_websocket` - True if connection is in WebSocket mode
- `ws_is_text` - True if current message is text frame (vs binary)
- `ws_message` - Ping/close payload (only for `EVENT_WS_PING` and `EVENT_WS_CLOSE`)

**HttpConnection methods (event mode):**
- `accept_websocket()` - Accept upgrade, switch to WebSocket mode
- `read_buffer()` - Read message data (returns bytes, use `ws_is_text` to check type)
- `ws_send(data)` - Send message (str → text frame, bytes → binary frame)
- `ws_ping(data=b'')` - Send ping frame
- `ws_close(code=1000, reason='')` - Close WebSocket connection
- `pause_reading(timeout=None)` / `resume_reading()` - Inbound backpressure (see [Backpressure](#backpressure-pausing-reads))

**WebSocket object (non-event mode):**
- `recv(timeout=None)` - Receive message (blocking). Returns str/bytes/None
- `send(data)` - Send message (str → text frame, bytes → binary frame)
- `ping(data=b'')` - Send ping frame
- `close(code=1000, reason='')` - Close connection
- `is_closed` - True if connection is closed
- `pause_reading(timeout=None)` / `resume_reading()` - Inbound backpressure. `timeout` is stored but not enforced by a standalone WebSocket (it is application-driven — apply the deadline in your own loop)

### Large Message Chunking

Messages larger than `MAX_WS_MESSAGE_LENGTH` (default 64KB, configurable via `max_ws_message_length` kwarg) are delivered in chunks via `EVENT_WS_CHUNK_FIRST`, `EVENT_WS_CHUNK_NEXT`, `EVENT_WS_CHUNK_LAST` events. Read chunk data with `read_buffer()`, check frame type with `ws_is_text`.

In non-event mode, messages exceeding the limit close the connection with status 1009.


## Backpressure (pausing reads)

When the application cannot keep up with an inbound WebSocket stream or a
request-body upload, it can stop reading the socket. Its kernel buffer then
fills and TCP stalls the peer — no application-level protocol needed. Outbound
sending is unaffected, so a paused connection can still send.

`pause_reading(timeout=None)` drops the socket's read interest; `resume_reading()`
re-arms it. A paused connection that never drains is closed by `maintenance()`
as a stuck-consumer guard: `timeout=None` uses the keep-alive timeout, a value
`<= 0` disables the deadline (you own the lifecycle). A slow-but-alive consumer
that resumes and drains periodically stays alive, because each read refreshes
the activity clock — this is the pattern for forwarding to a slow sink (a
low-baud serial port, a slow disk) without dropping the connection.

```python
# Event-mode server: throttle a fast WebSocket producer to a slow sink.
client = server.wait(1.0)
if client and client.event == EVENT_WS_MESSAGE:
    queue.append(client.read_buffer())
    if len(queue) > HIGH_WATER:
        client.pause_reading()          # stop reading; TCP stalls the peer
    ...
# elsewhere, once the sink has caught up:
if client.is_websocket and len(queue) < LOW_WATER:
    client.resume_reading()             # start reading again
```

The same applies to uploads: after `accept_body()` with `streaming=True`, call
`pause_reading()` while your `EVENT_DATA` handler is behind and `resume_reading()`
when it catches up. A standalone (non-event) `WebSocket` exposes the same two
methods, but stores `timeout` without enforcing it — it is application-driven,
so apply the deadline in your own loop.


## Event Mode

Event mode enables streaming large uploads without buffering entire body in memory.

```python
from uhttp.server import (
    HttpServer, EVENT_REQUEST, EVENT_HEADERS,
    EVENT_DATA, EVENT_COMPLETE, EVENT_ERROR
)

server = HttpServer(port=8080, event_mode=True)

while True:
    client = server.wait()
    if not client:
        continue

    if client.event == EVENT_REQUEST:
        # Small request or GET - handle normally
        client.respond({'status': 'ok'})

    elif client.event == EVENT_HEADERS:
        # Large upload starting - decide how to handle
        client.context = {'total': 0}
        client.accept_body()
        # Read any data that arrived with headers
        data = client.read_buffer()
        if data:
            client.context['total'] += len(data)

    elif client.event == EVENT_DATA:
        # More data arrived
        data = client.read_buffer()
        if data:
            client.context['total'] += len(data)

    elif client.event == EVENT_COMPLETE:
        # Upload finished
        data = client.read_buffer()
        if data:
            client.context['total'] += len(data)
        client.respond({'received': client.context['total']})

    elif client.event == EVENT_ERROR:
        print(f"Error: {client.error}")
```

For file uploads, use `to_file` parameter:

```python
elif client.event == EVENT_HEADERS:
    client.accept_body(to_file=f"/uploads/{uuid}.bin")

elif client.event == EVENT_COMPLETE:
    client.respond({'status': 'uploaded'})
```

**Note:** Small POST requests where headers and body arrive in the same TCP packet will receive `EVENT_REQUEST` (not `EVENT_HEADERS`), since the complete request is already available.


## IPv6 Support

Server supports both IPv4 and IPv6:

```python
import uhttp.server

# IPv4 only (default)
server = uhttp.server.HttpServer(address='0.0.0.0', port=80)

# Dual-stack (IPv4 + IPv6)
server = uhttp.server.HttpServer(address='::', port=80)

# IPv6 only
server = uhttp.server.HttpServer(address='::1', port=80)
```


## Behind a reverse proxy

By default `X-Forwarded-For` is ignored — the header is client-supplied, so
trusting it without a proxy in front is an open invitation to spoof any IP.
Pass `trusted_proxies` to honour it:

```python
server = uhttp.server.HttpServer(port=8080, trusted_proxies=['127.0.0.1'])
```

The client address is resolved by walking the chain **from the socket peer
leftwards** and taking the first hop that is not a trusted proxy — the same
rule as nginx's `realip` module with `set_real_ip_from` and
`real_ip_recursive on`:

```
X-Forwarded-For: 1.2.3.4, 203.0.113.50, 10.0.0.1     socket peer: 127.0.0.1
                 ^spoofed  ^client        ^proxy hop  ^proxy hop
trusted_proxies=['127.0.0.1', '10.0.0.1'] -> remote_address == '203.0.113.50'
```

A client can prepend anything to the header, but it cannot forge the hops a
proxy appended after it — so going right-to-left is what makes this
spoof-proof. Taking the leftmost entry instead would be safe only with
`proxy_set_header X-Forwarded-For $remote_addr;` (which overwrites the header)
and wide open with the far more common `$proxy_add_x_forwarded_for` (which
appends to it).

**List every proxy in the chain**, not just the one the server talks to. An
intermediate hop missing from `trusted_proxies` is where the walk stops, and
that proxy's own IP becomes `remote_address`. If all hops are trusted, the
nearest one is returned — `remote_address` is never empty.

`remote_addresses` gives the whole chain including the entries left of the
client, which is useful in a log but must not decide anything: only
`remote_address` and what follows it in the list is trustworthy. It holds the
header only — the socket peer stays in `socket_address`, the way Werkzeug
keeps `access_route` separate from `remote_addr` and nginx keeps
`$realip_remote_addr` separate from `$remote_addr`.


## Development

### Running tests

```bash
../.venv/bin/pip install -e .
../.venv/bin/python -m unittest discover -v tests/
```

### MicroPython integration tests

Tests run HTTP server on real ESP32 hardware, with test client on PC.
Requires [mpytool](https://github.com/cortexm/mpytool) and `uhttp-client`.

**Configuration:**

1. WiFi credentials in `~/.config/uhttp/wifi.json`:
   ```json
   {"ssid": "MyWiFi", "password": "secret"}
   ```

2. Serial port via environment variable or mpytool config:
   ```bash
   # Environment variable
   export MPY_TEST_PORT=/dev/ttyUSB0

   # Or mpytool config
   echo "/dev/ttyUSB0" > ~/.config/mpytool/ESP32
   ```

**Run tests:**

```bash
# Install dependencies
../.venv/bin/pip install uhttp-client mpytool
```

uhttp-client - for other integration tests
mpytool - for mpy_integration tests

```bash
# Run tests
MPY_TEST_PORT=/dev/ttyUSB0 ../.venv/bin/python -m unittest tests.test_mpy_integration -v
```

The tests upload `server.py` to ESP32, start HTTP server, and send requests from PC.

### CI

Tests run automatically via GitHub Actions:
- Unit tests (push + PR): Ubuntu + Windows in parallel, Python 3.10 + 3.14
- MicroPython tests (push only): Self-hosted runner with ESP32, runs after unit tests pass


## Expect: 100-continue

Server supports `Expect: 100-continue` header for large uploads:

**Non-event mode:** Server automatically sends `100 Continue` response when client sends `Expect: 100-continue` header, then waits for body.

**Event mode:** Server sends `100 Continue` only when application calls `accept_body()`. This allows rejecting uploads early (e.g., respond with 413 or 401) without accepting body data.

```python
from uhttp.server import HttpServer, EVENT_HEADERS

server = HttpServer(port=8080, event_mode=True)

while True:
    client = server.wait()
    if client and client.event == EVENT_HEADERS:
        if client.content_length > MAX_ALLOWED:
            # No 100 Continue sent - client won't upload
            client.respond({'error': 'too large'}, status=413)
        else:
            client.accept_body()  # Sends 100 Continue, starts receiving
```
