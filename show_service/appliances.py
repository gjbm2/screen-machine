"""appliances: the room's remote-worked appliances (a glitterball's motor, pinspots on an RF remote, ...) as members of
the art director's light groups (owner, 28 Oct: "one notion of scenes which can apply to screens OR appliances";
"we need an intermediate structural layer which the a.d. can direct -- and we have one already, which just needs
suitably generalising"). Pure: no I/O, no clock of its own. director.py drives it on show-brain.

**Config** (show.yaml ``lights.appliances``, ``parse``): each appliance names the room targets that work each of its
capabilities (contract.ATTRS words), so nothing here knows a device, a target or a value by name:

  pan_spin      power {target, on, off}: on while the spin isn't 0; speed {target, words: [[from, word], ...]}: the
                spin's size picks the last word whose ``from`` <= it; direction {target, cw, ccw, min_gap_s}: its sign
  intensity     power {target, on, off}: on while above 0; level {target, steps}: round(intensity x steps), at least 1
  colour_slot   {target, words?: {slot: value}}: the slot word (or its mapped value)
  shutter       {target, strobe, open}

Any control may carry ``min_gap_s`` (default MIN_GAP_S): the least time between two changes to its target (a motor that
reverses wants minutes). While an appliance is powered off, its other controls are left alone.

**States** (a light scene's or effect's ``appliances`` block, ``parse_states``): {attr: value}: ``pan_spin`` a number
in -1..1 or {speed: 0..1, dir: flow | cw | ccw} (flow: the room's flow turns it, cw +, ccw -, any other dir keeps the
last sense); ``intensity`` 0..1; ``colour_slot`` a word; ``shutter`` strobe | open.

**Sending** (``Executor.step``): only for appliances the room lets the director use, never while the room is changing
mode; a target is sent when the wanted value differs from what the room believes and from what was last sent, its
gap has passed, and no send is in flight. One PUT carries every target that is due, so the room plans each device's
presses together.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

ATTRS: tuple[str, ...] = ("pan_spin", "intensity", "colour_slot", "shutter")
SHUTTER_WORDS: tuple[str, ...] = ("strobe", "open")
#: what a capability rests at when neither the scene nor an effect sets it (a strobe ends with its effect)
NEUTRAL: dict[str, Any] = {"shutter": "open"}
SPIN_DIRS: tuple[str, ...] = ("flow", "cw", "ccw")
MIN_GAP_S = 2.0
_CTL_KEYS: dict[str, tuple[str, ...]] = {"pan_spin": ("power", "speed", "direction"),
                                         "intensity": ("power", "level")}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def _target(c: Any, where: str, probs: list[str], keys: tuple[str, ...] = ()) -> Optional[dict]:
    if not isinstance(c, Mapping) or not isinstance(c.get("target"), str) or not c["target"]:
        probs.append(f"{where}: needs target: <room target>: ignored")
        return None
    out = {"target": c["target"], "min_gap_s": MIN_GAP_S}
    g = c.get("min_gap_s")
    if g is not None:
        if _num(g) is None or g < 0:
            probs.append(f"{where}.min_gap_s: {g!r} isn't seconds: {MIN_GAP_S:g}")
        else:
            out["min_gap_s"] = float(g)
    for k in keys:
        if k not in c:
            probs.append(f"{where}: needs {k}: ignored")
            return None
        out[k] = c[k]
    return out


def parse(raw: Any, probs: list[str]) -> dict[str, dict]:
    """show.yaml ``lights.appliances`` -> {id: {"label", "controls": {attr: {...}}, "caps": [attr]}}. A bad control is
    left out with a problem line; an appliance with none is left out (degrade, never refuse)."""
    out: dict[str, dict] = {}
    if raw is None:
        return out
    if not isinstance(raw, Mapping):
        probs.append("lights.appliances: not a mapping: none")
        return out
    for aid, a in raw.items():
        w = f"lights.appliances.{aid}"
        if not isinstance(a, Mapping) or not isinstance(a.get("controls"), Mapping):
            probs.append(f"{w}: needs controls: left out")
            continue
        ctl: dict[str, dict] = {}
        for attr, c in a["controls"].items():
            cw = f"{w}.controls.{attr}"
            if attr not in ATTRS:
                probs.append(f"{cw}: not something an appliance can do ({', '.join(ATTRS)}): ignored")
                continue
            if attr in _CTL_KEYS:
                if not isinstance(c, Mapping):
                    probs.append(f"{cw}: not a mapping: ignored")
                    continue
                parts: dict[str, dict] = {}
                for part in _CTL_KEYS[attr]:
                    if part not in c:
                        continue
                    keys = {"power": ("on", "off"), "speed": ("words",), "direction": ("cw", "ccw"),
                            "level": ("steps",)}[part]
                    got = _target(c[part], f"{cw}.{part}", probs, keys)
                    if got is None:
                        continue
                    if part == "speed" and not (isinstance(got["words"], list) and got["words"] and all(
                            isinstance(x, list) and len(x) == 2 and _num(x[0]) is not None for x in got["words"])):
                        probs.append(f"{cw}.speed.words: not [[from, word], ...]: ignored")
                        continue
                    if part == "level" and not (isinstance(got["steps"], int) and got["steps"] > 0):
                        probs.append(f"{cw}.level.steps: not a whole number > 0: ignored")
                        continue
                    parts[part] = got
                if parts:
                    ctl[attr] = parts
            elif attr == "colour_slot":
                got = _target(c, cw, probs)
                if got is not None:
                    words = c.get("words") if isinstance(c, Mapping) else None
                    got["words"] = dict(words) if isinstance(words, Mapping) else {}
                    ctl[attr] = got
            elif attr == "shutter":
                got = _target(c, cw, probs, SHUTTER_WORDS)
                if got is not None:
                    ctl[attr] = got
        if not ctl:
            probs.append(f"{w}: no usable controls: left out")
            continue
        label = a.get("label") if isinstance(a.get("label"), str) and a.get("label") else str(aid)
        out[str(aid)] = {"label": label, "controls": ctl, "caps": [x for x in ATTRS if x in ctl]}
    return out


def parse_states(raw: Any, where: str, probs: list[str]) -> Optional[dict]:
    """A scene's or effect's ``appliances`` block -> {attr: value}; None when there is none or nothing usable."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        probs.append(f"{where}.appliances: not a mapping of what the appliances do: ignored")
        return None
    out: dict[str, Any] = {}
    for k, v in raw.items():
        w = f"{where}.appliances.{k}"
        if k == "pan_spin":
            n = _num(v)
            if n is not None:
                out[k] = max(-1.0, min(1.0, n))
            elif isinstance(v, Mapping) and _num(v.get("speed")) is not None and v.get("dir", "flow") in SPIN_DIRS:
                out[k] = {"speed": max(0.0, min(1.0, float(v["speed"]))), "dir": v.get("dir", "flow")}
            else:
                probs.append(f"{w}: {v!r} isn't -1..1 or {{speed: 0..1, dir: flow|cw|ccw}}: ignored")
        elif k == "intensity":
            n = _num(v)
            if n is None:
                probs.append(f"{w}: {v!r} isn't 0..1: ignored")
            else:
                out[k] = max(0.0, min(1.0, n))
        elif k == "colour_slot":
            if isinstance(v, str) and v:
                out[k] = v
            else:
                probs.append(f"{w}: {v!r} isn't a slot word: ignored")
        elif k == "shutter":
            if v in SHUTTER_WORDS:
                out[k] = v
            else:
                probs.append(f"{w}: {v!r} isn't {' or '.join(SHUTTER_WORDS)}: ignored")
        else:
            probs.append(f"{w}: not something an appliance can do ({', '.join(ATTRS)}): ignored")
    return out or None


