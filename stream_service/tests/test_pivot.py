"""The adaptive pivot (modulation.Pivots; owner, 17 Oct): each listed level centred on its own running median while
music plays, so a binding gives its base at the music's usual level, not at a fixed 0.5 that drive / tempo / motion
never reach; silence never drags the norm down, and the re-centring eases in and out (no GPU)."""
from __future__ import annotations

import dataclasses
import math

from stream_service import binding
from stream_service.modulation import PIVOT_SAMPLE_S, Pivots, SessionRuntime
from stream_service.music_feed import Response, parse_response

RATE = {"type": "number", "minimum": 0.0, "maximum": 4.0, "default": 1.0}


def cfg(**kw) -> Response:
    return dataclasses.replace(Response(), **kw)


def pivots(window=10.0, min_s=5.0, signals=("drive", "energy"), ease_s=0.0) -> Pivots:
    pv = Pivots()
    pv.configure(cfg(pivot_window_s=window, pivot_min_s=min_s, pivot_signals=frozenset(signals), pivot_ease_s=ease_s))
    pv.w.update(music=1.0, feed=1.0, act=1.0, desk=1.0)
    return pv


def run(pv, values, t0=0.0, avail=None):
    """One frame a second (each a sample), with ``values`` -> the last frame's re-centred signals."""
    sig = {}
    for i, v in enumerate(values):
        sig = {"drive": v, "energy": 0.5, "beat": 0.9, "beat_phase": 0.3}
        pv.step(sig, avail or {}, t0 + i * PIVOT_SAMPLE_S)
    return sig


def test_running_median_over_a_bounded_window():
    pv = pivots(window=5.0, min_s=3.0)
    run(pv, [0.1, 0.9, 0.2, 0.3, 0.25])
    assert pv.view["drive"] == {"median": 0.25, "applied": True, "samples": 5}
    ring, srt = pv.rings["drive"]
    assert list(ring) == [0.1, 0.9, 0.2, 0.3, 0.25] and srt == sorted(ring)
    run(pv, [0.4, 0.4, 0.4], t0=10.0)                     # the oldest three leave (and leave the sorted copy)
    ring, srt = pv.rings["drive"]
    assert list(ring) == [0.3, 0.25, 0.4, 0.4, 0.4] and srt == sorted(ring) and pv.view["drive"]["median"] == 0.4
    pv2 = pivots(window=10.0, min_s=1.0)
    run(pv2, [0.2, 0.3, 0.6, 0.7])                        # even: the middle two's mean
    assert abs(pv2.view["drive"]["median"] - 0.45) < 1e-9
    # once a second, however many frames
    pv3 = pivots()
    for k in range(30):
        pv3.step({"drive": 0.3}, {}, 100.0 + k / 30)
    assert pv3.view["drive"]["samples"] == 1


def test_needs_min_s_of_samples_then_recentres():
    pv = pivots(window=600.0, min_s=5.0)
    sig = run(pv, [0.25] * 4)
    assert pv.view["drive"]["applied"] is False and sig["drive"] == 0.25        # before min_s: the fixed 0.5
    sig = run(pv, [0.25], t0=10.0)
    assert pv.view["drive"]["applied"] is True and abs(sig["drive"] - 0.5) < 1e-9   # at its norm: 0.5
    s = {"drive": 0.45, "energy": 0.5}
    pv.apply(s)
    assert abs(s["drive"] - 0.7) < 1e-9 and s["energy"] == 0.5              # x' = clamp01(x - (median - 0.5))
    s = {"drive": 0.9}
    pv.apply(s)
    assert s["drive"] == 1.0                                                 # clamped
    pv.w["music"] = 0.5                                                      # half live: x = 0.5 + w (v - 0.5)
    s = {"drive": 0.5 + 0.5 * (0.45 - 0.5)}
    pv.apply(s)
    assert abs(s["drive"] - (0.5 + 0.5 * (0.45 - 0.25))) < 1e-9             # = 0.5 + w (v - median)
    pv.w["music"] = 0.0                                                      # the source down: idle stays idle
    s = {"drive": 0.5, "energy": None}
    pv.apply(s)
    assert s == {"drive": 0.5, "energy": None}


def test_held_while_na_or_not_fully_live():
    pv = pivots(window=600.0)
    for k in range(20):
        pv.step({"drive": None, "energy": 0.4}, {}, float(k))              # n/a: held
    assert pv.view["drive"]["samples"] == 0 and pv.view["energy"]["samples"] == 20
    pv.w["music"] = 0.5                                                      # fading in or out: held
    for k in range(20, 30):
        pv.step({"drive": 0.3, "energy": 0.4}, {}, float(k))
    assert pv.view["drive"]["samples"] == 0 and pv.view["energy"]["samples"] == 20
    pv.w["music"] = 1.0
    for k in range(30, 40):                                                  # avail (n/a-able) fading in: held
        pv.step({"drive": 0.3, "energy": 0.4}, {"drive": 0.5}, float(k))
    assert pv.view["drive"]["samples"] == 0 and pv.view["energy"]["samples"] == 30


