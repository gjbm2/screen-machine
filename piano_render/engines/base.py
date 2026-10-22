"""The engine interface: a Performance in, raw stereo audio out, sample 0 == MIDI time 0.

An engine is a class with
    name                                    the "engine" value in instruments.json
    available(engines_root) -> bool         (classmethod) can it run on this box?
    version(engines_root) -> str            (classmethod) what will render (goes into the render cache key)
    __init__(inst, engines_root)            inst = Registry.get(id); loading may be lazy (first render)
    render(perf, sr=48000, progress=None, pitch_cents=0.0) -> np.ndarray (N, 2) float32
    close()
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


class Engine:
    name = "base"
    block = 512                      # frames per render block

    def __init__(self, inst: dict, engines_root: "str | Path"):
        self.inst = inst
        self.engines_root = Path(engines_root).expanduser()
        self._loaded_sr: Optional[int] = None
        self.gamma = float(inst.get("velocity_gamma") or 1.0)
        self.stats: dict = {}

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
        if self._loaded_sr != sr:
            t0 = time.monotonic()
            self._load(sr)
            self._loaded_sr = sr
            self.stats["load_s"] = round(time.monotonic() - t0, 3)

    def render(self, perf: Performance, sr: int = 48000, progress: Progress = None,
               pitch_cents: float = 0.0) -> np.ndarray:
        sr = int(sr)
        self.ensure_loaded(sr)
        self._reset()
        self._set_pitch(float(pitch_cents))
        B = self.block
        evs = perf.events
        last_t = perf.duration_s
        last_frame = int(round(last_t * sr))
        cap = last_frame + int(round(TAIL_CAP_S * sr))
        nblk = cap // B + 1
        out = np.zeros((2, nblk * B), np.float32)
        hold_blocks = max(1, int(np.ceil(SILENCE_HOLD_S * sr / B)))
        floor_db = tail_floor_dbfs(self.inst.get("gain_db"))
        floor = 10 ** (floor_db / 20)
        quiet = 0
        ended = "cap"
        i = 0
        n_ev = len(evs)
        t_wall = time.monotonic()
        c0 = time.process_time()
        next_progress = t_wall
        end = nblk * B
        peak_voices = 0
        half = self.half_pedal
        pedal_sent = {64: 0, 66: 0}                  # _reset() put both pedals up
        for b in range(nblk):
            start = b * B
            stop = start + B
            batch: list[tuple[int, Event, float]] = []
            while i < n_ev:
                f = int(round(evs[i].t_s * sr))
                if f >= stop:
                    break
                e = evs[i]
                i += 1
                if e.kind == "cc" and e.num in pedal_sent and not (half and e.num == 64):
                    v = 127 if e.value >= 64 else 0
                    if v == pedal_sent[e.num]:
                        continue
                    pedal_sent[e.num] = v
                    e = Event(e.t_s, "cc", e.num, v)
                elif e.kind == "cc" and e.num == 64:
                    pedal_sent[64] = e.value
                batch.append((max(0, f - start), e, self.velocity01(e.value) if e.kind == "on" else 0.0))
            self._render_block(out[0, start:stop], out[1, start:stop], batch)
            if i >= n_ev and stop > last_frame:
                voices = self._active_voices()
                peak_voices = max(peak_voices, voices)
                blk_peak = float(np.max(np.abs(out[:, start:stop])))
                quiet = quiet + 1 if blk_peak < floor else 0
                if voices == 0 or quiet >= hold_blocks:
                    end = stop
                    ended = "voices" if voices == 0 else "floor"
                    break
            elif b % 8 == 0:
                peak_voices = max(peak_voices, self._active_voices())
            if progress is not None:
                now = time.monotonic()
                if now >= next_progress:
                    next_progress = now + PROGRESS_EVERY_S
                    progress(min(stop / sr, last_t), last_t)
        if progress is not None:
            progress(last_t, last_t)
        if ended != "voices":
            nf = min(int(round(TAIL_FADE_S * sr)), max(0, end - last_frame))
            if nf > 0:
                ramp = (0.5 + 0.5 * np.cos(np.linspace(0.0, np.pi, nf + 1)[1:])).astype(np.float32)
                out[:, end - nf:end] *= ramp
        wall = time.monotonic() - t_wall
        cpu = time.process_time() - c0
        audio_s = end / sr
        self.stats.update(render_wall_s=round(wall, 3), render_cpu_s=round(cpu, 3), audio_s=round(audio_s, 3),
                          x_realtime_cpu=round(audio_s / cpu, 2) if cpu > 0 else None,
                          tail_s=round(max(0.0, end / sr - last_t), 3), tail_end=ended,
                          tail_floor_dbfs=round(floor_db, 1), peak_voices=peak_voices)
        return np.ascontiguousarray(out[:, :end].T)


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

    def _active_voices(self) -> int:
        return -1

    def close(self) -> None:
        for e in getattr(self, "_subs", []):
            if e is not None:
                e.close()
        self._loaded_sr = None
