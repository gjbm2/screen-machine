"""Note highway 3D: the piano's notes as glowing tiles on a translucent piano roll laid on the floor of the scene.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  A sheet of 88 key lanes (A0 at the left, C8 at
the right, one lane per semitone, octave lines at each C) lies in perspective below a slowly swaying camera and
recedes to a soft, fogged horizon.  ``curve`` bends the sheet from flat into a half-pipe (a cylinder whose axis runs
into the distance), so the outer octaves rise up as walls.  Every strike starts a tile on its lane at the glowing
"now" line; while the key is held the tile's near end stays on the line, after key-up the whole tile travels away
(or, with ``direction: towards``, the strike line lies part-way out, before the fog, and the tile travels to
the viewer).  While the pedal keeps a released note sounding a thin ribbon follows it, dimming with the note's
own decay; a sounding key also lights its lane and pools light on the now line.  The sheet is a little wider
than the screen at the now line, so the middle six octaves fill it and the outermost lanes come into view as
they recede.

The room ensemble's register (docs/ENSEMBLE.md §8.6; ``self.sound.register``, register_split): a pitch outside this
screen's window draws its tiles, ribbon and lane light at ``outside`` strength (a per-pitch weight in the upload),
one inside at full; never hidden.

How it works.  Host side, a ring of the last K strikes per pitch (strike, key-up, sound end, velocity: the
waterfall's bookkeeping, healed against the notes table) is kept on the sound clock and written into the upload
only when something happens; the kernel subtracts "now".  One small ``.set()`` per frame carries it with the
camera, a per-pitch colour table and a blurred lane-light profile.  ``nh_draw`` runs at ``render_scale``
(default 0.5): per pixel an analytic ray-cylinder intersection (the stable quadratic, which is exactly the
ray-plane hit at curve 0) gives the arc position across the sheet (= pitch lane) and the distance along it
(= age), and the pixel only looks at the K slots of its own lane and its two neighbours (3 x 6 tiles, bounded).
The two neighbouring pixels' hits give the footprint, so lane, bar and tile edges are filtered (energy-conserving
lines, pixel-space SDFs): no shimmer in the distance.  A quarter-size bloom (4 x 4 box down, 15-tap separable
gaussian) is screened back onto that layer at internal size, and ``nh_comp`` (full resolution) samples it
bilinearly onto the scene: a light "glass" dim under the sheet and under the notes (legibility on bright
pictures), then a screen blend of the glow, all scaled by amount x opacity, so amount 0 is exactly the input.

Look: strikes bloom in over ~0.1 s (they land late, CONVENTIONS §2b) with a brief whitish flare that eases out;
a tile is brightest at its head (the strike) and dims toward its tail as the note decayed; the sheet fades with
depth.  pulse (beat) brightens the lanes, the bar lines and the now line, never the geometry.  Bar lines follow
the beat clock when one is sure (beats faint, bars brighter), else a free 120 bpm grid; the two cross-fade.
Alive without notes: the camera sways and breathes, the grid drifts by, the now line breathes (a faint idle).
With no sound at all the sheet's grid stays as that faint idle; the notes simply never appear.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service import ensemble
from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size

K = 6                                   # strikes remembered per pitch
LOW, HIGH = 21, 108                     # A0 .. C8
NL = HIGH - LOW + 1                     # 88 lanes
NP = 64                                 # camera + look params
NCOL = 128 * 3                          # per-pitch colour table
NPOOL = 96                              # blurred lane light (88 lanes + padding)
NSEG = 128 * K * 4                      # per-pitch strike slots: t_on, t_off, t_end (s since base), vel
NWGT = 128                              # per-pitch weight: the ensemble's register (1 = full)
OFF_COL, OFF_POOL, OFF_SEG = NP, NP + NCOL, NP + NCOL + NPOOL
OFF_WGT = OFF_SEG + NSEG
NPACK = OFF_WGT + NWGT
VFOV_DEG = 50.0
NOW_NDC = -0.70                         # the now line's screen height (ndc, -1 bottom) at the base camera
H_REF = 0.5                             # the camera height at which the sheet has its reference width
FIT = 1.25                              # the sheet's width at the now line over the screen's (outer lanes recede into view)
NOTE_GAIN = 2.2                         # tiles, ribbons and lane light before the tonemap
IDLE = 0.45                             # the sheet's grid with no piano heard (a faint idle)
END_LEVEL = 0.02                        # a released note's ribbon ends when its level drops below this
REBASE_S = 600.0                        # slot times are float32 seconds since a base, re-based this often
FAR, NEVER = -1.0e9, 1.0e9              # slot sentinels: an empty strike / a key still held or a note still sounding
DIRECTIONS = ("away", "towards")
TOWARDS_AT = 0.6                        # towards: the strike line at this fraction of the depth (before the fog)
COLOURS = ("register", "fifths")
OPTIONS = ("--std=c++14", "--use_fast_math")

# P layout (floats)
#  0 w | 1 h | 2 aspect | 3 tan(vfov/2) | 4..12 camera rotation (world = M cam, row-major)
#  13 camera x | 14 camera height | 15 curvature kappa | 16 sheet half width S (arc) | 17 z0 (now line)
#  18 travel v (world/s) | 19 depth D (world) | 20 direction (+1 away, -1 towards) | 21 lane glow | 22 glow
#  23 bar lines | 24 glass | 25 kick | 26 breath 0..1 | 27 note gain | 28 free beat q0 | 29 free beats/s
#  30 clock beat q0 | 31 clock beats/s | 32 clock weight | 33 free beats/bar | 34 clock beats/bar
#  35..37 grid tint | 38 presence (idle .. 1) | 39 lane width (world) | 40 now (s since the slot base)
#  41 the strike line's distance beyond the near edge (0 away, TOWARDS_AT x depth towards)
_SRC = HELPERS + r"""
#define K """ + str(K) + r"""
#define NL """ + str(NL) + r"""
#define LOWP """ + str(LOW) + r"""
#define OFF_COL """ + str(OFF_COL) + r"""
#define OFF_POOL """ + str(OFF_POOL) + r"""
#define OFF_SEG """ + str(OFF_SEG) + r"""
#define OFF_WGT """ + str(OFF_WGT) + r"""

