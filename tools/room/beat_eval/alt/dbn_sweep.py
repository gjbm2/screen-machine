#!/usr/bin/env python3
"""DEV-subset tuning for the madmom-DBN candidate (madmom_dbn.py): replay its dumps under many settings, score, compare.

The LSTM is the expensive part and nothing downstream feeds back into it, so each DEV track runs through the real
front end once (``madmom_dbn.py batch ... -- --record-all``: all 8 nets' outputs and every frame's host time in a
dump), and every DBN / line / conf / gate / re-seed setting, for 2, 4 or 8 nets, replays from the dumps in seconds a
track (madmom_dbn.py checks a replay gives the run's flashes exactly). Scores are score.py's (score_one) and the
aggregates baseline.py's (agg: means per track, steady-beat music = not ASAP, not GTZAN classical), so the numbers
read like the baseline report's. Tune on the DEV subset only: baseline-subset.json is the held-out test.

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/dbn_sweep.py eval --a DIR_A --c DIR_C \\
      [--variant NAME] [--set k=v ...] [--grid '{"eps": [0, 1e-6], "nets": [2, 8]}'] [--save OUT]
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/dbn_sweep.py calib --a DIR_A --c DIR_C [--set k=v ...]
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/dbn_sweep.py offset --a DIR_A [--set k=v ...]

``eval`` prints, per setting and condition, the baseline's columns for steady-beat music (F, CMLt, AMLt, hit25, gate
coverage, precision while gated per track and pooled, wrong-lock share, median time to lock over the tracks that
lock and with never = never, the share that locks) plus the expressive sets (tracks gated at all, tracks with >= 30 %
wrong-lock time) and the tracker's CPU (us a frame, ms per audio second). ``--save`` writes each replay's per-track
JSONs (score.py reads them as they are). ``calib`` fits the conf's logistic (madmom_dbn.CONF_W) on the beats the
tracker publishes: label = the line's next beat lands within 25 ms of an annotated one (a beat published during
the room-sound pre-roll is a 0: there is no beat to find), every track weighted the same, A and C pooled; it prints
the weights to paste into madmom_dbn.py, the AUC and a reliability table. ``offset`` is the flashes' median offset
from the annotations (the lag to take out). The replays hold the CPU lock in slices of about ``--slice-s`` (90 s:
several settings and conditions in one; one heavy process on this host); ``--no-lock`` for a quick look at a few
tracks.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import itertools
import json
import math
import os
import pathlib
import sys
import time

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

import numpy as np  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import madmom_dbn as mdb  # noqa: E402
import baseline  # noqa: E402
import score  # noqa: E402

LOCK = pathlib.Path("~/.cache/screen-machine/cpu.lock").expanduser()


@contextlib.contextmanager
def cpu_lock(on: bool = True):
    if not on:
        yield
        return
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Slicer:
    """The CPU lock in slices: taken before a unit of work if not held, given back after a unit once the slice has
    lasted ``slice_s`` (several settings share one ~90 s slice: each acquisition waited minutes on 19 Oct)."""

    def __init__(self, on: bool, slice_s: float = 90.0) -> None:
        self.on, self.slice_s = on, slice_s
        self._f = None
        self._t = 0.0

    @contextlib.contextmanager
    def unit(self):
        if self.on and self._f is None:
            LOCK.parent.mkdir(parents=True, exist_ok=True)
            self._f = LOCK.open("a")
            fcntl.flock(self._f, fcntl.LOCK_EX)
            self._t = time.time()
        try:
            yield
        finally:
            if self._f is not None and time.time() - self._t >= self.slice_s:
                self.release()

    def release(self) -> None:
        if self._f is not None:
            fcntl.flock(self._f, fcntl.LOCK_UN)
            self._f.close()
            self._f = None


SLICER = Slicer(False)


def load_dir(d: pathlib.Path) -> list[dict]:
    """Each track's dump, base result and annotations (host-time and track-time)."""
    out = []
    for dp in sorted(d.glob("*.dump.npz")):
        bp = dp.with_name(dp.name[:-len(".dump.npz")] + ".json")
        if not bp.exists():
            continue
        base = json.loads(bp.read_text())
        gt = score.read_beats(pathlib.Path(base["beats"])) - float(base.get("start_s") or 0.0)
        out.append({"name": bp.name, "base": base, "dump": mdb.load_dump(dp), "gt": gt})
    return out


