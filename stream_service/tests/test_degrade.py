"""4K60 plan change 5: the degradation controller (degrade.py, shared plumbing) and what it drives: the scene-rate
floor (layers.py), the fps step (renderer.py frame ids / fMP4 durations / NVENC Reconfigure), the SPS pin
(bitstream.py), the FX trim (fx_director.py), the session's reporting.  No GPU."""
from __future__ import annotations

import struct
import sys
import time
import types

import numpy as np
import pytest

from stream_service import degrade as dg
from stream_service.degrade import Controller, Ctx, window
from stream_service.encode import bitstream as bs
from stream_service.encode.fmp4 import TIMESCALE
from stream_service.layers import FrameCtx, SourceLayer
from stream_service.profiles import PROFILES, at_fps
from stream_service.renderer import Engine, EngineListener, _Rec, _Slot

from .test_nvenc import COLR, Dev, FakeCp, FakeNvc, fixture


# -- the decision logic -----------------------------------------------------------------------------------------------
def second(t0: float, fps: int, host: float, *, gpu: float = 0.0, enc: float = 1.0, held_every: int = 0,
           held_host: float = 0.5, repeats: int = 0, late: int = 0, span: float = 1.0) -> list:
    """One window of slot records starting at t0 (newest first, as the engine hands them over); repeats and late
    slots spread evenly over it."""
    n = int(round(fps * span))
    rep = {int(k * n / repeats) for k in range(repeats)} if repeats else set()
    lat = {int(k * n / late) + 1 for k in range(late)} if late else set()
    out = []
    for i in range(n):
        t = t0 + i / fps
        slip = 2000.0 / fps if i in lat else 0.0
        if i in rep:
            out.append(_Rec(i, True, slip, t_start=t, write_ms=enc))
            continue
        held = bool(held_every) and i % held_every != 0
        h = held_host if held else host
        out.append(_Rec(i, False, slip, h, t_start=t, cpu_ms=h * 0.8, runq_ms=0.0, blocked_ms=h * 0.2,
                        render_ms=(h + gpu) if gpu else h * 0.5, write_ms=enc, held=held))
    return out[::-1]


class Run:
    """Drives a Controller one decision a second, as the engine does."""

    def __init__(self, fps: int = 60, **ctx):
        self.c = Controller(fps, wall=lambda: 1_700_000_000.0)
        self.fps = fps
        self.t = 100.0
        self.ctx = Ctx(**{"has_fx": True, "can_fps": True, **ctx})
        self.changes: list = []
        self.c.decide([], self.ctx, self.t, 1000.0 / fps)          # the first open tick: a second from here

    @property
    def out_fps(self) -> int:
        return self.c.out_fps

    def tick(self, host: float, **kw) -> object:
        """One second at this per-slot host cost (and the scene-rate floor's held slots when it is on)."""
        if "scene_rate" in self.c.steps:
            kw.setdefault("held_every", 2)
        recs = second(self.t, self.out_fps, host, **kw)
        self.t += 1.0
        ch = self.c.decide(recs, self.ctx, self.t, 1000.0 / self.out_fps)
        if ch is not None:
            self.changes.append((round(self.t - 100.0), ch.step, ch.on, ch.why))
        return ch


def test_window_means_per_slot():
    w = window(second(0.0, 60, 10.0, gpu=4.0, enc=3.0, repeats=6, late=6, held_every=2), 1000 / 60)
    assert w.slots == 60 and w.repeats == 6 and w.rendered == 54 and w.late == 6
    assert w.enc_ms == pytest.approx(3.0)
    assert w.drawn_host_ms == pytest.approx(10.0) and w.host_ms < 10.0       # held slots are cheap
    assert w.drawn_gpu_ms == pytest.approx(14.0)                              # the span past the host's submit
    w2 = window(second(0.0, 60, 10.0), 1000 / 60)                             # span inside the host: not GPU work
    assert w2.gpu_ms == 0.0 and w2.load(1000 / 60)["host"] == pytest.approx(0.6)


