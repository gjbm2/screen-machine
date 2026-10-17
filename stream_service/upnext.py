"""Up next (owner, 16 Oct 2026): the art director's forward plan, which the owner sees and can edit at any time.

The session that directs a room (its leader, rooms.py; a lone session directs itself) keeps a short queue of the
scenes it will play next, each with the transition it comes in by, and of the effects it will bring in next.

  * The director fills the queue (``origin: "auto"``) up to SCENES_AHEAD / EFFECTS_AHEAD and re-plans its own
    entries when the direction moves enough to change what it would pick (the session's pick key), or when the
    pool changes.  The owner's entries (``origin: "you"``: added, edited or kept by hand) are never re-picked; a
    transition the director chose for one of them is (``transition_origin``).
  * When a scene's dwell is up the head plays (prepared PREPARE_AHEAD_S before, so an edit up to then is honoured);
    Next plays the head now; a scene pushed from the Library plays at once and leaves the queue as it is.
  * When the FX director adds or swaps an effect it takes the first queued effect that fits the bus then (stage,
    cost budget) and drops it from the queue; a remove leaves the queue alone; Next effect does the same as a move.
  * ETAs are estimates: a scene's from the dwell window (a section change on the bus can come sooner), an effect's
    from the FX director's cadence.

Pure state on the loop thread: sessions.py picks, prepares and plays.
"""
from __future__ import annotations

from typing import Any, Optional

from .tags import genre_fit

SCENES_AHEAD = 4
EFFECTS_AHEAD = 3
KINDS = ("scene", "effect")


def why_words(kind: str, tags: dict, direction: dict, rated: float = 1.0, stage: Optional[str] = None) -> str:
    """Why the director queued it, in plain words for the pages ("Fits calm, dreamy · you rated it 4★")."""
    tags = tags or {}
    want = list(direction.get("moods") or [])
    if kind == "effect":
        want = list((direction.get("fx") or {}).get("moods") or []) or want
    have = tags.get("moods") or []
    have = [have] if isinstance(have, str) else list(have)
    out = []
    hit = [m for m in have if m in set(want)]
    if hit:
        out.append("fits " + ", ".join(hit[:2]))
    g = direction.get("genres") or direction.get("genre")
    fit = genre_fit(g, tags)
    if fit is not None and fit >= 0.5:
        out.append(f"fits {direction.get('genre') or (next(iter(g)) if isinstance(g, dict) else g)}")
    e, rng = direction.get("energy"), tags.get("energy")
    if isinstance(e, (int, float)) and isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[0] <= e <= rng[1]:
        if e <= 0.35:
            out.append("suits the calm")
        elif e >= 0.65:
            out.append("suits the energy")
    if rated >= 8.0:
        out.append("you rated it 5★")
    elif rated >= 4.0:
        out.append("you rated it 4★")
    if kind == "effect" and stage:
        out.insert(0, f"a {stage} effect")
    if not out or out == [f"a {stage} effect"]:
        out.append("for variety")
    s = " · ".join(out)
    return s[:1].upper() + s[1:]


class UpNext:
    def __init__(self) -> None:
        self.scenes: list[dict] = []     # {id, scene, transition, origin, transition_origin, why}
        self.effects: list[dict] = []    # {id, effect, origin, why}
        self.rev = 0
        self._n = 0

    # -- reading --------------------------------------------------------------------------------------------------
    def items(self, kind: str) -> list[dict]:
        return self.scenes if kind == "scene" else self.effects

    def find(self, qid: Any) -> tuple[Optional[str], int]:
        for kind in KINDS:
            for i, e in enumerate(self.items(kind)):
                if e["id"] == qid:
                    return kind, i
        return None, -1

    def head(self, kind: str) -> Optional[dict]:
        q = self.items(kind)
        return q[0] if q else None

    # -- changing -------------------------------------------------------------------------------------------------
    def changed(self) -> None:
        self.rev += 1

    def _id(self, kind: str) -> str:
        self._n += 1
        return f"{'s' if kind == 'scene' else 'e'}{self._n}"

    def add(self, kind: str, entry: dict, at: Optional[int] = None) -> dict:
        q = self.items(kind)
        e = {"id": self._id(kind), **entry}
        q.insert(len(q) if at is None else max(0, min(len(q), int(at))), e)
        self.changed()
        return e

    def remove(self, qid: Any) -> Optional[dict]:
        kind, i = self.find(qid)
        if kind is None:
            return None
        self.changed()
        return self.items(kind).pop(i)

    def move(self, qid: Any, to: int) -> bool:
        kind, i = self.find(qid)
        if kind is None:
            return False
        q = self.items(kind)
        e = q.pop(i)
        q.insert(max(0, min(len(q), int(to))), e)
        self.changed()
        return True

    def drop_auto(self, kind: str) -> int:
        """The director's own entries go (to be picked again); the owner's stay, their auto transitions cleared."""
        q = self.items(kind)
        keep = [e for e in q if e.get("origin") == "you" or e.get("hold")]    # hold: picked for a moment (a drop,
        # a song change): a re-plan keeps it
        n = len(q) - len(keep)
        for e in keep:
            if kind == "scene" and e.get("transition_origin") == "auto":
                e["transition"], e["transition_origin"] = None, None
                n += 1
        if n:
            q[:] = keep
            self.changed()
        return n

    def clear(self) -> None:
        if self.scenes or self.effects:
            self.scenes, self.effects = [], []
            self.changed()

    # -- the view (GET session, session.upnext) --------------------------------------------------------------------
    def view(self, scene_eta: Optional[tuple[float, float]], window: tuple[float, float],
             fx_next_in: Optional[float], fx_interval: float) -> dict:
        """``scene_eta``: (eligible, max) seconds until the head may / must play (None: not scheduled);
        ``window``: one scene's dwell (lo, hi); ``fx_next_in``: seconds to the FX director's next move."""
        scenes = []
        for i, e in enumerate(self.scenes):
            eta = None if scene_eta is None else \
                [round(scene_eta[0] + i * window[0], 1), round(scene_eta[1] + i * window[1], 1)]
            scenes.append({**e, "eta_s": eta})
        effects = []
        for i, e in enumerate(self.effects):
            effects.append({**e, "eta_s": None if fx_next_in is None else round(fx_next_in + i * fx_interval, 1)})
        return {"rev": self.rev, "scenes": scenes, "effects": effects}
