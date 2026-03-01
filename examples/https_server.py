#!/usr/bin/env python3
"""
Simple HTTPS server example

Requirements:
1. Generate SSL certificate and key:
   openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes

2. Run the server:
   python examples/https_server.py

3. Test with:
   curl -k https://localhost:8443/
"""

import ssl
from uhttp import server as uhttp_server


def main():
    # Create SSL context
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')

    # Create HTTPS server
    server = uhttp_server.HttpServer(
        address='0.0.0.0',
        port=8443,
        ssl_context=context,
        keep_alive_timeout=30,
        keep_alive_max_requests=100
    )

    print("HTTPS server listening on https://0.0.0.0:8443")
    print("Press Ctrl+C to stop")

    try:
        while True:
            client = server.wait(timeout=1.0)
            if client:
                print(f"{client.method} {client.path} (secure={client.is_secure})")

                # Serve response
                client.respond({
                    'message': 'Hello from HTTPS!',
                    'secure': client.is_secure,
                    'path': client.path,
                    'method': client.method
                })

    except KeyboardInterrupt:
        print("\nShutting down...")
        server.close()


if __name__ == '__main__':
    main()
