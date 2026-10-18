"""Render workers (4K60 plan change 3), no GPU: the shared plumbing (framing, handle table, lifecycle), the seam's
encoding end to end (EngineProxy -> the worker's decoding -> engine commands), stats mirrored onto the stand-ins, and
the degrade path (a worker that cannot be spawned: the session renders in-process and says so)."""
from __future__ import annotations

import asyncio
import gc
import socket
import threading
import time
import types

import pytest

from stream_service import ipc
from stream_service.effects import EffectChain, EffectSlot, FxUpdate, SlotUpdate
from stream_service.ipc import BOUND, EXIT, IDLE, LINGERING, SPAWNING, Channel, Decoder, HandleTable, Lifecycle
from stream_service.modulation import Modulation, SessionRuntime
from stream_service.plugins.base import Effect, Source, Transition
from stream_service.renderer import Retire, StartTransition, UpdateParams


# -- framing --------------------------------------------------------------------------------------------------------
def test_frames_survive_any_split():
    frames = [ipc.pack(ipc.K_MSG, ipc.dumps(("hello", 0, {"pid": 1}))), ipc.pack(ipc.K_MEDIA, b"\x02\x01" + b"x" * 5000),
              ipc.pack(ipc.K_MSG, b"")]
    blob = b"".join(frames)
    for step in (1, 3, 7, 4096, len(blob)):
        d, got = Decoder(), []
        for i in range(0, len(blob), step):
            got += d.feed(blob[i:i + step])
        assert [k for k, _ in got] == [ipc.K_MSG, ipc.K_MEDIA, ipc.K_MSG]
        assert ipc.loads(got[0][1]) == ("hello", 0, {"pid": 1}) and got[1][1] == b"\x02\x01" + b"x" * 5000
        assert got[2][1] == b"" and not d.buf


def test_a_broken_stream_is_not_read_forever():
    with pytest.raises(ValueError):
        Decoder().feed(ipc.HDR.pack(ipc.MAX_FRAME + 1, ipc.K_MSG))


def _pair(on_b=None, on_close_b=None, lossy_keep=ipc.LOSSY_KEEP):
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    ca = Channel(a, "a", lambda k, p: None, lossy_keep=lossy_keep)
    cb = Channel(b, "b", on_b, on_close_b)
    return ca, cb


def test_channel_carries_messages_and_media_in_order():
    got, done = [], threading.Event()

    def on_b(kind, payload):
        got.append((kind, ipc.loads(payload) if kind == ipc.K_MSG else payload))
        if len(got) == 4:
            done.set()

    ca, cb = _pair(on_b)
    ca.start(), cb.start()
    ca.send(("submit", 0, ("retire", 3)))
    ca.send_raw(ipc.K_MEDIA, b"\x02\x01\x00\x00\x00\x07frag")
    ca.send(("stop", 9, {"sid": "s-1"}))
    ca.send_lossy(("in", 0, ("scope", (1.0, None, None, -20.0))))
    assert done.wait(5)
    assert got[0] == (ipc.K_MSG, ("submit", 0, ("retire", 3))) and got[1] == (ipc.K_MEDIA, b"\x02\x01\x00\x00\x00\x07frag")
    assert got[2][1][0] == "stop" and got[3][1][0] == "in"
    ca.close(), cb.close()


def test_lossy_inputs_drop_the_oldest_never_commands():
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    ca = Channel(a, "a", lossy_keep=4)                 # not started: everything waits in its queues
    for i in range(10):
        ca.send_lossy(("in", 0, i))
    for i in range(3):
        ca.send(("cmd", 0, i))
    assert ca.dropped == 6 and ca.backlog() == 7
    assert [ipc.loads(f[ipc.HDR.size:])[2] for f in ca._lossy] == [6, 7, 8, 9]
    a.close(), b.close()


def test_the_peer_closing_is_reported_once():
    why, done = [], threading.Event()
    ca, cb = _pair(lambda k, p: None, lambda w: (why.append(w), done.set()))
    ca.start(), cb.start()
    ca.close()
    assert done.wait(5) and len(why) == 1
    cb.send(("late", 0, None))                         # after close: dropped, never raises
    cb.close()
    assert len(why) == 1


