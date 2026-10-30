"""keyfield (stream_service/plugins/_lib/keyfield.py): the shared key-field timeline and the host reference of its
GPU bake, CPU only; the CUDA is compiled GPU-free with NVRTC."""
from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pytest

from stream_service.plugins._lib import keyfield as KF


def chord(pcs, level: float = 1.0, octave: int = 60) -> np.ndarray:
    notes = np.zeros((128, 4), np.float32)
    for pc in pcs:
        notes[octave + pc, 0] = level
    return notes


def frame(notes=None, onsets=(), t_ms=None):
    f = SimpleNamespace(notes=np.zeros((128, 4)) if notes is None else notes, onsets=list(onsets), key=None, mode=None)
    if t_ms is not None:
        f.t_ms = t_ms
    return f


def play(tl, snd, pcs, seconds: float, dt: float = 0.25, strike: bool = True, gap: float = 12.0) -> None:
    for i in range(int(round(seconds / dt))):
        snd.frame = frame(chord(pcs), [(60 + pcs[0], 0.8, 0.0)] if strike and i == 0 else ())
        tl.update(snd, dt, gap)


# ------------------------------------------------------------------------------------------------------- timeline
def test_binning_and_dedupe():
    tl = KF.KeyTimeline(seed=0)
    snd = SimpleNamespace(frame=None)
    notes = chord((0, 4, 7))
    e = np.zeros(12)
    e[[0, 4, 7]] = 1.0
    snd.frame = frame(notes, [(60, 1.0, 0.0), (64, 0.5, 0.0)])
    tl.update(snd, 0.125)
    assert tl.started and not tl.example and tl.pieces == 1 and tl.n == 0
    want = e * 0.125
    want[0] += KF.ONSET_W * 1.0
    want[4] += KF.ONSET_W * 0.5
    np.testing.assert_allclose(tl.acc, want)
    v = tl.version
    tl.update(snd, 0.125)                             # another plug-in on the same frame: nothing
    np.testing.assert_allclose(tl.acc, want)
    assert tl.tbin == 0.125
    tl.update(snd, 0.25)                              # a caller with a longer dt adds the difference, no strikes again
    want += e * 0.125
    np.testing.assert_allclose(tl.acc, want)
    assert tl.tbin == 0.25
    np.testing.assert_allclose(tl.open_row(), tl.S[0] + want / 0.5)      # the open bin, as keyscape extrapolates it
    assert tl.now() == pytest.approx(0.5)
    for _ in range(2):
        snd.frame = frame(notes)
        tl.update(snd, 0.125)
    assert tl.n == 1 and tl.tbin == 0.0
    assert tl.version == v + 1 and tl.dirty_from == 1
    want_bin = e * 0.5
    want_bin[0] += 0.3
    want_bin[4] += 0.15
    np.testing.assert_allclose(tl.S[1] - tl.S[0], want_bin)
    assert tl.dirty_since(v) == 1 and tl.dirty_since(tl.version) == KF.ROWS and tl.dirty_since(-1) == 0
    # a frame refreshed in place (SoundFrame) counts again only when its t_ms moves
    f = frame(notes, t_ms=1000.0)
    snd.frame = f
    tl.update(snd, 0.125)
    tl.update(snd, 0.125)
    assert tl.tbin == 0.125
    f.t_ms = 1125.0
    f.onsets = []
    tl.update(snd, 0.125)
    assert tl.tbin == 0.25
    # a long skip closes at most MAX_BINS bins
    n0 = tl.n
    snd.frame = frame(notes, t_ms=1250.0)
    tl.update(snd, 100.0)
    assert tl.n == n0 + KF.MAX_BINS
    assert np.isfinite(tl.S[:tl.n + 1]).all()


def test_shared_timeline_per_sound():
    a, b = SimpleNamespace(frame=None), SimpleNamespace(frame=None)
    assert KF.KeyTimeline.shared(a) is KF.KeyTimeline.shared(a)
    assert KF.KeyTimeline.shared(a) is not KF.KeyTimeline.shared(b)
    assert KF.KeyTimeline.shared(None) is not KF.KeyTimeline.shared(None)

    class Snd:                                        # the real Sound takes weak references
        frame = None
    s = Snd()
    tl = KF.KeyTimeline.shared(s, seed=5)
    assert KF.KeyTimeline.shared(s) is tl and tl.seed == 5
    # two plug-ins updating one shared timeline each frame advance it once
    for i in range(4):
        s.frame = frame(chord((0, 4, 7)), [(60, 1.0, 0.0)] if i == 0 else ())
        KF.KeyTimeline.shared(s).update(s, 0.25)
        KF.KeyTimeline.shared(s).update(s, 0.25)
    assert tl.n == 2 and tl.tbin == 0.0


