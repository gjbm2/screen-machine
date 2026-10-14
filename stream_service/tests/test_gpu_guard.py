"""GPU frame-time guard (guard.py), the exit from a frozen frame (renderer.py) and the vetted pool for the directors
(sessions.py auto_ok, library.Calibration) -- a fake GPU with a fake clock, NumPy layers, the fake engine; no GPU."""
from __future__ import annotations

import asyncio
import json
import os
import time

import numpy as np
import pytest

from stream_service import library as libmod
from stream_service.director import Director, validate_direction
from stream_service.effects import EffectChain, EffectLayer, EffectSlot, MasterBus
from stream_service.fx_director import FxDirector
from stream_service.guard import MAX_GPU_BACKLOG, TRIP_FRAME_MS, GpuTimer, SlowPlugin, trip_budget_ms
from stream_service.layers import FrameCtx, SourceLayer, TransitionLayer
from stream_service.profiles import PROFILES
from stream_service.renderer import Engine, EngineListener, StartTransition
from stream_service.server import MANAGER

from .test_cue import AUTO, cue, live
from .test_layers import Const, Mix, Pool, Recorder
from .test_sessions import AUTH, TOKEN, client, create  # noqa: F401 (fixture)

FPS = 30


# -- a fake GPU: serial work, events reached when the work before them is done --------------------------------
class FakeEv:
    def __init__(self):
        self.t = None


class FakeGpu:
    """Work runs back to back from when it is queued; an event completes when the GPU reaches it; ``now`` is the
    host clock (seconds), advanced by the test."""

    def __init__(self):
        self.now = 0.0
        self.busy_until = 0.0
        self.queries = 0

    def new(self):
        return FakeEv()

    def record(self, ev):
        ev.t = max(self.busy_until, self.now)

    def spend(self, ms):
        self.busy_until = max(self.busy_until, self.now) + ms / 1000.0

    def done(self, ev):
        self.queries += 1
        return ev.t is not None and ev.t <= self.now + 1e-12

    def elapsed_ms(self, a, b):
        return (b.t - a.t) * 1000.0

    def clock(self):
        return self.now


class Heavy:
    """A fake plug-in that costs ``ms`` of GPU time per render (a list: per call)."""

    def __init__(self, gpu, ms, name="st_heavy", value=1.0):
        self.gpu, self.ms, self.name, self.value = gpu, ms, name, value
        self.renders = 0
        self.closed = False
        self.duration_s, self.easing = 3.0, "linear"

    def cost(self):
        return self.ms[min(self.renders, len(self.ms) - 1)] if isinstance(self.ms, list) else self.ms

    def render(self, *args):
        self.gpu.spend(self.cost())
        self.renders += 1
        out = args[-1]
        if len(args) == 4 and isinstance(args[0], float) and isinstance(args[1], np.ndarray):   # transition
            out[...] = args[1] + (args[2] - args[1]) * args[0]
        elif len(args) == 4:                                                                       # effect
            out[...] = args[2] + 1.0
        else:
            out[...] = self.value

    def close(self):
        self.closed = True


class Internal(Heavy):
    internal = True


def drive(root, timer, gpu, n0, n1, *, guard=True, fps=FPS, until=None):
    """The engine's render loop in miniature: poll, back-pressure (repeat), render.  -> (values, errors, repeats)"""
    out = np.zeros((1, 1, 4))
    vals, errors, repeats = {}, [], 0
    for n in range(n0, n1):
        gpu.now = n / fps
        fc = FrameCtx(n, fps, Pool(), timer=timer)
        timer.poll(fc)
        if guard and timer.pending >= timer.backlog_limit():
            repeats += 1
        else:
            timer.frame_begin()
            root.render(fc, out)
            timer.frame_end()
            root = root.collapse(fc)
        vals[n] = float(out[0, 0, 0])
        errors += fc.errors
        if until is not None and until(errors):
            break
    return root, vals, errors, repeats


def timer_for(gpu, fps=FPS):
    return GpuTimer(gpu, fps, clock=gpu.clock)