def replay_rows(tracks: list[dict], cfg: dict, *, lock: bool, save: pathlib.Path | None = None,
                trace: bool = False) -> tuple[list[dict], dict]:
    rows, cpu, frames, audio = [], 0.0, 0, 0.0
    traces = []
    with SLICER.unit():                                         # the CPU lock, in ~90 s slices (Slicer)
        runs = [(tr, *mdb.replay_dump(tr["dump"], cfg, trace=trace)) for tr in tracks]
    for tr, bodies, trk, c in runs:
        r = mdb.result_from(tr["base"], tr["dump"], cfg, bodies, trk, c)
        if save is not None:
            save.mkdir(parents=True, exist_ok=True)
            (save / tr["name"]).write_text(json.dumps(r, separators=(",", ":"), default=float))
        row = score.score_one(r, tr["gt"])
        row["n_gated_all"] = sum(1 for f in r["flashes"] if f[2] == "beat")
        pre = float(r.get("preroll_s") or 0.0)
        if pre > 0:                                             # gated flashes on the room sound before the music
            row["pre_gated_per_min"] = sum(1 for f in r["flashes"] if f[2] == "beat" and f[0] < -1.0) / (pre / 60.0)
            row["pre_dmx_per_min"] = sum(1 for f in r["flashes"] if f[2] in ("beat", "dmx") and f[0] < -1.0) / (pre / 60.0)
        f70 = [f[0] for f in r["flashes"] if 0.0 <= f[0] <= r["track_s"]]
        row["any70"] = float(np.mean(score.nearest(np.array(f70), tr["gt"][(tr["gt"] >= 0) & (tr["gt"] <= r["track_s"])])
                                     <= score.TOL)) if f70 and len(tr["gt"]) else 0.0
        rows.append(row)
        cpu += c
        frames += int(trk.counts.get("frames", 0))
        audio += float(tr["base"].get("audio_s") or 0.0)
        if trace:
            traces.append((tr, trk.trace))
    return rows, {"cpu_s": cpu, "frames": frames, "audio_s": audio, "traces": traces}


