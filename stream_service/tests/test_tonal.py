"""plugins/_lib/tonal.py: the shared tonal maths of the harmony plug-ins (CPU only, exact known values).

  pytest stream_service/tests/test_tonal.py
"""
from __future__ import annotations

import math
import time

import numpy as np
import pytest

from stream_service.plugins._lib import tonal as t

C, Db, D, Eb, E, F, Gb, G, Ab, A, Bb, B = range(12)
CMAJ, AMIN = 0, 12 + 9


def chroma(*pcs, level=1.0) -> np.ndarray:
    c = np.zeros(12)
    c[[p % 12 for p in pcs]] = level
    return c


def notes_table(levels: dict) -> np.ndarray:
    n = np.zeros((128, 4), np.float32)
    n[:, 2] = np.inf
    n[:, 3] = np.inf
    for p, lv in levels.items():
        n[p, 0] = lv
    return n


# -- basics ----------------------------------------------------------------------------------------------------
def test_fifths_and_spelling():
    assert [t.fifths_of(x) for x in range(12)] == [0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5]
    assert [t.lof_name(k) for k in (0, -1, 6, -2, -6, -9, -12, 13, 7)] == \
        ["C", "F", "F#", "Bb", "Gb", "Bbb", "Dbb", "Fx", "C#"]
    assert t.lof_name(-2, unicode=True) == "B♭"
    assert t.spell(Ab, 0) == -4 and t.spell(Ab, 6) == 8                     # Ab from C, G# from E-ish context
    assert t.spell(Gb, 0) == 6                                               # tie at the tritone goes sharp
    out = np.empty(12, np.int64)
    for ref in (-7.3, 0.0, 1.1, 5.5, 13.9):
        assert list(t.spell_all(ref, out)) == [t.spell(p, ref) for p in range(12)]
    assert all(t.lof_pc(t.spell(p, 3.3)) == p for p in range(12))


def test_key_names():
    assert t.key_index("A#", "major") == Bb and t.key_index("Bb", "major") == Bb
    assert t.key_index("A", "minor") == AMIN and t.key_index("B♭", "minor") == 12 + Bb
    assert t.key_index(None, None) == -1 and t.key_index("H", "major") == -1 and t.key_index("C", "dorian") == -1
    assert [t.key_label(k) for k in (0, Eb, Gb, 12 + Eb, 12 + Db, AMIN)] == \
        ["C major", "Eb major", "F# major", "D# minor", "C# minor", "A minor"]
    assert t.key_label(AMIN, short=True) == "a" and t.key_label(Bb, short=True) == "Bb"
    assert [t.key_signature(k) for k in (0, G, F, AMIN, 12 + E, 12 + D, Db)] == [0, 1, -1, 0, 1, -1, -5]


def test_pc_energy_bass_loudest():
    n = notes_table({48: 0.5, 52: 0.4, 55: 0.6, 60: 0.3, 64: 0.01})
    e = t.pc_energy(n)
    assert e.dtype == np.float32 and e.shape == (12,)
    assert e[C] == pytest.approx(0.8) and e[E] == pytest.approx(0.41) and e[G] == pytest.approx(0.6)
    out = np.empty(12, np.float32)
    assert t.pc_energy(n, out) is out
    assert t.bass_pitch(n) == 48 and t.bass_pitch(notes_table({})) == -1
    assert t.bass_pitch(notes_table({30: 0.01, 40: 0.5})) == 40              # under the floor does not count
    assert list(t.loudest(n, 2)) == [48, 55] and list(t.loudest(n, 10)) == [48, 52, 55, 60]


def test_attack_release():
    ar = t.AttackRelease(12, attack_s=0.1, release_s=1.0)
    x = chroma(C)
    assert ar.step(x, 0.0)[C] == 0.0                                          # dt 0: nothing
    assert ar.step(x, float("nan"))[C] == 0.0 and ar.step(x, -1.0)[C] == 0.0
    v = ar.step(x, 0.1)[C]
    assert v == pytest.approx(1 - math.exp(-1))
    ar.step(x, 1e9)
    assert ar.value[C] == 1.0                                                 # huge dt lands, never overshoots
    ar.step(np.zeros(12), 1.0)
    assert ar.value[C] == pytest.approx(math.exp(-1))                         # release tau
    assert t.ema_alpha(0.0, 1.0) == 0.0 and t.ema_alpha(1.0, 0.0) == 1.0