def test_steps_down_in_order_once_a_second_and_says_why():
    r = Run()
    assert r.tick(40.0) is not None and r.c.steps == ["fx"]                  # 40 ms in a 16.7 ms slot: host
    assert r.c.state()["text"] == "fewer effects: computer busy"
    assert r.tick(40.0).step == "scene_rate"
    assert r.tick(40.0).step == "fps" and r.out_fps == 30                    # (20 ms a slot with the floor)
    st = r.c.state()
    assert st["level"] == 3 and st["step"] == "fps" and st["why"] == "computer busy" and st["out_fps"] == 30
    assert st["text"] == "60 -> 30 fps + scene 15 fps + fewer effects: computer busy" and st["since"].endswith("Z")
    assert [c[1] for c in r.changes] == ["fx", "scene_rate", "fps"] and [c[0] for c in r.changes] == [1, 2, 3]
    assert r.tick(30.0) is None and not r.c.state()["floor"]                  # 15 ms in 33 ms: holds
    assert r.tick(80.0) is None and r.c.state()["floor"]                      # nothing left: it stays, says so


def test_a_change_waits_a_whole_second_and_a_spike_in_one_half_is_not_held():
    r = Run()
    r.tick(25.0)
    half = second(r.t, 60, 25.0, span=0.5)
    assert r.c.decide(half, r.ctx, r.t + 0.5, 1000 / 60) is None             # not a whole second since the change
    r.t += 1.0
    spike = second(r.t, 60, 25.0, span=0.5) + second(r.t + 0.5, 60, 5.0, span=0.5)
    assert r.c.decide(spike, r.ctx, r.t + 1.0, 1000 / 60) is None and r.c.steps == ["fx"]


def test_frozen_across_entry_and_exit():
    r = Run(frozen=True)
    for _ in range(5):
        assert r.tick(40.0) is None
    r.ctx.frozen = False
    assert r.tick(40.0) is None                                               # opened: a second from here
    assert r.tick(40.0).step == "fx"
    r.ctx.frozen = True                                                       # the exit: nothing moves
    assert all(r.tick(1.0) is None for _ in range(40)) and r.c.steps == ["fx"]


def test_skips_steps_that_cannot_help_the_busy_stage():
    r = Run(has_fx=False)                                                     # an empty bus: straight to the scene
    assert r.tick(25.0).step == "scene_rate"
    r = Run(own_div=2)                                                        # GPU-bound, scene already at 1/2
    assert r.tick(1.0, gpu=30.0).step == "fx" and r.changes[-1][3] == "gpu"
    assert r.tick(1.0, gpu=30.0).step == "fps"
    r = Run()                                                                 # the encoder: only fps helps
    assert r.tick(2.0, enc=20.0).step == "fps" and r.c.state()["why"] == "encoder busy"
    r = Run(can_fps=False)                                                    # ... and not on the ffmpeg path
    assert r.tick(2.0, enc=20.0) is None and r.c.state()["floor"] and r.c.steps == []


def test_repeats_and_late_slots_count_as_overload():
    r = Run()
    assert r.tick(5.0, repeats=8).step == "fx"                                # 4 of 30 slots per half
    r = Run()
    assert r.tick(5.0, repeats=2) is None                                     # one a half: not yet
    r = Run()
    assert r.tick(5.0, late=20).step == "fx"                                  # a third of the slots late
    r = Run()
    assert r.tick(5.0) is None and r.c.steps == []


def test_steps_up_after_10_s_and_30_s_for_fps_when_the_projection_fits():
    r = Run()
    for _ in range(3):
        r.tick(40.0)
    assert r.c.steps == ["fx", "scene_rate", "fps"]
    t_last = r.changes[-1][0]
    for _ in range(40):
        r.tick(3.0)                                                           # the load went: 3 ms a slot
    ups = [c for c in r.changes if not c[2]]
    assert [u[1] for u in ups] == ["fps", "scene_rate"] and ups[0][0] == t_last + 30 and ups[1][0] == ups[0][0] + 10
    for _ in range(12):
        r.tick(3.0)
    assert r.c.steps == [] and r.c.state()["text"] == "full rate, 60 fps" and r.c.state()["level"] == 0


