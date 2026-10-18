"""outputs: the rig component (README S5.4), with a fake registry, engine and drivers; and once with the real
registry, engine and Art-Net driver against an ephemeral local sink and a virtual node.

Nothing here talks to a bridge, a node, the LAN, room_service or 127.0.0.1:6454: the real-pieces test sends only to a
UDP socket it binds on an ephemeral 127.0.0.1 port, and its nodes.json lives in tmp_path.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

from show_service import artnet_out, contract
from show_service import outputs as om


# -- fakes ---------------------------------------------------------------------------------------------------------------
def out(oid, driver, enabled=True, **kw):
    o = {"id": oid, "driver": driver, "enabled": enabled, "built": driver in contract.DRIVERS_BUILT, "node": None,
         "port_address": None, "latency_key": oid, "latency": {}, "hits": None, "min_attack_ms": None,
         "rate_hz": None, "settings": {}, "problems": []}
    o.update(kw)
    return o


def view(outputs: dict, rev="r1") -> dict:
    fx = {"lamp.a": {"id": "lamp.a", "type": "t", "kind": "lamp", "output": "o-hue"},
          "screen.x": {"id": "screen.x", "type": "s", "kind": "screen", "output": "o-screens"}}
    return {"revision": rev, "path": "test", "error": None, "problems": [], "outputs": outputs,
            "nodes": {"n1": {"id": "n1"}}, "types": {}, "fixtures": fx, "groups": {}}


class FakeRegistry:
    def __init__(self, v):
        self.v, self.polls, self.new = v, [], True

    def set(self, v):
        self.v, self.new = v, True

    def poll(self, looks=None, force=False, lamps=None):
        self.polls.append((looks, lamps))
        new, self.new = self.new, False
        return new

    def view(self):
        return self.v


class FakeEngine:
    def __init__(self, active=True):
        self.configured, self.looks, self.hits = [], [], []
        self.active = active
        self.evals: dict[str, int] = {}

    def configure(self, looks, rig, *, lamps=None):
        self.configured.append((looks, rig.get("revision"), lamps))
        return ["a problem"]

    def evaluate(self, oid, t, caps):
        self.evals[oid] = self.evals.get(oid, 0) + 1
        return {"output": oid, "seq": self.evals[oid], "t_send_ms": t * 1000.0, "active": self.active, "why": None,
                "fixtures": {"lamp.a": {"intensity": 0.5, "colour": [1.0, 0.5, 0.0], "rgb": [0.5, 0.25, 0.0],
                                        "t_eval_ms": t * 1000.0, "dmx": b"\x01\x02"}}}

    def set_look(self, **kw):
        self.looks.append(kw)

    def on_hit(self, d):
        self.hits.append(d)

    def takes_hit(self, d):
        return d.get("strength", 1.0) >= 0.5

    def stats(self):
        return {"program": {"revision": "p", "layers": [], "problems": []}, "per_output": {}, "errors": {}}


EVENTS: list = []


class FakeDriver:
    period = 0.02

    def __init__(self, oid, *, ctx=None, clock=None, settings=None, shared=None, mono=time.monotonic, kind="hue",
                 hits=False, **kw):
        self.output_id, self.kind, self.kw, self.settings = oid, kind, kw, settings
        self.caps = {"hits": hits, "min_attack_ms": 0.0, "grid": "fixed", "rate_hz": 50.0, "keepalive_s": None}
        self.configured, self.frames, self.started, self.stopped, self.ticks = [], [], 0, 0, 0
        self.fail = 0
        self.plan_hits = 0
        self.shared = shared
        self.requests = []
        self.settings_set = []

    def configure(self, o, rig):
        self.configured.append(o)

    def start(self):
        self.started += 1
        EVENTS.append(("start", self.output_id))

    def stop(self, timeout=2.0):
        self.stopped += 1
        EVENTS.append(("stop", self.output_id))

    def plan(self, t_last, now, active):
        return max(now, t_last + self.period)

    def set_frame(self, frame):
        if self.fail:
            self.fail -= 1
            raise RuntimeError("boom")
        self.frames.append((time.monotonic(), frame))
        EVENTS.append(("frame", self.output_id))

    def tick(self, now):
        self.ticks += 1

    def set_settings(self, s):
        self.settings_set.append(s)

    def status(self):
        return {"id": self.output_id, "driver": self.kind, "enabled": True, "state": "sending", "detail": None,
                "frames": len(self.frames), "rate_hz": 0.0, "late_ms": {"p50": None, "p95": None}, "errors": 0,
                "last_frame_age_s": None, "extra": {}}

    def hue_status(self):
        return om.hue_off(self.output_id, "fake") | {"state": "idle"}

    def request(self, lights, on):
        self.requests.append((lights, on))
        return self.hue_status()

    async def release(self, lights):
        return {"released": True, "state": "idle", "why": None, "still_following": [], "waited_ms": 0.0,
                "via": "show"}


class HitDriver(FakeDriver):
    period = 1.0                                     # a slow grid: a hit must pull a frame forward

    def plan_hit(self, t_last, now):
        self.plan_hits += 1
        return now


def make(registry, engine, factories=None, settings=None, **kw):
    ctx = contract.BrainContext(publish=lambda m: None)
    fac = factories or {"hue": lambda oid, **k: FakeDriver(oid, kind="hue", **k),
                        "artnet": lambda oid, **k: HitDriver(oid, kind="artnet", hits=True, **k)}
    rig = om.attach(ctx, registry=registry, engine=engine, driver_factories=fac, **kw)
    rig.configure(settings if settings is not None else {"rig": {"enabled": True, "artnet": {"poll_s": 3}},
                                                         "looks": {"master": 1.0}, "lamps": {"roles": {}}})
    return rig


def threads(prefix="show-out-"):
    return sorted(t.name for t in threading.enumerate() if t.name.startswith(prefix) and t.is_alive())


def wait_for(pred, timeout=3.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.01)
    raise AssertionError(f"timed out: {what}")


@pytest.fixture(autouse=True)
def _clean():
    EVENTS.clear()
    yield
    for t in threading.enumerate():
        assert not (t.name.startswith("show-out-") and t.is_alive()), f"runner {t.name} left running"


# -- tests ---------------------------------------------------------------------------------------------------------------
def test_runners_only_for_enabled_built_outputs_and_the_snapshot():
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue"), "o-dmx": out("o-dmx", "artnet", enabled=False),
                             "o-screens": out("o-screens", "showbus"), "o-ddp": out("o-ddp", "ddp")}))
    eng = FakeEngine()
    rig = make(reg, eng)
    rig.start()
    try:
        assert threads() == ["show-out-o-hue"]
        assert eng.configured == [({"master": 1.0}, "r1", {"roles": {}})]          # looks and lamps reach it
        assert rig.view() is reg.v and rig.provides() == {"rig": rig.view}
        wait_for(lambda: len(rig.drivers_of("hue")[0].frames) >= 3, what="frames")
        snap = rig.snapshot()
        assert set(snap) >= {"enabled", "registry", "outputs", "nodes", "engine", "errors", "problems", "modules"}
        st = snap["outputs"]
        assert st["o-hue"]["state"] == "sending" and "runner" in st["o-hue"]["extra"]
        assert st["o-dmx"]["state"] == "off" and "fixtures.yaml" in st["o-dmx"]["detail"]
        assert st["o-screens"]["state"] == "not_built" and st["o-ddp"]["state"] == "not_built"
        assert snap["nodes"]["n1"]["detail"].startswith("not polled")
        assert set(snap["outputs"]["o-dmx"]) == set(contract.OutputStatus.__annotations__)
        json.dumps(rig.snapshot_now())
    finally:
        assert rig.stop() is None
    assert threads() == []


def test_switching_outputs_on_and_off_at_reload_and_the_rollback():
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue"), "o-dmx": out("o-dmx", "artnet", enabled=False)}))
    rig = make(reg, FakeEngine())
    rig.start()
    try:
        hue = rig.drivers_of("hue")[0]
        assert hue.started == 1 and hue.configured
        reg.set(view({"o-hue": out("o-hue", "hue", enabled=False), "o-dmx": out("o-dmx", "artnet")}, rev="r2"))
        rig.tick(time.monotonic())
        assert hue.stopped == 1 and threads() == ["show-out-o-dmx"]
        assert rig.snapshot()["outputs"]["o-hue"]["state"] == "off"
        # the kind changes: stop, then a new driver of the new kind
        reg.set(view({"o-dmx": out("o-dmx", "hue")}, rev="r3"))
        rig.tick(time.monotonic())
        assert [type(d).__name__ for d in rig.drivers_of("hue")] == ["FakeDriver"] and rig.drivers_of("artnet") == []
        # the rollback: show.yaml rig.enabled false stops everything and hides the view
        rig.configure({"rig": {"enabled": False}})
        assert threads() == [] and rig.view() is None
        assert rig.snapshot()["outputs"] == {} and rig.snapshot()["enabled"] is False
        rig.configure({"rig": {"enabled": True}})
        assert threads() == ["show-out-o-dmx"] and rig.view() is not None
        rig.configure({"rig": {"enabled": "maybe"}})                     # not a bool: the rollback, with a line
        assert threads() == [] and any("rig.enabled" in p for p in rig.snapshot()["problems"])
    finally:
        rig.stop()


def test_settings_reach_the_drivers_on_a_reload():
    reg = FakeRegistry(view({"o-dmx": out("o-dmx", "artnet")}))
    rig = make(reg, FakeEngine())
    rig.start()
    try:
        d = rig.drivers_of("artnet")[0]
        assert d.settings == {"poll_s": 3}
        rig.configure({"rig": {"enabled": True, "artnet": {"poll_s": 5}}})
        assert d.settings_set == [{"poll_s": 5}] and len(d.configured) == 2
    finally:
        rig.stop()


def test_a_driver_module_that_does_not_load_reads_not_built(monkeypatch):
    monkeypatch.setitem(om.DRIVER_MODULES, "hue", "show_service.no_such_module:Nope")
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = om.attach(contract.BrainContext(publish=lambda m: None), registry=reg, engine=FakeEngine())
    rig.configure({})
    rig.start()
    try:
        st = rig.snapshot()["outputs"]["o-hue"]
        assert st["state"] == "not_built" and "didn't load" in st["detail"] and threads() == []
        assert rig.modules["no_such_module"].startswith("missing")
        assert rig.hue_status()["state"] == "off"
    finally:
        rig.stop()


def test_no_engine_means_nothing_runs():
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = make(reg, None)
    rig.engine = None
    rig.tick(time.monotonic())
    try:
        assert threads() == [] and rig.snapshot()["outputs"]["o-hue"]["state"] == "not_built"
    finally:
        rig.stop()


def test_a_raising_driver_is_counted_backs_off_and_carries_on(monkeypatch):
    monkeypatch.setattr(om, "RUNNER_BACKOFF_S", 0.3)
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = make(reg, FakeEngine())
    rig.start()
    try:
        d = rig.drivers_of("hue")[0]
        d.fail = 3
        st = wait_for(lambda: (lambda s: s if s["state"] == "error" else None)(rig.snapshot_now()["outputs"]["o-hue"]),
                      what="error state")
        assert "boom" in st["detail"]
        n = len(d.frames)
        wait_for(lambda: len(d.frames) > n + 3, what="frames again after the backoff")
        assert rig.snapshot_now()["outputs"]["o-hue"]["state"] == "sending"
        assert rig.snapshot_now()["outputs"]["o-hue"]["extra"]["runner"]["errors"] == 3
    finally:
        rig.stop()


def test_hits_wake_only_hits_capable_runners_and_reach_the_engine():
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue"), "o-dmx": out("o-dmx", "artnet")}))
    eng = FakeEngine()
    rig = make(reg, eng)
    rig.start()
    try:
        dmx, hue = rig.drivers_of("artnet")[0], rig.drivers_of("hue")[0]
        wait_for(lambda: dmx.frames, what="the first DMX frame")
        time.sleep(0.05)
        n = len(dmx.frames)
        t0 = time.monotonic()
        rig.on_hit({"t_ms": t0 * 1000.0, "band": "sub", "strength": 1.0})
        wait_for(lambda: len(dmx.frames) > n, timeout=0.5, what="a frame pulled forward by the hit")
        assert dmx.frames[-1][0] - t0 < 0.05 and dmx.plan_hits == 1        # its grid is 1 s: the hit pulled it
        assert hue.plan_hits == 0 and eng.hits == [{"t_ms": t0 * 1000.0, "band": "sub", "strength": 1.0}]
        # a hit the engine wouldn't flash (takes_hit false) reaches its ring but pulls no frame forward
        time.sleep(0.05)
        n = len(dmx.frames)
        rig.on_hit({"t_ms": time.monotonic() * 1000.0, "band": "sub", "strength": 0.1})
        time.sleep(0.2)
        assert len(dmx.frames) == n and dmx.plan_hits == 1 and len(eng.hits) == 2
        # a raising engine.on_hit is the caller's guard's (__main__'s "rig.hit")
    finally:
        rig.stop()


def test_plans_are_honoured():
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = make(reg, FakeEngine())
    rig.start()
    try:
        d = rig.drivers_of("hue")[0]
        wait_for(lambda: len(d.frames) >= 20, what="20 frames")
        ts = [t for t, _ in d.frames[-15:]]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        assert min(gaps) > 0.017 and sorted(gaps)[len(gaps) // 2] < 0.03, gaps
        late = rig.snapshot_now()["outputs"]["o-hue"]["extra"]["runner"]["late_ms"]
        assert late["p50"] is not None and late["p50"] < 5.0, late
    finally:
        rig.stop()


def test_stop_runners_then_drivers_and_gathers_awaitables():
    class AsyncStop(FakeDriver):
        def stop(self, timeout=2.0):
            EVENTS.append(("stop", self.output_id))

            async def done():
                await asyncio.sleep(0.01)
                EVENTS.append(("stopped", self.output_id))
            return done()

    closed = []

    class Nodes:
        def close(self):
            closed.append(True)

    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = make(reg, FakeEngine(), factories={"hue": lambda oid, **k: AsyncStop(oid, **k)})
    rig.start()
    rig._shared[om.NODES_KEY] = Nodes()
    wait_for(lambda: ("frame", "o-hue") in EVENTS, what="a frame")
    got = rig.stop()
    assert threads() == []
    i = EVENTS.index(("stop", "o-hue"))
    assert ("frame", "o-hue") not in EVENTS[i:]                         # the runner had stopped before the driver
    assert closed == []
    asyncio.run(got)
    assert EVENTS[-1] == ("stopped", "o-hue") and closed == [True]


def test_set_look_frame_view_and_failsafe():
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue"), "o-off": out("o-off", "artnet", enabled=False)}))
    eng = FakeEngine()
    rig = make(reg, eng)
    rig.start()
    try:
        rig.set_look(hue_depth=0.4, beat_lock=False)
        assert eng.looks == [{"hue_depth": 0.4, "beat_lock": False, "reset": False}]
        assert rig.frame_view("nope") is None
        assert rig.frame_view("o-off")["fixtures"] == {} and rig.frame_view("o-off")["why"] == "off"
        fv = wait_for(lambda: (lambda f: f if f["fixtures"] else None)(rig.frame_view("o-hue")), what="a frame")
        assert set(fv) == {"output", "seq", "t_send_ms", "age_s", "active", "why", "fixtures", "universe"}
        assert fv["fixtures"]["lamp.a"] == {"intensity": 0.5, "hex": "#804000", "rgb": [0.5, 0.25, 0.0],
                                            "dmx": [1, 2], "t_eval_ms": fv["fixtures"]["lamp.a"]["t_eval_ms"]}
        json.dumps(fv)
        assert rig.record_failsafe("nope") == {"recorded": False, "why": "unknown node nope"}
        assert rig.record_failsafe("n1")["recorded"] is False and rig.has_node("n1")
        assert rig.dmx_view() == {}
    finally:
        rig.stop()


def test_hue_routes_answer_off_without_a_hue_output_and_reach_the_driver():
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue", enabled=False)}))
    rig = make(reg, FakeEngine())
    rig.start()
    try:
        st = rig.hue_request(["lamp.a"], True)
        assert st["state"] == "off" and st["output"] == "o-hue" and st["enabled"] is False
        assert st["detail"] == "outputs.o-hue.enabled is false in config/room/fixtures.yaml"
        assert set(st) == set(contract.HueStatus.__annotations__)
        assert rig.hue_release(["lamp.a"]) is None and not rig.hue_running()
        with pytest.raises(ValueError, match="bad_request"):
            rig.hue_request("lamp.a", True)
        with pytest.raises(ValueError, match="bad_request"):
            rig.hue_request(["lamp.a"], "yes")
        reg.set(view({"o-hue": out("o-hue", "hue")}, rev="r2"))
        rig.tick(time.monotonic())
        assert rig.hue_running() and rig.hue_request(["lamp.a"], True)["state"] == "idle"
        assert rig.drivers_of("hue")[0].requests == [(["lamp.a"], True)]
        assert asyncio.run(rig.hue_release(None))["via"] == "show"
        assert rig.hue_release_timeout_s() == om.HUE_RELEASE_TIMEOUT_S
        rig.configure({"rig": {"enabled": True, "hue": {"release_timeout_s": 4}}})
        assert rig.hue_release_timeout_s() == 4.0
        rig.configure({"rig": {"enabled": True, "hue": {"release_timeout_s": 600}}})
        assert rig.hue_release_timeout_s() == contract.HUE_RELEASE_MAX_S      # room's adapter waits 20 s
        rig.configure({"rig": {"enabled": False}})
        assert rig.hue_status()["detail"] == "show.yaml rig.enabled is false: the rig is off"
    finally:
        rig.stop()


def test_two_hue_outputs_are_a_problem_line():
    reg = FakeRegistry(view({"a-hue": out("a-hue", "hue"), "b-hue": out("b-hue", "hue")}))
    rig = make(reg, FakeEngine())
    rig.start()
    try:
        assert any("2 hue outputs" in p for p in rig.snapshot()["problems"])
        assert rig.hue_request([], True)["output"] == "a-hue"
    finally:
        rig.stop()


# -- the real registry, engine and Art-Net driver, against an ephemeral sink -----------------------------------------
PAR = {"name": "Test RGB par", "availableChannels": {
    "Dimmer": {"capability": {"type": "Intensity"}},
    "Red": {"capability": {"type": "ColorIntensity", "color": "Red"}},
    "Green": {"capability": {"type": "ColorIntensity", "color": "Green"}},
    "Blue": {"capability": {"type": "ColorIntensity", "color": "Blue"}}},
    "modes": [{"name": "4ch", "channels": ["Dimmer", "Red", "Green", "Blue"]}]}


def test_the_real_pieces_send_to_a_sink_and_keep_a_virtual_universe(tmp_path):
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    sink.settimeout(0.2)
    port = sink.getsockname()[1]
    assert port != artnet_out.PORT
    (tmp_path / "types" / "ofl" / "t").mkdir(parents=True)
    (tmp_path / "types" / "ofl" / "t" / "par.json").write_text(json.dumps(PAR))
    (tmp_path / "patch.yaml").write_text("hue: {}\n")
    (tmp_path / "fixtures.yaml").write_text(f"""
