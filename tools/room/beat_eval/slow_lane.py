#!/usr/bin/env python3
"""The Beat This! slow lane's pre-pass for run_stack.py: the ML worker's ``beats`` task on the windows the live worker
would have run, each turned into the exact ``beats`` line it would have written (show_service/ml/worker.py).

Run by run_stack.py in the ML worker's own env (~/.venvs/sm-show-ml: onnxruntime), never by hand:

  ~/.venvs/sm-show-ml/bin/python tools/room/beat_eval/slow_lane.py SPEC.json OUT.json

SPEC.json: {"audio": "<.npy, float32 mono 48 kHz, what the stack hears>", "t0": <host s of sample 0>,
            "stream": "<name>", "models_dir", "model", "window_s", "interior_margin_s", "silence_dbfs",
            "runs": [{"k": <turn>, "a": <16 kHz index>, "b": <16 kHz index>}, ...]}
OUT.json:  {"model", "load_s", "runs": [{"k", "a", "b", "silent": bool, "cost_ms", "line": <MlBeats or null>}]}

What it copies from the live path, exactly: MLFeed's mono -> 16 kHz (soxr MQ, delay-compensated, so 16 kHz sample m
sounds at t0 + m / 16000); the worker's silence rule (``models.level_dbfs`` of the window under ``ml.silence_dbfs``:
no run); ``models.BeatsTask`` (soxr HQ to 22.05 kHz, the log-mel, the ONNX model, the minimal peaks); and
``Worker._line`` (the interior, the median-IBI bpm, the bar length from the downbeats). Not copied: the budget
(5.5 s live) never stops a run here, because this run's own cost under our nice and the renderer's load is not the
live one; run_stack.py times each result's arrival from the live cost instead.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import types

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import soxr  # noqa: E402

from show_service.ml import models as M  # noqa: E402
from show_service.ml import worker as W  # noqa: E402

FS_ML = 16000


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spec = json.loads(pathlib.Path(argv[0]).read_text())
    x48 = np.load(spec["audio"]).astype(np.float32)
    x16 = soxr.resample(x48, 48000, FS_ML, quality="MQ").astype(np.float32)   # MLFeed's ears (mllink.SOXR_QUALITY)
    models_dir = pathlib.Path(spec["models_dir"]).expanduser()
    t_load = time.perf_counter()
    model = M.Model(spec["model"], models_dir, M.read_index(models_dir))
    task = M.BeatsTask(model)
    load_s = time.perf_counter() - t_load
    window_s = float(spec["window_s"])
    me = types.SimpleNamespace(cfg={"tasks": {"beats": {"interior_margin_s": float(spec["interior_margin_s"]),
                                                       "model": spec["model"]}}})
    st = types.SimpleNamespace(name="beats", window_s=window_s)
    t0 = float(spec["t0"])
    out = []
    for r in spec["runs"]:
        a, b = int(r["a"]), int(r["b"])
        row = {"k": r["k"], "a": a, "b": b, "silent": False, "cost_ms": None, "line": None}
        pcm = x16[a:b]
        if a < 0 or len(pcm) != b - a:
            row["silent"] = None                   # not in the audio (run_stack doesn't ask for these)
            out.append(row)
            continue
        if M.level_dbfs(pcm[-int(window_s * FS_ML):]) < float(spec["silence_dbfs"]):
            row["silent"] = True                   # the worker counts it "silent" and writes nothing
            out.append(row)
            continue
        budget = M.Budget(1e9)                     # never stopped here (the module docstring)
        res = task.run(pcm, budget)
        cost = budget.elapsed_ms()
        w = W.Window(pcm, a, b, 0, spec.get("stream", "replay"), 0, t0 + a / FS_ML, t0 + (b - 1) / FS_ML, True)
        row["cost_ms"] = round(cost, 1)
        row["line"] = W.Worker._line(me, st, w, res, cost)
        out.append(row)
    pathlib.Path(argv[1]).write_text(json.dumps({"model": spec["model"], "load_s": round(load_s, 2), "runs": out},
                                                default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
