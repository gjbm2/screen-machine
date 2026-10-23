"""numba kernels for the streaming realism stages (piano_render/dsp/streaming.py); single-threaded, compiled once and
cached beside this file (the offline kernels are in _kernels.py)."""

from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def limiter_gain(req, L, a_rel, state, ring, out):
    """The limiter's gain for len(out) new signal frames j0 .. j0 + n - 1. req: the required gain (<= 1) of frames
    j0 - L .. j0 + n - 1 (n + L values; the caller keeps the last L for the next call). At frame j: hold =
    min(req of frames j - L .. j), r = hold when it falls, else r moves towards it by a_rel per frame (the release;
    within 1e-9 of 1 it is 1), and out = the mean of the last L values of r, the gain for the signal frame j - L (the
    signal is delayed by L). Every r in that mean is at most req of frame j - L, so the gain is down by the time the
    peak plays. state = [r, ring sum, ring index, ring values below 1]; ring: the last L values of r (start at 1).
    With nothing below 1 the sum is reset to exactly L, so an idle limiter is exactly a gain of 1, frame by frame
    (the result never depends on how the frames were split into calls)."""
    n = out.shape[0]
    m = n + L
    W = L + 1
    pre = np.empty(m)                       # van Herk / Gil-Werman sliding minimum over windows of W
    suf = np.empty(m)
    for j in range(m):
        pre[j] = req[j] if j % W == 0 else min(pre[j - 1], req[j])
    for j in range(m - 1, -1, -1):
        suf[j] = req[j] if (j == m - 1 or (j + 1) % W == 0) else min(suf[j + 1], req[j])
    r = state[0]
    acc = state[1]
    pos = int(state[2])
    below = int(state[3])
    for i in range(n):
        h = min(suf[i], pre[i + L])         # min(req[i .. i + L])
        if h < r:
            r = h
        else:
            r += a_rel * (h - r)
            if r > 1.0 - 1e-9:
                r = 1.0
        old = ring[pos]
        if old < 1.0:
            below -= 1
        if r < 1.0:
            below += 1
        acc += r - old
        ring[pos] = r
        pos += 1
        if pos == L:
            pos = 0
        if below == 0:
            acc = float(L)
        g = acc / L
        out[i] = g if g < 1.0 else 1.0
    state[0] = r
    state[1] = acc
    state[2] = pos
    state[3] = below


@njit(cache=True)
def sos_filter(x, sos, zi, out):
    """scipy.signal.sosfilt's arithmetic (direct form II transposed, float64), section by section over the block:
    out (float32) = x (float32, 1-D) through the sections sos (S, 6), state zi (S, 2) float64 carried. The same
    result as sosfilt, bit for bit (no fastmath), without its per-call overhead (~0.1 ms)."""
    n = x.shape[0]
    tmp = np.empty(n, np.float64)
    for i in range(n):
        tmp[i] = x[i]
    for s in range(sos.shape[0]):
        b0 = sos[s, 0]
        b1 = sos[s, 1]
        b2 = sos[s, 2]
        a1 = sos[s, 4]
        a2 = sos[s, 5]
        z0 = zi[s, 0]
        z1 = zi[s, 1]
        for i in range(n):
            xc = tmp[i]
            y = b0 * xc + z0
            z0 = b1 * xc - a1 * y + z1
            z1 = b2 * xc - a2 * y
            tmp[i] = y
        zi[s, 0] = z0
        zi[s, 1] = z1
    for i in range(n):
        out[i] = tmp[i]


