"""Confetti burst: the old picture bursts into thousands of two-sided paper chips.  Each chip's front is the patch
of the old picture it was cut from; its back is a palette (or metallic foil) colour.  Chips tumble in true 3D
perspective about their own spin axes, flutter side to side in a drag-limited fall and flash specular glints (with
small bloomed sparkles) as they turn, revealing the new picture behind them.  ``direction: rain`` assembles the
new picture from confetti raining in and settling into place; ``swap`` does both at once.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive
(Technique, written from scratch: the frame is cut into a grid of chips (``chip_size`` in 1080-line px) plus an
optional cannon of palette-only chips (``count``).  Each chip's pose is a closed-form function of progress: a
launch impulse (outward from ``burst_x/y`` and towards the camera, scaled by ``force``) relaxing under linear drag
to a per-chip terminal fall velocity that is solved so the chip leaves the frame before the transition ends; a
flutter sway + rock that grows as the chip slows; and a spin about a random axis.  Rain-in uses the mirrored law
with a soft landing (spin, sway and depth all ease to zero exactly at the chip's home cell).  Rendering is an
opaque chip rasteriser: one warp per flying chip ray-casts its quad into a 64-bit (depth | chip id) atomicMin
depth buffer at ``render_scale``; a full-resolution resolve then re-intersects the pixel ray with the (up to 4)
candidate chips from the nearest depth-buffer texels, so chip edges are exact and anti-aliased at 4K, composites
the two nearest chips over the picture plane (home cells show the old picture exactly; vacated cells reveal the new
one), texture-maps chip fronts from the picture, shades backs with the shared cosine palette (``pal()`` drifting
with progress) and a Blinn-Phong glint whose sharpness and strength follow ``metallic``.  Glint sparkles are
deposited with splat3d's ``sp_splat`` from the same fused kernel and bloomed by ``Splat3D.resolve`` (screen blend).
Endpoints are early-out copies.)

Binder: style wild; 3-5 s on a drop or a chorus entry, launching on a downbeat.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num
from stream_service.plugins._lib.splat3d import SPLAT3D_OPTIONS, SPLAT3D_SRC, Splat3D
from stream_service.plugins._lib.upsample import internal_size

NR = 16            # random floats per chip
CB = 16            # floats per chip in the per-frame chip buffer
NQ = 48            # per-frame scalar parameters
MAX_EXTRA = 20000  # palette-only cannon / rain chips at count = 1
MIN_CHIP = 6.0     # smallest chip_size (1080-line px): sizes the static buffers
CAM_D = 1.7        # camera distance (picture height = 1 world unit): ~33 degree vertical fov

_SRC = SPLAT3D_SRC + HELPERS + PALETTE_CUDA + r"""
#define NR 16
#define CB 16
#define TWO_PI 6.2831853f
// key light (surface -> light), unit: upper left, in front of the picture (z towards the camera)
#define LX (-0.3496f)
#define LY (0.5493f)
#define LZ (0.7590f)
#define ZB_EMPTY 0xFFFFFFFFFFFFFFFFull

struct Chip {
    float3 C, eu, ev;          // centre (world), unit in-plane axes
    float hw, hh, lam, s, idx; // half extents, launch ramp (0 = at rest), texture scale, palette index
    int kind, home;            // 0 = old picture chip, 1 = new picture chip, 2 = palette chip; home cell
};

__device__ __forceinline__ float wrap2pi(float a) { return a - TWO_PI * floorf(a * (1.f / TWO_PI)); }

__device__ __forceinline__ float3 rot_axis(float3 v, float3 k, float c, float s) {      // Rodrigues
    const float3 kxv = sp_cross3(k, v);
    const float kv = sp_dot3(k, v) * (1.f - c);
    return make_float3(v.x * c + kxv.x * s + k.x * kv, v.y * c + kxv.y * s + k.y * kv, v.z * c + kxv.z * s + k.z * kv);
}
__device__ __forceinline__ float3 nrm3(float3 v) { return sp_mul3(v, rsqrtf(fmaxf(sp_dot3(v, v), 1e-20f))); }

