"""Example: Combining HttpServer and HttpClient

Both register their sockets in one selectors.BaseSelector, so a single
loop drives them together - useful for proxies, API gateways, etc.
On a ready key, key.data.handle_event() dispatches to the owning
server / connection / client.
"""

import selectors
from uhttp.server import HttpServer, HttpConnection
from uhttp.client import HttpClient


def example_simple_proxy():
    """Simple HTTP proxy - forwards requests to backend"""
    print("=== Simple Proxy (forwards to httpbin.org) ===")
    print("Start server on port 8080, then test with:")
    print("  curl http://localhost:8080/get")
    print("  curl http://localhost:8080/post -d '{\"test\":1}'")
    print()

    # One selector drives the server and the backend client
    selector = selectors.DefaultSelector()
    server = HttpServer(port=8080, selector=selector)
    backend = HttpClient('httpbin.org', port=80, selector=selector)

    # A client handles one request at a time, so overlapping incoming
    # requests have to queue: forwarding straight from the event would
    # raise "Request already in progress" and kill the loop.
    waiting = []      # connections not forwarded yet
    forwarded = None  # the connection the backend is answering

    def forward_next():
        if forwarded is not None or not waiting:
            return None
        client = waiting.pop(0)
        print(f"-> Forwarding: {client.method} {client.path}")
        is_json = client.content_type == 'application/json'
        backend.request(
            client.method, client.path, query=client.query,
            json=client.data if is_json else None,
            data=None if is_json else client.data)
        return client

    print("Proxy running... (Ctrl+C to stop)")

    try:
        while True:
            for key, mask in selector.select(1.0):
                ready = key.data.handle_event(key.fileobj, mask)
                if ready is None:
                    continue

                if isinstance(ready, HttpConnection):
                    waiting.append(ready)

                elif ready is backend and forwarded is not None:
                    response = backend.response
                    print(f"<- Backend response: {response.status}")
                    forwarded.respond(
                        data=response.data,
                        status=response.status,
                        headers={'content-type': response.content_type})
                    forwarded = None

            if forwarded is None:
                forwarded = forward_next()

            server.maintenance()
            backend.maintenance()   # a hung backend has no event to time out

    except KeyboardInterrupt:
        print("\nStopping proxy...")

    backend.close()
    server.close()
    selector.close()


def example_api_aggregator():
    """Aggregate data from multiple APIs in parallel"""
    print("=== API Aggregator ===")

    # All backends share one selector, so one loop collects every response
    selector = selectors.DefaultSelector()
    backends = {
        name: HttpClient('httpbin.org', port=80, selector=selector)
        for name in ('api1', 'api2', 'api3')
    }

    for name, client in backends.items():
        client.get('/get', query={'source': name})

    results = {}
    while len(results) < len(backends):
        for key, mask in selector.select(10.0):
            ready = key.data.handle_event(key.fileobj, mask)
            if ready is None:
                continue
            for name, client in backends.items():
                if ready is client:
                    results[name] = client.response.json()
                    print(f"Got {name} data: {results[name]['args']}")
                    break
        for client in backends.values():
            client.maintenance()

    for client in backends.values():
        client.close()
    selector.close()

    print(f"\nAll {len(results)} API calls completed in parallel")


if __name__ == '__main__':
    # Run aggregator example (doesn't need server)
    example_api_aggregator()

    # Uncomment to run proxy (needs port 8080 available):
    # example_simple_proxy()