@njit(cache=True, fastmath=True)
def fdl_mac_soa(fr, fi, Hr, Hi, j0, K, mode, yl_r, yl_i, yr_r, yr_i):
    """The uniform-partitioned convolution's frequency-domain sum for one partition, on split real/imaginary
    float32 arrays (it vectorises; 3x faster than complex64): Y_c[f] = sum_k X[k partitions back][f] H[k][f].
    fr/fi (2K, 2, F): the last K input partitions' spectra, slot j0 + K - 1 - k = k back (fdl columns L, R);
    Hr/Hi (K, C, F): the IR's partition spectra. mode 0 = true stereo (H columns LL, LR, RL, RR):
    YL = XL HLL + XR HRL, YR = XL HLR + XR HRR; 1 = mono in (fdl column 0 = the mono mix; H columns L, R);
    2 = per channel (H column 0 on each of L, R)."""
    F = yl_r.shape[0]
    for f in range(F):
        yl_r[f] = 0.0
        yl_i[f] = 0.0
        yr_r[f] = 0.0
        yr_i[f] = 0.0
    for k in range(K):
        s = j0 + K - 1 - k
        ar = fr[s, 0]
        ai = fi[s, 0]
        br = fr[s, 1]
        bi = fi[s, 1]
        if mode == 0:
            h0r = Hr[k, 0]
            h0i = Hi[k, 0]
            h1r = Hr[k, 1]
            h1i = Hi[k, 1]
            h2r = Hr[k, 2]
            h2i = Hi[k, 2]
            h3r = Hr[k, 3]
            h3i = Hi[k, 3]
            for f in range(F):
                xr = ar[f]
                xi = ai[f]
                zr = br[f]
                zi = bi[f]
                yl_r[f] += xr * h0r[f] - xi * h0i[f] + zr * h2r[f] - zi * h2i[f]
                yl_i[f] += xr * h0i[f] + xi * h0r[f] + zr * h2i[f] + zi * h2r[f]
                yr_r[f] += xr * h1r[f] - xi * h1i[f] + zr * h3r[f] - zi * h3i[f]
                yr_i[f] += xr * h1i[f] + xi * h1r[f] + zr * h3i[f] + zi * h3r[f]
        elif mode == 1:
            h0r = Hr[k, 0]
            h0i = Hi[k, 0]
            h1r = Hr[k, 1]
            h1i = Hi[k, 1]
            for f in range(F):
                xr = ar[f]
                xi = ai[f]
                yl_r[f] += xr * h0r[f] - xi * h0i[f]
                yl_i[f] += xr * h0i[f] + xi * h0r[f]
                yr_r[f] += xr * h1r[f] - xi * h1i[f]
                yr_i[f] += xr * h1i[f] + xi * h1r[f]
        else:
            h0r = Hr[k, 0]
            h0i = Hi[k, 0]
            for f in range(F):
                xr = ar[f]
                xi = ai[f]
                zr = br[f]
                zi = bi[f]
                yl_r[f] += xr * h0r[f] - xi * h0i[f]
                yl_i[f] += xr * h0i[f] + xi * h0r[f]
                yr_r[f] += zr * h0r[f] - zi * h0i[f]
                yr_i[f] += zr * h0i[f] + zi * h0r[f]


@njit(cache=True, fastmath=True)
def true_peak_req(win, phases, ceiling, req):
    """The limiter's required gain per frame: req[i] = min(1, ceiling / tp(i)), tp(i) = the largest |value| of the
    two channels at the frame and at the 1/4, 2/4, 3/4 points after it (master.true_peak's 4x interpolation filter
    in polyphase form, phases (4, T), T = 2 H + 1): value_p(i) = sum_t phases[p, t] win[i + T - 1 - t].
    win: (len(req) + T - 1, 2) float32, frames from H before the first."""
    n = req.shape[0]
    P = phases.shape[0]
    T = phases.shape[1]
    tp = np.zeros(n)
    col = np.empty(n + T - 1)
    acc = np.empty(n)
    for c in range(2):
        for j in range(n + T - 1):
            col[j] = win[j, c]
        for p in range(P):
            for i in range(n):
                acc[i] = 0.0
            for t in range(T):
                h = phases[p, t]
                o = T - 1 - t
                for i in range(n):
                    acc[i] += h * col[i + o]
            for i in range(n):
                a = abs(acc[i])
                if a > tp[i]:
                    tp[i] = a
    for i in range(n):
        req[i] = 1.0 if tp[i] <= ceiling else ceiling / tp[i]
