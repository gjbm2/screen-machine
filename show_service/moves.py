"""moves: a change of scene, composed across the layers (owner, 26 Nov 2026).

"there should be one controlling visual logic -- the a.d. has to have dominion and make a reasoned set of judgments
about the total experience ... e.g. chorus ends, lights fly up, screen transitions, brief strobe, and bang we're back
in verse scene etc. it should _feel_ the music and express it across all the layers in an integrated way."

Every layer already changes scene on the art director's one cue (its look_change; README "One controlling logic"). A
**move** is what that cue IS: one entry of config/live/moves.yaml that gives each layer its part around the line, so
the lights' transition, the screens' transition and the accents are chosen together, not drawn apart:

  into, from   the kind of section the change goes into / comes from (SECTION_KINDS words, or any): a karaoke song's
               lyric plan names them; without one only ``any`` moves fit
  reasons      the look changes it is for (the director's own words: section, repeat, dwell, track, next, drop, ...)
  size         small | medium | big: a big move spends one of the feel's big moments (peak_tokens_per_hour) and is
               left out when none is left
  lights       {transition: <light_transitions.yaml name>, accents: [{effect: <light_effects.yaml name>, at_beats: n}]}:
               the lights' way across (its gesture leads into the line), and effects fired n beats from the line
  screen       {via: [<a renderer transition preset, or a tag word / facet:word of its tags>, ...]}: the screens'
               transition is drawn from the ones that match (the renderer's own weights; none matching: its own pick)
  land         0..1: where the line falls in the screens' transition (1: the new scene is fully there on the line)
  tags         energy, transition_style, weight: scored as every transition is (stream_service/tags.py)

A move with no ``lights`` or ``screen`` part leaves that layer to its own draw. Pure but for the file; nothing here
knows a transition, an effect or a section by anything but the catalogue's and the contract's words.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any, Callable, Mapping, Optional

from . import contract

log = logging.getLogger(__name__)

MOVES_FILE = contract.REPO_ROOT / "config" / "live" / "moves.yaml"
SECTION_KINDS: tuple[str, ...] = ("verse", "chorus", "final_chorus", "instrumental", "outro", "any")
SIZES: tuple[str, ...] = ("small", "medium", "big")
KEYS = frozenset({"label", "into", "from", "reasons", "size", "lights", "screen", "land", "tags"})
DEFAULT_REASONS: tuple[str, ...] = ("section", "repeat")
SPECIFIC = 2.0                  # a move made for this very boundary (its into / from names it) over an ``any`` one


def _words(v: Any, allowed: Optional[tuple[str, ...]], where: str, probs: list[str], default: list[str]) -> list[str]:
    if v is None:
        return list(default)
    v = [v] if isinstance(v, str) else v
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        probs.append(f"{where}: isn't a list of words: {', '.join(default)}")
        return list(default)
    if allowed is not None:
        bad = [x for x in v if x not in allowed]
        if bad:
            probs.append(f"{where}: {', '.join(bad)} isn't one of {', '.join(allowed)}: ignored")
        v = [x for x in v if x in allowed]
    return v or list(default)


def parse(raw: Any, probs: list[str]) -> dict[str, dict]:
    """moves.yaml's mapping -> {name: move}, bad entries left out with a problem line."""
    out: dict[str, dict] = {}
    moves = raw.get("moves") if isinstance(raw, Mapping) else None
    if not isinstance(moves, Mapping):
        if raw:
            probs.append("moves.yaml: needs moves: {<name>: {...}}")
        return out
    for name, m in moves.items():
        w = f"moves.{name}"
        if not isinstance(m, Mapping):
            probs.append(f"{w}: isn't a mapping: left out")
            continue
        for k in m:
            if k not in KEYS:
                probs.append(f"{w}.{k}: not a move's key ({', '.join(sorted(KEYS))}): ignored")
        size = m.get("size", "small")
        if size not in SIZES:
            probs.append(f"{w}.size: {size!r} isn't {', '.join(SIZES)}: small")
            size = "small"
        e: dict[str, Any] = {"label": str(m.get("label") or name),
                             "into": _words(m.get("into"), SECTION_KINDS, f"{w}.into", probs, ["any"]),
                             "from": _words(m.get("from"), SECTION_KINDS, f"{w}.from", probs, ["any"]),
                             "reasons": _words(m.get("reasons"), None, f"{w}.reasons", probs, list(DEFAULT_REASONS)),
                             "size": size, "lights": None, "screen": None, "land": None,
                             "tags": dict(m["tags"]) if isinstance(m.get("tags"), Mapping) else {}}
        li = m.get("lights")
        if li is not None:
            if not isinstance(li, Mapping):
                probs.append(f"{w}.lights: isn't a mapping: the lights draw their own")
            else:
                acc = []
                for i, a in enumerate(li.get("accents") or []):
                    at = a.get("at_beats", 0.0) if isinstance(a, Mapping) else None
                    if not isinstance(a, Mapping) or not isinstance(a.get("effect"), str) \
                            or isinstance(at, bool) or not isinstance(at, (int, float)):
                        probs.append(f"{w}.lights.accents[{i}]: needs {{effect: <name>, at_beats: n}}: left out")
                        continue
                    acc.append({"effect": a["effect"], "at_beats": float(at)})
                tr = li.get("transition")
                if tr is not None and not isinstance(tr, str):
                    probs.append(f"{w}.lights.transition: isn't a light transition's name: the lights draw their own")
                    tr = None
                e["lights"] = {"transition": tr, "accents": acc}
        sc = m.get("screen")
        if sc is not None:
            via = sc.get("via") if isinstance(sc, Mapping) else None
            via = [via] if isinstance(via, str) else via
            if not isinstance(via, list) or not via or not all(isinstance(x, str) and x for x in via):
                probs.append(f"{w}.screen: needs via: [<transition or tag>, ...]: the screens draw their own")
            else:
                e["screen"] = {"via": list(via)}
        la = m.get("land")
        if la is not None:
            if isinstance(la, bool) or not isinstance(la, (int, float)) or not 0.0 <= la <= 1.0:
                probs.append(f"{w}.land: {la!r} isn't 0..1: the director's own")
            else:
                e["land"] = float(la)
        out[str(name)] = e
    return out