def test_no_step_up_while_the_projection_is_over_0_7():
    r = Run(has_fx=False, own_div=1)
    r.tick(40.0)
    r.tick(40.0)
    assert r.c.steps == ["scene_rate", "fps"] and r.out_fps == 30
    for _ in range(60):
        r.tick(25.5)                    # 13 ms a slot with the floor, in 33 ms (0.39) -> 0.78 at 60 fps: stays at 30
    assert r.c.steps == ["scene_rate", "fps"]
    assert r.c.project("fps", window(second(0.0, 30, 13.0), 1000 / 30), r.ctx, 1000 / 30) == pytest.approx(0.78, 0.05)


def test_scene_rate_projection_uses_the_drawn_slots():
    c = Controller(60)
    ctx = Ctx(own_div=1)
    w = window(second(0.0, 60, 14.0, held_every=2, held_host=0.5), 1000 / 60)
    assert c.project("scene_rate", w, ctx, 1000 / 60) == pytest.approx(14.0 / (1000 / 60), 0.01)
    assert c.project("scene_rate", w, Ctx(own_div=3), 1000 / 60) < 0.5      # the scene is that slow by itself


def test_fx_projection_adds_back_what_the_bus_cost():
    c = Controller(60)
    c.fx_at = (6.0, 0.0)
    w = window(second(0.0, 60, 5.0), 1000 / 60)
    assert c.project("fx", w, Ctx(fx_host_ms=1.0), 1000 / 60) == pytest.approx((5.0 + 5.0) / (1000 / 60), 0.01)


def test_a_failed_step_up_doubles_its_wait_and_a_settled_one_resets():
    r = Run(has_fx=False, can_fps=False)
    r.tick(25.0)
    assert r.c.steps == ["scene_rate"]
    for _ in range(10):
        r.tick(3.0)
    assert r.c.steps == []                                                   # off after 10 s
    r.tick(25.0)                                                             # back on at once: failed
    assert r.c.steps == ["scene_rate"] and r.c.mult["scene_rate"] == 2 and r.c.failed_ups == 1
    on_at = r.t
    while r.c.steps:
        r.tick(3.0)
    assert r.t - on_at == pytest.approx(20.0)                                # the wait doubled
    for _ in range(int(dg.SETTLE_S) + 1):
        r.tick(3.0)
    r.tick(25.0)
    assert r.c.mult["scene_rate"] == 1                                       # it had stayed off: from 1x again
    assert r.c.state()["up_wait_s"]["scene_rate"] == 10.0


def test_drop_and_the_off_state():
    r = Run()
    for _ in range(3):
        r.tick(40.0)
    ch = r.c.drop("fps", r.t)
    assert ch.step == "fps" and not ch.on and r.out_fps == 60 and r.c.steps == ["fx", "scene_rate"]
    assert r.c.drop("fps", r.t) is None
    off = dg.off_state(60)
    assert off["mode"] == "off" and off["level"] == 0 and off["out_fps"] == 60
    assert dg.text(["fx"], "gpu", 60) == "fewer effects: GPU busy" and dg.text(["fps"], "host", 60) == \
        "60 -> 30 fps: computer busy"


# -- the scene-rate floor and the fps step in the layer tree ----------------------------------------------------------
class Src:
    name = "scene"

    def __init__(self):
        self.rendered = []

    def render(self, t, dt, out):
        self.rendered.append(round(t, 4))
        out[...] = len(self.rendered)


class Pool:
    def acquire(self):
        return np.zeros((2, 2, 4), np.uint8)

    def release(self, b):
        pass


def test_rate_floor_draws_every_2nd_slot_and_counts_output_slots_with_a_step():
    src = Src()
    lay = SourceLayer(src, 0.0)
    fc = FrameCtx(0, 60, Pool())
    out = np.zeros((2, 2, 4), np.uint8)
    fc.rate_floor = 2
    for n in range(8):
        fc.n = n
        lay.render(fc, out)
    assert len(src.rendered) == 4 and lay.div_now == 2 and lay.rate_div == 1   # the scene's own divider untouched
    fc.step = 2                                                                # 30 fps output, ids +2
    for n in range(8, 24, 2):
        fc.n = n
        lay.render(fc, out)
    assert src.rendered[4:] == [round(n / 60, 4) for n in (10, 14, 18, 22)]   # every 2nd slot, on the 60 Hz grid
    fc.rate_floor = 1
    fc.n = 24
    lay.render(fc, out)
    assert src.rendered[-1] == round(24 / 60, 4) and lay.hold is None


