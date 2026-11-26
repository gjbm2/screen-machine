"""picture: what the screens' scene looks like, for the lights (owner, 26 Nov: "have a.d. use motion of gigbar lights
in a smart way that matches e.g. scenes / effects"). Pure: no I/O but the facts file, no clock of its own.

Until now the lights never looked at the picture: the movers' scene and the screens' scene were separate draws on the
same music words. The renderer says what each screen shows and what is queued next (GET /api/sessions: ``scene``,
``upnext``, ``fx``; the director reads it through room); this module says what that scene's picture DOES, so the
director can make the room agree with it:

  dir        the picture's own direction of travel, a flow word (contract.FLOW_DIRS: a tunnel is ``inward``, a
             kaleidoscope ``cw``, fire ``up``, rain ``down``, a scroll ``right``, a burst ``outward``); None: none
  pace       0..1, how much the picture moves (the renderer's measured ``motion_pct``); None: not measured
  busy       0..1, how busy it is (``busy_pct``); bright 0..1 (``brightness_pct``); None: not measured

**Where the facts come from.** ``build`` (tools/room/scene_motion.py writes config/live/scene_motion.generated.json)
reads the renderer's two generated files: tags.generated.json (every preset's one-line ``_look``: what the picture
shows, in words) and look_stats.generated.json (the measured numbers). The direction is read from the ``_look``'s own
words and the preset's name (WORDS below): a first pass, on paper. config/live/scene_motion.yaml (``scenes: {<preset>:
{dir, pace, busy}}``) is the hand file: what it sets wins. A scene in neither file gets its direction from its name.

**What the director does with them** (director.py):
  * the flow: the scene showing votes its ``dir`` into the room's flow (show.yaml lights.flow.sources.screen), so the
    movers' flow-following scenes, the glitterball and every wave turn the way the picture travels;
  * the movers' pick (``prefer``): at a look change each light scene's weight is multiplied by how well it fits the
    scene queued NEXT (the one this look will show): x ``DIR_FIT`` when its flow tags name the picture's dir (or it
    honours the flow), x ``DIR_AGAINST`` when they name only the opposite; closer to the picture's pace is better
    (x 1 .. ``PACE_FAR``); and complexity contrasts: a busy picture wants simple movement, a sparse one lets the
    lights carry more (``BUSY_*``).
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import time
from typing import Any, Mapping, Optional

from . import contract

log = logging.getLogger(__name__)

LIVE_DIR = contract.REPO_ROOT / "config" / "live"
FACTS_FILE = LIVE_DIR / "scene_motion.generated.json"
HAND_FILE = LIVE_DIR / "scene_motion.yaml"
TAGS_FILE = LIVE_DIR / "tags.generated.json"
STATS_FILE = LIVE_DIR / "look_stats.generated.json"

FLOW_DIRS: tuple[str, ...] = getattr(contract, "FLOW_DIRS", ("still", "up", "down", "left", "right", "cw", "ccw",
                                                             "outward", "inward", "towards", "away"))
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left", "cw": "ccw", "ccw": "cw",
            "outward": "inward", "inward": "outward", "towards": "away", "away": "towards"}
#: a picture's direction from its words: (dir, weight, pattern). An explicit direction word outweighs a subject.
WORDS: tuple[tuple[str, float, str], ...] = (
    # the camera travelling is the strongest read; then a motion verb; then a subject that usually moves that way
    ("inward", 3.0, r"\b(zoom(s|ing)? in(to)?|endless zoom|infinite zoom|fly[- ]?through|fl(y|ies|ying|ight) "
                    r"(through|into|between|down|along)|fast flight|rush(es|ing)? (toward|towards|at|past)|"
                    r"hurtl\w+|into the (screen|centre|center|distance))\b"),
    ("inward", 2.0, r"\b(inwards?|converg\w+|collaps\w+|reced\w+|flight|flying|zoom\w*)\b"),
    ("inward", 1.0, r"\b(tunnel\w*|corridor\w*|wormhole\w*|hyperspace|warp (speed|tunnel)|vortex|funnel\w*|"
                    r"portal\w*)\b"),
    ("outward", 3.0, r"\b(zoom(s|ing)? out|outwards?|from the (centre|center))\b"),
    ("outward", 2.0, r"\b(expand\w*|burst\w*|explo\w+|radiat\w+|emanat\w+|shockwave\w*|spray\w*|"
                     r"rings? (grow|expand)\w*)\b"),
    ("outward", 1.0, r"\b(ripple\w*|firework\w*|bloom\w*|spread\w*)\b"),
    ("ccw", 3.0, r"\b(counter[- ]?clockwise|anti[- ]?clockwise)\b"),
    ("cw", 3.0, r"\bclockwise\b"),
    ("cw", 2.0, r"\b(rotat\w+|spin(s|ning)?|swirl(s|ing)?|twirl\w*|orbit(s|ing)?|whirl\w*|revolv\w+|turn(s|ing)|"
                r"circling|in a circle|tumbl\w+|gyrat\w+)\b"),
    ("cw", 1.0, r"\b(spiral\w*|kaleido\w*|pinwheel\w*|twist\w*|swirl|orbital)\b"),
    ("up", 3.0, r"\b(upwards?|lick(s|ing)? up|float(s|ing)? up)\b"),
    ("up", 2.0, r"\b(rising|rises?|ascend\w*|climb\w*)\b"),
    ("up", 1.0, r"\b(flames?|fire|bubbl\w+|smoke)\b"),
    ("down", 3.0, r"\b(downwards?)\b"),
    ("down", 2.0, r"\b(falling|falls?|descend\w*|sink(s|ing)?|pour\w*|cascad\w+|drip\w*)\b"),
    ("down", 1.0, r"\b(rain\w*|snow\w*|waterfall\w*|drizzle)\b"),
    ("left", 3.0, r"\b(leftwards?|to the left|right[- ]to[- ]left)\b"),
    ("right", 3.0, r"\b(rightwards?|to the right|left[- ]to[- ]right)\b"),
    ("right", 2.0, r"\b(scroll\w*|sideways|slid(e|es|ing) (across|past)|pann?ing|march\w*|conveyor\w*|"
                   r"drift(s|ing)? across|sweep(s|ing)? across)\b"),
    ("right", 1.0, r"\b(waveform\w*|oscilloscope|horizontal\w*)\b"),
)
_WORDS = tuple((d, w, re.compile(p, re.I)) for d, w, p in WORDS)
MIN_SCORE = 1.0                 # a direction needs this much to be read at all
#: ``prefer``'s factors (starting points)
DIR_FIT = 1.8                   # the light scene's flow tags name the picture's direction (or it honours the flow)
DIR_AGAINST = 0.6               # they name only its opposite
PACE_FAR = 0.5                  # the scene's energy band is a whole scale away from the picture's pace
BUSY_HIGH, BUSY_LOW = 0.7, 0.3  # a busy picture / a sparse one (busy_pct)
BUSY_ON_BUSY = 0.6              # busy movement over a busy picture
BUSY_ON_SPARSE = 1.3            # busy movement over a sparse picture: the lights carry it
#: how busy a position layer's movement is, by its mode (docs/LIGHTS.md §5.3 words; a mode not listed is 0.5)
MODE_BUSY = {"aim": 0.0, "drift": 0.1, "fan": 0.2, "lift": 0.2, "sweep": 0.5, "circle": 0.5, "tilt_wave": 0.5,
             "figure8": 0.8, "bounce": 0.8, "ballyhoo": 1.0}


def _num01(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
        return min(1.0, max(0.0, float(v)))
    return None


def read_dir(*texts: Any) -> tuple[Optional[str], list[str]]:
    """The direction a picture's words describe, and the words that said so: the dir with the highest score at
    MIN_SCORE or more (the first listed on a tie); (None, []) when nothing does."""
    scores: dict[str, float] = {}
    said: dict[str, list[str]] = {}
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        t = text.replace("-", " ").replace("_", " ")
        for d, w, rx in _WORDS:
            for m in rx.finditer(t):
                scores[d] = scores.get(d, 0.0) + w
                said.setdefault(d, []).append(m.group(0).lower())
    if not scores:
        return None, []
    best = max(scores, key=lambda d: (scores[d], -[x[0] for x in WORDS].index(d)))
    if scores[best] < MIN_SCORE:
        return None, []
    return best, sorted(set(said[best]))[:4]


def build(tags: Any, stats: Any) -> dict:
    """The facts file's body from the renderer's tags.generated.json and look_stats.generated.json (either may be
    None or lack a scene: what is known is written)."""
    looks = ((tags or {}).get("presets") or {}).get("scene") or {} if isinstance(tags, Mapping) else {}
    nums = (stats or {}).get("scenes") or {} if isinstance(stats, Mapping) else {}
    out: dict[str, dict] = {}
    for name in sorted(set(looks) | set(nums)):
        lk = looks.get(name) if isinstance(looks.get(name), Mapping) else {}
        st = nums.get(name) if isinstance(nums.get(name), Mapping) else {}
        d, said = read_dir(lk.get("_look"), name, st.get("plugin"))
        out[name] = {"dir": d, "pace": _num01(st.get("motion_pct")), "busy": _num01(st.get("busy_pct")),
                     "bright": _num01(st.get("brightness_pct")), "said": said}
    return {"format": 1, "gen": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "scenes": out}


class Facts:
    """The loaded facts (the generated file under the hand file), re-read when either changes (``poll``)."""

    def __init__(self, path: pathlib.Path = FACTS_FILE, hand: pathlib.Path = HAND_FILE) -> None:
        self.path, self.hand = pathlib.Path(path), pathlib.Path(hand)
        self.scenes: dict[str, dict] = {}
        self.error: Optional[str] = None
        self._key: Optional[tuple] = None

    def poll(self) -> bool:
        key = tuple(p.stat().st_mtime_ns if p.exists() else None for p in (self.path, self.hand))
        if key == self._key:
            return False
        self._key = key
        scenes: dict[str, dict] = {}
        err = None
        try:
            if self.path.exists():
                got = json.loads(self.path.read_text()).get("scenes") or {}
                scenes = {str(k): dict(v) for k, v in got.items() if isinstance(v, Mapping)}
        except (OSError, ValueError, AttributeError) as e:
            err = f"{self.path.name}: {type(e).__name__}: {e}"
        try:
            if self.hand.exists():
                import yaml
                got = (yaml.safe_load(self.hand.read_text()) or {}).get("scenes") or {}
                for k, v in got.items():
                    if isinstance(v, Mapping):
                        scenes[str(k)] = {**scenes.get(str(k), {}), **dict(v), "hand": True}
        except Exception as e:                            # noqa: BLE001 - a bad hand file keeps the generated facts
            err = f"{self.hand.name}: {type(e).__name__}: {e}"
        self.scenes, self.error = scenes, err
        return True

    def of(self, name: Any) -> Optional[dict]:
        """A scene's facts: {name, dir, pace, busy, bright, sure}. ``sure``: its dir came from the files (1.0) or from
        its name alone (0.6). None for no name."""
        if not isinstance(name, str) or not name:
            return None
        f = self.scenes.get(name)
        if f is not None:
            d = f.get("dir")
            if d is False:                                # YAML's bare off: no direction
                d = None
            return {"name": name, "dir": d if d in FLOW_DIRS and d != FLOW_DIRS[0] else None,
                    "pace": _num01(f.get("pace")), "busy": _num01(f.get("busy")), "bright": _num01(f.get("bright")),
                    "sure": 1.0}
        d, _ = read_dir(name)
        return {"name": name, "dir": d, "pace": None, "busy": None, "bright": None, "sure": 0.6}


def movement_busy(scene: Mapping[str, Any]) -> Optional[float]:
    """How busy a light scene's movement is, 0..1: its busiest position layer's mode (MODE_BUSY); None: it has none."""
    best = None
    for l in scene.get("layers") or ():
        if isinstance(l, Mapping) and l.get("kind") == "position":
            b = MODE_BUSY.get(str(l.get("mode")), 0.5)
            best = b if best is None else max(best, b)
    return best


def prefer(scenes: Mapping[str, Mapping[str, Any]], pic: Optional[Mapping[str, Any]]) -> dict[str, float]:
    """{light scene: factor} for the picture ``pic`` (``Facts.of``): only the scenes that move (a position layer),
    and only factors that aren't 1. {} with no picture."""
    if not isinstance(pic, Mapping):
        return {}
    out: dict[str, float] = {}
    d, pace, busy = pic.get("dir"), pic.get("pace"), pic.get("busy")
    for name, sc in scenes.items():
        mb = movement_busy(sc)
        if mb is None:
            continue
        f = 1.0
        tags = sc.get("tags") if isinstance(sc.get("tags"), Mapping) else {}
        fl = tags.get("flow") if isinstance(tags.get("flow"), Mapping) else {}
        dirs = set(fl.get("dirs") or ())
        if d:
            if d in dirs or fl.get("honours"):
                f *= DIR_FIT
            elif OPPOSITE.get(d) in dirs:
                f *= DIR_AGAINST
        band = tags.get("energy")
        if pace is not None and isinstance(band, (list, tuple)) and len(band) == 2:
            mid = (float(band[0]) + float(band[1])) / 2.0
            f *= 1.0 - (1.0 - PACE_FAR) * min(1.0, abs(mid - pace))
        if busy is not None and mb >= 0.7:
            f *= BUSY_ON_BUSY if busy >= BUSY_HIGH else BUSY_ON_SPARSE if busy <= BUSY_LOW else 1.0
        if abs(f - 1.0) > 1e-6:
            out[str(name)] = round(f, 4)
    return out