version: 1
outputs:
  dmx-a: {{driver: artnet, enabled: true, node: sink, port_address: 1}}
  dmx-v: {{driver: artnet, enabled: true, node: previz, port_address: 2}}
nodes:
  sink: {{last_ip: 127.0.0.1, port: {port}}}
  previz: {{virtual: true}}
types:
  par: {{source: ofl, file: ofl/t/par.json, mode: 4ch}}
fixtures:
  p1: {{type: par, output: dmx-a, address: 1, house: {{intensity: 1.0, hex: "#ff0000"}}}}
  p2: {{type: par, output: dmx-v, address: 5, house: {{intensity: 0.5, hex: "#00ff00"}}}}
""")
    ctx = contract.BrainContext(publish=lambda m: None)
    rig = om.attach(ctx, fixtures_path=tmp_path / "fixtures.yaml", types_dir=tmp_path / "types",
                    latency_path=tmp_path / "latency.json", patch_path=tmp_path / "patch.yaml",
                    palette_fn=lambda t: None, features_fn=lambda: None,
                    driver_kw={"artnet": {"state_file": tmp_path / "nodes.json"}})
    rig.configure({"rig": {"enabled": True, "artnet": {"keepalive_s": 0.2, "idle_eval_hz": 20}}, "looks": {}})
    rig.start()
    got = []
    try:
        assert rig.snapshot()["registry"]["problems"] == [], rig.snapshot()["registry"]
        assert threads() == ["show-out-dmx-a", "show-out-dmx-v"]
        end = time.monotonic() + 2.0
        while time.monotonic() < end and len([d for d in got if d[8:10] == b"\x00\x50"]) < 3:
            try:
                got.append(sink.recv(1024))
            except socket.timeout:
                pass
        dmx = [d for d in got if d[8:10] == b"\x00\x50"]                 # ArtDmx (ArtPolls go there too)
        assert len(dmx) >= 3, got
        # idle (no features): each fixture's house look; universe 1 (Port-Address 1), 4 channels
        assert dmx[-1][14] == 1 and dmx[-1][15] == 0 and list(dmx[-1][18:22]) == [255, 255, 0, 0]
        v = wait_for(lambda: rig.dmx_view().get("previz"), what="the virtual node's universe")
        assert v["2"]["channels"][4:8] == [128, 0, 255, 0], v
        fv = rig.frame_view("dmx-v")
        assert fv["universe"]["virtual"] is True and fv["fixtures"]["p2"]["dmx"] == [128, 0, 255, 0]
        snap = rig.snapshot_now()
        assert snap["outputs"]["dmx-a"]["state"] in ("sending", "idle") and snap["nodes"]["previz"]["ip_from"] == \
            "virtual"
    finally:
        aw = rig.stop()
        if aw is not None:
            asyncio.run(aw)
        sink.close()


# -- README S5.14: the review fixes ----------------------------------------------------------------------------------------
class PerOutputEngine(FakeEngine):
    """takes_hit(d, output): only o-dmx-a's fixtures are in the hits selection."""

    def takes_hit(self, d, output_id=None):
        return d.get("strength", 1.0) >= 0.5 and output_id in (None, "o-dmx-a")


