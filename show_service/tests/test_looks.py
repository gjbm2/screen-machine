"""looks: the look engine (README S5.5). Deterministic: fake clock bodies, fake features, fake palette_fn and look_fn,
and evaluate() called at chosen send times. Nothing is sent anywhere; config files are read, never written.

``-s`` prints the evaluate budgets (README S5.3: 4 Hue lamps <= 150 us, 32 DMX fixtures <= 600 us at p95).
"""

from __future__ import annotations

import math
import statistics
import time

import pytest
import yaml

from show_service import artnet_out as ao
from show_service import clock as clockmod
from show_service import contract
from show_service import looks as lk
from show_service.looks import LookEngine

HUE_CAPS = {"hits": False, "min_attack_ms": 80.0, "grid": "fixed", "rate_hz": 50.0, "keepalive_s": None}
DMX_CAPS = {"hits": True, "min_attack_ms": 0.0, "grid": "beat", "rate_hz": 44.0, "keepalive_s": 1.0}
_RANK = {"none": 0, "dmx": 1, "beat": 2}


# -- fakes ------------------------------------------------------------------------------------------------------------
class SnapClock:
    """A published music.clock body (snapshot) and artnet_out's BeatClock (next_beat, gated as clock.Clock does)."""

    def __init__(self, anchor: float = 10.0, period: float = 0.5, gate: str = "beat", k0: int = 0,
                 bar_anchor: int = 0, beats_per_bar: int = 4, phrase_bars: int = 8) -> None:
        self.body = {"bpm": 60.0 / period, "anchor_ms": anchor * 1000.0, "beat_at_anchor": k0,
                     "beats_per_bar": beats_per_bar, "bar_anchor_beat": bar_anchor, "phrase_bars": phrase_bars,
                     "conf": 0.9, "bar_conf": 0.9, "phrase_conf": 0.9, "state": "locked", "gate": gate, "epoch": 1,
                     "stability": 0.5, "source": "test", "t_ms": anchor * 1000.0}

    def snapshot(self):
        return self.body

    def set_gate(self, gate: str) -> None:
        self.body = {**self.body, "gate": gate}

    def next_beat(self, t_s: float, lead_s: float = 0.0, *, gate: str = "beat"):
        d = self.body
        if _RANK[d["gate"]] < _RANK[gate]:
            return None
        r = clockmod.next_beat(d, t_s, lead_s)
        return None if r is None else r[1]


def feats(t_s: float, *, energy: float = 0.5, gate: float = 1.0) -> dict:
    return {"t_ms": t_s * 1000.0, "stream": "test",
            "bands": {n: {"level": 0.0, "hits": 0, "presence": 0.0} for n in contract.BAND_NAMES},
            "onset": 0.0, "lufs_m": -20.0, "lufs_s": -20.0, "peak_dbfs": -6.0, "rms_dbfs": -20.0,
            "brightness": 0.5, "noisiness": 0.1, "gate": gate, "energy": energy, "punch": 0.5, "drive": 0.0,
            "spectrum32": [0.0] * 32}


def look(**kw) -> dict:
    d = {"t_ms": 0.0, "seq": 1, "look_id": "L1", "vibe": "v", "mode": None, "palette": {}, "level": 1.0,
         "floor": 0.0, "house": False, "low": False, "key_cct": 3000.0, "chroma_max": 0.3, "lamp_chroma_max": 0.3,
         "saturation": 0.5, "contrast": 0.5, "motion": {"speed": 0.0, "amount": 0.0},
         "accents": {"style": "beat", "density": 0.5, "subdivision": 1, "strobe": False, "beat_lock": True},
         "spread": 0.0, "change_rate": 0.5, "transition": {"style": "cut", "beats": None, "seconds": None},
         "lamps": {}, "screens": {}}
    d.update(kw)
    return d


# -- registry builders (contract.RigView shapes; fixtures.py isn't needed) ---------------------------------------------
HUE_T = {"id": "sm-hue", "source": "builtin", "kind": "lamp", "file": None, "mode": None, "footprint": 0,
         "attrs": ["intensity", "colour"], "channels": [], "emitters": {}, "wheel": [], "curve": "srgb", "problems": []}
SCREEN_T = {"id": "sm-screen", "source": "builtin", "kind": "screen", "file": None, "mode": None, "footprint": 0,
            "attrs": ["colour"], "channels": [], "emitters": {}, "wheel": [], "curve": "srgb", "problems": []}


def par_type(tid: str = "par") -> dict:
    """An RGBW par with a dimmer and a shutter (Open = 8)."""
    chans = [("Dimmer", "intensity", None), ("Shutter", "shutter", None), ("Red", "emitter", "red"),
             ("Green", "emitter", "green"), ("Blue", "emitter", "blue"), ("White", "emitter", "white")]
    out = []
    for i, (n, a, e) in enumerate(chans):
        c = {"offset": i, "name": n, "attr": a, "default": 8 if a == "shutter" else 0}
        if e:
            c["emitter"] = e
        out.append(c)
    return {"id": tid, "source": "ofl", "kind": "lamp", "file": None, "mode": None, "footprint": len(out),
            "attrs": ["intensity", "colour"], "channels": out, "emitters": {}, "wheel": [], "curve": "srgb",
            "problems": []}


def lat(rise: float = 0.0, colour: float | None = None, min_pulse: float = 40.0) -> dict:
    return {"rise_ms": rise, "fall_ms": 0.0, "colour_ms": rise if colour is None else colour, "position_ms": 0.0,
            "min_pulse_ms": min_pulse, "source": {}}


def fx(fid: str, typ: dict, output: str, **kw) -> dict:
    f = {"id": fid, "label": fid, "type": typ["id"], "kind": typ["kind"], "output": output, "address": None,
         "light": None, "screen": None, "zone": None, "placement": None, "role": None, "tags": [], "order": None,
         "wb": {}, "curve": None, "pin": {}, "house": None, "latency": lat()}
    f.update(kw)
    return f


def out(oid: str, driver: str, **kw) -> dict:
    o = {"id": oid, "driver": driver, "enabled": True, "built": True, "node": None, "port_address": None,
         "latency_key": oid, "latency": {}, "hits": None, "min_attack_ms": None, "rate_hz": None, "settings": {},
         "problems": []}
    o.update(kw)
    return o


def rigview(fixtures: list[dict], types: list[dict], outputs: list[dict], groups: dict | None = None) -> dict:
    return {"revision": "test", "path": "test", "error": None, "problems": [],
            "outputs": {o["id"]: o for o in outputs}, "nodes": {}, "types": {t["id"]: t for t in types},
            "fixtures": {f["id"]: f for f in fixtures}, "groups": groups or {}}


def lamps_rig(n: int = 4, rise: tuple = (), output: str = "hue") -> dict:
    fs = [fx(f"lamp.{i}", HUE_T, output, latency=lat(rise[i] if i < len(rise) else 0.0)) for i in range(n)]
    return rigview(fs, [HUE_T], [out(output, "hue")])


def layer(**kw) -> dict:
    d = {"id": "l1", "attr": "intensity", "fx": "square", "phase": "beat", "depth": 1.0, "needs": "beat",
         "without_beat": "energy", "attack_ms": 0, "decay_ms": 250, "select": "kind:lamp"}
    d.update(kw)
    return d


def show_looks() -> dict:
    return yaml.safe_load(contract.SHOW_FILE.read_text())["looks"]


def inten(frame: dict, fid: str) -> float:
    return frame["fixtures"][fid]["intensity"]


# -- the screens rule -------------------------------------------------------------------------------------------------
def test_a_screen_fixture_never_reaches_a_frame():
    screen_dmx_t = {**par_type("screen-par"), "kind": "screen"}
    fs = [fx("scr.a", SCREEN_T, "mixed", screen="s-a"), fx("scr.b", SCREEN_T, "screens"),
          fx("scr.c", screen_dmx_t, "mixed", address=40),
          fx("lamp.a", HUE_T, "mixed", screen="s-a", placement="p1"), fx("par.a", par_type(), "mixed", address=1)]
    rig = rigview(fs, [SCREEN_T, HUE_T, par_type(), screen_dmx_t],
                  [out("mixed", "artnet"), out("screens", "showbus")], groups={"all": ["scr.a", "lamp.a", "scr.b"]})
    looks = {"master": 0.5, "caps": {"flash_hz_max": 2.0},
             "layers": [layer(id="a", select="*"), layer(id="b", fx="sine", phase="time", period_s=1.0,
                                                         needs="none", select="group:all", spread=1.0)],
             "hits": {"select": "*", "styles": ["beat"], "min_strength": 0.0, "depth": 1.0, "decay_ms": 100}}
    clk = SnapClock()
    box = {"pal": {"scr.a": (1, 0, 0), "scr.b": (0, 1, 0), "scr.c": (0, 0, 1), "lamp.a": (1, 1, 1),
                   "par.a": (1, 1, 1)}, "f": feats(10.0)}
    e = LookEngine(palette_fn=lambda t: box["pal"], look_fn=lambda: look(), clock=clk, features_fn=lambda: box["f"])
    assert e.configure(looks, rig) == []
    prog = e._program
    assert all(not (set(L.members) & {"scr.a", "scr.b", "scr.c"}) for L in prog.layers)
    assert not prog.hits.members & {"scr.a", "scr.b", "scr.c"}
    e.on_hit({"t_ms": 10_000.0, "band": "bass", "strength": 1.0, "stream": "test"})
    seen = set()
    for i in range(40):
        t = 10.0 + i * 0.023
        if i == 20:
            box["pal"], box["f"] = None, feats(0.0)          # no vibe, stale features: inactive (the house looks)
        for oid in ("mixed", "screens", "nowhere"):
            seen |= set(e.evaluate(oid, t, DMX_CAPS)["fixtures"])
    assert seen == {"lamp.a", "par.a"}
    assert e.evaluate("screens", 11.0, DMX_CAPS)["fixtures"] == {}


