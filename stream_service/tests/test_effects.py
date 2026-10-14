"""Effect chains, the scene EffectLayer, the master bus and failure isolation (NumPy fakes, no GPU)."""
from __future__ import annotations

import numpy as np
import pytest

from stream_service.effects import (AMOUNT_EPS, EffectChain, EffectLayer, EffectSlot, FxUpdate, MasterBus,
                                    SlotUpdate, close_with_effects, smoothstep, stage_index)
from stream_service.layers import FrameCtx, SourceLayer, TransitionLayer
from stream_service.plugins.base import STAGES, ParamEaser

from .test_layers import Const, Mix, Pool, Recorder

FPS = 10


class FakeEffect:
    """out = src * mul + add * amount; records the params it saw."""

    def __init__(self, name="fx", stage="colour", add=0.0, mul=1.0, amount=1.0, fail_at=None, with_amount=True):
        self.name, self.stage = name, stage
        self.add, self.mul = add, mul
        props = {"pulse": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.0}}
        if with_amount:
            props["amount"] = {"type": "number", "minimum": 0, "maximum": 1, "default": amount}
        self.easer = ParamEaser(props, {k: p["default"] for k, p in props.items()}, 0.3)
        self.seen = []
        self.renders = 0
        self.closed = False
        self.fail_at = fail_at

    def step_params(self, dt):
        self.easer.step(dt)
        return self.easer.values()

    def update(self, params):
        self.easer.set_targets(params)

    def render(self, t_local, dt, src, out):
        p = self.step_params(dt)
        self.seen.append(dict(p))
        self.renders += 1
        if self.fail_at is not None and self.renders >= self.fail_at:
            raise RuntimeError("boom")
        out[...] = src * self.mul + self.add * p.get("amount", 1.0)

    def close(self):
        self.closed = True


def slot(eff, sid="s1", **kw):
    kw.setdefault("level", 1.0)
    kw.setdefault("target", kw["level"])
    return EffectSlot(sid, eff, name=eff.name, stage=eff.stage, **kw)


def frame(n=0, pool=None):
    return FrameCtx(n, FPS, pool or Pool())


def run_chain(chain, src_value, n0=0, n1=1, **kw):
    pool = Pool()
    out = np.zeros((1, 1, 4))
    vals = []
    for n in range(n0, n1):
        fc = FrameCtx(n, FPS, pool)
        chain.render(fc, lambda buf: buf.__setitem__(Ellipsis, src_value), out, **kw)
        vals.append(float(out[0, 0, 0]))
        assert pool.out == 0
    return vals, pool


def test_stage_order_is_stable_within_a_stage():
    a = slot(FakeEffect("a", "overlay"), "a")
    b = slot(FakeEffect("b", "warp"), "b")
    c = slot(FakeEffect("c", "colour"), "c")
    d = slot(FakeEffect("d", "warp"), "d")
    chain = EffectChain([a, b, c, d])
    assert [s.slot_id for s in chain.slots] == ["b", "d", "c", "a"]
    assert stage_index("bogus") == STAGES.index("stylise")


def test_chain_applies_active_slots_in_order():
    # (x + 1) * 2 vs x * 2 + 1: the order is observable
    add = slot(FakeEffect("add", "warp", add=1.0), "add")
    mul = slot(FakeEffect("mul", "colour", mul=2.0), "mul")
    chain = EffectChain([mul, add])
    vals, _ = run_chain(chain, 3.0)
    assert vals == [8.0]


class CountingPool(Pool):
    def __init__(self):
        super().__init__()
        self.peak = 0

    def acquire(self):
        b = super().acquire()
        self.peak = max(self.peak, self.out)
        return b


@pytest.mark.parametrize("n,bufs", [(1, 1), (2, 2), (3, 2)])
def test_ping_pong_buffer_use(n, bufs):
    chain = EffectChain([slot(FakeEffect(f"e{i}", "colour", add=1.0), f"e{i}") for i in range(n)])
    pool = CountingPool()
    out = np.zeros((1, 1, 4))
    chain.render(FrameCtx(0, FPS, pool), lambda buf: buf.__setitem__(Ellipsis, 0.0), out)
    assert float(out[0, 0, 0]) == n and pool.peak == bufs and pool.out == 0


def test_amount_zero_and_level_zero_skip_render_bit_exact():
    e1 = FakeEffect("z", add=5.0, amount=0.0)
    e2 = FakeEffect("l", add=5.0)
    chain = EffectChain([slot(e1, "z"), slot(e2, "l", level=0.0, target=0.0)])
    pool = CountingPool()
    src = np.full((1, 1, 4), 7.25)
    out = np.zeros((1, 1, 4))
    chain.render(FrameCtx(0, FPS, pool), lambda buf: buf.__setitem__(Ellipsis, src), out)
    assert e1.renders == 0 and e2.renders == 0
    assert np.array_equal(out, src) and pool.peak == 0           # direct draw: no buffer, bit-exact


