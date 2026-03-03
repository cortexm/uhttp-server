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
import select
import uhttp.server

# SSL context for HTTPS
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(
    certfile='/etc/letsencrypt/live/example.com/fullchain.pem',
    keyfile='/etc/letsencrypt/live/example.com/privkey.pem'
)

# HTTP server (redirects)
http_server = uhttp.server.HttpServer(port=80)

# HTTPS server (serves content)
https_server = uhttp.server.HttpServer(port=443, ssl_context=context)

while True:
    r, w, _ = select.select(
        http_server.read_sockets + https_server.read_sockets,
        http_server.write_sockets + https_server.write_sockets,
        [], 1.0
    )

    # Redirect HTTP to HTTPS
    http_client = http_server.process_events(r, w)
    if http_client:
        https_url = f"https://{http_client.host}{http_client.url}"
        http_client.respond_redirect(https_url)

    # Serve HTTPS content
    https_client = https_server.process_events(r, w)
    if https_client:
        https_client.respond({'message': 'Secure content'})
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
  - `max_waiting_clients` - Maximum concurrent connections (default: 5)
  - `keep_alive_timeout` - Keep-alive timeout in seconds (default: 30)
  - `keep_alive_max_requests` - Max requests per connection (default: 100)
  - `max_headers_length` - Maximum header size in bytes (default: 4KB)
  - `max_content_length` - Maximum body size in bytes (default: 512KB, only enforced when event_mode=False)

#### Properties:

**`socket(self)`**

- Server socket

**`read_sockets(self)`**

- All sockets waiting for read, used for select

**`write_sockets(self)`**

- All sockets with data to send, used for select

**`is_secure(self)`**

- Returns `True` if server uses SSL/TLS, `False` otherwise

**`event_mode(self)`**

- Returns `True` if event mode is enabled

#### Methods:

**`event_write(self, sockets)`**

- Send buffered data for sockets in list. Called internally by `process_events()`.

**`event_read(self, sockets)`**

- Process sockets with read event, returns None or instance of HttpConnection with established connection.

**`process_events(self, read_sockets, write_sockets)`**

- Process select results, returns None or instance of HttpConnection with established connection.

**`wait(self, timeout=1)`**

- Wait for new clients with specified timeout, returns None or instance of HttpConnection with established connection.


### Class `HttpConnection`:

**`HttpConnection(server, sock, addr, **kwargs)`**

#### Properties:

**`addr(self)`**

- Client address

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

#### Methods:

**`headers_get(self, key, default=None)`**

- Return value from headers by key, or default if key not found

**`process_request(self)`**

- Process HTTP request when read event on client socket

**`respond(self, data=None, status=200, headers=None, cookies=None)`**

- Create general response with data, status and headers as dict

**`respond_redirect(self, url, status=302, cookies=None)`**

- Create redirect response to URL

**`respond_file(self, file_name, headers=None)`**

- Respond with file content, streaming asynchronously to minimize memory usage

**`response_multipart(self, headers=None)`**

- Create multipart response with headers as dict (for MJPEG streams etc.)

**`response_multipart_frame(self, data, headers=None, boundary=None)`**

- Send multipart frame with data and headers

**`response_multipart_end(self, boundary=None)`**

- Finish multipart stream

**`accept_body(self, streaming=False, to_file=None)`** (event mode only)

- Accept body after `EVENT_HEADERS`. Call this to start receiving body data.
- `streaming=False` (default) - Buffer all data, receive only `EVENT_COMPLETE`
- `streaming=True` - Receive `EVENT_DATA` for each chunk, read with `read_buffer()`
- `to_file="/path"` - Save body directly to file
- Returns: Number of bytes already waiting in buffer

**`read_buffer(self)`** (event mode only)

- Read available data from buffer
- Returns: bytes or None if no data available


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
../.venv/bin/pip install -e ../mpytool uhttp-client

# Run tests
MPY_TEST_PORT=/dev/ttyUSB0 ../.venv/bin/python -m unittest tests.test_mpy_integration -v
```

The tests upload `server.py` to ESP32, start HTTP server, and send requests from PC.

### CI

Tests run automatically on push/PR via GitHub Actions:
- Unit tests: Ubuntu + Windows, Python 3.10 + 3.14
- MicroPython tests: Self-hosted runner with ESP32


## TODO

- Cookie attributes support (Path, Domain, Secure, HttpOnly, SameSite, Expires)
- Expect: 100-continue support - currently causes deadlock (client waits for 100, server waits for body)
- Streaming API for sending large responses (handle EAGAIN)
- Chunked transfer encoding support (receiving and sending)