# -- parity with the virtual Maestro ----------------------------------------------------------------------------------
def test_parity_with_the_virtual_maestro():
    lay = ao.load_layout(ao.PATCH_FILE)                          # read only: the hue devices and their channels
    hue_devs = [d for d in lay.devices if d.kind == "hue"]
    assert len(hue_devs) >= 1
    clk = SnapClock(anchor=10.0, period=0.5)
    box = {"f": feats(10.0)}
    vm = ao.VirtualMaestro(layout=lay, clock=clk, features_fn=lambda: box["f"], room_dmx_url=None)
    e = LookEngine(clock=clk, features_fn=lambda: box["f"])     # neutral: artnet_out's NEUTRAL_RGB and HUE_DEPTH
    fs = [fx(d.id, HUE_T, "hue", screen=d.screen) for d in hue_devs]
    rig = rigview(fs, [HUE_T], [out("hue", "hue")], groups={"around": [d.id for d in hue_devs]})
    assert e.configure(show_looks(), rig) == []
    counts = {"beat": 0, "dmx": 0, "none": 0, "idle_quiet": 0, "idle_stale": 0, "back": 0}
    idle = {"idle_quiet": 0, "idle_stale": 0}
    worst = 0.0
    t = 10.0
    while t < 43.0:
        t = vm.plan(t)
        energy = 0.45 + 0.35 * math.sin(1.3 * t)
        if t < 14.0:
            phase, box["f"] = "beat", feats(t - 0.012, energy=energy)
        elif t < 17.0:
            phase, box["f"] = "dmx", feats(t - 0.012, energy=energy)
        elif t < 19.0:
            phase, box["f"] = "none", feats(t - 0.012, energy=energy)
        elif t < 31.0:
            phase, box["f"] = "idle_quiet", feats(t - 0.012, energy=0.0, gate=0.0)     # silent for 12 s
        elif t < 34.0:
            phase, box["f"] = "back", feats(t - 0.012, energy=energy, gate=0.5)
        else:
            phase, box["f"] = "idle_stale", feats(34.0, energy=energy)                 # the features stop at 34 s
        clk.set_gate({"beat": "beat", "back": "beat", "dmx": "dmx"}.get(phase, "none"))
        ok = vm.compose(t)
        fr = e.evaluate("hue", t, HUE_CAPS)
        assert fr["active"] == ok, (t, phase, fr["why"], vm.idle_reason)
        if not ok:
            assert fr["why"] == vm.idle_reason
            idle[phase] += 1
            continue
        counts[phase] += 1
        data = vm.channels()
        for d in hue_devs:
            idx = {c.field: c.index for c in d.channels}
            want = [data[idx["r"]] / 255, data[idx["g"]] / 255, data[idx["b"]] / 255]
            got = fr["fixtures"][d.id]["rgb"]
            err = max(abs(a - b) for a, b in zip(got, want))
            worst = max(worst, err)
            assert err <= 1 / 255 + 1e-9, (t, phase, d.id, got, want)
    assert all(n > 20 for n in counts.values()) and all(n > 20 for n in idle.values()), (counts, idle)
    assert worst <= 0.5 / 255 + 1e-9                             # rounding to a byte is the only difference


def test_set_look_reaches_the_swell_like_artnet_outs():
    clk = SnapClock()
    vibe = {"pal": {"lamp.0": (1.0, 1.0, 1.0)}}
    e = LookEngine(palette_fn=lambda t: vibe["pal"], clock=clk)
    e.configure({"layers": [layer(fx="swell", depth="swell", attack_ms=80)]}, lamps_rig(1))
    t = 10.25                                                   # half way between beats: the swell has decayed
    e.set_look(hue_depth=0.0)
    assert inten(e.evaluate("hue", t, DMX_CAPS), "lamp.0") == pytest.approx(1.0)
    e.set_look(hue_depth=0.6)
    want = 1 - 0.6 + 0.6 * math.exp(-0.25 / ao.HUE_DECAY_S)
    assert inten(e.evaluate("hue", t + 0.5, DMX_CAPS), "lamp.0") == pytest.approx(want)
    e.set_look(hue_depth=float("nan"))                          # ignored
    assert e.hue_depth == 0.6
    e.set_look(reset=True)
    assert e.hue_depth == ao.HUE_DEPTH and e.beat_lock is True


# -- latency ----------------------------------------------------------------------------------------------------------
def test_each_lamp_is_evaluated_at_its_own_lead():
    clk = SnapClock(anchor=10.0, period=0.5)
    calls = []

    def pal(t):
        calls.append(round(t, 6))
        return {"lamp.0": (1.0, 0.5, 0.0), "lamp.1": (1.0, 0.5, 0.0)}

    e = LookEngine(palette_fn=pal, clock=clk)
    e.configure({"layers": [layer(fx="swell", depth=1.0, attack_ms=80, decay_ms=250)]}, lamps_rig(2, rise=(100.0, 0.0)))
    best = {}
    for i in range(600):
        t = 10.2 + i * 0.001
        fr = e.evaluate("hue", t, DMX_CAPS)
        for fid in ("lamp.0", "lamp.1"):
            v = inten(fr, fid)
            if v > best.get(fid, (-1, 0))[0] + 1e-12:
                best[fid] = (v, t)
    assert best["lamp.1"][1] == pytest.approx(10.5, abs=0.0011)        # no lead: peaks on the beat
    assert best["lamp.0"][1] == pytest.approx(10.4, abs=0.0011)        # 100 ms lead: 100 ms earlier in send time
    calls.clear()
    fr = e.evaluate("hue", 11.0, DMX_CAPS)
    assert fr["fixtures"]["lamp.0"]["t_eval_ms"] == pytest.approx(11100.0)
    assert fr["fixtures"]["lamp.1"]["t_eval_ms"] == pytest.approx(11000.0)
    assert sorted(calls) == [11.0, 11.1]                         # once per distinct time in the frame


def test_colour_follows_its_own_latency():
    pal = lambda t: {"lamp.0": (1.0, 0.0, 0.0) if t < 10.05 else (0.0, 0.0, 0.5)}   # noqa: E731
    e = LookEngine(palette_fn=pal)
    rig = rigview([fx("lamp.0", HUE_T, "hue", latency=lat(rise=0.0, colour=100.0))], [HUE_T], [out("hue", "hue")])
    e.configure({"layers": []}, rig)
    ff = e.evaluate("hue", 10.0, DMX_CAPS)["fixtures"]["lamp.0"]
    assert ff["intensity"] == 1.0                                # the level at t (red, full)
    assert ff["colour"] == [0.0, 0.0, 1.0]                       # the colour at t + 100 ms (blue, at full value)
    assert ff["rgb"] == [0.0, 0.0, 1.0]


# -- the gate policy --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("gate,beat_lock,wob,hue,dmx", [
    ("beat", True, "energy", 0.0, 0.0),        # beat-locked everywhere
    ("dmx", True, "energy", 0.4, 0.5),         # Hue: energy; DMX: beat-locked at middle_depth 0.5
    ("none", True, "energy", 0.4, 0.4),        # nothing beat-locked
    ("beat", False, "energy", 0.4, 0.4),       # the vibe's beat_lock off
    ("none", True, "none", 1.0, 1.0),          # without_beat none: steady
])
def test_the_gate_policy(gate, beat_lock, wob, hue, dmx):
    res = {}
    for oid, caps in (("hue", HUE_CAPS), ("dmx", DMX_CAPS)):
        clk = SnapClock(gate=gate)
        e = LookEngine(palette_fn=lambda t: {"lamp.0": (1.0, 1.0, 1.0)}, clock=clk,
                       features_fn=lambda: feats(10.0, energy=0.4))
        e.configure({"middle_depth": 0.5, "layers": [layer(without_beat=wob)]}, lamps_rig(1, output=oid))
        e.set_look(beat_lock=beat_lock)
        res[oid] = inten(e.evaluate(oid, 10.375, caps), "lamp.0")   # p 0.75: the square is 0
    assert res["hue"] == pytest.approx(hue) and res["dmx"] == pytest.approx(dmx)


def test_needs_grid_and_none_without_a_clock():
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1), "lamp.1": (1, 1, 1)})
    rig = lamps_rig(2)
    e.configure({"layers": [layer(id="g", needs="grid", without_beat="none", select="id:lamp.0"),
                            layer(id="n", needs="none", phase="bar", period_bars=1, select="id:lamp.1")]}, rig)
    # no clock at all: grid -> without_beat (steady); none free-runs at FREE_RUN_BPM from 0 (a bar = 2 s)
    fr = e.evaluate("hue", 101.0, DMX_CAPS)                     # 101 s = 50.5 bars: p 0.5 -> the square is 0
    assert inten(fr, "lamp.0") == 1.0 and inten(fr, "lamp.1") == 0.0
    assert inten(e.evaluate("hue", 102.2, DMX_CAPS), "lamp.1") == 1.0      # p 0.1


def test_free_run_keeps_the_last_tempo():
    clk = SnapClock(anchor=10.0, period=0.5)
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1)}, clock=clk)
    e.configure({"layers": [layer(needs="none")]}, lamps_rig(1))
    assert inten(e.evaluate("hue", 10.375, DMX_CAPS), "lamp.0") == 0.0
    clk.body = None                                              # the clock lost its grid
    assert inten(e.evaluate("hue", 20.375, DMX_CAPS), "lamp.0") == 0.0      # still on the old grid
    assert inten(e.evaluate("hue", 20.125, DMX_CAPS), "lamp.0") == 1.0


