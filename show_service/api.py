"""The show API (README "Stage 3 > S3.9", docs/SHOW_ENGINE.md §10, §11.2): the Music card's routes and its socket.

Mounted on the show bus (bus.py, thread ``show-bus``) by ``Bus(routes=api.routes(brain_ref, engine_ref))``. The
object ``routes()`` returns is the mount function (``api_obj(app)``) and also carries ``notify``, the thread-safe
``contract.Notify`` that ``__main__`` puts in ``BrainContext.notify``.

* **Threads.** Handlers run on show-bus and never touch brain state: they ``await brain.call(fn, ...)`` (fn runs on
  show-brain) and read ``snapshot()``s (any thread). Clock words go straight to ``ctx.clock_action`` (any thread).
* **Paths** have no ``/show`` prefix (room's pass-through strips it). Relative URLs only. **No auth.**
* **Bodies** are JSON. Errors are ``{"error": <code>, "detail": <plain words>, ...}``, like room's.
* **Degrade, never refuse.** A normal tap on the card never gets a 4xx:
  - at a limit, nothing to undo, an unknown chip id, stopping a test that isn't running: 200 with what is true now;
  - a missing component (its module didn't load): 200 with a neutral answer and a plain ``detail``/``why``/``line``;
  - 400 only for a body that isn't what the route takes (not JSON, a word not on offer), always with ``choices``
    where there are some, so a stale page can recover;
  - 503 ``starting`` before the brain runs, 503 ``busy`` when ``brain.call`` times out, 502 ``room_unreachable`` when
    Auto can't reach the room. ``POST /api/v1/hue/release`` answers ``released`` at once while no hue output runs here
    (room_service streams), and before the brain runs unless patch.yaml says ``hue.streamer: show`` with a hue output
    enabled in fixtures.yaml: then ``released: false``, ``why: "starting"`` (a crashed predecessor may still hold the
    area), so room checks the bridge itself.
* **Stage 5** (README S5.4, the rig): ``GET /api/v1/rig`` (the Rig snapshot plus ``registry_view``), ``GET
  /api/v1/rig/frame?output=<id>`` (the output's last frame; 404 ``unknown_output`` with ``choices``), ``GET
  /api/v1/rig/dmx`` (the virtual nodes' universes, decoded: previz), ``POST /api/v1/rig/nodes/{id}/record-failsafe``
  (404 ``unknown_node``), and room's hue adapter's routes (68's contract, 16 Oct): ``POST /api/v1/hue/stream
  {lights, on}`` (the full set each time; HueAnswer at once), ``POST /api/v1/hue/release {lights?}`` (HueRelease,
  ``released`` true once the lamps are writable; the call waits ``rig.hue.release_timeout_s`` + 2, at most 19 s) and
  ``GET /api/v1/hue/status`` (HueAnswer). Their ``state`` is exactly one of ``idle | armed | streaming | taken_over``
  (contract.hue_answer), with ``lights`` (what room asked, unfiltered) and ``boot_id`` (this process) for room's
  re-sync; the driver's own word and the rest stay in ``GET /api/v1/rig`` ``.hue``. With no hue output
  on, stream and status answer ``idle`` with a ``detail`` naming the switch, and release answers as Stage 3 did.
  Without a rig component: ``GET /api/v1/rig`` answers ``{"enabled": false, "why"}``.
  - Machine routes (``PUT``/``DELETE /api/v1/vibe``, the room adapter's) answer 503 ``unavailable`` without a
    director, so the adapter retries. ``PUT /api/v1/vibe`` keeps any target as asked (an unknown vibe plays neutral, a
    bad override is dropped; the read-back's ``known`` and ``problems`` say so) and 400s only a body that isn't a
    target; ``{"vibe": null}`` and DELETE are no feel. Every read-back carries ``boot_id`` (this process).
  - Auto on/off and Undo each wait on one room PUT (up to contract.ROOM_PUT_TIMEOUT_S): their ``brain.call`` gets
    contract.AUTO_CALL_TIMEOUT_S, so a slow room never cuts the PUT off mid-way.
  - Auto's rules in page words (README S5.19; the Room page's Auto editor): ``GET /api/v1/autopilot/rules`` (every
    mode of the room's config: sentences, problems, live readings, per node path) and ``POST /api/v1/autopilot/check
    {"mode"?, "autopilot"?, "modes"?}`` (one block as the editor has it, checked with Auto's own parser; never saved).
    Empty shapes: no ``autopilot`` = only when someone picks it; a rule absent, null or {} = never by itself; ``from``
    absent or [] = any mode; ``leave_to`` absent = "previous".
* **WS /v1/events** (the card's socket): ``welcome`` first, then RFC 7386 ``card`` patches at most ``CARD_HZ`` (at
  once on ``notify("card")``), ``auto`` and ``describe`` events; ``ping``/``resync`` from the client; 64 clients,
  256 queued messages each (full: the queue is dropped and a fresh welcome goes next). ``seq`` is per client, +1 per
  message sent.
"""

from __future__ import annotations

import asyncio
import collections
import inspect
import json
import logging
import math
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

from aiohttp import WSMsgType, web

from . import contract
from .contract import (CARD_HZ, CLOCK_ACTIONS, DIRECTOR_ACTIONS, EV_AUTO, EV_CARD, EV_DESCRIBE, EV_PONG, EV_WELCOME,
                       EVENTS_PATH, PALETTE_ART, PALETTE_AUTO, STEP_DIRS, TEST_SOURCES)
from .state_store import dumps as _dumps, tail_jsonl

log = logging.getLogger("show_service.api")

MAX_EVENT_CLIENTS = 64
CLIENT_QUEUE = 256
WS_HEARTBEAT_S = 20.0
WS_MAX_IN = 16 << 10
CLOSE_TIMEOUT_S = 2.0
LATE_REFRESH_S = 0.15            # after a change, the card is refreshed at once and again after one director tick
WHY_S = 1.0                       # room.why's period (docs/LIVE_DIAGNOSTICS.md: at most 1 Hz)
DESCRIBE_MAX_CHARS = 300
DESCRIBE_TIMEOUT_S = 8.0         # show.yaml listener.describe_timeout_s wins; the call gets that + 1 s
STALE_FEATURES_MS = 5000.0
CONFIDENT = 0.6
LOG_KINDS: tuple[str, ...] = ("director", "autopilot", "listener")
LOG_FILES: dict[str, Path] = {"director": contract.DIRECTOR_LOG, "autopilot": contract.AUTOPILOT_LOG,
                              "listener": contract.LISTENER_LOG}
LOG_N_DEFAULT, LOG_N_MAX = 50, 1000
CARD_PALETTE_EXTRA = 5           # card palette choices: Auto, From the art, then the vibe's first 5
_CODE_RE = re.compile(r"^[a-z][a-z_]{1,40}$")

# component names (README S3.2)
DIRECTOR, AUTOPILOT, LISTENER, SITUATIONS, SOURCES, ROOM = ("director", "autopilot", "listener", "situations",
                                                            "sources", "room")
STRUCTURE = "structure"                              # Stage 4 (README S4.7): music.context for the hearing line
RIG = "rig"                                          # Stage 5 (README S5.4): outputs.Rig
NO_RIG = "The rig isn't running (outputs.py isn't loaded), so no light is driven from here."
HUE_ROOM = {"released": True, "state": "idle", "via": "room",                  # no hue output runs here
            "detail": "nothing streams Hue from show_service (room_service streams, or the hue output is off)"}
HUE_STARTING = {"released": False, "state": "idle", "why": "starting", "still_following": [], "waited_ms": 0.0,
                "via": "show", "detail": "show_service is starting with patch.yaml hue.streamer: show and a hue output "
                                         "on: it can't tell yet whether the area is still streaming, so check the "
                                         "bridge"}

#: page words for a component whose module isn't running
MISSING: dict[str, str] = {
    DIRECTOR: "The director isn't running, so the lights keep their fixed colour.",
    AUTOPILOT: "Auto isn't running.",
    LISTENER: "The listener isn't running.",
    SITUATIONS: "Situations aren't being read.",
    SOURCES: "Test sources aren't available.",
    ROOM: "show_service isn't talking to the room.",
}
STARTING = "show_service is starting; try again in a moment."
SAY_SOURCE = "Say which test source: " + " or ".join(f"“{s}”" for s in TEST_SOURCES) + "."
BUSY = "show_service is busy; try again in a moment."

#: every (method, path) this module mounts; bus.py's 501 stubs fill only the pairs nobody registered
ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/v1/card"), ("GET", EVENTS_PATH),
    ("POST", "/api/v1/step"), ("PUT", "/api/v1/palette"), ("POST", "/api/v1/action"),
    ("DELETE", "/api/v1/nudge/{id}"),
    ("GET", "/api/v1/vibes"), ("GET", "/api/v1/vibe"), ("PUT", "/api/v1/vibe"), ("DELETE", "/api/v1/vibe"),
    ("POST", "/api/v1/describe"),
    ("GET", "/api/v1/listening"), ("PUT", "/api/v1/listening"),
    ("POST", "/api/v1/hue/release"), ("POST", "/api/v1/hue/stream"), ("GET", "/api/v1/hue/status"),
    ("GET", "/api/v1/rig"), ("GET", "/api/v1/rig/frame"), ("GET", "/api/v1/rig/dmx"),
    ("POST", "/api/v1/rig/nodes/{id}/record-failsafe"),
    ("GET", "/api/v1/autopilot"), ("PUT", "/api/v1/autopilot"), ("POST", "/api/v1/autopilot/undo"),
    ("POST", "/api/v1/autopilot/resume"), ("GET", "/api/v1/autopilot/rules"), ("POST", "/api/v1/autopilot/check"),
    ("GET", "/api/v1/situations"),
    ("GET", "/api/v1/test"), ("POST", "/api/v1/test"), ("DELETE", "/api/v1/test"), ("DELETE", "/api/v1/test/{source}"),
    ("GET", "/api/v1/look"), ("GET", "/api/v1/log"),
)


