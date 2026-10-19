#!/usr/bin/env python3
"""CPU of the madmom-DBN candidate against our clock, per second of audio, on the same music (one thread, as live).

  flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-show/bin/python \\
      tools/room/beat_eval/alt/dbn_bench.py AUDIO [--start-s 30] [--dur-s 60] [--reps 2] [--variant best] [--set k=v]

What it times (process CPU time, OMP/OpenBLAS at one thread as deploy/sm-show.env; the best of ``--reps``):
- ``act2`` / ``act8``: madmom's online LSTM alone (clock.MadmomActivation, 2 and 8 nets), per 441-sample hop, on
  the soxr'd 44.1 kHz audio: what beatproc.py's child process spends live (it runs the activation and nothing else);
- ``dbn``: the candidate's tracker (madmom_dbn.DbnTracker: forward step, line, conf, gate) on those activations;
- ``clock2``: our whole Clock (AGC + soxr + madmom 2 nets + peaks/tempogram/PLL/publish) inline, per 128-frame block;
- ``dbnclock2`` / ``dbnclock8``: the candidate's whole clock (DbnClock: the same front end + nets + tracker) inline.
Reported as ms of CPU per audio second (10 ms/s = 1 % of a core) and as multiples of act2 (madmom's 2 nets, ~0.13 of
a core live in its child process on 18 Oct, under the 4K render; this host's offline numbers run lower).
No quiet gate (the music plays throughout), so madmom runs on every hop.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_k] = "1"

import numpy as np  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("audio", type=pathlib.Path)
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--dur-s", type=float, default=60.0)
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--variant", default="best", help="madmom_dbn.VARIANTS (the tracker's settings; nets 2 and 8 both run)")
    ap.add_argument("--set", action="append", default=[], dest="sets")
    ap.add_argument("--json", type=pathlib.Path, default=None)
    a = ap.parse_args(argv)
    import warnings
    warnings.simplefilter("ignore")
    import soxr
    import run_stack
    import madmom_dbn as mdb
    from show_service import clock as clockmod
    from show_service.contract import AUDIO_SEGMENT

    y = run_stack.to_mono(run_stack.decode(a.audio, a.start_s, a.dur_s))
    dur = len(y) / 48000.0
    y44 = soxr.resample(y, 48000, 44100).astype(np.float32)
    hops = [y44[i:i + 441] for i in range(0, len(y44) - 440, 441)]
    out: dict = {"audio": str(a.audio), "start_s": a.start_s, "audio_s": round(dur, 2), "hops": len(hops)}

    def best(fn) -> float:
        return min(fn() for _ in range(max(1, a.reps)))

    acts = {}
    for n in (2, 8):
        act = clockmod.MadmomActivation(nets=n)

        def run_act(act=act, n=n) -> float:
            act.reset()
            v = np.empty(len(hops))
            c0 = time.process_time()
            for i, h in enumerate(hops):
                v[i] = act.process_hop(h)
            c = time.process_time() - c0
            acts[n] = v
            return c
        out[f"act{n}"] = best(run_act)

    cfg = mdb.settings(a.variant, a.sets)
    out["variant"] = a.variant

    def run_dbn() -> float:
        trk = mdb.DbnTracker(cfg)
        c0 = time.process_time()
        for i, v in enumerate(acts[2]):
            trk.frame(float(v), 1000.0 + (i + 1) * 0.01)
        return time.process_time() - c0
    out["dbn"] = best(run_dbn)

    def run_clock(make) -> float:
        clk = make()
        c0 = time.process_time()
        for n0 in range(0, len(y) - 127, 128):
            clk.process("bench", n0, 1000.0 + n0 / 48000.0, y[n0:n0 + 128].reshape(-1, 1), 48000,
                        AUDIO_SEGMENT if n0 == 0 else 0)
            if n0 % 480 == 0:
                clk.tick(1000.0 + n0 / 48000.0)
        c = time.process_time() - c0
        clk.stop()
        return c

    DbnClock = mdb.make_clock_class()
    out["clock2"] = best(lambda: run_clock(lambda: clockmod.Clock(activation="madmom", nets=2)))
    for n in (2, 8):
        c = dict(cfg, nets=n)
        out[f"dbnclock{n}"] = best(lambda c=c: run_clock(lambda: DbnClock(None, cfg=c)))

    print(f"{a.audio.name} {a.start_s:g}+{dur:.0f} s, {len(hops)} hops, best of {a.reps}; CPU ms per audio second "
          f"(10 = 1 % of a core), x act2")
    a2 = out["act2"]
    for k in ("act2", "act8", "dbn", "clock2", "dbnclock2", "dbnclock8"):
        v = out[k]
        print(f"  {k:10s} {v / dur * 1000:7.1f} ms/s  x{v / a2:5.2f}  ({v / len(hops) * 1e6:6.0f} us a hop)")
        out[k + "_ms_per_s"] = round(v / dur * 1000, 2)
    if a.json:
        a.json.write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