def test_midi_hz_and_beats():
    assert t.midi_hz(69) == pytest.approx(440.0)
    assert 3 * t.midi_hz(60) - 2 * t.midi_hz(67) == pytest.approx(0.89, abs=0.005)      # C4 + G4 beat
    assert abs(5 * t.midi_hz(60) - 4 * t.midi_hz(64)) == pytest.approx(10.4, abs=0.05)  # C4 + E4
    assert np.allclose(t.midi_hz(np.array([57.0, 81.0])), [220.0, 880.0])


def test_smoothsteps():
    assert t.smoothstep(0.75, 0.95, 0.7) == 0 and t.smoothstep(0.75, 0.95, 1.0) == 1
    assert t.smoothstep(0.0, 1.0, 0.5) == 0.5 and t.smootherstep(0.0, 1.0, 0.5) == 0.5


# -- keys ------------------------------------------------------------------------------------------------------
def test_key_scores_match_show_service():
    harmony = pytest.importorskip("show_service.harmony")
    rng = np.random.default_rng(1)
    for _ in range(20):
        c = rng.random(12)
        assert np.allclose(t.key_scores(c), harmony.key_scores(c))
    assert t.key_scores(np.ones(12)) is None and t.key_scores(np.zeros(12)) is None
    assert np.allclose(t.KK_Z, harmony._Z) and (t.SCALE_MASK == harmony._SCALES).all()
    for name in ("KEY_TAU_S", "KEY_MIN_R", "KEY_MARGIN", "KEY_FIRST_S", "KEY_HOLD_S", "CONF_MARGIN"):
        assert getattr(t, name) == getattr(harmony, name)


def _play(tracker_update, seq, step_s=0.25, dt=1 / 30):
    """Feed (pcs, seconds) chunks at 30 fps; each pcs set sounds at level 1."""
    for pcs, dur in seq:
        x = chroma(*pcs).astype(np.float32)
        for _ in range(int(round(dur / dt))):
            tracker_update(x, dt)


def test_key_tracker_c_major_scale():
    kt = t.KeyTracker()
    scale = [((p,), 0.25) for p in (C, D, E, F, G, A, B, C)]
    _play(kt.update, scale * 4)
    assert kt.key == CMAJ and kt.conf > 0.0


def test_key_tracker_a_minor_chords():
    kt = t.KeyTracker()
    prog = [((A, C, E), 1.0), ((D, F, A), 1.0), ((E, Ab, B), 1.0), ((A, C, E), 1.0)]
    _play(kt.update, prog * 3)
    assert kt.key == AMIN


def test_key_tracker_agrees_with_room_and_holds():
    harmony = pytest.importorskip("show_service.harmony")
    room = harmony.Harmony(lambda m: None)
    kt = t.KeyTracker()
    seq = [((C, E, G), 1.0), ((F, A, C), 1.0), ((G, B, D, F), 1.0), ((C, E, G), 1.0)] * 3 \
        + [((D, Gb, A), 1.0), ((G, B, D), 1.0), ((A, Db, E, G), 1.0), ((D, Gb, A), 1.0)] * 6
    tt = 0.0
    seen = []
    for pcs, dur in seq:
        x = chroma(*pcs)
        for _ in range(int(dur * 30)):
            tt += 1 / 30
            kt.update(x.astype(np.float32), 1 / 30)
            if abs(tt * 10 - round(tt * 10)) < 1e-6:          # harmony at 10 Hz
                d = room.compute(x, None, tt, "midi")
                seen.append((t.key_index(d["key"], d["mode"]), kt.key))
    agree = sum(1 for a, b in seen if a == b) / len(seen)
    assert agree > 0.95
    assert seen[-1] == (D, D)


def test_key_tracker_first_key_waits_and_pending():
    kt = t.KeyTracker()
    x = chroma(C, E, G).astype(np.float32)
    kt.update(x, 0.0)
    kt.update(x, 0.5)
    assert kt.key == -1 and kt.pending == CMAJ and 0.0 < kt.pending_frac < 1.0
    for _ in range(12):
        kt.update(x, 0.1)
    assert kt.key == CMAJ and kt.pending == -1


