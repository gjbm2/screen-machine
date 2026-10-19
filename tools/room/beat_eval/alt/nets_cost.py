#!/usr/bin/env python3
"""madmom's online beat LSTM ensemble: CPU per audio second for 1, 2, 4 and 8 nets, measured side by side on the same
audio (interleaved rounds, so the host's load hits every net count alike), for variant (i)'s cost line.

  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 flock ~/.cache/screen-machine/cpu.lock nice -n 19 \\
      ~/.venvs/sm-show/bin/python tools/room/beat_eval/alt/nets_cost.py [AUDIO] [--seconds 10] [--rounds 3]

It times ``clock.MadmomActivation(nets=N).process_hop`` (what the clock, or beatproc's child, runs per 10 ms hop)
with process CPU time (time.process_time: this process only, every thread), and prints ms of CPU per second of audio
(= thousandths of a core while music plays) per net count, plus the ratio to 2 nets.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "room" / "beat_eval"))

import numpy as np  # noqa: E402

from show_service import clock as C  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("audio", nargs="?", default=str(REPO / "tools/room/audio_cal/runs/20261018T145602Z-pc22-mic/"
                                                           "rec-music.wav"))
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--nets", default="1,2,4,8")
    a = ap.parse_args()
    import run_stack
    import soxr
    x = run_stack.to_mono(run_stack.decode(pathlib.Path(a.audio), 0.0, a.seconds))
    y = soxr.resample(x, 48000, C.FS).astype(np.float32)
    y *= np.float32(10 ** ((C.AGC_TARGET_DB - 10 * np.log10(np.mean(y ** 2) + 1e-20)) / 20))   # the lane's AGC aim
    hops = [y[i:i + C.HOP] for i in range(0, len(y) - C.HOP + 1, C.HOP)]
    nets = [int(n) for n in a.nets.split(",")]
    acts = {n: C.MadmomActivation(nets=n) for n in nets}
    cpu = {n: [] for n in nets}
    for _ in range(a.rounds):
        for n in nets:
            act = acts[n]
            act.reset()
            t = time.process_time()
            for h in hops:
                act.process_hop(h)
            cpu[n].append((time.process_time() - t) / (len(hops) / C.FPS) * 1000.0)
    base = float(np.median(cpu[2])) if 2 in cpu else None
    for n in nets:
        m = float(np.median(cpu[n]))
        print(f"nets {n}: {m:6.1f} ms CPU per audio s (rounds {', '.join(f'{v:.0f}' for v in cpu[n])})"
              + (f", x{m / base:.2f} of 2 nets" if base else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
