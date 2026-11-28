"""smsound.eq: a graphic equaliser for each sound output (owner, 27 Nov: "a graphic equaliser with some basic
settings/controls for audio output").

Seven bands, -12..+12 dB each, set by hand or by a preset. It is kept beside the output's latency in the same store
(the deployment's ``calibration/latency.json``, ``outputs.<id>.eq``: {"preset", "gains"}), because both describe the
speaker in this room. The players that shape the sound themselves (a screen's display page and a browser playing
it, both Web Audio: DisplayPage/karaoke/KaraokeLayer.tsx) apply it: the lowest band as a low shelf, the highest as a
high shelf, the rest as peaks, with the level lowered by the largest boost so a boost never clips.
"""
from __future__ import annotations

from typing import Any, Optional

import smconfig

from . import latency as L
from . import outputs as O

#: The bands' centres (Hz).
BANDS: tuple[int, ...] = (60, 150, 400, 1000, 2400, 6000, 15000)
RANGE_DB = 12.0

#: Presets, in the order the pickers show them: id -> (label, gains).
PRESETS: dict[str, tuple[str, tuple[float, ...]]] = {
    "flat": ("Flat", (0, 0, 0, 0, 0, 0, 0)),
    "vocals": ("Vocals", (-2, -1, 0, 2, 3, 2, 0)),
    "bass": ("Bass", (6, 4, 1, 0, 0, 0, 0)),
    "party": ("Party", (5, 3, 0, -1, 0, 2, 4)),
    "bright": ("Bright", (0, 0, 0, 0, 2, 4, 5)),
    "less_boom": ("Less boom", (-6, -4, -1, 0, 0, 0, 0)),
    "small": ("Small speaker", (-6, -2, 1, 2, 2, 1, 0)),
}

#: The kinds whose players apply it.
APPLIES = frozenset({"screen", "device"})


def _clamp(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return round(max(-RANGE_DB, min(RANGE_DB, f)), 1)


def _entry(oid: str, doc: dict) -> dict:
    ent = (doc.get("outputs") or {}).get(oid)
    e = ent.get("eq") if isinstance(ent, dict) else None
    return e if isinstance(e, dict) else {}


def gains(oid: str, dep: Optional[smconfig.Deployment] = None, doc: Optional[dict] = None) -> list[float]:
    """The output's gains (dB, one per band); all 0 when nothing is set."""
    g = _entry(oid, doc or L.read(dep)).get("gains")
    if not isinstance(g, list) or len(g) != len(BANDS):
        return [0.0] * len(BANDS)
    return [_clamp(x) for x in g]


def describe(oid: str, dep: Optional[smconfig.Deployment] = None, doc: Optional[dict] = None) -> dict:
    """{applies, preset, gains, bands, presets, range_db} for a picker."""
    doc = doc or L.read(dep)
    g = gains(oid, doc=doc)
    p = _entry(oid, doc).get("preset")
    if p not in PRESETS or list(PRESETS[p][1]) != g:
        p = next((k for k, (_, pg) in PRESETS.items() if list(pg) == g), None)
    return {"applies": (O.kind(oid) or "") in APPLIES, "preset": p, "gains": g, "bands": list(BANDS),
            "presets": [{"id": k, "label": lab, "gains": list(pg)} for k, (lab, pg) in PRESETS.items()],
            "range_db": RANGE_DB}


def set_eq(oid: str, *, preset: Optional[str] = None, gains_db: Optional[list] = None,
           dep: Optional[smconfig.Deployment] = None) -> dict:
    """Set a preset, or the gains by hand (the preset is then whichever matches, if any). -> describe()."""
    if O.parse(oid) is None or O.kind(oid) == "none":
        raise ValueError(f"not a sound output: {oid!r}")
    if preset is not None:
        if preset not in PRESETS:
            raise ValueError(f"preset: one of {', '.join(PRESETS)}")
        g = [float(x) for x in PRESETS[preset][1]]
    elif gains_db is not None:
        if not isinstance(gains_db, list) or len(gains_db) != len(BANDS):
            raise ValueError(f"gains: {len(BANDS)} numbers (dB)")
        g = [_clamp(x) for x in gains_db]
    else:
        raise ValueError("say preset or gains")
    with L._lock:
        doc = L.read(dep)
        ent = doc["outputs"].get(oid)
        ent = dict(ent) if isinstance(ent, dict) else {}
        if any(g):
            ent["eq"] = {"preset": preset, "gains": g}
        else:
            ent.pop("eq", None)
        doc["outputs"][oid] = ent
        L._write(doc, dep)
    return describe(oid, dep)
