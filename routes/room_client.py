"""room_service and show_service from the Flask side: the scheduler's room actions (set_mode, set_auto, room_state),
the Alexa room intents and the room events bridge (routes/room_events_bridge.py) all go through here.

room_service runs inside this process (room_in_app.py) but owns its own asyncio loop, so this talks to its LAN door
over HTTP (127.0.0.1:8700) like any other client: the room keeps its own rules (a mode request can be refused,
Auto's minimum stay, holds), nothing here writes room state directly. Every call is bounded and never raises:
it returns (ok, detail) so a schedule or a voice reply can say what happened.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests

ROOM_URL = os.environ.get("ROOM_SERVICE_URL", "http://127.0.0.1:8700").rstrip("/")
TIMEOUT_S = 5.0


def _get(path: str, timeout: float = TIMEOUT_S) -> Optional[Any]:
    try:
        r = requests.get(f"{ROOM_URL}{path}", timeout=timeout)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def _send(method: str, path: str, body: dict, timeout: float = TIMEOUT_S) -> tuple[bool, str]:
    try:
        r = requests.request(method, f"{ROOM_URL}{path}", json=body, timeout=timeout)
    except requests.RequestException as e:
        return False, f"room unreachable ({type(e).__name__})"
    if r.ok:
        return True, "ok"
    try:
        d = r.json()
        why = d.get("detail") or d.get("error") or r.text
    except ValueError:
        why = r.text
    return False, f"{r.status_code}: {str(why)[:200]}"


def status() -> Optional[dict]:
    """room_service's full status (mode, screens, subsystems), or None."""
    return _get("/api/v1/status")


def modes() -> dict[str, str]:
    """{mode key: label} from room_service (modes.json, hot-reloaded); {} if the room is down."""
    d = _get("/api/v1/modes") or {}
    ms = d.get("modes") if isinstance(d, dict) else None
    return {k: (v.get("label") or k) for k, v in ms.items()} if isinstance(ms, dict) else {}


def resolve_mode(name: str, known: Optional[dict[str, str]] = None) -> Optional[str]:
    """A mode key from its key or its Room page label, case- and space-insensitive ("Normal" -> "gallery",
    "live test" -> "live-test"); None if nothing matches."""
    known = modes() if known is None else known
    want = " ".join(str(name or "").lower().replace("-", " ").replace("_", " ").split())
    if not want:
        return None
    for k, label in known.items():
        if want == " ".join(k.lower().replace("-", " ").replace("_", " ").split()):
            return k
    for k, label in known.items():
        lab = " ".join(str(label).lower().replace("-", " ").split())
        if want == lab or want == lab.split(" (")[0]:
            return k
    return None


def set_mode(name: str, source: str, reason: Optional[str] = None) -> tuple[bool, str]:
    """Ask room_service for a mode (PUT /api/v1/mode); the room may refuse (its rules decide)."""
    key = resolve_mode(name)
    if key is None:
        return False, f"unknown mode {name!r}"
    body: dict = {"mode": key, "source": source}
    if reason:
        body["reason"] = str(reason)[:200]
    ok, detail = _send("PUT", "/api/v1/mode", body)
    return ok, (key if ok else detail)


def auto() -> Optional[bool]:
    """Auto (show_service's autopilot) on / off, None if unknown."""
    d = _get("/show/api/v1/autopilot")
    return d.get("on") if isinstance(d, dict) and isinstance(d.get("on"), bool) else None


def set_auto(on: bool) -> tuple[bool, str]:
    """Turn Auto on or off, the same way the Room page does (show_service, through room's /show proxy)."""
    return _send("PUT", "/show/api/v1/autopilot", {"on": bool(on)}, timeout=15.0)


def room_facts(st: Optional[dict] = None) -> dict:
    """The facts the scheduler and the bridge use, from one status: {mode, mode_label, mode_source, auto,
    screens: {id: {layer, desired, phase}}}. Missing parts are None, never invented."""
    st = st if st is not None else (status() or {})
    m = st.get("mode") or {}
    last = m.get("last_transition") or {}
    believed = ((st.get("subsystems") or {}).get("autopilot") or {}).get("believed")
    av = believed.get("main") if isinstance(believed, dict) else believed
    screens = {}
    for sid, s in (st.get("screens") or {}).items():
        ob = s.get("observed") or {}
        d = s.get("desired")
        screens[sid] = {"layer": ob.get("layer"), "phase": ob.get("phase"),
                        "desired": "art" if d == "art" else ("live" if isinstance(d, dict) else d)}
    return {"mode": m.get("current"), "mode_source": last.get("source") if last.get("to") == m.get("current") else None,
            "auto": True if av == "on" else False if av == "off" else None, "screens": screens}


# -- scenes and effects by voice (Alexa) ---------------------------------------------------------------------------
import math as _math
import re as _re
import time as _time

LIBRARY_TTL_S = 600.0
_lib_cache: dict = {"t": -1e9, "lib": None, "rt": -1e9, "ratings": {}}
STOP_WORDS = {"a", "an", "the", "to", "scene", "scenes", "effect", "effects", "something", "please", "some", "one",
              "with", "of", "on", "in", "and", "change", "switch", "show", "me", "put", "play", "use", "add", "set"}
