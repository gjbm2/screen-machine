"""The room ensemble, the renderer's half (docs/ENSEMBLE.md §8; owner, 22 Oct: "more of a conversation between room
elements e.g. something might happen on south (left) and then on north (right) and/or echo between").

show_service's director publishes ``room.direction.ensemble`` (contract.EnsembleData): the form the room plays, and
each player's part (contract.EnsemblePart).  A screen finds itself as ``pid = ensemble["screens"][<its screen>]``;
its part is ``ensemble["players"][pid]``, or ``ensemble["next"]["players"][pid]`` from ``next.at_ms`` on, at its own
photon time.  Every time here is photon time on show's clock: host CLOCK_MONOTONIC ms, the renderer's
``time.monotonic()``; a screen's photon time is its render time + its render->photon lead (Sampler.lead).

What a part moves on this screen (the levers; all neutral without an ensemble, which is today's code path):
  depth        x the binding depth (direction.intensity, the music depth; never brightness)   modulation.Modulation
  pulse        x the bindings' beat (the beat ladder's pulse)                                 SessionRuntime
  motion       x the scene clock (the scene's pace)                                           Modulation.scale_dt
  fx_density   x direction.fx.density for its own FX director; a screen mirroring another's   sessions.Session
               effects takes only the first round(n x fx_density) of them (fx_trim)
  energy_bias  + its direction energy, for its own bindings and FX weights (the leader's picks stay the room's)
  pal_shift    palette_role -> where that role's colour sits in the cosine palette: the palette is read that far
               along (t + shift), so the screen leans to the role's colour            Modulation.palette_rows
  register     [lo, hi] of 0 (A0) .. 1 (C8): note drawers draw notes inside it at full, the others at
               ensemble.params.outside (Sound.register, note_weights); notes are never hidden
The numbers come final in ``part.scale`` (show.yaml ensemble.roles); nothing here invents a value.  A change glides
over ``ensemble.glide_ms`` from ``next.at_ms`` (smoothstep, as show's Ensemble.scale); the register switches at
next.at_ms.  ``LeverGlide`` keeps the glide across show folding ``next`` in (the target doesn't move then).

The segment (one picture across the screens; owner, 22 Oct): ``segment`` gives this screen's ``(k, n)``, its place
among the ensemble's screens by their players' x (0 the leftmost).  Not a lever: it never glides and needs no part.
The session puts it in the runtime's ``extra["segment"]`` each tick (synced to a render worker as it is); the runtime
reads it each frame (``clean_segment``, SessionRuntime.segment) and the engine sets Sound.segment from it.

Staggered scene changes (§8.4): ``stagger_s`` is this screen's part's stagger in seconds (stagger_beats x the sure
clock's beat, else its stagger_s); rooms.stagger_s wraps it for Session._submit.

Pure helpers, no I/O and no locks: the loop thread builds ``wire`` for the runtime's ``extra`` (synced to a render
worker as it is), and each thread runs its own LeverGlide.

The same picture a round later (§8.7, §8.9; canon, and echo with clock_offset): ``clock_beats`` is the part's
clock_offset_beats and ``clock_s`` its seconds without a sure grid (clock_offset_beats x the part's own seconds per
beat, stagger_s / stagger_beats; 0 when the part has no stagger to say it).  They glide like the other levers.  Each
frame the runtime turns them into ``clock_offset_s`` (x the renderer's sure beat, which the wire carries as
``beat_s``, else ``clock_s``): the beat-bound signals (beat_phase, bar_phase, the beat ladder's pulse, an effect's
beat clock) are read at photon time - that offset, and a room scene's clock runs that far behind the room's
(layers.SourceLayer).  A scene clock follows a change of the offset only while the clock keeps running forward at a
sane rate (CLOCK_SLOW, CLOCK_FAST: ``clock_glide_ok``); a steeper change waits for the next cut.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

LINE_PREFIX = "Playing together: "            # show_service/ensemble.py's page words (the card's line)
HUD_PREFIX = "Together: "
PALETTE_ROLES = ("base", "accent", "highlight")   # show_service/contract.PALETTE_ROLES, in its order
REGISTER_A0, REGISTER_SPAN = 21, 87           # MIDI A0 .. C8 -> register 0 .. 1 (show_service/ensemble.py's)
OUTSIDE_DEFAULT = 0.25                        # register_split's own default ``outside`` (show's FORM_PARAMS), for a
                                              # register that arrives in ``next`` (which carries no params)
SCALE_LEVERS = ("depth", "pulse", "motion", "fx_density")
CLOCK_LEVERS = ("clock_beats", "clock_s")     # canon's clock offset, in beats and in seconds without a sure grid
NEUTRAL: dict = {"depth": 1.0, "pulse": 1.0, "motion": 1.0, "fx_density": 1.0, "energy_bias": 0.0,
                 "pal_shift": 0.0, "register": None, "clock_beats": 0.0, "clock_s": 0.0}
# A room scene's clock follows a change of its clock offset while the offset grows at most CLOCK_SLOW s per s (the
# scene keeps at least a quarter of its pace) and shrinks at most CLOCK_FAST s per s (at most 4x); a steeper change
# (or one with no glide) waits for the next cut.  Starting points, chosen on paper.
CLOCK_SLOW = 0.75
CLOCK_FAST = 3.0
GLIDE_PEAK = 1.5                              # smoothstep's steepest slope, x the mean (glide's from -> to over g)


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _smooth(x: float) -> float:
    x = 0.0 if x < 0.0 else 1.0 if x > 1.0 else x
    return x * x * (3.0 - 2.0 * x)


def _wrap(d: float) -> float:
    """A palette offset's shortest way round the cycle (-0.5 .. 0.5)."""
    return (d + 0.5) % 1.0 - 0.5


