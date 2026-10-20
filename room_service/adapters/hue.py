"""The `hue` subsystem (ROOM_STATE_MODEL §5.3/§5.4): one member per patched hue.* virtual device (`hue.ne` ...).

Values (canonical):
  "dmx"                                   follow Art-Net: the device streams in the Entertainment area while Maestro
                                          sends (HueOutput gates on fresh Art-Net, so with no Maestro nothing moves)
  "off"
  {"on": true, "bri": 0-100, "xy": [x, y]}  or  {"on": true, "bri": 0-100, "mirek": 153-500}
                                          a light state: what `store` snapshots and `retrieve` puts back
                                          ({"hex": "#rrggbb"} is accepted and becomes xy; with two colour keys
                                          the fresh one wins: hex > mirek > xy. {on, bri} alone keeps the colour)
  "taken_over"                            read-only: another app holds the light in its own Entertainment stream

* **read** is one GET /light. A light we are streaming (or armed to stream, waiting for Art-Net) reads "dmx".
  A reading within tolerance of the last value we commanded reads as that value, so the bridge's rounding
  (brightness is 0-254 inside) is not drift.
* **apply** sets HueOutput's wanted set for "dmx" members. For state values it first lets the stream stop (if
  nothing else still wants it), waits 1 s for the bridge's own post-entertainment state (r7 §6), PUTs each light
  (dynamics.duration = opts.over_s, default 1 s), and re-asserts once if the bridge overwrote it.
* **takeover**: if another app has the area, "dmx" members fail with retry=False ("taken_over"); never fought.
* **hue.streamer** (patch.yaml, hot-reloaded; SHOW_ENGINE §9.4): `room` (default) streams "dmx" members through
  room_service's HueOutput. `show` hands streaming to show_service (`hue.show_url`, default http://127.0.0.1:8703):
  "dmx" members are sent as POST /api/v1/hue/stream {lights: [the complete set of hue.* device ids that follow the
  show], on: bool}; before a state is written to a lamp that was following, POST /api/v1/hue/release {lights} must
  answer {released: true} (show_service replies once the bridge reports the area inactive plus ~1 s). show_service
  unreachable makes those members `unconfirmed`, never `failed`. Each read re-syncs with GET /api/v1/hue/status
  ({state, lights, boot_id}): first contact adopts its persisted set; a failed POST, a new boot_id or a mismatch
  re-sends ours. "show" is accepted as an alias of "dmx". Rollback:
  flip the switch back. The adapter's REST (house control, readback, identify) is its own client in both modes.
* **signal** `hue.<member>.identify`: the bridge's own identify breathe (not while streaming).
* Writes only when the patch's `hue.writes_enabled` is true; otherwise dry_run (reads work).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from ..hue.colour import describe, hex_to_rgb, rgb_to_xy
from ..roomstate.contract import Adapter, Outcome, Reading

XY_TOL, BRI_TOL, MIREK_TOL = 0.01, 1.5, 3
SHOW_URL = "http://127.0.0.1:8703"
log = logging.getLogger("room_service.adapters.hue")


def _canon_light(li: dict) -> Any:
    if not li.get("on", {}).get("on"):
        return "off"
    bri = round(li.get("dimming", {}).get("brightness", 100.0))
    ct = li.get("color_temperature") or {}
    if ct.get("mirek_valid") and ct.get("mirek") is not None:
        return {"on": True, "bri": bri, "mirek": int(ct["mirek"])}
    xy = (li.get("color") or {}).get("xy")
    if xy:
        return {"on": True, "bri": bri, "xy": [round(xy["x"], 3), round(xy["y"], 3)]}
    return {"on": True, "bri": bri}


def _close(a: Any, b: Any) -> bool:
    """a (a reading) is within tolerance of b (what we commanded)."""
    if a == b:
        return True
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    if not ({"xy", "mirek"} & b.keys()):        # brightness only: the lamp keeps (and reports) its own colour
        return abs(a.get("bri", 0) - b.get("bri", 0)) <= BRI_TOL
    if a.keys() != b.keys():
        return False
    if abs(a.get("bri", 0) - b.get("bri", 0)) > BRI_TOL:
        return False
    if "xy" in a and max(abs(p - q) for p, q in zip(a["xy"], b["xy"])) > XY_TOL:
        return False
    if "mirek" in a and abs(a["mirek"] - b["mirek"]) > MIREK_TOL:
        return False
    return True


class HueAdapter(Adapter):
    subsystem = "hue"
    subsystem_label = "Lamps"
    kind = "level"
    change_time_s = 8.0
    poll_s = 10.0
    poll_inflight_s = 1.0
    read_timeout_s = 5.0
    actions = {"identify": {"label": "Identify (the lamp breathes)", "schema": {"type": "object"}}}

    def __init__(self, output, patch, client=None) -> None:
        self.output = output
        self.client = client or output.client       # REST of its own (house control, readback, identify)
        self.patch = patch                          # DmxStack swaps in the reloaded patch (hue.streamer is live)
        self.show_wanted: set[str] = set()          # hue.* device ids we asked show_service to stream (mode show)
        self.show_state: str | None = None          # show_service's last reported state (mode show)
        self._show_boot: str | None = None          # show_service's boot_id when we last spoke (restart detection)
        self._show_posted = False                   # a stream POST has succeeded since room_service started
        self._show_pending = False                  # our last stream POST failed: re-send it, never adopt theirs
        self._http: aiohttp.ClientSession | None = None
        self.devices = {d.id.split(".", 1)[1]: d for d in patch.of_kind("hue") if d.extra.get("light")}
        self.members = list(self.devices)
        self.dry_run = not bool(patch.hue.get("writes_enabled", False))
        self._commanded: dict[str, Any] = {}
        self.house: dict[str, Any] = {}            # each lamp's last state under house control ("Stop following")
        self.last_light: dict[str, dict] = {}      # the bridge's last light record per member (fixtures view)
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> str:
        return "show" if self.patch.hue.get("streamer", "room") == "show" else "room"

    @property
    def show_url(self) -> str:
        return str(self.patch.hue.get("show_url", SHOW_URL)).rstrip("/")

    async def _show(self, method: str, path: str, body: dict | None = None, timeout_s: float = 5.0) -> dict | None:
        """show_service's Hue API; None when it can't be reached or refuses (never raises)."""
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        try:
            async with self._http.request(method, f"{self.show_url}{path}", json=body,
                                          timeout=aiohttp.ClientTimeout(total=timeout_s)) as r:
                data = await r.json(content_type=None) if r.content_length != 0 else {}
                if r.status >= 400:
                    log.warning("show_service %s %s: %s %s", method, path, r.status, data)
                    return None
                if isinstance(data, dict) and data.get("state"):
                    self.show_state = data["state"]
                return data if isinstance(data, dict) else {}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.info("show_service %s %s unreachable: %s", method, path, e)
            return None

    async def close(self) -> None:
        if self._http is not None and not self._http.closed:
            await self._http.close()
        if self.client is not self.output.client:
            await self.client.close()

    def label(self, member: str) -> str:
        return self.devices[member].label if member in self.devices else member

    WORDS = {"dmx": "Follows the music", "off": "Off", "taken_over": "Taken over by another app"}

    def value_schema(self, member: str) -> dict:
        """For the Room page's schema-driven editor. normalize() stays the real check (check_value ignores oneOf):
        canonical light states are {on, bri, xy} or {on, bri, mirek}; `hex` is an input convenience that
        normalize() turns into xy. "taken_over" is read-only, so it is worded (x-ui.labels) but not offered."""
        return {
            "oneOf": [
                {"const": "dmx", "x-ui": {"label": self.WORDS["dmx"]}},
                {"const": "off", "x-ui": {"label": "Off"}},
                # exclusive: the editor clears the other colour keys when one is set, so the value holds only the
                # control touched last (normalize's hex > mirek > xy order is the fallback for older values)
                {"type": "object", "x-ui": {"label": "On", "exclusive": [["hex", "mirek", "xy"]]},
                 "additionalProperties": False,
                 "default": {"on": True, "bri": 100},       # normalize()'s own default brightness
                 "properties": {
                     "on": {"const": True, "default": True, "x-ui": {"hidden": True}},
                     "bri": {"type": "integer", "minimum": 0, "maximum": 100, "default": 100,
                             "x-ui": {"label": "Brightness", "unit": "%", "widget": "slider"}},
                     "hex": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$",
                             "x-ui": {"label": "Colour", "widget": "color"}},
                     "xy": {"type": "array", "items": {"type": "number", "minimum": 0, "maximum": 1},
                            "minItems": 2, "maxItems": 2, "x-ui": {"label": "Colour (CIE xy)", "advanced": True}},
                     # mirek: 153 is 6500 K (cool), 500 is 2000 K (warm)
                     "mirek": {"type": "integer", "minimum": 153, "maximum": 500,
                               "x-ui": {"label": "White temperature", "widget": "slider",
                                        "ends": {"min": "Cool", "max": "Warm"}}},
                 }},
            ],
            "x-ui": {"label": self.label(member), "labels": dict(self.WORDS)},
        }

    def normalize(self, member: str, value: Any) -> Any:
        if value == "show":                         # SHOW_ENGINE §9.4 alias
            return "dmx"
        if value in ("dmx", "off", "taken_over"):
            return value
        if not isinstance(value, dict):
            raise ValueError(f"hue value: 'dmx', 'off' or {{on, bri, xy|mirek}}, got {value!r}")
        if value.get("on") is False:
            return "off"
        bri = value.get("bri", 100)
        if not isinstance(bri, (int, float)) or isinstance(bri, bool) or not 0 <= bri <= 100:
            raise ValueError("hue bri: 0-100")
        out: dict[str, Any] = {"on": True, "bri": round(bri)}
        # A canonical value never holds two colour keys, so two mean the form kept the old one beside a fresh pick.
        # Precedence: hex (the colour picker) > mirek (the white slider) > xy (advanced, rarely edited).
        if "hex" in value:
            value = {k: v for k, v in value.items() if k != "mirek"} | {"xy": list(rgb_to_xy(*hex_to_rgb(str(value["hex"]))))}
        elif "mirek" in value:
            value = {k: v for k, v in value.items() if k != "xy"}
        if "xy" in value:
            xy = value["xy"]
            if not (isinstance(xy, list) and len(xy) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1
                                                                  for v in xy)):
                raise ValueError("hue xy: [x, y], each 0-1")
            out["xy"] = [round(float(xy[0]), 3), round(float(xy[1]), 3)]
        elif "mirek" in value:
            m = value["mirek"]
            if not isinstance(m, int) or isinstance(m, bool) or not 153 <= m <= 500:
                raise ValueError("hue mirek: integer 153-500")
            out["mirek"] = m
        return out

    def words(self, member: str, value: Any) -> str:
        if isinstance(value, dict):
            colour = describe(value)
            return f"On, {value['bri']}%" + (f", {colour}" if colour else "")
        return self.WORDS.get(value, str(value))

    def show(self, member: str, value: Any) -> str:
        if isinstance(value, dict):
            col = f"xy {value['xy'][0]:.3f},{value['xy'][1]:.3f}" if "xy" in value else (
                f"{round(1e6 / value['mirek'])} K" if "mirek" in value else "")
            return f"on {value['bri']}% {col}".strip()
        return str(value)

    # -- read ---------------------------------------------------------------------------------------------------
    async def _lights(self) -> dict[str, dict]:
        return {li["id"]: li for li in await self.client.get("light")}

    def _reading(self, member: str, li: dict | None) -> Reading:
        dev = self.devices[member]
        if li is None:
            return Reading(None)
        if self.mode == "show":
            if dev.id in self.show_wanted:                  # following the show (streams only while there's sound)
                return Reading("taken_over" if self.show_state == "taken_over" else "dmx")
        else:
            armed = dev.id in self.output.wanted and self.output.state in ("streaming", "waiting_input", "starting")
            if armed:
                return Reading("dmx", moving=self.output.state == "starting")
        if li.get("mode") == "streaming":
            return Reading("taken_over")
        val = _canon_light(li)
        cmd = self._commanded.get(member)
        if cmd is not None and _close(val, cmd):
            val = cmd
        self.house[member] = val
        return Reading(val)

    async def _post_show_set(self) -> dict | None:
        reply = await self._show("POST", "/api/v1/hue/stream",
                                 {"lights": sorted(self.show_wanted), "on": bool(self.show_wanted)})
        self._show_pending = reply is None
        self._show_posted = self._show_posted or reply is not None
        return reply

    async def _sync_show(self) -> None:
        """Keep show_service's set equal to ours across its restarts and ours (c8, P5 review):
        - first contact since room_service started, with nothing posted yet: adopt its (persisted) set, so a room
          restart doesn't blink the lamps;
        - our last POST failed (a mode change while it was down): re-send ours, never adopt its restored set;
        - it restarted (boot_id changed), or its set or state doesn't match what we asked: re-send ours."""
        st = await self._show("GET", "/api/v1/hue/status", timeout_s=2.0)    # also refreshes show_state
        if st is None:
            return
        boot, theirs = st.get("boot_id"), st.get("lights")
        if self._show_pending:
            await self._post_show_set()
        elif not self._show_posted and self._show_boot is None:
            if isinstance(theirs, list):
                self.show_wanted = {str(x) for x in theirs if str(x).split(".", 1)[-1] in self.devices}
            self._show_posted = True                                         # in sync from here on
        elif ((boot is not None and self._show_boot is not None and boot != self._show_boot)
              or (isinstance(theirs, list) and set(theirs) != self.show_wanted)
              or (not isinstance(theirs, list) and self.show_wanted and st.get("state") == "idle")):
            log.info("show_service out of step (boot %s, lights %s, state %s): re-sending %s",
                     boot, theirs, st.get("state"), sorted(self.show_wanted))
            await self._post_show_set()
        if boot is not None:
            self._show_boot = boot

    async def read(self, members: list[str]) -> dict[str, Reading]:
        if self.mode == "show":
            await self._sync_show()
        lights = await self._lights()
        for m, d in self.devices.items():
            if d.extra["light"] in lights:
                self.last_light[m] = lights[d.extra["light"]]
        return {m: self._reading(m, lights.get(self.devices[m].extra["light"])) for m in members if m in self.devices}

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        if action != "identify" or member not in self.devices:
            return Outcome("failed", f"hue has no action {action!r} for {member!r}")
        li = (await self._lights()).get(self.devices[member].extra["light"], {})
        if li.get("mode") == "streaming" or (self.mode == "room" and self.output.is_streaming(self.devices[member].id)):
            return Outcome("failed", "following the show: stop following first", retry=False)
        device = (li.get("owner") or {}).get("rid")
        if not device:
            return Outcome("failed", "light not found on the bridge")
        try:
            await self.client.put("device", device, {"identify": {"action": "identify"}})
        except Exception as e:
            return Outcome("failed", str(e))
        return Outcome("ok")

    # -- apply --------------------------------------------------------------------------------------------------
    async def apply(self, desired: dict[str, Any], opts: dict) -> Outcome:
        async with self._lock:
            return await self._apply(desired, opts)

    @staticmethod
    def _worst(results: dict[str, Outcome]) -> Outcome:
        worst = "ok"
        for o in results.values():
            if o.status == "failed":
                worst = "failed"
            elif o.status == "unconfirmed" and worst == "ok":
                worst = "unconfirmed"
        return Outcome(worst, members=results, retry=all(o.retry for o in results.values()))

    async def _put_states(self, rest: dict[str, Any], opts: dict, results: dict[str, Outcome]) -> None:
        """PUT each lamp's state, then read back after the transition and re-assert once if overwritten."""
        dur = int(1000 * float(opts.get("over_s", 1.0)))
        for m, v in rest.items():
            results[m] = await self._put(m, v, dur)
        await asyncio.sleep(max(1.0, dur / 1000))
        lights = await self._lights()
        for m, v in rest.items():
            if results[m].status != "ok":
                continue
            got = self._reading(m, lights.get(self.devices[m].extra["light"])).value
            if got != v:                           # overwritten (or a slow lamp): re-assert once
                await self._put(m, v, 0)
                await asyncio.sleep(1.0)
                got = self._reading(m, (await self._lights()).get(self.devices[m].extra["light"])).value
                results[m] = Outcome("ok") if got == v else Outcome("unconfirmed", f"reads {got!r}")

    async def _apply_show(self, desired: dict[str, Any], opts: dict) -> Outcome:
        """hue.streamer = show: show_service streams; we ask it to, and wait for its release before writing states."""
        dmx = {self.devices[m].id for m, v in desired.items() if v == "dmx" and m in self.devices}
        rest = {m: v for m, v in desired.items() if v not in ("dmx", "taken_over") and m in self.devices}
        results: dict[str, Outcome] = {m: Outcome("failed", "taken_over is read-only", retry=False)
                                       for m, v in desired.items() if v == "taken_over" and m in self.devices}
        leaving = {self.devices[m].id for m in rest}
        releasing = sorted(leaving & self.show_wanted)
        new = (self.show_wanted - leaving) | dmx
        changed = new != self.show_wanted or bool(dmx)
        self.show_wanted = new
        reply = await self._post_show_set() if changed else {}
        for d in dmx:
            m = d.split(".", 1)[1]
            if reply is None:
                results[m] = Outcome("unconfirmed", "show_service unreachable")
            elif self.show_state == "taken_over":
                results[m] = Outcome("failed", "taken_over: another app holds the Entertainment area", retry=False)
            else:
                results[m] = Outcome("ok", f"following the show ({self.show_state or 'armed'})")
        if rest:
            if releasing:
                rel = await self._show("POST", "/api/v1/hue/release", {"lights": releasing}, timeout_s=20.0)
                if rel and not rel.get("released") and rel.get("why") == "taken_over":
                    for m in rest:                     # another app holds the area: never fight it
                        results[m] = Outcome("failed", "taken_over: another app holds the Entertainment area",
                                             retry=False)
                    return self._worst(results)
                if not (rel and rel.get("released")):
                    lights = await self._lights()      # can't confirm via show_service: ask the bridge
                    if any((lights.get(self.devices[d.split(".", 1)[1]].extra["light"]) or {}).get("mode")
                           == "streaming" for d in releasing):
                        for m in rest:
                            results[m] = Outcome("unconfirmed", "show_service hasn't released the lamps yet")
                        return self._worst(results)
                    await asyncio.sleep(1.0)           # the bridge's own post-entertainment write comes first
            await self._put_states(rest, opts, results)
        return self._worst(results)

    async def _apply(self, desired: dict[str, Any], opts: dict) -> Outcome:
        if self.mode == "show":
            return await self._apply_show(desired, opts)
        out = self.output
        dmx = {self.devices[m].id for m, v in desired.items() if v == "dmx" and m in self.devices}
        rest = {m: v for m, v in desired.items() if v != "dmx" and m in self.devices}
        bad = [m for m, v in rest.items() if v == "taken_over"]
        results: dict[str, Outcome] = {m: Outcome("failed", "taken_over is read-only", retry=False) for m in bad}
        for m in bad:
            rest.pop(m)
        leaving = {self.devices[m].id for m in rest}
        wanted = (out.wanted - leaving) | dmx
        was_streaming = out.state == "streaming" and bool(out.wanted & leaving)
        out.set_wanted(wanted)

        if rest:
            if was_streaming:
                await self._wait(lambda: out.state != "streaming" or not (out.wanted & leaving), 5.0)
                await asyncio.sleep(1.0)               # the bridge writes its post-entertainment state first
            await self._put_states(rest, opts, results)

        if dmx:
            ok = await self._wait(lambda: out.state in ("streaming", "waiting_input", "taken_over", "error"), 8.0)
            for d in dmx:
                m = d.split(".", 1)[1]
                if out.state == "taken_over":
                    results[m] = Outcome("failed", "taken_over: another app holds the Entertainment area",
                                         retry=False)
                elif out.state == "error":
                    results[m] = Outcome("failed", out.detail)
                elif not ok:
                    results[m] = Outcome("unconfirmed", f"stream {out.state}")
                else:
                    results[m] = Outcome("ok", "streaming" if out.state == "streaming" else "armed: waiting for Art-Net")
        return self._worst(results)

    async def _put(self, member: str, value: Any, duration_ms: int) -> Outcome:
        body: dict[str, Any] = {"dynamics": {"duration": duration_ms}}
        if value == "off":
            body["on"] = {"on": False}
        else:
            body["on"] = {"on": True}
            body["dimming"] = {"brightness": float(value["bri"])}
            if "xy" in value:
                body["color"] = {"xy": {"x": value["xy"][0], "y": value["xy"][1]}}
            elif "mirek" in value:
                body["color_temperature"] = {"mirek": value["mirek"]}
        try:
            await self.client.put("light", self.devices[member].extra["light"], body)
        except Exception as e:
            return Outcome("failed", str(e))
        self._commanded[member] = value
        return Outcome("ok")

    @staticmethod
    async def _wait(cond, timeout_s: float) -> bool:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if cond():
                return True
            await asyncio.sleep(0.1)
        return cond()
