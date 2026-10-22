"""Read-only LAN probe from WSL: finds a Bloc/Motion hub and a BroadLink unit.

Sends no RF and moves nothing. Per host it sends:
  - a Motion-protocol GetDeviceList (UDP unicast to :32100, no key needed);
  - a BroadLink hello (UDP unicast to :80).
Plus one DNS query to the router as a control for UDP through the WSL NAT.

Usage: .venv/bin/python probe_lan.py [--subnet 192.168.1] [--only IP ...]
"""

import argparse
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor

import broadlink


def dns_control(router: str) -> str:
    # Minimal A query for example.com.
    q = bytes.fromhex("abcd01000001000000000000") + b"\x07example\x03com\x00\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    t0 = time.monotonic()
    try:
        s.sendto(q, (router, 53))
        data, _ = s.recvfrom(512)
        return f"ok ({len(data)} bytes, {1000 * (time.monotonic() - t0):.0f} ms)"
    except OSError as e:
        return f"FAILED ({e})"
    finally:
        s.close()


def motion_probe(ip: str):
    msg = {"msgType": "GetDeviceList", "msgID": time.strftime("%Y%m%d%H%M%S000")}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.5)
    t0 = time.monotonic()
    try:
        s.sendto(json.dumps(msg).encode(), (ip, 32100))
        data, addr = s.recvfrom(16384)
        rtt = 1000 * (time.monotonic() - t0)
        return ip, rtt, json.loads(data)
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def broadlink_probe(ip: str):
    t0 = time.monotonic()
    try:
        dev = broadlink.hello(ip, timeout=1.5)
    except Exception:
        return None
    return ip, 1000 * (time.monotonic() - t0), dev


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subnet", default="192.168.1")
    ap.add_argument("--router", default="192.168.1.1")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    hosts = args.only or [f"{args.subnet}.{i}" for i in range(1, 255)]

    print(f"UDP control (DNS to {args.router}:53): {dns_control(args.router)}")

    with ThreadPoolExecutor(max_workers=64) as pool:
        motion = [r for r in pool.map(motion_probe, hosts) if r]
        bl = [r for r in pool.map(broadlink_probe, hosts) if r]

    print(f"\nMotion/Bloc hubs answering GetDeviceList on :32100: {len(motion)}")
    for ip, rtt, resp in motion:
        devs = resp.get("data", [])
        print(f"  {ip}  rtt {rtt:.0f} ms  mac {resp.get('mac')}  deviceType {resp.get('deviceType')}"
              f"  fwVersion {resp.get('fwVersion')}  protocol {resp.get('ProtocolVersion')}"
              f"  children {len(devs)}")
        for d in devs:
            print(f"    - mac {d.get('mac')}  deviceType {d.get('deviceType')}")

    print(f"\nBroadLink devices answering hello on :80: {len(bl)}")
    for ip, rtt, dev in bl:
        print(f"  {ip}  rtt {rtt:.0f} ms  type {dev.type} (0x{dev.devtype:04x})  model {dev.model}"
              f"  mfr {dev.manufacturer}  mac {dev.mac.hex(':')}  locked {dev.is_locked}  name {dev.name!r}")


if __name__ == "__main__":
    main()