__device__ __forceinline__ float nh_sdbox(float px, float py, float hx, float hy, float r) {
    const float qx = fabsf(px) - hx + r, qy = fabsf(py) - hy + r;
    const float ox = fmaxf(qx, 0.f), oy = fmaxf(qy, 0.f);
    return sqrtf(ox * ox + oy * oy) + fminf(fmaxf(qx, qy), 0.f) - r;
}
// an energy-conserving anti-aliased line: d >= 0 distance, w half width, f footprint (all the same units)
__device__ __forceinline__ float nh_line(float d, float w, float f) {
    const float we = fmaxf(w, f);
    return (w / we) * (1.f - smooth(0.f, we, d));
}
__device__ __forceinline__ bool nh_black(int p) {
    const int pc = p % 12;
    return pc == 1 || pc == 3 || pc == 6 || pc == 8 || pc == 10;
}

// Ray from the camera through internal pixel (px, py) against the sheet: the trough
// kappa (X^2 + u^2) - 2 u = 0 (u = height above its lowest line, X across), which is the plane u = 0 at kappa 0.
// The direction need not be unit length (t only scales).  Returns the arc position s across the sheet and the
// distance Z along it.
__device__ bool nh_hit(float px, float py, const float* __restrict__ P, float& s, float& Z)
{
    const float sx = (2.f * px / P[0] - 1.f) * P[2] * P[3];
    const float sy = (1.f - 2.f * py / P[1]) * P[3];
    const float dx = P[4] * sx + P[5] * sy + P[6];
    const float dy = P[7] * sx + P[8] * sy + P[9];
    const float dz = P[10] * sx + P[11] * sy + P[12];
    const float cx = P[13], h = P[14], k = P[15], S = P[16];
    const float a = k * (dx * dx + dy * dy);
    const float b = 2.f * k * (cx * dx + h * dy) - 2.f * dy;
    const float c = k * (cx * cx + h * h) - 2.f * h;
    const float disc = b * b - 4.f * a * c;
    if (disc < 0.f) return false;
    const float q = -0.5f * (b + copysignf(sqrtf(disc), b));
    float t0 = 1e30f, t1 = 1e30f;
    if (fabsf(q) > 1e-20f) t0 = c / q;
    if (a > 1e-12f) t1 = q / a;
    const float ta = fminf(t0, t1), tb = fmaxf(t0, t1);
    #pragma unroll
    for (int i = 0; i < 2; ++i) {
        const float t = i == 0 ? ta : tb;
        if (!(t > 1e-4f && t < 1e4f)) continue;
        const float X = cx + t * dx, u = h + t * dy;
        const float sa = k > 1e-6f ? atan2f(k * X, 1.f - k * u) / k : X;
        if (fabsf(sa) > 1.06f * S) continue;
        s = sa;
        Z = t * dz;
        return true;
    }
    return false;
}

