"""Sessions: the API-facing state machine (asyncio loop thread) around a render Engine (LIVE_V1 §1.2).

  starting --first IDR muxed--> holding --go--> entering --in done--> live
  live --PATCH scene--> live ; entering|live|exiting(|held) --exit--> exiting --out done--> held
  any --error--> failed ; any --DELETE/TTL--> ended

State changes happen only on the loop thread.  API calls validate the current state, change it at once
(go -> entering, exit -> exiting) and queue a command; the render thread applies it at the next frame
boundary and reports transition pts back, which drive entering -> live and exiting -> held.

LIVE_V1 1.1 (additive; the proposal as amended by the review, R1-R22):
  * cue {to: scene | "auto", via?, at: now|beat}: a scene change in entering/live only (409 elsewhere; go
    leaves holding, exit is the route to a still).  At most 2 in/scene transitions run at once; one more cue
    waits in a single pending slot (a newer manual cue replaces it; an auto cue never replaces a manual
    one).  PATCH scene is a cue too (cause "patch").  go, exit and cue carry cue_id / duration_s.
  * Accepting exit cancels the pending cue and any in-flight prepare and disarms the director for good;
    scene sources are prepared outside the session lock, so an exit never waits behind one (R5).
  * The director (director.py) is armed only in live; the manager ticks it at 10 Hz on this loop.
  * direction -> derived masters (speed = scene clock, depth = binding swing); palette -> the session palette;
    both reach only scene sources, never the anchor or exit targets (R1).
  * Inputs: each session samples its fixture (inputs.py) on the render thread, with a lead (R17) recomputed
    here at 1 Hz.  Events: session.cue, session.director, session.inputs (<= 1 / 2 s), session.palette
    (<= 1 Hz), session.warning.

Effects layer (EFFECTS_LAYER_DESIGN C-D): scene specs carry ``effects`` (prepared with the scene, attached as
its ``fx_chain``); the master FX bus is ``self.fx`` (fx_state.py: GET / PUT /fx, session.fx) and the FX director
(fx_director.py) is ticked here like the scene director.  A plug-in that fails in render emits
``session.plugin_error``; a failed bus slot is dropped from the stack, a failed current scene triggers a recovery
cue (the director's pick, else the entry scene) and is quarantined.  Prepare failures are 500 prepare_failed.

GPU frame-time guard (guard.py, renderer.py): no gates (owner, 18 Oct): a slow plug-in keeps playing, degraded; the
one stop is a hang (a call 3 s on the GPU), which arrives as a plug-in error with ``reason: "slow"``; it is recorded
process-wide (``SessionManager.slow``: the catalog's ``slow`` list, /api/diag, the library), never benched.  A
stopped current scene is recovered to the previous scene, else a core scene.  Vetted pool (``auto_ok``): the
directors only pick plug-ins that are not broken or blacklisted and that are core or have rendered at least once
(a harness sheet); a calibration entry for the session's GPU (library.Calibration) decides instead when there is one (status ok and fits), and its
``fit_scale`` becomes a scene's ``render_scale`` unless the cue sets one.  Manual cues may pick anything.
"""
from __future__ import annotations

import asyncio
import collections
import base64
import binascii
import copy
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import math
import re
import secrets
import time
from typing import Any, Callable, Optional

from . import binding, fx_interplay, palettes, rooms
from .bodies import PngData
from .director import AUTO_DEFAULTS, AUTO_FX_DEFAULTS, DIRECTION_DEFAULTS, FX_DIRECTION_DEFAULTS, Director, \
    validate_auto, validate_direction, weighted_pick
from .tags import scene_weight
from .effects import EffectChain, EffectSlot
from .fx_director import FX_EVENT_P, FxDirector, FxMove
from .fx_state import SessionFx
from .guard import trip_budget_ms
from .inputs import BEAT_FOLLOW, BIND_SIGNALS, Sampler, ShowInputs, default_fixture, load_photon
from .library import Calibration, origin_of, vetting
from .media import MediaHub
from .sound import new_sound
from .modulation import Modulation, SessionRuntime, speed_for, split_bindings
from .music_feed import MusicFeed, beat_at, clock_signals, blend as feed_blend, music_signals
from .upnext import EFFECTS_AHEAD, KINDS as UPNEXT_KINDS, SCENES_AHEAD, UpNext, why_words
from .music_direction import BANDS, ROOM_STALE_S, MusicDirection, effective_direction, music_fields, pick_key
from .piano import PIANO_LEVELS, PianoSignals, envelopes as piano_envelopes
from .plugins import Registry
from .plugins.base import ParamError
from .plugins.sources.image import ImageSource, load_png
from .presets import Presets, SceneSpec, TransitionSpec
from .profiles import PROFILES, Profile
from .renderer import Engine, EngineListener, Retire, StartTransition, UpdateParams
from .switches import StreamSwitches

log = logging.getLogger("stream_service.sessions")

TTL_DEFAULT_S = 120.0
TTL_MIN_S, TTL_MAX_S = 5.0, 3600.0
ENDED_KEEP_S = 600.0          # ended sessions stay visible to GET /api/sessions/{id} (and idempotency) this long
SCREEN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LIVE_STATES = ("entering", "live")
LOOK_FIELDS = ("direction", "palette", "palette_transition_s", "auto", "beat_follow", "direction_ease_s")
LOOK_SCOPES = ("room", "screen")    # owner 17 Oct: a control acts where it sits (a screen card: that screen only)
MAX_SCENE_TRANSITIONS = 2     # in / scene transitions running at once; one more cue waits (R6)
BEAT_MARGIN_S = 0.05          # a beat-snapped cue starts at least this far ahead of now
EVENT_MARGIN_S = 0.25         # a room.event's own time is used when it is at least this far past lead (G9)
CUT_EVENTS = ("section_change", "drop", "track_change")      # show_service's room.events that cue a scene change
TRACK_EVENTS = ("track_end_armed", "track_end", "track_change")   # its song ends (c8, SHOW_ENGINE §8.5 "Song ends")
DARK_SCENE = "black"          # fade-dark's dark: the scene preset shown across the gap between songs
DARK_MAX_S = 8.0              # never stay dark longer than this (or the event's dark_max_s)
FX_BAR_MAX_S = 4.0            # an auto effect move waits for the next downbeat when one is this close
CUE_SOURCES = ("panel", "director", "mode")
INPUTS_EVENT_MIN_S = 2.0
PALETTE_S_DEFAULT = 4.0
FIXTURE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class SessionError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def lenient_direction(body: dict, vocab: Optional[dict]) -> tuple[dict, list[str]]:
    """validate_direction for show_service's room.direction: a bad field (a transition_style outside the vocab, an
    unknown fx key) takes its default instead of losing the whole direction; returns (direction, dropped fields)."""
    try:
        return validate_direction(body, vocab), []
    except ParamError:
        pass
    dropped = []
    for k in list(body):
        try:
            validate_direction({k: body[k]}, vocab)      # a missing field defaults, so each field checks alone
        except ParamError:
            dropped.append(k)
    return validate_direction({k: v for k, v in body.items() if k not in dropped}, vocab), dropped


def decode_still(spec: Any, field: str) -> tuple[Optional[bytes], Optional[tuple[int, int, int]]]:
    """Validate an anchor / exit target: {"png_b64": ...} | {"rgb": [r, g, b]} -> (png bytes, rgb)."""
    if not isinstance(spec, dict) or len(spec) != 1 or not ({"png_b64", "rgb"} & set(spec)):
        raise ParamError("bad_still", f"{field} must be {{\"png_b64\": ...}} or {{\"rgb\": [r, g, b]}}")
    if "rgb" in spec:
        v = spec["rgb"]
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(c, int) and not isinstance(c, bool)
                                                              and 0 <= c <= 255 for c in v)):
            raise ParamError("bad_still", f"{field}.rgb must be [r, g, b] integers 0-255")
        return None, (v[0], v[1], v[2])
    b64 = spec["png_b64"]
    if isinstance(b64, PngData):          # already decoded by bodies.parse_body (chunked, off the loop)
        data = b64
    else:
        if not isinstance(b64, str):
            raise ParamError("bad_still", f"{field}.png_b64 must be a base64 string")
        try:
            data = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise ParamError("bad_still", f"{field}.png_b64 is not valid base64") from None
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ParamError("bad_image", f"{field}.png_b64 is not a PNG")
    return data, None



def _canon_bytes(o: Any) -> str:
    """json.dumps default for an idempotency canon: bodies.parse_body turns big png_b64 values into bytes."""
    if isinstance(o, bytes):
        return "sha256:" + hashlib.sha256(o).hexdigest()
    raise TypeError(f"{type(o).__name__} is not JSON serializable")


def sets_render_scale(spec: Any) -> bool:
    """Does a scene spec given in a request set render_scale itself (then calibration does not)?"""
    return isinstance(spec, dict) and isinstance(spec.get("params"), dict) and "render_scale" in spec["params"]


def spec_body(spec: SceneSpec, drop_effects: Any = ()) -> dict:
    """A scene spec as a request body that resolves back to it (params with bindings inline)."""
    params = {**{k: v for k, v in spec.params.items() if k not in spec.bindings}, **spec.bindings}
    body: dict = {"preset": spec.preset} if spec.preset else {"source": spec.source}
    body["params"] = params
    body["effects"] = [e.as_spec() for e in spec.effects if e.effect not in drop_effects]
    return body


def parse_palette(v: Any) -> Optional[str]:
    if v is not None and v not in palettes.PALETTES:
        raise ParamError("unknown_palette", f"unknown palette {v!r} (known: {palettes.NAMES})")
    return v


