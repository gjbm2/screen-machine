"""The golden probe (multi-room plan, step 1): room_service on a real deployment's files, with nothing real behind it.

    SM_DEPLOYMENT=<id> python -m room_service.tests.golden_probe > out.json

Starts a RoomService in-process on the deployment's own room/room.yaml, modes.json, rooms.json,
publish-destinations.json, lights/patch.yaml and room/ device files, but every device is inert:
  * fake stream_service, fake Flask (overlay WS), fake snapshotter; state, tokens and output in a temp dir;
  * DMX off in the config (no Art-Net, no show bus, no Hue stream: the patched hue.* lamps are the placeholder);
  * RF devices are their placeholders (the rf capability read as off here), blinds the placeholder of blinds.json,
    Sonos the placeholder of sonos.json, the TVs tv.json's verify-only placeholder;
  * show_service is an unreachable address: the real ShowAdapter (its members and schema) and LightsAdapter, the
    latter seeded with two fixed lights (GOLDEN_LIGHTS) as its cached rig, so brightness.lamps exists.
Then it prints, as one JSON document: the scrubbed status, GET /api/v1/modes, /room/describe, /room/config's revision,
the derived '<room>:*' groups, and POST /room/plan for every mode. Last, "switches": it switches to every mode in
turn (sorted, then back to the default) and records each PUT /api/v1/mode answer, the mode.transition events (their
phase, from, to, via, source, room), the event types seen, the status after, the state dir's files (each one's keys)
and sensor_mask.json and brightness.json as written. room_service/tests/test_golden.py runs it per deployment and
compares with room_service/tests/golden/<id>_*.json. Ages, epochs, times and temp paths are scrubbed (and, in
"switches", the repo's own path).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRUB_KEYS = re.compile(r"(^|_)(age_s|age_ms|at|ts|t|t_ms|time|epoch|since|until|started|ended|updated|mtime|"
                        r"seen|last_seen|uptime_s|boot|boot_id|pid)$")
SCRUB_SUFFIX = ("_age_s", "_at", "_ts", "_epoch", "_mtime", "_since", "_until", "_ms_ago", "_s_ago", "_in_s",
                "until_s")
TOKEN = "g" * 64
GOLDEN_LIGHTS = [{"id": "par-left", "label": "Par left"}, {"id": "par-right", "label": "Par right"}]


def scrub(v: Any, tmp: str, key: str = "") -> Any:
    """Times, ages, epochs and the temp dir out of a JSON value, so two runs on the same tree compare equal."""
    if isinstance(v, dict):
        return {k: scrub(x, tmp, k) for k, x in v.items()}
    if isinstance(v, list):
        return [scrub(x, tmp, key) for x in v]
    if isinstance(v, (int, float)) and not isinstance(v, bool) and \
            (SCRUB_KEYS.search(key) or key.endswith(SCRUB_SUFFIX)):
        return "<scrubbed>"
    if isinstance(v, str):
        if SCRUB_KEYS.search(key) or key.endswith(SCRUB_SUFFIX):
            if re.match(r"^\d{4}-\d\d-\d\d", v) or re.match(r"^[0-9a-f-]{6,}$", v):
                return "<scrubbed>"
        v = v.replace(tmp, "<tmp>").replace(str(REPO), "<repo>")   # the same in any checkout (PC22's path differs)
        v = re.sub(r"\bt-[0-9a-f]{8}\b", "t-<id>", v)       # a transition's id
        v = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:<port>", v)
        v = re.sub(r"\b\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:?\d\d)?\b", "<time>", v)
        v = re.sub(r"\b\d\d:\d\d:\d\d\b", "<clock>", v)
    return v


async def probe() -> dict:
    import aiohttp
    from aiohttp.test_utils import TestServer

    import smconfig
    from room_service import config as rc
    from room_service.adapters import blinds as blinds_mod
    from room_service.adapters import broadlink_rf
    from room_service.service import RoomService

    from .fakes import FakeFlask, FakeSnapshotter, FakeStreamService

    broadlink_rf.rf_enabled = lambda: False              # every RF device file: its placeholder, nothing sent
    tmp = Path(tempfile.mkdtemp(prefix="sm-golden-"))
    stream = FakeStreamService("s" * 64)
    flask = FakeFlask()
    await stream.start()
    await flask.start()
    (tmp / "stream.token").write_text("s" * 64 + "\n")
    os.chmod(tmp / "stream.token", 0o600)
    cfg = rc.load_config(smconfig.path("room", "room.yaml"))
    cfg.stream_url, cfg.stream_token_file = stream.url, tmp / "stream.token"
    cfg.flask_url, cfg.flask_ws, cfg.art_page_base = flask.url, flask.ws_url, "http://127.0.0.1:1"
    cfg.show_url = "http://127.0.0.1:1"
    cfg.state_dir, cfg.token_file, cfg.page_keys_file = tmp / "state", tmp / "room.token", tmp / "page_keys.json"
    cfg.output_dir = tmp / "output"
    cfg.ingest.sensors_file = tmp / "room_sensors.json"   # none: this machine's sensor keys never colour the status
    cfg.dmx_enabled, cfg.sonos_enabled = False, False
    # show_service's DMX lights as its last-known rig (the cache it keeps in the state dir), the same two lights on
    # every deployment, so light.* members, the lights groups and brightness.lamps are exercised without a rig.
    (tmp / "state").mkdir(mode=0o700)
    (tmp / "state" / "lights_rig.json").write_text(json.dumps({"revision": "golden", "lights": GOLDEN_LIGHTS}))
    svc = RoomService(cfg, snapshotter=FakeSnapshotter(), token=TOKEN, show=None, remotes=None,
                      blinds=blinds_mod.placeholder_from_config())
    server = TestServer(svc.app, host="127.0.0.1")
    await server.start_server()
    base = f"http://127.0.0.1:{server.port}"
    out: dict[str, Any] = {"deployment": smconfig.deployment().id}
    try:
        async with aiohttp.ClientSession(headers={"Authorization": "Bearer " + TOKEN}) as s:
            async def get(path: str) -> Any:
                async with s.get(base + path) as r:
                    return {"status": r.status, "body": await r.json()}

            for _ in range(100):                         # the first reads in: every adapter has been read once
                await asyncio.sleep(0.05)
                st = (await get("/api/v1/status"))["body"]
                if all((sub or {}).get("members") is not None for sub in (st.get("subsystems") or {}).values()):
                    break
            await asyncio.sleep(0.5)
            out["status"] = (await get("/api/v1/status"))["body"]
            out["modes"] = (await get("/api/v1/modes"))["body"]
            out["describe"] = (await get("/api/v1/room/describe"))["body"]
            conf = (await get("/api/v1/room/config"))["body"]
            out["revision"] = conf.get("revision")
            out["groups"] = {k: sorted(v) if isinstance(v, (list, set, tuple)) else v
                             for k, v in sorted((svc.room_config.groups or {}).items())}
            plans = {}
            for mode in (out["modes"].get("modes") or {}):
                async with s.post(base + "/api/v1/room/plan", json={"mode": mode}) as r:
                    plans[mode] = {"status": r.status, "body": await r.json()}
            out["plan"] = plans
            out["switches"] = await switches(s, base, get, tmp)
    finally:
        await server.close()
        await stream.stop()
        await flask.stop()
    out = scrub(out, str(tmp))
    out["switches"] = _no_repo(out["switches"])
    return out


REPO = Path(__file__).resolve().parents[2]
MT_KEYS = ("phase", "from", "to", "via", "source", "room", "skip", "reapply", "resumes")


def _no_repo(v: Any) -> Any:
    """The repo's own path out of a JSON value (status names the deployment's files), so the part is the same in
    any checkout."""
    return json.loads(json.dumps(v).replace(str(REPO), "<repo>"))


async def switches(s: Any, base: str, get: Any, tmp: Path) -> dict:
    """Every mode in turn (sorted), then the default: each PUT's answer, the transition events, the status after and
    the state files written. Each switch is awaited to its end before the next."""
    events: list[dict] = []
    ws = await s.ws_connect(base + "/api/v1/events")

    async def pump() -> None:
        async for msg in ws:
            try:
                e = json.loads(msg.data)
            except (TypeError, ValueError):
                continue
            if isinstance(e, dict) and e.get("type") not in ("status", "hello", "ping"):
                events.append(e)
    task = asyncio.ensure_future(pump())
    modes = (await get("/api/v1/modes"))["body"]
    seq = sorted(modes.get("modes") or {}) + [modes.get("default")]
    puts, transitions = [], []
    for mode in seq:
        before = len(events)
        async with s.put(base + "/api/v1/mode", json={"mode": mode, "source": "probe"}) as r:
            puts.append({"mode": mode, "status": r.status, "body": await r.json()})
        for _ in range(600):
            await asyncio.sleep(0.05)
            st = (await get("/api/v1/status"))["body"]
            m = st.get("mode") or {}
            if m.get("target") is None and (m.get("transition") or {}).get("status") != "running":
                break
        await asyncio.sleep(0.2)
        transitions.append({"mode": mode, "events": [
            {k: v for k, v in (e.get("data") or {}).items() if k in MT_KEYS}
            for e in events[before:] if e.get("type") in ("mode.transition", "room_mode.transition")]})
    await asyncio.sleep(0.3)
    task.cancel()
    await ws.close()
    files = {}
    for p in sorted((tmp / "state").rglob("*")):
        if p.is_file():
            try:
                d = json.loads(p.read_text())
                files[str(p.relative_to(tmp / "state"))] = sorted(d) if isinstance(d, dict) else type(d).__name__
            except ValueError:
                files[str(p.relative_to(tmp / "state"))] = "not json"

    def written(name: str) -> Any:
        p = tmp / "state" / name
        return json.loads(p.read_text()) if p.exists() else None
    return {"puts": puts, "transitions": transitions,
            "event_types": sorted({str(e.get("type")) for e in events}),
            "status_after": (await get("/api/v1/status"))["body"], "files": files,
            "sensor_mask.json": written("sensor_mask.json"), "brightness.json": written("brightness.json")}


def main() -> int:
    logging.basicConfig(level=logging.CRITICAL)
    print(json.dumps(asyncio.run(probe()), indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
