#!/usr/bin/env python3
"""Scores one timeline (replay.py's, with its ``reference``) against the song's answer key.

    python tools/room/ad_eval/score.py <timeline.json> [...]

Every metric is per song, 0..1. ``overall`` = ``goals`` x ``gate`` (weights.yaml):
  goals   the weighted mean of what the show should do with the song: be on its beat, follow its loudness, change on
          its boundaries, have its form, and end with it
  gate    restraint, as a limit and not a goal: each of design.py's parts over its bar counts 1 (under the limit
          earns nothing more: one static look all song kept every limit and said nothing), under its bar in proportion
What the score can and can't tell apart is itself tested: controls.py scores the real show against deliberately wrong
ones (the wrong song, late, backwards, its looks dealt to the wrong sections, one static look, a loudness box), and a
run whose score can't tell them apart says so (scorecard.json ``controls``). SCORE_VERSION changes when ``overall``
means something else: runs are compared only within one version. Numbers in the formulas (tolerances, scales) are
starting points, all in weights.yaml.
  beat        F-measure of the show's pulses vs the reference beats (± tol_beat_s); lock_s; octave share
  snap        the lag (s) at the peak cross-correlation of lamp level (and screen energy) vs the reference energy
  contour     Spearman of lamp level (and screen energy) vs reference energy, both smoothed
  sections    share of reference boundaries with a look change within ± one beat, each boundary counted by how big
              a change in the music it is (form.music_change: the key cuts songs finely, and a boundary between two
              parts that sound alike is hardly one); median lateness; strays a minute
  form, contrast, proportion, arc, peak, release
              what the lights showed in each section, and across each lift in the music, against the song's shape
              (form.py). Of the whole set (run.py): ``character``, different songs get different shows
  ending      how far the lamps' fall to dark is from the song's end
  design      restraint (design.py: one palette a song, a handful of looks, rare flourishes, heads that hold still):
              the gate
Reported, not in ``overall``: snap (it scored a show played backwards 0.94), climax (the big moves' timing; peak
reads the high point from the lights), coherence (1.0 for every show).
"""
from __future__ import annotations

import json
import math
import pathlib
import sys
from typing import Any, Optional

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402
import design as design_mod  # noqa: E402
import form as form_mod  # noqa: E402

#: What ``overall`` means. 2 (27 Nov 2026): goals x gate; form, contrast, proportion, arc and peak in; snap, climax,
#: coherence and design out of the mean; sections weighted by the size of each boundary. 3: moments. 4 (28 Nov): a
#: limit with nothing to measure counts as met (Dancing Queen live: no look change all song left the gate as the one
#: moving scene's stillness, 0, and the song 0 whatever its goals; nightly eval issue 1). The ending: dark is where
#: the lamps go down and stay down, and on a fade anywhere in its second half is on time (issue 5).
SCORE_VERSION = 4
LIGHT_KINDS = ("light_scene", "lineup", "light_program")
DEFAULT_WEIGHTS = {
    "weights": {"beat": 1.0, "contour": 1.0, "sections": 1.0, "form": 1.0, "contrast": 1.0, "proportion": 1.0,
                "arc": 1.0, "peak": 0.5, "release": 1.0, "ending": 0.5},
    "set_weights": {"character": 0.15},
    "gates": {"palette": 0.5, "kit": 0.75, "flourish": 0.75, "stillness": 0.75},
    "design": {**design_mod.DEFAULTS, "still_by_liveliness": [0.85, 0.65, 0.45, 0.25, 0.1]},
    "form": {},
    "controls": {"margin": 0.05, "p": 0.05},
    "tol_beat_s": 0.07, "smooth_s": 2.0, "snap_smooth_s": 0.5, "max_lag_s": 5.0, "snap_scale_s": 2.0, "snap_min_corr": 0.2,
    "stray_scale_per_min": 2.0, "ending_scale_s": 5.0, "dark_share": 0.3, "case_window_s": 10.0,
}


def weights(path: pathlib.Path = common.WEIGHTS_YAML) -> dict:
    w = json.loads(json.dumps(DEFAULT_WEIGHTS))
    if path.is_file():
        import yaml
        got = yaml.safe_load(path.read_text()) or {}
        for k, v in got.items():
            if isinstance(v, dict) and isinstance(w.get(k), dict):
                w[k].update(v)
            else:
                w[k] = v
    return w


