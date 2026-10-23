"""Piano signals (piano.py): the eight bind signals from the sounding notes, and their use in the bindings."""
from __future__ import annotations

import math

import pytest

from stream_service import binding, piano
from stream_service.inputs import BIND_SIGNALS, NA_SIGNALS, TRIGGERS, signal_catalog
from stream_service.modulation import SessionRuntime
from stream_service.piano import NoteState, PianoSignals, envelopes, tension_of


def chroma(*pcs):
    c = [0.0] * 12
    for p in pcs:
        c[p % 12] += 1.0
    return c


def amps(*notes, a=0.8):
    v = [0.0] * 128
    for n in notes:
        v[n] = a
    return v


def test_tension_orders_the_chords():
    major, dom7, dim7, cluster = (tension_of(chroma(*x)) for x in ((0, 4, 7), (0, 4, 7, 10), (0, 3, 6, 9), (0, 1, 2)))
    assert tension_of(chroma(0)) == 0.0 and tension_of(chroma(0, 7)) < major < dom7 < dim7 < cluster == 1.0


def test_signals_follow_the_playing_and_go_na_when_silent():
    ps = PianoSignals()
    t = 0.0
    out = ps.step(t, NoteState(online=True))
    assert out["velocity"] is None and out["pedal"] is None               # connected, nothing played yet
    # a fast, loud run high on the keyboard with the pedal down
    for i in range(40):
        t += 0.1
        out = ps.step(t, NoteState(online=True, amps=amps(84, 88, 91), pedal=1.0,
                                   onsets=[(t - 0.05, 84 + i % 8, 0.9)]))
    assert out["note_rate"] > 0.7 and out["velocity"] > 0.85 and out["register"] > 0.7 and out["pedal"] == 1.0
    assert out["note_on_t"] == pytest.approx(t - 0.05) and out["note_on_vel"] == 0.9
    slow = PianoSignals()
    t2 = 0.0
    for i in range(40):
        t2 += 0.1
        o2 = slow.step(t2, NoteState(online=True, amps=amps(40), onsets=[(t2, 40, 0.3)] if i % 10 == 0 else []))
    assert o2["note_rate"] < out["note_rate"] and o2["velocity"] < out["velocity"] and o2["register"] < out["register"]
    assert o2["spread"] < out["spread"]
    for _ in range(100):                                                # silence
        t += 0.1
        out = ps.step(t, NoteState(online=True))
    assert out["note_rate"] is None and out["velocity"] is None and out["pedal"] is None
    assert all(v is None for k, v in ps.step(t + 1, None).items() if k in piano.PIANO_LEVELS)


def test_numpy_amps_give_plain_floats():
    import json
    import numpy as np
    a = np.zeros(128, np.float32)
    a[[60, 64, 67]] = 0.7
    ps = PianoSignals()
    ps.step(0.0, NoteState(online=True, amps=a, onsets=[(0.0, 60, 0.5)]))
    out = ps.step(0.1, NoteState(online=True, amps=a, pedal=np.float32(0.5)))
    json.dumps(out)                                                        # the /inputs monitor serialises these
    assert type(out["register"]) is float and type(out["tension"]) is float


def test_chord_changes_fire_once_per_new_harmony():
    ps = PianoSignals()
    t, fired = 0.0, []
    for chord in ((60, 64, 67), (60, 64, 67), (62, 65, 69), (62, 65, 69), (62, 65, 69, 72), (55, 59, 62)):
        for _ in range(5):
            t += 0.1
            out = ps.step(t, NoteState(online=True, amps=amps(*chord)))
            if out["chord_change_t"] not in fired and out["chord_change_t"] is not None:
                fired.append(out["chord_change_t"])
    assert len(fired) == 3                         # C, Dm, (Dm add D: the same harmony), G


def test_envelopes_and_runtime_merge():
    assert envelopes({"note_on_t": 1.0, "note_on_vel": 0.8, "chord_change_t": None}, 1.0)["note_on"] == pytest.approx(0.8)
    e = envelopes({"note_on_t": 1.0, "note_on_vel": 0.8, "chord_change_t": 0.0}, 1.18)
    assert e["note_on"] == pytest.approx(0.8 * math.exp(-1)) and e["chord_change"] == pytest.approx(math.exp(-1.18))
    rt = SessionRuntime(None)
    rt.extra = {"velocity": 0.7, "register": 0.4, "pedal": 1.0, "note_on_t": 0.5, "note_on_vel": 1.0, "chord_change_t": None}
    for i in range(11):
        rt.begin_frame(0.5 + 0.5 * i)                         # avail eases in over ~2 s
    assert rt.signals["velocity"] == 0.7 and rt.signals["pedal"] == 1.0 and rt.avail["velocity"] > 0.9
    assert rt.signals["note_on"] < 1e-6 and rt.signals["chord_change"] == 0.0


def test_catalog_and_bindings_use_them():
    cat = signal_catalog()
    assert {"note_on", "chord_change"} <= set(TRIGGERS) and set(piano.PIANO_LEVELS) <= set(NA_SIGNALS)
    assert all(s in BIND_SIGNALS for s in piano.PIANO_SIGNALS) and cat["tension"]["from"] == "piano"
    prop = {"type": "number", "minimum": 0.0, "maximum": 2.0, "x-bindable": True}
    b = binding.parse_v2("swirl_speed", prop, {"in": [{"sig": "velocity", "w": .6}, {"sig": "note_rate", "w": .4}],
                                               "map": {"scale": [.8, 1.3]}, "kick": {"sig": "note_on", "amt": .1}})
    bd = binding.Bound("swirl_speed", prop, b)
    assert bd.value({}, None, 1.0, 1.0) == pytest.approx(1.0)                          # silent piano: the preset
    assert bd.value({"velocity": 1.0, "note_rate": 1.0, "note_on": 1.0}, None, 1.0, 1.0) == pytest.approx(1.5)
    with pytest.raises(binding.MappingError, match="mixed_kinds"):
        binding.parse_v2("x", prop, {"in": [{"sig": "note_on"}, {"sig": "velocity"}], "map": {"around": [-.1, .1]}})
