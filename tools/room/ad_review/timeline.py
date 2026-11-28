#!/usr/bin/env python3
"""The art director's decisions, laid against the song, for review (.claude/skills/ad-review).

  timeline.py sessions [--hours 48]           songs played recently (from the show's own logs), newest last
  timeline.py build SONG [--play N] [--out F]  one play of a song (id or part of the title; N: 1 = latest)
                                               -> a timeline JSON (the format tools/room/ad_eval writes)
  timeline.py note SONG T good|bad DIM WORDS  record a verdict on a moment (ad_feedback.jsonl)
  timeline.py notes                          the verdicts so far
  timeline.py sheet FILE [--lyrics]           a timeline JSON (built here, or an ad_eval run's) as a review sheet:
                                               every decision at its song time, with the reference sections between

Reads only: the director/cues/listener logs (~/.local/state/screen-machine/show/), the karaoke library
(~/.local/state/screen-machine/karaoke/), the eval's reference cache (~/.cache/sm-ad-eval/ref/<song>.json) and the
song shape cache. Song time: the log line's own ``song.pos_s`` (since 27 Nov), else seconds since the play's
track change (marked approx: a lead-in shifts it by up to 8 s).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any, Iterable, Optional

STATE = pathlib.Path.home() / ".local/state/screen-machine"
SHOW = STATE / "show"
KARAOKE = STATE / "karaoke"
REF = pathlib.Path.home() / ".cache/sm-ad-eval/ref"
# verdicts on moments: the owner's own, and findings the owner agreed. tools/room/ad_eval scores every run against
# them (each is a regression case), so a judgement given once keeps counting.
FEEDBACK = SHOW / "ad_feedback.jsonl"


def _sha() -> str:
    import subprocess
    try:
        return subprocess.run(["git", "-C", str(pathlib.Path(__file__).resolve().parents[3]), "rev-parse", "--short",
                               "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:                                   # noqa: BLE001
        return ""

# kinds that are decisions worth reviewing (the rest are bookkeeping; --all keeps them)
DECISIONS = {"look_change", "move", "section_look", "section_kept", "section_memory", "token", "transition", "track",
             "song_end", "light_scene", "lineup", "light_program", "moment", "treatment", "screens_palette",
             "song_character", "drop", "cue", "nudge", "vibe"}
NOISY = {"light_effect", "room_light", "flow", "ensemble", "ensemble_gestures"}


def _jsonl(paths: Iterable[pathlib.Path]) -> list[dict]:
    out = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(d, dict):
                        out.append(d)
        except FileNotFoundError:
            pass
    return out


def director_log() -> list[dict]:
    rows = _jsonl([SHOW / "director.jsonl.1", SHOW / "director.jsonl"])
    rows.sort(key=lambda d: d.get("t") or 0)
    return rows


def songs() -> dict[str, dict]:
    try:
        return json.loads((KARAOKE / "state.json").read_text()).get("songs") or {}
    except (OSError, ValueError):
        return {}


def _title(s: dict) -> str:
    return f"{s.get('title') or '?'} — {s.get('artist') or '?'}"


def plays(rows: list[dict], lib: dict[str, dict]) -> list[dict]:
    """Each play: {song_id, title, t0 (wall), t1, exact}. From the log's own song field where it has one, else a
    'track' change followed by the song_character naming it."""
    by_title = {}
    for sid, s in lib.items():
        by_title[_title(s)] = sid
        by_title[str(s.get("title") or "")] = sid
    out: list[dict] = []
    chars: list[tuple[float, str]] = []
    cur: Optional[dict] = None
    for d in rows:
        t = d.get("t")
        if t is None:
            continue
        sg = d.get("song") if isinstance(d.get("song"), dict) else None
        if sg and sg.get("id"):
            pos = sg.get("pos_s")
            if cur is None or cur["song_id"] != sg["id"] or (pos is not None and cur.get("last_pos") is not None
                                                             and pos < cur["last_pos"] - 5):
                cur = {"song_id": sg["id"], "title": sg.get("title") or _title(lib.get(sg["id"], {})),
                       "t0": t - (pos or 0.0), "t1": t, "exact": True}
                out.append(cur)
            cur["t1"], cur["last_pos"] = t, pos
            continue
        if d.get("kind") == "track":
            cur = {"song_id": None, "title": None, "t0": t, "t1": t, "exact": False}
            out.append(cur)
            continue
        if cur is not None:
            cur["t1"] = t
        if d.get("kind") == "song_character":
            chars.append((t, str(d.get("song") or "")))
    # a song's character is logged around its track change (often a second before it): it names the play whose
    # start is nearest, within -5 .. +60 s
    for t, name in chars:
        cands = [p for p in out if not p["exact"] and -5.0 <= t - p["t0"] <= 60.0]
        if not cands:
            continue
        p = min(cands, key=lambda q: abs(t - q["t0"]))
        if p["song_id"] is None:
            p["song_id"] = by_title.get(name) or by_title.get(name.split(" — ")[0])
            p["title"] = name
    for p in out:
        p.pop("last_pos", None)
    return [p for p in out if p["t1"] - p["t0"] >= 20]


def _ref(song_id: str, lib: dict[str, dict]) -> dict:
    """The answer key: the eval's hindsight reference when it has been built, else what the library knows (the
    lyric lines, the show's shape) marked as rough."""
    p = REF / f"{song_id}.json"
    if p.exists():
        try:
            r = json.loads(p.read_text())
            r.setdefault("source", "ad_eval reference")
            return r
        except ValueError:
            pass
    r: dict[str, Any] = {"source": "rough: lyric lines and the show's own shape (not an independent reference)"}
    sp = SHOW / "shapes" / f"{song_id}.json"
    if sp.exists():
        try:
            sh = json.loads(sp.read_text())
            r["energy"] = {"hop_s": sh.get("hop"), "v": sh.get("v")}
            r["climax"] = sh.get("climax")
        except ValueError:
            pass
    s = lib.get(song_id) or {}
    r["end_s"] = s.get("audio_s") or s.get("duration")
    return r