def parse_seconds(v: Any, field: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ParamError("bad_request", f"{field} must be a number of seconds")
    return min(hi, max(lo, float(v)))


class Session(EngineListener):
    def __init__(self, mgr: "SessionManager", *, sid: str, screen: str, gpu: int, profile: Profile,
                 scene: SceneSpec, t_in: TransitionSpec, t_out: TransitionSpec, ttl_s: float,
                 idempotency_key: Optional[str], auto: Optional[dict] = None, direction: Optional[dict] = None,
                 palette: Optional[str] = None, palette_s: float = PALETTE_S_DEFAULT, fixture: Optional[str] = None,
                 beat_follow: str = "extrapolate", direction_ease_s: float = 8.0):
        self.mgr = mgr
        self.id = sid
        self.key = secrets.token_urlsafe(18)
        self.screen = screen
        self.gpu = gpu
        self.profile = profile
        self.scene_spec = scene
        self.t_in, self.t_out = t_in, t_out
        self.ttl_s = ttl_s
        self.idempotency_key = idempotency_key
        self.created_at = time.time()
        self.expires_at = time.monotonic() + ttl_s
        self.ended_at: Optional[float] = None
        self.state = "starting"
        self.hold_pts: Optional[float] = None
        self.error: Optional[str] = None
        self.end_reason: Optional[str] = None
        self.media = MediaHub(sid, profile.fps)
        self.engine = Engine(profile=profile, gpu=gpu, post=mgr.post, listener=self, name=sid)
        sw = getattr(mgr, "switches", None)     # stream_switches.json, read now: this screen's encoder (plan change 2)
        self.engine.encoder = sw.encoder(screen) if sw is not None else "ffmpeg"
        self.media.want_key = getattr(self.engine, "request_keyframe", None)   # a joining player gets an IDR at once
        self.lock = asyncio.Lock()
        self.pending_scene: Any = None      # set up, shown on go
        self.current_scene: Any = None      # the scene source that is (or is becoming) visible
        self.current_name: str = scene.name
        self._tid = 0
        self.in_tid: Optional[int] = None
        self.exit_tid: Optional[int] = None
        self.ready = False
        self.attach: dict = {}
        # -- LIVE_V1 1.1: direction, masters, palette, inputs (modulation.py, inputs.py) --
        self.direction = direction or dict(DIRECTION_DEFAULTS, moods=[], fx=dict(FX_DIRECTION_DEFAULTS))
        self.direction_ease_s = direction_ease_s
        self.fixture = fixture or default_fixture(screen)
        self.beat_follow = beat_follow
        self.photon_ms, self.photon_source = load_photon(mgr.latency_file, self.fixture)
        self.lead_parts: dict = {}
        self.lead_ms: Optional[float] = None
        self._lead_t = 0.0
        self._buffered_ema: Optional[float] = None
        self._edge_jumps = 0
        self.sampler = Sampler(mgr.inputs, self.fixture, self._initial_lead() / 1000.0, beat_follow)
        self.runtime = SessionRuntime(self.sampler, speed=speed_for(self.direction["pace"]),
                                      depth=self.direction["intensity"], ease_s=direction_ease_s)
        self.runtime.feed = mgr.music_feed        # the room's sound and beat clock (music_feed.py)
        self.palette = palette
        self.palette_s = palette_s
        self.runtime.palette, self.runtime.palette_s = palette, palette_s
        # -- what scenes can hear (sound.py): the piano and the room mic, one view per session --
        self.sound = new_sound(feed=getattr(mgr, "scope_feed", None))   # + the fast scope / fft feed
        self.engine.sound = self.sound
        self.engine.frame_hook = self._frame_hook
        if getattr(self.engine, "bus", None) is not None:
            self.engine.bus.runtime = self.runtime        # master bus bindings
        # -- cues and the director (director.py) --
        self.director = Director(auto, self.direction)
        self._cue_n = 0
        self.cue_info: dict[int, dict] = {}       # tid -> fields added to that transition's events
        self.inflight: dict[int, dict] = {}       # in / scene transitions submitted and not done
        self.pending: Optional[dict] = None       # the single pending-cue slot (R6)
        self.cue_keys: dict[str, tuple[str, dict]] = {}
        self.last_cue: Optional[dict] = None
        self._epoch = 0                           # bumped by exit: in-flight prepares are cancelled (R5)
        self.exited = False
        self._auto_task: Optional[asyncio.Task] = None
        self._pick_task: Optional[asyncio.Task] = None
        self.prepared_pick: Optional[dict] = None
        self.upnext = UpNext()                    # the director's forward plan, editable (upnext.py)
        self._upnext_sent = -1
        self._fill_retry = {"scene": 0.0, "effect": 0.0}   # a queue that could not be filled waits this long
        self._event_hint: Optional[float] = None   # a room.event's glass time for the next cut (G9)
        self._building = False                     # a build is on: the next scene is the peak one, prepared (G10)
        self._dark: Optional[dict] = None          # fade-dark between songs: {since, in_s, prev, task}
        self.hud_wanted = False                    # the details panel on this screen's stream (hud.py)
        self._hud_t = -1e9
        self._hud_period = 0.5
        self._hud_lines: tuple[float, list, list] = (-1e9, [], [])
        self.own: set[str] = set()                 # look parts this screen set for itself (scope "screen"): never
                                                   # mirrored from the room; a room-scope change of the part clears it
        self._track_arm: Optional[dict] = None     # track_end_armed: {id, at, transition}
        self._inputs_state: Optional[tuple] = None
        self._inputs_t = -1e9
        self._palette_sent: Any = None
        self._palette_t = -1e9
        # -- effects layer (EFFECTS_LAYER_DESIGN C-D) --
        self.entry_spec = scene                   # the recovery scene when there is no auto block
        self.fx = SessionFx(self)
        self.fx_director = FxDirector(self.director.seed)
        self.fx_director.configure((auto or {}).get("fx"), self.direction)
        # -- the music proposes direction (music_direction.py): the directors use the panel's, trimmed by it --
        self.music = MusicDirection(self.director.auto.get("music") if self.director.auto else None)
        self.piano = PianoSignals()               # the piano's bind signals (piano.py) from self.sound
        self.direction_effective = self.direction
        self._dir_key = pick_key(self.direction)
        self._dir_gen = 0                         # bumped when a prepared pick no longer fits the direction
        self._dir_drop_t = -1e9
        self._fx_task: Optional[asyncio.Task] = None
        self._scene_fx_n = 0
        self.plugin_error_count = 0
        self._recovering = False
        # -- GPU frame-time guard and the vetted pool --
        self.slow_trips = 0
        self.current_spec: Optional[SceneSpec] = None     # the scene on screen (or becoming so)
        self.prev_spec: Optional[SceneSpec] = None        # the one before it: a slow scene's recovery target
        self.director.vet = self.fx_director.vet = self.room_ok
        self.director.rate = self.fx_director.rate = self.mgr.rating_weight     # the owner's stars (ratings.py)
        self.fx_director.clock = self._fx_clock     # FX cadence in phrases, fades in beats, while the clock is sure
        self.fx_director.looks = fx_interplay.shared(self.mgr.presets.dir)   # scenes and effects together
        self.fx_director.scene = lambda: (self.current_name, self.mgr.presets.scenes.get(self.current_name or ""))
        self.fx_director.heard = self._heard_now    # an effect drawn from the sound: weighted by what there is
        # -- room-level direction (rooms.py): set by the manager on create --
        self.room: Optional[str] = None
        self.scene_cause: Optional[str] = None    # what put the current scene up: go | auto | manual | patch
        self.follow_pick: Optional[dict] = None   # the leader's next pick, prepared here
        self._follow_prep: Optional[asyncio.Task] = None
        self._follow_want: Optional[tuple] = None
        self._follow_gen = 0
        self._room_synced: Optional[str] = None   # the leader this follower last caught up with
        self._fx_want: Optional[tuple] = None     # (leader's auto presets, fade_s, why) to mirror
        self._fx_check_t = 0.0
        self._following = False

    # -- helpers -----------------------------------------------------------------
    def emit(self, type_: str, **data) -> None:
        self.mgr.events.emit({"type": type_, "session": self.id, "screen": self.screen, **data})

    def set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        self.emit("session.state", state=state)

    def renew(self) -> None:
        if self.state != "ended":
            self.expires_at = time.monotonic() + self.ttl_s

    def next_tid(self) -> int:
        self._tid += 1
        return self._tid

    def next_cue_id(self) -> str:
        self._cue_n += 1
        return f"c-{self._cue_n:04d}"

    def _transition(self, spec: TransitionSpec):
        return self.mgr.registry.transition(spec.transition)(spec.params)

    def _cue_signals(self) -> dict:
        """The signals now, as the render thread would see them (bus + music features), for cue-mode mappings."""
        now = time.monotonic()
        sig = dict(self.sampler.peek(now))
        feed = self.mgr.music_feed
        c = feed.clock(now)
        wf = feed.liveness(now)
        feed_blend(sig, feed.snapshot(), wf, clock_signals(c, now + self.sampler.lead) if c else {},
                   feed.levels(now), feed.onset_pulse(now))
        ex = self.runtime.extra or {}
        w = ex.get("music_w")
        fast = None if w is None else feed.energy_now(now)
        music_signals(sig, ex, self.sampler.w if w is None else w, fast, wf)
        self.runtime.pivots.apply(sig)            # each level centred on its own norm, as the frames see it
        return sig

    def _sample_transition(self, spec: TransitionSpec) -> TransitionSpec:
        """A transition's cue-mode mappings sampled now: a spec with plain values (the room's followers get this
        one, so every screen runs the same transition)."""
        if not spec.bindings:
            return spec
        cls = self.mgr.registry.transition(spec.transition)
        grid = self._beat_grid(time.monotonic())
        vals = binding.sample_cue(cls.param_props(), spec.bindings, spec.params, self._cue_signals(), None,
                                  self.runtime.depth, f"transition/{spec.transition}", grid[1] if grid else None)
        if not vals:
            return dataclasses.replace(spec, bindings={})
        try:
            params = cls.validate({**spec.params, **vals})
        except ParamError:
            params = spec.params
        return dataclasses.replace(spec, params=params, bindings={})

    def _beat_grid(self, now: float) -> Optional[tuple[float, float]]:
        """(anchor, period) of the beat in host monotonic seconds (acoustic): show_service's music.clock while it
        is sure (SessionManager.music_grid), else the lighting desk's pulse follower; None when not following."""
        if self.sampler.follow != "extrapolate":
            return None
        r = self.mgr.room_direction_now(now)
        if r is not None and r["vibe"] is not None and r.get("beat_lock") is False:   # the vibe wants no beat lock
            return None
        return self.mgr.music_grid(now) or self.sampler.beat_grid(now)

    def _fx_clock(self, now: float) -> Optional[tuple[float, int]]:
        """(beat s, beats per bar) while the beat grid is sure (the FX director's phrases and beats), else None."""
        g = self._beat_grid(now)
        if g is None:
            return None
        c = self.mgr.music_feed.clock(now) or {}
        try:
            bpb = max(1, int(c.get("beats_per_bar") or 4))
        except (TypeError, ValueError):
            bpb = 4
        return g[1], bpb

    def _heard_now(self) -> dict:
        """{listens kind: 0..1} what there is to hear now (fx_interplay's listens): the piano's notes, and the
        room's music for spectrum / scope / fft.  Not sound.scope_live: the fast feed connects only once such an
        effect is on screen (c6f43e9b), and fft follows spectrum32 until then.  Unknown kinds are left out."""
        out: dict = {}
        snd = self.sound
        if snd is not None:
            out["notes"] = out["chroma"] = 1.0 if snd.present else 0.0
        music = self.mgr.music_feed.presence(time.monotonic())
        if music is not None:
            out["spectrum"] = out["scope"] = out["fft"] = music
        return out

    def _next_downbeat(self, now: float, margin: float = BEAT_MARGIN_S) -> Optional[float]:
        """The photon time (monotonic s, acoustic) of the next downbeat that can still land on glass, from
        show_service's clock while it is sure (bars only come from there); None otherwise."""
        if self._beat_grid(now) is None or self.mgr.music_grid(now) is None:
            return None
        c = self.mgr.music_feed.clock(now)
        if c is None:
            return None
        try:
            period = 60.0 / float(c["bpm"])
            bpb = max(1, int(c.get("beats_per_bar") or 4))
            bar0 = float(c.get("bar_anchor_beat") or 0)
            b = beat_at(c, now + self.sampler.lead + margin)
            k = math.ceil((b - bar0) / bpb)
            beat_n = bar0 + k * bpb
            return float(c["anchor_ms"]) / 1000.0 + (beat_n - float(c.get("beat_at_anchor") or 0)) * period
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

    async def _transition_ready(self, spec: TransitionSpec, mapped: bool = True):
        """A transition instance, set up on a worker thread when the engine supports it (R20).  ``mapped``: sample
        its cue-mode music mappings (never for go / exit: art <-> live stays as authored)."""
        spec = self._sample_transition(spec) if mapped else spec
        t = self._transition(spec)
        t.frozen_spec = spec
        if hasattr(self.engine, "prepare_transition") and getattr(self.engine, "ctx", None) is not None:
            try:
                await self._run(self.engine.prepare_transition, t)
            except ParamError:
                raise
            except Exception as e:
                self.note_prepare_failure("transition", spec.transition, spec.preset, e)
                raise SessionError(500, "prepare_failed", f"{spec.name}: {type(e).__name__}") from None
        return t

    def _require(self, *states: str) -> None:
        if self.state not in states:
            raise SessionError(409, "bad_state", f"session is {self.state}; this needs {'/'.join(states)}")

    async def _run(self, fn: Callable, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def decode(self, png: Optional[bytes], rgb: Optional[tuple[int, int, int]]) -> ImageSource:
        """PNG bytes (decoded + letterboxed in a worker thread) or a colour -> an ImageSource, not yet set up."""
        if png is not None:
            p = self.profile
            return ImageSource(pixels=await self._run(load_png, png, p.w, p.h))
        return ImageSource(rgb=rgb)

    async def _prepare(self, src: Any, params: Optional[dict] = None):
        return await self._run(self.engine.prepare, src, params or {})

    async def _make_scene(self, spec: SceneSpec):
        cls = self.mgr.registry.source(spec.source)
        src = cls()
        # scene sources (never the internal image source: R1) get the session's modulation
        props = cls.param_props()
        params, live = self._cue_split(props, spec.bindings, spec.params, f"scene/{spec.source}")
        src.modulation = Modulation(self.runtime, props, live, key=f"scene/{spec.source}")
        try:
            src = await self._run(self.engine.prepare, src, params)
        except ParamError:
            raise
        except Exception as e:         # C.3: a plug-in that fails to set up / prime
            self.note_prepare_failure("source", spec.source, spec.preset, e)
            self.director.quarantine_preset(spec.name, time.monotonic(), session=True)   # never auto-picked again
            raise SessionError(500, "prepare_failed", f"{spec.name}: {type(e).__name__}") from None
        if spec.effects:
            self._scene_fx_n += 1
            slots: list[EffectSlot] = []
            for i, es in enumerate(spec.effects):
                ecls = self.mgr.registry.effect(es.effect)
                eparams, elive = self._cue_split(ecls.param_props(), es.bindings, es.params, f"effect/{es.effect}")
                try:
                    eff = await self.prepare_effect(ecls(), eparams)
                except Exception as e:
                    self.note_prepare_failure("effect", es.effect, es.preset, e)
                    for sl in slots:
                        self.retire(sl.effect)
                    self.retire(src)
                    raise SessionError(500, "prepare_failed", f"{es.name}: {type(e).__name__}") from None
                slots.append(EffectSlot(f"s{self._scene_fx_n}-{i}", eff, name=ecls.name, preset=es.preset,
                                        stage=getattr(ecls, "stage", "stylise"),
                                        modulation=Modulation(self.runtime, ecls.param_props(), elive,
                                                              key=f"effect/{es.effect}"),
                                        mix=getattr(ecls, "amount_mode", "native") == "mix", level=1.0, target=1.0,
                                        where="scene", label=es.label))
            src.fx_chain = EffectChain(slots)
            src.fx_specs = spec.effects
        return src

    def _cue_split(self, props: dict, bindings: dict, params: dict, key: str) -> tuple[dict, dict]:
        """-> (params with the cue-mode mappings sampled in, the bindings left for the render thread)."""
        if not any(binding.is_v2(b) and b.get("at") == "cue" and "colour" not in b for b in bindings.values()):
            return params, bindings
        vals = binding.sample_cue(props, bindings, params, self._cue_signals(), None, self.runtime.depth, key)
        live = {k: b for k, b in bindings.items() if k not in vals}
        return {**params, **vals}, live

    async def prepare_effect(self, eff: Any, params: dict) -> Any:
        """Set an effect up (and prime it) on a worker thread."""
        if hasattr(self.engine, "prepare_effect"):
            return await self._run(self.engine.prepare_effect, eff, params)
        return eff

    def note_prepare_failure(self, kind: str, name: str, preset: Optional[str], exc: BaseException,
                             slot: Optional[str] = None) -> None:
        detail = f"{type(exc).__name__}: {exc}"[:300]
        log.error("[%s] %s %s failed to prepare: %s", self.id, kind, name, detail)
        count = self.mgr.note_plugin_error(kind, name, detail, self.id)
        self.plugin_error_count += 1
        self.emit("session.plugin_error", kind=kind, name=name, preset=preset, phase="prepare", action="rejected",
                  detail=detail, count=count, **({"slot": slot} if slot else {}))

    def _retire(self, *objs: Any) -> None:
        for obj in objs:
            if obj is not None:
                self.engine.submit(Retire(obj))

    def retire(self, obj: Any) -> None:
        self._retire(obj)

    def pts_now(self) -> float:
        return self._pts_now()

    def pixels_ratio(self) -> float:
        """This session's pixels over 4K's (bench costs are 4K ms)."""
        return (self.profile.w * self.profile.h) / (3840 * 2160)

    def trip_budget_4k(self) -> float:
        """The GPU guard's per-plug-in budget for this session, in 4K-equivalent ms (harness costs are 4K)."""
        return trip_budget_ms(self.profile.fps) / max(1e-6, self.pixels_ratio())

    def auto_ok(self, kind: str, plugin: Optional[str]) -> bool:
        """May the directors pick this plug-in on their own?  (the vetted pool; manual cues may pick anything)"""
        return self.mgr.auto_ok(kind, plugin, self.gpu, self.trip_budget_4k())

    # -- room-level direction (rooms.py): one director per room, the room's other screens follow it ----------
    def room_leader(self) -> Optional["Session"]:
        """The session whose director decides for this one's room (itself when it leads); None: not in a room."""
        if not self.room or self.director.auto is None or self.exited:
            return None
        return self.mgr.room_leader(self.room)

    def following(self) -> Optional["Session"]:
        ld = self.room_leader()
        return ld if ld is not None and ld is not self else None

    def room_peers(self) -> list["Session"]:
        if not self.room or self.director.auto is None or self.exited:
            return []
        return [s for s in rooms.members(self.mgr.sessions.values(), self.room) if s is not self]

    def followers(self) -> list["Session"]:
        return self.room_peers() if self.room_leader() is self else []

    def room_ok(self, kind: str, plugin: Optional[str]) -> bool:
        """The directors' vetted pool: what every screen in the room may show."""
        return self.auto_ok(kind, plugin) and all(p.auto_ok(kind, plugin) for p in self.room_peers())

    def room_status(self) -> Optional[dict]:
        if not self.room:
            return None
        ld = self.room_leader()
        return {"id": self.room, "role": "solo" if ld is None else "leader" if ld is self else "follower",
                "leader": None if ld is None else ld.screen,
                "screens": [s.screen for s in rooms.members(self.mgr.sessions.values(), self.room)]}

    def _room_prepare(self, pk: dict) -> None:
        """Leader: the next pick is prepared here, so prepare it on the followers too."""
        for f in self.followers():
            f.follow_prepare(pk["spec"], getattr(pk["trans"], "frozen_spec", None) or pk["trans_spec"])

    async def _room_ready(self) -> None:
        """Leader: wait (at most READY_S) for the followers' copies of the pick, so the screens cut together."""
        tasks = [f._follow_prep for f in self.followers() if f._follow_prep is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=rooms.READY_S)

    def _room_go(self, cue: dict) -> None:
        """Leader: an auto cue just started here; the followers cut to the same scene."""
        for f in self.followers():
            f.follow_cue(cue["spec"], getattr(cue["trans"], "frozen_spec", None) or cue["trans_spec"], cue["at"],
                         f"{self.screen}: {cue.get('detail') or cue['scene']}", cue.get("photon_at"))

    async def _prepare_like(self, spec: SceneSpec, trans_spec: TransitionSpec) -> dict:
        """The leader's scene and transition, prepared on this session's GPU (with its own fit)."""
        mine = self._fit(self.mgr.presets.resolve_scene(rooms.room_body(spec)), False)
        src = await self._make_scene(mine)
        try:
            trans = await self._transition_ready(trans_spec)
        except Exception:
            try:
                trans_spec, trans = self.t_in, await self._transition_ready(self.t_in)
            except Exception:
                self._retire(src)
                raise
        return {"want": (spec.name, trans_spec.name), "spec": mine, "src": src, "trans_spec": trans_spec,
                "trans": trans}

    def follow_prepare(self, spec: SceneSpec, trans_spec: TransitionSpec) -> None:
        if self.exited or self.state not in LIVE_STATES:
            return
        want = (spec.name, trans_spec.name)
        if self._follow_want == want and (self._follow_prep is not None or self.follow_pick is not None):
            return
        self._follow_gen += 1
        self._follow_want = want
        self._follow_prep = asyncio.create_task(self._follow_prepare(self._follow_gen, spec, trans_spec))

    async def _follow_prepare(self, gen: int, spec: SceneSpec, trans_spec: TransitionSpec) -> None:
        epoch = self._epoch
        try:
            pk = await self._prepare_like(spec, trans_spec)
            pk["want"] = (spec.name, trans_spec.name)
        except Exception as e:
            log.warning("[%s] room: preparing the leader's pick %s failed: %s", self.id, spec.name,
                        getattr(e, "detail", None) or f"{type(e).__name__}: {e}")
            return
        finally:
            if gen == self._follow_gen:
                self._follow_prep = None
        if gen != self._follow_gen or self._epoch != epoch or self.state not in LIVE_STATES:
            self._retire(pk["src"], pk["trans"])
            return
        old, self.follow_pick = self.follow_pick, pk
        if old is not None:
            self._retire(old["src"], old["trans"])

    def _drop_follow(self) -> None:
        self._follow_gen += 1
        self._follow_want = None
        if self.follow_pick is not None:
            pk, self.follow_pick = self.follow_pick, None
            self._retire(pk["src"], pk["trans"])

    def follow_cue(self, spec: SceneSpec, trans_spec: TransitionSpec, at: str, detail: str,
                   photon_at: Optional[float] = None) -> None:
        """``photon_at``: the leader's glass time for the cut, so every screen lands on the same beat (its own
        lead subtracted here), not each on its own next beat."""
        if self.exited or self.state not in LIVE_STATES:
            return
        asyncio.create_task(self._follow_cue(spec, trans_spec, at, detail, photon_at))

    async def _follow_cue(self, spec: SceneSpec, trans_spec: TransitionSpec, at: str, detail: str,
                          photon_at: Optional[float] = None) -> None:
        want = (spec.name, trans_spec.name)
        epoch = self._epoch
        try:
            if self._follow_prep is not None and self._follow_want == want:
                await asyncio.wait([self._follow_prep], timeout=rooms.READY_S * 2)
            pk, self.follow_pick = self.follow_pick, None
            if pk is not None and pk["want"] != want:
                self._retire(pk["src"], pk["trans"])
                pk = None
            self._follow_gen += 1                       # a copy still being prepared is dropped when it lands
            self._follow_want = None
            if pk is None:
                pk = await self._prepare_like(spec, trans_spec)
            async with self.lock:
                if self._epoch != epoch or self.state not in LIVE_STATES or self.following() is None:
                    self._retire(pk["src"], pk["trans"])
                    return
                cue = self._new_cue("auto", pk["spec"], pk["trans_spec"], pk["src"], pk["trans"], at, "room", detail,
                                    photon_at=photon_at)
                if self._dispatch(cue) != "skipped":
                    self.scene_spec = pk["spec"]
        except Exception as e:
            log.warning("[%s] room: following %s failed: %s", self.id, spec.name,
                        getattr(e, "detail", None) or f"{type(e).__name__}: {e}")

    def _room_direction(self, cause: str) -> None:
        """Leader: its effective direction just changed; the followers take it."""
        for f in self.followers():
            f._apply_direction(cause)

    def fx_source(self) -> Optional["Session"]:
        """The session whose auto effects this one mirrors, or None when it directs its own: a screen that set its
        effects for itself (``own`` fx) does; the room's effects come from the leader, or, when the leader set its
        own, from the first other screen that didn't."""
        if "fx" in self.own or not self.room or self.director.auto is None or self.exited:
            return None
        ld = self.room_leader()
        if ld is None:
            return None
        if "fx" not in ld.own:
            return None if ld is self else ld
        heads = [m for m in rooms.members(self.mgr.sessions.values(), self.room) if "fx" not in m.own]
        return None if not heads or heads[0] is self else heads[0]

    def _room_fx(self) -> None:
        """This screen's auto FX just moved: the screens mirroring it follow."""
        want = [p for _, p in rooms.auto_fx(self.fx.stack)]
        for f in self.room_peers():
            if f.fx_source() is self:
                f.follow_fx(want, self.fx_director.fade_s(), "room")

    def follow_fx(self, want: list, fade_s: float, why: str) -> None:
        self._fx_want = (list(want), fade_s, why)
        if self._fx_task is None and self.state == "live" and not self.exited:
            self._fx_task = asyncio.create_task(self._follow_fx())

    async def _follow_fx(self) -> None:
        """Move this screen's auto effects to the leader's, one add / remove / swap at a time."""
        try:
            for _ in range(8):
                if self._fx_want is None:
                    break
                want, fade_s, why = self._fx_want
                add, remove = rooms.fx_step(rooms.auto_fx(self.fx.stack), want)
                if add is None and remove is None:
                    self._fx_want = None
                    break
                op = "swap" if add and remove else "add" if add else "remove"
                res = await self.fx.auto_move(FxMove(op, add, remove, f"{why}: {op} {add or remove}"), fade_s)
                if res is None:
                    break
                if add and res.get("rejected"):          # cannot run here: leave it out on this screen
                    want.remove(add)
        except Exception:
            log.exception("[%s] room: mirroring the leader's effects failed", self.id)
        finally:
            self._fx_task = None

    def _follow_tick(self, now: float, ld: "Session") -> None:
        """Follower: catch up with a (new) leader's scene once, and keep the auto effects equal to its."""
        if self.state != "live" or self.exited:
            return
        if self._room_synced != ld.id and not self.inflight and self.pending is None:
            self._room_synced = ld.id
            if ld.current_spec is not None and ld.current_name != self.current_name \
                    and ld.scene_cause in ("go", "auto") and ld.state == "live":
                self.follow_cue(ld.current_spec, self.t_in, "now", f"joined {ld.screen}")
        src = self.fx_source()
        if src is None:                             # its own effects (or the room's, from here): direct them
            self._fx_tick(now)
        elif now - self._fx_check_t >= rooms.FX_SYNC_S and self._fx_task is None and src._fx_task is None:
            self._fx_check_t = now
            want = [p for _, p in rooms.auto_fx(src.fx.stack)]
            if rooms.fx_step(rooms.auto_fx(self.fx.stack), want) != (None, None):
                self.follow_fx(want, src.fx_director.fade_s(), "room")

    def _fit(self, spec: SceneSpec, explicit: bool) -> SceneSpec:
        return self.mgr.fit_scene(spec, self.gpu, self.pixels_ratio(), explicit)

    def fx_auto_status(self) -> dict:
        return (self.following() or self).fx_director.status(time.monotonic())

    def _pts_now(self) -> float:
        return getattr(self.engine, "frames", 0) / self.profile.fps

    def _pts_at(self, t_mono: Optional[float]) -> float:
        dt_s = 0.0 if t_mono is None else max(0.0, t_mono - time.monotonic())
        return round(self._pts_now() + dt_s, 3)

    # -- lifecycle -----------------------------------------------------------------
    def _frame_hook(self, now: float) -> None:
        """Render thread, once per rendered frame: the modulation clock, then the sound view at photon time."""
        self.runtime.begin_frame(now)
        if self.sound is not None:
            self.sound.update(now + self.sampler.lead)

    def _alloc_sound(self) -> None:
        ctx = getattr(self.engine, "ctx", None)
        if self.sound is not None and ctx is not None:
            with ctx.device:
                self.sound.alloc(ctx.cp)

    async def start(self, anchor: ImageSource) -> None:
        await self._run(self.engine.create_context)
        await self._run(self._alloc_sound)
        if getattr(self.engine, "ctx", None) is not None: self.engine.ctx.screen = self.screen  # for live_art
        if hasattr(self.engine, "warm"):    # compile/load the usual transitions once per GPU (R20); usually done at start
            await self.mgr.ensure_warm(self.gpu, lambda: self._run(self.engine.warm, self.mgr.warm_plugins))
        anchor = await self._prepare(anchor)
        self.pending_scene = await self._make_scene(self.scene_spec)
        await self._run(self.engine.start, anchor)

    async def go(self) -> dict:
        async with self.lock:
            self._require("holding")
            trans = await self._transition_ready(self.t_in, mapped=False)
            if self.state != "holding":
                self._retire(trans)
                self._require("holding")
            tid = self.next_tid()
            self.in_tid = tid
            src, self.pending_scene = self.pending_scene, None
            self.current_scene = src
            self.current_name = self.scene_spec.name
            self.current_spec = self.scene_spec
            cue_id = self.next_cue_id()
            self.cue_info[tid] = {"cue_id": cue_id, "cause": "go", "scene": self.scene_spec.name,
                                  "transition": self.t_in.name}
            self.inflight[tid] = {"cue_id": cue_id, "cause": "go"}
            self.set_state("entering")
            self.engine.submit(StartTransition("in", tid, src, trans))
            self.director.note_cue(self.scene_spec.name, self.t_in.name, self.sampler.snapshot())
            self.scene_cause = "go"
            return {"cue_id": cue_id, "duration_s": trans.duration_s}

    def _patched_spec(self, body: dict) -> SceneSpec:
        """The scene spec a PATCH asks for, from the current one (raises ParamError)."""
        spec = self.scene_spec
        if body.get("scene") is not None:
            spec = self.mgr.presets.resolve_scene(body["scene"])
            spec = self._fit(spec, sets_render_scale(body["scene"]) or sets_render_scale(body))
        if body.get("params") is not None:
            cls = self.mgr.registry.source(spec.source)
            plain, bindings = split_bindings(cls.param_props(), body["params"], spec.bindings,
                                             self.mgr.presets.scene_restore(spec))
            spec = SceneSpec(spec.source, cls.validate(plain, base=spec.params), spec.preset, spec.label, bindings)
        return spec

    async def patch(self, body: dict) -> None:
        if "hud" in body:                          # the details panel: its own branch, this screen only
            v = body["hud"]
            if not isinstance(v, bool):
                raise ParamError("bad_request", "hud must be true or false")
            if self.state in ("ended", "failed"):
                raise SessionError(409, "bad_state", f"session is {self.state}")
            self.hud_wanted = v
            self._hud_sync(time.monotonic())
            body = {k: x for k, x in body.items() if k != "hud"}
            if not body:
                return
        unknown = set(body) - {"scene", "params", "transition", "scope", *LOOK_FIELDS}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        scope = body.get("scope", "room")
        if scope not in LOOK_SCOPES:
            raise ParamError("bad_scope", f"scope must be one of {list(LOOK_SCOPES)}")
        body = {k: v for k, v in body.items() if k != "scope"}
        look = self._parse_look(body)             # validated before anything changes (400)
        if not {"scene", "params", "transition"} & set(body):
            # direction / palette / auto / beat_follow only: every non-terminal state (R12)
            if self.state in ("ended", "failed"):
                raise SessionError(409, "bad_state", f"session is {self.state}")
            self._apply_look(look, scope)
            return
        self._require("holding", *LIVE_STATES)
        spec = self._patched_spec(body)           # validated before anything changes (400)
        trans_spec = self.mgr.presets.resolve_transition(body["transition"]) if body.get("transition") \
            else self.t_in
        new_scene = body.get("scene") is not None
        # In holding the scene is not on screen yet, so new params rebuild it: a live edit would only start
        # easing (from the old values) after go, e.g. morphing a fractal's c through other Julia sets
        # during the in transition.
        if self.state == "holding" and (new_scene or body.get("params") is not None):
            # Decided and prepared under the session lock (R5 does not apply: exit is a 409 in holding), so a
            # go that arrives meanwhile waits and starts the rebuilt scene, and PATCHes apply one on another.
            async with self.lock:
                if self.state == "holding":
                    spec = self._patched_spec(body)            # on top of any PATCH that ran while we waited
                    src = await self._make_scene(spec)
                    if self.state != "holding":                # e.g. failed while preparing
                        self._retire(src)
                        raise SessionError(409, "bad_state", f"session is {self.state}")
                    old, self.pending_scene = self.pending_scene, src
                    self._retire(old)
                    self.scene_spec = spec
                    self._apply_look(look)
                    return
            # a go won the lock while this PATCH waited: it is an entering/live PATCH from here on
            self._require(*LIVE_STATES)
            spec = self._patched_spec(body)
        if new_scene:
            # entering/live: prepared outside the session lock, so an exit never waits behind it (R5)
            epoch = self._epoch
            src = await self._make_scene(spec)
            trans = await self._transition_ready(trans_spec)
            async with self.lock:
                if self._epoch != epoch or self.state not in LIVE_STATES:   # e.g. exited or failed
                    self._retire(src, trans)
                    raise SessionError(409, "bad_state", f"session is {self.state}")
                self.scene_spec = spec
                self._dispatch(self._new_cue("patch", spec, trans_spec, src, trans, "now"))
        else:
            if body.get("params") is not None and self.current_scene is not None:
                self.engine.submit(UpdateParams(self.current_scene, spec.params, bindings=spec.bindings))
            self.scene_spec = spec
        self._apply_look(look)

    async def exit(self, body: dict) -> dict:
        unknown = set(body) - {"target", "transition"}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        if "target" not in body:
            raise ParamError("bad_request", "exit needs a target")
        png, rgb = decode_still(body["target"], "target")
        trans_spec = self.mgr.presets.resolve_transition(body["transition"]) if body.get("transition") \
            else self.t_out
        exit_states = ("entering", "live", "exiting", "held")
        # 'held' is accepted too (additive): a new target from the hold re-runs the out transition
        self._require(*exit_states)
        target = await self.decode(png, rgb)   # a bad PNG is a 400 before anything changes
        self._require(*exit_states)
        self._cancel_for_exit()                # accepted: pending cues and the director stop here (R5)
        async with self.lock:
            self._require(*exit_states)
            target = await self._prepare(target)
            trans = await self._transition_ready(trans_spec, mapped=False)
            if self.state not in exit_states:
                self._retire(target, trans)
                raise SessionError(409, "bad_state", f"session is {self.state}")
            tid = self.next_tid()
            self.exit_tid = tid
            self.hold_pts = None
            cue_id = self.next_cue_id()
            self.cue_info[tid] = {"cue_id": cue_id, "cause": "exit", "transition": trans_spec.name}
            self.set_state("exiting")
            self.engine.submit(StartTransition("out", tid, target, trans))
            return {"cue_id": cue_id, "duration_s": trans.duration_s}

    def _cancel_for_exit(self) -> None:
        self._epoch += 1
        self.exited = True
        self.fx.cancel_for_exit()
        self.fx_director.arm(False, time.monotonic())
        if self.director.arm(False):
            self._emit_director()
        if self.pending is not None:
            p, self.pending = self.pending, None
            self._retire(p["src"], p["trans"])
            self._cue_event("cancelled", p, reason="exit")
        if self.prepared_pick is not None:
            pk, self.prepared_pick = self.prepared_pick, None
            self._retire(pk["src"], pk["trans"])
        self._drop_follow()

    async def end(self, reason: str, detail: Optional[str] = None) -> None:
        if self.state == "ended":
            return
        self.end_reason = reason
        self.ended_at = time.monotonic()
        self._epoch += 1
        self.set_state("ended")
        self.emit("session.ended", reason=reason, **({"detail": detail} if detail else {}))
        self.media.end()
        pending, self.pending_scene = self.pending_scene, None
        leftovers = []
        if self.pending is not None:
            leftovers += [self.pending["src"], self.pending["trans"]]
            self.pending = None
        if self.prepared_pick is not None:
            leftovers += [self.prepared_pick["src"], self.prepared_pick["trans"]]
            self.prepared_pick = None
        if self.follow_pick is not None:
            leftovers += [self.follow_pick["src"], self.follow_pick["trans"]]
            self.follow_pick = None
        self._follow_gen += 1
        await self._run(self.engine.stop)
        for obj in [pending, *leftovers]:
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass

    # -- cue (LIVE_V1 1.1 §1.6; review R3-R10) ---------------------------------------
    def _new_cue(self, cause: str, spec: SceneSpec, trans_spec: TransitionSpec, src: Any, trans: Any, at: str,
                 reason: Optional[str] = None, detail: Optional[str] = None, source: Optional[str] = None,
                 photon_at: Optional[float] = None) -> dict:
        """``photon_at``: when the change should reach the glass (monotonic s, acoustic): the leader's, so a room's
        screens cut on the same beat, or a room.event's time (G9)."""
        return {"cue_id": self.next_cue_id(), "cause": cause, "reason": reason, "detail": detail,
                "source": source, "scene": spec.name, "transition": trans_spec.name, "spec": spec, "src": src,
                "trans": trans, "trans_spec": trans_spec, "at": at, "fallback": None, "at_pts": None,
                "duration_s": trans.duration_s, "photon_at": photon_at}

    def _cue_event(self, phase: str, cue: dict, **extra) -> None:
        fields = {k: cue[k] for k in ("cause", "reason", "source", "scene", "transition", "at", "fallback",
                                      "at_pts", "detail") if cue.get(k) is not None}
        self.emit("session.cue", cue_id=cue["cue_id"], phase=phase, **{**fields, **extra})

    def _dispatch(self, cue: dict) -> str:
        """Start a scene cue now (or on its beat), or park it in the pending slot (R6).  Loop thread, no awaits.
        -> "scheduled" | "pending" | "skipped"."""
        if len(self.inflight) >= MAX_SCENE_TRANSITIONS:
            if self.pending is not None:
                if cue["cause"] == "auto" and self.pending["cause"] != "auto":   # never replaces a manual one
                    self._retire(cue["src"], cue["trans"])
                    self._cue_event("skipped", cue, why="a manual cue is pending")
                    return "skipped"
                old, self.pending = self.pending, None
                self._retire(old["src"], old["trans"])
                self._cue_event("cancelled", old, reason="replaced")
            ends = sorted(i.get("end_pts") or self._pts_now() for i in self.inflight.values())
            cue["at_pts"] = round(ends[0], 3) if ends else None
            self.pending = cue
            self._cue_event("scheduled", cue, pending=True)
            return "pending"
        self._submit(cue)
        return "scheduled"

    def _submit(self, cue: dict) -> None:
        now = time.monotonic()
        at_mono = None
        lead = self.sampler.lead
        pa = cue.get("photon_at")
        if pa is not None and pa - lead > now + BEAT_MARGIN_S:   # the leader's beat / the event's time, if still ahead
            at_mono = pa - lead
        elif cue["at"] == "bar":
            t = self._next_downbeat(now)
            if t is not None:
                at_mono = t - lead
            else:
                cue["at"], cue["fallback"] = "beat", "no_bar"
        if at_mono is None and cue["at"] == "beat":
            grid = self._beat_grid(now)
            if grid is None:
                cue["at"], cue["fallback"] = "now", "no_beat"
            else:                 # the next predicted beat that can still land on glass on time
                anchor, period = grid
                lead = self.sampler.lead
                k = math.ceil((now + lead + BEAT_MARGIN_S - anchor) / period)
                at_mono = anchor + k * period - lead
        cue["photon_at"] = None if at_mono is None else at_mono + lead
        tid = self.next_tid()
        cue["tid"] = tid
        cue["at_pts"] = self._pts_at(at_mono)
        cue["end_pts"] = cue["at_pts"] + cue["duration_s"]
        self.cue_info[tid] = {k: cue[k] for k in ("cue_id", "cause", "reason", "source", "scene", "transition",
                                                    "detail") if cue.get(k) is not None}
        self.inflight[tid] = cue
        if self.current_spec is not None and self.current_spec.name != cue["scene"]:
            self.prev_spec = self.current_spec
        self.current_spec = cue["spec"]
        self.current_scene = cue["src"]
        self.current_name = cue["scene"]
        self.engine.submit(StartTransition("scene", tid, cue["src"], cue["trans"], at_mono=at_mono))
        self._cue_event("scheduled", cue)
        self.director.note_cue(cue["scene"], cue["transition"], self.sampler.snapshot())
        if cue["cause"] != "auto" and self.director.dwell_start is not None:
            self.director.scene_done(now)     # a hand-picked scene gets a full dwell (again from its transition's done)
        self.last_cue = {k: cue.get(k) for k in ("cue_id", "cause", "reason", "detail", "scene", "transition", "at",
                                                 "fallback", "at_pts", "duration_s")}
        self.director.last = self.last_cue if cue["cause"] == "auto" else self.director.last
        self.scene_cause = cue["cause"]
        if cue["cause"] == "auto" and cue.get("reason") != "room" and self.room_leader() is self:
            self._room_go(cue)

    def _release_pending(self) -> None:
        if self.pending is None or len(self.inflight) >= MAX_SCENE_TRANSITIONS:
            return
        cue, self.pending = self.pending, None
        if self.state not in LIVE_STATES or self.exited:
            self._retire(cue["src"], cue["trans"])
            self._cue_event("cancelled", cue, reason="exit")
            return
        self._submit(cue)

    def _take_prepared(self) -> Optional[dict]:
        pk, self.prepared_pick = self.prepared_pick, None
        if pk is not None and (pk["scene"] == self.current_name          # stale: the screen moved on by hand
                               or not self._is_head(pk)                  # Up next was edited meanwhile
                               or (pk.get("origin") != "you" and not self.auto_ok("source", pk["spec"].source))
                               or not self.auto_ok("transition", pk["trans_spec"].transition)):
            self._retire(pk["src"], pk["trans"])
            return None
        return pk

    PICK_TRIES = 4

    async def _pick_and_prepare(self, via: Optional[TransitionSpec] = None) -> dict:
        """Up next's head (the director's pick, or the owner's), prepared off the lock.  A scene that fails to
        prepare (a shader that does not compile, a plug-in that raises in setup) is blacklisted for the session,
        leaves the queue and the next one is tried (up to PICK_TRIES); a failing transition falls back to the
        session's in transition.  Raises SessionError."""
        presets = self.mgr.presets
        src = spec = e = None
        for _ in range(self.PICK_TRIES):
            now = time.monotonic()
            self._upnext_fill(now, need=1)
            e = self.upnext.head("scene")
            if e is None:
                self.emit("session.warning", detail="auto: the scene pool is empty")
                raise SessionError(409, "auto_unavailable", "the auto pool has no scenes")
            if e["scene"] == self.current_name and e.get("origin") != "you":
                self.upnext.remove(e["id"])                 # it came up by hand meanwhile
                continue
            try:
                spec = self._fit(presets.resolve_scene(e.get("spec") or e["scene"]), False)
                src = await self._make_scene(spec)
                break
            except Exception as ex:
                self.director.quarantine_preset(e["scene"], now, session=True)
                self.upnext.remove(e["id"])
                log.warning("[%s] auto: preset %s failed to prepare (%s); skipped for this session", self.id,
                            e["scene"], getattr(ex, "detail", None) or f"{type(ex).__name__}: {ex}")
                self.emit("session.warning", detail=f"auto: preset {e['scene']} failed to prepare "
                                                    f"({type(ex).__name__}); skipped for this session")
        if src is None:
            raise SessionError(500, "prepare_failed", f"auto: {self.PICK_TRIES} picks failed to prepare")
        why_scene = e.get("why") or f"{e['scene']} (up next, yours)"
        if via is None:
            tname = e.get("transition")
            if tname is None or tname not in presets.transitions:
                t = self.director.pick_transition(presets.transitions)
                tname, why_trans = (t[0], t[1]) if t else (None, None)
            else:
                why_trans = f"{tname} (up next)"
            trans_spec = presets.resolve_transition(tname) if tname else self.t_in
            why_trans = why_trans or f"{self.t_in.name} (session in: empty transition pool)"
        else:
            trans_spec, why_trans = via, f"{via.name} (given)"
        try:
            trans = await self._transition_ready(trans_spec)
        except Exception as ex:
            if via is not None or trans_spec is self.t_in:
                self._retire(src)
                raise
            self.director.blacklist_transition(trans_spec.name)
            self.emit("session.warning", detail=f"auto: transition {trans_spec.name} failed to prepare "
                                                f"({type(ex).__name__}); skipped for this session")
            try:
                trans_spec, why_trans = self.t_in, f"{self.t_in.name} (fallback: {trans_spec.name} failed)"
                trans = await self._transition_ready(trans_spec)
            except Exception:
                self._retire(src)
                raise
            if e.get("transition") is not None:
                e["transition"] = self.t_in.name             # the queue says what will really play
                self.upnext.changed()
        return {"scene": e["scene"], "spec": spec, "src": src, "trans_spec": trans_spec, "trans": trans,
                "why": f"{why_scene} via {why_trans}", "qid": e["id"], "origin": e.get("origin")}

    # -- Up next (upnext.py): the director's forward plan, which the owner can edit ------------------------
    def _is_head(self, pk: dict) -> bool:
        e = self.upnext.head("scene")
        return e is not None and pk.get("qid") == e["id"] and pk["scene"] == e["scene"] and \
            (e.get("transition") is None or pk["trans_spec"].name == e["transition"])

    FILL_RETRY_S = 5.0

    def _replan(self) -> None:
        """The direction or the pool moved: the director picks its own entries again (the owner's stay)."""
        self.upnext.drop_auto("scene")
        self.upnext.drop_auto("effect")
        self._fill_retry = {"scene": 0.0, "effect": 0.0}

    def _upnext_fill(self, now: float, need: int = 0) -> None:
        """Top the queue up with the director's picks (``need``: at least that many scenes even with scene
        picking off, for Next).  Loop thread, no awaits."""
        q, d, fd, presets = self.upnext, self.director, self.fx_director, self.mgr.presets
        if d.auto is None:
            q.clear()
            return
        if d.enabled or need:
            prev_t = d.recent_transitions[-1] if d.recent_transitions else None
            for e in q.scenes:                            # the owner's entries: the director picks the way in
                if e.get("transition") is None and d.enabled:
                    t = d.pick_transition(presets.transitions, prev=prev_t)
                    if t is not None:
                        e["transition"], e["transition_origin"] = t[0], "auto"
                        q.changed()
                prev_t = e.get("transition") or prev_t
            want = SCENES_AHEAD if d.enabled else need
            while len(q.scenes) < want and (need or now >= self._fill_retry["scene"]):
                got = d.pick_scene(presets.scenes, self.current_name, now, tuple(e["scene"] for e in q.scenes))
                if got is None:
                    self._fill_retry["scene"] = now + self.FILL_RETRY_S
                    break
                t = d.pick_transition(presets.transitions, prev=prev_t)
                q.add("scene", {"scene": got[0], "transition": t[0] if t else None, "origin": "auto",
                                "transition_origin": "auto" if t else None, "why": got[1],
                                "why_words": self._why("scene", got[0])})
                prev_t = t[0] if t else prev_t
        else:
            q.drop_auto("scene")
        if fd.enabled:
            while len(q.effects) < EFFECTS_AHEAD and now >= self._fill_retry["effect"]:
                got = fd.ahead(now, self.fx.stack, presets.effects, lambda n: self.mgr.cost_ms("effect", n),
                               self.pixels_ratio(), tuple(e["effect"] for e in q.effects))
                if got is None:         # nothing else fits the bus now: look again later, not every tick
                    self._fill_retry["effect"] = now + self.FILL_RETRY_S
                    break
                q.add("effect", {"effect": got[0], "origin": "auto", "why": got[1],
                                 "why_words": self._why("effect", got[0])})
        else:
            q.drop_auto("effect")

    def _why(self, kind: str, name: str) -> str:
        """The plain-words why for one of the director's entries (upnext.why_words)."""
        presets = self.mgr.presets
        p = (presets.scenes if kind == "scene" else presets.effects).get(name) or {}
        plugin = p.get("source") if kind == "scene" else p.get("effect")
        return why_words(kind, p.get("tags") or {}, self.director.direction,
                         self.mgr.rating_weight("source" if kind == "scene" else "effect", plugin), p.get("stage"))

    def _upnext_edited(self) -> None:
        """After an edit: a prepared pick that is no longer the head is dropped (the new head is prepared)."""
        if self.prepared_pick is not None and not self._is_head(self.prepared_pick):
            self._drop_prepared()

    def upnext_view(self, now: Optional[float] = None) -> dict:
        """Up next for GET and session.upnext; a room's followers show their leader's."""
        ld = self.following()
        if ld is not None:
            return ld.upnext_view(now)
        now = time.monotonic() if now is None else now
        d, fd = self.director, self.fx_director
        nx = d.next_eligible(now)
        lo, hi, _ = d.window()
        scene_eta = (nx["eligible_in_s"], nx["max_in_s"]) \
            if d.armed and d.enabled and nx["eligible_in_s"] is not None else None
        fx_in = None if fd.next_at is None or not (fd.enabled and fd.armed) else max(0.0, fd.next_at - now)
        return {**self.upnext.view(scene_eta, (lo, hi), fx_in, fd.interval()), "planned_by": self.screen}

    def _upnext_emit(self) -> None:
        if self.upnext.rev == self._upnext_sent:
            return
        self._upnext_sent = self.upnext.rev
        v = self.upnext_view()
        self.emit("session.upnext", **v)
        for f in self.followers():
            f.emit("session.upnext", **v)

    def _upnext_entry(self, kind: str, body: dict, partial: bool = False) -> dict:
        """The owner's entry from a request body (validated: 400 on an unknown scene, transition or effect)."""
        presets, out = self.mgr.presets, {}
        if kind == "scene":
            if "scene" in body or not partial:
                sp = body.get("scene")
                if sp is None:
                    raise ParamError("bad_request", "scene is required")
                presets.resolve_scene(sp)                   # 400 when unknown
                name = sp if isinstance(sp, str) else (sp.get("preset") or sp.get("source"))
                out.update(scene=name, spec=None if isinstance(sp, str) else sp)
            if "transition" in body:
                t = body["transition"]
                if t is not None:
                    if not isinstance(t, str) or t not in presets.transitions:
                        raise ParamError("unknown_preset", f"unknown transition preset {t!r}")
                out.update(transition=t, transition_origin="you" if t else None)
        else:
            ef = body.get("effect")
            if not isinstance(ef, str) or ef not in presets.effects:
                raise ParamError("unknown_preset", f"unknown effect preset {ef!r}")
            out["effect"] = ef
        return out

    async def upnext_op(self, body: Any) -> dict:
        """POST /api/sessions/{sid}/upnext: the owner edits the plan.  {op: add|set|remove|move|keep|reroll|play,
        kind?: scene|effect, id?, scene?, transition?, effect?, at?, to?}.  In a room the leader's plan."""
        ld = self.following()
        if ld is not None:
            return await ld.upnext_op(body)
        if not isinstance(body, dict):
            raise ParamError("bad_request", "body must be a JSON object")
        if self.director.auto is None:
            raise SessionError(409, "auto_unavailable", "the session has no auto block")
        if self.state in ("ended", "failed"):
            raise SessionError(409, "bad_state", f"session is {self.state}")
        op, q, now = body.get("op"), self.upnext, time.monotonic()
        qid = body.get("id")
        kind = q.find(qid)[0] if qid is not None else body.get("kind", "scene")
        if kind not in UPNEXT_KINDS:
            raise ParamError("bad_request", f"kind must be one of {list(UPNEXT_KINDS)}" if qid is None
                             else f"nothing up next has id {qid!r}")
        if op in ("set", "remove", "move", "keep", "play") and qid is None:
            raise ParamError("bad_request", f"{op} needs the entry's id")
        at = body.get("at", body.get("to"))
        if at is not None and (isinstance(at, bool) or not isinstance(at, int)):
            raise ParamError("bad_request", "at / to must be a position (0 = next)")
        result: dict = {}
        if op == "add":
            e = q.add(kind, {**self._upnext_entry(kind, body), "origin": "you", "why": None,
                             "why_words": "You queued it"}, 0 if at is None else at)
            result["id"] = e["id"]
        elif op == "set":
            i = q.find(qid)[1]
            q.items(kind)[i].update(self._upnext_entry(kind, body, partial=True), origin="you",
                                    why_words="You chose it")
            q.changed()
        elif op == "remove":
            q.remove(qid)
        elif op == "move":
            q.move(qid, 0 if at is None else at)
        elif op == "keep":
            e = q.items(kind)[q.find(qid)[1]]
            e["origin"], e["why_words"] = "you", "You kept it"
            if e.get("transition_origin") == "auto":
                e["transition_origin"] = "you"
            q.changed()
        elif op == "reroll":
            if qid is None:
                q.drop_auto(kind)
            else:
                items = q.items(kind)
                i = q.find(qid)[1]
                old = items[i]
                if kind == "scene":
                    prior = tuple(x["scene"] for x in items[:i]) + (old["scene"],)
                    got = self.director.pick_scene(self.mgr.presets.scenes, self.current_name, now, prior)
                    t = self.director.pick_transition(self.mgr.presets.transitions,
                                                      prev=items[i - 1].get("transition") if i else None)
                    if got is not None:
                        items[i] = {"id": old["id"], "scene": got[0], "transition": t[0] if t else None,
                                    "origin": "auto", "transition_origin": "auto" if t else None, "why": got[1],
                                    "why_words": self._why("scene", got[0])}
                else:
                    got = self.fx_director.ahead(now, self.fx.stack, self.mgr.presets.effects,
                                                 lambda n: self.mgr.cost_ms("effect", n), self.pixels_ratio(),
                                                 tuple(x["effect"] for x in items))
                    if got is not None:
                        items[i] = {"id": old["id"], "effect": got[0], "origin": "auto", "why": got[1],
                                    "why_words": self._why("effect", got[0])}
                q.changed()
        elif op == "play":
            q.move(qid, 0)
            self._upnext_edited()
            if kind == "scene":
                result["cue"] = await self.cue({"to": "auto", "at": body.get("when") or "now"})
            else:
                result["fx"] = await self.fx_next()
        else:
            raise ParamError("bad_request", "op must be add, set, remove, move, keep, reroll or play")
        self._upnext_edited()
        self._upnext_fill(time.monotonic())
        self._upnext_emit()
        return {**result, "upnext": self.upnext_view()}

    async def cue(self, body: Any, *, cause: str = "manual", reason: Optional[str] = None,
                  detail: Optional[str] = None, photon_at: Optional[float] = None) -> dict:
        if not isinstance(body, dict):
            raise ParamError("bad_request", "body must be a JSON object")
        unknown = set(body) - {"to", "via", "at", "idempotency_key", "source"}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        key = body.get("idempotency_key")
        canon = None
        if key is not None:
            if not isinstance(key, str) or not 0 < len(key) <= 200:
                raise ParamError("bad_request", "idempotency_key must be a non-empty string")
            canon = json.dumps({k: v for k, v in body.items() if k != "idempotency_key"}, sort_keys=True,
                               default=_canon_bytes)
            if key in self.cue_keys:
                seen, resp = self.cue_keys[key]
                if seen != canon:
                    raise SessionError(409, "idempotency_conflict", "this idempotency_key was used for another cue")
                return resp
        at = body.get("at", "now")
        if at not in ("now", "beat", "bar"):
            raise ParamError("bad_at", "at must be \"now\", \"beat\" or \"bar\"")
        source = body.get("source")
        if source is not None and source not in CUE_SOURCES:
            raise ParamError("bad_request", f"source must be one of {list(CUE_SOURCES)}")
        to = body.get("to")
        if to is None or (isinstance(to, dict) and "still" in to):
            raise ParamError("bad_to", "to must be a scene spec or \"auto\" (exit is the route to a still)")
        if self.state in ("starting", "holding"):
            raise SessionError(409, "bad_state", f"session is {self.state}; use go")
        self._require(*LIVE_STATES)
        via = self.mgr.presets.resolve_transition(body["via"]) if body.get("via") is not None else None
        epoch = self._epoch
        if to == "auto":
            if self.director.auto is None:
                raise SessionError(409, "auto_unavailable", "the session has no auto block")
            ld = self.following()
            if ld is not None and cause == "manual":    # "next" on any screen in a room moves the room
                return await ld.cue(body, cause=cause, reason=reason, detail=detail)
            if cause == "manual":
                cause, reason = "auto", "next"
            pk = self._take_prepared()
            if pk is not None and via is not None:      # the warmed scene, with the given way in
                try:
                    trans = await self._transition_ready(via)
                    self._retire(pk["trans"])
                    pk = {**pk, "trans_spec": via, "trans": trans, "why": f"{pk['why'].split(' via ')[0]} via {via.name}"}
                except Exception:
                    pass                                 # its own transition stays (degrade, never refuse)
            if pk is None:
                pk = await self._pick_and_prepare(via)
                self._room_prepare(pk)
            if self.upnext.remove(pk.get("qid")) is not None:   # it plays: off the queue
                self._upnext_fill(time.monotonic())
            await self._room_ready()                     # the room's other screens cut with this one
            spec, trans_spec, src, trans = pk["spec"], pk["trans_spec"], pk["src"], pk["trans"]
            detail = f"{detail}; {pk['why']}" if detail else pk["why"]
        else:
            spec = self._fit(self.mgr.presets.resolve_scene(to), sets_render_scale(to))
            if via is not None:
                trans_spec = via
            elif self.director.enabled:
                t = self.director.pick_transition(self.mgr.presets.transitions)
                trans_spec = self.mgr.presets.resolve_transition(t[0]) if t else self.t_in
            else:
                trans_spec = self.t_in
            src = await self._make_scene(spec)          # outside the lock (R5)
            try:
                trans = await self._transition_ready(trans_spec)
            except Exception as e:
                if via is not None or trans_spec is self.t_in:
                    self._retire(src)
                    raise
                self.director.blacklist_transition(trans_spec.name)     # the director's pick: fall back
                self.emit("session.warning", detail=f"transition {trans_spec.name} failed to prepare "
                                                    f"({type(e).__name__}); skipped for this session")
                trans_spec = self.t_in
                try:
                    trans = await self._transition_ready(trans_spec)
                except Exception:
                    self._retire(src)
                    raise
        async with self.lock:
            if self._epoch != epoch or self.state not in LIVE_STATES:
                self._retire(src, trans)
                c = self._new_cue(cause, spec, trans_spec, None, trans, at, reason, detail, source)
                self._cue_event("cancelled", c, reason="exit" if self.exited else self.state)
                raise SessionError(409, "bad_state", f"session is {self.state}")
            cue = self._new_cue(cause, spec, trans_spec, src, trans, at, reason, detail, source, photon_at)
            outcome = self._dispatch(cue)
            if outcome != "skipped":
                self.scene_spec = spec
        resp = {"session": self.id, "state": self.state, "cue_id": cue["cue_id"], "at_pts": cue["at_pts"],
                "duration_s": cue["duration_s"]}
        if outcome == "skipped":
            resp["skipped"] = True
        if key is not None:
            self.cue_keys[key] = (canon, resp)
        return resp

    # -- direction, palette, auto, beat_follow (R11-R14) -----------------------------------
    def _parse_look(self, body: dict) -> dict:
        out: dict = {}
        if "direction" in body:
            out["direction"] = validate_direction(body["direction"], self.mgr.presets.vocab.words)
        if "auto" in body:
            a = body["auto"]
            out["auto"] = None if a is None else validate_auto(a, self.director.auto or AUTO_DEFAULTS)
            if isinstance(a, dict):
                out["auto_given"] = sorted(a)            # which parts the body set (scope: is it the effects?)
        if "palette" in body:
            out["palette"] = parse_palette(body["palette"])
        if "palette_transition_s" in body:
            out["palette_transition_s"] = parse_seconds(body["palette_transition_s"], "palette_transition_s", 0, 60)
        if "beat_follow" in body:
            if body["beat_follow"] not in BEAT_FOLLOW:
                raise ParamError("bad_request", f"beat_follow must be one of {list(BEAT_FOLLOW)}")
            out["beat_follow"] = body["beat_follow"]
        if "direction_ease_s" in body:
            out["direction_ease_s"] = parse_seconds(body["direction_ease_s"], "direction_ease_s", 0, 120)
        return out

    def _apply_look(self, look: dict, scope: str = "room") -> None:
        """A look is the room's (rooms.py): it applies to this session and to the room's other screens, and the
        parts it sets are the room's again everywhere.  ``scope`` "screen" (a screen card's control, owner 17 Oct):
        this session only, and those parts become its own (never mirrored from the room)."""
        parts = self._look_parts(look)
        if scope == "screen":
            self.own |= parts
            self._apply_look_here(look)
            if "direction" in parts or "fx" in parts:
                self._apply_direction("panel")
            return
        peers = self.room_peers() if look else []
        for x in (self, *peers):
            x.own -= parts
        self._apply_look_here(look)
        for p in peers:
            p._apply_look_here(copy.deepcopy(look))

    @staticmethod
    def _look_parts(look: dict) -> set:
        parts = set()
        if "direction" in look or "direction_ease_s" in look:
            parts.add("direction")
        if "palette" in look or "palette_transition_s" in look:
            parts.add("palette")
        if "beat_follow" in look:
            parts.add("beat_follow")
        if look.get("auto") is not None and "fx" in look.get("auto_given", ()):
            parts.add("fx")
        return parts

    def _apply_look_here(self, look: dict) -> None:
        if "direction_ease_s" in look:
            self.direction_ease_s = self.runtime.ease_s = look["direction_ease_s"]
        if "direction" in look:
            self.direction = look["direction"]
        fx_only = False
        if "auto" in look:
            old = self.director.auto
            strip = lambda a: {k: v for k, v in (a or {}).items() if k != "fx"}       # noqa: E731
            fx_only = "direction" not in look and old is not None and look["auto"] is not None \
                and strip(old) == strip(look["auto"])
            self.director.set_auto(look["auto"])
            self.music.configure(self.director.auto.get("music") if self.director.auto else None)
        if fx_only:                                      # effects only: the scene plan and the prepared pick stay
            self.fx_director.configure((self.director.auto or {}).get("fx"), self.direction_effective)
            self.upnext.drop_auto("effect")
            self._fill_retry["effect"] = 0.0
        elif look.keys() & {"direction", "auto"}:
            self._apply_direction("panel" if "direction" in look else "auto")
        if "palette_transition_s" in look:
            self.palette_s = self.runtime.palette_s = look["palette_transition_s"]
        if "palette" in look and look["palette"] != self.palette:
            if self.palette is None and look["palette"] is not None:
                log.info("[%s] session palette %s set: scene palette params are ignored from now on", self.id,
                         look["palette"])
            self.palette = self.runtime.palette = look["palette"]
        if "beat_follow" in look:
            self.beat_follow = self.sampler.follow = look["beat_follow"]

    # The one place a direction takes effect: the panel's (self.direction), trimmed by the music's proposal.
    # A prepared pick is dropped when the pick-relevant part changes (energy decile, moods, style, genre,
    # palette family): always for a panel / auto change, at most once per DIR_DROP_MIN_S for the music's own moves.
    DIR_DROP_MIN_S = 30.0

    def _apply_direction(self, cause: str) -> None:
        now = time.monotonic()
        ld = self.following()
        eff = ld.direction_effective if ld is not None and "direction" not in self.own else \
            effective_direction(self.direction, self.music.proposal, self.music.m)
        self.direction_effective = eff
        key = pick_key(eff)
        if key != self._dir_key and (cause in ("panel", "auto") or now - self._dir_drop_t >= self.DIR_DROP_MIN_S):
            self._dir_key, self._dir_gen, self._dir_drop_t = key, self._dir_gen + 1, now
            self._drop_prepared()                       # it was picked for the old direction
            self._replan()
        elif cause == "auto":
            self._drop_prepared()                       # a new pool / transitions / dwell
            self._replan()
        self.director.direction = eff
        self.runtime.speed_target = speed_for(eff["pace"])     # eased over direction_ease_s
        self.runtime.depth_target = eff["intensity"]
        fd = self.fx_director
        fd.configure((self.director.auto or {}).get("fx"), eff)
        if fd.next_at is not None:                      # a busier direction may bring the next move earlier, never later
            fd.next_at = min(fd.next_at, now + fd.interval())
        if cause != "music":                            # the music's numeric drift is visible through GET only
            self._emit_director(cause)
        if ld is None:
            self._room_direction(cause)

    def _drop_prepared(self) -> None:
        if self.prepared_pick is not None:
            pk, self.prepared_pick = self.prepared_pick, None
            self._retire(pk["src"], pk["trans"])

    def _emit_director(self, cause: Optional[str] = None) -> None:
        now = time.monotonic()
        nx = self.director.next_eligible(now)
        eff = self.direction_effective
        self.emit("session.director", armed=self.director.armed, enabled=self.director.enabled,
                  dwell_s=nx["dwell_s"], dwell_from=nx["dwell_from"],
                  eligible_at_pts=self._in_pts(nx["eligible_in_s"]), max_at_pts=self._in_pts(nx["max_in_s"]),
                  **({"cause": cause} if cause else {}),
                  direction_effective={k: eff.get(k) for k in ("energy", "change_rate", "transition_style", "moods")},
                  music={"band": self.music.band, "m": self.music.m})

    def _in_pts(self, in_s: Optional[float]) -> Optional[float]:
        return None if in_s is None else round(self._pts_now() + in_s, 3)

    # -- the director and inputs, ticked by the manager (loop thread, ~10 Hz) ----------------
    def tick(self, now: float) -> None:
        if self.state in ("starting", "ended", "failed"):   # nothing is ticked or emitted before session.ready
            return
        d = self.director
        snap = self.sampler.snapshot()
        fresh = self.sampler.freshness(now, snap) is None
        armed = self.state == "live" and not self.exited and d.enabled
        if d.arm(armed):
            if armed and d.dwell_start is None:
                d.scene_done(now)
            self._emit_director()
        d.observe(snap, fresh, follow_desk=self.mgr.music_feed.fresh(now) is None)
        feed = self.mgr.music_feed
        cause = self.music.update(now, snap, fresh, (d.auto or AUTO_DEFAULTS).get("follow_dmx"), armed,
                                  room=self.mgr.room_direction_now(now), feed=feed.fresh(now))
        # the mappings' music and piano signals (binding.py; piano.py reads the notes this session's view has seen)
        pn = self.piano.step(now, self.sound.view.tap() if self.sound is not None else None)
        hm = feed.harmony(now)
        if hm is not None:                  # no piano playing: the room's harmony (show_service, from the audio)
            for k in ("tension", "register", "spread"):
                if pn.get(k) is None:
                    pn[k] = round(hm[k], 4) if hm[k] is not None else None
            if hm["chord_change_t"] is not None and (pn.get("chord_change_t") is None
                                                     or hm["chord_change_t"] > pn["chord_change_t"]):
                pn["chord_change_t"] = hm["chord_change_t"]
        # the music signals' weight: show_service's liveness while its features arrive and while it fades out after
        # they stop (the fast energy fades with it, no jump to idle), handing over to the desk's (None) at the end
        lw = feed.liveness(now)
        mw = lw if self.music.feeding else max(lw, self.sampler.w) if lw > 0.0 else None
        self.runtime.extra = {**self.music.signals(now), "music_w": mw, **pn}
        ld = self.following()
        if ld is not None:          # the room's leader directs this screen: its music, picks and effects
            self.upnext.clear()     # the leader's plan is the room's
            self._following = True
            self._follow_tick(now, ld)
            self._inputs_tick(now)
            self._hud_sync(now)     # a follower's panel too (18 Oct: north's froze on its first text)
            return
        if self._following:         # promoted (the leader left): direct from here on
            self._following, self._room_synced = False, None
            cause = cause or "room"
        if cause is not None:
            self._apply_direction(cause)
        self._hud_sync(now)
        if self.state == "live" and not self.exited:
            self._build_tick(now)
            self._upnext_fill(now)
            self._upnext_emit()
        if armed and self._auto_task is None and not self.inflight and self.pending is None:
            if self.prepared_pick is None and self._pick_task is None and d.prepare_due(now):
                self._pick_task = asyncio.create_task(self._prepare_next())
            due = None if self.music.settling(now) or self._dark is not None or self.mgr.room_hold(now) \
                else d.due(now)   # a section change:
            # pick for the new direction (between songs, the next song's own cue brings the scene in)
            if due is not None:
                self._auto_task = asyncio.create_task(self._auto_cue(*due))
        self._fx_tick(now)
        self._inputs_tick(now)

    def _fx_tick(self, now: float) -> None:
        fd = self.fx_director
        armed = self.state == "live" and not self.exited and self.fx.open and fd.enabled
        fd.arm(armed, now)
        if not armed or not fd.due(now) or self._fx_task is not None or self.fx.lock.locked():
            return
        if self.mgr.room_hold(now):                      # Keep this look: the effects stay too
            fd.done(now, None)
            return
        if self.inflight or self.pending is not None:     # never during a scene transition
            return
        self._fx_task = asyncio.create_task(self._fx_auto(now))

    async def _fx_auto(self, now: float) -> None:
        fd = self.fx_director
        move = None
        try:
            presets = self.mgr.presets.effects
            move = fd.plan(now, self.fx.stack, presets, lambda n: self.mgr.cost_ms("effect", n),
                           self.pixels_ratio(), prefer=tuple(e["effect"] for e in self.upnext.effects))
            if move is not None:
                t = self._next_downbeat(time.monotonic())
                if t is not None:                   # effects come in on the bar (G9)
                    delay = t - self.sampler.lead - time.monotonic()
                    if 0.0 < delay < FX_BAR_MAX_S:
                        await asyncio.sleep(delay)
                res = await self.fx.auto_move(move, fd.fade_s())
                if res is not None:
                    fd.last = {"op": move.op, "preset": move.add, "removed": move.remove, "why": move.why,
                               "at_pts": round(self._pts_now(), 3)}
                    self._upnext_took(move.add)
                    if move.add and res.get("rejected"):      # failed to prepare: never again this session
                        fd.quarantine(move.add, now, session=True)
                    self._room_fx()
        except SessionError as e:
            log.warning("[%s] auto fx move failed: %s", self.id, e.detail)
        except ParamError as e:
            log.warning("[%s] auto fx move rejected: %s", self.id, e.detail)
            if move is not None and move.add:
                fd.quarantine(move.add, now)
        except Exception:
            log.exception("[%s] auto fx move failed", self.id)
            if move is not None and move.add:
                fd.quarantine(move.add, now)
        finally:
            fd.done(time.monotonic(), move)
            self._fx_task = None

    def room_event(self, kind: str, at_s: Optional[float], detail: str, data: Optional[dict] = None) -> None:
        """show_service's room.event (loop thread): a section change, a drop or a new track is a cue to change
        scene, on its own time when that is still ahead, else on the next bar (once min dwell is reached).  The
        song-end events with a ``transition`` are handled by _track_event (no min dwell: a song's end is the
        moment)."""
        if self.following() is not None or self.state != "live" or self.exited:
            return
        self.fx_director.on_event(kind, time.monotonic())     # the bus follows the song too (fx_director.py)
        data = data or {}
        if kind in TRACK_EVENTS and (data.get("transition") is not None or data.get("held")
                                     or kind != "track_change" or detail in ("disarmed", "sound_back")):
            self._track_event(kind, at_s, detail, data)
            return
        if kind not in CUT_EVENTS or self.mgr.room_hold(time.monotonic()):
            return
        if self.director.hint(f"{kind.replace('_', ' ')} (the room's music{': ' + detail if detail else ''})",
                              urgent=kind == "drop"):
            self._event_hint = at_s

    # -- song ends (c8's room.events; SHOW_ENGINE §8.5 "Song ends") ------------------------------------------------
    def _track_event(self, kind: str, at_s: Optional[float], detail: str, data: dict) -> None:
        d = self.director
        if data.get("held") or not (d.armed and d.enabled):
            return                                   # Keep this look (or a person's pending change): leave it be
        tr = data.get("transition") if isinstance(data.get("transition"), dict) else {}
        now = time.monotonic()
        if kind == "track_end_armed":
            if detail == "disarmed":
                self._track_arm = None
                return
            self._track_arm = {"id": data.get("id"), "at": at_s, "transition": tr}
            self._track_head(tr.get("change"))        # provisional (big): the director's next pick, ready in time
            if self._pick_task is None and self.prepared_pick is None:
                self._pick_task = asyncio.create_task(self._prepare_next())
            return
        if kind == "track_end":
            if detail == "sound_back":                # the music came back with no new song: back to the scene
                dk = self._dark
                if dk is not None:
                    self._end_dark()
                    self._spawn_track_cue(dk["prev"] or "auto", self._xfade(dk["in_s"]), None, "now",
                                          "the music came back")
                return
            if tr.get("style") == "fade-dark" and self._dark is None:
                out_s = self._secs(tr.get("out_s"), 2.5)
                cap = min(DARK_MAX_S, self._secs(tr.get("dark_max_s"), DARK_MAX_S))
                self._dark = {"since": now, "in_s": self._secs(tr.get("in_s"), 2.0), "prev": self.current_name,
                              "task": None}
                self._spawn_track_cue(DARK_SCENE, self._xfade(out_s), None, "now", "the song ended: fading out")
                self._dark["task"] = asyncio.create_task(self._dark_timeout(out_s + cap))
            return
        # track_change: the new song.  Its own transition, landing on its first beat; change big = a fresh pick,
        # small = a variant of what is on (the owner's queued scenes stay first either way)
        self._track_arm = None
        self._track_head(tr.get("change"))
        style = tr.get("style")
        if self._dark is not None:
            in_s = self._secs(tr.get("in_s"), self._dark["in_s"])
            self._end_dark()
            via = self._xfade(in_s)
        elif style == "cut":
            via = "cut"
        elif style == "crossfade" or style == "fade-dark":
            via = self._xfade(self._secs(tr.get("seconds"), 2.0))
        else:
            via = None
        on_beat = tr.get("on", "downbeat") == "downbeat"
        ahead = at_s is not None and at_s >= now + self.sampler.lead + EVENT_MARGIN_S
        self._spawn_track_cue("auto", via, at_s if ahead and on_beat else None, "bar" if on_beat else "now",
                              "a new song" + (f" ({tr.get('change')} change)" if tr.get("change") else ""))

    @staticmethod
    def _secs(v: Any, default: float) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(30.0, f)) if math.isfinite(f) else default

    @staticmethod
    def _xfade(seconds: float) -> Any:
        return {"preset": "crossfade-1s", "params": {"duration_s": round(max(0.2, seconds), 2)}}

    def _track_head(self, change: Optional[str]) -> None:
        """Up next's head for the new song: big = a fresh pick for it, small = another preset of the scene on screen
        (a variant).  The owner's entries stay first."""
        head = self.upnext.head("scene")
        if head is not None and (head.get("origin") == "you" or (change and head.get("for_song") == change)):
            return                                   # the owner's, or already picked (and warmed) for this song
        now = time.monotonic()
        name = None
        if change == "small":
            cur = self.mgr.presets.scenes.get(self.current_name or "") or {}
            src = cur.get("source")
            scenes, d = self.mgr.presets.scenes, self.director
            sib = sorted(n for n, p in scenes.items()
                         if src and p.get("source") == src and n != self.current_name and n != DARK_SCENE
                         and (p.get("tags") or {}).get("auto", True) is not False and self.room_ok("source", src))
            got = weighted_pick(sib, lambda n: scene_weight(scenes[n].get("tags") or {}, d.direction)
                                * self.mgr.rating_weight("source", src), d.rng_scene) if sib else None
            if got is not None:
                name = got[0]
                why, words = f"{name} (a variant for the next song)", "A variant: the next song is close to this one"
        elif change == "big":
            got = self.director.pick_scene(self.mgr.presets.scenes, self.current_name, now)
            if got is not None:
                name, why, words = got[0], f"{got[1]} for the next song", "For the next song"
        if name is None:
            return
        if head is not None:
            self.upnext.remove(head["id"])
        self.upnext.add("scene", {"scene": name, "transition": None, "origin": "auto", "transition_origin": None,
                                  "why": why, "why_words": words, "for_song": change, "hold": True}, at=0)
        self._upnext_edited()

    def _end_dark(self) -> None:
        dk, self._dark = self._dark, None
        if dk is not None and dk.get("task") is not None:
            dk["task"].cancel()

    async def _dark_timeout(self, after_s: float) -> None:
        """Never dark longer than dark_max_s: then the next scene comes in anyway."""
        try:
            await asyncio.sleep(after_s)
        except asyncio.CancelledError:
            return
        dk = self._dark
        if dk is None or self.state != "live":
            return
        self._dark = None
        await self._track_cue("auto", self._xfade(dk["in_s"]), None, "now", "dark long enough")

    def _spawn_track_cue(self, to: Any, via: Any, photon_at: Optional[float], at: str, why: str) -> None:
        asyncio.create_task(self._track_cue(to, via, photon_at, at, why))

    async def _track_cue(self, to: Any, via: Any, photon_at: Optional[float], at: str, why: str) -> None:
        try:
            if self._auto_task is not None:            # the director is mid-cut: let it land first
                await asyncio.shield(self._auto_task)
            body = {"to": to, "at": at, **({"via": via} if via is not None else {})}
            await self.cue(body, cause="auto", reason="track", detail=why, photon_at=photon_at)
        except SessionError as e:
            log.warning("[%s] song-end cue failed: %s", self.id, e.detail)
        except Exception:
            log.exception("[%s] song-end cue failed", self.id)

    # -- the details panel (hud.py; docs/LIVE_DIAGNOSTICS.md) -------------------------------------------------------
    def _hud_sync(self, now: float) -> None:
        """Loop thread (the tick, ~10 Hz): the panel on / off, and its text at 2 Hz (1 Hz when a build is slow)."""
        h = getattr(self.engine, "hud", None)
        if h is None:
            return
        if h.lamp is None:
            h.lamp = lambda: (self.runtime.signals or {}).get("beat", 0.0)
        h.on = bool(self.hud_wanted) and self.state not in ("ended", "failed")
        if not (h.on or h.fade > 0.0) or now - self._hud_t < self._hud_period:
            return
        self._hud_t = now
        lines, warn = self.hud_lines(now)
        from .hud import encode
        h.set(*encode(lines, warn))

    def hud_lines(self, now: Optional[float] = None, max_age: float = 0.0) -> tuple[list, list]:
        """The panel's lines (built now, or the last ones when younger than ``max_age``)."""
        now = time.monotonic() if now is None else now
        t, lines, warn = self._hud_lines
        if max_age > 0.0 and now - t < max_age:
            return lines, warn
        from .hud import BUILD_SLOW_MS, build_lines
        h = getattr(self.engine, "hud", None)
        t0 = time.perf_counter()
        try:
            lines, warn = build_lines(self, now, getattr(h, "ema_ms", None))
        except Exception as e:                       # the panel never takes anything down
            log.warning("[%s] details lines failed: %s", self.id, e)
            lines, warn = [f"{self.screen}: details unavailable ({type(e).__name__})"], [0]
        ms = (time.perf_counter() - t0) * 1000.0
        self._hud_period = 1.0 if ms > BUILD_SLOW_MS else 0.5
        self._hud_lines = (now, lines, warn)
        return lines, warn

    def hud_view(self) -> dict:
        """GET /api/sessions/{sid}/hud: the same lines, panel on or off (built at most once a second when off)."""
        h = getattr(self.engine, "hud", None)
        on = bool(h is not None and h.on)
        lines, warn = self.hud_lines(max_age=0.5 if on else 1.0)
        return {"on": on, "failed": getattr(h, "failed", None), "at": _iso(time.time()), "lines": lines,
                "warn": warn}

    def hud_summary(self) -> dict:
        h = getattr(self.engine, "hud", None)
        return {"on": bool(h is not None and h.on), "failed": getattr(h, "failed", None),
                "rate_hz": round(1.0 / self._hud_period, 1) if h is not None and h.on else 0.0}

    BUILD_ON, BUILD_OFF = 0.5, 0.15

    def _build_tick(self, now: float) -> None:
        """A build in the music (music.context build_progress >= BUILD_ON): the head of Up next becomes a peak pick
        (the director's own pick for high energy), prepared now so the drop can cut to it at once (d4's G10)."""
        d = self.director
        b = self.mgr.music_feed.building(now)
        if self._building and b < self.BUILD_OFF:
            self._building = False
            return
        if self._building or b < self.BUILD_ON or not (d.armed and d.enabled):
            return
        self._building = True
        head = self.upnext.head("scene")
        if head is not None and head.get("origin") == "you":
            return                                   # the owner's next scene stays next
        # the peak in this mode's own words (the music's where the mode sets none), not fixed EDM ones (2b's audit)
        mood = list(d.direction.get("moods") or d.direction.get("music_moods") or BANDS["peak"][0])
        style = "wild" if (d.direction.get("transition_style") or "wild") != "gentle" else "gentle"
        peak = {**d.direction, "energy": max(0.85, float(d.direction.get("energy", 0.5))), "moods": mood,
                "transition_style": style}
        keep, d.direction = d.direction, peak
        try:
            got = d.pick_scene(self.mgr.presets.scenes, self.current_name, now)
            t = d.pick_transition(self.mgr.presets.transitions)
        finally:
            d.direction = keep
        if got is None:
            return
        self.upnext.add("scene", {"scene": got[0], "transition": t[0] if t else None, "origin": "auto",
                                  "transition_origin": "auto" if t else None, "why": f"{got[1]} for the drop",
                                  "why_words": "For the drop: the music is building", "hold": True}, at=0)
        self._upnext_edited()
        if self._pick_task is None and self.prepared_pick is None and self.state == "live":
            self._pick_task = asyncio.create_task(self._prepare_next())

    def _upnext_took(self, effect: Optional[str]) -> None:
        """An effect move added ``effect``: its queue entry (the first with that preset) is done."""
        for e in self.upnext.effects:
            if e["effect"] == effect:
                self.upnext.remove(e["id"])
                break

    async def fx_next(self, body: Optional[dict] = None) -> dict:
        """POST /api/sessions/{sid}/fx/next: change the effect now (the owner's Next for effects).  In a room the
        leader moves and the followers mirror it, like Next for scenes.  Body, all optional (owner 17 Oct, the Room
        page's effect controls): ``effect`` a preset to bring in (else the director's pick), ``replace`` the slot id
        to swap out ("none": add; else the oldest auto one), ``only`` true: every other auto effect goes too."""
        body = dict(body) if isinstance(body, dict) else {}
        scope = body.pop("scope", "room")
        if scope not in LOOK_SCOPES:
            raise ParamError("bad_scope", f"scope must be one of {list(LOOK_SCOPES)}")
        if scope == "screen":                        # a screen card's Next: this screen's effects are its own now
            self.own.add("fx")
        else:
            head = self.fx_source()
            if head is not None:
                return await head.fx_next(body)
        unknown = set(body) - {"effect", "replace", "only"}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        eff, rep, only = body.get("effect"), body.get("replace"), bool(body.get("only"))
        if eff is not None and (not isinstance(eff, str) or eff not in self.mgr.presets.effects):
            raise ParamError("unknown_preset", f"unknown effect preset {eff!r}")
        ids = {st.id for st in self.fx.stack}
        if rep is not None and rep != "none" and rep not in ids:
            raise ParamError("bad_request", f"replace: no slot {rep!r} on the bus (have {sorted(ids)})")
        self._require("live")
        if self._fx_task is not None:                    # the director is mid-move: let it land first
            await asyncio.shield(self._fx_task)
        fd, now = self.fx_director, time.monotonic()
        autos = [st for st in self.fx.stack if getattr(st, "origin", "manual") == "auto"]
        if eff is None:
            move = fd.next_move(now, self.fx.stack, self.mgr.presets.effects, lambda n: self.mgr.cost_ms("effect", n),
                                self.pixels_ratio(), prefer=tuple(e["effect"] for e in self.upnext.effects))
            if move is None:
                raise SessionError(409, "no_effect", "no effect in the pool fits here right now")
            if rep is not None:
                move = FxMove("add" if rep == "none" else "swap", move.add, None if rep == "none" else rep, move.why,
                              level=move.level)
        else:
            old = None if rep == "none" else rep if rep is not None else (autos[0].id if autos else None)
            move = FxMove("swap" if old else "add", eff, old, f"chosen: {eff}")
        res = await self.fx.auto_move(move, fd.fade_s())
        if res is None:
            raise SessionError(409, "bad_state", f"session is {self.state}")
        if only:                                         # the one just added stays; every other auto effect goes
            keep_id = next((st.id for st in reversed(self.fx.stack)
                            if getattr(st, "origin", "manual") == "auto" and st.spec.preset == move.add), None)
            await self.fx.remove(lambda st: getattr(st, "origin", "manual") != "auto" or st.id == keep_id,
                                 fd.fade_s(), "only this effect")
        fd.last = {"op": move.op, "preset": move.add, "removed": move.remove, "why": move.why,
                   "at_pts": round(self._pts_now(), 3)}
        fd.done(time.monotonic(), move)                  # a full interval before the director's own next move
        self._upnext_took(move.add)
        self._room_fx()
        return {"op": move.op, "preset": move.add, "removed": move.remove, "why": move.why, "only": only}

    async def fx_clear(self, body: Optional[dict] = None) -> dict:
        """POST /api/sessions/{sid}/fx/clear: take effects off every screen in the room (owner 17 Oct): all of them,
        or ``effect`` (a preset name or a slot id).  The director leaves a cleared preset alone for 10 min and
        waits a full interval before its next move, so nothing comes straight back (the followers' mirror is
        cleared with it)."""
        body = dict(body) if isinstance(body, dict) else {}
        scope = body.pop("scope", "room")
        if scope not in LOOK_SCOPES:
            raise ParamError("bad_scope", f"scope must be one of {list(LOOK_SCOPES)}")
        if scope == "screen":
            self.own.add("fx")
        else:
            head = self.fx_source()
            if head is not None:
                return await head.fx_clear(body)
        unknown = set(body) - {"effect"}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        eff = body.get("effect")
        self._require("live")
        if self._fx_task is not None:
            await asyncio.shield(self._fx_task)
        fd, now = self.fx_director, time.monotonic()
        hit = lambda st: eff is None or st.id == eff or st.spec.preset == eff or st.spec.effect == eff   # noqa: E731
        gone = sorted({st.spec.preset for st in self.fx.stack if hit(st) and st.spec.preset})
        mirrors = [] if "fx" in self.own else [f for f in self.room_peers() if f.fx_source() is self]
        for s in [self, *mirrors]:
            await s.fx.remove(lambda st: not hit(st), fd.fade_s(), "cleared")
        for p in gone:
            fd.quarantine(p, now)                        # out of its own picks for QUARANTINE_S (10 min)
        if eff is None:
            self.upnext.drop_auto("effect")
        fd.done(now, None)
        return {"cleared": gone, "stack": self.fx.summary().get("stack")}

    async def _prepare_next(self) -> None:
        epoch, gen = self._epoch, self._dir_gen
        try:
            pk = await self._pick_and_prepare()
            if (self._epoch != epoch or self._dir_gen != gen or self.state != "live"
                    or self.prepared_pick is not None or not self._is_head(pk)):
                self._retire(pk["src"], pk["trans"])
            else:
                self.prepared_pick = pk
                self._room_prepare(pk)
        except SessionError:
            pass
        except Exception:
            log.exception("[%s] auto: preparing the next pick failed", self.id)
        finally:
            self._pick_task = None

    async def _auto_cue(self, reason: str, detail: str) -> None:
        try:
            if self._pick_task is not None:             # a pre-prepare is in flight: use it
                await asyncio.shield(self._pick_task)
            at, photon_at = (self.director.auto or AUTO_DEFAULTS)["snap"], None
            if reason == "room_event":              # show_service's section / drop / track: on its time, or the bar
                at, hint, self._event_hint = "bar", self._event_hint, None
                if hint is not None and hint >= time.monotonic() + self.sampler.lead + EVENT_MARGIN_S:
                    photon_at = hint
            await self.cue({"to": "auto", "at": at}, cause="auto", reason=reason, detail=detail, photon_at=photon_at)
        except SessionError as e:
            if self.state == "live":
                log.warning("[%s] auto cue failed: %s", self.id, e.detail)
                self.director.scene_done(time.monotonic())   # back off a full dwell
        except Exception:
            log.exception("[%s] auto cue failed", self.id)
            self.director.scene_done(time.monotonic())
        finally:
            self._auto_task = None

    def _initial_lead(self) -> float:
        p = self.profile
        frame = 1000.0 / p.fps
        self.lead_parts = {"server_ms": round(3 * frame, 1), "buffered_ms": float(p.target_buffer_ms),
                           "frame_ms": round(frame, 1), "photon_ms": self.photon_ms, "maestro_ms": 0.0,
                           "trim_ms": 0.0, "photon_source": self.photon_source, "measured": False}
        self.lead_ms = sum(v for k, v in self.lead_parts.items() if k.endswith("_ms"))
        return self.lead_ms

    def _update_lead(self, now: float) -> None:
        """R17 (approximate): server + the glass client's buffer (EMA) + 1 frame + photon + maestro + trim,
        recomputed at 1 Hz and slewed <= 20 ms/s except after an edge jump."""
        dt_s = min(5.0, now - self._lead_t) if self._lead_t else 1.0
        self._lead_t = now
        ring = getattr(self.engine, "server_ms", None)
        server = ring.summary()["p50"] if ring is not None else None
        stats = [c.stats for c in self.media.clients.values() if c.stats]
        bufs = [s["buffered_ms"] for s in stats if isinstance(s.get("buffered_ms"), (int, float))]
        jumps = sum(int(s.get("edge_jumps") or 0) for s in stats)
        jumped = jumps > self._edge_jumps
        self._edge_jumps = jumps
        if bufs:
            b = max(bufs)
            self._buffered_ema = b if self._buffered_ema is None or jumped else \
                self._buffered_ema + 0.3 * (b - self._buffered_ema)
        parts = self.lead_parts
        if server is not None:
            parts["server_ms"] = round(server, 1)
        if self._buffered_ema is not None:
            parts["buffered_ms"] = round(self._buffered_ema, 1)
        parts["measured"] = server is not None and self._buffered_ema is not None
        target = sum(v for k, v in parts.items() if k.endswith("_ms"))
        if jumped or self.lead_ms is None:
            self.lead_ms = target
        else:
            step = 20.0 * dt_s
            self.lead_ms += max(-step, min(step, target - self.lead_ms))
        self.sampler.lead = self.lead_ms / 1000.0

    def _inputs_tick(self, now: float) -> None:
        if now - self._lead_t >= 1.0:
            self._update_lead(now)
        online = self.sampler.freshness(now, self.sampler.snapshot()) is None
        locked = self._beat_grid(now) is not None
        state = (online, locked)
        if state != self._inputs_state and now - self._inputs_t >= INPUTS_EVENT_MIN_S:
            self._inputs_state, self._inputs_t = state, now
            self.emit("session.inputs", online=online, beat_locked=locked, stale=self.sampler.stale)
        if self.palette != self._palette_sent and now - self._palette_t >= 1.0:
            self._palette_sent, self._palette_t = self.palette, now
            self.emit("session.palette", palette=self.palette, cause="set",
                      swatch=palettes.swatch(self.palette) if self.palette else None)

    def inputs(self) -> dict:
        """GET /api/sessions/{id}/inputs (LIVE_V1 1.1 §1.9 monitor)."""
        now = time.monotonic()
        snap = self.sampler.snapshot()
        sig = self.sampler.peek(now)
        feed = self.mgr.music_feed                 # the room's sound and clock, as the frames see them
        fsnap, fw, clk = feed.snapshot(), feed.liveness(now), feed.clock(now)
        csig = clock_signals(clk, now + self.sampler.lead) if clk else {}
        beat_src = feed_blend(sig, fsnap, fw, csig, feed.levels(now), feed.onset_pulse(now))
        tb = feed.tempo_bpm(now)
        r3 = lambda v: None if v is None else round(v, 4)  # noqa: E731
        out: dict = {"online": self.sampler.freshness(now, snap) is None, "stale": self.sampler.stale,
                     "liveness": round(self.sampler.w, 3), "fixture": self.fixture,
                     "age_ms": None if snap is None else round((now - snap.arrival) * 1000, 1),
                     "bus": self.mgr.inputs.status() if self.mgr.inputs is not None else {"configured": False}}
        if snap is not None:
            raw = dict(snap.raw)
            raw["rgb"] = [round(c, 4) for c in raw["rgb"]]
            out["values"] = raw
            slow = snap.slow
            out["derived"] = {"activity_slow": r3(slow["activity_slow"]), "level_slow": r3(slow["level_slow"]),
                              "rgb_slow": [round(c, 4) for c in slow["rgb_slow"]]}
            out["follower"] = {**snap.follower,
                               "period_ms": None if snap.period is None else round(snap.period * 1000, 1),
                               "last_edge_age_ms": None if snap.last_edge is None
                               else round((now - snap.last_edge) * 1000, 1)}
        else:
            out["values"], out["derived"], out["follower"] = None, {}, {}
        out["derived"].update(bpm=r3(sig["bpm"]), beat_confidence=r3(sig["beat_confidence"]),
                              beat_phase=r3(sig["beat_phase"]), beat=r3(sig["beat"]),
                              beat_locked=sig["beat_locked"], beat_follow=self.beat_follow,
                              beat_source=beat_src or ("desk" if self.sampler.w > 0.0 else None),
                              bpm_source="desk" if sig["bpm"] is not None else None)
        out["feed"] = None if fsnap is None else {
            "live": round(fw, 3), "age_ms": round((now - fsnap.t) * 1000, 1), "energy": r3(fsnap.energy),
            "energy_fast": r3(feed.energy_now(now)), "activity": r3(fsnap.activity),
            "activity_slow": r3(fsnap.activity_slow), "drive": r3(fsnap.drive), "punch": r3(fsnap.punch),
            "onset": r3(fsnap.onset_last), "onset_mean": r3(fsnap.onset_mean), "onset_n": fsnap.onset_n,
            "onset_age_ms": None if fsnap.onset_t is None else round((now - fsnap.onset_t) * 1000, 1),
            "section": fsnap.section_id, "tempo_bpm": r3(tb),
            "clock": None if clk is None else {k: clk.get(k) for k in ("bpm", "conf", "gate", "state", "epoch")}}
        if csig:                                   # the clock's beat, not the desk follower's
            out["derived"].update(beat_phase=r3(csig.get("beat_phase")), beat=r3(csig.get("beat", sig.get("beat"))),
                                  beat_confidence=r3(csig.get("beat_confidence")))
            if clk.get("gate") == "beat":
                out["derived"].update(bpm=r3(float(clk["bpm"])), beat_locked=True)
        sure = (clk or {}).get("gate") == "beat"
        if tb is not None and (sure or not sig["beat_locked"]):   # the mappings' tempo (a locked desk's stays shown)
            out["derived"].update(bpm=r3(tb), bpm_source="clock" if sure else "clock_tentative")
        mg = self.mgr.music_grid(now)
        out["cuts_on"] = None if self._beat_grid(now) is None else "show_service clock" if mg is not None \
            else "lighting desk pulse"
        out["clock"] = None if mg is None else {"bpm": round(60.0 / mg[1], 2)}
        ex = self.runtime.extra or {}
        mw = ex.get("music_w")
        music_signals(sig, ex, self.sampler.w if mw is None else mw, None if mw is None else feed.energy_now(now), fw)
        sig.update({k: ex.get(k) for k in PIANO_LEVELS}, **piano_envelopes(ex, now))
        out["signals"] = {k: r3(sig.get(k)) for k in BIND_SIGNALS}
        out["section_follow"] = round(self.runtime.follow, 3)      # how far section mappings follow live now
        out["music_response"] = feed.response.describe()          # config/live/music_response.json, as loaded
        out["avail"] = {k: round(v, 3) for k, v in self.runtime.avail.items()}
        out["epoch"] = list(self.runtime.epoch)
        out["colour"] = {"maestro": sig.get("colour"), "amount": round(self.runtime.colour_avail, 3),
                         "scene": round(getattr(getattr(self.current_scene, "modulation", None), "colour_amount",
                                                lambda: 0.0)(), 3)}
        mod = getattr(self.current_scene, "modulation", None)
        out["bindings"] = mod.describe() if mod is not None else []
        out["fx_bindings"] = self.fx.bindings()
        out["masters"] = self.runtime.masters()
        self.runtime.inputs_view(out, now)        # the beat ladder's rung (derived.beat_source) and the pivots
        out["lead"] = {"total_ms": None if self.lead_ms is None else round(self.lead_ms, 1), **self.lead_parts}
        ds = self.following() or self                  # who directs this screen (the room's leader, else itself)
        nx = ds.director.next_eligible(now)
        out["auto"] = {**nx, "armed": self.director.armed, "enabled": self.director.enabled,
                       "pending_change": ds.director.latched, "last_decision": ds.director.last}
        out["music"] = ds._music_status(now)
        out["room"] = self.room_status()
        return out

    def _music_status(self, now: float) -> dict:
        st = self.music.status(now)
        lc = st.get("last_change")
        if lc:
            st["last_change"] = {"cause": lc["cause"], "at_pts": self._in_pts(lc["at"] - now)}
        return st

    # -- engine callbacks (loop thread) ----------------------------------------------
    def on_encoder_config(self, cfg, config_msg: dict, init_msg: bytes) -> None:
        if self.state == "ended":
            return
        self.media.set_config(config_msg, init_msg)
        for note in cfg.notes:
            log.info("[%s] %s", self.id, note)

    def on_media(self, frame_id: int, key: bool, msg: bytes) -> None:
        if self.state == "ended":
            return
        if key and not self.ready:
            self.ready = True
            if self.state == "starting":
                self.set_state("holding")
            self.emit("session.ready")
        self.media.on_frame(frame_id, key, msg)

    def on_transition(self, kind: str, phase: str, pts: float, tid: int, superseded: bool) -> None:
        if self.state in ("ended", "failed"):
            return
        extra = {"superseded": True} if superseded else {}
        self.emit("session.transition", kind=kind, phase=phase, pts=round(pts, 6), **extra,
                  **self.cue_info.get(tid, {}))
        if phase != "done":
            return
        self.cue_info.pop(tid, None)
        if self.inflight.pop(tid, None) is not None:
            self.director.scene_done(time.monotonic())        # dwell runs from the previous done (R11)
            if self.director.armed:
                self._emit_director()
            self._release_pending()
        if kind == "in" and tid == self.in_tid and self.state == "entering":
            self.set_state("live")
        elif kind == "out" and tid == self.exit_tid and self.state == "exiting" and not superseded:
            self.hold_pts = round(pts, 6)
            self.set_state("held")
            self.emit("session.held", hold_pts=self.hold_pts)

    def on_fx(self, info: dict) -> None:
        if self.state in ("ended", "failed"):
            return
        if "bus" in info:
            self.fx.on_bus(info["bus"] == "open")
        if info.get("retired"):
            self.fx.on_retired(info["retired"])

    def on_plugin_error(self, info: dict) -> None:
        """A plug-in failed in render and was substituted (C.2)."""
        if self.state in ("ended", "failed"):
            return
        kind, name = info.get("kind"), info.get("name")
        count = self.mgr.note_plugin_error(kind, name, info.get("detail"), self.id)
        self.plugin_error_count += 1
        slow = info.get("reason") == "slow"
        extra: dict = {}
        if slow:                                   # the GPU guard stopped it (a hang): recorded for the whole process
            self.slow_trips += 1
            self.mgr.note_slow(info, self.id)
            extra = {k: info.get(k) for k in ("reason", "gpu_ms", "budget_ms", "why")}
        slot = info.get("slot")
        if kind == "effect" and info.get("where") == "bus" and slot:
            st = self.fx.by_engine_id(slot)
            if st is not None and st.origin == "auto" and st.spec.preset:
                self.fx_director.quarantine(st.spec.preset, time.monotonic(), session=True)
            slot = self.fx.on_slot_failed(slot, info.get("detail") or "") or (st.id if st else slot)
        self.emit("session.plugin_error", kind=kind, name=name, preset=info.get("preset"),
                  **({"slot": slot} if slot else {}), phase=info.get("phase", "render"), action=info.get("action"),
                  detail=info.get("detail"), count=count, **extra)
        if kind == "source" and info.get("obj_id") == id(self.current_scene) and self.state in LIVE_STATES \
                and not self.exited and not self._recovering:
            asyncio.get_running_loop().create_task(self._recover(info))

    def _safe_scene(self, failed: str, source: Optional[str]) -> Optional[dict]:
        """Where a scene over its GPU budget recovers to: the previous scene, else a core scene (the entry scene,
        else the director's pick among core presets, config/live first).  None: nothing safe."""
        mgr = self.mgr

        def usable(spec: Optional[SceneSpec]) -> bool:
            return spec is not None and spec.name != failed and spec.source != source

        slow_fx: set = set()
        if usable(self.prev_spec):
            return spec_body(self.prev_spec, slow_fx)
        if usable(self.entry_spec) and origin_of(self.entry_spec.source) == "core":
            return spec_body(self.entry_spec, slow_fx)
        scenes = mgr.presets.scenes
        core = {n: p for n, p in scenes.items() if n != failed and p.get("source") != source
                and origin_of(str(p.get("source"))) == "core" and self.auto_ok("source", p.get("source"))}
        curated = {n: p for n, p in core.items() if n in mgr.presets._file.get("scene", {})} or core
        got = self.director.pick_scene(curated, self.current_name, time.monotonic()) if curated else None
        if got is not None:
            return {"preset": got[0]}
        return {"preset": sorted(curated)[0]} if curated else None

    async def _recover(self, info: dict) -> None:
        """The scene on screen failed: cue the director's pick (else the entry scene) over a 1 s crossfade.  A scene
        over its GPU budget goes back to the previous scene, else a core scene."""
        self._recovering = True
        failed = self.current_name
        try:
            self.director.quarantine_preset(failed, time.monotonic())
            via = {"transition": "crossfade", "params": {"duration_s": 1.0}}
            if info.get("reason") == "slow":
                to = self._safe_scene(failed, info.get("name"))
                if to is None:
                    self.emit("session.warning", detail=f"{failed} is over its GPU budget and no safe scene is "
                                                        f"known: showing black until the next cue")
                    return
                await self.cue({"to": to, "via": via, "at": "now"}, cause="auto", reason="plugin_error",
                               detail=f"{failed} over its GPU budget: {info.get('detail')}")
                return
            if self.director.auto is not None:
                to: Any = "auto"
            else:
                e = self.entry_spec
                if e.name == failed:
                    self.emit("session.warning", detail=f"{failed} failed in render and is the entry scene: "
                                                        f"showing black until the next cue")
                    return
                to = {"preset": e.preset} if e.preset else {"source": e.source, "params": e.params}
            await self.cue({"to": to, "via": via, "at": "now"}, cause="auto", reason="plugin_error",
                           detail=f"{failed} failed: {info.get('detail')}")
        except Exception as e:
            log.warning("[%s] recovery cue after a plug-in error failed: %s", self.id, e)
        finally:
            self._recovering = False

    def on_failed(self, detail: str) -> None:
        if self.state in ("ended", "failed"):
            return
        log.error("[%s] failed: %s", self.id, detail)
        self.error = detail
        self.emit("session.error", detail=detail)
        self.set_state("failed")
        self.media.end()
        asyncio.get_running_loop().run_in_executor(None, self.engine.stop)

    # -- reporting ---------------------------------------------------------------
    def summary(self) -> dict:
        e = self.engine.summary()
        ds = self.following() or self                  # who directs this screen (the room's leader, else itself)
        nx = ds.director.next_eligible(time.monotonic())
        return {
            "session": self.id, "screen": self.screen, "state": self.state, "profile": self.profile.name,
            "created_at": _iso(self.created_at), "scene": self.scene_spec.name,
            "clients": len(self.media.clients), "frames": e["frames"], "late_frames": e["late_frames"],
            "render_ms": e["render_ms"], "encode_queue": e["encode_queue"],
            # additive fields
            "gpu": self.gpu, "codec": self.profile.codec, "w": self.profile.w, "h": self.profile.h,
            "fps": self.profile.fps, "color_trc": self.profile.color_trc,
            "scene_params": self.scene_spec.params,
            "transitions": {"in": self.t_in.name, "out": self.t_out.name},
            "hold_pts": self.hold_pts, "error": self.error, "end_reason": self.end_reason,
            "encoder": e.get("encoder"), "encoder_fallback": e.get("encoder_fallback"),     # nvenc | ffmpeg (change 2)
            "expires_in_s": None if self.state == "ended" else round(self.expires_at - time.monotonic(), 1),
            "stats": {**{k: e[k] for k in ("rendered", "repeats", "resyncs", "frames_encoded", "write_ms", "slip_ms",
                                           "host_ms", "encode_ms", "bitrate_mbps")},
                      **{k: e.get(k) for k in ("cpu_ms", "runq_ms", "blocked_ms", "content_fps", "content_frames",
                                               "held_frames", "probe_frames")},     # W0.2, W1.7
                      **{k: e.get(k) for k in ("encode_cpu_ms", "slot_to_packet_ms", "forced_idrs")}},   # change 2
            "client_stats": self.media.summary(),
            # LIVE_V1 1.1 (R22)
            "current_scene": self.current_name, "scene_bindings": self.scene_spec.bindings,
            "cue": {"pending": None if self.pending is None else {
                        k: self.pending.get(k) for k in ("cue_id", "cause", "scene", "transition", "at_pts")},
                    "last": self.last_cue, "in_flight": len(self.inflight)},
            "auto": None if self.director.auto is None else {
                **self.director.auto, "armed": self.director.armed, "seed": self.director.seed,
                "fx": {**(self.director.auto.get("fx") or AUTO_FX_DEFAULTS), "armed": ds.fx_director.armed},
                "eligible_at_pts": self._in_pts(nx["eligible_in_s"]),
                "dwell_s_effective": nx["dwell_s"], "dwell_from": nx["dwell_from"]},
            "upnext": self.upnext_view(), "own": sorted(self.own), "hud": self.hud_summary(),
            "direction": self.direction, "palette": self.palette, "palette_transition_s": self.palette_s,
            "direction_effective": self.direction_effective, "music": ds._music_status(time.monotonic()),
            "music_fields": music_fields(ds.direction, ds.music.proposal, ds.music.m),
            "room": self.room_status(),
            "masters": self.runtime.masters(), "fixture": self.fixture, "beat_follow": self.beat_follow,
            "lead_ms": None if self.lead_ms is None else round(self.lead_ms, 1),
            # effects layer (EFFECTS_LAYER_DESIGN D.2)
            "fx": self.fx.summary(),
            "current_scene_effects": [{"effect": e.effect, "preset": e.preset,
                                       "stage": getattr(self.mgr.registry.effects.get(e.effect), "stage", None)}
                                      for e in (getattr(self.current_scene, "fx_specs", None) or ())],
            "plugin_errors": self.plugin_error_count,
            # GPU frame-time guard
            "slow_trips": self.slow_trips, "gpu_guard": e.get("gpu_guard"),
        }

    def snapshot_events(self) -> list[dict]:
        base = {"session": self.id, "screen": self.screen}
        out = [{"type": "session.state", **base, "state": self.state}]
        if self.state == "held" and self.hold_pts is not None:
            out.append({"type": "session.held", **base, "hold_pts": self.hold_pts})
        return out


class SessionManager:
    def __init__(self, *, registry: Registry, presets: Presets, events, max_sessions: int = 4,
                 public_base: Optional[str] = None, inputs: Optional[ShowInputs] = None,
                 latency_file: Optional[Any] = None):
        self.registry = registry
        self.presets = presets
        self.events = events
        self.max_sessions = max_sessions
        self.public_base = public_base.rstrip("/") if public_base else None
        self.sessions: dict[str, Session] = {}
        self.idempotency: dict[str, str] = {}
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._gpu_cache: tuple[float, list[dict]] = (0.0, [])
        self._creating: dict[Any, asyncio.Future] = {}   # slot (idempotency key or a token) -> done future
        self._creating_ids: dict[Any, str] = {}          # slot -> the session id being created
        self.inputs = inputs                             # the show bus (None: no --show-bus)
        self.latency_file = latency_file
        self.warmed: set[int] = set()                    # GPUs whose plug-in kernels are loaded
        self.warming: dict[int, asyncio.Future] = {}     # a warm in progress per GPU (server start or a session)
        self.plugin_errors: dict[tuple[str, str], dict] = {}   # (kind, name) -> {count, sessions, last_*}
        self.library: Any = None                         # library.Library (dev: costs, thumbnails)
        self.slow: dict[tuple[str, str], dict] = {}      # (kind, plug-in) -> the GPU guard's trip (process-wide)
        self.slow_trips = 0
        self.calibration = Calibration()                 # tools/calibrate.py's per-GPU costs (re-read on change)
        self.ratings: Any = None                         # the owner's Library ratings (ratings.Ratings; make_app)
        self.rooms = rooms.RoomMap()                     # screen -> room (rooms.json): one director per room
        self.switches = StreamSwitches.in_dir(getattr(presets, "dir", None))   # stream_switches.json (change 2)
        self.room_direction: Optional[dict] = None       # show_service's last room.direction (on_room_message)
        self.music_feed = MusicFeed(getattr(presets, "music_response", None))   # show_service's music.* (+ config)
        self.room_events_ring: collections.deque = collections.deque(maxlen=8)   # (wall s, kind, detail)
        self.room_why: Optional[tuple[dict, float]] = None    # show_service's room.why (data, arrival)
        self.room_leaders: dict[str, str] = {}           # room -> its leader's session id (kept while it is live)
        self._broken_names: tuple = (None, frozenset())

    def note_plugin_error(self, kind: Optional[str], name: Optional[str], detail: Optional[str], sid: str) -> int:
        rec = self.plugin_errors.setdefault((str(kind), str(name)), {"count": 0, "sessions": []})
        rec["count"] += 1
        if sid not in rec["sessions"]:
            rec["sessions"] = (rec["sessions"] + [sid])[-8:]
        rec["last_detail"] = detail
        rec["last_at"] = _iso(time.time())
        return rec["count"]

    def plugin_error_list(self) -> list[dict]:
        return [{"kind": k, "name": n, **v} for (k, n), v in sorted(self.plugin_errors.items())]

    # -- GPU frame-time guard and the vetted pool ----------------------------------------
    def note_slow(self, info: dict, sid: str) -> dict:
        """The GPU guard stopped a plug-in (a hang, any session): recorded for the catalog and the diagnostics,
        never benched (owner, 18 Oct: no gates; the directors may pick it again)."""
        key = (str(info.get("kind")), str(info.get("name")))
        wall = time.time()
        rec = self.slow.setdefault(key, {"count": 0, "first_at": _iso(wall)})
        rec.update(count=rec["count"] + 1, ms=info.get("gpu_ms"), budget_ms=info.get("budget_ms"),
                   why=info.get("why"), profile=info.get("profile"), session=sid, at=_iso(wall))
        self.slow_trips += 1
        return rec

    def slow_runtime_list(self) -> list[dict]:
        return [{"kind": k, "name": n, "source": "runtime", "status": "slow", **v}
                for (k, n), v in sorted(self.slow.items())]

    def slow_list(self) -> list[dict]:
        """The catalog's ``slow`` list: GPU guard trips, then calibration entries that are not ok."""
        return self.slow_runtime_list() + self.calibration.problems()

    def slow_for(self, kind: str, name: str) -> Optional[dict]:
        """One plug-in's slow mark for the library (a trip, else a calibration entry that is not ok)."""
        rec = self.slow.get((kind, name))
        if rec is not None:
            return {"source": "runtime", "status": "slow", **rec}
        p = self.calibration.plugins().get(name)
        if isinstance(p, dict):
            for k, e in sorted(p.items()):
                if k.startswith("gpu") and isinstance(e, dict) and not Calibration.ok(e):
                    st = e.get("status")
                    return {"source": "calibration", "gpu": k[3:], "calibration": st,
                            "status": "broken" if st in ("hung", "error") else "slow",
                            "ms": e.get("fit_p95") if e.get("fit_scale") else e.get("p95"),
                            "budget_ms": e.get("budget_ms")}
        return None

    def broken_names(self) -> frozenset:
        reg = self.registry
        key = (reg.rev, len(reg.broken))
        if self._broken_names[0] != key:
            self._broken_names = (key, frozenset(str(b.get("name")) for b in reg.broken.values()))
        return self._broken_names[1]

    def rating_weight(self, kind: str, plugin: Optional[str]) -> float:
        """How much the auto-directors favour a plug-in by its Library rating (ratings.STAR_WEIGHT); 1.0 unrated."""
        return 1.0 if self.ratings is None else self.ratings.weight(kind, plugin)

    def auto_ok(self, kind: str, plugin: Optional[str], gpu: Optional[int] = None,
                budget_4k: Optional[float] = None) -> bool:
        """The vetted pool: may a director pick this plug-in on its own?  Not rejected in the Library and not
        broken; then the calibration entry for this GPU decides when there is one (status ok and fits); otherwise core, or imported with a harness sheet (it rendered at least once) and no harness cost over
        the session's budget."""
        if not plugin:
            return True
        if self.rating_weight(kind, plugin) == 0.0:      # rejected in the Library
            return False
        if plugin in self.broken_names():
            return False
        if gpu is not None:
            e = self.calibration.entry(plugin, gpu)
            if e is not None:
                return Calibration.ok(e)
        sheets, est = vetting()
        if origin_of(plugin) == "imported" and plugin not in sheets:
            return False
        ms = est.get(plugin)
        return budget_4k is None or ms is None or ms <= budget_4k

    def first_scene(self, p: dict, look: dict, leader: Optional[Session]) -> SceneSpec:
        """A session created with scene "auto": the room leader's scene when joining a room (the screens start
        together), else the director's weighted pick from the auto pool for this direction (vetted pool, ratings),
        falling back to the whole vetted pool when the auto pool has nothing that fits."""
        pixels = p["profile"].w * p["profile"].h / (3840 * 2160)
        name = None
        if leader is not None and leader.screen != p["screen"]:
            name = (leader.current_spec or leader.scene_spec).name
        if name is None or name not in self.presets.scenes:
            budget = trip_budget_ms(p["profile"].fps) / max(1e-6, pixels)
            for auto in (look.get("auto"), None):
                d = Director(auto, look.get("direction") or dict(DIRECTION_DEFAULTS, moods=[]))
                d.vet = lambda kind, plugin: self.auto_ok(kind, plugin, p["gpu"], budget)
                d.rate = self.rating_weight
                got = d.pick_scene(self.presets.scenes, None, time.monotonic())
                if got is not None:
                    name = got[0]
                    break
        if name is None:
            raise ParamError("empty_pool", "scene \"auto\": no scene in the pool can run here")
        log.info("first scene (auto) for %s: %s", p["screen"], name)
        return self.fit_scene(self.presets.resolve_scene(name), p["gpu"], pixels)

    def fit_scene(self, spec: SceneSpec, gpu: int, pixels_ratio: float = 1.0, explicit: bool = False) -> SceneSpec:
        """The calibration's ``fit_scale`` (measured at 4K on this GPU) as the scene's render_scale, scaled to the
        session's pixels; never raised above the spec's own value, and not when the cue sets render_scale."""
        if explicit or "render_scale" in spec.bindings or "render_scale" not in spec.params:
            return spec
        e = self.calibration.entry(spec.source, gpu)
        fit = e.get("fit_scale") if e is not None else None
        if isinstance(fit, bool) or not isinstance(fit, (int, float)) or fit <= 0:
            return spec
        cls = self.registry.sources.get(spec.source)
        prop = cls.param_props().get("render_scale") if cls is not None else None
        if prop is None:
            return spec
        scale = min(1.0, float(fit) / math.sqrt(max(1e-6, pixels_ratio)))      # cost ~ rendered pixels
        scale = max(float(prop.get("minimum", 0.0)), min(float(prop.get("maximum", 1.0)), scale))
        if scale >= float(spec.params["render_scale"]):
            return spec
        log.info("calibration: %s on GPU %s at render_scale %.2f (fit_scale %.2f)", spec.name, gpu, scale, fit)
        return dataclasses.replace(spec, params={**spec.params, "render_scale": round(scale, 3)})

    def cost_ms(self, kind: str, name: str) -> Optional[float]:
        """4K bench ms of a preset / plug-in (lab harness or conformance), or None."""
        lib = self.library
        if lib is None:
            return None
        try:
            return lib.cost_ms(kind, name)
        except Exception:
            return None

    WARM_TRANSITIONS = ("cut", "crossfade")

    def warm_plugins(self, ctx) -> None:
        """Worker thread, first session on a GPU: set up the transitions config/live presets use (plus cut and
        crossfade, which recovery may start unprepared), so the usual ones are compiled before the render thread
        needs them.  Every other plug-in (hundreds) is set up and primed on demand when a cue prepares it (R20)."""
        names = sorted(set(self.WARM_TRANSITIONS) | self.presets.file_plugins("transition"))
        for name in names:
            cls = self.registry.transitions.get(name)
            if cls is None:
                continue
            try:
                t = cls(cls.validate({}))
                t.setup(ctx)
                t.close()
            except Exception:
                log.exception("warming transition %s failed", name)
        ctx.cp.cuda.get_current_stream().synchronize()

    async def ensure_warm(self, gpu: int, run: Callable[[], Any]) -> None:
        """Warm `gpu` once.  A caller arriving while a warm runs (e.g. the server-start one) waits on that warm;
        if it failed, the caller runs its own.  A failed warm is logged, never raised: plug-ins then compile on
        demand when a cue prepares them."""
        while gpu not in self.warmed:
            task = self.warming.get(gpu)
            if task is None:
                task = self.warming[gpu] = asyncio.ensure_future(self._warm(gpu, run))
                await asyncio.shield(task)
                return
            await asyncio.shield(task)

    async def _warm(self, gpu: int, run: Callable[[], Any]) -> None:
        t0 = time.monotonic()
        try:
            await run()
            self.warmed.add(gpu)
            log.info("GPU %d warm in %.1f s", gpu, time.monotonic() - t0)
        except Exception:
            log.exception("warming GPU %d failed", gpu)
        finally:
            self.warming.pop(gpu, None)

    def warm_gpu(self, gpu: int) -> None:
        """Worker thread, server start: a small throwaway context on `gpu`, then warm_plugins, so the first live
        session on that GPU never pays the CUDA start-up and the transition compiles (kernels are cached per
        device for the whole process)."""
        from .gpu import RenderContext

        ctx = RenderContext(gpu, 1280, 720, 30, prealloc_buffers=0)
        with ctx.device:
            self.warm_plugins(ctx)
            try:    # live_art's pinned upload buffers (process-wide, idempotent): its first set-up never stalls
                from .plugins.sources import live_art
                live_art.reserve_pinned(ctx.cp, 3840, 2160)
            except Exception:
                log.exception("reserving live_art's pinned buffers failed")

    async def warm_all(self) -> None:
        """Server start (--warm): warm every GPU in the background, one after another."""
        loop = asyncio.get_running_loop()
        for gpu in range(len(self.gpu_names())):
            await self.ensure_warm(gpu, lambda gpu=gpu: loop.run_in_executor(None, self.warm_gpu, gpu))

    def post(self, fn: Callable, *args) -> None:
        """Run fn(*args) on the loop thread (from any thread)."""
        try:
            self.loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # the loop has closed (service shutdown): engine threads are finishing
            pass

    def active(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.state != "ended"]

    # -- sound (sound.py): every music.* / show.state message to each session's view; a test piano -------------
    def on_room_message(self, msg: dict) -> None:
        """Loop thread (the music bus): show_service's room.direction, validated, for MusicDirection (§8.7), and
        its room.events (cue hints, G9).  room.look (the palette) goes through room_service, the one palette writer."""
        if msg.get("type") == "room.event" and isinstance(msg.get("data"), dict):
            self._room_event(msg["data"])
            return
        if msg.get("type") == "room.why" and isinstance(msg.get("data"), dict):
            self.room_why = (msg["data"], time.monotonic())      # show_service's reasons, for the details panel
            return
        if msg.get("type") != "room.direction" or not isinstance(msg.get("data"), dict):
            return
        data = msg["data"]
        d, dropped = lenient_direction(data, self.presets.vocab.words)
        warned = self.__dict__.setdefault("_room_dir_warned", set())
        if set(dropped) - warned:
            log.warning("room.direction: bad field(s) %s took their defaults", sorted(set(dropped) - warned))
            warned.update(dropped)
        groove = data.get("groove")
        self.music_feed.on_room_direction(data)
        self.room_direction = {"direction": d, "vibe": data.get("vibe") or None, "section": data.get("section"),
                               "groove": float(groove) if isinstance(groove, (int, float)) else None,
                               "beat_lock": data.get("beat_lock"), "hold": data.get("hold") is True,
                               "seq": data.get("seq"), "t": time.monotonic()}

    def _room_event(self, data: dict) -> None:
        kind = data.get("kind")
        if kind not in CUT_EVENTS and kind not in FX_EVENT_P and kind not in TRACK_EVENTS:   # breakdown: FX only
            return
        if kind == "section_change" and data.get("detail") == "track":
            return                                   # the same junction as the track_change: one cut, not two
        try:
            at_s = float(data["at_ms"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            at_s = None
        # a stable id (re-sends and updates of one event share it; detail / time tell the updates apart), else
        # (an older show_service, two publishers with separate seqs) the kind and time
        key = (data.get("id") or kind, data.get("detail"), None if at_s is None else round(at_s * 4))
        seen = getattr(self, "_room_events_seen", None)
        if seen is None:
            seen = self._room_events_seen = collections.deque(maxlen=32)
        if key in seen:
            return
        seen.append(key)
        detail = str(data.get("detail") or "")[:80]
        self.room_events_ring.append((time.time(), kind, detail))
        for s in list(self.sessions.values()):
            s.room_event(kind, at_s, detail, data)

    def room_hold(self, now: float) -> bool:
        """A person holds the look (show_service's room.direction ``hold``: Keep this look, or their own pick still
        pending): neither director changes anything, scenes or effects (owner 17 Oct).  A person's own Next still
        works."""
        r = self.room_direction_now(now)
        return bool(r and r.get("hold"))

    def room_direction_now(self, now: float) -> Optional[dict]:
        """The room director's direction while it is fresh, else None (show_service's features, then the desk's,
        propose).  With no vibe it still follows the music (show_service's director: band moods, energy, pace), so
        it fills the slot then too (d4's G3, owner 17 Oct)."""
        r = self.room_direction
        if r is None or now - r["t"] > ROOM_STALE_S:
            return None
        return r

    CLOCK_FRESH_S = 1.5        # music.clock comes at 10 Hz and on every re-anchor

    def music_grid(self, now: float) -> Optional[tuple[float, float]]:
        """show_service's beat clock as (anchor, period) seconds on CLOCK_MONOTONIC (time.monotonic's clock), while
        it is fresh, has a tempo and its gate is "beat" (conf >= 0.70 to lock, 0.60 to let go, there); else None."""
        d = self.music_feed.clock(now)
        if d is None:
            return None
        try:
            bpm, anchor = float(d["bpm"]), float(d["anchor_ms"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            return None
        if bpm <= 0.0 or d.get("gate") != "beat" or d.get("state") not in ("locked", "holding"):
            return None
        return anchor, 60.0 / bpm

    SCOPE_LISTENS = frozenset(("scope", "fft"))

    def wants_scope(self) -> bool:
        """A live session shows a plug-in that reads the fast feed (listens "scope" / "fft": its scene, the scene's
        effects or the bus): scope_feed.ScopeFeed connects only then (3a, 18 Oct: the meter costs show_service ~25 %
        of its bus loop while a client is on).  Loop thread; loop-owned state only."""
        reg = self.registry

        def hears(table: dict, name: Any) -> bool:
            return bool(self.SCOPE_LISTENS.intersection(getattr(table.get(name or ""), "listens", ()) or ()))

        for s in list(self.sessions.values()):
            if s.state in ("ended", "failed"):
                continue
            sp = s.current_spec
            if sp is not None and (hears(reg.sources, sp.source) or any(hears(reg.effects, e.effect)
                                                                       for e in sp.effects)):
                return True
            if any(hears(reg.effects, st.spec.effect) for st in list(s.fx.stack)):
                return True
        return False

    def broadcast_sound(self, msg: dict) -> None:
        """Any thread (the bus loop): append-only on each view."""
        if str(msg.get("type") or "").startswith("music."):
            self.music_feed.on_message(msg)
        for s in list(self.sessions.values()):
            if s.sound is not None and s.state not in ("ended", "failed"):
                s.sound.on_message(msg)

    def test_piano(self, on: bool, path: Optional[str] = None, phrase: Optional[str] = None,
                   piece: Optional[str] = None) -> dict:
        """Loop thread: start / stop a generated (or .mid) piano feeding every session, until the real MIDI is in.
        ``piece`` is an id from piano_replay.library() (a phrase, or file:<name> in config/live/test-piano/)."""
        from .piano_replay import TestPiano, resolve
        t = getattr(self, "_piano_task", None)
        if t is not None:
            t.cancel()
            self._piano_task = None
        self._piano_piece = None
        if on:
            if piece:
                piano = resolve(piece)
            else:
                piano = TestPiano.from_file(path, loop=True) if path else TestPiano(phrase or "medley", loop=True)
                piece = f"file:{path.replace(chr(92), '/').rsplit('/', 1)[-1]}" if path else (phrase or "medley")

            async def run() -> None:
                while True:
                    for m in piano.poll(time.monotonic() * 1000.0):
                        self.broadcast_sound(m)
                    await asyncio.sleep(0.02)

            self._piano_task = asyncio.ensure_future(run())
            self._piano_piece, self._piano_since = piece, time.monotonic()
        return {"test_piano": on, **self.test_piano_status()}

    def test_piano_status(self) -> dict:
        """What the test piano is playing and what it could play (GET /api/sound/test, the Room page's Test music)."""
        from .piano_replay import library
        piece = getattr(self, "_piano_piece", None)
        running = piece is not None and getattr(self, "_piano_task", None) is not None
        return {"available": True, "running": running, "piece": piece if running else None,
                "playing_s": round(time.monotonic() - self._piano_since, 1) if running else None,
                "pieces": library()}

    def room_leader(self, room: str) -> Optional[Session]:
        """The session that directs a room (rooms.py): kept while it stays live, else the oldest live member."""
        ld = rooms.pick_leader(rooms.members(self.sessions.values(), room), self.room_leaders.get(room))
        if ld is None:
            self.room_leaders.pop(room, None)
        elif self.room_leaders.get(room) != ld.id:
            self.room_leaders[room] = ld.id
            log.info("[%s] room %s: %s leads", ld.id, room, ld.screen)
        return ld

    def get(self, sid: str) -> Session:
        s = self.sessions.get(sid)
        if s is None:
            raise SessionError(404, "not_found", f"no session {sid!r}")
        return s

    # -- create ------------------------------------------------------------------
    def _parse(self, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ParamError("bad_request", "body must be a JSON object")
        known = {"screen", "gpu", "profile", "anchor", "scene", "transitions", "ttl_s", "idempotency_key",
                 "color_trc", "fixture", "width", "height", "room", "hud", *LOOK_FIELDS}
        unknown = set(body) - known
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        screen = body.get("screen")
        if not isinstance(screen, str) or not SCREEN_RE.match(screen):
            raise ParamError("bad_request", "screen must be an id like 'devtest'")
        gpu = body.get("gpu", 0)
        n_gpus = len(self.gpu_names())
        if isinstance(gpu, bool) or not isinstance(gpu, int) or gpu < 0 or (n_gpus and gpu >= n_gpus):
            raise ParamError("bad_request", f"gpu must be an index 0..{max(0, n_gpus - 1)}")
        pname = body.get("profile", "hevc-2160p60")
        if pname not in PROFILES:
            raise ParamError("unknown_profile", f"unknown profile {pname!r} (known: {sorted(PROFILES)})")
        try:
            profile = PROFILES[pname].with_trc(body.get("color_trc"))
        except ValueError as e:
            raise ParamError("bad_request", str(e)) from None
        if "width" in body or "height" in body:
            # The display's own size (e.g. a portrait TV: 2160 x 3840), up to the profile's pixel count.
            w, h = body.get("width"), body.get("height")
            if not all(isinstance(v, int) and not isinstance(v, bool) and 64 <= v <= 4096 for v in (w, h)):
                raise ParamError("bad_request", "width and height must both be integers 64..4096")
            if w * h > profile.w * profile.h:
                raise ParamError("bad_request", f"width x height is over the profile's {profile.w}x{profile.h} "
                                                "pixels: pick a smaller size or a bigger profile")
            profile = dataclasses.replace(profile, w=w - w % 2, h=h - h % 2)    # 4:2:0 needs even sizes
        if profile.codec == "av1":
            from .gpu import supports_av1_encode
            if supports_av1_encode(gpu) is False:
                raise ParamError("unsupported_profile", f"GPU {gpu} has no AV1 encoder (needs an Ada GPU)")
        if "scene" not in body:
            raise ParamError("bad_request", "scene is required (a preset, a spec, or \"auto\": the director picks it)")
        # "auto" / {"auto": true} (owner, 15 Oct: modes carry tags and direction, never scenes): the first scene is the
        # director's pick from the session's auto pool and direction, or the room leader's scene (create()).
        scene = None if body["scene"] == "auto" or body["scene"] == {"auto": True} else \
            self.fit_scene(self.presets.resolve_scene(body["scene"]), gpu, profile.w * profile.h / (3840 * 2160),
                           sets_render_scale(body["scene"]))
        trans = body.get("transitions") or {}
        if not isinstance(trans, dict) or set(trans) - {"in", "out"}:
            raise ParamError("bad_request", "transitions must be {\"in\": spec, \"out\": spec}")
        t_in = self.presets.resolve_transition(trans.get("in", "crossfade-3s"))
        t_out = self.presets.resolve_transition(trans.get("out", "crossfade-3s"))
        ttl = body.get("ttl_s", TTL_DEFAULT_S)
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)):
            raise ParamError("bad_request", "ttl_s must be a number")
        ttl = min(TTL_MAX_S, max(TTL_MIN_S, float(ttl)))
        key = body.get("idempotency_key")
        if key is not None and (not isinstance(key, str) or not 0 < len(key) <= 200):
            raise ParamError("bad_request", "idempotency_key must be a non-empty string")
        if "anchor" not in body:
            raise ParamError("bad_request", "anchor is required")
        png, rgb = decode_still(body["anchor"], "anchor")
        # LIVE_V1 1.1: direction applies at once on create (R12); an absent auto block means no auto (R11)
        look = dict(auto=None, direction=None, palette=None, palette_s=PALETTE_S_DEFAULT, fixture=None,
                    beat_follow="extrapolate", direction_ease_s=8.0)
        if body.get("direction") is not None:
            look["direction"] = validate_direction(body["direction"], self.presets.vocab.words)
        if body.get("auto") is not None:
            look["auto"] = validate_auto(body["auto"])
        look["palette"] = parse_palette(body.get("palette"))
        if body.get("palette_transition_s") is not None:
            look["palette_s"] = parse_seconds(body["palette_transition_s"], "palette_transition_s", 0, 60)
        if body.get("direction_ease_s") is not None:
            look["direction_ease_s"] = parse_seconds(body["direction_ease_s"], "direction_ease_s", 0, 120)
        if body.get("beat_follow") is not None:
            if body["beat_follow"] not in BEAT_FOLLOW:
                raise ParamError("bad_request", f"beat_follow must be one of {list(BEAT_FOLLOW)}")
            look["beat_follow"] = body["beat_follow"]
        if body.get("fixture") is not None:
            if not isinstance(body["fixture"], str) or not FIXTURE_RE.match(body["fixture"]):
                raise ParamError("bad_request", "fixture must be an id like 'screen.north'")
            look["fixture"] = body["fixture"]
        room = body.get("room")
        if room is not None and (not isinstance(room, str) or not rooms.ROOM_RE.match(room)):
            raise ParamError("bad_request", "room must be an id like 'drawing-room'")
        look_room = room or self.rooms.room_of(screen)
        hud = body.get("hud", False)
        if not isinstance(hud, bool):
            raise ParamError("bad_request", "hud must be true or false")
        return dict(screen=screen, gpu=gpu, profile=profile, scene=scene, t_in=t_in, t_out=t_out, ttl_s=ttl,
                    idempotency_key=key, png=png, rgb=rgb, look=look, room=look_room, hud=hud)

    def attach_for(self, s: Session, host: str) -> dict:
        base = self.public_base or f"ws://{host}"
        return {"url": f"{base}/media/{s.id}?k={s.key}", "codec": s.profile.codec, "w": s.profile.w,
                "h": s.profile.h, "fps": s.profile.fps, "target_buffer_ms": s.profile.target_buffer_ms}

    async def create(self, body: Any, host: str) -> tuple[Session, bool]:
        """-> (session, created).  An idempotency_key that names a known session returns that session."""
        p = self._parse(body)
        key = p["idempotency_key"]
        if key is not None:
            while key in self._creating:         # the same request is being served right now: wait for it
                await asyncio.shield(self._creating[key])
            sid = self.idempotency.get(key)
            if sid is not None and sid in self.sessions:
                s = self.sessions[sid]
                s.renew()
                return s, False
        # sessions being created count too; once one is in self.sessions, active() already counts it
        starting = sum(1 for sid in self._creating_ids.values() if sid not in self.sessions)
        if len(self.active()) + starting >= self.max_sessions:
            raise SessionError(503, "too_many_sessions", f"at most {self.max_sessions} sessions at once")
        fut: asyncio.Future = self.loop.create_future()
        slot = key if key is not None else object()
        sid = "s-" + secrets.token_hex(4)
        self._creating[slot] = fut
        self._creating_ids[slot] = sid
        try:
            look = p["look"]
            ld = self.room_leader(p["room"]) if p["room"] and look["auto"] is not None else None
            if ld is not None and ld.screen != p["screen"]:
                look = {**look, **rooms.look_of(ld)}      # joining a room: the room's look, not the body's
            if p["scene"] is None:
                p["scene"] = self.first_scene(p, look, ld)
            s = Session(self, sid=sid, screen=p["screen"], gpu=p["gpu"], profile=p["profile"], scene=p["scene"],
                        t_in=p["t_in"], t_out=p["t_out"], ttl_s=p["ttl_s"], idempotency_key=key, **look)
            s.room = p["room"]
            s.hud_wanted = p["hud"]
            s.attach = self.attach_for(s, host)
            anchor = await s.decode(p["png"], p["rgb"])   # ParamError (400) before the session exists
            self.sessions[sid] = s
            try:
                s.emit("session.state", state="starting")
                await s.start(anchor)
            except Exception as e:
                log.exception("[%s] start failed", sid)
                await self._discard(s, "error", f"{type(e).__name__}: {e}")
                raise SessionError(500, "start_failed", f"{type(e).__name__}: {e}") from None
            if key is not None:
                self.idempotency[key] = sid
        finally:
            self._creating.pop(slot, None)
            self._creating_ids.pop(slot, None)
            fut.set_result(None)
        log.info("[%s] created: screen=%s gpu=%d profile=%s scene=%s", sid, s.screen, s.gpu, s.profile.name,
                 s.scene_spec.name)
        return s, True

    async def _discard(self, s: Session, reason: str, detail: Optional[str] = None) -> None:
        try:
            await s.end(reason, detail)
        finally:
            self.sessions.pop(s.id, None)

    async def delete(self, sid: str) -> None:
        s = self.get(sid)
        await s.end("deleted")

    # -- housekeeping --------------------------------------------------------------
    async def reaper(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            for s in list(self.sessions.values()):
                if s.state != "ended" and now > s.expires_at:
                    log.info("[%s] TTL expired", s.id)
                    asyncio.create_task(s.end("ttl"))
                elif s.state == "ended" and s.ended_at is not None and now - s.ended_at > ENDED_KEEP_S:
                    self.sessions.pop(s.id, None)
                    if s.idempotency_key and self.idempotency.get(s.idempotency_key) == s.id:
                        self.idempotency.pop(s.idempotency_key, None)

    async def ticker(self) -> None:
        """The directors and inputs of every session, at 10 Hz on the loop thread."""
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            for s in list(self.sessions.values()):
                try:
                    s.tick(now)
                except Exception:
                    log.exception("[%s] tick failed", s.id)

    async def shutdown(self) -> None:
        await asyncio.gather(*(s.end("deleted", "service shutdown") for s in self.active()),
                             return_exceptions=True)

    def snapshot_events(self) -> list[dict]:
        return [ev for s in self.active() for ev in s.snapshot_events()]

    # -- GPUs ----------------------------------------------------------------------
    def gpu_names(self) -> list[dict]:
        from .gpu import device_names

        if not hasattr(self, "_devices"):
            self._devices = device_names()
        return self._devices

    def gpu_state(self) -> list[dict]:
        """[{index, name, mem_used_mb}] via NVML (diag.py: no fork; nvidia-smi only without NVML), cached for 2 s."""
        t, cached = self._gpu_cache
        if time.monotonic() - t < 2.0:
            return cached
        from .diag import _query_gpus

        by_bus: dict[int, int] = {}
        try:
            for g in _query_gpus():
                if g.get("mem_used_mb") is not None:
                    by_bus[g["pci_bus"]] = int(g["mem_used_mb"])
        except Exception:
            pass
        res = [{"index": d["index"], "name": d["name"], "mem_used_mb": by_bus.get(d["pci_bus"])}
               for d in self.gpu_names()]
        self._gpu_cache = (time.monotonic(), res)
        return res
