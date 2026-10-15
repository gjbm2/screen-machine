"""Bindings v2: per-item music mappings (owner, 15 Oct 2026).  A parameter follows a blend of show signals.

  {"at": "live" | "section" | "cue",
   "in": [{"sig": "energy", "w": 0.7}, {"sig": "drive", "w": 0.3, "inv": false, "floor": 0}],    1-4 inputs
   "op": "mean" | "max" | "mul",              mean (weighted), max (weighted), mul (product; weights ignored)
   "curve": "linear" | "ease" | "in" | "out",
   "map": {"around": [a, b]}                  base + a*span .. base + b*span   (a <= 0 <= b, |a|, |b| <= 0.35)
        | {"scale": [f0, f1]}                 base*f0 .. base*f1               (0.4 <= f0 <= 1 <= f1 <= 2)
        | {"range": [lo, hi], "rest"?: r}     absolute (v1-like: linear when there is no rest)
        | {"band": p}                         the preset's tags.energy band, padded by p (mappings.py resolves it)
   "kick": {"sig": "beat" | "section", "amt": 0.08, "by": "punch"},   live only: + amt*span*kick*(0.6 + 0.4 by)
   "steps": [v0, v1, ...],                    cue: enum / bool / int picked by the blend's bin (null = base)
   "quant": "beat",                           cue, duration_s: whole beats while the tempo is locked
   "ease_s": s | [up, down],                  live 0, section 4 by default
   "live_ok": bool, "on": bool, "fam" / "src" / "why": metadata}
or the colour form {"colour": "room", "mix": 0.5, "at"?: "live" | "cue"} on a palette param: the palette blends
toward Maestro's colour (modulation.py), by mix x liveness.

Pivot: with every input at its idle value a binding gives exactly its base (the preset's plain value, the eased
easer value), so no bus, a stale bus or a quiet room shows the authored look; the music moves the param around
it.  Modes: ``live`` every frame; ``section`` re-sampled when the session's epoch moves (a GOBO section change, a
new energy band: at most every 8 s; at once when the bus comes or goes), then eased; ``cue`` once, when the item
is instantiated (sessions.py samples it before setup, so params read only in setup take it too).

Depth (``direction.intensity``) scales every excursion around base: the operator's "how much the music moves
the picture".  Inputs that are n/a (None: tempo unlocked, no movement, unlit) drop out and the weights
renormalise; ``avail`` (0-1, eased by the runtime) fades an input in and out so nothing pops.
"""
from __future__ import annotations

import math
import re
import zlib
from typing import Any, Optional

from .inputs import BIND_SIGNALS, TRIGGERS, idle_value, signal_kind
from .plugins.base import ParamError

MODES = ("live", "section", "cue")
OPS = ("mean", "max", "mul")
CURVES = {"linear": lambda x: x, "ease": lambda x: x * x * (3.0 - 2.0 * x), "in": lambda x: x * x,
          "out": lambda x: 1.0 - (1.0 - x) * (1.0 - x)}
INPUT_SIGNALS = tuple(s for s in BIND_SIGNALS if s != "beat_phase") + ("gobo",)
KEYS = {"at", "in", "op", "curve", "map", "kick", "steps", "quant", "ease_s", "live_ok", "on", "fam", "src", "why"}
COLOUR_KEYS = {"colour", "mix", "at", "ease_s", "on", "fam", "src", "why"}
MAX_INPUTS = 4
KICK_SIGNALS = ("beat", "section", "note_on", "chord_change")
SECTION_MIN_S = 8.0
SECTION_EASE_S = 4.0
NEVER = {"speed", "intensity", "seed", "render_scale", "random_seed", "start_time"}
NEVER_RX = re.compile(r"(^|_)(iterations?|octaves|quality|max_steps|raymarch_steps|samples|ao_samples|taps)($|_)")
FLASH_RX = re.compile(r"flash|strobe|flicker|blink")
RATE_RX = re.compile(r"speed|rate|spin|velocity|_vel$|omega|per_s")   # rotation_speed is a rate, not an angle
POSITION_RX = re.compile(r"(^|_)(pan|tilt|x|y|z|pos|position|centre|center|origin|offset|angle|rotation|"
                         r"direction|heading)($|_)")


