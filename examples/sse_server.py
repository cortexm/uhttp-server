#!/usr/bin/env python3
"""
Server-Sent Events (SSE) example

Serves a simple HTML page with live sensor data updates via SSE.

Run the server:
    python examples/sse_server.py

Open in browser:
    http://localhost:8080/

SSE stream endpoint:
    http://localhost:8080/events
    curl http://localhost:8080/events
"""

import time
import math
from uhttp import server as uhttp_server

HTML_PAGE = """\
<!DOCTYPE html>
<html>
<head><title>SSE Demo</title></head>
<body>
<h1>SSE Demo - Live Sensor Data</h1>
<div id="data">Connecting...</div>
<div id="status"></div>
<script>
const es = new EventSource('/events');
const dataEl = document.getElementById('data');
const statusEl = document.getElementById('status');

es.addEventListener('sensor', (e) => {
    const d = JSON.parse(e.data);
    dataEl.innerHTML = `
        <p>Temperature: ${d.temp.toFixed(1)} C</p>
        <p>Humidity: ${d.humidity.toFixed(1)} %</p>
        <p>Event ID: ${e.lastEventId}</p>
        <p>Time: ${new Date().toLocaleTimeString()}</p>`;
});

es.onopen = () => { statusEl.textContent = 'Connected'; };
es.onerror = () => { statusEl.textContent = 'Reconnecting...'; };
</script>
</body>
</html>"""


def main():
    server = uhttp_server.HttpServer(port=8080)
    sse_clients = []
    event_counter = 0
    last_send = 0
    last_ping = 0

    print("SSE server listening on http://localhost:8080")
    print("Press Ctrl+C to stop")

    try:
        while True:
            client = server.wait(timeout=0.1)

            if client:
                if client.path == '/':
                    client.respond(HTML_PAGE)
                elif client.path == '/events':
                    if client.response_stream():
                        # Set retry to 3 seconds
                        client.send_event(retry=3000)
                        sse_clients.append(client)
                        print(f"SSE client connected ({len(sse_clients)} total)")
                else:
                    client.respond("Not found", status=404)

            now = time.time()

            # Send sensor data every second
            if sse_clients and now - last_send >= 1.0:
                last_send = now
                event_counter += 1

                # Simulate sensor readings
                data = {
                    'temp': 22.0 + 3.0 * math.sin(now / 10),
                    'humidity': 55.0 + 10.0 * math.cos(now / 15),
                }

                for sc in list(sse_clients):
                    if not sc.send_event(
                            data, event='sensor',
                            event_id=event_counter):
                        sse_clients.remove(sc)
                        print(f"SSE client disconnected ({len(sse_clients)} total)")

            # Send keep-alive comment every 15 seconds
            elif sse_clients and now - last_send >= 15.0:
                last_ping = now
                for sc in list(sse_clients):
                    if not sc.send_chunk(':\n\n'):
                        sse_clients.remove(sc)

    except KeyboardInterrupt:
        print("\nShutting down...")
        for sc in sse_clients:
            sc.response_stream_end()
        server.close()


if __name__ == '__main__':
    main()
