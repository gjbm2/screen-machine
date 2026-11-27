#!/usr/bin/env python3
"""Form measures: does the show have the song's shape, in what the lights actually did?

score.py's first metrics read *when* the show changed and how bright the lamps were. A show with one scene and one
colour whose brightness is the song's loudness scored 0.90 on them, and dealing the real show's looks to the wrong
sections changed nothing (controls.py; owner, 27 Nov 2026: "I worry your eval scoring isn't very insightful"). These
read *what* was shown, from the look engine's own frames (replay.py records ``Describer`` rows 20 times a second):

  lit       the share of the lights that are on             heads    the share of the moving heads that are on
  move      how fast the lit heads travel (deg/s)           flicker  how fast levels change (per second)
  strobe    the share of lit lights strobing                r g b    the colour on stage (weighted by level)
  variety   how far the lit lights' colours are from that   tilt     where the lit heads point, up or down (deg)
  spread    how far apart the lit heads' pans are (deg)     gobo     the share of lit heads with a pattern in

and, with the lamps' level, make one vector for each of the song's sections (its later part: a look that arrives a
little late is still that section's look). Four questions of the song's shape (docs/LIGHT_DESIGN_NOTES.md):

  form        sections that sound alike look alike, and ones that sound different look different: the rank
              correlation, over every pair of sections, of how alike they sound (the reference's ``sim``) and how
              alike they look
  contrast    ... by enough to see, in more than one way: in each family of means (brightness, which lights are
              on, movement, colour, texture), how much further apart the unlike-sounding pairs look than the
              like-sounding ones; the second biggest of those, over CONTRAST_BAR (a show that only turns one knob,
              a sound-to-light box, has the order right and one way of showing it: 0)
  proportion  a big change in the music gets a big change in the look, a small one a small one: the rank
              correlation, over the boundaries, of the size of each
  arc         the look grows with the song: the rank correlation of each section's loudness and the size of its
              look (level, lights on, movement, flicker)
  peak        the biggest look is kept for the high point: the share of the other sections whose look is smaller
              than the climax's
  release     where the music lifts across a boundary (an annotated key: where it says the music arrives at a bigger
              section; else louder in the two bars after it than the two before, by LIFT_MIN), the lights lift on
              the line: the bar after is bigger than the bar before by LIFT_BAR
              (owner, 27 Nov: "in a build / release moment ... lights don't seem to follow the pacing / mood / tension
              build & release"). ``wind``, reported: how far the lights pull in over the bar before those lines.

and one of the whole song set (``character``; run.py): songs of different kinds get shows that look different: the
rank correlation, over every pair of songs, of how far apart they are in kind (keys.yaml's ``liveliness``, judged by
ear; for songs without one, the reference's own measures of the sound) and how far apart their shows' looks are (owner, 27 Nov: "a serious ballad
should just feel different from an ABBA track").

    python tools/room/ad_eval/form.py <timeline.json> [...]

Pure functions over a timeline and its reference. The scales (what counts as a full-size difference in each
descriptor) and CONTRAST_BAR are starting points in weights.yaml's ``form`` block: what they must do is tell the real
show from the wrong ones (controls.py), and agree with the owner's eye.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys
from typing import Any, Mapping, Optional

import numpy as np

KEYS = ("lit", "heads", "move", "flicker", "strobe", "r", "g", "b", "variety", "tilt", "spread", "gobo")
#: The means a look is made of. Two looks' distance is the mean over these of how far apart they are in each, so a
#: show that only turns one knob (a sound-to-light box: brightness) can never look more than a fifth different.
FAMILIES = {"brightness": ("level",), "extent": ("lit", "heads"), "movement": ("move", "tilt", "spread"),
            "colour": ("r", "g", "b", "variety"), "texture": ("flicker", "strobe", "gobo")}
LIT = 0.05                  # a light under this level isn't on
ENV_S = 0.6                 # level_env: the lamps' highest level within this (about a beat)
FLICKER_CAP = 20.0          # one light's level can't count for more than this a second (a strobe's edge)
DEFAULTS = {
    # a difference this big in a descriptor is a full-size one
    "scales": {"level": 0.5, "lit": 1.0, "heads": 1.0, "move": 60.0, "flicker": 2.0, "strobe": 1.0, "r": 1.0, "g": 1.0,
               "b": 1.0, "variety": 0.5, "tilt": 60.0, "spread": 60.0, "gobo": 1.0},
    # what makes a look big, each as a share of this. level_env is how bright the lamps GET (their level's highest
    # over ENV_S around each moment): a chorus pulsing on the beat is bigger than a steady glow of the same mean.
    "size": {"level_env": 1.0, "lit": 1.0, "move": 60.0, "flicker": 2.0},
    "settle": 0.4,          # a section's look is read after this share of it has passed
    "min_section_s": 6.0,   # a shorter section isn't judged
    "contrast_bar": 0.15,   # unlike-sounding sections this much further apart than like-sounding ones, in the
                            # second most telling family of means, scores 1
    "means_min": 0.08,      # a family of means in which sections differ by this on average is one the show uses
    "energy_scale": 0.3,    # a jump this big in a section's loudness (0..1) is a full-size change in the music
    "peak_eps": 0.02,       # a look within this of the climax's size isn't smaller
    "lift_min": 0.08,       # release: the music lifts across a boundary by at least this (loudness 0..1)
    "lift_bar": 0.1,        # release: the lights' size rising by this across the line scores 1
    "character_keys": ("bpm", "punch", "range_db", "bright", "low"),
    "min_pairs": 10, "min_points": 5,
}


class Describer:
    """One row of KEYS for each of the look engine's frames (its ``fixtures``), given in time order."""

    def __init__(self) -> None:
        self.prev: dict[str, tuple] = {}
        self.t: Optional[float] = None

    def row(self, fixtures: Mapping[str, Any], t: float) -> list[float]:
        dt = (t - self.t) if self.t is not None else 0.0
        n = n_heads = lit_n = lit_heads = strobe = gobo = 0
        flick = move = 0.0
        cols: list[tuple[float, float, float, float]] = []
        pans: list[float] = []
        tilts: list[float] = []
        cur: dict[str, tuple] = {}
        for fid, f in (fixtures or {}).items():
            if not isinstance(f, Mapping):
                continue
            try:
                lv = float(f.get("intensity") or 0.0)
            except (TypeError, ValueError):
                continue
            n += 1
            a = f.get("attrs") if isinstance(f.get("attrs"), Mapping) else {}
            pan, tilt = a.get("pan"), a.get("tilt")
            head = isinstance(pan, (int, float)) and isinstance(tilt, (int, float))
            n_heads += 1 if head else 0
            p = self.prev.get(fid)
            if p is not None and dt > 0.0:
                flick += min(FLICKER_CAP, abs(lv - p[0]) / dt)
            if lv >= LIT:
                lit_n += 1
                c = f.get("colour")
                if isinstance(c, (list, tuple)) and len(c) >= 3:
                    cols.append((lv, float(c[0]), float(c[1]), float(c[2])))
                sh = a.get("shutter")
                if isinstance(sh, Mapping) and sh.get("mode") not in (None, "open", "closed"):
                    strobe += 1
                if head:
                    lit_heads += 1
                    pans.append(float(pan))
                    tilts.append(float(tilt))
                    if p is not None and p[1] is not None and p[0] >= LIT and dt > 0.0:
                        move += math.hypot(pan - p[1], tilt - p[2]) / dt
                    if a.get("gobo") not in (None, 0, False, "open"):
                        gobo += 1
            cur[fid] = (lv, float(pan) if head else None, float(tilt) if head else None)
        self.prev, self.t = cur, t
        r = g = b = variety = 0.0
        w = sum(c[0] for c in cols)
        if w > 0.0:
            r, g, b = (sum(c[0] * c[k] for c in cols) / w for k in (1, 2, 3))
            variety = sum(c[0] * math.sqrt((c[1] - r) ** 2 + (c[2] - g) ** 2 + (c[3] - b) ** 2) for c in cols) / w
        return [round(x, 3) for x in (
            lit_n / n if n else 0.0, lit_heads / n_heads if n_heads else 0.0, move / lit_heads if lit_heads else 0.0,
            flick / n if n else 0.0, strobe / lit_n if lit_n else 0.0, r, g, b, variety,
            float(np.mean(tilts)) if tilts else 0.0, float(np.std(pans)) if len(pans) > 1 else 0.0,
            gobo / lit_heads if lit_heads else 0.0)]


