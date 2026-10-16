"""The analysis worker: a window of played notes in, beats / metre / key signature / hands out, in its own process
(PM2S's networks need torch, ~0.3-1.5 s a window on two threads; the render thread never waits on them).

    ~/.venvs/sm-score-ml/bin/python -m stream_service.score.analyse      (pipes; started by live_score's Analyser)

Protocol (as engrave.py's): a 4-byte big-endian length, then JSON; each reply is followed by an empty blob.
  request  {"id", "t0", "phrase", "notes": [[pitch, onset s from t0, duration s, velocity 1-127, note id], ...]}
  reply    {"id", "ok", "t0", "phrase", "beats": [s from t0], "last_onset": s,
            "metre": {"beats", "phase", "compound", "conf", "compound_p", "probs", "phases"} | null,
            "key_fifths", "key_p", "hands": {note id: 0 treble (right hand) | 1 bass (left hand)}, "ms", "error"?}
``phase`` indexes ``beats``.  Weights: PM2S's (pm2s_net.py) in ~/.local/share/screen-machine/pm2s; metre.py's
fitted weights beside it in the repo.
"""
from __future__ import annotations

import json
import os
import pathlib
import struct
import sys
import time

import numpy as np

WEIGHTS = pathlib.Path(os.environ.get("SM_PM2S_WEIGHTS", pathlib.Path.home() / ".local/share/screen-machine/pm2s"))


def analyse(net, model, req: dict) -> dict:
    from stream_service.score import pm2s_net as P
    rows = req["notes"]
    ns = np.array([r[:4] for r in rows], dtype=np.float64)
    ids = [int(r[4]) for r in rows]
    order = np.argsort(ns[:, 1], kind="stable")
    ns, ids = ns[order], [ids[i] for i in order]
    out = net(ns)
    beats, downs = P.beat_pps(out["beat"], out["down"], ns[:, 1])
    m = model(ns, beats, out["down"], out["ts"], downs)
    w = np.clip(ns[:, 2], 0.05, 2.0)[:, None]
    kp = (out["key"] * w).sum(0)
    kp = kp / kp.sum()
    k = int(np.argmax(kp))
    hands = {str(i): (1 if h > 0.5 else 0) for i, h in zip(ids, out["hand"])}   # PM2S: 1 = left hand (checked)
    return {"t0": req.get("t0"), "phrase": req.get("phrase"), "beats": [round(float(b), 4) for b in beats],
            "last_onset": float(ns[-1, 1]),
            "metre": None if m is None else {"beats": m.beats_per_bar, "phase": m.phase, "compound": m.compound,
                                             "conf": round(m.confidence, 3), "compound_p": round(m.compound_p, 3),
                                             "probs": {str(a): round(b, 3) for a, b in m.probs.items()},
                                             "phases": {str(a): b for a, b in m.phases.items()}},
            "key_fifths": P.KEY_FIFTHS[k], "key_p": round(float(kp[k]), 3), "hands": hands}


def _read(f) -> dict | None:
    head = f.read(4)
    if len(head) < 4:
        return None
    return json.loads(f.read(struct.unpack(">I", head)[0]))


def _write(f, meta: dict) -> None:
    b = json.dumps(meta).encode()
    f.write(struct.pack(">I", len(b)) + b + struct.pack(">I", 0))
    f.flush()


def main() -> None:
    from stream_service.score.metre import MetreModel
    from stream_service.score.pm2s_net import PM2S
    net = PM2S(WEIGHTS, threads=2)
    model = MetreModel()
    fin, fout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        req = _read(fin)
        if req is None:
            return
        t = time.perf_counter()
        try:
            rep = {"id": req.get("id"), "ok": True, **analyse(net, model, req)}
        except Exception as e:                       # a bad window answers, never kills the worker
            rep = {"id": req.get("id"), "ok": False, "t0": req.get("t0"), "phrase": req.get("phrase"),
                   "error": f"{type(e).__name__}: {e}"}
        rep["ms"] = round((time.perf_counter() - t) * 1000, 1)
        _write(fout, rep)


if __name__ == "__main__":
    main()