class MappingError(ParamError):
    """400 invalid_mapping with a list of problems [{path, code, detail}]."""

    def __init__(self, problems: list[dict]):
        super().__init__("invalid_mapping", "; ".join(f"{p['path']}: {p['code']} ({p['detail']})" for p in problems))
        self.problems = problems


def is_v2(b: Any) -> bool:
    return isinstance(b, dict) and ("in" in b or "colour" in b or "steps" in b)


def not_mappable(name: str, prop: dict) -> Optional[str]:
    if name in NEVER or prop.get("x-tier") == "quality" or NEVER_RX.search(name):
        return f"{name} is never mapped (speed, intensity, seed, render_scale, quality, iteration counts)"
    return None


def is_palette_param(name: str, prop: dict) -> bool:
    return bool(prop.get("x-palette")) or name == "palette" or (name.endswith("palette") and "enum" in prop)


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_v2(name: str, prop: dict, spec: dict, *, kind: str = "scene") -> dict:
    """Validate a v2 binding (or the colour form) for one param; -> a normalised copy.  Raises MappingError."""
    problems: list[dict] = []

    def bad(code: str, detail: str) -> None:
        problems.append({"path": name, "code": code, "detail": detail})

    if not isinstance(spec, dict):
        raise MappingError([{"path": name, "code": "bad_map", "detail": "a binding must be an object"}])
    why = not_mappable(name, prop)
    if why:
        raise MappingError([{"path": name, "code": "not_mappable", "detail": why}])
    meta = {k: spec[k] for k in ("fam", "src", "why") if isinstance(spec.get(k), str)}
    on = spec.get("on", True)
    if not isinstance(on, bool):
        bad("unknown_key", "on must be true or false")
    # -- the colour form ----------------------------------------------------------------------------------
    if "colour" in spec:
        extra = set(spec) - COLOUR_KEYS
        if extra:
            bad("unknown_key", f"unknown keys {sorted(extra)}")
        if spec["colour"] != "room":
            bad("bad_map", "colour must be \"room\" (Maestro's colour)")
        if not is_palette_param(name, prop):
            bad("bad_mode", "the colour form goes on a palette param")
        mix = spec.get("mix", 0.5)
        if not _num(mix) or not 0.0 <= mix <= 1.0:
            bad("bad_weight", "mix must be 0-1")
        at = spec.get("at", "live")
        if at not in ("live", "cue"):
            bad("bad_mode", "the colour form is live or cue")
        if problems:
            raise MappingError(problems)
        return {"colour": "room", "mix": float(mix), "at": at, "ease_s": _ease(spec.get("ease_s", 3.0), bad),
                "on": on, **meta}
    # -- a blend ------------------------------------------------------------------------------------------
    extra = set(spec) - KEYS
    if extra:
        bad("unknown_key", f"unknown keys {sorted(extra)} (v2: {sorted(KEYS)})")
    at = spec.get("at", "live")
    if at not in MODES:
        bad("bad_mode", f"at must be one of {list(MODES)}")
    ins = spec.get("in")
    terms: list[dict] = []
    if not isinstance(ins, list) or not ins:
        bad("bad_map", "in must be a list of 1-4 inputs")
        ins = []
    elif len(ins) > MAX_INPUTS:
        bad("too_many_inputs", f"at most {MAX_INPUTS} inputs")
    for i, t in enumerate(ins):
        if not isinstance(t, dict) or set(t) - {"sig", "w", "inv", "floor"}:
            bad("unknown_key", f"in[{i}] must be {{sig, w?, inv?, floor?}}")
            continue
        sig = t.get("sig")
        if sig not in INPUT_SIGNALS:
            bad("unknown_signal", f"in[{i}]: unknown signal {sig!r}")
            continue
        w = t.get("w", 1.0)
        if not _num(w) or not 0.0 < w <= 10.0:
            bad("bad_weight", f"in[{i}].w must be in (0, 10]")
            w = 1.0
        fl = t.get("floor", 0.0)
        if not _num(fl) or not 0.0 <= fl <= 1.0:
            bad("bad_weight", f"in[{i}].floor must be 0-1")
            fl = 0.0
        if sig == "gobo" and at == "live":
            bad("bad_mode", "gobo is a section input: section or cue only")
        terms.append({"sig": sig, "w": float(w), **({"inv": True} if t.get("inv") else {}),
                      **({"floor": float(fl)} if fl else {})})
    op = spec.get("op", "mean")
    if op not in OPS:
        bad("bad_map", f"op must be one of {list(OPS)}")
    kinds = {"trigger" if t["sig"] in TRIGGERS else "level" for t in terms}
    if op in ("mean", "max") and len(kinds) > 1:
        bad("mixed_kinds", "a trigger cannot be averaged with levels: use mul, or a kick")
    curve = spec.get("curve", "linear")
    if curve not in CURVES:
        bad("bad_map", f"curve must be one of {list(CURVES)}")
    typ = prop.get("type")
    tier = prop.get("x-tier")
    bindable = bool(prop.get("x-bindable"))
    out: dict = {"at": at, "in": terms, "op": op, "curve": curve, "on": on}
    # -- steps (enum, bool, int at cue) or a numeric map ----------------------------------------------------
    if "steps" in spec:
        steps = spec["steps"]
        if at != "cue":
            bad("bad_mode", "steps are cue only")
        if not isinstance(steps, list) or not 2 <= len(steps) <= 16:
            bad("bad_steps", "steps must list 2-16 values")
        else:
            for v in steps:
                if v is None:
                    continue
                if "enum" in prop and v not in prop["enum"]:
                    bad("bad_steps", f"{v!r} is not one of {prop['enum']}")
                elif typ == "boolean" and not isinstance(v, bool):
                    bad("bad_steps", f"{v!r} is not a boolean")
                elif typ == "integer" and not (isinstance(v, int) and not isinstance(v, bool)):
                    bad("bad_steps", f"{v!r} is not an integer")
                elif typ == "number" and not _num(v):
                    bad("bad_steps", f"{v!r} is not a number")
            out["steps"] = list(steps)
        if "map" in spec:
            bad("bad_map", "steps replace map")
    else:
        if typ not in ("number", "integer") or "enum" in prop:
            bad("bad_mode", f"a {typ or 'non-numeric'} param needs steps (cue)")
        m = spec.get("map")
        if not isinstance(m, dict) or len(set(m) - {"rest"}) != 1:
            bad("bad_map", "map must be exactly one of around / scale / range / band")
        else:
            out["map"] = _map(m, prop, bad)
    # -- modes allowed for this param (the tier contract) ---------------------------------------------------
    ease = spec.get("ease_s")
    out["ease_s"] = _ease(SECTION_EASE_S if ease is None and at == "section" else (ease or 0.0), bad)
    up = out["ease_s"] if _num(out["ease_s"]) else min(out["ease_s"])
    live_ok = bool(spec.get("live_ok"))
    if live_ok:
        out["live_ok"] = True
    if kind == "transition" and at != "cue":
        bad("bad_mode", "transitions are cue only")
    elif at == "live":
        if typ == "integer" and not bindable:
            bad("bad_mode", "an integer that is not x-bindable changes at cue only")
        elif tier == "structural":
            bad("bad_mode", "structural params change at cue only")
        elif tier == "regional" and not bindable:
            if not live_ok:
                bad("live_needs_ok", "live on a regional param needs live_ok (the plug-in reads it every frame)")
            elif up < 2.0:
                bad("smooth_too_short", "live on a regional param needs ease_s >= 2")
    elif at == "section":
        if typ == "integer" or tier == "structural":
            bad("bad_mode", "integers and structural params change at cue only")
    # -- kick -------------------------------------------------------------------------------------------------
    k = spec.get("kick")
    if k is not None:
        if not isinstance(k, dict) or set(k) - {"sig", "amt", "by"}:
            bad("bad_kick", "kick must be {sig, amt, by?}")
        elif at != "live":
            bad("bad_kick", "kicks are live only")
        elif k.get("sig") not in KICK_SIGNALS:
            bad("bad_kick", f"kick.sig must be one of {list(KICK_SIGNALS)}")
        elif not _num(k.get("amt")) or not 0.0 < k["amt"] <= 0.3:
            bad("bad_kick", "kick.amt must be in (0, 0.3]")
        elif k.get("by") is not None and (k["by"] not in INPUT_SIGNALS or k["by"] in TRIGGERS or k["by"] == "gobo"):
            bad("bad_kick", "kick.by must be a level signal")
        elif POSITION_RX.search(name) and not RATE_RX.search(name):
            bad("bad_kick", "no kicks on positions or angles")
        elif k["sig"] == "beat" and tier == "regional" and not bindable:
            bad("bad_kick", "no beat kick on a regional param")
        elif FLASH_RX.search(name) and k["amt"] > 0.15:
            bad("flash_rule", "a flash-class kick is at most 0.15")
        else:
            out["kick"] = {"sig": k["sig"], "amt": float(k["amt"]), **({"by": k["by"]} if k.get("by") else {})}
    if FLASH_RX.search(name) and isinstance(out.get("map"), dict):
        mm = out["map"]
        if ("around" in mm and mm["around"][1] > 0) or ("scale" in mm and mm["scale"][1] > 1.0):
            bad("flash_rule", "a flash-class param never goes above its base")
    q = spec.get("quant")
    if q is not None:
        if q != "beat" or at != "cue" or name != "duration_s":
            bad("bad_quant", "quant: beat is for a transition's duration_s at cue")
        else:
            out["quant"] = "beat"
    if problems:
        raise MappingError(problems)
    return {**out, **meta}