def _lyrics(song_id: str) -> list[dict]:
    try:
        d = json.loads((KARAOKE / "songs" / song_id / "lyrics.json").read_text())
    except (OSError, ValueError):
        return []
    return [{"t_s": float(x["start"]), "text": x.get("text", "")} for x in d.get("lines") or [] if "start" in x]


def build(song: str, play: int = 1, keep_all: bool = False) -> dict:
    rows, lib = director_log(), songs()
    ps = plays(rows, lib)
    want = [p for p in ps if p["song_id"] == song or (song.lower() in str(p["title"] or "").lower())]
    if not want:
        raise SystemExit(f"no play of {song!r} in the logs (timeline.py sessions lists them)")
    p = want[-play]
    sid = p["song_id"]
    s = lib.get(sid or "", {})
    cues = [d for d in _jsonl([SHOW / "cues.jsonl"]) if p["t0"] - 2 <= float(d.get("at") or 0) <= p["t1"] + 15]
    dec = []
    for d in rows:
        t = d.get("t")
        if t is None or not (p["t0"] - 2 <= t <= p["t1"] + 15):
            continue
        k = d.get("kind")
        if not keep_all and k in NOISY:
            continue
        sg = d.get("song") if isinstance(d.get("song"), dict) else None
        ts = sg["pos_s"] if sg and sg.get("id") == sid and sg.get("pos_s") is not None else t - p["t0"]
        dec.append({"t_s": round(ts, 2), "kind": k, "why": d.get("why") or d.get("reason"), "detail": d})
    for c in cues:
        dec.append({"t_s": round(float(c["at"]) - p["t0"], 2), "kind": "cue", "why": f"{c.get('cue')} {c.get('kind')}",
                    "detail": c})
    dec.sort(key=lambda x: x["t_s"])
    return {"song": {"id": sid, "title": s.get("title") or p["title"], "artist": s.get("artist"),
                     "duration_s": s.get("audio_s") or s.get("duration"), "genre": s.get("genre"), "year": s.get("year")},
            "mode": "session", "run": {"from": "show logs", "played_at": time.strftime("%Y-%m-%d %H:%M",
                                                                                         time.localtime(p["t0"])),
                                       "song_time": "exact" if p["exact"] else "approx (since the track change)"},
            "reference": _ref(sid, lib) if sid else {}, "lyrics": _lyrics(sid) if sid else [], "decisions": dec}


