#!/usr/bin/env python3
"""The beat stack's baseline in this room: one fixed subset, four conditions, one comparison report.

The stack gets improved later; this pins down what "before" was, so the same tracks and conditions can be rerun
after each change and compared like with like:

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py subset          # writes the subset index
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py run [--conditions C,A,B,D] [--datasets a,b] [--tag T]
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/baseline.py report [--conditions A,B,C,D] [--tag T]

``subset`` deals ``--counts`` tracks per dataset from the combined index, round-robin over its genres, the biggest
genres first (run_batch.pick takes them alphabetically, which leaves Harmonix's Pop out of a 6-track pick), each
genre shuffled by ``--seed``; ballroom's duplicates are left out. It writes
~/.cache/screen-machine/beat-eval/baseline-subset.json (an index run_batch.py reads as it is). A dataset that is not
there (SMC is offline) is reported and skipped.

``run`` runs each condition with run_batch.py on that index (each track under the CPU lock at nice 19, resumable):
  A  clean, the default gate (dsp's gate_ref_lufs -30)                       slow lane off
  B  through the room model (config/room/audio_cal.json's pc22-mic model), default gate          off
  C  room model, the calibrated gate: -30 + (music_lufs + 30) = music_lufs, as show_service's
     GateOffsetWatch sets it live for pc22-mic (-47 if the file has none)                          off
  D  as C with the slow lane on (Beat This! votes, as live)
  E  a control: as C with the model's room sound taken out (--snr-db 60), so the mic chain alone degrades the music
``--datasets`` runs a condition on those datasets' tracks only (18 Oct: D on the full-length sets, where the slow
lane gets its turns; a 30 s excerpt gets three, mostly over the pre-roll; E on the excerpts).
Each track has its own fixed seed (run_batch --track-seeds: --seed + a hash of its id), the same in every
condition, so the room model's noise stretch, processing draw and dropouts differ from track to track (with one
seed for all, the whole subset hears one 30 s stretch of room sound and the result swings with it). Every track's
replay is capped at ``--dur-cap`` s (260: every Harmonix pick plays whole; the long TapCorrect and
ASAP pieces stop there). Results: ~/.cache/screen-machine/beat-eval/baseline-<cond>[-<tag>]/, plus baseline.json
(the condition, its run_batch arguments and the stack fingerprint at the start). C runs first: it is the verdict.

``report`` scores every condition with score.py's own scorer (whole replay, from the music's start: what the room
sees) and prints, per condition, overall / per dataset / per dataset-genre: F, CMLt, AMLt, the +-25 ms hit rate,
gate coverage (gate "beat"), precision while gated (per-track mean; and pooled over all gated flashes), wrong-lock
share, time to lock (median over the tracks that locked; share locked), bpm Acc1/Acc2. Then the 10 worst tracks in
C with a diagnosis, the expressive sets (did the gate stay off?), and the bars for steady-beat music in C. It also
writes report.md and report.json in ~/.cache/screen-machine/beat-eval/baseline-report[-<tag>]/.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import os
import pathlib
import random
import subprocess
import sys
from typing import Optional

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):    # the host renders 4K
    os.environ.setdefault(_k, "1")

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
import run_batch  # noqa: E402

CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval").expanduser()
COMBINED = pathlib.Path("~/.cache/screen-machine/beat-datasets/index.json").expanduser()
SUBSET = CACHE / "baseline-subset.json"
AUDIO_CAL = REPO / "config" / "room" / "audio_cal.json"
STREAM = "pc22-mic"
COUNTS = "ballroom=20,gtzan=20,harmonix=6,tapcorrect=2,asap=3,smc=4"
DUR_CAP_S = 260.0
SEED = 0
EXPRESSIVE = {("asap", ""), ("smc", ""), ("gtzan", "classical")}   # (dataset, genre or "" = all): no steady beat
NO_FALSE_LOCK = ("asap", "smc")                                    # the sets whose gate should stay off


# -- the subset --------------------------------------------------------------------------------------------------------
def deal(entries: list[dict], n: int, seed: int) -> list[dict]:
    """``n`` entries round-robin over their genres, the biggest genre first (ties by ``seed``), each shuffled."""
    rng = random.Random(seed)
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for e in entries:
        groups[e["genre"]].append(e)
    names = sorted(groups)
    rng.shuffle(names)
    names.sort(key=lambda g: -len(groups[g]))                    # stable: the shuffle breaks the ties
    for g in names:
        groups[g].sort(key=lambda e: e["id"])
        rng.shuffle(groups[g])
    out: list[dict] = []
    while len(out) < n and any(groups[g] for g in names):
        for g in names:
            if groups[g] and len(out) < n:
                out.append(groups[g].pop())
    return out


def cmd_subset(a: argparse.Namespace) -> int:
    _, entries = run_batch.load_index(COMBINED)
    entries = [e for e in entries if not e["duplicate"]]
    counts = {k.strip(): int(v) for k, v in (p.split("=") for p in a.counts.split(",") if p.strip())}
    items, missing = [], []
    for ds, n in counts.items():
        have = [e for e in entries if e["dataset"] == ds and e["audio"] and pathlib.Path(e["audio"]).exists()]
        if not have:
            missing.append(ds)
            continue
        for e in deal(have, n, a.seed):
            items.append({"id": e["id"], "dataset": ds, "genre": e["genre"], "audio": str(e["audio"]),
                          "beats": str(e["beats"]), "duration_s": e.get("duration_s")})
    doc = {"name": "baseline", "seed": a.seed, "counts": counts, "missing": missing, "items": items}
    SUBSET.parent.mkdir(parents=True, exist_ok=True)
    SUBSET.write_text(json.dumps(doc, indent=1))
    by = collections.Counter(i["dataset"] for i in items)
    secs = collections.Counter()
    for i in items:
        secs[i["dataset"]] += min(float(i["duration_s"] or 0), a.dur_cap)
    print(f"{len(items)} tracks -> {SUBSET}: " + ", ".join(f"{k} {v} ({secs[k]:.0f} s)" for k, v in by.items())
          + (f"; not available: {', '.join(missing)}" if missing else ""))
    return 0


# -- the conditions ----------------------------------------------------------------------------------------------------
def calibration() -> tuple[str, float, str]:
    """(model path, the calibrated gate reference, where it came from) for STREAM."""
    try:
        s = json.loads(AUDIO_CAL.read_text())["streams"][STREAM]
        model = str(REPO / s["model"])
        lufs = s.get("summary", {}).get("music_lufs")
        if isinstance(lufs, (int, float)) and math.isfinite(lufs):
            return model, round(float(lufs), 2), f"{AUDIO_CAL.name} {STREAM} summary.music_lufs"
        return model, -47.0, "-47 (no music_lufs in audio_cal.json)"
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f"no room calibration for {STREAM} in {AUDIO_CAL}: {e}")


def conditions() -> dict[str, dict]:
    model, ref, src = calibration()
    return {
        "A": {"what": "clean, default gate (-30), slow lane off", "args": ["--no-slow-lane"]},
        "B": {"what": "room model, default gate (-30), slow lane off", "args": ["--degrade", model, "--no-slow-lane"]},
        "C": {"what": f"room model, calibrated gate ({ref:g}: {src}), slow lane off",
              "args": ["--degrade", model, "--gate-ref-lufs", f"{ref:g}", "--no-slow-lane"]},
        "D": {"what": f"room model, calibrated gate ({ref:g}), slow lane on",
              "args": ["--degrade", model, "--gate-ref-lufs", f"{ref:g}"]},
        "E": {"what": f"room model without its room sound (snr 60 dB), calibrated gate ({ref:g}), slow lane off",
              "args": ["--degrade", model, "--snr-db", "60", "--gate-ref-lufs", f"{ref:g}", "--no-slow-lane"]},
    }


def cond_dir(c: str, tag: str) -> pathlib.Path:
    return CACHE / (f"baseline-{c}" + (f"-{tag}" if tag else ""))


def cmd_run(a: argparse.Namespace) -> int:
    if not SUBSET.exists():
        raise SystemExit(f"{SUBSET} is missing: run `baseline.py subset` first")
    conds = conditions()
    rc = 0
    for c in [s.strip() for s in a.conditions.split(",") if s.strip()]:
        spec = conds[c]
        out = cond_dir(c, a.tag)
        out.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(HERE / "run_batch.py"), str(SUBSET), "--seed", str(a.seed), "--track-seeds",
               "--out", str(out), "--dur-s", f"{a.dur_cap:g}", *spec["args"]]
        if a.datasets:
            cmd += ["--datasets", a.datasets]
        meta_p = out / "baseline.json"
        if not meta_p.exists():                       # the first start's stack; a resume keeps it
            meta_p.write_text(json.dumps({"condition": c, **spec, "cmd": cmd, "stack": stack_fingerprint()}, indent=1))
        print(f"\n=== condition {c}: {spec['what']}\n{' '.join(cmd)}", flush=True)
        p = subprocess.run(cmd, cwd=str(REPO))
        rc = rc or p.returncode
    return rc


def stack_fingerprint() -> dict:
    """run_stack.py's fingerprint (its STACK_FILES) without importing show_service: run_stack needs sm-show's env."""
    import ast
    import hashlib
    tree = ast.parse((HERE / "run_stack.py").read_text())
    files = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "STACK_FILES" for t in n.targets))
    out: dict = {"files": {}}
    for f in files:
        try:
            out["files"][f] = hashlib.sha256((REPO / f).read_bytes()).hexdigest()[:12]
        except OSError:
            out["files"][f] = None
    out["id"] = "s" + hashlib.sha256(json.dumps(out["files"], sort_keys=True).encode()).hexdigest()[:10]
    p = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], cwd=str(REPO), capture_output=True, text=True)
    out["head"] = p.stdout.strip()
    return out


# -- the report --------------------------------------------------------------------------------------------------------
NAN = float("nan")


def finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def load_rows(d: pathlib.Path) -> list[dict]:
    """score.py's row per result in ``d``, plus what the diagnosis needs from the result itself."""
    import numpy as np
    import score
    rows = []
    for p in score.result_files([d]):
        if p.name == "baseline.json":
            continue
        try:
            r = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if "flashes" not in r or not r.get("beats"):
            continue
        gt = score.read_beats(pathlib.Path(r["beats"])) - float(r.get("start_s") or 0.0)
        row = score.score_one(r, gt)
        ff = r["feature_fields"]
        feats = [f for f in r["features"] if 0.0 <= f[0] <= r["track_s"]]
        if feats:
            row["quiet_share"] = float(np.mean([bool(f[ff.index("quiet")]) for f in feats]))
            g = np.array([f[ff.index("gate")] or 0.0 for f in feats], float)
            row["dsp_gate_mean"] = float(g.mean())
            row["dsp_gate_low"] = float(np.mean(g < 0.5))
            lu = np.array([f[ff.index("lufs_s")] for f in feats if f[ff.index("lufs_s")] is not None], float)
            row["lufs_med"] = float(np.median(lu)) if len(lu) else NAN
        bf = r["body_fields"]
        st = collections.Counter()
        cv, cw = [], []
        for i, b in enumerate(r["bodies"]):
            h1 = r["bodies"][i + 1][0] if i + 1 < len(r["bodies"]) else r["track_s"]
            lo, hi = max(0.0, b[0]), min(r["track_s"], h1)
            if hi > lo:
                st[b[bf.index("state")]] += hi - lo
                cv.append(float(b[bf.index("conf")] or 0.0))
                cw.append(hi - lo)
        tot = sum(st.values()) or 1.0
        row["state_share"] = {k: round(v / tot, 3) for k, v in st.items()}
        if cv:                                        # the clock's conf over the music (the beat gate opens at 0.70)
            cv_, cw_ = np.array(cv), np.array(cw)
            o = np.argsort(cv_)
            c = np.cumsum(cw_[o]) / cw_.sum()
            row["conf_med"] = float(cv_[o][np.searchsorted(c, 0.5)])
            row["conf_p90"] = float(cv_[o][min(len(o) - 1, np.searchsorted(c, 0.9))])
        row["n_gated_all"] = sum(1 for f in r["flashes"] if f[2] == "beat")
        row["stack"] = (r.get("stack") or {}).get("id", "")
        rows.append(row)
    return rows