extern "C" __global__
void nh_draw(uchar4* __restrict__ ov, const float* __restrict__ B, const float* __restrict__ notes)
{
    const float* P = B;
    const int w = (int)P[0], h = (int)P[1];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float px = x + 0.5f, py = y + 0.5f;
    float s, Z;
    if (!nh_hit(px, py, P, s, Z)) { ov[y * w + x] = make_uchar4(0, 0, 0, 0); return; }

    const float S = P[16], z0 = P[17], v = fmaxf(P[18], 1e-3f), Dp = fmaxf(P[19], 1e-2f), dir = P[20];
    const float lw = P[39];
    const float rel = Z - z0;                                // world distance beyond the now line
    const float zn = rel / Dp;
    float sheet = __expf(-0.8f * fmaxf(zn, 0.f)) * (1.f - smooth(0.72f, 1.06f, zn));
    sheet *= smooth(-0.32f * z0, 0.f, rel);                  // a short fade in front of the now line
    if (sheet < 2e-3f) { ov[y * w + x] = make_uchar4(0, 0, 0, 0); return; }
    // footprint from the neighbouring pixels' hits
    float s1, Z1, s2, Z2, fs = 0.f, fz = 0.f;
    const bool h1 = nh_hit(px + 1.f, py, P, s1, Z1), h2 = nh_hit(px, py + 1.f, P, s2, Z2);
    if (h1) { fs += fabsf(s1 - s); fz += fabsf(Z1 - Z); }
    if (h2) { fs += fabsf(s2 - s); fz += fabsf(Z2 - Z); }
    if (h1 != h2) { fs *= 2.f; fz *= 2.f; }
    if (!h1 && !h2) { fs = lw; fz = 0.05f * Dp; }
    fs = fminf(fmaxf(fs, 1e-6f), 4.f * lw);
    fz = fminf(fmaxf(fz, 1e-6f), 0.5f * Dp);
    sheet *= 1.f - smooth(S - 1.2f * lw - fs, S + 0.2f * lw + fs, fabsf(s));
    if (sheet < 2e-3f) { ov[y * w + x] = make_uchar4(0, 0, 0, 0); return; }

    const float L = (s + S) / lw;                            // lane coordinate 0..NL
    const int li = min(NL - 1, max(0, (int)floorf(L)));
    const float du = L - li - 0.5f;
    const float fl = fs / lw;                                // lanes per pixel
    const float a = dir * (rel - P[41]) / v;                 // the age (s) this point on the sheet shows
    const float dn = fabsf(a) * v;                           // world distance from the now line
    const float kick = P[25], pres = P[38];
    const float lg = P[21], glow = P[22], now = P[40];
    float3 acc = make_float3(0.f, 0.f, 0.f);
    float dimn = 0.f;

    // ---- the sheet: key lanes, octave lines, white-key fill, bar lines, the now line ----------------------
    {
        const float3 tint = make_float3(P[35], P[36], P[37]);
        const int bi = (int)rintf(L);
        const float dl = fabsf(L - (float)bi) * lw;
        const bool octave = ((LOWP + bi) % 12) == 0;
        float g = nh_line(dl, (octave ? 0.07f : 0.035f) * lw, 0.7f * fs) * (octave ? 0.55f : 0.16f);
        g += nh_black(LOWP + li) ? 0.f : 0.035f * (1.f - smooth(0.3f, 1.2f, fl));
        g *= lg * (1.f + 1.4f * kick);
        // bar lines: q (beats) = q0 - age * rate; the free grid and the beat clock's cross-fade
        float bars = 0.f;
        #pragma unroll
        for (int j = 0; j < 2; ++j) {
            const float wgt = j == 0 ? 1.f - P[32] : P[32];
            if (wgt <= 1e-3f) continue;
            const float rate = fmaxf(j == 0 ? P[29] : P[31], 1e-3f), bpb = fmaxf(j == 0 ? P[33] : P[34], 1.f);
            const float q = (j == 0 ? P[28] : P[30]) - a * rate;
            const float qi = rintf(q);
            const float dq = fabsf(q - qi) * v / rate;       // world distance to the nearest beat line
            const float m = qi - bpb * floorf(qi / bpb);
            const bool bar = m < 0.5f;
            bars += wgt * nh_line(dq, (bar ? 0.30f : 0.16f) * lw, 0.7f * fz) * (bar ? 0.45f : 0.14f);
        }
        g += P[23] * bars * (1.f + 1.0f * kick);
        // the now line: a thin bright thread with a soft glow that breathes
        const float nl = nh_line(dn, 0.10f * lw, 0.7f * fz) * 0.6f + __expf(-dn / (lw * (0.6f + 2.5f * glow))) * 0.10f * glow;
        g += nl * (0.8f + 0.2f * P[26]) * (1.f + 0.6f * kick);
        const float k = g * pres * sheet;
        acc.x += k * tint.x; acc.y += k * tint.y; acc.z += k * tint.z;
    }

    // ---- sounding keys pool light on the now line (host-blurred across a few lanes) ------------------------
    {
        const float pl = fminf(fmaxf(L - 0.5f, 0.f), (float)(NL - 1));
        const int p0 = (int)floorf(pl);
        const float pf = pl - p0;
        const float pool = B[OFF_POOL + p0] * (1.f - pf) + B[OFF_POOL + min(p0 + 1, NL - 1)] * pf;
        if (pool > 1e-4f) {
            const float spread = lw * (1.5f + 3.f * glow);
            const float k = pool * 0.55f * (0.35f + glow) * __expf(-dn / spread) * (1.f + 0.6f * kick) * lg * sheet;
            const float* C = B + OFF_COL + 3 * (LOWP + min(NL - 1, max(0, (int)rintf(pl))));
            acc.x += k * (0.4f + C[0]); acc.y += k * (0.4f + C[1]); acc.z += k * (0.4f + C[2]);
        }
    }

    // ---- notes: the tiles of this lane and its two neighbours ---------------------------------------------
    const float amax = Dp / v + 0.6f;
    const float gw = lw * (0.15f + 0.6f * glow);
    const float fx = fmaxf(fs, 1e-6f), fzz = fmaxf(fz, 1e-6f);
    for (int c = -1; c <= 1; ++c) {
        const int pc = LOWP + li + c;
        if (pc < LOWP || pc >= LOWP + NL) continue;
        const float* C = B + OFF_COL + 3 * pc;
        const float dxw = (du - (float)c) * lw;              // world offset from this lane's centre
        const float hw = 0.42f * lw;
        const float acr = fmaxf(fabsf(dxw) - hw, 0.f);
        const float win = 1.f - smooth(0.45f * lw, 1.05f * lw, acr);   // halos stop before the next-but-one lane
        // the lane itself glows while its note sounds (bloomed in), brightest near the now line
        const float* N = notes + 4 * pc;
        const float lvl = N[0] * smooth(0.f, 0.1f, N[2]);
        float k = 0.f, kw = 0.f, dimk = 0.f;
        if (lvl > 1e-3f) {
            const float beam = __expf(-(dxw * dxw) / (2.f * 0.09f * lw * lw)) * win;
            k += lg * lvl * beam * (0.10f + 0.30f * __expf(-dn / (lw * 14.f))) * (1.f + 0.8f * kick);
        }
        const float tau = fmaxf(0.3f, 5.f * exp2f(-(float)(pc - LOWP) / 24.f));
        const float* Sg = B + OFF_SEG + pc * K * 4;
        for (int j = 0; j < K; ++j) {
            const float aon = now - Sg[4 * j];               // seconds since the strike (huge: an empty slot)
            const float aoff = now - Sg[4 * j + 1];          // since key-up (negative: still held)
            const float aend = now - Sg[4 * j + 2];          // since the sound ended (negative: still sounding)
            const float at = fminf(fmaxf(aoff, 0.f), aon);   // the tile's tail: key-up (0 while held)
            const float ae = fminf(fmaxf(aend, 0.f), at);    // the ribbon's end
            if (!(aon >= 0.f && aon < 1.0e8f) || ae > amax) continue;   // not yet struck / empty / out of sight
            const float vel = Sg[4 * j + 3];
            const float bv = 0.5f + 0.5f * vel;
            const float bl = smooth(0.f, 0.12f, aon);         // bloom in: the strike lands late
            const float young = __expf(-aon / 0.45f);         // a brief flare that eases out
            // body (key held): rounded tile from at to aon, at least a short tile for staccato notes
            const float hl = fmaxf(0.5f * (aon - at) * v, 0.45f * lw);
            const float ac = 0.5f * (aon + at);
            const float dzw = (a - ac) * v;
            const float r = fminf(hw, hl) * 0.8f;
            const float dw = nh_sdbox(dxw, dzw, hw, hl, r);                                  // world
            const float dp = nh_sdbox(dxw / fx, dzw / fzz, hw / fx, hl / fzz, fminf(hw / fx, hl / fzz) * 0.8f);  // pixels
            const float core = 1.f - smooth(-0.75f, 0.75f, dp);
            const float env = __expf(-fmaxf(aon - a, 0.f) / tau);                            // the note's decay
            const float eb = 0.3f + 0.7f * env;
            const float cb = core * bv * bl * eb * (1.f + 0.9f * young);
            const float halo = __expf(-fmaxf(dw, 0.f) / gw) * win * glow * bv * bl * (0.3f + 0.7f * eb)
                               * (0.55f + 0.9f * young);
            k += cb + 0.5f * halo;
            kw += cb * (0.08f + 0.45f * young);
            dimk += core * bl * 0.75f + 0.12f * halo;
            // sustain ribbon (released but still sounding): thin, dimming with the decay
            if (at - ae > 0.02f) {
                const float rl = 0.5f * (at - ae) * v, rc = 0.5f * (at + ae);
                const float rw = 0.12f * lw;
                const float drz = (a - rc) * v;
                const float drp = nh_sdbox(dxw / fx, drz / fzz, rw / fx, rl / fzz, 0.f);
                const float rcore = (rw / fmaxf(rw, 0.7f * fx)) * (1.f - smooth(-0.75f, 0.75f, drp));
                const float rh = __expf(-fmaxf(nh_sdbox(dxw, drz, rw, rl, 0.f), 0.f) / (0.7f * gw)) * win * glow;
                k += (0.6f * rcore + 0.25f * rh) * bv * bl * env;
            }
        }
        if (k > 0.f) {
            const float wg = B[OFF_WGT + pc];                // the ensemble's register weight for this pitch
            const float kk = k * wg * P[27] * sheet, ww = kw * wg * P[27] * sheet;
            acc.x += kk * C[0] + ww; acc.y += kk * C[1] + ww; acc.z += kk * C[2] + ww;
            dimn += dimk * wg * sheet;
        }
    }
    const float dim = fminf(0.85f, P[24] * sheet * pres + 0.6f * dimn);
    ov[y * w + x] = make_uchar4((unsigned char)fminf(255.f, 255.f * (1.f - __expf(-acc.x)) + 0.5f),
                                (unsigned char)fminf(255.f, 255.f * (1.f - __expf(-acc.y)) + 0.5f),
                                (unsigned char)fminf(255.f, 255.f * (1.f - __expf(-acc.z)) + 0.5f),
                                (unsigned char)fminf(255.f, 255.f * dim + 0.5f));
}