def _ease(v: Any, bad) -> Any:
    if _num(v) and 0.0 <= v <= 60.0:
        return float(v)
    if isinstance(v, list) and len(v) == 2 and all(_num(x) and 0.0 <= x <= 60.0 for x in v):
        return [float(v[0]), float(v[1])]
    bad("bad_map", "ease_s must be seconds 0-60, or [up, down]")
    return 0.0


def _map(m: dict, prop: dict, bad) -> dict:
    if "around" in m:
        a = m["around"]
        if not (isinstance(a, list) and len(a) == 2 and all(_num(x) for x in a) and -0.35 <= a[0] <= 0 <= a[1] <= 0.35):
            bad("bad_map", "around must be [a, b] with -0.35 <= a <= 0 <= b <= 0.35")
            return {"around": [0.0, 0.0]}
        return {"around": [float(a[0]), float(a[1])]}
    if "scale" in m:
        f = m["scale"]
        if not (isinstance(f, list) and len(f) == 2 and all(_num(x) for x in f) and 0.4 <= f[0] <= 1 <= f[1] <= 2.0):
            bad("bad_map", "scale must be [f0, f1] with 0.4 <= f0 <= 1 <= f1 <= 2")
            return {"scale": [1.0, 1.0]}
        return {"scale": [float(f[0]), float(f[1])]}
    if "band" in m:
        p = m["band"]
        if not _num(p) or not 0.0 <= p <= 0.2:
            bad("bad_map", "band must be 0-0.2")
            return {"band": 0.1}
        return {"band": float(p)}
    if "range" in m:
        r = m["range"]
        if not (isinstance(r, list) and len(r) == 2 and all(_num(x) for x in r)):
            bad("bad_map", "range must be [lo, hi]")
            return {"range": [0.0, 1.0]}
        lo, hi = (_clamp(prop, float(x)) for x in r)
        out: dict = {"range": [lo, hi]}
        if m.get("rest") is not None:
            if not _num(m["rest"]):
                bad("bad_map", "rest must be a number")
            else:
                out["rest"] = _clamp(prop, float(m["rest"]))
        return out
    bad("bad_map", "map must be one of around / scale / range / band")
    return {"around": [0.0, 0.0]}


