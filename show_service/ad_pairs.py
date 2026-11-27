"""The blind pairs' routes (tools/room/ad_eval/pairs.py makes the batches; the /lights/pairs page shows them).

    GET  /api/v1/eval/pairs                  the batches, each with how many pairs and how many are picked; ``next``:
                                             the first pair not yet picked, newest batch first
    GET  /api/v1/eval/pairs/{batch}          a batch's pairs (song, start, length) and which are picked
    GET  /api/v1/eval/pairs/{batch}/{n}      one pair's two sides' DMX frames (base64, one per hop), left and right
    POST /api/v1/eval/pairs/{batch}/{n}      {"pick": "left" | "right" | "same"}: kept in the batch's picks.jsonl

A batch's key (which run is on which side) is never served: the picks stay blind. Files only; no brain state.
"""
from __future__ import annotations

import json
import pathlib
import re
import time
from typing import Any

from aiohttp import web

from . import contract

PAIRS = contract.STATE_DIR / "ad_pairs"
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PICKS = ("left", "right", "same")


def _batch(name: str) -> pathlib.Path:
    if not _NAME.match(name) or name.startswith("."):
        raise web.HTTPNotFound()
    d = PAIRS / name
    if not (d / "pairs.json").is_file():
        raise web.HTTPNotFound()
    return d


def _picked(d: pathlib.Path) -> dict[int, str]:
    out: dict[int, str] = {}
    p = d / "picks.jsonl"
    if p.is_file():
        for line in p.read_text().splitlines():
            try:
                o = json.loads(line)
                out[int(o["n"])] = str(o["pick"])
            except (ValueError, KeyError, TypeError):
                continue
    return out


def _summary(d: pathlib.Path) -> dict[str, Any]:
    b = json.loads((d / "pairs.json").read_text())
    picked = _picked(d)
    ns = [int(p["n"]) for p in b.get("pairs") or ()]
    return {"batch": d.name, "made_at": b.get("made_at"), "kind": b.get("kind"), "pairs": len(ns),
            "picked": sum(1 for n in ns if n in picked),
            "next": next((n for n in ns if n not in picked), None)}


async def batches(request: web.Request, brain: Any) -> web.Response:
    ds = sorted((p for p in PAIRS.iterdir() if (p / "pairs.json").is_file()), key=lambda p: p.name, reverse=True) \
        if PAIRS.is_dir() else []
    rows = [_summary(d) for d in ds]
    nxt = next(({"batch": r["batch"], "n": r["next"]} for r in rows if r["next"] is not None), None)
    return web.json_response({"batches": rows, "next": nxt})


async def batch(request: web.Request, brain: Any) -> web.Response:
    d = _batch(request.match_info["batch"])
    b = json.loads((d / "pairs.json").read_text())
    picked = _picked(d)
    return web.json_response({**b, "picked": {str(n): p for n, p in picked.items()}})


async def clip(request: web.Request, brain: Any) -> web.Response:
    d = _batch(request.match_info["batch"])
    n = request.match_info["n"]
    if not n.isdigit():
        raise web.HTTPNotFound()
    p = d / f"clip-{int(n)}.json"
    if not p.is_file():
        raise web.HTTPNotFound()
    return web.Response(body=p.read_bytes(), content_type="application/json")


async def pick(request: web.Request, brain: Any) -> web.Response:
    d = _batch(request.match_info["batch"])
    n = request.match_info["n"]
    if not n.isdigit() or not (d / f"clip-{int(n)}.json").is_file():
        raise web.HTTPNotFound()
    try:
        body = await request.json()
    except ValueError:
        body = {}
    choice = body.get("pick") if isinstance(body, dict) else None
    if choice not in PICKS:
        return web.json_response({"error": "bad_pick", "message": f"pick is one of {', '.join(PICKS)}"}, status=400)
    with (d / "picks.jsonl").open("a") as f:
        f.write(json.dumps({"n": int(n), "pick": choice, "at": round(time.time(), 1)}) + "\n")
    return web.json_response({"ok": True, **_summary(d)})


ROUTES = [
    ("GET", "/api/v1/eval/pairs", batches),
    ("GET", "/api/v1/eval/pairs/{batch}", batch),
    ("GET", "/api/v1/eval/pairs/{batch}/{n}", clip),
    ("POST", "/api/v1/eval/pairs/{batch}/{n}", pick),
]
