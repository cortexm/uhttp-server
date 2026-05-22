#!/usr/bin/env python3
"""WS-on-MicroPython diagnostic — sequential, no threads.

Usage:
    MPY_TEST_PORT=/dev/cu.usbmodem* python tools/diag_mpy_ws.py

What it does:
1. Cold-mounts uhttp on ESP32, connects WiFi.
2. Boots a verbose echo server (try/except + sys.print_exception +
   gc.mem_free) using fire-and-forget exec.
3. PC opens a WS connection, sends a binary frame, waits for echo —
   short timeout (3 s) so we don't block forever.
4. Sends Ctrl-C to the device REPL, then DRAINS serial buffer so we
   can see any traceback the server printed.
"""
import json
import os
import socket
import sys
import time
from pathlib import Path

PORT = os.environ.get('MPY_TEST_PORT', '/dev/cu.usbmodem101')
ESP32_SERVER_PORT = 8081
wifi_cfg = json.loads(
    (Path.home() / '.config/uhttp/wifi.json').read_text())
WIFI_SSID = wifi_cfg['ssid']
WIFI_PASSWORD = wifi_cfg.get('password', '')

import mpytool
from mpytool.mpy_cross import MpyCross


def log(msg):
    print(f"[diag] {msg}", flush=True)


def drain_serial(conn, label, max_total_s=3.0):
    """Drain whatever the device emitted to the serial bus and print it.

    conn.read(timeout=0.1) is non-blocking-ish (returns None when no
    data). We loop until N consecutive empty reads or max_total_s.
    """
    deadline = time.time() + max_total_s
    buf = bytearray()
    empty_streak = 0
    while time.time() < deadline:
        try:
            chunk = conn.read(timeout=0.1)
        except Exception as e:
            log(f"drain {label}: read err {e!r}")
            break
        if chunk:
            buf.extend(chunk)
            empty_streak = 0
        else:
            empty_streak += 1
            if empty_streak >= 5:
                break
    if buf:
        text = bytes(buf).decode('utf-8', errors='replace')
        print(f"--- ESP32 stdout ({label}) ---")
        print(text)
        print(f"--- end ESP32 stdout ({label}) ---")
    else:
        log(f"drain {label}: (no output)")


# 1. Connect to device, soft reset
log(f"Connecting to ESP32 on {PORT}…")
conn = mpytool.ConnSerial(port=PORT, baudrate=115200)
mpy = mpytool.Mpy(conn)
mpy.stop()
try:
    conn.write(b'\x03\x03\x04')  # Ctrl-C Ctrl-C Ctrl-D
    time.sleep(2)
    conn.read_all()
except Exception:
    pass
mpy.stop()

# 2. Mount uhttp
server_dir = Path(__file__).parent.parent / 'uhttp'
mpy_cross = MpyCross()
mpy_cross.init(mpy.platform())
mount = mpy.mount(
    str(server_dir), mount_point='/lib/uhttp', mpy_cross=mpy_cross)

# 3. WiFi
wifi = f"""
import network, time
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
if not wlan.isconnected():
    wlan.connect({WIFI_SSID!r}, {WIFI_PASSWORD!r})
    for _ in range(30):
        if wlan.isconnected(): break
        time.sleep(0.5)
print('IP:', wlan.ifconfig()[0] if wlan.isconnected() else 'FAIL')
"""
out = mpy.comm.exec(wifi, timeout=20).decode('utf-8')
ip = None
for line in out.strip().split('\n'):
    if line.startswith('IP:'):
        ip = line.split(':', 1)[1].strip()
if not ip or ip == 'FAIL':
    log(f"WiFi failed: {out}")
    sys.exit(1)
log(f"ESP32 IP: {ip}")

# 4. Verbose server, fire-and-forget
server_code = f"""
import sys, gc
sys.path.insert(0, '/lib')
from uhttp.server import (
    HttpServer, EVENT_REQUEST, EVENT_WS_REQUEST, EVENT_WS_MESSAGE,
    EVENT_WS_CLOSE, EVENT_WS_PING)
gc.collect()
server = HttpServer(port={ESP32_SERVER_PORT}, event_mode=True)
print('READY mem=', gc.mem_free())
while True:
    ev = -1
    client = None
    try:
        client = server.wait(timeout=1)
    except Exception as e:
        print('ERR_WAIT:')
        sys.print_exception(e)
        continue
    if not client:
        continue
    try:
        ev = client.event
        if ev == EVENT_WS_REQUEST:
            print('WS_REQ path=', client.path, 'mem=', gc.mem_free())
            client.accept_websocket()
            print('WS_ACC_OK mem=', gc.mem_free())
        elif ev == EVENT_REQUEST:
            print('HTTP_REQ', client.path)
            if client.path == '/health':
                client.respond({{'status': 'ok'}})
            else:
                client.respond({{'path': client.path}})
        elif ev == EVENT_WS_MESSAGE:
            ml = len(client.ws_message) if client.ws_message else 0
            print('WS_MSG len=', ml, 'mem=', gc.mem_free())
            client.ws_send(client.ws_message)
            print('WS_SEND_OK mem=', gc.mem_free())
        elif ev == EVENT_WS_PING:
            print('WS_PING')
        elif ev == EVENT_WS_CLOSE:
            print('WS_CLOSE')
        else:
            print('EV?', ev)
    except Exception as e:
        print('ERR_EV', ev, ':')
        sys.print_exception(e)
        try:
            client.close()
        except Exception:
            pass
"""
mpy.comm.exec(server_code, timeout=0)
log("Server boot fired, waiting 2s for READY…")
time.sleep(2)

