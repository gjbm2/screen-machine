"""Effect chains: scene-attached effects and the master FX bus (EFFECTS_LAYER_DESIGN B.1).

GPU-agnostic like layers.py (the buffers come from the frame context), so it is unit-tested with NumPy fakes.

* :class:`EffectSlot` wraps one set-up effect plug-in (engine ``Effect``, the lab's ``smfx`` Effect or any class
  with the same interface).  Once per rendered frame it steps the slot's level envelope and the plug-in's params,
  applies the slot's bindings (Modulation) and scales ``amount`` by the fade level and the outer gain; ``render``
  then hands those params to the plug-in through its wrapped ``step_params``.  A slot whose effective amount is
  below :data:`AMOUNT_EPS` is skipped: an empty or silent chain costs nothing and passes frames through
  bit-exact.  A plug-in without an ``amount`` param runs in *mix* mode (its output crossfaded with the input).
* :class:`EffectChain` keeps slots in stage order (:data:`plugins.base.STAGES`; stable within a stage) and
  renders the active ones ping-pong through at most two pooled buffers (one more for a mix-mode slot).
* :class:`EffectLayer` wraps a scene's SourceLayer with its chain (scene-attached effects transition with it).
* :class:`MasterBus` runs one chain after the whole layer tree, with its own level envelope (closed until the
  ``in`` transition is done, faded out on exit and sealed on the frame the exit completes, so the held frame is
  bit-exact) and a master ``amount`` / ``pulse`` that can be bound to show signals.

Effects run on stream time (``t_local`` since the slot was first rendered), never on the scene clock.  Fade
envelopes advance by frame numbers, so repeats and skipped frames keep the timeline.

Tempo lock (owner, 17 Oct: an effect's natural pacing follows the bpm): while the session's beat clock is sure
(:func:`beat_clock`: show_service's clock, what cuts use, else the lighting desk's locked follower), each slot's
``x-tempo`` params run at one cycle per musical division (plugins/_lib/beat.py, :class:`TempoLock`), and the
plug-in gets the clock as ``beat_clock`` (with ``locks``: param -> beats per cycle) for an opt-in phase lock.

Each effect render is timed on the GPU when the frame context has a timer (guard.py); a slot over its budget is
tripped (``EffectSlot.trip``): failed and skipped from then on, i.e. dropped from the chain or the bus.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Iterator, Optional

from .layers import FrameCtx, Layer
from .plugins._lib.beat import BeatClock, cycles_per_s, tempo_unit, value_for
from .plugins.base import STAGES

AMOUNT_EPS = 1e-3
BUS_OPEN_S = 2.0          # bus fade-in after the art->live `in` transition is done
BUS_CLOSE_S = 1.0         # bus fade-out on exit (capped by the out transition's duration)
MAX_BUS_SLOTS = 6
MAX_SCENE_EFFECTS = 4
POOL_RESERVE = 7          # frames reserved before the first effect: 2 per transition level x 2 + 2 bus + 1 mix
MASTER_PROPS = {
    "amount": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0, "x-bindable": True},
    "pulse": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0, "x-bindable": True},
}


def stage_index(stage: Optional[str]) -> int:
    try:
        return STAGES.index(stage)            # type: ignore[arg-type]
    except ValueError:
        return STAGES.index("stylise")


def smoothstep(x: float) -> float:
    x = 0.0 if x <= 0.0 else 1.0 if x >= 1.0 else x
    return x * x * (3.0 - 2.0 * x)


TEMPO_SMOOTH_S = 1.0      # tempo lock: the pace it snaps is this slow an average (kicks and lurches ride on top)
TEMPO_EASE_S = 0.4        # ... and its scale eases in and out: the clock comes or goes, a new division


def beat_clock(runtime: Any) -> Optional[BeatClock]:
    """The session's beat clock at this frame's photon time while it is sure, else None: show_service's clock while
    fresh, gate "beat", locked or holding (what cuts use: SessionManager.music_grid), else the lighting desk's
    locked follower (no bars).  Render thread (``runtime``: modulation.SessionRuntime, after begin_frame); never
    raises."""
    try:
        if runtime is None:
            return None
        now = float(runtime.now)
        sampler = getattr(runtime, "sampler", None)
        if sampler is not None and getattr(sampler, "follow", "extrapolate") != "extrapolate":
            return None
        t = now + float(getattr(sampler, "lead", 0.0) or 0.0)
        feed = getattr(runtime, "feed", None)
        c = feed.clock(now) if feed is not None else None
        if c is not None and c.get("gate") == "beat" and c.get("state") in ("locked", "holding"):
            from .music_feed import beat_at
            bpm = float(c["bpm"])
            if bpm > 0.0 and math.isfinite(bpm):
                return BeatClock(beats=beat_at(c, t), bpm=bpm, beats_per_bar=max(1, int(c.get("beats_per_bar") or 4)),
                                 bar_beat=float(c.get("bar_anchor_beat") or 0.0), source="show")
        grid = sampler.beat_grid(now) if sampler is not None else None
        if grid is not None and grid[1] and grid[1] > 0.0:
            anchor, period = grid
            return BeatClock(beats=(t - anchor) / period, bpm=60.0 / period, source="desk")
    except Exception:
        return None
    return None


class TempoLock:
    """One slot's ``x-tempo`` params on the beat (render thread; plugins/_lib/beat.py).  Each frame, while the clock
    is sure, a param's slow average pace (TEMPO_SMOOTH_S, in octaves) snaps to a division (BeatClock.snap, with
    hysteresis) and its value is scaled by locked / average, so the mapping's kicks and the music's nudges ride on
    the locked pace; the scale eases (TEMPO_EASE_S) whenever it changes, so locking, a new division and letting go
    are glides, never jumps.  A pace with no division in reach, or whose locked value is off the param's range,
    keeps its value."""

    def __init__(self, props: dict):
        self.props = props
        self.units = {k: u for k, p in props.items()
                      if p.get("type") == "number" and (u := tempo_unit(p)) is not None}
        self.avg: dict[str, float] = {}       # log2 cycles/s, slow
        self.n: dict[str, float] = {}         # beats per cycle while locked
        self.k: dict[str, float] = {}         # the eased scale on cycles/s (1: free)
        self.locks: dict[str, float] = {}     # this frame's locked params (read by the API)
        self.started: set[str] = set()        # params seen before: only a slot's first frame may start locked

    def apply(self, p: dict, clock: Optional[BeatClock], dt: float) -> dict:
        """Scale ``p``'s tempo params in place; -> the locked ones this frame (param -> beats per cycle)."""
        a_s = 1.0 - math.exp(-dt / TEMPO_SMOOTH_S) if dt > 0.0 else 0.0
        a_k = 1.0 - math.exp(-dt / TEMPO_EASE_S) if dt > 0.0 else 0.0
        locks: dict[str, float] = {}
        for key, unit in self.units.items():
            v = p.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                continue
            cps = cycles_per_s(v, unit)
            target = 1.0
            n = None
            if cps is None:
                self.avg.pop(key, None)                       # stopped: average afresh when it moves again
            else:
                x = math.log2(cps)
                m = self.avg.get(key)
                m = x if m is None else m + (x - m) * a_s
                self.avg[key] = m
                if clock is not None:
                    n = clock.snap(2.0 ** m, self.n.get(key))
                if n is not None:
                    locked = clock.bps / n                    # type: ignore[union-attr]
                    lv = value_for(locked, unit, v)
                    prop = self.props[key]
                    lo, hi = prop.get("minimum"), prop.get("maximum")
                    if (lo is not None and lv < lo - 1e-9) or (hi is not None and lv > hi + 1e-9):
                        n = None                              # off the param's range: free
                    else:
                        target = locked / 2.0 ** m
            if n is None:
                self.n.pop(key, None)
            else:
                self.n[key] = locks[key] = n
            k = self.k.get(key)
            if k is None:
                k = 1.0 if key in self.started else target     # first frame: the slot is fading in anyway
            k += (target - k) * a_k
            self.started.add(key)
            if n is None and abs(k - 1.0) < 1e-3:
                self.k.pop(key, None)                         # free and settled: the value passes untouched
                continue
            self.k[key] = k
            out = v / k if unit == "period" else v * k
            prop = self.props[key]
            lo, hi = prop.get("minimum"), prop.get("maximum")
            if lo is not None:
                out = max(float(lo), out)
            if hi is not None:
                out = min(float(hi), out)
            p[key] = out
        self.locks = locks
        return locks


