"""idiom: the kind of show a song asks for, as limits on what the art director may draw (config/live/idioms.yaml).

Owner, 27 Nov 2026: "a serious ballad should just feel different from an ABBA track, and tonal quality of scene
selection should follow that much more than it does." Until now a karaoke song's character was a weight in a random
draw from one feel's whole catalogue: over 20 songs, how different two songs were said nothing about how different
their mover scenes were (rank correlation -0.08), and Hallelujah got bars pumping through a change, heads flying up
and the strobe zones flashing on the bar. An idiom is a vocabulary: the scenes, lights and transitions outside it are
not drawn at all.

What a scene is like is read from the scene itself (``traits``), so a new scene falls under every idiom's limits
with nothing to tag:

  motion   how far its moving heads travel: none (it moves no head) < still (an aim, a fan, a drift) < slow (a path
           taking four bars or more) < moving (two bars or more) < fast (quicker; a bounce, a ballyhoo)
  pulse    how hard its levels show the beat: none < soft (a sine, a triangle, anything shallow) < bar (a swell or a
           saw each bar or slower) < beat (each beat, or subdivided) < hard (a square: on and off; a strobe)

Pure: no engine, no clock, no I/O beyond ``load``.
"""
from __future__ import annotations

import pathlib
from typing import Any, Mapping, Optional

MOTION = ("none", "still", "slow", "moving", "fast")
PULSE = ("none", "soft", "bar", "beat", "hard")
#: The smallest section a group of lights plays in, smallest first; "never" is none of them.
LEVELS = ("verse", "chorus", "peak")
ARRIVE = ("none", "swell", "bump", "snap")
LANDS = frozenset({"bump", "snap"})                # the arrivals that land on the line (a cut, not a fade, takes them)
STYLES = ("gentle", "lively", "wild")              # vocab.yaml's transition styles
STILL_MODES = frozenset({"aim", "fan", "drift", "home"})
FAST_MODES = frozenset({"bounce", "ballyhoo"})
SHALLOW = 0.25                  # a level effect no deeper than this only breathes, whatever its shape
LOOK_SHARP = 3.0                # a scene one step from the wanted look is drawn this many times less often
PULSE_SHARE = 0.5               # ... a moving-head scene's pulse counting this much beside its motion
PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "live" / "idioms.yaml"

#: What an idiom falls back to (an unknown or missing idiom limits nothing).
OPEN: dict[str, Any] = {"label": "Any", "for": "", "motion_max": "fast", "pulse_max": "hard", "groups": {},
                        "motion_cap": 1.0, "screen_style": None, "looks": {},
                        "transitions": None, "arrive": "bump", "wind": 0.3, "lift": 0.35, "flourishes": 3,
                        "level": [0.3, 1.0], "palettes": [], "sound": {}}


def _slowest(layer: Mapping[str, Any]) -> Optional[float]:
    """A layer's period in bars at its slowest (a [slow, fast] pair runs between them with the music's energy);
    None when it has none in bars."""
    p = layer.get("period_bars")
    if isinstance(p, (list, tuple)) and p:
        nums = [float(x) for x in p if isinstance(x, (int, float))]
        return max(nums) if nums else None
    if isinstance(p, (int, float)):
        return float(p)
    if layer.get("phase") == "phrase":
        return 8.0
    if isinstance(layer.get("period_s"), (int, float)):
        return float(layer["period_s"]) / 2.0                    # about two seconds a bar
    return None


def _motion_of(layer: Mapping[str, Any]) -> str:
    mode = layer.get("mode")
    if mode in FAST_MODES:
        return "fast"
    if mode in STILL_MODES:
        return "still"
    if mode == "lift" and not isinstance(layer.get("depth"), Mapping):
        return "still"                                           # a lift held where it is
    bars = _slowest(layer.get("depth") if isinstance(layer.get("depth"), Mapping) and mode == "lift" else layer)
    if bars is None:
        return "moving"
    return "slow" if bars >= 4.0 else "moving" if bars >= 2.0 else "fast"