def _spin(v: Any, flow_dir: Optional[str], last_sign: float) -> float:
    if isinstance(v, Mapping):
        d = v["dir"]
        sign = 1.0 if d == "cw" else -1.0 if d == "ccw" else \
            1.0 if flow_dir == "cw" else -1.0 if flow_dir == "ccw" else last_sign
        return sign * float(v["speed"])
    return float(v)


def targets(app: Mapping[str, Any], state: Mapping[str, Any], flow_dir: Optional[str],
            last_sign: float = 1.0) -> dict[str, tuple[Any, float]]:
    """{room target: (value, min_gap_s)} for one appliance doing ``state`` (attrs it lacks or the state leaves out are
    left alone). Powered off: only the power target."""
    ctl = app["controls"]
    out: dict[str, tuple[Any, float]] = {}
    off = False
    if "pan_spin" in state and "pan_spin" in ctl:
        c, s = ctl["pan_spin"], _spin(state["pan_spin"], flow_dir, last_sign)
        on = abs(s) > 1e-6
        if "power" in c:
            out[c["power"]["target"]] = (c["power"]["on"] if on else c["power"]["off"], c["power"]["min_gap_s"])
            off = off or not on
        if on and "speed" in c:
            word = None
            for frm, w in c["speed"]["words"]:
                if abs(s) >= float(frm) - 1e-9:
                    word = w
            if word is not None:
                out[c["speed"]["target"]] = (word, c["speed"]["min_gap_s"])
        if on and "direction" in c:
            d = c["direction"]
            out[d["target"]] = (d["cw"] if s > 0 else d["ccw"], d["min_gap_s"])
    if "intensity" in state and "intensity" in ctl:
        c, i = ctl["intensity"], float(state["intensity"])
        on = i > 1e-6
        if "power" in c:
            out[c["power"]["target"]] = (c["power"]["on"] if on else c["power"]["off"], c["power"]["min_gap_s"])
            off = off or not on
        if on and "level" in c:
            out[c["level"]["target"]] = (max(1, round(i * c["level"]["steps"])), c["level"]["min_gap_s"])
    if off:                                               # the rest of a device that is off is left alone
        power = {c["power"]["target"] for c in (ctl.get("pan_spin"), ctl.get("intensity"))
                 if isinstance(c, Mapping) and "power" in c}
        return {t: v for t, v in out.items() if t in power}
    if "colour_slot" in state and "colour_slot" in ctl:
        c = ctl["colour_slot"]
        out[c["target"]] = (c["words"].get(state["colour_slot"], state["colour_slot"]), c["min_gap_s"])
    if "shutter" in state and "shutter" in ctl:
        c = ctl["shutter"]
        out[c["target"]] = (c[state["shutter"]], c["min_gap_s"])
    return out