class Moves:
    """The loaded catalogue, re-read when the file changes (``poll``; a bad edit keeps the last good moves)."""

    def __init__(self, path: pathlib.Path = MOVES_FILE) -> None:
        self.path = pathlib.Path(path)
        self.moves: dict[str, dict] = {}
        self.problems: list[str] = []
        self.error: Optional[str] = None
        self._sig: Any = ()

    def poll(self) -> bool:
        sig = self.path.stat().st_mtime_ns if self.path.exists() else None
        if sig == self._sig:
            return False
        self._sig = sig
        if sig is None:
            self.moves, self.problems, self.error = {}, [], None
            return True
        try:
            import yaml
            probs: list[str] = []
            self.moves, self.problems, self.error = parse(yaml.safe_load(self.path.read_text()) or {}, probs), probs, \
                None
        except Exception as e:                            # noqa: BLE001 - keep the last good moves
            self.error = f"{self.path.name}: {type(e).__name__}: {e}"
            log.warning("moves: %s (keeping the last good ones)", self.error)
        return True

    def pick(self, reason: str, into: Optional[str], frm: Optional[str], rng: Any, *,
             weight: Callable[[Mapping[str, Any]], float], big_ok: bool = True,
             usable: Optional[Callable[[dict], bool]] = None) -> Optional[tuple[str, str]]:
        """A move for a look change of ``reason`` going ``frm`` -> ``into`` (section kinds, None: unknown): a weighted
        draw over the moves whose reasons, into and from fit (``any`` fits all), x SPECIFIC for each of into / from
        that names the boundary itself, x ``weight(tags)``; never a big one unless ``big_ok``; ``usable(move)`` False
        leaves one out (a part this rig can't play). (name, why) or None."""
        ws: dict[str, float] = {}
        for name, m in self.moves.items():
            if reason not in m["reasons"] or (m["size"] == "big" and not big_ok):
                continue
            f = 1.0
            ok = True
            for kind, have in ((into, m["into"]), (frm, m["from"])):
                if kind is not None and kind in have:
                    f *= SPECIFIC
                elif "any" not in have:
                    ok = False
            if not ok or (usable is not None and not usable(m)):
                continue
            try:
                f *= max(0.0, float(weight(m["tags"])))
            except Exception:                             # noqa: BLE001 - an odd tag: its plain weight
                f *= float(m["tags"].get("weight", 1.0)) if isinstance(m["tags"].get("weight"), (int, float)) else 1.0
            if f > 0:
                ws[name] = f
        if not ws:
            return None
        total = sum(ws.values())
        x = rng.random() * total
        for name, f in ws.items():
            x -= f
            if x <= 0:
                break
        m = self.moves[name]
        where = " -> ".join(k for k in (frm, into) if k) or reason
        return name, f"{m['label']} ({where}; p {ws[name] / total:.2f} of {len(ws)})"

    def view(self) -> dict:
        return {"path": str(self.path), "moves": {n: m["label"] for n, m in self.moves.items()},
                "error": self.error, "problems": list(self.problems)}
