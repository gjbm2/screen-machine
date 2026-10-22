#!/usr/bin/env python3
# Minimal single-stream TCP throughput tool (no dependencies), used for the kiosk Wi-Fi measurements in
# docs/KIOSK_INFRASTRUCTURE_NOTES.md section 4. Copy it to the mini-PC with scp; it needs only python3.
# tput.py recv PORT            : accept connections, receive, print Mbit/s per connection (runs until killed)
# tput.py push HOST PORT MB    : connect and send MB megabytes
# tput.py serve PORT MB COUNT  : accept COUNT connections, send MB to each
# tput.py get HOST PORT        : connect and receive until close
import socket, sys, time
m = sys.argv[1]
if m == "recv":
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", int(sys.argv[2]))); s.listen(4)
    while True:
        c, a = s.accept(); t0 = time.time(); n = 0
        while True:
            b = c.recv(1 << 20)
            if not b: break
            n += len(b)
        dt = time.time() - t0; print(f"recv {n/1e6:.1f} MB in {dt:.2f}s = {n*8/dt/1e6:.1f} Mbit/s", flush=True); c.close()
elif m == "push":
    s = socket.create_connection((sys.argv[2], int(sys.argv[3]))); n = int(float(sys.argv[4]) * 1e6); buf = b"\x5a" * 65536; t0 = time.time()
    while n > 0:
        k = s.send(buf[:min(n, len(buf))]); n -= k
    s.shutdown(socket.SHUT_WR); s.recv(1); s.close()
elif m == "serve":
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", int(sys.argv[2]))); s.listen(4)
    buf = b"\x5a" * 65536
    for _ in range(int(sys.argv[4])):
        c, _ = s.accept(); n = int(float(sys.argv[3]) * 1e6)
        try:
            while n > 0:
                n -= c.send(buf[:min(n, len(buf))])
        except OSError:
            pass
        c.close()
else:
    t0 = time.time(); s = socket.create_connection((sys.argv[2], int(sys.argv[3]))); n = 0
    while True:
        b = s.recv(1 << 20)
        if not b: break
        n += len(b)
    dt = time.time() - t0; print(f"get {n/1e6:.1f} MB in {dt:.2f}s = {n*8/dt/1e6:.1f} Mbit/s")