def summarise(rows: list[dict], cost: dict) -> dict:
    steady = [r for r in rows if not baseline.is_expressive(r)]
    g = baseline.agg(steady)
    ex = [r for r in rows if r["dataset"] in baseline.NO_FALSE_LOCK or baseline.is_expressive(r)]
    g["ex_n"] = len(ex)
    g["ex_gated"] = sum(1 for r in ex if r.get("n_gated_all", 0) > 0)
    g["ex_false"] = sum(1 for r in ex if baseline.finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
    pg = [r["pre_gated_per_min"] for r in rows if "pre_gated_per_min" in r]
    g["pre_gated"] = float(np.mean(pg)) if pg else float("nan")      # room sound: gated flashes a minute
    pd = [r["pre_dmx_per_min"] for r in rows if "pre_dmx_per_min" in r]
    g["pre_dmx"] = float(np.mean(pd)) if pd else float("nan")        # ... gated beat or dmx
    g["any70"] = float(np.mean([r["any70"] for r in steady])) if steady else float("nan")   # a perfect gate's cover
    g["us_frame"] = cost["cpu_s"] / max(1, cost["frames"]) * 1e6
    g["ms_per_s"] = cost["cpu_s"] / max(1e-9, cost["audio_s"]) * 1e3
    return g


COLS = (("F", "f", "{:.2f}"), ("CMLt", "cmlt", "{:.2f}"), ("AMLt", "amlt", "{:.2f}"), ("hit25", "hit25", "{:.2f}"),
        ("cover", "cover_beat", "{:.2f}"), ("prec", "prec_beat", "{:.2f}"), ("pPool", "prec_pooled", "{:.2f}"),
        ("wrong", "wrong_lock", "{:.3f}"), ("tLock", "t_lock_med", "{:.1f}"), ("tLkAll", "t_lock_med_all", "{:.1f}"),
        ("locked", "locked", "{:.2f}"), ("exG", "ex_gated", "{:d}"), ("exF", "ex_false", "{:d}"),
        ("preG/min", "pre_gated", "{:.1f}"), ("preGD/min", "pre_dmx", "{:.1f}"), ("any70", "any70", "{:.2f}"),
        ("us/f", "us_frame", "{:.0f}"))


def fmt(v, spec):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "never" if v == math.inf else "-"
    return spec.format(int(v)) if spec == "{:d}" else spec.format(v)


def grid_settings(a: argparse.Namespace) -> list[tuple[str, dict]]:
    base_sets = list(a.sets)
    if not a.grid:
        return [(a.variant or "default", mdb.settings(a.variant, base_sets))]
    g = json.loads(a.grid)
    keys = list(g)
    out = []
    for vals in itertools.product(*(g[k] for k in keys)):
        sets = base_sets + [f"{k}={v}" for k, v in zip(keys, vals)]
        name = ",".join(f"{k}={v}" for k, v in zip(keys, vals))
        out.append((name, mdb.settings(a.variant, sets)))
    return out


def cmd_eval(a: argparse.Namespace) -> int:
    conds = [(c, d) for c, d in (("A", a.a), ("C", a.c)) if d]
    data = {c: load_dir(d.expanduser()) for c, d in conds}
    print("tracks: " + ", ".join(f"{c} {len(v)}" for c, v in data.items()) + "; steady-beat means per track")
    print("| setting | cond | " + " | ".join(h for h, _, _ in COLS) + " |")
    print("|---|---|" + "---|" * len(COLS))
    results = []
    for name, cfg in grid_settings(a):
        for c, tracks in data.items():
            if a.datasets:
                tracks = [t for t in tracks if t["base"].get("dataset") in a.datasets.split(",")]
            t0 = time.time()
            save = (a.save / f"{name.replace(',', '_').replace('=', '-')}-{c}") if a.save else None
            rows, cost = replay_rows(tracks, cfg, lock=not a.no_lock, save=save)
            g = summarise(rows, cost)
            results.append({"setting": name, "cond": c, "cfg": cfg, "agg": g, "wall_s": time.time() - t0})
            print(f"| {name} | {c} | " + " | ".join(fmt(g.get(k), s) for _, k, s in COLS) + " |", flush=True)
    if a.json:
        a.json.write_text(json.dumps(results, indent=1, default=lambda v: None))
    return 0


def nearest_err(gt: np.ndarray, t: float) -> float:
    if not len(gt):
        return math.inf
    i = int(np.searchsorted(gt, t))
    best = math.inf
    for j in (i - 1, i):
        if 0 <= j < len(gt):
            best = min(best, abs(gt[j] - t))
    return best


def collect(a: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list]:
    """Per published beat: features, label25, label70, weight (each track sums to 1)."""
    X, y25, y70, w, meta = [], [], [], [], []
    cfg = mdb.settings(a.variant, a.sets)
    for c, d in (("A", a.a), ("C", a.c)):
        if not d:
            continue
        tracks = load_dir(d.expanduser())
        _, cost = replay_rows(tracks, cfg, lock=not a.no_lock, trace=True)
        for tr, trace in cost["traces"]:
            zero = float(tr["dump"]["meta"]["zero"])
            gt = tr["gt"] + zero
            if len(gt) < 2:
                continue
            ibi = float(np.median(np.diff(gt)))
            pre_end = zero - 1.0
            xs = []
            for e in trace:
                t, k_a, t_a, T = e[:4]
                pred = t_a + T
                if gt[0] - ibi / 2 <= pred <= gt[-1] + ibi / 2:
                    err = nearest_err(gt, pred)
                    xs.append((e[4:], err <= 0.025, err <= 0.07))
                elif mdb.T0 + 5.0 <= pred <= pre_end:          # the room-sound pre-roll: no beat to find
                    xs.append((e[4:], False, False))
            for f, l25, l70 in xs:
                X.append(f)
                y25.append(l25)
                y70.append(l70)
                w.append(1.0 / len(xs))
                meta.append((c, tr["name"], tr["base"].get("dataset"), tr["base"].get("genre")))
    return np.array(X, float), np.array(y25, float), np.array(y70, float), np.array(w, float), meta


def logistic(X: np.ndarray, y: np.ndarray, w: np.ndarray, l2: float = 1e-2, iters: int = 50) -> np.ndarray:
    """Weighted L2 logistic regression (Newton); X standardised with an intercept column first."""
    n, d = X.shape
    beta = np.zeros(d)
    R = l2 * np.eye(d)
    R[0, 0] = 0.0
    for _ in range(iters):
        z = X @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))
        g = X.T @ (w * (p - y)) + R @ beta
        H = (X * (w * p * (1 - p))[:, None]).T @ X + R
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-8:
            break
    return beta


def auc(p: np.ndarray, y: np.ndarray) -> float:
    o = np.argsort(p)
    r = np.empty(len(p))
    r[o] = np.arange(1, len(p) + 1)
    n1, n0 = y.sum(), (1 - y).sum()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / max(1, n1 * n0))