// bloom: the layer's colour box-averaged 4 x 4 into a small float buffer, a separable gaussian, then screened
// back onto the layer in place (its dim channel untouched)
extern "C" __global__
void nh_down(const uchar4* __restrict__ ov, const int w, const int h, float4* __restrict__ bl, const int bw, const int bh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= bw || y >= bh) return;
    float r = 0.f, g = 0.f, b = 0.f;
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int yy = min(y * 4 + j, h - 1);
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            const uchar4 c = ov[yy * w + min(x * 4 + i, w - 1)];
            r += c.x; g += c.y; b += c.z;
        }
    }
    const float n = 1.f / (16.f * 255.f);
    bl[y * bw + x] = make_float4(r * n, g * n, b * n, 0.f);
}

extern "C" __global__
void nh_blur(const float4* __restrict__ a, float4* __restrict__ b, const int bw, const int bh, const int dx, const int dy)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= bw || y >= bh) return;
    float r = 0.f, g = 0.f, bb = 0.f, ws = 0.f;
    #pragma unroll
    for (int i = -7; i <= 7; ++i) {
        const float wt = __expf(-(float)(i * i) / (2.f * 3.2f * 3.2f));
        const int xx = min(max(x + i * dx, 0), bw - 1), yy = min(max(y + i * dy, 0), bh - 1);
        const float4 c = a[yy * bw + xx];
        r += wt * c.x; g += wt * c.y; bb += wt * c.z; ws += wt;
    }
    const float n = 1.f / ws;
    b[y * bw + x] = make_float4(r * n, g * n, bb * n, 0.f);
}

