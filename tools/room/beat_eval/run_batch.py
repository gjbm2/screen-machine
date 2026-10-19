#!/usr/bin/env python3
"""Replay a dataset (or a stratified subset) through the live beat stack, one track at a time, then score it.

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/run_batch.py INDEX.json [--datasets a,b]
      [--subset N --stratify genre --seed S] [--degrade tools/room/audio_cal/runs/<run>/model/model.json [--snr-db N]]
      [--gate-ref-lufs L] [--no-slow-lane] [--dur-s S] [--out DIR] [-- more run_stack.py options]

Each track is ``flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-show/bin/python run_stack.py ...``
(the host renders 4K: one heavy process at a time, never a pool). Resumable: a track whose result JSON is already in
the results dir is skipped, so a stopped batch carries on where it was (delete a result to redo it). ``batch.jsonl``
in the results dir logs each track's exit code and timing: ``wall_s`` (from the command's start, so it includes
waiting for the lock), ``lock_wait_s`` (until run_stack.py's process started), ``run_s`` (the process itself, imports
included) and ``cost`` (run_s per second of audio replayed, pre-roll included). At the end score.py writes scores.csv
and summary.csv there and prints the summary table.

INDEX.json (the datasets' layout, README "Datasets"; also the pseudo-GT and synth indexes): a list of entries, or
{"tracks": [...]} / {"items": [...]}, or {id: entry}. An entry gives ``id``, the audio (``audio``, ``file`` or
``path``; relative to the index's folder; else <dir>/audio/<id>.*), the beats (``beats``; else
<dir>/beats/<id>.beats), and optionally ``genre``, ``dataset``, ``start_s``, ``dur_s``, ``duplicate``. The combined
index (~/.cache/screen-machine/beat-datasets/index.json, ids "<dataset>/<id>") works with ``--datasets``; a track's
result has the same name from either index, so the two share results. Entries marked ``duplicate`` (13 ballroom
excerpts that repeat another) are left out unless ``--with-duplicates``.

``--stratify`` takes an entry key, or several joined by commas (``dataset,genre``): the subset is dealt round-robin
over the groups, each shuffled by ``--seed``, so N tracks spread as evenly as the groups allow. The same ``--seed``
also seeds the room model's noise and the slow lane's grid phase (run_stack.py --seed): every track then hears the
same stretch of room sound, the same processing draw and dropouts at the same times. ``--track-seeds`` gives each
track its own seed instead, ``--seed`` + crc32("<dataset>/<id>") mod 100000 (still reproducible, and the same for a
track in every condition), so a subset averages over the room's noise rather than measuring one stretch of it.

The results dir defaults to ~/.cache/screen-machine/beat-eval/results/<dataset>[-<room run>][-snrN][-refL][-noslow]
[-durS] (the room run is the model's run folder name).
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import random
import subprocess
import sys
import time
import zlib
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
SHOW_PY = pathlib.Path("~/.venvs/sm-show/bin/python").expanduser()
RT_PY = pathlib.Path("~/.venvs/sm-roundtrip/bin/python").expanduser()
LOCK = pathlib.Path("~/.cache/screen-machine/cpu.lock").expanduser()
RESULTS = pathlib.Path("~/.cache/screen-machine/beat-eval/results").expanduser()
AUDIO_EXT = (".wav", ".flac", ".mp3", ".ogg", ".m4a", ".opus", ".aiff", ".au")
THREADS = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}   # as deploy/sm-show.env


def load_index(path: pathlib.Path) -> tuple[str, list[dict]]:
    """(dataset name, entries with absolute ``audio`` and ``beats``) from any of the layouts in the docstring."""
    d = json.loads(path.read_text())
    base = path.parent
    name = d.get("name") if isinstance(d, dict) and isinstance(d.get("name"), str) else base.name
    if isinstance(d, list):
        items = [(None, e) for e in d]
    elif isinstance(d.get("tracks"), list):
        items = [(None, e) for e in d["tracks"]]
    elif isinstance(d.get("items"), list):
        items = [(None, e) for e in d["items"]]
    else:
        items = [(k, e) for k, e in d.items() if isinstance(e, dict) and not str(k).startswith("_")]
    out = []
    for key, e in items:
        if not isinstance(e, dict):
            continue
        tid = str(e.get("id") or key or "")
        if not tid:
            continue
        ds = str(e.get("dataset") or name)
        short = tid[len(ds) + 1:] if tid.startswith(ds + "/") else tid      # the combined index's "<dataset>/<id>"
        audio = next((e[k] for k in ("audio", "file", "path", "audio_path", "wav") if e.get(k)), None)
        if audio:
            audio = pathlib.Path(audio).expanduser()
            audio = audio if audio.is_absolute() else base / audio
        else:
            audio = next((p for p in sorted((base / "audio").glob(f"{short}.*")) if p.suffix.lower() in AUDIO_EXT),
                         None)
        beats = next((e[k] for k in ("beats", "beats_file", "annotation", "gt") if e.get(k)), None)
        if beats:
            beats = pathlib.Path(beats).expanduser()
            beats = beats if beats.is_absolute() else base / beats
        else:
            beats = base / "beats" / f"{short}.beats"
        out.append({"id": short, "audio": audio, "beats": beats, "genre": str(e.get("genre") or e.get("style") or ""),
                    "dataset": ds, "start_s": e.get("start_s"), "dur_s": e.get("dur_s"),
                    "duplicate": bool(e.get("duplicate")), "duration_s": e.get("duration_s")})
    return name, out


def pick(entries: list[dict], n: Optional[int], stratify: Optional[str], seed: int) -> list[dict]:
    """``n`` entries (all with None): shuffled by ``seed``, round-robin over the ``stratify`` key(s)' values."""
    rng = random.Random(seed)
    if not n or n >= len(entries):
        return list(entries)
    if not stratify:
        return rng.sample(entries, n)
    keys = [k.strip() for k in stratify.split(",") if k.strip()]
    groups: dict[tuple, list[dict]] = {}
    for e in entries:
        groups.setdefault(tuple(str(e.get(k) or "") for k in keys), []).append(e)
    for g in groups.values():
        rng.shuffle(g)
    by_first: dict[str, list[tuple]] = {}               # several keys: alternate the first one's values first, so
    for k in sorted(groups):                             # dataset,genre takes from each dataset in turn
        by_first.setdefault(k[0], []).append(k)
    order = [k for tier in itertools.zip_longest(*by_first.values()) for k in tier if k is not None]
    out: list[dict] = []
    while len(out) < n and any(groups.values()):
        for k in order:
            if groups[k] and len(out) < n:
                out.append(groups[k].pop())
    return out


