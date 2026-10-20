"""Sensor ingest through a real RoomService (in-process, random port) and fakes:
`/v1/ingest/lux` auth, validation, acks, limits; the MaskArbiter against the Flask fake;
held; overrides; config reload; and calib-3's own RoomSink code against the server.

Temp key files and temp light config only; no real device, Flask or key file is touched.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import sys
import time
import types
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio

from .ingest_helpers import KEY, REPO, SENSOR, FakeRelay, IngestHarness, Viewer, write_keys

FAST_FILTER = {"stale_after_s": 1.0, "stale_ramp_s": 2.0, "fresh_ramp_s": 0.5}


class H(IngestHarness):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.clients: list = []

    async def relay(self, **kw) -> FakeRelay:
        hello = kw.pop("hello", True)
        r = FakeRelay(self.ws_base + "/v1/ingest/lux", **kw)
        self.clients.append(r)
        return await r.connect(hello=hello)

    async def viewer(self, screen: str = "devtest") -> Viewer:
        v = Viewer(self.ws_base, screen)
        self.clients.append(v)
        return await v.connect()

    async def status(self) -> dict:
        st, body = await self.api("GET", "/api/v1/status", token=None)
        assert st == 200
        return body

    async def stop(self) -> None:
        for c in self.clients:
            await c.close()
        await super().stop()


@pytest_asyncio.fixture
async def make_h(tmp_path: Path):
    made: list[H] = []

    async def _make(**kw) -> H:
        kw.setdefault("keys", {SENSOR: KEY})
        h = H(tmp_path, **kw)
        made.append(h)
        await h.start()
        return h

    try:
        yield _make
    finally:
        for h in made:
            await h.stop()


async def wait_until(pred, timeout: float = 3.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


async def feed(relay: FakeRelay, lux: float, seconds: float, hz: float = 10.0, **kw) -> list[int]:
    seqs, n = [], max(1, int(seconds * hz))
    for _ in range(n):
        seqs.append(await relay.reading(lux, **kw))
        await asyncio.sleep(1.0 / hz)
    return seqs


async def until_sensor(h: H, screen: str = "devtest", timeout: float = 3.0) -> None:
    await wait_until(lambda: h.svc.mask.tier.get(screen) == "sensor", timeout, f"{screen} on the sensor")


# ── no keys (owner ruling, 14 Oct): ingest is open ──────────────────────────

async def test_no_keys_file_ingest_is_open(make_h, caplog):
    caplog.set_level(logging.INFO)
    h = await make_h(keys=None)
    page = await h.page()
    m = await page.wait_for("mask.set", timeout=3)
    assert m["rev"] == 1 and m["brightness"] == 0.5                 # Flask's mask until a sensor speaks
    body = await h.status()
    assert body["mask"]["ingest"]["armed"] is True and body["mask"]["ingest"]["disabled_reason"] is None
    r = await h.relay(key=None)                                     # no keys file, no key: accepted
    await r.reading(50.0)
    await wait_until(lambda: r.acks, 2, "ack without a key")
    assert "no sensor keys file" not in caplog.text


# ── keys are ignored; the hello, protocol and browser rules still hold ──────

async def test_any_sensor_accepted_and_close_codes(make_h, caplog):
    caplog.set_level(logging.DEBUG)
    h = await make_h(ingest={"hello_timeout_s": 0.5})
    # A wrong key, no key, or a sensor the keys file doesn't list: all accepted (any sensor id).
    for kw in ({"key": "x" * 40}, {"key": None}, {"sensor": "kitchen", "key": None}):
        r = await h.relay(**kw)
        await r.reading(30.0)
        await wait_until(lambda: r.acks, 2, f"ack for {kw}")
        await r.close()
    r = await h.relay(hello=False)                                  # wrong role
    await r.send("hello", {"role": "viewer", "sensor": SENSOR})
    assert await r.wait_closed() == 4401
    r = await h.relay(hello=False)                                  # no hello in time
    assert await r.wait_closed(2) == 4002
    r = await h.relay(hello=False)
    await r.ws.send_str(json.dumps({"v": 2, "type": "hello", "data": {}}))
    assert await r.wait_closed() == 4400
    # Browser rules: a foreign Origin is refused at the upgrade; an allowed one works.
    with pytest.raises(aiohttp.WSServerHandshakeError) as ei:
        await h.relay(origin="http://evil.example")
    assert ei.value.status == 403
    async with aiohttp.ClientSession() as s:
        with pytest.raises(aiohttp.WSServerHandshakeError) as ei:
            await s.ws_connect(h.ws_base + "/v1/ingest/lux", headers={"Host": "evil.example"})
        assert ei.value.status == 403
    ok = await h.relay(origin="http://localhost:5174")
    await ok.reading(20.0)
    await wait_until(lambda: ok.acks, 2, "ack")
    # A newer socket for the same sensor replaces it (4009).
    r1 = await h.relay()
    await r1.reading(21.0)
    await wait_until(lambda: r1.acks, 2, "ack")
    assert await ok.wait_closed() == 4009
    body = await h.status()
    assert SENSOR in [c["sensor"] for c in body["mask"]["ingest"]["connections"]]
    assert body["mask"]["ingest"]["key_failures"] == 0
    assert KEY not in caplog.text and "x" * 40 not in caplog.text
    assert KEY not in json.dumps(body)


# ── validation ──────────────────────────────────────────────────────────────

async def test_validation_and_seq_reset(make_h):
    h = await make_h()
    r = await h.relay(seq0=1000)
    await r.reading(10.0)                                           # 1001 ok
    for bad in (float("nan"), float("inf"), float("-inf"), "12", True, None):
        await r.reading(bad)                                        # invalid_lux (seq advances)
    await r.reading(-3.0)                                           # clamped to 0
    await r.reading(5e6)                                            # clamped to 200 klx
    await r.reading(10.0, seq=1005)                                 # backwards
    await r.reading(10.0, seq=r.seq)                                # duplicate
    await r.reading(10.0, seq="7")                                  # invalid_seq (no ack: nothing to echo)
    await r.send("ingest.lux", {"sensor": SENSOR, "lux": 10.0, "mono_ms": 1})          # missing seq
    await r.reading(10.0, held="yes")                               # invalid_held
    await r.reading(10.0, sensor="kitchen")                         # wrong_sensor: a key is per sensor
    last = await r.reading(11.0)
    await wait_until(lambda: r.acks and r.acks[-1][1] == last, 2, "ack of the last reading")
    rej = h.svc.mask.sensors.rejected[SENSOR]
    assert rej == {"invalid_lux": 6, "seq_backwards": 1, "seq_duplicate": 1, "invalid_seq": 1}, rej
    conn = h.svc.mask.hub.conns[SENSOR]
    assert conn.rejected == {"invalid_lux": 6, "seq_backwards": 1, "seq_duplicate": 1, "invalid_seq": 1,
                             "missing_seq": 1, "invalid_held": 1, "wrong_sensor": 1}
    assert conn.accepted == 4
    f = h.svc.mask.sensors.filters[SENSOR]
    assert 0 <= f.lux <= 200_000
    # A new hello resets the seq check (calib-3 reseeds seq from the wall clock at relay start).
    r2 = await h.relay(seq0=5)
    s = await r2.reading(12.0)
    await wait_until(lambda: r2.acks and r2.acks[-1][1] == s, 2, "ack after re-hello")
    assert h.svc.mask.hub.conns[SENSOR].accepted == 1


# ── acks and limits ─────────────────────────────────────────────────────────

async def test_ack_cadence_matches_calib3(make_h):
    """calib-3 drops a link with no lux.ack 5 s after a send. Every reading is acked within
    ack_min_interval_s (200 ms), acks are at most 5/s, and a 1 Hz heartbeat is acked each time."""
    h = await make_h()
    r = await h.relay()
    sent = await feed(r, 30.0, 3.0, hz=10)
    await asyncio.sleep(0.4)
    ack_t = [t for t, _ in r.acks]
    assert all(b - a >= 0.19 for a, b in zip(ack_t, ack_t[1:])), "coalesced to <= 1 per 200 ms"
    assert max(b - a for a, b in zip(ack_t, ack_t[1:])) < 0.35
    for seq in sent:                                                # each reading: acked within ~200 ms
        at = min(t for t, s in r.acks if s >= seq)
        assert at - r.sent[seq] < 0.3, (seq, at - r.sent[seq])
    n = len(r.acks)
    hb = await feed(r, 30.0, 3.0, hz=1)                             # steady light: the 1 Hz heartbeat
    await asyncio.sleep(0.3)
    assert len(r.acks) - n == len(hb) and r.acks[-1][1] == hb[-1]


async def test_flood_closes_4029(make_h):
    h = await make_h()
    r = await h.relay()
    for i in range(60):
        try:
            await r.reading(10.0 + i)
        except (ConnectionError, RuntimeError):
            break
    assert await r.wait_closed() == 4029
    assert (await h.status())["mask"]["ingest"]["closes"]["4029"] == 1


async def test_readings_beyond_20_per_s_are_dropped(make_h):
    h = await make_h()
    r = await h.relay()
    for i in range(35):                                             # 35 readings in ~0.35 s
        await r.reading(10.0)
        await asyncio.sleep(0.01)
    await wait_until(lambda: r.acks and r.acks[-1][1] == r.seq, 2, "last ack")
    conn = h.svc.mask.hub.conns[SENSOR]
    assert conn.dropped >= 10 and conn.accepted <= 20 and not r.ws.closed


# ── the arbiter against Flask ───────────────────────────────────────────────

async def test_arbiter_stops_flask_fetch_for_live_sensor_and_resumes_when_stale(make_h):
    h = await make_h(ingest={"filter": FAST_FILTER})
    v = await h.viewer()
    await v.wait_mask(lambda m: "source" not in m)                  # today's Flask push first
    await wait_until(lambda: h.flask.mask_requests("devtest") >= 2, 3, "Flask fetches")
    r = await h.relay()
    feeder = asyncio.get_running_loop().create_task(feed(r, 50.0, 3.0))
    await until_sensor(h)
    t_switch = time.monotonic()
    _, m = await v.wait_mask(lambda m: m.get("source") == "sensor", after=0)
    assert m["transition_ms"] == 2000                               # one source change, slow fade
    assert h.flask.override_requests, "overrides read from Flask's GET endpoints"
    await asyncio.sleep(1.5)
    assert h.flask.mask_requests("devtest", since=t_switch + 0.05) == 0, "no /mask fetch while the sensor is live"
    await feeder
    t_stop = time.monotonic()                                       # the relay goes quiet: stale after 1 s
    await wait_until(lambda: h.svc.mask.tier.get("devtest") == "flask", 3, "back on Flask")
    assert time.monotonic() - t_stop >= 0.9
    await wait_until(lambda: h.flask.mask_requests("devtest", since=t_stop) >= 1, 2, "Flask fetches resume")
    _, m = await v.wait_mask(lambda m: m.get("source") == "flask", after=t_stop)
    assert m["transition_ms"] == 2000 and m["brightness"] == 0.5
    body = await h.status()
    assert body["screens"]["devtest"]["mask"]["tier"] == "flask"
    assert body["mask"]["tiers"] == {"devtest": "flask"}


async def test_step_snaps_fast_and_drift_is_paced(make_h):
    h = await make_h(ingest={"overrides_poll_s": 0.5})
    v = await h.viewer()
    r = await h.relay()
    await feed(r, 5.0, 1.0)
    await until_sensor(h)
    await asyncio.sleep(0.2)
    n0 = len(v.masks)
    t_step = time.monotonic()
    await r.reading(40.0)                                           # first far reading: spike guard
    await asyncio.sleep(0.1)
    assert len(v.masks) == n0
    s = await r.reading(40.0)                                       # confirming reading
    at, m = await v.wait_mask(lambda m: m["transition_ms"] == 200, after=t_step, timeout=1)
    assert at - r.sent[s] < 0.1, f"client-observed {1000 * (at - r.sent[s]):.1f} ms"
    await feed(r, 40.0, 8.0)
    sends = [(t, m) for t, m in v.masks if t >= t_step]
    drift = [t for t, m in sends if m["transition_ms"] == 1000]
    assert drift, "the tail of the step goes as drift"
    assert all(b - a >= 2.95 for a, b in zip(drift, drift[1:])), drift
    per_s = max(sum(1 for t, _ in sends if w <= t < w + 1) for w, _ in sends)
    assert per_s <= 10
    lat = (await h.status())["mask"]["latency_ms"]
    assert lat["immediate"]["n"] >= 2 and lat["p95"] < 50, lat


async def test_held_window_pushes_nothing_and_fetches_nothing(make_h):
    h = await make_h()
    v = await h.viewer()
    r = await h.relay()
    await feed(r, 30.0, 1.0)
    await until_sensor(h)
    await asyncio.sleep(0.5)
    t0, n0 = time.monotonic(), len(v.masks)
    for i in range(14):                                             # calibration flashes, held, ~1.4 Hz
        await r.reading(2000.0 if i % 2 else 0.0, held=True)
        await asyncio.sleep(0.15)
    assert len(v.masks) == n0, v.masks[n0:]
    assert h.flask.mask_requests("devtest", since=t0) == 0
    assert (await h.status())["screens"]["devtest"]["mask"]["held"] is True
    await feed(r, 30.0, 0.5)                                        # released at the same light
    assert not h.svc.mask.sensors.view("devtest", time.monotonic()).held
    assert all(abs(m["brightness"] - v.masks[n0 - 1][1]["brightness"]) < 0.02 for _, m in v.masks[n0:])


async def test_overrides_and_suspend(make_h):
    h = await make_h()
    v = await h.viewer()
    r = await h.relay()
    feeder = asyncio.get_running_loop().create_task(feed(r, 30.0, 6.0))
    try:
        await until_sensor(h)
        t = time.monotonic()
        h.flask.mask_enabled["devtest"] = False                     # POST maskoff on Flask
        _, m = await v.wait_mask(lambda m: m["source"] == "maskoff", after=t, timeout=2)
        assert m["brightness"] == 1.0 and m["transition_ms"] == 200
        h.flask.brightness_override["devtest"] = 0.4
        _, m = await v.wait_mask(lambda m: m["source"] == "manual_override", after=t, timeout=2)
        assert m["brightness"] == 0.4
        h.flask.brightness_override.pop("devtest")
        h.flask.mask_enabled["devtest"] = True
        t = time.monotonic()
        await v.wait_mask(lambda m: m["source"] == "sensor", after=t, timeout=2)
        h.svc.mask.set_suspended(True)                              # mode: sensor_mask suspended
        t = time.monotonic()
        h.flask.mask_enabled["devtest"] = False
        await asyncio.sleep(0.8)
        assert v.since(t) == []
        h.svc.mask.set_suspended(False)
        await v.wait_mask(lambda m: m["source"] == "maskoff", after=t, timeout=2)
    finally:
        await feeder


async def test_light_config_reload_and_persisted_warm_start(make_h):
    h = await make_h()
    v = await h.viewer()
    r = await h.relay()
    await feed(r, 22.0, 1.0)
    await until_sensor(h)
    _, m = await v.wait_mask(lambda m: m.get("source") == "sensor")
    assert m["brightness"] == pytest.approx(0.50, abs=0.01)
    # The LightSense editor rewrites intensity.json: picked up without a restart.
    p = h.light["intensity_json"]
    data = json.loads(p.read_text())
    for pt in data["sensor_mappings"][SENSOR]["lux_to_intensity"]:
        if pt["lux"] == 22:
            pt["intensity"] = 0.30
    p.write_text(json.dumps(data))
    t = time.monotonic()
    feeder = asyncio.get_running_loop().create_task(feed(r, 22.0, 1.5))
    _, m = await v.wait_mask(lambda m: abs(m["brightness"] - 0.30) < 0.01, after=t, timeout=2)
    await feeder
    await h.restart_room()                                          # persisted filter state
    state = json.loads((h.state_dir / "sensors.json").read_text())
    assert state["filters"][SENSOR]["lux"] == pytest.approx(22.0, rel=0.01)
    assert h.svc.mask.sensors.filter_for(SENSOR).lux == pytest.approx(22.0, rel=0.01)


# ── calib-3's own relay code against this server ─────────────────────────────

def load_calib3_roomsink(monkeypatch) -> dict:
    """RoomSink and its constants from tools/room/latency_cal/lightsense.py (read-only), with
    fake yoctopuce modules; the relay's main() is not run."""
    path = REPO / "tools" / "room" / "latency_cal" / "lightsense.py"
    tree = ast.parse(path.read_text())
    keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
            or (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in n.targets))
            or (isinstance(n, ast.ClassDef) and n.name == "RoomSink")]
    api = types.ModuleType("yoctopuce.yocto_api")
    api.YAPI = type("YAPI", (), {"HandleEvents": staticmethod(lambda err: 0), "SUCCESS": 0})
    api.YRefParam = lambda *a: types.SimpleNamespace(value=None)
    ls = types.ModuleType("yoctopuce.yocto_lightsensor")
    ls.YLightSensor = type("YLightSensor", (), {"CURRENTVALUE_INVALID": -999999.0})
    monkeypatch.setitem(sys.modules, "yoctopuce", types.ModuleType("yoctopuce"))
    monkeypatch.setitem(sys.modules, "yoctopuce.yocto_api", api)
    monkeypatch.setitem(sys.modules, "yoctopuce.yocto_lightsensor", ls)
    ns: dict = {"__name__": "calib3_lightsense"}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), ns)
    assert ns["VERSION"] == "calib-3" and ns["ROOM_ACK_TIMEOUT_S"] == 5.0
    return ns