# -- this screen's part --------------------------------------------------------------------------------------------
def player_id(ens: Optional[Mapping], screen: str) -> Optional[str]:
    if not isinstance(ens, Mapping):
        return None
    scr = ens.get("screens")
    pid = scr.get(screen) if isinstance(scr, Mapping) else None
    return pid if isinstance(pid, str) and pid else None


def _next_at(ens: Mapping) -> tuple[Optional[Mapping], Optional[float]]:
    nx = ens.get("next")
    if not isinstance(nx, Mapping):
        return None, None
    at = _num(nx.get("at_ms"), math.nan)
    return (nx, at) if math.isfinite(at) else (None, None)


def my_part(ens: Optional[Mapping], screen: str, at_photon_ms: Optional[float]) -> Optional[Mapping]:
    """This screen's part at photon time ``at_photon_ms`` (show's clock, ms): ``next``'s once the time reaches its
    at_ms (and next names this player), else the current one.  None: no ensemble, or this screen isn't a player."""
    pid = player_id(ens, screen)
    if pid is None:
        return None
    nx, at = _next_at(ens)
    if nx is not None and at_photon_ms is not None and at_photon_ms >= at:
        p = (nx.get("players") or {}).get(pid)
        if isinstance(p, Mapping):
            return p
    p = (ens.get("players") or {}).get(pid)
    return p if isinstance(p, Mapping) else None


def stagger_s(ens: Optional[Mapping], screen: str, beat_s: Optional[float], at_photon_ms: Optional[float]) -> float:
    """How long after the room's moment a scene change lands on this screen (s): its part's stagger_beats x
    ``beat_s`` while the renderer's clock is sure (``beat_s`` given), else its stagger_s; 0 without a part."""
    p = my_part(ens, screen, at_photon_ms)
    if p is None:
        return 0.0
    b = _num(beat_s, 0.0)
    v = _num(p.get("stagger_beats"), 0.0) * b if b > 0.0 else _num(p.get("stagger_s"), 0.0)
    return v if v > 0.0 else 0.0


def lead_screens(ens: Optional[Mapping], at_photon_ms: Optional[float]) -> list[str]:
    """The screens whose part has role ``lead`` at that photon time (in the ensemble's screens order)."""
    if not isinstance(ens, Mapping) or not isinstance(ens.get("screens"), Mapping):
        return []
    return [scr for scr in ens["screens"] if (my_part(ens, scr, at_photon_ms) or {}).get("role") == "lead"]


def fx_trim(want: list, density: Optional[float]) -> list:
    """A mirroring screen's share of its source's auto effects (§8.3): the first round(n x fx_density) of them, in the
    source's bus order (half up: one effect at 0.5 is mirrored).  density None or >= 1: all of them (the same list)."""
    if density is None or density >= 1.0 or not want:
        return want
    k = int(math.floor(len(want) * max(0.0, density) + 0.5))
    return list(want[:max(0, min(len(want), k))])


