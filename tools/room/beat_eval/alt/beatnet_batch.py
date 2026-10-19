#!/usr/bin/env python3
"""Run the BeatNet candidate (alt/beatnet_stack.py) on a subset under the baseline's conditions, sweep its variants
on the cached stage 1 and score them in one process, or report result dirs next to the baseline.

  # the measure phase: every track as run_batch.py runs the baseline (the same per-track seeds, pre-roll, 260 s cap,
  # condition arguments from baseline.conditions()), one track at a time under the CPU lock (run_batch.py --runner);
  # score.py at the end. --subset test = the held-out baseline-subset.json, for the final measurement only
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/beatnet_batch.py run --subset test --conditions C,A \
      --tag room -- --preset room
  # -> ~/.cache/screen-machine/beat-eval/alt-beatnet/test-C-room/, test-A-room/ (also --preset stock / hmm)
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/beatnet_batch.py report \
      ~/.cache/screen-machine/beat-eval/alt-beatnet/test-C-room ~/.cache/screen-machine/beat-eval/alt-beatnet/test-A-room

  # tuning (dev only): each variant's filter + line on every track's cached stage 1, scored with score.py's scorer,
  # aggregated as baseline.py's report (steady-beat means, the expressive sets' false locks), CPU per audio second
  ~/.venvs/sm-beat-beatnet/bin/python tools/room/beat_eval/alt/beatnet_batch.py sweep --subset dev --conditions C,A \
      --variant "room=--preset room" --variant "cs70=--preset room --conf-scale 0.7" [--write] [--json out.json]

``sweep`` computes a missing stage 1 itself and holds the shared CPU lock in slices of ``--hold-s`` (45 s: several
short tracks per turn, then it lets go); it never runs on the test subset unless ``--allow-test`` (the held-out set
is not for tuning). ``report`` prints, per dir: baseline.py's steady-beat aggregate, the expressive sets (gated /
false locks), the candidate's CPU, and the baseline's own row on the same tracks (baseline-<cond>/).

CPU (process CPU per audio second, nice 19, one torch/BLAS thread, under the shared 4K render; the ``cpu`` field of
every result): the replay runs the CRNN over a whole track at once (``crnn`` about 3 ms/s). A live port runs it as
the audio arrives: torch in 100 ms chunks measured 17 ms/s (``--act-chunk 5`` replays that latency), torch frame by
frame 54-95 ms/s (per-call overhead), plain numpy frame by frame (beatnet_stack.LiveLane: features + CRNN) 50-63
ms/s. The rest per audio second: AGC + soxr 8, features 5, the forward filter at 100 fps 13, the line 1.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import pathlib
import shlex
import subprocess
import sys
import time
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent
BEVAL = HERE.parent
REPO = BEVAL.parents[2]
sys.path.insert(0, str(BEVAL))
sys.path.insert(0, str(HERE))
import baseline  # noqa: E402
import run_batch  # noqa: E402

CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval").expanduser()
SUBSETS = {"dev": CACHE / "dev-subset.json", "test": CACHE / "baseline-subset.json"}
OUT = CACHE / "alt-beatnet"
BN_PY = pathlib.Path("~/.venvs/sm-beat-beatnet/bin/python").expanduser()
RT_PY = pathlib.Path("~/.venvs/sm-roundtrip/bin/python").expanduser()
LOCK = pathlib.Path("~/.cache/screen-machine/cpu.lock").expanduser()
RUNNER = HERE / "beatnet_stack.py"
SEED = 0
DUR_CAP_S = baseline.DUR_CAP_S


def cmd_run(a, extra: list[str]) -> int:
    conds = baseline.conditions()
    rc = 0
    for c in [s.strip() for s in a.conditions.split(",") if s.strip()]:
        out = OUT / (f"{a.subset}-{c}" + (f"-{a.tag}" if a.tag else ""))
        out.mkdir(parents=True, exist_ok=True)
        cmd = [str(RT_PY), str(BEVAL / "run_batch.py"), str(SUBSETS[a.subset]), "--seed", str(SEED), "--track-seeds",
               "--out", str(out), "--dur-s", f"{DUR_CAP_S:g}", "--runner", str(RUNNER), "--runner-python", str(BN_PY),
               *conds[c]["args"]]
        if a.datasets:
            cmd += ["--datasets", a.datasets]
        if extra:
            cmd += ["--", *extra]
        (out / "beatnet_batch.json").write_text(json.dumps({"condition": c, "what": conds[c]["what"], "cmd": cmd,
                                                           "extra": extra}, indent=1))
        print(f"\n=== {a.subset} condition {c}: {conds[c]['what']} {' '.join(extra)}\n{' '.join(cmd)}", flush=True)
        rc = rc or subprocess.run(cmd, cwd=str(REPO)).returncode
    return rc


# -- the sweep ---------------------------------------------------------------------------------------------------------
def track_argv(e: dict, cond_args: list[str]) -> list[str]:
    """What run_batch.py would pass the runner for this track (minus --out)."""
    argv = [str(e["audio"]), "--id", e["id"], "--dataset", e["dataset"], "--genre", e["genre"],
            "--seed", str(run_batch.track_seed(SEED, e))]
    if e["beats"] and pathlib.Path(e["beats"]).exists():
        argv += ["--beats", str(e["beats"])]
    if e.get("start_s"):
        argv += ["--start-s", str(e["start_s"])]
    argv += ["--dur-s", str(float(DUR_CAP_S)), *cond_args]                # run_batch.py's spelling
    return argv


class Lock:
    """The shared CPU lock, held in slices of at most ``hold_s`` (several short tracks per turn, then let go)."""

    def __init__(self, hold_s: float = 45.0) -> None:
        self.hold_s = hold_s
        self.f = None
        self.t = 0.0
        self.waited = 0.0

    def take(self) -> None:
        if self.f is not None and time.time() - self.t > self.hold_s:
            self.release()
        if self.f is None:
            t0 = time.time()
            self.f = LOCK.open("a")
            fcntl.flock(self.f, fcntl.LOCK_EX)
            self.t = time.time()
            self.waited += self.t - t0

    def release(self) -> None:
        if self.f is not None:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()
            self.f = None


def cmd_sweep(a) -> int:
    if a.subset == "test" and not a.allow_test:
        raise SystemExit("the test subset is held out: sweep on dev (or pass --allow-test for the final measurement)")
    try:
        os.nice(19)
    except OSError:
        pass
    import numpy as np
    import beatnet_stack as bs
    import score
    conds = baseline.conditions()
    _, entries = run_batch.load_index(SUBSETS[a.subset])
    if a.datasets:
        want = {s.strip() for s in a.datasets.split(",")}
        entries = [e for e in entries if e["dataset"] in want]
    if a.limit:
        entries = entries[:a.limit]
    variants = []
    for v in a.variant or ["default="]:
        name, _, args = v.partition("=")
        variants.append((name.strip() or "default", shlex.split(args)))
    report: dict = {}
    lock = Lock(a.hold_s)
    for c in [s.strip() for s in a.conditions.split(",") if s.strip()]:
        rows_by_v: dict[str, list[dict]] = {n: [] for n, _ in variants}
        cpu_by_v: dict[str, list[float]] = {n: [] for n, _ in variants}
        for i, e in enumerate(entries, 1):
            if not e["audio"] or not pathlib.Path(e["audio"]).exists():
                continue
            base = track_argv(e, conds[c]["args"])
            gt = score.read_beats(pathlib.Path(e["beats"]))
            lock.take()
            if True:
                for name, vargs in variants:
                    ap = argparse.ArgumentParser()
                    bs.lcm.add_stack_args(ap)
                    bs.add_candidate_args(ap)
                    full = bs.expand_preset(base + vargs)
                    va = ap.parse_args(full)
                    va.argv = full
                    arrays, meta = bs.stage1(va, cache_on=True)
                    res = bs.build_result(va, arrays, meta)
                    if a.write:
                        d = OUT / "sweep" / f"{a.subset}-{c}-{name}"
                        bs.lcm.write(d / f"{run_batch.safe(e['dataset'])}__{run_batch.safe(e['id'])}.json", res)
                    row = score.score_one(res, gt - float(res.get("start_s") or 0.0))
                    row["n_gated_all"] = sum(1 for f in res["flashes"] if f[2] == "beat")
                    rows_by_v[name].append(row)
                    cpu_by_v[name].append(res["cpu"]["candidate_ms_per_s"])
            if a.verbose:
                print(f"[{c} {i}/{len(entries)}] {e['dataset']}/{e['id']} (lock waits so far {lock.waited:.0f} s)",
                      flush=True)
        lock.release()
        print(f"\n=== {a.subset} {c}: {conds[c]['what']} ({len(entries)} tracks)")
        hdr = ("variant", "n", "F", "CMLt", "AMLt", "hit25", "cover", "prec", "precPool", "wrong", "tLock", "locked",
               "acc1", "acc2", "exprGated", "exprFalse", "cpu")
        print(" | ".join(hdr))
        rep_c = {}
        for name, _ in variants:
            rs = rows_by_v[name]
            steady = [r for r in rs if not baseline.is_expressive(r)]
            expr = [r for r in rs if baseline.is_expressive(r)]
            g = baseline.agg(steady)
            xg = sum(1 for r in expr if r.get("n_gated_0", 0) > 0)
            xf = sum(1 for r in expr if baseline.finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
            cpu = float(np.mean(cpu_by_v[name])) if cpu_by_v[name] else math.nan
            vals = [name, g["n"], g["f"], g["cmlt"], g["amlt"], g["hit25"], g["cover_beat"], g["prec_beat"],
                    g["prec_pooled"], g["wrong_lock"], g["t_lock_med"], g["locked"], g["acc1"], g["acc2"],
                    f"{xg}/{len(expr)}", f"{xf}/{len(expr)}", cpu]
            print(" | ".join(baseline.fmt(v, "{:d}") if isinstance(v, int) else
                             (f"{v:.2f}" if isinstance(v, float) else str(v)) for v in vals), flush=True)
            rep_c[name] = {"steady": g, "expressive_gated": xg, "expressive_false": xf, "n_expr": len(expr),
                           "cpu_ms_per_s": cpu, "tracks": [{k: r.get(k) for k in ("dataset", "id", "genre", "f_0",
                                                                                     "cover_beat_0", "prec_beat_0",
                                                                                     "wrong_lock_0", "t_lock_0",
                                                                                     "est_bpm_0", "gt_bpm_0",
                                                                                     "hit25_0")} for r in rs]}
        report[c] = rep_c
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(report, indent=1, default=lambda v: None))
    return 0


def cmd_report(a) -> int:
    """baseline.py's numbers for result dirs: steady-beat means, the expressive sets, the bars (C), next to the
    baseline's own (baseline-<cond>/) on the same tracks."""
    import numpy as np
    dirs = [pathlib.Path(d).expanduser() for d in a.dirs]
    print("dir | n | F | CMLt | AMLt | hit25 | cover | prec | precPool | wrong | tLock | locked | tLockAll | "
          "expr gated/false | cpu ms/s")
    for d in dirs:
        rows = baseline.load_rows(d)
        if not rows:
            print(f"{d}: no results")
            continue
        ids = {(r["dataset"], r["id"]) for r in rows}
        cond = "C" if any("-C" in part for part in (d.name,)) else "A"
        ref = [r for r in baseline.load_rows(CACHE / f"baseline-{cond}") if (r["dataset"], r["id"]) in ids] \
            if (CACHE / f"baseline-{cond}").exists() else []
        cpu = []
        for p in d.glob("*.json"):
            try:
                c = json.loads(p.read_text()).get("cpu") or {}
                if "candidate_ms_per_s" in c:
                    cpu.append(c["candidate_ms_per_s"])
            except (OSError, ValueError):
                pass
        for name, rs in ((str(d), rows), (f"  baseline-{cond} (same tracks)", ref)):
            if not rs:
                continue
            st = [r for r in rs if not baseline.is_expressive(r)]
            ex = [r for r in rs if r["dataset"] in baseline.NO_FALSE_LOCK or baseline.is_expressive(r)]
            g = baseline.agg(st)
            xg = sum(1 for r in ex if r.get("n_gated_0", 0) > 0)
            xf = sum(1 for r in ex if baseline.finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
            print(f"{name} | {g['n']} | {g['f']:.2f} | {g['cmlt']:.2f} | {g['amlt']:.2f} | {g['hit25']:.2f} | "
                  f"{g['cover_beat']:.2f} | {g['prec_beat']:.2f} | {g['prec_pooled']:.2f} | {g['wrong_lock']:.2f} | "
                  f"{g['t_lock_med']:.1f} | {g['locked']:.2f} | {g['t_lock_med_all']:.1f} | {xg}/{xf} of {len(ex)} | "
                  + (f"{np.mean(cpu):.1f}" if cpu and rs is rows else "-"))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("run", "sweep", "report"))
    ap.add_argument("dirs", nargs="*", help="report: result dirs (e.g. alt-beatnet/test-C-room)")
    ap.add_argument("--subset", choices=tuple(SUBSETS), default="dev")
    ap.add_argument("--conditions", default="A,C")
    ap.add_argument("--datasets", default=None)
    ap.add_argument("--tag", default="")
    ap.add_argument("--variant", action="append", help="sweep: NAME=beatnet_stack.py options (repeatable)")
    ap.add_argument("--write", action="store_true", help="sweep: also write each variant's results (score.py-able)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", default=None, help="sweep: write the aggregates here")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--allow-test", action="store_true")
    ap.add_argument("--hold-s", type=float, default=45.0, help="sweep: let the CPU lock go after this long")
    argv = list(sys.argv[1:] if argv is None else argv)
    cut = argv.index("--") if "--" in argv else len(argv)
    a = ap.parse_args(argv[:cut])
    extra = argv[cut + 1:]
    if a.cmd == "run":
        return cmd_run(a, extra)
    if a.cmd == "report":
        return cmd_report(a)
    return cmd_sweep(a)


if __name__ == "__main__":
    raise SystemExit(main())