@dataclass
class SlotUpdate:
    """One slot's change in an FxUpdate.  params: the full validated plain params (the plug-in eases them);
    bindings: the slot's bindings after the change (None: unchanged); target: level (bypass -> 0)."""
    id: str
    params: Optional[dict] = None
    bindings: Optional[dict] = None
    target: Optional[float] = None
    remove: bool = False


@dataclass
class FxUpdate:
    """One PUT /fx or one auto move, applied atomically at a frame boundary (render thread)."""
    rev: int
    add: list = field(default_factory=list)          # EffectSlot, set up + primed, level 0
    update: list = field(default_factory=list)       # SlotUpdate
    fade_s: float = 2.0
    master: Optional[dict] = None                    # {"amount": value|binding, "pulse": value|binding}


class EffectSlot:
    def __init__(self, slot_id: str, effect: Any, *, name: str, preset: Optional[str] = None,
                 stage: Optional[str] = None, modulation: Any = None, mix: bool = False, level: float = 1.0,
                 target: float = 1.0, fade_s: float = 0.0, seq: int = 0, where: str = "bus",
                 label: Optional[str] = None):
        self.slot_id = slot_id
        self.effect = effect
        self.name = name
        self.preset = preset
        self.label = label or name
        self.stage = stage if stage in STAGES else getattr(effect, "stage", "stylise")
        if self.stage not in STAGES:
            self.stage = "stylise"
        self.modulation = modulation
        self.mix = mix
        self.level = max(0.0, min(1.0, float(level)))
        self.target = max(0.0, min(1.0, float(target)))
        self.fade_s = max(0.0, float(fade_s))
        self.seq = seq
        self.where = where                   # "bus" | "scene" (the failure action differs) | "gesture" (a bus slot, last)
        self.failed = False
        self.slow = False                    # tripped by the GPU guard
        self.removing = False
        self.n0: Optional[int] = None
        self.last_n: Optional[int] = None
        self.dt = 0.0
        self.amount_now = 0.0                # read by the API (plain float: racy reads are fine)
        self.render_count = 0
        self._p: Optional[dict] = None
        self._orig_step: Optional[Callable[[float], dict]] = None
        props = getattr(modulation, "props", None)
        self.tempo = TempoLock(props) if isinstance(props, dict) else None
        self.clock: Optional[BeatClock] = None

    # -- set-up -----------------------------------------------------------------------
    def install(self) -> None:
        """Wrap the plug-in's step_params: during render it returns this frame's params (eased, bound, faded).
        Works for engine Effects, smfx Effects and imported classes alike (an instance attribute)."""
        if self._orig_step is not None:
            return
        self._orig_step = self.effect.step_params
        slot = self

        def step_params(dt: float) -> dict:
            return slot._p if slot._p is not None else slot._orig_step(dt)   # type: ignore[misc]

        self.effect.step_params = step_params

    # -- per frame (render thread) -------------------------------------------------------
    def _step_level(self, dt: float) -> None:
        if self.level == self.target:
            return
        if self.fade_s <= 0.0:
            self.level = self.target
            return
        step = dt / self.fade_s
        if self.level < self.target:
            self.level = min(self.target, self.level + step)
        else:
            self.level = max(self.target, self.level - step)

    def frame(self, fc: FrameCtx, outer_gain: float = 1.0, pulse_gain: float = 1.0) -> bool:
        """Once per rendered frame, even when skipped: step the envelope and the params.  -> render this frame?"""
        self.install()
        if self.last_n is None:
            self.n0, dt = fc.n, 0.0
        else:
            dt = max(0, fc.n - self.last_n) / fc.fps
        self.last_n = fc.n
        self.dt = dt
        self._step_level(dt)
        if self.failed:
            self._p = None
            self.amount_now = 0.0
            return False
        try:
            p = dict(self._orig_step(dt))           # type: ignore[misc]
            if self.modulation is not None:
                p = self.modulation.apply(p)
            clock = beat_clock(getattr(self.modulation, "runtime", None))
            locks = self.tempo.apply(p, clock, dt) if self.tempo is not None and self.tempo.units else {}
            self.clock = replace(clock, locks=locks) if clock is not None else None
        except Exception as e:
            self._fail(fc, e)
            return False
        try:
            self.effect.beat_clock = self.clock     # plugins/_lib/beat.py: an opt-in phase lock reads it
            # the session's runtime (read-only: the frame's time, the render->photon lead, the palette), for a plug-in
            # that times things on the glass (gesture_accents, docs/ENSEMBLE.md §8.2)
            self.effect.session_runtime = getattr(self.modulation, "runtime", None)
        except Exception:
            pass
        g = smoothstep(self.level) * outer_gain
        if self.mix:
            amount = g
        else:
            amount = float(p.get("amount", 1.0)) * g
            p["amount"] = amount
        if "pulse" in p and pulse_gain != 1.0:
            p["pulse"] = float(p["pulse"]) * pulse_gain
        self._p = p
        self.amount_now = amount
        return amount > AMOUNT_EPS

    def render(self, fc: FrameCtx, src: Any, out: Any) -> None:
        t_local = (fc.n - (self.n0 or 0)) / fc.fps
        timer = fc.timer
        try:
            if self.mix:
                tmp = fc.pool.acquire()
                try:
                    tok = timer.begin(self, "effect", self.effect) if timer is not None else None
                    try:
                        self.effect.render(t_local, self.dt, src, tmp)
                    finally:
                        if tok is not None:
                            timer.end(tok)
                    fc.ops.crossfade(src, tmp, out, self.amount_now)
                finally:
                    fc.pool.release(tmp)
            else:
                tok = timer.begin(self, "effect", self.effect) if timer is not None else None
                try:
                    self.effect.render(t_local, self.dt, src, out)
                finally:
                    if tok is not None:
                        timer.end(tok)
            self.render_count += 1
        except Exception as e:    # C.1: passthrough, skipped from now on
            fc.ops.copy(src, out)
            self._fail(fc, e)

    def trip(self, fc: FrameCtx, exc: BaseException) -> None:
        """Over its GPU budget (guard.py): dropped (skipped from the next frame; a bus slot leaves the stack)."""
        if self.failed:
            return
        self.slow = True
        self._fail(fc, exc, "removed")

    def _fail(self, fc: FrameCtx, exc: BaseException, action: Optional[str] = None) -> None:
        self.failed = True
        self._p = None
        self.amount_now = 0.0
        fc.failed("effect", self, exc, action or ("passthrough" if self.where == "scene" else "removed"))

    # -- control (applied at a frame boundary) ------------------------------------------
    def set_target(self, target: float, fade_s: float) -> None:
        self.target = max(0.0, min(1.0, float(target)))
        self.fade_s = max(0.0, float(fade_s))

    def update(self, params: Optional[dict], bindings: Optional[dict]) -> None:
        if params is not None:
            self.effect.update(params)
        if bindings is not None and self.modulation is not None:
            self.modulation.set_bindings(bindings)

    @property
    def done(self) -> bool:
        """Faded out after a removal (or failed while being removed): ready to retire."""
        return self.removing and (self.level <= 0.0 or self.failed)

    @property
    def state(self) -> str:
        if self.failed:
            return "failed"
        if self.removing:
            return "fading_out"
        if self.target <= 0.0:
            return "bypassed" if self.level <= 0.0 else "fading_out"
        if self.level < self.target:
            return "fading_in"
        if self.level > self.target:
            return "fading_out"
        return "active"

    def describe(self) -> dict:
        d = {"id": self.slot_id, "effect": self.name, "preset": self.preset, "stage": self.stage,
             "level": round(self.level, 4), "target": round(self.target, 4),
             "amount": round(self.amount_now, 4), "failed": self.failed, "slow": self.slow}
        if self.tempo is not None and self.tempo.locks:
            d["tempo"] = dict(self.tempo.locks)     # param -> beats per cycle, locked to the beat
        return d