def test_a_hit_wakes_only_the_outputs_whose_fixtures_it_would_flash():
    reg = FakeRegistry(view({"o-dmx-a": out("o-dmx-a", "artnet"), "o-dmx-b": out("o-dmx-b", "artnet")}))
    rig = make(reg, PerOutputEngine())
    rig.start()
    try:
        a, b = sorted(rig.drivers_of("artnet"), key=lambda d: d.output_id)
        wait_for(lambda: a.frames and b.frames, what="the first frames")
        time.sleep(0.05)
        rig.on_hit({"t_ms": time.monotonic() * 1000.0, "band": "sub", "strength": 1.0})
        wait_for(lambda: a.plan_hits == 1, timeout=0.5, what="o-dmx-a pulled forward")
        time.sleep(0.1)
        assert b.plan_hits == 0                                          # its fixtures never flash: left on its grid
    finally:
        rig.stop()


class StateDriver(FakeDriver):
    """A driver with persist_state/restore_state (hue_out's, README S5.14)."""

    def __init__(self, oid, **kw):
        super().__init__(oid, **kw)
        self.asked: list = []
        self.restored: list = []

    def persist_state(self):
        return {"asked": list(self.asked)}

    def restore_state(self, saved):
        self.restored.append(saved)
        self.asked = list(saved.get("asked") or [])
        return True