// home cell k: world centre + distance from the burst origin normalised to the farthest corner
__device__ __forceinline__ float cell_home(int k, const float* __restrict__ q, float& x0, float& y0) {
    const int cols = (int)q[4], rows = (int)q[5];
    const int c = k % cols, r = k / cols;
    const float A = q[2];
    x0 = (c + 0.5f) * (A / cols) - 0.5f * A;
    y0 = 0.5f - (r + 0.5f) / rows;
    const float dx = x0 - q[16], dy = y0 - q[17];
    return sp_clamp01(sqrtf(dx * dx + dy * dy) / q[18]);
}
__device__ __forceinline__ float burst_pl(float rn, float r0, const float* __restrict__ q) {      // launch progress
    return 0.02f + q[15] * q[23] * (0.8f * rn + 0.2f * r0);
}
__device__ __forceinline__ float rain_pd(float rn, float r0, const float* __restrict__ q) {       // landing progress
    return q[26] - (q[26] - q[25]) * q[15] * (1.f - (0.8f * rn + 0.2f * r0));
}

// Pose of chip `id` at this progress; false if it is not in flight (at home, landed, waiting or gone).
__device__ bool chip_state(int id, const float* __restrict__ R, const float* __restrict__ q, Chip& ch) {
    const float P = q[0], dur = q[1], A = q[2], D = q[3];
    const int cols = (int)q[4], rows = (int)q[5], nimg = (int)q[6], nmax = (int)q[7], nset = (int)q[8];
    const int nex = (int)q[9], mode = (int)q[10];
    const float force = q[11], spinp = q[12], flut = q[13], ox = q[16], oy = q[17];
    int role, row;
    float x0 = 0.f, y0 = 0.f, z0 = 0.f, rn = 0.f, hw0, hh0;
    const float cw = A / cols, chh = 1.f / rows;
    if (id < nset * nimg) {
        const int set = id / nimg, k = id - set * nimg;
        role = (mode == 1 || set == 1) ? 1 : 0;
        row = set * nmax + k;
        rn = cell_home(k, q, x0, y0);
        hw0 = 0.5f * cw; hh0 = 0.5f * chh;
        ch.kind = role; ch.home = k;
    } else {
        const int j = id - nset * nimg;
        if (j >= nex) return false;
        row = 2 * nmax + j;
        role = (mode == 1) ? 3 : 2;             // 2 = cannon chip from the burst origin, 3 = decorative rain
        ch.kind = 2; ch.home = -1;
        const float base = 0.5f * chh * (0.55f + 0.6f * R[(size_t)row * NR + 13]);
        hh0 = base; hw0 = base * (0.6f + 0.8f * R[(size_t)row * NR + 14]);
    }
    const float* r = R + (size_t)row * NR;
    const float om = spinp * (2.5f + 7.f * r[9]) * (r[10] < 0.5f ? -1.f : 1.f);
    const float wf = 4.f + 5.f * r[6];
    const float Af = flut * (0.025f + 0.05f * r[8]);
    const float m = 1.5f * fmaxf(hw0, hh0) + 0.04f;
    const float wind = q[28] + 0.12f * force * (r[5] - 0.5f);
    float px, py, pz, theta, roll, lam, s;
    if (role == 0 || role == 2) {
        // ---- burst: impulse, then linear drag towards a terminal fall that clears the frame by q[22]
        float Pl, v0x, v0y, v0z;
        const float Pe = q[22];
        if (role == 0) {
            Pl = burst_pl(rn, r[0], q);
            const float dx = x0 - ox, dy = y0 - oy;
            float ang = (dx * dx + dy * dy > 1e-8f) ? atan2f(dy, dx) : TWO_PI * r[1];
            ang += (r[1] - 0.5f) * 0.6f;
            const float sp = force * (0.35f + 0.85f * r[2]) * (1.15f - 0.55f * rn);
            v0x = __cosf(wrap2pi(ang)) * sp;
            v0y = __sinf(wrap2pi(ang)) * sp + force * (0.1f + 0.45f * r[3]);
            v0z = force * (0.45f + 0.9f * r[4]) * (1.f - 0.35f * rn);
        } else {
            x0 = ox; y0 = oy; z0 = 0.02f;
            Pl = 0.02f + 0.1f * r[0];
            const float ang = TWO_PI * r[1];
            const float sp = force * (0.6f + 1.8f * r[2]);
            v0x = __cosf(ang) * sp;
            v0y = __sinf(ang) * sp * 0.8f + force * (0.3f + 0.6f * r[3]);
            v0z = force * (0.5f + 1.1f * r[4]);
        }
        if (P < Pl || P >= Pe) return false;
        const float k = 2.0f;
        v0z = fminf(v0z, 0.55f * D * k);
        const float tau = (P - Pl) * dur, L = fmaxf((Pe - Pl) * dur, 0.2f);
        const float E = 1.f - __expf(-k * tau), EL = 1.f - __expf(-k * L);
        const float need = y0 + 0.5f + m + v0y * EL / k;
        const float vt = fminf(fmaxf(need / fmaxf(L - EL / k, 0.1f), 0.18f), 4.f);
        px = x0 + v0x * E / k + wind * tau;
        py = y0 + (v0y + vt) * E / k - vt * tau;
        pz = z0 + v0z * E / k;
        const float g = E * E;
        const float ph = wrap2pi(wf * tau + TWO_PI * r[7]);
        px += Af * __sinf(ph) * g;
        pz += Af * 0.8f * (0.5f + 0.5f * __cosf(ph)) * g;
        roll = flut * 0.6f * __sinf(wrap2pi(ph + 0.8f)) * g;
        theta = om * tau;
        lam = (role == 0) ? sp_smooth(0.f, 0.12f, tau) : 1.f;
        s = (1.f - 0.12f * lam) * (1.f - sp_smooth(0.86f, 1.f, tau / L));
        if (role == 2) s *= sp_smooth(0.f, 0.15f, tau);
    } else {
        // ---- rain: fall from above the frame with a soft landing at the home cell (tau = time to landing)
        float Pd;
        const float Ps = q[24];
        if (role == 1) {
            Pd = rain_pd(rn, r[0], q);
        } else {
            x0 = (r[1] - 0.5f) * A * 1.15f;
            y0 = -0.56f - 0.35f * r[2];
            z0 = 0.45f * D * r[3];
            Pd = q[26] - 0.08f * r[0];
        }
        if (P >= Pd || P < Ps) return false;
        const float t0 = 0.35f;
        const float tau = (Pd - P) * dur, L = fmaxf((Pd - Ps) * dur, 0.2f);
        const float G = 1.f - __expf(-tau / t0), GL = 1.f - __expf(-L / t0);
        const float h = tau - t0 * G, hL = L - t0 * GL;
        const float vt = fminf(fmaxf((0.5f + m - y0) / fmaxf(hL, 0.05f), 0.1f), 5.f);
        px = x0 - wind * h;
        py = y0 + vt * h;
        pz = z0 + ((role == 1) ? fminf(force * (0.2f + 0.5f * r[4]), 0.5f * D) * G * G : 0.f);
        const float g = (role == 1) ? G * G : 1.f;
        const float ph = wrap2pi(wf * tau + TWO_PI * r[7]);
        px += Af * __sinf(ph) * g;
        pz += Af * 0.8f * (0.5f + 0.5f * __cosf(ph)) * g;
        roll = flut * 0.6f * __sinf(wrap2pi(ph + 0.8f)) * g;
        theta = om * ((role == 1) ? h : tau);
        lam = (role == 1) ? sp_smooth(0.f, 0.3f, tau) : 1.f;
        s = 1.f - 0.12f * lam;
    }
    // orientation: spin about the chip's own axis, then a flutter rock about the view axis
    const float phi = TWO_PI * r[11], cz = 2.f * r[12] - 1.f, sz = sqrtf(fmaxf(1.f - cz * cz, 0.f));
    const float3 ax = make_float3(sz * __cosf(phi), sz * __sinf(phi), cz);
    float sth, cth;
    __sincosf(wrap2pi(theta), &sth, &cth);
    float3 eu = rot_axis(make_float3(1.f, 0.f, 0.f), ax, cth, sth);
    float3 ev = rot_axis(make_float3(0.f, 1.f, 0.f), ax, cth, sth);
    float sr, cr;
    __sincosf(roll, &sr, &cr);
    eu = make_float3(eu.x * cr - eu.y * sr, eu.x * sr + eu.y * cr, eu.z);
    ev = make_float3(ev.x * cr - ev.y * sr, ev.x * sr + ev.y * cr, ev.z);
    ch.C = make_float3(px, py, pz);
    ch.eu = eu; ch.ev = ev;
    ch.hw = hw0 * s; ch.hh = hh0 * s;
    ch.lam = lam; ch.s = fmaxf(s, 1e-3f);
    ch.idx = r[15] * q[20] + q[19];
    return s > 1e-3f;
}