# -- small helpers --------------------------------------------------------------------------------------------------------
def _json(body: Any, status: int = 200) -> web.Response:
    return web.Response(status=status, text=_dumps(body), content_type="application/json")


def _err(status: int, code: str, detail: str, **extra: Any) -> web.Response:
    return _json({"error": code, "detail": detail, **extra}, status)


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _num(v: Any, default: Optional[float] = 0.0) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    x = float(v)
    return x if math.isfinite(x) else default


def _clamp01(x: Optional[float]) -> float:
    return 0.0 if x is None else min(1.0, max(0.0, x))


def _code(e: BaseException, default: str) -> tuple[str, str]:
    """``ValueError("unknown_vibe: party2")`` -> ("unknown_vibe", "party2"); anything else -> (default, text)."""
    s = str(e).strip()
    head, sep, rest = s.partition(":")
    head = head.strip()
    if _CODE_RE.match(head):
        return head, rest.strip()
    return default, s


def _is_test_stream(name: Any) -> bool:
    return isinstance(name, str) and (name in contract.TEST_STREAMS or name.startswith("test-"))


def _running(brain: Any) -> bool:
    """A brain answers once it runs: an explicit ``running``/``started``/``ready`` flag (bool or method) wins; else
    its ``ctx.loop`` is set (brain.start() sets it); else assume yes."""
    for name in ("running", "started", "ready"):
        v = getattr(brain, name, None)
        if callable(v):
            try:
                v = v()
            except Exception:
                v = None
        if isinstance(v, bool):
            return v
    ctx = getattr(brain, "ctx", None)
    if ctx is not None and hasattr(ctx, "loop"):
        return getattr(ctx, "loop") is not None
    return True


def _components(brain: Any) -> Mapping[str, Any]:
    comps = getattr(brain, "components", None)
    return comps if isinstance(comps, Mapping) else {}


def _snap_of(comp: Any) -> dict:
    if comp is None:
        return {}
    try:
        s = comp.snapshot()
    except Exception:
        return {}
    return s if isinstance(s, dict) else {}