# -- the trip ----------------------------------------------------------------------------------------------------
def test_sustained_over_budget_scene_trips_within_half_a_second_and_goes_black():
    gpu = FakeGpu()
    timer = timer_for(gpu)
    src = Heavy(gpu, 80.0)                               # budget at 30 fps: 1.5 x 33.3 = 50 ms
    layer = SourceLayer(src, 0.0)
    _, vals, errors, repeats = drive(layer, timer, gpu, 0, 60, until=lambda e: bool(e))
    assert len(errors) == 1
    kind, obj, exc, action = errors[0]
    assert kind == "source" and obj is src and action == "solid" and isinstance(exc, SlowPlugin)
    assert exc.why == "sustained" and exc.ms == pytest.approx(80.0) and exc.budget_ms == pytest.approx(50.0)
    assert gpu.now <= 0.8                                # 0.5 s of over-budget GPU time, read a frame late
    assert layer.failed and layer.slow and repeats > 0   # the back-pressure kept the GPU at most 2 frames behind
    n = max(vals) + 1
    _, vals, errors, _ = drive(layer, timer, gpu, n, n + 40)
    assert errors == [] and list(vals.values())[-1] == 0.0             # black, and the plug-in is not rendered
    assert timer.trips == 1 and timer.stats()["last_trip"]["why"] == "sustained"


def test_a_frame_still_running_after_half_a_second_trips_and_only_one_is_queued():
    gpu = FakeGpu()
    timer = timer_for(gpu)
    src = Heavy(gpu, 1400.0)                             # the 20:57 shader (1.4 s per frame)
    layer = SourceLayer(src, 0.0)
    _, _, errors, repeats = drive(layer, timer, gpu, 0, 60, until=lambda e: bool(e))
    exc = errors[0][2]
    assert exc.why == "stuck" and exc.ms >= TRIP_FRAME_MS
    assert gpu.now <= 0.6 + 1e-9                         # tripped while the frame still runs (seen ~33 ms late)
    assert src.renders == 1                              # a new plug-in is on probation: nothing more was queued
    _, _, errors, _ = drive(layer, timer, gpu, 60, 90)
    assert errors == [] and src.renders == 1


def test_a_single_slow_frame_read_late_trips_as_frame():
    gpu = FakeGpu()
    timer = timer_for(gpu)
    src = Heavy(gpu, 900.0)
    layer = SourceLayer(src, 0.0)
    fc = FrameCtx(0, FPS, Pool(), timer=timer)
    timer.frame_begin()
    layer.render(fc, np.zeros((1, 1, 4)))
    timer.frame_end()
    gpu.now = 1.0                                        # the next poll comes after the frame finished
    fc = FrameCtx(30, FPS, Pool(), timer=timer)
    timer.poll(fc)
    assert fc.errors[0][2].why == "frame" and fc.errors[0][2].ms == pytest.approx(900.0) and layer.failed


def test_fast_plugins_spikes_and_internal_stills_never_trip():
    gpu = FakeGpu()
    timer = timer_for(gpu)
    spiky = Heavy(gpu, [5.0, 5.0, 120.0, 5.0, 5.0, 300.0] + [5.0] * 100)
    _, _, errors, repeats = drive(SourceLayer(spiky, 0.0), timer, gpu, 0, 100)
    assert errors == [] and timer.trips == 0 and timer.pending <= 1
    still = Internal(gpu, 2000.0, name="image")
    t2 = timer_for(gpu)
    drive(SourceLayer(still, 0.0), t2, gpu, 100, 140, guard=False)
    assert t2.samples == 0 and t2.trips == 0             # internal: never timed
    # steady state polls only the frame's end event: tiny overhead
    gpu2 = FakeGpu()
    t3 = timer_for(gpu2)
    drive(SourceLayer(Heavy(gpu2, 2.0), 0.0), t3, gpu2, 0, 100)
    assert gpu2.queries <= 100 and t3.samples == 99 and t3.stats()["frame_gpu_ms"] == pytest.approx(2.0)


def test_slow_transition_finishes_as_a_cut():
    gpu = FakeGpu()
    timer = timer_for(gpu)
    rec = Recorder()
    t = Heavy(gpu, 70.0, name="glt_slow")
    tl = TransitionLayer(SourceLayer(Const(0.0), 0.0), SourceLayer(Const(10.0), 0.0), t, 0, kind="scene", tid=7,
                         notify=rec)
    root, vals, errors, _ = drive(tl, timer, gpu, 0, 90)
    tripped = [e for e in errors if e[0] == "transition"]
    assert len(tripped) == 1 and tripped[0][3] == "cut" and tripped[0][2].why == "sustained"
    done = [e for e in rec.events if e[1] == "done"]
    assert len(done) == 1 and not done[0][4] and done[0][2] < 1.5      # done long before its 3 s
    assert isinstance(root, SourceLayer) and list(vals.values())[-1] == 10.0 and t.closed      # retired


