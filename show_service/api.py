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
  - The Lights page (README S5.23): ``GET /api/v1/rig`` carries ``fixtures`` (contract.RigFixtureRow each), ``GET
    /api/v1/rig/types`` (a list of contract.RigTypeRow), ``POST /api/v1/rig/fixtures`` and ``PUT``/``DELETE
    /api/v1/rig/fixtures/{id}`` (fixtures.added.yaml through Rig.add_fixture etc.: ``{fixture, problems}``,
    ``{removed}``; a light that can't be added as asked is 400 with ``problems``, an unknown id 404, a hand-written one
    409),
    ``POST /api/v1/rig/fixtures/{id}/identify {seconds?}`` (``{ok, seconds}`` or ``{ok: false, why}``), and room's
    DMX-lights device's ``POST``/``GET /api/v1/rig/follow`` (``{fixtures: {id: "dmx" | "off" | {hex, bri}}}``).
    Without a rig component the writes answer 503 ``unavailable`` (room's adapter retries).
  - Moving lights, the Room map and the live view (docs/LIGHTS.md §8.1, README S5.30): ``POST
    /api/v1/rig/fixtures/{id}/jog`` (Rig.jog, direct: no brain hop), ``.../setup`` (home, beam, limits and the rest of
    fixtures.setup.yaml; "here" reads the light's hold: 409 ``not_held`` without one), ``.../positions`` (+ ``DELETE
    .../positions/{name}``; a calibrated mover's new name becomes a room point where its beam lands), ``.../teach`` (+
    ``DELETE .../teach/{n}``, ``DELETE .../teach``: the samples and the pose solve), ``GET /api/v1/rig/space`` (the
    SpaceView and every aimable ``targets``), ``PUT /api/v1/rig/space/room``, ``PUT``/``DELETE
    /api/v1/rig/space/{appliances|points|regions}/{id}`` and ``GET /api/v1/rig/live``. ``GET /api/v1/rig`` gains
    ``lights_ui`` (show.yaml ``lights.jog``) and each row's ``caps`` and ``setup``. Writes run on show-brain, then
    ``Rig.reload_now``. A missing piece degrades: no space module, an empty map with ``error``; no hold in the rig,
    ``{held: false, why}``; the fixture routes answer 503 without a rig, the map's work without one.
  - Machine routes (``PUT``/``DELETE /api/v1/vibe``, the room adapter's) answer 503 ``unavailable`` without a
    director, so the adapter retries. ``PUT /api/v1/vibe`` keeps any target as asked (an unknown vibe plays neutral, a
    bad override is dropped; the read-back's ``known`` and ``problems`` say so) and 400s only a body that isn't a
    target; ``{"vibe": null}`` and DELETE are no feel. Every read-back carries ``boot_id`` (this process).
  - ``GET``/``PUT /api/v1/listening`` (owner, 20 Oct): the PUT takes ``{"on"?: bool, "mode"?: bool}`` (at least
    one; a bare bool is ``{"on": ...}``): ``on`` is the Room page's AI listening switch, ``mode`` the mode's
    permission (room_service's per-mode device ``show.ai``). Both answer the ListenerView (with ``mode``) and
    ``boot_id``, so room re-sends ``mode`` after a show_service restart.
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
import datetime
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
RECORDER = "recorder"                                # the Room page's Record (recorder.py, 22 Oct)
NO_RECORDER = "Recording isn't available here (recorder.py isn't loaded)."
RECORD_TIMEOUT_S = 30.0                              # Stop encodes and writes the take (in a worker thread)
RECORDING_FILE_TIMEOUT_S = 10.0                      # a rename or delete
UNKNOWN_RECORDING = "There's no recording with that id (it may have been deleted)."
NO_RIG = "The rig isn't running (outputs.py isn't loaded), so no light is driven from here."
#: README S5.23: the Lights page's writes answer 400 for a light that can't be added as asked, except these
RIG_ERROR_STATUS: dict[str, int] = {"unknown_fixture": 404, "in_hand_file": 409, "no_registry": 503, "not_saved": 500,
                                    "not_held": 409, "unavailable": 503, "not_found": 404, "added_unreadable": 409,
                                    "setup_unreadable": 409}
FOLLOW_CHOICES: list = [*contract.FOLLOW_WORDS, {"hex": "#rrggbb", "bri": "0..100"}]
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
    ("GET", "/api/v1/listening"), ("PUT", "/api/v1/listening"), ("GET", "/api/v1/listening/clips/{name}"),
    ("POST", "/api/v1/hue/release"), ("POST", "/api/v1/hue/stream"), ("GET", "/api/v1/hue/status"),
    ("GET", "/api/v1/rig"), ("GET", "/api/v1/rig/frame"), ("GET", "/api/v1/rig/dmx"),
    ("POST", "/api/v1/rig/nodes/{id}/record-failsafe"),
    ("GET", "/api/v1/rig/types"), ("POST", "/api/v1/rig/fixtures"), ("PUT", "/api/v1/rig/fixtures/{id}"),
    ("DELETE", "/api/v1/rig/fixtures/{id}"), ("POST", "/api/v1/rig/fixtures/{id}/identify"),
    ("GET", "/api/v1/rig/follow"), ("POST", "/api/v1/rig/follow"),
    ("POST", "/api/v1/rig/fixtures/{id}/jog"), ("POST", "/api/v1/rig/fixtures/{id}/setup"),
    ("POST", "/api/v1/rig/fixtures/{id}/positions"), ("DELETE", "/api/v1/rig/fixtures/{id}/positions/{name}"),
    ("POST", "/api/v1/rig/fixtures/{id}/teach"), ("DELETE", "/api/v1/rig/fixtures/{id}/teach/{n}"),
    ("DELETE", "/api/v1/rig/fixtures/{id}/teach"),
    ("GET", "/api/v1/rig/space"), ("PUT", "/api/v1/rig/space/room"),
    ("PUT", "/api/v1/rig/space/appliances/{id}"), ("DELETE", "/api/v1/rig/space/appliances/{id}"),
    ("PUT", "/api/v1/rig/space/points/{name}"), ("DELETE", "/api/v1/rig/space/points/{name}"),
    ("PUT", "/api/v1/rig/space/regions/{name}"), ("DELETE", "/api/v1/rig/space/regions/{name}"),
    ("GET", "/api/v1/rig/live"),
    ("GET", "/api/v1/autopilot"), ("PUT", "/api/v1/autopilot"), ("POST", "/api/v1/autopilot/undo"),
    ("POST", "/api/v1/autopilot/resume"), ("GET", "/api/v1/autopilot/rules"), ("POST", "/api/v1/autopilot/check"),
    ("GET", "/api/v1/situations"),
    ("GET", "/api/v1/test"), ("POST", "/api/v1/test"), ("DELETE", "/api/v1/test"), ("DELETE", "/api/v1/test/{source}"),
    ("GET", "/api/v1/piano/recordings"), ("POST", "/api/v1/piano/record"),
    ("GET", "/api/v1/piano/recordings/{id}.mid"), ("PATCH", "/api/v1/piano/recordings/{id}"),
    ("DELETE", "/api/v1/piano/recordings/{id}"),
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
AUTO_CHOICE: dict = {"name": PALETTE_AUTO, "label": "Let it choose", "family": None, "swatch": []}   # 09's F10
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
MIC_MUTED = "Room mic switched off"                # 09's audit H7: the Room page's own word for its Mute
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
                     "age_s": int(lk["age_s"]) if isinstance(lk.get("age_s"), (int, float)) else None,
                     "together": _cut(_d(cb.get("look")).get("together"))}}   # docs/ENSEMBLE.md §8 item 8 (ff's HUD)


def card(brain_snapshot: Optional[dict], engine: Optional[dict], *, describe: Optional[dict] = None) -> dict:
    """``contract.CardData`` from the brain's snapshot (``{"components": {name: snapshot}}``) and the engine's
    ``{"active_audio", "features": {"energy", "age_ms", "stream"}, "clock"}``. Pure; any missing piece reads neutral.

    hearing.input is the active stream's raw meter ({peak_dbfs, rms_dbfs} over the last 0.5 s) and hearing.input_trace
    its last 10 closed 100 ms bins ({seq, hz, peak_dbfs: [...], rms_dbfs: [...]}, oldest first; seq numbers the newest,
    so a client appends only bins newer than the last it drew); both None without active audio. hearing.level is
    energy (loudness against the mode's expected loudness, gated), not a meter. hearing.mic is inputs.Inputs.mic():
    {stream, device, chosen} for the active stream (None without one).

    hearing.text, first match wins: why nothing arrives when no sound comes in (the mic on demand first: "Room mic
    switched off" /
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
                "next_pending": bool(director.get("next_pending")),
                "together": _d(director.get("ensemble")).get("line")}    # docs/ENSEMBLE.md §9: "Playing together: …"

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
                     # owner, 21 Oct: one of listener.STATUS_WORDS, and the last round trips and skips, newest first
                     "status": lview.get("status") if isinstance(lview.get("status"), str) else None,
                     "history": [dict(x) for x in lview.get("history") or [] if isinstance(x, dict)],
                     "mode": bool(lview.get("mode")),     # show.ai's last word (shown, never a gate since 21 Oct)
                     "spend_today_usd": round(_num(lview.get("spend_today_usd"), 0.0) or 0.0, 4),
                     "est_evening_usd": None if est is None else round(est, 2),
                     "provider": lview.get("provider") if isinstance(lview.get("provider"), str) else None,
                     # README S5.22: what the AI hears in a line, its recommended approach, and the cue sheet
                     "character": lview.get("character") if isinstance(lview.get("character"), str) else None,
                     "approach": dict(lview["approach"]) if isinstance(lview.get("approach"), dict) else None,
                     "armed": [dict(x) for x in lview.get("armed") or [] if isinstance(x, dict)]}
    else:
        listening = {"on": False, "effective": False, "why": MISSING[LISTENER], "status": None, "history": [],
                     "mode": False, "spend_today_usd": 0.0,
                     "est_evening_usd": None, "provider": None, "character": None, "approach": None, "armed": []}

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
    return {"on": False, "mode": False, "effective": False, "why": why, "status": "Off", "detail": why,
            "history": [], "stream": None, "next_in_s": None,
            "cadence_s": 0.0, "fast": None, "in_flight": 0, "calls_today": 0, "spend_today_usd": 0.0,
            "budget_usd": None, "model": None, "last": None, "errors": 0, "provider": None, "est_evening_usd": None,
            "estimate": None, "character": None, "approach": None, "armed": []}


def neutral_autopilot(line: str) -> dict:
    return {"on": None, "state": "off", "why": None, "line": line, "since_s": 0.0, "until_in_s": None,
            "entered": None, "hold": None, "last": None, "chip": None, "suggest": None, "suppressed": [],
            "candidates": [], "settings": {}, "room": {"reachable": False, "revision": None, "current": None,
                                                       "transition_running": False}}


def neutral_source(source: str, why: Optional[str] = None) -> dict:
    player = contract.TEST_SOURCE_PLAYERS.get(source) or {}
    return {"source": source, "state": "off", "piece": None, "label": None, "stream": player.get("stream", ""),
            "sender": "", "pid": None, "since_s": None, "error": why,
            "instrument": None, "output": None, "once": False, "progress": None}


#: GET /api/v1/test's sound choices when the sources component can't say (an older one, or none)
NO_TEST_CHOICES: dict[str, Any] = {"instruments": [], "default_instrument": None, "outputs": []}
NO_RECORDINGS: dict[str, Any] = {"recording": {"state": "off", "since_s": None, "notes": 0, "duration_s": 0.0},
                                 "recordings": []}


def content_disposition(title: str) -> str:
    """``attachment`` with the title as the file name: an ASCII ``filename`` and the exact one as ``filename*``."""
    from urllib.parse import quote
    safe = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", title).strip(" ._") or "recording"
    return f"attachment; filename=\"{safe[:100]}.mid\"; filename*=UTF-8''{quote(title + '.mid', safe='')}"


# -- moving lights and the Room map (docs/LIGHTS.md §8.1; pure) ------------------------------------------------------------
#: show.yaml ``lights.jog``'s page keys (GET /api/v1/rig ``lights_ui``). Starting points, as in show.yaml's own block
#: (docs/LIGHTS.md §12): show.yaml wins, key by key.
JOG_UI: dict[str, Any] = {"send_hz": 20.0, "idle_s": 60.0, "nudge_deg": {"fine": 1.0, "coarse": 5.0},
                          "drag_deg_per_px": {"fine": 0.1, "coarse": 0.5}}
#: POST .../setup's keys. PASS_KEYS go to fixtures.SetupStore.patch as sent (it validates them).
SETUP_BODY_KEYS: tuple[str, ...] = ("home", "beam", "limit", "limits", "slots", "max_speed", "range", "jog_flip",
                                    "strobe_hz_max")
PASS_KEYS: tuple[str, ...] = ("slots", "max_speed", "range", "jog_flip", "strobe_hz_max")
BEAM_ATTRS: tuple[str, ...] = ("zoom", "focus", "iris", "frost")     # setup ``beam: "here"`` keeps these
AXES: tuple[str, ...] = ("pan", "tilt")
LIMIT_ENDS: tuple[str, ...] = ("here", "low", "high")   # here: the end nearer the held value (docs/LIGHTS.md §8.1)
AXIS_WORDS: dict[str, str] = {"pan": "turn", "tilt": "tilt"}
#: a light's full pan/tilt range when neither its type nor its setup ``range`` says: ofl.py's UNKNOWN_PAN_DEG and
#: UNKNOWN_TILT_DEG (docs/LIGHTS.md §2.2), read from there when it has them
UNKNOWN_RANGE_DEG: dict[str, float] = {"pan": 540.0, "tilt": 270.0}
FRONTS: tuple[str, ...] = ("north", "east", "south", "west")
ROOM_KEYS: tuple[str, ...] = ("size", "front")
APPLIANCE_KEYS: tuple[str, ...] = ("at", "mount", "plane")
POINT_KEYS: tuple[str, ...] = ("at", "label", "device")
REGION_KEYS: tuple[str, ...] = ("surface", "centre", "size", "label")
NO_SPACE = "The Room map isn't available yet (show_service needs updating)."
NO_SETUP = "This light's settings can't be kept yet (show_service needs updating)."
NOT_HELD = "Open Move it first."
CANT_MOVE = "This light can't be moved from here yet."
NO_LIVE = "The live view isn't available yet (show_service needs updating)."
_PLACE_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")      # fixtures.ID_RE: fixture, point and region ids
_SLUG = re.compile(r"[^a-z0-9]+")
_ARTICLE = re.compile(r"^(the|a|an)\s+")


class Refusal(ValueError):
    """A moving-light route's own refusal, shaped like fixtures.Refused (``code``, ``detail``, ``extra``)."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.extra = code, detail, extra


def place_id(words: str) -> str:
    """A place's or a position's id from its words, as the page makes them: "The piano" -> "piano", "Dance floor" ->
    "dance-floor" (lower case, a leading the/a/an dropped, runs of anything else one dash; at most 64). An id already
    in fixtures.py's ID_RE shape is kept as it is."""
    s = words.strip()
    if _PLACE_ID.match(s):
        return s
    s = _ARTICLE.sub("", s.lower())
    return _SLUG.sub("-", s).strip("-")[:64].strip("-")


def place_words(name: str) -> str:
    """Page words for a name with no label: "mirror_ball" -> "Mirror ball"."""
    s = re.sub(r"[-_.]+", " ", name).strip()
    return s[:1].upper() + s[1:] if s else name


def _pair(v: Any) -> Optional[tuple[float, float]]:
    if isinstance(v, (list, tuple)) and len(v) == 2:
        a, b = _num(v[0], None), _num(v[1], None)
        if a is not None and b is not None:
            return (min(a, b), max(a, b))
    return None


def _vec3(v: Any) -> Optional[tuple[float, float, float]]:
    if isinstance(v, (list, tuple)) and len(v) == 3:
        xs = [_num(x, None) for x in v]
        if all(x is not None for x in xs):
            return (float(xs[0]), float(xs[1]), float(xs[2]))      # type: ignore[arg-type]
    return None


def _deg(x: float) -> float:
    return round(float(x), 2)


def axis_ranges(caps: Any, setup: Any) -> dict[str, tuple[float, float]]:
    """(lo, hi) centred degrees per axis: the light's caps (``pan``/``tilt`` min and max), else ± half its setup
    ``range``, else ± half UNKNOWN_RANGE_DEG."""
    out: dict[str, tuple[float, float]] = {}
    for ax in AXES:
        c = _d(_d(caps).get(ax))
        lo, hi = _num(c.get("min"), None), _num(c.get("max"), None)
        if lo is None or hi is None or hi <= lo:
            full = _num(_d(_d(setup).get("range")).get(ax), None)
            full = full if full is not None and full > 0 else _unknown_range(ax)
            lo, hi = -full / 2.0, full / 2.0
        out[ax] = (float(lo), float(hi))
    return out


def _unknown_range(ax: str) -> float:
    try:
        from . import ofl
        v = _num(getattr(ofl, "UNKNOWN_PAN_DEG" if ax == "pan" else "UNKNOWN_TILT_DEG", None), None)
    except Exception:                                    # noqa: BLE001
        v = None
    return v if v is not None and v > 0 else UNKNOWN_RANGE_DEG[ax]


def held_position(here: Optional[Mapping[str, Any]]) -> tuple[float, float]:
    """The held (pan, tilt), or Refusal ``no_position`` for a light that doesn't report both."""
    h = _d(here)
    pan, tilt = _num(h.get("pan"), None), _num(h.get("tilt"), None)
    if pan is None or tilt is None:
        raise Refusal("no_position", "This light doesn't turn and tilt, so it has no position to keep.")
    return _deg(pan), _deg(tilt)


def setup_patch(body: Mapping[str, Any], current: Mapping[str, Any], here: Optional[Mapping[str, Any]],
                caps: Any) -> dict:
    """POST .../setup's body -> fixtures.SetupStore.patch's patch. ``"here"`` reads the held values (``here``: the
    hold's target over its now). ``limit: {axis, end}`` moves one end of that axis's limits to the held value: ``low``
    or ``high`` that end, ``here`` the end nearer it (docs/LIGHTS.md §8.1); with no limits set the ends start at the
    full range, and the result is always sorted, so two taps at either end make that span. Refusal on a bad shape."""
    patch: dict[str, Any] = {k: body[k] for k in PASS_KEYS if k in body}
    if "home" in body:
        v = body["home"]
        if v == "here":
            pan, tilt = held_position(here)
            v = {"pan": pan, "tilt": tilt}
        patch["home"] = v
    if "beam" in body:
        v = body["beam"]
        if v == "here":
            h = _d(here)
            v = {a: round(float(h[a]), 3) for a in BEAM_ATTRS if _num(h.get(a), None) is not None}
            if not v:
                raise Refusal("no_beam", "This light has no beam settings to keep.")
        patch["beam"] = v
    if "limits" in body:
        patch["limits"] = body["limits"]
    if "limit" in body:
        lim = body["limit"]
        if not isinstance(lim, Mapping) or lim.get("axis") not in AXES or lim.get("end", "here") not in LIMIT_ENDS:
            raise Refusal("bad_request", 'A limit is {"axis": "pan" | "tilt", "end": "here" | "low" | "high"}.',
                          choices=list(LIMIT_ENDS))
        ax, end = str(lim["axis"]), str(lim.get("end", "here"))
        v = _num(_d(here).get(ax), None)
        if v is None:
            raise Refusal("no_position", f"This light doesn't {AXIS_WORDS[ax]}, so it has no {ax} limit.")
        base = patch["limits"] if "limits" in body else current.get("limits")
        base = dict(base) if isinstance(base, Mapping) else {}
        lo, hi = _pair(base.get(ax)) or axis_ranges(caps, current)[ax]
        if end == "low" or (end == "here" and abs(v - lo) <= abs(v - hi)):
            lo = v
        else:
            hi = v
        base[ax] = [_deg(min(lo, hi)), _deg(max(lo, hi))]
        patch["limits"] = base
    return patch


def setup_notes(setup: Any) -> list[str]:
    """Page words about a light's setup as it now stands: a home outside its limits."""
    s = _d(setup)
    home, lim = _d(s.get("home")), _d(s.get("limits"))
    out = []
    for ax in AXES:
        h, p = _num(home.get(ax), None), _pair(lim.get(ax))
        if h is not None and p is not None and not p[0] - 0.01 <= h <= p[1] + 0.01:
            out.append(f"Its home is outside where it may {AXIS_WORDS[ax]}, so it rests at the nearest limit.")
    return out


def neutral_space(why: Optional[str]) -> dict:
    """GET /api/v1/rig/space with no room space: contract.SpaceView's shape, empty (the frame's own default front)."""
    return {"revision": "", "error": why, "problems": [],
            "room": {"size": None, "front": "north", "centre": None, "right": [1.0, 0.0, 0.0]},
            "surfaces": {}, "points": {}, "regions": {}, "appliances": {}, "extent": None, "targets": []}


def _surface_centre(s: Mapping[str, Any]) -> Optional[list[float]]:
    """A bounded surface's centre (origin + u w/2 + v h/2); None when it has no size."""
    o, u, v, size = _vec3(s.get("origin")), _vec3(s.get("u")), _vec3(s.get("v")), s.get("size")
    wh = [_num(x, None) for x in size] if isinstance(size, (list, tuple)) and len(size) == 2 else []
    if o is None or u is None or v is None or len(wh) != 2 or None in wh:
        return None
    w, h = float(wh[0]), float(wh[1])                     # type: ignore[arg-type]
    return [round(o[i] + u[i] * w / 2.0 + v[i] * h / 2.0, 4) for i in range(3)]


def space_targets(view: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """Every name a light can be pointed at, with page words, in the order the page lists them: the room's places, its
    areas, the placed things (a screen as "Centre of the north screen"), then the room's surfaces (their centres).
    ``[{name, label, kind: point | region | screen | appliance | surface, at}]``."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: Any, label: Any, kind: str, at: Any) -> None:
        if not isinstance(name, str) or name in seen:
            return
        seen.add(name)
        xyz = _vec3(at)
        out.append({"name": name, "label": label if isinstance(label, str) and label else place_words(name),
                    "kind": kind, "at": list(xyz) if xyz else None})

    for n, p in _d(view.get("points")).items():
        add(n, _d(p).get("label"), "point", _d(p).get("at"))
    for n, r in _d(view.get("regions")).items():
        add(n, _d(r).get("label"), "region", _d(r).get("centre"))
    for n, a in _d(view.get("appliances")).items():
        at = _d(a).get("at")
        if _vec3(at) is None:
            continue
        row = _d(rows.get(n))
        label = row.get("label") if isinstance(row.get("label"), str) else None
        if row.get("kind") == "screen" or _d(a).get("plane"):
            zone = row.get("zone")
            add(n, f"Centre of the {zone} screen" if isinstance(zone, str) and zone else
                f"Centre of {label or place_words(n)}", "screen", at)
        else:
            add(n, label, "appliance", at)
    for n, s in _d(view.get("surfaces")).items():
        c = _surface_centre(_d(s))
        if c is not None:
            add(n, _d(s).get("label"), "surface", c)
    return out


def front_word(v: Any) -> Optional[str]:
    """"n" | "north" (any case) -> "north"; None for anything else."""
    if not isinstance(v, str):
        return None
    w = v.strip().lower()
    return next((f for f in FRONTS if w in (f, f[0])), None)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


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
                 fixtures_path: Optional[Path] = None, setup_store: Any = None, space_mod: Any = None) -> None:
        self.brain_ref, self.engine_ref = brain_ref, engine_ref
        # the moving-light routes (docs/LIGHTS.md §8.1): fixtures.SetupStore and the space module, found lazily
        # (tests: fakes on tmp files)
        self.setup_store, self.space_mod = setup_store, space_mod
        self._setup_made: Optional[tuple[Any, Any]] = None     # (path, the SetupStore made for it)
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
            ("GET", "/api/v1/listening/clips/{name}", self._listening_clip),
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
            ("GET", "/api/v1/piano/recordings", self._rec_view),
            ("POST", "/api/v1/piano/record", self._rec_record),
            ("GET", "/api/v1/piano/recordings/{id}.mid", self._rec_file),
            ("PATCH", "/api/v1/piano/recordings/{id}", self._rec_rename),
            ("DELETE", "/api/v1/piano/recordings/{id}", self._rec_delete),
            ("GET", "/api/v1/look", self._look),
            ("GET", "/api/v1/log", self._log),
            ("GET", "/api/v1/rig", self._rig),
            ("GET", "/api/v1/rig/frame", self._rig_frame),
            ("GET", "/api/v1/rig/dmx", self._rig_dmx),
            ("POST", "/api/v1/rig/nodes/{id}/record-failsafe", self._record_failsafe),
            ("GET", "/api/v1/rig/types", self._rig_types),
            ("POST", "/api/v1/rig/fixtures", self._rig_add),
            ("PUT", "/api/v1/rig/fixtures/{id}", self._rig_update),
            ("DELETE", "/api/v1/rig/fixtures/{id}", self._rig_remove),
            ("POST", "/api/v1/rig/fixtures/{id}/identify", self._rig_identify),
            ("GET", "/api/v1/rig/follow", self._rig_follow_get),
            ("POST", "/api/v1/rig/follow", self._rig_follow_post),
            ("POST", "/api/v1/rig/fixtures/{id}/jog", self._rig_jog),
            ("POST", "/api/v1/rig/fixtures/{id}/setup", self._rig_setup),
            ("POST", "/api/v1/rig/fixtures/{id}/positions", self._rig_position_add),
            ("DELETE", "/api/v1/rig/fixtures/{id}/positions/{name}", self._rig_position_remove),
            ("POST", "/api/v1/rig/fixtures/{id}/teach", self._rig_teach),
            ("DELETE", "/api/v1/rig/fixtures/{id}/teach/{n}", self._rig_untaught),
            ("DELETE", "/api/v1/rig/fixtures/{id}/teach", self._rig_untaught),
            ("GET", "/api/v1/rig/space", self._space_get),
            ("PUT", "/api/v1/rig/space/room", self._space_room),
            ("PUT", "/api/v1/rig/space/appliances/{id}", self._space_appliance_put),
            ("DELETE", "/api/v1/rig/space/appliances/{id}", self._space_appliance_delete),
            ("PUT", "/api/v1/rig/space/points/{name}", self._space_point_put),
            ("DELETE", "/api/v1/rig/space/points/{name}", self._space_point_delete),
            ("PUT", "/api/v1/rig/space/regions/{name}", self._space_region_put),
            ("DELETE", "/api/v1/rig/space/regions/{name}", self._space_region_delete),
            ("GET", "/api/v1/rig/live", self._rig_live),
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
        return card(self.snapshot(brain), self.engine(), describe=self._describe_view(brain))

    def _describe_view(self, brain: Any) -> dict:
        """The card's ``describe``: describe_state, with ``suggest`` null once the room is in the suggested mode
        (09's audit F7: the offer to switch stayed up after the switch)."""
        d = self.describe_state if isinstance(self.describe_state, dict) else dict(DESCRIBE_IDLE)
        mode = _d(d.get("suggest")).get("mode")
        if mode and mode == self._room_view(brain).get("current"):
            return {**d, "suggest": None}
        return d

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
        return _json(card(self.snapshot(brain), self.engine(), describe=self._describe_view(brain)))

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
        on = body.get("on") if isinstance(body, dict) else None    # house / low: the wanted state (09's audit F6)
        if isinstance(on, bool):
            out = await self._call(brain, director.action, do, on)
        else:                                       # no "on" (an older page): it toggles, as before
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
    def _listening(self, view: Any) -> dict:
        """The listener's view with this process's ``boot_id``: room's show.ai adapter re-sends ``mode`` when it
        changes (a show_service restart), as it does for the vibe."""
        return {**(view if isinstance(view, dict) else neutral_listener(MISSING[LISTENER])), "boot_id": self.boot_id}

    async def _listening_get(self, request: web.Request, brain: Any) -> web.Response:
        listener = _components(brain).get(LISTENER)
        if listener is None:
            return _json(self._listening(neutral_listener(MISSING[LISTENER])))
        return _json(self._listening(await self._call(brain, listener.view)))

    async def _listening_clip(self, request: web.Request, brain: Any) -> web.Response:
        """One clip the listener sent, its exact bytes (09, 21 Oct: hear what the AI hears): a history row's
        ``clip_url``. 404 for a bad name or one already pruned (it keeps the last listener.CLIPS_KEEP)."""
        listener = _components(brain).get(LISTENER)
        fn = getattr(listener, "clip_path", None)
        p = fn(request.match_info.get("name", "")) if fn is not None else None
        if p is None:
            return _err(404, "not_found", "no such clip (only the last 20 are kept)")
        ctype = "audio/wav" if p.suffix == ".wav" else "audio/mpeg"
        return web.FileResponse(p, headers={"Content-Type": ctype, "Cache-Control": "no-store"})

    async def _listening_put(self, request: web.Request, brain: Any) -> web.Response:
        """``{"on"?: bool, "mode"?: bool}``, at least one: ``on`` the Room page's AI listening switch (the master),
        ``mode`` the mode's permission (owner, 20 Oct; room_service's per-mode device ``show.ai``). A bare true/false
        is ``{"on": ...}`` (the Room page's older body). Other keys are ignored."""
        body, bad = await self._body(request)
        if bad:
            return bad
        o = body if isinstance(body, dict) else {"on": body}
        on, mode = o.get("on"), o.get("mode")
        if (on is None and mode is None) or any(v is not None and not isinstance(v, bool) for v in (on, mode)):
            return _err(400, "bad_request", 'Say {"on": true|false} and/or {"mode": true|false}.')
        listener = _components(brain).get(LISTENER)
        if listener is None:
            return _json(self._listening(neutral_listener(MISSING[LISTENER])))
        out = None
        if on is not None:
            out = await self._call(brain, listener.set_on, on)
        if mode is not None:
            out = await self._call(brain, listener.set_mode, mode)
        self._changed()
        return _json(self._listening(out))

    # -- the rig (Stage 5) -------------------------------------------------------------------------------------------------------
    async def _rig(self, request: web.Request, brain: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        if rig is None:
            return _json({"enabled": False, "why": NO_RIG})
        fn = getattr(rig, "snapshot_now", None) or getattr(rig, "snapshot", None)
        snap = dict(fn()) if callable(fn) else {}
        view = getattr(rig, "view", None)
        snap["registry_view"] = view() if callable(view) else None
        snap["lights_ui"] = self.lights_ui(brain)                       # docs/LIGHTS.md §8.1: Move it's numbers
        rows = snap.get("fixtures")
        if isinstance(rows, list) and any(isinstance(r, dict) and ("caps" not in r or "setup" not in r) for r in rows):
            snap["fixtures"] = await self._rows_with_setup(rig, rows, snap["registry_view"])
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

    # -- the Lights page (README S5.23) --------------------------------------------------------------------------------
    @staticmethod
    def _rig_refused(e: ValueError) -> web.Response:
        """fixtures.Refused (``code``, ``detail``, ``extra``: choices, problems) or a "code: words" ValueError."""
        code, detail, extra = getattr(e, "code", None), getattr(e, "detail", None), getattr(e, "extra", None)
        if not isinstance(code, str):
            code, detail = _code(e, "bad_request")
            extra = None
        return _err(RIG_ERROR_STATUS.get(code, 400), code, detail or "That light can't be changed as asked.",
                    **(extra if isinstance(extra, dict) else {}))

    async def _rig_types(self, request: web.Request, brain: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        fn = getattr(rig, "types_view", None) if rig is not None else None
        if callable(fn):
            return _json(fn())
        try:
            from . import fixtures as fx                 # no rig: the generic types still list
            return _json(fx.type_rows(None))
        except Exception:                                # noqa: BLE001
            return _json([])

    async def _rig_write(self, request: web.Request, brain: Any, method: str, *args: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        fn = getattr(rig, method, None) if rig is not None else None
        if not callable(fn):
            return _err(503, "unavailable", NO_RIG)
        try:
            got = await self._call(brain, fn, *args)
        except ValueError as e:
            return self._rig_refused(e)
        row = got.get("fixture") if isinstance(got, dict) else None
        if isinstance(row, dict) and ("caps" not in row or "setup" not in row):     # docs/LIGHTS.md §2.3's row
            got = {**got, "fixture": (await self._rows_with_setup(rig, [row], self._view_of(rig)))[0]}
        return _json(got)

    async def _rig_add(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        return await self._rig_write(request, brain, "add_fixture", body)

    async def _rig_update(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        return await self._rig_write(request, brain, "update_fixture", request.match_info["id"], body)

    async def _rig_remove(self, request: web.Request, brain: Any) -> web.Response:
        return await self._rig_write(request, brain, "remove_fixture", request.match_info["id"])

    async def _rig_identify(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        fid = request.match_info["id"]
        seconds = body.get("seconds") if isinstance(body, dict) else None
        rig = _components(brain).get(RIG)
        if rig is None or not callable(getattr(rig, "identify", None)):
            return _json({"ok": False, "why": NO_RIG})
        view = rig.view() if callable(getattr(rig, "view", None)) else None
        known = (view or {}).get("fixtures") or {}
        if view is not None and fid not in known:
            return _err(404, "unknown_fixture", f"No light {fid!r} in the rig.", choices=sorted(known))
        try:
            return _json(await self._call(brain, rig.identify, fid, seconds))
        except ValueError as e:
            return self._rig_refused(e)

    async def _rig_follow_get(self, request: web.Request, brain: Any) -> web.Response:
        rig = _components(brain).get(RIG)
        fn = getattr(rig, "follow_view", None) if rig is not None else None
        return _json(fn() if callable(fn) else {"fixtures": {}})

    async def _rig_follow_post(self, request: web.Request, brain: Any) -> web.Response:
        """room's DMX-lights device: ``{fixtures: {id: "dmx" | "off" | {hex, bri} | null}}``, merged (null forgets
        one) -> ``{accepted: true, fixtures: the whole mapping, unknown, saved}``."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        fixtures = body.get("fixtures") if isinstance(body, dict) else None
        if not isinstance(fixtures, dict):
            return _err(400, "bad_request", 'The body is {"fixtures": {<fixture id>: "dmx" | "off" | {"hex": '
                                            '"#rrggbb", "bri": 0..100}}}.', choices=FOLLOW_CHOICES)
        rig = _components(brain).get(RIG)
        if rig is None or not callable(getattr(rig, "set_follow", None)):
            return _err(503, "unavailable", NO_RIG)
        try:
            return _json(await self._call(brain, rig.set_follow, fixtures))
        except ValueError as e:
            code, detail = _code(e, "bad_follow")
            return _err(400, code, detail or "That isn't something a light can follow.", choices=FOLLOW_CHOICES)

    # -- moving lights: Move it, setup, positions and teach (docs/LIGHTS.md §8.1) --------------------------------------
    @staticmethod
    def _lights_cfg(brain: Any) -> dict:
        """show.yaml ``lights`` (docs/LIGHTS.md §12), {} without it."""
        return _d(_d(getattr(getattr(brain, "ctx", None), "settings", None)).get("lights"))

    def lights_ui(self, brain: Any) -> dict:
        """GET /api/v1/rig ``lights_ui``: show.yaml ``lights.jog``'s send_hz, idle_s, nudge_deg and drag_deg_per_px,
        each key that isn't a positive number filled from JOG_UI."""
        jog = _d(self._lights_cfg(brain).get("jog"))
        out: dict[str, Any] = {}
        for k, dflt in JOG_UI.items():
            if isinstance(dflt, dict):
                got = _d(jog.get(k))
                out[k] = {w: n if (n := _num(got.get(w), None)) is not None and n > 0 else d for w, d in dflt.items()}
            else:
                n = _num(jog.get(k), None)
                out[k] = n if n is not None and n > 0 else dflt
        return out

    @staticmethod
    def _view_of(rig: Any) -> Optional[dict]:
        fn = getattr(rig, "view", None) if rig is not None else None
        try:
            v = fn() if callable(fn) else None
        except Exception:                                # noqa: BLE001
            return None
        return v if isinstance(v, dict) else None

    def _no_light(self, rig: Any, fid: str) -> Optional[web.Response]:
        """404 ``unknown_fixture`` for an id the rig's view doesn't have (no view: the rig's own call says)."""
        v = self._view_of(rig)
        known = _d((v or {}).get("fixtures"))
        if v is None or fid in known:
            return None
        return _err(404, "unknown_fixture", f"No light {fid!r} in the rig.", choices=sorted(known))

    def _rig_for(self, brain: Any, fid: str) -> tuple[Any, Optional[web.Response]]:
        """(the rig, None), or (None, the 503 / 404 answer)."""
        rig = _components(brain).get(RIG)
        if rig is None:
            return None, _err(503, "unavailable", NO_RIG)
        return rig, self._no_light(rig, fid)

    @staticmethod
    def _held(rig: Any, fid: str) -> Optional[dict]:
        """The light's hold (Rig.holds, docs/LIGHTS.md §5.7) as one mapping, its target over its smoothed now; None
        when it isn't held (or the rig has no holds yet)."""
        fn = getattr(rig, "holds", None)
        try:
            hv = _d(fn()).get(fid) if callable(fn) else None
        except Exception:                                # noqa: BLE001
            return None
        return {**_d(hv.get("now")), **_d(hv.get("target"))} if isinstance(hv, dict) else None

    def _caps(self, rig: Any, fid: str) -> dict:
        """Rig.caps_of(fid), else fixture_encode.caps over the view's type and setup, else {}."""
        fn = getattr(rig, "caps_of", None)
        if callable(fn):
            try:
                got = fn(fid)
            except Exception:                            # noqa: BLE001
                got = None
            if isinstance(got, dict):
                return got
        v = self._view_of(rig)
        f = _d(_d((v or {}).get("fixtures")).get(fid))
        t = _d((v or {}).get("types")).get(f.get("type"))
        if not f or not isinstance(t, dict):
            return {}
        try:
            from . import fixture_encode as fe
            got = fe.caps(t, f.get("setup") or None)
        except Exception:                                # noqa: BLE001 (encode not built yet: the page shows less)
            return {}
        return dict(got) if isinstance(got, Mapping) else {}

    def _setup_of(self, rig: Any) -> Any:
        """fixtures.SetupStore (docs/LIGHTS.md §2.6): the injected one, the registry's own, else one made for the
        registry's ``setup_path`` (contract.FIXTURES_SETUP_FILE). None when fixtures.py has none yet."""
        if self.setup_store is not None:
            return self.setup_store
        reg = getattr(rig, "registry", None)
        own = getattr(reg, "setup_store", None)
        if callable(getattr(own, "patch", None)) and callable(getattr(own, "read", None)):
            return own
        path = getattr(reg, "setup_path", None) or getattr(contract, "FIXTURES_SETUP_FILE", None) \
            or contract.CONFIG_ROOM_DIR / "fixtures.setup.yaml"
        if self._setup_made is not None and self._setup_made[0] == path:
            return self._setup_made[1]
        try:
            from . import fixtures as fx
            store = fx.SetupStore(path=Path(path))
        except Exception:                                # noqa: BLE001
            return None
        self._setup_made = (path, store)
        return store

    @staticmethod
    def _setup_for(store: Any, fid: str) -> dict:
        """One light's setup from the store, {} when there's none or the file doesn't read (ranges then fall back)."""
        try:
            return _d(_d(store.read()).get(fid)) if store is not None else {}
        except ValueError:
            return {}

    def _space_mod(self) -> Any:
        if self.space_mod is not None:
            return self.space_mod
        try:
            from . import space as sp
        except Exception:                                # noqa: BLE001 (not built yet: the map degrades)
            return None
        return sp

    def _space_store(self) -> Any:
        sp = self._space_mod()
        fn = getattr(sp, "shared", None) if sp is not None else None
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:                                # noqa: BLE001
            log.exception("api: space.shared() failed")
            return None

    @staticmethod
    def _reload(rig: Any) -> None:
        """Rig.reload_now (show-brain): the engine re-reads the registry and the space at its next sync."""
        fn = getattr(rig, "reload_now", None) or getattr(rig, "_reload_now", None)
        if callable(fn):
            fn()

    async def _rows_with_setup(self, rig: Any, rows: list, view: Any) -> list:
        """GET /api/v1/rig's rows with ``caps`` (Rig.caps_of) and ``setup`` (the registry's FixtureInfo.setup, else
        the setup file) where the rig's own rows don't carry them yet."""
        fixtures = _d(_d(view).get("fixtures"))
        stored: Optional[dict] = None
        if any(isinstance(r, dict) and "setup" not in r and "setup" not in _d(fixtures.get(r.get("id")))
               for r in rows):
            store = self._setup_of(rig)
            if store is not None:
                try:
                    stored = _d(await asyncio.to_thread(store.read))
                except Exception:                        # noqa: BLE001
                    stored = None
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            r = dict(r)
            fid = r.get("id")
            if "caps" not in r:
                r["caps"] = self._caps(rig, fid) or None
            if "setup" not in r:
                own = _d(fixtures.get(fid)).get("setup")
                r["setup"] = dict(own) if isinstance(own, dict) else dict(_d(_d(stored).get(fid)))
            out.append(r)
        return out

    async def _rig_jog(self, request: web.Request, brain: Any) -> web.Response:
        """POST .../jog ``{values?: {attr: value}, release?, free?}`` -> Rig.jog's answer, called here on show-bus (any
        thread, no brain hop: docs/LIGHTS.md §8.1). Without the rig's hold: ``{held: false, why}``."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        body = {} if body is None else body
        values = body.get("values") if isinstance(body, dict) else None
        values = {} if values is None else values
        release, free = (body.get(k, False) if isinstance(body, dict) else None for k in ("release", "free"))
        if not isinstance(body, dict) or not isinstance(values, dict) or not isinstance(release, bool) \
                or not isinstance(free, bool):
            return _err(400, "bad_request", 'The body is {"values": {<what>: <value>}, "release": true | false, '
                                            '"free": true | false}.')
        fid = request.match_info["id"]
        rig, no = self._rig_for(brain, fid)
        if no is not None:
            return no
        fn = getattr(rig, "jog", None)
        if not callable(fn):
            return _json({"held": False, "until_s": None, "idle_s": self.lights_ui(brain)["idle_s"], "target": {},
                          "now": {}, "unknown": sorted(values), "why": CANT_MOVE})
        try:
            got = fn(fid, values, release=release, free=free)
        except ValueError as e:
            return self._rig_refused(e)
        return _json(got if isinstance(got, dict) else {"held": False, "why": CANT_MOVE})

    async def _rig_setup(self, request: web.Request, brain: Any) -> web.Response:
        """POST .../setup: any of ``home``/``beam`` ("here" | a value | null), ``limit: {axis, end}``, ``limits``
        (null: none), ``slots``, ``max_speed``, ``range``, ``jog_flip``, ``strobe_hz_max`` -> ``{setup, problems}``.
        "here" and ``limit`` read the light's hold: 409 ``not_held`` ("Open Move it first.") without one."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or not body:
            return _err(400, "bad_request", "Say what to keep for this light.", choices=list(SETUP_BODY_KEYS))
        extra = sorted(k for k in body if k not in SETUP_BODY_KEYS)
        if extra:
            return _err(400, "bad_request", f"A light doesn't keep {', '.join(extra)}.", choices=list(SETUP_BODY_KEYS))
        fid = request.match_info["id"]
        rig, no = self._rig_for(brain, fid)
        if no is not None:
            return no
        store = self._setup_of(rig)
        if store is None:
            return _err(503, "unavailable", NO_SETUP)
        here = self._held(rig, fid)
        if here is None and ("limit" in body or "here" in (body.get("home"), body.get("beam"))):
            return _err(409, "not_held", NOT_HELD)
        caps = self._caps(rig, fid)

        def run() -> dict:
            patch = setup_patch(body, _d(_d(store.read()).get(fid)), here, caps)
            new = _d(store.patch(fid, patch))
            self._reload(rig)
            return {"setup": new, "problems": setup_notes(new)}

        try:
            return _json(await self._call(brain, run))
        except ValueError as e:
            return self._rig_refused(e)

    async def _rig_position_add(self, request: web.Request, brain: Any) -> web.Response:
        """POST .../positions ``{name, label?, at?: "here" | {pan, tilt}}`` -> ``{positions, point, problems}``. The
        name is kept as a place id (place_id: "The piano" -> "piano"). A calibrated mover's new name (one the room map
        doesn't have) becomes a room point where its beam lands (space.ray_hit from its pose, docs/LIGHTS.md §3.6)."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        name = self._word(body, "name") if isinstance(body, dict) else None
        pid = place_id(name) if name else ""
        if not pid:
            return _err(400, "bad_request", 'Give the position a name: {"name": "Over the piano"}.')
        label = self._word(body, "label") or (name if name != pid else None)
        at = body.get("at", "here")
        fid = request.match_info["id"]
        rig, no = self._rig_for(brain, fid)
        if no is not None:
            return no
        store = self._setup_of(rig)
        if store is None:
            return _err(503, "unavailable", NO_SETUP)
        if at == "here":
            here = self._held(rig, fid)
            if here is None:
                return _err(409, "not_held", NOT_HELD)
            pos = here
        elif isinstance(at, dict):
            pos = at
        else:
            return _err(400, "bad_request", '"at" is "here" (where Move it holds it) or {"pan": <°>, "tilt": <°>}.')
        try:
            pan, tilt = held_position(pos)
        except ValueError as e:
            return self._rig_refused(e)

        def run() -> dict:
            new = _d(store.patch(fid, {"positions": {pid: {"pan": pan, "tilt": tilt}}}))
            point, problems = self._point_from_beam(fid, pid, label, pan, tilt)
            self._reload(rig)
            return {"positions": _d(new.get("positions")), "point": point, "problems": problems}

        try:
            return _json(await self._call(brain, run))
        except ValueError as e:
            return self._rig_refused(e)

    def _point_from_beam(self, fid: str, pid: str, label: Optional[str], pan: float,
                         tilt: float) -> tuple[Optional[dict], list[str]]:
        """show-brain: a calibrated light's position ``pid`` as a room point where its beam lands (set_point with
        source = the light), when the room map has no place by that name. (None, []) for an uncalibrated light."""
        sp, st = self._space_mod(), self._space_store()
        if sp is None or st is None:
            return None, []
        view = _d(st.view())
        pose = _d(_d(view.get("appliances")).get(fid)).get("pose")
        origin = _vec3(_d(pose).get("at"))
        if not isinstance(pose, dict) or origin is None:
            return None, []
        try:
            if sp.resolve(view, pid) is not None:                 # the map's own place: a light never moves it
                return None, []
            hit = sp.ray_hit(view, origin, sp.beam_dir(pose, pan, tilt))
            if not hit:
                return None, ["Its beam doesn't land on the room's floor, walls or ceiling here, so it isn't a place "
                              "on the map."]
            at = [round(float(x), 3) for x in hit[0]]
            st.set_point(pid, at, label, source=fid)
        except ValueError as e:
            return None, [str(getattr(e, "detail", "") or e)]
        return {"name": pid, "at": at, "surface": hit[1]}, []

    async def _rig_position_remove(self, request: web.Request, brain: Any) -> web.Response:
        """DELETE .../positions/{name} -> ``{positions}`` (a room point it made stays on the map)."""
        fid, pid = request.match_info["id"], request.match_info["name"]
        rig, no = self._rig_for(brain, fid)
        if no is not None:
            return no
        store = self._setup_of(rig)
        if store is None:
            return _err(503, "unavailable", NO_SETUP)

        def run() -> dict:
            new = _d(store.patch(fid, {"positions": {pid: None}}))
            self._reload(rig)
            return {"positions": _d(new.get("positions"))}

        try:
            return _json(await self._call(brain, run))
        except ValueError as e:
            return self._rig_refused(e)

    def _fit(self, sp: Any, view: Mapping[str, Any], fid: str, samples: list, ranges: dict,
             cfg: Optional[dict]) -> dict:
        """space.solve_pose over the light's samples (its map placement and mount as the hint); a solver that fails
        is a "bad" fit in words, and the samples stay."""
        app = _d(_d(view.get("appliances")).get(fid))
        try:
            kw: dict[str, Any] = {"at_hint": _vec3(app.get("at")), "mount": app.get("mount"),
                                  "pan_range": ranges["pan"], "tilt_range": ranges["tilt"], "cfg": cfg}
            if "view" in inspect.signature(sp.solve_pose).parameters:     # the room's centre as the prior
                kw["view"] = view
            fit = sp.solve_pose(list(samples), **kw)
        except Exception as e:                           # noqa: BLE001
            log.exception("api: solve_pose for %s failed", fid)
            fit = {"pose": None, "rms_deg": None, "worst_deg": None, "worst_index": None, "spread_deg": 0.0,
                   "samples": len(samples), "level": "bad",
                   "words": f"It couldn't work out where it hangs just now ({type(e).__name__}); the places it "
                            f"learnt are kept."}
        return dict(fit) if isinstance(fit, Mapping) else {"pose": None, "samples": len(samples), "level": "bad",
                                                            "words": "It couldn't work out where it hangs just now."}

    def _teach_parts(self, brain: Any, fid: str) -> tuple[Any, Any, Optional[web.Response]]:
        rig, no = self._rig_for(brain, fid)
        if no is not None:
            return None, None, no
        sp, st = self._space_mod(), self._space_store()
        if sp is None or st is None or not callable(getattr(sp, "solve_pose", None)):
            return None, None, _err(503, "unavailable", NO_SPACE)
        return rig, (sp, st), None

    async def _rig_teach(self, request: web.Request, brain: Any) -> web.Response:
        """POST .../teach ``{point}`` (a room map name) -> ``{samples, fit, problems}``: the held pan/tilt pointed at
        that place is a TeachSample (and the light's position by that name); from 3 samples the pose is solved and set
        (docs/LIGHTS.md §3.6). 409 ``not_held`` without Move it, 400 ``unknown_point`` with the map's names."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        point = self._word(body, "point") if isinstance(body, dict) else None
        fid = request.match_info["id"]
        rig, parts, no = self._teach_parts(brain, fid)
        if no is not None:
            return no
        sp, st = parts
        names = [t["name"] for t in space_targets(_d(st.view()), {})]
        if point is None:
            return _err(400, "bad_request", 'Say which place it points at: {"point": <name>}.', choices=names)
        here = self._held(rig, fid)
        if here is None:
            return _err(409, "not_held", NOT_HELD)
        try:
            pan, tilt = held_position(here)
        except ValueError as e:
            return self._rig_refused(e)
        caps, store, cfg = self._caps(rig, fid), self._setup_of(rig), self._lights_cfg(brain).get("calibration")

        def add() -> tuple[list, dict, list]:
            view = _d(st.view())
            at = _vec3(sp.resolve(view, point))
            if at is None:
                raise Refusal("unknown_point", f"There's no place called “{point}” on the Room map.", choices=names)
            samples = list(st.add_sample(fid, {"pan": pan, "tilt": tilt, "point": point,
                                               "at": [round(x, 4) for x in at], "t": _now_iso()}))
            problems, setup = [], {}
            if store is not None:
                try:
                    setup = _d(store.patch(fid, {"positions": {point: {"pan": pan, "tilt": tilt}}}))
                except ValueError as e:
                    problems.append(str(getattr(e, "detail", "") or e))
            return samples, setup, problems

        try:
            samples, setup, problems = await self._call(brain, add)
            fit = await self._solve_and_keep(brain, rig, sp, st, fid, samples, axis_ranges(caps, setup), cfg)
        except ValueError as e:
            return self._rig_refused(e)
        return _json({"samples": samples, "fit": fit, "problems": problems})

    async def _solve_and_keep(self, brain: Any, rig: Any, sp: Any, st: Any, fid: str, samples: list, ranges: dict,
                              cfg: Any) -> dict:
        """The pose solve on a worker thread (scipy: ~0.3 s, the first one ~1 s: never on show-brain), then set_pose
        and Rig.reload_now on show-brain, unless the light's samples changed meanwhile (that change solves again)."""
        fit = await asyncio.to_thread(self._fit, sp, _d(st.view()), fid, samples, ranges, _d(cfg) or None)

        def keep() -> None:
            now = list(_d(_d(_d(st.view()).get("appliances")).get(fid)).get("teach") or [])
            if now == list(samples):
                st.set_pose(fid, fit.get("pose"))
            self._reload(rig)

        await self._call(brain, keep)
        return fit

    async def _rig_untaught(self, request: web.Request, brain: Any) -> web.Response:
        """DELETE .../teach/{n} (forget sample n, 0-based) or DELETE .../teach (forget them all) -> ``{samples,
        fit}``; the pose is solved again (fewer than 3: none). A sample that isn't there: what is true now."""
        fid, n = request.match_info["id"], request.match_info.get("n")
        index: Optional[int] = None
        if n is not None:
            try:
                index = int(n)
            except ValueError:
                return _err(400, "bad_request", "Say which place to forget by its number (0 is the first).")
        rig, parts, no = self._teach_parts(brain, fid)
        if no is not None:
            return no
        sp, st = parts
        caps, store, cfg = self._caps(rig, fid), self._setup_of(rig), self._lights_cfg(brain).get("calibration")

        def forget() -> tuple[list, dict]:
            samples: list = list(_d(_d(_d(st.view()).get("appliances")).get(fid)).get("teach") or [])
            if index is None:
                st.clear_samples(fid)
                samples = []
            elif 0 <= index < len(samples):
                samples = list(st.remove_sample(fid, index))
            return samples, self._setup_for(store, fid)

        try:
            samples, setup = await self._call(brain, forget)
            fit = await self._solve_and_keep(brain, rig, sp, st, fid, samples, axis_ranges(caps, setup), cfg)
        except ValueError as e:
            return self._rig_refused(e)
        return _json({"samples": samples, "fit": fit})

    # -- the Room map and the live view (docs/LIGHTS.md §3, §8.1) -------------------------------------------------------
    @staticmethod
    def _rows_by_id(rig: Any) -> dict[str, dict]:
        fn = getattr(rig, "fixture_rows", None) if rig is not None else None
        try:
            rows = fn() if callable(fn) else []
        except Exception:                                # noqa: BLE001
            rows = []
        return {r["id"]: r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)}

    def _space_out(self, view: Any, rig: Any) -> dict:
        v = dict(_d(view))
        v["targets"] = space_targets(v, self._rows_by_id(rig))
        return v

    async def _space_write(self, brain: Any, fn: Callable[[Any], Any]) -> web.Response:
        """A Room map write on show-brain: ``fn(store)``'s answer (the store writes space.added.yaml and polls), then
        Rig.reload_now when a rig runs. 503 without the space module; space.Refused -> 400 in its words."""
        st = self._space_store()
        if st is None:
            return _err(503, "unavailable", NO_SPACE)
        rig = _components(brain).get(RIG)

        def run() -> Any:
            got = fn(st)
            self._reload(rig)
            return got

        try:
            return _json(await self._call(brain, run))
        except ValueError as e:
            return self._rig_refused(e)

    async def _space_get(self, request: web.Request, brain: Any) -> web.Response:
        """GET /api/v1/rig/space -> SpaceView + ``targets`` (space_targets). Polled on show-brain first, so a hand edit
        to space.yaml shows. No space module: neutral_space with ``error``."""
        st = self._space_store()
        rig = _components(brain).get(RIG)
        if st is None:
            return _json(neutral_space(NO_SPACE))
        poll = getattr(st, "poll", None)
        if callable(poll):
            await self._call(brain, poll)
        return _json(self._space_out(st.view(), rig))

    async def _space_room(self, request: web.Request, brain: Any) -> web.Response:
        """PUT /api/v1/rig/space/room ``{size?: [x, y, z] | null, front?: n | e | s | w | north ...}`` -> SpaceView (+
        targets). A key left out keeps what the map has."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or not body or any(k not in ROOM_KEYS for k in body):
            return _err(400, "bad_request", 'The body is {"size": [<east-west m>, <north-south m>, <height m>], '
                                            '"front": "north" | "east" | "south" | "west"}.', choices=list(ROOM_KEYS))
        front = front_word(body["front"]) if "front" in body else None
        if "front" in body and front is None:
            return _err(400, "bad_request", "Say which wall people face: north, east, south or west.",
                        choices=list(FRONTS))
        size = _vec3(body.get("size"))
        if body.get("size") is not None and (size is None or min(size) <= 0):
            return _err(400, "bad_request", "The room's size is three lengths in metres: east-west, north-south and "
                                            "floor to ceiling.")
        rig = _components(brain).get(RIG)

        def write(st: Any) -> dict:
            # space.Store.set_room: None leaves that as it is, a size of [] clears it
            st.set_room((list(size) if size else []) if "size" in body else None, front)
            return self._space_out(st.view(), rig)

        return await self._space_write(brain, write)

    async def _space_appliance_put(self, request: web.Request, brain: Any) -> web.Response:
        """PUT /api/v1/rig/space/appliances/{id} ``{at?, mount?, plane?}`` (null clears one) -> the appliance."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or not body or any(k not in APPLIANCE_KEYS for k in body):
            return _err(400, "bad_request", 'Say where it is: {"at": [x, y, z], "mount": ..., "plane": ...}.',
                        choices=list(APPLIANCE_KEYS))
        aid = request.match_info["id"]
        return await self._space_write(brain, lambda st: st.set_appliance(aid, **body))

    async def _space_appliance_delete(self, request: web.Request, brain: Any) -> web.Response:
        aid = request.match_info["id"]

        def write(st: Any) -> dict:
            st.remove_appliance(aid)
            return {"removed": aid}

        return await self._space_write(brain, write)

    async def _space_point_put(self, request: web.Request, brain: Any) -> web.Response:
        """PUT /api/v1/rig/space/points/{name} ``{at, label?, device?}`` -> the point (source "page")."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or "at" not in body or any(k not in POINT_KEYS for k in body):
            return _err(400, "bad_request", 'Say where the place is: {"at": [x, y, z], "label": "The piano"}.',
                        choices=list(POINT_KEYS))
        name = request.match_info["name"]

        def write(st: Any) -> Any:
            kw: dict[str, Any] = {"source": "page"}
            if body.get("device") is not None and "device" in inspect.signature(st.set_point).parameters:
                kw["device"] = body["device"]
            return st.set_point(name, body["at"], body.get("label"), **kw)

        return await self._space_write(brain, write)

    async def _space_point_delete(self, request: web.Request, brain: Any) -> web.Response:
        name = request.match_info["name"]

        def write(st: Any) -> dict:
            st.remove_point(name)
            return {"removed": name}

        return await self._space_write(brain, write)

    async def _space_region_put(self, request: web.Request, brain: Any) -> web.Response:
        """PUT /api/v1/rig/space/regions/{name} ``{surface, centre, size, label?}`` -> the region."""
        body, bad = await self._body(request)
        if bad is not None:
            return bad
        if not isinstance(body, dict) or any(k not in body for k in ("surface", "centre", "size")) \
                or any(k not in REGION_KEYS for k in body):
            return _err(400, "bad_request", 'An area is {"surface": "floor", "centre": [x, y, z], "size": [w, d]}.',
                        choices=list(REGION_KEYS))
        name = request.match_info["name"]
        return await self._space_write(brain, lambda st: st.set_region(name, body["surface"], body["centre"],
                                                                       body["size"], body.get("label")))

    async def _space_region_delete(self, request: web.Request, brain: Any) -> web.Response:
        name = request.match_info["name"]
        st = self._space_store()
        if st is not None and not callable(getattr(st, "remove_region", None)):
            return _err(503, "unavailable", "Removing an area isn't possible yet (show_service needs updating).")

        def write(store: Any) -> dict:
            store.remove_region(name)
            return {"removed": name}

        return await self._space_write(brain, write)

    async def _rig_live(self, request: web.Request, brain: Any) -> web.Response:
        """GET /api/v1/rig/live -> Rig.live_view() (contract LiveView, any thread); without it an empty view with
        ``why``."""
        rig = _components(brain).get(RIG)
        fn = getattr(rig, "live_view", None) if rig is not None else None
        if not callable(fn):
            return _json({"t_ms": round(time.time() * 1000.0, 1), "flow": None, "programs": {}, "fixtures": {},
                          "why": NO_RIG if rig is None else NO_LIVE})
        return _json(fn())

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
                      "pieces": {s: pieces.get(s) or [] for s in TEST_SOURCES},
                      **(await self._test_choices(brain, src))})

    async def _test_choices(self, brain: Any, src: Any) -> dict:
        """The test piano's ``instruments``, ``default_instrument`` and ``outputs`` (Sources.choices)."""
        fn = getattr(src, "choices", None)
        if not callable(fn):
            return dict(NO_TEST_CHOICES)
        got = _d(await self._call(brain, fn))
        return {k: got.get(k, v) for k, v in NO_TEST_CHOICES.items()}

    async def _test_post(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad:
            return bad
        source = (self._word(body, "source") or "").lower()
        piece = self._word(body, "piece") if isinstance(body, dict) else None
        if source not in TEST_SOURCES:
            return _err(400, "unknown_source", SAY_SOURCE, choices=list(TEST_SOURCES))
        # the test piano's sound (22 Oct): passed on only when sent, so an older Sources keeps working
        opts: dict[str, Any] = {}
        for key in ("instrument", "output"):
            v = self._word(body, key) if isinstance(body, dict) else None
            if v is not None:
                opts[key] = v
        once = body.get("once") if isinstance(body, dict) else None
        if once is not None and not isinstance(once, bool):
            return _err(400, "bad_request", "“once” is true or false.", choices=[True, False])
        if once is not None:
            opts["once"] = once
        src = _components(brain).get(SOURCES)
        if src is None:
            return _json(neutral_source(source, MISSING[SOURCES]))
        try:
            if opts:
                out = await self._call(brain, lambda: src.start_test(source, piece, **opts))
            else:
                out = await self._call(brain, src.start_test, source, piece)
        except ValueError as e:
            code, _ = _code(e, "unknown_piece")
            if code == "unknown_source":
                return _err(400, code, SAY_SOURCE, choices=list(TEST_SOURCES))
            if code in ("unknown_instrument", "unknown_output"):
                key = "instruments" if code == "unknown_instrument" else "outputs"
                ids: list = []
                try:
                    ids = [x.get("id") for x in (await self._test_choices(brain, src)).get(key) or []
                           if isinstance(x, dict) and x.get("id")]
                except Exception:
                    pass
                what = opts.get("instrument" if key == "instruments" else "output")
                word = "sound" if key == "instruments" else "place to play"
                text = f"There's no piano {word} called “{what}”." if what else f"No piano {word} is available."
                return _err(400, code, text, choices=ids)
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

    # -- piano recordings (the Room page's Record; recorder.py) -------------------------------------------------------------
    async def _rec_view(self, request: web.Request, brain: Any) -> web.Response:
        rec = _components(brain).get(RECORDER)
        if rec is None:
            return _json({**NO_RECORDINGS, "detail": NO_RECORDER})
        return _json(await self._call(brain, rec.view))

    async def _rec_record(self, request: web.Request, brain: Any) -> web.Response:
        body, bad = await self._body(request)
        if bad:
            return bad
        on = body.get("on") if isinstance(body, dict) else body
        if not isinstance(on, bool):
            return _err(400, "bad_request", "Say {\"on\": true} to record, or {\"on\": false} to stop and keep it.",
                        choices=[True, False])
        rec = _components(brain).get(RECORDER)
        if rec is None:
            return _json({**NO_RECORDINGS, **({} if on else {"last": None}), "detail": NO_RECORDER})
        return _json(await self._call(brain, rec.record, on, timeout=RECORD_TIMEOUT_S))

    async def _rec_rename(self, request: web.Request, brain: Any) -> web.Response:
        rid = request.match_info.get("id", "")
        body, bad = await self._body(request)
        if bad:
            return bad
        title = body.get("title") if isinstance(body, dict) else body
        rec = _components(brain).get(RECORDER)
        if rec is None:
            return _err(503, "unavailable", NO_RECORDER)
        try:
            return _json(await self._call(brain, rec.rename, rid, title, timeout=RECORDING_FILE_TIMEOUT_S))
        except LookupError:
            return _err(404, "unknown_recording", UNKNOWN_RECORDING)

    async def _rec_delete(self, request: web.Request, brain: Any) -> web.Response:
        rid = request.match_info.get("id", "")
        rec = _components(brain).get(RECORDER)
        if rec is None:
            return _err(503, "unavailable", NO_RECORDER)
        try:
            return _json(await self._call(brain, rec.delete, rid, timeout=RECORDING_FILE_TIMEOUT_S))
        except LookupError:                    # already gone: what is true now (degrade, never refuse)
            return _json({"deleted": rid, "detail": "That recording was already gone."})

    async def _rec_file(self, request: web.Request, brain: Any) -> web.StreamResponse:
        rid = request.match_info.get("id", "")
        rec = _components(brain).get(RECORDER)
        if rec is None:
            return _err(404, "unknown_recording", NO_RECORDER)
        try:
            path, title = await self._call(brain, rec.file_of, rid)
            data = await asyncio.get_running_loop().run_in_executor(None, Path(path).read_bytes)
        except (LookupError, OSError):
            return _err(404, "unknown_recording", UNKNOWN_RECORDING)
        return web.Response(body=data, content_type="audio/midi",
                            headers={"Content-Disposition": content_disposition(str(title))})

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
    files), ``setup_store`` (a fixtures.SetupStore; default: the registry's, else one on its setup path) and
    ``space_mod`` (the space module; default: show_service.space, imported lazily). The result mounts with
    ``api_obj(app)`` and its ``notify`` goes in ``BrainContext.notify``."""
    return Api(brain_ref, engine_ref, **kw)
