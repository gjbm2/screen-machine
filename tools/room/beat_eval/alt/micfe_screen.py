#!/usr/bin/env python3
"""Screen micfe front-end variants on the DEV subset (never the held-out baseline subset), conditions A and C, with our
clock and with madmom's stock online DBN: which compensation helps, by how much, at what cost.

  # run (show_service's env; one track at a time under the CPU lock, nice 19, resumable)
  ~/.venvs/sm-show/bin/python tools/room/beat_eval/alt/micfe_screen.py run --variants none,coheq,trans_lo \
      [--conds A,C] [--trackers clock,dbn] [--act batch|exact] [--datasets ballroom,gtzan] [--limit N]
  # compare (score.py's scorer and baseline.py's aggregates; sm-roundtrip's env)
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/micfe_screen.py report [--variants ...] [--act batch]

Conditions as baseline.py's (A clean, gate -30; C the pc22-mic room model with its 30 s room-sound pre-roll, the
calibrated gate -48.64; also C0 = C without the pre-roll, a diagnostic: the front end's effect on the music itself,
apart from the room sound seeding a tempo before the music starts), no slow lane, the replay capped at 260 s, each track its own seed (run_batch.track_seed),
so a variant's numbers compare like with like with the baseline's conditions. Every track runs micfe_run.main
in this one process (imports and nets load once); the room model's output is cached
(~/.cache/screen-machine/beat-eval/alt-micfe/audio-cache), identical to run_stack's.

``--act exact`` (default) runs madmom per hop inside the clock, as live. ``--act batch`` gives the clock madmom's
activation computed once over the lane (micfe_run.py: about 40 % cheaper), but it differs from the live path after
every madmom reset (a quiet room ending), and the clock is chaotic enough that single tracks then go another way
(disco.00021 in C: 88 bpm exact, the right 132 batch): screening uses exact. The DBN always uses the batch
activation (it never resets).

Results: ~/.cache/screen-machine/beat-eval/alt-micfe/dev-<cond>-<variant>-<tracker>[-batch]/<dataset>__<id>.json
(run_batch.py's names), ``screen.jsonl`` per run (wall, cost). ``report`` prints, per condition, the steady-beat
means (F, CMLt, AMLt, hit25, cover, prec gated, precPool, wrong-lock, tLock, locked, acc1/acc2) per variant x
tracker, the expressive sets' gated flashes and false locks, and the front end's cost (ms per audio second,
streaming 128-frame blocks) next to them; also report.md / report.json in alt-micfe/.
"""
from __future__ import annotations

import argparse
import collections
import fcntl
import json
import math
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
REPO = HERE.parents[3]
CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval").expanduser()
OUT = CACHE / "alt-micfe"
DEV = CACHE / "dev-subset.json"
LOCK = pathlib.Path("~/.cache/screen-machine/cpu.lock").expanduser()
MODEL = REPO / "tools/room/audio_cal/runs/20261018T145602Z-pc22-mic/model/model.json"
GATE_C = -48.64
DUR_CAP = 260.0


def cond_args(c: str) -> list[str]:
    if c == "A":
        return ["--no-slow-lane"]
    if c == "C":
        return ["--degrade", str(MODEL), "--gate-ref-lufs", f"{GATE_C:g}", "--no-slow-lane"]
    if c == "C0":                                         # C without the room-sound pre-roll: the front end on the
        return ["--degrade", str(MODEL), "--gate-ref-lufs", f"{GATE_C:g}", "--no-slow-lane", "--preroll-s", "0"]
    if c == "E":
        return ["--degrade", str(MODEL), "--snr-db", "60", "--gate-ref-lufs", f"{GATE_C:g}", "--no-slow-lane"]
    raise SystemExit(f"unknown condition {c}")


def res_dir(c: str, variant: str, tracker: str, act: str, suffix: str = "") -> pathlib.Path:
    return OUT / (f"dev-{c}-{variant}-{tracker}" + ("-batch" if act == "batch" and tracker == "clock" else "")
                  + (f"-{suffix}" if suffix else ""))


def items(datasets: str | None, limit: int | None, per_dataset: int | None = None) -> list[dict]:
    doc = json.loads(DEV.read_text())
    it = doc["items"]
    if datasets:
        want = {s.strip() for s in datasets.split(",")}
        it = [i for i in it if i["dataset"] in want]
    if per_dataset:                                       # the dealer's first N per dataset: one per genre first
        seen: collections.Counter = collections.Counter()
        keep = []
        for i in it:
            if seen[i["dataset"]] < per_dataset:
                keep.append(i)
                seen[i["dataset"]] += 1
        it = keep
    return it[:limit] if limit else it