STAR_WEIGHT = {0: 0.35, 1: 0.05, 2: 0.3, 3: 1.5, 4: 4.0, 5: 8.0}   # tie-breaks only (config/live/rating_weights.json)


def library() -> dict:
    """room_service's library (scenes, effects, vocab), cached LIBRARY_TTL_S; {} if the room is down."""
    now = _time.monotonic()
    if _lib_cache["lib"] is None or now - _lib_cache["t"] > LIBRARY_TTL_S:
        lib = _get("/api/v1/library", timeout=10.0)
        if isinstance(lib, dict):
            _lib_cache.update(t=now, lib=lib)
    return _lib_cache["lib"] or {}


def _ratings() -> dict:
    now = _time.monotonic()
    if now - _lib_cache["rt"] > LIBRARY_TTL_S:
        d = _get("/renderer/dev/ratings") or {}
        _lib_cache.update(rt=now, ratings=d.get("ratings") or {})
    return _lib_cache["ratings"]


def _toks(s: Any) -> list[str]:
    out = []
    for t in _re.split(r"[^a-z0-9]+", str(s or "").lower()):
        if t and t not in STOP_WORDS:
            out.append(t[:-1] if len(t) > 3 and t.endswith("s") else t)   # fractals ~ fractal
    return out


def _score(query: list[str], phrase: str, item: dict, key: str) -> float:
    tags = item.get("tags") or {}
    fields = ((3.0, _toks(item.get("label")) + _toks(item.get("name")) + _toks(item.get(key))),
              (2.0, _toks(tags.get("family"))), (1.5, [t for m in (tags.get("moods") or []) for t in _toks(m)]))
    s = 0.0
    for q in query:
        exact = max((w for w, ts in fields if q in ts), default=0.0)
        stem = max((0.7 * w for w, ts in fields for t in ts                     # oceanic ~ ocean, cosmos ~ cosmic
                    if len(q) >= 4 and len(t) >= 4 and (q.startswith(t[:4]) and t.startswith(q[:4]))), default=0.0)
        s += max(exact, stem)
    if phrase and phrase in str(item.get("label", "")).lower():
        s += 4.0
    return s


def find(kind: str, query: str) -> Optional[dict]:
    """The best scene ("scenes") or effect ("effects") preset for a spoken query: words matched against labels, names,
    families and moods; the owner's stars break ties (rejects never win). None when nothing matches."""
    items = library().get(kind) or []
    q = _toks(query)
    if not q:
        return None
    phrase = " ".join(str(query).lower().split())
    key = "source" if kind == "scenes" else "effect"
    rk = "scene" if kind == "scenes" else "effect"
    rt = _ratings()
    best, best_s = None, 0.0
    for it in items:
        s = _score(q, phrase, it, key)
        if s <= 0.0:
            continue
        r = rt.get(f"{rk}/{it.get(key)}") or {}
        if r.get("reject"):
            continue
        s *= 1.0 + 0.1 * _math.log(STAR_WEIGHT.get(int(r.get("stars") or 0), 0.35) / 0.35 + 1e-9 + 1.0)
        if s > best_s:
            best, best_s = it, s
    return best


def room_screens(screen: Optional[str], st: Optional[dict] = None) -> list[str]:
    """The screens in ``screen``'s room (room_service's rooms), else just [screen]."""
    st = st if st is not None else (status() or {})
    for r in st.get("rooms") or []:
        if screen in (r.get("screens") or []):
            return list(r["screens"])
    return [screen] if screen else []


def live_targets(screen: Optional[str]) -> list[str]:
    """The live screens a voice command should act on: the asker's room's live screens, else every live screen."""
    st = status() or {}
    live = [sid for sid, s in (st.get("screens") or {}).items() if (s.get("observed") or {}).get("layer") == "live"]
    near = [s for s in room_screens(screen, st) if s in live]
    return near or live


def cue_scene(screen: str, to: str) -> tuple[bool, str]:
    """Cue a scene preset (or "auto": the director's next pick) on a live screen."""
    return _send("POST", f"/api/v1/screens/{screen}/cue", {"to": to})


def effects(screen: str) -> list:
    d = _get(f"/api/v1/screens/{screen}/fx") or {}
    return [x for x in (d.get("stack") or []) if isinstance(x, dict)]


def set_effects(screen: str, stack: list) -> tuple[bool, str]:
    """Replace a live screen's effect stack (preset names or the GET entries echoed back)."""
    return _send("PUT", f"/api/v1/screens/{screen}/fx", {"stack": stack})


_modes_cache: dict = {"t": -1e9, "modes": {}}


def modes_cached(ttl_s: float = 60.0) -> dict[str, str]:
    """modes(), cached for ``ttl_s`` (voice replies have an 8 s budget)."""
    now = _time.monotonic()
    if not _modes_cache["modes"] or now - _modes_cache["t"] > ttl_s:
        m = modes()
        if m:
            _modes_cache.update(t=now, modes=m)
    return _modes_cache["modes"]