# -- shapes, subdivision, spread, glides ------------------------------------------------------------------------------
def test_shapes():
    for p, sine, saw, sq, tri in ((0.0, 1.0, 1.0, 1.0, 1.0), (0.25, 0.5, 0.75, 1.0, 0.5),
                                  (0.5, 0.0, 0.5, 0.0, 0.0), (0.75, 0.5, 0.25, 0.0, 0.5)):
        assert lk.shape("sine", 7 + p) == pytest.approx(sine)
        assert lk.shape("saw", 7 + p) == pytest.approx(saw)
        assert lk.shape("square", 7 + p) == sq
        assert lk.shape("triangle", 7 + p) == pytest.approx(tri)
    P, a, d = 0.5, 0.08, 0.25
    assert lk.shape("swell", 3.0, P, a, d) == 1.0
    assert lk.shape("swell", 3.0 - 0.04 / P, P, a, d) == pytest.approx(0.5)             # half the attack: 0.5
    assert lk.shape("swell", 3.0 + 0.1 / P, P, a, d) == pytest.approx(math.exp(-0.1 / d))


def test_subdivision_and_the_two_attack_rule():
    pal = lambda t: {"lamp.0": (1, 1, 1)}   # noqa: E731
    clk = SnapClock(anchor=10.0, period=0.5)
    e = LookEngine(palette_fn=pal, clock=clk, look_fn=lambda: look(accents={"style": "beat", "subdivision": 0.5}))
    e.configure({"layers": [layer(subdivide=True, needs="none")]}, lamps_rig(1))
    assert inten(e.evaluate("hue", 10.15, DMX_CAPS), "lamp.0") == 0.0      # half-beat cycles: p 0.6
    assert inten(e.evaluate("hue", 10.275, DMX_CAPS), "lamp.0") == 1.0     # the off-beat starts a cycle
    # 150 bpm, 1/4: a 100 ms cycle is under 2 x 80 ms on Hue, so it runs at 1/2 there; DMX keeps 1/4
    clk2 = SnapClock(anchor=10.0, period=0.4)
    for oid, caps, want in (("hue", HUE_CAPS, 0.0), ("dmx", DMX_CAPS, 1.0)):
        e2 = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1)}, clock=clk2,
                        look_fn=lambda: look(accents={"style": "beat", "subdivision": 0.25}))
        e2.configure({"layers": [layer(subdivide=True, needs="none")]}, lamps_rig(1, output=oid))
        assert inten(e2.evaluate(oid, 10.12, caps), "lamp.0") == want, oid     # 1/2: p 0.6; 1/4: p 0.2


def test_phase_spread_walks_the_group_in_order():
    order = ["lamp.3", "lamp.0", "lamp.2", "lamp.1"]
    rig = {**lamps_rig(4), "groups": {"ring": order}}
    clk = SnapClock(anchor=10.0, period=0.5)                     # a bar = 2 s
    e = LookEngine(palette_fn=lambda t: {f"lamp.{i}": (1, 1, 1) for i in range(4)}, clock=clk,
                   look_fn=lambda: look(spread=1.0))
    e.configure({"layers": [layer(fx="sine", phase="bar", period_bars=1, needs="none", spread="look.spread",
                                  select="group:ring")]}, rig)
    fr = e.evaluate("hue", 12.0, DMX_CAPS)                       # a bar line
    assert [round(inten(fr, f), 6) for f in order] == [1.0, 0.5, 0.0, 0.5]   # a quarter cycle apart
    fr = e.evaluate("hue", 12.5, DMX_CAPS)                       # a quarter bar later: the next member peaks
    assert [round(inten(fr, f), 6) for f in order] == [0.5, 1.0, 0.5, 0.0]


def test_parameter_changes_glide_over_the_transition_in_beats():
    clk = SnapClock(anchor=10.0, period=0.5)                     # 4 beats = 2 s
    box = {"look": look(motion={"speed": 0.0, "amount": 0.2},
                        transition={"style": "crossfade", "beats": 4, "seconds": None})}
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1)}, clock=clk, look_fn=lambda: box["look"])
    e.configure({"layers": [layer(depth="motion.amount", needs="none")]}, lamps_rig(1))
    t = 10.375                                                   # p 0.75 every half second: the square is 0
    assert inten(e.evaluate("hue", t, DMX_CAPS), "lamp.0") == pytest.approx(0.8)     # the first value: at once
    box["look"] = {**box["look"], "motion": {"speed": 0.0, "amount": 0.6}}
    assert inten(e.evaluate("hue", t + 0.5, DMX_CAPS), "lamp.0") == pytest.approx(0.8)
    assert inten(e.evaluate("hue", t + 1.5, DMX_CAPS), "lamp.0") == pytest.approx(0.6)   # half way: d 0.4
    assert inten(e.evaluate("hue", t + 2.5, DMX_CAPS), "lamp.0") == pytest.approx(0.4)   # arrived
    box["look"] = {**box["look"], "motion": {"speed": 0.0, "amount": 0.1},
                   "transition": {"style": "cut", "beats": None, "seconds": None}}
    assert inten(e.evaluate("hue", t + 3.0, DMX_CAPS), "lamp.0") == pytest.approx(0.9)   # a cut: at once


def test_a_period_change_crossfades_the_waves():
    clk = SnapClock(anchor=10.0, period=0.5)                     # a bar = 2 s
    box = {"look": look(motion={"speed": 0.0, "amount": 1.0},
                        transition={"style": "crossfade", "beats": None, "seconds": 4.0})}
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1)}, clock=clk, look_fn=lambda: box["look"])
    e.configure({"layers": [layer(phase="bar", period_bars=[8, 1], needs="none")]}, lamps_rig(1))
    t = 10.0 + 16.0 * 2 + 3.0                                    # 1.5 bars past a phrase of 8 bars
    assert inten(e.evaluate("hue", t, DMX_CAPS), "lamp.0") == 1.0           # 8-bar wave: p 0.1875 -> 1
    box["look"] = {**box["look"], "motion": {"speed": 1.0, "amount": 1.0}}
    assert inten(e.evaluate("hue", t, DMX_CAPS), "lamp.0") == 1.0           # the fade starts at the old wave
    assert inten(e.evaluate("hue", t + 2.0, DMX_CAPS), "lamp.0") == pytest.approx(0.5)   # 8-bar 1, 1-bar 0: halfway
    assert inten(e.evaluate("hue", t + 4.0, DMX_CAPS), "lamp.0") == 0.0     # the 1-bar wave: p 0.5 -> 0
    assert e._program.layers[0].period == (8.0, 1.0)
    for sp, want in ((0.0, 8.0), (1.0, 1.0), (0.34, 4.0), (0.66, 2.0)):
        assert e._period(e._program.layers[0], look(motion={"speed": sp, "amount": 0})) == want


# -- hits -------------------------------------------------------------------------------------------------------------
def hits_engine(*, style="beat", gate="beat", bands=None):
    clk = SnapClock(gate=gate)
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (0.4, 0.2, 0.0)}, clock=clk,
                   look_fn=lambda: look(accents={"style": style, "subdivision": 1}))
    hits = {"select": "kind:lamp", "styles": ["beat", "full"], "min_strength": 0.3, "depth": 0.5, "decay_ms": 120}
    if bands is not None:
        hits["bands"] = bands
    rig = rigview([fx("lamp.0", HUE_T, "o", latency=lat(min_pulse=40.0))], [HUE_T], [out("o", "artnet")])
    assert e.configure({"layers": [], "hits": hits}, rig) == []
    return e


def test_hits_flash_htp_then_hold_then_decay():
    e = hits_engine()
    e.on_hit({"t_ms": 20_000.0, "band": "bass", "strength": 0.8, "stream": "test"})
    assert inten(e.evaluate("o", 19.99, DMX_CAPS), "lamp.0") == pytest.approx(0.8)    # shown at once (age 0)
    assert inten(e.evaluate("o", 20.039, DMX_CAPS), "lamp.0") == pytest.approx(0.8)   # held min_pulse_ms
    assert inten(e.evaluate("o", 20.16, DMX_CAPS), "lamp.0") == pytest.approx(0.4 + 0.4 * math.exp(-1.0))
    assert inten(e.evaluate("o", 21.5, DMX_CAPS), "lamp.0") == pytest.approx(0.4)
    assert e.stats()["per_output"]["o"]["hits"] == 1
    # never on an output whose caps don't take hits
    e2 = hits_engine()
    e2.on_hit({"t_ms": 20_000.0, "band": "bass", "strength": 0.8, "stream": "test"})
    assert inten(e2.evaluate("o", 20.01, HUE_CAPS), "lamp.0") == pytest.approx(0.4)
    # an output that turns them off in fixtures.yaml
    e3 = hits_engine()
    rig = rigview([fx("lamp.0", HUE_T, "o")], [HUE_T], [out("o", "artnet", hits=False)])
    e3.configure({"layers": [], "hits": {"select": "*", "styles": ["beat"], "min_strength": 0.0}}, rig)
    e3.on_hit({"t_ms": 20_000.0, "band": "bass", "strength": 1.0, "stream": "test"})
    assert inten(e3.evaluate("o", 20.01, DMX_CAPS), "lamp.0") == pytest.approx(0.4)