def test_slow_effects_are_dropped_from_the_bus_and_the_scene_chain():
    gpu = FakeGpu()
    timer = timer_for(gpu)
    slow_bus = Heavy(gpu, 90.0, name="isf_slow")
    slow_bus.stage, slow_bus.step_params = "colour", (lambda dt: {"amount": 1.0})
    bus = MasterBus()
    bus.chain.add(EffectSlot("e1", slow_bus, name="isf_slow", stage="colour", where="bus"))
    bus.open(0.0)
    slow_scene = Heavy(gpu, 90.0, name="isf_scene")
    slow_scene.stage, slow_scene.step_params = "colour", (lambda dt: {"amount": 1.0})
    scene = EffectLayer(SourceLayer(Const(1.0), 0.0),
                        EffectChain([EffectSlot("s1", slow_scene, name="isf_scene", stage="colour", where="scene")]))

    class Root:
        def render(self, fc, out):
            bus.render(fc, lambda buf: scene.render(fc, buf), out)

        def collapse(self, fc):
            return self

    _, vals, errors, _ = drive(Root(), timer, gpu, 0, 120)
    acts = {(e[0], e[1].slot_id, e[3]) for e in errors}
    assert acts == {("effect", "e1", "removed"), ("effect", "s1", "removed")}
    assert bus.chain.slots[0].failed and bus.chain.slots[0].slow and scene.chain.slots[0].slow
    assert list(vals.values())[-1] == 1.0                # both dropped: the scene passes through


def test_monitor_budget_and_backlog_limit():
    assert trip_budget_ms(60) == pytest.approx(25.0) and trip_budget_ms(30) == pytest.approx(50.0)
    gpu = FakeGpu()
    timer = timer_for(gpu)
    assert timer.backlog_limit() == MAX_GPU_BACKLOG
    layer = SourceLayer(Heavy(gpu, 1.0), 0.0)
    timer.frame_begin()
    layer.render(FrameCtx(0, FPS, Pool(), timer=timer), np.zeros((1, 1, 4)))
    timer.frame_end()
    assert timer.probation == 1 and timer.backlog_limit() == 1          # new and unmeasured
    gpu.now = 0.1
    timer.poll(FrameCtx(3, FPS, Pool(), timer=timer))
    assert timer.probation == 0 and timer.pending == 0 and not timer.over_budget()


# -- exit from a frozen frame ------------------------------------------------------------------------------------
def make_engine():
    eng = Engine(profile=PROFILES["h264-720p30"], gpu=0, post=lambda fn, *a: None, listener=EngineListener())
    retired = []
    eng._retire = retired.append                         # (deferred close needs CUDA events)
    eng.frame = np.full((1, 1, 4), 7.0)                  # the last rendered frame
    eng.rendered = 100
    return eng, retired


def exit_and_render(eng, n0, frames=40):
    target = Const(100.0)
    t = Mix(1.0)
    t.prepared = True
    pool = Pool()
    fc = FrameCtx(n0, FPS, pool, retire=eng._retire)
    eng._start_transition(fc, StartTransition("out", 9, target, t))
    out = np.zeros((1, 1, 4))
    vals = []
    for n in range(n0, n0 + frames):
        fc.n = n
        eng.root.render(fc, out)
        vals.append(float(out[0, 0, 0]))
        eng.root = eng.root.collapse(fc)
    return vals, pool


def test_exit_over_a_tripped_scene_crossfades_from_the_frozen_frame():
    eng, retired = make_engine()
    gpu = FakeGpu()
    src = Heavy(gpu, 1400.0)
    layer = SourceLayer(src, 0.0)
    layer.trip(FrameCtx(99, FPS, Pool()), SlowPlugin("source", "st_heavy", 1400, 50, "stuck"))
    eng.root = layer
    vals, pool = exit_and_render(eng, 100)
    assert eng.frozen_exits == 1 and src in retired and src.renders == 0      # the scene is never rendered again
    assert vals[0] == 7.0 and vals[15] == pytest.approx(7.0 + 93.0 * 0.5) and vals[-1] == 100.0
    assert isinstance(eng.root, SourceLayer) and pool.out == 0                # collapsed; the frozen copy released
    assert eng.bus.target == 0.0 and eng.bus.fade_s == 0.0                    # the bus is in the frozen frame


