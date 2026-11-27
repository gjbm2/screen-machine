"""Latency: the one store and the one sign rule for every delay the app compensates (docs/SOUND.md; owner, 27 Nov:
"ensure it always correctly implements the various latency calibration machinery ... latency calibration itself
should also be generalised across the app and sensibly exposed").

**The file.** The deployment's ``calibration/latency.json`` (schema v2: ``screens``, ``outputs``, ``sources``), the
file the lights, the screens and the streams were already measured into. Sound outputs live beside them:

    outputs["<sound output id>"]["sound"] = {
        "delay_ms":    measured: from the moment the sender says the sound starts (its clock) to the moment the room
                       hears it; + = heard later. A screen: the display page's schedule to the room (browser output,
                       HDMI, the TV or Bluetooth). Sonos: room_service's play request to the room (piano_render/sonos.py
                       ``start_ms``: the same number).
        "trim_ms":     a person's nudge on top (kept when it is measured again),
        "method":      "chirp" (smsound's calibration: chirps and the room mic) | "manual",
        "measured_at": ISO time, and the measurement's own figures (spread_ms, agree, of, readings_ms, mic),
    }

Only this module writes ``sound`` entries; it changes nothing else in the file (others write their own keys: the
light and screen tools). Writes are atomic (a temporary file, then a rename) under a lock.

**The rule.** Every latency, in every category, is milliseconds, + = the physical event happens later than the clock
it is scheduled on, and the consumer starts that much earlier (or shows that much later). ``effective_ms`` = measured
+ trim, else the kind's default (``DEFAULTS``: Sonos 500 ms, measured by hand 21 Oct), else None (the consumer's own
fallback: a browser's reported output latency).

``overview()`` is every latency the deployment knows, in one table, for the Settings page: sound outputs (this file),
screens (render to light: tools/room/latency_cal), lights (each light output's rise: latency_cal/hue_cal), and the
room's sound sources (``sources.<stream>.acoustic_offset_ms``), each with what it measures, how it is measured and
where it is used.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

import smconfig

from . import outputs as O

VERSION = 2
FILE_PARTS = ("calibration", "latency.json")
#: The kinds' defaults when nothing is measured (None: the consumer's own: a browser's reported output latency).
DEFAULTS: dict[str, Optional[float]] = {"sonos": 500.0}
TRIM_RANGE = (-2000.0, 2000.0)
DELAY_RANGE = (-500.0, 20000.0)

_lock = threading.Lock()
_cache: dict[str, tuple[tuple, dict]] = {}       # path -> ((mtime_ns, size), doc): read() runs on every /now poll


def path(dep: Optional[smconfig.Deployment] = None) -> Path:
    return (dep or smconfig.deployment()).path(*FILE_PARTS)


def normalise(doc: Any) -> dict:
    """A latency.json document with its three parts (a missing or bad one: empty)."""
    doc = dict(doc) if isinstance(doc, dict) else {}
    for k in ("screens", "outputs", "sources"):
        if not isinstance(doc.get(k), dict):
            doc[k] = {}
    doc.setdefault("version", VERSION)
    return doc


def read(dep: Optional[smconfig.Deployment] = None) -> dict:
    """The whole file ({} parts when it is missing or unreadable). A copy: change it freely."""
    p = path(dep)
    try:
        stt = p.stat()
        mt = (stt.st_mtime_ns, stt.st_size)
        hit = _cache.get(str(p))
        if hit and hit[0] == mt:
            return json.loads(json.dumps(hit[1]))
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            _cache[str(p)] = (mt, doc)
            doc = json.loads(json.dumps(doc))
    except (OSError, ValueError):
        doc = {}
    return normalise(doc)


def _write(doc: dict, dep: Optional[smconfig.Deployment] = None) -> None:
    p = path(dep)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    _cache.pop(str(p), None)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    return float(v)


def sound(oid: str, dep: Optional[smconfig.Deployment] = None, doc: Optional[dict] = None) -> dict:
    """An output's ``sound`` entry ({} when nothing is stored)."""
    ent = ((doc or read(dep)).get("outputs") or {}).get(oid)
    s = ent.get("sound") if isinstance(ent, dict) else None
    return dict(s) if isinstance(s, dict) else {}


def effective_ms(oid: str, dep: Optional[smconfig.Deployment] = None, doc: Optional[dict] = None) -> Optional[float]:
    """The delay a consumer compensates for ``oid``: measured + trim, else the kind's default (+ trim), else None.
    A Sonos zone's measured ``start_ms`` from before smsound (piano_render/sonos.py's key) counts as measured."""
    doc = doc or read(dep)
    s = sound(oid, doc=doc)
    trim = _num(s.get("trim_ms")) or 0.0
    d = _num(s.get("delay_ms"))
    if d is None and O.kind(oid) == "sonos":
        legacy = ((doc.get("outputs") or {}).get(oid) or {})
        d = _num(legacy.get("start_ms")) if isinstance(legacy, dict) else None
    if d is None:
        d = DEFAULTS.get(O.kind(oid) or "")
    if d is None:
        return trim if s.get("trim_ms") else None
    return d + trim


