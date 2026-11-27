#!/usr/bin/env python3
"""Known-answer controls: can the score tell the real show from deliberately wrong ones?

A score is only worth steering by if it falls when the show is made worse in a way anyone would see. Owner, 27 Nov
2026: "I worry your eval scoring isn't very insightful ... how can we increase confidence that high scores mean
lighting that is artistically successful". The first scorer could not: the real show's looks dealt to the wrong
sections scored the same (0.612 and 0.612), and a loudness box scored 0.90. So every run now scores, beside the real
show of each song, these made from it:

  wrong song   the same show against other songs' answer keys (three of them, averaged)
  late         everything eight bars late (still on the beat)
  backwards    the show played end to start
  shuffled     each section given another section's lights (the decisions and their times kept): right times, wrong
               content
  static       one look held all song: the lights' middle frame, the first decisions only

and one benchmark that is not made from the show:

  loudness box one scene, one colour, every light on, brightness = the song's loudness, a look change on every
               boundary of the key. It is built from the answer key, so it follows the music as well as anything can;
               it has nothing to say. Reported (``beats_box``): a show worth having outscores it.

``check`` -> {pass, margins {name: {real, wrong, margin, wins, p, ok}}, beats_box, table {name: {metric: mean}}}:
the score passes when the real show outscores every wrong show by weights.yaml ``controls.margin`` on average, and in
more of the songs than chance would give (a sign test over the songs: ``p`` at most ``controls.p``). A scorecard whose
controls fail is not one to steer the show by (.claude/skills/ad-improve).

What a pass does not say: that the show is good. A measure on which the real show is at chance (27 Nov: its lights
rose across a lift in the music no more often than the shuffled show's) loses nothing when the show is made wrong;
the unit tests hold each measure to a show that is right (test_ad_eval.py), and the box is the mark to beat.

    python tools/room/ad_eval/controls.py <run dir>
"""
from __future__ import annotations

import copy
import glob
import json
import math
import pathlib
import random
import sys
from typing import Optional

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import form  # noqa: E402
import score as scoremod  # noqa: E402

WRONG = ("wrong song", "late", "backwards", "shuffled", "static")
BOX = "loudness box"
TABLE = ("overall", "goals", "gate", "beat", "contour", "sections", "form", "contrast", "proportion", "arc", "peak",
         "release", "ending")
TKEYS = ("t_s", "land_s")
ONCE = ("look_change", "light_program", "lineup", "light_scene")
NEVER = ("move", "token", "transition")


def _frames(o: dict) -> dict[str, np.ndarray]:
    """The outputs' per-frame arrays (lamp_level ..., and ``shown``'s rows as "shown")."""
    out = {k: np.asarray(v, float) for k, v in o.items() if isinstance(v, list) and v}
    sh = o.get("shown") or {}
    if sh.get("v"):
        out["shown"] = np.asarray(sh["v"], float)
    return out


def _put(o: dict, fr: dict[str, np.ndarray]) -> None:
    for k, a in fr.items():
        if k == "shown":
            o["shown"] = {**o["shown"], "v": np.round(a, 3).tolist()}
        else:
            o[k] = np.round(a, 4).tolist()


def _times(o: dict, n: int) -> np.ndarray:
    return float(o.get("t0_s") or 0.0) + np.arange(n) * float(o.get("hop_s") or 0.05)


def late(tl: dict, dt: float) -> dict:
    t = copy.deepcopy(tl)
    for d in t["decisions"]:
        for k in TKEYS:
            if d.get(k) is not None:
                d[k] = d[k] + dt
    t["outputs"]["t0_s"] = float(t["outputs"].get("t0_s") or 0.0) + dt
    sh = t.get("show") or {}
    sh["beats"] = [b + dt for b in sh.get("beats") or []]
    if sh.get("lock_s") is not None:
        sh["lock_s"] += dt
    return t


