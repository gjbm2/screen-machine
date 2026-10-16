"""Harmony (show_service/harmony.py): chroma, key, tension, chord change, register (docs/SHOW_ENGINE.md §3.5, §11.1).
Synthetic notes, "does it work" level."""

from __future__ import annotations

import numpy as np
import pytest

from show_service import contract, vban
from show_service.harmony import (Harmony, chroma_from_levels, key_estimate, key_fit, key_scores, roughness)
from show_service.midi import Piano


class FakePiano:
    def __init__(self, pitches=(), present: bool = True) -> None:
        self.present = present
        self.set(pitches)

    def set(self, pitches, level: float = 1.0) -> None:
        self.lv = np.zeros(128)
        self.lv[list(pitches)] = level

    def levels(self, t_s: float) -> np.ndarray:
        return self.lv.copy()


def chroma_of(pitches) -> np.ndarray:
    lv = np.zeros(128)
    lv[list(pitches)] = 1.0
    return chroma_from_levels(lv)


def run(h: Harmony, piano, t0: float, seconds: float, audio=None) -> float:
    t = t0
    while t < t0 + seconds:
        h.tick(t, piano, audio)
        t = round(t + 0.01, 6)
    return t


def test_chroma_from_levels():
    lv = np.zeros(128)
    lv[[48, 60, 64, 67]] = [0.5, 0.5, 0.25, 0.5]
    c = chroma_from_levels(lv)
    assert c.shape == (12,) and c[0] == 1.0 and c[4] == 0.25 and c[7] == 0.5 and c.sum() == pytest.approx(1.75)
    assert not chroma_from_levels(np.zeros(128)).any()


def test_key_estimate_c_major_triad_and_a_minor_triad():
    assert key_estimate(chroma_of([60, 64, 67]))[:2] == ("C", "major")
    assert key_estimate(chroma_of([57, 60, 64]))[:2] == ("A", "minor")
    assert key_estimate(np.zeros(12)) == (None, None, 0.0)
    assert key_estimate(np.ones(12)) == (None, None, 0.0)
    _, _, conf = key_estimate(chroma_of([60, 64, 67]))
    assert 0.3 < conf <= 1.0
    s = key_scores(chroma_of([60, 64, 67]))
    assert s is not None and s.shape == (24,) and np.all(np.abs(s) <= 1.0 + 1e-9)


def test_diminished_is_rougher_and_tenser_than_major():
    maj, dim = chroma_of([60, 64, 67]), chroma_of([59, 62, 65])
    assert roughness(maj) < 0.15 < roughness(dim)
    assert roughness(chroma_of([60, 61, 62])) > roughness(dim)
    assert roughness(chroma_of([60])) == 0.0 and roughness(np.zeros(12)) == 0.0
    faint = chroma_of([69]) + 0.15 * chroma_of([71])
    assert roughness(faint) < 0.5 * roughness(chroma_of([69, 71]))     # a faint neighbour counts for little
    out = {}
    for name, pcs in (("maj", [60, 64, 67]), ("dim", [59, 62, 65])):
        h = Harmony(lambda m: None)
        run(h, FakePiano(pcs), 0.0, 1.5)
        out[name] = h.latest
    assert out["dim"]["tension"] > out["maj"]["tension"] + 0.1


def test_c_major_triad_gives_c_major_published_at_10_hz():
    msgs: list[dict] = []
    h = Harmony(msgs.append)
    run(h, FakePiano([48, 60, 64, 67]), 100.0, 3.0)
    assert 28 <= len(msgs) <= 31
    assert all(m["type"] == contract.MUSIC_HARMONY for m in msgs)
    d = msgs[-1]["data"]
    assert set(d) == set(contract.HarmonyData.__annotations__)
    assert (d["key"], d["mode"], d["source"]) == ("C", "major", "midi")
    assert d["chroma12"] == [1.0, 0, 0, 0, 0.5, 0, 0, 0.5, 0, 0, 0, 0]          # C doubled: E and G at half
    assert 0.0 < d["conf"] <= 1.0 and d["tension"] < 0.1
    assert d["t_ms"] == pytest.approx(msgs[-1]["data"]["t_ms"]) and d["t_ms"] > 102000.0
    for k in ("tension", "chord_change", "register", "spread", "conf"):
        assert 0.0 <= d[k] <= 1.0


