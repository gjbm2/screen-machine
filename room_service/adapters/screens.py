"""screens.<id>: a thin shim around ScreenController (ROOM_STATE_MODEL §5.4 screens).

Values: "art" or {"live": {scene, transitions, params?, profile?}}; read-only "failed" while the controller has given up
on a live request. The cut handshake stays inside the controller; this only sets desired values and reports glass.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ..roomstate.contract import Adapter, Outcome, Reading
from ..live_art import scene_tag_list
from ..screens import LiveDisabled, desired_label, normalize_desired

if TYPE_CHECKING:
    from ..screens import ScreenController


class ScreensAdapter(Adapter):
    subsystem = "screens"
    subsystem_label = "Screens"
    kind = "level"
    change_time_s = 40.0
    poll_s = 1.0
    poll_inflight_s = 0.5

    def __init__(self, controllers: dict[str, "ScreenController"]) -> None:
        self.c = controllers
        self.members = list(controllers)

    def value_schema(self, member: str) -> dict:
        """The Room page's screen editor: "art" or a live spec. A mode never names a scene (owner, 15 Oct): it steers
        the art director by `direction` (descriptors from the renderer's catalog.vocab) and, for a closed set
        only, `scene_tags`. Built from the cached catalog; the vocab lists are empty until it has been read."""
        vocab: dict = {}
        vocab_labels: dict = {}
        tag_list: list[dict] = []
        transitions: list[tuple[str, str]] = []
        c = next(iter(self.c.values()), None)
        cat = c.mgr.stream.cached_catalog() if c is not None else None
        if isinstance(cat, dict):
            vocab = cat.get("vocab") or {}
            vocab_labels = cat.get("vocab_labels") or {}
            tag_list = scene_tag_list(cat)
            tp = ((cat.get("presets") or {}).get("transitions")) or {}
            transitions = sorted(((n, str((p or {}).get("label") or n)) for n, p in tp.items()), key=lambda x: x[1].lower())
        unit = lambda label: {"type": ["number", "null"], "minimum": 0, "maximum": 1,      # noqa: E731
                              "x-ui": {"label": label, "widget": "slider", "ends": {"min": "Low", "max": "High"}}}
        choice = lambda label, key: {"type": ["string", "null"], "enum": [*(vocab.get(key) or []), None],  # noqa: E731
                                     "x-ui": {"label": label}}
        direction = {
            "type": "object",
            "x-ui": {"label": "Art direction", "unset_label": "Neutral (the music decides)",
                     "help": "Unset = neutral (the music decides)."},
            "properties": {
                "energy": unit("Energy"), "pace": unit("Pace"), "intensity": unit("Intensity"),
                "change_rate": unit("How often it changes"),
                "moods": {"type": "array", "items": {"type": "string", "enum": list(vocab.get("moods") or [])},
                          "uniqueItems": True, "x-ui": {"label": "Moods"}},
                "genre": choice("Genre", "genre"), "transition_style": choice("Transitions", "transition_style"),
                "palette_family": choice("Palette", "palette_family"),
                "fx": {"type": "object", "x-ui": {"label": "Effects"},
                       "properties": {"density": unit("How many"), "rate": unit("How often")}},
            },
        }
        if vocab.get("music"):                    # how the heard music combines with this look: the renderer's words
            labels = vocab_labels.get("music") if isinstance(vocab_labels.get("music"), dict) else {}
            direction["properties"]["music"] = {
                "type": ["string", "null"], "enum": [*vocab["music"], None],
                "x-ui": {"label": "How the music changes it", "unset_label": "The usual",
                         **({"labels": labels} if labels else {})}}
        via = lambda label: {"type": ["string", "null"], "enum": [n for n, _ in transitions] + [None],   # noqa: E731
                             "x-ui": {"label": label, "enum_labels": [lb for _, lb in transitions] + ["The usual"],
                                      "unset_label": "The usual"}}
        return {"x-ui": {"label": member, "widget": "screen"},
                "oneOf": [{"const": "art", "x-ui": {"label": "Art"}},
                          {"type": "object", "default": {"live": {"auto": True}},
                           "x-ui": {"label": "Live", "help": "The Art Director picks the scenes, effects and transitions."},
                           "required": ["live"], "properties": {"live": {
                              "type": "object", "properties": {
                                  "direction": direction,
                                  "scene_tags": {"type": "array", "uniqueItems": True,
                                                 "items": {"type": "string"},   # open: any tag word
                                                 # matching: a word = any facet, facet:word = that facet, any-of
                                                 "x-ui": {"label": "Only use these scenes", "widget": "tags",
                                                          "tags": tag_list,
                                                          "help": "The Art Director only picks scenes with any of "
                                                                  "these tags."}},
                                  # always on for a mode (a mode never names a scene): kept for validation, not shown
                                  "auto": {"type": "boolean", "x-ui": {"label": "Art director", "hidden": True}},
                                  "transitions": {"type": "object", "x-ui": {"label": "Cut to and from art"},
                                                  "properties": {"in": via("Into live"), "out": via("Back to art")}},
                              }}}}]}

    def normalize(self, member: str, value: Any) -> Any:
        return normalize_desired(value)

    def show(self, member: str, value: Any) -> str:
        return value if value in ("art", "failed") else desired_label(value)

    def words(self, member: str, value: Any) -> str:
        if value in ("art", "failed"):
            return {"art": "Art", "failed": "Live failed (showing art)"}[value]
        tags = (value.get("live") or {}).get("scene_tags") if isinstance(value, dict) else None
        return f"Live ({', '.join(tags)})" if tags else "Live"

    def _glass(self, c: "ScreenController") -> Reading:
        moving = c._handshake is not None or c._adopt is not None
        if c.phase == "failed" and c.desired != "art":
            return Reading("failed", moving=False)               # R12: on glass that is art, but it isn't "art"
        if c.layer == "live" and c.session:
            live = c.session.get("live")
            if c.desired != "art" and c.desired.get("live") == live:
                return Reading(c.desired, moving=moving)
            return Reading(normalize_desired({"live": live}) if live else c.desired, moving=moving)
        return Reading("art", moving=moving or c.desired != "art")

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {m: self._glass(self.c[m]) for m in members}

    async def _one(self, m: str, value: Any, source: str) -> Outcome:
        c = self.c[m]
        try:
            c.set_desired(value, source=source)
        except LiveDisabled:
            return Outcome("failed", "live_disabled", retry=False)
        except ValueError as e:
            return Outcome("failed", str(e))
        status, detail = await c.wait_settled(value, self.change_time_s)
        if status == "cancelled":
            return Outcome("unconfirmed", "desired changed meanwhile")
        if status == "failed":
            return Outcome("failed", detail, retry=False)       # the controller gave up: retrying defeats its guard
        return Outcome(status, detail)

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        source = "mode"
        names = list(desired)
        outs = await asyncio.gather(*(self._one(m, desired[m], source) for m in names))
        members = dict(zip(names, outs))
        rank = {"ok": 0, "unconfirmed": 1, "failed": 2}
        worst = max(outs, key=lambda o: rank[o.status]) if outs else Outcome("ok")
        return Outcome(worst.status, "; ".join(f"{m}: {o.detail}" for m, o in members.items() if o.status != "ok"),
                       members, retry=all(o.retry for o in outs))