def backwards(tl: dict, end: float) -> dict:
    t = copy.deepcopy(tl)
    for d in t["decisions"]:
        for k in TKEYS:
            if d.get(k) is not None:
                d[k] = end - d[k]
    t["decisions"].sort(key=lambda d: d["t_s"])
    o = t["outputs"]
    fr = _frames(o)
    for k, a in fr.items():
        tt = _times(o, len(a))
        hop = float(o.get("hop_s") or 0.05)
        fr[k] = a[np.clip(np.round((end - tt - tt[0]) / hop).astype(int), 0, len(a) - 1)]
    _put(o, fr)
    sh = t.get("show") or {}
    sh["beats"] = sorted(end - b for b in sh.get("beats") or [])
    return t


def shuffled(tl: dict, ref: dict, rng: random.Random) -> dict:
    t = copy.deepcopy(tl)
    secs = [(float(s["start_s"]), float(s["end_s"])) for s in ref.get("sections") or []]
    if len(secs) < 2:
        return t
    src = list(range(len(secs)))
    for _ in range(50):                                          # a dealing in which no section keeps its own
        rng.shuffle(src)
        if all(a != b for a, b in enumerate(src)):
            break
    o = t["outputs"]
    fr = _frames(o)
    for k, a in fr.items():
        tt = _times(o, len(a))
        new = a.copy()
        for i, (a0, a1) in enumerate(secs):
            dst = np.where((tt >= a0) & (tt < a1))[0]
            b0, b1 = secs[src[i]]
            frm = np.where((tt >= b0) & (tt < b1))[0]
            if len(dst) and len(frm):
                new[dst] = a[frm[np.arange(len(dst)) % len(frm)]]
        fr[k] = new
    _put(o, fr)
    return t


def static(tl: dict) -> dict:
    t = copy.deepcopy(tl)
    seen, keep = set(), []
    for d in t["decisions"]:
        if d["kind"] in ONCE:
            if d["kind"] in seen:
                continue
            seen.add(d["kind"])
        elif d["kind"] in NEVER:
            continue
        keep.append(d)
    t["decisions"] = keep
    o = t["outputs"]
    fr = _frames(o)
    _put(o, {k: np.broadcast_to(np.median(a, axis=0), a.shape).copy() for k, a in fr.items()})
    return t


def box(tl: dict, ref: dict) -> dict:
    t = copy.deepcopy(tl)
    o = t["outputs"]
    hop = float(o.get("hop_s") or 0.05)
    n = len(o.get("lamp_level") or [])
    tt = _times(o, n)
    e = ref["energy"]
    lv = np.interp(tt, np.arange(len(e["v"])) * float(e["hop_s"]), np.asarray(e["v"], float))
    end = float(ref["end_s"])
    lv[tt > end] = 0.0
    o["lamp_level"] = np.round(lv, 4).tolist()
    if o.get("screen_energy"):
        o["screen_energy"] = np.round(lv, 4).tolist()
    row = {"lit": 1.0, "heads": 1.0, "r": 0.2, "g": 0.4, "b": 1.0, "tilt": 45.0}
    rows = np.tile([row.get(k, 0.0) for k in form.KEYS], (n, 1))
    rows[:, form.KEYS.index("flicker")] = np.minimum(form.FLICKER_CAP, np.abs(np.diff(lv, prepend=lv[:1])) / hop)
    rows[tt > end] = 0.0
    o["shown"] = {"keys": list(form.KEYS), "v": np.round(rows, 3).tolist()}
    dec = [d for d in t["decisions"] if d["kind"] not in ONCE + NEVER]
    c0 = (ref.get("climax") or [None])[0]
    for s in ref.get("sections") or []:
        ts = float(s["start_s"])
        dec.append({"t_s": ts, "kind": "look_change", "detail": {"palette": "one", "reason": "section"}})
        dec.append({"t_s": ts, "kind": "light_program",
                    "detail": {"groups": {"movers": {"scene": "one", "transition": "cut"}}}})
    if c0 is not None:
        dec.append({"t_s": c0, "kind": "move", "land_s": c0, "detail": {"size": "big", "move": "one"}})
        dec.append({"t_s": c0, "kind": "lineup", "detail": {"plays": ["movers", "lamps"], "into": "climax"}})
    t["decisions"] = sorted(dec, key=lambda d: d["t_s"])
    return t


