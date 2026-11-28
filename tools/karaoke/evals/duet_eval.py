"""How right are a duet's parts? Each line's part (routes/karaoke/duet.py's verdict, duet.json) against an answer key
made from part-marked lyric sources (duet_keys.json), with every error put down to a cause. Owner, 28 Nov: "voice
assignment in duets is pretty patchy ... can we improve systematically?". Nothing measured it against the truth: the
detector's own figure ("the AI's lines agree with the sound for 76%") is agreement with one of its own inputs.

    ~/.venvs/sm-show/bin/python tools/karaoke/evals/duet_eval.py [--redecide] [--songs ID ...] [-v]

  --redecide   score duet.decide run again from the saved AI answer and voices (nothing written): for a change to
               decide, before any song is touched
  -v           every wrong line, with its cause

The key (duet_keys.json): {song id: {"voices": [first, second], "parts": [per line: a singer's name | both | split |
backing | unknown], "sources": [...], "agree": share the two annotators agreed on}}. Names become A and B as the
verdict's ``voices`` order them (A: voices[0]). Only lines the key knows are judged (not unknown, not backing).

What is reported, per song and overall:
  right      the shown part is the key's (a "both" line shown as both; a solo line shown as its singer)
  wrong      shown as the other singer, or as both when one sang, or one when both did
  unshown    the verdict left it plain (no part): not wrong, but not helped either
  ai         the AI's own call, scored the same way (how good the prior is)
  pitch      a line given to whichever voice's median pitch it is nearer (how far pitch alone gets)
and each wrong line's cause, first that fits:
  split      the key says the line is shared within it: a line can't be right as one part
  names      the song's A and B are swapped (it scores better swapped): a naming error, not a hearing one
  ai         the AI said this part and the verdict believed it
  sound      the verdict went against the AI on the sound and was wrong
  short      a line of fewer than 3 words: too little to hear
  both       a both/solo confusion
  other
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Optional

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
KEYS = pathlib.Path(__file__).with_name("duet_keys.json")
SONGS = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "karaoke" / "songs"


def norm(part: Any, voices: list[str]) -> Optional[str]:
    """A key's or verdict's part as A | B | both | split | backing | None (unknown or unshown)."""
    if part is None:
        return None
    p = str(part).strip()
    if p in ("A", "B", "both", "split", "backing"):
        return p
    low = p.lower()
    if low in ("unknown", "", "none"):
        return None
    for i, v in enumerate(voices[:2]):
        if v and (low == v.lower() or low in v.lower() or v.lower().split()[0] == low.split()[0]):
            return "AB"[i]
    return None


def score(key_parts: list[Optional[str]], shown: list[Optional[str]]) -> dict:
    judged = [(k, s) for k, s in zip(key_parts, shown) if k in ("A", "B", "both")]
    right = sum(1 for k, s in judged if s == k)
    wrong = sum(1 for k, s in judged if s is not None and s != k)
    return {"judged": len(judged), "right": right, "wrong": wrong, "unshown": len(judged) - right - wrong}


def swap(p: Optional[str]) -> Optional[str]:
    return {"A": "B", "B": "A"}.get(p or "", p)


def pitch_parts(heard: list, key_parts: list[Optional[str]]) -> list[Optional[str]]:
    """A line to whichever voice's median pitch (over the key's own solo lines: the best pitch alone could do) is
    nearer: an upper bound on a pitch-only split."""
    mid = {}
    for v in ("A", "B"):
        xs = [float(h["midi"]) for h, k in zip(heard, key_parts) if k == v and h and h.get("midi") is not None]
        mid[v] = float(np.median(xs)) if xs else None
    if None in mid.values():
        return [None] * len(heard)
    return [None if not h or h.get("midi") is None else
            ("A" if abs(float(h["midi"]) - mid["A"]) <= abs(float(h["midi"]) - mid["B"]) else "B") for h in heard]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keys", type=pathlib.Path, default=KEYS)
    ap.add_argument("--songs", nargs="*")
    ap.add_argument("--redecide", action="store_true")
    ap.add_argument("-v", action="store_true")
    a = ap.parse_args()
    keys = json.loads(a.keys.read_text())
    tot = {"final": [0, 0, 0, 0], "ai": [0, 0, 0, 0], "pitch": [0, 0, 0, 0]}
    causes: dict[str, int] = {}
    print(f"{'song':32s} {'judged':>6s} {'right':>6s} {'wrong':>6s} {'plain':>6s} {'ai right':>8s} {'pitch':>6s}  causes")
    for sid, k in keys.items():
        if a.songs and sid not in a.songs:
            continue
        d = SONGS / sid
        try:
            verdict = json.loads((d / "duet.json").read_text())
            lines = json.loads((d / "lyrics.json").read_text())["lines"]
        except (OSError, ValueError):
            print(f"{sid}: no verdict or lyrics")
            continue
        try:
            heard = (json.loads((d / "voices.json").read_text()) or {}).get("lines") or []
        except (OSError, ValueError):
            heard = []
        if a.redecide:
            from routes.karaoke import duet
            ai = verdict.get("ai") or {}
            prior = {"duet": bool(ai.get("duet")), "voices": ai.get("voices") or [],
                     "parts": ai.get("parts") or [None] * len(lines),
                     "echoes": {int(i): e for i, e in (ai.get("echoes") or {}).items() if str(i).isdigit()}}
            texts = [str(x.get("text") or "") for x in lines]
            verdict = {**duet.decide(len(lines), prior, heard, [duet.text_tag(t, prior["voices"]) for t in texts],
                                     texts), "ai": ai}
        vnames = [str(v.get("name") or "") for v in verdict.get("voices") or []]
        if len(k["parts"]) != len(lines):
            print(f"{sid}: the key has {len(k['parts'])} lines, the lyrics {len(lines)}: made for other lyrics")
            continue
        kp = [norm(p, vnames) for p in k["parts"]]
        fin = [norm(p, vnames) for p in verdict.get("parts") or [None] * len(lines)]
        aip = [norm(p, vnames) for p in (verdict.get("ai") or {}).get("parts") or [None] * len(lines)]
        pp = pitch_parts(heard, kp) if len(heard) == len(lines) else [None] * len(lines)
        sf, sa, sp = score(kp, fin), score(kp, aip), score(kp, pp)
        swapped = score(kp, [swap(x) for x in fin])["right"] > sf["right"] + 2
        song_causes: dict[str, int] = {}
        for i, (kk, s) in enumerate(zip(kp, fin)):
            if kk not in ("A", "B", "both", "split") or s is None or s == kk:
                continue
            words = len(str(lines[i].get("text") or "").split())
            c = ("split" if kk == "split" else "names" if swapped else "ai" if aip[i] == s
                 else "sound" if aip[i] is not None and aip[i] != s else "short" if words < 3
                 else "both" if "both" in (kk, s) else "other")
            song_causes[c] = song_causes.get(c, 0) + 1
            if a.v:
                print(f"    {i:3d} {lines[i]['start']:6.1f}s key {kk:5s} shown {s:5s} ai {aip[i]!s:5s} "
                      f"pitch {pp[i]!s:5s} [{c}] {lines[i]['text'][:50]}")
        for c, n in song_causes.items():
            causes[c] = causes.get(c, 0) + n
        for name, s in (("final", sf), ("ai", sa), ("pitch", sp)):
            for j, f in enumerate(("judged", "right", "wrong", "unshown")):
                tot[name][j] += s[f]
        title = str(verdict.get("title") or k.get("title") or sid)[:32]
        print(f"{title:32s} {sf['judged']:6d} {sf['right']:6d} {sf['wrong']:6d} {sf['unshown']:6d} "
              f"{sa['right']:8d} {sp['right']:6d}  {song_causes}{'  SWAPPED' if swapped else ''}")
    def pct(x: list) -> str:
        return f"{100.0 * x[1] / max(1, x[0]):.0f}% right, {100.0 * x[2] / max(1, x[0]):.0f}% wrong, " \
               f"{100.0 * x[3] / max(1, x[0]):.0f}% plain"
    print(f"\nshown:  {pct(tot['final'])}  (of {tot['final'][0]} lines the key knows)")
    print(f"AI:     {pct(tot['ai'])}")
    print(f"pitch:  {pct(tot['pitch'])}  (nearest voice's median pitch: a ceiling for pitch alone)")
    print(f"causes of the wrong lines: {dict(sorted(causes.items(), key=lambda kv: -kv[1]))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