# -- the levers ----------------------------------------------------------------------------------------------------
def palette_shift(role: Any) -> float:
    """palette_role -> how far along the cosine palette (0..1) this screen reads it: the roles sit a third of the
    cycle apart in PALETTE_ROLES' order (base where the palette starts).  An unknown role or None: 0."""
    return PALETTE_ROLES.index(role) / len(PALETTE_ROLES) if role in PALETTE_ROLES else 0.0


def levers(part: Optional[Mapping], params: Optional[Mapping] = None) -> dict:
    """A part's levers (NEUTRAL's keys).  A missing or bad number is neutral, never an invented one."""
    if not isinstance(part, Mapping):
        return NEUTRAL
    sc = part.get("scale") if isinstance(part.get("scale"), Mapping) else {}
    out = {k: max(0.0, _num(sc.get(k), 1.0)) for k in SCALE_LEVERS}
    out["energy_bias"] = max(-1.0, min(1.0, _num(part.get("energy_bias"), 0.0)))
    out["pal_shift"] = palette_shift(part.get("palette_role"))
    cb = max(0.0, _num(part.get("clock_offset_beats"), 0.0))
    sb, ss = _num(part.get("stagger_beats"), 0.0), _num(part.get("stagger_s"), 0.0)
    out["clock_beats"] = cb
    out["clock_s"] = cb * ss / sb if cb > 0.0 and sb > 0.0 and ss > 0.0 else 0.0
    reg = part.get("register")
    out["register"] = None
    if isinstance(reg, (list, tuple)) and len(reg) == 2:
        lo, hi = _num(reg[0], math.nan), _num(reg[1], math.nan)
        if math.isfinite(lo) and math.isfinite(hi) and lo <= hi:
            pr = params if isinstance(params, Mapping) else {}
            outside = min(1.0, max(0.0, _num(pr.get("outside"), OUTSIDE_DEFAULT)))
            out["register"] = (lo, hi, outside)
    return NEUTRAL if out == NEUTRAL else out


def wire(ens: Optional[Mapping], screen: str, beat_s: Optional[float] = None) -> Optional[dict]:
    """This screen's levers as the runtime's ``extra["ensemble"]`` (loop thread; plain values, synced to a render
    worker as they are): {cur, next, at_ms, glide_ms, beat_s}; ``beat_s``: the renderer's beat while its clock is
    sure (SessionManager.music_grid), else None (the clock offset is then each part's clock_s).  None: no part now or
    next (unison: today's path)."""
    pid = player_id(ens, screen)
    if pid is None:
        return None
    params = ens.get("params") if isinstance(ens.get("params"), Mapping) else {}
    cur = levers((ens.get("players") or {}).get(pid), params)
    nx, at = _next_at(ens)
    nxt = None
    if nx is not None:
        p = (nx.get("players") or {}).get(pid)
        if isinstance(p, Mapping):
            nxt = levers(p, params)
    if cur is NEUTRAL and nxt in (None, NEUTRAL):
        return None
    b = _num(beat_s, 0.0)
    return {"cur": cur, "next": nxt, "at_ms": at if nxt is not None else None,
            "glide_ms": max(0.0, _num(ens.get("glide_ms"), 0.0)), "beat_s": b if b > 0.0 else None}


def clean(d: Any) -> dict:
    """A levers dict as received (a render worker's copy of ``wire``): every key present and in range; NEUTRAL for
    anything else, so a bad message can never break a frame."""
    if not isinstance(d, Mapping):
        return NEUTRAL
    out = {k: max(0.0, _num(d.get(k), 1.0)) for k in SCALE_LEVERS}
    out["energy_bias"] = max(-1.0, min(1.0, _num(d.get("energy_bias"), 0.0)))
    out["pal_shift"] = _num(d.get("pal_shift"), 0.0) % 1.0
    for k in CLOCK_LEVERS:                    # (a control that predates them sends none: 0, unison)
        out[k] = max(0.0, _num(d.get(k), 0.0))
    reg = d.get("register")
    out["register"] = None
    if isinstance(reg, (list, tuple)) and len(reg) == 3:
        lo, hi, o = (_num(x, math.nan) for x in reg)
        if math.isfinite(lo) and math.isfinite(hi) and lo <= hi and 0.0 <= o <= 1.0:
            out["register"] = (lo, hi, o)
    return NEUTRAL if out == NEUTRAL else out


