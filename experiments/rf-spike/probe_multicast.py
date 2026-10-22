"""Passive multicast check from WSL: joins 238.0.0.18:32101 and listens for the
Motion/Bloc hub's Heartbeat and Report pushes. Also sends one GetDeviceList to
the multicast group (read-only; the hub answers on multicast). Moves nothing.

Usage: .venv/bin/python probe_multicast.py [seconds]
"""
import json
import socket
import struct
import sys
import time

GROUP, PORT_SEND, PORT_RECV = "238.0.0.18", 32100, 32101
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 75

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", PORT_RECV))
s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("=4sl", socket.inet_aton(GROUP), socket.INADDR_ANY))
s.settimeout(1)
s.sendto(json.dumps({"msgType": "GetDeviceList", "msgID": time.strftime("%Y%m%d%H%M%S000")}).encode(), (GROUP, PORT_SEND))
t_end, n = time.monotonic() + secs, 0
while time.monotonic() < t_end:
    try:
        data, addr = s.recvfrom(16384)
    except socket.timeout:
        continue
    n += 1
    try:
        print(f"{time.strftime('%H:%M:%S')} {addr[0]} {json.loads(data).get('msgType')}")
    except ValueError:
        print(f"{time.strftime('%H:%M:%S')} {addr[0]} non-JSON {len(data)} bytes")
print(f"multicast packets received in {secs:.0f} s: {n}")