def is_expressive(r: dict) -> bool:
    return (r["dataset"], "") in EXPRESSIVE or (r["dataset"], r["genre"]) in EXPRESSIVE


def agg(rows: list[dict]) -> dict:
    """Means per track over the whole replay (_0); lock: median over the tracks that locked, and the share."""
    import numpy as np
    out: dict = {"n": len(rows)}
    for k in ("f", "cmlt", "amlt", "hit25", "cover_beat", "cover_gated", "prec_beat", "prec_beat_dh", "wrong_lock",
              "acc1", "acc2", "acc1_time", "f_gated", "hit70_gated"):
        v = np.array([r.get(f"{k}_0", NAN) for r in rows], float)
        out[k] = float(np.nanmean(v)) if np.isfinite(v).any() else NAN
    out["f5"] = float(np.nanmean([r.get("f_5", NAN) for r in rows])) if rows else NAN
    lk = np.array([r.get("t_lock_0", NAN) for r in rows], float)
    out["locked"] = float(np.mean(np.isfinite(lk))) if len(lk) else NAN
    out["t_lock_med"] = float(np.median(lk[np.isfinite(lk)])) if np.isfinite(lk).any() else NAN
    allk = np.where(np.isfinite(lk), lk, np.inf)                  # never locked counts as never
    out["t_lock_med_all"] = float(np.median(allk)) if len(allk) else NAN
    ng = np.array([r.get("n_gated_0", 0) for r in rows], float)   # pooled precision: every gated flash weighs the same
    pb = np.array([r.get("prec_beat_0", NAN) for r in rows], float)
    m = np.isfinite(pb) & (ng > 0)
    out["prec_pooled"] = float(np.sum(pb[m] * ng[m]) / np.sum(ng[m])) if m.any() else NAN
    out["gated_tracks"] = int(np.sum(ng > 0))
    return out