def test_only_listed_levels_never_triggers_phases_or_positions():
    pv = pivots(window=10.0, min_s=1.0, signals=("drive", "beat", "beat_phase", "pan_slow", "downbeat"))
    assert set(pv.rings) == {"drive"}                                        # configure filters too
    sig = run(pv, [0.2, 0.2, 0.2])
    assert sig["beat"] == 0.9 and sig["beat_phase"] == 0.3 and sig["energy"] == 0.5   # untouched
    c, errs = parse_response({"pivot": {"signals": ["drive", "beat", "pan", "note_on", "bar_phase", "motion"],
                                        "window_s": 20, "min_s": 30, "speed": 2}})
    assert c.pivot_signals == frozenset({"drive", "motion"}) and c.pivot_window_s == 20.0
    assert any("['bar_phase', 'beat', 'note_on', 'pan']" in e for e in errs)
    assert any("pivot.speed" in e for e in errs) and any("min_s is over window_s" in e for e in errs)
    c, errs = parse_response({"pivot": {"on": "yes", "window_s": 1}})
    assert c.pivot_on is True and c.pivot_window_s == 600.0 and len(errs) == 2     # bad values: the defaults
    assert "motion" in Response().pivot_signals and "beat" not in Response().pivot_signals


def test_hot_reload_keeps_samples_and_off_clears():
    pv = pivots(window=10.0, min_s=2.0)
    run(pv, [0.2, 0.3, 0.4])
    pv.configure(cfg(pivot_window_s=2.0, pivot_min_s=2.0, pivot_signals=frozenset({"drive", "punch"}),
                     pivot_ease_s=0.0))
    assert list(pv.rings["drive"][0]) == [0.3, 0.4] and pv.rings["drive"][1] == [0.3, 0.4]
    assert pv.view["punch"] == {"median": None, "applied": False, "samples": 0}
    pv.configure(cfg(pivot_on=False, pivot_ease_s=0.0))
    assert pv.rings == {} and pv.tgt == {} and pv.view == {} and pv.active      # the offset still to ease out
    s = {"drive": 0.3}
    pv.step(s, {}, 50.0)
    assert s == {"drive": 0.3} and pv.off == {} and not pv.active


