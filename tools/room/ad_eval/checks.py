"""Fault checks: pass/fail rules on a replay's transitions, against the song's key (docs/AD_EVAL.md §4).

The score's whole-song averages can't see one fix (a fix touches a few moments; the average moves less than the
draws do) and have not been shown to agree with the owner's eye. A check can: it asks one plain question of one
moment, and a draw of the same code answers it the same way or nearly. The overnight loop keeps a fix only when no
check that passed before fails after, and the fix's own target check passes (.claude/skills/ad-improve/SKILL.md).
Each failure is also a moment for the owner's morning reel (reel.py), which is how a check earns its keep: the
owner's "that's fine" on a flagged moment says the check is wrong, not the show.

The checks (each on the key's times; a beat is the key's median beat, the tolerance max(0.4 s, one beat)):
  lands       a section that arrives (the key's arrive is not none) gets a look change within the tolerance
  stray       a look change lands near a section boundary, a stop or the ending, not mid-section
  peak        the climax is the song's biggest: the lamps' level at least every other section's (less 0.05), and
              its line-up plays at least as many groups as any before it
  dark        the lamps don't sit dark (under 0.03 for 1.5 s) while the song plays, outside its stops and its fade
  stop        a stop (the band drops out) takes the lamps down to half what they were
  ending      where the lamps go dark and stay dark: within 1.5 s of the last hit for a cold or ringing ending;
              for a fade, in its second half and by 2 s after it ends (as score.py's ending, v4)

    ~/.venvs/sm-show/bin/python tools/room/ad_eval/checks.py <run dir> [song id ...]    failures, song by song
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Optional

import numpy as np

HOP = 0.1
DARK_LEVEL, DARK_S = 0.03, 1.5
PEAK_SLACK = 0.05
CHECKS = ("lands", "stray", "peak", "dark", "stop", "ending")


def _grid(out: dict, dur: float) -> np.ndarray:
    v = np.asarray(out.get("lamp_level") or [], dtype=float)
    if not len(v):
        return np.zeros(int(dur / HOP) + 1)
    hop, t0 = float(out.get("hop_s") or 0.05), float(out.get("t0_s") or 0.0)
    tt = np.arange(0.0, dur + 6.0, HOP)
    return np.interp(tt, t0 + np.arange(len(v)) * hop, v, left=v[0], right=v[-1])


def _mean(lamp: np.ndarray, a: float, b: float) -> Optional[float]:
    i, j = max(0, int(round(a / HOP))), min(len(lamp), int(round(b / HOP)))
    return float(np.mean(lamp[i:j])) if j > i else None


def _smooth(x: np.ndarray, s: float) -> np.ndarray:
    n = max(1, int(round(s / HOP)))
    return np.convolve(x, np.ones(n) / n, mode="same") if n > 1 and len(x) > n else x


def _ts(d: dict) -> float:
    return float(d["land_s"] if d.get("land_s") is not None else d["t_s"])


def look_changes(tl: dict) -> list[float]:
    return sorted(_ts(d) for d in tl.get("decisions") or [] if d.get("kind") == "look_change" and d["t_s"] >= -0.5)


def tolerance(ref: dict) -> float:
    b = ref.get("beats") or []
    beat = float(np.median(np.diff(b))) if len(b) > 2 else 0.5
    return max(0.4, beat)


def _clock(t: float) -> str:
    t = max(0.0, t)
    return f"{int(t // 60)}:{t % 60:04.1f}"


def transitions(ref: dict) -> list[dict]:
    """The song's transitions, the moments the owner reviews: each section boundary (after the first 2 s), the
    climax, each stop and the ending. {t_s, what, label}."""
    out = []
    secs = ref.get("sections") or []
    for prev, s in zip(secs, secs[1:]):
        t = float(s["start_s"])
        if t <= 2.0:
            continue
        arr = s.get("arrive")
        lift = f" ({arr})" if arr not in (None, "none") else ""
        out.append({"t_s": t, "what": "section", "arrive": arr,
                    "label": f"{prev.get('kind') or prev.get('label')} → {s.get('kind') or s.get('label')}{lift}"})
    c = ref.get("climax") or [None]
    if isinstance(c[0], (int, float)):
        out.append({"t_s": float(c[0]), "what": "climax", "label": "the climax"})
    for m in ref.get("moments") or []:
        if m.get("kind") == "stop" and float(m.get("salience") or 0) >= 0.5:
            out.append({"t_s": float(m["t"]), "what": "stop", "label": "a stop"})
        elif m.get("kind") == "end":
            out.append({"t_s": float(m["end"] if m.get("style") == "fade" else ref.get("last_hit_s") or m["t"]),
                        "what": "ending", "label": f"the ending ({m.get('style') or 'end'})"})
    return sorted(out, key=lambda x: x["t_s"])


def _lineups(tl: dict) -> list[tuple[float, int, str]]:
    return [(float(d["t_s"]), len((d.get("detail") or {}).get("plays") or ()), str((d.get("detail") or {}).get("lineup")))
            for d in tl.get("decisions") or [] if d.get("kind") == "lineup"]


def run(tl: dict, ref: dict) -> list[dict]:
    """Every check's verdict on this replay: [{check, t_s, ok, why}], in time order."""
    dur = float(ref.get("end_s") or (tl.get("song") or {}).get("duration_s") or 0.0)
    tol = tolerance(ref)
    ch = look_changes(tl)
    lamp = _grid(tl.get("outputs") or {}, dur)
    secs = ref.get("sections") or []
    moments = ref.get("moments") or []
    stops = [m for m in moments if m.get("kind") == "stop" and float(m.get("salience") or 0) >= 0.5]
    end_m = next((m for m in moments if m.get("kind") == "end"), None)
    last_hit = float(ref.get("last_hit_s") or dur)
    out: list[dict] = []

    def put(check: str, t: float, ok: bool, why: str) -> None:
        out.append({"check": check, "t_s": round(t, 2), "ok": bool(ok), "why": why})

    # lands
    for s in secs[1:]:
        t = float(s["start_s"])
        if t <= 2.0 or s.get("arrive") in (None, "none"):
            continue
        near = [c - t for c in ch if abs(c - t) <= 8.0]
        d = min(near, key=abs) if near else None
        if d is not None and abs(d) <= tol:
            put("lands", t, True, f"the {s.get('kind')} lands with a change ({d:+.2f} s)")
        elif d is None:
            put("lands", t, False, f"the {s.get('kind')} at {_clock(t)} arrives with no look change")
        else:
            put("lands", t, False, f"the {s.get('kind')} at {_clock(t)}: its change came {abs(d):.1f} s "
                f"{'early' if d < 0 else 'late'}")
    # stray
    anchors = [float(s["start_s"]) for s in secs] + [float(m["t"]) for m in stops] + [last_hit]
    if end_m is not None:
        anchors.append(float(end_m["t"]))
    for c in ch:
        if c < 2.0 or c > dur - 1.0:
            continue
        if any(abs(c - a) <= tol for a in anchors):
            put("stray", c, True, f"a look change at {_clock(c)}, on a boundary")
            continue
        inside = next((s for s in secs if float(s["start_s"]) <= c < float(s["end_s"])), None)
        what = inside.get("kind") if inside else "section"
        put("stray", c, False, f"a look change at {_clock(c)}, in the middle of the {what}")
    # peak
    c0, c1 = (ref.get("climax") or [None, None]) + [None] * 0
    if isinstance(c0, (int, float)) and isinstance(c1, (int, float)) and c1 > c0:
        top = _mean(lamp, c0 + 0.5, c1)
        others = [(_mean(lamp, float(s["start_s"]) + 0.5, float(s["end_s"])), s) for s in secs
                  if float(s["end_s"]) <= c0 + 0.5 or float(s["start_s"]) >= c1 - 0.5]
        others = [(v, s) for v, s in others if v is not None and float(s["end_s"]) - float(s["start_s"]) >= 4.0]
        lu = _lineups(tl)
        before = [n for t, n, _ in lu if t < c0 - tol]
        at = [(n, name) for t, n, name in lu if t <= c0 + 1.0]
        n_at, name_at = at[-1] if at else (None, None)
        bigger = max(others, key=lambda x: x[0]) if others else None
        why = []
        if top is not None and bigger is not None and top < bigger[0] - PEAK_SLACK:
            why.append(f"the lamps at {top:.2f}, under the {bigger[1].get('kind')} at "
                       f"{_clock(float(bigger[1]['start_s']))} ({bigger[0]:.2f})")
        if n_at is not None and before and n_at < max(before):
            why.append(f"its line-up {name_at} plays {n_at} group{'s' if n_at != 1 else ''}, fewer than "
                       f"earlier ({max(before)})")
        put("peak", float(c0), not why, "the climax at " + _clock(float(c0)) + (": " + "; ".join(why) if why
                                                                                  else " is the biggest"))
    # dark
    ref_beats = ref.get("beats") or [0.0]
    excl = [(float(m["t"]) - 0.3, float(m.get("end", m["t"])) + 0.8) for m in moments
            if m.get("kind") in ("stop", "end")]
    ls = _smooth(lamp, 0.5)
    t_lo, t_hi = float(ref_beats[0]) + 1.0, last_hit - 1.0
    run_start = None
    flagged = 0
    for i, v in enumerate(ls):
        t = i * HOP
        dark = t_lo <= t <= t_hi and v < DARK_LEVEL and not any(a <= t <= b for a, b in excl)
        if dark and run_start is None:
            run_start = t
        if (not dark or t >= t_hi) and run_start is not None:
            if t - run_start >= DARK_S and flagged < 5:
                put("dark", run_start, False, f"the lamps dark {t - run_start:.1f} s from {_clock(run_start)} "
                    f"while the song plays")
                flagged += 1
            run_start = None
    if not flagged:
        put("dark", t_lo, True, "the lamps never sit dark while the song plays")
    # stop
    for m in stops:
        t, e = float(m["t"]), float(m.get("end", m["t"]))
        before, gap = _mean(lamp, t - 1.5, t - 0.1), _mean(lamp, t + 0.05, max(t + 0.1, e - 0.05))
        if before is None or gap is None or before <= 0.05:
            continue
        ok = gap <= 0.5 * before
        put("stop", t, ok, f"the stop at {_clock(t)}: the lamps {before:.2f} → {gap:.2f}"
            + ("" if ok else ", not down by half"))
    # ending
    if end_m is not None and lamp.size:
        med = float(np.median(ls[int(t_lo / HOP):int(t_hi / HOP)])) if t_hi > t_lo + 5 else float(np.median(ls))
        thr = max(0.02, 0.3 * med)
        style = end_m.get("style") or "cold"
        # where the lamps go dark and stay dark (a dip on the last hit that comes back up is not the end: score v4)
        lo = max(0, int((last_hit - 5.0) / HOP))
        t_dark = None
        for i in range(len(ls) - 1, lo - 1, -1):
            if ls[i] >= thr:
                t_dark = (i + 1) * HOP if i + 1 < len(ls) else None
                break
        else:
            t_dark = lo * HOP
        if style == "fade":
            f0, f1 = float(end_m["t"]), float(end_m.get("end", end_m["t"]))
            soon = f0 + 0.5 * (f1 - f0)                         # dark anywhere in the fade's second half
            ok = t_dark is not None and soon <= t_dark <= f1 + 2.0
            if t_dark is None:
                why = f"the fade ends at {_clock(f1)} and the lamps never go dark"
            elif t_dark < soon:
                why = f"the lamps dark at {_clock(t_dark)}, {f1 - t_dark:.1f} s before the fade is out"
            else:
                why = f"the fade goes out with the lamps (dark at {_clock(t_dark)}, the fade out at {_clock(f1)})"
            put("ending", f1, ok, why)
        else:
            ok = t_dark is not None and last_hit - 0.5 <= t_dark <= last_hit + 1.5
            if t_dark is None:
                why = f"the last hit at {_clock(last_hit)} and the lamps never go dark"
            elif ok:
                why = f"dark {t_dark - last_hit:+.1f} s from the last hit"
            else:
                why = f"the lamps dark {abs(t_dark - last_hit):.1f} s {'before' if t_dark < last_hit else 'after'} " \
                      f"the last hit ({style} ending)"
            put("ending", last_hit, ok, why)
    return sorted(out, key=lambda x: x["t_s"])