# -- helpers --------------------------------------------------------------------------------------------------------
def _grid(v: list, hop: float, t0: float, tt: np.ndarray) -> np.ndarray:
    if not v:
        return np.zeros_like(tt)
    t = t0 + np.arange(len(v)) * hop
    return np.interp(tt, t, np.asarray(v, float))


def _smooth(x: np.ndarray, s: float, hop: float) -> np.ndarray:
    k = max(1, int(round(s / hop)))
    return np.convolve(x, np.ones(k) / k, mode="same") if len(x) >= k else x


def _rank(x: np.ndarray) -> np.ndarray:
    o = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[o] = np.arange(len(x))
    return r


def spearman(a: np.ndarray, b: np.ndarray) -> Optional[float]:
    if len(a) < 10 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return None
    return float(np.corrcoef(_rank(a), _rank(b))[0, 1])


def best_lag(ref: np.ndarray, out: np.ndarray, hop: float, max_lag_s: float) -> tuple[Optional[float], float]:
    """(lag s where corr(ref(t), out(t + lag)) peaks over 0..max_lag, that corr)."""
    if len(ref) < 20 or np.std(out) < 1e-9 or np.std(ref) < 1e-9:
        return None, 0.0
    a = (ref - ref.mean()) / ref.std()
    b = (out - out.mean()) / out.std()
    best, lag = -2.0, 0
    for L in range(0, int(max_lag_s / hop) + 1):
        x, y = a[:len(a) - L], b[L:]
        if len(x) < 20:
            break
        c = float(np.mean(x * y))
        if c > best:
            best, lag = c, L
    return round(lag * hop, 3), round(best, 3)


def beat_f(show: list, ref: list, tol: float, t_lo: float = -math.inf, t_hi: float = math.inf) -> dict:
    s = np.asarray([b for b in show if t_lo <= b <= t_hi])
    r = np.asarray([b for b in ref if t_lo <= b <= t_hi])
    if len(r) == 0:
        return {"f": None, "precision": None, "recall": None}
    if len(s) == 0:
        return {"f": 0.0, "precision": 0.0, "recall": 0.0}
    hit_s = np.array([np.min(np.abs(r - x)) <= tol for x in s])
    hit_r = np.array([np.min(np.abs(s - x)) <= tol for x in r])
    p, rc = float(hit_s.mean()), float(hit_r.mean())
    return {"f": round(2 * p * rc / (p + rc), 3) if p + rc > 0 else 0.0, "precision": round(p, 3),
            "recall": round(rc, 3)}


def look_changes(tl: dict) -> list[float]:
    out = []
    for d in tl.get("decisions") or []:
        if d.get("kind") == "look_change" and (d.get("t_s") or 0) >= -0.5:
            out.append(float(d.get("land_s") if d.get("land_s") is not None else d["t_s"]))
    return sorted(out)


def boundaries(ref: dict, skip_s: float = 2.0) -> list[float]:
    return [float(s["start_s"]) for s in ref.get("sections") or [] if float(s["start_s"]) > skip_s]


def ibi(ref: dict) -> float:
    b = ref.get("beats") or []
    return float(np.median(np.diff(b))) if len(b) > 2 else 0.5


# -- the metrics ----------------------------------------------------------------------------------------------------
def moments_measure(moments: Any, lamp: np.ndarray, tt: np.ndarray, W: dict) -> dict:
    """``moments``: the share of the song's chosen stabs and stops the lamps answered. A stab: the lamps up by
    moments.stab_rise within 0.3 s of it, against the 1.5 s before. A stop: the lamps down by moments.stop_drop
    (a share of where they were) through the gap. Judged on every stop and the song's moments.stabs strongest stabs
    (the show plays a few of a song's stabs, not all: its idiom says how many). None for a song with none."""
    w = W.get("moments") or {}
    rise, drop, n_stab = float(w.get("stab_rise", 0.06)), float(w.get("stop_drop", 0.5)), int(w.get("stabs", 2))
    if not isinstance(moments, list) or not len(tt):
        return {"moments": None, "moments_n": 0}
    hop = float(tt[1] - tt[0]) if len(tt) > 1 else 0.1

    def mean(a: float, b: float) -> Optional[float]:
        i, j = int(round(a / hop)), int(round(b / hop))
        i, j = max(0, i), min(len(lamp), max(j, i + 1))
        return float(np.mean(lamp[i:j])) if j > i else None

    def peak(a: float, b: float) -> Optional[float]:
        i, j = max(0, int(round(a / hop))), min(len(lamp), int(round(b / hop)) + 1)
        return float(np.max(lamp[i:j])) if j > i else None
    floor = float(w.get("min_salience", 0.5))
    stabs = sorted((x for x in moments if x.get("kind") == "stab" and float(x.get("salience") or 0) >= floor),
                   key=lambda x: -float(x.get("salience") or 0))[:n_stab]
    stops = [x for x in moments if x.get("kind") == "stop" and float(x.get("salience") or 0) >= floor]
    got = []
    for x in stabs:
        t = float(x["t"])
        before, at = mean(t - 1.5, t - 0.3), peak(t, t + 0.3)
        if before is not None and at is not None:
            got.append(1.0 if at - before >= rise else 0.0)
    for x in stops:
        t, e = float(x["t"]), float(x.get("end", x["t"]))
        before, gap = mean(t - 1.5, t - 0.1), mean(t + 0.05, max(t + 0.1, e - 0.05))
        if before is not None and gap is not None and before > 0.05:
            got.append(1.0 if gap <= (1.0 - drop) * before else 0.0)
    return {"moments": round(float(np.mean(got)), 3) if got else None, "moments_n": len(got)}


