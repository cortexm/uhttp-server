"""Example: Combining HttpServer and HttpClient

This example shows how to use both server and client together
in a single select loop - useful for proxies, API gateways, etc.
"""

import select
from uhttp.server import HttpServer
from uhttp.client import HttpClient


def example_simple_proxy():
    """Simple HTTP proxy - forwards requests to backend"""
    print("=== Simple Proxy (forwards to httpbin.org) ===")
    print("Start server on port 8080, then test with:")
    print("  curl http://localhost:8080/get")
    print("  curl http://localhost:8080/post -d '{\"test\":1}'")
    print()

    # Local server
    server = HttpServer(port=8080)

    # Backend client
    backend = HttpClient('httpbin.org', port=80)

    # Track pending requests: {client_connection: backend_request_started}
    pending = {}

    print("Proxy running... (Ctrl+C to stop)")

    try:
        while True:
            # Collect all sockets
            read_socks = server.read_sockets + backend.read_sockets
            write_socks = server.write_sockets + backend.write_sockets

            r, w, _ = select.select(read_socks, write_socks, [], 1.0)

            # Process server events (incoming requests)
            incoming = server.process_events(r, w)
            if incoming:
                print(f"-> Incoming: {incoming.method} {incoming.path}")

                # Forward to backend (async is default)
                backend.request(
                    incoming.method,
                    incoming.path,
                    query=incoming.query,
                    json=incoming.data if incoming.content_type == 'application/json' else None,
                    data=incoming.data if incoming.content_type != 'application/json' else None
                )
                pending[id(backend)] = incoming

            # Process backend events (responses from backend)
            backend_response = backend.process_events(r, w)
            if backend_response and pending:
                # Get the original client request
                client_conn = pending.pop(id(backend), None)
                if client_conn:
                    print(f"<- Backend response: {backend_response.status}")

                    # Forward response to client
                    client_conn.respond(
                        data=backend_response.data,
                        status=backend_response.status,
                        headers={'content-type': backend_response.content_type}
                    )

    except KeyboardInterrupt:
        print("\nStopping proxy...")

    server.close()
    backend.close()


def example_api_aggregator():
    """Aggregate data from multiple APIs in parallel"""
    print("=== API Aggregator ===")

    # Multiple backend clients (all to httpbin for demo)
    backends = {
        'api1': HttpClient('httpbin.org', port=80),
        'api2': HttpClient('httpbin.org', port=80),
        'api3': HttpClient('httpbin.org', port=80),
    }

    # Start parallel requests (async is default)
    backends['api1'].get('/get', query={'source': 'api1'})
    backends['api2'].get('/get', query={'source': 'api2'})
    backends['api3'].get('/get', query={'source': 'api3'})

    results = {}

    # Wait for all responses
    while len(results) < len(backends):
        read_socks = []
        write_socks = []
        for client in backends.values():
            read_socks.extend(client.read_sockets)
            write_socks.extend(client.write_sockets)

        r, w, _ = select.select(read_socks, write_socks, [], 10.0)

        for name, client in backends.items():
            if name not in results:
                resp = client.process_events(r, w)
                if resp:
                    results[name] = resp.json()
                    print(f"Got {name} data: {results[name]['args']}")

    # Cleanup
    for client in backends.values():
        client.close()

    print(f"\nAll {len(results)} API calls completed in parallel")


if __name__ == '__main__':
    # Run aggregator example (doesn't need server)
    example_api_aggregator()

    # Uncomment to run proxy (needs port 8080 available):
    # example_simple_proxy()
