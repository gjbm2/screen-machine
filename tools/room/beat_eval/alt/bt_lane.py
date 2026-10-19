#!/usr/bin/env python3
"""The slow lane's Beat This! pre-pass (tools/room/beat_eval/slow_lane.py) with each window's level normalised first:
a candidate ML-worker change for a quiet mic, measured by alt/clockvar.py's variant c (``c:bt_norm=-20``).

Why: through the PC22 room model the mic delivers music at about -48 dBFS RMS, and Beat This! (beat-this-small0,
log-mel input, no gain stage of its own) then finds no beats at all in most windows (18 Oct: 0 beats in a 30 s
ChaChaCha window at -48.8 dBFS; 28 beats at 125 bpm, the right tempo, scaled to -20 dBFS). madmom's lane has its
own AGC (clock.AGC_TARGET_DB); the worker's beats task has none.

  ~/.venvs/sm-show-ml/bin/python -I tools/room/beat_eval/alt/bt_lane.py SPEC.json OUT.json

SPEC.json is slow_lane.py's, plus ``norm_dbfs`` (each window's RMS is scaled to it before BeatsTask; the worker's
silence rule still reads the raw level). Everything else is slow_lane.py's own code.
"""
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[3]))

import numpy as np  # noqa: E402

import slow_lane  # noqa: E402
from show_service.ml import models as M  # noqa: E402


def main(argv: list[str]) -> int:
    spec = json.loads(pathlib.Path(argv[0]).read_text())
    norm = spec.get("norm_dbfs")
    if norm is not None:
        run0 = M.BeatsTask.run

        def run(self, pcm, budget, **kw):
            lvl = M.level_dbfs(pcm)
            g = np.float32(10.0 ** ((float(norm) - lvl) / 20.0))
            return run0(self, (pcm * g).astype(np.float32), budget, **kw)

        M.BeatsTask.run = run
    return slow_lane.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