extern "C" __global__
void nh_merge(uchar4* __restrict__ ov, const int w, const int h, const float4* __restrict__ bl, const int bw,
              const int bh, const float bg)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float fx = (x + 0.5f) * bw / w - 0.5f, fy = (y + 0.5f) * bh / h - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), bw - 1), xb = min(max(x0 + 1, 0), bw - 1);
    const int ya = min(max(y0, 0), bh - 1), yb = min(max(y0 + 1, 0), bh - 1);
    const float4 a = bl[ya * bw + xa], b = bl[ya * bw + xb], c = bl[yb * bw + xa], d = bl[yb * bw + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    const float br = fminf(bg * (v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x)), 1.f);
    const float bgg = fminf(bg * (v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y)), 1.f);
    const float bb = fminf(bg * (v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z)), 1.f);
    if (br + bgg + bb < 0.5f / 255.f) return;
    const uchar4 o = ov[y * w + x];
    const float r = 255.f - (255.f - o.x) * (1.f - br);
    const float g = 255.f - (255.f - o.y) * (1.f - bgg);
    const float b2 = 255.f - (255.f - o.z) * (1.f - bb);
    ov[y * w + x] = make_uchar4((unsigned char)fminf(r + 0.5f, 255.f), (unsigned char)fminf(g + 0.5f, 255.f),
                                (unsigned char)fminf(b2 + 0.5f, 255.f), o.w);
}

extern "C" __global__
void nh_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const uchar4* __restrict__ ov,
             const int W, const int H, const int w, const int h, const float k)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s = src[y * W + x];
    const float fx = (x + 0.5f) * w / W - 0.5f, fy = (y + 0.5f) * h / H - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const uchar4 a = ov[ya * w + xa], b = ov[ya * w + xb], c = ov[yb * w + xa], d = ov[yb * w + xb];
    if (((*(const unsigned int*)&a) | (*(const unsigned int*)&b) | (*(const unsigned int*)&c)
         | (*(const unsigned int*)&d)) == 0u) {
        out[y * W + x] = s;
        return;
    }
    const float u = 1.f - tx, v = 1.f - ty;
    const float w00 = u * v, w10 = tx * v, w01 = u * ty, w11 = tx * ty;
    const float orr = (w00 * a.x + w10 * b.x + w01 * c.x + w11 * d.x) * (1.f / 255.f);
    const float og = (w00 * a.y + w10 * b.y + w01 * c.y + w11 * d.y) * (1.f / 255.f);
    const float ob = (w00 * a.z + w10 * b.z + w01 * c.z + w11 * d.z) * (1.f / 255.f);
    const float od = (w00 * a.w + w10 * b.w + w01 * c.w + w11 * d.w) * (1.f / 255.f);
    const float keep = 1.f - k * od;
    float r = s.x * keep, g = s.y * keep, b2 = s.z * keep;
    r += k * orr * (255.f - r); g += k * og * (255.f - g); b2 += k * ob * (255.f - b2);
    out[y * W + x] = pack(make_float4(r, g, b2, 255.f));
}
"""


def _smooth_to(x: float, target: float, dt: float, tau: float) -> float:
    if dt <= 0.0:
        return x
    return x + (target - x) * (1.0 - math.exp(-dt / tau))


def _ev_ms(t_ms: float, age_s) -> float:
    """A note event's sound time (ms) from the frame time and its age (s); a bad age counts as now."""
    try:
        a = float(age_s)
    except (TypeError, ValueError):
        return t_ms
    return t_ms - a * 1000.0 if math.isfinite(a) else t_ms