@pytest.mark.parametrize("kw,hit", [({}, {"strength": 0.2}), ({"style": "soft"}, {}), ({"gate": "none"}, {}),
                                    ({"bands": ["sub"]}, {"band": "mid"})])
def test_hits_filters(kw, hit):
    e = hits_engine(**kw)
    e.on_hit({"t_ms": 20_000.0, "band": "bass", "strength": 0.8, "stream": "test", **hit})
    assert inten(e.evaluate("o", 20.01, DMX_CAPS), "lamp.0") == pytest.approx(0.4)


@pytest.mark.parametrize("steady", ["house", "low"])
def test_no_hits_under_house_lights_or_lights_low(steady):
    """c8's integration ruling (16 Oct): House lights and Lights low are steady, as the director's own drop hit and
    swell depth are; a music.hit never flashes over them."""
    clk = SnapClock()
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (0.4, 0.2, 0.0)}, clock=clk,
                   look_fn=lambda: look(**{steady: True}, accents={"style": "beat", "subdivision": 1}))
    rig = rigview([fx("lamp.0", HUE_T, "o", latency=lat(min_pulse=40.0))], [HUE_T], [out("o", "artnet")])
    assert e.configure({"layers": [], "hits": {"select": "kind:lamp", "styles": ["beat"], "min_strength": 0.0,
                                                "depth": 0.5, "decay_ms": 120}}, rig) == []
    e.on_hit({"t_ms": 20_000.0, "band": "bass", "strength": 1.0, "stream": "test"})
    assert inten(e.evaluate("o", 20.01, DMX_CAPS), "lamp.0") == pytest.approx(0.4)


def test_takes_hit_follows_the_hits_block_the_look_and_the_gate():
    e = hits_engine(bands=["bass"])
    assert e.takes_hit({"t_ms": 1.0, "band": "bass", "strength": 0.8})
    assert not e.takes_hit({"t_ms": 1.0, "band": "bass", "strength": 0.2})           # under min_strength
    assert not e.takes_hit({"t_ms": 1.0, "band": "air", "strength": 0.8})            # not one of the bands
    assert not e.takes_hit("junk") and not e.takes_hit({"strength": "x"})
    assert not hits_engine(style="soft").takes_hit({"band": "bass", "strength": 1.0})
    assert not hits_engine(gate="none").takes_hit({"band": "bass", "strength": 1.0})
    e2 = LookEngine(look_fn=lambda: look(low=True))
    assert not e2.takes_hit({"strength": 1.0})                                      # no hits block at all


def test_hit_garbage_is_ignored():
    e = hits_engine()
    for bad in ({}, {"t_ms": "x"}, {"t_ms": float("nan"), "strength": 1}):
        e.on_hit(bad)
    assert len(e._hits) == 0


# -- master and caps --------------------------------------------------------------------------------------------------
def test_master_scales_lamps():
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (1.0, 0.5, 0.0)})
    e.configure({"master": 0.5, "layers": []}, lamps_rig(1))
    ff = e.evaluate("hue", 10.0, DMX_CAPS)["fixtures"]["lamp.0"]
    assert ff["intensity"] == 0.5 and ff["rgb"] == [0.5, 0.25, 0.0] and ff["colour"] == [1.0, 0.5, 0.0]


def test_no_rise_faster_than_min_attack():
    box = {"v": 0.0}
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (box["v"],) * 3})
    e.configure({"layers": []}, lamps_rig(1))
    t, prev, rises, t_dark, t_full = 10.0, None, [], None, None
    for i in range(30):
        box["v"] = 0.0 if i < 5 else 1.0                          # a step from dark to full
        v = inten(e.evaluate("hue", t, HUE_CAPS), "lamp.0")
        if prev is not None:
            rises.append(v - prev)
        if v == 0.0:
            t_dark = t
        if v >= 1.0 - 1e-9 and t_full is None:
            t_full = t
        prev, t = v, t + 0.02
    lim = lk.SLEW_PEAK * 0.02 / 0.080
    assert max(rises) == pytest.approx(lim) and prev == 1.0
    assert all(r <= lim + 1e-9 for r in rises)
    assert t_full - t_dark >= 0.080 / lk.SLEW_PEAK - 1e-9          # dark to full takes at least 53 ms
    # the same step on DMX (min_attack 0): at once
    e2 = LookEngine(palette_fn=lambda t: {"lamp.0": (box["v"],) * 3})
    e2.configure({"layers": []}, lamps_rig(1))
    box["v"] = 0.0
    e2.evaluate("hue", 10.0, DMX_CAPS)
    box["v"] = 1.0
    assert inten(e2.evaluate("hue", 10.02, DMX_CAPS), "lamp.0") == 1.0


def test_flash_rate_cap():
    box = {"v": 0.0}
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (box["v"],) * 3})
    e.configure({"layers": [], "caps": {"flash_hz_max": 2.0}}, lamps_rig(1))
    big = []
    for i in range(100):                                          # a 10 Hz full flash, frames every 25 ms
        t = 10.0 + i * 0.025
        box["v"] = 1.0 if (i // 2) % 4 == 3 else 0.0
        v = inten(e.evaluate("hue", t, DMX_CAPS), "lamp.0")
        if v >= 1.0 - 1e-9:
            big.append(t)
    starts = [b for i, b in enumerate(big) if i == 0 or b - big[i - 1] > 0.03]
    assert len(starts) >= 4
    assert all(b - a >= 0.5 - 1e-9 for a, b in zip(starts, starts[1:]))


# -- allocation, house, neutral ---------------------------------------------------------------------------------------
def test_allocation_by_role_and_placement_then_house():
    par = par_type()
    fs = [fx("lamp.n1", HUE_T, "hue", placement="p-up", screen="s1", zone="z1"),
          fx("lamp.n2", HUE_T, "hue", placement="p-op", screen="s1", zone="z1"),
          fx("lamp.s1", HUE_T, "hue", placement="p-up", screen="s2", zone="z2"),
          fx("spot.x", par, "dmx", address=1, placement="p-up", screen="s2", zone="z2"),
          fx("spot.y", par, "dmx", address=7, role="r-key"),
          fx("spot.z", par, "dmx", address=13),
          fx("spot.w", par, "dmx", address=19, house={"intensity": 0.5, "hex": "#ff0000"})]
    rig = rigview(fs, [HUE_T, par], [out("hue", "hue"), out("dmx", "artnet")])
    pal = {"lamp.n1": (1.0, 0.0, 0.0), "lamp.n2": (0.0, 1.0, 0.0), "lamp.s1": (0.0, 0.0, 1.0)}
    box = {"pal": pal}
    lk_ = look(lamps={"lamp.n2": {"role": "r-key", "screen": "s1", "hex": "#00ff00", "rgb": [0, 1, 0], "level": 1}})
    e = LookEngine(palette_fn=lambda t: box["pal"], look_fn=lambda: lk_)
    assert e.configure({"layers": []}, rig) == []
    fr = e.evaluate("dmx", 10.0, DMX_CAPS)["fixtures"]
    assert fr["spot.x"]["rgb"] == [0.0, 0.0, 1.0]                # the same placement, the same screen first
    assert fr["spot.y"]["rgb"] == [0.0, 1.0, 0.0]                # a lamp the director gives the same role
    assert fr["spot.z"]["rgb"] == [0.0, 0.0, 0.0]                # nothing: dark
    assert fr["spot.w"]["rgb"] == [0.5, 0.0, 0.0]                # its house look
    box["pal"] = {**pal, "*": (0.2, 0.2, 0.2)}
    assert e.evaluate("dmx", 10.1, DMX_CAPS)["fixtures"]["spot.z"]["rgb"] == pytest.approx([0.2] * 3)
    # no vibe: the neutral colour at full value; one colour from palette_fn: that colour
    box["pal"] = None
    for ff in e.evaluate("dmx", 10.2, DMX_CAPS)["fixtures"].values():
        assert ff["rgb"] == pytest.approx(list(ao.NEUTRAL_RGB))
    box["pal"] = (0.0, 0.5, 1.0)
    assert e.evaluate("dmx", 10.3, DMX_CAPS)["fixtures"]["spot.y"]["rgb"] == [0.0, 0.5, 1.0]


def test_lamps_let_roles_follow_placements():
    par = par_type()
    fs = [fx("lamp.n2", HUE_T, "hue", placement="p-op", screen="s1"),
          fx("lamp.n1", HUE_T, "hue", placement="p-up", screen="s1"),
          fx("spot.k", par, "dmx", address=1, role="r-key")]
    rig = rigview(fs, [HUE_T, par], [out("hue", "hue"), out("dmx", "artnet")])
    pal = {"lamp.n1": (1.0, 0.0, 0.0), "lamp.n2": (0.0, 1.0, 0.0)}
    lamps = {"roles": {"r-key": {}, "r-ext": {}}, "placement_roles": {"p-op": "r-key", "p-up": "r-ext"},
             "default_role": "r-ext"}
    looks = {"layers": [layer(id="key", needs="none", depth=1.0, select="role:r-key")]}
    e = LookEngine(palette_fn=lambda t: pal)
    e.configure(looks, rig)                                       # written roles only
    assert e.evaluate("dmx", 10.0, DMX_CAPS)["fixtures"]["spot.k"]["rgb"] == [0.0, 0.0, 0.0]
    assert set(e._program.layers[0].members) == {"spot.k"}
    assert lk.role_of(fs[0], None) is None and lk.role_of(fs[0], lamps) == "r-key"
    assert lk.role_of(fs[2], lamps) == "r-key" and lk.role_of({"kind": "lamp"}, lamps) == "r-ext"
    assert e.configure(looks, rig, lamps=lamps) == []             # show.yaml lamps: roles by placement
    assert e.evaluate("dmx", 10.1, DMX_CAPS)["fixtures"]["spot.k"]["rgb"] == [0.0, 1.0, 0.0]
    assert set(e._program.layers[0].members) == {"spot.k", "lamp.n2"}


def test_inactive_frames_carry_the_house_look():
    par = par_type()
    fs = [fx("spot.a", par, "dmx", address=1, house={"intensity": 0.5, "hex": "#ff8000"}),
          fx("spot.b", par, "dmx", address=7)]
    rig = rigview(fs, [par], [out("dmx", "artnet")])
    box = {"f": feats(0.0)}
    e = LookEngine(features_fn=lambda: box["f"])
    assert e.configure(None, rig) == []
    fr = e.evaluate("dmx", 10.0, DMX_CAPS)
    assert fr["active"] is False and fr["why"] == "no_features"
    a, b = fr["fixtures"]["spot.a"], fr["fixtures"]["spot.b"]
    assert a["intensity"] == 0.5 and a["colour"] == pytest.approx([1.0, 128 / 255, 0.0])
    assert a["dmx"][0] == 128 and a["dmx"][1] == 8                # dimmer at the house level, the shutter Open
    assert b["intensity"] == 0.0 and list(b["dmx"]) == [0, 8, 0, 0, 0, 0]
    assert e.active(10.0) == (False, "no_features")
    box["f"] = feats(10.0, gate=0.0)                              # fresh, but silent
    assert e.evaluate("dmx", 10.0, DMX_CAPS)["active"] is True
    box["f"] = feats(19.0, gate=0.0)
    assert e.evaluate("dmx", 19.0, DMX_CAPS)["active"] is True
    box["f"] = feats(20.1, gate=0.0)
    fr = e.evaluate("dmx", 20.1, DMX_CAPS)
    assert fr["active"] is False and fr["why"] == "quiet"
    box["f"] = feats(21.0, gate=0.06)
    assert e.evaluate("dmx", 21.0, DMX_CAPS)["active"] is True


def test_a_vibe_holds_through_silence_without_following_the_sound():
    clk = SnapClock()
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (0.8, 0.8, 0.8)}, clock=clk, features_fn=lambda: feats(0.0),
                   look_fn=lambda: look())
    e.configure({"layers": [layer()], "hits": {"select": "*", "styles": ["beat"], "min_strength": 0}},
                lamps_rig(1, output="o"))
    e.on_hit({"t_ms": 10_370.0, "band": "bass", "strength": 1.0})
    fr = e.evaluate("o", 10.375, DMX_CAPS)                       # the square would be 0; a hit would flash
    assert fr["active"] is True and inten(fr, "lamp.0") == pytest.approx(0.8)
    assert e.stats()["per_output"]["o"]["held"] == "no_features"