class EffectChain:
    def __init__(self, slots: Iterable[EffectSlot] = ()):
        self.slots: list[EffectSlot] = []
        self._seq = 0
        for s in slots:
            self.add(s)

    def add(self, slot: EffectSlot) -> None:
        self._seq += 1
        slot.seq = self._seq
        self.slots.append(slot)
        # the room's gesture slot (where "gesture", fx_state.GESTURE) is the last on the bus, after every overlay
        self.slots.sort(key=lambda s: (stage_index(s.stage), s.where == "gesture", s.seq))

    def get(self, slot_id: str) -> Optional[EffectSlot]:
        return next((s for s in self.slots if s.slot_id == slot_id), None)

    def apply(self, cmd: FxUpdate, fc: Optional[FrameCtx] = None) -> None:
        for s in cmd.add:
            self.add(s)
        for u in cmd.update:
            s = self.get(u.id)
            if s is None:
                continue
            if u.remove:
                s.removing = True
                s.set_target(0.0, cmd.fade_s)
                continue
            s.update(u.params, u.bindings)
            if u.target is not None:
                s.set_target(u.target, cmd.fade_s)

    def render(self, fc: FrameCtx, draw_input: Callable[[Any], None], out: Any, *, gain: float = 1.0,
               pulse_gain: float = 1.0) -> None:
        active = [s for s in self.slots if s.frame(fc, gain, pulse_gain)]
        if not active:
            draw_input(out)            # zero extra cost: an empty or silent chain is a passthrough
            return
        a = fc.pool.acquire()
        held = [a]
        try:
            draw_input(a)
            cur, b = a, None
            for i, s in enumerate(active):
                if i == len(active) - 1:
                    dst = out
                else:
                    if b is None:
                        b = fc.pool.acquire()
                        held.append(b)
                    dst = b if cur is a else a
                s.render(fc, cur, dst)
                cur = dst
        finally:
            for buf in held:           # one stream: a buffer reused later this frame is stream-ordered
                fc.pool.release(buf)

    def retire_done(self, fc: FrameCtx) -> list[str]:
        done = [s for s in self.slots if s.done]
        for s in done:
            self.slots.remove(s)
            fc.retire(s.effect)
        return [s.slot_id for s in done]

    def close(self, fc: FrameCtx) -> None:
        slots, self.slots = self.slots, []
        for s in slots:
            fc.retire(s.effect)

    def close_now(self) -> None:
        """Close every slot's effect at once (the engine's closer thread, or a never-shown scene)."""
        slots, self.slots = self.slots, []
        for s in slots:
            try:
                s.effect.close()
            except Exception:
                pass


