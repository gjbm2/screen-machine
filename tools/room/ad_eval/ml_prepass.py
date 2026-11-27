#!/usr/bin/env python3
"""The ML worker's lines for a whole replay, computed once and cached (run by replay.py in ~/.venvs/sm-show-ml).

    ~/.venvs/sm-show-ml/bin/python -I tools/room/ad_eval/ml_prepass.py SPEC.json OUT.json

SPEC: {"audio": <.npy float32 mono 48 kHz, what the stack hears>, "t0": <host s of sample 0>, "stream": <name>,
       "ml": <show.yaml ml, merged>, "models_dir": <dir>}
OUT:  {"lines": [[turn_s, line], ...], "load_s", "tasks": {name: runs}}

The live worker's own code runs each task (worker.Worker._run_task: its ring, windows, silence rule and line
format) on 16 kHz audio resampled as MLFeed does (soxr MQ). Approximate: each task turns on its own grid every
``every_s`` from the first moment its window is full (live, the fast lane shares one grid and a CPU guard can
stretch it), and the cost a line arrives after is fixed per task in replay.py, not this run's.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import soxr  # noqa: E402

from show_service import contract as C  # noqa: E402
from show_service.ml import protocol as P  # noqa: E402
from show_service.ml import worker as W  # noqa: E402


def main(argv: list[str]) -> int:
    spec = json.loads(pathlib.Path(argv[0]).read_text())
    x48 = np.load(spec["audio"]).astype(np.float32)
    x16 = soxr.resample(x48, 48000, C.ML_FS, quality="MQ").astype(np.float32)
    t0, stream = float(spec["t0"]), str(spec["stream"])
    now = [t0]
    lines: list = []
    turn = [t0]
    w = W.Worker(pathlib.Path(spec["models_dir"]), fake=False, nice=19, out=lambda o: lines.append([turn[0], o]),
                 clock=lambda: now[0])
    w.configure(spec.get("ml") or {})
    t_load = time.perf_counter()
    b = w.load_models()
    load_s = time.perf_counter() - t_load
    states = {n: st for n, st in w.states.items() if b.tasks.get(n) is not None}
    nxt = {n: None for n in states}
    F = C.ML_FRAME_SAMPLES
    for i, n0 in enumerate(range(0, len(x16), F)):
        seg = x16[n0:n0 + F]
        fr = P.Frame(magic=C.ML_AUDIO_MAGIC, version=C.ML_PROTOCOL_V, flags=C.ML_FLAG_SEGMENT if i == 0 else 0,
                     n=len(seg), stream=stream, seg=1, n0=n0, t0=t0 + n0 / C.ML_FS, dropped=0,
                     payload=seg.astype("<f4").tobytes())
        w.ring.push(fr)
        now[0] = t0 + (n0 + len(seg)) / C.ML_FS
        held = w.ring.held_s()
        for name, st in states.items():
            if nxt[name] is None:
                if held >= st.window_s:
                    nxt[name] = now[0]
                else:
                    continue
            if now[0] + 1e-9 >= nxt[name]:
                turn[0] = nxt[name]
                w._run_task(st, b)
                nxt[name] += st.every_s
    out = {"lines": lines, "load_s": round(load_s, 2), "tasks": {n: st.runs for n, st in states.items()}}
    pathlib.Path(argv[1]).write_text(json.dumps(out, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