def _unit(v) -> float:
    """A 0..1 level (amount, pulse); NaN / inf / junk reads as 0 so it can never blank the frame."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(max(x, 0.0), 1.0) if math.isfinite(x) else 0.0


class NoteHighway3D(Effect):
    name = "note_highway_3d"
    stage = "overlay"
    label = "Note highway 3D"
    description = ("The piano's notes as glowing tiles on a translucent piano roll laid in perspective over the "
                   "scene: each strike starts at a glowing line and travels away into a fogged horizon.")
    listens = ("notes",)
    params = {
        **standard_effect_params(),
        "opacity": num(0.0, 1.0, 0.85, "Opacity", tier="regional"),
        "tilt": num(5.0, 55.0, 22.0, "Camera tilt", unit="°", step=0.5, tier="regional"),
        "height": num(0.3, 1.2, 0.5, "Camera height", step=0.01, tier="regional"),
        "curve": num(0.0, 1.0, 0.0, "Curve (flat → half-pipe)", tier="regional", basic=True),
        "depth": num(0.6, 6.0, 1.8, "Depth (how far notes travel)", step=0.05, tier="regional"),
        "lane_glow": num(0.0, 2.0, 0.8, "Lane glow", tier="regional", basic=True),
        "travel": num(0.06, 1.2, 0.2, "Travel speed", step=0.01, tier="regional"),
        "glow": num(0.0, 1.5, 0.6, "Note glow", tier="regional"),
        "bar_lines": num(0.0, 1.5, 0.5, "Bar lines", tier="regional"),
        "glass": num(0.0, 0.6, 0.18, "Glass (dims the scene under the sheet)", tier="regional"),
        "sway": num(0.0, 2.0, 0.6, "Camera sway", tier="regional"),
        "hue": num(0.0, 0.75, 0.0, "Hue shift", tier="regional"),
        "direction": enum(DIRECTIONS, "away", "Direction"),
        "colours": enum(COLOURS, "register", "Colours"),
        **palette_params("rainbow", period=0.8, drift=0.02),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
    }

    # ------------------------------------------------------------------------------------------------- setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, OPTIONS)
        self._kd = mod.get_function("nh_draw")
        self._kdn = mod.get_function("nh_down")
        self._kb = mod.get_function("nh_blur")
        self._km = mod.get_function("nh_merge")
        self._kc = mod.get_function("nh_comp")
        self.w, self.h = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.ov = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.bw, self.bh = max(8, (self.w + 3) // 4), max(8, (self.h + 3) // 4)
        self.bl0 = cp.zeros((self.bh, self.bw, 4), cp.float32)
        self.bl1 = cp.zeros((self.bh, self.bw, 4), cp.float32)
        self.dev = cp.zeros(NPACK, cp.float32)
        self.host = np.zeros(NPACK, np.float32)
        self.wgt = self.host[OFF_WGT:OFF_WGT + NWGT]     # a view into the upload: the register weights
        self.wgt[:] = 1.0
        self.pitches = np.arange(128)
        self.quiet = cp.zeros((128, 4), cp.float32)
        iw, ih, bw, bh = (np.int32(v) for v in (self.w, self.h, self.bw, self.bh))
        self._i = (iw, ih, bw, bh, np.int32(ctx.width), np.int32(ctx.height))
        self._a_down = (self.ov, iw, ih, self.bl0, bw, bh)
        self._a_bh = (self.bl0, self.bl1, bw, bh, np.int32(1), np.int32(0))
        self._a_bv = (self.bl1, self.bl0, bw, bh, np.int32(0), np.int32(1))
        self.pool_lin = np.zeros(NL, np.float64)
        g = np.exp(-0.5 * (np.arange(-6, 7) / 2.0) ** 2)
        self.pool_kernel = g / g.sum() * 1.6
        # strike history on the sound clock (ms): slot 0 is the latest
        self.on_ms = np.full((128, K), np.nan)
        self.off_ms = np.full((128, K), np.nan)
        self.end_ms = np.full((128, K), np.nan)
        self.vel = np.zeros((128, K))
        self.seg = self.host[OFF_SEG:OFF_SEG + NSEG].reshape(128, K, 4)     # a view into the upload
        self.base_ms = None
        self.dirty = True
        self.t_ms = 0.0
        self.have_t = False
        pitches = np.arange(128, dtype=np.float64)
        self.idx_register = np.clip((pitches - LOW) / (HIGH - LOW), 0.0, 1.0)
        self.idx_fifths = ((pitches.astype(np.int64) * 7) % 12) / 12.0
        self.clock = 0.0
        self.drift = 0.0
        self.kick = 0.0
        self.presence = IDLE
        self.clock_w = 0.0
        self.q_free = 0.0
        self.q_clk = None                   # the clock grid's own phase (beats since a downbeat), tempo, beats/bar
        self.r_clk = 2.0
        self.bpb_clk = 4.0
        self.geo = None                     # eased geometry (tilt, height, curve, depth, travel)

    # -- the strike history (as piano_waterfall) ---------------------------------------------------------------
    def _strike(self, p: int, t_on: float, vel: float) -> None:
        for arr in (self.on_ms, self.off_ms, self.end_ms, self.vel):
            arr[p, 1:] = arr[p, :-1]
        self.on_ms[p, 0], self.off_ms[p, 0], self.end_ms[p, 0], self.vel[p, 0] = t_on, np.nan, np.nan, vel
        self.dirty = True
        if math.isnan(self.on_ms[p, 1]):
            return
        if math.isnan(self.off_ms[p, 1]):
            self.off_ms[p, 1] = t_on              # re-struck while held: the old tile ends here
        if math.isnan(self.end_ms[p, 1]):
            self.end_ms[p, 1] = t_on              # ... and its ribbon

    def _forget(self) -> None:
        self.on_ms[:] = np.nan
        self.off_ms[:] = np.nan
        self.end_ms[:] = np.nan
        self.vel[:] = 0.0
        self.dirty = True

    def _listen(self, dt: float):
        """Fold this frame's strikes and releases into the history; returns (notes on the GPU, time ms, present,
        host levels or None)."""
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        ng = getattr(snd, "notes_gpu", None) if snd is not None else None
        if f is None or ng is None:
            self.t_ms += dt * 1000.0
            held = ~np.isnan(self.on_ms[:, 0]) & np.isnan(self.end_ms[:, 0])
            if held.any():                        # no feed: whatever was held lets go and drifts away
                self.off_ms[held & np.isnan(self.off_ms[:, 0]), 0] = self.t_ms
                self.end_ms[held, 0] = self.t_ms
                self.dirty = True
            return self.quiet, self.t_ms, False, None
        t = float(f.t_ms)
        if not math.isfinite(t):
            t = self.t_ms + dt * 1000.0
        if self.have_t and t < self.t_ms - 2000.0:  # the sound clock went back (a new feed): start afresh
            self._forget()
        self.t_ms, self.have_t = t, True
        # this frame's strikes and key-ups in the order they happened: a key released and struck again within one
        # frame (fast repeated notes) closes its old tile, not the new one (key-ups first on a tie)
        ev = []
        for p, vel, age in f.onsets:
            p = int(p)
            if LOW <= p <= HIGH:
                ev.append((_ev_ms(t, age), 1, p, float(vel)))
        for p, _vo, age in f.releases:
            p = int(p)
            if LOW <= p <= HIGH:
                ev.append((_ev_ms(t, age), 0, p, 0.0))
        ev.sort()
        for te, on, p, vel in ev:
            if on:
                self._strike(p, te, vel)
            elif not math.isnan(self.on_ms[p, 0]) and math.isnan(self.off_ms[p, 0]):
                self.off_ms[p, 0] = max(self.on_ms[p, 0], te)
                self.dirty = True
        n = f.notes
        held = n[:, 3] < 0.0
        open_ = np.isnan(self.off_ms[:, 0]) & ~np.isnan(self.on_ms[:, 0])
        for p in np.flatnonzero(held & ~open_):   # a key held that we never saw struck (we came in mid-note)
            if LOW <= p <= HIGH and np.isfinite(n[p, 2]):
                self._strike(int(p), t - float(n[p, 2]) * 1000.0, float(n[p, 1]))
        for p in np.flatnonzero(open_ & ~held):   # a key that went up unseen
            a = float(n[p, 3])
            self.off_ms[p, 0] = t - (a * 1000.0 if np.isfinite(a) and a >= 0.0 else 0.0)
            self.dirty = True
        # a released note's ribbon ends when it stops sounding
        lvl = n[:, 0]
        done = (~np.isnan(self.on_ms[:, 0]) & ~np.isnan(self.off_ms[:, 0]) & np.isnan(self.end_ms[:, 0])
                & (lvl < END_LEVEL))
        if done.any():
            self.end_ms[done, 0] = t
            self.dirty = True
        return ng, t, bool(f.present), lvl

    def _write_slots(self, t_ms: float) -> float:
        """Slot times as float32 seconds since a base (re-based now and then); returns now on that base."""
        if self.base_ms is None or t_ms - self.base_ms > REBASE_S * 1000.0 or t_ms < self.base_ms:
            self.base_ms = t_ms
            self.dirty = True
        if self.dirty:
            b, sg = self.base_ms, self.seg
            sg[:, :, 0] = np.where(np.isnan(self.on_ms), FAR, (self.on_ms - b) / 1000.0)
            sg[:, :, 1] = np.where(np.isnan(self.off_ms), NEVER, (self.off_ms - b) / 1000.0)
            sg[:, :, 2] = np.where(np.isnan(self.end_ms), NEVER, (self.end_ms - b) / 1000.0)
            sg[:, :, 3] = self.vel
            self.dirty = False
        return (t_ms - self.base_ms) / 1000.0

    def _palette(self, name: str) -> np.ndarray:
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        return np.asarray(PALETTES[name] if rows is None else rows, np.float64).reshape(-1)[:12]

    # ------------------------------------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        dtc = min(dt, 0.1)
        amount = _unit(p["amount"])
        pulse = _unit(p["pulse"])
        notes, t_ms, present, lvl = self._listen(dt)
        self.clock += dtc
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 1000.0)
        self.kick = _smooth_to(self.kick, pulse, dtc, 0.07)
        self.presence = _smooth_to(self.presence, 1.0 if present else IDLE, dtc, 1.2)
        # geometry eases a little slower than the param easer (the art director's knobs move it, never a beat)
        tgt = (float(p["tilt"]), float(p["height"]), float(p["curve"]), float(p["depth"]), float(p["travel"]))
        self.geo = list(tgt) if self.geo is None else [_smooth_to(g, x, dtc, 0.8) for g, x in zip(self.geo, tgt)]
        tilt_deg, height, curve, depth, travel = self.geo
        now = self._write_slots(t_ms)

        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        # ---- camera: the base framing pins the now line and the sheet's width; sway moves only the camera
        tanf = math.tan(math.radians(VFOV_DEG) * 0.5)
        aspect = self.w / self.h
        phi = math.radians(tilt_deg)
        beta = math.atan(-NOW_NDC * tanf)
        ang = min(phi + beta, math.radians(88.0))
        z0 = height / math.tan(ang)
        S = FIT * (H_REF / math.sin(ang)) * math.cos(beta) * tanf * aspect
        lw = 2.0 * S / NL
        kappa = curve * (0.5 * math.pi) / S
        sw = float(p["sway"])
        c = self.clock
        yaw = math.radians(1.6 * sw) * (0.65 * math.sin(2 * math.pi * 0.047 * c)
                                        + 0.35 * math.sin(2 * math.pi * 0.113 * c + 1.3))
        roll = math.radians(0.9 * sw) * math.sin(2 * math.pi * 0.061 * c + 0.7)
        pitch = phi + math.radians(0.8 * sw) * math.sin(2 * math.pi * 0.083 * c + 2.1)
        cam_h = height * (1.0 + 0.04 * sw * math.sin(2 * math.pi * 0.071 * c + 0.4))
        cam_x = 0.03 * S * sw * math.sin(2 * math.pi * 0.039 * c + 2.6)
        cp_, sp_ = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        cr, sr = math.cos(roll), math.sin(roll)

        # ---- the beat grid: the beat clock when it is sure, else a free 120 bpm grid; they cross-fade.  The clock
        # grid keeps its own phase: it runs on at the last tempo when the clock goes (so it fades out where it was),
        # eases onto tempo changes and is pulled (not snapped) onto a re-anchored clock, so no line jumps.
        bc = getattr(self, "beat_clock", None)
        q_t = r_t = None
        if bc is not None:
            try:
                q_t = float(bc.beats) - float(bc.bar_beat)
                r_t = float(bc.bpm) / 60.0
                bpb = float(max(1, int(bc.beats_per_bar)))
                if math.isfinite(q_t) and math.isfinite(r_t) and r_t > 1e-3:
                    self.bpb_clk = bpb
                else:
                    q_t = None
            except Exception:
                q_t = None
        sure = q_t is not None
        if sure and (self.q_clk is None or abs(q_t - self.q_clk) > 64.0 * self.bpb_clk):
            self.q_clk, self.r_clk = q_t, r_t          # first sight (its weight is 0 then) or hopelessly far off
        else:
            if sure:
                self.r_clk = _smooth_to(self.r_clk, r_t, dtc, 0.5)
            if self.q_clk is not None:
                self.q_clk += self.r_clk * dtc
                if sure:
                    self.q_clk = _smooth_to(self.q_clk, q_t, dtc, 0.25)
        self.clock_w = _smooth_to(self.clock_w, 1.0 if sure else 0.0, dtc, 1.0)
        self.q_free = math.fmod(self.q_free + 2.0 * dtc, 4096.0)

        H = self.host
        H[0], H[1], H[2], H[3] = self.w, self.h, aspect, tanf
        # world = Ry(yaw) Rx(pitch down) Rz(roll) cam
        H[4:13] = (cy * cr + sy * sp_ * sr, -cy * sr + sy * sp_ * cr, sy * cp_,
                   cp_ * sr, cp_ * cr, -sp_,
                   -sy * cr + cy * sp_ * sr, sy * sr + cy * sp_ * cr, cy * cp_)
        H[13], H[14], H[15], H[16], H[17] = cam_x, cam_h, kappa, S, z0
        H[18], H[19] = travel, depth
        H[20] = 1.0 if p["direction"] == "away" else -1.0
        H[21], H[22], H[23], H[24] = float(p["lane_glow"]), float(p["glow"]), float(p["bar_lines"]), float(p["glass"])
        H[25] = self.kick
        H[26] = 0.5 + 0.5 * math.sin(2 * math.pi * 0.21 * c)
        H[27] = NOTE_GAIN
        H[28], H[29] = self.q_free, 2.0
        # wrapped by whole bars (float32 keeps the phase exact; which lines are bar lines never changes at the wrap)
        H[30] = math.fmod(self.q_clk, 256.0 * self.bpb_clk) if self.q_clk is not None else 0.0
        H[31] = self.r_clk
        H[32], H[33], H[34] = self.clock_w, 4.0, self.bpb_clk
        # colours: per-pitch table and the grid's tint from the cosine palette
        pal = self._palette(p["palette"])
        a_, b_, c_, d_ = pal[0:3], pal[3:6], pal[6:9], pal[9:12]
        idx = self.idx_register if p["colours"] == "register" else self.idx_fifths
        tt = idx * float(p["palette_period"]) + (self.drift + float(p["hue"]))
        cols = np.clip(a_ + b_ * np.cos(2 * np.pi * (np.multiply.outer(tt, c_) + d_)), 0.0, 1.0)
        H[OFF_COL:OFF_COL + NCOL] = cols.reshape(-1)
        t0 = self.drift + float(p["hue"]) + 0.5 * float(p["palette_period"])
        H[35:38] = 0.55 * np.clip(a_ + b_ * np.cos(2 * np.pi * (c_ * t0 + d_)), 0.0, 1.0) + 0.45
        H[38], H[39], H[40] = self.presence, lw, now
        H[41] = 0.0 if p["direction"] == "away" else TOWARDS_AT * depth
        # the ensemble's register: per-pitch weights (1 everywhere without a window)
        wr = ensemble.note_weights(getattr(getattr(self, "sound", None), "register", None), self.pitches)
        self.wgt[:] = 1.0 if wr is None else wr
        # the lane-light pool: sounding levels (bloomed in), blurred across a few lanes
        pool = H[OFF_POOL:OFF_POOL + NPOOL]
        if lvl is not None:
            bloom = np.clip(self.sound.frame.notes[LOW:HIGH + 1, 2] * 10.0, 0.0, 1.0)
            self.pool_lin[:] = np.minimum(lvl[LOW:HIGH + 1], 1.0) * bloom * bloom * (3.0 - 2.0 * bloom) \
                * self.wgt[LOW:HIGH + 1]
            pool[:NL] = np.convolve(self.pool_lin, self.pool_kernel, mode="same")
            pool[NL:] = 0.0
        else:
            pool[:] = 0.0
        self.dev.set(H)

        iw, ih, bw, bh, W, Hh = self._i
        launch_2d(self._kd, self.w, self.h, (self.ov, self.dev, notes))
        launch_2d(self._kdn, self.bw, self.bh, self._a_down)
        launch_2d(self._kb, self.bw, self.bh, self._a_bh)
        launch_2d(self._kb, self.bw, self.bh, self._a_bv)
        launch_2d(self._km, self.w, self.h, (self.ov, iw, ih, self.bl0, bw, bh, np.float32(2.6 * float(p["glow"]))))
        launch_2d(self._kc, self.ctx.width, self.ctx.height,
                  (src, out, self.ov, W, Hh, iw, ih, np.float32(amount * float(p["opacity"]))))

    def close(self) -> None:
        self.ov = self.dev = self.quiet = self.bl0 = self.bl1 = None
        self._a_down = self._a_bh = self._a_bv = None