def test_example_cold_start_cleared_by_first_note():
    tl = KF.KeyTimeline(seed=3)
    ex = KF.example_bins(3)
    assert tl.example and not tl.started and tl.n == len(ex) == 120 and tl.now() == 120.0
    np.testing.assert_allclose(np.diff(tl.S[:121], axis=0), ex)
    k, clar = tl.summit()
    assert k == 0 and clar > 0                        # the example is in C major
    snd = SimpleNamespace(frame=None)
    v = tl.version
    for _ in range(10):                               # silence, and held keys with no strike, keep the example
        snd.frame = frame()
        tl.update(snd, 0.1)
    snd.frame = frame(chord((0, 4, 7)))
    tl.update(snd, 0.1)
    assert tl.example and tl.n == 120 and tl.version == v
    snd.frame = frame(chord((2, 6, 9)), [(62, 0.8, 0.0)])
    tl.update(snd, 0.1)
    assert not tl.example and tl.started and tl.n == 0 and tl.pieces == 1
    assert not tl.S[1:].any() and tl.dirty_since(v) == 0


def test_piece_gap_freezes_and_next_note_starts_a_new_piece():
    tl = KF.KeyTimeline()
    snd = SimpleNamespace(frame=None)
    play(tl, snd, (0, 4, 7), 4.0)
    n_play = tl.n
    assert n_play == 8 and not tl.frozen
    n_hold = None
    for i in range(60):                               # 15 s of silence, piece_gap 12 s
        snd.frame = frame()
        tl.update(snd, 0.25, 12.0)
        if i == 15:
            n_hold = tl.n                             # 4 s in: the clock stopped after QUIET_HOLD
        if i == 40:
            assert not tl.frozen                      # 10.25 s
    assert tl.frozen and tl.n == n_hold
    assert 0 < n_hold - n_play <= KF.QUIET_HOLD / KF.BIN_S + 1
    snd.frame = frame(chord((5, 9, 0)))               # keys held with no strike do not start a piece
    tl.update(snd, 0.25, 12.0)
    assert tl.frozen and tl.pieces == 1
    snd.frame = frame(chord((7, 11, 2)), [(67, 0.7, 0.0)])
    tl.update(snd, 0.25, 12.0)
    assert not tl.frozen and tl.started and tl.pieces == 2 and tl.n == 0 and tl.tbin == 0.25

    # the frames' clock jumping past piece_gap (no key-field scene heard it) freezes the piece too
    tl2 = KF.KeyTimeline()
    snd.frame = frame(chord((0, 4, 7)), [(60, 1.0, 0.0)], t_ms=0.0)
    tl2.update(snd, 0.1, 12.0)
    snd.frame = frame(chord((0, 4, 7)), t_ms=100.0)
    tl2.update(snd, 0.1, 12.0)
    assert not tl2.frozen
    snd.frame = frame(chord((0, 4, 7)), t_ms=30100.0)
    tl2.update(snd, 0.1, 12.0)
    assert tl2.frozen and tl2.pieces == 1