COLS = (("n", "n", "{:d}"), ("F", "f", "{:.2f}"), ("F5", "f5", "{:.2f}"), ("CMLt", "cmlt", "{:.2f}"),
        ("AMLt", "amlt", "{:.2f}"), ("hit25", "hit25", "{:.2f}"), ("cover", "cover_beat", "{:.2f}"),
        ("prec", "prec_beat", "{:.2f}"), ("precPool", "prec_pooled", "{:.2f}"), ("precDH", "prec_beat_dh", "{:.2f}"),
        ("wrong", "wrong_lock", "{:.2f}"), ("tLock", "t_lock_med", "{:.1f}"), ("locked", "locked", "{:.2f}"),
        ("acc1", "acc1", "{:.2f}"), ("acc2", "acc2", "{:.2f}"))


def fmt(v, spec: str) -> str:
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "-"
    return spec.format(int(v)) if spec == "{:d}" else spec.format(v)


def table(groups: list[tuple[str, dict]]) -> list[str]:
    head = ["group"] + [h for h, _, _ in COLS]
    lines = [head] + [[name] + [fmt(g.get(k), s) for _, k, s in COLS] for name, g in groups]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(c) + " |" for c in lines[1:]]
    return out


def groups_of(rows: list[dict]) -> list[tuple[str, dict]]:
    out = [("all", agg(rows)), ("steady (not ASAP, not GTZAN classical)", agg([r for r in rows if not is_expressive(r)]))]
    for ds in sorted({r["dataset"] for r in rows}):
        rs = [r for r in rows if r["dataset"] == ds]
        out.append((ds, agg(rs)))
    for ds in sorted({r["dataset"] for r in rows}):
        gs = sorted({r["genre"] for r in rows if r["dataset"] == ds})
        if len(gs) > 1:
            for g in gs:
                out.append((f"{ds}/{g}", agg([r for r in rows if r["dataset"] == ds and r["genre"] == g])))
    return out


