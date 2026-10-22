"""Room-level direction (owner, 15 Oct 2026): the screens in one room share one art direction.

One session per room runs the director, the leader (the oldest live one; it keeps the role while it stays live).
The room's other sessions follow it:
  * scenes: the leader's next pick is prepared on every screen ahead of time and the leader cuts only when they
    are ready (at most ``READY_S``), so all the screens change together, on the same beat;
  * effects: the followers' auto FX bus is kept equal to the leader's (same presets, same order);
  * direction: the leader's effective direction (the panel trimmed by the music) is theirs;
  * a look PATCH (direction, auto, palette...) on any screen in the room applies to all of them, and a new session
    joining the room takes the room's look.
Each screen keeps its own fixture, so Maestro's per-screen colour, pulse and pan/tilt/zoom still drive it alone,
and its own GPU fit (render_scale, frame rate).  Manual cues (a Library push, a PATCH scene) stay on their screen.

Membership: a session with a room (the create body's ``room``, else its screen's room in routes/data/rooms.json)
and an auto block, not exited.  One session per screen counts (the newest).

The ensemble (docs/ENSEMBLE.md §8; owner, 22 Oct: "more of a conversation between room elements"): show_service's
room.direction.ensemble gives each screen a part.  The leader still picks and still prepares its pick on every screen,
but each screen then cuts at the room's moment + its own stagger (``stagger_s``): the leader passes its base photon
time (before its own stagger) to the followers.  Not staggered: a song change (fade-dark, segue: together), a cue a
person made, a scene's failure, a transition with ``sweep_beats``.  A screen whose part has role ``lead`` is the one
whose auto effects the others mirror (``fx_source``); a mirror takes the first round(n x fx_density) of them
(ensemble.fx_trim).  No ensemble, or every stagger 0: exactly the unison above.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
from typing import Any, Optional

from . import ensemble

log = logging.getLogger("stream_service.rooms")

REPO = pathlib.Path(__file__).resolve().parent.parent
ROOMS_FILE = REPO / "routes" / "data" / "rooms.json"
ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LIVE_STATES = ("entering", "live")
READY_S = 3.0              # the leader waits at most this long for the followers' copies of its pick
FX_SYNC_S = 2.0            # a follower checks its auto FX against the leader's this often
UNSTAGGERED = ("track", "next", "plugin_error")   # auto cue reasons that cut together: a song change (fade-dark and
                                                  # segue go together), a person's Next, a failed scene's replacement


class RoomMap:
    """screen -> room, from rooms.json (re-read when it changes)."""

    def __init__(self, path: Any = ROOMS_FILE):
        self.path = pathlib.Path(path)
        self._mtime: Optional[float] = None
        self._map: dict[str, str] = {}

    def room_of(self, screen: str) -> Optional[str]:
        try:
            m = self.path.stat().st_mtime
        except OSError:
            return self._map.get(screen)
        if m != self._mtime:
            try:
                data = json.loads(self.path.read_text())
                self._map = {scr: rid for rid, r in data.items() if isinstance(r, dict) and ROOM_RE.match(rid)
                             for scr in (r.get("screens") or []) if isinstance(scr, str)}
            except (OSError, ValueError) as e:
                log.warning("rooms: %s unreadable (%s): keeping the last map", self.path, e)
            self._mtime = m
        return self._map.get(screen)


def members(sessions: Any, room: Optional[str]) -> list:
    """The sessions taking part in a room's direction, oldest first (one per screen: the newest)."""
    if not room:
        return []
    by_screen: dict[str, Any] = {}
    for s in sessions:
        if s.room != room or s.state in ("ended", "failed") or s.exited or s.director.auto is None:
            continue
        o = by_screen.get(s.screen)
        if o is None or s.created_at > o.created_at:
            by_screen[s.screen] = s
    return sorted(by_screen.values(), key=lambda s: (s.created_at, s.id))


def pick_leader(ms: list, current: Optional[str]) -> Any:
    """The current leader while it is still a live member, else the oldest live member, else the oldest."""
    live = [s for s in ms if s.state in LIVE_STATES]
    for s in live:
        if s.id == current:
            return s
    return (live or ms or [None])[0]


def look_of(s: Any) -> dict:
    """A session's look as Session(...) keyword arguments (a new member takes the room's look)."""
    import copy
    return dict(auto=copy.deepcopy(s.director.auto), direction=copy.deepcopy(s.direction), palette=s.palette,
                palette_s=s.palette_s, beat_follow=s.beat_follow, direction_ease_s=s.direction_ease_s)


def auto_fx(stack: list) -> list[tuple[str, Optional[str]]]:
    """A FX bus's auto slots as (slot id, preset), in order."""
    return [(st.id, st.spec.preset) for st in stack if getattr(st, "origin", None) == "auto"]


def fx_step(have: list[tuple[str, Optional[str]]], want: list[Optional[str]]) -> tuple[Optional[str], Optional[str]]:
    """One move from a follower's auto effects towards the leader's: (preset to add, slot id to remove)."""
    rest = list(want)
    extra = None
    for sid, preset in have:
        if preset in rest:
            rest.remove(preset)
        elif extra is None:
            extra = sid
    return (rest[0] if rest else None), extra


def fx_source(me: Any, leader: Any, ms: list, leads: Any = ()) -> Any:
    """The session whose auto effects ``me`` mirrors, or None when it directs its own: the room's lead screen (an
    ensemble part with role ``lead``, in ``leads``) when one doesn't set its own effects; else the leader, or, when
    the leader set its own, the first member that didn't."""
    heads = [m for m in ms if "fx" not in m.own]
    lead = next((m for m in heads if m.screen in leads), None) if leads else None
    src = lead if lead is not None else leader if "fx" not in leader.own else heads[0] if heads else None
    return None if src is None or src is me else src


def stagger_s(ens: Optional[dict], screen: str, beat_s: Optional[float], at_mono: float) -> float:
    """This screen's stagger (s) for a scene change whose room moment reaches the glass at ``at_mono`` (photon time,
    monotonic s): its part in effect then (current or next); stagger_beats x ``beat_s`` while the renderer's clock
    is sure (SessionManager.music_grid), else its stagger_s; 0 without an ensemble."""
    return ensemble.stagger_s(ens, screen, beat_s, at_mono * 1000.0)


def room_body(spec: Any) -> Any:
    """The leader's scene as a spec this screen resolves and fits for itself (its own GPU's render_scale)."""
    from .sessions import spec_body
    return {"preset": spec.preset} if spec.preset else spec_body(spec)