class LeverGlide:
    """This screen's levers at photon time, glided (one per thread that reads them: the render thread's runtime, the
    loop's session).  A new target (``next`` reaching its at_ms, a new ensemble, none any more) glides from where the
    levers are, over the ensemble's glide_ms, from next.at_ms (or now); show folding ``next`` in leaves the target
    where it was, so the glide runs on.  ``at`` returns None while the levers rest at NEUTRAL (today's path)."""

    def __init__(self) -> None:
        self.frm: dict = NEUTRAL
        self.to: dict = NEUTRAL
        self.t0 = 0.0
        self.g = 0.0
        self.value: Optional[dict] = None
        self._raw: Any = None                 # the last target as received (compared before it is cleaned)

    def _eval(self, t_ms: float) -> dict:
        if self.to is self.frm or self.g <= 0.0 or t_ms >= self.t0 + self.g:
            return self.to
        s = _smooth((t_ms - self.t0) / self.g)
        f, to = self.frm, self.to
        out = {k: f[k] + (to[k] - f[k]) * s for k in SCALE_LEVERS + CLOCK_LEVERS}
        out["energy_bias"] = f["energy_bias"] + (to["energy_bias"] - f["energy_bias"]) * s
        out["pal_shift"] = (f["pal_shift"] + _wrap(to["pal_shift"] - f["pal_shift"]) * s) % 1.0
        out["register"] = to["register"]
        return out

    def at(self, w: Optional[Mapping], t_ms: float) -> Optional[dict]:
        raw, t0, g = None, t_ms, self.g
        if isinstance(w, Mapping):
            g = max(0.0, _num(w.get("glide_ms"), 0.0))
            raw = w.get("cur")
            nxt, at = w.get("next"), _num(w.get("at_ms"), math.nan)
            if isinstance(nxt, Mapping) and math.isfinite(at) and t_ms >= at:
                raw, t0 = nxt, min(at, t_ms)
        if raw != self._raw:
            self._raw = dict(raw) if isinstance(raw, Mapping) else None
            to = clean(raw)
            if to != self.to:
                self.frm = dict(self._eval(t_ms))
                self.to, self.t0, self.g = to, t0, g
        v = self._eval(t_ms)
        self.value = None if v is NEUTRAL or v == NEUTRAL else v
        return self.value


# -- the clock offset (canon, echo with clock_offset; §8.7, §8.9) ---------------------------------------------------
def clock_offset_s(lv: Optional[Mapping], beat_s: Optional[float]) -> float:
    """How far behind the room this screen's beat-bound motion runs (s): clock_beats x ``beat_s`` while the clock is
    sure, else clock_s; 0 without levers (unison)."""
    if not lv:
        return 0.0
    b = _num(beat_s, 0.0)
    v = lv.get("clock_beats", 0.0) * b if b > 0.0 else lv.get("clock_s", 0.0)
    return v if v > 0.0 else 0.0


def clock_glide_ok(g: "LeverGlide", beat_s: Optional[float]) -> bool:
    """May a running scene clock follow the glide ``g`` is on (its clock offset from ``g.frm`` to ``g.to`` over
    ``g.g`` ms)?  Yes while the offset doesn't move, or it moves over a glide no steeper than CLOCK_SLOW (growing:
    the scene slows, never runs backwards) or CLOCK_FAST (shrinking: it hurries); no glide is a jump: no."""
    d = clock_offset_s(g.to, beat_s) - clock_offset_s(g.frm, beat_s)
    if abs(d) < 1e-6:
        return True
    if g.g <= 0.0:
        return False
    slope = GLIDE_PEAK * d / (g.g / 1000.0)
    return -CLOCK_FAST <= slope <= CLOCK_SLOW


def apply_signals(sig: dict, lv: Optional[Mapping]) -> None:
    """The bindings' signals on this screen (in place, after the beat ladder and the pivots): the beat x pulse, the
    energy + energy_bias (clamped 0..1).  n/a (None) stays n/a."""
    if not lv:
        return
    p = lv["pulse"]
    if p != 1.0 and sig.get("beat") is not None:
        sig["beat"] = sig["beat"] * p
    b = lv["energy_bias"]
    if b:
        for k in ("energy", "energy_slow"):
            v = sig.get(k)
            if v is not None:
                sig[k] = min(1.0, max(0.0, v + b))