def cmd_run(a) -> int:
    import run_batch
    import micfe_run
    try:
        os.nice(19)
    except OSError:
        pass
    its = items(a.datasets, a.limit, a.per_dataset)
    jobs = []
    for c in a.conds.split(","):
        for v in a.variants.split(","):
            for tr in a.trackers.split(","):
                d = res_dir(c, v, tr, a.act, a.suffix)
                for e in its:
                    res = d / f"{run_batch.safe(e['dataset'])}__{run_batch.safe(e['id'])}.json"
                    if not res.exists():
                        jobs.append((c, v, tr, d, e, res))
    if a.track_major:                                     # every variant of a track before the next track
        order = {(e["dataset"], e["id"]): i for i, e in enumerate(its)}
        jobs.sort(key=lambda j: (order[(j[4]["dataset"], j[4]["id"])], j[0]))
    print(f"{len(jobs)} runs to do", flush=True)
    fails = 0
    lk = None
    t_got = 0.0
    t_ask = time.time()
    for c, v, tr, d, e, res in jobs:
        if lk is None:                                    # take the CPU lock for a slice of up to --slice-s
            t_ask = time.time()
            lk = LOCK.open("a")
            fcntl.flock(lk, fcntl.LOCK_EX)
            t_got = time.time()
            wait = t_got - t_ask
        else:
            wait = 0.0
        d.mkdir(parents=True, exist_ok=True)
        seed = run_batch.track_seed(0, e)
        argv = [e["audio"], "--out", str(res), "--id", e["id"], "--dataset", e["dataset"], "--genre",
                e["genre"], "--seed", str(seed), "--beats", e["beats"], "--dur-s", f"{DUR_CAP:g}",
                *cond_args(c), "--fe", v, "--tracker", tr, "--act", a.act,
                "--audio-cache", str(OUT / "audio-cache"), *a.extra.split()]
        t1 = time.time()
        try:
            rc = micfe_run.main(argv)
        except SystemExit as ex:                          # run_stack's own refusals
            rc = int(ex.code or 1)
        except Exception as ex:                           # noqa: BLE001  log it, carry on with the next
            print(f"FAILED {e['dataset']}/{e['id']}: {ex!r}", flush=True)
            rc = 99
        t2 = time.time()
        if t2 - t_got >= a.slice_s:                       # let the others have a turn
            fcntl.flock(lk, fcntl.LOCK_UN)
            lk.close()
            lk = None
        fails += rc != 0
        with (d / "screen.jsonl").open("a") as f:
            f.write(json.dumps({"id": e["id"], "dataset": e["dataset"], "rc": rc, "lock_wait_s": round(wait, 1),
                                "run_s": round(t2 - t1, 1), "at": time.strftime("%H:%M:%S")}) + "\n")
        print(f"[{c} {v} {tr}] {e['dataset']}/{e['id']}: rc {rc}, {t2 - t1:.1f} s (+{wait:.0f} s lock)", flush=True)
    if lk is not None:
        lk.close()
    return 1 if fails else 0


# -- the real calibration recording -------------------------------------------------------------------------------------
RUN = MODEL.parent.parent
REAL = {   # name: (audio, beats, what)
    "rec-music": (RUN / "rec-music.wav", RUN / "reference/beats-at-mic.beats", "the real mic, the clean 53 s of music"),
    "rec-full": (RUN / "rec.wav", OUT / "real/beats-rec-full.beats",
                 "the real mic, whole: 182 s of the room's real sound (talk, typing, the owner seeking), then the music"),
}