# -- config -----------------------------------------------------------------------------------------------------------
def test_bad_layers_are_left_out_with_problem_lines():
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1)})
    probs = e.configure({"layers": [
        layer(id="ok", wobble=1),
        layer(id="ok"),                                            # used twice
        layer(id="col", attr="colour"),                            # a colour layer needs a role (docs/LIGHTS.md §5.2)
        layer(id="fx", fx="zigzag"),
        layer(id="ph", phase="fortnight"),
        layer(id="nd", needs="vibes"),
        {**{k: v for k, v in layer(id="nodepth").items() if k != "depth"}},
        layer(id="dw", depth="loudness"),
        layer(id="t", phase="time"),                               # no period_s
        layer(id="sel", select="kind:"),
        layer(id="off", enabled="yes"),
        layer(id="quiet", enabled=False),
        "not a layer",
    ], "master": 3, "middle_depth": "half", "caps": {"strobe_hz": 4}, "colours": 1}, lamps_rig(1))
    ids = [L.id for L in e._program.layers]
    assert ids == ["ok", "sel"]
    text = "\n".join(probs)
    for needle in ("looks.layers.ok.wobble", "used twice", "a colour layer needs a role", "zigzag", "fortnight",
                   "vibes", "no depth", "loudness", "period_s", "kind:", "enabled", "layers[12]", "looks.master",
                   "looks.middle_depth", "strobe_hz", "looks.colours"):
        assert needle in text, needle
    assert e._program.master == lk.DEFAULT_MASTER and e._program.middle_depth == lk.DEFAULT_MIDDLE_DEPTH
    assert e._program.layers[1].members == {}                     # a bad selector selects nothing
    fr = e.evaluate("hue", 10.0, DMX_CAPS)                        # the rest runs
    assert fr["active"] and "lamp.0" in fr["fixtures"]


def test_no_layers_key_is_the_virtual_maestros_swell():
    e = LookEngine()
    assert e.configure({}, lamps_rig(1)) == []
    (L,) = e._program.layers
    assert (L.fx, L.phase, L.depth, L.needs, L.without_beat) == ("swell", "beat", "swell", "beat", "energy")
    assert L.attack_s == ao.HUE_ATTACK_S and L.decay_s == ao.HUE_DECAY_S
    assert e.configure({"layers": []}, lamps_rig(1)) == [] and e._program.layers == ()


def test_unknown_types_and_kinds_are_left_out():
    odd = {**HUE_T, "id": "odd", "kind": "hologram"}
    fs = [fx("lamp.0", HUE_T, "hue"), {**fx("lamp.1", HUE_T, "hue"), "type": "nope"}, fx("lamp.2", odd, "hue")]
    e = LookEngine()
    probs = e.configure({"layers": []}, rigview(fs, [HUE_T, odd], [out("hue", "hue")]))
    assert any("fixtures.lamp.1" in p and "nope" in p for p in probs)
    assert any("fixtures.lamp.2" in p and "hologram" in p for p in probs)
    assert list(e.evaluate("hue", 10.0, DMX_CAPS)["fixtures"]) == ["lamp.0"]
    assert e.evaluate("elsewhere", 10.0, DMX_CAPS)["why"] == "no_look"


def test_the_real_show_yaml_and_fixtures_yaml_compile_clean():
    raw = yaml.safe_load(contract.FIXTURES_FILE.read_text())
    types = {"sm-hue": HUE_T, "sm-screen": SCREEN_T}
    fs = []
    for fid, f in raw["fixtures"].items():
        t = types[f["type"]]
        fs.append(fx(fid, t, f.get("output"), screen=f.get("screen"), zone=f.get("zone"),
                      placement=f.get("placement"), light=f.get("light")))
    outs = [out(oid, o["driver"], enabled=o.get("enabled", False)) for oid, o in raw["outputs"].items()]
    groups = {g: list(v.get("order") or []) for g, v in (raw.get("groups") or {}).items()}
    rig = rigview(fs, list(types.values()), outs, groups)
    e = LookEngine(clock=SnapClock(), features_fn=lambda: feats(10.0))
    assert e.configure(show_looks(), rig) == []
    lamp_ids = {fid for fid, f in raw["fixtures"].items() if types[f["type"]]["kind"] == "lamp"}
    for oid in raw["outputs"]:
        got = set(e.evaluate(oid, 10.0, HUE_CAPS)["fixtures"])
        assert got == {fid for fid in lamp_ids if raw["fixtures"][fid].get("output") == oid}
    st = e.stats()
    assert st["program"]["layers"] and st["program"]["problems"] == []
    assert set(st["per_output"]) == set(raw["outputs"])


def test_stats_shape():
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1)})
    e.configure({"layers": []}, lamps_rig(1))
    for i in range(10):
        e.evaluate("hue", 10 + i * 0.02, DMX_CAPS)
    s = e.stats()
    po = s["per_output"]["hue"]
    assert set(s) == {"program", "per_output", "errors", "identify", "follow", "drops", "motion", "holds",
                      "gestures"}                                # docs/ENSEMBLE.md §7.2
    assert set(s["program"]) == {"revision", "layers", "problems", "scenes", "groups", "effects", "skipped",
                                 "modules"}
    assert set(s["program"]["scenes"]) == {"revision", "compiled", "problems"}
    assert po["frames"] == 10 and po["eval_us"]["p50"] is not None and po["eval_us"]["p95"] >= po["eval_us"]["p50"]


def test_a_failing_input_degrades():
    def boom(*a):
        raise RuntimeError("x")

    e = LookEngine(palette_fn=boom, look_fn=boom, clock=boom, features_fn=boom)
    e.configure({}, lamps_rig(1))
    fr = e.evaluate("hue", 10.0, HUE_CAPS)                       # features fail -> no_features (no vibe): idle
    assert fr["active"] is False and e.errors["palette"] >= 1 and e.errors["features"] >= 1
    e2 = LookEngine(palette_fn=boom, look_fn=boom, clock=boom)
    e2.configure({}, lamps_rig(1))
    fr = e2.evaluate("hue", 10.0, HUE_CAPS)
    assert fr["active"] is True and fr["fixtures"]["lamp.0"]["colour"] == pytest.approx(
        [c / max(ao.NEUTRAL_RGB) for c in ao.NEUTRAL_RGB])


# -- budgets (README S5.3) --------------------------------------------------------------------------------------------
def _p95(xs):
    xs = sorted(xs)
    return xs[int(0.95 * (len(xs) - 1))]


