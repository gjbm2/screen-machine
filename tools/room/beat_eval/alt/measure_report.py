#!/usr/bin/env python3
"""The held-out measure's one table: our clock (the baseline) and every candidate's best variant on
baseline-subset.json, conditions A and C, scored as baseline.py's report scores them, with each one's CPU beside it.

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/measure_report.py [--out DIR]

Rows come from ~/.cache/screen-machine/beat-eval/<dir>/ (``ROWS`` below: improve-baseline-{A,C} = baseline-{A,C}
(C with one stale-seed track replaced: its README.txt) and
improve-<candidate>-{A,C}), each result scored by score.py through baseline.load_rows and aggregated by
baseline.agg: steady-beat music (not ASAP, not GTZAN classical) as means per track from the music's start; tLock the
median over the tracks that locked; prec = precision while gated "beat" (per-track mean, pooled in brackets). The
expressive sets (3 ASAP pieces, 2 GTZAN classical): the share of beats gated "beat" (mean per track), the pooled
precision of their gated flashes and the false locks (>= 30 % of gated time on a wrong lock). Condition C is broken
down per dataset. A candidate is compared like for like: only the tracks every row has in that condition (n says how
many). CPU: the rhythm lane's ms per second of music, single thread, on 3 tracks in C
(improve-cpu/*.cpu.json: alt/lane_cpu.py for the run_stack-based clocks, the BeatNet runner's own stage CPU), the
mean over the tracks (and rounds) measured.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import pathlib
import sys

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_k, "1")

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import baseline  # noqa: E402

CACHE = baseline.CACHE
# label, dir stem ({cond} filled), CPU key in improve-cpu (None: none measured)
ROWS = [
    ("our clock (baseline)", "improve-baseline-{cond}", "clock2"),
    ("madmom_dbn best (8 nets)", "improve-madmom_dbn-{cond}", "madmom_dbn_best"),
    ("madmom_dbn best2 (2 nets, replayed)", "improve-madmom_dbn_best2-{cond}", "madmom_dbn_best2"),
    ("beatnet room", "improve-beatnet-{cond}", "beatnet"),
    ("ourclock nets=8+b+a", "improve-ourclock-{cond}", "clock8"),
    ("micfe derev_trans_lo_h480 + our clock", "improve-micfe-{cond}", "micfe"),
]
CPU_KEYS = ("madmom_dbn_best2", "madmom_dbn_best", "beatnet_stream", "beatnet", "clock2", "clock8", "micfe")
COLS = (("n", "n", "{:d}"), ("F", "f", "{:.2f}"), ("CMLt", "cmlt", "{:.2f}"), ("AMLt", "amlt", "{:.2f}"),
        ("hit25", "hit25", "{:.2f}"), ("cover", "cover_beat", "{:.2f}"), ("prec gated (pooled)", "prec", None),
        ("wrong-lock", "wrong_lock", "{:.3f}"), ("tLock s", "t_lock_med", "{:.1f}"), ("locked", "locked", "{:.2f}"),
        ("Acc1", "acc1", "{:.2f}"), ("Acc2", "acc2", "{:.2f}"))


def f(v, spec: str) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-"
    return spec.format(int(v)) if spec == "{:d}" else spec.format(v)


def cells(g: dict) -> list[str]:
    out = []
    for _, k, s in COLS:
        if k == "prec":
            out.append(f"{f(g.get('prec_beat'), '{:.2f}')} ({f(g.get('prec_pooled'), '{:.2f}')})")
        else:
            out.append(f(g.get(k), s))
    return out


def expressive(rows: list[dict]) -> dict:
    ex = [r for r in rows if baseline.is_expressive(r)]
    ng = [r.get("n_gated_0", 0) or 0 for r in ex]
    pb = [r.get("prec_beat_0") for r in ex]
    tot = sum(n for n, p in zip(ng, pb) if n and baseline.finite(p))
    prec = sum(n * p for n, p in zip(ng, pb) if n and baseline.finite(p)) / tot if tot else math.nan
    cov = [r.get("cover_beat_0") for r in ex if baseline.finite(r.get("cover_beat_0"))]
    false = sum(1 for r in ex if baseline.finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
    return {"n": len(ex), "gated_tracks": sum(1 for n in ng if n), "share_gated": sum(cov) / len(cov) if cov else math.nan,
            "prec_pooled": prec, "n_gated": sum(ng), "false_locks": false,
            "per_track": {r["id"]: {"gated": r.get("n_gated_0", 0), "cover": r.get("cover_beat_0"),
                                    "prec": r.get("prec_beat_0"), "wrong": r.get("wrong_lock_0")} for r in ex}}


def cpu_table(d: pathlib.Path) -> dict:
    """runner key -> {"music": mean ms per s of music, "n": files, "tracks": {...}}."""
    by = collections.defaultdict(list)
    for p in sorted(d.glob("*.cpu.json")):
        try:
            c = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        rest = p.name.split("-", 1)[1]                    # r<round>-<runner>-<dataset>__<id>.cpu.json
        key = next((k for k in CPU_KEYS if rest.startswith(k + "-")), rest)
        if "candidate_ms_per_s" in c:                      # BeatNet's own stage CPU (process time per audio s)
            v = float(c["candidate_ms_per_s"])
            extra = {k: c.get(k) for k in ("lane", "feat", "crnn", "pf", "line", "crnn_mode")}
        else:
            v = float(c["lane_ms_per_s_music"])
            extra = {k: c.get(k) for k in ("clock_ms_per_s_preroll", "dsp_ms_per_s_music", "fe_ms_per_s")}
        by[key].append({"id": c.get("id"), "ms_per_s": v, **extra})
    return {k: {"music": sum(x["ms_per_s"] for x in v) / len(v), "n": len(v), "tracks": v} for k, v in by.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=pathlib.Path, default=CACHE / "improve-report")
    ap.add_argument("--conds", default="C,A")
    ap.add_argument("--own-tracks", action="store_true", help="each row on the tracks it has (not like for like)")
    a = ap.parse_args(argv)
    cpu = cpu_table(CACHE / "improve-cpu")
    md: list[str] = ["# Held-out measure: baseline-subset.json (51 tracks), conditions C and A", ""]
    rep: dict = {"cpu": cpu, "conditions": {}}
    base_cpu = cpu.get("clock2", {}).get("music")
    md.append("CPU = the rhythm lane's ms of CPU per second of music, one thread, nice 19 under the 4K render, mean of "
              "3 tracks in C (x = multiple of our clock's lane). BeatNet's is its replay's stage CPU (the CRNN over "
              "the whole track at once); its frame-by-frame torch CRNN (the live upper bound) is in the CPU table.")
    for cond in [c.strip() for c in a.conds.split(",") if c.strip()]:
        data = {}
        for label, stem, ck in ROWS:
            d = CACHE / stem.format(cond=cond)
            if d.exists():
                rs = baseline.load_rows(d)
                if rs:
                    data[label] = (rs, ck, d)
        if not data:
            continue
        common = set.intersection(*[{(r["dataset"], r["id"]) for r in rs} for rs, _, _ in data.values()])
        if a.own_tracks:                                  # interim: every row on all the tracks it has
            common = set.union(*[{(r["dataset"], r["id"]) for r in rs} for rs, _, _ in data.values()])
        stacks = {label: dict(collections.Counter(r["stack"] for r in rs)) for label, (rs, _, _) in data.items()}
        md.append(f"\n## Condition {cond}: {baseline.conditions()[cond]['what']}\n")
        md.append(f"Tracks every row has: {len(common)} (per row: " +
                  ", ".join(f"{lab} {len(rs)}" for lab, (rs, _, _) in data.items()) + ").\n")
        md.append("### Steady-beat music\n")
        head = ["row"] + [h for h, _, _ in COLS] + ["CPU ms/s (x)"]
        md += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        crep: dict = {"steady": {}, "expressive": {}, "datasets": {}, "stacks": stacks, "n_common": len(common)}
        for label, (rs, ck, d) in data.items():
            rs_c = [r for r in rs if (r["dataset"], r["id"]) in common]
            g = baseline.agg([r for r in rs_c if not baseline.is_expressive(r)])
            c = cpu.get(ck, {}).get("music") if ck else None
            cs = "-" if c is None else (f"{c:.0f} (x{c / base_cpu:.2f})" if base_cpu else f"{c:.0f}")
            md.append("| " + " | ".join([label] + cells(g) + [cs]) + " |")
            crep["steady"][label] = {**g, "cpu_ms_per_s": c, "dir": str(d)}
        md.append("\n### Expressive sets (ASAP x3, GTZAN classical x2): should stay off the beat gate\n")
        md.append("| row | n | tracks gated | share gated (mean) | gated flashes | prec gated (pooled) | false locks |")
        md.append("|---|---|---|---|---|---|---|")
        for label, (rs, ck, d) in data.items():
            e = expressive([r for r in rs if (r["dataset"], r["id"]) in common])
            md.append(f"| {label} | {e['n']} | {e['gated_tracks']} | {f(e['share_gated'], '{:.3f}')} | {e['n_gated']} | "
                      f"{f(e['prec_pooled'], '{:.2f}')} | {e['false_locks']} |")
            crep["expressive"][label] = e
        md.append("\nPer expressive track (gated flashes / share gated / precision / wrong-lock):\n")
        ids = sorted({i for lab, (rs, _, _) in data.items() for i in expressive(rs)["per_track"]})
        md.append("| row | " + " | ".join(i[:28] for i in ids) + " |")
        md.append("|---|" + "---|" * len(ids))
        for label, (rs, ck, d) in data.items():
            pt = expressive([r for r in rs if (r["dataset"], r["id"]) in common])["per_track"]
            md.append(f"| {label} | " + " | ".join(
                (f"{pt[i]['gated']} / {f(pt[i]['cover'], '{:.2f}')} / {f(pt[i]['prec'], '{:.2f}')} / "
                 f"{f(pt[i]['wrong'], '{:.2f}')}") if i in pt else "-" for i in ids) + " |")
        if cond == "C":
            md.append("\n### Condition C per dataset (steady-beat tracks; asap = expressive, all columns)\n")
            dss = sorted({r["dataset"] for rs, _, _ in data.values() for r in rs})
            head = ["dataset", "row"] + [h for h, _, _ in COLS]
            md += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
            for ds in dss:
                for label, (rs, ck, d) in data.items():
                    sel = [r for r in rs if (r["dataset"], r["id"]) in common and r["dataset"] == ds
                           and (ds == "asap" or not baseline.is_expressive(r))]
                    if not sel:
                        continue
                    g = baseline.agg(sel)
                    md.append("| " + " | ".join([ds, label] + cells(g)) + " |")
                    crep["datasets"].setdefault(ds, {})[label] = g
            md.append("\n### The bars, condition C, steady-beat music\n")
            md.append("| row | prec >= 0.9 (per track / pooled) | cover >= 0.7 | tLock <= 8 s (locked / never=never) | "
                      "expressive false locks (ASAP) | clean (A) not worse |")
            md.append("|---|---|---|---|---|---|")
            crep["bars"] = {}
            for label, (rs, ck, d) in data.items():
                g = crep["steady"][label]
                ex = [r for r in rs if (r["dataset"], r["id"]) in common and r["dataset"] in baseline.NO_FALSE_LOCK]
                fl = sum(1 for r in ex if baseline.finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
                ok = lambda b: "yes" if b else "NO"   # noqa: E731
                tl = g["t_lock_med_all"]
                md.append(f"| {label} | {f(g['prec_beat'], '{:.2f}')} / {f(g['prec_pooled'], '{:.2f}')} "
                          f"{ok(g['prec_beat'] >= 0.9)} | {f(g['cover_beat'], '{:.2f}')} {ok(g['cover_beat'] >= 0.7)} | "
                          f"{f(g['t_lock_med'], '{:.1f}')} / {'never' if tl == math.inf else f(tl, '{:.1f}')} "
                          f"{ok(g['t_lock_med'] <= 8)} | {fl} of {len(ex)} | (see A) |")
                crep["bars"][label] = {"prec": g["prec_beat"], "prec_pooled": g["prec_pooled"],
                                       "cover": g["cover_beat"], "t_lock": g["t_lock_med"], "t_lock_all": tl,
                                       "asap_false": fl, "asap_n": len(ex)}
        md.append(f"\nStack ids per row: " + "; ".join(f"{lab}: {s}" for lab, s in stacks.items()))
        rep["conditions"][cond] = crep
    if cpu:
        md.append("\n## CPU per candidate (rhythm lane, ms of CPU per second of music, one thread)\n")
        md.append("| runner | mean | per track |")
        md.append("|---|---|---|")
        for k, v in sorted(cpu.items()):
            md.append(f"| {k} | {v['music']:.0f} | " + "; ".join(f"{t['id']} {t['ms_per_s']:.0f}" for t in v["tracks"])
                      + " |")
    text = "\n".join(md) + "\n"
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report.md").write_text(text)
    (a.out / "report.json").write_text(json.dumps(rep, indent=1, default=lambda v: None))
    print(text)
    print(f"-> {a.out}/report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