def test_key_resolver_prefers_room_then_own():
    kr = t.KeyResolver(hold_s=0.5)
    x = chroma(C, E, G).astype(np.float32)
    assert kr.key == CMAJ and not kr.known and kr.source == "default"
    ch = kr.update(x, 1 / 30, "A#", "major")                        # the room's first key: taken at once
    assert ch is not None and ch.first and ch.to == Bb and ch.fifths == -2 and kr.source == "room"
    assert kr.tonic == Bb and kr.mode == "major" and kr.label == "Bb major" and kr.tonic_lof == -2
    for _ in range(10):                                               # a new room key must hold 0.5 s
        assert kr.update(x, 0.04, "F", "major") is None
    assert kr.key == Bb and kr.pending == F and kr.pending_s > 0
    ch = None
    for _ in range(5):
        ch = ch or kr.update(x, 0.04, "F", "major")
    assert ch is not None and ch.frm == Bb and ch.to == F and ch.fifths == 1 and ch.signature == 1
    assert kr.update(x, 0.04, "F", "major") is None                  # reported once

    own = t.KeyResolver()                                              # no room key: the own tracker's
    for _ in range(60):
        own.update(chroma(A, C, E).astype(np.float32), 1 / 30)
        own.update(chroma(E, Ab, B).astype(np.float32), 1 / 30)
    assert own.known and own.source == "own" and own.key == AMIN and own.conf > 0


def test_key_change_fifths():
    kr = t.KeyResolver(hold_s=0.0)
    x = np.zeros(12, np.float32)
    kr.update(x, 0.1, "C", "major")
    assert kr.update(x, 0.1, "A", "minor").fifths == 3                # tonic moved C -> A
    assert kr.change.signature == 0                                   # same signature
    assert kr.update(x, 0.1, "D#", "minor").fifths == 6               # A -> Eb: the tritone, +6
    assert kr.update(x, 0.1, "G", "major").fifths == 4                # Eb -> G: Bb F C G


# -- Spiral Array --------------------------------------------------------------------------------------------
def test_spiral_constants():
    assert t.SPIRAL_H == pytest.approx(0.3651, abs=1e-4)
    assert t.SPIRAL_N0 == pytest.approx(np.linalg.norm(t.spiral_point(0) - t.spiral_point(12)))
    assert np.allclose(t.spiral_point(1), [1, 0, t.SPIRAL_H], atol=1e-12)
    assert t.spiral_point(np.arange(4)).shape == (4, 3)


C_MAJOR_STATIONS = {   # name: (triad id, S, beta)  (tonal_gravity spec, verified)
    "C": (C, 0.08, 0), "Cm": (12 + C, 0.14, -1.3), "G": (G, 0.20, 1.0), "Gm": (12 + G, 0.21, -0.3),
    "Am": (12 + A, 0.22, 1.7), "Em": (12 + E, 0.23, 2.7), "F": (F, 0.23, -1.0), "Dm": (12 + D, 0.26, 0.7),
    "Fm": (12 + F, 0.26, -2.3), "D": (D, 0.32, 2.0), "Bb": (Bb, 0.32, -2.0), "Eb": (Eb, 0.32, -3.0),
    "A": (A, 0.32, 3.0), "E": (E, 0.34, 4.0), "Ab": (Ab, 0.35, -4.0), "B": (B, 0.45, 5.0),
    "Db": (Db, 0.47, -5.0), "F#": (Gb, 0.56, 6.0),
}


def test_spiral_station_table_c_major():
    sk = t.SpiralKey(CMAJ)
    for name, (tid, s, beta) in C_MAJOR_STATIONS.items():
        assert sk.strain[tid] == pytest.approx(s, abs=0.0075), name
        assert sk.bearing[tid] == pytest.approx(beta, abs=0.05), name
        assert sk.strain_of(sk.stations[tid]) == pytest.approx(sk.strain[tid])
        assert sk.bearing_of(sk.stations[tid]) == pytest.approx(sk.bearing[tid])
    order = sorted(C_MAJOR_STATIONS, key=lambda n: sk.strain[C_MAJOR_STATIONS[n][0]])
    assert order[:3] == ["C", "Cm", "G"]                              # V is the nearest major terrace
    assert sk.dominant_distance(sk.stations[G]) == pytest.approx(0.0)