def test_budgets(capsys):
    clk = SnapClock(anchor=10.0, period=0.469)
    f = feats(10.0)
    lk_ = look()
    pal4 = {f"lamp.{i}": (1.0, 0.6, 0.3) for i in range(4)}
    e = LookEngine(palette_fn=lambda t: pal4, clock=clk, features_fn=lambda: f, look_fn=lambda: lk_)
    e.configure(show_looks(), lamps_rig(4, rise=(120.0, 120.0, 140.0, 140.0)))
    hue = []
    for i in range(2000):
        f["t_ms"] = (10.0 + i * 0.02) * 1000.0
        c0 = time.perf_counter()
        e.evaluate("hue", 10.0 + i * 0.02, HUE_CAPS)
        hue.append((time.perf_counter() - c0) * 1e6)
    par = par_type()
    fs = [fx(f"spot.{i}", par, "dmx", address=1 + 6 * i, latency=lat(30.0)) for i in range(32)]
    pal32 = {f"spot.{i}": (1.0, 0.1 * (i % 10), 0.3) for i in range(32)}
    e2 = LookEngine(palette_fn=lambda t: pal32, clock=clk, features_fn=lambda: f, look_fn=lambda: lk_)
    assert e2.configure(show_looks(), rigview(fs, [par], [out("dmx", "artnet")])) == []
    dmx = []
    for i in range(1000):
        t = 10.0 + i * 0.023
        f["t_ms"] = t * 1000.0
        if i % 10 == 0:
            e2.on_hit({"t_ms": t * 1000.0, "band": "bass", "strength": 0.9})
        c0 = time.perf_counter()
        e2.evaluate("dmx", t, DMX_CAPS)
        dmx.append((time.perf_counter() - c0) * 1e6)
    with capsys.disabled():
        print(f"\n  looks.evaluate, 4 Hue lamps: p50 {statistics.median(hue):.0f} us, p95 {_p95(hue):.0f} us "
              f"(budget 150)\n  looks.evaluate, 32 DMX RGBW pars: p50 {statistics.median(dmx):.0f} us, "
              f"p95 {_p95(dmx):.0f} us (budget 600)")
    assert _p95(hue) < 1500 and _p95(dmx) < 6000                 # 10x the budgets: only a pathology fails here


# -- README S5.14: the review fixes ------------------------------------------------------------------------------------
def _real_caps():
    from show_service import artnet_lan, hue_out
    return dict(hue_out.HueOut.caps), dict(artnet_lan.CAPS)


def drop_look(t0: float, t1: float, predicted: bool, colour=(0.0, 0.2, 1.0), **kw) -> dict:
    return look(hit={"t0_ms": t0 * 1000.0, "t1_ms": t1 * 1000.0, "predicted": predicted, "swell_ms": 300.0,
                     "lamps": {"lamp.0": list(colour)}}, **kw)


@pytest.mark.parametrize("predicted", [False, True])
def test_the_drop_hit_follows_each_outputs_caps(predicted):
    """An unforeseen drop: DMX (caps take hits) at once; Hue swells, its level taking >= 300 ms to rise and its colour
    never stepping. A predicted drop line: on time on both (Hue's own slew still applies, 53 ms full scale)."""
    hue_caps, dmx_caps = _real_caps()
    base = (0.5, 0.25, 0.0)                                     # a dim amber; the hit is a full blue
    t0, t1 = 20.0, 20.47
    box = {"look": drop_look(t0, t1, predicted)}
    res = {}
    for oid, caps in (("hue", hue_caps), ("dmx", dmx_caps)):
        e = LookEngine(palette_fn=lambda t: {"lamp.0": base}, look_fn=lambda: box["look"],
                       features_fn=lambda: feats(20.0))
        e.configure({"layers": []}, lamps_rig(1, output=oid))
        rows = []
        for i in range(-5, 60):
            t = t0 + i * 0.02
            ff = e.evaluate(oid, t, caps)["fixtures"]["lamp.0"]
            rows.append((t, ff["intensity"], ff["colour"]))
        res[oid] = rows
    # DMX: the hit at once and only for [t0, t1)
    dm = {round(t, 3): (i, c) for t, i, c in res["dmx"]}
    assert dm[round(t0, 3)][0] == pytest.approx(1.0) and dm[round(t0, 3)][1] == pytest.approx([0.0, 0.2, 1.0])
    assert dm[round(t0 - 0.02, 3)][0] == pytest.approx(0.5) and dm[round(t1 + 0.01, 3)][0] == pytest.approx(0.5)
    hue = res["hue"]
    lv = [i for _, i, _ in hue]
    t_full = next(t for t, i, _ in hue if i >= 0.99)
    if predicted:                                                # on time: full within the slew's 53 ms
        assert t_full - t0 <= 0.080 / lk.SLEW_PEAK + 0.021
    else:
        assert t_full - t0 >= 0.300 - 1e-9                       # a swell: >= 300 ms to rise (§5.4)
        from show_service.hue_out import rgb_to_xy
        xy = [rgb_to_xy(*c) for _, _, c in hue]                  # the chromaticity Hue gets, frame by frame
        steps = [math.dist(a, b) for a, b in zip(xy, xy[1:])]
        total = math.dist(xy[0], rgb_to_xy(0.0, 0.2, 1.0))
        assert total > 0.3 and max(steps) < total / 4            # the colour glides with the level, never steps
        assert max(b - a for a, b in zip(lv, lv[1:])) < 0.1      # no frame jumps (20 ms frames)
        after = [i for t, i, _ in hue if t >= t1 + 0.3 + 1e-9]
        assert after and all(i == pytest.approx(0.5) for i in after)   # and back down over the swell after t1


def test_a_hit_with_no_vibe_or_garbage_is_ignored():
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (0.5, 0.5, 0.5)},
                   look_fn=lambda: look(hit={"t0_ms": "x", "t1_ms": 1.0, "lamps": {}}))
    e.configure({"layers": []}, lamps_rig(1))
    assert inten(e.evaluate("hue", 10.0, DMX_CAPS), "lamp.0") == pytest.approx(0.5)


def test_no_vibe_is_the_house_look_where_nothing_room_side_gates_the_output():
    """artnet's caps say idle_without_vibe: with no vibe, loud fresh features still give the house look (§8.6), never
    the neutral amber swelling to TV audio. Hue's don't: room's hue adapter decides which lamps follow."""
    hue_caps, dmx_caps = _real_caps()
    par = par_type()
    fs = [fx("spot.a", par, "dmx", address=1, house={"intensity": 0.0, "hex": "#ffffff"}),
          fx("lamp.h", HUE_T, "hue")]
    rig = rigview(fs, [par, HUE_T], [out("dmx", "artnet"), out("hue", "hue")])
    box = {"pal": None}
    e = LookEngine(palette_fn=lambda t: box["pal"], clock=SnapClock(), features_fn=lambda: feats(10.0, energy=0.9))
    assert e.configure(show_looks(), rig) == []
    fr = e.evaluate("dmx", 10.0, dmx_caps)
    assert fr["active"] is False and fr["why"] == "no_vibe"
    assert fr["fixtures"]["spot.a"]["intensity"] == 0.0 and fr["fixtures"]["spot.a"]["dmx"][0] == 0
    hf = e.evaluate("hue", 10.0, hue_caps)
    assert hf["active"] is True and hf["fixtures"]["lamp.h"]["intensity"] > 0.0
    box["pal"] = {"spot.a": (1.0, 0.0, 0.0), "lamp.h": (1.0, 0.0, 0.0)}   # a vibe: DMX follows the director
    fr = e.evaluate("dmx", 10.1, dmx_caps)
    assert fr["active"] is True and fr["fixtures"]["spot.a"]["colour"] == [1.0, 0.0, 0.0]


@pytest.mark.parametrize("gate", ["beat", "dmx", "none"])
def test_beat_depth_keeps_dmx_beat_locked_at_middle_confidence(gate):
    """§5.4: Middle (gate dmx): beat-locked on DMX only, at middle_depth. The director's hue_depth is 0 off gate beat
    (the virtual Maestro's Hue stays steady), so the swell layer reads beat_depth while it runs beat-locked."""
    hue_caps, dmx_caps = _real_caps()
    res = {}
    for oid, caps in (("hue", hue_caps), ("dmx", dmx_caps)):
        clk = SnapClock(gate=gate)
        e = LookEngine(palette_fn=lambda t: {"lamp.0": (1.0, 1.0, 1.0)}, clock=clk,
                       features_fn=lambda: feats(10.0, energy=0.4), look_fn=lambda: look())
        e.configure({"middle_depth": 0.5, "layers": [layer(depth="swell", without_beat="energy")]},
                    lamps_rig(1, output=oid))
        e.set_look(hue_depth=0.6 if gate == "beat" else 0.0, beat_lock=True, beat_depth=0.6)
        res[oid] = inten(e.evaluate(oid, 10.375, caps), "lamp.0")   # p 0.75: the square is 0
    want = {"beat": (0.4, 0.4), "dmx": (1.0, 1.0 - 0.6 * 0.5), "none": (1.0, 1.0)}[gate]
    assert (res["hue"], res["dmx"]) == pytest.approx(want)
    e = LookEngine()
    e.set_look(beat_depth=0.3)
    e.set_look(reset=True)
    assert e.beat_depth is None


