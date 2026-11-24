"""GET /api/screensaver/download?screen=<id>&origin=<the page's origin>: the Windows screensaver (tools/screensaver/).

One built file (tools/screensaver/build.sh) for every site; each download gets its server and screen written into a
fixed slot inside the program (stamp()), which it reads back. Never appended after the end of the file: Defender's
model flags a program with data after its end as a hidden script, and the domain in plain text as a banking trojan
(Trojan:Script/Wacatac.B!ml, .H!ml, 24 Nov). The server is
the page's own origin (the address the person reached the app at), else this request's.
"""
import base64
import json
import re
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

screensaver_api = Blueprint("screensaver_api", __name__)

BUILT = Path(__file__).resolve().parent.parent / "tools" / "screensaver" / "out" / "ScreenMachine-screensaver.exe"
VERSION = BUILT.parent / "version.txt"  # written by build.sh beside the build
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")
_SCREEN = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


def _server():
    origin = (request.args.get("origin") or "").rstrip("/")
    if _ORIGIN.match(origin):
        return origin
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme
    return f"{proto}://{request.headers.get('X-Forwarded-Host') or request.host}"


_OPEN = "[[SMSS-CONFIG".encode("utf-16-le")
_CLOSE = "]]".encode("utf-16-le")


def stamp(exe: bytes, server: str, screen: str) -> bytes:
    """The program with {"server", "screen"} in its slot: a UTF-16 string literal "[[SMSS-CONFIG<spaces>]]" in the
    program (ScreenMachineSaver.cs, Self.Slot), overwritten in place at the same length with base64 JSON. Never the
    address as plain text: Defender's model blocked the program with the site's domain ("...bank...") in it."""
    start = exe.find(_OPEN)
    if start < 0:
        raise ValueError("no config slot in the screensaver build")
    body_at = start + len(_OPEN)
    end = exe.find(_CLOSE, body_at)
    room = (end - body_at) // 2
    text = base64.b64encode(json.dumps({"server": server, "screen": screen}).encode()).decode()
    if end < 0 or len(text) > room:
        raise ValueError("the screensaver's config slot is too small")
    return exe[:body_at] + text.ljust(room).encode("utf-16-le") + exe[end:]


@screensaver_api.route("/screensaver/download", methods=["GET"])
def download():
    if not BUILT.is_file():
        return jsonify({"error": "the screensaver isn't built on this server (tools/screensaver/build.sh)"}), 404
    screen = request.args.get("screen") or ""
    if not _SCREEN.match(screen):
        screen = ""
    data = stamp(BUILT.read_bytes(), _server(), screen)
    return Response(data, mimetype="application/vnd.microsoft.portable-executable", headers={
        "Content-Disposition": 'attachment; filename="ScreenMachine-screensaver.exe"',
        "Cache-Control": "no-store",
    })


@screensaver_api.route("/screensaver/version", methods=["GET"])
def version():
    """The built screensaver's version (its UTC build time, yyyyMMddHHmm): installed copies check it once a day and
    update themselves from /screensaver/download when it's newer."""
    try:
        v = VERSION.read_text().strip() if BUILT.is_file() else ""
    except OSError:
        v = ""
    return jsonify({"version": v}), 200, {"Cache-Control": "no-store"}