def _mmss(t: Optional[float]) -> str:
    if t is None:
        return "  ?  "
    sgn = "-" if t < 0 else ""
    t = abs(t)
    return f"{sgn}{int(t // 60):02d}:{t % 60:04.1f}"


def _brief(d: dict) -> str:
    """One decision's essentials, from the director's own log entry."""
    x = d.get("detail") or {}
    k = d.get("kind")
    m = x.get("music") or {}
    bits: list[str] = []
    if k == "look_change":
        tr = x.get("transition") or {}
        sec = x.get("section") or {}
        bits += [str(x.get("look_id")), f"{tr.get('style')} {tr.get('beats') or tr.get('seconds')}",
                 f"palette {x.get('palette')}"]
        if sec:
            bits.append(f"section {sec.get('letter')}/{sec.get('section_kind')}{' predicted' if sec.get('predicted') else ''}")
        if x.get("recalled"):
            bits.append("recalled")
    elif k == "move":
        bits += [str(x.get("move")), f"{x.get('from')}→{x.get('into')}", f"size {x.get('size')}"]
    elif k == "light_scene":
        bits += [f"{x.get('was')}→{x.get('scene')}"]
    elif k == "lineup":
        bits += [str(x.get("lineup")), f"plays {x.get('plays')}", f"drive {x.get('drive')}"]
    elif k == "token":
        bits += [str(x.get("decision")), str((x.get("event") or {}).get("kind"))]
    elif k in ("transition", "track"):
        tr = x.get("transition") or {}
        bits += [f"{tr.get('style')} on {tr.get('on')}", f"change {tr.get('change')}"]
    elif k == "song_character":
        bits += [f"moods {x.get('moods')}", f"valence {x.get('valence')}", f"arousal {x.get('arousal')}"]
    if m:
        bits.append(f"[e {m.get('energy')} bpm {m.get('bpm')} {m.get('clock_gate')}]")
    w = d.get("why")
    return " · ".join(b for b in bits if b and b != "None") + (f"  — {w}" if w and k != "look_change" else
                                                               (f"  — {w}" if w else ""))