def causes(r: dict) -> list[tuple[str, str]]:
    """What went wrong on one track: (cause, the words with its numbers), from the result itself (whole replay)."""
    why: list[tuple[str, str]] = []
    ratio = r.get("est_bpm_0", NAN) / r.get("gt_bpm_0", NAN) if finite(r.get("gt_bpm_0")) else NAN
    if r.get("n_gated_all", 0) == 0:
        if r.get("quiet_share", 0) >= 0.3:
            why.append(("never gated: quiet gate", f"never gated: the clock's quiet gate {r['quiet_share']:.0%} "
                                                   f"of the time"))
        elif r.get("dsp_gate_low", 0) >= 0.3:
            why.append(("never gated: loudness gate", f"never gated: dsp's loudness gate under 0.5 "
                                                      f"{r['dsp_gate_low']:.0%} of the time"))
        elif r.get("state_share", {}).get("locked", 0) < 0.05:
            why.append(("never locked", "never locked (searching/holding)"))
        else:
            why.append(("never gated: conf under 0.70",
                        f"never gate=beat: locked {r['state_share'].get('locked', 0):.0%} of the time, conf median "
                        f"{r.get('conf_med', NAN):.2f}, p90 {r.get('conf_p90', NAN):.2f} (the gate opens at 0.70)"))
    elif finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3:
        why.append(("false lock", f"false lock ({r['wrong_lock_0']:.0%} of gated time)"))
    if finite(ratio):
        for name, L in (("double tempo", 2.0), ("half tempo", 0.5), ("x1.5 tempo", 1.5), ("x2/3 tempo", 2 / 3),
                        ("x3 tempo", 3.0), ("x1/3 tempo", 1 / 3)):
            if abs(ratio / L - 1) <= 0.04:
                why.append((f"tempo: {name}", f"{name} ({r['est_bpm_0']:.0f} vs {r['gt_bpm_0']:.0f} bpm)"))
                break
        else:
            if abs(ratio - 1) > 0.04:
                why.append(("tempo: wrong, not an octave",
                            f"wrong tempo ({r['est_bpm_0']:.0f} vs {r['gt_bpm_0']:.0f} bpm)"))
            elif r.get("n_gated_0", 0) and finite(r.get("prec_beat_0")) and r["prec_beat_0"] < 0.5:
                why.append(("phase", f"phase: right tempo, gated flashes on the beat {r['prec_beat_0']:.0%}"))
    elif r.get("n_flash_0", 0) == 0:
        why.append(("no grid", "no grid at all"))
    if r.get("n_gated_all", 0) and finite(r.get("cover_beat_0")) and r["cover_beat_0"] < 0.5:
        why.append(("gated under half the beats", f"gate=beat on only {r['cover_beat_0']:.0%} of the beats"))
    if finite(r.get("t_lock_0")) and r["t_lock_0"] > 8:
        why.append(("late lock (> 8 s)", f"late lock ({r['t_lock_0']:.0f} s)"))
    if r.get("quiet_share", 0) >= 0.1 and not any("quiet" in c for c, _ in why):
        why.append(("quiet gate >= 10 %", f"quiet gate {r['quiet_share']:.0%}"))
    return why