def _pulse_of(layer: Mapping[str, Any]) -> str:
    if layer.get("kind") == "shutter":
        return "hard" if layer.get("mode") not in (None, "open", "closed") else "none"
    fx = layer.get("fx")
    if fx in (None, "dim"):
        return "none"
    depth = layer.get("depth")
    if isinstance(depth, (int, float)) and depth <= SHALLOW:
        return "soft"
    if fx in ("sine", "triangle"):
        return "soft"
    on_beat = layer.get("phase") == "beat" or bool(layer.get("subdivide"))
    if fx == "square":
        return "hard" if on_beat or (_slowest(layer) or 1.0) < 1.0 else "beat"
    if on_beat:
        return "beat"
    bars = _slowest(layer)
    return "bar" if bars is None or bars >= 1.0 else "beat"


def traits(scene: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """{motion, pulse} of a light scene, from its layers (module docstring). ``tags.traits`` overrides either."""
    motion, pulse = 0, 0
    for layer in (scene or {}).get("layers") or ():
        if not isinstance(layer, Mapping):
            continue
        if layer.get("kind") == "position":
            motion = max(motion, MOTION.index(_motion_of(layer)))
        else:
            pulse = max(pulse, PULSE.index(_pulse_of(layer)))
            d = layer.get("depth")
            if isinstance(d, Mapping):                           # a colour or a beam riding an effect
                pulse = max(pulse, PULSE.index(_pulse_of(d)))
    out = {"motion": MOTION[motion], "pulse": PULSE[pulse]}
    over = ((scene or {}).get("tags") or {}).get("traits")
    if isinstance(over, Mapping):
        if over.get("motion") in MOTION:
            out["motion"] = over["motion"]
        if over.get("pulse") in PULSE:
            out["pulse"] = over["pulse"]
    return out


def clean(name: str, raw: Any, probs: Optional[list[str]] = None) -> dict:
    """One idiom's entry with every field present and in range (a bad one falls back to OPEN's, and says so)."""
    probs = probs if probs is not None else []
    r = raw if isinstance(raw, Mapping) else {}
    out = dict(OPEN)
    out["label"] = str(r.get("label") or name)
    out["for"] = str(r.get("for") or "")
    for key, scale in (("motion_max", MOTION), ("pulse_max", PULSE), ("arrive", ARRIVE)):
        if r.get(key) in scale:
            out[key] = r[key]
        elif key in r:
            probs.append(f"idioms.{name}.{key}: {r.get(key)!r} is not one of {', '.join(scale)}")
    g = r.get("groups")
    out["groups"] = {}
    if isinstance(g, Mapping):
        for gid, lv in g.items():
            if lv in LEVELS or lv == "never":
                out["groups"][str(gid)] = lv
            else:
                probs.append(f"idioms.{name}.groups.{gid}: {lv!r} is not verse, chorus, peak or never")
    t = r.get("transitions")
    out["transitions"] = [str(x) for x in t] if isinstance(t, (list, tuple)) and t else None
    for key in ("wind", "lift", "motion_cap"):
        v = r.get(key)
        out[key] = min(1.0, max(0.0, float(v))) if isinstance(v, (int, float)) else OPEN[key]
    out["screen_style"] = r.get("screen_style") if r.get("screen_style") in STYLES else None
    out["looks"] = {}
    for lv, t in (r.get("looks") or {}).items() if isinstance(r.get("looks"), Mapping) else ():
        if lv in LEVELS and isinstance(t, (list, tuple)) and len(t) == 2 and t[0] in MOTION and t[1] in PULSE:
            out["looks"][lv] = (t[0], t[1])
        else:
            probs.append(f"idioms.{name}.looks.{lv}: {t!r} is not [motion, pulse] for verse, chorus or peak")
    v = r.get("flourishes")
    out["flourishes"] = max(0, int(v)) if isinstance(v, (int, float)) else OPEN["flourishes"]
    lv = r.get("level")
    if isinstance(lv, (list, tuple)) and len(lv) == 2 and all(isinstance(x, (int, float)) for x in lv):
        out["level"] = [min(1.0, max(0.0, float(lv[0]))), min(1.0, max(0.0, float(lv[1])))]
    out["palettes"] = [str(x) for x in r.get("palettes") or () if isinstance(x, str)]
    out["sound"] = {k: float(x) for k, x in (r.get("sound") or {}).items() if isinstance(x, (int, float))} \
        if isinstance(r.get("sound"), Mapping) else {}
    return out


def load(path: Optional[pathlib.Path] = None) -> dict:
    """{idioms {name: entry}, palettes {name: words}, problems [..]} from idioms.yaml ({} of each with no file)."""
    probs: list[str] = []
    out: dict[str, Any] = {"idioms": {}, "palettes": {}, "problems": probs}
    p = path or PATH
    try:
        import yaml
        raw = yaml.safe_load(p.read_text()) or {}
    except OSError:
        return out
    except Exception as e:                                       # noqa: BLE001 - a bad file is no idioms, said
        probs.append(f"{p.name}: {type(e).__name__}: {e}")
        return out
    for name, entry in (raw.get("idioms") or {}).items():
        out["idioms"][str(name)] = clean(str(name), entry, probs)
    out["palettes"] = {str(k): str(v) for k, v in (raw.get("palettes") or {}).items()}
    return out


def allows(idiom: Optional[Mapping[str, Any]], scene: Optional[Mapping[str, Any]]) -> bool:
    """A scene is within an idiom's motion and pulse. A rest, or a scene made for a moment, always is."""
    if not idiom or not isinstance(scene, Mapping):
        return True
    tags = scene.get("tags") or {}
    if tags.get("rest") or tags.get("moments"):
        return True
    t = traits(scene)
    return MOTION.index(t["motion"]) <= MOTION.index(idiom.get("motion_max", "fast")) and \
        PULSE.index(t["pulse"]) <= PULSE.index(idiom.get("pulse_max", "hard"))


def closeness(idiom: Optional[Mapping[str, Any]], tr: Mapping[str, str], level: Optional[str]) -> float:
    """How close a scene (its ``traits``) is to the look the idiom wants for a section of this ``level``: 1.0 at
    it, LOOK_SHARP times less for each step away in motion (and half that for each step in pulse; a scene that
    moves no head is judged by its pulse alone). 1.0 when the idiom wants nothing there."""
    looks = (idiom or {}).get("looks") or {}
    t = looks.get(level if level in LEVELS else "verse")
    if t is None:
        return 1.0
    dp = abs(PULSE.index(tr.get("pulse", "none")) - PULSE.index(t[1]))
    if tr.get("motion", "none") == "none":
        d = float(dp)
    else:
        d = abs(MOTION.index(tr["motion"]) - MOTION.index(t[0])) + PULSE_SHARE * dp
    return LOOK_SHARP ** -d


def plays(idiom: Optional[Mapping[str, Any]], group: str, level: Optional[str]) -> bool:
    """A group of lights may play in a section of this ``level`` (verse | chorus | peak; None: not known, so the
    idiom's "never" alone holds it back)."""
    need = ((idiom or {}).get("groups") or {}).get(group)
    if need is None:
        return True
    if need == "never":
        return False
    if level not in LEVELS:
        return True
    return LEVELS.index(level) >= LEVELS.index(need)


def transition_ok(idiom: Optional[Mapping[str, Any]], name: Optional[str]) -> bool:
    """A light transition is one the idiom uses (an idiom that lists none uses any)."""
    allowed = (idiom or {}).get("transitions")
    return not allowed or name is None or name in allowed


def nearest(idioms: Mapping[str, Mapping[str, Any]], sound: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The idiom whose ``sound`` is closest to a recording's own measures (bpm, punch, range_db): the choice with no
    AI. None with no idioms or no measures."""
    if not idioms or not isinstance(sound, Mapping):
        return None
    scale = {"bpm": 40.0, "punch": 0.02, "range_db": 6.0}
    best, best_d = None, None
    for name, e in idioms.items():
        s = e.get("sound") or {}
        ds = [((float(sound[k]) - float(s[k])) / scale[k]) ** 2 for k in scale
              if isinstance(sound.get(k), (int, float)) and isinstance(s.get(k), (int, float))]
        if not ds:
            continue
        d = sum(ds) / len(ds)
        if best_d is None or d < best_d:
            best, best_d = name, d
    return best
