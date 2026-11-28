"""The owner's verdict on how the show interpreted a song (owner, 28 Nov: "it's the interpretation of a song generally
[I want to give feedback on]. We need a sensible input field for that"). One line each in the art director's
feedback file, ~/.local/state/screen-machine/show/ad_feedback.jsonl, the one the overnight eval scores every run
against (tools/room/ad_eval: a song-wide note is a case on the song's overall score; a good one must not fall, a bad one
should rise) and the overnight loop reads (.claude/skills/ad-improve):
  {"at", "song", "title", "scope": "song", "t_s": null, "verdict": good|bad, "dimension": "overall", "words",
   "by": "owner", "sha", "from": "app"}

  POST /api/show-notes {song, title?, verdict, words}  -> the line
  GET  /api/show-notes?song=                           -> {"notes": that song's notes, newest first (any scope)}
"""
from __future__ import annotations

import json
import subprocess
import threading
import time

from flask import Blueprint, jsonify, request

import smconfig

show_notes_api = Blueprint("show_notes_api", __name__)
_lock = threading.Lock()
MAX_WORDS = 1000
VERDICTS = ("good", "bad")


def path():
    return smconfig.state("show", "ad_feedback.jsonl")


def _sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=smconfig.REPO_ROOT, capture_output=True,
                              text=True, timeout=3).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


@show_notes_api.post("/show-notes")
def add_note():
    b = request.get_json(silent=True) or {}
    song, verdict, words = b.get("song"), b.get("verdict"), str(b.get("words") or "").strip()
    if not isinstance(song, str) or not song or verdict not in VERDICTS or not words:
        return jsonify({"error": "song, verdict (good | bad) and words"}), 400
    line = {"at": round(time.time(), 1), "song": song, "title": str(b.get("title") or "")[:200] or None,
            "scope": "song", "t_s": None, "verdict": verdict, "dimension": "overall", "words": words[:MAX_WORDS],
            "by": "owner", "sha": _sha(), "from": "app"}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return jsonify(line)


@show_notes_api.get("/show-notes")
def list_notes():
    song = request.args.get("song")
    out = []
    try:
        for ln in path().read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            if isinstance(o, dict) and (not song or o.get("song") == song):
                out.append(o)
    except OSError:
        pass
    return jsonify({"notes": out[::-1][:100]})