# -- the handle table -------------------------------------------------------------------------------------------
class Obj:
    pass


def test_handle_table_owns_until_given_away():
    t = HandleTable()
    a, b = Obj(), Obj()
    ha, hb = t.add(a), t.add(b)
    assert ha != hb and t.get(ha) is a and t.hid_of(b) == hb and t.hid_of_id(id(a)) == ha and t.owns(ha)
    assert t.give(ha) is a and not t.owns(ha) and t.get(ha) is a
    del a
    gc.collect()
    assert t.get(ha) is None and t.hid_of_id(ha) is None             # given away and gone: never kept alive here
    assert t.drop(hb) is b and t.get(hb) is None and t.drop(hb) is None
    c = Obj()
    hc = t.add(c, hid=40)
    assert hc == 40 and t.add(Obj()) == 41 and len(t) == 2
    owned = t.clear()
    assert c in owned and len(t) == 0 and t.get(40) is None


def test_handle_table_weak_entries_and_stale_ids():
    t = HandleTable()
    x = Obj()
    h = t.add(x, owned=False)                        # the control's stand-ins: weak from the start
    assert not t.owns(h) and t.items() == [(h, x)]
    oid = id(x)
    del x
    gc.collect()
    assert t.get(h) is None and t.hid_of_id(oid) is None and t.clear() == []


# -- the lifecycle ------------------------------------------------------------------------------------------------
def test_lifecycle_spawn_idle_bound_linger_exit():
    lc = Lifecycle(30.0, now=0.0)
    assert lc.state == SPAWNING and lc.free() and not lc.due(100.0)
    lc.hello(1.0)
    assert lc.state == IDLE and lc.ready and lc.left_s(11.0) == 20.0
    assert lc.due(31.0)                               # an idle worker (a warm hint nobody used) exits too
    assert lc.claim(5.0) and lc.state == BOUND and lc.binds == 1 and not lc.free() and not lc.claim(6.0)
    lc.release(10.0)
    assert lc.state == LINGERING and not lc.due(39.9) and lc.due(40.0)
    assert lc.claim(20.0) and lc.state == BOUND and lc.binds == 2
    lc.release(21.0)
    lc.gone(22.0, "linger over")
    assert lc.state == EXIT and lc.why == "linger over" and not lc.free() and not lc.claim(23.0)
    with pytest.raises(ValueError):
        lc.move(IDLE, 24.0)


def test_lifecycle_claimed_while_spawning():
    lc = Lifecycle(30.0, now=0.0)
    assert lc.claim(0.1) and lc.state == BOUND and not lc.ready
    lc.hello(1.0)
    assert lc.state == BOUND and lc.ready             # still the session's
    lc2 = Lifecycle(30.0, now=0.0)
    lc2.claim(0.1)
    lc2.release(0.5)                                  # its session ended before the hello
    assert lc2.state == SPAWNING
    lc2.hello(2.0)
    assert lc2.state == IDLE and lc2.due(32.0)


# -- the seam, end to end (no processes, no GPU) ------------------------------------------------------------------
class TSource(Source):
    name = "t-source"
    params = {"speed": {"type": "number", "default": 1.0, "minimum": 0, "maximum": 4, "x-bindable": True}}


class TTrans(Transition):
    name = "t-trans"
    params: dict = {}


class TFx(Effect):
    name = "t-fx"
    params = {"amount": {"type": "number", "default": 1.0, "minimum": 0, "maximum": 1}}


class FakeConn:
    n = 1
    pid = 4242

    def __init__(self):
        self.sent, self.calls = [], []
        self.ready = threading.Event()
        self.ready.set()
        self.dead = None
        self.life = Lifecycle(30.0, 0.0)

    def send(self, msg):
        self.sent.append(msg)

    def call(self, op, data, timeout=None):
        self.calls.append((op, data))
        return {}


