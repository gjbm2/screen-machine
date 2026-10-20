"""Pair with a Hue bridge: `python -m room_service.hue.pair 192.168.1.72 [--wait 120]`.

Polls POST /api {devicetype, generateclientkey} until someone presses the bridge's link button, then writes
{host, bridge_id, name, model, app_key, clientkey} to ~/.config/screen-machine/hue/<bridge_id>.json (0600).
The clientkey (the Entertainment PSK) is only ever returned here, once, so the file is the only copy.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

SECRETS_DIR = Path("~/.config/screen-machine/hue").expanduser()


def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # bridge cert is Signify-CA signed, CN = bridge id
    return ctx


def _req(method: str, url: str, body: dict | None = None, timeout: float = 5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=_ctx(), timeout=timeout) as r:
        return json.loads(r.read())


def secrets_path(bridge_id: str) -> Path:
    return SECRETS_DIR / f"{bridge_id.lower()}.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("--wait", type=float, default=120, help="seconds to keep polling for the link button")
    ap.add_argument("--devicetype", default="screen-machine#room_service")
    a = ap.parse_args()

    cfg = _req("GET", f"https://{a.host}/api/0/config")
    bridge_id = cfg["bridgeid"].lower()
    out = secrets_path(bridge_id)
    if out.exists():
        print(f"already paired: {out}")
        return 0
    print(f"bridge {cfg['name']!r} {cfg['modelid']} {bridge_id} at {a.host}: press its link button now", flush=True)
    deadline = time.monotonic() + a.wait
    while time.monotonic() < deadline:
        res = _req("POST", f"https://{a.host}/api", {"devicetype": a.devicetype, "generateclientkey": True})[0]
        if "success" in res:
            SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            rec = {"host": a.host, "bridge_id": bridge_id, "name": cfg["name"], "model": cfg["modelid"],
                   "app_key": res["success"]["username"], "clientkey": res["success"]["clientkey"],
                   "paired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(rec, f, indent=2)
            print(f"paired; secrets in {out}")
            return 0
        if res.get("error", {}).get("type") != 101:          # 101 = link button not pressed
            print(f"bridge refused: {res}", file=sys.stderr)
            return 2
        time.sleep(1.0)
    print("timed out waiting for the link button", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