def cmd_calib(a: argparse.Namespace) -> int:
    X, y25, y70, w, meta = collect(a)
    names = list(mdb.FEATURES)
    if a.features:
        keep = [names.index(f.strip()) for f in a.features.split(",") if f.strip()]
        X, names = X[:, keep], [names[i] for i in keep]
    print(f"{len(X)} published beats ({int(y25.sum())} within 25 ms, {int(y70.sum())} within 70 ms), "
          f"{len({m[1] + m[0] for m in meta})} track-conditions")
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    y = y25 if a.label == "25" else y70
    beta = logistic(Z, y, w, l2=a.l2)
    p = 1.0 / (1.0 + np.exp(-(Z @ beta)))
    print(f"label: within {a.label} ms; AUC {auc(p, y):.3f} (h8 alone {auc(X[:, names.index('h8')], y):.3f})")
    print("reliability (weighted): conf bin -> share right (25 ms) / (70 ms), weight share")
    for lo in np.arange(0.0, 1.0, 0.1):
        m = (p >= lo) & (p < lo + 0.1)
        if m.any():
            ww = w[m]
            print(f"  {lo:.1f}-{lo + 0.1:.1f}: {np.sum(ww * y25[m]) / ww.sum():.2f} / {np.sum(ww * y70[m]) / ww.sum():.2f}"
                  f"  ({ww.sum() / w.sum():.3f}, n {int(m.sum())})")
    W = {"b": round(float(beta[0]), 4), "w": {k: round(float(b), 4) for k, b in zip(names, beta[1:])},
         "mu": {k: round(float(v), 5) for k, v in zip(names, mu)}, "sd": {k: round(float(v), 5) for k, v in zip(names, sd)},
         "label_ms": int(a.label), "fitted_on": "dev-subset.json A+C, " + time.strftime("%Y-%m-%d"),
         "settings": {k: v for k, v in mdb.settings(a.variant, a.sets).items() if k in ("nets", "eps", "ch_every_s",
                                                                                        "lag_ms")}}
    print("CONF_W = " + json.dumps(W))
    if a.out_w:
        a.out_w.write_text(json.dumps(W, indent=1))
        print(f"-> {a.out_w}")
    return 0


def cmd_offset(a: argparse.Namespace) -> int:
    cfg = mdb.settings(a.variant, a.sets)
    for c, d in (("A", a.a), ("C", a.c)):
        if not d:
            continue
        rows, _ = replay_rows(load_dir(d.expanduser()), cfg, lock=not a.no_lock)
        off = np.array([r.get("offset_ms_0", math.nan) for r in rows], float)
        steady = np.array([not baseline.is_expressive(r) for r in rows])
        m = np.isfinite(off) & steady
        print(f"{c}: median flash - annotation over {m.sum()} steady tracks {np.median(off[m]):+.1f} ms "
              f"(IQR {np.percentile(off[m], 25):+.1f} .. {np.percentile(off[m], 75):+.1f}); by dataset: "
              + ", ".join(f"{ds} {np.median(off[m & np.array([r['dataset'] == ds for r in rows])]):+.1f}"
                          for ds in sorted({r['dataset'] for r in rows})
                          if (m & np.array([r['dataset'] == ds for r in rows])).any()))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("eval", "calib", "offset"))
    ap.add_argument("--a", type=pathlib.Path, default=None, help="condition A's dumps (a madmom_dbn batch dir)")
    ap.add_argument("--c", type=pathlib.Path, default=None, help="condition C's dumps")
    ap.add_argument("--variant", default=None)
    ap.add_argument("--set", action="append", default=[], dest="sets")
    ap.add_argument("--grid", default=None, help="JSON {setting: [values]}: every combination")
    ap.add_argument("--datasets", default=None)
    ap.add_argument("--save", type=pathlib.Path, default=None)
    ap.add_argument("--json", type=pathlib.Path, default=None, help="eval: write the aggregates here")
    ap.add_argument("--label", choices=("25", "70"), default="25")
    ap.add_argument("--l2", type=float, default=1e-2)
    ap.add_argument("--out-w", type=pathlib.Path, default=None, help="calib: write the weights here (--set conf_file=)")
    ap.add_argument("--features", default=None, help="calib: only these features (comma-separated)")
    ap.add_argument("--no-lock", action="store_true")
    ap.add_argument("--slice-s", type=float, default=90.0, help="CPU-lock slice length (several settings in one)")
    a = ap.parse_args(argv)
    global SLICER
    SLICER = Slicer(not a.no_lock, a.slice_s)
    try:
        return {"eval": cmd_eval, "calib": cmd_calib, "offset": cmd_offset}[a.cmd](a)
    finally:
        SLICER.release()


if __name__ == "__main__":
    raise SystemExit(main())