def test_what_room_asks_while_the_hue_output_is_off_is_kept_for_on_again():
    fac = {"hue": lambda oid, **k: StateDriver(oid, kind="hue", **k)}
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue", enabled=False)}))
    rig = make(reg, FakeEngine(), factories=fac)
    persisted = []
    rig.ctx.persist = lambda: persisted.append(1)
    rig.start()
    try:
        st = rig.hue_request(["lamp.a", "lamp.b"], True)
        assert st["state"] == "off" and st["asked"] == ["lamp.a", "lamp.b"]  # the routes' lights (room's re-sync)
        assert rig.persist() == {"outputs": {"o-hue": {"asked": ["lamp.a", "lamp.b"]}}} and persisted
        assert rig.hue_release(["lamp.b"]) is None                              # nothing settling: Stage 3's answer
        assert rig.persist() == {"outputs": {"o-hue": {"asked": ["lamp.a"]}}}
        assert rig.hue_status()["asked"] == ["lamp.a"] and rig.hue_status()["wanted"] == []
        reg.set(view({"o-hue": out("o-hue", "hue")}, rev="r2"))
        rig.tick(time.monotonic())
        assert rig.drivers_of("hue")[0].restored == [{"asked": ["lamp.a"]}]     # on again: room's latest ask
    finally:
        rig.stop()


