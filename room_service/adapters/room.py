"""Adapters for state that lives in room_service itself: sensor_mask.main, page.<screen>.timed_reload,
diag.<screen> (the live-details panel), brightness.screens / brightness.lamps, director.*, and log signals.

Both level adapters persist what they applied in their own file under the state directory and restore it at start
(ROOM_STATE_MODEL §5.2 rule 10), so read() stays truthful after a restart: without that, a restart mid-show would let
the art mask follow the show-lit sensor again.

Words for the screens' device come from the deployment (manifest wording.screen_device, e.g. "TV"; generic "display").
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

import aiohttp

import smconfig

from ..mask import parse_mask
from ..roomstate.contract import Adapter, Outcome, Reading
from ..roomstate.schema import enum_schema, range_schema
from ..util import spawn, wait_until
from .screens import screen_absent, screen_label

if TYPE_CHECKING:
    from ..screens import ScreenController

log = logging.getLogger("room_service.adapters.room")


def device_word() -> str:
    """What this site calls the thing a screen's page runs on ("TV" at a site of TVs): the deployment's
    wording.screen_device, else "display"."""
    try:
        return smconfig.deployment().words("screen_device", "display")
    except Exception:                    # no deployment readable (a bare test tree): the generic word
        return "display"


def screens_follow_a_sensor(screens: Iterable[str], intensity_json: str | Path | None,
                            destinations_json: str | Path | None) -> bool | None:
    """True when a light sensor of this site (intensity.json sensor_mappings) drives at least one of `screens`, by
    lightmask's own target resolution (Flask's); False when none does; None when it can't be told (a file unreadable,
    or lightmask not importable): the caller keeps its usual words."""
    try:
        import lightmask as lm                       # noqa: PLC0415 - needs astral; optional here
        mapping = json.loads(Path(intensity_json).read_text()) if intensity_json else None
        dests = json.loads(Path(destinations_json).read_text()) if destinations_json else []
        if not isinstance(mapping, dict) or not isinstance(dests, list):
            return None
        conf = lm.LightConfig.from_data({}, mapping, [d for d in dests if isinstance(d, dict) and d.get("id")])
    except FileNotFoundError:
        return False if intensity_json and not Path(intensity_json).exists() else None   # no intensity.json: none
    except Exception:
        return None
    want = set(screens)
    return any(want & set(lm.resolve_targets(conf, name)) for name in conf.sensor_mappings)


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class SensorMaskAdapter(Adapter):
    """sensor_mask.main: "follow" | "suspended". Room-wide, through the mask's set_suspended(). The file keeps the
    held value per screen too, so after a restart while suspended the TV pages' welcome carries what they showed.

    With mode zones (zones={room: [screens]}, room_service/zones.py) there is one member per zone too:
    sensor_mask.<room> holds only that room's screens, and "main" the screens in no zone. The file is then
    {"scopes": {"main": bool, <room>: bool}, "held"}; an old {"suspended", "held"} file loads as main. Without zones
    every byte is as before."""

    subsystem = "sensor_mask"
    subsystem_label = "Art brightness"
    category = "setting"
    kind = "level"
    members = ["main"]
    poll_s = 1.0
    VALUES = ("follow", "suspended")

    def __init__(self, mask: Any, state_dir: str | Path, sensed: bool | None = None,
                 pushed: bool | None = None, zones: dict[str, list[str]] | None = None) -> None:
        """sensed: whether a light sensor drives any of this site's live screens (screens_follow_a_sensor; False:
        the mask follows the time of day, and "follow" says so). pushed: whether any screen takes a pushed mask
        (mask_push); False: the setting is said to change nothing here. zones: {room: [screens]} of the mode zones."""
        self.mask = mask
        self.path = Path(state_dir) / "sensor_mask.json"
        self.sensed, self.pushed = sensed, pushed
        self.zones = {r: list(ss) for r, ss in (zones or {}).items()}
        if self.zones:
            self.members = ["main", *self.zones]
            if hasattr(mask, "zone_of"):
                mask.zone_of = {sid: r for r, ss in self.zones.items() for sid in ss}

    def _set(self, member: str, suspended: bool) -> None:
        if member == "main":
            self.mask.set_suspended(suspended)
        else:
            self.mask.set_suspended(suspended, scope=member)

    def _is_suspended(self, member: str) -> bool:
        if member == "main":
            return bool(self.mask.suspended)
        return member in (getattr(self.mask, "suspended_scopes", None) or ())

    def restore(self) -> None:
        saved = _load(self.path)
        scopes = saved.get("scopes") if self.zones else None
        if isinstance(scopes, dict):
            scopes = {m: v for m, v in scopes.items() if m in self.members and isinstance(v, bool)}
        else:
            suspended = saved.get("suspended")
            if not isinstance(suspended, bool):
                return
            scopes = {"main": suspended}
        if any(scopes.values()):
            self._seed(saved.get("held"))
        for m, v in scopes.items():
            self._set(m, v)

    def _seed(self, held: Any) -> None:
        """LM-1: put the held values back as what the pages show, for screens still configured with mask_push."""
        cfg, pushed = getattr(self.mask, "cfg", None), getattr(self.mask, "pushed", None)
        if not isinstance(held, dict) or cfg is None or not isinstance(pushed, dict):
            return
        sources = getattr(self.mask, "pushed_source", None)
        for sid, v in held.items():
            sc = cfg.screen(sid)
            mv = parse_mask(v) if isinstance(v, dict) else None
            if not sc or not sc.mask_push or mv is None:
                continue
            pushed[sid] = mv
            if isinstance(sources, dict):
                sources[sid] = v.get("source")

    def value_schema(self, member: str) -> dict:
        follow = "Follows the time of day" if self.sensed is False else "Follows the room's light"
        s = enum_schema(list(self.VALUES), "Art brightness", {"follow": follow, "suspended": "Stays as it was"})
        s["x-ui"]["advanced"] = True
        if self.pushed is False:
            s["x-ui"]["help"] = "No screen here takes a pushed light mask, so this changes nothing"
        return s

    def normalize(self, member: str, value: Any) -> Any:
        if value not in self.VALUES:
            raise ValueError(f"{value!r} is not one of {list(self.VALUES)}")
        return value

    def label(self, member: str) -> str:
        return f"Art brightness ({member})" if member in self.zones else member

    async def read(self, members: list[str]) -> dict[str, Reading]:
        if not self.zones:
            return {"main": Reading("suspended" if self.mask.suspended else "follow")}
        return {m: Reading("suspended" if self._is_suspended(m) else "follow") for m in members if m in self.members}

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        pushed = getattr(self.mask, "pushed", None) or {}
        src = getattr(self.mask, "pushed_source", None) or {}
        held = {sid: {**v.as_dict(), "source": src.get(sid)} for sid, v in pushed.items()}
        if not self.zones:
            suspended = desired["main"] == "suspended"
            self.mask.set_suspended(suspended)
            _save(self.path, {"suspended": suspended, "held": held})
            return Outcome("ok", "", {"main": Outcome("ok")})
        ms = [m for m in desired if m in self.members]
        for m in ms:                         # one read-modify-write, no wait inside: zones never interleave here
            self._set(m, desired[m] == "suspended")
        _save(self.path, {"scopes": {m: self._is_suspended(m) for m in self.members}, "held": held})
        return Outcome("ok", "", {m: Outcome("ok") for m in ms})


class PageAdapter(Adapter):
    """page.<screen>.timed_reload: true | false, sent as `policy` on the screen's control socket.

    Every page is told at once and the waits for their reports overlap (a page reports kiosk.state every 5 s). The
    policy goes out on the socket that the screen's own freeze and live.attach follow, so the coordinator proves it
    behind the step (prove_behind): a rule's screens step starts at once, never waiting for a page's report."""

    subsystem = "page"
    subsystem_label = "Display pages"     # the site's word: __init__ ("TV pages" where wording.screen_device is TV)
    category = "setting"
    kind = "level"
    change_time_s = 10.0
    poll_s = 5.0
    prove_behind = True

    def __init__(self, controllers: dict[str, "ScreenController"], state_dir: str | Path, timeout_s: float = 10.0,
                 known: dict[str, str] | None = None) -> None:
        self.c = controllers
        self.members = [f"{sid}.timed_reload" for sid in controllers]
        self.path = Path(state_dir) / "page.json"
        self.timeout_s = timeout_s
        self.known = dict(known or {})                  # every screen the site has (absent(): art-only ones)
        self.word = device_word()
        self.subsystem_label = f"{self.word[:1].upper()}{self.word[1:]} pages"

    def label(self, member: str) -> str:
        return screen_label(self.c, self.known, self._sid(member))

    def absent(self, rest: str) -> str | None:
        return screen_absent(self.c, self.known, rest, (".timed_reload",))

    def restore(self) -> None:
        for sid, value in _load(self.path).items():
            if sid in self.c and isinstance(value, bool) and self.c[sid].timed_reload != value:
                self.c[sid].set_timed_reload(value)            # the next welcome carries it

    def _sid(self, member: str) -> str:
        return member.rsplit(".", 1)[0]

    def value_schema(self, member: str) -> dict:
        return {"type": "boolean", "default": True,
                "x-ui": {"label": f"Refresh the {self.word} pages now and then", "advanced": True,
                         "labels": {"true": "On", "false": "Off"}}}

    def normalize(self, member: str, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("timed_reload must be true or false")
        return value

    async def read(self, members: list[str]) -> dict[str, Reading]:
        out = {}
        for m in members:
            c = self.c[self._sid(m)]
            applied = c.page_timed_reload()
            out[m] = Reading(c.timed_reload if applied is None else applied)
        return out

    async def _confirm(self, m: str, value: bool, n: int) -> Outcome:
        c = self.c[self._sid(m)]
        if n == 0 or c._page() is None:
            return Outcome("ok", f"kept; the {self.word} page gets it when it opens")
        if c.kiosk_state is not None and "policy" not in c.kiosk_state:
            return Outcome("ok", "sent")
        if await wait_until(lambda: c.page_timed_reload() == value, c.changed, self.timeout_s):
            return Outcome("ok")
        return Outcome("unconfirmed", "sent; the page hasn't reported it in kiosk.state")

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        saved = _load(self.path)
        sent = {}
        for m, v in desired.items():                    # every page told before any wait (prove_behind)
            sent[m] = self.c[self._sid(m)].set_timed_reload(v)
            saved[self._sid(m)] = v
        _save(self.path, saved)
        outs = await asyncio.gather(*(self._confirm(m, v, sent[m]) for m, v in desired.items()))
        members = dict(zip(desired, outs))
        worst = max((o.status for o in members.values()), key=["ok", "unconfirmed", "failed"].index, default="ok")
        return Outcome(worst, "", members)


class DiagAdapter(Adapter):
    """diag.<screen>: true | false, the live-details panel burned into that screen's stream (docs/LIVE_DIAGNOSTICS.md),
    through ScreenController.set_hud(). Saved in its own file and put back at start, like page. No mode sets it unless
    it lists diag.<screen>; the Room page switches it with PUT /room/targets."""

    subsystem = "diag"
    subsystem_label = "Live details"
    category = "setting"
    kind = "level"
    change_time_s = 5.0
    poll_s = 5.0

    def __init__(self, controllers: dict[str, "ScreenController"], state_dir: str | Path,
                 known: dict[str, str] | None = None) -> None:
        self.c = controllers
        self.members = list(controllers)
        self.path = Path(state_dir) / "diag.json"
        self.wanted: dict[str, bool] = {}               # what we set, while a controller can't take it yet
        self.known = dict(known or {})                  # every screen the site has (absent(): art-only ones)
        self.word = device_word()

    def restore(self) -> None:
        for sid, value in _load(self.path).items():
            if sid in self.c and isinstance(value, bool):
                self.wanted[sid] = value
                if callable(getattr(self.c[sid], "set_hud", None)):
                    self.c[sid].set_hud(value)          # the next live session carries it

    def label(self, member: str) -> str:
        return screen_label(self.c, self.known, member)

    def absent(self, rest: str) -> str | None:
        return screen_absent(self.c, self.known, rest)

    def value_schema(self, member: str) -> dict:
        return {"type": "boolean", "default": False,
                "x-ui": {"label": f"Show details on the {self.word}", "labels": {"true": "On", "false": "Off"}}}

    def normalize(self, member: str, value: Any) -> Any:
        if not isinstance(value, bool):
            raise ValueError("diag is true or false")
        return value

    async def read(self, members: list[str]) -> dict[str, Reading]:
        out = {}
        for m in members:
            hud = getattr(self.c[m], "hud", None)
            out[m] = Reading(hud if isinstance(hud, bool) else self.wanted.get(m, False))
        return out

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        saved = _load(self.path)
        members = {}
        for m, v in desired.items():
            self.wanted[m] = saved[m] = v
            set_hud = getattr(self.c[m], "set_hud", None)
            if callable(set_hud):
                set_hud(v)
                members[m] = Outcome("ok")
            else:
                members[m] = Outcome("unconfirmed", "kept; this screen can't show details yet")
        _save(self.path, saved)
        worst = max((o.status for o in members.values()), key=["ok", "unconfirmed", "failed"].index, default="ok")
        return Outcome(worst, "", members)


RIG_LEVEL = "/api/v1/brightness"    # show_service: the master level of its lamps that follow the look (below)
NO_LAMPS = "this site has no lamps that follow the music"


class BrightnessAdapter(Adapter):
    """brightness.screens and brightness.lamps, 0-100 %: how bright the live screens and the lamps that follow the
    music are, for a mode whose star is something else (Disco: the glitterball in the pinspots; owner, 28 Oct). A gain
    on top of whatever the show does: screens through the renderer (ScreenController.set_level, a session's
    `level`), lamps through the room's Hue stream (HueOutput.level) and show_service's own lights (the `light`
    subsystem's DMX lights, through its rig level, below). Both ease over ~2 s. Kept in the state dir, so a restart
    keeps a dimmed room dimmed.

    `lamps` is a member only while the site has lamps that follow the music: a Hue output, or show_service lights
    (`rig`, the LightsAdapter, with at least one light). Without either, a mode naming brightness.lamps names nothing
    (absent(), a lint warning), and an apply that reaches it anyway is unconfirmed with the reason, never "ok".

    show_service's lamps brightness (show_service/api.py, owner 10 Nov): GET /api/v1/brightness -> 200
    {"lamps": 0..1, "fixtures": n}; PUT /api/v1/brightness {"lamps": 0..1} -> 200 the same (400 for a bad value, 503
    when show_service has no rig): a master gain on every lamp that follows the look (its Art-Net lights, and Hue when
    show_service streams it), kept by show_service across its restarts. While show_service has lights, keep_in_step() checks
    it every RIG_CHECK_S (at once after a change) and puts the room's level back when it differs (a show_service
    restart without the level kept, a light added later). A 404 (an older show_service) or 503 (no rig) is unconfirmed.

    brightness.screens may instead follow the room's light (owner, 29 Oct: "for ambient mode, we can take light sensor
    input as a dimming control for the screens ... smoothed because otherwise the screens own light intensity risk
    causing weird feedback loops in a darkened room"): {"follow": "sensor", "min": %, "max": %, "smooth_s": s}. Each
    screen's input is the brightness the art's mask has from the room's light (MaskArbiter.room_brightness: the same
    sensor and mapping, so live and art agree), mapped onto min..max and smoothed: an exponential ease with time
    constant smooth_s (owner: "10s or so"), sent when it has moved SEND_STEP. The first reading after it starts
    following is taken as it is (the art showed that a moment ago, so there is no jump). With mode zones it follows
    for the screens in no zone; a zone's brightness.<room>.screens is a fixed level."""

    subsystem = "brightness"
    subsystem_label = "Brightness"
    category = "setting"
    kind = "level"
    ALL = ("screens", "lamps")
    poll_s = 5.0
    RIG_CHECK_S = 10.0
    FOLLOW = "sensor"
    FOLLOW_STEP_S = 1.0         # how often a following screen reads the room's light
    SMOOTH_S = 10.0             # owner, 29 Oct: "3 mins is too smoothed. I was thinking 10s or so"
    SEND_STEP = 0.01            # a level change under this isn't sent
    LABELS = {"screens": "Screens (live)", "lamps": "Lamps (following the music)"}

    def __init__(self, controllers: dict[str, "ScreenController"], hue_output: Any, state_dir: str | Path,
                 mask: Any = None, clock: Callable[[], float] | None = None, rig: Any = None,
                 zones: dict[str, list[str]] | None = None) -> None:
        """mask: the room's MaskArbiter (room_brightness per screen), which brightness.screens follows when it is
        {"follow": "sensor", ...}. clock: the follow loop's clock (tests). rig: show_service's lights
        (adapters.lights.LightsAdapter: its url and members), or None (no show_service lights here: the show capability
        off, or tests). zones: {room: [screens]} of the mode zones (room_service/zones.py): each gets
        brightness.<room>.screens, which dims only its own live screens, and brightness.screens then dims the screens
        in no zone. lamps stay site-wide (one Hue stream)."""
        self.c = controllers
        self.hue = hue_output
        self.mask = mask
        self.rig = rig
        self.path = Path(state_dir) / "brightness.json"
        self.zones = {r: [sid for sid in ss] for r, ss in (zones or {}).items()}
        self.zone_of = {sid: r for r, ss in self.zones.items() for sid in ss}
        self.all = (*self.ALL, *(f"{r}.screens" for r in self.zones))
        self.values: dict[str, Any] = {m: 100 for m in self.all}
        self._nudge: asyncio.Event | None = None
        self._said: str | None = None
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self.follow_now: dict[str, float] = {}      # screen -> the smoothed level (0..1) it follows to
        self._sent: dict[str, float] = {}
        self._follow_t: float | None = None
        self._task: asyncio.Task | None = None

    @property
    def members(self) -> list[str]:          # type: ignore[override]  # lamps only while there are lamps
        return ["screens", *(["lamps"] if self.has_lamps() else []), *(f"{r}.screens" for r in self.zones)]

    def rig_lights(self) -> bool:
        return self.rig is not None and bool(getattr(self.rig, "members", None))

    def has_lamps(self) -> bool:
        return self.hue is not None or self.rig_lights()

    def absent(self, rest: str) -> str | None:
        return NO_LAMPS[:1].upper() + NO_LAMPS[1:] if rest == "lamps" and not self.has_lamps() else None

    def restore(self) -> None:
        saved = _load(self.path)
        for m in self.all:
            try:
                v = self.normalize(m, saved[m]) if m in saved else None
            except ValueError:
                v = None
            if v is not None:
                self.values[m] = v
        self._push([m for m in self.all if self.values[m] != 100])

    def label(self, member: str) -> str:
        return self.LABELS.get(member.rsplit(".", 1)[-1] if member.endswith(".screens") else member, member)

    def value_schema(self, member: str) -> dict:
        fixed = range_schema(0, 100, self.label(member), "%", step=5, default=100, ends=("Dark", "Full"))
        if member != "screens":
            return fixed
        pct = lambda label, d: {**range_schema(0, 100, label, "%", step=5, default=d)}   # noqa: E731
        return {"x-ui": {"label": self.label(member)},
                "oneOf": [{**fixed, "x-ui": {**fixed["x-ui"], "label": "Set"}},
                          {"type": "object", "required": ["follow"], "default": {"follow": self.FOLLOW, "min": 20,
                                                                                 "max": 100, "smooth_s": self.SMOOTH_S},
                           "x-ui": {"label": "Follows the room's light",
                                    "help": "Dim in a dark room, full in daylight, as the art is; it changes slowly."},
                           "properties": {"follow": {"const": self.FOLLOW, "x-ui": {"hidden": True}},
                                          "min": pct("In the dark", 20), "max": pct("In daylight", 100),
                                          "smooth_s": {"type": "number", "minimum": 1, "maximum": 600,
                                                       "default": self.SMOOTH_S,
                                                       "x-ui": {"label": "Smoothing", "unit": "s", "widget": "slider",
                                                                "ends": {"min": "Quick", "max": "Slow"}}}}}]}

    def normalize(self, member: str, value: Any) -> Any:
        if member == "screens" and isinstance(value, dict):
            if value.get("follow") != self.FOLLOW or set(value) - {"follow", "min", "max", "smooth_s"}:
                raise ValueError('brightness.screens follows the room\'s light as {"follow": "sensor", "min": %, '
                                 '"max": %, "smooth_s": s}')
            lo, hi = value.get("min", 20), value.get("max", 100)
            for v in (lo, hi):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                    raise ValueError(f"brightness is 0-100, got {v!r}")
            sm = value.get("smooth_s", self.SMOOTH_S)
            if isinstance(sm, bool) or not isinstance(sm, (int, float)) or not 1 <= sm <= 600:
                raise ValueError(f"smooth_s is 1-600 seconds, got {sm!r}")
            lo, hi = sorted((round(lo), round(hi)))
            return {"follow": self.FOLLOW, "min": lo, "max": hi, "smooth_s": float(sm)}
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100:
            raise ValueError(f"brightness is 0-100, got {value!r}")
        return round(value)

    def show(self, member: str, value: Any) -> str:
        if isinstance(value, dict):
            return f"room light {value['min']}-{value['max']}%"
        return f"{value}%"

    def words(self, member: str, value: Any) -> str:
        if isinstance(value, dict):
            return f"Follows the room's light ({value['min']}-{value['max']}%)"
        return "Full" if value == 100 else f"{value}%"

    def _following(self) -> bool:
        return isinstance(self.values["screens"], dict)

    def _push(self, members: list[str]) -> None:
        if "screens" in members:
            if self._following():
                self._start_follow()
            else:
                self._stop_follow()
                for sid, c in self.c.items():
                    if sid not in self.zone_of:      # no zones: every screen
                        c.set_level(self.values["screens"] / 100.0)
        for r, ss in self.zones.items():
            if f"{r}.screens" in members:
                for sid in ss:
                    if sid in self.c:
                        self.c[sid].set_level(self.values[f"{r}.screens"] / 100.0)
        if "lamps" in members and self.hue is not None:
            self.hue.level = self.values["lamps"] / 100.0
            cb = getattr(self.hue, "on_level", None)
            if callable(cb):
                cb()                           # the hue adapter: lamps on under house control follow the dimmer
        if "lamps" in members and self._nudge is not None:
            self._nudge.set()                  # show_service's lights: keep_in_step sends it

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {m: Reading(self.values[m]) for m in members if m in self.values}

    # -- following the room's light ---------------------------------------------------------------------------------
    def _room(self, sid: str) -> float | None:
        fn = getattr(self.mask, "room_brightness", None)
        if callable(fn):
            return fn(sid)
        v = (getattr(self.mask, "latest", None) or {}).get(sid) or (getattr(self.mask, "pushed", None) or {}).get(sid)
        return float(v.brightness) if v is not None else None

    def follow_step(self, now: float) -> None:
        """One step for every screen (FOLLOW_STEP_S apart in the task; tests call it with their own clock)."""
        cfg = self.values["screens"]
        if not isinstance(cfg, dict):
            return
        dt = 0.0 if self._follow_t is None else max(0.0, now - self._follow_t)
        self._follow_t = now
        lo, hi = cfg["min"] / 100.0, cfg["max"] / 100.0
        for sid, c in self.c.items():
            if sid in self.zone_of:                             # a zone's screens keep their zone's level
                continue
            b = self._room(sid)
            if b is None:
                continue
            target = lo + (hi - lo) * max(0.0, min(1.0, b))
            cur = self.follow_now.get(sid)
            if cur is None:
                cur = target                                    # as the art showed it a moment ago
            else:
                cur += (target - cur) * (1.0 - math.exp(-dt / cfg.get("smooth_s", self.SMOOTH_S)))
            self.follow_now[sid] = cur
            last = self._sent.get(sid)
            if last is None or abs(cur - last) >= self.SEND_STEP:
                self._sent[sid] = cur
                c.set_level(round(cur, 4))

    async def _follow_loop(self) -> None:
        while self._following():
            self.follow_step(self._clock())
            await asyncio.sleep(self.FOLLOW_STEP_S)

    def _start_follow(self) -> None:
        self.follow_now.clear()
        self._sent.clear()
        self._follow_t = None
        if self._task is not None and not self._task.done():
            return
        try:
            self._task = spawn(self._follow_loop(), "brightness-follow")
        except RuntimeError:                              # no loop (tests): follow_step is called by hand
            self._task = None

    def _stop_follow(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.follow_now.clear()
        self._sent.clear()

    # -- show_service's lights ---------------------------------------------------------------------------------------
    async def _rig_http(self, method: str, body: Any = None) -> tuple[int, Any]:
        """(status, JSON body or None) for RIG_LEVEL on show_service. Raises on connection errors and timeouts."""
        url = str(getattr(self.rig, "url", "") or smconfig.url("show")).rstrip("/") + RIG_LEVEL
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0)) as s:
            async with s.request(method, url, json=body) as r:
                try:
                    data = await r.json(content_type=None)
                except ValueError:
                    data = None
                return r.status, data

    @staticmethod
    def _level(body: Any) -> float | None:
        v = body.get("lamps") if isinstance(body, dict) else None
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    async def send_rig(self) -> Outcome:
        """Put brightness.lamps on show_service's lights now (PUT RIG_LEVEL); ok when it says it holds it."""
        want = self.values["lamps"] / 100.0
        try:
            status, body = await self._rig_http("PUT", {"lamps": want})
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            return Outcome("unconfirmed", f"show_service unreachable ({type(e).__name__}): its lights not dimmed yet")
        if status == 404:
            return Outcome("unconfirmed", "show_service can't dim its lights yet (no brightness route)", retry=False)
        if status == 503:
            return Outcome("unconfirmed", "show_service has no lights running", retry=False)
        if status == 400:
            return Outcome("failed", f"show_service refused the level ({(body or {}).get('error') or 'bad_level'})",
                           retry=False)
        got = self._level(body)
        if status == 200 and got is not None and abs(got - want) <= 0.005:
            return Outcome("ok")
        return Outcome("unconfirmed", f"show_service answered {status} ({got!r})")

    async def keep_in_step(self) -> None:
        """For the service's life (service.py runs it): while show_service has lights, its rig level follows
        brightness.lamps: checked every RIG_CHECK_S, at once after a change, sent when it differs."""
        self._nudge = asyncio.Event()
        while True:
            self._nudge.clear()
            if self.rig_lights():
                try:
                    status, body = await self._rig_http("GET")
                    got = self._level(body) if status == 200 else None
                    if got is None or abs(got - self.values["lamps"] / 100.0) > 0.005:
                        o = await self.send_rig()
                        why = None if o.status == "ok" else o.detail
                    else:
                        why = None
                except asyncio.CancelledError:
                    raise
                except Exception as e:           # unreachable or a bad answer: tried again next round
                    why = f"show_service unreachable ({type(e).__name__})"
                if why and why != self._said:
                    log.warning("brightness.lamps: %s", why)
                self._said = why
            try:
                await asyncio.wait_for(self._nudge.wait(), self.RIG_CHECK_S)
            except asyncio.TimeoutError:
                pass

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        ms = [m for m in desired if m in self.values]
        moved = [m for m in ms if self.values[m] != desired[m] or (m == "screens" and isinstance(desired[m], dict)
                                                                  and self._task is None)]
        for m in ms:
            self.values[m] = desired[m]
        _save(self.path, dict(self.values))
        self._push([m for m in ms if m in moved or not (m == "screens" and self._following())])
        out = {m: Outcome("ok") for m in ms}
        if "lamps" in out:
            out["lamps"] = await self._lamps_outcome()
        worst = "unconfirmed" if any(o.status != "ok" for o in out.values()) else "ok"
        if any(o.status == "failed" for o in out.values()):
            worst = "failed"
        return Outcome(worst, "", out, retry=False)

    async def _lamps_outcome(self) -> Outcome:
        if not self.has_lamps():
            return Outcome("unconfirmed", NO_LAMPS, retry=False)
        parts: list[Outcome] = []
        hue = getattr(self.hue, "patch", None)
        if hue is not None and hue.hue.get("streamer", "room") == "show":
            parts.append(Outcome("unconfirmed", "show_service streams the lamps (hue.streamer: show): not dimmed",
                                 retry=False))
        if self.rig_lights():
            parts.append(await self.send_rig())
        bad = [o for o in parts if o.status != "ok"]
        if not bad:
            return Outcome("ok")
        status = "failed" if any(o.status == "failed" for o in bad) else "unconfirmed"
        return Outcome(status, "; ".join(o.detail for o in bad if o.detail), retry=False)