async def _maybe_await(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


# -- RFC 7386 merge patches ---------------------------------------------------------------------------------------------
def merge_patch(old: Any, new: Any) -> dict:
    """The RFC 7386 patch that turns ``old`` into ``new`` (both objects): objects recurse, lists and scalars are
    replaced whole, a removed key (or one that became null) is ``None``. ``{}`` when nothing changed."""
    old, new = _d(old), _d(new)
    patch: dict[str, Any] = {}
    for k, o in old.items():
        if o is not None and new.get(k) is None:
            patch[k] = None
    for k, v in new.items():
        if v is None:
            continue
        o = old.get(k)
        if isinstance(v, dict) and isinstance(o, dict):
            sub = merge_patch(o, v)
            if sub:
                patch[k] = sub
        elif type(o) is not type(v) or o != v:
            patch[k] = v
    return patch


def apply_patch(target: Any, patch: Any) -> Any:
    """RFC 7386 MergePatch(target, patch) (what a client does); returns a new value, never mutates ``target``."""
    if not isinstance(patch, dict):
        return patch
    out = dict(target) if isinstance(target, dict) else {}
    for k, v in patch.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = apply_patch(out.get(k), v)
    return out


# -- the card (pure) -----------------------------------------------------------------------------------------------------
NEUTRAL_STEPS: dict = {"value": 0, "min": 0, "max": 0, "at_limit": False}
DESCRIBE_IDLE: dict = {"state": "idle", "text": None, "reply": None, "suggest": None}
AUTO_CHOICE: dict = {"name": PALETTE_AUTO, "label": "Auto", "family": None, "swatch": []}
ART_CHOICE: dict = {"name": PALETTE_ART, "label": "From the art", "family": None, "swatch": []}


def _listener_view(snap: dict) -> dict:
    """The listener's snapshot is its ListenerView (maybe with ``beliefs``), or nests it under ``view``."""
    v = snap.get("view")
    return v if isinstance(v, dict) else snap


def _reading(readings: dict, sid: str) -> Optional[bool]:
    r = readings.get(sid)
    v = r.get("value") if isinstance(r, dict) else None
    return v if isinstance(v, bool) else None


def _confident_words(beliefs: dict) -> Optional[str]:
    if beliefs.get("state") not in ("fresh", "holding"):
        return None
    g = _d(beliefs.get("genre"))
    word = g.get("word")
    if not isinstance(word, str) or not word or word == "unknown" or (_num(g.get("conf")) or 0.0) < CONFIDENT:
        return None
    moods = [m.get("word") for m in beliefs.get("moods") or [] if isinstance(m, dict) and isinstance(m.get("word"), str)]
    return f"{word} · {moods[0]}" if moods else word


def _context_words(context: dict, beliefs: dict) -> Optional[str]:
    """README S4.8 (the card's hearing rule 1): music.context's genre word at conf >= CONFIDENT, with the listener's
    first mood while its beliefs stand ("<genre> · <mood>"), or the genre alone."""
    g = _d(context.get("genre"))
    word = g.get("word")
    if not isinstance(word, str) or not word or word == "unknown" or (_num(g.get("conf")) or 0.0) < CONFIDENT:
        return None
    moods = []
    if beliefs.get("state") in ("fresh", "holding"):
        moods = [m.get("word") for m in beliefs.get("moods") or []
                 if isinstance(m, dict) and isinstance(m.get("word"), str)]
    return f"{word} · {moods[0]}" if moods else word


#: hearing.text when no sound arrives, by what's connected (engine "inputs": permanent senders, room streams' states).
NO_SENDER = "No microphone connected yet"
NOT_ANSWERING = "The microphone isn't answering"
NO_DEVICE = "No microphone plugged in"
STOPPED = "The microphone stopped sending"
MIC_STARTING = "The microphone is starting"     # the mic is on, its sender hasn't opened it yet (README S5.16)
#: hearing.text when the mic on demand (mic.py, README S5.16) is why no room sound arrives: these win over the above
MIC_MUTED = "Mic muted"
MIC_OFF = "Mic off"


def _input_words(inputs: Any) -> Optional[str]:
    """Why no sound arrives, or None while a room stream is up (or nothing is known): nothing configured, the sender
    not answering, the sender up but with no capture device, a mic still opening (every room stream "off" while the
    mic is on), or a stream that stopped."""
    if not isinstance(inputs, dict):
        return None
    states = [s for s in _d(inputs.get("room")).values() if isinstance(s, str)]
    if any(s in ("streaming", "quiet") for s in states):
        return None
    if "no_device" in states:
        return NO_DEVICE
    if states and all(s == "off" for s in states):
        return MIC_STARTING
    if states:
        return STOPPED
    return NO_SENDER if not inputs.get("senders") else NOT_ANSWERING


def _mic_words(mic_state: Any) -> Optional[str]:
    """The words while the mic on demand is off (README S5.16): MIC_MUTED or MIC_OFF; None while it is on or unknown."""
    if not isinstance(mic_state, dict) or mic_state.get("on") is not False:
        return None
    return MIC_MUTED if mic_state.get("muted") else MIC_OFF


def _palette_choices(director: dict) -> list[dict]:
    pal = _d(director.get("palette"))
    raw = [c for c in pal.get("choices") or [] if isinstance(c, dict) and isinstance(c.get("name"), str)]

    def first(name: str, default: dict) -> dict:     # the director's entry (it may carry a swatch), else ours
        got = next((c for c in raw if c["name"] == name), None)
        return {**default, **got, "label": got.get("label") or default["label"]} if got else dict(default)

    rest = [c for c in raw if c["name"] not in (PALETTE_AUTO, PALETTE_ART)][:CARD_PALETTE_EXTRA]
    return [first(PALETTE_AUTO, AUTO_CHOICE), first(PALETTE_ART, ART_CHOICE)] + rest


def _cut(v: Any) -> Optional[str]:
    return v[:contract.WHY_TEXT_MAX] if isinstance(v, str) and v else None


def why_of(brain_snapshot: Optional[dict], card_body: Optional[dict], *, wall_ms: float) -> contract.WhyData:
    """room.why (docs/LIVE_DIAGNOSTICS.md v2): the card's words plus Auto's last switch and the director's last look
    reason, each cut to WHY_TEXT_MAX. Built from snapshots only (any thread)."""
    cb = card_body if isinstance(card_body, dict) else {}
    comps = _d(_d(brain_snapshot).get("components"))
    au, li, ms = _d(cb.get("auto")), _d(cb.get("listening")), _d(cb.get("mic_state"))
    last = _d(_d(comps.get(AUTOPILOT)).get("last"))
    last_words = None
    if last.get("to"):
        reason = str(last.get("reason") or "").rsplit(" (", 1)[0]
        last_words = f"{last.get('from_label') or last.get('from') or '?'} -> {last.get('to_label') or last['to']}" \
            + (f": {reason}" if reason else "")
    lk = _d(_d(comps.get(DIRECTOR)).get("last_look"))
    return {"t_ms": round(wall_ms, 1),
            "auto": {"on": bool(au.get("on")), "line": _cut(au.get("line")), "last": _cut(last_words)},
            "listening": {"on": bool(li.get("on")), "effective": bool(li.get("effective")),
                          "why": None if li.get("effective") else _cut(li.get("why"))},
            "mic": {"on": bool(ms.get("on")), "why": _cut(ms.get("why"))},
            "look": {"why": _cut(lk.get("why")),
                     "age_s": int(lk["age_s"]) if isinstance(lk.get("age_s"), (int, float)) else None}}


def card(brain_snapshot: Optional[dict], engine: Optional[dict], *, describe: Optional[dict] = None) -> dict:
    """``contract.CardData`` from the brain's snapshot (``{"components": {name: snapshot}}``) and the engine's
    ``{"active_audio", "features": {"energy", "age_ms", "stream"}, "clock"}``. Pure; any missing piece reads neutral.

    hearing.input is the active stream's raw meter ({peak_dbfs, rms_dbfs} over the last 0.5 s) and hearing.input_trace
    its last 10 closed 100 ms bins ({seq, hz, peak_dbfs: [...], rms_dbfs: [...]}, oldest first; seq numbers the newest,
    so a client appends only bins newer than the last it drew); both None without active audio. hearing.level is
    energy (loudness against the mode's expected loudness, gated), not a meter. hearing.mic is inputs.Inputs.mic():
    {stream, device, chosen} for the active stream (None without one).

    hearing.text, first match wins: why nothing arrives when no sound comes in (the mic on demand first: "Mic muted" /
    "Mic off", README S5.16; then _input_words: no microphone connected / not answering / not plugged in / starting /
    stopped sending), music.context's confident genre (Stage 4, structure's snapshot; with the listener's first mood),
    confident beliefs ("soul · relaxed"), Talking, Quiet, No sound coming in (no active audio, or features older than
    5 s), Music, Listening. ``mic_state`` is the engine's (mic.Mic.state(): GET /api/v1/mic's body), None without
    mic.py."""
    comps = _d(_d(brain_snapshot).get("components"))
    director, ap, sit, src = (_d(comps.get(n)) for n in (DIRECTOR, AUTOPILOT, SITUATIONS, SOURCES))
    lsnap = _d(comps.get(LISTENER))
    lview = _listener_view(lsnap)
    eng = _d(engine)
    feats = _d(eng.get("features"))
    readings = _d(sit.get("readings"))

    active = eng.get("active_audio")
    age_ms = _num(feats.get("age_ms"), None)
    stale = not active or age_ms is None or age_ms > STALE_FEATURES_MS
    beliefs = _d(lsnap.get("beliefs") if "beliefs" in lsnap else lview.get("beliefs"))
    context = _d(_d(comps.get(STRUCTURE)).get("context"))
    mic_state = eng.get("mic_state") if isinstance(eng.get("mic_state"), dict) else None
    why = (_mic_words(mic_state) or _input_words(eng.get("inputs"))) if stale else None
    text = why or (None if stale else _context_words(context, beliefs))
    if text is None:
        text = _confident_words(beliefs)
    if text is None:
        if _reading(readings, "talking"):
            text = "Talking"
        elif _reading(readings, "quiet"):
            text = "Quiet"
        elif stale:
            text = "No sound coming in"
        elif _reading(readings, "music_audible"):
            text = "Music"
        else:
            text = "Listening"
    direction = _d(director.get("direction"))
    stream = active if isinstance(active, str) else feats.get("stream") if isinstance(feats.get("stream"), str) else None
    inp = _d(eng.get("input")) if eng.get("active_audio") else {}
    trace = _d(inp.get("trace"))
    hearing = {"text": text, "level": 0.0 if stale else round(_clamp01(_num(feats.get("energy"))), 2),
               "loose": bool(direction.get("loose")), "stream": stream, "test": _is_test_stream(stream),
               "input": {"peak_dbfs": inp.get("peak_dbfs"), "rms_dbfs": inp.get("rms_dbfs")} if inp else None,
               "input_trace": {"seq": trace.get("seq"), "hz": trace.get("hz"), "peak_dbfs": trace.get("peak_dbfs"),
                               "rms_dbfs": trace.get("rms_dbfs")} if trace else None,
               "mic": dict(eng["mic"]) if isinstance(eng.get("mic"), dict) else None}

    rb = _d(director.get("readback"))
    name = rb.get("vibe") if isinstance(rb.get("vibe"), str) else None
    vibe = {"name": name, "label": rb.get("label") if isinstance(rb.get("label"), str) and rb.get("label") else
            (name or ""), "known": bool(rb.get("known"))}

    steps = director.get("steps")
    steps = dict(steps) if isinstance(steps, dict) else dict(NEUTRAL_STEPS)

    pal = _d(director.get("palette"))
    look = _d(director.get("look"))
    current = pal.get("current")
    if current is None:
        current = _d(look.get("palette")).get("name")
    palette = {"pick": pal.get("pick") if isinstance(pal.get("pick"), str) else PALETTE_AUTO, "current": current,
               "choices": _palette_choices(director)}

    keep = _num(director.get("keep_in_s"), None)
    look_out = {"id": look.get("look_id") if isinstance(look.get("look_id"), str) else None,
                "keep_in_s": None if keep is None else float(round(keep)),
                "next_pending": bool(director.get("next_pending"))}

    chips = []
    for ch in director.get("chips") or []:
        if isinstance(ch, dict):
            ch = dict(ch)
            if _num(ch.get("expires_in_s"), None) is not None:
                ch["expires_in_s"] = float(round(ch["expires_in_s"]))
            chips.append(ch)

    if LISTENER in comps:          # README S5.20: today's spend and the evening's estimate, even while it is off
        est = _num(lview.get("est_evening_usd"), None)
        listening = {"on": bool(lview.get("on")), "effective": bool(lview.get("effective")), "why": lview.get("why"),
                     "spend_today_usd": round(_num(lview.get("spend_today_usd"), 0.0) or 0.0, 4),
                     "est_evening_usd": None if est is None else round(est, 2),
                     "provider": lview.get("provider") if isinstance(lview.get("provider"), str) else None}
    else:
        listening = {"on": False, "effective": False, "why": MISSING[LISTENER], "spend_today_usd": 0.0,
                     "est_evening_usd": None, "provider": None}

    if AUTOPILOT in comps:
        auto = {"on": ap.get("on") if isinstance(ap.get("on"), bool) else None, "state": ap.get("state") or "off",
                "line": ap.get("line") or "Auto is off", "chip": ap.get("chip"), "suggest": ap.get("suggest")}
    else:
        auto = {"on": None, "state": "off", "line": MISSING[AUTOPILOT], "chip": None, "suggest": None}

    tests = {k: v for k, v in _d(src.get("tests")).items() if isinstance(v, dict)}
    return {"hearing": hearing, "vibe": vibe, "steps": steps, "palette": palette, "look": look_out,
            "house": bool(director.get("house")), "low": bool(director.get("low")), "chips": chips,
            "listening": listening, "describe": dict(describe) if isinstance(describe, dict) else dict(DESCRIBE_IDLE),
            "auto": auto, "tests": tests, "mic_state": _plain(mic_state) if mic_state is not None else None}


def _plain(o: Any) -> Any:
    """A JSON-only copy (dicts and lists copied, so the card never shares the mic's own body)."""
    if isinstance(o, dict):
        return {str(k): _plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_plain(v) for v in o]
    return o


# -- neutral answers for a missing component -------------------------------------------------------------------------------
def neutral_readback(why: str) -> dict:
    return {"vibe": None, "listening": False, "source": "none", "set_age_s": None, "known": False, "label": "",
            "effective": {}, "listening_effective": False, "listening_why": why, "problems": []}


def neutral_catalog(why: str) -> dict:
    return {"revision": "", "path": "config/room/vibes.yaml", "error": why, "order": [], "vibes": {}, "fields": {},
            "neutral": {}, "palettes": [], "vocab": {}}


def neutral_listener(why: str) -> dict:
    return {"on": False, "effective": False, "why": why, "stream": None, "next_in_s": None, "cadence_s": 0.0,
            "calls_today": 0, "spend_today_usd": 0.0, "budget_usd": None, "model": None, "last": None, "errors": 0,
            "provider": None, "est_evening_usd": None, "estimate": None}


def neutral_autopilot(line: str) -> dict:
    return {"on": None, "state": "off", "why": None, "line": line, "since_s": 0.0, "until_in_s": None,
            "entered": None, "hold": None, "last": None, "chip": None, "suggest": None, "suppressed": [],
            "candidates": [], "settings": {}, "room": {"reachable": False, "revision": None, "current": None,
                                                       "transition_running": False}}


def neutral_source(source: str, why: Optional[str] = None) -> dict:
    player = contract.TEST_SOURCE_PLAYERS.get(source) or {}
    return {"source": source, "state": "off", "piece": None, "label": None, "stream": player.get("stream", ""),
            "sender": "", "pid": None, "since_s": None, "error": why}


# -- the /v1/events hub (show-bus) ---------------------------------------------------------------------------------------
class _EvClient:
    __slots__ = ("ws", "queue", "wake", "seq", "closing", "task", "since", "sent", "overflows", "peer")

    def __init__(self, ws: web.WebSocketResponse, peer: str) -> None:
        self.ws, self.peer = ws, peer
        self.queue: collections.deque[tuple[str, Any]] = collections.deque()   # ("welcome", card) | ("msg", {...})
        self.wake = asyncio.Event()
        self.seq = self.sent = self.overflows = 0
        self.closing = False
        self.task: Optional[asyncio.Task] = None
        self.since = time.monotonic()


class EventsHub:
    """WS /v1/events: one per Api, living on the bus's loop. ``notify`` is the only any-thread entry."""

    def __init__(self, api: "Api", *, max_clients: int = MAX_EVENT_CLIENTS, queue_max: int = CLIENT_QUEUE,
                 hz: float = CARD_HZ) -> None:
        self.api = api
        self.max_clients, self.queue_max, self.hz = int(max_clients), int(queue_max), float(hz)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.clients: set[_EvClient] = set()
        self.last_card: Optional[dict] = None
        self._task: Optional[asyncio.Task] = None
        self._refresh_pending = False
        self.patches = self.refreshes = self.card_errors = self.refused = self.notified = 0

    # -- lifecycle (aiohttp signals, on the bus loop) ------------------------------------------------------------
    async def on_startup(self, app: web.Application) -> None:
        self.loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._card_loop(), name="show-api-card")

    async def on_shutdown(self, app: web.Application) -> None:
        if self._task is not None:
            self._task.cancel()
        clients = list(self.clients)
        for c in clients:
            c.closing = True
            c.wake.set()
        if clients:
            await asyncio.gather(*(self._close(c, 1001, b"show_service stopping") for c in clients),
                                 return_exceptions=True)

    async def _close(self, c: _EvClient, code: int, message: bytes) -> None:
        try:
            await asyncio.wait_for(c.ws.close(code=code, message=message), CLOSE_TIMEOUT_S)
        except Exception:
            pass

    # -- any thread ---------------------------------------------------------------------------------------------------
    def notify(self, kind: str, payload: Optional[dict] = None) -> None:
        """``contract.Notify``: "card" (recompute now), "auto" (AutoAnnouncement), "describe" (DescribeResult).
        Never blocks, never raises; before the bus loop runs it is dropped."""
        loop = self.loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._on_notify, kind, payload)
        except RuntimeError:                       # the loop closed
            pass

    # -- show-bus -----------------------------------------------------------------------------------------------------
    def _on_notify(self, kind: str, payload: Optional[dict]) -> None:
        self.notified += 1
        if kind == EV_AUTO and isinstance(payload, dict):
            self.broadcast({"type": EV_AUTO, "event": payload})
        elif kind == EV_DESCRIBE and isinstance(payload, dict):
            self.broadcast({"type": EV_DESCRIBE, "result": payload})
        self.refresh_soon()

    def refresh_soon(self, delay: float = 0.0) -> None:
        """Recompute the card on the loop (coalesced); ``delay`` > 0 schedules one more later."""
        loop = self.loop
        if loop is None:
            return
        if delay > 0:
            loop.call_later(delay, self.refresh_soon)
            return
        if self._refresh_pending:
            return
        self._refresh_pending = True
        loop.call_soon(self._refresh_cb)

    def _refresh_cb(self) -> None:
        self._refresh_pending = False
        self.refresh()

    def refresh(self) -> Optional[dict]:
        """Compute the card; if it changed, keep it and send the patch to every client. Returns the patch."""
        try:
            new = self.api.build_card()
        except Exception:
            self.card_errors += 1
            if self.card_errors <= 3 or self.card_errors % 100 == 0:
                log.exception("api: building the card failed")
            return None
        if new is None:
            return None
        self.refreshes += 1
        old = self.last_card
        if old is None:
            self.last_card = new
            return None
        patch = merge_patch(old, new)
        if not patch:
            return None
        self.last_card = new
        self.patches += 1
        self.broadcast({"type": EV_CARD, "patch": patch})
        return patch

    def broadcast(self, msg: dict) -> None:
        for c in list(self.clients):
            self._offer(c, ("msg", msg))

    def _offer(self, c: _EvClient, item: tuple[str, Any]) -> None:
        if c.closing:
            return
        if len(c.queue) >= self.queue_max:          # a phone that stopped reading: drop its backlog, resync it
            c.queue.clear()
            c.overflows += 1
            item = ("welcome", self.last_card if self.last_card is not None else self._card_now())
        c.queue.append(item)
        c.wake.set()

    def _welcome(self, c: _EvClient) -> None:
        c.queue.clear()
        c.queue.append(("welcome", self.last_card if self.last_card is not None else self._card_now()))
        c.wake.set()

    def _card_now(self) -> dict:
        try:
            got = self.api.build_card()
        except Exception:
            got = None
        if got is None:
            got = card(None, None)
        self.last_card = got
        return got

    async def _card_loop(self) -> None:
        period = 1.0 / max(0.1, self.hz)
        next_why = 0.0
        while True:
            await asyncio.sleep(period)
            if self.clients:
                self.refresh()
            now = time.monotonic()
            if self.api.publish is not None and now >= next_why:      # room.why, 1 Hz, whoever is watching
                next_why = now + WHY_S
                self.api.publish_why()

    async def _sender(self, c: _EvClient) -> None:
        try:
            while not c.closing:
                while not c.queue:
                    c.wake.clear()
                    await c.wake.wait()
                    if c.closing:
                        return
                kind, item = c.queue.popleft()
                c.seq += 1
                if kind == "welcome":
                    msg = {"type": EV_WELCOME, "v": 1, "seq": c.seq, "t_ms": round(time.monotonic() * 1000.0, 1),
                           "card": item}
                else:
                    msg = {"type": item.get("type"), "seq": c.seq, **{k: v for k, v in item.items() if k != "type"}}
                await c.ws.send_str(_dumps(msg))
                c.sent += 1
        except Exception:                           # the connection went: the handler's read loop cleans up
            pass

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if self.api.brain() is None:
            return _err(503, "starting", STARTING)
        if len(self.clients) >= self.max_clients:
            self.refused += 1
            return _err(503, "too_many_clients",
                        f"Too many Music cards are open ({self.max_clients}); close one and try again.")
        if self.loop is None:                       # mounted without the startup signal (shouldn't happen)
            self.loop = asyncio.get_running_loop()
        ws = web.WebSocketResponse(heartbeat=WS_HEARTBEAT_S, max_msg_size=WS_MAX_IN, timeout=CLOSE_TIMEOUT_S)
        await ws.prepare(request)
        self.refresh()                              # others get the patch; the newcomer's welcome is the same card
        c = _EvClient(ws, str(request.remote or "?"))
        self._welcome(c)
        self.clients.add(c)
        c.task = asyncio.create_task(self._sender(c), name="show-api-events-send")
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                t = m.get("type") if isinstance(m, dict) else None
                if t == "ping":
                    self._offer(c, ("msg", {"type": EV_PONG}))
                elif t == "resync":
                    self.refresh()
                    self._welcome(c)
        finally:
            self.clients.discard(c)
            c.closing = True
            c.wake.set()
            if c.task is not None:
                c.task.cancel()
        return ws

    def stats(self) -> dict:
        return {"clients": len(self.clients), "patches": self.patches, "refreshes": self.refreshes,
                "card_errors": self.card_errors, "refused": self.refused, "notified": self.notified,
                "overflows": sum(c.overflows for c in self.clients)}


