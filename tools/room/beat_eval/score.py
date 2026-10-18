#!/usr/bin/env python3
"""Score run_stack.py's replays against beat annotations: would the room's lights have flashed on the beat?

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/score.py RESULTS... [--csv scores.csv] [--summary summary.csv]
      [--tracks auto|on|off]

RESULTS are result JSONs or directories of them (run_batch.py's results dir). Each result names its ground truth
(``beats``: "<time_s>\\t<pos>" lines, pos 1 = a downbeat; only the time is read). Light: no flock needed.

Per track, twice: from 0 s (``_0``) and with the first 5 s left out (``_5``, mir_eval's convention), over the part
that was replayed. "Flashes" are run_stack's causal integer-beat crossings of the published clock line; "gated" are
those with gate == "beat" (what beat-locked consumers fire on).
  f, cmlt, amlt, p    mir_eval F-measure (70 ms), CMLt, AMLt and P-score of all flashes (the line, whatever the gate)
  f_gated             F-measure of the gated flashes only: a beat the gate held back counts as missed
  hit25, hit25_gated  share of annotated beats with a flash (a gated flash) within +-25 ms
  hit70_gated         share of annotated beats with a gated flash within +-70 ms (run_stack's "gated within 70 ms")
  offset_ms, offset_iqr_ms  the median (and interquartile range) of flash - annotated beat over the flashes within
                      70 ms of one: a steady offset is latency (the clock's lag, the annotations' own lag, a room
                      model's delay), not tracking; hit25 suffers from it, F (70 ms) mostly doesn't
  cover_beat          share of annotated beats while the published gate was "beat"
  cover_gated         ... while it was "beat" or "dmx"
  prec_beat           share of gated flashes within 70 ms of an annotated beat (only flashes inside the annotated
                      span, half a beat either side, are judged here and in wrong_lock)
  prec_beat_dh        the same, allowing double/half: a flash at about twice the annotated tempo may also land on
                      the annotated off-beat (the midpoint); at half tempo every flash is on a beat anyway. The same
                      tempo in anti-phase still counts as wrong.
  wrong_lock          share of gated time (each gated flash weighs its period) whose trailing 4-bar (16-beat) window
                      of gated flashes has prec_beat_dh under 50 %; wrong_lock_strict uses prec_beat
  t_lock, t_lock_dh   seconds from the first annotated beat to the first gated flash that starts 4 gated flashes in a
                      row on annotated beats (strict, or double/half allowed); empty if it never locks. Always
                      over the whole replay from the music's start, so the same in both ranges
  acc1, acc2          the published bpm (its time-weighted median over the range) within 4 % of the annotations'
                      (60 / median IBI); acc2 also at x2, x1/2, x3, x1/3
  acc1_time, acc2_time  share of the range (from the first annotated beat) with the published bpm within 4 % of the
                      local annotated tempo (acc2: octave errors allowed); no grid counts as wrong
Aggregates: the mean per track (t_lock: the median over tracks that locked, and the share that did), overall, per
dataset and per dataset/genre. The summary table is printed for both ranges, after a table per track (up to 30
tracks, or ``--tracks on``). Each row also carries the result's ``stack`` id (run_stack.py): a NOTE says when the
results mix stacks (show_service changed between the runs).
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import os
import pathlib
import sys
from typing import Optional

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):   # one BLAS thread: the host renders 4K
    os.environ.setdefault(_k, "1")

import numpy as np  # noqa: E402

try:
    import mir_eval
except ImportError:                                   # pragma: no cover
    sys.exit("score.py needs mir_eval: run it with ~/.venvs/sm-roundtrip/bin/python")

TOL = 0.07
TOL25 = 0.025
SKIPS = (0.0, 5.0)
WINDOW_BEATS = 16
LOCK_RUN = 4
ACC_TOL = 0.04
NAN = float("nan")
KEYS = ("f", "cmlt", "amlt", "p", "f_gated", "hit25", "hit25_gated", "hit70_gated", "offset_ms", "offset_iqr_ms",
        "cover_beat", "cover_gated", "prec_beat", "prec_beat_dh", "wrong_lock", "wrong_lock_strict", "t_lock",
        "t_lock_dh", "acc1", "acc2", "acc1_time", "acc2_time")
TABLE = (("n", "n", "{:d}"), ("F", "f", "{:.3f}"), ("CMLt", "cmlt", "{:.3f}"), ("AMLt", "amlt", "{:.3f}"),
         ("P", "p", "{:.3f}"), ("Fgate", "f_gated", "{:.3f}"), ("hit25", "hit25", "{:.3f}"),
         ("gHit70", "hit70_gated", "{:.3f}"), ("offMs", "offset_ms", "{:+.0f}"),
         ("covB", "cover_beat", "{:.3f}"), ("covBD", "cover_gated", "{:.3f}"), ("precB", "prec_beat", "{:.3f}"),
         ("precDH", "prec_beat_dh", "{:.3f}"), ("wrong", "wrong_lock", "{:.3f}"), ("tLock", "t_lock", "{:.1f}"),
         ("locked", "locked", "{:.2f}"), ("acc1", "acc1", "{:.2f}"), ("acc2", "acc2", "{:.2f}"),
         ("acc1t", "acc1_time", "{:.2f}"), ("rt", "rt", "{:.2f}"))


# -- reading -----------------------------------------------------------------------------------------------------------
def read_beats(path: pathlib.Path) -> np.ndarray:
    ts = []
    for line in path.read_text().splitlines():
        parts = line.replace(",", " ").split()
        if not parts or parts[0].startswith("#"):
            continue
        try:
            ts.append(float(parts[0]))
        except ValueError:
            continue
    return np.unique(np.asarray(ts, float))


def result_files(paths: list[pathlib.Path]) -> list[pathlib.Path]:
    out = []
    for p in paths:
        if p.is_dir():
            out += sorted(q for q in p.glob("*.json") if q.name not in ("summary.json", "batch.json"))
        else:
            out.append(p)
    return out


class Line:
    """The published line over host time: the body in force at t (the last one published at or before t)."""

    def __init__(self, r: dict) -> None:
        f = r["body_fields"]
        rows = r["bodies"]
        self.h = np.array([b[f.index("h")] for b in rows], float)
        self.bpm = np.array([b[f.index("bpm")] or 0.0 for b in rows], float)
        self.gate = np.array([b[f.index("gate")] for b in rows], object)

    def at(self, t: np.ndarray) -> np.ndarray:
        """Index of the body in force at each t (-1: none yet)."""
        return np.searchsorted(self.h, t, side="right") - 1


# -- helpers -----------------------------------------------------------------------------------------------------------
def nearest(ref: np.ndarray, t: np.ndarray) -> np.ndarray:
    """|t - the nearest ref| for each t (inf with no ref)."""
    if not len(ref):
        return np.full(len(t), np.inf)
    i = np.clip(np.searchsorted(ref, t), 0, len(ref) - 1)
    j = np.clip(i - 1, 0, len(ref) - 1)
    return np.minimum(np.abs(ref[i] - t), np.abs(ref[j] - t))


def local_ibi(gt: np.ndarray, t: np.ndarray) -> np.ndarray:
    """The annotated inter-beat interval around each t."""
    if len(gt) < 2:
        return np.full(len(t), NAN)
    mids = (gt[:-1] + gt[1:]) / 2
    ibi = np.diff(gt)
    return np.interp(t, mids, ibi)


def signed_err(ref: np.ndarray, t: np.ndarray) -> np.ndarray:
    """t - the nearest ref for each t (inf with no ref)."""
    if not len(ref):
        return np.full(len(t), np.inf)
    i = np.clip(np.searchsorted(ref, t), 0, len(ref) - 1)
    j = np.clip(i - 1, 0, len(ref) - 1)
    a, b = t - ref[i], t - ref[j]
    return np.where(np.abs(a) <= np.abs(b), a, b)


def match_dh(gt: np.ndarray, t: np.ndarray, bpm: np.ndarray) -> np.ndarray:
    """Each flash on an annotated beat, or, at about twice the annotated tempo, on an annotated off-beat."""
    strict = nearest(gt, t) <= TOL
    if len(gt) < 2:
        return strict
    ratio = local_ibi(gt, t) / (60.0 / np.maximum(bpm, 1e-6))          # flash tempo / annotated tempo
    mid = (gt[:-1] + gt[1:]) / 2
    on_mid = nearest(mid, t) <= TOL
    return strict | ((np.abs(ratio - 2.0) < 0.3) & on_mid)


def weighted_median(v: np.ndarray, w: np.ndarray) -> float:
    m = np.isfinite(v) & (w > 0)
    if not m.any():
        return NAN
    v, w = v[m], w[m]
    o = np.argsort(v)
    c = np.cumsum(w[o])
    return float(v[o][np.searchsorted(c, c[-1] / 2)])


def octave_ok(ratio: np.ndarray, levels=(1.0, 2.0, 0.5, 3.0, 1 / 3)) -> np.ndarray:
    return np.any([np.abs(ratio / L - 1.0) <= ACC_TOL for L in levels], axis=0)


def lock_time(t: np.ndarray, gated: np.ndarray, ok: np.ndarray) -> float:
    """The first gated flash that starts LOCK_RUN consecutive flashes, all gated and all on the beat."""
    n = len(t)
    for i in range(n - LOCK_RUN + 1):
        if gated[i:i + LOCK_RUN].all() and ok[i:i + LOCK_RUN].all():
            return float(t[i])
    return NAN


def wrong_share(t: np.ndarray, period: np.ndarray, ok: np.ndarray) -> float:
    """Share of the gated flashes' time whose trailing 4-bar window of gated flashes is under 50 % on the beat."""
    if not len(t):
        return NAN
    bad = np.zeros(len(t), bool)
    for i in range(len(t)):
        lo = np.searchsorted(t, t[i] - WINDOW_BEATS * period[i], side="right")
        w = ok[lo:i + 1]
        bad[i] = len(w) >= 2 and w.mean() < 0.5
    return float(np.sum(period[bad]) / np.sum(period))


