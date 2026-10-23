"""The engine interface: a Performance in, raw stereo audio out, sample 0 == MIDI time 0.

An engine is a class with
    name                                    the "engine" value in instruments.json
    available(engines_root) -> bool         (classmethod) can it run on this box?
    version(engines_root) -> str            (classmethod) what will render (goes into the render cache key)
    __init__(inst, engines_root)            inst = Registry.get(id); loading may be lazy (first render)
    render(perf, sr=48000, progress=None, pitch_cents=0.0) -> np.ndarray (N, 2) float32
    close()
and, for streaming (piano_render/stream.py), the same render in steps:
    realtime = True                         (set before loading) the engine will be paced at playback speed, so it
                                            may load samples in the background (sfizz: live mode, partial preload)
    open(sr)                                load (what ensure_loaded does; a no-op when loaded)
    start(perf, sr=48000, pitch_cents=0.0)  begin a render (clean state, as render() does)
    render_blocks(n) -> (m, 2) float32      render up to n more blocks and return the frames now final: all but the
                                            last holdback_frames(sr) (so the tail's fade can still reach them), the
                                            rest once the tail has ended; (0, 2) once everything has been returned
    finished                                True once the whole render has been returned
render() is built on the same per-block step, so a streamed render is the offline one, sample for sample.
Rules every engine keeps: no trimming (sample 0 is MIDI time 0, so a note at t plays from round(t * sr)); after the
last event the tail runs until no voice is active, or until the output has stayed below the tail floor for 0.5 s,
capped at the last event + 30 s; each render starts from a clean state (no held notes, pedals up, no pitch offset),
and pitch_cents moves the pitch only, never the timing.

The tail floor is -70 dBFS after the instrument's gain_db (so -70 - gain_db before it; kept within -120..-60 dBFS),
measured as the block peak of both channels. The voice count alone is not enough: a library can leave voices that
never end (sfizz 1.2.3 with a polyphony limit on release samples left hundreds hanging at -84 to -101 dBFS after
pedalled passages, measured 22 Oct on K18 and Maestro CF3), and every such render ran to the 30 s cap. When the
floor or the cap ends the tail, its last 50 ms are faded out (raised cosine), so the cut never clicks.

progress(done_s, total_s) is called at least every 0.25 s of wall time with MIDI seconds rendered.
The instrument's velocity_gamma maps every strike: v' = (v / 127) ** gamma (as a 0..1 velocity; engines with
integer velocities round it to 1..127).

Pedals: the damper (CC64) and sostenuto (CC66) reach the engine as on/off, 127 at >= 64 and 0 below, sent only
when that changes. That is the MIDI convention and FluidSynth's own rule; sfizz 1.2.3 would otherwise hold notes
at any CC64 >= 1 (its default sustain_lo, measured 22 Oct), which turns the MAESTRO files' resting-foot values
(11-63) into a pedal held down. An instrument with "half_pedal": true gets CC64 raw (for SFZs that model
half-pedalling themselves). The soft pedal (CC67) is always raw. The continuous values stay in the Performance
for the realism stage (score.Performance.controls).

Engine subclasses implement the hooks used by Engine.render (_load, _reset, _set_pitch, _send, _render_block,
_active_voices); a paid engine that renders a whole file in one go (Pianoteq's CLI, say) can override render().

Layers (LayeredEngine, made by engines.create for an instrument with "layers"): a blend of several files of one
engine, e.g. mic positions of one sampled piano recorded at the same time. Each layer {"file", "gain_db" (default 0),
"offset_ms" (default 0)} is rendered by the instrument's own engine with the same events (and the same pitch_cents),
scaled by its gain_db, moved by offset_ms rounded to whole samples (positive = later; a negative one drops the
layer's first samples, so sample 0 stays MIDI time 0), and summed. Each layer keeps the tail rules above with the
floor taken after the instrument's gain_db plus its own. Only one layer's engine is loaded at a time (a bitKlavier
mic set takes ~4.8 GB in sfizz), and the one loaded last stays loaded and renders first next time; the sum is
always made in the configured order.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..score import Event, Performance

Progress = Optional[Callable[[float, float], None]]

TAIL_CAP_S = 30.0
TAIL_FLOOR_DBFS = -70.0             # after the instrument's gain_db
TAIL_FLOOR_RANGE_DBFS = (-120.0, -60.0)   # the floor before gain stays within this
SILENCE_HOLD_S = 0.5
TAIL_FADE_S = 0.05
PROGRESS_EVERY_S = 0.25


def tail_floor_dbfs(gain_db: "float | None") -> float:
    """The engine's tail floor in dBFS before gain: TAIL_FLOOR_DBFS after the instrument's gain_db."""
    lo, hi = TAIL_FLOOR_RANGE_DBFS
    return float(min(hi, max(lo, TAIL_FLOOR_DBFS - float(gain_db or 0.0))))


class EngineError(RuntimeError):
    pass


class _Run:
    """One render in progress (Engine.start): the event cursor, the pedal values sent, and the tail bookkeeping."""

    def __init__(self, eng: "Engine", perf: Performance, sr: int):
        B = eng.block
        self.sr = sr
        self.B = B
        self.evs = perf.events
        self.n_ev = len(self.evs)
        self.last_t = perf.duration_s
        self.last_frame = int(round(self.last_t * sr))
        self.cap = self.last_frame + int(round(TAIL_CAP_S * sr))
        self.nblk = self.cap // B + 1
        self.hold_blocks = max(1, int(np.ceil(SILENCE_HOLD_S * sr / B)))
        self.floor_db = tail_floor_dbfs(eng.inst.get("gain_db"))
        self.floor = 10 ** (self.floor_db / 20)
        self.quiet = 0
        self.ended = "cap"
        self.i = 0                                   # next event
        self.b = 0                                   # next block
        self.end = self.nblk * B
        self.peak_voices = 0
        self.half = eng.half_pedal
        self.pedal_sent = {64: 0, 66: 0}             # _reset() put both pedals up
        self.done = False
        self.reported = False                        # streaming: stats written
        self.t_wall = time.monotonic()
        self.c0 = time.process_time()

    def fade_len(self) -> int:
        """Frames at the end of the render the tail's fade covers (0 when the voices ended by themselves)."""
        if self.ended == "voices":
            return 0
        return min(int(round(TAIL_FADE_S * self.sr)), max(0, self.end - self.last_frame))