def check(tls: list[dict], W: Optional[dict] = None, seed: int = 1) -> dict:
    """One mode's timelines (each with its ``reference``) -> the controls' verdict (module docstring)."""
    W = W or scoremod.weights()
    tls = [t for t in tls if (t.get("reference") or {}).get("energy") and (t.get("outputs") or {}).get("lamp_level")]
    n = len(tls)
    out: dict = {"pass": False, "songs": n, "margins": {}, "beats_box": None, "table": {}}
    if n < 4:
        out["why"] = "fewer than four songs: nothing to tell apart"
        return out
    rng = random.Random(seed)
    rows: dict[str, list[dict]] = {k: [] for k in ("the real show", *WRONG, BOX)}
    for i, tl in enumerate(tls):
        ref = tl["reference"]
        end = float(ref["end_s"])
        rows["the real show"].append(scoremod.score(tl, ref, W))
        others = [scoremod.score(tl, tls[(i + j) % n]["reference"], W) for j in (1, 3, 7) if (i + j) % n != i]
        rows["wrong song"].append({k: (float(np.mean([m[k] for m in others if m.get(k) is not None]))
                                       if any(m.get(k) is not None for m in others) else None) for k in TABLE})
        rows["late"].append(scoremod.score(late(tl, 32 * scoremod.ibi(ref)), ref, W))
        rows["backwards"].append(scoremod.score(backwards(tl, end), ref, W))
        rows["shuffled"].append(scoremod.score(shuffled(tl, ref, rng), ref, W))
        rows["static"].append(scoremod.score(static(tl), ref, W))
        rows[BOX].append(scoremod.score(box(tl, ref), ref, W))
    for name, ms in rows.items():
        out["table"][name] = {}
        for k in TABLE:
            v = [m[k] for m in ms if m.get(k) is not None]
            out["table"][name][k] = round(float(np.mean(v)), 3) if v else None
    real = [m["overall"] or 0.0 for m in rows["the real show"]]
    cw = W.get("controls") or {}
    for name in (*WRONG, BOX):
        wrong = [m["overall"] or 0.0 for m in rows[name]]
        d = [a - b for a, b in zip(real, wrong)]
        k = sum(x > 0 for x in d)                               # a sign test: this many wins or more, by chance
        row = {"real": round(float(np.mean(real)), 3), "wrong": round(float(np.mean(wrong)), 3),
               "margin": round(float(np.mean(d)), 3), "wins": round(k / n, 3),
               "p": round(sum(math.comb(n, j) for j in range(k, n + 1)) / 2.0 ** n, 4)}
        if name == BOX:
            out["beats_box"] = bool(row["margin"] > 0.0)
            row["ok"] = out["beats_box"]
        else:
            row["ok"] = bool(row["margin"] >= float(cw.get("margin", 0.05)) and row["p"] <= float(cw.get("p", 0.05)))
        out["margins"][name] = row
    out["pass"] = all(out["margins"][k]["ok"] for k in WRONG)
    return out


def show(res: dict, label: str = "") -> list[str]:
    lines = [f"controls {label}: {'pass' if res.get('pass') else 'FAIL'} ({res.get('songs')} songs)"
             + ("" if res.get("beats_box") is None else f"; the show {'beats' if res['beats_box'] else 'LOSES TO'} "
                                                         f"a loudness box")]
    if res.get("table"):
        lines.append(f"  {'':14s}" + "".join(f"{k[:8]:>9s}" for k in TABLE) + "   margin  wins")
        for name, row in res["table"].items():
            mg = res["margins"].get(name) or {}
            lines.append(f"  {name:14s}" + "".join("        -" if row.get(k) is None else f"{row[k]:9.3f}"
                                                   for k in TABLE)
                         + (f"   {mg['margin']:+.3f}  {mg['wins']:.2f}{'' if mg['ok'] else '  !'}" if mg else ""))
    return lines


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    W = scoremod.weights()
    import reference
    for mode in ("karaoke", "live"):
        tls = []
        for f in sorted(glob.glob(f"{sys.argv[1].rstrip('/')}/*.{mode}.json")):
            tl = json.loads(pathlib.Path(f).read_text())
            tl["reference"] = reference.load(tl["song"]["id"])
            tls.append(tl)
        if tls:
            print("\n".join(show(check(tls, W), mode)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
