"""Adapters for state that lives in room_service itself: sensor_mask.main, page.<screen>.timed_reload,
diag.<screen> (the live-details panel), brightness.screens / brightness.lamps, director.*, and log signals.

Both level adapters persist what they applied in their own file under the state directory and restore it at start
(ROOM_STATE_MODEL §5.2 rule 10), so read() stays truthful after a restart: without that, a restart mid-show would let
the art mask follow the show-lit sensor again.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..mask import parse_mask
from ..roomstate.contract import Adapter, Outcome, Reading
from ..roomstate.schema import enum_schema, range_schema
from ..util import spawn, wait_until

if TYPE_CHECKING:
    from ..screens import ScreenController


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
    held value per screen too, so after a restart while suspended the TV pages' welcome carries what they showed."""

    subsystem = "sensor_mask"
    subsystem_label = "Art brightness"
    category = "setting"
    kind = "level"
    members = ["main"]
    poll_s = 1.0
    VALUES = ("follow", "suspended")

    def __init__(self, mask: Any, state_dir: str | Path) -> None:
        self.mask = mask
        self.path = Path(state_dir) / "sensor_mask.json"

    def restore(self) -> None:
        saved = _load(self.path)
        suspended = saved.get("suspended")
        if not isinstance(suspended, bool):
            return
        if suspended:
            self._seed(saved.get("held"))
        self.mask.set_suspended(suspended)

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
        s = enum_schema(list(self.VALUES), "Art brightness",
                        {"follow": "Follows the room's light", "suspended": "Stays as it was"})
        s["x-ui"]["advanced"] = True
        return s

    def normalize(self, member: str, value: Any) -> Any:
        if value not in self.VALUES:
            raise ValueError(f"{value!r} is not one of {list(self.VALUES)}")
        return value

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {"main": Reading("suspended" if self.mask.suspended else "follow")}

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        suspended = desired["main"] == "suspended"
        self.mask.set_suspended(suspended)
        pushed = getattr(self.mask, "pushed", None) or {}
        src = getattr(self.mask, "pushed_source", None) or {}
        _save(self.path, {"suspended": suspended,
                          "held": {sid: {**v.as_dict(), "source": src.get(sid)} for sid, v in pushed.items()}})
        return Outcome("ok", "", {"main": Outcome("ok")})