def test_envelope_fade_in_out_retire_and_bypass():
    e = FakeEffect("f", add=10.0, amount=0.5)
    s = slot(e, "f", level=0.0, target=1.0, fade_s=1.0)
    chain = EffectChain([s])
    vals, _ = run_chain(chain, 0.0, 0, 12)
    assert s.level == 1.0 and vals[0] == 0.0                     # first frame: dt 0, still at 0
    assert vals[-1] == pytest.approx(5.0)                        # 10 x 0.5 x smoothstep(1)
    # the amount the effect sees = plug-in amount x smoothstep(level) x gain
    assert e.seen[4]["amount"] == pytest.approx(0.5 * smoothstep(0.5))
    # bypass: target 0 keeps the slot
    chain.apply(FxUpdate(1, update=[SlotUpdate("f", target=0.0)], fade_s=0.5))
    run_chain(chain, 0.0, 12, 20)
    assert s.level == 0.0 and not s.done and chain.slots == [s] and s.state == "bypassed"
    # remove: fades out, then done -> retired through fc.retire
    chain.apply(FxUpdate(2, update=[SlotUpdate("f", target=1.0)], fade_s=0.0))
    chain.apply(FxUpdate(3, update=[SlotUpdate("f", remove=True)], fade_s=0.3))
    retired = []
    pool = Pool()
    out = np.zeros((1, 1, 4))
    for n in range(20, 26):
        fc = FrameCtx(n, FPS, pool, retire=lambda obj: retired.append(obj))
        chain.render(fc, lambda buf: buf.__setitem__(Ellipsis, 0.0), out)
        ids = chain.retire_done(fc)
        if ids:
            assert ids == ["f"]
            break
    assert retired == [e] and chain.slots == []


def test_outer_gain_and_pulse_gain_scale_params():
    e = FakeEffect("g", amount=0.8)
    e.easer.current["pulse"] = e.easer.target["pulse"] = 0.5
    chain = EffectChain([slot(e, "g")])
    run_chain(chain, 1.0, gain=0.5, pulse_gain=0.5)
    assert e.seen[0]["amount"] == pytest.approx(0.4) and e.seen[0]["pulse"] == pytest.approx(0.25)


def test_mix_mode_crossfades_with_the_effect_output():
    e = FakeEffect("m", add=10.0, with_amount=False)
    s = slot(e, "m", mix=True, level=0.5)           # smoothstep(0.5) = 0.5
    chain = EffectChain([s])
    pool = CountingPool()
    out = np.zeros((1, 1, 4))
    chain.render(FrameCtx(0, FPS, pool), lambda buf: buf.__setitem__(Ellipsis, 2.0), out)
    # effect output = 2 + 10 = 12; mixed at 0.5 with the input 2 -> 7
    assert float(out[0, 0, 0]) == pytest.approx(7.0) and pool.peak == 2 and pool.out == 0


def test_effect_layer_inside_a_transition_blends_processed_scenes_and_closes():
    rec = Recorder()
    a = SourceLayer(Const(0.0), 0.0)
    e = FakeEffect("x", add=4.0)
    src_b = Const(1.0)
    b = EffectLayer(SourceLayer(src_b, 0.0), EffectChain([slot(e, "x", where="scene")]))
    tl = TransitionLayer(a, b, Mix(1.0), 0, kind="scene", tid=1, notify=rec)
    pool = Pool()
    out = np.zeros((1, 1, 4))
    retired = []
    root = tl
    vals = []
    for n in range(0, 12):
        fc = FrameCtx(n, FPS, pool, retire=lambda o: retired.append(o))
        root.render(fc, out)
        vals.append(float(out[0, 0, 0]))
        root = root.collapse(fc)
    assert vals[5] == pytest.approx(0.5 * 5.0)                   # half way to the processed b (1 + 4)
    assert vals[-1] == pytest.approx(5.0)
    assert isinstance(root, EffectLayer)                         # collapse keeps the EffectLayer
    fc = FrameCtx(12, FPS, pool, retire=lambda o: retired.append(o))
    root.close(fc)
    assert e in retired and src_b in retired and root.chain.slots == []


def test_close_with_effects_closes_a_never_shown_scenes_chain():
    src = Const(1.0)
    e = FakeEffect("y")
    src.fx_chain = EffectChain([slot(e, "y", where="scene")])
    close_with_effects(src)
    assert src.closed and e.closed and src.fx_chain.slots == []
    close_with_effects(src)                                      # idempotent