def score(tl: dict, ref: dict, W: Optional[dict] = None) -> dict:
    W = W or weights()
    dur = float(ref.get("end_s") or (tl.get("song") or {}).get("duration_s") or 0.0)
    out = tl.get("outputs") or {}
    hop = 0.1
    tt = np.arange(0.0, max(hop, dur), hop)
    e_ref = _grid(ref["energy"]["v"], ref["energy"]["hop_s"], 0.0, tt)
    o_hop, o_t0 = float(out.get("hop_s") or 0.05), float(out.get("t0_s") or 0.0)
    lamp = _grid(out.get("lamp_level") or [], o_hop, o_t0, tt)
    scr = _grid(out.get("screen_energy") or [], o_hop, o_t0, tt)
    m: dict[str, Any] = {}
    # beat
    ref_beats = ref.get("beats") or []
    show_beats = (tl.get("show") or {}).get("beats") or []
    t_lo = ref_beats[0] if ref_beats else 0.0
    bf = beat_f(show_beats, ref_beats, float(W["tol_beat_s"]), t_lo, dur)
    m["beat"] = bf["f"]
    m["beat_precision"], m["beat_recall"] = bf["precision"], bf["recall"]
    lk = (tl.get("show") or {}).get("lock_s")
    m["lock_s"] = round(float(lk) - t_lo, 2) if lk is not None else None
    sb = np.asarray([b for b in show_beats if t_lo <= b <= dur])
    if len(sb) > 2 and len(ref_beats) > 2:
        r = np.diff(sb) / ibi(ref)
        m["octave_share"] = round(float(np.mean((np.abs(r - 2) < 0.2) | (np.abs(r - 0.5) < 0.1))), 3)
    else:
        m["octave_share"] = None
    # snap and contour
    sm, cs = float(W["snap_smooth_s"]), float(W["smooth_s"])
    lag_l, c_l = best_lag(_smooth(e_ref, sm, hop), _smooth(lamp, sm, hop), hop, float(W["max_lag_s"]))
    lag_s, c_s = best_lag(_smooth(e_ref, sm, hop), _smooth(scr, sm, hop), hop, float(W["max_lag_s"]))
    m["lag_lamp_s"], m["lag_screen_s"] = lag_l, lag_s
    m["xcorr_lamp"], m["xcorr_screen"] = c_l, c_s
    mc = float(W["snap_min_corr"])                       # a lag only means something where the curves agree at all
    lags = [x for x, c in ((lag_l, c_l), (lag_s, c_s)) if x is not None and c >= mc]
    m["snap"] = round(float(np.mean([max(0.0, 1.0 - x / float(W["snap_scale_s"])) for x in lags])), 3) if lags \
        else None
    sp_l = spearman(_smooth(e_ref, cs, hop), _smooth(lamp, cs, hop))
    sp_s = spearman(_smooth(e_ref, cs, hop), _smooth(scr, cs, hop))
    m["contour_lamp"], m["contour_screen"] = (round(sp_l, 3) if sp_l is not None else None,
                                              round(sp_s, 3) if sp_s is not None else None)
    cv = [x for x in (sp_l, sp_s) if x is not None]
    m["contour"] = round(max(0.0, float(np.mean(cv))), 3) if cv else None
    # sections
    bnd = boundaries(ref)
    ch = look_changes(tl)
    beat = ibi(ref)
    if bnd:
        hit = [any(abs(c - b) <= beat for c in ch) for b in bnd]
        size = dict(zip([float(x["start_s"]) for x in (ref.get("sections") or [])[1:]], form_mod.music_change(ref, W)))
        wt = [max(0.05, float(size.get(b) if size.get(b) is not None else 1.0)) for b in bnd]
        late = []
        for b in bnd:
            after = [c - b for c in ch if -beat <= c - b <= 16.0]
            if after:
                late.append(min(after, key=abs))
        stray = [c for c in ch if 0 <= c <= dur and not any(abs(c - b) <= beat for b in bnd)]
        per_min = len(stray) / max(1.0, dur / 60.0)
        m["section_hit_plain"] = round(float(np.mean(hit)), 3)
        m["section_hit"] = round(float(np.dot(hit, wt) / sum(wt)), 3)
        m["section_late_s"] = round(float(np.median(late)), 2) if late else None
        m["strays_per_min"] = round(per_min, 2)
        m["sections"] = round(m["section_hit"] / (1.0 + per_min / float(W["stray_scale_per_min"])), 3)
    else:
        m["section_hit"] = m["section_late_s"] = m["sections"] = None
        m["strays_per_min"] = round(len(ch) / max(1.0, dur / 60.0), 2)
    m["changes_per_min"] = round(len([c for c in ch if 0 <= c <= dur]) / max(1.0, dur / 60.0), 2)
    # the big moment
    c0, c1 = ref.get("climax") or [None, None]
    big = []
    for d in tl.get("decisions") or []:
        det = d.get("detail") or {}
        if (d.get("kind") == "move" and det.get("size") == "big") or \
                (d.get("kind") == "token" and det.get("decision") == "spent"):
            big.append(float(d.get("land_s") if d.get("land_s") is not None else d["t_s"]))
    if c0 is not None:
        inside = [b for b in big if c0 - beat <= b <= c1 + beat]
        m["big_moments"] = len(big)
        m["big_in_climax"] = len(inside)
        m["climax"] = round((1.0 if inside else 0.0) * (len(inside) / len(big) if big else 0.0), 3)
    else:
        m["climax"] = None
    # the ending
    end = float(ref.get("end_s") or dur)
    ls = _smooth(lamp, 1.0, hop)
    med = float(np.median(ls[(tt > 0) & (tt < end)])) if np.any((tt > 0) & (tt < end)) else 0.0
    t_after = float(ref.get("last_hit_s") or end) - 5.0
    tail_t = np.arange(0.0, end + 6.0, hop)
    tail = _grid(out.get("lamp_level") or [], o_hop, o_t0, tail_t)
    tail = _smooth(tail, 1.0, hop)
    # dark: from where the lamps go down and stay down (a dip on the last hit, with the song going on, is not the
    # end: Hallelujah's lamps dipped at 223 s, its last strong hit, and the song sings on to 269 s)
    lit = [t for t, v in zip(tail_t, tail) if t >= t_after and v >= float(W["dark_share"]) * max(1e-6, med)]
    after = [t for t in tail_t if t >= t_after and (not lit or t > lit[-1])]
    # on a record's fade the end is a stretch, not a moment: dark anywhere from half way down the fade to its end
    # is on time (Dancing Queen's 14.5 s fade: dark 5 s before the last of it scored 0)
    fade = next((x for x in ref.get("moments") or () if isinstance(x, dict) and x.get("kind") == "end"
                 and x.get("style") == "fade" and isinstance(x.get("end"), (int, float))), None)
    lo = hi = end
    if fade is not None:
        lo, hi = 0.5 * (float(fade["t"]) + float(fade["end"])), max(end, float(fade["end"]))
    if med > 0.02:
        t_dark = after[0] if after else end + 6.0
        err = t_dark - lo if t_dark < lo else (t_dark - hi if t_dark > hi else 0.0)
        m["dark_err_s"] = round(err, 2)
        m["ending"] = round(max(0.0, 1.0 - abs(err) / float(W["ending_scale_s"])), 3)
    else:
        m["dark_err_s"] = m["ending"] = None
    # moments: do the lights answer the song's stabs and stops (utils/song_moments.py)?
    m.update(moments_measure(ref.get("moments"), lamp, tt, W))
    # coherence (approximate: decisions in the same tick)
    dec = tl.get("decisions") or []
    lt = [float(d["t_s"]) for d in dec if d.get("kind") in LIGHT_KINDS]
    lc = [float(d["t_s"]) for d in dec if d.get("kind") == "look_change" and d["t_s"] >= 0]
    m["coherence"] = round(float(np.mean([any(abs(a - b) <= 0.15 for b in lt) for a in lc])), 3) if lc else None
    # design: restraint (the gate); form: what the lights showed against the song's shape
    m.update(design_mod.design(tl, W=W))
    m.update(form_mod.measure(tl, ref, W))
    m["key_agree"] = (ref.get("sim") or {}).get("agree")
    # how still the heads should be depends on the song (keys.yaml's liveliness): a hushed one nearly always, a
    # club track hardly; design.py's one bar for every song called a ballad and an anthem alike
    lv = (ref.get("character") or {}).get("liveliness")
    bars = (W.get("design") or {}).get("still_by_liveliness")
    if lv is not None and bars and m.get("still_share") is not None:
        bar = float(bars[max(0, min(len(bars) - 1, int(round(float(lv)))))])
        m["stillness"] = round(min(1.0, m["still_share"] / bar), 3) if bar > 0 else 1.0
    # overall: the goals, held to the limits
    ws = W["weights"]
    parts = [(float(ws[k]), m[k]) for k in ws if m.get(k) is not None and float(ws[k]) > 0.0]
    m["goals"] = round(sum(w * v for w, v in parts) / sum(w for w, _ in parts), 3) if parts else None
    g = [min(1.0, float(m[k]) / float(bar)) if m.get(k) is not None else 1.0
         for k, bar in (W.get("gates") or {}).items() if float(bar) > 0.0]
    m["gate"] = round(float(np.mean(g)), 3) if g else 1.0
    m["overall"] = round(m["goals"] * m["gate"], 3) if m["goals"] is not None else None
    m["score_version"] = SCORE_VERSION
    return m


