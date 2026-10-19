#!/usr/bin/env python3
"""The ML worker's CED-mini lane, offline, for alt/clockvar_replay.py: one ``ced`` line per fast-lane turn of a
replayed stream, so a clock variant can hear "music / speech / ..." at the live cadence and latency.

Run by clockvar_replay.ced_prepass in the worker's own env (~/.venvs/sm-show-ml: onnxruntime), never by hand:

  ~/.venvs/sm-show-ml/bin/python -I tools/room/beat_eval/alt/ced_lane.py SPEC.json OUT.json

SPEC.json: {"audio": "<.npy float32 mono 48 kHz>", "models_dir", "silence_dbfs", "groups": {word: [classes]} (show.yaml
            ml.ced_groups), "model": "ced-mini", "runs": [{"k", "a", "b"}, ...] (16 kHz sample ranges),
            "norm_dbfs": null | dB (each window scaled to this RMS before the model: a candidate worker change for a
            quiet mic; the silence rule still reads the raw level)}
OUT.json:  {"model", "load_s", "cost_ms_mean", "runs": [{"k", "a", "b", "<word>_p" ..., "skipped"?, "cost_ms"}]}

Copied from the live path: MLFeed's mono -> 16 kHz (soxr MQ, delay-compensated, as slow_lane.py), the worker's
silence rule (``models.level_dbfs`` of the window under ``ml.silence_dbfs``: every word 0, silence 1, no run) and
``models.CedTask`` (the model, its sigmoid, the words as the max of their classes).
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import soxr  # noqa: E402
import yaml  # noqa: E402

from show_service import contract as C  # noqa: E402
from show_service.ml import models as M  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    spec = json.loads(pathlib.Path(argv[0]).read_text())
    x16 = soxr.resample(np.load(spec["audio"]).astype(np.float32), 48000, 16000, quality="MQ").astype(np.float32)
    models_dir = pathlib.Path(spec["models_dir"]).expanduser()
    groups = spec.get("groups")
    if groups is None:
        cfg = yaml.safe_load((REPO / "config" / "room" / "show.yaml").read_text()) or {}
        groups = (cfg.get("ml") or {}).get("ced_groups") or {}
    t = time.perf_counter()
    task = M.CedTask(M.Model(spec.get("model", "ced-mini"), models_dir, M.read_index(models_dir)), groups,
                     lambda s: print(s, file=sys.stderr))
    load_s = time.perf_counter() - t
    out, costs = [], []
    for r in spec["runs"]:
        a, b = int(r["a"]), int(r["b"])
        pcm = x16[a:b]
        row: dict = {"k": r["k"], "a": a, "b": b}
        lvl = M.level_dbfs(pcm)
        if lvl < float(spec.get("silence_dbfs", -70.0)):
            row.update({f"{g}_p": 0.0 for g in C.CED_GROUPS})
            row.update({"silence_p": 1.0, "skipped": "silence", "cost_ms": 0.0})
        else:
            if spec.get("norm_dbfs") is not None:      # the window scaled to this RMS level first (a quiet mic)
                pcm = (pcm * np.float32(10.0 ** ((float(spec["norm_dbfs"]) - lvl) / 20.0))).astype(np.float32)
            budget = M.Budget(1e9)
            row.update({k: round(v, 4) for k, v in task.run(pcm, budget).items()})
            row["cost_ms"] = round(budget.elapsed_ms(), 2)
            costs.append(row["cost_ms"])
        out.append(row)
    pathlib.Path(argv[1]).write_text(json.dumps({"model": spec.get("model", "ced-mini"), "load_s": round(load_s, 2),
                                                 "cost_ms_mean": round(float(np.mean(costs)), 2) if costs else None,
                                                 "runs": out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