def describe(oid: str, dep: Optional[smconfig.Deployment] = None, doc: Optional[dict] = None) -> dict:
    """An output's latency as the pages show it: {effective_ms, delay_ms, trim_ms, method, measured_at, source}
    (``source``: "measured" | "manual" | "default" | "device" | "live" | "browser" | "none")."""
    doc = doc or read(dep)
    s = sound(oid, doc=doc)
    k = O.kind(oid)
    d = _num(s.get("delay_ms"))
    src = "measured" if d is not None and s.get("method") != "manual" else "manual" if d is not None else None
    if d is None and k == "sonos" and _num(((doc.get("outputs") or {}).get(oid) or {}).get("start_ms")) is not None:
        src = "measured"
    if src is None:
        src = ("default" if DEFAULTS.get(k or "") is not None else
               {"device": "device", "pc22": "live", "screen": "browser", "none": "none"}.get(k or "", "none"))
    return {"effective_ms": effective_ms(oid, doc=doc), "delay_ms": d, "trim_ms": _num(s.get("trim_ms")) or 0.0,
            "method": s.get("method"), "measured_at": s.get("measured_at"), "source": src,
            **{k2: s[k2] for k2 in ("spread_ms", "agree", "of") if k2 in s}}


def _update(oid: str, fn, dep: Optional[smconfig.Deployment] = None) -> dict:
    if O.parse(oid) is None:
        raise ValueError(f"not a sound output: {oid!r}")
    with _lock:
        doc = read(dep)
        ent = doc["outputs"].get(oid)
        ent = dict(ent) if isinstance(ent, dict) else {}
        s = dict(ent.get("sound") or {}) if isinstance(ent.get("sound"), dict) else {}
        s = fn(s)
        ent["sound"] = s
        doc["outputs"][oid] = ent
        _write(doc, dep)
        return s


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def record(oid: str, delay_ms: float, *, method: str = "chirp", extra: Optional[dict] = None,
           dep: Optional[smconfig.Deployment] = None) -> dict:
    """A measured delay for ``oid`` (the trim is kept)."""
    d = _num(delay_ms)
    if d is None or not DELAY_RANGE[0] <= d <= DELAY_RANGE[1]:
        raise ValueError(f"delay out of range: {delay_ms!r}")

    def fn(s: dict) -> dict:
        keep = {"trim_ms": s["trim_ms"]} if _num(s.get("trim_ms")) else {}
        return {"delay_ms": round(d, 1), **keep, "method": method, "measured_at": _now_iso(), **(extra or {})}
    return _update(oid, fn, dep)


def set_trim(oid: str, trim_ms: float, dep: Optional[smconfig.Deployment] = None) -> dict:
    t = _num(trim_ms)
    if t is None:
        raise ValueError("trim_ms is a number")
    t = max(TRIM_RANGE[0], min(TRIM_RANGE[1], t))

    def fn(s: dict) -> dict:
        return {**s, "trim_ms": round(t, 1)}
    return _update(oid, fn, dep)


def set_manual(oid: str, delay_ms: Optional[float], dep: Optional[smconfig.Deployment] = None) -> dict:
    """A typed-in delay (None: forget the stored delay; the trim stays)."""
    if delay_ms is None:
        return _update(oid, lambda s: {k: v for k, v in s.items() if k == "trim_ms"}, dep)
    return record(oid, float(delay_ms), method="manual", dep=dep)


SOURCE_RANGE = (-1000.0, 1000.0)


def record_source(stream: str, offset_ms: float, *, method: str = "chirp", extra: Optional[dict] = None,
                  dep: Optional[smconfig.Deployment] = None) -> dict:
    """A sound coming in: ``sources.<stream>.acoustic_offset_ms`` (show_service adds it to the stream's timestamps:
    - = the beat clock had the sound later than the room heard it)."""
    o = _num(offset_ms)
    if not isinstance(stream, str) or not stream or o is None or not SOURCE_RANGE[0] <= o <= SOURCE_RANGE[1]:
        raise ValueError(f"bad source offset: {stream!r} {offset_ms!r}")
    with _lock:
        doc = read(dep)
        e = {"acoustic_offset_ms": round(o, 1), "method": method, "measured_at": _now_iso(), **(extra or {})}
        doc["sources"][stream] = e
        _write(doc, dep)
        return e


LIGHT_RANGE = (0.0, 2000.0)