def cmd_real(a) -> int:
    """The calibration recording itself (no room model: this is the mic), at the calibrated gate."""
    import micfe_run
    try:
        os.nice(19)
    except OSError:
        pass
    (OUT / "real").mkdir(parents=True, exist_ok=True)
    rc_all = 0
    lk, t_got = None, 0.0
    for name, (audio, beats, _) in REAL.items():
        for v in a.variants.split(","):
            for tr in a.trackers.split(","):
                res = OUT / "real" / f"{name}-{v}-{tr}.json"
                if res.exists():
                    continue
                argv = [str(audio), "--out", str(res), "--id", name, "--dataset", "real", "--genre", name,
                        "--beats", str(beats), "--seed", "0", "--gate-ref-lufs", f"{GATE_C:g}", "--no-slow-lane",
                        "--fe", v, "--fe-model", str(MODEL), "--tracker", tr, "--act", a.act]
                if lk is None:                            # the CPU lock in slices, as cmd_run
                    lk = LOCK.open("a")
                    fcntl.flock(lk, fcntl.LOCK_EX)
                    t_got = time.time()
                t1 = time.time()
                try:
                    rc = micfe_run.main(argv)
                except Exception as ex:                   # noqa: BLE001
                    print(f"FAILED {name} {v} {tr}: {ex!r}", flush=True)
                    rc = 99
                if time.time() - t_got >= a.slice_s:
                    fcntl.flock(lk, fcntl.LOCK_UN)
                    lk.close()
                    lk = None
                print(f"[real {name} {v} {tr}] rc {rc}, {time.time() - t1:.1f} s", flush=True)
                rc_all = rc_all or rc
    if lk is not None:
        lk.close()
    return rc_all