def _believed(room: Any, target: str) -> Any:
    m = ((room or {}).get("members") or {}).get(target) if isinstance(room, Mapping) else None
    return m.get("believed") if isinstance(m, Mapping) else None


class Executor:
    """What has been sent, when, and the spin's last sense per appliance; ``step`` says what to send now."""

    def __init__(self) -> None:
        self.sent: dict[str, tuple[Any, float]] = {}      # target -> (value, when)
        self.sign: dict[str, float] = {}                  # appliance -> the spin's last sense (+1 cw, -1 ccw)
        self.wanted: dict[str, dict[str, Any]] = {}       # appliance -> {target: value} (the view)

    def step(self, now_s: float, wants: Mapping[str, tuple[Mapping[str, Any], Mapping[str, Any]]],
             flow_dir: Optional[str], room: Any, busy: bool) -> dict[str, Any]:
        """``wants``: {appliance id: (its config, its state)} for the appliances the director may use now. The
        targets to PUT now ({} for none)."""
        self.wanted = {}
        due: dict[str, Any] = {}
        changing = isinstance(room, Mapping) and room.get("transition") is not None
        for aid, (app, state) in wants.items():
            if isinstance(state.get("pan_spin"), Mapping) and flow_dir in ("cw", "ccw"):
                self.sign[aid] = 1.0 if flow_dir == "cw" else -1.0
            tg = targets(app, state, flow_dir, self.sign.get(aid, 1.0))
            self.wanted[aid] = {t: v for t, (v, _) in tg.items()}
            for t, (v, gap) in tg.items():
                last = self.sent.get(t)
                if _believed(room, t) == v and (last is None or last[0] == v):
                    continue
                if last is not None and last[0] == v:
                    continue                              # asked already: the room is on it
                if last is not None and now_s - last[1] < gap:
                    continue
                due[t] = v
        if busy or changing or not due:
            return {}
        for t, v in due.items():
            self.sent[t] = (v, now_s)
        return due

    def forget(self, targets_: Any = None) -> None:
        """Hands off: what was sent no longer stands (an appliance the director may not use any more)."""
        if targets_ is None:
            self.sent.clear()
        else:
            for t in targets_:
                self.sent.pop(t, None)