def test_rebase_keeps_windows_content():
    tl = KF.KeyTimeline()
    prog = [(0, 4, 7), (5, 9, 0), (7, 11, 2), (9, 0, 4), (2, 6, 9), (4, 8, 11), (10, 2, 5)]
    snd = SimpleNamespace(frame=frame(chord(prog[0]), [(60, 1.0, 0.0)]))
    tl.update(snd, 0.0)
    i = 0
    while tl.n < KF.CAP + KF.DROP - KF.MAX_BINS:
        i += 1
        snd.frame = frame(chord(prog[(i // 3) % len(prog)]))
        tl.update(snd, 4.0)                           # 8 bins a frame
    nb, v = tl.n, tl.version
    Sb = tl.S[:nb + 1].copy()
    snd.frame = frame(chord(prog[0]))
    tl.update(snd, 4.0)
    assert tl.n == nb + KF.MAX_BINS - KF.DROP         # re-based on the way
    assert tl.dirty_since(v) == 0 and tl.dirty_from == 0
    j = np.arange(0, nb - KF.DROP + 1)
    np.testing.assert_allclose(tl.S[j], Sb[j + KF.DROP] - Sb[KF.DROP], rtol=1e-12, atol=1e-9)
    rng = np.random.default_rng(1)
    L = rng.uniform(KF.L_MIN, 900.0, 200)
    s = rng.uniform(0.0, nb - KF.DROP - L)
    e = s + L
    ids_a, clar_a, fog_a = KF.windows_keys(tl.S, tl.n, s, e, L)
    ids_b, clar_b, fog_b = KF.windows_keys(Sb, nb, s + KF.DROP, e + KF.DROP, L)
    assert (ids_a == ids_b).all() and (ids_a >= 0).sum() > 100
    np.testing.assert_allclose(clar_a, clar_b, atol=1e-9)
    np.testing.assert_allclose(fog_a, fog_b, atol=1e-9)
    org, sp, now, lo = KF.view_geometry(tl)
    assert sp == KF.CAP and lo == org == now - KF.CAP and lo >= 0.0


# ----------------------------------------------------------------------------------------------------------- bake
def _grid(tl, fw, fh, lr, span=None):
    org, sp, now, lo = KF.view_geometry(tl, span)
    u = (np.arange(fw) + 0.5) / fw
    v = (np.arange(fh) + 0.5) / fh
    V, U = np.meshgrid(v, u, indexing="ij")
    return org + U * sp, KF.window_len(V, sp, lr)


def test_bake_numpy_centred_matches_windows_keys():
    tl = KF.KeyTimeline()
    fw, fh, lr = 48, 24, 0.85
    A, B, C = KF.bake_numpy(tl, KF.CENTRED, None, lr, fw, fh)
    assert A.shape == B.shape == C.shape == (fh, fw, 4) and A.dtype == np.float32
    assert np.isfinite(A).all() and np.isfinite(B).all() and np.isfinite(C).all()
    c, L = _grid(tl, fw, fh, lr)
    inside = A[..., 3] >= 0.999
    assert inside.sum() > 200
    ids, clar, fog = KF.windows_keys(tl.S, tl.n, (c - 0.5 * L)[inside], (c + 0.5 * L)[inside], L[inside])
    assert (A[..., 0][inside] == ids).all()
    np.testing.assert_allclose(A[..., 1][inside], clar, atol=1e-5)
    np.testing.assert_allclose(A[..., 2][inside], fog, atol=1e-5)
    keyed = ids[ids >= 0]
    assert keyed.size > 100 and {0, 7} <= set(keyed.tolist())         # C, and the passage in G
    # Sapp's triangle: the top corners are outside, the middle of the base inside
    assert A[-1, 0, 3] == 0.0 and A[-1, -1, 3] == 0.0 and A[0, fw // 2, 3] == 1.0
    assert ((B[..., :3] >= 0) & (B[..., :3] <= 1)).all() and ((C[..., 2:] >= 0) & (C[..., 2:] <= 1)).all()
    ph = np.hypot(C[..., 0], C[..., 1])
    assert np.allclose(ph[ph > 0], 1.0, atol=1e-5)
    np.testing.assert_allclose(1.0 - A[..., 2][inside & (A[..., 1] > 0)],
                               (lambda d: d * d * (3 - 2 * d))(C[..., 3][inside & (A[..., 1] > 0)]), atol=1e-5)


def test_open_bin_counts_as_in_keyscape():
    tl = KF.KeyTimeline()
    snd = SimpleNamespace(frame=None)
    play(tl, snd, (0, 4, 7), 6.0)
    play(tl, snd, (2, 7, 11), 0.375, dt=0.125, strike=False)           # 3/4 into an open bin
    assert tl.tbin == 0.375
    P = KF._prefix_np(tl.S, tl.n, 1, tl.open_row(), np.array([tl.now()]))
    np.testing.assert_allclose(P[0], tl.S[tl.n] + tl.acc)
    A, B, C = KF.bake_numpy(tl, KF.TRAILING, None, 1.0, 64, 16)
    assert np.isfinite(A).all() and np.isfinite(B).all() and np.isfinite(C).all()


def test_trailing_is_a_rectangle():
    tl = KF.KeyTimeline()                             # the example: the view is the whole piece
    A, _, _ = KF.bake_numpy(tl, KF.TRAILING, None, 1.0, 48, 24)
    assert (A[..., 3] >= 0.999).all()
    assert (A[-1, :, 0] >= 0).sum() > 30              # long windows cut at the start still know their key
    snd = SimpleNamespace(frame=None)
    play(tl, snd, (0, 4, 7), 5.0)                     # a live piece shorter than V_MIN: 10 bins of a 40-bin view
    org, sp, now, lo = KF.view_geometry(tl)
    assert sp == KF.V_MIN and org == lo == 0.0
    fw, fh = 40, 8
    A, _, _ = KF.bake_numpy(tl, KF.TRAILING, None, 1.0, fw, fh)
    assert (A[..., 3] == A[:1, :, 3]).all()           # every row alike: a rectangle
    pos = org + (np.arange(fw) + 0.5) / fw * sp
    assert (A[0, pos < now - 1, 3] == 1.0).all() and (A[0, pos > now + 1, 3] == 0.0).all()
    Ac, _, _ = KF.bake_numpy(tl, KF.CENTRED, None, 1.0, fw, fh)
    rows = (Ac[..., 3] >= 0.999).sum(axis=1)
    assert rows[0] > rows[-1] and (np.diff(rows) <= 0).all()            # CENTRED: a triangle
    # a zoomed view ending at now
    Az, _, _ = KF.bake_numpy(tl, KF.TRAILING, 6.0, 1.0, 12, 4)
    org, sp, now, lo = KF.view_geometry(tl, 6.0)
    assert sp == 6.0 and org == pytest.approx(now - 6.0) and (Az[..., 3] >= 0.999).all()


def test_home_is_zero_on_the_summit_key():
    tl = KF.KeyTimeline()
    sk, sc = tl.summit()
    assert sk == 0 and sc > 0
    A, B, _ = KF.bake_numpy(tl, KF.CENTRED, None, 0.85, 96, 48)
    key, hm = A[..., 0], B[..., 3]
    assert (key == sk).any() and (hm[key == sk] == 0.0).all()
    assert (hm[key < 0] == 1.0).all()
    for k in np.unique(key[key >= 0]).astype(int):
        np.testing.assert_allclose(hm[key == k], KF.home(k, sk), atol=1e-6)
    assert (key == 7).any() and np.allclose(hm[key == 7], 1.0 / 6.0)


def test_circle_of_fifths_angle_and_home():
    step = 2.0 * math.pi / 12.0
    for k, pos in ((0, 0), (7, 1), (2, 2), (9, 3), (5, 11), (10, 10), (6, 6),
                   (21, 0), (16, 1), (12, 9), (13, 4)):              # a minor at C, e minor at G, c at Eb, c# at E
        assert KF.fifths_pos(k) == pos
        assert KF.key_angle(k) == pytest.approx(pos * step)
    assert KF.key_angle(-1) == 0.0
    assert KF.home(0, 0) == 0.0 and KF.home(7, 0) == pytest.approx(1 / 6) and KF.home(5, 0) == pytest.approx(1 / 6)
    assert KF.home(2, 0) == pytest.approx(2 / 6) and KF.home(6, 0) == 1.0 and KF.home(11, 5) == 1.0
    assert KF.home(21, 0) == 0.0 and KF.home(12, 3) == 0.0 and KF.home(16, 7) == 0.0
    assert KF.home(-1, 0) == 1.0 and KF.home(0, -1) == 1.0
    vals = {KF.home(a, b) for a in range(24) for b in range(24)}
    assert vals <= {d / 6.0 for d in range(7)}
    assert all(KF.home(a, b) == KF.home(b, a) for a in range(24) for b in range(24))


def test_host_colour_and_labels():
    assert KF.label(0) == "C" and KF.label(13) == "c♯" and KF.label(-1) == ""
    c, a = KF.key_rgb(0), KF.key_rgb(21)
    assert all(0.0 <= x <= 1.0 for x in c + a) and sum(c) > sum(a)                # major light, minor deep
    assert KF.key_rgb(-1) == KF.key_rgb(None)


# ---------------------------------------------------------------------------------------------- CUDA, GPU-free
def _nvrtc():
    try:
        from cupy_backends.cuda.libs import nvrtc
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no NVRTC: {exc}")

    def check(src, opts):
        prog = nvrtc.createProgram(src, "kf.cu", [], [])
        try:
            nvrtc.compileProgram(prog, ["-arch=compute_61", *[o for o in opts if not o.startswith("-arch")]])
        except Exception:  # noqa: BLE001
            return nvrtc.getProgramLog(prog)[:2000]
        return None
    return check


def test_cuda_compiles_gpu_free():
    check = _nvrtc()
    err = check(KF._SRC, KF._OPTS)
    assert err is None, err
    from stream_service.plugins._lib.cuda import HELPERS
    src = HELPERS + KF.KEYFIELD_CUDA + r"""
extern "C" __global__ void use_kf(float* o, const float4* A, const float4* B, const float4* C, int fw, int fh) {
    const float4 a = kf_tex_nearest(A, fw, fh, 0.3f, 0.7f);
    const float4 c = kf_tex(C, fw, fh, 0.3f, 0.7f);
    const float3 k = kf_key_rgb(a.x, a.y, 15.f);
    const float3 w = kf_wave_rgb(c, 0.f);
    const float3 m = kf_oklab_srgb(kf_mist(kf_key_lab(a.x, a.y, 0.f), a.z));
    o[0] = k.x + w.y + m.z + kf_key_angle(a.x) + kf_home(a.x, 0.f) + kf_tex(B, fw, fh, 0.5f, 0.5f).w;
}
"""
    err = check(src, KF._OPTS)
    assert err is None, err
