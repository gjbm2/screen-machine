"""The analysis worker: a window of played notes in, beats / metre / key signature / hands out, in its own process
(PM2S's networks need torch, ~0.3-1.5 s a window on two threads; the render thread never waits on them).

    ~/.venvs/sm-score-ml/bin/python -m stream_service.score.analyse      (pipes; started by live_score's Analyser)

Protocol (as engrave.py's): a 4-byte big-endian length, then JSON; each reply is followed by an empty blob.
  request  {"id", "t0", "phrase", "notes": [[pitch, onset s from t0, duration s, velocity 1-127, note id], ...]}
  reply    {"id", "ok", "t0", "phrase", "beats": [s from t0], "last_onset": s,
            "metre": {"beats", "phase", "compound", "conf", "compound_p", "probs", "phases"} | null,
            "key_fifths", "key_p", "key_probs": [12, by KEY_FIFTHS order], "ts_triple",
            "hands": {note id: 0 treble (right hand) | 1 bass (left hand)}, "ms", "error"?}
``phase`` indexes ``beats``.  Weights: PM2S's (pm2s_net.py) in ~/.local/share/screen-machine/pm2s; metre.py's
fitted weights beside it in the repo.

The worker keeps each phrase's state (metre.Phrase: its notes with PM2S's per-note outputs, and the pulse held at one
level across windows; the newest two phrases): the metre is read over the phrase's held grid (metre.phrase_metre),
not one window, and ``beats`` are the phrase's written beats over the window (one level reply to reply); the reply
also carries ``grid`` (the phrase's written beats from its start, s from t0: a rewrite in another metre re-lays the
phrase on them) and in ``metre`` the class probabilities (``cls``) and the written beat against the held pulse
(``level``).  Without the phrase network in metre_weights.json, or before the phrase has a grid: one window's metre.
"""
from __future__ import annotations

import collections
import json
import os
import pathlib
import struct
import sys
import time

import numpy as np

WEIGHTS = pathlib.Path(os.environ.get("SM_PM2S_WEIGHTS", pathlib.Path.home() / ".local/share/screen-machine/pm2s"))
PHRASES: "collections.OrderedDict" = collections.OrderedDict()     # phrase -> metre.Phrase (the newest two)


def _phrase(key):
    from stream_service.score.metre import Phrase
    ph = PHRASES.get(key)
    if ph is None:
        ph = PHRASES[key] = Phrase()
        while len(PHRASES) > 2:
            PHRASES.popitem(last=False)
    return ph


def _metre_dict(m, beats=None, downs=None) -> dict:
    d = {"beats": m.beats_per_bar, "phase": m.phase, "compound": m.compound, "conf": round(m.confidence, 3),
         "compound_p": round(m.compound_p, 3), "probs": {str(a): round(b, 3) for a, b in m.probs.items()},
         "phases": {str(a): b for a, b in m.phases.items()}}
    if downs is not None and len(beats):                       # the downbeats as indices into the reply's beats
        idx = {L: int(np.argmin(np.abs(beats - t))) for L, t in downs.items()}
        d["phases"] = {str(L): i for L, i in idx.items()}
        d["phase"] = idx[m.beats_per_bar]
    return d


def analyse(net, model, req: dict) -> dict:
    from stream_service.score import pm2s_net as P
    rows = req["notes"]
    ns = np.array([r[:4] for r in rows], dtype=np.float64)
    ids = [int(r[4]) for r in rows]
    order = np.argsort(ns[:, 1], kind="stable")
    ns, ids = ns[order], [ids[i] for i in order]
    out = net(ns)
    beats, downs = P.beat_pps(out["beat"], out["down"], ns[:, 1])
    t0 = float(req.get("t0") or 0.0)
    extra, md = {}, None
    if getattr(model, "pnet", None):
        from stream_service.score.metre import phrase_metre
        ph = _phrase(req.get("phrase"))
        ph.add(ns, ids, t0, out)
        ph.grid.update(beats + t0, t0 + float(ns[-1, 1]))
        r = phrase_metre(model, ph, t0 + float(ns[-1, 1]))
        if r is not None:
            pm, written, info = r
            wb = written[(written >= t0 + (beats[0] if len(beats) else 0.0) - 0.05)] - t0
            if len(wb) >= 4:
                beats = wb
                md = _metre_dict(pm, beats, {L: t - t0 for L, t in info["downs"].items()})
                md["cls"] = {c: round(p, 3) for c, p in info["cls"].items()}
                md["level"] = info["level"]
                extra["grid"] = [round(float(b), 4) for b in written - t0]
    m = None if md is not None else model(ns, beats, out["down"], out["ts"], downs)
    w = np.clip(ns[:, 2], 0.05, 2.0)[:, None]
    kp = (out["key"] * w).sum(0)
    kp = kp / kp.sum()
    k = int(np.argmax(kp))
    hands = {str(i): (1 if h > 0.5 else 0) for i, h in zip(ids, out["hand"])}   # PM2S: 1 = left hand (checked)
    return {"t0": req.get("t0"), "phrase": req.get("phrase"), "beats": [round(float(b), 4) for b in beats],
            "last_onset": float(ns[-1, 1]),
            "metre": md if md is not None else None if m is None else _metre_dict(m),
            "key_fifths": P.KEY_FIFTHS[k], "key_p": round(float(kp[k]), 3),
            "key_probs": [round(float(x), 4) for x in kp],      # the builder weighs the signature with the phrase's notes
            "ts_triple": round(float(np.mean(out["ts"] > 0.5)), 3) if len(out["ts"]) else None,
            "hands": hands, **extra}


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