def test_a_release_right_after_the_hue_output_is_switched_off_waits_for_its_stop_and_the_settle():
    """68's contract: the output switched off stops streaming at once; a release then answers released only after
    the stop has finished and rig.hue.release_settle_s has passed (the bridge's own post-entertainment write)."""
    class SlowStop(FakeDriver):
        def stop(self, timeout=2.0):
            EVENTS.append(("stop", self.output_id))

            async def done():
                await asyncio.sleep(0.15)                              # the PUT stop
                EVENTS.append(("stopped", self.output_id))
            return done()

    async def main():
        reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
        rig = make(reg, FakeEngine(), factories={"hue": lambda oid, **k: SlowStop(oid, kind="hue", **k)},
                   settings={"rig": {"enabled": True, "hue": {"release_settle_s": 0.2}}})
        rig.ctx.loop = asyncio.get_running_loop()
        rig.start()
        try:
            assert rig.hue_running()
            reg.set(view({"o-hue": out("o-hue", "hue", enabled=False)}, rev="r2"))
            rig.tick(time.monotonic())                                  # switched off: stopped at once
            assert rig.drivers_of("hue") == [] and ("stop", "o-hue") in EVENTS
            assert rig.hue_running()                                    # still giving the lamps back
            t0 = time.monotonic()
            r = await rig.hue_release(["lamp.a"])
            assert r["released"] is True and r["via"] == "show" and r["state"] == "idle"
            assert ("stopped", "o-hue") in EVENTS and time.monotonic() - t0 >= 0.3        # the stop, then 0.2 s
            await asyncio.sleep(0.25)
            assert not rig.hue_running() and rig.hue_release(None) is None
        finally:
            got = rig.stop()
            if got is not None:
                await got
    asyncio.run(main())


def test_driver_state_survives_a_restart_and_off_and_on():
    fac = {"hue": lambda oid, **k: StateDriver(oid, kind="hue", **k)}
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = make(reg, FakeEngine(), factories=fac)
    rig.start()
    try:
        d = rig.drivers_of("hue")[0]
        assert rig.persist() == {"outputs": {"o-hue": {"asked": []}}}
        d.asked = ["lamp.a"]
        assert rig.persist() == {"outputs": {"o-hue": {"asked": ["lamp.a"]}}}
        reg.set(view({"o-hue": out("o-hue", "hue", enabled=False)}, rev="r2"))   # switched off: kept
        rig.tick(time.monotonic())
        assert rig.drivers_of("hue") == [] and rig.persist() == {"outputs": {"o-hue": {"asked": ["lamp.a"]}}}
        reg.set(view({"o-hue": out("o-hue", "hue")}, rev="r3"))                   # on again: handed back
        rig.tick(time.monotonic())
        d2 = rig.drivers_of("hue")[0]
        assert d2 is not d and d2.restored == [{"asked": ["lamp.a"]}] and d2.asked == ["lamp.a"]
    finally:
        rig.stop()
    saved = rig.persist()                                                         # the final state.json
    assert saved == {"outputs": {"o-hue": {"asked": ["lamp.a"]}}}
    json.dumps(saved)
    reg2 = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))                   # show_service restarted
    rig2 = make(reg2, FakeEngine(), factories=fac)
    rig2.restore(saved)
    rig2.start()
    try:
        assert rig2.drivers_of("hue")[0].restored == [{"asked": ["lamp.a"]}]
    finally:
        rig2.stop()
    rig3 = make(FakeRegistry(view({})), FakeEngine())
    rig3.restore({"outputs": "junk"})
    assert rig3.persist() is None
    rig3.restore(None)
    assert rig3.persist() is None


class SlowEngine(FakeEngine):
    def evaluate(self, oid, t, caps):
        time.sleep(0.002)                                                     # a 2 ms evaluate (GIL waits and all)
        return super().evaluate(oid, t, caps)


def test_a_runner_builds_ahead_by_its_own_evaluate_cost(monkeypatch):
    """README S5.14: the build-ahead is max(BUILD_AHEAD_S, evaluate p95 + the GIL switch interval), capped, so a slow
    evaluate doesn't make every frame late."""
    monkeypatch.setattr(om, "BUILD_AHEAD_EVERY", 8)
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = make(reg, SlowEngine())
    rig.start()
    try:
        r = rig._outs["o-hue"].runner
        assert r.ahead_s == om.BUILD_AHEAD_S
        wait_for(lambda: r._eval_n >= 16, what="frames")
        assert r.ahead_s == pytest.approx(min(om.BUILD_AHEAD_MAX_S, 0.002 + __import__("sys").getswitchinterval()),
                                          abs=0.0015)
        assert r.ahead_s <= om.BUILD_AHEAD_MAX_S
        assert rig.snapshot_now()["outputs"]["o-hue"]["extra"]["runner"]["build_ahead_ms"] > 1.0
    finally:
        rig.stop()


