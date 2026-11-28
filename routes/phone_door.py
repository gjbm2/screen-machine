"""The phones' door when the Wi-Fi keeps them from the laptop (owner, 28 Nov: "a lot of commercial wifis may block
traffic between lan clients. we need a workaround for mobile to access same page as laptop is serving"; chose: a
Cloudflare tunnel, the laptop's own hotspot as the fallback).

With the deployment's ``phone_tunnel`` capability, Flask keeps a Cloudflare quick tunnel open (``cloudflared tunnel
--url http://127.0.0.1:<flask>``: outbound only, no account, a new https://<words>.trycloudflare.com each start). A
phone then reaches the karaoke panel over the internet, whatever the venue's Wi-Fi allows between its clients, and
with nothing to install. The screens' QR code (routes/karaoke/join.py) is the tunnel's address while it is up, and
the laptop's own address when it isn't (no internet: the hotspot).

The tunnel's origin is the door (``serve``, a small aiohttp proxy in this process on loopback port ``phone_door``),
laid out as finsbury's public HTTPS is (Caddy): ``/rs/*`` is room_service (HTTP and its event WebSocket), so the
karaoke panel's screen card and lights work from a phone too (owner, 28 Nov: "why can't I interact with screen
controls and light controls from my mobile over cloudflare?"); everything else is Flask. Through the tunnel Flask
lets the karaoke panel only (``guard``): its page, the app's static files and the karaoke and sound APIs; any other
page goes to /karaoke, anything else answers 404 (owner: "generator ends up exposed, which I don't think is
appropriate"); the app itself shows the karaoke panel alone there (src/shared/viaTunnel.ts).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from typing import Optional

import smconfig
from utils.logger import error, info

TUNNEL_HOST = re.compile(r"\.trycloudflare\.com$", re.I)
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
RETRY_S = 20.0

#: What a phone may reach through the tunnel: the karaoke panel and what it loads (path prefixes, or exact paths).
ALLOW_PREFIXES = ("/karaoke", "/assets/", "/api/karaoke/", "/api/songs/", "/api/sound/", "/fonts/", "/icons/")
ALLOW_EXACT = {"/favicon.ico", "/favicon.svg", "/manifest.webmanifest", "/manifest.json", "/robots.txt",
               "/api/deployment", "/api/auth/me", "/api/auth/config", "/apple-touch-icon.png"}

_url: Optional[str] = None
_proc: Optional[subprocess.Popen] = None
_lock = threading.Lock()


def url() -> Optional[str]:
    """The tunnel's https address while it is up, else None."""
    with _lock:
        return _url if _proc is not None and _proc.poll() is None else None


def allowed(path: str) -> bool:
    return path in ALLOW_EXACT or path.startswith(ALLOW_PREFIXES)


def through_tunnel(host: str) -> bool:
    return bool(TUNNEL_HOST.search((host or "").split(":")[0]))


def guard(request):
    """Flask before_request: through the tunnel, the karaoke panel only: its address "/" goes to /karaoke; anything
    else -> 404 (None: carry on)."""
    if not through_tunnel(request.host):
        return None
    # the door is on this machine, so Flask's gates pass it: the sound settings are read-only through the tunnel
    if allowed(request.path) and not (request.path.startswith("/api/sound/") and request.method not in ("GET", "HEAD")):
        return None
    from flask import jsonify, redirect
    if request.path == "/" or (request.accept_mimetypes.accept_html and not request.path.startswith("/api/")):
        return redirect("/karaoke", code=302)          # a page that isn't the panel: the panel
    return jsonify({"error": "not here"}), 404


def _run() -> None:
    global _url, _proc
    exe = shutil.which("cloudflared")
    if not exe:
        error("phone tunnel: cloudflared isn't installed (the screens' QR stays the laptop's own address)")
        return
    while True:
        p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{smconfig.port('phone_door')}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
        with _lock:
            _proc, _url = p, None
        for line in p.stderr or []:
            m = URL_RE.search(line)
            if m:
                with _lock:
                    _url = m.group(0)
                info(f"phone tunnel: {_url} (the karaoke panel for phones on any Wi-Fi)")
                _clear_join_cache()
        p.wait()
        with _lock:
            _url = None
        _clear_join_cache()
        info(f"phone tunnel ended ({p.returncode}); again in {RETRY_S:.0f} s")
        time.sleep(RETRY_S)


