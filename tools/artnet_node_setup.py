"""Find and set up an Art-Net node over the network, with no vendor tool or front-panel menu (owner, 26 Nov: "can you
really not do this yourself??"). Run it as Windows Python on the machine whose Ethernet the node is on, while nothing
else holds udp 6454 there (stop the virtual node first: tools/virtual_node/run-windows.sh stop).

  py -3.12 artnet_node_setup.py find  --from 192.168.50.1                 ArtPoll; list every node that replies
  py -3.12 artnet_node_setup.py ip    --from 10.0.0.250 --node 10.201.6.100 --set 192.168.50.10/24   ArtIpProg
  py -3.12 artnet_node_setup.py universe --from 192.168.50.1 --node 192.168.50.10 --port-address 1   ArtAddress

--from is this machine's address on the node's network (to reach a node still on its factory 2.x or 10.x address, give
the adapter a temporary address in that range first: elevated New-NetIPAddress, then remove it). The laptop's Pknight
CR011R came at 10.201.6.100/8, universe 0; docs/GIGBAR_RIG.md §2.3 has what it was set to.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import struct
import time

PORT = 6454
HEAD = b"Art-Net\x00"
VER = struct.pack(">H", 14)
OP_POLL, OP_REPLY, OP_ADDRESS, OP_IPPROG, OP_IPPROG_REPLY = 0x2000, 0x2100, 0x6000, 0xF800, 0xF900


def _op(d: bytes) -> int | None:
    return struct.unpack("<H", d[8:10])[0] if len(d) >= 10 and d[:8] == HEAD else None


def _socks(src: str) -> tuple[socket.socket, socket.socket]:
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    rx.bind(("0.0.0.0", PORT))
    rx.settimeout(0.3)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    tx.bind((src, 0))
    return rx, tx


def describe(d: bytes, ip: str) -> str:
    net, sub, swout, gout = d[18], d[19], d[190], d[182]
    short = d[26:44].split(b"\x00")[0].decode("latin1", "replace")
    report = d[108:172].split(b"\x00")[0].decode("latin1", "replace")
    return (f"{ip}: {short!r}, says ip {'.'.join(map(str, d[10:14]))}, port-address out "
            f"{(net << 8) | (sub << 4) | (swout & 0x0F)}, {'outputting' if gout & 0x80 else 'not outputting'}, {report!r}")


def poll(rx: socket.socket, tx: socket.socket, dst: str, seconds: float = 4.0, only: str | None = None) -> dict:
    pkt = HEAD + struct.pack("<H", OP_POLL) + VER + b"\x06\x00"
    seen: dict[str, bytes] = {}
    end = time.time() + seconds
    while time.time() < end:
        tx.sendto(pkt, (dst, PORT))
        stop = time.time() + 0.8
        while time.time() < stop:
            try:
                d, (ip, _) = rx.recvfrom(2048)
            except socket.timeout:
                continue
            if _op(d) == OP_REPLY and (only is None or ip == only):
                seen[ip] = d
    return seen


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("find", "ip", "universe"))
    ap.add_argument("--from", dest="src", required=True, help="this machine's address on the node's network")
    ap.add_argument("--node", help="the node's current address (ip, universe)")
    ap.add_argument("--set", help="ip: the new address/prefix, e.g. 192.168.50.10/24")
    ap.add_argument("--port-address", type=int, help="universe: the output's Port-Address (0-15 here: net/subnet 0)")
    a = ap.parse_args()
    rx, tx = _socks(a.src)
    if a.what == "find":
        for ip, d in poll(rx, tx, "255.255.255.255").items():
            print(describe(d, ip))
        return
    if not a.node:
        raise SystemExit("--node is needed")
    if a.what == "ip":
        net = ipaddress.ip_interface(a.set or "")
        cmd = 0x80 | 0x04 | 0x02                                     # program: ip and mask
        pkt = (HEAD + struct.pack("<H", OP_IPPROG) + VER + b"\x00\x00" + bytes([cmd, 0]) + net.ip.packed
               + net.network.netmask.packed + b"\x00\x00" + b"\x00" * 8)
        tx.sendto(pkt, (a.node, PORT))
        print(f"sent ArtIpProg: {a.node} -> {net} (the node may move at once and not reply); now poll it from an "
              f"address on {net.network}")
        return
    if a.port_address is None or not 0 <= a.port_address <= 15:
        raise SystemExit("--port-address 0-15")
    pkt = (HEAD + struct.pack("<H", OP_ADDRESS) + VER + bytes([0x7F, 1]) + b"\x00" * 18 + b"\x00" * 64
           + bytes([0x7F] * 4) + bytes([0x80 | a.port_address, 0x7F, 0x7F, 0x7F]) + bytes([0x7F, 0, 0x00]))
    tx.sendto(pkt, (a.node, PORT))
    time.sleep(1.5)
    for ip, d in poll(rx, tx, a.node, only=a.node).items():
        print(describe(d, ip))


if __name__ == "__main__":
    main()