// ---- K1: one warp per chip: pose, store, glint sparkle (sp_splat), ray-cast the quad into the depth buffer
extern "C" __global__ void __launch_bounds__(256)
cb_raster(const float* __restrict__ R, const float* __restrict__ q, float* __restrict__ cbuf,
          unsigned long long* __restrict__ zb, float* __restrict__ acc, const int ntot, const int IW, const int IH)
{
    const int id = (int)((blockIdx.x * blockDim.x + threadIdx.x) >> 5);
    const int lane = threadIdx.x & 31;
    if (id >= ntot) return;
    Chip ch;
    if (!chip_state(id, R, q, ch)) return;
    const float D = q[3];
    const float3 n = sp_cross3(ch.eu, ch.ev);
    const float f = IH * D, cx = 0.5f * IW, cy = 0.5f * IH;
    float bx0 = 1e30f, bx1 = -1e30f, by0 = 1e30f, by1 = -1e30f;
    for (int k = 0; k < 4; ++k) {
        const float su = (k & 1) ? ch.hw : -ch.hw, sv = (k & 2) ? ch.hh : -ch.hh;
        const float px = ch.C.x + ch.eu.x * su + ch.ev.x * sv;
        const float py = ch.C.y + ch.eu.y * su + ch.ev.y * sv;
        const float pz = ch.C.z + ch.eu.z * su + ch.ev.z * sv;
        const float dep = D - pz;
        if (dep < 0.08f) return;                                  // flew past the camera
        const float sx = cx + f * px / dep, sy = cy - f * py / dep;
        bx0 = fminf(bx0, sx); bx1 = fmaxf(bx1, sx); by0 = fminf(by0, sy); by1 = fmaxf(by1, sy);
    }
    const int x0 = max((int)floorf(bx0 - 1.5f), 0), x1 = min((int)ceilf(bx1 + 1.5f), IW - 1);
    const int y0 = max((int)floorf(by0 - 1.5f), 0), y1 = min((int)ceilf(by1 + 1.5f), IH - 1);
    if (x0 > x1 || y0 > y1) return;
    if (lane == 0) {
        float* cb = cbuf + (size_t)id * CB;
        cb[0] = ch.C.x; cb[1] = ch.C.y; cb[2] = ch.C.z;
        cb[3] = ch.eu.x; cb[4] = ch.eu.y; cb[5] = ch.eu.z;
        cb[6] = ch.ev.x; cb[7] = ch.ev.y; cb[8] = ch.ev.z;
        cb[9] = ch.hw; cb[10] = ch.hh; cb[11] = ch.lam; cb[12] = ch.s;
        cb[13] = (float)ch.kind; cb[14] = (float)ch.home; cb[15] = ch.idx;
        // specular glint sparkle at the chip centre (bloomed later by Splat3D.resolve)
        if (q[30] > 0.f && ch.lam > 0.f) {
            const float dep = D - ch.C.z;
            const float3 V = nrm3(make_float3(-ch.C.x, -ch.C.y, dep));
            const float vn = sp_dot3(n, V);
            const float3 nf = vn >= 0.f ? n : sp_mul3(n, -1.f);
            const float3 Hh = nrm3(make_float3(LX + V.x, LY + V.y, LZ + V.z));
            const float sp = __powf(fmaxf(sp_dot3(nf, Hh), 0.f), 90.f);
            const bool foil = (ch.kind == 2) || vn < 0.f;
            const float e = q[30] * ch.lam * sp * (foil ? (0.35f + 1.3f * q[14]) : 0.3f);
            if (e > 0.003f) {
                const float sx = cx + f * ch.C.x / dep, sy = cy - f * ch.C.y / dep;
                const float rpx = fmaxf(ch.hw, ch.hh) * f / dep;   // chip half size in accumulator px
                const float rho = fminf(fmaxf(1.1f * rpx, 1.5f), 8.f);
                const float k = 60.f * e * fminf(rpx * (1.f / 6.f), 2.f);
                sp_splat(acc, IW, IH, sx, sy, rho, make_float3(k, 0.93f * k, 0.8f * k), 0.f, false);
            }
        }
    }
    const int bw = min(x1 - x0 + 1, 512), bh = min(y1 - y0 + 1, 512);
    const float3 CC = make_float3(ch.C.x, ch.C.y, ch.C.z - D);   // centre relative to the camera at (0, 0, D)
    const float nc = sp_dot3(n, CC);
    const float tol = 1.f / f;                                     // 1 accumulator px per unit depth (dilation)
    const unsigned long long lo = (unsigned long long)(unsigned int)id;
    for (int i = lane; i < bw * bh; i += 32) {
        const int px = x0 + i % bw, py = y0 + i / bw;
        const float3 rr = make_float3((px + 0.5f - cx) / f, -(py + 0.5f - cy) / f, -1.f);
        const float dn = sp_dot3(n, rr);
        if (fabsf(dn) < 1e-5f) continue;
        const float t = nc / dn;
        if (t <= 0.05f) continue;
        const float3 d = make_float3(rr.x * t - CC.x, rr.y * t - CC.y, rr.z * t - CC.z);
        const float u = sp_dot3(d, ch.eu), v = sp_dot3(d, ch.ev);
        const float tw = t * tol;
        if (fabsf(u) > ch.hw + tw || fabsf(v) > ch.hh + tw) continue;
        atomicMin(zb + (size_t)py * IW + px, ((unsigned long long)__float_as_uint(t) << 32) | lo);
    }
}

