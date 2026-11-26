#!/usr/bin/env python3
"""Layer sync eval: do the screens and the lights change scene together, on the art director's line?

Owner, 26 Nov: "there should be one controlling visual logic ... it should _feel_ the music and express it across all
the layers in an integrated way"; "do you have evals in place for this?"

Plays a stretch of a karaoke song on this machine's rig (the karaoke player, so the lyric sections are known), then
reads the two services' own logs for that stretch:

  * every MOVE the art director drew (config/live/moves.yaml), with the boundary it crossed;
  * the line each change landed on (the lights' programs are committed on it);
  * the screens' scene transitions: started, finished, which transition;
  * each light group's scene after the change, and the parts commissioned around it (gestures, accents).

Scores, per stretch:
  coincide   share of screen scene changes with a lights change within COINCIDE_S, and the reverse
  strays     screen scene changes with no art-director change near them (must be 0 while it cues)
  landing    how far from the line each screen transition finished (s; + late), median and worst
  moves      how many changes drew a move made for that very boundary (not "any")

    .venv/bin/python tools/room/sync_eval/run.py "Don't Stop Me Now" [--from 46] [--for 88] [--json]

It drives the live room (Karaoke mode comes on, the song plays, then stops): never during a party.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics
import sys
import time
import urllib.request

FLASK = "http://localhost:5000"
HOME = os.path.expanduser("~/.local/state/screen-machine")
COINCIDE_S = 2.0


def api(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(FLASK + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b"{}")


def read(t0: float, t1: float) -> dict:
    scr: dict = {}
    for ln in open(HOME + "/logs/stream_service.log", errors="ignore"):
        if '"session.transition"' not in ln or '"kind":"scene"' not in ln:
            continue
        try:
            ts = datetime.datetime.strptime(ln[:23], "%Y-%m-%d %H:%M:%S,%f").timestamp()
            e = json.loads(ln[ln.index("{"):])
        except ValueError:
            continue
        if t0 <= ts <= t1 and e.get("phase") in ("started", "done"):
            scr.setdefault(e.get("cue_id"), {"transition": e.get("transition"), "scene": e.get("scene"),
                                             "reason": e.get("reason")})[e["phase"]] = ts
    moves, lines, parts = [], [], []
    for ln in open(HOME + "/show/director.jsonl", errors="ignore"):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if not t0 <= e.get("t", 0) <= t1:
            continue
        k = e.get("kind")
        if k == "move":
            moves.append({"t": e["t"], "move": e["move"], "from": e.get("from"), "into": e.get("into"),
                          "size": e.get("size")})
        elif k == "light_program":
            lines.append({"t": e["t"], "groups": {g: v.get("scene") for g, v in e["groups"].items()},
                          "via": {g: v.get("transition") for g, v in e["groups"].items()},
                          "why": next(iter(e["groups"].values())).get("why", "").split(":")[0]})
        elif k == "light_effect" and e.get("trigger") == "transition":
            parts.append({"t": e["t"], "group": e.get("group"), "name": e["run"]["name"]})
    return {"screen": [v for v in scr.values() if "started" in v], "moves": moves, "lines": lines, "parts": parts}


def score(d: dict) -> dict:
    scr, lines = d["screen"], d["lines"]
    near = lambda t, arr, key: any(abs(t - a[key]) <= COINCIDE_S for a in arr)      # noqa: E731
    ends = [s for s in scr if "done" in s]
    land = []
    for ln in lines:
        s = min(ends, key=lambda x: abs(x["done"] - ln["t"]), default=None)
        if s is not None and abs(s["done"] - ln["t"]) <= 6.0:
            land.append(round(s["done"] - ln["t"], 2))
    made = [m for m in d["moves"] if m.get("into") or m.get("from")]
    return {"screen_changes": len(scr), "lights_changes": len(lines),
            "screen_with_lights": sum(near(s.get("done", s["started"]), lines, "t") for s in scr),
            "lights_with_screen": sum(any(abs(ln["t"] - s.get("done", s["started"])) <= COINCIDE_S for s in scr)
                                      for ln in lines),
            "strays": sum(not any(abs(s["started"] - ln["t"]) <= 6.0 for ln in lines) for s in scr),
            "landing_s": land, "landing_median_s": round(statistics.median(land), 2) if land else None,
            "landing_worst_s": max(land, key=abs) if land else None,
            "moves": len(d["moves"]), "moves_for_a_boundary": len(made)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("song", help="a title in the karaoke library (case-insensitive substring)")
    ap.add_argument("--from", dest="pos", type=float, default=0.0, help="start at this song position (s)")
    ap.add_argument("--for", dest="dur", type=float, default=90.0, help="play this long (s)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    songs = api("/api/karaoke/state")["songs"]
    songs = songs if isinstance(songs, list) else list(songs.values())
    hit = next((s for s in songs if a.song.lower() in str(s.get("title", "")).lower()
                and (s.get("state") or s.get("status")) == "ready"), None)
    if hit is None:
        sys.exit(f"no ready song matches {a.song!r}")
    api("/api/karaoke/player", {"action": "play", "song_id": hit["id"]})
    time.sleep(14.0)                                    # the room goes to Karaoke and the screen live
    t0 = time.time()
    api("/api/karaoke/player", {"action": "seek", "pos_s": a.pos})
    time.sleep(a.dur)
    api("/api/karaoke/player", {"action": "stop"})
    time.sleep(1.0)
    d = read(t0, t0 + a.dur)
    s = score(d)
    if a.json:
        print(json.dumps({"song": hit.get("title"), "from_s": a.pos, "for_s": a.dur, "score": s, **d}, indent=1))
        return
    print(f"{hit.get('title')}: {a.pos:g} s .. {a.pos + a.dur:g} s")
    for m in d["moves"]:
        ln = min((x for x in d["lines"] if x["t"] >= m["t"]), key=lambda x: x["t"], default=None)
        sc = min((x for x in d["screen"] if "done" in x), key=lambda x: abs(x["done"] - (ln or m)["t"]), default=None)
        ps = sorted({p["name"] for p in d["parts"] if abs(p["t"] - m["t"]) < 0.5})
        print(f"  {m['t'] - t0:5.1f}s  {m['move']:14} {m.get('from')} -> {m.get('into')}")
        if ln is not None:
            print(f"          lights: {ln['groups']}")
            print(f"          parts: {ps}; screen via {sc['transition'] if sc else None}, finished "
                  f"{round(sc['done'] - ln['t'], 2) if sc else None} s from the line")
    print(f"  screen changes {s['screen_changes']}, lights changes {s['lights_changes']}; together within "
          f"{COINCIDE_S:g} s: {s['screen_with_lights']}/{s['screen_changes']} and "
          f"{s['lights_with_screen']}/{s['lights_changes']}; strays {s['strays']}")
    print(f"  landing: median {s['landing_median_s']} s, worst {s['landing_worst_s']} s ({s['landing_s']}); "
          f"moves made for the boundary: {s['moves_for_a_boundary']}/{s['moves']}")


if __name__ == "__main__":
    main()
