"""numba kernels for the resonator bank (single-threaded; compiled once and cached beside this file)."""

from __future__ import annotations

import numpy as np
from numba import njit

DB_PER_NEPER = 8.685889638065035


@njit(cache=True)
def own_gate(struck, free, lift, dt, tau_free, tau_damp, thr_hi, thr_lo, uc, uc_self):
    """(n, K) float32 input gate per block and key: 0 while the key's own sound is still up (a level model reset
    to 0 dB at each strike, decaying at tau_free while the string is free and at
    tau_damp + (tau_free - tau_damp) * lift^2 while damped), opening linearly from thr_hi down to thr_lo dB;
    never below uc_self * una_corda (the soft pedal's unstruck string, r4 item 8)."""
    n, K = struck.shape
    gate = np.empty((n, K), np.float32)
    lvl = np.full(K, -300.0)
    prev = np.full(K, np.inf)
    span = thr_hi - thr_lo
    for i in range(n):
        d2 = lift[i] * lift[i]
        selfg = uc_self * uc[i]
        for k in range(K):
            s = struck[i, k]
            if s < prev[k]:
                lvl[k] = 0.0
            else:
                if free[i, k]:
                    tau = tau_free[k]
                else:
                    tau = tau_damp[k] + (tau_free[k] - tau_damp[k]) * d2
                lvl[k] -= DB_PER_NEPER * dt / tau
                if lvl[k] < -300.0:
                    lvl[k] = -300.0
            prev[k] = s
            g = (thr_hi - lvl[k]) / span
            if g < 0.0:
                g = 0.0
            elif g > 1.0:
                g = 1.0
            if selfg > g:
                g = selfg
            gate[i, k] = g
    return gate


@njit(cache=True, fastmath=True)
def bank_run(x, b0, b1, B, lift_q, free, gate, live, kstart, kend, rfree, rtab, cw, sw, g, owl, owr,
             yr, yi, dormant, outl, outr, eps):
    """Run blocks b0..b1 of the bank over x (bank rate, float32), writing outl/outr; the state (yr, yi, dormant)
    carries over between calls. Each resonator: y <- r e^{jw} y + g gate x, output Re(y) panned (owl, owr). r per
    block: rfree while the key's string is free, else rtab[r, lift_q] (the half-pedal decay). A key neither free nor
    under a lifted damper gets no input, and once its ringing is below eps it is skipped until it is live again.
    Each block gathers the running resonators into compact local arrays (a plain loop LLVM can vectorise), runs
    four samples per pass over them, then scatters the state back: about 0.3 ns per resonator-sample on this box
    (5.7 ns for a straightforward loop over index ranges). Returns the number of resonator-samples computed."""
    K = kstart.shape[0]
    R = cw.shape[0]
    n_tot = x.shape[0]
    cpr = np.zeros(R, np.float32)
    cpi = np.zeros(R, np.float32)
    cge = np.zeros(R, np.float32)
    cwl = np.zeros(R, np.float32)
    cwr = np.zeros(R, np.float32)
    cyr = np.zeros(R, np.float32)
    cyi = np.zeros(R, np.float32)
    idx = np.zeros(R, np.int64)
    work = 0
    for b in range(b0, b1):
        s0 = b * B
        if s0 >= n_tot:
            break
        s1 = min(s0 + B, n_tot)
        q = lift_q[b]
        m = 0
        for k in range(K):
            lv = live[b, k]
            if (not lv) and dormant[k]:
                continue
            gin = gate[b, k] if lv else np.float32(0.0)
            fr = free[b, k]
            for r in range(kstart[k], kend[k]):
                rr = rfree[r] if fr else rtab[r, q]
                cpr[m] = rr * cw[r]
                cpi[m] = rr * sw[r]
                cge[m] = g[r] * gin
                cwl[m] = owl[r]
                cwr[m] = owr[r]
                cyr[m] = yr[r]
                cyi[m] = yi[r]
                idx[m] = r
                m += 1
        work += m * (s1 - s0)
        n = s0
        while n + 4 <= s1:              # four samples per pass over the resonators (half the memory traffic)
            x0 = x[n]
            x1 = x[n + 1]
            x2 = x[n + 2]
            x3 = x[n + 3]
            l0 = np.float32(0.0)
            l1 = np.float32(0.0)
            l2 = np.float32(0.0)
            l3 = np.float32(0.0)
            r0 = np.float32(0.0)
            r1 = np.float32(0.0)
            r2 = np.float32(0.0)
            r3 = np.float32(0.0)
            for i in range(m):
                a = cyr[i]
                c = cyi[i]
                p = cpr[i]
                pq = cpi[i]
                gg = cge[i]
                wl = cwl[i]
                wr = cwr[i]
                a1 = p * a - pq * c + gg * x0
                c1 = p * c + pq * a
                a2 = p * a1 - pq * c1 + gg * x1
                c2 = p * c1 + pq * a1
                a3 = p * a2 - pq * c2 + gg * x2
                c3 = p * c2 + pq * a2
                a4 = p * a3 - pq * c3 + gg * x3
                c4 = p * c3 + pq * a3
                cyr[i] = a4
                cyi[i] = c4
                l0 += wl * a1
                l1 += wl * a2
                l2 += wl * a3
                l3 += wl * a4
                r0 += wr * a1
                r1 += wr * a2
                r2 += wr * a3
                r3 += wr * a4
            outl[n] = l0
            outl[n + 1] = l1
            outl[n + 2] = l2
            outl[n + 3] = l3
            outr[n] = r0
            outr[n + 1] = r1
            outr[n + 2] = r2
            outr[n + 3] = r3
            n += 4
        while n < s1:
            xn = x[n]
            al = np.float32(0.0)
            ar = np.float32(0.0)
            for i in range(m):
                a = cyr[i]
                c = cyi[i]
                na = cpr[i] * a - cpi[i] * c + cge[i] * xn
                cyi[i] = cpr[i] * c + cpi[i] * a
                cyr[i] = na
                al += cwl[i] * na
                ar += cwr[i] * na
            outl[n] = al
            outr[n] = ar
            n += 1
        for i in range(m):
            a = cyr[i]
            c = cyi[i]
            if abs(a) < 1e-20 and abs(c) < 1e-20:     # flush before float32 denormals (100x slower) appear
                a = 0.0
                c = 0.0
            yr[idx[i]] = a
            yi[idx[i]] = c
        for k in range(K):
            lv = live[b, k]
            if dormant[k] and not lv:
                continue
            e = np.float32(0.0)
            for r in range(kstart[k], kend[k]):
                e += yr[r] * yr[r] + yi[r] * yi[r]
            limit = eps if not lv else eps * np.float32(1e-12)
            if e < limit:
                for r in range(kstart[k], kend[k]):
                    yr[r] = 0.0
                    yi[r] = 0.0
                dormant[k] = not lv
            else:
                dormant[k] = False
    return work