// exact ray / chip test at an output pixel: depth t, chip coords (u, v), anti-aliased coverage, n.ray
__device__ __forceinline__ bool chip_hit(const float* __restrict__ cb, float3 r, float D, float f,
                                         float& t, float& u, float& v, float& cov, float& dn) {
    const float3 eu = make_float3(cb[3], cb[4], cb[5]), ev = make_float3(cb[6], cb[7], cb[8]);
    const float3 n = sp_cross3(eu, ev);
    dn = sp_dot3(n, r);
    if (fabsf(dn) < 1e-6f) return false;
    const float3 CC = make_float3(cb[0], cb[1], cb[2] - D);
    t = sp_dot3(n, CC) / dn;
    if (t <= 0.05f) return false;
    const float3 d = make_float3(r.x * t - CC.x, r.y * t - CC.y, r.z * t - CC.z);
    u = sp_dot3(d, eu); v = sp_dot3(d, ev);
    const float e = fminf(cb[9] - fabsf(u), cb[10] - fabsf(v));
    cov = sp_clamp01(e * f / t + 0.5f + (1.f - cb[11]));         // fattened at rest: no seams at launch
    return cov > 0.f;
}

__device__ float3 chip_colour(const float* __restrict__ cb, float3 r, float u, float v, float dn,
                              const uchar4* __restrict__ a, const uchar4* __restrict__ b,
                              const float* __restrict__ q, const float* __restrict__ PAL, int W, int H) {
    const float lam = cb[11], s = cb[12], idx = cb[15];
    const int kind = (int)cb[13], home = (int)cb[14];
    const float3 n = sp_cross3(make_float3(cb[3], cb[4], cb[5]), make_float3(cb[6], cb[7], cb[8]));
    const bool front = dn < 0.f;
    const float3 nf = front ? n : sp_mul3(n, -1.f);
    const float3 V = nrm3(sp_mul3(r, -1.f));
    const float3 Lt = make_float3(LX, LY, LZ);
    const float ndl = fmaxf(sp_dot3(nf, Lt), 0.f);
    const float diff = fminf((0.3f + 0.7f * ndl) / (0.3f + 0.7f * LZ), 1.3f);
    const float shade = 1.f + (diff - 1.f) * lam;                  // exactly 1 at rest
    const float3 Hh = nrm3(sp_add3(Lt, V));
    const float ndh = fmaxf(sp_dot3(nf, Hh), 0.f);
    const float metal = q[14];
    if (kind < 2 && front) {                                       // picture side: the patch it was cut from
        const int cols = (int)q[4], rows = (int)q[5];
        const float hcx = ((home % cols) + 0.5f) * W / cols, hcy = ((home / cols) + 0.5f) * H / rows;
        const float4 tx = bilin(kind == 0 ? a : b, hcx + u / s * H, hcy - v / s * H, W, H);
        const float sp = lam * (0.12f + 0.3f * metal) * __powf(ndh, 40.f) * 255.f;
        return make_float3(tx.x * shade + sp, tx.y * shade + sp, tx.z * shade + sp);
    }
    const float3 pc = pal(idx + ((kind == 2 && front) ? 0.37f : 0.f), PAL);
    const float sh = shade * (1.f - 0.45f * metal);
    const float l = (kind == 2) ? 1.f : lam;
    const float g = l * ((0.3f + 1.4f * metal) * __powf(ndh, 10.f + 50.f * metal) + 0.35f * metal * ndh * ndh * ndh);
    const float tm = 0.6f * metal;
    return make_float3(255.f * (pc.x * sh + g * (1.f + (pc.x - 1.f) * tm)),
                       255.f * (pc.y * sh + g * (1.f + (pc.y - 1.f) * tm)),
                       255.f * (pc.z * sh + g * (1.f + (pc.z - 1.f) * tm)));
}