def _fade_ramp(nf: int) -> np.ndarray:
    return (0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, nf + 1)[1:])).astype(np.float32)


class Engine:
    name = "base"
    block = 512                      # frames per render block
    realtime = False                 # streaming at playback pace (module docstring); set before loading

    def __init__(self, inst: dict, engines_root: "str | Path"):
        self.inst = inst
        self.engines_root = Path(engines_root).expanduser()
        self._loaded_sr: Optional[int] = None
        self._loaded_rt: Optional[bool] = None
        self.gamma = float(inst.get("velocity_gamma") or 1.0)
        self.stats: dict = {}
        self._run: Optional[_Run] = None
        self._pend: list = []                        # streaming: rendered (2, B) blocks not yet returned
        self._returned = 0                           # streaming: frames returned so far

    @property
    def half_pedal(self) -> bool:
        return bool(self.inst.get("half_pedal"))

    # --- class-level --------------------------------------------------------------------------------------
    @classmethod
    def available(cls, engines_root: "str | Path") -> bool:
        return False

    @classmethod
    def version(cls, engines_root: "str | Path") -> str:
        return cls.name

    # --- hooks ------------------------------------------------------------------------------------------------
    def _load(self, sr: int) -> None:
        raise NotImplementedError

    def _reset(self) -> None:
        raise NotImplementedError

    def _set_pitch(self, cents: float) -> None:
        raise NotImplementedError

    def _send(self, offset: int, ev: Event, vel01: float) -> None:
        """Dispatch one event ``offset`` frames into the coming block (vel01 = mapped 0..1 velocity for 'on')."""
        raise NotImplementedError

    def _render_block(self, out_l: np.ndarray, out_r: np.ndarray, events: list[tuple[int, Event, float]]) -> None:
        """Render len(out_l) frames into the two float32 views, applying ``events`` (offset, event, vel01) at
        their offsets. Default: send them all, then render (engines with sample-accurate offsets)."""
        raise NotImplementedError

    def _active_voices(self) -> int:
        return -1

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # --- shared ---------------------------------------------------------------------------------------------
    def velocity01(self, v: int) -> float:
        x = min(max(v, 1), 127) / 127.0
        return x ** self.gamma if self.gamma != 1.0 else x

    def ensure_loaded(self, sr: int) -> None:
        if self._loaded_sr != sr or self._loaded_rt != bool(self.realtime):
            t0 = time.monotonic()
            self._load(sr)
            self._loaded_sr = sr
            self._loaded_rt = bool(self.realtime)
            self.stats["load_s"] = round(time.monotonic() - t0, 3)

    def open(self, sr: int = 48000) -> None:
        """Load for this sample rate (and this realtime setting) now; a no-op when already loaded."""
        self.ensure_loaded(int(sr))

    def max_speed(self, sr: int = 48000) -> Optional[float]:
        """Streaming: the fastest this engine may render, in times real time (None: no limit). An engine that loads
        samples in the background while they play (sfizz in live mode) is safe only while every fresh note's
        preloaded start lasts long enough in wall time for its background load."""
        return None

    # --- one render, block by block (render() and the streaming calls share it) -------------------------------
    def start(self, perf: Performance, sr: int = 48000, pitch_cents: float = 0.0) -> None:
        """Begin a render of ``perf`` from a clean state; then render_blocks() (streaming) or render() (offline)."""
        sr = int(sr)
        self.ensure_loaded(sr)
        self._reset()
        self._set_pitch(float(pitch_cents))
        self._run = _Run(self, perf, sr)
        self._pend = []
        self._returned = 0

    def _step(self, run: _Run, out_l: np.ndarray, out_r: np.ndarray) -> bool:
        """Render block run.b into the two views (each run.B frames). False once the tail has ended with it."""
        B, sr = run.B, run.sr
        start = run.b * B
        stop = start + B
        evs = run.evs
        batch: list[tuple[int, Event, float]] = []
        while run.i < run.n_ev:
            e = evs[run.i]
            f = int(round(e.t_s * sr))
            if f >= stop:
                break
            run.i += 1
            if e.kind == "cc" and e.num in run.pedal_sent and not (run.half and e.num == 64):
                v = 127 if e.value >= 64 else 0
                if v == run.pedal_sent[e.num]:
                    continue
                run.pedal_sent[e.num] = v
                e = Event(e.t_s, "cc", e.num, v)
            elif e.kind == "cc" and e.num == 64:
                run.pedal_sent[64] = e.value
            batch.append((max(0, f - start), e, self.velocity01(e.value) if e.kind == "on" else 0.0))
        self._render_block(out_l, out_r, batch)
        b = run.b
        run.b = b + 1
        if run.i >= run.n_ev and stop > run.last_frame:
            voices = self._active_voices()
            run.peak_voices = max(run.peak_voices, voices)
            blk_peak = max(float(np.max(np.abs(out_l))), float(np.max(np.abs(out_r))))
            run.quiet = run.quiet + 1 if blk_peak < run.floor else 0
            if voices == 0 or run.quiet >= run.hold_blocks:
                run.end = stop
                run.ended = "voices" if voices == 0 else "floor"
                run.done = True
                return False
        elif b % 8 == 0:
            run.peak_voices = max(run.peak_voices, self._active_voices())
        if run.b >= run.nblk:                       # the cap (run.end and run.ended already say so)
            run.done = True
            return False
        return True

    def _finish_stats(self, run: _Run) -> None:
        wall = time.monotonic() - run.t_wall
        cpu = time.process_time() - run.c0
        audio_s = run.end / run.sr
        self.stats.update(render_wall_s=round(wall, 3), render_cpu_s=round(cpu, 3), audio_s=round(audio_s, 3),
                          x_realtime_cpu=round(audio_s / cpu, 2) if cpu > 0 else None,
                          tail_s=round(max(0.0, run.end / run.sr - run.last_t), 3), tail_end=run.ended,
                          tail_floor_dbfs=round(run.floor_db, 1), peak_voices=run.peak_voices)

    def render(self, perf: Performance, sr: int = 48000, progress: Progress = None,
               pitch_cents: float = 0.0) -> np.ndarray:
        self.start(perf, sr, pitch_cents)
        run = self._run
        B, sr = run.B, run.sr
        out = np.zeros((2, run.nblk * B), np.float32)
        next_progress = run.t_wall = time.monotonic()
        run.c0 = time.process_time()
        while True:
            start = run.b * B
            stop = start + B
            if not self._step(run, out[0, start:stop], out[1, start:stop]):
                break
            if progress is not None:
                now = time.monotonic()
                if now >= next_progress:
                    next_progress = now + PROGRESS_EVERY_S
                    progress(min(stop / sr, run.last_t), run.last_t)
        if progress is not None:
            progress(run.last_t, run.last_t)
        nf = run.fade_len()
        if nf > 0:
            out[:, run.end - nf:run.end] *= _fade_ramp(nf)
        self._finish_stats(run)
        self._run = None
        return np.ascontiguousarray(out[:, :run.end].T)

    # --- streaming ------------------------------------------------------------------------------------------
    def holdback_frames(self, sr: int = 48000) -> int:
        """Frames render_blocks keeps back until the tail has ended: the fade's length in whole blocks."""
        B = self.block
        return int(-(-int(round(TAIL_FADE_S * int(sr))) // B)) * B

    @property
    def finished(self) -> bool:
        run = self._run
        return run is None or (run.done and not self._pend)

    def render_blocks(self, n: int) -> np.ndarray:
        """After start(): render up to n more blocks; return the frames that are final now, (m, 2) float32, in
        order from frame 0 (module docstring)."""
        run = self._run
        if run is None:
            return np.zeros((0, 2), np.float32)
        B = run.B
        for _ in range(max(0, int(n))):
            if run.done:
                break
            blk = np.zeros((2, B), np.float32)
            self._step(run, blk[0], blk[1])
            self._pend.append(blk)
        if run.done:
            keep = 0
            if self._pend:
                pend = np.concatenate(self._pend, axis=1)
                nf = run.fade_len()
                if nf > 0:
                    pend[:, pend.shape[1] - nf:] *= _fade_ramp(nf)
                self._pend = [pend]
        else:
            keep = self.holdback_frames(run.sr) // B
        if len(self._pend) <= keep:
            out = np.zeros((0, 2), np.float32)
        else:
            give = self._pend[:len(self._pend) - keep]
            self._pend = self._pend[len(self._pend) - keep:]
            out = np.ascontiguousarray(np.concatenate(give, axis=1).T)
            self._returned += out.shape[0]
        if run.done and not self._pend and not run.reported:
            self._finish_stats(run)
            run.reported = True
        return out


def layer_specs(inst: dict) -> list[dict]:
    """The instrument's "layers" as [{"file", "gain_db", "offset_ms"}] (floats filled in), [] when it has none.
    Raises EngineError for a layer without a file."""
    out = []
    for i, layer in enumerate(inst.get("layers") or []):
        if not isinstance(layer, dict) or not layer.get("file"):
            raise EngineError(f"instrument {inst.get('id')!r}: layer {i} has no file")
        out.append({"file": layer["file"], "gain_db": float(layer.get("gain_db") or 0.0),
                    "offset_ms": float(layer.get("offset_ms") or 0.0)})
    return out


class StreamMixer:
    """Streams that arrive in pieces, summed: output frame p = sum_i gain_i * x_i[p - shift_i] (0 outside x_i;
    shift > 0 = later, < 0 = x_i's first -shift frames are never heard), from frame 0. push(i, frames, finished) as
    each stream renders; pull() returns the output frames every unfinished stream has reached (all of them once
    every stream is finished: the longest shifted stream sets the length). The sum runs in stream order, each term
    scaled in float32 first, exactly like LayeredEngine.render's."""

    def __init__(self, gains, shifts):
        self.g = [float(g) for g in gains]
        self.s = [int(s) for s in shifts]
        n = len(self.g)
        if n == 0 or len(self.s) != n:
            raise ValueError("StreamMixer needs one shift per gain, and at least one stream")
        self.buf = [np.zeros((0, 2), np.float32) for _ in range(n)]
        self.base = [0] * n                          # input frame index of buf[i][0]
        self.total = [0] * n                         # input frames pushed
        self.fin = [False] * n
        self.pos = 0                                 # output frames pulled

    @property
    def lookahead(self) -> int:
        """Input frames a stream needs beyond an output frame (a negative shift's)."""
        return max(0, -min(self.s))

    def push(self, i: int, x: np.ndarray, finished: bool = False) -> None:
        x = np.asarray(x, np.float32)
        if x.shape[0]:
            self.buf[i] = np.concatenate([self.buf[i], x]) if self.buf[i].shape[0] else x
            self.total[i] += x.shape[0]
        self.fin[i] = self.fin[i] or bool(finished)

    def _end(self) -> int:
        if all(self.fin):
            return max(0, max(t + s for t, s in zip(self.total, self.s)))
        return min(self.total[i] + self.s[i] for i in range(len(self.g)) if not self.fin[i])

    @property
    def done(self) -> bool:
        return all(self.fin) and self.pos >= self._end()

    def pull(self) -> np.ndarray:
        end = self._end()
        if end <= self.pos:
            return np.zeros((0, 2), np.float32)
        out = np.zeros((end - self.pos, 2), np.float32)
        for i, (g, s) in enumerate(zip(self.g, self.s)):
            a, b = self.pos - s, end - s             # input frames for this output span
            lo, hi = max(a, self.base[i]), min(b, self.total[i])
            if hi > lo:
                src = self.buf[i][lo - self.base[i]:hi - self.base[i]]
                out[lo - a:hi - a] += src * np.float32(g) if g != 1.0 else src
            drop = min(max(0, b - self.base[i]), self.buf[i].shape[0])
            if drop:
                self.buf[i] = self.buf[i][drop:]
                self.base[i] += drop
        self.pos = end
        return out


class LayeredEngine(Engine):
    """An instrument with "layers" (module docstring): one engine of the instrument's own kind per layer file,
    rendered with identical events and summed sample-aligned. ``create(inst, engines_root)`` makes a layer's engine
    (engines.create, given a copy of the instrument with that layer's file, no "layers", and gain_db = the
    instrument's plus the layer's, so its tail floor is the one its output is heard at)."""

    name = "layers"
    KEEP_LOADED = 1                  # layer engines kept loaded between renders

    def __init__(self, inst: dict, engines_root: "str | Path", create: Callable[[dict, "str | Path"], Engine]):
        super().__init__(inst, engines_root)
        self.layers = layer_specs(inst)
        if not self.layers:
            raise EngineError(f"instrument {inst.get('id')!r} has no layers")
        self._create = create
        self._subs: list[Optional[Engine]] = [None] * len(self.layers)
        self._order: list[int] = list(range(len(self.layers)))     # last rendered (still loaded) first

    def _layer_inst(self, i: int) -> dict:
        spec = self.layers[i]
        sub = {k: v for k, v in self.inst.items() if k != "layers"}
        sub["file"] = spec["file"]
        sub["gain_db"] = float(self.inst.get("gain_db") or 0.0) + spec["gain_db"]
        return sub

    def _sub(self, i: int) -> Engine:
        """Layer i's engine, current with self.inst; every other loaded one beyond KEEP_LOADED - 1 is closed first."""
        e = self._subs[i]
        if e is None:
            e = self._subs[i] = self._create(self._layer_inst(i), self.engines_root)
        else:
            e.inst = self._layer_inst(i)
        e.gamma = self.gamma
        return e

    def _make_room(self, i: int) -> None:
        loaded = [j for j in self._order if j != i and self._subs[j] is not None
                  and self._subs[j]._loaded_sr is not None]
        for j in loaded[max(0, self.KEEP_LOADED - 1):]:
            self._subs[j].close()

    def ensure_loaded(self, sr: int) -> None:
        """Load the layer the next render starts with (render.py's "load" stage); the others load as they render."""
        i = self._order[0]
        e = self._sub(i)
        if e._loaded_sr != sr:
            self._make_room(i)
            t0 = time.monotonic()
            e.ensure_loaded(sr)
            self.stats["load_s"] = round(time.monotonic() - t0, 3)
        self._loaded_sr = sr

    def render(self, perf: Performance, sr: int = 48000, progress: Progress = None,
               pitch_cents: float = 0.0) -> np.ndarray:
        sr = int(sr)
        n = len(self.layers)
        outs: list[Optional[np.ndarray]] = [None] * n
        per: list[dict] = [{} for _ in range(n)]
        last_t = perf.duration_s
        order = list(self._order)
        t_wall, c0 = time.monotonic(), time.process_time()
        for j, i in enumerate(order):
            e = self._sub(i)
            if e._loaded_sr != sr:
                self._make_room(i)
            t0 = time.monotonic()
            loaded_before = e._loaded_sr == sr
            e.ensure_loaded(sr)
            cb = None
            if progress is not None:
                cb = (lambda d, t, j=j: progress(last_t * (j + (min(d, t) / t if t > 0 else 1.0)) / n, last_t))
            a = np.asarray(e.render(perf, sr=sr, progress=cb, pitch_cents=pitch_cents), np.float32)
            g = 10.0 ** (self.layers[i]["gain_db"] / 20.0)
            if g != 1.0:
                a = a * np.float32(g)
            outs[i] = a
            per[i] = dict(e.stats, file=self.layers[i]["file"], gain_db=self.layers[i]["gain_db"],
                          offset_ms=self.layers[i]["offset_ms"], layer_load_s=0.0 if loaded_before
                          else round(e.stats.get("load_s", 0.0), 3), layer_wall_s=round(time.monotonic() - t0, 3))
        self._order = [order[-1]] + order[:-1]
        shifts = [int(round(spec["offset_ms"] * sr / 1000.0)) for spec in self.layers]
        total = max(max(0, a.shape[0] + s) for a, s in zip(outs, shifts))
        out = np.zeros((total, 2), np.float32)
        for a, s in zip(outs, shifts):                 # configured order: the same sum whatever ran first
            src = a[-s:] if s < 0 else a
            out[max(0, s):max(0, s) + src.shape[0]] += src
        del outs
        if progress is not None:
            progress(last_t, last_t)
        wall, cpu = time.monotonic() - t_wall, time.process_time() - c0
        longest = max(range(n), key=lambda i: per[i].get("audio_s", 0.0) + shifts[i] / sr)
        self.stats.update(
            layers=per, render_wall_s=round(wall, 3), render_cpu_s=round(cpu, 3), audio_s=round(total / sr, 3),
            x_realtime_cpu=round(total / sr / cpu, 2) if cpu > 0 else None,
            tail_s=round(max(0.0, total / sr - last_t), 3), tail_end=per[longest].get("tail_end"),
            tail_floor_dbfs=per[longest].get("tail_floor_dbfs"),
            peak_voices=max(int(p.get("peak_voices") or 0) for p in per),
            layer_loads_s=round(sum(p["layer_load_s"] for p in per), 3))
        return out

    # --- streaming: every layer's engine runs at once (render() above keeps one loaded at a time) ---------------
    def open(self, sr: int = 48000) -> None:
        t0 = time.monotonic()
        for i in range(len(self.layers)):
            e = self._sub(i)
            e.realtime = self.realtime
            e.open(sr)
        self._loaded_sr = int(sr)
        self._loaded_rt = bool(self.realtime)
        self.stats["load_s"] = round(time.monotonic() - t0, 3)

    def start(self, perf: Performance, sr: int = 48000, pitch_cents: float = 0.0) -> None:
        sr = int(sr)
        self.open(sr)
        self._streams = [self._sub(i) for i in range(len(self.layers))]
        for e in self._streams:
            e.start(perf, sr, pitch_cents)
        self._mixer = StreamMixer([10.0 ** (spec["gain_db"] / 20.0) for spec in self.layers],
                                  [int(round(spec["offset_ms"] * sr / 1000.0)) for spec in self.layers])
        self._run = None
        self._stream_reported = False

    def max_speed(self, sr: int = 48000) -> Optional[float]:
        caps = [c for c in (self._sub(i).max_speed(sr) for i in range(len(self.layers))) if c is not None]
        return min(caps) if caps else None

    def holdback_frames(self, sr: int = 48000) -> int:
        lead = getattr(self, "_mixer", None)
        return max(self._sub(i).holdback_frames(sr) for i in range(len(self.layers))) + \
            (lead.lookahead if lead is not None else 0)

    @property
    def finished(self) -> bool:
        m = getattr(self, "_mixer", None)
        return m is None or m.done

    def render_blocks(self, n: int) -> np.ndarray:
        m = getattr(self, "_mixer", None)
        if m is None:
            return np.zeros((0, 2), np.float32)
        for i, e in enumerate(self._streams):
            if not m.fin[i]:
                m.push(i, e.render_blocks(n), e.finished)
        out = m.pull()
        if m.done and not self._stream_reported:
            self._stream_reported = True
            per = [dict(e.stats, file=spec["file"]) for e, spec in zip(self._streams, self.layers)]
            self.stats.update(layers=per, audio_s=round(m.pos / self._streams[0]._loaded_sr, 3),
                              peak_voices=max(int(p.get("peak_voices") or 0) for p in per))
        return out

    def _active_voices(self) -> int:
        return -1

    def close(self) -> None:
        for e in getattr(self, "_subs", []):
            if e is not None:
                e.close()
        self._loaded_sr = None