# 5. Wait for TCP listening
log("Health-checking server…")
ready = False
for _ in range(20):
    try:
        s = socket.socket()
        s.settimeout(2)
        s.connect((ip, ESP32_SERVER_PORT))
        s.sendall(b'GET /health HTTP/1.0\r\nHost: t\r\n\r\n')
        if b'200' in s.recv(1024):
            s.close()
            ready = True
            break
        s.close()
    except OSError:
        pass
    time.sleep(1)
if not ready:
    log("Server did not become ready")
    drain_serial(conn, 'no-ready')
    conn.write(b'\x03')
    sys.exit(1)
log("Server ready.")

# 6. Reproduce test_binary_echo
WS_OPCODE_BINARY = 0x2


def build_masked_frame(opcode, payload, mask=b'\x37\xfa\x21\x3d'):
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    frame = bytearray()
    frame.append(0x80 | opcode)
    length = len(payload)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.append((length >> 8) & 0xFF)
        frame.append(length & 0xFF)
    else:
        frame.append(0x80 | 127)
        for i in range(8):
            frame.append((length >> (56 - 8 * i)) & 0xFF)
    frame.extend(mask)
    masked = bytearray(payload)
    for i in range(len(masked)):
        masked[i] ^= mask[i % 4]
    frame.extend(masked)
    return bytes(frame)


def ws_upgrade(sock, host, path='/ws'):
    key = b'x3JJHMbDL1EzLkh9GBhXDw=='
    sock.sendall(
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key.decode()}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n".encode())
    sock.settimeout(5)
    response = b''
    while b'\r\n\r\n' not in response:
        chunk = sock.recv(1024)
        if not chunk:
            raise RuntimeError(f"upgrade EOF: {response!r}")
        response += chunk
    return response


def recv_frame(sock, timeout=3):
    sock.settimeout(timeout)
    header = b''
    while len(header) < 2:
        b = sock.recv(2 - len(header))
        if not b:
            raise RuntimeError("EOF during header")
        header += b
    fin = bool(header[0] & 0x80)
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        ext = b''
        while len(ext) < 2:
            ext += sock.recv(2 - len(ext))
        length = (ext[0] << 8) | ext[1]
    elif length == 127:
        ext = b''
        while len(ext) < 8:
            ext += sock.recv(8 - len(ext))
        length = int.from_bytes(ext, 'big')
    payload = b''
    while len(payload) < length:
        chunk = sock.recv(length - len(payload))
        if not chunk:
            raise RuntimeError("EOF during payload")
        payload += chunk
    return fin, opcode, payload


log("=== test_binary_echo repro ===")
sock = socket.socket()
sock.settimeout(10)
try:
    sock.connect((ip, ESP32_SERVER_PORT))
    log("PC: TCP connected")
    resp = ws_upgrade(sock, ip, '/ws')
    log(f"PC: upgrade OK ({resp[:30]!r}…)")
    payload = bytes(range(256))
    log(f"PC: sending binary frame, len={len(payload)}")
    sock.sendall(build_masked_frame(WS_OPCODE_BINARY, payload))
    log("PC: frame sent, waiting up to 3s for echo…")
    try:
        fin, op, echo = recv_frame(sock, timeout=3)
        log(f"PC: ECHO OK fin={fin} op={op:#x} len={len(echo)} "
            f"match={echo == payload}")
    except Exception as e:
        log(f"PC: NO ECHO — {type(e).__name__}: {e}")
finally:
    try:
        sock.close()
    except Exception:
        pass

# 7. Stop server, drain serial output
log("Sending Ctrl-C to ESP32 to stop server loop…")
conn.write(b'\x03')
time.sleep(1)
drain_serial(conn, 'after-test')
mpy.stop()
log("Done.")