# -- owner notes as cases -------------------------------------------------------------------------------------------
def case_value(tl: dict, ref: dict, note: dict, W: Optional[dict] = None) -> Optional[float]:
    """The note's dimension measured over t_s ± case_window_s (None when that dimension has no measure)."""
    W = W or weights()
    t, w = float(note.get("t_s") or 0.0), float(W["case_window_s"])
    lo, hi = t - w, t + w
    dim = note.get("dimension")
    out = tl.get("outputs") or {}
    hop = 0.1
    if dim == "cadence":
        return beat_f((tl.get("show") or {}).get("beats") or [], ref.get("beats") or [], float(W["tol_beat_s"]),
                      lo, hi)["f"]
    if dim == "contour":
        tt = np.arange(max(0.0, lo), hi, hop)
        e = _grid(ref["energy"]["v"], ref["energy"]["hop_s"], 0.0, tt)
        lamp = _grid(out.get("lamp_level") or [], float(out.get("hop_s") or 0.05), float(out.get("t0_s") or 0.0), tt)
        v = spearman(_smooth(e, 1.0, hop), _smooth(lamp, 1.0, hop))
        return round(v, 3) if v is not None else None
    if dim in ("shape", "restraint", "transition"):
        bnd = [b for b in boundaries(ref) if lo <= b <= hi]
        ch = [c for c in look_changes(tl) if lo <= c <= hi]
        beat = ibi(ref)
        if dim == "restraint":
            return round(len(ch) / (2 * w / 60.0), 2)
        if not bnd:
            return 1.0 if not ch else 0.0                  # no boundary here: any change is a stray
        return round(float(np.mean([any(abs(c - b) <= beat for c in ch) for b in bnd])), 3)
    if dim == "coherence":
        dec = tl.get("decisions") or []
        lt = [float(d["t_s"]) for d in dec if d.get("kind") in LIGHT_KINDS]
        lc = [float(d["t_s"]) for d in dec if d.get("kind") == "look_change" and lo <= d["t_s"] <= hi]
        return round(float(np.mean([any(abs(a - b) <= 0.15 for b in lt) for a in lc])), 3) if lc else None
    if dim == "ending":
        return score(tl, ref, W).get("ending")
    return None


def main() -> int:
    W = weights()
    for p in sys.argv[1:]:
        tl = json.loads(pathlib.Path(p).read_text())
        ref = tl.get("reference")
        if ref is None:
            import reference
            ref = reference.load(tl["song"]["id"])
        print(pathlib.Path(p).name, json.dumps(score(tl, ref, W)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