class FakePool:
    def __init__(self):
        self.conn = FakeConn()
        self.watched = set()
        self.fallbacks = 0
        self.cold = []

    def acquire(self, gpu, proxy):
        return self.conn

    def release(self, conn, proxy):
        pass

    def post(self, fn, *a):
        fn(*a)


class FakeWorkerEngine:
    def __init__(self):
        self.cmds = []
        self._released = False
        self.ctx = None

    def submit(self, cmd):
        self.cmds.append(cmd)


def _proxy():
    from stream_service.profiles import PROFILES
    from stream_service.workers import EngineProxy
    listener = types.SimpleNamespace(mgr=types.SimpleNamespace(registry=None), screen="devtest", state="live")
    pool = FakePool()
    px = EngineProxy(profile=PROFILES["h264-720p30"], gpu=1, post=lambda fn, *a: fn(*a), listener=listener,
                     name="s-test", pool=pool)
    return px, pool.conn


def _worker():
    from stream_service.worker import Worker
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    c, d = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    w = Worker(1, a, c, config_dir=None, show_bus=None)
    w.engine, w.sid = FakeWorkerEngine(), "s-test"
    w.runtime = SessionRuntime(None)
    return w, (a, b, c, d)


def _deliver(w, msg):
    w._on_frame(ipc.K_MSG, ipc.dumps(msg))


def test_commands_cross_the_seam_as_the_engine_expects_them():
    px, conn = _proxy()
    w, socks = _worker()
    rt = SessionRuntime(None)
    # stand-ins made and prepared on the control's side ...
    src = TSource()
    src.modulation = Modulation(rt, TSource.param_props(), {}, key="scene/t-source")
    px.prepare(src, {"speed": 2.0})
    trans = TTrans(TTrans.validate({"duration_s": 1.5}))
    px.prepare_transition(trans)
    eff = TFx()
    px.prepare_effect(eff, {"amount": 0.5})
    (op_s, spec_s), (op_t, spec_t), (op_e, spec_e) = conn.calls
    assert (op_s, op_t, op_e) == ("prepare", "prepare", "prepare")
    assert spec_s["cls"][:4] == (__name__, "TSource", "t-source", "source") and spec_s["params"] == {"speed": 2.0}
    assert spec_s["mod"]["key"] == "scene/t-source" and spec_t["params"]["duration_s"] == 1.5
    # ... and their real twins made by the worker from those specs (as its prepare does, minus the GPU)
    for spec in (spec_s, spec_t, spec_e):
        w.table.add(w._make(spec), hid=spec["hid"])
    w_src, w_trans, w_eff = (w.table.get(s["hid"]) for s in (spec_s, spec_t, spec_e))
    assert type(w_src).__name__ == "TSource" and w_src.modulation.key == "scene/t-source"
    assert w_src.modulation.runtime is w.runtime and w_trans.duration_s == 1.5
    # a scene with its own effect chain, on a beat
    slot = EffectSlot("s1-0", eff, name="t-fx", preset="p", stage="stylise",
                      modulation=Modulation(rt, TFx.param_props(), {}, key="effect/t-fx"), level=1.0, target=1.0,
                      where="scene")
    src.fx_chain = EffectChain([slot])
    px.submit(StartTransition("scene", 7, src, trans, at_mono=123.5))
    px.submit(UpdateParams(src, {"speed": 3.0}, bindings={}))
    for m in conn.sent:
        _deliver(w, m)
    st, up = w.engine.cmds
    assert isinstance(st, StartTransition) and (st.kind, st.tid, st.at_mono) == ("scene", 7, 123.5)
    assert st.source is w_src and st.transition is w_trans
    ws = st.source.fx_chain.slots[0]
    assert ws.effect is w_eff and ws.slot_id == "s1-0" and ws.where == "scene" and ws.modulation.runtime is w.runtime
    assert isinstance(up, UpdateParams) and up.source is w_src and up.params == {"speed": 3.0}
    assert not w.table.owns(spec_s["hid"]) and not w.table.owns(spec_e["hid"])      # the engine's now
    # the bus: an FX update with a new slot, then a retire
    conn.sent.clear()
    eff2 = TFx()
    px.prepare_effect(eff2, {"amount": 1.0})
    spec2 = conn.calls[-1][1]
    w.table.add(w._make(spec2), hid=spec2["hid"])
    slot2 = EffectSlot("e1", eff2, name="t-fx", preset="q", stage="stylise", level=0.0, target=1.0, fade_s=2.0)
    px.submit(FxUpdate(3, add=[slot2], update=[SlotUpdate("e0", remove=True)], fade_s=2.0, master={"amount": 1.0}))
    px.submit(Retire(trans))
    for m in conn.sent:
        _deliver(w, m)
    fx, ret = w.engine.cmds[2:]
    assert isinstance(fx, FxUpdate) and fx.rev == 3 and fx.add[0].effect is w.table.get(spec2["hid"])
    assert fx.update[0].id == "e0" and fx.update[0].remove and fx.master == {"amount": 1.0}
    assert isinstance(ret, Retire) and ret.obj is w_trans and w.table.get(spec_t["hid"]) is None
    assert px.table.get(spec_t["hid"]) is None                     # forgotten on both sides
    for s in socks:
        s.close()


