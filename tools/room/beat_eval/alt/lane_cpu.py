#!/usr/bin/env python3
"""The rhythm lane's CPU per audio second, the same way for every candidate that replays through run_stack.Replay:
our clock (2 or 8 nets), madmom_dbn (DbnClock) and micfe (the front end + our clock).

  flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-show/bin/python \
      tools/room/beat_eval/alt/lane_cpu.py RUNNER AUDIO [run_stack.py options] [-- runner options] --cpu-out CPU.json

RUNNER: ``clock2`` (run_stack.py as it is: show_service's Clock, 2 nets), ``clock8`` (the same Clock with nets=8:
the live cost of ourclock's nets=8+b+a, whose b and a only add bookkeeping on the same hops; clockvar's replay
``clock_cpu`` shows it), ``madmom_dbn`` (madmom_dbn.py run), ``micfe`` (micfe_run.py). Why: the batch results' own
cost fields are wall time (run_batch's run_s) or each builder's own bench, which are not comparable; here every
call the replay makes into the clock object (process, wait_beat, tick, set_level, observe_hit) is timed with
``time.thread_time`` (madmom runs inline in that thread offline), split into the pre-roll (room sound) and the
music by the block's stream time. dsp (Features) is timed apart: it is the same for every candidate. micfe's front
end runs once over the stream before the replay (micfe_run.FEReplay.run); its streaming cost (micfe.stream_cost,
process time over 128-frame blocks) is added from the result. BeatNet does not replay through run_stack.Replay
(alt/lineclock.py); its runner reports its own stage CPU (``cpu`` in its result, process time).

Single thread: OMP/OPENBLAS/MKL threads 1 (set here before numpy loads). The host renders 4K at the same time, so
CPU time per call is inflated by contention; measure all candidates in one sitting and compare them side by side.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_k] = "1"

HERE = pathlib.Path(__file__).resolve().parent
BEAT_EVAL = HERE.parent
sys.path.insert(0, str(BEAT_EVAL))
sys.path.insert(0, str(HERE))

import run_stack  # noqa: E402

CLOCK_CALLS = ("process", "wait_beat", "tick", "set_level", "observe_hit")
FEATURE_CALLS = ("process", "tick")
ACC: dict = {"clock": [], "dsp": []}          # (stream/host time of the call, thread CPU s)


def _timed(fn, bucket: list, rp, when):
    tt = time.thread_time

    def w(*a, **k):
        t = tt()
        try:
            return fn(*a, **k)
        finally:
            bucket.append((when(rp, a), tt() - t))
    return w


def _when_block(rp, a):          # process(stream, n0, t0, ...): the block's stream time; others: the host time now
    return a[2] if len(a) >= 3 and isinstance(a[2], float) else rp.now


def _when_now(rp, a):
    return rp.now


_orig_init = run_stack.Replay.__init__


def _init(self, *a, **k):
    _orig_init(self, *a, **k)
    for name in CLOCK_CALLS:
        fn = getattr(self.clock, name, None)
        if fn is not None:
            setattr(self.clock, name, _timed(fn, ACC["clock"], self, _when_block if name == "process" else _when_now))
    for name in FEATURE_CALLS:
        fn = getattr(self.features, name, None)
        if fn is not None:
            setattr(self.features, name, _timed(fn, ACC["dsp"], self, _when_block if name == "process" else _when_now))


run_stack.Replay.__init__ = _init          # subclasses (micfe's FEReplay) call it through super()


def main(argv: list[str]) -> int:
    if len(argv) < 2 or "--cpu-out" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    i = argv.index("--cpu-out")
    cpu_out = pathlib.Path(argv[i + 1])
    argv = argv[:i] + argv[i + 2:]
    runner, rest = argv[0], argv[1:]
    stack_args, extra = (rest[:rest.index("--")], rest[rest.index("--") + 1:]) if "--" in rest else (rest, [])
    t_proc = time.process_time()
    if runner == "clock2":
        rc = run_stack.main(stack_args)
    elif runner == "clock8":
        from show_service import clock as clockmod
        base = clockmod.Clock

        class Clock8(base):
            def __init__(self, *a, **k):
                k["nets"] = 8
                super().__init__(*a, **k)
        clockmod.Clock = Clock8
        rc = run_stack.main(stack_args)
    elif runner == "madmom_dbn":
        import madmom_dbn
        rc = madmom_dbn.main(["run", *stack_args, *extra])
    elif runner == "micfe":
        import micfe_run
        rc = micfe_run.main([*stack_args, *extra])
    else:
        raise SystemExit(f"unknown runner {runner!r}")
    t_proc = time.process_time() - t_proc
    out = pathlib.Path(stack_args[stack_args.index("--out") + 1])
    r = json.loads(out.read_text())
    pre = float(r.get("preroll_s") or 0.0)
    zero = run_stack.T0 + pre + float((r.get("degrade") or {}).get("delay_s") or 0.0)
    audio_s = float(r.get("audio_s") or 0.0)
    music_s = max(1e-9, audio_s - pre)
    res = {"runner": runner, "extra": extra, "id": r.get("id"), "dataset": r.get("dataset"), "audio_s": audio_s,
           "preroll_s": pre, "music_s": music_s, "process_cpu_s": round(t_proc, 3), "stack": r.get("stack", {}).get("id")}
    for name, rows in ACC.items():
        mus = sum(dt for t, dt in rows if t >= zero)
        prr = sum(dt for t, dt in rows if t < zero)
        res[f"{name}_ms_per_s_music"] = round(1000.0 * mus / music_s, 2)
        res[f"{name}_ms_per_s_preroll"] = round(1000.0 * prr / pre, 2) if pre > 0 else None
        res[f"{name}_ms_per_s_all"] = round(1000.0 * (mus + prr) / max(audio_s, 1e-9), 2)
        res[f"{name}_calls"] = len(rows)
    cand = r.get("candidate") or {}
    if runner == "micfe":
        res["fe_ms_per_s"] = cand.get("fe_cost_ms_per_s")
        res["lane_ms_per_s_music"] = round(res["clock_ms_per_s_music"] + (res["fe_ms_per_s"] or 0.0), 2)
    else:
        res["lane_ms_per_s_music"] = res["clock_ms_per_s_music"]
    cpu_out.parent.mkdir(parents=True, exist_ok=True)
    cpu_out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