# -- what each section showed -------------------------------------------------------------------------------------
def _w(W: Optional[dict]) -> dict:
    w = dict(DEFAULTS)
    got = (W or {}).get("form") or {}
    w.update({k: v for k, v in got.items() if k not in ("scales", "size")})
    w["scales"] = {**DEFAULTS["scales"], **(got.get("scales") or {})}
    w["size"] = {**DEFAULTS["size"], **(got.get("size") or {})}
    return w


def shown(tl: dict) -> Optional[tuple[np.ndarray, np.ndarray, list[str]]]:
    """(frame times, one row a frame, the columns' names): the lamps' level, the Describer's KEYS, then level_env. None for a
    timeline made before replay.py recorded them."""
    out = tl.get("outputs") or {}
    sh = out.get("shown") or {}
    v, lamp = sh.get("v") or [], out.get("lamp_level") or []
    if not v or not lamp:
        return None
    n = min(len(v), len(lamp))
    hop = float(out.get("hop_s") or 0.05)
    lv = np.asarray(lamp[:n], float)
    k = max(1, int(round(ENV_S / hop)))                      # the level's highest over ENV_S, centred
    pad = np.pad(lv, (k // 2, k - 1 - k // 2), mode="edge")
    env = np.max(np.lib.stride_tricks.sliding_window_view(pad, k), axis=1) if k > 1 and n >= k else lv
    A = np.column_stack([lv, np.asarray(v[:n], float), env[:n]])
    tt = float(out.get("t0_s") or 0.0) + np.arange(n) * hop
    return tt, A, ["level", *list(sh.get("keys") or KEYS), "level_env"]


def section_looks(tl: dict, ref: dict, W: Optional[dict] = None) -> list[dict]:
    """One entry for each reference section long enough to judge: {i (its place in ref.sections), start_s, end_s,
    energy, v {descriptor: mean over its later part}, size}."""
    w = _w(W)
    got = shown(tl)
    if got is None:
        return []
    tt, A, cols = got
    end = float(ref.get("end_s") or (tt[-1] if len(tt) else 0.0))
    ci = {k: j for j, k in enumerate(cols)}
    out = []
    for i, s in enumerate(ref.get("sections") or []):
        a, b = float(s["start_s"]), min(float(s["end_s"]), end)
        if b - a < float(w["min_section_s"]):
            continue
        m = (tt >= a + float(w["settle"]) * (b - a)) & (tt < b)
        if m.sum() < 10:
            continue
        X = A[m]
        v = {k: float(X[:, j].mean()) for k, j in ci.items()}
        hw = X[:, ci["heads"]] if "heads" in ci else None        # where the heads point counts while they are lit
        if hw is not None and hw.sum() > 1e-6:
            for k in ("tilt", "spread", "move", "gobo"):
                if k in ci:
                    v[k] = float((X[:, ci[k]] * hw).sum() / hw.sum())
        lw = X[:, ci["lit"]] if "lit" in ci else None            # and the colour while anything is
        if lw is not None and lw.sum() > 1e-6:
            for k in ("r", "g", "b", "variety"):
                if k in ci:
                    v[k] = float((X[:, ci[k]] * lw).sum() / lw.sum())
        size = float(np.mean([min(1.0, v.get(k, 0.0) / float(s)) for k, s in w["size"].items()]))
        out.append({"i": i, "start_s": a, "end_s": b, "energy": float(s.get("energy") or 0.0),
                    "v": {k: round(x, 4) for k, x in v.items()}, "size": round(size, 4)})
    return out


def song_look(tl: dict, ref: dict, W: Optional[dict] = None) -> Optional[dict]:
    """What the lights showed over the whole song: the mean of each descriptor, and its size."""
    w = _w(W)
    got = shown(tl)
    if got is None:
        return None
    tt, A, cols = got
    m = (tt >= 0.0) & (tt < float(ref.get("end_s") or (tt[-1] if len(tt) else 0.0)))
    if m.sum() < 10:
        return None
    v = {k: round(float(A[m][:, j].mean()), 4) for j, k in enumerate(cols)}
    v["size"] = round(float(np.mean([min(1.0, v.get(k, 0.0) / float(s)) for k, s in w["size"].items()])), 4)
    return v


def character(rows: list[tuple[Optional[dict], Optional[dict]]], W: Optional[dict] = None) -> dict:
    """Over a set of songs, each (its reference's ``character``, its ``song_look``): do songs that sound different
    get shows that look different? {character 0..1 or None, character_rho, songs}."""
    w = _w(W)
    keys = [k for k in w["character_keys"]]
    judged = [(c, lk) for c, lk in rows if isinstance(c, dict) and isinstance(lk, dict)
              and isinstance(c.get("liveliness"), (int, float))]
    if len(judged) >= 6:                                 # the songs' kinds as judged by ear (keys.yaml): the better
        ok, keys = judged, ["liveliness"]                # word on how unlike two songs are than five measures
    else:
        ok = [(c, lk) for c, lk in rows if isinstance(c, dict) and isinstance(lk, dict)
              and all(isinstance(c.get(k), (int, float)) for k in keys)]
    out: dict[str, Any] = {"character": None, "character_rho": None, "songs": len(ok), "character_by": keys}
    if len(ok) < 6:
        return out
    C = np.array([[float(c[k]) for k in keys] for c, _lk in ok])
    C = (C - C.mean(axis=0)) / (C.std(axis=0) + 1e-9)
    dm, dv = [], []
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            dm.append(float(np.linalg.norm(C[i] - C[j])))
            d = distances(ok[i][1], ok[j][1], w["scales"])       # how busy and big the shows are, not their hue:
            dv.append(sum(v for f, v in d.items() if f != "colour") / (len(d) - 1))   # a palette is a song's own
    rho = spearman(dm, dv)
    out["character_rho"] = round(rho, 3) if rho is not None else None
    out["character"] = round(max(0.0, rho), 3) if rho is not None else 0.0
    return out


def distances(u: Mapping[str, float], v: Mapping[str, float], scales: Mapping[str, float]) -> dict[str, float]:
    """How different two looks are in each of the FAMILIES of means, 0 (the same) .. 1 (a full-size difference)."""
    out = {}
    for fam, keys in FAMILIES.items():
        d = [min(1.0, abs(u.get(k, 0.0) - v.get(k, 0.0)) / float(scales[k])) ** 2 for k in keys if k in scales]
        out[fam] = math.sqrt(sum(d) / len(d)) if d else 0.0
    return out


def distance(u: Mapping[str, float], v: Mapping[str, float], scales: Mapping[str, float]) -> float:
    """How different two looks are, 0 (the same) .. 1 (a full-size difference in every family of means)."""
    d = distances(u, v, scales)
    return sum(d.values()) / len(d)


def sound_alike(ref: dict) -> Optional[np.ndarray]:
    """How alike each pair of the reference's sections sound, 0..1: its ``sim`` (the audio's, with the lyrics' where
    both sections are sung); for a reference without one, 1 for the same letter and 0 otherwise."""
    secs = ref.get("sections") or []
    n = len(secs)
    if n < 2:
        return None
    sim = ref.get("sim") or {}
    A = sim.get("audio")
    if not A or len(A) != n:
        return np.array([[1.0 if secs[i].get("label") == secs[j].get("label") else 0.0 for j in range(n)]
                         for i in range(n)])
    M = np.asarray(A, float)
    off = M[~np.eye(n, dtype=bool)]
    lo, hi = float(off.min()), float(off.max())
    M = np.clip((M - lo) / (hi - lo), 0.0, 1.0) if hi - lo > 1e-9 else np.zeros_like(M)
    L = sim.get("lyrics")
    if L and len(L) == n:
        for i in range(n):
            for j in range(n):
                if L[i][j] is not None:
                    M[i, j] = 0.5 * (M[i, j] + float(L[i][j]))
    np.fill_diagonal(M, 1.0)
    return M


def music_change(ref: dict, W: Optional[dict] = None) -> list[Optional[float]]:
    """For each boundary between the reference's sections (the start of section 1, 2 ...): how big a change in the
    music it is, 0..1: how unlike the two sections sound, and the jump in loudness."""
    w = _w(W)
    secs = ref.get("sections") or []
    M = sound_alike(ref)
    out: list[Optional[float]] = []
    for i in range(len(secs) - 1):
        de = min(1.0, abs(float(secs[i].get("energy") or 0.0) - float(secs[i + 1].get("energy") or 0.0))
                 / float(w["energy_scale"]))
        out.append(round(0.5 * ((1.0 - float(M[i, i + 1])) if M is not None else 1.0) + 0.5 * de, 4))
    return out


def _rank(x: np.ndarray) -> np.ndarray:
    """Ranks, ties sharing their mean rank."""
    o = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[o] = np.arange(len(x), dtype=float)
    xs = x[o]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            r[o[i:j + 1]] = 0.5 * (i + j)
        i = j + 1
    return r


def spearman(a, b) -> Optional[float]:
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return None
    return float(np.corrcoef(_rank(a), _rank(b))[0, 1])


# -- the measures ---------------------------------------------------------------------------------------------------
def measure(tl: dict, ref: dict, W: Optional[dict] = None) -> dict:
    """The form measures of one timeline against its reference (each 0..1 or None, with the numbers behind it)."""
    w = _w(W)
    m: dict[str, Any] = {k: None for k in ("form", "contrast", "proportion", "arc", "peak", "release")}
    looks = section_looks(tl, ref, W)
    m["sections_judged"] = len(looks)
    m["look"] = song_look(tl, ref, W)
    m.update(_release(tl, ref, w))
    if len(looks) < 3:
        return m
    sc = w["scales"]
    n = len(looks)
    D = np.zeros((n, n))
    fam = {f: [] for f in FAMILIES}
    for i in range(n):
        for j in range(i + 1, n):
            d = distances(looks[i]["v"], looks[j]["v"], sc)
            D[i, j] = D[j, i] = sum(d.values()) / len(d)
            for f, x in d.items():
                fam[f].append(x)
    # which means the show used to tell its sections apart (reported: a one-knob show uses one)
    m["means"] = {f: round(float(np.mean(x)), 3) for f, x in fam.items() if x}
    m["means_used"] = sum(1 for x in m["means"].values() if x >= float(w["means_min"]))
    M_all = sound_alike(ref)
    idx = [lk["i"] for lk in looks]
    # form and contrast: over every pair of sections
    if M_all is not None:
        pm, pd = [], []
        for i in range(n):
            for j in range(i + 1, n):
                pm.append(float(M_all[idx[i], idx[j]]))
                pd.append(float(D[i, j]))
        if len(pm) >= int(w["min_pairs"]):
            rho = spearman(pm, [-d for d in pd])
            m["form_rho"] = round(rho, 3) if rho is not None else None
            m["form"] = round(max(0.0, rho), 3) if rho is not None else 0.0
            order = np.argsort(pm, kind="mergesort")
            k = max(1, len(pm) // 3)
            unlike = float(np.mean([pd[q] for q in order[:k]]))
            alike = float(np.mean([pd[q] for q in order[-k:]]))
            m["look_gap_unlike"], m["look_gap_alike"] = round(unlike, 3), round(alike, 3)
            gaps = {f: float(np.mean([x[q] for q in order[:k]]) - np.mean([x[q] for q in order[-k:]]))
                    for f, x in fam.items() if len(x) == len(pm)}
            m["contrast_by"] = {f: round(g, 3) for f, g in gaps.items()}
            second = sorted(gaps.values(), reverse=True)[1] if len(gaps) > 1 else 0.0
            m["contrast"] = round(min(1.0, max(0.0, second) / float(w["contrast_bar"])), 3)
    # proportion: over the boundaries between sections that are both judged
    ch = music_change(ref, W)
    cm, cv = [], []
    for a, b in zip(range(n - 1), range(1, n)):
        if idx[b] == idx[a] + 1 and ch[idx[a]] is not None:
            cm.append(ch[idx[a]])
            cv.append(float(D[a, b]))
    if len(cm) >= int(w["min_points"]):
        rho = spearman(cm, cv)
        m["proportion_rho"] = round(rho, 3) if rho is not None else None
        m["proportion"] = round(max(0.0, rho), 3) if rho is not None else 0.0
    # arc and peak: the size of each section's look
    sizes = [lk["size"] for lk in looks]
    if n >= int(w["min_points"]):
        rho = spearman([lk["energy"] for lk in looks], sizes)
        m["arc_rho"] = round(rho, 3) if rho is not None else None
        m["arc"] = round(max(0.0, rho), 3) if rho is not None else 0.0
    c0, c1 = ref.get("climax") or [None, None]
    if c0 is not None:
        over = [max(0.0, min(lk["end_s"], c1) - max(lk["start_s"], c0)) for lk in looks]
        if max(over) > 0.0:
            k = int(np.argmax(over))
            others = [s for q, s in enumerate(sizes) if q != k]
            m["peak"] = round(float(np.mean([s < sizes[k] - float(w["peak_eps"]) for s in others])), 3)
            m["peak_size"], m["size_max"] = sizes[k], max(sizes)
    return m


def arrivals(ref: dict, w: dict) -> list[dict]:
    """The reference's boundaries where the music lifts: [{t_s, bar_s, lift}] (louder in the two bars after than the
    two before, by lift_min)."""
    e = ref.get("energy") or {}
    v, hop = np.asarray(e.get("v") or [], float), float(e.get("hop_s") or 0.1)
    db = ref.get("downbeats") or []
    bt = ref.get("beats") or []
    bar = float(np.median(np.diff(db))) if len(db) > 2 else (4.0 * float(np.median(np.diff(bt))) if len(bt) > 2 else 2.0)
    out = []
    secs = ref.get("sections") or []
    if any("arrive" in s for s in secs):                 # an annotated key says where the music arrives, and how
        for a, b in zip(secs, secs[1:]):
            rise = float(b.get("size") or 0.0) - float(a.get("size") or 0.0)
            if b.get("arrive") in ("swell", "bump", "snap") and rise > 0.0 and bar > 0.0:
                out.append({"t_s": float(b["start_s"]), "bar_s": bar, "lift": round(max(0.05, rise), 4),
                            "kind": b["arrive"]})
        return out
    if len(v) < 10 or bar <= 0.0:
        return out

    def mean(t0: float, t1: float) -> Optional[float]:
        i0, i1 = max(0, int(t0 / hop)), min(len(v), int(t1 / hop))
        return float(v[i0:i1].mean()) if i1 - i0 >= 3 else None
    for s in (ref.get("sections") or [])[1:]:
        b = float(s["start_s"])
        before, after = mean(b - 2 * bar, b), mean(b, b + 2 * bar)
        if before is not None and after is not None and after - before >= float(w["lift_min"]):
            out.append({"t_s": b, "bar_s": bar, "lift": round(after - before, 4)})
    return out


def _release(tl: dict, ref: dict, w: dict) -> dict:
    got = shown(tl)
    arr = arrivals(ref, w)
    out: dict[str, Any] = {"release": None, "wind": None, "arrivals": len(arr)}
    if got is None or not arr:
        return out
    tt, A, cols = got
    ci = {k: j for j, k in enumerate(cols)}
    size = np.mean([np.minimum(1.0, A[:, ci[k]] / float(s)) for k, s in w["size"].items() if k in ci], axis=0)

    def mean(t0: float, t1: float) -> Optional[float]:
        m = (tt >= t0) & (tt < t1)
        return float(size[m].mean()) if m.sum() >= 3 else None
    num = den = 0.0
    rises, winds = [], []
    for a in arr:
        b, bar = a["t_s"], a["bar_s"]
        before, after, earlier = mean(b - bar, b), mean(b, b + bar), mean(b - 3 * bar, b - bar)
        if before is None or after is None:
            continue
        rises.append(after - before)
        if earlier is not None:
            winds.append(earlier - before)
        num += a["lift"] * min(1.0, max(0.0, after - before) / float(w["lift_bar"]))
        den += a["lift"]
    if den > 0.0:
        out["release"] = round(num / den, 3)
        out["release_rise"] = round(float(np.mean(rises)), 3)
        out["wind"] = round(float(np.mean(winds)), 3) if winds else None
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for p in sys.argv[1:]:
        tl = json.loads(pathlib.Path(p).read_text())
        ref = tl.get("reference") or {}
        m = measure(tl, ref)
        title = (tl.get("song") or {}).get("title") or pathlib.Path(p).stem
        print(f"{title}: form {m['form']}  contrast {m['contrast']}  proportion {m['proportion']}  arc {m['arc']}  "
              f"peak {m['peak']}  release {m['release']} ({m['arrivals']} arrivals, wind {m['wind']})  "
              f"({m['sections_judged']} sections judged)")
        for lk in section_looks(tl, ref):
            s = (ref.get("sections") or [])[lk["i"]]
            v = lk["v"]
            print(f"  {lk['start_s']:6.1f}-{lk['end_s']:6.1f} {s.get('label', '?')} {str(s.get('kind'))[:5]:5s} "
                  f"loud {lk['energy']:.2f}  size {lk['size']:.2f}  level {v.get('level', 0):.2f} lit {v.get('lit', 0):.2f} "
                  f"heads {v.get('heads', 0):.2f} move {v.get('move', 0):5.1f} flicker {v.get('flicker', 0):.2f} "
                  f"rgb {v.get('r', 0):.2f} {v.get('g', 0):.2f} {v.get('b', 0):.2f} tilt {v.get('tilt', 0):5.1f} "
                  f"gobo {v.get('gobo', 0):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