def test_spiral_station_table_a_minor():
    sk = t.SpiralKey(AMIN)
    assert sk.k_tonic == 3
    for tid, s in ((12 + A, 0.08), (A, 0.13), (12 + E, 0.18), (E, 0.22), (12 + D, 0.23), (C, 0.23)):
        assert sk.strain[tid] == pytest.approx(s, abs=0.0075)
    assert sk.bearing[12 + A] == pytest.approx(0.0)


def test_centre_of_effect():
    ks = t.spell_all(0.0)
    assert t.centre_of_effect(np.zeros(12), ks) is None
    one = t.centre_of_effect(chroma(G), ks)
    assert np.allclose(one, t.spiral_point(1))                        # a single note's CE is its own P(k)
    tri = chroma(C, E, G)
    tri[C], tri[G], tri[E] = t.SPIRAL_W
    assert np.allclose(t.centre_of_effect(tri, ks), t.chord_point(0))   # weighted triad -> its chord point


# -- chords ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("key,prog", [
    (CMAJ, [(C, "maj", "I"), (A, "min", "vi"), (D, "min", "ii"), (G, "7", "V7"), (C, "maj", "I"),
            (F, "maj", "IV"), (B, "dim", "vii°"), (E, "min", "iii"), (B, "hdim7", "viiø7"),
            (B, "dim7", "vii°7"), (D, "maj", "V/V"), (D, "7", "V7/V"), (E, "maj", "V/vi"), (A, "7", "V7/ii"),
            (C, "7", "V7/IV"), (Ab, "maj", "bVI"), (F, "min", "iv"), (Db, "maj", "bII"), (Bb, "maj", "bVII"),
            (Eb, "maj", "bIII"), (G, "min", "v"), (C, "min", "i"), (Gb, "maj", "bV"), (Gb, "dim7", "vii°7/V"),
            (Db, "min", "bii"), (C, "aug", "I+"), (F, "maj7", "IVmaj7"), (D, "m7", "ii7"), (G, "sus4", "Vsus4")]),
    (AMIN, [(A, "min", "i"), (D, "min", "iv"), (E, "maj", "V"), (E, "7", "V7"), (F, "maj", "VI"),
            (C, "maj", "III"), (G, "maj", "VII"), (B, "dim", "ii°"), (B, "hdim7", "iiø7"),
            (Ab, "dim7", "vii°7"), (Ab, "dim", "vii°"), (E, "min", "v"), (D, "maj", "IV"),
            (B, "maj", "V/V"), (A, "maj", "V/iv"), (G, "7", "V7/III"), (C, "7", "V7/VI"), (Bb, "maj", "bII")]),
])
def test_roman_numerals(key, prog):
    for root, q, want in prog:
        assert t.roman(root, q, key) == want, (t.key_label(key), root, q)


def test_roman_unicode_and_no_secondary():
    assert t.roman(Ab, "maj", CMAJ, unicode=True) == "♭VI"
    assert t.roman(D, "maj", CMAJ, secondary=False) == "II"


def test_function_classes():
    T, PD, D_, X = t.FN_T, t.FN_PD, t.FN_D, t.FN_CHROMATIC
    cmaj = [(C, "maj", T), (A, "min", T), (E, "min", T), (D, "min", PD), (F, "maj", PD), (F, "min", PD),
            (Ab, "maj", PD), (Db, "maj", PD), (G, "maj", D_), (G, "7", D_), (B, "dim", D_), (B, "dim7", D_),
            (D, "7", D_), (E, "maj", D_), (Eb, "maj", X), (Gb, "maj", X), (C, "maj7", T)]
    for root, q, fn in cmaj:
        assert t.function_class(root, q, CMAJ) == fn, (root, q)
    amin = [(A, "min", T), (C, "maj", T), (F, "maj", T), (D, "min", PD), (B, "dim", PD), (E, "7", D_),
            (Ab, "dim7", D_), (G, "maj", D_), (B, "maj", D_)]
    for root, q, fn in amin:
        assert t.function_class(root, q, AMIN) == fn, (root, q)
    assert t.FUNCTION_NAMES[t.FN_PD] == "PD"