def test_the_worker_stats_reach_the_stand_ins():
    px, conn = _proxy()
    rt = SessionRuntime(None)
    src = TSource()
    src.modulation = Modulation(rt, TSource.param_props(), {}, key="scene/t-source")
    px.prepare(src, {})
    hid = conn.calls[-1][1]["hid"]
    eff = TFx()
    px.prepare_effect(eff, {})
    slot = EffectSlot("e4", eff, name="t-fx", modulation=Modulation(rt, TFx.param_props(), {}), level=0.0,
                      target=1.0)
    px.submit(FxUpdate(1, add=[slot]))
    from stream_service.renderer import pace_clock
    t = pace_clock()
    px._on_stats({"t": t, "frames": 300, "summary": {"frames": 300, "rendered": 290}, "window": {"content_fps": 30},
                  "guard": {"trips": 0}, "server_ms": {"p50": 41.0}, "bus_level": 0.75,
                  "encoder_active": "nvenc", "layer": {"rate_div": 2, "cost_ema": 18.5},
                  "slots": {"e4": {"level": 0.6, "target": 1.0, "amount_now": 0.4, "failed": False, "slow": False,
                                   "removing": False, "last": {"amount": 0.4}, "rows": {"amount": {"follows": True}}}},
                  "mods": {hid: {"last": {"speed": 1.7}, "colour_k": 0.25, "describe": [{"param": "speed"}]}}})
    assert slot.level == 0.6 and slot.state == "fading_in" and slot.modulation.last == {"amount": 0.4}
    assert slot.modulation.section_row("amount") == {"follows": True} and slot.modulation.section_row("x") == {}
    assert src.modulation.describe() == [{"param": "speed"}] and src.modulation.colour_amount() == 0.25
    assert px.bus.level == 0.75 and px.server_ms.summary()["p50"] == 41.0 and px.encoder_active == "nvenc"
    assert px.root.rate_div == 2 and px.root.cost_ema == 18.5 and px.window_stats()["content_fps"] == 30
    e = px.summary()
    assert e["rendered"] == 290 and e["late_frames"] == 0 and e["render_ms"]["n"] == 0
    assert 300 <= px.frames <= 302                                    # extrapolated from the sample on the same clock


def test_runtime_targets_and_dropped_stand_ins_are_synced_at_the_tick():
    px, conn = _proxy()
    s = px.listener
    s.runtime = SessionRuntime(None)
    s.sampler = types.SimpleNamespace(lead=0.12, follow="extrapolate")
    hooks = []
    s._frame_hook = hooks.append
    s.runtime.palette = "ember"
    px.tick(1.0)
    sync = [m for m in conn.sent if m[0] == "sync"]
    assert hooks == [1.0] and sync[0][2]["palette"] == "ember" and sync[0][2]["lead"] == 0.12
    conn.sent.clear()
    px.tick(1.1)
    assert not conn.sent                                            # nothing moved: nothing sent
    s.runtime.speed_target = 1.5
    obj = TFx()
    px.prepare_effect(obj, {})
    hid = conn.calls[-1][1]["hid"]
    del obj
    gc.collect()
    px.tick(1.2)
    assert ("sync", 0, {"sid": "s-test", "speed_target": 1.5}) in conn.sent and ("gone", 0, [hid]) in conn.sent