def summary(results: list[dict]) -> dict[str, dict[str, int]]:
    s: dict[str, dict[str, int]] = {}
    for r in results:
        x = s.setdefault(r["check"], {"pass": 0, "fail": 0})
        x["pass" if r["ok"] else "fail"] += 1
    return s


def failed(results: list[dict]) -> set[tuple[str, float]]:
    """A replay's failures as (check, time rounded to 1 s): compared across two runs of the same song and seed."""
    return {(r["check"], round(r["t_s"])) for r in results if not r["ok"]}


def compare(before: Optional[dict], after: dict) -> tuple[list[tuple[str, str, int]], list[tuple[str, str, int]]]:
    """Two scorecards' faults, replay by replay (the same song, mode and draw in both): (new, fixed), each
    [(replay, check, t_s)]. A fault within 2 s of one at the other run is the same fault, moved."""
    fa = ((before or {}).get("checks") or {}).get("faults") or {}
    fb = (after.get("checks") or {}).get("faults") or {}
    new, fixed = [], []
    for key in sorted(set(fa) & set(fb)):
        a = [(c, int(t)) for c, t in fa[key]]
        b = [(c, int(t)) for c, t in fb[key]]
        new += [(key, c, t) for c, t in b if not any(c == c2 and abs(t - t2) <= 2 for c2, t2 in a)]
        fixed += [(key, c, t) for c, t in a if not any(c == c2 and abs(t - t2) <= 2 for c2, t2 in b)]
    return new, fixed


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run_dir = pathlib.Path(sys.argv[1])
    want = set(sys.argv[2:])
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import reference
    tot: dict[str, dict[str, int]] = {}
    for p in sorted(run_dir.glob("*.karaoke.json")):
        sid = p.name.split(".")[0]
        if want and sid not in want:
            continue
        tl = json.loads(p.read_text())
        ref: Any = tl.get("reference") or reference.load(sid)
        res = run(tl, ref)
        for k, v in summary(res).items():
            t = tot.setdefault(k, {"pass": 0, "fail": 0})
            t["pass"] += v["pass"]
            t["fail"] += v["fail"]
        bad = [r for r in res if not r["ok"]]
        print(f"{sid} {(tl.get('song') or {}).get('title')}: {len(res) - len(bad)} pass, {len(bad)} fail")
        for r in bad:
            print(f"   {r['check']:7s} {r['why']}")
    print("all:", "  ".join(f"{k} {v['pass']}/{v['pass'] + v['fail']}" for k, v in sorted(tot.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