# -- README S5.23: the Lights page (registration, identify, follow, online) --------------------------------------------
class FakeSock:
    """artnet_lan's socket, faked: what would have gone out (nothing touches the network). ``fail``: every send raises
    it (an absent node: no route)."""

    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def setblocking(self, flag):
        pass

    def sendto(self, data, addr):
        if self.fail is not None:
            raise self.fail
        self.sent.append((bytes(data), addr))
        return len(data)

    def recvfrom(self, n):
        raise BlockingIOError

    def close(self):
        pass


class LightsEngine(FakeEngine):
    """A FakeEngine with the scenes builder's hand-off methods (looks.LookEngine.identify / set_follow)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.identified, self.follows = [], []

    def identify(self, fid, seconds, rgb=(255, 255, 255)):
        self.identified.append((fid, seconds))

    def set_follow(self, mapping):
        self.follows.append(mapping)


HAND = """
version: 1
outputs:
  o-hue: {driver: hue, enabled: false}
types:
  sm-hue: {source: builtin}
fixtures:
  hue.a: {type: sm-hue, output: o-hue, light: aaa, placement: above}
"""

BODY = {"label": "Ball spot", "type": "generic-rgbw", "node_ip": "192.0.2.10", "universe": 3, "address": 1,
        "zone": "north"}


def lights_rig(tmp_path, engine=None, sock=None, settings=None, **kw):
    """A Rig on the real registry (tmp files) and the real Art-Net driver on a fake socket."""
    (tmp_path / "fixtures.yaml").write_text(HAND)
    (tmp_path / "patch.yaml").write_text("hue: {}\n")
    ctx = contract.BrainContext(publish=lambda m: None)
    kw.setdefault("ping_fn", lambda ip: None)
    eng = {"engine": engine} if engine is not None else {"palette_fn": lambda t: {"*": [1.0, 0.0, 0.0]},
                                                         "features_fn": None}
    rig = om.attach(ctx, fixtures_path=tmp_path / "fixtures.yaml", types_dir=tmp_path / "types",
                    latency_path=tmp_path / "latency.json", patch_path=tmp_path / "patch.yaml",
                    driver_kw={"artnet": {"state_file": tmp_path / "nodes.json", "sock": sock or FakeSock()}},
                    follow_file=tmp_path / "state" / "follow.json", **eng, **kw)
    rig.configure(settings or {"rig": {"enabled": True, "artnet": {"keepalive_s": 0.2, "idle_eval_hz": 20}},
                               "looks": {"layers": []}})               # no layers: the palette's colour as it is
    return rig


def stop(rig):
    aw = rig.stop()
    if aw is not None:
        asyncio.run(aw)


def test_a_registered_light_starts_sending_at_once_and_stops_when_removed(tmp_path):
    sock = FakeSock()
    rig = lights_rig(tmp_path, sock=sock)
    rig.start()
    try:
        assert threads() == [] and rig.snapshot_now()["fixtures"][0]["id"] == "hue.a"
        got = rig.add_fixture(BODY)
        fid, oid = "dmx.ball-spot", "dmx-192-0-2-10-u3"
        assert got["problems"] == [] and got["fixture"]["id"] == fid
        assert threads() == [f"show-out-{oid}"]                          # no debounce: it runs at once
        row = got["fixture"]
        assert set(row) == set(contract.RigFixtureRow.__annotations__)
        assert (row["node_ip"], row["universe"], row["address"], row["footprint"], row["driver"]) == \
            ("192.0.2.10", 3, 1, 4, "artnet")
        assert row["editable"] is True and row["type_label"] == "RGBW light (4 channels)" and row["zone"] == "north"
        rig.tick(time.monotonic())                                      # show-brain's 1 Hz: an ArtPoll goes out
        dmx = wait_for(lambda: [d for d, a in sock.sent if d[8:10] == b"\x00\x50"], what="ArtDmx frames")
        assert all(a == ("192.0.2.10", 6454) for _, a in sock.sent)
        assert any(d[8:10] == b"\x00\x20" for d, _ in sock.sent)        # ArtPoll, unicast to the node
        assert dmx[-1][14] == 3 and dmx[-1][15] == 0 and list(dmx[-1][18:22]) == [255, 0, 0, 0]   # red on RGBW
        rows = {r["id"]: r for r in wait_for(lambda: [r for r in rig.fixture_rows() if r["last_rgb"]] and
                                             rig.fixture_rows(), what="a row with its last colour")}
        assert rows[fid]["last_rgb"] == "#ff0000" and rows["hue.a"]["editable"] is False
        assert rows["hue.a"]["node_ip"] is None and rows["hue.a"]["role"] is None      # no lamps given: own role only
        assert rig.snapshot_now()["outputs"][oid]["state"] == "sending"
        if callable(getattr(rig.engine, "identify", None)):            # the real engine's hand-off (looks.py)
            assert rig.identify(fid, 0.5) == {"ok": True, "seconds": 0.5}
            wait_for(lambda: [d for d, _ in sock.sent[-3:] if list(d[18:22]) == [0, 0, 0, 255]],
                     what="full white on RGBW (the W channel)")
            wait_for(lambda: list(sock.sent[-1][0][18:22]) == [255, 0, 0, 0], what="back to red after it")
        if callable(getattr(rig.engine, "set_follow", None)):
            assert rig.set_follow({fid: "off"})["accepted"] is True
            wait_for(lambda: list(sock.sent[-1][0][18:22]) == [0, 0, 0, 0], what="dark when it doesn't follow")
            rig.set_follow({fid: None})                                 # forgotten: it follows again
            wait_for(lambda: list(sock.sent[-1][0][18:22]) == [255, 0, 0, 0], what="red again")
        got = rig.remove_fixture(fid)
        assert got == {"removed": fid, "outputs": [oid], "nodes": ["node-192-0-2-10"], "types": ["generic-rgbw"]}
        assert threads() == [] and [r["id"] for r in rig.fixture_rows()] == ["hue.a"]
    finally:
        stop(rig)


def test_sending_to_an_absent_node_is_harmless(tmp_path):
    rig = lights_rig(tmp_path, sock=FakeSock(fail=OSError(113, "No route to host")))
    rig.start()
    try:
        rig.add_fixture(BODY)
        rig.tick(time.monotonic())
        d = rig.drivers_of("artnet")[0]
        wait_for(lambda: d.send_errors >= 3, what="failed sends")
        st = rig.snapshot_now()["outputs"]["dmx-192-0-2-10-u3"]
        assert st["state"] == "error" and "No route to host" in st["detail"]
        assert st["extra"]["runner"]["alive"] is True and st["extra"]["runner"]["errors"] == 0   # it carries on
    finally:
        stop(rig)


def test_registration_refusals_and_a_rig_without_a_registry(tmp_path):
    from show_service import fixtures as fx
    rig = lights_rig(tmp_path)
    rig.start()
    try:
        with pytest.raises(fx.Refused) as e:
            rig.add_fixture({**BODY, "address": 510})
        assert e.value.code == "not_added" and e.value.extra["problems"]
        with pytest.raises(fx.Refused) as e:
            rig.update_fixture("hue.a", {"label": "x"})
        assert e.value.code == "in_hand_file"
        assert not (tmp_path / fx.ADDED_NAME).exists() and threads() == []
        fid = rig.add_fixture(BODY)["fixture"]["id"]
        got = rig.update_fixture(fid, {"universe": 4, "label": "Spot"})
        assert got["fixture"]["universe"] == 4 and got["fixture"]["label"] == "Spot"
        assert threads() == ["show-out-dmx-192-0-2-10-u4"]
        assert [t["id"] for t in rig.types_view()][:1] == ["generic-dimmer"]
    finally:
        stop(rig)
    bare = make(FakeRegistry(view({})), FakeEngine())
    with pytest.raises(ValueError, match="^no_registry: "):
        bare.add_fixture(BODY)
    assert bare.types_view()[0]["id"] == "generic-dimmer"


def test_identify_through_a_running_output(tmp_path):
    eng = LightsEngine()
    rig = lights_rig(tmp_path, engine=eng)
    rig.start()
    try:
        assert rig.identify("nope") == {"ok": False, "why": "There's no light 'nope' in the rig."}
        no = rig.identify("hue.a")
        assert no["ok"] is False and "outputs.o-hue.enabled is false" in no["why"]
        fid = rig.add_fixture(BODY)["fixture"]["id"]
        assert rig.identify(fid) == {"ok": True, "seconds": om.IDENTIFY_S}
        assert rig.identify(fid, 600) == {"ok": True, "seconds": om.IDENTIFY_MAX_S}
        assert eng.identified == [(fid, om.IDENTIFY_S), (fid, om.IDENTIFY_MAX_S)]
        with pytest.raises(ValueError, match="^bad_request"):
            rig.identify(fid, "soon")
        del LightsEngine.identify                                       # an engine without it: degrade
        try:
            assert rig.identify(fid) == {"ok": False, "why": "The look engine can't flash a light yet."}
        finally:
            LightsEngine.identify = lambda self, fid, seconds, rgb=(255, 255, 255): self.identified.append(
                (fid, seconds))
    finally:
        stop(rig)


def test_identify_on_a_hue_lamp_only_while_it_follows():
    class Hue(FakeDriver):
        wanted: list = []

        def hue_status(self):
            return {**om.hue_off(self.output_id, "fake"), "state": "streaming", "wanted": list(self.wanted)}
    eng = LightsEngine()
    reg = FakeRegistry(view({"o-hue": out("o-hue", "hue")}))
    rig = make(reg, eng, factories={"hue": lambda oid, **k: Hue(oid, kind="hue", **k)})
    rig.start()
    try:
        assert rig.identify("lamp.a")["ok"] is False and eng.identified == []
        Hue.wanted = ["lamp.a"]
        assert rig.identify("lamp.a", 2) == {"ok": True, "seconds": 2.0} and eng.identified == [("lamp.a", 2.0)]
        assert rig.identify("screen.x") == {"ok": False, "why": "A screen isn't flashed from here."}
    finally:
        Hue.wanted = []
        rig.stop()


def test_follow_is_validated_merged_saved_and_handed_to_the_engine(tmp_path):
    eng = LightsEngine()
    rig = lights_rig(tmp_path, engine=eng)
    rig.start()
    try:
        assert eng.follows == [{}]                                      # applied after the first configure
        fid = rig.add_fixture(BODY)["fixture"]["id"]
        got = rig.set_follow({fid: "dmx", "dmx.later": {"hex": "#FFAA00", "bri": 40}})
        assert got == {"accepted": True, "fixtures": {fid: "dmx", "dmx.later": {"hex": "#ffaa00", "bri": 40}},
                       "unknown": ["dmx.later"], "saved": True}
        assert eng.follows[-1] == got["fixtures"]
        saved = json.loads((tmp_path / "state" / "follow.json").read_text())
        assert saved == {"v": 1, "fixtures": got["fixtures"]}
        for bad in ({fid: "on"}, {fid: {"hex": "red"}}, {fid: {"hex": "#000000", "bri": 101}},
                    {fid: {"hex": "#000000", "extra": 1}}, ["dmx"]):
            with pytest.raises(ValueError):
                rig.set_follow(bad)
        assert rig.follow_view()["fixtures"] == got["fixtures"]        # all or nothing: unchanged
        assert rig.set_follow({"dmx.later": None, fid: {"hex": "#102030"}})["fixtures"] == \
            {fid: {"hex": "#102030", "bri": 100}}
        assert next(r for r in rig.fixture_rows() if r["id"] == fid)["follow"] == {"hex": "#102030", "bri": 100}
        n = len(eng.follows)
        rig.configure({"rig": {"enabled": True}, "looks": {"master": 0.5}})   # a new look config: re-applied
        assert len(eng.follows) == n + 1 and eng.follows[-1] == {fid: {"hex": "#102030", "bri": 100}}
        rig.remove_fixture(fid)                                         # its follow entry goes with it
        assert rig.follow_view() == {"fixtures": {}}
    finally:
        stop(rig)
    (tmp_path / "state" / "follow.json").write_text(json.dumps({"v": 1, "fixtures": {"a": "off", "b": "junk"}}))
    rig2 = lights_rig(tmp_path, engine=LightsEngine())                   # a restart reads what was kept
    assert rig2.follow_view() == {"fixtures": {"a": "off"}}
    rig3 = make(FakeRegistry(view({})), FakeEngine())                    # no follow_file: memory only, no engine
    assert rig3.set_follow({"x": "dmx"})["saved"] is False and rig3.follow_view() == {"fixtures": {"x": "dmx"}}


def test_online_pings_each_node_address_off_the_frame_path(tmp_path):
    asked = []

    def ping(ip):
        asked.append((threading.current_thread().name, ip))
        return ip == "192.0.2.10"
    rig = lights_rig(tmp_path, ping_fn=ping, online_check_s=0.05)
    rig.start()
    try:
        rig.add_fixture(BODY)
        rig.add_fixture({**BODY, "label": "Far", "node_ip": "192.0.2.99"})
        rows = wait_for(lambda: (lambda rs: rs if all(r["online"] is not None for r in rs if r["node_ip"]) else None)(
            rig.fixture_rows()), what="both nodes checked")
        on = {r["id"]: r["online"] for r in rows}
        assert on == {"hue.a": None, "dmx.ball-spot": True, "dmx.far": False}
        assert {n for n, _ in asked} == {"show-rig-online"} and {ip for _, ip in asked} >= {"192.0.2.10", "192.0.2.99"}
        assert rig.snapshot_now()["online"]["rounds"] >= 1
    finally:
        stop(rig)
    time.sleep(0.1)
    assert not [t for t in threading.enumerate() if t.name == "show-rig-online" and t.is_alive()]


def test_ping_once():
    assert om.ping_once("not-an-ip") is None and om.ping_once("-c9") is None
    assert om.ping_once("127.0.0.1") in (True, None)                     # None: no ping on this machine


# -- movers: the jog, busy frames and the live view (docs/LIGHTS.md §5.8) ----------------------------------------------
class RateDriver(FakeDriver):
    """Plans at 100 Hz while a frame is active (or busy), 2 Hz when idle: artnet's grid vs its keepalive."""

    def plan(self, t_last, now, active):
        return max(now, t_last + (0.01 if active else 0.5))


