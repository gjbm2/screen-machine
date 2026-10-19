#!/usr/bin/env python3
"""Compare OUR clock's variants (alt/clockvar.py) on a subset: the bars' numbers per variant and condition, with CPU
next to each, and (``--fit``) the logistic calibration of variant d's confidence on the dev subset's beats.

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/clockvar_report.py [--subset dev] [--conds A,C]
      [--variants base,stock,...] [--groups] [--md OUT.md]
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/clockvar_report.py --fit NAME --fit-variant stock
      [--fit-target 70|25] [--subset dev] [--conds A,C]

Columns (steady-beat music: everything but ASAP and GTZAN classical; whole replay from the music's start, means per
track, as baseline.py's report): F, CMLt, AMLt, hit25, cover (gate "beat"), prec (gated flashes within 70 ms; per
track mean) and precPool (pooled), wrong (wrong-lock share of gated time), tLock (median s over the tracks that
locked) and tLockAll (never counts as never), locked (share), then the expressive sets' worst wrong-lock share and
gated flashes (``exprFL``: tracks with >= 30 % wrong-lock time / ``exprG``: gated flashes in all of them), and CPU:
``clk`` the clock's own show-rx time per audio second (ms/s, madmom excluded; measured on the fast replay, nice 19
under the 4K render) and ``act`` madmom's own (ms/s, recorded; 2 nets unless the variant says ``nets=N``).
``--fit`` reads each judged beat's terms from the results' ``trace`` (clockvar.TRACE_FIELDS) with the outcome "the
estimator's next beat lands within 70 (or 25) ms of an annotated beat", fits P(outcome | terms) by logistic
regression (numpy IRLS, L2), prints its calibration table and writes OUT/fits/NAME.json for ``d:fit=NAME``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import numpy as np  # noqa: E402

import baseline  # noqa: E402
import score  # noqa: E402

OUT = pathlib.Path("~/.cache/screen-machine/beat-eval/clockvar").expanduser()
FEATS = ("h", "prec", "clar", "cterm", "agree", "coh", "nhit8", "npk", "sig", "clar1", "tgcos")


def finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def variant_dirs(subset: str, cond: str, want: list[str] | None) -> list[pathlib.Path]:
    root = OUT / f"{subset}-{cond}"
    if not root.exists():
        return []
    ds = sorted(d for d in root.iterdir() if d.is_dir())
    if want:
        names = {w.replace(":", "~").replace("+", "_") for w in want}
        names = {"".join(c if c.isalnum() or c in "-_." else "_" for c in n) for n in names}
        ds = [d for d in ds if d.name in names]
    return ds


def cpu_of(d: pathlib.Path) -> tuple[float, float]:
    clk, act = [], []
    for p in d.glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        c = r.get("clock_cpu") or {}
        if finite(c.get("clock_ms_per_s")):
            clk.append(c["clock_ms_per_s"])
        if finite(c.get("act_ms_per_s")):
            act.append(c["act_ms_per_s"])
    return (float(np.mean(clk)) if clk else math.nan, float(np.mean(act)) if act else math.nan)


def summarize(d: pathlib.Path) -> dict:
    rows = baseline.load_rows(d)
    steady = [r for r in rows if not baseline.is_expressive(r)]
    g = baseline.agg(steady) if steady else {}
    ex = [r for r in rows if baseline.is_expressive(r)]
    g["exprFL"] = sum(1 for r in ex if finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
    g["exprG"] = sum(int(r.get("n_gated_0", 0)) for r in ex)
    g["n_expr"] = len(ex)
    g["clk"], g["act"] = cpu_of(d)
    g["stacks"] = len({r["stack"] for r in rows})
    g["rows"] = rows
    return g


COLS = (("n", "n", "{:d}"), ("F", "f", "{:.3f}"), ("CMLt", "cmlt", "{:.3f}"), ("AMLt", "amlt", "{:.3f}"),
        ("hit25", "hit25", "{:.3f}"), ("cover", "cover_beat", "{:.3f}"), ("prec", "prec_beat", "{:.3f}"),
        ("precPool", "prec_pooled", "{:.3f}"), ("wrong", "wrong_lock", "{:.3f}"), ("tLock", "t_lock_med", "{:.1f}"),
        ("tLockAll", "t_lock_med_all", "{:.1f}"), ("locked", "locked", "{:.2f}"), ("exprFL", "exprFL", "{:d}"),
        ("exprG", "exprG", "{:d}"), ("clk ms/s", "clk", "{:.1f}"), ("act ms/s", "act", "{:.0f}"))


def fmt(v, spec: str) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-" if v != math.inf else "never"
    return spec.format(int(v)) if spec == "{:d}" else spec.format(v)


def cmd_table(a: argparse.Namespace) -> int:
    want = [v for v in a.variants.split(";" if ";" in (a.variants or "") else ",") if v] if a.variants else None
    md = []
    for cond in a.conds.split(","):
        ds = variant_dirs(a.subset, cond, want)
        if not ds:
            continue
        md.append(f"\n### {a.subset} {cond} (steady-beat music; CPU: clk = clock's show-rx ms per audio s, act = "
                  f"madmom's)\n")
        md.append("| variant | " + " | ".join(h for h, _, _ in COLS) + " |")
        md.append("|---|" + "---|" * len(COLS))
        for d in ds:
            g = summarize(d)
            if not g.get("n"):
                continue
            md.append(f"| {d.name} | " + " | ".join(fmt(g.get(k), s) for _, k, s in COLS) + " |")
            if a.groups:
                for ds_name in sorted({r["dataset"] for r in g["rows"]}):
                    rs = [r for r in g["rows"] if r["dataset"] == ds_name and not baseline.is_expressive(r)]
                    if rs:
                        gg = baseline.agg(rs)
                        md.append(f"| . {ds_name} | " + " | ".join(fmt(gg.get(k), s) for _, k, s in COLS[:12])
                                  + " |" * (len(COLS) - 12) + " |")
    text = "\n".join(md) + "\n"
    print(text)
    if a.md:
        pathlib.Path(a.md).write_text(text)
    return 0


# -- the calibration fit -----------------------------------------------------------------------------------------------
def beat_rows(d: pathlib.Path, target_s: float) -> tuple[np.ndarray, np.ndarray, list]:
    X, y, ids = [], [], []
    for p in score.result_files([d]):
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if "trace" not in r or not r.get("beats"):
            continue
        if baseline.is_expressive({"dataset": r.get("dataset"), "genre": r.get("genre")}) and not ARGS.with_expr:
            continue
        gt = score.read_beats(pathlib.Path(r["beats"])) - float(r.get("start_s") or 0.0)
        gt = gt[(gt >= 0) & (gt <= r["track_s"])]
        if len(gt) < 4:
            continue
        f = r["trace_fields"]
        ix = {k: f.index(k) for k in f}
        lo, hi = gt[0] - 0.5, gt[-1] + 0.5
        for row in r["trace"]:
            tn = row[ix["t_next"]]
            if tn is None or not (lo <= tn <= hi) or row[ix["h"]] is None or not row[ix["confirmed"]]:
                continue
            d = {k: row[ix[k]] for k in f}
            if d.get("clar1") is not None and d.get("tgph") is not None:
                d["tgcos"] = d["clar1"] * math.cos(2 * math.pi * d["tgph"])
            X.append([float(d[k]) if d.get(k) is not None else math.nan for k in FEATS])
            y.append(float(score.nearest(gt, np.array([tn]))[0] <= target_s))
            ids.append(r["id"])
    return np.array(X, float), np.array(y, float), ids


def design(X: np.ndarray) -> np.ndarray:
    Z = X.copy()
    j = FEATS.index("sig")
    Z[:, j] = np.where(np.isfinite(Z[:, j]), np.minimum(Z[:, j], 0.1), 0.1)
    Z = np.nan_to_num(Z, nan=0.0)
    return np.c_[np.ones(len(Z)), Z]


def logit_fit(Z: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 50) -> np.ndarray:
    w = np.zeros(Z.shape[1])
    R = l2 * np.eye(Z.shape[1])
    R[0, 0] = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-(Z @ w)))
        W = p * (1 - p) + 1e-9
        g = Z.T @ (p - y) + R @ w
        H = Z.T @ (Z * W[:, None]) + R
        step = np.linalg.solve(H, g)
        w -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return w


def cmd_fit(a: argparse.Namespace) -> int:
    Xs, ys = [], []
    fv = dict(kv.split("=", 1) for kv in a.fit_variant.split(",")) if "=" in a.fit_variant else {}
    for cond in a.conds.split(","):
        d = OUT / f"{a.subset}-{cond}" / fv.get(cond, a.fit_variant)
        X, y, _ = beat_rows(d, a.fit_target / 1000.0)
        print(f"{cond}: {len(y)} judged beats, {y.mean() if len(y) else math.nan:.3f} within {a.fit_target} ms")
        Xs.append(X)
        ys.append(y)
    X, y = np.vstack(Xs), np.concatenate(ys)
    Z = design(X)
    mu, sd = Z[:, 1:].mean(0), Z[:, 1:].std(0) + 1e-9
    Zs = np.c_[np.ones(len(Z)), (Z[:, 1:] - mu) / sd]
    ws = logit_fit(Zs, y, a.l2)
    w = np.r_[ws[0] - np.sum(ws[1:] * mu / sd), ws[1:] / sd]          # back to raw units
    p = 1.0 / (1.0 + np.exp(-(Z @ w)))
    print("weights (raw units):", {k: round(float(v), 4) for k, v in zip(("b0",) + FEATS, w)})
    print("standardised:", {k: round(float(v), 3) for k, v in zip(("b0",) + FEATS, ws)})
    print("calibration (predicted -> observed):")
    for lo in np.arange(0, 1.0, 0.1):
        m = (p >= lo) & (p < lo + 0.1)
        if m.any():
            print(f"  {lo:.1f}-{lo + 0.1:.1f}: n {m.sum():6d}  observed {y[m].mean():.3f}")
    for thr in (0.5, 0.6, 0.7, 0.8, 0.9):
        m = p >= thr
        print(f"  conf >= {thr}: share of beats {m.mean():.3f}, observed {y[m].mean() if m.any() else math.nan:.3f}")
    stock = X[:, FEATS.index("h")] * X[:, FEATS.index("prec")] * X[:, FEATS.index("cterm")]
    for thr in (0.6, 0.7):
        m = stock >= thr
        print(f"  stock conf >= {thr}: share {m.mean():.3f}, observed {y[m].mean() if m.any() else math.nan:.3f}")
    (OUT / "fits").mkdir(parents=True, exist_ok=True)
    doc = {"w": {k: float(v) for k, v in zip(("b0",) + FEATS, w)}, "target_ms": a.fit_target,
           "variant": a.fit_variant, "subset": a.subset, "conds": a.conds, "n": int(len(y)), "l2": a.l2}
    (OUT / "fits" / f"{a.fit}.json").write_text(json.dumps(doc, indent=1))
    print(f"-> {OUT / 'fits' / (a.fit + '.json')}")
    return 0


ARGS: argparse.Namespace


def main(argv=None) -> int:
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--subset", default="dev")
    ap.add_argument("--conds", default="A,C")
    ap.add_argument("--variants", default=None)
    ap.add_argument("--groups", action="store_true")
    ap.add_argument("--md", default=None)
    ap.add_argument("--fit", default=None)
    ap.add_argument("--fit-variant", default="stock", help="the variant whose traces are fitted (its dir name), or per "
                                                            "condition: A=stock,C=b_a")
    ap.add_argument("--fit-target", type=float, default=70.0)
    ap.add_argument("--l2", type=float, default=1.0)
    ap.add_argument("--with-expr", action="store_true", help="--fit: the expressive sets' beats too")
    ARGS = a = ap.parse_args(argv)
    return cmd_fit(a) if a.fit else cmd_table(a)


if __name__ == "__main__":
    raise SystemExit(main())