# -- one track ---------------------------------------------------------------------------------------------------------
def score_one(r: dict, gt_all: np.ndarray) -> dict:
    ff = r["flash_fields"]
    fl = r["flashes"]
    ft = np.array([x[ff.index("t")] for x in fl], float)
    fg = np.array([x[ff.index("gate")] for x in fl], object)
    fb = np.array([x[ff.index("bpm")] for x in fl], float)
    line = Line(r)
    end = float(r["track_s"])
    row: dict = {"id": r["id"], "dataset": r.get("dataset") or "", "genre": r.get("genre") or "",
                 "degrade": (r.get("degrade") or {}).get("model", ""), "snr_db": (r.get("degrade") or {}).get("snr_db"),
                 "slow_lane": r.get("slow_lane", {}).get("on"), "slow_applied": r.get("slow_lane", {}).get("applied"),
                 "audio_s": r.get("audio_s"), "track_s": end, "rt": r.get("rt"),
                 "wall_s": (r.get("wall_s") or {}).get("total"), "seeds": r["stats"].get("seeds", 0),
                 "octave_changes": r["stats"].get("octave_changes", 0),
                 "cold_searches": r["stats"].get("cold_searches", 0),
                 "corr_applied": r["stats"].get("corr_applied", 0), "errors": sum((r.get("errors") or {}).values()),
                 "gate_ref_lufs": (r.get("options") or {}).get("gate_ref_lufs"),
                 "preroll_s": r.get("preroll_s"), "stack": (r.get("stack") or {}).get("id", "")}
    # the lock time: from the first annotated beat (the music's start), over the whole replay, the same for both ranges
    gt_in = gt_all[(gt_all >= 0) & (gt_all <= end)]
    t_lock = t_lock_dh = NAN
    if len(gt_in) >= 2 and len(ft):
        gated_all = fg == "beat"
        t_lock = lock_time(ft, gated_all, nearest(gt_in, ft) <= TOL) - gt_in[0]
        t_lock_dh = lock_time(ft, gated_all, match_dh(gt_in, ft, fb)) - gt_in[0]
    for skip in SKIPS:
        sfx = f"_{int(skip)}"
        gt = gt_all[(gt_all >= skip) & (gt_all <= end)]
        m = (ft >= skip) & (ft <= end)
        t, g, b = ft[m], fg[m], fb[m]
        gated = g == "beat"
        tg = t[gated]
        out: dict = {"n_gt": len(gt), "n_flash": len(t), "n_gated": int(gated.sum())}
        if len(gt) >= 2:
            ref, est = gt, t
            out["f"] = mir_eval.beat.f_measure(ref, est, TOL) if len(est) else 0.0
            if len(est) >= 2:
                c = mir_eval.beat.continuity(ref, est)
                out["cmlt"], out["amlt"] = c[1], c[3]
            else:
                out["cmlt"] = out["amlt"] = 0.0
            out["p"] = mir_eval.beat.p_score(ref, est) if len(est) >= 2 else 0.0
            out["f_gated"] = mir_eval.beat.f_measure(ref, tg, TOL) if len(tg) else 0.0
            out["hit25"] = float(np.mean(nearest(t, gt) <= TOL25)) if len(t) else 0.0
            out["hit25_gated"] = float(np.mean(nearest(tg, gt) <= TOL25)) if len(tg) else 0.0
            out["hit70_gated"] = float(np.mean(nearest(tg, gt) <= TOL)) if len(tg) else 0.0
            e = signed_err(gt, t)
            e = e[np.abs(e) <= TOL] * 1000.0
            out["offset_ms"] = float(np.median(e)) if len(e) else NAN
            out["offset_iqr_ms"] = float(np.subtract(*np.percentile(e, [75, 25]))) if len(e) else NAN
            bi = line.at(gt)
            gates = np.where(bi >= 0, line.gate[np.maximum(bi, 0)], "none")
            out["cover_beat"] = float(np.mean(gates == "beat"))
            out["cover_gated"] = float(np.mean((gates == "beat") | (gates == "dmx")))
            # precision and wrong locks judge only the flashes the annotations cover (half a beat either side)
            ibi0 = float(np.median(np.diff(gt)))
            span = (t >= gt[0] - ibi0 / 2) & (t <= gt[-1] + ibi0 / 2)
            ok = nearest(gt, t) <= TOL
            okdh = match_dh(gt, t, b)
            gs = gated & span
            out["prec_beat"] = float(ok[gs].mean()) if gs.any() else NAN
            out["prec_beat_dh"] = float(okdh[gs].mean()) if gs.any() else NAN
            per = 60.0 / np.maximum(b[gs], 1e-6)
            out["wrong_lock"] = wrong_share(t[gs], per, okdh[gs])
            out["wrong_lock_strict"] = wrong_share(t[gs], per, ok[gs])
            out["t_lock"], out["t_lock_dh"] = t_lock, t_lock_dh
            # tempo: the published bpm over the range, by the time each body was in force
            gt_bpm = 60.0 / float(np.median(np.diff(gt)))
            hs = np.r_[line.h, end]
            lo, hi = np.maximum(hs[:-1], skip), np.minimum(hs[1:], end)
            w = np.maximum(0.0, hi - lo)
            est_bpm = weighted_median(np.where(line.bpm > 0, line.bpm, NAN), w)
            ratio = est_bpm / gt_bpm if math.isfinite(est_bpm) else NAN
            out["est_bpm"], out["gt_bpm"] = est_bpm, gt_bpm
            out["acc1"] = float(abs(ratio - 1.0) <= ACC_TOL) if math.isfinite(ratio) else 0.0
            out["acc2"] = float(octave_ok(np.array([ratio]))[0]) if math.isfinite(ratio) else 0.0
            ts = np.arange(max(skip, gt[0]), min(end, gt[-1]), 0.1)
            if len(ts):
                bi = line.at(ts)
                bp = np.where(bi >= 0, line.bpm[np.maximum(bi, 0)], 0.0)
                rr = np.where(bp > 0, bp / (60.0 / local_ibi(gt, ts)), NAN)
                fin = np.isfinite(rr)
                out["acc1_time"] = float(np.mean(fin & (np.abs(np.nan_to_num(rr) - 1.0) <= ACC_TOL)))
                out["acc2_time"] = float(np.mean(fin & octave_ok(np.nan_to_num(rr))))
            else:
                out["acc1_time"] = out["acc2_time"] = NAN
        else:
            out.update({k: NAN for k in KEYS})
        out["locked"] = float(math.isfinite(out.get("t_lock", NAN)))
        row.update({f"{k}{sfx}": v for k, v in out.items()})
    return row