// ---- K2: full-resolution resolve: picture plane (home cells / revealed scene) + the two nearest chips
extern "C" __global__
void cb_resolve(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                const float* __restrict__ q, const float* __restrict__ R, const float* __restrict__ cbuf,
                const unsigned long long* __restrict__ zb, const float* __restrict__ PAL,
                const int W, const int H, const int IW, const int IH)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float P = q[0], D = q[3];
    const int cols = (int)q[4], rows = (int)q[5], nmax = (int)q[7], mode = (int)q[10];
    const int c = min((int)((x + 0.5f) * cols / W), cols - 1), rr = min((int)((y + 0.5f) * rows / H), rows - 1);
    const int k = rr * cols + c;
    float hx, hy;
    const float rn = cell_home(k, q, hx, hy);
    const float bgk = q[29];
    float4 base;
    bool solid = false;
    if (mode == 0) {
        if (P < burst_pl(rn, R[(size_t)k * NR], q)) { base = ld(a, x, y, W, H); solid = true; }
        else { base = ld(b, x, y, W, H); base.x *= bgk; base.y *= bgk; base.z *= bgk; }
    } else if (mode == 1) {
        if (P >= rain_pd(rn, R[(size_t)k * NR], q)) { base = ld(b, x, y, W, H); solid = true; }
        else { base = ld(a, x, y, W, H); base.x *= bgk; base.y *= bgk; base.z *= bgk; }
    } else {
        if (P >= rain_pd(rn, R[(size_t)(nmax + k) * NR], q)) { base = ld(b, x, y, W, H); solid = true; }
        else if (P < burst_pl(rn, R[(size_t)k * NR], q)) { base = ld(a, x, y, W, H); solid = true; }
        else {
            base = mix4(ld(a, x, y, W, H), ld(b, x, y, W, H), q[31]);
            base.x *= bgk; base.y *= bgk; base.z *= bgk;
        }
    }
    // candidate chips from the 2x2 nearest depth-buffer texels, re-tested exactly at this pixel
    const float fi = (x + 0.5f) * IW / W - 0.5f, fj = (y + 0.5f) * IH / H - 0.5f;
    const int i0 = min(max((int)floorf(fi), 0), IW - 1), j0 = min(max((int)floorf(fj), 0), IH - 1);
    const int i1 = min(i0 + 1, IW - 1), j1 = min(j0 + 1, IH - 1);
    const float f = H * D;
    const float3 ray = make_float3((x + 0.5f - 0.5f * W) / f, -(y + 0.5f - 0.5f * H) / f, -1.f);
    unsigned int seen[4];
    int ns = 0, id1 = -1, id2 = -1;
    float t1 = 1e30f, t2 = 1e30f, u1 = 0.f, v1 = 0.f, c1 = 0.f, d1 = 0.f, u2 = 0.f, v2 = 0.f, c2 = 0.f, d2 = 0.f;
    for (int m = 0; m < 4; ++m) {
        const int sx = (m & 1) ? i1 : i0, sy = (m & 2) ? j1 : j0;
        const unsigned long long key = zb[(size_t)sy * IW + sx];
        if (key == ZB_EMPTY) continue;
        const unsigned int id = (unsigned int)(key & 0xFFFFFFFFull);
        bool dup = false;
        for (int s2 = 0; s2 < ns; ++s2) dup |= (seen[s2] == id);
        if (dup) continue;
        seen[ns++] = id;
        float t, u, v, cv, dn;
        if (!chip_hit(cbuf + (size_t)id * CB, ray, D, f, t, u, v, cv, dn)) continue;
        if (solid && t > D + 1e-3f) continue;                     // behind a home chip on the picture plane
        if (t < t1) {
            id2 = id1; t2 = t1; u2 = u1; v2 = v1; c2 = c1; d2 = d1;
            id1 = (int)id; t1 = t; u1 = u; v1 = v; c1 = cv; d1 = dn;
        } else if (t < t2) {
            id2 = (int)id; t2 = t; u2 = u; v2 = v; c2 = cv; d2 = dn;
        }
    }
    float3 col = make_float3(base.x, base.y, base.z);
    if (id2 >= 0) {
        const float3 cc = chip_colour(cbuf + (size_t)id2 * CB, ray, u2, v2, d2, a, b, q, PAL, W, H);
        col = make_float3(col.x + (cc.x - col.x) * c2, col.y + (cc.y - col.y) * c2, col.z + (cc.z - col.z) * c2);
    }
    if (id1 >= 0) {
        const float3 cc = chip_colour(cbuf + (size_t)id1 * CB, ray, u1, v1, d1, a, b, q, PAL, W, H);
        col = make_float3(col.x + (cc.x - col.x) * c1, col.y + (cc.y - col.y) * c1, col.z + (cc.z - col.z) * c1);
    }
    // a soft palette bloom at the burst origin as it pops (screen blend)
    if (q[32] > 0.f) {
        const float gx = x + 0.5f - q[33], gy = y + 0.5f - q[34];
        const float g = q[32] * __expf(-(gx * gx + gy * gy) * q[35]);
        const float3 gc = pal(q[19] + 0.15f, PAL);
        col.x += (255.f - col.x) * sp_clamp01(g * gc.x);
        col.y += (255.f - col.y) * sp_clamp01(g * gc.y);
        col.z += (255.f - col.z) * sp_clamp01(g * gc.z);
    }
    out[(size_t)y * W + x] = pack(make_float4(col.x, col.y, col.z, 255.f));
}
"""

_MODES = ("burst", "rain", "swap")


def _sm(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class ConfettiBurst(Transition):
    name = "confetti_burst"
    label = "Confetti burst"
    description = ("The old picture bursts into thousands of two-sided confetti chips that tumble, flutter and "
                   "glint as they fall away to reveal the new one (or rain in to build it).")
    default_duration_s = 3.5
    params = {
        "chip_size": num(MIN_CHIP, 48.0, 16.0, "Chip size", step=0.5, unit="px", tier="regional", basic=True),
        "force": num(0.3, 2.0, 1.0, "Burst force", tier="regional", basic=True),
        "count": num(0.0, 1.0, 0.35, "Extra confetti (cannon / rain)", tier="regional"),
        "spin": num(0.0, 2.5, 1.0, "Spin", unit="×", tier="regional"),
        "flutter": num(0.0, 1.0, 0.6, "Flutter", tier="regional"),
        "metallic": num(0.0, 1.0, 0.5, "Metallic foil backs", tier="regional"),
        "sparkle": num(0.0, 1.0, 0.6, "Glint sparkle", tier="regional"),
        "stagger": num(0.0, 1.0, 0.6, "Stagger (all at once -> wave)", tier="regional"),
        "burst_x": num(0.0, 1.0, 0.5, "Burst origin x", tier="regional"),
        "burst_y": num(0.0, 1.0, 0.55, "Burst origin y", tier="regional"),
        "palette": enum(list(PALETTES), "rainbow", "Confetti palette", tier="regional"),
        "palette_period": num(0.1, 4.0, 1.0, "Palette spread", tier="regional"),
        "colour_drift": num(-1.0, 1.0, 0.12, "Colour drift", unit="/s", tier="regional"),
        "direction": enum(list(_MODES), "burst", "Direction (burst out / rain in / swap)", tier="structural"),
        "render_scale": num(0.35, 1.0, 0.5, "Depth-buffer scale", step=0.05, tier="quality"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        W, H = ctx.width, ctx.height
        self._A = W / H
        self._rows_max = int(round(1080.0 / MIN_CHIP))
        self._cols_max = int(round(self._A * self._rows_max)) + 1
        self._nmax = self._rows_max * self._cols_max
        ntot_max = 2 * self._nmax + MAX_EXTRA
        s = int(self.params.get("seed", -1))
        seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        rng = np.random.default_rng(seed)
        self._R = cp.asarray(rng.random((ntot_max, NR), dtype=np.float32))
        self._cb = cp.zeros(ntot_max * CB, cp.float32)
        self._wind = 0.06 * float(rng.choice((-1.0, 1.0)))
        self._d0 = float(rng.random())
        rs = min(max(float(self.params.get("render_scale", 0.5)), 0.35), 1.0)
        self._IW, self._IH = internal_size(ctx, rs)
        self._zb = cp.empty(self._IW * self._IH, cp.uint64)
        self._sp = Splat3D(ctx, self._IW, self._IH, max_points=16, max_lines=16, pool=4096)
        self._q = cp.zeros(NQ, cp.float32)
        self._qh = np.zeros(NQ, np.float32)
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        mod = raw_module(_SRC, SPLAT3D_OPTIONS)
        self._k_raster = mod.get_function("cb_raster")
        self._k_resolve = mod.get_function("cb_resolve")

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "rainbow")
        if name not in PALETTES:
            name = "rainbow"
        if name != self._pal:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        P = float(progress)
        if P <= 1e-4:
            cp.copyto(out, a)
            return
        if P >= 0.9999:
            cp.copyto(out, b)
            return
        p = self.params
        W, H = self.ctx.width, self.ctx.height
        IW, IH = self._IW, self._IH
        A = self._A
        self._sync_palette()
        mode = _MODES.index(p.get("direction", "burst")) if p.get("direction", "burst") in _MODES else 0
        chip = min(max(float(p.get("chip_size", 16.0)), MIN_CHIP), 48.0)
        rows = int(min(max(round(1080.0 / chip), 4), self._rows_max))
        cols = int(min(max(round(A * rows), 4), self._cols_max))
        nimg = rows * cols
        nset = 2 if mode == 2 else 1
        nex = int(round(min(max(float(p.get("count", 0.35)), 0.0), 1.0) * MAX_EXTRA))
        ntot = nset * nimg + nex
        dur = max(float(self.duration_s), 0.5)
        force = min(max(float(p.get("force", 1.0)), 0.3), 2.0)
        bx = min(max(float(p.get("burst_x", 0.5)), 0.0), 1.0)
        by = min(max(float(p.get("burst_y", 0.55)), 0.0), 1.0)
        ox, oy = (bx - 0.5) * A, 0.5 - by
        rmax = max(math.hypot(cx - ox, cy - oy) for cx in (-0.5 * A, 0.5 * A) for cy in (-0.5, 0.5))
        drift = (self._d0 + float(p.get("colour_drift", 0.12)) * P * dur) % 2.0
        if mode == 2:
            pe, wl, ps, pdlo, pdhi = 0.8, 0.3, 0.25, 0.5, 0.97
            bgk = 1.0 - 0.45 * math.sin(math.pi * P)
        else:
            pe, wl, ps, pdlo, pdhi = 0.97, 0.42, 0.02, 0.35, 0.97
            bgk = 1.0 - 0.3 * (1.0 - _sm(0.25, 0.95, P)) if mode == 0 else 1.0 - 0.3 * _sm(0.05, 0.6, P)
        flash = 0.0 if mode == 1 else 0.22 * min(force, 1.5) * _sm(0.0, 0.03, P) * (1.0 - _sm(0.05, 0.35, P))
        sparkle = min(max(float(p.get("sparkle", 0.6)), 0.0), 1.0)
        q = self._qh
        q[:] = 0.0
        q[0:21] = (P, dur, A, CAM_D, cols, rows, nimg, self._nmax, nset, nex, mode, force,
                   min(max(float(p.get("spin", 1.0)), 0.0), 2.5), min(max(float(p.get("flutter", 0.6)), 0.0), 1.0),
                   min(max(float(p.get("metallic", 0.5)), 0.0), 1.0), min(max(float(p.get("stagger", 0.6)), 0.0), 1.0),
                   ox, oy, rmax, drift, min(max(float(p.get("palette_period", 1.0)), 0.1), 4.0))
        q[21] = nset * nimg
        q[22:27] = (pe, wl, ps, pdlo, pdhi)
        q[28] = self._wind
        q[29] = bgk
        q[30] = sparkle
        q[31] = _sm(0.3, 0.75, P)
        q[32] = flash
        q[33], q[34] = bx * W, by * H
        q[35] = 1.0 / (0.3 * H) ** 2
        self._q.set(q)
        self._zb.data.memset_async(0xFF, self._zb.nbytes)
        if sparkle > 0.0:
            self._sp.clear()
        blocks = (ntot * 32 + 255) // 256
        self._k_raster((blocks,), (256,), (self._R, self._q, self._cb, self._zb, self._sp.acc, np.int32(ntot),
                                           np.int32(IW), np.int32(IH)))
        launch_2d(self._k_resolve, W, H, (a, b, out, self._q, self._R, self._cb, self._zb, self._P,
                                          np.int32(W), np.int32(H), np.int32(IW), np.int32(IH)))
        if sparkle > 0.0:
            self._sp.resolve(out, exposure=1.0, tonemap="exp", glow=0.8, glow_radius_px=10.0, glow_wide=0.35,
                             background=out, blend="screen", dither=False)

    def close(self) -> None:
        if getattr(self, "_sp", None) is not None:
            self._sp.close()
        self._sp = None
        self._R = self._cb = self._zb = self._q = self._P = None