def _clamp(prop: dict, v: float) -> float:
    lo, hi = prop.get("minimum"), prop.get("maximum")
    if lo is not None and v < lo:
        v = float(lo)
    if hi is not None and v > hi:
        v = float(hi)
    return v


def _h01(*parts: Any) -> float:
    return (zlib.crc32("/".join(map(str, parts)).encode()) & 0xFFFFFF) / float(1 << 24)


class Bound:
    """One compiled v2 binding (render thread state: the eased output and the section / cue hold)."""
    __slots__ = ("key", "mode", "terms", "op", "curve", "x0", "mapk", "m0", "m1", "rest", "lo", "hi", "span",
                 "is_int", "steps", "kick", "ease_up", "ease_down", "hk", "y", "target", "epoch", "t_sampled",
                 "quant", "last")

    def __init__(self, key: str, prop: dict, b: dict, item: str = ""):
        self.key = key
        self.mode = b["at"]
        tot = sum(t["w"] for t in b["in"]) or 1.0
        self.terms = tuple((t["sig"], t["w"] / tot, bool(t.get("inv")), float(t.get("floor", 0.0))) for t in b["in"])
        self.op = b.get("op", "mean")
        self.curve = CURVES[b.get("curve", "linear")]
        self.lo, self.hi = prop.get("minimum"), prop.get("maximum")
        self.span = (self.hi - self.lo) if _num(self.lo) and _num(self.hi) else 1.0
        self.is_int = prop.get("type") == "integer"
        self.steps = b.get("steps")
        m = b.get("map") or {}
        self.rest = None
        if "around" in m:
            self.mapk, (self.m0, self.m1) = "around", m["around"]
        elif "scale" in m:
            self.mapk, (self.m0, self.m1) = "scale", m["scale"]
        elif "range" in m:
            self.mapk, (self.m0, self.m1) = "range", m["range"]
            self.rest = m.get("rest")
        else:                                   # an unresolved band (no tags): a modest swing around base
            self.mapk, self.m0, self.m1 = "around", -(m.get("band") or 0.1) * 2.5, (m.get("band") or 0.1) * 2.5
        k = b.get("kick")
        self.kick = (k["sig"], k["amt"] * self.span, k.get("by")) if k else None
        e = b.get("ease_s", 0.0)
        self.ease_up, self.ease_down = (e, e) if _num(e) else (e[0], e[1])
        self.hk = _h01(item, key)
        self.quant = b.get("quant")
        self.x0 = self.x({}, None)              # the blend at idle: the pivot
        self.y: Optional[float] = None
        self.target: Any = None
        self.epoch: Any = None
        self.t_sampled = -1e9
        self.last: Any = None

    def x(self, sig: dict, avail: Optional[dict]) -> float:
        """The blend 0-1 (``sig`` {} = every input idle)."""
        acc, wsum, mx, mw = 0.0, 0.0, 0.0, 0.0
        for s, w, inv, fl in self.terms:
            if s == "gobo":
                g = sig.get("gobo") if sig else None
                v = 0.5 if g is None or not sig else (self.hk + 0.6180339887 * float(g)) % 1.0
            elif not sig:
                v = idle_value(s)
            else:
                v = sig.get(s)
                if v is None:                      # n/a: dropped, the rest renormalise
                    continue
                v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else float(v)
            if avail is not None and s in avail:
                w *= avail[s]
                if w <= 0.0:
                    continue
            if inv:
                v = 1.0 - v
            if fl:
                v = fl + (1.0 - fl) * v
            wsum += w
            if self.op == "max":
                if v * w > mx:
                    mx = v * w
                mw = max(mw, w)
            elif self.op == "mul":
                acc += math.log(max(v, 1e-4))
            else:
                acc += w * v
        if wsum <= 0.0:
            return self.x0 if sig else self.curve(0.5)
        if self.op == "max":
            return self.curve(mx / mw)
        if self.op == "mul":
            return self.curve(math.exp(acc))
        return self.curve(acc / wsum)

    def value(self, sig: dict, avail: Optional[dict], base: Any, depth: float) -> Any:
        """The mapped value now (unsmoothed); ``base`` is the param's plain value."""
        x = self.x(sig, avail)
        if self.steps is not None:
            n = len(self.steps)
            v = self.steps[min(n - 1, int(x * n))]
            return base if v is None else v
        if not _num(base):
            return base
        x0 = self.x0
        if self.mapk == "range":
            lo_end, hi_end = float(self.m0), float(self.m1)
            if self.rest is None:
                pivot = lo_end + (hi_end - lo_end) * x0
                v = lo_end + (hi_end - lo_end) * x
            else:
                pivot = float(self.rest)
                v = _piece(x, x0, lo_end, pivot, hi_end)
        else:
            pivot = float(base)
            if self.mapk == "around":
                lo_end, hi_end = pivot + self.m0 * self.span, pivot + self.m1 * self.span
            elif pivot != 0.0:
                lo_end, hi_end = pivot * self.m0, pivot * self.m1
            else:
                lo_end, hi_end = pivot - 0.15 * self.span, pivot + 0.15 * self.span
            v = _piece(x, x0, lo_end, pivot, hi_end)
        if self.kick is not None and sig:
            ks, amt, by = self.kick
            kv = sig.get(ks) or 0.0
            if kv > 0.0:
                f = 1.0 if by is None else 0.6 + 0.4 * min(1.0, max(0.0, sig.get(by) if sig.get(by) is not None else 0.5))
                v += amt * kv * f
        v = pivot + depth * (v - pivot)
        if not math.isfinite(v):
            v = pivot
        if _num(self.lo) and v < self.lo:
            v = float(self.lo)
        if _num(self.hi) and v > self.hi:
            v = float(self.hi)
        return v

    def step(self, sig: dict, avail: Optional[dict], base: Any, depth: float, epoch: Any, now: float,
             dt: float) -> Any:
        """The output this frame: the mode's hold and the ease (render thread)."""
        if self.mode == "cue":
            if self.target is None:
                self.target = self.value(sig, avail, base, depth)
            v = self.target
        elif self.mode == "section":
            music_ep, live_ep = epoch if isinstance(epoch, tuple) else (epoch, 0)
            due = self.target is None or (self.epoch is not None and live_ep != self.epoch[1]) or (
                self.epoch is not None and music_ep != self.epoch[0] and now - self.t_sampled >= SECTION_MIN_S)
            if due:
                self.target, self.epoch, self.t_sampled = self.value(sig, avail, base, depth), (music_ep, live_ep), now
            v = self._ease(self.target, dt)
        else:
            v = self._ease(self.value(sig, avail, base, depth), dt)
        if self.is_int and _num(v):
            v = int(round(v))
        self.last = v
        return v

    def _ease(self, v: Any, dt: float) -> Any:
        if not _num(v):
            return v
        if self.y is None or dt <= 0.0:
            self.y = v if self.y is None else self.y
            return self.y
        tau = self.ease_up if v > self.y else self.ease_down
        self.y = v if tau <= 0.0 else self.y + (v - self.y) * (1.0 - math.exp(-dt / tau))
        return self.y


