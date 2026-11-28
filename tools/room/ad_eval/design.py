#!/usr/bin/env python3
"""Design measures: is a song's show coherent and restrained, as well as on the music?

score.py's own metrics say whether the show follows the music (beat, snap, contour, sections, climax, ending). A show
can score well on all of them and still "flash like a school disco" (owner, 27 Nov 2026): a new palette and a new
scene on every section, a flourish on every change. These measure what working lighting designers hold to
(docs/LIGHT_DESIGN_NOTES.md), from the art director's own decisions (a timeline's ``decisions``):

  palette     one palette a song: 1 / the number of palettes its looks used
  kit         a handful of looks a song: for each group of lights, KIT_SIZE / the number of different scenes it
              showed (resting and waiting scenes aside), at most 1; the mean over the groups
  flourish    a flourish is rare: FLOURISH_BUDGET / the number of light changes that carried a gesture (every head
              flies up, a pump, a sweep through, a strobe across the change), at most 1
  stillness   the moving heads hold still or drift most of the time: the share of the song their scene does, over
              STILL_SHARE, at most 1
  reserve     the whole rig is kept for the song's high point: the share of its biggest line-ups that are the climax
  design      the mean of those that could be measured

    python tools/room/ad_eval/design.py <timeline.json> [...]     # replay.py's, or ad_review/timeline.py build --out

Pure functions over a timeline; the scene and transition catalogues say which scenes rest, which move the heads and
which transitions carry a gesture. The bars (KIT_SIZE ...) are starting points from the research, in weights.yaml's
``design`` block: tune them by watching the room, on purpose (a change makes runs incomparable).
"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402

DEFAULTS = {"kit_size": 3, "flourish_budget": 3, "still_share": 0.6}
#: Position modes that keep a head where it is, or wander slowly; every other mode travels (sweep, circle, hops ...).
STILL_MODES = frozenset({"aim", "fan", "drift", "home"})
MOVERS = "movers"

_cat: Optional[tuple[dict, dict]] = None


def catalogues() -> tuple[dict, dict]:
    """(the light scenes, the light transitions) as the show's own files have them."""
    global _cat
    if _cat is None:
        import yaml
        live = common.REPO / "config" / "live"
        sc = (yaml.safe_load((live / "light_scenes.yaml").read_text()) or {}).get("scenes") or {}
        tr = (yaml.safe_load((live / "light_transitions.yaml").read_text()) or {}).get("transitions") or {}
        _cat = (sc, tr)
    return _cat


def is_look(scene: Optional[str], scenes: dict) -> bool:
    """A scene that is a look of the song: not a group's rest, not a waiting or lead-in scene."""
    tags = (scenes.get(scene or "") or {}).get("tags") or {}
    return bool(scene) and scene in scenes and not tags.get("rest") and not tags.get("moments")


def holds_still(scene: Optional[str], scenes: dict) -> bool:
    """The heads stand or drift in this scene (or it isn't a scene of theirs at all: a rest)."""
    layers = (scenes.get(scene or "") or {}).get("layers") or []
    modes = [l.get("mode") for l in layers if isinstance(l, dict) and l.get("kind") == "position"]
    return all(m in STILL_MODES for m in modes)


def design(tl: dict, scenes: Optional[dict] = None, transitions: Optional[dict] = None,
           W: Optional[dict] = None) -> dict:
    """The design measures of one timeline (each 0..1 or None, with the counts behind it)."""
    if scenes is None or transitions is None:
        sc, tr = catalogues()
        scenes, transitions = scenes if scenes is not None else sc, transitions if transitions is not None else tr
    w = {**DEFAULTS, **((W or {}).get("design") or {})}
    dec = [d for d in tl.get("decisions") or [] if isinstance(d.get("detail"), dict)]
    song = tl.get("song") or {}
    ref = tl.get("reference") or {}
    end = float(ref.get("end_s") or song.get("duration_s") or song.get("duration") or 0.0) or \
        max([float(d["t_s"]) for d in dec] or [0.0])
    body = [d for d in dec if 0.0 <= float(d["t_s"]) <= end]
    m: dict[str, Any] = {}

    # one palette a song
    pals = [d["detail"].get("palette") for d in body if d.get("kind") == "look_change" and d["detail"].get("palette")]
    m["palettes"] = len(set(pals))
    m["palette"] = round(1.0 / m["palettes"], 3) if pals else None

    # a handful of looks a song, and how long the heads hold still
    shown: dict[str, list[str]] = {}
    mover_at: list[tuple[float, Optional[str]]] = []
    flourishes = changes = 0
    for d in dec:
        if d.get("kind") != "light_program":
            continue
        t = float(d["t_s"])
        groups = d["detail"].get("groups") or {}
        if 0.0 <= t <= end:
            changes += 1
            if any(isinstance((transitions.get((g or {}).get("transition") or "") or {}).get("gesture"), dict)
                   for g in groups.values()):
                flourishes += 1
        for gid, g in groups.items():
            sc_name = (g or {}).get("scene")
            if gid == MOVERS:
                mover_at.append((t, sc_name))
            if 0.0 <= t <= end and is_look(sc_name, scenes):
                shown.setdefault(gid, [])
                if sc_name not in shown[gid]:
                    shown[gid].append(sc_name)
    m["scenes_per_group"] = {g: len(v) for g, v in shown.items()}
    m["kit"] = round(sum(min(1.0, w["kit_size"] / len(v)) for v in shown.values()) / len(shown), 3) if shown else None
    m["light_changes"], m["flourishes"] = changes, flourishes
    m["flourish"] = round(min(1.0, w["flourish_budget"] / flourishes), 3) if flourishes else (1.0 if changes else None)
    still = total = 0.0
    for (t0, name), (t1, _n) in zip(mover_at, mover_at[1:] + [(end, None)]):
        a, b = max(0.0, t0), min(end, t1)
        if b > a:
            total += b - a
            still += (b - a) if holds_still(name, scenes) else 0.0
    m["still_share"] = round(still / total, 3) if total > 0 else None
    m["stillness"] = round(min(1.0, m["still_share"] / w["still_share"]), 3) if total > 0 else None

    # the whole rig kept for the high point
    lus = [(len(d["detail"].get("plays") or []), d["detail"].get("into") == "climax" or bool(d["detail"].get("peak")))
           for d in body if d.get("kind") == "lineup"]
    if lus and any(pk for _n, pk in lus):
        top = max(n for n, _pk in lus)
        biggest = [pk for n, pk in lus if n == top]
        m["reserve"] = round(sum(biggest) / len(biggest), 3)
    else:
        m["reserve"] = None                              # a song with no high point: nothing to keep it for
    parts = [m[k] for k in ("palette", "kit", "flourish", "stillness", "reserve") if m.get(k) is not None]
    m["design"] = round(sum(parts) / len(parts), 3) if parts else None
    return m


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    for p in sys.argv[1:]:
        tl = json.loads(pathlib.Path(p).read_text())
        m = design(tl)
        title = (tl.get("song") or {}).get("title") or pathlib.Path(p).stem
        print(f"{title}: design {m['design']}  palette {m['palette']} ({m['palettes']})  kit {m['kit']} "
              f"{m['scenes_per_group']}  flourish {m['flourish']} ({m['flourishes']} of {m['light_changes']})  "
              f"stillness {m['stillness']} (still {m['still_share']})  reserve {m['reserve']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