@njit(cache=True, fastmath=True)
def fir_down(x, h, D, delay, n_out):
    """Decimate 1-D float32 x by D with the FIR h (a centred low-pass at the input rate):
    y[o] = sum_k h[k] x[o D + delay - k]."""
    n = x.shape[0]
    L = h.shape[0]
    y = np.zeros(n_out, np.float32)
    for o in range(n_out):
        m = o * D + delay
        acc = np.float32(0.0)
        if m - L + 1 >= 0 and m < n:
            for k in range(L):
                acc += h[k] * x[m - k]
        else:
            for k in range(L):
                i = m - k
                if 0 <= i < n:
                    acc += h[k] * x[i]
        y[o] = acc
    return y


@njit(cache=True, fastmath=True)
def fir_up2(x, h, U, delay, n_out):
    """Upsample (n, 2) float32 x by U with the FIR h (a centred low-pass at the output rate, gain U):
    y[o] = sum_k h[k] xs[o + delay - k], xs = x zero-stuffed; run as U polyphase rows of T = ceil(L / U) taps."""
    n = x.shape[0]
    L = h.shape[0]
    T = (L + U - 1) // U
    hp = np.zeros((U, T), np.float32)
    for k in range(L):
        hp[k % U, k // U] = h[k]
    y = np.zeros((n_out, 2), np.float32)
    for o in range(n_out):
        m = o + delay
        ph = m % U
        i0 = m // U
        a0 = np.float32(0.0)
        a1 = np.float32(0.0)
        if i0 - T + 1 >= 0 and i0 < n:
            for j in range(T):
                c = hp[ph, j]
                a0 += c * x[i0 - j, 0]
                a1 += c * x[i0 - j, 1]
        else:
            for j in range(T):
                i = i0 - j
                if 0 <= i < n:
                    c = hp[ph, j]
                    a0 += c * x[i, 0]
                    a1 += c * x[i, 1]
        y[o, 0] = a0
        y[o, 1] = a1
    return y


@njit(cache=True, fastmath=True)
def fir_down2(x, h, D, delay, n_out):
    """fir_down on the mean of the two columns of (n, 2) float32 x."""
    n = x.shape[0]
    L = h.shape[0]
    y = np.zeros(n_out, np.float32)
    for o in range(n_out):
        m = o * D + delay
        acc = np.float32(0.0)
        if m - L + 1 >= 0 and m < n:
            for k in range(L):
                acc += h[k] * (x[m - k, 0] + x[m - k, 1])
        else:
            for k in range(L):
                i = m - k
                if 0 <= i < n:
                    acc += h[k] * (x[i, 0] + x[i, 1])
        y[o] = np.float32(0.5) * acc
    return y


@njit(cache=True, fastmath=True)
def fir_up2_add(xl, xr, h, U, delay, out, n_out):
    """out[:n_out] += the two 1-D float32 signals xl, xr upsampled by U (fir_up2's filter, gain folded into h)."""
    n = xl.shape[0]
    L = h.shape[0]
    T = (L + U - 1) // U
    hp = np.zeros((U, T), np.float32)
    for k in range(L):
        hp[k % U, k // U] = h[k]
    for o in range(n_out):
        m = o + delay
        ph = m % U
        i0 = m // U
        a0 = np.float32(0.0)
        a1 = np.float32(0.0)
        if i0 - T + 1 >= 0 and i0 < n:
            for j in range(T):
                c = hp[ph, j]
                a0 += c * xl[i0 - j]
                a1 += c * xr[i0 - j]
        else:
            for j in range(T):
                i = i0 - j
                if 0 <= i < n:
                    c = hp[ph, j]
                    a0 += c * xl[i]
                    a1 += c * xr[i]
        out[o, 0] += a0
        out[o, 1] += a1
