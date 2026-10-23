"""Render as it plays: a MIDI performance -> stereo float32 blocks, paced to the sink, nothing written to disk.

    st = Streamer(perf_or_midi_path, "concert-d")           # an instrument id (or a Registry.get() entry)
    st.open()                                                # load the engine (partial preload), IR, tuning
    for blk in st.blocks(position=lambda: sink_frames_played()):
        sink.write(blk.data)                                 # blk.pos = frame of blk.data[0]; frame 0 = MIDI time 0
    st.close(); st.stats

What it does (piano_render/README.md, "Latency and compute", has the measurements):
  * The engine runs with ``realtime = True``: sfizz in live mode with only the start of each sample preloaded
    (engines/sfizz.py STREAM_PRELOAD: a load takes 1-6 s and ~100-300 MB, not 10-50 s and 2.5-7 GB), the rest read by
    sfizz's background loader while a voice plays its preloaded start. That is safe only because the render is paced:
    a fresh note gives the loader the preload's length in wall time. FluidSynth loads its SF2 whole (17-141 MB).
  * Engine blocks go through dsp/streaming.py's chain: una corda, EQ and width, the resonance bank, the room
    (uniform-partitioned convolution), the end trim, and the master (the instrument's calibrated gain_db and a
    3.2 ms lookahead true-peak limiter at -1 dBTP; no whole-file loudness pass). Detuned copies ("detune_cents")
    and blend layers ("layers") are parallel engine instances summed block by block.
  * Pacing: blocks(position) yields a block whenever the output is less than ahead_s ahead of position() (the
    sink's playback position in frames; default: the wall clock from the moment the first ahead_s were ready, i.e.
    a sink that starts playing then). Faster than that only to fill the buffer; otherwise it sleeps. Even then the
    engine never renders faster than its max_speed (sfizz in live mode: STREAM_PRELOAD / LOADER_BUDGET_S, 2.3x, so
    each fresh note leaves its background load 0.3 s of wall time; FluidSynth: no limit); next_block() and
    render_all() keep that limit too unless max_speed=None. A block handed
    over after the sink already reached its first frame counts as late (stats "late_blocks", "max_late_ms").
  * The pipeline holds back a little beyond what it hands over: the engine's tail-fade holdback (2,560 frames),
    the resonance FIRs and bank block (<= ~1,150), the room's partition (<= 1,023) and the limiter (154): about
    100 ms, so the engine itself runs up to ahead_s + 0.1 s ahead of the sink. stats "max_lag_frames" says how far.
  * Output: sample 0 = MIDI time 0; the end is the realism trim's (tails below -120 dBFS), within QUIET_END_S.

prepare_process(): the streaming process's own settings (call it first, before anything loads): normal scheduling
(it leaves SCHED_IDLE if it inherited it and Linux lets it), nice NICE (5: under the TVs' streams, above batch work;
SCHED_IDLE starves under load), single-threaded DSP (the package sets OMP/NUMBA threads to 1), and a hard memory cap
by RLIMIT_DATA (DATA_CAP_MB; allocations beyond it fail, so a runaway can't push the box into swap).

    python -m piano_render.stream MIDI --instrument ID [--room ROOM|none] [--no-realism] [--ahead S] [--unpaced]
                                       [--out FILE.wav | --out -]  [--json]
writes a 32-bit float WAV (or raw f32le stereo 48 kHz to stdout with --out -), paced like a sink at 1x unless
--unpaced; the stats go to stderr (one JSON line), or to stdout with --json when --out is a file.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import resource
import sys
import threading
import time
from typing import Callable, Iterator, NamedTuple, Optional, Union

import numpy as np

from . import engines as _engines
from . import instruments as _instruments
from . import score as _score

log = logging.getLogger("piano_render.stream")

SR = 48000
BLOCK = 512
AHEAD_S = 0.5
AHEAD_RANGE_S = (0.05, 30.0)
NICE = 5
DATA_CAP_MB_ENV = "PIANO_STREAM_DATA_CAP_MB"
DATA_CAP_MB = 3072.0                  # README "Latency and compute": the heaviest stream measured, with headroom
CTL_EXTRA_S = 45.0                    # control rows past the last event: engine tail cap + bank tail + margin
CHAIN_FRAMES = 2048                   # frames per push through the realism chain (fewer, larger numpy calls)


class Block(NamedTuple):
    pos: int                          # frame of data[0]; frame 0 = MIDI time 0
    data: np.ndarray                  # (n, 2) float32


def _status_kb() -> dict:
    d: dict = {}
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                k, _, v = line.partition(":")
                if k in ("VmRSS", "VmHWM", "VmData"):
                    d[k] = int(v.split()[0])
    except OSError:
        pass
    return d


def prepare_process(nice: int = NICE, data_cap_mb: Optional[float] = None) -> dict:
    """This process's scheduling and memory cap for one stream (module docstring). data_cap_mb None:
    $PIANO_STREAM_DATA_CAP_MB, else DATA_CAP_MB; 0 = no cap. Returns what it did."""
    out: dict = {}
    try:
        if os.sched_getscheduler(0) == getattr(os, "SCHED_IDLE", -1):
            os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
            out["left_sched_idle"] = True
    except (OSError, AttributeError) as e:
        out["sched_idle_stays"] = str(e)
    try:
        cur = os.getpriority(os.PRIO_PROCESS, 0)
        if cur < nice:
            os.setpriority(os.PRIO_PROCESS, 0, nice)
        out["nice"] = os.getpriority(os.PRIO_PROCESS, 0)
    except OSError as e:
        out["nice_error"] = str(e)
    if data_cap_mb is None:
        env = os.environ.get(DATA_CAP_MB_ENV)
        data_cap_mb = float(env) if env else DATA_CAP_MB
    if data_cap_mb and data_cap_mb > 0:
        cap = int(data_cap_mb * (1 << 20))
        soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
        new_hard = cap if hard == resource.RLIM_INFINITY or hard > cap else hard
        resource.setrlimit(resource.RLIMIT_DATA, (min(cap, new_hard), new_hard))
        out["data_cap_mb"] = round(min(cap, new_hard) / (1 << 20), 1)
        # At the cap sfizz's loader throws std::bad_alloc and the process aborts (measured 23 Oct): that must not
        # also write a core (WSL pipes it to a ~1 GB .dmp in %TEMP%\wsl-crashes). No core at all from this process.
        try:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            import ctypes
            ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)          # PR_SET_DUMPABLE = 4: not dumpable
            out["core_dumps"] = False
        except (OSError, ValueError, AttributeError) as e:
            out["core_dumps_error"] = str(e)
    return out


class _Voices:
    """Several engine streams summed (detuned copies at 1 / sqrt(n) each, realism.py's mix)."""

    def __init__(self, engs: list):
        from .engines.base import StreamMixer
        self.engs = engs
        n = len(engs)
        self.mix = StreamMixer([1.0 / math.sqrt(n)] * n, [0] * n)

    def render_blocks(self, k: int) -> np.ndarray:
        for i, e in enumerate(self.engs):
            if not self.mix.fin[i]:
                self.mix.push(i, e.render_blocks(k), e.finished)
        return self.mix.pull()

    @property
    def finished(self) -> bool:
        return self.mix.done


class Streamer:
    def __init__(self, perf: Union[_score.Performance, str, os.PathLike], instrument: Union[str, dict, None] = None,
                 *, registry=None, sr: int = SR, block: int = BLOCK, ahead_s: float = AHEAD_S,
                 room: Optional[str] = None, realism: bool = True, gain_db: Optional[float] = None,
                 limiter: bool = True, pitch_cents: float = 0.0, max_speed: Union[float, str, None] = "auto",
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.perf = perf if isinstance(perf, _score.Performance) else _score.load_midi(perf)
        self.reg = registry
        self.instrument = instrument
        self.sr = int(sr)
        self.block = int(block)
        if not 64 <= self.block <= 8192:
            raise ValueError(f"block {block} frames: want 64..8192")
        self.ahead_s = float(min(max(ahead_s, AHEAD_RANGE_S[0]), AHEAD_RANGE_S[1]))
        self.room_arg = room
        self.realism = bool(realism)
        self.gain_override = gain_db
        self.limiter = bool(limiter)
        self.pitch_cents = float(pitch_cents)
        self.clock = clock
        self.sleep = sleep
        self.max_speed_arg = max_speed
        self.max_speed: Optional[float] = None
        self.engines: list = []
        self.chain = None
        self.stats: dict = {}
        self._opened = False
        self._started = False

    # --- open ------------------------------------------------------------------------------------------------
    def open(self) -> dict:
        """Load everything a stream needs; returns (and keeps in stats) what it took."""
        if self._opened:
            return self.stats
        from . import render as _render
        t0, c0 = time.monotonic(), time.process_time()
        reg = self.reg if self.reg is not None else _instruments.load()
        self.reg = reg
        if isinstance(self.instrument, dict):
            inst = dict(self.instrument)
        else:
            inst_id = self.instrument or reg.default
            if not inst_id:
                raise KeyError("no instrument given and the registry has no default")
            inst = reg.get(inst_id)
        self.inst = inst
        if inst.get("engine") in _engines.NEVER_RENDERED:
            raise ValueError(f"instrument {inst.get('id')!r} uses {inst.get('engine')!r}, the live PC22 synth")
        room_id = _render.resolve_room(reg, inst, self.room_arg, self.realism) if self.realism else None
        room = reg.room(room_id) if room_id else None
        cents = [0.0]
        if self.realism:
            cents += [float(c) for c in (inst.get("detune_cents") or []) if float(c) != 0.0]
        for _c in cents:
            e = _engines.create(inst, reg.engines_root)
            e.realtime = True
            self.engines.append(e)
        # The chain (its imports: scipy.signal alone takes 1.8-4 s here; the room's IR; the pedal and key state of
        # the whole piece for the bank) is built in a thread while the samples load: the engine loads in C (ctypes
        # lets go of the GIL), so the two overlap.
        built: dict = {}

        def build() -> None:
            t = time.monotonic()
            try:
                built["chain"] = self._build_chain(inst, room)
            except BaseException as e:               # re-raised below, in open()'s own thread
                built["error"] = e
            built["s"] = time.monotonic() - t

        th = threading.Thread(target=build, name="stream-chain", daemon=True)
        th.start()
        t_eng = time.monotonic()
        try:
            for e in self.engines:
                e.open(self.sr)
        finally:
            load_engine_s = time.monotonic() - t_eng
            th.join()
        if "error" in built:
            raise built["error"]
        self._cents = cents
        if self.max_speed_arg == "auto":
            caps = [c for c in (e.max_speed(self.sr) for e in self.engines) if c is not None]
            self.max_speed = min(caps) if caps else None
        else:
            self.max_speed = None if self.max_speed_arg is None else float(self.max_speed_arg)
        self.chain, cst = built["chain"]
        self._room = room
        cst["build_chain_s"] = round(built["s"], 3)
        gain = float(inst.get("gain_db") or 0.0) if self.gain_override is None else float(self.gain_override)
        self.stats.update(instrument=inst.get("id"), engine=inst.get("engine"), room=room_id if room else None,
                          realism=self.realism, voices=len(self.engines), gain_db=gain, sr=self.sr, block=self.block,
                          ahead_s=self.ahead_s, max_speed=None if self.max_speed is None else round(self.max_speed, 2),
                          load_s=round(time.monotonic() - t0, 3),
                          load_engine_s=round(load_engine_s, 3), load_cpu_s=round(time.process_time() - c0, 3),
                          engine_load=dict(self.engines[0].stats), mem_after_load_kb=_status_kb(), **cst)
        self._opened = True
        return self.stats

    def _build_chain(self, inst: dict, room: Optional[dict]):
        from .dsp import streaming as S
        sr = self.sr
        st: dict = {}
        gain = float(inst.get("gain_db") or 0.0) if self.gain_override is None else float(self.gain_override)
        if not self.realism:
            tone = S.ToneStage(inst, sr, None, una_corda=False, width=False)
            trim = S.TrimStage(sr)
            stages = [tone, trim, S.MasterStage(sr, gain, limiter=self.limiter)]
            self._trim = trim
            return S.Chain(stages), st
        from .dsp import resonance as _res
        ctl = self.perf.controls(_res.FS_BANK / _res.BLOCK, duration_s=self.perf.duration_s + CTL_EXTRA_S)
        tone = S.ToneStage(inst, sr, ctl)
        stages: list = [tone]
        st["una_corda"] = dict(tone.uc_info)
        rdb = inst.get("resonance_db")
        if rdb is not None:
            try:
                tuning, how = self._tuning(inst)
                st["tuning"] = how
                prior = 10 ** ((S.BURST_PRIOR_DB - gain) / 20)
                stages.append(S.ResonanceStage(sr, self.perf, float(rdb), tuning,
                                               pan_width=float(inst.get("resonance_pan", _res.PAN_WIDTH)), ctl=ctl,
                                               rms_prior=prior))
            except ImportError as e:                 # numba missing: the rest still plays (realism.py's rule)
                log.warning("resonance bank skipped: %s", e)
                st["resonance_skipped"] = str(e)
        if room and room.get("wet_db") is not None:
            from .dsp import room as _room
            try:
                stages.append(S.RoomStage(room, sr))
            except (_room.RoomError, OSError, RuntimeError) as e:
                log.warning("room %s skipped: %s", room.get("id"), e)
                st["room_skipped"] = str(e)
        trim = S.TrimStage(sr)
        self._trim = trim
        stages += [trim, S.MasterStage(sr, gain, limiter=self.limiter)]
        return S.Chain(stages), st

    def _tuning(self, inst: dict):
        """The instrument's measured partials when analysis.py has them cached, else the model (measuring is a
        5-minute probe render: never on the way to a play)."""
        from .dsp import analysis as _an
        eng = self.engines[0]
        try:
            path = _an.analysis_dir() / f"{inst.get('id', 'inst')}-{_an.cache_key(inst, eng, self.sr)}.json"
            with open(path, encoding="utf-8") as f:
                t = _an.Tuning.from_json(json.load(f))
            return t, f"measured (cached, {t.measured} keys)"
        except (OSError, ValueError, KeyError, TypeError):
            return _an.model_tuning(), "model (no measured tuning cached for this instrument)"

    def rewind(self) -> dict:
        """Back to frame 0 for another play of the same piece (a loop, a re-press): the engines start again from a
        clean state with their samples still loaded, and the chain is built again (its state is the last play's; the
        imports are done by now, so this is the IR and the piece's control rows: tenths of a second). Never while
        another thread is in blocks(): the caller ends that first. Returns {"rewind_s"}."""
        if not self._opened or not self._started:
            return {"rewind_s": 0.0}
        t = time.monotonic()
        self.chain, cst = self._build_chain(self.inst, self._room)
        self._started = False
        self._ended = False
        took = round(time.monotonic() - t, 3)
        self.stats["rewind_s"] = took
        return {"rewind_s": took, **cst}

    # --- render ----------------------------------------------------------------------------------------------
    def _start(self) -> None:
        if not self._opened:
            self.open()
        for e, c in zip(self.engines, self._cents):
            e.start(self.perf, self.sr, self.pitch_cents + c)
        self._src = self.engines[0] if len(self.engines) == 1 else _Voices(self.engines)
        self._n_dry = 0
        self._eng_frames = 0                       # frames the engine has rendered (incl. its holdback)
        self._eng_done = False
        self._q: list = []
        self._qn = 0
        self._emitted = 0
        self._ended = False
        self._max_lag = 0
        self._late = 0
        self._max_late = 0
        self._t_wall = time.monotonic()
        self._c0 = time.process_time()
        self._started = True
        self._t_r0: Optional[float] = None
        self._speed_waits = 0
        self._zeros = np.zeros((max(CHAIN_FRAMES, self.engines[0].block), 2), np.float32)

    def _pump(self) -> None:
        if not self._eng_done:
            k = max(1, CHAIN_FRAMES // self.engines[0].block)
            if self.max_speed is not None:                # the engine's own speed limit (Engine.max_speed)
                if self._t_r0 is None:
                    self._t_r0 = self.clock()
                while True:
                    allowed = (self.clock() - self._t_r0) * self.max_speed * self.sr + k * self.engines[0].block
                    if self._eng_frames + k * self.engines[0].block <= allowed:
                        break
                    self._speed_waits += 1
                    self.sleep(min(0.05, max(0.0005, (self._eng_frames + k * self.engines[0].block - allowed)
                                             / (self.max_speed * self.sr))))
            x = self._src.render_blocks(k)
            self._eng_frames += k * self.engines[0].block
            self._n_dry += x.shape[0]
            if self._src.finished:
                self._eng_done = True
                self._trim.set_dry_end(self._n_dry)
                self.chain.end_input(self._n_dry)
        else:
            x = self._zeros
            self._eng_frames += x.shape[0]
        y = self.chain.push(x)
        if y.shape[0]:
            self._q.append(y)
            self._qn += y.shape[0]
        self._max_lag = max(self._max_lag, self._eng_frames - (self._emitted + self._qn))

    def _final_len(self) -> Optional[int]:
        e = self.chain.out_end
        return None if e is None else max(e, self._emitted)

    def next_block(self) -> Optional[Block]:
        """The next block, rendered now without pacing (None after the last)."""
        if not self._started:
            self._start()
        if self._ended:
            return None
        while self._qn < self.block and not self.chain.done:
            self._pump()
        end = self._final_len()
        n = self.block if end is None else min(self.block, end - self._emitted)
        n = min(n, self._qn)
        if n <= 0:
            self._finish()
            return None
        data = np.concatenate(self._q) if len(self._q) > 1 else self._q[0]
        out, rest = data[:n], data[n:]
        self._q = [rest] if rest.shape[0] else []
        self._qn = rest.shape[0]
        blk = Block(self._emitted, np.ascontiguousarray(out, np.float32))
        self._emitted += n
        if end is not None and self._emitted >= end:
            self._finish()
        return blk

    def blocks(self, position: Optional[Callable[[], float]] = None) -> Iterator[Block]:
        """Paced (module docstring). position(): the sink's playback position in frames; None = the wall clock from
        the moment the first ahead_s were ready."""
        if not self._started:
            self._start()
        ahead = int(round(self.ahead_s * self.sr))
        t0: Optional[float] = None
        while True:
            if position is not None:
                pos = float(position())
            elif t0 is None:
                pos = 0.0
            else:
                pos = (self.clock() - t0) * self.sr
            if self._emitted >= pos + ahead and (position is not None or t0 is not None):
                self.sleep(min(0.05, max(0.0005, (self._emitted - pos - ahead) / self.sr)))
                continue
            blk = self.next_block()
            if blk is None:
                return
            if position is not None or t0 is not None:     # where the sink is now, after the render
                now_pos = float(position()) if position is not None else (self.clock() - t0) * self.sr
                if blk.pos < now_pos:
                    self._late += 1
                    self._max_late = max(self._max_late, now_pos - blk.pos)
            yield blk
            if position is None and t0 is None and self._emitted >= ahead:
                t0 = self.clock()

    def _finish(self) -> None:
        if self._ended:
            return
        self._ended = True
        wall = time.monotonic() - self._t_wall
        cpu = time.process_time() - self._c0
        audio_s = self._emitted / self.sr
        eng = self.engines[0]
        self.stats.update(
            audio_s=round(audio_s, 3), dry_s=round(self._n_dry / self.sr, 3), wall_s=round(wall, 3),
            cpu_s=round(cpu, 3), cpu_pct_of_core=round(100.0 * cpu / audio_s, 1) if audio_s > 0 else None,
            late_blocks=self._late, max_late_ms=round(1000.0 * self._max_late / self.sr, 1),
            max_lag_frames=int(self._max_lag), speed_waits=self._speed_waits, mem_end_kb=_status_kb(),
            engine=dict(eng.stats),
            chain=self.chain.stats())

    def render_all(self) -> np.ndarray:
        """Everything at once, unpaced (tests, comparisons): (N, 2) float32."""
        parts = []
        while True:
            b = self.next_block()
            if b is None:
                break
            parts.append(b.data)
        return np.concatenate(parts) if parts else np.zeros((0, 2), np.float32)

    def close(self) -> None:
        for e in self.engines:
            try:
                e.close()
            except Exception:
                pass
        self.engines = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# --- CLI ------------------------------------------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m piano_render.stream", description=__doc__.split("\n")[0])
    ap.add_argument("midi")
    ap.add_argument("--instrument", default=None)
    ap.add_argument("--room", default=None)
    ap.add_argument("--no-realism", action="store_true")
    ap.add_argument("--no-limiter", action="store_true")
    ap.add_argument("--ahead", type=float, default=AHEAD_S, metavar="S")
    ap.add_argument("--block", type=int, default=BLOCK)
    ap.add_argument("--unpaced", action="store_true", help="render as fast as it can (no sink to pace it)")
    ap.add_argument("--out", default=None, metavar="FILE.wav|-")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--data-cap-mb", type=float, default=None)
    ap.add_argument("--config", default=None)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    prep = prepare_process(data_cap_mb=a.data_cap_mb)
    reg = _instruments.load(a.config) if a.config else None
    st = Streamer(a.midi, a.instrument, registry=reg, ahead_s=a.ahead, block=a.block, room=a.room,
                  realism=not a.no_realism, limiter=not a.no_limiter)
    raw = a.out == "-"
    sink = None
    if a.out and not raw:
        import soundfile as sf
        sink = sf.SoundFile(a.out, "w", samplerate=SR, channels=2, subtype="FLOAT")
    try:
        st.open()
        it = (iter(st.next_block, None) if a.unpaced else st.blocks())
        for blk in it:
            if raw:
                sys.stdout.buffer.write(np.ascontiguousarray(blk.data, "<f4").tobytes())
            elif sink is not None:
                sink.write(blk.data)
        if raw:
            sys.stdout.buffer.flush()
    finally:
        if sink is not None:
            sink.close()
        st.close()
    st.stats["process"] = prep
    line = json.dumps(st.stats, default=str)
    if a.json and not raw:
        print(line)
    else:
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