def test_a_rate_mapping_holds_its_base_at_the_musics_norm_end_to_end():
    """drive sits at 0.3 all song (it never reaches 0.5): a scale mapping ran the param below its base; once the
    pivot has min_s of samples it gives the base, and with the music gone the signal is idle and so is the param."""
    rt = SessionRuntime(None)
    rt.response = cfg(pivot_window_s=600.0, pivot_min_s=60.0)
    music = {"energy": 0.4, "energy_slow": 0.4, "drive": 0.3, "punch": 0.3, "tempo": 0.2, "music_w": 1.0}
    rt.extra = dict(music)
    bd = binding.Bound("rate", RATE, binding.parse_v2("rate", RATE, {"in": [{"sig": "drive"}],
                                                                     "map": {"scale": [0.7, 1.4]}}), "scene/x")
    t, seen, step = 0.0, {}, 0.0
    for k in range(110 * 10):                             # 110 s at 10 fps
        t = k / 10
        prev = rt.signals.get("drive")
        rt.begin_frame(t)
        if prev is not None and k > 1:
            step = max(step, abs(rt.signals["drive"] - prev))
        if k % 100 == 0:
            seen[k // 10] = bd.value(rt.signals, rt.avail, 1.0, 1.0)
    assert seen[30] < 0.9                                 # before min_s of live samples: below base (0.7 + ...)
    assert step < 0.2 * (1.0 - math.exp(-0.1 / 3.0)) + 1e-9                  # eased in (tau 3 s), never a step
    assert abs(bd.value(rt.signals, rt.avail, 1.0, 1.0) - 1.0) < 1e-3 and abs(rt.signals["drive"] - 0.5) < 1e-3
    pv = rt.pivots.view
    assert pv["drive"]["applied"] and pv["drive"]["median"] == 0.3 and pv["energy"]["median"] == 0.4
    assert pv["bass"]["samples"] == 0 and not pv["bass"]["applied"]           # n/a (no feed): held
    out = {}
    rt.inputs_view(out, t)
    assert out["pivot"]["signals"]["drive"]["applied"] and out["pivot"]["on"] and out["pivot"]["window_s"] == 600.0
    # louder than its norm: above base, as the mapping says
    rt.extra = {**music, "drive": 0.45}
    rt.begin_frame(t + 0.1)
    assert bd.value(rt.signals, rt.avail, 1.0, 1.0) > 1.05
    # the cue mappings' signals (sessions.Session._cue_signals, loop thread): the same re-centring
    s = {"drive": 0.3, "beat": 1.0}
    rt.pivots.apply(s)
    assert abs(s["drive"] - 0.5) < 1e-3 and s["beat"] == 1.0
    # the music's source fading out: eased to idle, the pivot by the same weight: the base all the way
    for wm in (0.6, 0.2, 0.0):
        rt.extra = {**music, "music_w": wm}
        rt.begin_frame(t + 1.0)
        assert abs(rt.signals["drive"] - 0.5) < 1e-3 and abs(bd.value(rt.signals, rt.avail, 1.0, 1.0) - 1.0) < 1e-3
    n = rt.pivots.view["drive"]["samples"]
    for k in range(5):
        rt.begin_frame(t + 2.0 + k)                       # not live: no samples
    assert rt.pivots.view["drive"]["samples"] == n


def test_silence_never_drags_the_norm_down():
    """show_service keeps publishing in a quiet room (energy, drive ... read 0 under its gate, fully live): those
    seconds are not music, so the ring holds; the music's own norm applies after min_s of it."""
    rt = SessionRuntime(None)
    rt.response = cfg(pivot_min_s=60.0, pivot_ease_s=0.0)
    quiet = {"energy": 0.0, "energy_slow": 0.0, "drive": 0.0, "punch": 0.0, "tempo": None, "music_w": 1.0}
    music = {"energy": 0.3, "energy_slow": 0.3, "drive": 0.3, "punch": 0.3, "tempo": None, "music_w": 1.0}
    t = 0.0
    rt.extra = quiet
    for k in range(15 * 60 * 2):                          # 15 minutes of a quiet room at 2 fps
        t = k / 2
        rt.begin_frame(t)
    assert rt.pivots.view["energy"]["samples"] == 0 and rt.pivots.view["drive"]["samples"] == 0
    rt.extra = music
    for k in range(1, 90 * 2):                            # then 90 s of music
        rt.begin_frame(t + k / 2)
    v = rt.pivots.view
    assert v["drive"]["applied"] and v["drive"]["median"] == 0.3 and v["energy"]["median"] == 0.3
    assert abs(rt.signals["drive"] - 0.5) < 1e-9 and abs(rt.signals["energy"] - 0.5) < 1e-9
    # present_at 0: every live second counts (silence too)
    rt2 = SessionRuntime(None)
    rt2.response = cfg(pivot_present_at=0.0)
    rt2.extra = quiet
    for k in range(40):
        rt2.begin_frame(k / 2)
    assert rt2.pivots.view["drive"]["samples"] > 0


def test_eased_in_and_out_and_shown():
    pv = pivots(window=600.0, min_s=3.0, ease_s=2.0)
    run(pv, [0.3] * 3)                                    # the third sample: drive applies (median 0.3), eased in
    c0 = pv.off["drive"][1]                               # one frame's worth on the frame it applies (dt <= 0.5 s)
    assert pv.tgt["drive"] == -0.2 and abs(c0 + 0.2 * (1.0 - math.exp(-0.5 / 2.0))) < 1e-9
    sig, t = {}, 2.0
    for k in range(1, 61):                                # 6 s at 10 fps
        sig = {"drive": 0.3}
        pv.step(sig, {}, t + k / 10)
    assert abs(pv.off["drive"][1] - (-0.2 + (c0 + 0.2) * math.exp(-6.0 / 2.0))) < 1e-6
    assert abs(pv.shift("drive") - pv.off["drive"][1]) < 1e-12 and pv.shift("bass") == 0.0
    pv.configure(cfg(pivot_on=False, pivot_ease_s=2.0))   # switched off: eased out, then gone
    t += 6.0
    for k in range(1, 400):
        pv.step({"drive": 0.3}, {}, t + k / 10)
    assert not pv.active
    # the inputs API: the shift in effect and the value the bindings read
    rt = SessionRuntime(None)
    rt.response = cfg(pivot_min_s=2.0, pivot_ease_s=0.0)
    rt.extra = {"energy": 0.4, "energy_slow": 0.4, "drive": 0.3, "punch": 0.3, "tempo": None, "music_w": 1.0}
    for k in range(30 * 10):
        rt.begin_frame(k / 10)
    out = {}
    rt.inputs_view(out, 30.0)
    d = out["pivot"]["signals"]["drive"]
    assert d["applied"] and abs(d["shift"] + 0.2) < 1e-6 and abs(d["value"] - 0.5) < 1e-6
    assert out["pivot"]["present_at"] == 0.03 and out["pivot"]["ease_s"] == 0.0
