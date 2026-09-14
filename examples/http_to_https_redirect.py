#!/usr/bin/env python3
"""
HTTP to HTTPS redirect server example

This example shows how to run two servers:
- HTTP server on port 80/8080 that redirects all requests to HTTPS
- HTTPS server on port 443/8443 that serves actual content

Requirements:
1. Generate SSL certificate and key:
   openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes

2. Run the servers:
   python examples/http_to_https_redirect.py

3. Test with:
   curl -L http://localhost:8080/test
   curl -k https://localhost:8443/test
"""

import ssl
import selectors
from uhttp import server as uhttp_server


def main():
    # Create SSL context for HTTPS server
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')

    # One shared selector drives both servers. On the key's readiness,
    # key.data.handle_event() dispatches to whichever server or connection
    # owns the socket.
    selector = selectors.DefaultSelector()

    # HTTP server - redirects to HTTPS
    http_server = uhttp_server.HttpServer(
        address='0.0.0.0',
        port=8080,  # Use 80 for production (requires root)
        selector=selector,
    )

    # HTTPS server - serves actual content
    https_server = uhttp_server.HttpServer(
        address='0.0.0.0',
        port=8443,  # Use 443 for production (requires root)
        ssl_context=context,
        keep_alive_timeout=30,
        keep_alive_max_requests=100,
        selector=selector,
    )

    print("HTTP server listening on http://0.0.0.0:8080 (redirects to HTTPS)")
    print("HTTPS server listening on https://0.0.0.0:8443")
    print("Press Ctrl+C to stop")

    def serve(client):
        # Tell the two servers apart by their TLS state.
        if client.is_secure:
            print(f"HTTPS: {client.method} {client.path}")
            client.respond({
                'message': 'Hello from HTTPS!',
                'secure': client.is_secure,
                'path': client.path,
                'method': client.method
            })
        else:
            host = client.host.replace(':8080', ':8443')
            https_url = f"https://{host}{client.url}"
            print(f"HTTP: {client.method} {client.path} → {https_url}")
            client.respond_redirect(https_url)

    try:
        while True:
            for key, mask in selector.select(1.0):
                client = key.data.handle_event(key.fileobj, mask)
                if isinstance(client, uhttp_server.HttpConnection):
                    serve(client)
                    while client.next():  # drain events buffered from one recv
                        serve(client)
            # Enforce idle/keep-alive timeouts once per loop iteration.
            http_server.maintenance()
            https_server.maintenance()

    except KeyboardInterrupt:
        print("\nShutting down...")
        http_server.close()
        https_server.close()
        selector.close()


if __name__ == '__main__':
    main()