class FakeStore:
    """space.Store's shape: poll and view (a SpaceView with a revision)."""

    def __init__(self):
        self.v, self.polls = {"revision": "s1", "appliances": {}}, []

    def poll(self, force=False):
        self.polls.append(force)
        return False

    def view(self):
        return self.v


def test_jog_holds_a_light_at_the_active_rate_and_frame_view_shows_its_attrs():
    from show_service import fixtures as fxmod
    from show_service.looks import LookEngine
    mover_t = fxmod.BUILTIN_TYPES["generic-mover-full"]
    v = view({"o-dmx": out("o-dmx", "artnet")})
    v["types"] = {mover_t["id"]: mover_t}
    v["fixtures"] = {"mv": {"id": "mv", "label": "Mover", "type": mover_t["id"], "kind": "lamp", "output": "o-dmx",
                            "address": 1, "pin": {}, "wb": {}, "setup": {}, "latency": {}}}
    eng = LookEngine(features_fn=lambda: None, light_scenes=False, space_mod=False)   # stale: inactive frames
    store = FakeStore()
    rig = make(FakeRegistry(v), eng, factories={"artnet": lambda oid, **k: RateDriver(oid, kind="artnet", **k)},
               settings={"rig": {"enabled": True}, "looks": {"layers": []},
                         "lights": {"jog": {"idle_s": 30, "smooth_ms": 0}, "motion": {"accel_s": 0}}},
               space_store=store)
    rig.start()
    try:
        d = rig.drivers_of("artnet")[0]
        assert rig.snapshot_now()["space"]["revision"] == "s1" and store.polls
        time.sleep(0.3)
        idle = len(d.frames)
        assert idle <= 2                                                 # inactive and nothing held: keepalive only
        with pytest.raises(ValueError, match="^bad_request"):
            rig.jog("mv", ["pan"])
        got = rig.jog("mv", {"pan": 45.0, "tilt": -10.0, "sparkle": 1})
        assert got["held"] is True and got["idle_s"] == 30 and got["unknown"] == ["sparkle"]
        assert got["target"]["pan"] == 45.0 and rig.holds()["mv"]["target"]["tilt"] == -10.0
        time.sleep(0.6)
        assert len(d.frames) - idle >= 20                                # held: the active rate while inactive
        fv = rig.frame_view("o-dmx")
        assert fv["active"] is False and fv["fixtures"]["mv"]["attrs"]["pan"] == 45.0
        json.dumps(fv)
        assert rig.caps_of("mv")["moves"] is True and rig.caps_of("nope") is None
        lv = rig.live_view()
        assert lv["fixtures"]["mv"]["pan"] == 45.0 and lv["fixtures"]["mv"]["held"] is True
        assert rig.jog("nope", {})["why"] == "There's no light 'nope' in the rig."
        assert rig.jog("mv", release=True)["held"] is False and rig.holds() == {}
        rig.reload_now()
        assert store.polls[-1] is True                                   # an API write re-reads the space at once
    finally:
        rig.stop()


def test_jog_degrades_without_an_engine_that_holds():
    v = view({"o-hue": out("o-hue", "hue")})
    rig = make(FakeRegistry(v), FakeEngine(), space_store=False)
    rig.start()
    try:
        assert rig.jog("lamp.a", {"intensity": 1}) == {"held": False, "until_s": None, "idle_s": om.JOG_IDLE_S,
                                                       "target": {}, "now": {}, "unknown": [],
                                                       "why": "This light can't be moved from here yet."}
        assert rig.holds() == {} and rig.caps_of("lamp.a") is None
        assert rig.live_view()["fixtures"] == {}
        assert rig._engine_kw == frozenset()                             # a fake engine's configure: lamps only
    finally:
        rig.stop()