# -- aggregates --------------------------------------------------------------------------------------------------------
def aggregate(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = collections.OrderedDict()
    if len({r["dataset"] for r in rows}) > 1:                # one dataset: its own row is the overall one
        groups[("all", "")] = rows
    for r in rows:
        groups.setdefault((r["dataset"] or "-", ""), []).append(r)
    for r in rows:
        if r["genre"]:
            groups.setdefault((r["dataset"] or "-", r["genre"]), []).append(r)
    out = []
    for (ds, genre), rs in groups.items():
        g = {"dataset": ds, "genre": genre, "n": len(rs),
             "rt": float(np.nanmean([r["rt"] for r in rs if r.get("rt") is not None] or [NAN])),
             "wall_s": float(np.nansum([r["wall_s"] or 0 for r in rs]))}
        for skip in SKIPS:
            sfx = f"_{int(skip)}"
            for k in KEYS + ("locked",):
                v = np.array([r.get(f"{k}{sfx}", NAN) for r in rs], float)
                if k.startswith("t_lock"):
                    v = v[np.isfinite(v)]
                    g[f"{k}{sfx}"] = float(np.median(v)) if len(v) else NAN
                else:
                    g[f"{k}{sfx}"] = float(np.nanmean(v)) if np.isfinite(v).any() else NAN
        out.append(g)
    return out


def fmt(v, spec: str) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-"
    return spec.format(int(v)) if spec == "{:d}" else spec.format(v)


def print_table(groups: list[dict], skip: float, per_track: bool = False) -> None:
    """The summary table (``per_track``: one line per track, the rows score_one made, instead of the groups)."""
    sfx = f"_{int(skip)}"
    head = ["track" if per_track else "group"] + [h for h, _, _ in TABLE]
    lines = [head]
    for g in groups:
        if per_track:
            name = f"{g['dataset']}/{g['id']}" if g["dataset"] else g["id"]
        else:
            name = g["dataset"] + (f"/{g['genre']}" if g["genre"] else "")
        cells = [name[:48]]
        for _, k, spec in TABLE:
            if k == "n":
                v = 1 if per_track else g.get("n")
            else:
                v = g.get(k) if k == "rt" else g.get(f"{k}{sfx}")
            cells.append(fmt(v, spec))
        lines.append(cells)
    widths = [max(len(c[i]) for c in lines) for i in range(len(head))]
    title = "including the first 5 s" if skip == 0 else f"excluding the first {skip:g} s"
    what = "per track; tLock = s to a 4-beat correct lock" if per_track else \
        "means per track; tLock = median s to a 4-beat correct lock over the tracks that locked"
    print(f"\nBeat stack vs annotations, {title} ({what})")
    for c in lines:
        print("  ".join(s.rjust(w) if i else s.ljust(w) for i, (s, w) in enumerate(zip(c, widths))))


def write_csv(path: pathlib.Path, rows: list[dict]) -> None:
    keys: list[str] = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 5) if isinstance(v, float) and math.isfinite(v) else
                            ("" if isinstance(v, float) else v)) for k, v in r.items()})


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", nargs="+", type=pathlib.Path)
    ap.add_argument("--csv", type=pathlib.Path, help="per-track rows (default <dir>/scores.csv for one dir)")
    ap.add_argument("--summary", type=pathlib.Path, help="per-group rows (default <dir>/summary.csv for one dir)")
    ap.add_argument("--tracks", choices=("auto", "on", "off"), default="auto",
                    help="also print one line per track (auto: up to 30 tracks)")
    a = ap.parse_args(argv)
    files = result_files(a.results)
    rows, skipped = [], []
    for p in files:
        try:
            r = json.loads(p.read_text())
            if "flashes" not in r:
                continue
            gt_path = pathlib.Path(r["beats"]) if r.get("beats") else None
            if gt_path is None or not gt_path.exists():
                skipped.append(f"{p.name}: no ground truth")
                continue
            rows.append(score_one(r, read_beats(gt_path) - float(r.get("start_s") or 0.0)))   # an excerpt's own time
        except (OSError, ValueError, KeyError) as e:
            skipped.append(f"{p.name}: {type(e).__name__}: {e}")
    if not rows:
        print("nothing to score" + (": " + "; ".join(skipped) if skipped else ""))
        return 1
    groups = aggregate(rows)
    one_dir = a.results[0] if len(a.results) == 1 and a.results[0].is_dir() else None
    csv_p = a.csv or (one_dir / "scores.csv" if one_dir else None)
    sum_p = a.summary or (one_dir / "summary.csv" if one_dir else None)
    if csv_p:
        write_csv(csv_p, rows)
    if sum_p:
        write_csv(sum_p, groups)
    per_track = a.tracks == "on" or (a.tracks == "auto" and len(rows) <= 30)
    for skip in SKIPS:
        if per_track:
            print_table(rows, skip, per_track=True)
        print_table(groups, skip)
    stacks = collections.Counter(r["stack"] or "?" for r in rows)
    if len(stacks) > 1:                                  # show_service changed between these runs (run_stack "stack")
        print("\nNOTE: these results come from different stacks: " +
              ", ".join(f"{k} ({v} tracks)" for k, v in stacks.most_common()) +
              ". Delete the stale ones and rerun run_batch.py to compare like with like.")
    if skipped:
        print(f"\nskipped {len(skipped)}: " + "; ".join(skipped[:10]))
    if csv_p:
        print(f"\nper track: {csv_p}" + (f"; groups: {sum_p}" if sum_p else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