def test_inversions_and_resolution():
    assert t.inversion("maj", C, E) == "6" and t.inversion("maj", C, G) == "6/4" and t.inversion("maj", C, C) == ""
    assert t.inversion("7", G, B) == "6/5" and t.inversion("7", G, D) == "4/3" and t.inversion("7", G, F) == "4/2"
    assert t.inversion("min", A, -1) == "" and t.inversion("maj", C, D) == ""
    assert t.resolution(G, C) == t.RES_FULFILLED                        # V -> I
    assert t.resolution(D, G) == t.RES_FULFILLED                        # V/V -> V
    assert t.resolution(G, A) == t.RES_DECEPTIVE                        # V -> vi
    assert t.resolution(E, F) == t.RES_DECEPTIVE                        # V -> VI in A minor
    assert t.resolution(G, Eb) == t.RES_NONE and t.resolution(G, G) == t.RES_NONE
    assert t.resolution(G, F) == t.RES_NONE and t.resolution(-1, C) == t.RES_NONE


def test_chord_labels():
    assert t.chord_label(Eb, "maj", CMAJ) == "Eb" and t.chord_label(Gb, "min", CMAJ) == "F#m"
    assert t.chord_label(Ab, "maj", 12 + Db) == "G#" and t.chord_label(B, "hdim7", CMAJ) == "Bø7"
    assert t.chord_pcs(G, "7") == (G, B, D, F)


def _feed(reader, pcs_seq, dt=1 / 60, **kw):
    changes = []
    for pcs, dur, bass in pcs_seq:
        x = chroma(*pcs).astype(np.float32)
        for _ in range(int(round(dur / dt))):
            if reader.update(x, dt, bass, **kw):
                changes.append((reader.root, reader.quality))
    return changes


def test_chord_reader_cosine_triads_with_hold():
    r = t.ChordReader()
    assert r.chord == -1 and r.quality is None and r.triad() == -1
    _feed(r, [((C, E, G), 0.05, C)])
    assert r.chord == -1                                              # 80 ms hold not served yet
    _feed(r, [((C, E, G), 0.05, C)])
    assert (r.root, r.quality, r.triad()) == (C, "maj", C) and r.conf > 0.9
    ch = _feed(r, [((A, C, E), 0.5, A), ((D, F, A), 0.5, D), ((G, B, D), 0.5, G), ((C, E, G), 0.5, C)])
    assert ch == [(A, "min"), (D, "min"), (G, "maj"), (C, "maj")]
    assert r.prev == G and r.minor is False
    _feed(r, [((A, C, E), 0.06, A), ((C, E, G), 0.5, C)])           # a 60 ms flicker never lands
    assert r.root == C and not r.changed
    _feed(r, [((), 0.5, -1)])                                         # silence keeps the chord, conf 0
    assert r.root == C and r.conf == 0.0 and not r.sounding


def test_chord_reader_bass_weighting():
    r = t.ChordReader()
    # A C E G: Am7 notes; with A in the bass the triad reader says Am, with C in the bass it says C
    _feed(r, [((A, C, E, G), 0.3, A)])
    assert (r.root, r.quality) == (A, "min")
    r2 = t.ChordReader()
    _feed(r2, [((A, C, E, G), 0.3, C)])
    assert (r2.root, r2.quality) == (C, "maj")


def test_chord_reader_score_method():
    r = t.ChordReader(t.QUALITIES, "score", rate_hz=10)
    ch = _feed(r, [((G, B, D, F), 0.5, G), ((C, E, G), 0.5, C), ((B, D, F, Ab), 0.5, B)], key=CMAJ)
    assert ch[:2] == [(G, "7"), (C, "maj")] and r.quality == "dim7"
    assert r.root in (B, D, F, Ab)
    # a dim7 has no root: the reader keeps the rotation nearest the previous root on the line of fifths (C -> B)
    assert r.root == B
    ok = _feed(r, [((A, C, E), 0.5, A)], key=CMAJ, allow=False)
    assert ok == [] and r.quality == "dim7"                           # the gate holds the chord
    assert r.n_pcs == 3


def test_chord_reader_rejects_bad_method():
    with pytest.raises(ValueError):
        t.ChordReader(method="nope")