def diagnose(r: dict) -> str:
    return "; ".join(w for _, w in causes(r)) or "-"


def cmd_report(a: argparse.Namespace) -> int:
    conds = conditions()
    want = [s.strip() for s in a.conditions.split(",") if s.strip()]
    data = {c: load_rows(cond_dir(c, a.tag)) for c in want if cond_dir(c, a.tag).exists()}
    data = {c: rs for c, rs in data.items() if rs}
    if not data:
        print("no results yet")
        return 1
    md: list[str] = []
    rep: dict = {"conditions": {}}
    sub = json.loads(SUBSET.read_text()) if SUBSET.exists() else {}
    md.append(f"# Beat stack baseline ({len(sub.get('items', []))} tracks, seed {sub.get('seed')}, "
              f"replay capped at {a.dur_cap:g} s)")
    if sub.get("missing"):
        md.append(f"\nNot available: {', '.join(sub['missing'])}.")
    stacks = collections.Counter(r["stack"] for rs in data.values() for r in rs)
    md.append(f"\nStack ids: {dict(stacks)}. Whole replay from the music's start; F5 = mir_eval F without the first "
              f"5 s. Means per track; tLock = median s over the tracks that locked; precPool = all gated flashes "
              f"pooled.")
    ids = {c: {(r["dataset"], r["id"]) for r in rs} for c, rs in data.items()}
    full = max(len(v) for v in ids.values())
    for c, rs in data.items():                    # a condition run on part of the subset: everything on its tracks
        if len(ids[c]) < full:
            md.append(f"\n## On {c}'s {len(ids[c])} tracks only, every condition\n")
            md += table([(x, agg([r for r in data[x] if (r["dataset"], r["id"]) in ids[c]])) for x in data
                         if ids[x] & ids[c]])
    md.append("\n## Overall, per condition\n")
    md += table([(f"{c}: {conds[c]['what']}", agg(rs)) for c, rs in data.items()])
    md.append("\n## Steady-beat music, per condition\n")
    md += table([(c, agg([r for r in rs if not is_expressive(r)])) for c, rs in data.items()])
    for c, rs in data.items():
        md.append(f"\n## {c}: {conds[c]['what']}\n")
        g = groups_of(rs)
        md += table(g)
        rep["conditions"][c] = {"what": conds[c]["what"], "groups": {k: v for k, v in g}}
    if "C" in data:
        rs = [r for r in data["C"] if not is_expressive(r)]
        rs.sort(key=lambda r: (r.get("f_gated_0", 0) if finite(r.get("f_gated_0")) else 0, r.get("f_0", 0)))
        md.append("\n## The 10 worst steady-beat tracks in C (by F of the gated flashes)\n")
        md.append("| track | genre | F | Fgated | cover | prec | wrong | tLock | bpm est/GT | quiet | what went wrong |")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|")
        worst = []
        for r in rs[:10]:
            d = diagnose(r)
            worst.append({"dataset": r["dataset"], "id": r["id"], "genre": r["genre"], "diagnosis": d,
                          **{k: r.get(f"{k}_0") for k in ("f", "f_gated", "cover_beat", "prec_beat", "wrong_lock",
                                                         "t_lock", "est_bpm", "gt_bpm")},
                          "quiet_share": r.get("quiet_share")})
            md.append(f"| {r['dataset']}/{r['id'][:34]} | {r['genre']} | {fmt(r.get('f_0'), '{:.2f}')} | "
                      f"{fmt(r.get('f_gated_0'), '{:.2f}')} | {fmt(r.get('cover_beat_0'), '{:.2f}')} | "
                      f"{fmt(r.get('prec_beat_0'), '{:.2f}')} | {fmt(r.get('wrong_lock_0'), '{:.2f}')} | "
                      f"{fmt(r.get('t_lock_0'), '{:.1f}')} | {fmt(r.get('est_bpm_0'), '{:.0f}')}/"
                      f"{fmt(r.get('gt_bpm_0'), '{:.0f}')} | {fmt(r.get('quiet_share'), '{:.2f}')} | {d} |")
        rep["worst_C"] = worst
        why = collections.Counter(c for r in rs for c, _ in causes(r))
        md.append(f"\nCauses over all {len(rs)} steady-beat tracks in C (a track can have several): "
                  + ", ".join(f"{k} {v}" for k, v in why.most_common()))
        rep["causes_C"] = dict(why)
    md.append("\n## Expressive sets (should stay off the beat gate)\n")
    md.append("| cond | track | gated flashes | cover | prec | wrong | gated s on a wrong lock |")
    md.append("|---|---|---|---|---|---|---|")
    for c, rs in data.items():
        for r in rs:
            if is_expressive(r):
                wl = r.get("wrong_lock_0") if finite(r.get("wrong_lock_0")) else 0.0
                gated_s = r.get("n_gated_0", 0) * 60.0 / r["gt_bpm_0"] if finite(r.get("gt_bpm_0")) else NAN
                md.append(f"| {c} | {r['dataset']}/{r['id'][:40]} | {r.get('n_gated_0', 0)} | "
                          f"{fmt(r.get('cover_beat_0'), '{:.2f}')} | {fmt(r.get('prec_beat_0'), '{:.2f}')} | "
                          f"{fmt(r.get('wrong_lock_0'), '{:.2f}')} | {fmt(gated_s * wl, '{:.0f}')} |")
    if "C" in data:
        g = agg([r for r in data["C"] if not is_expressive(r)])
        ex = [r for r in data["C"] if r["dataset"] in NO_FALSE_LOCK]
        fl = sum(1 for r in ex if finite(r.get("wrong_lock_0")) and r["wrong_lock_0"] >= 0.3)
        bars = [("precision while gated >= 0.9 (per-track mean)", g["prec_beat"], g["prec_beat"] >= 0.9),
                ("precision while gated >= 0.9 (pooled)", g["prec_pooled"], g["prec_pooled"] >= 0.9),
                ("coverage >= 0.7", g["cover_beat"], g["cover_beat"] >= 0.7),
                ("median time to lock <= 8 s (locked tracks)", g["t_lock_med"], g["t_lock_med"] <= 8),
                ("median time to lock <= 8 s (never = never)", g["t_lock_med_all"], g["t_lock_med_all"] <= 8),
                (f"false locks on the expressive sets rare ({fl} of {len(ex)} with >= 30 % wrong-lock time)",
                 fl, len(ex) > 0 and fl <= max(0, len(ex) // 10))]
        md.append("\n## The bars, condition C, steady-beat music\n")
        md.append("| bar | value | pass |")
        md.append("|---|---|---|")
        for name, v, ok in bars:
            v = float(v)
            md.append(f"| {name} | {'never' if v == math.inf else fmt(v, '{:.2f}')} | {'yes' if ok else 'NO'} |")
        rep["bars_C"] = [{"bar": n, "value": v, "pass": bool(ok)} for n, v, ok in bars]
    text = "\n".join(md) + "\n"
    print(text)
    out = CACHE / ("baseline-report" + (f"-{a.tag}" if a.tag else ""))
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(text)
    (out / "report.json").write_text(json.dumps(rep, indent=1, default=lambda v: None))
    rows_out = {c: [{k: v for k, v in r.items() if not isinstance(v, dict)} | {"diagnosis": diagnose(r)}
                    for r in rs] for c, rs in data.items()}
    (out / "tracks.json").write_text(json.dumps(rows_out, default=lambda v: None))
    print(f"-> {out}/report.md")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("subset", "run", "report"))
    ap.add_argument("--counts", default=COUNTS, help=f"tracks per dataset (default {COUNTS})")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--conditions", default=None, help="run: C,A,B,D; report: A,B,C,D")
    ap.add_argument("--dur-cap", type=float, default=DUR_CAP_S, help="replay at most this much of each track")
    ap.add_argument("--tag", default="", help="a suffix for the results dirs (a rerun after a stack change)")
    ap.add_argument("--datasets", default=None, help="run: only these datasets' tracks (comma-separated)")
    a = ap.parse_args(argv)
    if a.cmd == "subset":
        return cmd_subset(a)
    if a.cmd == "run":
        a.conditions = a.conditions or "C,A,B,D"
        return cmd_run(a)
    a.conditions = a.conditions or "A,B,C,D,E"
    return cmd_report(a)


if __name__ == "__main__":
    raise SystemExit(main())
