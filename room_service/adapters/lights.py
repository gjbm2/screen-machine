"""The `light` subsystem: the room's DMX lights, one member per light registered with show_service that isn't a Hue lamp
(kind "lamp" on an Art-Net output: config/room/fixtures.yaml plus the Lights setup page's fixtures.added.yaml), as
`light.<fixture id>`. show_service drives them (Art-Net, unicast to each node); the room only says which of them
follow the music. Hue lamps stay the `hue` subsystem's (adapters/hue.py).

Values (canonical):
  "dmx"                             follows the music: show_service's director colours it ("show" is an alias)
  "off"                             dark
  {"hex": "#rrggbb", "bri": 0-100}  a steady colour, show_service's house state for it ({"on": false} is "off";
                                    bri defaults to 100)

* **members** come from show_service's GET /api/v1/rig (its per-fixture rows, or the registry view's fixtures when it
  has no rows). watch_members() re-reads it every REFRESH_S, sooner when a follow answer carries a rig revision this
  adapter hasn't seen, and keeps the list in the state dir, so the lights are there at start while show_service is
  down. A change calls room_service back, which rebuilds the room config (modes, rooms.json groups, the page) without
  moving anything. `dynamic_members` tells the room config that a light a mode names but show_service doesn't have
  (not registered yet, or removed) names nothing: never an error that would stop modes.json loading.
* **read** is GET /api/v1/rig/follow ({fixtures: {id: value}}); a light missing from it reads unknown. Raises when
  show_service can't be reached or doesn't answer 200 (the belief goes stale).
* **apply** POSTs the complete mapping (show_service's current one, with ours over it) to POST /api/v1/rig/follow
  {fixtures: {...}}, then reads it back; when its current mapping can't be read, nothing is sent (a partial mapping
  could drop another light's value). show_service unreachable, without the route yet, or a 5xx: the members are
  `unconfirmed`, never `failed` (as the hue adapter in show mode). A 400 is failed and never retried; so is a light
  show_service doesn't have any more.
* **signal** `light.<member>.identify` {seconds?}: POST /api/v1/rig/fixtures/{id}/identify, full white for a few
  seconds whatever it was doing (show_service's default length when no seconds are given).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import aiohttp

from ..hue.colour import describe, hex_to_rgb, rgb_to_xy
from ..roomstate.contract import Adapter, Outcome, Reading
from .room import _load, _save

log = logging.getLogger("room_service.adapters.lights")

SHOW_URL = "http://127.0.0.1:8703"   # show_service's API over loopback (as adapters/show.py): a default
RIG = "/api/v1/rig"
FOLLOW = "/api/v1/rig/follow"
REFRESH_S = 10.0                     # members: show_service's rig is re-read this often
DRIVERS = ("artnet",)                # outputs whose lamps are this subsystem's (contract C); Hue lamps are `hue`'s
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ID_RE = re.compile(r"^[^\s*?\[\]]+$")  # a fixture id usable in a room selector (no whitespace, no wildcard)
WORDS = {"dmx": "Follows the music", "off": "Off"}
STATUSES = ("ok", "unconfirmed", "failed")   # best to worst (contract rule 4)


class ShowUnreachable(ConnectionError):
    """show_service can't be reached, or didn't answer 200: the belief goes stale."""


def rig_lights(body: Any) -> dict[str, dict]:
    """{fixture id: its row} for every light of this subsystem in a GET /api/v1/rig answer, in show_service's order:
    a fixture of kind "lamp" on an output whose driver is in DRIVERS. Rows are the answer's "fixtures" (a list of rows,
    or an object by id), else its registry view's; a row's driver is its own, else its output's. Pure."""
    if not isinstance(body, dict):
        return {}
    view = body.get("registry_view") if isinstance(body.get("registry_view"), dict) else {}
    rows = body.get("fixtures") if body.get("fixtures") is not None else view.get("fixtures")
    if isinstance(rows, dict):
        rows = [{"id": k, **v} for k, v in rows.items() if isinstance(v, dict)]
    if not isinstance(rows, list):
        return {}
    outputs: dict[str, dict] = {}
    for src in (view.get("outputs"), body.get("outputs")):
        if isinstance(src, dict):
            outputs.update({k: v for k, v in src.items() if isinstance(v, dict)})
    out: dict[str, dict] = {}
    for r in rows:
        fid = r.get("id") if isinstance(r, dict) else None
        if not isinstance(fid, str) or not ID_RE.match(fid):
            continue
        driver = r.get("driver") or (outputs.get(str(r.get("output"))) or {}).get("driver")
        if r.get("kind") == "lamp" and driver in DRIVERS:
            out[fid] = r
    return out