# -- the engine's fps step: frame ids, fMP4 durations, Reconfigure ----------------------------------------------------
class Capture(EngineListener):
    def __init__(self):
        self.media, self.degrade = [], []

    def on_encoder_config(self, cfg, config_msg, init_msg):
        self.config = config_msg

    def on_media(self, frame_id, key, msg):
        self.media.append((frame_id, key, msg))

    def on_degrade(self, info):
        self.degrade.append(info)


def trun(msg: bytes) -> tuple[int, int]:
    """(tfdt, sample duration) of one media message."""
    frag = msg[6:]
    i = frag.index(b"tfdt")
    tfdt = struct.unpack(">Q", frag[i + 8:i + 16])[0]
    j = frag.index(b"trun")
    dur = struct.unpack(">I", frag[j + 16:j + 20])[0]
    return tfdt, dur


class ReconfNvc(FakeNvc):
    """FakeNvc plus Reconfigure (the probe on the P4000: GetEncodeReconfigureParams, Reconfigure -> True), and packet
    timestamps that are the encoder's own input count, as PyNvVideoCodec 2.2.3's are (whatever inputTimeStamp says)."""

    def __init__(self, seq, pkts):
        super().__init__(seq, pkts)
        self.reconf, outer = [], self
        create = self.CreateEncoder

        def create2(*a, **kw):
            e = create(*a, **kw)
            encode, count = e.Encode, iter(range(10**9))

            def counted(frame, pp):
                out = encode(frame, pp)
                return [{**x, "timestamp": next(count)} for x in out]
            e.Encode = counted

            def params():
                return types.SimpleNamespace(frameRateNum=int(e.kw["fps"]), frameRateDen=1)

            def reconfigure(rp):
                outer.reconf.append((len(e.queue), rp.frameRateNum))
                return True
            e.GetEncodeReconfigureParams, e.Reconfigure = params, reconfigure
            return e
        self.CreateEncoder = create2