SHOW_FILE = smconfig.shared("room", "show.yaml")              # the shared tuning; the site's layer goes over it
LIGHT_SCENES_FILE = smconfig.shared("live", "light_scenes.yaml")


ANY_SCENES = {"scene_tags": []}


def _site_show_file() -> Path | None:
    """This deployment's show.yaml layer (deployments/<id>/show.yaml), or None when none is selected or it has none."""
    try:
        p = smconfig.path("show.yaml")
    except Exception:
        return None
    return p if p.is_file() else None


class DirectorAdapter(Adapter):
    """director.<appliance> and director.light_scenes: what a mode lets the art director do with the lights (owner,
    28 Oct: "disco mode limits the available lighting scenes to being those involving the disco ball (much as e.g.
    ambient limits the available screen scenes to those with tag 'ambient')").

    - director.<appliance> (true | false): the director may use it (show.yaml lights.appliances: the glitterball,
      the pinspots...); false (the default) leaves it to the room.
    - director.light_scenes ({"scene_tags": [...]}): the light scenes it may pick from, by tag (a word in any facet,
      or facet:word, as screens' scene_tags); a light group none of them suits keeps all its scenes. No tags: any.
      Never null, so a mode's put-back has a value to put back (null reads as nothing stored).

    The room only keeps the values (in the state dir); show_service reads them from the room's status and does the
    rest (director.py, appliances.py). Members come from show.yaml at start (the shared file and this deployment's
    layer, as show_service reads it: a site's appliances are in its layer)."""

    subsystem = "director"
    subsystem_label = "Art director"
    category = "setting"
    kind = "level"
    poll_s = 5.0
    SCENES = "light_scenes"

    def __init__(self, state_dir: str | Path, show_file: Path | None = None,
                 scenes_file: Path = LIGHT_SCENES_FILE, site_file: Path | None = None) -> None:
        """show_file None: show_service's own settings, the shared show.yaml with this deployment's layer over it
        (deployments/<id>/show.yaml, where a site's appliances are). A show_file given (tests) is read alone, with
        site_file over it when given."""
        self.path = Path(state_dir) / "director.json"
        self.scenes_file = scenes_file
        if show_file is None:
            show_file, site_file = SHOW_FILE, _site_show_file()
        self.apps = self._appliances(show_file, site_file)
        self.members = [self.SCENES, *self.apps]
        self.values: dict[str, Any] = {self.SCENES: dict(ANY_SCENES), **{a: False for a in self.apps}}

    @staticmethod
    def _appliances(path: Path, site: Path | None = None) -> dict[str, str]:
        try:
            doc = smconfig.load_layered_yaml(path, site if site is not None and Path(site).is_file() else None)
            apps = (doc.get("lights") or {}).get("appliances") or {}
        except Exception:
            log.exception("director: show.yaml's lights.appliances unreadable: no appliance rows")
            return {}
        return {str(k): str((v or {}).get("label") or k) for k, v in apps.items() if isinstance(v, dict)}

    def _tag_list(self) -> list[dict]:
        """Every tag word on the light scenes ([{tag, facet, count}], as the screens' scene tag list)."""
        try:
            import yaml
            scenes = (yaml.safe_load(self.scenes_file.read_text()) or {}).get("scenes") or {}
        except Exception:
            return []
        counts: dict[tuple[str, str], int] = {}
        for sc in scenes.values():
            for facet, v in ((sc or {}).get("tags") or {}).items():
                for w in [v] if isinstance(v, str) else v if isinstance(v, list) else []:
                    if isinstance(w, str) and w:
                        counts[(str(facet), w)] = counts.get((str(facet), w), 0) + 1
        return [{"tag": w, "facet": f, "count": n} for (f, w), n in sorted(counts.items())]

    def restore(self) -> None:
        saved = _load(self.path)
        for m in self.members:
            if m in saved:
                try:
                    self.values[m] = self.normalize(m, saved[m])
                except ValueError:
                    pass

    def label(self, member: str) -> str:
        if member == self.SCENES:
            return "Light scenes"
        return f"Uses the {self.apps.get(member, member).lower()}"

    def value_schema(self, member: str) -> dict:
        if member == self.SCENES:
            return {"type": "object", "default": dict(ANY_SCENES),
                    "x-ui": {"label": "Light scenes", "unset_label": "Any light scene"},
                    "properties": {"scene_tags": {
                        "type": "array", "uniqueItems": True, "items": {"type": "string"},
                        "x-ui": {"label": "Only use these light scenes", "widget": "tags", "tags": self._tag_list(),
                                 "help": "The art director only picks light scenes with any of these tags, for the "
                                         "lights that have some."}}}}
        return {"type": "boolean", "default": False, "x-ui": {
            "label": self.label(member), "labels": {"true": "The art director uses it", "false": "Left to the room"}}}

    def normalize(self, member: str, value: Any) -> Any:
        if member == self.SCENES:
            if value is None or value == {}:
                return dict(ANY_SCENES)
            tags = value.get("scene_tags") if isinstance(value, dict) else None
            if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags) or set(value) - {
                    "scene_tags"}:
                raise ValueError('light scenes: {"scene_tags": [tag, ...]}')
            return {"scene_tags": list(dict.fromkeys(tags))}
        if not isinstance(value, bool):
            raise ValueError(f"{member}: true or false, got {value!r}")
        return value

    def words(self, member: str, value: Any) -> str:
        if member == self.SCENES:
            tags = (value or {}).get("scene_tags") or []
            return "Any light scene" if not tags else "Only light scenes tagged " + ", ".join(tags)
        return "The art director uses it" if value else "Left to the room"

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {m: Reading(self.values[m]) for m in members if m in self.values}

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        ms = [m for m in desired if m in self.values]
        for m in ms:
            self.values[m] = desired[m]
        _save(self.path, dict(self.values))
        return Outcome("ok", "", {m: Outcome("ok") for m in ms})


class LogAdapter(Adapter):
    """Signals log.info / log.warning / log.error with {"msg"}: a line in the room log."""

    subsystem = "log"
    kind = "action"
    members: list[str] = []
    actions = {lvl: {"label": f"Log ({lvl})", "schema": {"type": "object", "properties": {
        "msg": {"type": "string", "default": "", "x-ui": {"label": "Message"}}}}} for lvl in ("info", "warning", "error")}

    def __init__(self, sink: Callable[[str, str], None]) -> None:
        self.sink = sink

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        if action not in self.actions:
            return Outcome("failed", f"log has no action {action!r}", retry=False)
        self.sink(action, str(args.get("msg", "")))
        return Outcome("ok")