def record_light(key: str, rise_ms: float, fall_ms: Optional[float] = None, *, attr: str = "intensity",
                 method: str = "camera", extra: Optional[dict] = None,
                 dep: Optional[smconfig.Deployment] = None) -> dict:
    """A light's measured response (``key``: a fixture id, or an output's latency_key) as the show engine reads it:
    ``outputs.<key>.<attr>`` {rise: {t50}, fall: {t50}} (show_service fixtures.resolve_latency; + = seen that long
    after the frame was sent). The trim and the entry's other attributes are kept."""
    if not isinstance(key, str) or not key or O.parse(key) is not None:
        raise ValueError(f"not a light: {key!r}")
    r, f = _num(rise_ms), _num(fall_ms) if fall_ms is not None else None
    if r is None or not LIGHT_RANGE[0] <= r <= LIGHT_RANGE[1]:
        raise ValueError(f"rise out of range: {rise_ms!r}")
    if fall_ms is not None and (f is None or not LIGHT_RANGE[0] <= f <= LIGHT_RANGE[1]):
        raise ValueError(f"fall out of range: {fall_ms!r}")
    with _lock:
        doc = read(dep)
        ent = dict(doc["outputs"].get(key) or {}) if isinstance(doc["outputs"].get(key), dict) else {}
        old = ent.get(attr) if isinstance(ent.get(attr), dict) else {}
        e = {"rise": {"t50": round(r, 1)}, **({"fall": {"t50": round(f, 1)}} if f is not None else {}),
             **({"trim_ms": old["trim_ms"]} if _num(old.get("trim_ms")) else {}),
             "method": method, "measured_at": _now_iso(), **(extra or {})}
        ent[attr] = e
        doc["outputs"][key] = ent
        _write(doc, dep)
        return e


# ---------------------------------------------------------------- every latency, for the Settings page

def _edge_ms(ent: Any) -> Optional[float]:
    """A light/screen entry's rise t50 (the onset the show engine aligns on, before align_pct)."""
    if not isinstance(ent, dict):
        return None
    rise = ent.get("rise") if isinstance(ent.get("rise"), dict) else None
    return _num(rise.get("t50")) if rise else None


def overview(dep: Optional[smconfig.Deployment] = None, extra_outputs: Optional[list[dict]] = None) -> dict:
    """Every latency the deployment knows, grouped (sound, screens, lights, sources): rows of
    {key, label, value_ms, trim_ms, source, measured_at, method, measures, how, used_by}."""
    dep = dep or smconfig.deployment()
    doc = read(dep)
    rows_sound = []
    for r in O.static_outputs(dep) + list(extra_outputs or []):
        if r["kind"] == "none":
            continue
        rows_sound.append({**r, "latency": describe(r["id"], doc=doc)})
    screens = []
    for sid, ent in doc["screens"].items():
        eff = (ent.get("effective") or {}) if isinstance(ent, dict) else {}
        screens.append({"key": sid, "label": f"Screen {sid}: picture to light",
                        "value_ms": _num(eff.get("presented_to_photon_ms")), "measured_at": ent.get("measured_at"),
                        "method": ent.get("method"),
                        "measures": "a frame presented on the screen's page to its light leaving the TV",
                        "how": "tools/room/latency_cal/calibrate.py (the light sensor on the screen)",
                        "used_by": "stream_service: the live picture is made this much ahead of the beat"})
    lights, sources = [], []
    for key, ent in doc["outputs"].items():
        if O.parse(key) is not None or not isinstance(ent, dict):
            continue                                     # a sound output: above
        for attr, e in ent.items():
            if not isinstance(e, dict):
                continue
            lights.append({"key": f"{key}.{attr}", "label": f"{key}: {attr}", "value_ms": _edge_ms(e),
                           "trim_ms": _num(e.get("trim_ms")) or 0.0, "measured_at": e.get("measured_at"),
                           "method": e.get("method"), "measures": f"the command sent to the {attr} seen (rise, t50)",
                           "how": ("the camera (Calibrate, here)" if e.get("method") == "camera" else
                                   "tools/room/latency_cal (hue_cal.py for Hue; calibrate.py for a screen)"),
                           "used_by": "show_service: each light is driven this much ahead"})
    for stream, ent in doc["sources"].items():
        v = _num(ent.get("acoustic_offset_ms")) if isinstance(ent, dict) else None
        sources.append({"key": stream, "label": f"Sound in: {stream}", "value_ms": v,
                        "measured_at": ent.get("measured_at") if isinstance(ent, dict) else None,
                        "measures": "the stream's timestamps to the sound in the room",
                        "how": "chirps from this device (Calibrate, here)" if isinstance(ent, dict) and
                        ent.get("method") == "chirp" else "set by hand in latency.json",
                        "used_by": "show_service's beat clock"})
    return {"file": str(path(dep).relative_to(smconfig.REPO_ROOT)) if path(dep).is_relative_to(smconfig.REPO_ROOT)
            else str(path(dep)),
            "rule": "milliseconds; + = the room hears or sees it that much after the moment it is scheduled for",
            "sound": rows_sound, "screens": screens, "lights": lights, "sources": sources}