def test_fps_step_keeps_ids_on_the_grid_doubles_durations_and_reconfigures(monkeypatch):
    meta, seq, pkts = fixture()
    pkts = [(i, 3 if i % 60 == 0 else 1, pkts[i % len(pkts)][2]) for i in range(80)]
    nvc = ReconfNvc(seq, pkts)
    monkeypatch.setitem(sys.modules, "PyNvVideoCodec", nvc)
    lis = Capture()
    e = Engine(profile=PROFILES["hevc-2160p60"], gpu=0, post=lambda fn, *a: fn(*a), listener=lis, name="s-t")
    e.w, e.h = meta["w"], meta["h"]
    e.ctx = types.SimpleNamespace(cp=FakeCp(), stream=types.SimpleNamespace(ptr=1))
    gpu = e._gpu_enc = e.pipeline = e._make_gpu_encoder()
    assert gpu.opts["level"] == "5.1" and gpu.opts["tier"] == "main" and gpu.opts["vbvbufsize"] == "40000000"
    slot = _Slot(0, None, None, dev=Dev(np.zeros(8, np.uint8)), surface=object())
    ids = list(range(0, 10)) + list(range(10, 30, 2)) + list(range(30, 40))   # 60 fps, 30 fps from id 10, 60 from 30
    for n in ids:
        step = 2 if 10 <= n < 30 else 1
        if step != 1:
            e._steps[n] = step
        e._submit(n, slot, step)
    assert nvc.reconf == [(3, 30), (3, 60)] and gpu.reconfigures == 2 and e.reconfigure_ms is not None
    got = [(fid, *trun(m)) for fid, _, m in lis.media]
    assert [g[0] for g in got] == ids[:len(got)]
    for (f0, t0, d0), (f1, t1, _) in zip(got, got[1:]):
        assert t0 == f0 * (TIMESCALE // 60) and t0 + d0 == t1              # contiguous on the 60 Hz grid
    assert {d for f, _, d in got if 10 <= f < 30} == {3000} and {d for f, _, d in got if f < 10} == {1500}
    assert [n for n in range(0, 61, 2) if e._idr_due(n)] == [0, 60]          # the GOP grid still lands at step 2


def test_boundary_applies_the_fps_step_on_an_even_id_and_drops_it_on_a_fallback():
    lis = Capture()
    e = Engine(profile=PROFILES["hevc-2160p60"], gpu=0, post=lambda fn, *a: fn(*a), listener=lis, name="s-t")
    e.degrade = Controller(60)
    e.ctx = types.SimpleNamespace(fps=60)
    fc = FrameCtx(0, 60, Pool())
    e._step_want = 2
    e._degrade_boundary(fc, 7)
    assert e.step == 1                                                        # odd id: not yet
    e._dg_next = 1e18
    e._degrade_boundary(fc, 8)
    assert e.step == 2 and fc.step == 2 and e.ctx.fps == 30 and e.summary()["out_fps"] == 30
    e.degrade.steps = ["fps"]
    e._fps_lost = "NVENC failed"
    e._degrade_boundary(fc, 10)
    assert e.step == 1 and e.degrade.steps == [] and lis.degrade[-1]["out_fps"] == 60


def test_engine_reports_degrade_and_the_off_switch():
    e = Engine(profile=PROFILES["hevc-2160p30"], gpu=0, post=lambda fn, *a: fn(*a), listener=Capture())
    s = e.summary()
    assert s["degrade"]["mode"] == "on" and s["out_fps"] == 30 and s["fps_steps"] == 0
    e.degrade_mode = "off"
    assert e.summary()["degrade"]["mode"] == "off"


# -- the SPS pin and the level -----------------------------------------------------------------------------------------
def _retime(sps: bytes, time_scale: int) -> bytes:
    rbsp = bytearray(bs.unescape_rbsp(sps[2:]))
    pos: dict = {}
    bs._hevc_sps(bs.BitReader(bytes(rbsp)), bs._hvcc_rec(), pos)
    t = pos["timing"] + 32
    for i in range(32):
        bit = (time_scale >> (31 - i)) & 1
        p = t + i
        rbsp[p >> 3] = (rbsp[p >> 3] & ~(1 << (7 - (p & 7)))) | (bit << (7 - (p & 7)))
    return sps[:2] + bs.escape_rbsp(bytes(rbsp))


def test_an_inband_sps_that_differs_only_in_timing_is_sent_as_the_records():
    meta, seq, pkts = fixture()
    nals = bs.annexb_nals(seq)
    sps = next(n for n in nals if bs.hevc_nal_type(n) == bs.HEVC_SPS)
    other = _retime(sps, 12345)
    assert other != sps and bs.hevc_sps_same_but_timing(sps, other)
    lvl = bytearray(sps)
    lvl[3] ^= 0x20                                                            # the tier flag: a real change
    assert not bs.hevc_sps_same_but_timing(sps, bytes(lvl))
    mux = bs.HevcAnnexB(meta["w"], meta["h"], COLR)
    mux.on_param_sets(seq)
    pinned = mux._pin
    idr = bs.annexb_nals(pkts[0][2])
    assert any(bs.hevc_nal_type(n) == bs.HEVC_SPS for n in idr)
    for repl, want_pinned in ((other, True), (bytes(lvl), False)):
        au = b"".join(b"\x00\x00\x00\x01" + (repl if bs.hevc_nal_type(n) == bs.HEVC_SPS else n) for n in idr)
        sample, _ = mux.sample(au)
        got = [n for n in bs.split_length_prefixed(sample) if bs.hevc_nal_type(n) == bs.HEVC_SPS][0]
        assert (got == pinned) is want_pinned
    assert mux.pinned_sps == 1


def test_the_60p_profile_holds_level_5_1_main_tier_on_both_paths():
    p = PROFILES["hevc-2160p60"]
    assert (p.bitrate, p.vbv_s, p.level) == ("40M", 1.0, "5.1")
    args = p.encoder_args()
    assert args[args.index("-bufsize") + 1] == "40000000" and args[args.index("-level") + 1] == "5.1" \
        and args[args.index("-tier") + 1] == "main"
    assert "-bufsize" not in PROFILES["hevc-2160p30"].encoder_args()        # 30p unchanged
    q = at_fps(PROFILES["hevc-2160p30"].with_trc("bt709"), 60)
    assert q.name == "hevc-2160p60" and q.color_trc == "bt709" and at_fps(q, 30).name == "hevc-2160p30"


# -- the FX trim ------------------------------------------------------------------------------------------------------
def test_fx_trim_removes_one_auto_effect_and_halves_the_budget():
    from stream_service.fx_director import FX_BUS_BUDGET_MS, FxDirector

    fd = FxDirector(1)
    fd.configure({"enabled": True, "max": 2, "pool": "all"}, {"fx": {"density": 1.0, "rate": 0.5}})
    fd.armed = True
    spec = lambda n: types.SimpleNamespace(preset=n, effect=n, name=n)        # noqa: E731
    stack = [types.SimpleNamespace(id=f"x{i}", spec=spec(f"e{i}"), stage=f"s{i}", origin="auto") for i in range(2)]
    presets = {"cheap": {"effect": "cheap", "stage": "colour", "tags": {"energy": "low"}},
               "dear": {"effect": "dear", "stage": "colour", "tags": {"energy": "low"}}}
    cost = {"e0": 0.5, "e1": 0.5, "cheap": 0.2, "dear": FX_BUS_BUDGET_MS * 0.6}.get
    assert set(fd.candidates(0.0, stack[:1], presets, cost, 1.0)) == {"cheap", "dear"}
    assert fd.set_trim(True, 50.0, 2) and fd.due(50.0)                        # a move now
    mv = fd.plan(50.0, stack, presets, cost)
    assert mv.op == "remove" and mv.remove == "x0" and "fewer effects" in mv.why
    assert fd.candidates(0.0, stack[:1], presets, cost, 1.0) == ["cheap"]     # half the budget: cheaper picks
    assert fd.status(50.0)["trimmed"] and not fd.set_trim(True, 51.0, 1)
    assert not fd.set_trim(False, 52.0, 1) and fd.trim_max is None


# -- the session: session.degrade, /api/sessions, the panel's line, the FX trim, the media backlog ------------------------
from .test_sessions import AUTH, client, create  # noqa: E402,F401 (fixture)


async def test_the_session_reports_a_step_and_hands_the_fx_trim_on(client):  # noqa: F811
    _, s = await create(client, profile="hevc-2160p60")                      # devtest: 60 fps in the shipped file
    assert s.profile.fps == 60
    s.engine.root = None                                                     # (the fake engine has no layer tree)
    c = Controller(60, wall=lambda: 1_700_000_000.0)
    c.steps, c.why, c.since = ["fx"], "gpu", 1_700_000_000.0
    s.on_degrade(c.state())
    ev = [e for e in s.mgr.events.history if e["type"] == "session.degrade"][-1]
    assert ev["session"] == s.id and ev["level"] == 1 and ev["step"] == "fx" and ev["why"] == "GPU busy"
    assert ev["text"] == "fewer effects: GPU busy" and ev["since"] == "2023-11-14T22:13:20.000Z"
    assert s.fx_director.trim_max == 0                                       # no auto effects on the bus: none
    summ = (await (await client.get(f"/api/sessions/{s.id}", headers=AUTH)).json())
    assert summ["degrade"]["level"] == 1 and summ["degrade"]["text"] == "fewer effects: GPU busy"
    body = await (await client.get(f"/api/sessions/{s.id}/hud", headers=AUTH)).json()
    i = body["lines"].index("fewer effects: GPU busy")
    assert i in body["warn"]
    c.steps = ["fx", "scene_rate", "fps"]
    s.on_degrade(c.state())
    assert s.media.fps == 30 and s.degrade["out_fps"] == 30
    from stream_service.hud import build_lines
    lines, warn = build_lines(s, time.monotonic())
    assert lines[lines.index("60 -> 30 fps + scene 15 fps + fewer effects: GPU busy")]
    c.steps = []
    s.on_degrade(c.state())
    lines, _ = build_lines(s, time.monotonic())
    assert s.fx_director.trim_max is None and s.media.fps == 60 and not any("busy" in x for x in lines)