# -- the degrade path, through the API -----------------------------------------------------------------------------
async def test_a_worker_that_cannot_spawn_leaves_the_session_in_process(monkeypatch, tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from stream_service import sessions as sessions_mod
    from stream_service import workers as workers_mod
    from stream_service.server import MANAGER, make_app
    from stream_service.switches import StreamSwitches

    from .test_sessions import REPO_CONFIG, FakeEngine, body, make_ready

    FakeEngine.instances.clear()
    monkeypatch.setattr(sessions_mod, "Engine", FakeEngine)

    def no_spawn(self, *a, **k):
        raise OSError("no such interpreter")

    monkeypatch.setattr(workers_mod.WorkerConn, "spawn", no_spawn)
    app = make_app(config_dir=REPO_CONFIG, max_sessions=3, workers=True)
    p = tmp_path / "stream_switches.json"
    p.write_text('{"screens": {"devtest": {"workers": "proc"}}}')
    async with TestClient(TestServer(app)) as c:
        mgr = c.app[MANAGER]
        mgr.switches = StreamSwitches(p)
        events = await c.ws_connect("/api/events")
        r = await c.post("/api/sessions", json=body())
        assert r.status == 201 and (await r.json())["state"] == "starting"      # 201 at starting, start goes on
        sid = (await r.json())["session"]
        s = mgr.sessions[sid]
        for _ in range(100):
            if isinstance(s.engine, FakeEngine) and s.engine.started_with is not None:
                break
            await asyncio.sleep(0.01)
        assert isinstance(s.engine, FakeEngine) and s.engine.started_with is not None
        assert s.engine.worker["mode"] == "inproc" and "spawn failed" in s.engine.worker["fallback"]
        await make_ready(s)
        r = await c.get(f"/api/sessions/{sid}")
        assert (await r.json())["worker"]["mode"] == "inproc" and mgr.workers.fallbacks == 1
        seen = []
        t_end = time.monotonic() + 2
        while time.monotonic() < t_end and not any(e.get("type") == "session.warning" for e in seen):
            try:
                seen.append(await events.receive_json(timeout=0.5))
            except asyncio.TimeoutError:
                break
        assert any(e.get("type") == "session.warning" and "render worker unavailable" in e.get("detail", "")
                   for e in seen)
        r = await c.get("/api/state")
        assert (await r.json())["workers"]["fallbacks"] == 1
        r = await c.post("/api/workers/warm", json={"screen": "north-screen"})
        assert (await r.json()) == {"screen": "north-screen", "workers": "inproc", "spawned": False}
        await events.close()


async def test_inproc_screens_never_touch_the_pool(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from stream_service import sessions as sessions_mod
    from stream_service.server import MANAGER, make_app

    from .test_sessions import REPO_CONFIG, FakeEngine, body

    FakeEngine.instances.clear()
    monkeypatch.setattr(sessions_mod, "Engine", FakeEngine)
    app = make_app(config_dir=REPO_CONFIG, max_sessions=3, workers=True)
    async with TestClient(TestServer(app)) as c:
        mgr = c.app[MANAGER]
        r = await c.post("/api/sessions", json=body(screen="north-screen"))
        assert r.status == 201
        s = mgr.sessions[(await r.json())["session"]]
        assert isinstance(s.engine, FakeEngine) and s.engine.started_with is not None     # started inside the POST
        assert mgr.workers.conns == [] and mgr.workers.spawned == 0
        r = await c.get(f"/api/sessions/{s.id}")
        assert (await r.json())["worker"] is None
