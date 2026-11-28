"""The blind pairs' routes (tools/room/ad_eval/pairs.py makes the batches; the /lights/pairs page shows them).

    GET  /api/v1/eval/pairs                  the batches, each with how many pairs and how many are picked; ``next``:
                                             the first pair not yet picked, newest batch first
    GET  /api/v1/eval/pairs/{batch}          a batch's pairs (song, start, length) and which are picked
    GET  /api/v1/eval/pairs/{batch}/{n}      one pair's two sides' DMX frames (base64, one per hop), left and right
    GET  /api/v1/eval/pairs/{batch}/{n}/frames?from=&to=
                                             the item's sides' DMX frames over [from, to) s of the song (its section,
                                             or the whole song), from the replays' own recordings
    POST /api/v1/eval/pairs/{batch}/{n}      {"pick": "left" | "right" | "same"} for a pair, "good" | "wrong" | "skip"
                                             for a reel's single item (tools/room/ad_eval/reel.py); "words" optional.
                                             Kept in the batch's picks.jsonl; a reel item's good or wrong is also a
                                             moment note in the a.d.'s feedback file (ad_feedback.jsonl), which the
                                             eval scores as a case and the overnight loop reads first

A batch's key (which run is on which side) is never served: the picks stay blind. Files only; no brain state.
"""
from __future__ import annotations

import base64
import json
import pathlib
import re
import time
from typing import Any

import numpy as np

from aiohttp import web

from . import contract

PAIRS = contract.STATE_DIR / "ad_pairs"
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PICKS = ("left", "right", "same", "good", "wrong", "skip")
FEEDBACK = contract.STATE_DIR / "ad_feedback.jsonl"
MAX_WORDS = 1000
MAX_SPAN_S = 900.0


def _batch(name: str) -> pathlib.Path:
    if not _NAME.match(name) or name.startswith("."):
        raise web.HTTPNotFound()
    d = PAIRS / name
    if not (d / "pairs.json").is_file():
        raise web.HTTPNotFound()
    return d


def _key_item(d: pathlib.Path, n: int) -> dict:
    try:
        return next((k for k in json.loads((d / "key.json").read_text()).get("pairs") or () if int(k["n"]) == n), {})
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _public_item(d: pathlib.Path, n: int) -> dict:
    b = json.loads((d / "pairs.json").read_text())
    return next((p for p in b.get("pairs") or () if int(p["n"]) == n), {})


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


async def frames(request: web.Request, brain: Any) -> web.Response:
    """Each side's frames over a stretch of the song (the reel's "section" and "song"), read from the replay's DMX."""
    d = _batch(request.match_info["batch"])
    n = request.match_info["n"]
    if not n.isdigit():
        raise web.HTTPNotFound()
    k = _key_item(d, int(n))
    if not k:
        raise web.HTTPNotFound()
    try:
        a = max(0.0, float(request.query.get("from", "0")))
        b = float(request.query.get("to", "1e9"))
    except ValueError:
        return web.json_response({"error": "bad_range"}, status=400)
    b = min(b, a + MAX_SPAN_S)
    out: dict[str, Any] = {}
    for side in ("left", "right"):
        s = k.get(side)
        if not s:
            continue
        tl = pathlib.Path(str(s.get("timeline") or ""))
        z = tl.with_name(tl.name[:-len(".json")] + ".dmx.npz")
        if not z.is_file():
            raise web.HTTPNotFound()
        with np.load(z) as f:
            dmx, t0, hop = f["dmx"], float(f["t0_s"]), float(f["hop_s"])
        i0 = max(0, int(round((a - t0) / hop)))
        i1 = max(i0, min(len(dmx), int(round((b - t0) / hop))))
        out[side] = [base64.b64encode(bytes(x)).decode() for x in dmx[i0:i1]]
        out["t0_s"], out["hop_s"] = round(t0 + i0 * hop, 3), hop
    return web.json_response(out)


def _note(d: pathlib.Path, n: int, choice: str, words: str) -> None:
    """A reel item's verdict as a moment note for the eval and the overnight loop (routes/show_notes.py's layout)."""
    if choice not in ("good", "wrong"):
        return
    p, k = _public_item(d, n), _key_item(d, n)
    if not p.get("song"):
        return
    line = {"at": round(time.time(), 1), "song": p["song"], "title": p.get("title"), "scope": "moment",
            "t_s": p.get("at_s"), "verdict": "good" if choice == "good" else "bad",
            "dimension": "ending" if k.get("what") == "ending" else "transition",
            "words": words or (p.get("label") or ""), "by": "owner", "from": "reel", "check": k.get("check"),
            "flag": p.get("flag"), "batch": d.name, "n": n, "run": (k.get("left") or {}).get("run")}
    FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


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
    words = str(body.get("words") or "").strip()[:MAX_WORDS] if isinstance(body, dict) else ""
    with (d / "picks.jsonl").open("a") as f:
        f.write(json.dumps({"n": int(n), "pick": choice, "at": round(time.time(), 1),
                            **({"words": words} if words else {})}) + "\n")
    _note(d, int(n), choice, words)
    return web.json_response({"ok": True, **_summary(d)})


ROUTES = [
    ("GET", "/api/v1/eval/pairs", batches),
    ("GET", "/api/v1/eval/pairs/{batch}", batch),
    ("GET", "/api/v1/eval/pairs/{batch}/{n}", clip),
    ("GET", "/api/v1/eval/pairs/{batch}/{n}/frames", frames),
    ("POST", "/api/v1/eval/pairs/{batch}/{n}", pick),
]