def real_rows(variants: list[str], trackers: list[str]) -> list[str]:
    """The real recording's table: score.py's numbers, F/hit25 over the music only (rec-full's room sound first)."""
    import numpy as np
    import mir_eval
    import score
    md = ["| recording | variant | tracker | F (music) | hit25 | cover | prec | wrong | tLock | bpm | fe ms/s |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, (_, beats, _) in REAL.items():
        for v in variants:
            for tr in trackers:
                p = OUT / "real" / f"{name}-{v}-{tr}.json"
                if not p.exists():
                    continue
                r = json.loads(p.read_text())
                gt = score.read_beats(pathlib.Path(r["beats"]))
                row = score.score_one(r, gt)
                ff = r["flash_fields"]
                ft = np.array([f[ff.index("t")] for f in r["flashes"]], float)
                t0 = float(gt[0]) - 0.5
                fm = ft[ft >= t0]
                F = mir_eval.beat.f_measure(gt, fm, 0.07) if len(fm) else 0.0
                c = r.get("candidate") or {}
                md.append(f"| {name} | {v} | {tr} | {F:.2f} | {row.get('hit25_0', float('nan')):.2f} | "
                          f"{row.get('cover_beat_0', float('nan')):.2f} | {row.get('prec_beat_0', float('nan')):.2f} | "
                          f"{row.get('wrong_lock_0', float('nan')):.2f} | {row.get('t_lock_0', float('nan')):.1f} | "
                          f"{row.get('est_bpm_0', float('nan')):.0f} | {c.get('fe_cost_ms_per_s', 0) or 0:.0f} |")
    return md


# -- the report (sm-roundtrip) ------------------------------------------------------------------------------------------
COLS = (("F", "f", "{:.2f}"), ("CMLt", "cmlt", "{:.2f}"), ("AMLt", "amlt", "{:.2f}"), ("hit25", "hit25", "{:.2f}"),
        ("cover", "cover_beat", "{:.2f}"), ("prec", "prec_beat", "{:.2f}"), ("precPool", "prec_pooled", "{:.2f}"),
        ("wrong", "wrong_lock", "{:.2f}"), ("tLock", "t_lock_med", "{:.1f}"), ("locked", "locked", "{:.2f}"),
        ("acc1", "acc1", "{:.2f}"), ("acc2", "acc2", "{:.2f}"))


def cand_cost(d: pathlib.Path) -> dict:
    fe, tr, n = [], [], 0
    for p in d.glob("*.json"):
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        c = r.get("candidate") or {}
        if isinstance(c.get("fe_cost_ms_per_s"), (int, float)) and math.isfinite(c["fe_cost_ms_per_s"]):
            fe.append(c["fe_cost_ms_per_s"])
        if isinstance(c.get("tracker_ms_per_s"), (int, float)):
            tr.append(c["tracker_ms_per_s"])
        n += 1
    med = lambda v: sorted(v)[len(v) // 2] if v else float("nan")        # noqa: E731
    return {"fe_ms_s": med(fe), "tracker_ms_s": med(tr), "n_files": n}


def cmd_report(a) -> int:
    import baseline
    md = [f"# micfe screen on the dev subset ({DEV.name}), act {a.act}", ""]
    rep: dict = {}
    conds = a.conds.split(",")
    for c in conds:
        md += [f"## Condition {c}", ""]
        head = ["variant", "tracker", "n"] + [h for h, _, _ in COLS] + ["exprGated", "exprFalse", "fe ms/s", "trk ms/s",
                                                                        "dF vs none (+/-)", "dAMLt", "dHit25"]
        md += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        for v in a.variants.split(","):
            for tr in a.trackers.split(","):
                d = res_dir(c, v, tr, a.act, a.suffix)
                if not d.exists():
                    continue
                rows = baseline.load_rows(d)
                if not rows:
                    continue
                base = {(r["dataset"], r["id"]): r for r in baseline.load_rows(res_dir(c, "none", tr, a.act))} \
                    if v != "none" and res_dir(c, "none", tr, a.act).exists() else {}
                if a.common and base:                            # only the tracks both have
                    rows = [r for r in rows if (r["dataset"], r["id"]) in base]
                st = [r for r in rows if not baseline.is_expressive(r)]
                ex = [r for r in rows if baseline.is_expressive(r)]
                g = baseline.agg(st)
                ng = sum(r.get("n_gated_0", 0) for r in ex)
                fl = sum(1 for r in ex if baseline.finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
                cost = cand_cost(d)
                rep.setdefault(c, {})[f"{v}/{tr}"] = {**g, "expr_gated": ng, "expr_false": fl, **cost, "n_expr": len(ex)}
                dd = {}
                for k in ("f", "amlt", "hit25"):
                    pairs = [(r.get(f"{k}_0"), base[(r["dataset"], r["id"])].get(f"{k}_0")) for r in st
                             if (r["dataset"], r["id"]) in base]
                    pairs = [(x, y) for x, y in pairs if baseline.finite(x) and baseline.finite(y)]
                    dd[k] = (sum(x - y for x, y in pairs) / len(pairs), sum(x > y + 0.02 for x, y in pairs),
                             sum(x < y - 0.02 for x, y in pairs)) if pairs else None
                rep[c][f"{v}/{tr}"]["delta_vs_none"] = dd
                cells = [v, tr, str(g["n"])] + [baseline.fmt(g.get(k), s) for _, k, s in COLS] + \
                        [str(ng), f"{fl}/{len(ex)}", baseline.fmt(cost["fe_ms_s"], "{:.1f}"),
                         baseline.fmt(cost["tracker_ms_s"], "{:.0f}")] + \
                        [(f"{dd['f'][0]:+.3f} ({dd['f'][1]}/{dd['f'][2]})" if dd.get("f") else "-"),
                         (f"{dd['amlt'][0]:+.3f}" if dd.get("amlt") else "-"),
                         (f"{dd['hit25'][0]:+.3f}" if dd.get("hit25") else "-")]
                md.append("| " + " | ".join(cells) + " |")
        md.append("")
    rr = real_rows(a.variants.split(","), a.trackers.split(","))
    if len(rr) > 2:
        md += ["## The real calibration recording (no model), gate -48.64", ""] + rr + [""]
    text = "\n".join(md)
    print(text)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"-{a.tag}" if a.tag else ""
    (OUT / f"report-{a.act}{tag}.md").write_text(text + "\n")
    (OUT / f"report-{a.act}{tag}.json").write_text(json.dumps(rep, indent=1, default=lambda v: None))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=("run", "real", "report"))
    ap.add_argument("--variants", default="none")
    ap.add_argument("--conds", default="C,A")
    ap.add_argument("--trackers", default="clock,dbn")
    ap.add_argument("--act", choices=("batch", "exact"), default="exact")
    ap.add_argument("--datasets", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--per-dataset", type=int, default=None, help="only the first N tracks of each dataset")
    ap.add_argument("--tag", default="")
    ap.add_argument("--extra", default="", help="run: more micfe_run.py options for every track (e.g. --fe-dsp)")
    ap.add_argument("--suffix", default="", help="results dirs dev-<cond>-<variant>-<tracker>-<suffix> (with --extra)")
    ap.add_argument("--common", action="store_true", help="report: only the tracks 'none' also has")
    ap.add_argument("--slice-s", type=float, default=45.0, help="hold the CPU lock for at most this long at a time")
    ap.add_argument("--track-major", action="store_true", help="all variants of a track, then the next track")
    a = ap.parse_args(argv)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(k, "2")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    return {"run": cmd_run, "real": cmd_real, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