@pytest.mark.parametrize("steady", ["house", "low", "dip"])
def test_house_low_and_the_dip_are_steady(steady):
    """README S5.14: House lights and Lights low run no layer at all (like a held frame: the motion layer would wave
    the house light), and no music.hit flashes over them or over the pre-drop dip."""
    clk = SnapClock()
    lk_ = look(**{steady: True}, motion={"speed": 0.0, "amount": 1.0}, accents={"style": "beat", "subdivision": 1})
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (0.4, 0.2, 0.0)}, clock=clk, look_fn=lambda: lk_)
    rig = rigview([fx("lamp.0", HUE_T, "o", latency=lat(min_pulse=40.0))], [HUE_T], [out("o", "artnet")])
    assert e.configure({"layers": [layer(id="m", depth="motion.amount", needs="none")],
                        "hits": {"select": "kind:lamp", "styles": ["beat"], "min_strength": 0.0, "depth": 0.5,
                                 "decay_ms": 120}}, rig) == []
    e.on_hit({"t_ms": 20_370.0, "band": "bass", "strength": 1.0})
    v = inten(e.evaluate("o", 20.375, DMX_CAPS), "lamp.0")        # the square would be 0; the hit would flash
    if steady == "dip":                                           # the dip keeps its layers, only hits stop
        assert v == pytest.approx(0.0)
    else:
        assert v == pytest.approx(0.4)
    assert not e.takes_hit({"t_ms": 20_370.0, "band": "bass", "strength": 1.0})


def test_a_hue_output_never_runs_an_oscillator_faster_than_its_min_period():
    """caps.min_period_ms (hue: 2 x min_attack): a square on time at 0.1 s would flicker Hue at 10 Hz; there it runs
    at 0.2 s (coarser, on its own grid); DMX keeps 0.1 s. Listed in stats."""
    hue_caps, dmx_caps = _real_caps()
    assert hue_caps["min_period_ms"] == 2 * hue_caps["min_attack_ms"] and dmx_caps["min_period_ms"] == 0.0
    for oid, caps, period in (("hue", hue_caps, 0.2), ("dmx", dmx_caps, 0.1)):
        e = LookEngine(palette_fn=lambda t: {"lamp.0": (1, 1, 1)})
        e.configure({"layers": [layer(phase="time", period_s=0.1, needs="none")]}, lamps_rig(1, output=oid))
        lv = [inten(e.evaluate(oid, 10.0 + i * 0.005, caps), "lamp.0") for i in range(400)]
        downs = [i for i in range(1, len(lv)) if lv[i] < lv[i - 1] - 0.5]
        gaps = [(b - a) * 0.005 for a, b in zip(downs, downs[1:])]
        assert gaps and all(abs(g - period) <= 0.0051 for g in gaps), (oid, gaps)
        assert e.stats()["per_output"][oid]["coarsened"] == (["l1"] if oid == "hue" else [])


def test_takes_hit_per_output_follows_the_hits_selection():
    """README S5.14: a runner is woken only when its own output has a fixture in looks.hits.select."""
    clk = SnapClock()
    e = LookEngine(palette_fn=lambda t: {}, clock=clk, look_fn=lambda: look())
    par = par_type()
    fs = [fx("spot.a", par, "dmx-a", address=1, tags=["ball"]), fx("spot.b", par, "dmx-b", address=1)]
    rig = rigview(fs, [par], [out("dmx-a", "artnet"), out("dmx-b", "artnet")])
    assert e.configure({"layers": [], "hits": {"select": "ball", "styles": ["beat"], "min_strength": 0.0}}, rig) == []
    h = {"t_ms": 1.0, "band": "bass", "strength": 1.0}
    assert e.takes_hit(h) and e.takes_hit(h, "dmx-a")
    assert not e.takes_hit(h, "dmx-b") and not e.takes_hit(h, "nowhere")


# -- light scenes (README S5.24) ------------------------------------------------------------------------------------------
class FakeScenes:
    """A light_scenes.Catalogue's shape: ``scenes`` {name: {label, layers, hits, caps, tags}}, ``revision``, ``poll``
    and ``subscribe`` (the engine recompiles when it is told)."""

    def __init__(self, scenes: dict) -> None:
        self.scenes, self.revision, self.subs = scenes, "r1", []

    def poll(self) -> bool:
        return False

    def subscribe(self, fn) -> None:
        self.subs.append(fn)

    def swap(self, scenes: dict) -> None:
        self.scenes, self.revision = scenes, self.revision + "+"
        for fn in self.subs:
            fn(self)


def scene(layers=(), hits=None, caps=None) -> dict:
    return {"label": "x", "layers": list(layers), "hits": hits, "caps": caps, "tags": {"weight": 1.0}}


def _scene_engine(scenes: dict, looks: dict, box: dict, rig=None, **kw):
    e = LookEngine(palette_fn=lambda t: box.get("pal", {"lamp.0": (1, 1, 1), "lamp.1": (1, 1, 1)}),
                   look_fn=lambda: box["look"], clock=box.setdefault("clk", SnapClock(anchor=10.0, period=0.5)),
                   light_scenes=FakeScenes(scenes), **kw)
    assert e.configure(looks, rig or lamps_rig(2, output="o")) == []
    return e


def test_a_light_scene_replaces_the_layers_and_a_name_that_isnt_compiled_runs_them():
    box = {"look": look()}
    e = _scene_engine({"still": scene(), "deep": scene([layer(id="sq")])}, {"layers": [layer(id="base")]}, box)
    assert [L.id for L in e._program.scenes["deep"].layers] == ["deep/sq"]
    on, off = 10.1, 10.3                                             # the square's high and low halves of a beat
    for name, want_off in ((None, 0.0), ("still", 1.0), ("deep", 0.0), ("nowhere", 0.0)):
        box["look"] = look(light_scene=name)
        assert inten(e.evaluate("o", on, DMX_CAPS), "lamp.0") == pytest.approx(1.0)
        assert inten(e.evaluate("o", off, DMX_CAPS), "lamp.0") == pytest.approx(want_off), name
        assert e.stats()["per_output"]["o"]["light_scene"] == (name if name in ("still", "deep") else None)


def test_a_scene_change_crossfades_the_layer_depths_over_the_looks_transition():
    box = {"look": look(light_scene="deep", transition={"style": "crossfade", "beats": None, "seconds": 2.0})}
    e = _scene_engine({"still": scene(), "deep": scene([layer(id="sq")])}, {"layers": []}, box)
    assert inten(e.evaluate("o", 10.3, DMX_CAPS), "lamp.0") == pytest.approx(0.0)
    box["look"] = {**box["look"], "light_scene": "still"}
    assert inten(e.evaluate("o", 10.3 + 0.5, DMX_CAPS), "lamp.0") == pytest.approx(0.0)   # x = 0 at the change
    assert inten(e.evaluate("o", 10.3 + 1.5, DMX_CAPS), "lamp.0") == pytest.approx(0.5)   # half way: depth 0.5
    assert inten(e.evaluate("o", 10.3 + 2.5, DMX_CAPS), "lamp.0") == pytest.approx(1.0)   # the new scene: steady
    box["look"] = {**box["look"], "light_scene": None, "transition": {"style": "cut"}}       # no scene: looks.layers
    assert inten(e.evaluate("o", 10.3 + 3.0, DMX_CAPS), "lamp.0") == pytest.approx(1.0)


def test_a_scene_brings_its_own_hits_and_flash_cap():
    hits = {"select": "kind:lamp", "styles": ["beat"], "min_strength": 0.0, "depth": 0.5, "decay_ms": 100}
    box = {"look": look(), "pal": {"lamp.0": (0.2, 0.2, 0.2), "lamp.1": (0.2, 0.2, 0.2)}}
    e = _scene_engine({"quiet": scene(), "flashy": scene(hits={**hits, "depth": 1.0}, caps={"flash_hz_max": 1.0})},
                      {"layers": [], "hits": hits}, box)
    h = {"t_ms": 10_000.0, "band": "bass", "strength": 1.0}
    e.on_hit(h)
    for name, want, takes in ((None, 0.7, True), ("quiet", 0.2, False), ("flashy", 1.0, True)):
        box["look"] = look(light_scene=name)
        assert inten(e.evaluate("o", 10.01, DMX_CAPS), "lamp.0") == pytest.approx(want), name
        assert e.takes_hit(h) is takes and e.takes_hit(h, "o") is takes
    # the scene's cap: a second big flash within 1 s rises only half way over its low (README S5.5 step 7)
    box2 = {"look": look(light_scene="flashy"), "pal": box["pal"]}
    e2 = _scene_engine({"flashy": scene(hits={**hits, "depth": 1.0}, caps={"flash_hz_max": 1.0})}, {"layers": []},
                       box2)
    assert inten(e2.evaluate("o", 20.0, DMX_CAPS), "lamp.0") == pytest.approx(0.2)
    e2.on_hit({"t_ms": 20_100.0, "band": "bass", "strength": 1.0})
    assert inten(e2.evaluate("o", 20.11, DMX_CAPS), "lamp.0") == pytest.approx(1.0)
    low = inten(e2.evaluate("o", 20.5, DMX_CAPS), "lamp.0")          # back down near the level
    assert low == pytest.approx(0.2 + math.exp(-3.6))
    e2.on_hit({"t_ms": 20_600.0, "band": "bass", "strength": 1.0})
    assert inten(e2.evaluate("o", 20.61, DMX_CAPS), "lamp.0") == pytest.approx(low + lk.BIG_RISE)
    assert e.configure({"layers": []}, lamps_rig(1)) == []           # a scene's problems are never configure's
    bad = _scene_engine({"b": scene(caps={"flash_hz_max": -1, "zap": 1})}, {"layers": []}, {"look": look()})
    assert any("caps.flash_hz_max" in p for p in bad._program.scene_problems)
    assert any("caps.zap" in p for p in bad._program.scene_problems)


def _drop_rig():
    fs = [fx("spot.a", HUE_T, "dmx"), fx("spot.late", HUE_T, "dmx", latency=lat(100.0)), fx("hue.a", HUE_T, "hue")]
    return rigview(fs, [HUE_T], [out("dmx", "artnet"), out("hue", "hue")])