def _clear_join_cache() -> None:
    try:
        from routes.karaoke import join
        join.forget()
    except Exception:
        pass


def _orphans() -> list[int]:
    """Tunnels a previous Flask left running (cloudflared quick tunnels to our port whose parent is gone)."""
    import os
    want = f"--url http://127.0.0.1:{smconfig.port('phone_door')}"
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
            ppid = int(open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if "cloudflared" in cmd and want in cmd and ppid == 1:
            out.append(int(d))
    return out


def start() -> None:
    """Only with the deployment's phone_tunnel capability; once. A tunnel the last Flask left is ended first."""
    if not smconfig.enabled("phone_tunnel") or getattr(start, "_on", False):
        return
    start._on = True  # type: ignore[attr-defined]
    import os
    import signal
    for pid in _orphans():
        try:
            os.kill(pid, signal.SIGTERM)
            info(f"phone tunnel: ended the one a previous Flask left (pid {pid})")
        except OSError:
            pass
    threading.Thread(target=_serve_forever, name="phone-door", daemon=True).start()
    threading.Thread(target=_run, name="phone-tunnel", daemon=True).start()


def stop() -> None:
    with _lock:
        if _proc is not None and _proc.poll() is None:
            _proc.terminate()


# -- the door: /rs/* -> room_service, the rest -> Flask (both on loopback) ---------------------------------------
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "content-length", "content-encoding"}


def _target(path: str) -> str:
    if path == "/rs" or path.startswith("/rs/"):
        return f"http://127.0.0.1:{smconfig.port('room')}" + (path[3:] or "/")
    return f"http://127.0.0.1:{smconfig.port('flask')}" + path


def make_app():
    import aiohttp
    from aiohttp import web

    async def ws_pass(request, url):
        ours = web.WebSocketResponse()
        await ours.prepare(request)
        async with aiohttp.ClientSession() as cs:
            async with cs.ws_connect(url.replace("http://", "ws://", 1)) as theirs:
                async def up():
                    async for m in ours:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            await theirs.send_str(m.data)
                        elif m.type == aiohttp.WSMsgType.BINARY:
                            await theirs.send_bytes(m.data)
                    await theirs.close()

                async def down():
                    async for m in theirs:
                        if m.type == aiohttp.WSMsgType.TEXT:
                            await ours.send_str(m.data)
                        elif m.type == aiohttp.WSMsgType.BINARY:
                            await ours.send_bytes(m.data)
                    await ours.close()

                import asyncio
                await asyncio.gather(up(), down(), return_exceptions=True)
        return ours

    async def handle(request):
        url = _target(request.path) + (f"?{request.query_string}" if request.query_string else "")
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await ws_pass(request, url)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
        body = await request.read()
        async with request.app["cs"].request(request.method, url, headers=headers, data=body or None,
                                             allow_redirects=False) as r:
            out = web.StreamResponse(status=r.status, headers={k: v for k, v in r.headers.items()
                                                               if k.lower() not in HOP})
            await out.prepare(request)
            async for chunk in r.content.iter_chunked(64 * 1024):
                await out.write(chunk)
            await out.write_eof()
            return out

    async def on_start(app):
        app["cs"] = aiohttp.ClientSession(auto_decompress=False, timeout=aiohttp.ClientTimeout(total=None, sock_connect=5))

    async def on_stop(app):
        await app["cs"].close()

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("*", "/{tail:.*}", handle)
    app.on_startup.append(on_start)
    app.on_cleanup.append(on_stop)
    return app


def _serve_forever() -> None:
    import asyncio
    from aiohttp import web
    try:
        asyncio.set_event_loop(asyncio.new_event_loop())
        web.run_app(make_app(), host="127.0.0.1", port=smconfig.port("phone_door"), print=None,
                    handle_signals=False)
    except Exception as e:  # noqa: BLE001
        error(f"phone door stopped: {e}")