# -- DFT and symmetry -------------------------------------------------------------------------------------------
SIGMA = {  # set: {order: sigma}  (symmetry_lens spec, verified)
    "note": ((C,), {2: 0, 3: 0, 4: 0, 6: 0}),
    "fifth": ((C, G), {2: 0, 3: 0, 4: 0, 6: 0}),
    "diatonic": ((C, D, E, F, G, A, B), {2: 0, 3: 0, 4: 0, 6: 0}),
    "major": ((C, E, G), {3: 0.19}),
    "minor": ((C, Eb, G), {3: 0.19}),
    "G7": ((G, B, D, F), {2: 0.31, 4: 0.31}),
    "dim": ((C, Eb, Gb), {2: 0.59, 4: 0.59}),
    "dim7": ((C, Eb, Gb, A), {2: 1, 4: 1}),
    "octatonic": ((C, Db, Eb, E, Gb, G, A, Bb), {2: 1, 4: 1}),
    "augmented": ((C, E, Ab), {3: 1, 6: 0.27}),
    "whole-tone": ((C, D, E, Gb, Ab, Bb), {2: 1, 3: 1, 6: 1}),
    "tritone": ((C, Gb), {2: 1, 4: 0.27}),
    "French sixth": ((C, E, Gb, Bb), {2: 1}),
}


@pytest.mark.parametrize("name", list(SIGMA))
def test_symmetry_sigma_table(name):
    pcs, want = SIGMA[name]
    s = t.symmetry(chroma(*pcs))
    for n, v in want.items():
        assert s[t.SYMMETRY_ORDERS.index(n)] == pytest.approx(v, abs=0.006), (name, n)


def test_symmetry_silence_and_dft():
    assert (t.symmetry(np.zeros(12)) == 0).all()
    assert (t.symmetry(np.ones(12)) == 0).all()
    dft = t.pc_dft(chroma(C, D, E, F, G, A, B))
    assert dft[0] == pytest.approx(7) and abs(dft[5]) / 7 == pytest.approx(0.533, abs=0.001)     # diatonicity
    assert abs(t.pc_dft(chroma(C, E, G))[5]) / 3 == pytest.approx(0.644, abs=0.001)
    assert abs(t.pc_dft(chroma(C, Eb, Gb, A))[5]) == pytest.approx(0.0, abs=1e-12)
    # transposing up s semitones turns F_n by -2 pi n s / 12 (the fold angle psi_n = -arg F_n / n turns +30 deg x s)
    a, b = t.pc_dft(chroma(C, E, Ab)), t.pc_dft(chroma(Db, F, A))
    d = np.angle(b[3] / a[3])
    assert d == pytest.approx(-2 * np.pi * 3 / 12)
    out = np.empty(7, np.complex128)
    assert t.pc_dft(chroma(C), out) is out


# -- Tonnetz ----------------------------------------------------------------------------------------------------
def test_plr_moves():
    Cm, Em, Am = 12 + C, 12 + E, 12 + A
    assert t.plr(C, "P") == Cm and t.plr(C, "L") == Em and t.plr(C, "R") == Am
    for tid in range(24):
        for o in range(3):
            assert t.plr(t.plr(tid, o), o) == tid                     # each move is an involution


def test_plr_distances_from_c():
    want = {12 + A: 1, 12 + C: 1, 12 + E: 1, G: 2, F: 2, A: 2, Ab: 2, E: 2, Eb: 2, 12 + D: 3,
            D: 4, Bb: 4, B: 4, Db: 4, Gb: 4, 12 + Bb: 5}
    for tid, d in want.items():
        assert t.PLR_DIST[C, tid] == d, tid
    assert t.PLR_DIST.max() == 5 and (t.PLR_DIST == t.PLR_DIST.T).all()
    for a in range(24):
        for b in range(24):
            path = t.plr_path(a, b)
            assert len(path) == t.PLR_DIST[a, b]
            x = a
            for o in path:
                x = t.plr(x, o)
            assert x == b