class PageAdapter(Adapter):
    """page.<screen>.timed_reload: true | false, sent as `policy` on the screen's control socket.

    Every page is told at once and the waits for their reports overlap (a page reports kiosk.state every 5 s). The
    policy goes out on the socket that the screen's own freeze and live.attach follow, so the coordinator proves it
    behind the step (prove_behind): a rule's screens step starts at once, never waiting for a page's report."""

    subsystem = "page"
    subsystem_label = "TV pages"
    category = "setting"
    kind = "level"
    change_time_s = 10.0
    poll_s = 5.0
    prove_behind = True

    def __init__(self, controllers: dict[str, "ScreenController"], state_dir: str | Path, timeout_s: float = 10.0) -> None:
        self.c = controllers
        self.members = [f"{sid}.timed_reload" for sid in controllers]
        self.path = Path(state_dir) / "page.json"
        self.timeout_s = timeout_s

    def restore(self) -> None:
        for sid, value in _load(self.path).items():
            if sid in self.c and isinstance(value, bool) and self.c[sid].timed_reload != value:
                self.c[sid].set_timed_reload(value)            # the next welcome carries it

    def _sid(self, member: str) -> str:
        return member.rsplit(".", 1)[0]

    def value_schema(self, member: str) -> dict:
        return {"type": "boolean", "default": True,
                "x-ui": {"label": "Refresh the TV pages now and then", "advanced": True,
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
            return Outcome("ok", "kept; the TV page gets it when it opens")
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

    def __init__(self, controllers: dict[str, "ScreenController"], state_dir: str | Path) -> None:
        self.c = controllers
        self.members = list(controllers)
        self.path = Path(state_dir) / "diag.json"
        self.wanted: dict[str, bool] = {}               # what we set, while a controller can't take it yet

    def restore(self) -> None:
        for sid, value in _load(self.path).items():
            if sid in self.c and isinstance(value, bool):
                self.wanted[sid] = value
                if callable(getattr(self.c[sid], "set_hud", None)):
                    self.c[sid].set_hud(value)          # the next live session carries it

    def label(self, member: str) -> str:
        return member

    def value_schema(self, member: str) -> dict:
        return {"type": "boolean", "default": False,
                "x-ui": {"label": "Show details on the TV", "labels": {"true": "On", "false": "Off"}}}

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


class BrightnessAdapter(Adapter):
    """brightness.screens and brightness.lamps, 0-100 %: how bright the live screens and the lamps that follow the
    music are, for a mode whose star is something else (Disco: the glitterball in the pinspots; owner, 28 Oct). A gain
    on top of whatever the show does: screens through the renderer (ScreenController.set_level, a session's
    `level`), lamps through the room's Hue stream (HueOutput.level). Both ease over ~2 s. Kept in the state dir, so
    a restart keeps a dimmed room dimmed.

    brightness.screens may instead follow the room's light (owner, 29 Oct: "for ambient mode, we can take light sensor
    input as a dimming control for the screens ... smoothed because otherwise the screens own light intensity risk
    causing weird feedback loops in a darkened room"): {"follow": "sensor", "min": %, "max": %}. Each screen's input is
    the brightness the art's mask has from the room's light (MaskArbiter.room_brightness: the same sensor and mapping,
    so live and art agree), mapped onto min..max and smoothed: an exponential ease with RISE_TAU_S going up and
    FALL_TAU_S going down, sent when it has moved SEND_STEP. The first reading after it starts following is taken
    as it is (the art showed that a moment ago, so there is no jump)."""

    subsystem = "brightness"
    subsystem_label = "Brightness"
    category = "setting"
    kind = "level"
    members = ["screens", "lamps"]
    poll_s = 5.0
    LABELS = {"screens": "Screens (live)", "lamps": "Lamps (following the music)"}
    FOLLOW = "sensor"
    FOLLOW_STEP_S = 2.0         # how often a following screen reads the room's light
    RISE_TAU_S = 180.0          # starting points: brightening is slow (the screens' own light reaches the sensor) ...
    FALL_TAU_S = 45.0           # ... dimming quicker (the room's lights going down)
    SEND_STEP = 0.01            # a level change under this isn't sent

    def __init__(self, controllers: dict[str, "ScreenController"], hue_output: Any, state_dir: str | Path,
                 mask: Any = None, clock: Callable[[], float] | None = None) -> None:
        self.c = controllers
        self.hue = hue_output
        self.mask = mask
        self.path = Path(state_dir) / "brightness.json"
        self.values: dict[str, Any] = {m: 100 for m in self.members}
        self._clock = clock or (lambda: asyncio.get_running_loop().time())
        self.follow_now: dict[str, float] = {}      # screen -> the smoothed level (0..1) it follows to
        self._sent: dict[str, float] = {}
        self._follow_t: float | None = None
        self._task: asyncio.Task | None = None

    def restore(self) -> None:
        saved = _load(self.path)
        for m in self.members:
            try:
                v = self.normalize(m, saved[m]) if m in saved else None
            except ValueError:
                v = None
            if v is not None:
                self.values[m] = v
        self._push([m for m in self.members if self.values[m] != 100])

    def label(self, member: str) -> str:
        return self.LABELS.get(member, member)

    def value_schema(self, member: str) -> dict:
        fixed = range_schema(0, 100, self.label(member), "%", step=5, default=100, ends=("Dark", "Full"))
        if member != "screens":
            return fixed
        pct = lambda label, d: {**range_schema(0, 100, label, "%", step=5, default=d)}   # noqa: E731
        return {"x-ui": {"label": self.label(member)},
                "oneOf": [{**fixed, "x-ui": {**fixed["x-ui"], "label": "Set"}},
                          {"type": "object", "required": ["follow"], "default": {"follow": self.FOLLOW, "min": 20,
                                                                                 "max": 100},
                           "x-ui": {"label": "Follows the room's light",
                                    "help": "Dim in a dark room, full in daylight, as the art is; it changes slowly."},
                           "properties": {"follow": {"const": self.FOLLOW, "x-ui": {"hidden": True}},
                                          "min": pct("In the dark", 20), "max": pct("In daylight", 100)}}]}

    def normalize(self, member: str, value: Any) -> Any:
        if member == "screens" and isinstance(value, dict):
            if value.get("follow") != self.FOLLOW or set(value) - {"follow", "min", "max"}:
                raise ValueError('brightness.screens follows the room\'s light as {"follow": "sensor", "min": %, '
                                 '"max": %}')
            lo, hi = value.get("min", 20), value.get("max", 100)
            for v in (lo, hi):
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100:
                    raise ValueError(f"brightness is 0-100, got {v!r}")
            lo, hi = sorted((round(lo), round(hi)))
            return {"follow": self.FOLLOW, "min": lo, "max": hi}
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
                for c in self.c.values():
                    c.set_level(self.values["screens"] / 100.0)
        if "lamps" in members and self.hue is not None:
            self.hue.level = self.values["lamps"] / 100.0

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
            b = self._room(sid)
            if b is None:
                continue
            target = lo + (hi - lo) * max(0.0, min(1.0, b))
            cur = self.follow_now.get(sid)
            if cur is None:
                cur = target                                    # as the art showed it a moment ago
            else:
                tau = self.RISE_TAU_S if target > cur else self.FALL_TAU_S
                cur += (target - cur) * (1.0 - math.exp(-dt / tau))
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

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {m: Reading(self.values[m]) for m in members if m in self.values}

    async def apply(self, desired: dict[str, Any], opts: dict | None = None) -> Outcome:
        ms = [m for m in desired if m in self.values]
        moved = [m for m in ms if self.values[m] != desired[m] or (m == "screens" and isinstance(desired[m], dict)
                                                                  and self._task is None)]
        for m in ms:
            self.values[m] = desired[m]
        _save(self.path, dict(self.values))
        self._push([m for m in ms if m in moved or not (m == "screens" and self._following())])
        out = {m: Outcome("ok") for m in ms}
        hue = getattr(self.hue, "patch", None)
        if "lamps" in out and hue is not None and hue.hue.get("streamer", "room") == "show":
            out["lamps"] = Outcome("unconfirmed", "show_service streams the lamps (hue.streamer: show): not dimmed",
                                   retry=False)
        worst = "unconfirmed" if any(o.status != "ok" for o in out.values()) else "ok"
        return Outcome(worst, "", out, retry=False)


SHOW_FILE = Path(__file__).resolve().parents[2] / "config" / "room" / "show.yaml"
LIGHT_SCENES_FILE = Path(__file__).resolve().parents[2] / "config" / "live" / "light_scenes.yaml"


class DirectorAdapter(Adapter):
    """director.<appliance> and director.light_scenes: what a mode lets the art director do with the lights (owner,
    28 Oct: "disco mode limits the available lighting scenes to being those involving the disco ball (much as e.g.
    ambient limits the available screen scenes to those with tag 'ambient')").

    - director.<appliance> (true | false): the director may use it (show.yaml lights.appliances: the glitterball,
      the pinspots...); false (the default) leaves it to the room.
    - director.light_scenes ({"scene_tags": [...]} | null): the light scenes it may pick from, by tag (a word in any
      facet, or facet:word, as screens' scene_tags); a light group none of them suits keeps all its scenes. null: any.

    The room only keeps the values (in the state dir); show_service reads them from the room's status and does the
    rest (director.py, appliances.py). Members come from show.yaml at start."""

    subsystem = "director"
    subsystem_label = "Art director"
    category = "setting"
    kind = "level"
    poll_s = 5.0
    SCENES = "light_scenes"

    def __init__(self, state_dir: str | Path, show_file: Path = SHOW_FILE,
                 scenes_file: Path = LIGHT_SCENES_FILE) -> None:
        self.path = Path(state_dir) / "director.json"
        self.scenes_file = scenes_file
        self.apps = self._appliances(show_file)
        self.members = [self.SCENES, *self.apps]
        self.values: dict[str, Any] = {self.SCENES: None, **{a: False for a in self.apps}}

    @staticmethod
    def _appliances(path: Path) -> dict[str, str]:
        try:
            import yaml
            apps = ((yaml.safe_load(path.read_text()) or {}).get("lights") or {}).get("appliances") or {}
        except Exception:
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
            return {"type": ["object", "null"], "x-ui": {"label": "Light scenes", "unset_label": "Any light scene"},
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
                return None
            tags = value.get("scene_tags") if isinstance(value, dict) else None
            if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags) or set(value) - {
                    "scene_tags"}:
                raise ValueError('light scenes: {"scene_tags": [tag, ...]} or null')
            return {"scene_tags": list(dict.fromkeys(tags))} if tags else None
        if not isinstance(value, bool):
            raise ValueError(f"{member}: true or false, got {value!r}")
        return value

    def words(self, member: str, value: Any) -> str:
        if member == self.SCENES:
            return "Any light scene" if not value else "Only light scenes tagged " + ", ".join(value["scene_tags"])
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
