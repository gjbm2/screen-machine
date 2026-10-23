"""The phrase-level metre (metre.HeldGrid, metre.Phrase, metre.phrase_metre, analyse.py's phrase state): the pulse
held at one level while the windows' beats change level, and the class read over the phrase."""
import numpy as np
import pytest

from stream_service.score import metre as M


def _windows(beats, step=1.0, span=12.0, start=3.0):
    """(window beats, newest time) as the worker sees them: each window's beats on one level, the level changing
    window to window (as PM2S's do)."""
    end = beats[-1]
    for k, t in enumerate(np.arange(start, end, step)):
        w = beats[(beats >= t - span) & (beats <= t)]
        if len(w) < 6:
            continue
        kind = k % 4
        if kind == 1:
            w = w[1::2]                                              # half the rate
        elif kind == 2:
            w = M._subdivide(w, 2)                                   # twice the rate
        elif kind == 3:
            w = M._decimate(M._subdivide(w, 2), 3)                   # 3:2 (a dotted reading)
        yield w, t


def test_held_grid_keeps_one_level_while_windows_change_it():
    rng = np.random.default_rng(0)
    beats = np.cumsum(np.full(80, 0.5) + rng.normal(0, 0.01, 80))
    g = M.HeldGrid()
    for w, t in _windows(beats):
        g.update(w, t)
    assert abs(g.P - 0.5) < 0.03, g.P
    inner = g.t[(g.t > beats[2]) & (g.t < beats[-8])]
    d = np.abs(inner[:, None] - beats[None, :]).min(1)
    assert np.mean(d < 0.04) > 0.9, np.round(d, 3)


def test_held_grid_follows_a_level_the_windows_settle_on():
    beats = np.arange(0.0, 40.0, 0.4)
    g = M.HeldGrid()
    g.update(beats[(beats >= 0) & (beats <= 2.5)][::2], 2.5)       # a short first window at half the rate
    for t in np.arange(3.5, 40.0, 1.0):
        g.update(beats[(beats >= t - 12) & (beats <= t)], t)
    assert abs(g.P - 0.4) < 0.03, g.P


def _waltz(bars=26, beat=0.5, t0=0.3, seed=1):
    """3/4: a low bass note on each downbeat, chords on beats 2 and 3, an eighth-note melody."""
    rng = np.random.default_rng(seed)
    notes = []
    for b in range(bars * 3):
        t = t0 + b * beat
        if b % 3 == 0:
            notes.append([36 + rng.integers(0, 12), t, 3 * beat * 0.9, 95])
        else:
            notes += [[60 + rng.integers(0, 3), t, beat * 0.4, 55], [64 + rng.integers(0, 3), t, beat * 0.4, 55]]
        notes.append([72 + rng.integers(0, 12), t, beat / 2, 70 if b % 3 == 0 else 60])
        notes.append([72 + rng.integers(0, 12), t + beat / 2, beat / 2, 60])
    return np.array(sorted(notes, key=lambda r: r[1]), float)


def test_the_phrase_reads_a_waltz_while_the_windows_change_level():
    """The worker's phrase state over windows whose beats come at the beat, twice it, half it and 3:2 (as PM2S's
    do): the held grid stays on the beat and the phrase network reads 3/4 (needs metre_weights.json's "phrase")."""
    model = M.MetreModel()
    if not model.pnet:
        pytest.skip("metre_weights.json has no phrase network")
    notes = _waltz()
    beats = 0.3 + 0.5 * np.arange(int((notes[-1, 1] - 0.3) / 0.5) + 1)
    ph = M.Phrase()
    res = None
    for k, (w, now) in enumerate(_windows(beats, start=4.0)):
        sel = (notes[:, 1] >= now - 12) & (notes[:, 1] <= now)
        ns, t0 = notes[sel].copy(), float(notes[sel][0, 1])
        ids = [int(i) for i in np.nonzero(sel)[0]]
        ns[:, 1] -= t0
        n = len(ns)
        down = (np.abs(((ns[:, 1] + t0 - 0.3) / 1.5) % 1.0) < 0.02).astype(float)
        ph.add(ns, ids, t0, {"down": down, "ts": np.full(n, 0.3)})
        ph.grid.update(w, now)
        res = M.phrase_metre(model, ph, now)
    assert abs(ph.grid.P - 0.5) < 0.03, ph.grid.P
    m, written, info = res
    assert m.beats_per_bar == 3 and not m.compound, (m, info["cls"])
    assert abs(np.median(np.diff(written)) - 0.5) < 0.03
