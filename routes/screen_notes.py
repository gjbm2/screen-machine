"""The owner's comments on what a screen showed (owner, 27 Nov: the unrolled screen view shows what was, what is and
what is coming, "so that we can see what was before and score it, give comments"). Each comment is one line in
~/.local/state/screen-machine/show/screen_notes.jsonl:
  {"at", "screen", "kind": scene|effect, "key": its rating key, "label", "when": was|now|next, "words",
   "song"?: {"id", "title", "t_s"}}   (the karaoke song playing then, and where it was)
for the art director's reviews (.claude/skills/ad-review) beside ad_feedback.jsonl.

  POST /api/screen-notes {screen, kind, key, label, when, words}  -> the line
  GET  /api/screen-notes?screen=&limit=                           -> {"notes": newest first}
"""
from __future__ import annotations

import json
import threading
import time

from flask import Blueprint, jsonify, request

import smconfig

screen_notes_api = Blueprint("screen_notes_api", __name__)
_lock = threading.Lock()
MAX_WORDS = 500


def path():
    return smconfig.state("show", "screen_notes.jsonl")


def _song() -> dict | None:
    try:
        import routes.karaoke.api as K
        st = K._store
        if st is None:
            return None
        p = st.s["player"]
        if p.get("state") != "playing" or not p.get("song_id"):
            return None
        song = st.s["songs"].get(p["song_id"]) or {}
        return {"id": p["song_id"], "title": song.get("title"), "t_s": round(st.position(), 1)}
    except Exception:
        return None


def read(screen: str | None = None, limit: int = 50) -> list[dict]:
    p = path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            n = json.loads(line)
        except ValueError:
            continue
        if screen and n.get("screen") != screen:
            continue
        out.append(n)
    return out[::-1][:limit]


@screen_notes_api.route("/screen-notes", methods=["POST"])
def add():
    b = request.get_json(silent=True) or {}
    words = str(b.get("words") or "").strip()[:MAX_WORDS]
    key = str(b.get("key") or "").strip()
    if not words or not key:
        return jsonify({"error": "say key and words"}), 400
    kind = b.get("kind") if b.get("kind") in ("scene", "effect") else ("effect" if key.startswith("effect/") else "scene")
    n = {"at": round(time.time(), 1), "screen": str(b.get("screen") or "")[:40], "kind": kind, "key": key[:120],
         "label": str(b.get("label") or "")[:120], "when": b.get("when") if b.get("when") in ("was", "now", "next") else "now",
         "words": words}
    s = _song()
    if s:
        n["song"] = s
    with _lock:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(n, ensure_ascii=False) + "\n")
    return jsonify(n)


@screen_notes_api.route("/screen-notes", methods=["GET"])
def list_():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    return jsonify({"notes": read(request.args.get("screen") or None, limit)})
