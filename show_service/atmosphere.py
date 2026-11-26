"""atmosphere: the room's haze, held at a density by the hazers (owner, 26 Nov: "it's more of an ongoing effect").
Pure: no I/O, no clock of its own. director.py steps it on show-brain about once a second.

Haze is slow. Its DMX switches at once, but the air takes a minute or more to fill and many minutes to clear, so it is
never a look, a layer or an effect: the feel says how thick the room should be (vibes.yaml ``haze``: off | light |
medium | thick; a person's pick from the API wins until the feel changes), and this module works out each hazer's
output and fan from a model of the air.

**The model.** One number: the fluid in the room's air, in ml per m³ (``density``). Each second, the hazers add
output x their fluid rate (ml/min at 100 %, the type's) / the room's volume, and the room loses a share of it set by
its half-life (space.yaml ``room.haze_half_life_s``: how long the haze takes to halve once the hazer stops; time it
once by eye). The volume is the room's size (x . y . z); with no size, ``volume_m3`` in show.yaml.

**Off** sends nothing: the fixture's ``pin`` (Haze 0) and the Lights page's More hold it again.

**Holding.** To hold density D the hazers must put back what the room loses: D x V x ln 2 / half-life ml/s. Below
the target they run faster (the gap x ``gain``, so a fill runs near full), above it slower. Fan: ``fill_fan`` while
filling (below ``fill_until`` of the target), else ``hold_fan``. Off: 0 out (the room clears by itself).

**Bursts, not a long full output.** Many hazers hold their last DMX frame when the signal stops. So a fill never runs
above ``burst_above`` for longer than ``burst_s``: then it rests at the hold rate for ``rest_s``. If show_service
stops mid-fill, the machine is left at most at a burst's level, for the time the node keeps the frame.

**What it doesn't know.** There is no readback: the density is an estimate, and it assumes the machine is warm and
filled (the Z-350 heats for about 4.5 min after power-on and outputs nothing until then).

Config (show.yaml ``lights.atmosphere``, ``parse``): ``select`` (the hazers: a fixtures.yaml selector), ``types``
({fixture type: {output: {channel, range: [lo, hi]}, fan: {channel, range}, fluid_ml_min}}: how each type's channels
take 0..1; nothing here knows a channel name), ``levels`` ({word: ml per m³}), ``max`` (the thickest word allowed
here, e.g. for the smoke alarms), ``half_life_s`` and ``volume_m3`` (used when the room doesn't say), ``gain``,
``fill_until``, ``fill_fan``, ``hold_fan``, ``burst_above``, ``burst_s``, ``rest_s``.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

LEVELS: tuple[str, ...] = ("off", "light", "medium", "thick")     # contract.HAZE_LEVELS
FALLBACK: dict[str, Any] = {
    "select": "tag:haze",
    "types": {},
    # Starting points, from a 200 m³ room with a 150 s half-life and a 3 ml/min hazer: thick holds at ~37 % output,
    # light at ~9 %; a fill from clear air to medium takes about a minute at full output. Tune by eye.
    "levels": {"off": 0.0, "light": 0.005, "medium": 0.01, "thick": 0.02},
    "max": "thick",
    "half_life_s": 150.0,
    "volume_m3": 200.0,
    "gain": 3.0,
    "fill_until": 0.8,
    "fill_fan": 1.0,
    "hold_fan": 0.3,
    "burst_above": 0.6,
    "burst_s": 30.0,
    "rest_s": 15.0,
}
DT_MAX_S = 5.0                       # a step longer than this (a stalled tick) is taken as this


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def _off_key(m: Any) -> Any:
    """A mapping with YAML's bare ``off`` (read as False) as "off" again."""
    if isinstance(m, Mapping) and False in m:
        m = {("off" if k is False else k): v for k, v in m.items()}
    return m