def direction(eff: dict, lv: Optional[Mapping]) -> dict:
    """This screen's direction for its own FX director: energy + energy_bias, fx.density x fx_density.  The same
    object without levers (the leader's picks stay the room's: the director keeps ``eff`` itself)."""
    if not lv or (lv["fx_density"] == 1.0 and not lv["energy_bias"]):
        return eff
    out = dict(eff)
    if lv["energy_bias"] and isinstance(eff.get("energy"), (int, float)):
        out["energy"] = round(min(1.0, max(0.0, eff["energy"] + lv["energy_bias"])), 3)
    fx = eff.get("fx")
    if lv["fx_density"] != 1.0 and isinstance(fx, Mapping) and isinstance(fx.get("density"), (int, float)):
        out["fx"] = {**fx, "density": round(min(1.0, max(0.0, fx["density"] * lv["fx_density"])), 3)}
    return out


# -- the segment (one picture across the screens) -------------------------------------------------------------------
def segment(ens: Optional[Mapping], screen: str) -> Optional[tuple[int, int]]:
    """This screen's place in one picture spanning the ensemble's screens (owner, 22 Oct: a score scene's one tape,
    notes entering on the right-hand screen and flowing into the left): ``(k, n)``, k its index among the ensemble's
    screens ordered by their players' ``x`` (0 the leftmost; a tie by player id, show's own order), n how many
    screens.  A screen whose player has no ``x`` is left out (never placed at a made-up x).  None: no ensemble, this
    screen isn't one of its screens, or it is the only one."""
    if not isinstance(ens, Mapping):
        return None
    scr, players = ens.get("screens"), ens.get("players")
    if not isinstance(scr, Mapping) or not isinstance(players, Mapping):
        return None
    placed = []
    for name, pid in scr.items():
        p = players.get(pid) if isinstance(pid, str) else None
        x = _num(p.get("x"), math.nan) if isinstance(p, Mapping) else math.nan
        if isinstance(name, str) and math.isfinite(x):
            placed.append((x, pid, name))
    names = [name for _, _, name in sorted(placed)]
    if len(names) < 2 or screen not in names:
        return None
    return names.index(screen), len(names)


def clean_segment(v: Any) -> Optional[tuple[int, int]]:
    """A segment as received (the runtime's ``extra["segment"]``, synced to a render worker as it is): ``(k, n)`` with
    n >= 2 and 0 <= k < n, else None, so a bad message can never break a frame."""
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    k, n = v
    if isinstance(k, bool) or isinstance(n, bool) or not isinstance(k, int) or not isinstance(n, int):
        return None
    return (k, n) if n >= 2 and 0 <= k < n else None


# -- the register (note drawers) ------------------------------------------------------------------------------------
def register_of(pitch: Any) -> Any:
    """A MIDI pitch (or a numpy array of them) as register, 0 (A0) .. 1 (C8)."""
    import numpy as np
    return np.clip((np.asarray(pitch, np.float64) - REGISTER_A0) / REGISTER_SPAN, 0.0, 1.0)


def note_weights(register: Any, pitches: Any) -> Any:
    """How strongly to draw each pitch under this screen's register window ``(lo, hi, outside)`` (Sound.register):
    1 inside [lo, hi], ``outside`` elsewhere; None without a window (draw everything at full)."""
    if not register:
        return None
    import numpy as np
    lo, hi, outside = register
    r = register_of(pitches)
    return np.where((r >= lo) & (r <= hi), 1.0, float(outside))


# -- the details panel (hud.py) --------------------------------------------------------------------------------------
def hud_line(ens: Optional[Mapping], together: Optional[str] = None,
             at_photon_ms: Optional[float] = None) -> Optional[str]:
    """One line: "Together: echo, left to right · lead south-screen" (the card's words: room.why's look.together
    when it carries them, else the ensemble's ``line``).  None while neutral (no line)."""
    words = together if isinstance(together, str) and together else \
        (ens.get("line") if isinstance(ens, Mapping) else None)
    if not isinstance(words, str) or not words:
        return None
    if words.startswith(LINE_PREFIX):
        words = words[len(LINE_PREFIX):]
    leads = lead_screens(ens, at_photon_ms)
    return HUD_PREFIX + words + (" · lead " + ", ".join(leads) if leads else "")