def test_drop_hits_flash_at_the_line_on_outputs_that_take_hits():
    drop = {"select": "kind:lamp", "styles": [], "drop": {"depth": 1.0, "decay_ms": 200}}
    box = {"look": look(light_scene="boom"), "pal": {k: (0.2, 0.2, 0.2) for k in ("spot.a", "spot.late", "hue.a")}}
    e = _scene_engine({"boom": scene(hits=drop), "calm": scene()}, {"layers": []}, box, rig=_drop_rig())
    assert e.trigger_hit(11.0, key="k") and not e.trigger_hit(11.0, key="k")   # one line, once
    fr = e.evaluate("dmx", 10.95, DMX_CAPS)
    assert inten(fr, "spot.a") == pytest.approx(0.2)                 # not yet...
    assert inten(fr, "spot.late") == pytest.approx(1.0)              # ...but a 100 ms fixture lands on the line
    assert inten(e.evaluate("dmx", 11.01, DMX_CAPS), "spot.a") == pytest.approx(1.0)
    assert inten(e.evaluate("dmx", 11.24, DMX_CAPS), "spot.a") == pytest.approx(0.2 + math.exp(-1.0))
    assert inten(e.evaluate("hue", 11.01, HUE_CAPS), "hue.a") == pytest.approx(0.2)   # Hue never takes hits
    assert not e.takes_hit({"t_ms": 1.0, "strength": 1.0})           # styles []: no music.hit flashes
    for lk_, want in ((look(light_scene="boom", dip=True), 1.0),     # over the pre-drop dip
                      (look(light_scene="boom", house=True), 0.2),   # never under House or Low
                      (look(light_scene="boom", low=True), 0.2),
                      (look(light_scene="calm"), 0.2)):              # a scene without drop hits
        box["look"] = lk_
        assert inten(e.evaluate("dmx", 11.02, DMX_CAPS), "spot.a") == pytest.approx(want), lk_
    assert e.stats()["drops"] == 1


def test_lookdata_light_scene_drop_is_taken_once_per_id():
    drop = {"select": "kind:lamp", "styles": [], "drop": {"depth": 1.0, "decay_ms": 200}}
    box = {"look": look(light_scene="boom", light_scene_drop={"id": "drop:1", "at_ms": 12_000.0, "strength": 0.5}),
           "pal": {k: (0.2, 0.2, 0.2) for k in ("spot.a", "spot.late", "hue.a")}}
    e = _scene_engine({"boom": scene(hits=drop)}, {"layers": [], "hits": {**drop, "drop": None}}, box,
                      rig=_drop_rig())
    for t in (11.9, 12.01, 12.02):
        fr = e.evaluate("dmx", t, DMX_CAPS)
    assert e.drops == 1 and inten(fr, "spot.a") == pytest.approx(0.2 + 0.5)   # strength 0.5 x depth 1
    e.evaluate("hue", 12.03, HUE_CAPS)
    assert e.drops == 1
    box["look"] = {**box["look"], "light_scene_drop": {"id": "drop:2", "at_ms": 13_000.0, "strength": 1.0}}
    e.evaluate("dmx", 12.5, DMX_CAPS)
    assert e.drops == 2 and inten(e.evaluate("dmx", 13.01, DMX_CAPS), "spot.a") == pytest.approx(1.0)
    box["look"] = {**box["look"], "light_scene": None}               # looks.hits with no drop key: no drop flash
    assert inten(e.evaluate("dmx", 13.02, DMX_CAPS), "spot.a") == pytest.approx(0.2)
    assert not e.trigger_hit(float("nan")) and not e.trigger_hit("x")


def test_identify_is_full_white_whatever_the_frame_until_it_expires():
    clock = {"now": 10.0}
    box = {"look": look(), "f": feats(10.0)}
    e = LookEngine(palette_fn=lambda t: box.get("pal"), look_fn=lambda: box["look"], features_fn=lambda: box["f"],
                   clock=SnapClock(anchor=10.0, period=0.5), mono=lambda: clock["now"], light_scenes=False)
    par = par_type()
    rig = rigview([fx("lamp.0", HUE_T, "o"), fx("lamp.1", HUE_T, "o"), fx("par.0", par, "o", address=1)],
                  [HUE_T, par], [out("o", "artnet")])
    assert e.configure({"layers": [layer()]}, rig) == []
    box["pal"] = {"lamp.0": (0.5, 0, 0), "lamp.1": (0.5, 0, 0), "par.0": (0.5, 0, 0)}
    assert e.identify("lamp.0", 2.0) is True and e.identify("par.0", 2.0) is True
    assert e.identify("ghost", 2.0) is False                         # kept, but nothing evaluates it now
    fr = e.evaluate("o", 10.3, DMX_CAPS)                             # the square's low half: the look is dark
    assert fr["fixtures"]["lamp.0"]["rgb"] == pytest.approx([1.0, 1.0, 1.0])
    assert inten(fr, "lamp.1") == pytest.approx(0.0)
    assert max(fr["fixtures"]["par.0"]["dmx"]) == 255
    box["pal"], box["f"] = None, feats(0.0)                          # no vibe, stale features: an inactive frame
    fr = e.evaluate("o", 10.4, DMX_CAPS)
    assert fr["active"] is False and fr["fixtures"]["lamp.0"]["rgb"] == pytest.approx([1.0, 1.0, 1.0])
    assert e.stats()["identify"] == {"lamp.0": 2.0, "par.0": 2.0, "ghost": 2.0}
    box["pal"], box["f"] = {"lamp.0": (0.5, 0, 0)}, feats(12.1)
    assert inten(e.evaluate("o", 12.1, DMX_CAPS), "lamp.0") == pytest.approx(0.5)   # expired: the look again
    e.identify("lamp.1", 5.0, rgb=(255, 0, 0))
    assert e.evaluate("o", 12.3, DMX_CAPS)["fixtures"]["lamp.1"]["rgb"] == pytest.approx([1.0, 0.0, 0.0])
    e.identify("lamp.1", 0)                                          # ends it
    assert inten(e.evaluate("o", 12.3, DMX_CAPS), "lamp.1") == pytest.approx(0.0)
    assert e.identify("lamp.0", "soon") is False


def test_follow_values_show_the_house_state_instead_of_the_look():
    box = {"look": look(), "f": feats(10.0)}
    e = LookEngine(palette_fn=lambda t: box.get("pal"), look_fn=lambda: box["look"], features_fn=lambda: box["f"],
                   clock=SnapClock(anchor=10.0, period=0.5), light_scenes=False)
    assert e.configure({"layers": [layer(fx="sine", depth=0.5)]}, lamps_rig(4, output="o")) == []
    box["pal"] = {f"lamp.{i}": (0.0, 0.0, 1.0) for i in range(4)}
    got = e.set_follow({"lamp.0": "off", "lamp.1": {"hex": "#FF8000", "bri": 50}, "lamp.2": "dmx",
                        "lamp.3": {"hex": "zz"}, "lamp.9": {"hex": "#00ff00"}})
    assert got == {"lamp.0": "off", "lamp.1": {"hex": "#ff8000", "bri": 50.0}, "lamp.2": "dmx",
                   "lamp.9": {"hex": "#00ff00", "bri": 100.0}}
    fr = e.evaluate("o", 10.0, DMX_CAPS)
    assert inten(fr, "lamp.0") == 0.0 and fr["fixtures"]["lamp.0"]["rgb"] == [0.0, 0.0, 0.0]
    assert inten(fr, "lamp.1") == pytest.approx(0.5)
    assert fr["fixtures"]["lamp.1"]["colour"] == pytest.approx([1.0, 128 / 255, 0.0])
    assert fr["fixtures"]["lamp.2"]["colour"] == pytest.approx([0.0, 0.0, 1.0])   # follows: the look's colour
    assert fr["fixtures"]["lamp.3"]["colour"] == pytest.approx([0.0, 0.0, 1.0])   # never named: follows
    box["pal"], box["f"] = None, feats(0.0)                          # inactive: the follow value, not the house look
    fr = e.evaluate("o", 10.2, DMX_CAPS)
    assert fr["active"] is False and inten(fr, "lamp.1") == pytest.approx(0.5)
    assert e.set_follow({"lamp.0": "dmx"}, merge=True) == {"lamp.0": "dmx"}   # merge: lamp.1 keeps its colour
    assert e.follow() == {"lamp.1": {"hex": "#ff8000", "bri": 50.0}, "lamp.9": {"hex": "#00ff00", "bri": 100.0}}
    e.set_follow({"lamp.2": "off"})                                  # the whole mapping (outputs.Rig's): the rest follow
    assert e.follow() == {"lamp.2": "off"} and e.stats()["follow"] == {"lamp.2": "off"}
    assert e.set_follow("nope") == {}
    e.identify("lamp.2", 5.0)                                        # identify wins over off
    assert inten(e.evaluate("o", 10.3, DMX_CAPS), "lamp.2") == 1.0


def test_a_follow_colour_still_rises_through_the_hue_rise_limit():
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (0.0, 0.0, 0.0)}, light_scenes=False)
    assert e.configure({"layers": []}, lamps_rig(1)) == []
    e.set_follow({"lamp.0": "off"})
    e.evaluate("hue", 10.0, HUE_CAPS)
    e.set_follow({"lamp.0": {"hex": "#ffffff", "bri": 100}})
    lim = lk.SLEW_PEAK * 0.02 / 0.08
    assert inten(e.evaluate("hue", 10.02, HUE_CAPS), "lamp.0") == pytest.approx(lim)
    e.identify("lamp.0", 1.0)                                        # identify is never limited
    assert inten(e.evaluate("hue", 10.04, HUE_CAPS), "lamp.0") == 1.0
