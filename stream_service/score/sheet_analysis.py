"""The slower, better reading of a performance, asked for on its own (owner, 23 Oct: "if there's a more sophisticated
neural analysis pass then user could request that on demand").

The live score's own networks (PM2S: beats and downbeats, hands, key, time signature; pm2s_net.py) run ONCE over the
whole piece, in overlapping chunks (CHUNK notes, each keeping its middle: a bidirectional network wants context both
ways), not on a 12 s window every second as on the screens (each note read ~12 times: minutes a piece).  Then
sheet.transcribe plays the piece into the live ScoreBuilder in simulated time and answers each of its analysis
requests from those whole-piece outputs, sliced to the request's notes and post-processed per window as
analyse.py does (one global beat track drifted the builder's grid), in its reply format.
Needs torch: run by ~/.venvs/sm-score-ml's python (sheet.py's make --neural).  ~25 ms of CPU a note.
"""
from __future__ import annotations

import os
import pathlib
from typing import Callable, Optional

import numpy as np

CHUNK, KEEP = 480, 360               # notes a pass, and the middle of it kept (60 either side are context)
THREADS = 4
WEIGHTS = pathlib.Path(os.environ.get("SM_PM2S_WEIGHTS", pathlib.Path.home() / ".local/share/screen-machine/pm2s"))


class WholePiece:
    """``__call__(request) -> reply`` for ScoreBuilder.analysis_request()'s requests, from one pass over ``events``."""

    def __init__(self, events: list, progress: Optional[Callable[[float], None]] = None) -> None:
        from stream_service.piano_replay import NOTE_OFF, NOTE_ON
        from stream_service.score import pm2s_net as P
        from stream_service.score.metre import MetreModel
        held: dict = {}
        rows = []
        for te, st, d1, d2 in sorted(events):
            if st == NOTE_ON and d2 > 0:
                held.setdefault(d1, []).append((te, d2))
            elif st in (NOTE_ON, NOTE_OFF) and held.get(d1):
                on, v = held[d1].pop(0)
                rows.append((d1, on, max(0.03, te - on), v))
        rows.sort(key=lambda r: (r[1], r[0]))
        self.P, self.model = P, MetreModel()
        self.ns = np.array(rows, dtype=np.float64).reshape(-1, 4)
        n = len(self.ns)
        net = P.PM2S(WEIGHTS, threads=THREADS)
        keys = ("beat", "down", "hand", "ts")
        self.out = {k: np.zeros(n) for k in keys}
        self.out["key"] = np.zeros((n, 12))
        starts = list(range(0, max(1, n - CHUNK + KEEP), KEEP)) if n > CHUNK else [0]
        for c, s0 in enumerate(starts):
            lo, hi = max(0, s0 - (CHUNK - KEEP) // 2), min(n, s0 + KEEP + (CHUNK - KEEP) // 2)
            if c == len(starts) - 1:
                hi = n
            seg = self.ns[lo:hi].copy()
            seg[:, 1] -= seg[0, 1]
            o = net(seg)
            a, b = s0, (n if c == len(starts) - 1 else min(n, s0 + KEEP))
            for k in keys:
                self.out[k][a:b] = np.asarray(o[k]).reshape(-1)[a - lo:b - lo]
            self.out["key"][a:b] = o["key"][a - lo:b - lo]
            if progress is not None:
                progress(0.8 * (c + 1) / len(starts))
        self.index = {(int(p), int(round(t * 100))): i for i, (p, t, _d, _v) in enumerate(self.ns)}

    def _find(self, pitch: int, t: float) -> Optional[int]:
        k = int(round(t * 100))
        for d in (0, -1, 1, -2, 2):
            i = self.index.get((pitch, k + d))
            if i is not None:
                return i
        return None

    def __call__(self, req: dict) -> dict:
        P = self.P
        t0 = float(req["t0"])
        rows = req["notes"]
        idx, ids = [], []
        for pitch, rel, _dur, _vel, nid in rows:
            i = self._find(int(pitch), t0 + float(rel))
            if i is not None:
                idx.append(i)
                ids.append(int(nid))
        if len(idx) < 4:
            return {"ok": False, "t0": t0, "phrase": req.get("phrase"), "error": "notes not found"}
        idx = np.array(idx)
        ns = self.ns[idx].copy()
        ns[:, 1] -= t0
        last = float(ns[:, 1].max())
        b, d = P.beat_pps(self.out["beat"][idx], self.out["down"][idx], ns[:, 1])     # per window, as analyse.py
        m = self.model(ns, b, self.out["down"][idx], self.out["ts"][idx], d)
        w = np.clip(ns[:, 2], 0.05, 2.0)[:, None]
        kp = (self.out["key"][idx] * w).sum(0)
        kp = kp / kp.sum()
        k = int(np.argmax(kp))
        return {"ok": True, "t0": t0, "phrase": req.get("phrase"), "beats": [round(float(x), 4) for x in b],
                "last_onset": last,
                "metre": None if m is None else {"beats": m.beats_per_bar, "phase": m.phase, "compound": m.compound,
                                                 "conf": round(m.confidence, 3), "compound_p": round(m.compound_p, 3),
                                                 "probs": {str(a): round(v, 3) for a, v in m.probs.items()},
                                                 "phases": {str(a): v for a, v in m.phases.items()}},
                "key_fifths": P.KEY_FIFTHS[k], "key_p": round(float(kp[k]), 3),
                "key_probs": [round(float(x), 4) for x in kp],
                "ts_triple": round(float(np.mean(self.out["ts"][idx] > 0.5)), 3),
                "hands": {str(i): (1 if h > 0.5 else 0) for i, h in zip(ids, self.out["hand"][idx])}}
