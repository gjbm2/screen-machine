"""GET /api/screensaver/download?screen=<id>&origin=<the page's origin>: the Windows screensaver (tools/screensaver/).

One built file (tools/screensaver/build.sh) for every site; each download gets its server and screen appended as a
trailer the program reads back: [utf-8 JSON]{"server", "screen"}[int32 length, little-endian]["SMSS"]. The server is the
page's own origin (the address the person reached the app at), else this request's.
"""
import json
import re
import struct
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

screensaver_api = Blueprint("screensaver_api", __name__)

BUILT = Path(__file__).resolve().parent.parent / "tools" / "screensaver" / "out" / "ScreenMachine-screensaver.exe"
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")
_SCREEN = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


def _server():
    origin = (request.args.get("origin") or "").rstrip("/")
    if _ORIGIN.match(origin):
        return origin
    proto = request.headers.get("X-Forwarded-Proto") or request.scheme
    return f"{proto}://{request.headers.get('X-Forwarded-Host') or request.host}"


def trailer(server: str, screen: str) -> bytes:
    body = json.dumps({"server": server, "screen": screen}).encode()
    return body + struct.pack("<i", len(body)) + b"SMSS"


@screensaver_api.route("/screensaver/download", methods=["GET"])
def download():
    if not BUILT.is_file():
        return jsonify({"error": "the screensaver isn't built on this server (tools/screensaver/build.sh)"}), 404
    screen = request.args.get("screen") or ""
    if not _SCREEN.match(screen):
        screen = ""
    data = BUILT.read_bytes() + trailer(_server(), screen)
    return Response(data, mimetype="application/vnd.microsoft.portable-executable", headers={
        "Content-Disposition": 'attachment; filename="ScreenMachine-screensaver.exe"',
        "Cache-Control": "no-store",
    })
