"""The address a phone opens the karaoke panel at: the QR code on the screens while karaoke rests (owner, 27 Nov:
"the resting state of any screens in karaoke mode [should] include a QR code for the user that opens the control
ui"). KaraokeLayer draws it from /api/karaoke/now's ``join``.

* ``.env`` VITE_APP_LAN_URL when it names a real host (finsbury: the friendly HTTPS name), + /karaoke.
* Else (the laptop: its .env says localhost, which a phone can't open) this machine's LAN address, looked up fresh
  every LOOKUP_S because the laptop moves between networks: under WSL, the Windows adapter with a default gateway
  (Windows forwards the Flask port to WSL); on plain Linux, the address of the default route. With the Flask port.
* None when neither is known: the screens show no code.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import smconfig

LOOKUP_S = 30.0
PAGE = "/karaoke"
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

_lock = threading.Lock()
_cached: tuple[float, Optional[str]] = (0.0, None)


def join_url() -> Optional[str]:
    """The karaoke panel's address for a phone, or None (cached LOOKUP_S)."""
    global _cached
    with _lock:
        at, url = _cached
        if time.monotonic() - at < LOOKUP_S and at:
            return url
    url = _lookup()
    with _lock:
        _cached = (time.monotonic(), url)
    return url


def _lookup() -> Optional[str]:
    base = (os.getenv("VITE_APP_LAN_URL") or os.getenv("VITE_URL") or "").strip().rstrip("/")
    host = urlparse(base).hostname if base else None
    if host and host not in _LOOPBACK:
        return base + PAGE
    ip = lan_ip()
    if not ip:
        return None
    return f"http://{ip}:{smconfig.port('flask')}{PAGE}"


def lan_ip() -> Optional[str]:
    """This machine's address on the network phones are on (None if it can't tell)."""
    if _is_wsl():
        return _windows_ip()
    return _route_ip()


def _is_wsl() -> bool:
    try:
        return "microsoft" in open("/proc/version").read().lower()
    except OSError:
        return False


def _windows_ip() -> Optional[str]:
    try:
        out = subprocess.run(["ipconfig.exe"], capture_output=True, timeout=5).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return pick_ipconfig(out)


def pick_ipconfig(out: str) -> Optional[str]:
    """From ``ipconfig`` output: the private IPv4 of an adapter that has an IPv4 default gateway (Wi-Fi or Ethernet;
    not Tailscale or the WSL switch, which have none). The first such adapter wins."""
    sections = re.split(r"\r?\n(?=\S)", out)
    for sec in sections:
        ips = re.findall(r"IPv4 Address[ .]*:\s*([\d.]+)", sec)
        gw = re.search(r"Default Gateway[ .]*:([^\n]*(?:\n\s{20,}[^\n]*)*)", sec)
        gws = re.findall(r"\b\d+\.\d+\.\d+\.\d+\b", gw.group(1)) if gw else []
        for ip in ips:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if gws and a.is_private and not a.is_loopback and not a.is_link_local:
                return ip
    return None


def _route_ip() -> Optional[str]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent: it only picks the default route's source address
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") else ip