def test_master_bus_closed_open_close_seal():
    bus = MasterBus()
    e = FakeEffect("b", add=8.0)
    bus.apply(FxUpdate(1, add=[slot(e, "b", level=0.0, target=1.0, fade_s=0.0)], fade_s=0.0))
    pool = Pool()
    out = np.zeros((1, 1, 4))

    def step(n):
        fc = FrameCtx(n, FPS, pool)
        bus.render(fc, lambda buf: buf.__setitem__(Ellipsis, 1.0), out)
        return float(out[0, 0, 0])

    assert step(0) == 1.0 and e.renders == 0                     # closed: exact passthrough
    bus.open(1.0)
    vals = [step(n) for n in range(1, 13)]
    assert vals[-1] == pytest.approx(9.0) and bus.level == 1.0
    bus.close(0.5)
    vals = [step(n) for n in range(13, 19)]
    assert bus.level == 0.0 and vals[-1] == 1.0                  # reached 0 within 0.5 s (5 frames)
    bus.open(0.1)
    bus.seal()
    bus.open(0.1)                                                # never after seal
    before = e.renders
    assert step(19) == 1.0 and step(20) == 1.0 and e.renders == before


def test_master_amount_scales_the_bus():
    bus = MasterBus()
    e = FakeEffect("b", add=8.0)
    bus.apply(FxUpdate(1, add=[slot(e, "b")], master={"amount": 0.5}))
    bus.open(0.0)
    out = np.zeros((1, 1, 4))
    bus.render(FrameCtx(0, FPS, Pool()), lambda buf: buf.__setitem__(Ellipsis, 0.0), out)
    assert float(out[0, 0, 0]) == pytest.approx(4.0)
    bus.apply(FxUpdate(2, master={"amount": 0.0}))
    bus.render(FrameCtx(1, FPS, Pool()), lambda buf: buf.__setitem__(Ellipsis, 0.0), out)
    assert float(out[0, 0, 0]) == 0.0


def test_failing_effect_passes_through_and_is_skipped_afterwards():
    e = FakeEffect("bad", add=3.0, fail_at=2)
    s = slot(e, "bad", where="bus")
    chain = EffectChain([s])
    pool = Pool()
    out = np.zeros((1, 1, 4))
    errors = []
    for n in range(4):
        fc = FrameCtx(n, FPS, pool)
        chain.render(fc, lambda buf: buf.__setitem__(Ellipsis, 1.0), out)
        errors += fc.errors
        if n == 0:
            assert float(out[0, 0, 0]) == 4.0
        else:
            assert float(out[0, 0, 0]) == 1.0                   # passthrough
    assert e.renders == 2 and len(errors) == 1
    kind, obj, exc, action = errors[0]
    assert kind == "effect" and obj is s and action == "removed" and s.failed and s.state == "failed"


class Boom:
    name = "boom"

    def __init__(self, at=1):
        self.at, self.n, self.closed = at, 0, False

    def render(self, *args):
        self.n += 1
        if self.n >= self.at:
            raise ValueError("kernel exploded")

    def close(self):
        self.closed = True


def test_failing_transition_becomes_a_crossfade():
    rec = Recorder()
    t = Mix(1.0)
    t.render = Boom().render
    t.name = "boomfade"
    tl = TransitionLayer(SourceLayer(Const(0.0), 0.0), SourceLayer(Const(10.0), 0.0), t, 0, kind="scene",
                         tid=1, notify=rec)
    pool = Pool()
    out = np.zeros((1, 1, 4))
    fc = FrameCtx(3, FPS, pool)
    tl.render(fc, out)
    assert float(out[0, 0, 0]) == pytest.approx(3.0) and tl.failed and len(fc.errors) == 1
    assert fc.errors[0][0] == "transition" and fc.errors[0][3] == "crossfade"
    fc2 = FrameCtx(6, FPS, pool)
    tl.render(fc2, out)
    assert float(out[0, 0, 0]) == pytest.approx(6.0) and fc2.errors == []


def test_failing_source_goes_black_and_stays_failed():
    src = Boom()
    layer = SourceLayer(src, 0.0)
    out = np.full((1, 1, 4), 9.0)
    fc = FrameCtx(0, FPS, Pool())
    layer.render(fc, out)
    assert layer.failed and out[0, 0, 0] == 0 and out[0, 0, 3] == 255 and fc.errors[0][3] == "solid"
    fc2 = FrameCtx(1, FPS, Pool())
    layer.render(fc2, out)
    assert src.n == 1 and fc2.errors == []                      # not called again


def test_reporter_gets_failures_instead_of_errors():
    got = []
    fc = FrameCtx(0, FPS, Pool(), report=lambda *a: got.append(a))
    SourceLayer(Boom(), 0.0).render(fc, np.zeros((1, 1, 4)))
    assert len(got) == 1 and fc.errors == []


def test_amount_eps_threshold():
    e = FakeEffect("eps", add=1.0, amount=AMOUNT_EPS / 2)
    chain = EffectChain([slot(e, "eps")])
    run_chain(chain, 0.0)
    assert e.renders == 0