def close_with_effects(obj: Any) -> None:
    """Close a retired object and, for a scene source that carries a chain, every effect in it."""
    try:
        obj.close()
    finally:
        chain = getattr(obj, "fx_chain", None)
        if isinstance(chain, EffectChain):
            chain.close_now()


class EffectLayer(Layer):
    """A scene with its attached effects (slot levels fixed at 1, no envelope): transitions blend the processed
    scenes, and the chain is closed with the scene."""

    def __init__(self, child: Layer, chain: EffectChain):
        self.child = child
        self.chain = chain

    def render(self, fc: FrameCtx, out: Any) -> None:
        self.chain.render(fc, lambda buf: self.child.render(fc, buf), out)

    def collapse(self, fc: FrameCtx) -> Layer:
        self.child = self.child.collapse(fc)
        return self

    def close(self, fc: FrameCtx) -> None:
        self.chain.close(fc)
        self.child.close(fc)

    def walk(self) -> Iterator[Layer]:
        yield self
        yield from self.child.walk()

    def describe(self) -> Any:
        return {"effects": [s.describe() for s in self.chain.slots], "child": self.child.describe()}


class MasterBus:
    """The master FX bus over the whole programme (after the transitions)."""

    def __init__(self, runtime: Any = None):
        self.runtime = runtime
        self.chain = EffectChain()
        self.level = 0.0
        self.target = 0.0
        self.fade_s = BUS_OPEN_S
        self.sealed = False
        self.master_values = {"amount": 1.0, "pulse": 1.0}
        self.master_bindings: dict = {}
        self.master_mod: Any = None
        self.master_now = dict(self.master_values)
        self.gain_now = 0.0
        self.last_n: Optional[int] = None

    def open(self, fade_s: float = BUS_OPEN_S) -> None:
        if self.sealed:
            return
        self.target = 1.0
        self.fade_s = max(0.0, fade_s)

    def close(self, fade_s: float) -> None:
        self.target = 0.0
        self.fade_s = max(0.0, fade_s)

    def seal(self) -> None:
        self.level = 0.0
        self.target = 0.0
        self.sealed = True

    @property
    def is_open(self) -> bool:
        return not self.sealed and self.target > 0.0

    def set_master(self, master: dict) -> None:
        from .modulation import Modulation, split_bindings

        plain, bindings = split_bindings(MASTER_PROPS, master)
        self.master_values = {**self.master_values, **{k: float(v) for k, v in plain.items()}}
        for k in master:
            if k not in bindings:
                self.master_bindings.pop(k, None)
        self.master_bindings.update(bindings)
        self.master_mod = Modulation(self.runtime, MASTER_PROPS, self.master_bindings) \
            if self.master_bindings and self.runtime is not None else None

    def apply(self, cmd: FxUpdate, fc: Optional[FrameCtx] = None) -> None:
        self.chain.apply(cmd, fc)
        if cmd.master is not None:
            self.set_master(cmd.master)

    def _step(self, fc: FrameCtx) -> None:
        dt = 0.0 if self.last_n is None else max(0, fc.n - self.last_n) / fc.fps
        self.last_n = fc.n
        if self.level == self.target:
            return
        if self.fade_s <= 0.0:
            self.level = self.target
        elif self.level < self.target:
            self.level = min(self.target, self.level + dt / self.fade_s)
        else:
            self.level = max(self.target, self.level - dt / self.fade_s)

    def render(self, fc: FrameCtx, draw_root: Callable[[Any], None], out: Any) -> None:
        self._step(fc)
        m = dict(self.master_values)
        if self.master_mod is not None:
            m = self.master_mod.apply(m)
        self.master_now = m
        gain = 0.0 if self.sealed else smoothstep(self.level) * float(m.get("amount", 1.0))
        self.gain_now = gain
        self.chain.render(fc, draw_root, out, gain=gain, pulse_gain=float(m.get("pulse", 1.0)))

    def idle(self) -> bool:
        return self.sealed or not self.chain.slots or (self.level <= 0.0 and self.target <= 0.0)