def test_lattice_triangles():
    assert t.lattice_pc(1, 0) == G and t.lattice_pc(0, 1) == E and t.lattice_lof(-1, 1) == 3     # A
    for tid in range(24):
        a, b, up = t.triad_tri(tid)
        assert t.tri_triad(a, b, up) == tid
        root, third, fifth = (t.lattice_pc(*v) for v in t.tri_vertices(a, b, up))
        q = "maj" if tid < 12 else "min"
        assert (root, third, fifth) == t.chord_pcs(tid % 12, q)
        for o in range(3):
            na, nb, nup = t.tri_move(a, b, up, o)
            assert t.tri_triad(na, nb, nup) == t.plr(tid, o)
            shared = set(t.tri_vertices(a, b, up)) & set(t.tri_vertices(na, nb, nup))
            assert len(shared) == 2                                    # a flip over a shared edge
    assert t.lof_name(t.lattice_lof(*t.tri_vertices(0, 0, False)[0])) == "E"     # down (0,0) = E minor


def test_comma_pump_and_cents():
    assert t.lattice_cents(-4, 1) == pytest.approx(-21.506, abs=1e-3)
    assert t.lattice_cents(0, -3) == pytest.approx(41.058, abs=1e-3)
    assert t.lattice_cents(4, -1) == pytest.approx(21.506, abs=1e-3)
    # I-vi-ii-V-I by the shortest lattice moves lands a syntonic comma from home
    tri = t.triad_tri(C)
    home = tri
    for tid in (12 + A, 12 + D, G, C):
        cur = t.tri_triad(*tri)
        for o in t.plr_path(cur, tid):
            tri = t.tri_move(*tri, o)
    assert t.tri_triad(*tri) == C
    da, db = tri[0] - home[0], tri[1] - home[1]
    assert (da, db) != (0, 0) and abs(t.lattice_cents(da, db)) == pytest.approx(21.506, abs=1e-3)


def test_nearest_copy():
    for tid in (C, 12 + A, Gb):
        for a_f, b_f in ((0.3, 0.3), (37.2, -11.8), (-120.0, 55.5)):
            a, b, up = t.nearest_copy(tid, a_f, b_f)
            assert t.tri_triad(a, b, up) == tid
            ca, cb = t.tri_centroid(a, b, up)
            x, y = t.lattice_xy(ca - a_f, cb - b_f)
            d = math.hypot(x, y)
            for i, j in ((4, -1), (-4, 1), (0, 3), (0, -3), (4, 2), (-4, -2)):
                x2, y2 = t.lattice_xy(ca + i - a_f, cb + j - b_f)
                assert d <= math.hypot(x2, y2) + 1e-9
    assert t.xy_lattice(*t.lattice_xy(2.5, -1.25)) == pytest.approx((2.5, -1.25))


# -- the ring ---------------------------------------------------------------------------------------------------
def test_ring_log():
    r = t.RingLog(4, 3)
    assert len(r) == 0 and r.ordered().shape == (0, 3)
    for i in range(6):
        r.push(i, i * 10)
    assert len(r) == 4 and r.pushed == 6
    assert r.row(0)[0] == 5 and r.row(3)[0] == 2 and r.row(0)[2] == 0
    assert list(r.ordered()[:, 0]) == [2, 3, 4, 5]
    out = np.empty((4, 3))
    assert list(r.ordered(out)[:, 1]) == [20, 30, 40, 50]
    with pytest.raises(IndexError):
        r.row(4)
    r.clear()
    assert len(r) == 0


# -- cost -------------------------------------------------------------------------------------------------------
def test_per_frame_cost():
    """Generous bounds (the host is shared with live encoding); typical costs are 5-35 us."""
    n = notes_table({48: 0.5, 52: 0.4, 55: 0.6, 60: 0.3})
    e = np.empty(12, np.float32)
    kr, cr, ar = t.KeyResolver(), t.ChordReader(), t.AttackRelease()
    s4, F = np.empty(4), np.empty(7, np.complex128)

    def frame():
        t.pc_energy(n, e)
        ar.step(e, 1 / 60)
        kr.update(e, 1 / 60, "C", "major")
        cr.update(e, 1 / 60, t.bass_pitch(n) % 12, kr.key)
        t.symmetry(e, s4, t.pc_dft(e, F))

    frame()
    best = math.inf
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(200):
            frame()
        best = min(best, (time.perf_counter() - t0) / 200)
    assert best < 1e-3