class FakeSensor:
    def __init__(self, lux: float) -> None:
        self.value = lux
        self.cb = None

    def registerValueCallback(self, cb) -> None:
        self.cb = cb

    def isOnline(self) -> bool:
        return True

    def get_currentValue(self) -> float:
        return self.value

    def measure(self, lux: float) -> None:
        """A new measurement: the value callback fires (yocto passes a string)."""
        self.value = lux
        if self.cb:
            self.cb(None, f"{lux:.3f}")


class FakeCapture:
    def __init__(self) -> None:
        self.held_lux = None

    def holding(self) -> bool:
        return self.held_lux is not None

    def output_lux(self, lux):
        return self.held_lux if self.held_lux is not None else lux


async def test_calib3_roomsink_against_server(make_h, monkeypatch):
    pytest.importorskip("websockets")
    ns = load_calib3_roomsink(monkeypatch)
    h = await make_h()
    v = await h.viewer()
    sensor, capture = FakeSensor(8.0), FakeCapture()
    sink = ns["RoomSink"](sensor, capture)
    sink.configure({"url": h.ws_base + "/v1/ingest/lux", "key": KEY})
    try:
        for _ in range(12):                                         # ~10 Hz value callbacks
            sensor.measure(8.0)
            await asyncio.sleep(0.1)
        await until_sensor(h)
        t_step = time.monotonic()
        sensor.measure(60.0)                                        # one callback; calib-3 re-reads ~120 ms later
        at, m = await v.wait_mask(lambda m: m["transition_ms"] == 200, after=t_step, timeout=1.0)
        assert at - t_step < 0.35, "the confirming re-read makes the step visible at once"
        b0 = h.svc.mask.pushed["devtest"].brightness
        # With no further callback (a clean step), the rest waits for the 1 Hz heartbeat
        # (lightmask advances only on samples): converged within ~1.2 s.
        await v.wait_mask(lambda m: m["brightness"] > 0.77, after=t_step, timeout=2.0)
        assert b0 > 0.5
        n = len(v.masks)
        capture.held_lux = 60.0                                     # a calibration capture starts
        for i in range(20):
            sensor.measure(2000.0 if i % 2 else 0.5)
            await asyncio.sleep(0.1)
        assert len(v.masks) == n, "held readings never re-mask"
        assert h.svc.mask.sensors.view("devtest", time.monotonic()).held
        capture.held_lux = None
        await asyncio.sleep(4.0)                                    # steady: 1 Hz heartbeats only
        hub = h.svc.mask.hub
        assert hub.hellos == 1, "the relay never dropped the link (no 5 s ack timeout)"
        assert hub.conns[SENSOR].accepted >= 30 and not hub.conns[SENSOR].rejected
        assert sink.acked
    finally:
        await sink.close()