def _piece(x: float, x0: float, lo_end: float, pivot: float, hi_end: float) -> float:
    """Piecewise linear through (0, lo_end), (x0, pivot), (1, hi_end)."""
    if x <= x0:
        return pivot if x0 <= 0.0 else lo_end + (pivot - lo_end) * (x / x0)
    return pivot if x0 >= 1.0 else pivot + (hi_end - pivot) * ((x - x0) / (1.0 - x0))


def sample_cue(props: dict, bindings: dict, params: dict, sig: dict, avail: Optional[dict], depth: float,
               item: str = "", period: Optional[float] = None) -> dict:
    """The cue-mode bindings' values now (sessions.py, before setup): {param: value}."""
    out: dict = {}
    for key, b in bindings.items():
        if not is_v2(b) or b.get("at") != "cue" or "colour" in b or not b.get("on", True) or key not in props:
            continue
        try:
            bd = Bound(key, props[key], b, item)
            v = bd.value(sig, avail, params.get(key, props[key].get("default")), depth)
            if bd.quant == "beat" and period and _num(v):
                v = max(1, round(v / period)) * period
            if bd.is_int and _num(v):
                v = int(round(v))
            out[key] = v
        except Exception:                       # a binding never takes a prepare down: the plain value stays
            continue
    return out


def maestro_rows(hue: float, spread: float, sat: float) -> list[list[float]]:
    """Maestro's colour as cosine palette rows [a, b, c, d] (each RGB): hue at t = 0.5, sweeping +-c/2 turns."""
    v = 0.85
    amp = 0.5 * max(0.0, min(1.0, sat)) * v
    a, b = v - amp, amp
    c = min(0.6, max(0.12, 2.0 * spread + 0.12))
    d = [o - hue - c / 2.0 for o in (0.0, 1.0 / 3.0, 2.0 / 3.0)]
    return [[a, a, a], [b, b, b], [c, c, c], d]


def describe(key: str, b: dict, value: Any = None) -> dict:
    """The /inputs and editor view of a binding (v1 or v2)."""
    out = {"param": key, **b}
    if value is not None:
        out["value"] = round(value, 5) if isinstance(value, float) else value
    return out


def signal_kinds() -> dict:
    return {s: signal_kind(s) for s in INPUT_SIGNALS if s != "gobo"} | {"gobo": "section"}
