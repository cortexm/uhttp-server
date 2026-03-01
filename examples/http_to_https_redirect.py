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
import select
from uhttp import server as uhttp_server


def main():
    # Create SSL context for HTTPS server
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')

    # HTTP server - redirects to HTTPS
    http_server = uhttp_server.HttpServer(
        address='0.0.0.0',
        port=8080  # Use 80 for production (requires root)
    )

    # HTTPS server - serves actual content
    https_server = uhttp_server.HttpServer(
        address='0.0.0.0',
        port=8443,  # Use 443 for production (requires root)
        ssl_context=context,
        keep_alive_timeout=30,
        keep_alive_max_requests=100
    )

    print("HTTP server listening on http://0.0.0.0:8080 (redirects to HTTPS)")
    print("HTTPS server listening on https://0.0.0.0:8443")
    print("Press Ctrl+C to stop")

    try:
        while True:
            # Combine read/write sockets from both servers
            read_sockets = http_server.read_sockets + https_server.read_sockets
            write_sockets = http_server.write_sockets + https_server.write_sockets

            # Wait for socket events
            read, write, _ = select.select(read_sockets, write_sockets, [], 1.0)

            # Handle HTTP requests - redirect to HTTPS
            http_client = http_server.process_events(read, write)
            if http_client:
                host = http_client.host.replace(':8080', ':8443')
                https_url = f"https://{host}{http_client.url}"
                print(f"HTTP: {http_client.method} {http_client.path} → {https_url}")
                http_client.respond_redirect(https_url)

            # Handle HTTPS requests - serve content
            https_client = https_server.process_events(read, write)
            if https_client:
                print(f"HTTPS: {https_client.method} {https_client.path}")
                https_client.respond({
                    'message': 'Hello from HTTPS!',
                    'secure': https_client.is_secure,
                    'path': https_client.path,
                    'method': https_client.method
                })

    except KeyboardInterrupt:
        print("\nShutting down...")
        http_server.close()
        https_server.close()


if __name__ == '__main__':
    main()