def track_seed(seed: int, e: dict) -> int:
    """--track-seeds: a seed of the track's own, from its name (the same in every run and condition)."""
    return seed + zlib.crc32(f"{e['dataset']}/{e['id']}".encode()) % 100000


def safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def results_dir(a: argparse.Namespace, name: str) -> pathlib.Path:
    if a.out:
        return a.out.expanduser()
    tag = safe(a.datasets.replace(",", "+")) if a.datasets else name
    if a.degrade:
        tag += "-" + safe(a.degrade.resolve().parent.parent.name or a.degrade.stem)
    if a.snr_db is not None:
        tag += f"-snr{a.snr_db:g}"
    if a.gate_ref_lufs is not None:
        tag += f"-ref{a.gate_ref_lufs:g}"
    if a.no_slow_lane:
        tag += "-noslow"
    if a.dur_s:
        tag += f"-dur{a.dur_s:g}"
    return RESULTS / tag


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("index", type=pathlib.Path)
    ap.add_argument("--datasets", default=None, help="only these datasets (comma-separated; the combined index)")
    ap.add_argument("--subset", type=int, default=None, help="this many tracks (default: all)")
    ap.add_argument("--stratify", default=None, help="entry key(s) to spread the subset over (genre; dataset,genre)")
    ap.add_argument("--seed", type=int, default=0, help="the subset's shuffle, the room noise and the ML grid phase")
    ap.add_argument("--with-duplicates", action="store_true", help="keep entries marked duplicate")
    ap.add_argument("--track-seeds", action="store_true",
                    help="each track's run_stack --seed = --seed + crc32(dataset/id) mod 100000 (its own noise draw)")
    ap.add_argument("--degrade", type=pathlib.Path, help="room model.json (tools/room/audio_cal/runs/<run>/model/)")
    ap.add_argument("--snr-db", type=float, default=None)
    ap.add_argument("--gate-ref-lufs", type=float, default=None,
                    help="run_stack.py --gate-ref-lufs (default: its own, dsp's -30)")
    ap.add_argument("--no-slow-lane", action="store_true")
    ap.add_argument("--dur-s", type=float, default=None, help="replay at most this much of each track")
    ap.add_argument("--out", type=pathlib.Path, default=None, help="the results dir")
    ap.add_argument("--no-score", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="list what would run")
    ap.add_argument("--runner", type=pathlib.Path, default=HERE / "run_stack.py",
                    help="the per-track replay (default run_stack.py; a candidate clock's runner in alt/ that takes "
                         "run_stack.py's options and writes the same JSON)")
    ap.add_argument("--runner-python", type=pathlib.Path, default=SHOW_PY,
                    help=f"the interpreter for --runner (default {SHOW_PY})")
    ap.epilog = "After --: more run_stack.py options for every track, e.g. -- --preroll-s 10 --rx-latency-ms 40"
    argv = list(sys.argv[1:] if argv is None else argv)
    cut = argv.index("--") if "--" in argv else len(argv)      # argparse can't take "--" after the positional
    a = ap.parse_args(argv[:cut])
    a.stack_args = argv[cut + 1:]
    try:
        sys.stdout.reconfigure(line_buffering=True)          # progress lines before score.py's table, into a log too
    except (AttributeError, ValueError):
        pass

    name, entries = load_index(a.index.expanduser())
    if a.datasets:
        want = {s.strip() for s in a.datasets.split(",") if s.strip()}
        entries = [e for e in entries if e["dataset"] in want]
    n_dup = sum(e["duplicate"] for e in entries)
    if not a.with_duplicates:
        entries = [e for e in entries if not e["duplicate"]]
    chosen = pick(entries, a.subset, a.stratify, a.seed)
    out = results_dir(a, name)
    if not a.dry_run:
        out.mkdir(parents=True, exist_ok=True)
    log_p = out / "batch.jsonl"
    print(f"{len(chosen)} of {len(entries)} tracks from {a.index}"
          f"{f' ({n_dup} duplicates left out)' if n_dup and not a.with_duplicates else ''} -> {out}")
    done = failed = skipped = 0
    cost_audio = cost_run = 0.0
    t_batch = time.time()
    for i, e in enumerate(chosen, 1):
        res = out / f"{safe(e['dataset'])}__{safe(e['id'])}.json"
        if res.exists():
            skipped += 1
            continue
        if not e["audio"] or not pathlib.Path(e["audio"]).exists():
            print(f"[{i}/{len(chosen)}] {e['id']}: no audio ({e['audio']})")
            failed += 1
            continue
        seed = track_seed(a.seed, e) if a.track_seeds else a.seed
        cmd = ["flock", str(LOCK), "nice", "-n", "19", str(a.runner_python.expanduser()),
               str(a.runner.expanduser().resolve()), str(e["audio"]),
               "--out", str(res), "--id", e["id"], "--dataset", e["dataset"], "--genre", e["genre"],
               "--seed", str(seed)]
        if e["beats"] and pathlib.Path(e["beats"]).exists():
            cmd += ["--beats", str(e["beats"])]
        if e.get("start_s"):
            cmd += ["--start-s", str(e["start_s"])]
        dur = a.dur_s if a.dur_s is not None else e.get("dur_s")
        if dur:
            cmd += ["--dur-s", str(dur)]
        if a.degrade:
            cmd += ["--degrade", str(a.degrade.resolve())]
        if a.snr_db is not None:
            cmd += ["--snr-db", str(a.snr_db)]
        if a.gate_ref_lufs is not None:
            cmd += ["--gate-ref-lufs", str(a.gate_ref_lufs)]
        if a.no_slow_lane:
            cmd += ["--no-slow-lane"]
        cmd += a.stack_args
        if a.dry_run:
            print(" ".join(cmd))
            continue
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=dict(os.environ, **THREADS))
        t1 = time.time()
        rec = {"id": e["id"], "dataset": e["dataset"], "genre": e["genre"], "seed": seed, "rc": p.returncode,
               "wall_s": round(t1 - t0, 1), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if p.returncode == 0 and res.exists():
            try:
                r = json.loads(res.read_text())
                ts = r.get("t_start_unix")
                run_s = t1 - ts if isinstance(ts, (int, float)) else t1 - t0
                audio_s = float(r.get("audio_s") or 0.0)
                rec.update(audio_s=audio_s, lock_wait_s=round(max(0.0, (ts or t0) - t0), 1), run_s=round(run_s, 1),
                           cost=round(run_s / audio_s, 3) if audio_s else None, rt=r.get("rt"),
                           stack_wall_s=r.get("wall_s"), stack=(r.get("stack") or {}).get("id"))
                cost_audio += audio_s
                cost_run += run_s
            except (OSError, ValueError):
                pass
            done += 1
            said = [s.strip() for s in p.stdout.splitlines() if s.strip().startswith(("end:", "vs GT", "ERRORS"))]
            print(f"[{i}/{len(chosen)}] {e['dataset']}/{e['id']}: {rec.get('audio_s') or 0:.0f} s of audio, "
                  f"{rec.get('run_s', rec['wall_s']):.0f} s run ({rec.get('cost') or 0:.2f} s per audio s), "
                  f"{rec.get('lock_wait_s', 0):.0f} s lock wait" + "".join(f"\n    {s}" for s in said))
        else:
            failed += 1
            rec["stderr"] = p.stderr[-600:]
            print(f"[{i}/{len(chosen)}] {e['id']}: FAILED rc {p.returncode}: {p.stderr.strip()[-300:]}")
        with log_p.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    print(f"done {done}, already there {skipped}, failed {failed}; {time.time() - t_batch:.0f} s"
          + (f"; cost {cost_run / cost_audio:.2f} s of run per s of audio ({cost_run:.0f} s for {cost_audio:.0f} s)"
             if cost_audio else ""))
    if not a.no_score and not a.dry_run:
        subprocess.run([str(RT_PY), str(HERE / "score.py"), str(out)], cwd=str(REPO), env=dict(os.environ, **THREADS))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