def test_exit_over_an_over_budget_scene_freezes_too_but_a_healthy_one_renders():
    eng, _ = make_engine()
    gpu = FakeGpu()
    timer = timer_for(gpu)
    timer.total_ema = 45.0                               # > the 33.3 ms frame period, not tripped yet
    eng.timer = timer
    src = Heavy(gpu, 45.0)
    eng.root = SourceLayer(src, 0.0)
    exit_and_render(eng, 100)
    assert eng.frozen_exits == 1 and src.renders == 0
    eng2, _ = make_engine()
    ok = Const(3.0)
    eng2.root = SourceLayer(ok, 0.0)
    vals, _ = exit_and_render(eng2, 100)
    assert eng2.frozen_exits == 0 and ok.calls and vals[0] == 3.0 and vals[-1] == 100.0
    assert eng2.bus.fade_s == pytest.approx(1.0)


# -- the vetted pool ---------------------------------------------------------------------------------------------
@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Harness output: sheets for st_seen / st_heavy_est (est 2000 ms at 4K); a calibration for GPU 1."""
    out = tmp_path / "out"
    for name, summary in (("st_seen", {"est_ms_4k": 20.0}), ("st_heavy_est", {"est_ms_4k": 2000.0})):
        (out / name).mkdir(parents=True)
        (out / name / "sheet.png").write_bytes(b"png")
        (out / name / "summary.json").write_text(json.dumps(summary))
    (out / "st_nosheet").mkdir()
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"gpus": {"1": {"name": "x"}}, "plugins": {
        "st_cal_ok": {"kind": "source", "gpu1": {"status": "ok", "fits": True, "p95": 4.0, "budget_ms": 6.0}},
        "st_seen": {"kind": "source", "gpu1": {"status": "too_slow", "fits": False, "p95": 40.0, "budget_ms": 6.0}},
        "st_hung": {"kind": "source", "gpu1": {"status": "hung"}},
        "warp_stack": {"kind": "source", "gpu1": {"status": "ok", "fits": True, "p95": 5.0, "budget_ms": 6.0,
                                                  "render_scale": 0.5, "fit_scale": 0.3, "fit_p95": 5.5}},
        "fractal_zoom": {"kind": "source", "gpu1": {"status": "error", "error": "CUDA_ERROR_ILLEGAL_ADDRESS"}}}}))
    monkeypatch.setattr(libmod, "FXTEST_OUT", out)
    monkeypatch.setattr(libmod, "CALIBRATION_FILE", cal)
    return cal


async def test_auto_ok_sheet_rule_calibration_slow_and_broken(client, harness):
    mgr = client.app[MANAGER]
    budget = 450.0                                       # 720p30 in 4K-equivalent ms
    assert mgr.auto_ok("source", "warp_stack", 0, budget)               # core
    assert mgr.auto_ok("source", "st_seen", 0, budget)                  # imported, rendered at least once
    assert not mgr.auto_ok("source", "st_nosheet", 0, budget)           # imported, never rendered
    assert not mgr.auto_ok("source", "st_heavy_est", 0, budget)         # harness cost far over the budget
    assert mgr.auto_ok("source", "st_heavy_est", 0, None)
    # GPU 1 has calibration: it decides (status ok and fits), even over the sheet rule
    assert mgr.auto_ok("source", "st_cal_ok", 1, budget) and not mgr.auto_ok("source", "st_cal_ok", 0, budget)
    assert not mgr.auto_ok("source", "st_seen", 1, budget) and not mgr.auto_ok("source", "st_hung", 1, budget)
    assert not mgr.auto_ok("source", "fractal_zoom", 1, budget) and mgr.auto_ok("source", "fractal_zoom", 0, budget)
    # a runtime trip: slow everywhere; a broken module: never
    mgr.note_slow({"kind": "source", "name": "st_seen", "gpu_ms": 80.0, "budget_ms": 50.0, "why": "sustained"}, "s-x")
    assert not mgr.auto_ok("source", "st_seen", 0, budget) and mgr.is_slow("source", "st_seen")
    mgr.registry.broken["x.sources.st_broke"] = {"kind": "source", "name": "st_broke", "error": "nope"}
    try:
        assert not mgr.auto_ok("source", "st_broke", 0, budget)
    finally:
        mgr.registry.broken.pop("x.sources.st_broke")
    rows = {(r["name"], r["source"]): r for r in mgr.slow_list()}
    assert rows[("st_seen", "runtime")]["ms"] == 80.0
    assert rows[("st_seen", "calibration")]["status"] == "slow" and rows[("st_hung", "calibration")]["status"] == "broken"
    assert rows[("fractal_zoom", "calibration")]["status"] == "broken"
    cat = await (await client.get("/api/catalog", headers=AUTH)).json()
    assert {(r["name"], r["source"]) for r in cat["slow"]} == set(rows)
    mgr.slow.clear()


def test_calibration_is_reread_when_its_mtime_changes(harness, monkeypatch):
    monkeypatch.setattr(libmod, "CAL_CHECK_S", 0.0)
    cal = libmod.Calibration()
    assert cal.entry("st_cal_ok", 1)["status"] == "ok" and cal.entry("st_cal_ok", 0) is None
    data = json.loads(harness.read_text())
    data["plugins"]["st_cal_ok"]["gpu1"]["status"] = "too_slow"
    harness.write_text(json.dumps(data))
    st = harness.stat()
    os.utime(harness, (st.st_atime, st.st_mtime + 5))
    assert cal.entry("st_cal_ok", 1)["status"] == "too_slow"
    harness.unlink()
    assert cal.entry("st_cal_ok", 1) is None


def test_directors_pick_only_vetted_plugins():
    scenes = {n: {"source": n, "tags": {"energy": [0.0, 1.0], "weight": 1.0}} for n in ("core_a", "st_b", "st_c")}
    trans = {n: {"transition": n, "tags": {"energy": [0.0, 1.0], "weight": 1.0}} for n in ("wipe", "glt_x")}
    d = Director({"enabled": True, "pool": "all", "transitions": "all", "avoid_repeat": 0, "dwell_s": None,
                  "snap": "now", "follow_dmx": [], "seed": 5, "fx": {}}, validate_direction({}))
    d.vet = lambda kind, plugin: not plugin.startswith(("st_", "glt_")) or plugin == "st_c"
    picks = {d.pick_scene(scenes, None, 0.0)[0] for _ in range(60)}
    assert picks == {"core_a", "st_c"}
    assert {d.pick_transition(trans)[0] for _ in range(20)} == {"wipe"}
    fx = FxDirector(1)
    fx.configure({"enabled": True, "pool": "all", "max": 2}, validate_direction({}))
    presets = {"ok": {"effect": "grade", "stage": "colour", "tags": {"weight": 1.0}},
               "bad": {"effect": "isf_bad", "stage": "glitch", "tags": {"weight": 1.0}}}
    assert fx.candidates(0.0, [], presets, lambda n: 0.1, 1.0) == ["ok", "bad"]
    fx.vet = lambda kind, plugin: kind == "effect" and plugin == "grade"
    assert fx.candidates(0.0, [], presets, lambda n: 0.1, 1.0) == ["ok"]


async def test_auto_never_picks_unvetted_imports_but_a_manual_cue_may(client):
    mgr = client.app[MANAGER]
    imported = sorted(n for n, p in mgr.presets.scenes.items() if p["source"].startswith("st_"))[:3]
    assert imported
    pool = ["warp-cells", "warp-smoke", *imported]
    sid, s, eng = await live(client, auto={**AUTO, "pool": pool, "avoid_repeat": 0})
    for _ in range(8):
        r = await cue(client, sid, to="auto")
        assert r.status == 202, await r.text()
        assert s.scene_spec.preset in ("warp-cells", "warp-smoke")
        for tid in list(s.inflight):
            s.on_transition("scene", "done", 1.0, tid, False)
    r = await cue(client, sid, to=imported[0])
    assert r.status == 202 and s.scene_spec.preset == imported[0]


# -- slow at runtime: the session's side ---------------------------------------------------------------------------
async def test_slow_scene_trip_event_marks_recovers_to_previous_and_is_listed(client):
    events = await client.ws_connect(f"/api/events?token={TOKEN}")
    sid, s, eng = await live(client, auto={**AUTO, "pool": ["warp-cells", "fractal-dendrite"], "avoid_repeat": 0})
    r = await cue(client, sid, to="warp-cells")
    assert r.status == 202
    first = next(c for c in reversed(eng.cmds) if isinstance(c, StartTransition))
    s.on_transition("scene", "done", 3.0, first.tid, False)
    assert s.prev_spec.name == "fractal-zoom" and s.current_spec.name == "warp-cells"
    n_cmds = len(eng.cmds)
    s.on_plugin_error({"kind": "source", "name": "warp_stack", "phase": "render", "action": "solid",
                       "detail": "SlowPlugin: warp_stack took 1400 ms", "obj_id": id(s.current_scene), "count": 1,
                       "reason": "slow", "gpu_ms": 1400.0, "budget_ms": 50.0, "why": "stuck",
                       "profile": "h264-720p30"})
    while True:
        m = await events.receive_json(timeout=2)
        if m["type"] == "session.plugin_error":
            break
    assert m["reason"] == "slow" and m["gpu_ms"] == 1400.0 and m["budget_ms"] == 50.0 and m["why"] == "stuck"
    for _ in range(50):
        rec = [c for c in eng.cmds[n_cmds:] if isinstance(c, StartTransition)]
        if rec:
            break
        await asyncio.sleep(0.02)
    assert rec and rec[0].source.name == "fractal_zoom" and rec[0].transition.name == "crossfade"   # previous scene
    assert s.cue_info[rec[0].tid]["reason"] == "plugin_error"
    mgr = s.mgr
    assert mgr.is_slow("source", "warp_stack") and not s.auto_ok("source", "warp_stack")
    assert all(s.director.pick_scene(mgr.presets.scenes, None, time.monotonic())[0] != "warp-cells"
               for _ in range(10))
    cat = await (await client.get("/api/catalog", headers=AUTH)).json()
    assert cat["slow"] == [{"kind": "source", "name": "warp_stack", "source": "runtime", "status": "slow",
                            **mgr.slow[("source", "warp_stack")]}]
    diag = await (await client.get("/api/diag", headers=AUTH)).json()
    assert diag["slow"]["trips"] == 1 and diag["slow"]["plugins"][0]["name"] == "warp_stack"
    assert diag["sessions"][0]["slow_trips"] == 1 and s.summary()["slow_trips"] == 1
    lib = await (await client.get("/dev/library")).json()
    item = next(i for i in lib["items"] if i["key"] == "scene/warp_stack")
    assert item["slow"]["ms"] == 1400.0 and item["slow"]["source"] == "runtime"
    await events.close()
    mgr.slow.clear()


async def test_slow_entry_scene_recovers_to_a_core_scene(client):
    sid, s, eng = await live(client)
    n_cmds = len(eng.cmds)
    s.on_plugin_error({"kind": "source", "name": "fractal_zoom", "obj_id": id(s.current_scene), "reason": "slow",
                       "gpu_ms": 90.0, "budget_ms": 50.0, "why": "sustained", "action": "solid", "detail": "x"})
    for _ in range(50):
        rec = [c for c in eng.cmds[n_cmds:] if isinstance(c, StartTransition)]
        if rec:
            break
        await asyncio.sleep(0.02)
    assert rec and rec[0].source.name != "fractal_zoom"
    assert libmod.origin_of(rec[0].source.name) == "core"
    s.mgr.slow.clear()


# -- calibration's fit_scale ---------------------------------------------------------------------------------------
async def test_fit_scale_becomes_render_scale_unless_the_cue_sets_it(client, harness):
    sid, s, eng = await live(client, gpu=1, profile="hevc-2160p60")
    r = await cue(client, sid, to="warp-cells")                         # preset render_scale 0.5, fit 0.3 at 4K
    assert r.status == 202 and s.scene_spec.params["render_scale"] == pytest.approx(0.3)
    cmd = next(c for c in reversed(eng.cmds) if isinstance(c, StartTransition))
    assert cmd.source.prepared_params["render_scale"] == pytest.approx(0.3)
    for tid in list(s.inflight):
        s.on_transition("scene", "done", 1.0, tid, False)
    r = await cue(client, sid, to={"preset": "warp-cells", "params": {"render_scale": 0.8}})
    assert r.status == 202 and s.scene_spec.params["render_scale"] == pytest.approx(0.8)
    # at 720p the 4K fit leaves room: the preset's own value stays
    sid2, s2, _ = await live(client, gpu=1, scene="warp-cells")
    assert s2.scene_spec.params["render_scale"] == pytest.approx(0.5)
    _, s3 = await create(client, gpu=1, profile="hevc-2160p60", scene="warp-cells")
    assert s3.scene_spec.params["render_scale"] == pytest.approx(0.3)