def sheet(tl: dict, lyrics: bool = False) -> str:
    s, ref = tl.get("song") or {}, tl.get("reference") or {}
    out = [f"# {s.get('title')} — {s.get('artist')}  ({s.get('genre')}, {s.get('year')}; {_mmss(s.get('duration_s'))})",
           f"mode {tl.get('mode')} · {json.dumps(tl.get('run') or {})}",
           f"reference: {ref.get('source', 'ad_eval reference')}"]
    if ref.get("climax"):
        out.append(f"reference climax {_mmss(ref['climax'][0])}–{_mmss(ref['climax'][1])}")
    dec = tl.get("decisions") or []
    kinds: dict[str, int] = {}
    for d in dec:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    dur = s.get("duration_s") or (dec[-1]["t_s"] if dec else 0) or 1
    out.append(f"decisions: {kinds}; look changes/min {kinds.get('look_change', 0) / (float(dur) / 60):.2f}")
    sc = tl.get("scores")
    if sc:
        out.append(f"scores: {json.dumps(sc)}")
    out.append("")
    rows: list[tuple[float, int, str]] = []
    for x in ref.get("sections") or []:
        rows.append((float(x["start_s"]), 0, f"{_mmss(x['start_s'])}  ===== {x.get('kind') or ''} {x.get('label') or ''} (reference)"))
    for x in (tl.get("show") or {}).get("sections") or []:
        rows.append((float(x["t_s"]), 1, f"{_mmss(x['t_s'])}  ----- show heard: section {x.get('letter')} {x.get('kind') or ''}"))
    if lyrics:
        for x in tl.get("lyrics") or []:
            rows.append((x["t_s"], 2, f"{_mmss(x['t_s'])}      ♪ {x['text']}"))
    for d in dec:
        rows.append((d["t_s"], 3, f"{_mmss(d['t_s'])}  {d['kind']:<14} {_brief(d)}"))
    rows.sort(key=lambda r: (r[0], r[1]))
    out += [r[2] for r in rows]
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("sessions")
    a.add_argument("--hours", type=float, default=48.0)
    b = sub.add_parser("build")
    b.add_argument("song")
    b.add_argument("--play", type=int, default=1)
    b.add_argument("--out")
    b.add_argument("--all", action="store_true", help="keep the noisy kinds (light_effect, flow, ensemble...)")
    b.add_argument("--sheet", action="store_true", help="print the review sheet too")
    b.add_argument("--lyrics", action="store_true")
    c = sub.add_parser("sheet")
    c.add_argument("file")
    c.add_argument("--lyrics", action="store_true")
    n = sub.add_parser("note", help="record a verdict on a moment (the owner's, or a finding the owner agreed)")
    n.add_argument("song", help="song id")
    n.add_argument("t_s", type=float, help="song seconds")
    n.add_argument("verdict", choices=["good", "bad"])
    n.add_argument("dimension", help="cadence|contour|shape|tone|restraint|coherence|transition|ending|other")
    n.add_argument("words", help="what was wrong or right, in plain words")
    n.add_argument("--want", default="", help="what should have happened")
    n.add_argument("--cause", default="", help="the code or config behind it, if known")
    n.add_argument("--by", default="owner")
    sub.add_parser("notes")
    args = ap.parse_args(argv)
    if args.cmd == "note":
        rec = {"at": round(time.time(), 1), "song": args.song, "t_s": args.t_s, "verdict": args.verdict,
               "dimension": args.dimension, "words": args.words, "want": args.want, "cause": args.cause,
               "by": args.by, "sha": _sha()}
        FEEDBACK.parent.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"noted in {FEEDBACK}")
        return 0
    if args.cmd == "notes":
        lib = songs()
        for r in _jsonl([FEEDBACK]):
            print(f"{r['verdict']:<4} {r['dimension']:<10} {_title(lib.get(r['song'], {}))[:40]:<40} {_mmss(r['t_s'])}  "
                  f"{r['words']}{'  → ' + r['want'] if r.get('want') else ''}")
        return 0
    if args.cmd == "sessions":
        lib = songs()
        cut = time.time() - args.hours * 3600
        for p in plays(director_log(), lib):
            if p["t1"] < cut:
                continue
            print(f"{time.strftime('%a %d %b %H:%M', time.localtime(p['t0']))}  {_mmss(p['t1'] - p['t0'])}  "
                  f"{p['song_id'] or '?':<12} {p['title'] or '?'}{'' if p['exact'] else '  (approx times)'}")
        return 0
    if args.cmd == "build":
        tl = build(args.song, args.play, args.all)
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(tl, indent=1, default=str))
            print(f"wrote {args.out}", file=sys.stderr)
        if args.sheet or not args.out:
            print(sheet(tl, args.lyrics))
        return 0
    tl = json.loads(pathlib.Path(args.file).read_text())
    print(sheet(tl, args.lyrics))
    return 0


if __name__ == "__main__":
    sys.exit(main())