def _play(piano: Piano, h: Harmony, notes, t0: float = 0.0, step: float = 0.3) -> float:
    t, seq = t0, 0
    for n in notes:
        seq += 1
        ev = [(int(t * 1e9), 0x90, n, 80), (int((t + step * 0.9) * 1e9), 0x80, n, 0)]
        piano.on_midi(vban.MidiBatch(seq, ev, [], "s"), t, lambda x: x / 1e9)
        for k in range(int(round(step / 0.01))):
            piano.tick(t + k * 0.01)
            h.tick(t + k * 0.01, piano)
        t += step
    return t


def test_key_detection_on_played_scales():
    a_minor = [57, 59, 60, 62, 64, 65, 67, 69, 67, 65, 64, 62, 60, 59, 57]
    c_major = [60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65, 64, 62, 60]
    for notes, want in ((a_minor, ("A", "minor")), (c_major, ("C", "major"))):
        msgs: list[dict] = []
        h = Harmony(msgs.append)
        _play(Piano("room-piano", lambda m: None), h, notes)
        assert h.key == want
        assert msgs[0]["data"]["key"] is None                        # a lone first note names no key


def test_key_hysteresis_holds_through_a_short_excursion():
    h = Harmony(lambda m: None)
    p = FakePiano([60, 64, 67])
    t = run(h, p, 0.0, 6.0)
    assert h.key == ("C", "major")
    p.set([54, 58, 61])                     # F# major: 1 s is not enough to move an 8 s average ...
    t = run(h, p, t, 1.0)
    assert h.key == ("C", "major")
    t = run(h, p, t, 20.0)                  # ... a long stay is
    assert h.key == ("F#", "major")


def test_chord_change_pulses_then_decays():
    h = Harmony(lambda m: None)
    p = FakePiano([60, 64, 67])
    t = run(h, p, 0.0, 2.0)
    assert h.latest["chord_change"] < 0.05
    p.set([54, 58, 61])                     # F# major: shares nothing with C major
    t = run(h, p, t, 0.11)
    assert h.latest["chord_change"] > 0.8
    run(h, p, t, 2.0)
    assert h.latest["chord_change"] < 0.05


def test_register_and_spread():
    low, high, wide = Harmony(lambda m: None), Harmony(lambda m: None), Harmony(lambda m: None)
    low.tick(0.0, FakePiano([28, 31]))
    high.tick(0.0, FakePiano([96, 100]))
    wide.tick(0.0, FakePiano([28, 100]))
    assert low.latest["register"] < 0.2 < 0.8 < high.latest["register"]
    assert wide.latest["spread"] == 1.0 and low.latest["spread"] < 0.1
    assert wide.latest["register"] == pytest.approx(0.5, abs=0.05)


def test_audio_chroma_when_the_piano_is_not_present_and_silence():
    msgs: list[dict] = []
    h = Harmony(msgs.append)
    h.tick(0.0, FakePiano([60], present=False))           # no source: nothing
    h.tick(0.1, None)
    assert msgs == []
    audio = np.zeros(12)
    audio[[2, 6, 9]] = [0.4, 0.2, 0.3]                       # D major-ish, unnormalised
    h.tick(0.2, FakePiano([60], present=False), audio)
    d = msgs[-1]["data"]
    assert d["source"] == "audio" and d["chroma12"][2] == 1.0 and d["register"] == 0.5 and d["spread"] == 0.0
    h.tick(0.3, None, np.zeros(12))                          # silence: a valid frame
    d = msgs[-1]["data"]
    assert d["chroma12"] == [0.0] * 12 and d["register"] == 0.5 and d["chord_change"] >= 0.0
    s = Harmony(lambda m: None).compute(np.zeros(12), np.zeros(128), 1.0, "midi")
    assert s["key"] is None and s["mode"] is None and s["tension"] == 0.0 and s["conf"] == 0.0


def test_key_fit():
    assert key_fit(chroma_of([60, 64, 67]), 0) == 1.0                  # C major in C major
    assert key_fit(chroma_of([61, 66]), 0) == 0.0
    assert key_fit(chroma_of([61]), None) == 1.0