def _chan(raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    raw = _off_key(raw)
    if not isinstance(raw, Mapping) or not isinstance(raw.get("channel"), str) or not raw["channel"]:
        probs.append(f"{where}: needs {{channel: <the type's channel name>, range: [lo, hi]}}: left out")
        return None
    rg = raw.get("range", [0, 255])
    if not (isinstance(rg, (list, tuple)) and len(rg) == 2 and all(_num(x) is not None and 0 <= x <= 255 for x in rg)
            and rg[0] <= rg[1]):
        probs.append(f"{where}.range: {rg!r} isn't [lo, hi] in 0..255: [0, 255]")
        rg = [0, 255]
    off = raw.get("off", 0)
    if _num(off) is None or not 0 <= off <= 255:
        probs.append(f"{where}.off: {off!r} isn't 0..255: 0")
        off = 0
    return {"channel": raw["channel"], "range": [int(rg[0]), int(rg[1])], "off": int(off)}


def parse(raw: Any, probs: list[str]) -> dict:
    """show.yaml ``lights.atmosphere`` -> the settings (FALLBACK's for what is missing or bad; problem lines)."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in FALLBACK.items()}
    if raw is None:
        return out
    if not isinstance(raw, Mapping):
        probs.append("lights.atmosphere: isn't a mapping: ignored")
        return out
    w = "lights.atmosphere"
    for k in raw:
        if k not in FALLBACK:
            probs.append(f"{w}.{k}: not an atmosphere key ({', '.join(FALLBACK)}): ignored")
    if isinstance(raw.get("select"), str) and raw["select"].strip():
        out["select"] = raw["select"].strip()
    types: dict[str, dict] = {}
    for tid, t in (raw.get("types") or {}).items() if isinstance(raw.get("types"), Mapping) else ():
        tw = f"{w}.types.{tid}"
        if not isinstance(t, Mapping):
            probs.append(f"{tw}: isn't a mapping: ignored")
            continue
        o = _chan(t.get("output"), f"{tw}.output", probs)
        if o is None:
            continue
        f = _chan(t.get("fan"), f"{tw}.fan", probs) if t.get("fan") is not None else None
        rate = _num(t.get("fluid_ml_min"))
        if rate is None or rate <= 0:
            probs.append(f"{tw}.fluid_ml_min: {t.get('fluid_ml_min')!r} isn't ml a minute at full output: 3")
            rate = 3.0
        types[str(tid)] = {"output": o, "fan": f, "fluid_ml_min": rate}
    out["types"] = types
    lv = _off_key(raw.get("levels"))
    if lv is not None:
        if isinstance(lv, Mapping) and all(k in LEVELS and _num(v) is not None and v >= 0 for k, v in lv.items()):
            out["levels"].update({k: float(v) for k, v in lv.items()})
        else:
            probs.append(f"{w}.levels: needs {{{' | '.join(LEVELS)}: ml per m³}}: the starting points")
    if raw.get("max") is not None:
        if raw["max"] in LEVELS or raw["max"] is False:
            out["max"] = raw["max"] or "off"
        else:
            probs.append(f"{w}.max: {raw['max']!r} isn't {', '.join(LEVELS)}: thick")
    for k, lo, hi in (("half_life_s", 1.0, 36000.0), ("volume_m3", 1.0, 100000.0), ("gain", 0.0, 100.0),
                      ("fill_until", 0.0, 1.0), ("fill_fan", 0.0, 1.0), ("hold_fan", 0.0, 1.0),
                      ("burst_above", 0.0, 1.0), ("burst_s", 1.0, 3600.0), ("rest_s", 0.0, 3600.0)):
        if raw.get(k) is None:
            continue
        v = _num(raw[k])
        if v is None or not lo <= v <= hi:
            probs.append(f"{w}.{k}: {raw[k]!r} isn't {lo:g}..{hi:g}: {FALLBACK[k]:g}")
        else:
            out[k] = v
    return out


def to_byte(share: float, ch: Mapping[str, Any]) -> int:
    """0..1 -> the channel's byte: ``off`` at 0, else linear across ``range`` (lo is the least output)."""
    if share <= 0.0:
        return int(ch["off"])
    lo, hi = ch["range"]
    return int(round(lo + min(1.0, share) * (hi - lo)))


class Air:
    """The estimate and the controller. ``step(now_s, want, hazers, room)`` -> the plan (``view``'s shape):
    ``want`` a LEVELS word (None: off); ``hazers`` [(fixture id, fixture type)]; ``room`` the space view's room."""

    def __init__(self) -> None:
        self.cfg: dict = parse(None, [])
        self.density = 0.0
        self.t: Optional[float] = None
        self.out = 0.0
        self.fan = 0.0
        self.phase = "off"
        self.want = "off"
        self.target = 0.0
        self.high_since: Optional[float] = None
        self.rest_until: Optional[float] = None
        self.volume = float(FALLBACK["volume_m3"])
        self.half_life = float(FALLBACK["half_life_s"])
        self.rate_ml_s = 0.0
        self.channels: dict[str, dict[str, int]] = {}
        self.why: Optional[str] = None

    def configure(self, cfg: dict) -> None:
        self.cfg = cfg

    def _room(self, room: Any) -> None:
        c = self.cfg
        size = room.get("size") if isinstance(room, Mapping) else None
        vol = None
        if isinstance(size, (list, tuple)) and len(size) == 3 and all(_num(x) and x > 0 for x in size):
            vol = float(size[0]) * float(size[1]) * float(size[2])
        self.volume = vol if vol is not None else float(c["volume_m3"])
        hl = _num(room.get("haze_half_life_s")) if isinstance(room, Mapping) else None
        self.half_life = hl if hl is not None and hl > 0 else float(c["half_life_s"])

    def step(self, now_s: float, want: Optional[str], hazers: list[tuple[str, str]], room: Any) -> dict:
        c = self.cfg
        self._room(room)
        dt = 0.0 if self.t is None else min(DT_MAX_S, max(0.0, now_s - self.t))
        self.t = now_s
        known = [(fid, c["types"][tid]) for fid, tid in hazers if tid in c["types"]]
        self.rate_ml_s = sum(t["fluid_ml_min"] for _, t in known) / 60.0
        # the air: what last step's output added, less what the room lost
        k = math.log(2.0) / self.half_life
        self.density = self.density * math.exp(-k * dt) + self.out * self.rate_ml_s * dt / self.volume
        w = want if want in LEVELS else "off"
        cap = c["max"]
        if LEVELS.index(w) > LEVELS.index(cap):
            w = cap
        self.want = w
        self.target = float(c["levels"].get(w, 0.0))
        self.why = None
        if not hazers:
            self.why = "no hazer (lights.atmosphere.select)"
        elif not known:
            self.why = "no hazer of a type lights.atmosphere.types knows"
        if self.target <= 0.0 or not known:
            out, fan, phase = 0.0, 0.0, "off" if self.density < 1e-4 else "clearing"
            self.high_since = self.rest_until = None
        else:
            hold = self.target * self.volume * k / self.rate_ml_s
            gap = (self.target - self.density) / self.target
            out = min(1.0, max(0.0, hold * (1.0 + c["gain"] * gap)))
            phase = "filling" if self.density < c["fill_until"] * self.target else "holding"
            fan = c["fill_fan"] if phase == "filling" else c["hold_fan"]
            if hold > 1.0:
                self.why = "the hazers can't hold this level in this room (the half-life is too short)"
            if self.rest_until is not None and now_s < self.rest_until:
                out, phase = min(out, hold), "resting"
            elif out > c["burst_above"]:
                if self.high_since is None:
                    self.high_since = now_s
                if now_s - self.high_since >= c["burst_s"]:
                    self.rest_until, self.high_since = now_s + c["rest_s"], None
                    out, phase = min(out, hold), "resting"
            else:
                self.high_since = None
        self.out, self.fan, self.phase = out, fan, phase
        ch: dict[str, dict[str, int]] = {}
        for fid, t in known if self.target > 0.0 else ():   # off: the fixture's own pin (and the Lights page) again
            vals = {t["output"]["channel"]: to_byte(out, t["output"])}
            if t["fan"] is not None:
                vals[t["fan"]["channel"]] = to_byte(fan, t["fan"])
            ch[fid] = vals
        self.channels = ch
        return self.view()

    def view(self) -> dict:
        t = self.target
        return {"want": self.want, "phase": self.phase, "target_ml_m3": round(t, 5),
                "estimate_ml_m3": round(self.density, 5),
                "share": round(self.density / t, 3) if t > 0 else None,
                "output": round(self.out, 3), "fan": round(self.fan, 3),
                "volume_m3": round(self.volume, 1), "half_life_s": round(self.half_life, 1),
                "fluid_ml_min": round(self.rate_ml_s * 60.0, 2), "channels": {k: dict(v) for k, v in
                                                                              self.channels.items()},
                "max": self.cfg["max"], "why": self.why}
