"""accents: light moments, short occasional changes to the room's remote-worked lights at a big moment in the music
(config/room/accents.yaml; owner, 28 Oct: faster glitterball spin and pinspot strobe in Disco, now and then in Party).

Pure apart from reading the file: director.py feeds it the music's events (``on_event``) and ticks it (``tick``) with
the room's view (autopilot.RoomView: every member's believed value), and sends what it returns through room's
PUT /api/v1/room/targets. Nothing here knows a moment, a target or a mode by name.

* **Allowed** by the room's ``accents.<id>`` row (0..100, set by the mode; missing or 0 = never): at an event in the
  moment's ``on`` list, it starts with probability row / 100, if its ``needs`` read as believed, its ``set`` isn't
  already what the room shows, it isn't playing and ``gap_s`` has passed since it ended.
* **Plays** for a random ``hold_s``, then puts each target back to the value the room believed when it started; not
  when the room's mode has changed since (the new mode's own values stand), nor for a target the room no longer
  believes is the moment's value (someone changed it).
* ``held()``: the targets a playing moment holds, so the flow's glitterball motor leaves them alone meanwhile.
"""

from __future__ import annotations

import collections
import logging
import pathlib
import random
from typing import Any, Mapping, Optional

log = logging.getLogger("show_service.accents")

FILE = pathlib.Path(__file__).resolve().parent.parent / "config" / "room" / "accents.yaml"
ROW = "accents"              # the room's subsystem: accents.<id> = 0..100
EVENTS = ("drop", "build", "section_change", "track_change")
RELOAD_S = 2.0


def parse(raw: Any) -> tuple[dict[str, dict], list[str]]:
    """{id: {label, needs, set, on, hold_s, gap_s}} and problem lines; a bad moment is dropped (degrade, never refuse)."""
    out: dict[str, dict] = {}
    probs: list[str] = []
    acc = (raw or {}).get("accents") if isinstance(raw, Mapping) else None
    if not isinstance(acc, Mapping):
        return out, ["accents.yaml: no accents mapping"] if raw is not None else []
    for aid, a in acc.items():
        where = f"accents.{aid}"
        if not isinstance(a, Mapping) or not isinstance(a.get("set"), Mapping) or not a["set"]:
            probs.append(f"{where}: needs a set of room targets")
            continue
        on = [e for e in (a.get("on") or []) if e in EVENTS]
        if not on:
            probs.append(f"{where}.on: none of {list(EVENTS)}")
            continue
        hold = a.get("hold_s", [30, 60])
        if not (isinstance(hold, list) and len(hold) == 2 and all(isinstance(x, (int, float)) for x in hold)
                and 0 < hold[0] <= hold[1]):
            probs.append(f"{where}.hold_s: not [shortest, longest] seconds; 30-60 s")
            hold = [30, 60]
        gap = a.get("gap_s", 180)
        if not isinstance(gap, (int, float)) or gap < 0:
            probs.append(f"{where}.gap_s: not seconds; 180 s")
            gap = 180
        needs = a.get("needs") or {}
        out[str(aid)] = {"label": str(a.get("label") or aid), "needs": dict(needs) if isinstance(needs, Mapping) else {},
                         "set": dict(a["set"]), "on": on, "hold_s": [float(hold[0]), float(hold[1])],
                         "gap_s": float(gap)}
    return out, probs


def load(path: pathlib.Path = FILE) -> tuple[dict[str, dict], list[str]]:
    try:
        import yaml
        return parse(yaml.safe_load(path.read_text()))
    except FileNotFoundError:
        return {}, []
    except Exception as e:
        return {}, [f"accents.yaml: {e}"]


def _believed(room: Any, target: str) -> Any:
    m = ((room or {}).get("members") or {}).get(target) if isinstance(room, Mapping) else None
    return m.get("believed") if isinstance(m, Mapping) else None


class Accents:
    def __init__(self, path: pathlib.Path = FILE, rng: Optional[random.Random] = None) -> None:
        self.path = path
        self.rng = rng or random.Random()
        self.cfg: dict[str, dict] = {}
        self.problems: list[str] = []
        self._stamp: Any = None
        self._checked = -1e9
        self.playing: dict[str, dict] = {}     # id -> {until, before: {target: value}, mode}
        self.ended: dict[str, float] = {}      # id -> when it last ended
        self.room: Any = None                  # the last room view (tick)
        self.log: list[dict] = []              # recent starts and ends, newest last
        self._seen: collections.deque = collections.deque(maxlen=64)   # events already heard (a song change comes twice)

    def poll(self, now_s: float) -> None:
        if now_s - self._checked < RELOAD_S:
            return
        self._checked = now_s
        try:
            st = self.path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        if stamp != self._stamp:
            self._stamp = stamp
            self.cfg, self.problems = load(self.path)
            for p in self.problems:
                log.warning("%s", p)

    def allowance(self, aid: str) -> float:
        v = _believed(self.room, f"{ROW}.{aid}")
        return max(0.0, min(1.0, float(v) / 100.0)) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    def held(self) -> set[str]:
        return {t for p in self.playing.values() for t in p["values"]}

    def on_event(self, kind: str, now_s: float, key: Any = None) -> dict[str, Any]:
        """The targets to PUT for the moments this event starts ({} for none). ``key``: the event's id, heard once."""
        room = self.room
        if not isinstance(room, Mapping) or kind not in EVENTS:
            return {}
        if key is not None:
            if key in self._seen:
                return {}
            self._seen.append(key)
        out: dict[str, Any] = {}
        for aid, a in self.cfg.items():
            if kind not in a["on"] or aid in self.playing or now_s - self.ended.get(aid, -1e9) < a["gap_s"]:
                continue
            if set(a["set"]) & (self.held() | set(out)):
                continue                       # another moment holds one of its targets
            if any(_believed(room, t) != v for t, v in a["needs"].items()):
                continue
            before = {t: _believed(room, t) for t in a["set"]}
            if all(before[t] == v for t, v in a["set"].items()):
                continue                       # the room shows it already
            p = self.allowance(aid)
            if p <= 0.0 or self.rng.random() >= p:
                continue
            hold = self.rng.uniform(*a["hold_s"])
            self.playing[aid] = {"until": now_s + hold, "before": before, "values": dict(a["set"]),
                                 "mode": room.get("current")}
            out.update(a["set"])
            self._note({"start": aid, "on": kind, "hold_s": round(hold, 1), "values": dict(a["set"])})
        return out

    def tick(self, now_s: float, room: Any) -> dict[str, Any]:
        """The targets to PUT back for the moments that end now ({} for none)."""
        self.poll(now_s)
        self.room = room
        out: dict[str, Any] = {}
        for aid in [a for a, p in self.playing.items() if now_s >= p["until"]]:
            p = self.playing.pop(aid)
            self.ended[aid] = now_s
            same_mode = isinstance(room, Mapping) and room.get("current") == p["mode"]
            back = {t: v for t, v in p["before"].items()
                    if same_mode and v is not None and _believed(room, t) == p["values"][t]}
            out.update(back)
            self._note({"end": aid, "back": back, **({} if same_mode else {"mode_changed": True})})
        return out

    def _note(self, rec: dict) -> None:
        self.log.append(rec)
        del self.log[:-20]

    def view(self, now_s: float) -> dict:
        return {"moments": {aid: {"label": a["label"], "allowed": round(self.allowance(aid) * 100),
                                  "playing_s": (round(self.playing[aid]["until"] - now_s, 1)
                                                if aid in self.playing else None)}
                            for aid, a in self.cfg.items()},
                "problems": list(self.problems), "recent": list(self.log[-5:])}