def rig_revision(body: Any) -> str | None:
    """The rig registry's revision in a GET /api/v1/rig answer (None when it has none)."""
    if not isinstance(body, dict):
        return None
    for src in (body.get("registry"), body.get("registry_view")):
        if isinstance(src, dict) and isinstance(src.get("revision"), str):
            return src["revision"]
    return None


def follow_map(body: Any) -> dict[str, Any]:
    """GET /api/v1/rig/follow's answer -> {fixture id: value as show_service holds it}. ValueError when it isn't one."""
    if not isinstance(body, dict) or not isinstance(body.get("fixtures"), dict):
        raise ValueError("show_service's /api/v1/rig/follow answer has no fixtures object")
    return {str(k): v for k, v in body["fixtures"].items()}


def _why(e: BaseException) -> str:
    return "timeout" if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else (str(e) or type(e).__name__)


def _refusal(body: Any) -> str:
    if not isinstance(body, dict):
        return "show_service refused it"
    return "; ".join(str(x) for x in (body.get("error") or "refused", body.get("detail") or body.get("why")) if x)


class LightsAdapter(Adapter):
    subsystem = "light"
    subsystem_label = "Lights"
    kind = "level"
    change_time_s = 2.0
    poll_s = 5.0
    read_timeout_s = 2.0
    dynamic_members = True        # model.py: a light named but not registered names nothing (never a config error)
    actions = {"identify": {"label": "Identify (full white for a few seconds)",
                            "schema": {"type": "object", "properties": {
                                "seconds": {"type": "number", "minimum": 0.5, "maximum": 30,
                                            "x-ui": {"label": "For", "unit": "s"}}}}}}

    def __init__(self, url: str = SHOW_URL, cache_file: str | Path | None = None,
                 refresh_s: float = REFRESH_S) -> None:
        """cache_file: where the last good list of lights is kept (the room's state dir), so the members are there
        at start while show_service is down. None: memory only."""
        self.url = url.rstrip("/")
        self.cache_file = Path(cache_file) if cache_file else None
        self.refresh_s = refresh_s
        self.members: list[str] = []
        self.labels: dict[str, str] = {}
        self.revision: str | None = None        # the rig registry's revision when last read
        self._lock = asyncio.Lock()
        self._nudge: asyncio.Event | None = None
        try:
            saved = _load(self.cache_file) if self.cache_file else {}
        except OSError as e:                     # unreadable: start without it (never stop the room starting)
            log.warning("light: cache not read: %s", e)
            saved = {}
        lights = saved.get("lights") if isinstance(saved, dict) else None
        for x in lights if isinstance(lights, list) else []:
            if isinstance(x, dict) and isinstance(x.get("id"), str) and ID_RE.match(x["id"]):
                self.members.append(x["id"])
                self.labels[x["id"]] = str(x.get("label") or x["id"])
        if isinstance(saved, dict) and isinstance(saved.get("revision"), str):
            self.revision = saved["revision"]

    # -- members ---------------------------------------------------------------------------------------------------------
    async def refresh_members(self) -> bool:
        """Re-read show_service's rig; True when the list of lights changed (labels alone don't count: describe() reads
        them live). Raises ShowUnreachable (or ValueError on an answer that isn't a rig) and keeps the list it had."""
        body = await self._get(RIG)
        if isinstance(body, dict) and body.get("enabled") is False:
            raise ValueError(f"show_service's rig isn't running ({body.get('why') or 'no reason given'})")
        lights = rig_lights(body)
        self.revision = rig_revision(body) or self.revision
        labels = {fid: str(r.get("label") or fid) for fid, r in lights.items()}
        changed = list(lights) != self.members
        if changed or labels != self.labels:
            self.members, self.labels = list(lights), labels
            if self.cache_file is not None:
                try:
                    _save(self.cache_file, {"revision": self.revision,
                                            "lights": [{"id": m, "label": labels[m]} for m in self.members]})
                except OSError as e:
                    log.warning("light: cache not saved: %s", e)
        return changed

    async def watch_members(self, on_change: Callable[[str], None] | None = None) -> None:
        """Keep the members in step with show_service (room_service runs this for the service's life): every
        refresh_s, or at once when a read saw a new rig revision. on_change(subsystem) after the list changed."""
        self._nudge = asyncio.Event()
        said: str | None = None
        pending = False                          # a change the room hasn't taken yet (its callback raised)
        while True:
            self._nudge.clear()                  # a nudge from here on (a read during the refresh) is kept
            try:
                if await self.refresh_members():
                    log.info("light: members now %s", ", ".join(self.members) or "none")
                    pending = True
                said = None
            except asyncio.CancelledError:
                raise
            except Exception as e:               # unreachable or a bad answer: the list stays as it was
                if str(e) != said:               # said once per reason
                    said = str(e)
                    log.warning("light: members not refreshed: %s", e)
            if pending and on_change is not None:
                try:
                    on_change(self.subsystem)
                    pending = False
                except Exception as e:           # a bug on the room's side: tried again next round
                    log.error("light: the room didn't take the new members: %s", e)
            try:
                await asyncio.wait_for(self._nudge.wait(), self.refresh_s)
            except asyncio.TimeoutError:
                pass

    # -- the editor ------------------------------------------------------------------------------------------------------
    def label(self, member: str) -> str:
        return self.labels.get(member, member)

    def value_schema(self, member: str) -> dict:
        """For the Room page's schema-driven editor; normalize() stays the real check (check_value ignores oneOf)."""
        return {
            "oneOf": [
                {"const": "dmx", "x-ui": {"label": WORDS["dmx"]}},
                {"const": "off", "x-ui": {"label": WORDS["off"]}},
                {"type": "object", "x-ui": {"label": "On"}, "additionalProperties": False, "required": ["hex"],
                 "default": {"hex": "#ffffff", "bri": 100},     # the form's starting point: full white
                 "properties": {
                     "hex": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$",
                             "x-ui": {"label": "Colour", "widget": "color"}},
                     "bri": {"type": "integer", "minimum": 0, "maximum": 100, "default": 100,
                             "x-ui": {"label": "Brightness", "unit": "%", "widget": "slider"}},
                 }},
            ],
            "x-ui": {"label": self.label(member), "labels": dict(WORDS)},
        }

    def normalize(self, member: str, value: Any) -> Any:
        if value == "show":                         # as the hue adapter: an alias of "dmx"
            return "dmx"
        if value in ("dmx", "off"):
            return value
        if not isinstance(value, dict):
            raise ValueError(f"light value: 'dmx', 'off' or {{hex, bri}}, got {value!r}")
        if value.get("on") is False:
            return "off"
        if extra := sorted(map(str, set(value) - {"hex", "bri", "on"})):
            raise ValueError(f"light value: unknown keys {extra}; a steady colour is {{hex, bri}}")
        h = value.get("hex")
        if not isinstance(h, str) or not HEX_RE.match(h):
            raise ValueError("light hex: '#rrggbb'")
        bri = value.get("bri", 100)
        if not isinstance(bri, (int, float)) or isinstance(bri, bool) or not 0 <= bri <= 100:
            raise ValueError("light bri: 0-100")
        return {"hex": h.lower(), "bri": round(bri)}

    def words(self, member: str, value: Any) -> str:
        if isinstance(value, dict) and "hex" in value:
            colour = describe({"xy": list(rgb_to_xy(*hex_to_rgb(value["hex"])))})
            return f"On, {value.get('bri', 100)}%" + (f", {colour}" if colour else "")
        return WORDS.get(value, str(value)) if isinstance(value, str) else super().words(member, value)

    def show(self, member: str, value: Any) -> str:
        if isinstance(value, dict) and "hex" in value:
            return f"on {value.get('bri', 100)}% {value['hex']}"
        return str(value)

    # -- show_service ----------------------------------------------------------------------------------------------------
    async def _http(self, method: str, path: str, body: Any = None, timeout: float | None = None) -> tuple[int, Any]:
        """(status, JSON body or None). A short-lived session per call (as adapters/show.py): a few calls a minute over
        loopback, and nothing left open on a loop that has gone. Raises on connection errors and timeouts."""
        t = aiohttp.ClientTimeout(total=timeout or self.read_timeout_s)
        async with aiohttp.ClientSession(timeout=t) as s:
            async with s.request(method, self.url + path, json=body) as r:
                try:
                    data = await r.json(content_type=None)
                except ValueError:
                    data = None
                return r.status, data

    async def _get(self, path: str) -> Any:
        try:
            status, body = await self._http("GET", path)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            raise ShowUnreachable(f"show_service unreachable ({_why(e)})") from None
        if status != 200:
            raise ShowUnreachable(f"show_service answered {status} to GET {path}")
        return body

    def _canon(self, member: str, raw: Any) -> Any:
        """A value as show_service holds it, canonical; None (unknown) when it is missing or not one of ours."""
        if raw is None:
            return None
        try:
            return self.normalize(member, raw)
        except ValueError:
            return None

    def _seen(self, body: Any) -> None:
        """A follow answer that names a rig revision this adapter hasn't seen: re-read the members now."""
        rev = body.get("revision") if isinstance(body, dict) else None
        if isinstance(rev, str) and rev != self.revision and self._nudge is not None:
            self._nudge.set()

    async def read(self, members: list[str]) -> dict[str, Reading]:
        body = await self._get(FOLLOW)
        mapping = follow_map(body)                  # ValueError on a malformed answer: stale, like unreachable
        self._seen(body)
        return {m: Reading(self._canon(m, mapping.get(m))) for m in members if m in self.members}

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        if action != "identify" or member not in self.members:
            return Outcome("failed", f"light has no action {action!r} for {member!r}")
        seconds = (args or {}).get("seconds")
        body = {"seconds": seconds} if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else {}
        try:
            status, got = await self._http("POST", f"/api/v1/rig/fixtures/{quote(member, safe='')}/identify", body)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            return Outcome("unconfirmed", f"show_service unreachable ({_why(e)})")
        if status == 200 and isinstance(got, dict) and got.get("ok"):
            return Outcome("ok")
        why = got.get("why") if isinstance(got, dict) and got.get("why") else _refusal(got) if status >= 400 else ""
        return Outcome("failed", why or f"show_service answered {status}", retry=False)

    # -- apply -----------------------------------------------------------------------------------------------------------
    @staticmethod
    def _worst(results: dict[str, Outcome]) -> Outcome:
        worst = max((o.status for o in results.values()), key=STATUSES.index, default="ok")
        return Outcome(worst, members=results, retry=all(o.retry for o in results.values()))

    async def apply(self, desired: dict[str, Any], opts: dict) -> Outcome:
        async with self._lock:
            return await self._apply(desired)

    async def _apply(self, desired: dict[str, Any]) -> Outcome:
        results = {m: Outcome("failed", f"{m} isn't a registered light any more", retry=False)
                   for m in desired if m not in self.members}
        ours = {m: v for m, v in desired.items() if m in self.members}
        if not ours:
            return self._worst(results)

        def every(status: str, detail: str, retry: bool = True) -> Outcome:
            results.update({m: Outcome(status, detail, retry=retry) for m in ours})
            return self._worst(results)
        try:                                        # the complete mapping: theirs, ours over it (nothing dropped)
            base = follow_map(await self._get(FOLLOW))
        except (ShowUnreachable, ValueError) as e:  # never send a mapping that could drop another light's value
            return every("unconfirmed", str(e))
        mapping = {**base, **ours}
        try:
            status, body = await self._http("POST", FOLLOW, {"fixtures": mapping})
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            return every("unconfirmed", f"show_service unreachable ({_why(e)})")
        if status == 400:                           # a body it refuses on purpose: never retried
            return every("failed", _refusal(body), retry=False)
        if not 200 <= status < 300:                 # 404 (no route yet), 5xx, starting: retried
            return every("unconfirmed", f"show_service answered {status}")
        try:
            now = follow_map(await self._get(FOLLOW))
        except (ShowUnreachable, ValueError) as e:
            return every("unconfirmed", f"sent; not read back ({e})")
        for m, v in ours.items():
            got = self._canon(m, now.get(m))
            results[m] = Outcome("ok") if got == v else Outcome("unconfirmed", f"reads {got!r}")
        return self._worst(results)