# -- the API --------------------------------------------------------------------------------------------------------------
Handler = Callable[[web.Request, Any], Awaitable[web.StreamResponse]]


class Api:
    """The routes and the events hub. Build with ``routes(brain_ref, engine_ref)``; mount with ``api_obj(app)``."""

    def __init__(self, brain_ref: Callable[[], Any], engine_ref: Optional[Callable[[], Optional[dict]]] = None, *,
                 clock_action: Optional[contract.ClockAction] = None, log_files: Optional[Mapping[str, Path]] = None,
                 max_clients: int = MAX_EVENT_CLIENTS, queue_max: int = CLIENT_QUEUE, card_hz: float = CARD_HZ,
                 late_refresh_s: float = LATE_REFRESH_S, patch_path: Optional[Path] = None,
                 fixtures_path: Optional[Path] = None) -> None:
        self.brain_ref, self.engine_ref = brain_ref, engine_ref
        # read only by a Hue release before the brain runs (_hue_handed_to_show)
        self.patch_path = Path(patch_path) if patch_path is not None else contract.PATCH_FILE
        self.fixtures_path = Path(fixtures_path) if fixtures_path is not None else contract.FIXTURES_FILE
        self.clock_action = clock_action
        self.log_files = dict(LOG_FILES if log_files is None else log_files)
        self.late_refresh_s = float(late_refresh_s)
        self.events = EventsHub(self, max_clients=max_clients, queue_max=queue_max, hz=card_hz)
        self.describe_state: dict = dict(DESCRIBE_IDLE)
        self.mounted: list[tuple[str, str]] = []
        self.boot_id = uuid.uuid4().hex[:12]          # this process: the read-back carries it (the room adapter's
                                                       # cold-start check)
        self.publish: Optional[contract.Publish] = None    # the show bus (__main__ sets it): room.why
        self.why_published = self.why_errors = 0

    # -- mounting (show-bus, inside Bus._startup) -------------------------------------------------------------------
    def __call__(self, app: web.Application) -> None:
        r = app.router
        table: list[tuple[str, str, Callable]] = [
            ("GET", "/api/v1/card", self._card),
            ("POST", "/api/v1/step", self._step),
            ("PUT", "/api/v1/palette", self._palette),
            ("POST", "/api/v1/action", self._action),
            ("DELETE", "/api/v1/nudge/{id}", self._nudge),
            ("GET", "/api/v1/vibes", self._vibes),
            ("GET", "/api/v1/vibe", self._vibe_get),
            ("PUT", "/api/v1/vibe", self._vibe_put),
            ("DELETE", "/api/v1/vibe", self._vibe_delete),
            ("POST", "/api/v1/describe", self._describe),
            ("GET", "/api/v1/listening", self._listening_get),
            ("PUT", "/api/v1/listening", self._listening_put),
            ("GET", "/api/v1/autopilot", self._auto_get),
            ("PUT", "/api/v1/autopilot", self._auto_put),
            ("POST", "/api/v1/autopilot/undo", self._auto_undo),
            ("POST", "/api/v1/autopilot/resume", self._auto_resume),
            ("GET", "/api/v1/autopilot/rules", self._auto_rules),
            ("POST", "/api/v1/autopilot/check", self._auto_check),
            ("GET", "/api/v1/situations", self._situations),
            ("GET", "/api/v1/test", self._test_get),
            ("POST", "/api/v1/test", self._test_post),
            ("DELETE", "/api/v1/test", self._test_delete),
            ("DELETE", "/api/v1/test/{source}", self._test_delete),
            ("GET", "/api/v1/look", self._look),
            ("GET", "/api/v1/log", self._log),
            ("GET", "/api/v1/rig", self._rig),
            ("GET", "/api/v1/rig/frame", self._rig_frame),
            ("GET", "/api/v1/rig/dmx", self._rig_dmx),
            ("POST", "/api/v1/rig/nodes/{id}/record-failsafe", self._record_failsafe),
            ("POST", "/api/v1/hue/stream", self._hue_stream),
            ("GET", "/api/v1/hue/status", self._hue_status),
        ]
        for method, path, fn in table:
            r.add_route(method, path, self._guard(fn))
            self.mounted.append((method, path))
        r.add_post("/api/v1/hue/release", self._hue_release)        # answers even before the brain runs
        self.mounted.append(("POST", "/api/v1/hue/release"))
        r.add_get(EVENTS_PATH, self.events.handle)
        self.mounted.append(("GET", EVENTS_PATH))
        app.on_startup.append(self.events.on_startup)
        app.on_shutdown.append(self.events.on_shutdown)

    def notify(self, kind: str, payload: Optional[dict] = None) -> None:
        """``BrainContext.notify``: any thread."""
        self.events.notify(kind, payload)

    # -- plumbing -------------------------------------------------------------------------------------------------------
    def brain(self) -> Any:
        try:
            b = self.brain_ref() if self.brain_ref is not None else None
        except Exception:
            return None
        if b is None or not _running(b):
            return None
        return b

    def engine(self) -> dict:
        try:
            e = self.engine_ref() if self.engine_ref is not None else None
        except Exception:
            return {}
        return e if isinstance(e, dict) else {}

    def snapshot(self, brain: Any = None) -> dict:
        """brain.snapshot() (any thread), with each component's snapshot under ``components``; the listener's
        ``beliefs`` are added from ``beliefs()`` when its snapshot lacks them."""
        brain = brain if brain is not None else self.brain()
        if brain is None:
            return {}
        try:
            snap = brain.snapshot()
        except Exception:
            snap = None
        snap = dict(snap) if isinstance(snap, dict) else {}
        comps = snap.get("components")
        comps = dict(comps) if isinstance(comps, dict) else {n: _snap_of(c) for n, c in _components(brain).items()}
        lis = _components(brain).get(LISTENER)
        if lis is not None:
            ls = _d(comps.get(LISTENER))
            if "beliefs" not in ls and callable(getattr(lis, "beliefs", None)):
                try:
                    comps[LISTENER] = {**ls, "beliefs": lis.beliefs()}
                except Exception:
                    pass
        snap["components"] = comps
        return snap

    def publish_why(self) -> None:
        """room.why onto the show bus (want-only: the renderer asks for it). Never raises."""
        try:
            brain = self.brain()
            if brain is None or self.publish is None:
                return
            body = why_of(self.snapshot(brain), self.build_card(), wall_ms=time.time() * 1000.0)
            self.publish({"type": contract.ROOM_WHY, "data": body})
            self.why_published += 1
        except Exception:                          # noqa: BLE001
            self.why_errors += 1
            if self.why_errors <= 3:
                log.exception("api: room.why failed")

    def build_card(self) -> Optional[dict]:
        brain = self.brain()
        if brain is None:
            return None
        return card(self.snapshot(brain), self.engine(), describe=self.describe_state)

    def _changed(self) -> None:
        """A tap changed something: refresh every card now, and once more after a director tick."""
        self.events.refresh_soon()
        if self.late_refresh_s > 0:
            self.events.refresh_soon(self.late_refresh_s)

    def _guard(self, fn: Handler) -> Callable[[web.Request], Awaitable[web.StreamResponse]]:
        async def handler(request: web.Request) -> web.StreamResponse:
            brain = self.brain()
            if brain is None:
                return _err(503, "starting", STARTING)
            try:
                return await fn(request, brain)
            except asyncio.TimeoutError:
                return _err(503, "busy", BUSY)
            except asyncio.CancelledError:
                raise
            except web.HTTPException:
                raise
            except ValueError as e:
                code, detail = _code(e, "bad_request")
                return _err(400, code, detail or "That request wasn't understood.")
            except Exception as e:
                log.exception("api: %s %s failed", request.method, request.path)
                return _err(500, "failed", f"Something went wrong ({type(e).__name__}); the show carries on.")
        return handler

    @staticmethod
    async def _call(brain: Any, fn: Callable, *args: Any, timeout: Optional[float] = None) -> Any:
        if timeout is None:
            return await _maybe_await(brain.call(fn, *args))
        return await _maybe_await(brain.call(fn, *args, timeout=timeout))

    @staticmethod
    async def _body(request: web.Request) -> tuple[Any, Optional[web.Response]]:
        """(parsed JSON or None when empty, an error response when it isn't JSON)."""
        try:
            raw = await request.read()
        except Exception:
            return None, _err(400, "bad_request", "The request body couldn't be read.")
        if not raw or not raw.strip():
            return None, None
        try:
            return json.loads(raw.decode("utf-8")), None
        except (UnicodeDecodeError, ValueError):
            return None, _err(400, "bad_request", "The request body isn't JSON.")

    @staticmethod
    def _word(body: Any, key: str) -> Optional[str]:
        v = body.get(key) if isinstance(body, dict) else body if isinstance(body, str) else None
        return v.strip() if isinstance(v, str) and v.strip() else None

    # -- the card ---------------------------------------------------------------------------------------------------------
    async def _card(self, request: web.Request, brain: Any) -> web.Response:
        return _json(card(self.snapshot(brain), self.engine(), describe=self.describe_state))

    # -- nudges ------------------------------------------------------------------------------------------------------------
    async def _step(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad:
            return bad
        d = self._word(body, "dir")
        d = d.lower() if d else None
        if d not in STEP_DIRS:
            return _err(400, "bad_request", 'Say which way: "calmer" or "wilder".', choices=list(STEP_DIRS))
        director = _components(brain).get(DIRECTOR)
        if director is None:
            return _json({"steps": dict(NEUTRAL_STEPS), "chips": [], "done": False, "detail": MISSING[DIRECTOR]})
        out = await self._call(brain, director.step, d)
        self._changed()
        return _json(out)

    async def _palette_names(self, brain: Any) -> list[str]:
        """Every name PUT /palette takes: auto, art, then the whole catalog (``catalog()["palettes"]``), else the
        card's choices from the director's snapshot."""
        director = _components(brain).get(DIRECTOR)
        names = [PALETTE_AUTO, PALETTE_ART]
        found: list = []
        if director is not None:
            try:
                found = _d(await self._call(brain, director.catalog)).get("palettes") or []
            except Exception:
                found = []
            if not found:
                found = _d(_snap_of(director).get("palette")).get("choices") or []
        for c in found:
            n = c.get("name") if isinstance(c, dict) else None
            if isinstance(n, str) and n not in names:
                names.append(n)
        return names

    async def _palette(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad:
            return bad
        name = self._word(body, "name")
        if name is None:
            return _err(400, "bad_request", 'Say which palette: {"name": "auto" | "art" | <a palette>}.',
                        choices=await self._palette_names(brain))
        director = _components(brain).get(DIRECTOR)
        if director is None:
            return _json({"palette": {"pick": PALETTE_AUTO, "current": None}, "chips": [], "done": False,
                          "detail": MISSING[DIRECTOR]})
        try:
            out = await self._call(brain, director.set_palette, name)
        except ValueError as e:
            code, _ = _code(e, "unknown_palette")
            return _err(400, code, f"There's no palette called “{name}”.", choices=await self._palette_names(brain))
        self._changed()
        return _json(out)

    async def _action(self, request: web.Request, brain: Any) -> web.Response:
        t_s = time.monotonic()                      # clock words are stamped on arrival
        body, bad = await self._body(request)
        if bad:
            return bad
        do = self._word(body, "do")
        do = do.lower() if do else None
        if do in CLOCK_ACTIONS:
            fn = self.clock_action or getattr(getattr(brain, "ctx", None), "clock_action", None)
            if fn is None:
                return _json({"done": False, "clock": self._clock(brain), "detail": "The beat clock isn't running."})
            try:
                fn(do, t_s)                         # any thread: the clock queues it for show-rx
            except Exception as e:
                log.warning("api: clock action %s failed: %s", do, e)
                return _json({"done": False, "clock": self._clock(brain), "detail": "The beat clock didn't take that."})
            return _json({"done": True, "clock": self._clock(brain)})
        if do not in DIRECTOR_ACTIONS:
            return _err(400, "unknown_action", "That isn't something the card can do.",
                        choices=list(DIRECTOR_ACTIONS + CLOCK_ACTIONS))
        director = _components(brain).get(DIRECTOR)
        if director is None:
            return _json({"done": False, "chips": [], "look": {"id": None, "next_pending": False},
                          "detail": MISSING[DIRECTOR]})
        out = await self._call(brain, director.action, do)
        self._changed()
        return _json(out)

    def _clock(self, brain: Any) -> Optional[dict]:
        c = self.engine().get("clock")
        if isinstance(c, dict):
            return c
        fn = getattr(getattr(brain, "ctx", None), "clock", None)
        if callable(fn):
            try:
                got = fn()
            except Exception:
                return None
            return got if isinstance(got, dict) else None
        return None

    async def _nudge(self, request: web.Request, brain: Any) -> web.Response:
        nid = request.match_info.get("id", "")
        director = _components(brain).get(DIRECTOR)
        if director is None:
            return _json({"cleared": [], "chips": [], "detail": MISSING[DIRECTOR]})
        cleared = await self._call(brain, director.clear, nid)
        cleared = [str(x) for x in cleared] if isinstance(cleared, (list, tuple)) else []
        chips = await self._call(brain, _chips_after, director, cleared, nid)
        if cleared:
            self._changed()
        return _json({"cleared": cleared, "chips": chips})

    # -- vibes ---------------------------------------------------------------------------------------------------------------
    async def _vibes(self, request: web.Request, brain: Any) -> web.Response:
        director = _components(brain).get(DIRECTOR)
        if director is None:
            return _json(neutral_catalog(MISSING[DIRECTOR]))
        return _json(await self._call(brain, director.catalog))

    async def _vibe_get(self, request: web.Request, brain: Any) -> web.Response:
        director = _components(brain).get(DIRECTOR)
        if director is None:
            return _json(neutral_readback(MISSING[DIRECTOR]))
        return _json(self._readback(brain, await self._call(brain, director.readback)))

    @staticmethod
    def _with_listener(brain: Any, rb: Any) -> Any:
        """The readback's ``listening_effective``/``listening_why`` hold only the director's part (the vibe's
        ``listener``; the target's ``listening`` no longer gates, README S5.20). When it allows it, the listener's own
        gate has the last word (the AI switch, Auto and the mic, the music check, the sound, the model), from its last
        snapshot (at most one 1 Hz tick old)."""
        if not isinstance(rb, dict) or not rb.get("listening_effective"):
            return rb
        lis = _components(brain).get(LISTENER)
        if lis is None:
            return {**rb, "listening_effective": False, "listening_why": MISSING[LISTENER]}
        lv = _listener_view(_snap_of(lis))
        if not isinstance(lv.get("effective"), bool):
            return rb
        return {**rb, "listening_effective": lv["effective"], "listening_why": None if lv["effective"] else lv.get("why")}

    async def _vibe_put(self, request: web.Request, brain: Any) -> web.Response:
        """The room adapter's PUT (modes' show rows). Degrade, never refuse: an unknown vibe or a bad override is kept
        as asked and played as well as it can be (the read-back's ``known`` and ``problems`` say what); ``{"vibe":
        null}`` (or DELETE) is no feel. 400 only for a body that isn't a target at all."""
        body, bad = await self._body(request)
        if bad:
            return bad
        if body is None:
            return _err(400, "bad_target", 'Say which feel: {"vibe": <name> | null, "listening": true|false}.')
        return await self._set_vibe(request, brain, body)

    async def _vibe_delete(self, request: web.Request, brain: Any) -> web.Response:
        """No feel: the director goes neutral (the same as PUT {"vibe": null})."""
        return await self._set_vibe(request, brain, None)

    async def _set_vibe(self, request: web.Request, brain: Any, body: Any) -> web.Response:
        source = request.query.get("source", "mode")
        source = source if source in ("mode", "api") else "mode"
        director = _components(brain).get(DIRECTOR)
        if director is None:
            return _err(503, "unavailable", MISSING[DIRECTOR])
        try:
            out = await self._call(brain, director.set_target, body, source)
        except ValueError as e:
            code, detail = _code(e, "bad_target")
            return _err(400, "bad_target", detail or "That isn't a feel the room can set.",
                        choices=await self._vibe_names(brain, director))
        self._changed()
        return _json(self._readback(brain, out))

    def _readback(self, brain: Any, rb: Any) -> Any:
        """The director's read-back with the listener's part and this process's ``boot_id``."""
        rb = self._with_listener(brain, rb)
        return {**rb, "boot_id": self.boot_id} if isinstance(rb, dict) else rb

    async def _vibe_names(self, brain: Any, director: Any) -> list[str]:
        try:
            cat = await self._call(brain, director.catalog)
        except Exception:
            return []
        order = _d(cat).get("order")
        return [str(n) for n in order] if isinstance(order, list) else []

    # -- Describe it -----------------------------------------------------------------------------------------------------------
    def _describe_timeout(self, brain: Any) -> float:
        settings = getattr(getattr(brain, "ctx", None), "settings", None)
        v = _num(_d(_d(settings).get("listener")).get("describe_timeout_s"), None)
        return v if v is not None and v > 0 else DESCRIBE_TIMEOUT_S

    def _room_view(self, brain: Any) -> dict:
        room = _components(brain).get(ROOM)
        for fn in (getattr(room, "view", None), getattr(getattr(brain, "ctx", None), "room", None)):
            if callable(fn):
                try:
                    v = fn()
                except Exception:
                    continue
                if isinstance(v, dict):
                    return v
        return _snap_of(room)

    def describe_context(self, brain: Any) -> dict:
        """``{"vibe", "persona", "palettes", "palette_labels", "moods", "modes": [{"name", "label"}],
        "current_mode"}`` from the director's snapshot and the RoomView (README S3.9)."""
        ds = _snap_of(_components(brain).get(DIRECTOR))
        rb = _d(ds.get("readback"))
        eff = _d(rb.get("effective"))
        choices = [c for c in _d(ds.get("palette")).get("choices") or [] if isinstance(c, dict)
                   and isinstance(c.get("name"), str) and c["name"] != PALETTE_AUTO]
        names = [c["name"] for c in choices]
        if PALETTE_ART not in names:
            names.append(PALETTE_ART)
        labels = {c["name"]: c.get("label") or c["name"] for c in choices}
        labels.setdefault(PALETTE_ART, ART_CHOICE["label"])
        moods = _d(ds.get("direction")).get("moods") or eff.get("moods") or []
        room = self._room_view(brain)
        modes_cfg = _d(room.get("modes"))
        order = [n for n in room.get("order") or [] if isinstance(n, str)] or list(modes_cfg)
        modes = [{"name": n, "label": _d(modes_cfg.get(n)).get("label") or n} for n in order]
        return {"vibe": rb.get("vibe"), "persona": eff.get("persona") or "", "palettes": names,
                "palette_labels": labels, "moods": [m for m in moods if isinstance(m, str)], "modes": modes,
                "current_mode": room.get("current")}

    async def _describe(self, request: web.Request, brain: Any) -> web.Response:
        t0 = time.monotonic()
        body, bad = await self._body(request)
        if bad:
            return bad
        text = body.get("text") if isinstance(body, dict) else body if isinstance(body, str) else None
        if text is not None and not isinstance(text, str):
            return _err(400, "bad_request", 'Send the words as {"text": "..."}.')
        text = " ".join((text or "").split())[:DESCRIBE_MAX_CHARS]
        if not text:
            return _json(self._describe_done(t0, text, "keywords",
                                             "Say how it should feel, like “mellow” or “more energy”.", [], None))
        self.describe_state = {"state": "working", "text": text, "reply": None, "suggest": None}
        self._changed()
        comps = _components(brain)
        listener, director = comps.get(LISTENER), comps.get(DIRECTOR)
        context = self.describe_context(brain)
        intent: dict = {}
        via = "keywords"
        reply: Optional[str] = None
        if listener is None:
            reply = MISSING[LISTENER] + " Try the buttons instead."
        else:
            try:
                got = await self._call(brain, listener.describe, text, context,
                                       timeout=self._describe_timeout(brain) + 1.0)
                if isinstance(got, (list, tuple)) and len(got) == 2:
                    intent, via = _d(got[0]), str(got[1] or "keywords")
                else:
                    intent = _d(got)
            except asyncio.TimeoutError:
                reply = "That took too long; try again in a moment."
            except Exception as e:
                log.warning("api: describe failed: %s", e)
                reply = "I didn't catch that: try a mood like “mellow” or a palette like “ember”."
        applied: list = []
        if intent and reply is None:
            if director is None:
                reply = MISSING[DIRECTOR]
            else:
                try:
                    chips = await self._call(brain, director.apply_describe, intent)
                    applied = [c for c in chips if isinstance(c, dict)] if isinstance(chips, (list, tuple)) else []
                except asyncio.TimeoutError:
                    reply = BUSY
                except Exception as e:
                    log.warning("api: apply_describe failed: %s", e)
                    reply = "I understood, but couldn't change the look just now."
        if reply is None:
            reply = intent.get("reply") if isinstance(intent.get("reply"), str) and intent.get("reply") else \
                "I didn't catch that: try a mood like “mellow” or a palette like “ember”."
        suggest = None
        mode = intent.get("switch_mode")
        if isinstance(mode, str) and mode and mode != context.get("current_mode"):
            label = next((m["label"] for m in context["modes"] if m["name"] == mode), mode)
            suggest = {"mode": mode, "label": label}
        result = self._describe_done(t0, text, via, reply, applied, suggest)
        self.events.broadcast({"type": EV_DESCRIBE, "result": result})
        return _json(result)

    def _describe_done(self, t0: float, text: str, via: str, reply: str, applied: list,
                       suggest: Optional[dict]) -> dict:
        result = {"ok": True, "via": via, "text": text, "reply": reply, "applied": applied, "suggest": suggest,
                  "working_ms": round((time.monotonic() - t0) * 1000.0, 1)}
        self.describe_state = {"state": "done", "text": text or None, "reply": reply, "suggest": suggest}
        self._changed()
        return result

    # -- listening -------------------------------------------------------------------------------------------------------------
    async def _listening_get(self, request: web.Request, brain: Any) -> web.Response:
        listener = _components(brain).get(LISTENER)
        if listener is None:
            return _json(neutral_listener(MISSING[LISTENER]))
        return _json(await self._call(brain, listener.view))

    async def _listening_put(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad:
            return bad
        on = body.get("on") if isinstance(body, dict) else body
        if not isinstance(on, bool):
            return _err(400, "bad_request", 'Say {"on": true} or {"on": false}.')
        listener = _components(brain).get(LISTENER)
        if listener is None:
            return _json(neutral_listener(MISSING[LISTENER]))
        out = await self._call(brain, listener.set_on, on)
        self._changed()
        return _json(out)

    # -- the rig (Stage 5) -------------------------------------------------------------------------------------------------------
    async def _rig(self, request: web.Request, brain: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        if rig is None:
            return _json({"enabled": False, "why": NO_RIG})
        fn = getattr(rig, "snapshot_now", None) or getattr(rig, "snapshot", None)
        snap = dict(fn()) if callable(fn) else {}
        view = getattr(rig, "view", None)
        snap["registry_view"] = view() if callable(view) else None
        return _json(snap)

    async def _rig_frame(self, request: web.Request, brain: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        oid = request.query.get("output", "")
        view = rig.view() if rig is not None and callable(getattr(rig, "view", None)) else None
        choices = sorted((view or {}).get("outputs") or {})
        body = rig.frame_view(oid) if rig is not None and oid else None
        if body is None:
            return _err(404, "unknown_output", f"No output {oid!r} in config/room/fixtures.yaml." if oid else
                        "Say which output: ?output=<id>.", choices=choices)
        return _json(body)

    async def _rig_dmx(self, request: web.Request, brain: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        fn = getattr(rig, "dmx_view", None) if rig is not None else None
        return _json({"nodes": fn() if callable(fn) else {}})

    async def _record_failsafe(self, request: web.Request, brain: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        nid = request.match_info["id"]
        if rig is None or not rig.has_node(nid):
            view = rig.view() if rig is not None else None
            return _err(404, "unknown_node", f"No node {nid!r} in config/room/fixtures.yaml.",
                        choices=sorted((view or {}).get("nodes") or {}))
        return _json(await self._call(brain, rig.record_failsafe, nid))

    async def _hue_stream(self, request: web.Request, brain: Any) -> web.Response:
        """room's hue adapter (mode show): ``{lights, on}``, the complete set of hue.* lamps that follow the show (not a
        delta; ``[]`` or ``on: false`` is none) -> HueAnswer at once (it doesn't wait for streaming)."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        body = body if isinstance(body, dict) else {}
        lights, on = body.get("lights"), body.get("on", True)
        if not isinstance(lights, list) or not all(isinstance(x, str) for x in lights) or not isinstance(on, bool):
            return _err(400, "bad_request", "The body is {\"lights\": [fixture ids], \"on\": true or false}.")
        rig = _components(brain).get(RIG)
        if rig is None:
            return _json(contract.hue_answer(self._hue_off(NO_RIG), self.boot_id))
        return _json(contract.hue_answer(await self._call(brain, rig.hue_request, lights, on), self.boot_id))

    async def _hue_status(self, request: web.Request, brain: Any) -> web.Response:
        """room's hue adapter (each read while a lamp follows the show, 2 s timeout): HueAnswer, with ``lights`` (what
        room asked, unfiltered) and ``boot_id`` for its re-sync (68's addendum)."""
        rig = _components(brain).get(RIG)
        if rig is None:
            return _json(contract.hue_answer(self._hue_off(NO_RIG), self.boot_id))
        return _json(contract.hue_answer(rig.hue_status(), self.boot_id))

    @staticmethod
    def _hue_off(detail: str) -> dict:
        return dict(contract.hue_off_status(None, detail))

    # -- Hue ----------------------------------------------------------------------------------------------------------------------
    def _hue_handed_to_show(self) -> bool:
        """Before the brain runs: patch.yaml says ``hue.streamer: show`` and fixtures.yaml enables a hue output, so a
        predecessor that crashed while streaming may still hold the area. A file that's there but doesn't read counts
        as yes (room then checks the bridge itself, always safe); a missing one as no (room's own defaults)."""
        import yaml
        try:
            patch = yaml.safe_load(self.patch_path.read_text()) or {}
        except FileNotFoundError:
            return False
        except Exception:                                   # noqa: BLE001
            return True
        hue = patch.get("hue") if isinstance(patch, dict) else None
        if not isinstance(hue, dict) or hue.get("streamer") != "show":
            return False
        try:
            fx = yaml.safe_load(self.fixtures_path.read_text()) or {}
        except FileNotFoundError:
            return False
        except Exception:                                   # noqa: BLE001
            return True
        outs = fx.get("outputs") if isinstance(fx, dict) else None
        return isinstance(outs, dict) and any(isinstance(o, dict) and o.get("driver") == "hue"
                                              and o.get("enabled") is True for o in outs.values())

    async def _hue_release(self, request: web.Request) -> web.Response:
        """Stage 5: with a hue output running here, its release (HueRelease once the lamps are writable). Otherwise
        ``released`` at once, as Stage 3 did: room_service streams Hue. Answers before the brain runs too, so a mode's
        exit never waits on show_service: ``released`` at once, unless patch.yaml hands the area to show_service with a
        hue output on (``HUE_STARTING``: released false, why ``starting``; room checks the bridge itself)."""
        out = dict(HUE_ROOM)
        brain = self.brain()
        if brain is None and await asyncio.to_thread(self._hue_handed_to_show):
            return _json(dict(HUE_STARTING))
        rig = _components(brain).get(RIG) if brain is not None else None
        if rig is not None and callable(getattr(rig, "hue_running", None)) and rig.hue_running():
            body, bad = await self._body(request)
            if bad is not None:
                return bad
            lights = body.get("lights") if isinstance(body, dict) else None
            if lights is not None and (not isinstance(lights, list) or not all(isinstance(x, str) for x in lights)):
                return _err(400, "bad_request", "lights must be a list of fixture ids (or left out: every lamp).")
            try:
                got = await self._call(brain, rig.hue_release, lights, timeout=rig.hue_release_timeout_s() + 2.0)
            except asyncio.TimeoutError:
                return _json({"released": False, "state": contract.hue_room_state(rig.hue_status()),
                              "why": "timeout", "still_following": [], "waited_ms": None, "via": "show"})
            except ValueError as e:
                code, detail = _code(e, "bad_request")
                return _err(400, code, detail or "That request wasn't understood.")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("api: hue release failed")
                return _err(500, "failed", f"The release failed ({type(e).__name__}); the lamps may still stream.")
            if isinstance(got, dict):
                if got.get("state") not in contract.HUE_ROOM_STATES:
                    got = {**got, "state": contract.hue_room_state(rig.hue_status())}
                return _json(got)
        director = _components(brain).get(DIRECTOR) if brain is not None else None
        if director is not None:
            try:
                got = await self._call(brain, director.release_hue)
                if isinstance(got, dict):
                    out = {**out, **got, "released": True, "state": "idle"}
            except Exception as e:
                log.warning("api: release_hue failed (answered released anyway): %s", e)
        return _json(out)

    # -- Auto ------------------------------------------------------------------------------------------------------------------
    async def _auto_get(self, request: web.Request, brain: Any) -> web.Response:
        ap = _components(brain).get(AUTOPILOT)
        if ap is None:
            return _json(neutral_autopilot(MISSING[AUTOPILOT]))
        return _json(await self._call(brain, ap.state))

    async def _auto_put(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad:
            return bad
        on = body.get("on") if isinstance(body, dict) else body
        if not isinstance(on, bool):
            return _err(400, "bad_request", 'Say {"on": true} or {"on": false}.')
        ap = _components(brain).get(AUTOPILOT)
        if ap is None:
            return _json(neutral_autopilot(MISSING[AUTOPILOT]))
        try:
            out = await self._call(brain, ap.set_on, on, timeout=contract.AUTO_CALL_TIMEOUT_S)
        except RuntimeError as e:
            return self._room_error(e)
        except ValueError as e:
            return self._room_refused(e, "The room didn't take that")
        self._changed()
        return _json(out)

    @staticmethod
    def _room_error(e: BaseException) -> web.Response:
        code, detail = _code(e, "room_unreachable")
        if code != "room_unreachable":
            raise e
        return _err(502, "room_unreachable", "The room isn't answering, so Auto can't change that right now.")

    @staticmethod
    def _room_refused(e: ValueError, words: str) -> web.Response:
        """The room's own refusal (autopilot raises ValueError(<room error code>) with ``.detail``/``.status``):
        502 with the room's code and words, so the card can say what happened."""
        code, parsed = _code(e, "room_refused")
        detail = str(getattr(e, "detail", "") or parsed or "").strip()
        status = getattr(e, "status", None)
        return _err(502, code, words + (f": {detail}" if detail else "."), via="room",
                    room_status=status if isinstance(status, int) else None)

    async def _auto_undo(self, request: web.Request, brain: Any) -> web.Response:
        ap = _components(brain).get(AUTOPILOT)
        if ap is None:
            return _json({"undone": False, "why": "unavailable", "detail": MISSING[AUTOPILOT]})
        try:
            out = await self._call(brain, ap.undo, timeout=contract.AUTO_CALL_TIMEOUT_S)
        except LookupError as e:
            code, _ = _code(e, "nothing_to_undo")
            return _json({"undone": False, "why": code, "detail": "There's nothing to undo."})
        except RuntimeError as e:
            return self._room_error(e)
        except ValueError as e:
            return self._room_refused(e, "The room didn't take the undo")
        self._changed()
        return _json(out)

    async def _auto_resume(self, request: web.Request, brain: Any) -> web.Response:
        ap = _components(brain).get(AUTOPILOT)
        if ap is None:
            return _json(neutral_autopilot(MISSING[AUTOPILOT]))
        out = await self._call(brain, ap.resume)
        self._changed()
        return _json(out)

    async def _auto_rules(self, request: web.Request, brain: Any) -> web.Response:
        """GET /api/v1/autopilot/rules -> {"modes": [contract.RuleView + "auto", "unknown_words"], "revision",
        "reachable", "error"}: every mode of the room's config, in its order. A mode with no ``autopilot`` key starts
        only when someone picks it (every rule None); a rule absent, null or {} is never by itself; ``from`` absent or
        [] is any mode (None); ``leave_to`` absent is "previous". Paths name nodes in the block ("enter_when",
        "enter_when.any[1]", "leave_when.all[0].not", "from", "leave_to")."""
        ap = _components(brain).get(AUTOPILOT)
        if ap is None or not callable(getattr(ap, "rules", None)):
            return _json({"modes": [], "revision": None, "reachable": False, "error": None,
                          "detail": MISSING[AUTOPILOT]})
        return _json(await self._call(brain, ap.rules))

    async def _auto_check(self, request: web.Request, brain: Any) -> web.Response:
        """POST /api/v1/autopilot/check {"mode": "<name>" (optional), "autopilot": {one mode's block} | null | absent,
        "modes": ["<unsaved mode>", ...] (optional)} -> {"ok": no error, "problems": [{"path", "text", "level":
        error|warn}], "enter", "suggest", "leave" (sentences, None: no rule), "summary", "from", "leave_to",
        "unknown_words", "available", "live", "nodes", "now", "mode", "label"}. "from" and "leave_to" may name the
        room's modes, ``modes`` and (leave_to) "previous". The same empty shapes as GET /api/v1/autopilot/rules: an
        absent or null block is ok (only when someone picks it). Never saves anything. 400 only for a body that isn't
        an object, a ``mode`` that isn't a name or ``modes`` that isn't a list of names."""
        body, bad = await self._body(request)
        if bad:
            return bad
        if not isinstance(body, dict):
            return _err(400, "bad_request", 'Send {"mode": "<name>", "autopilot": {...}}.')
        mode = body.get("mode")
        if mode is not None and not (isinstance(mode, str) and mode.strip()):
            return _err(400, "bad_request", '"mode" is the name of the mode this block belongs to.')
        extra = body.get("modes")
        if extra is not None and not (isinstance(extra, list) and all(isinstance(m, str) and m for m in extra)):
            return _err(400, "bad_request", '"modes" lists the names of modes being made in the editor.')
        ap = _components(brain).get(AUTOPILOT)
        if ap is None or not callable(getattr(ap, "check_block", None)):
            return _json({"ok": False, "checked": False, "detail": MISSING[AUTOPILOT],
                          "problems": [{"path": "", "text": "Auto isn't running, so this couldn't be checked",
                                        "level": "error"}],
                          "enter": None, "suggest": None, "leave": None, "unknown_words": [], "live": {}, "nodes": {}})
        out = await self._call(brain, ap.check_block, mode.strip() if mode else None, body.get("autopilot"),
                               list(extra or []))
        return _json(out)

    async def _situations(self, request: web.Request, brain: Any) -> web.Response:
        sit = _components(brain).get(SITUATIONS)
        snap = _snap_of(sit)
        vocab = snap.get("vocabulary")
        if not isinstance(vocab, list) and sit is not None and callable(getattr(sit, "vocabulary", None)):
            vocab = await self._call(brain, sit.vocabulary)
        out = {"vocabulary": vocab if isinstance(vocab, list) else [],
               "readings": _d(snap.get("readings")), "combinators": list(contract.COMBINATORS)}
        if sit is None:
            out["detail"] = MISSING[SITUATIONS]
        return _json(out)

    # -- test sources (98's API) ----------------------------------------------------------------------------------------------
    async def _test_get(self, request: web.Request, brain: Any) -> web.Response:
        src = _components(brain).get(SOURCES)
        if src is None:
            return _json({"sources": {s: neutral_source(s, MISSING[SOURCES]) for s in TEST_SOURCES},
                          "pieces": {s: [] for s in TEST_SOURCES}, "detail": MISSING[SOURCES]})
        status = _d(await self._call(brain, src.status))
        pieces = _d(await self._call(brain, src.pieces))
        return _json({"sources": {s: status.get(s) or neutral_source(s) for s in TEST_SOURCES},
                      "pieces": {s: pieces.get(s) or [] for s in TEST_SOURCES}})

    async def _test_post(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad:
            return bad
        source = (self._word(body, "source") or "").lower()
        piece = self._word(body, "piece") if isinstance(body, dict) else None
        if source not in TEST_SOURCES:
            return _err(400, "unknown_source", SAY_SOURCE, choices=list(TEST_SOURCES))
        src = _components(brain).get(SOURCES)
        if src is None:
            return _json(neutral_source(source, MISSING[SOURCES]))
        try:
            out = await self._call(brain, src.start_test, source, piece)
        except ValueError as e:
            code, _ = _code(e, "unknown_piece")
            if code == "unknown_source":
                return _err(400, code, SAY_SOURCE, choices=list(TEST_SOURCES))
            choices: list = []
            try:
                pieces = _d(await self._call(brain, src.pieces)).get(source) or []
                choices = [p.get("id") for p in pieces if isinstance(p, dict) and p.get("id")]
            except Exception:
                pass
            return _err(400, "unknown_piece", f"There's no {source} piece called “{piece}”.", choices=choices)
        self._changed()
        return _json(out)

    async def _test_delete(self, request: web.Request, brain: Any) -> web.Response:
        source = request.match_info.get("source")
        if source is None:
            body, bad = await self._body(request)
            if bad:
                return bad
            source = self._word(body, "source") if body is not None else None
        source = source.lower() if isinstance(source, str) else None
        src = _components(brain).get(SOURCES)
        if src is None:
            return _json({"stopped": [], "detail": MISSING[SOURCES]})
        if source is not None and source not in TEST_SOURCES:
            return _json({"stopped": [], "detail": f"There's no test source called “{source}”.",
                          "choices": list(TEST_SOURCES)})
        stopped = await self._call(brain, src.stop_test, source)
        stopped = [str(s) for s in stopped] if isinstance(stopped, (list, tuple)) else []
        if stopped:
            self._changed()
        return _json({"stopped": stopped})

    # -- the console ---------------------------------------------------------------------------------------------------------
    async def _look(self, request: web.Request, brain: Any) -> web.Response:
        snap = _snap_of(_components(brain).get(DIRECTOR))
        direction, look = snap.get("direction"), snap.get("look")
        return _json({"direction": direction if isinstance(direction, dict) else None,
                      "look": look if isinstance(look, dict) else None})

    async def _log(self, request: web.Request, brain: Any) -> web.Response:
        kind = request.query.get("kind", "director")
        if kind not in LOG_KINDS:
            return _err(400, "bad_request", f"There's no log called “{kind}”.", choices=list(LOG_KINDS))
        try:
            n = int(request.query.get("n", LOG_N_DEFAULT))
        except ValueError:
            n = LOG_N_DEFAULT
        n = max(1, min(LOG_N_MAX, n))
        comp = _components(brain).get(kind)
        if comp is not None and callable(getattr(comp, "log", None)):
            entries = await self._call(brain, comp.log, n)
            entries = list(entries)[-n:] if isinstance(entries, (list, tuple)) else []
            return _json({"entries": entries})
        path = self.log_files.get(kind)
        entries = tail_jsonl(path, n) if path is not None else []
        detail = MISSING.get(kind, "") if comp is None else "It keeps no log here."
        if path is not None:
            detail += " These entries come from its log file."
        return _json({"entries": entries, "detail": detail.strip()})

    def stats(self) -> dict:
        return {"events": self.events.stats(), "mounted": len(self.mounted)}


def _chips_after(director: Any, cleared: list[str], nid: str) -> list:
    """The chips after a clear (on show-brain): ``director.chips()`` when it exists, else the snapshot's chips minus
    the cleared ones (a snapshot may lag one tick)."""
    fn = getattr(director, "chips", None)
    if callable(fn):
        got = fn()
        if isinstance(got, (list, tuple)):
            return [c for c in got if isinstance(c, dict)]
    chips = [c for c in _snap_of(director).get("chips") or [] if isinstance(c, dict)]
    if nid == "all" and cleared:
        return []
    gone = set(cleared)
    return [c for c in chips if c.get("id") not in gone]


def routes(brain_ref: Callable[[], Any], engine_ref: Optional[Callable[[], Optional[dict]]] = None,
           **kw: Any) -> Api:
    """``Bus(routes=api.routes(brain_ref, engine_ref))``. ``brain_ref()``: the Brain, or None until it runs.
    ``engine_ref()``: ``{"active_audio", "features": {"energy", "age_ms", "stream"}, "clock": ClockData | None}``.
    Keywords: ``clock_action`` (else ``brain.ctx.clock_action``), ``log_files``, ``max_clients``, ``queue_max``,
    ``card_hz``, ``patch_path`` and ``fixtures_path`` (a Hue release before the brain runs reads them; tests: tmp
    files). The result mounts with ``api_obj(app)`` and its ``notify`` goes in ``BrainContext.notify``."""
    return Api(brain_ref, engine_ref, **kw)
