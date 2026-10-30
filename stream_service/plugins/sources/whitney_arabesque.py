"""Whitney arabesque: thousands of glowing dots in John Whitney's differential motion, falling into the held chord's figure.

Source: Original (inspired by John Whitney, "Permutations" 1968, "Arabesque" 1975 and his book "Digital Harmony: On
        the Complementarity of Music and Visual Art", 1980; https://microfest.org/visual-microtonality, Bill Alves on
        Whitney's differential dynamics: "the second traveling twice as fast as the first, the third three times as
        fast as the first, and so on", chaotic scatter as visual dissonance and symmetry as consonance).  Nothing is
        copied: the motion law, the chord-to-figure mapping and all the code are written here from the idea.
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
  A dark room with one large luminous figure in the middle, made of a few hundred to four thousand soft dots that
  leave light trails.  Every dot travels the same path (a spiral, a foil-shaped rose or a looping harmonic curve,
  the ``figure`` morph), but dot number k travels it k times as fast as dot one: Whitney's differential motion.  So
  the dots keep falling into symmetric figures (a three-armed triskelion, a hexafoil, a pinwheel) where their speeds
  line up, and melt into swirls between them.  Dot k also sits on its own nested copy of the path (``depth``), so
  the figure is a rosette of arms from the centre to the rim; with depth low it thins to one fine filigree curve.
  A second field of dots is its mirror image (``counter``): the two wind opposite ways, cross in an arabesque lattice
  when they swirl, and close onto one another into one crisp star when they lock.  ``tilt`` lays the figure into a
  slowly orbiting shallow 3D bowl.

How the music drives it (self.sound, notes; CONVENTIONS 2b)
  * The held chord sets the symmetry.  The sounding pitch classes are tuned as just ratios over the bass (choosing,
    for the seventh, tritone, seconds and minor third, whichever just ratio makes the chord simplest) and written as
    a run of harmonics, e.g. a major triad 4:5:6.  Whitney's order of the chord is the highest harmonic (6), or, read
    downwards like a minor chord (10:12:15 = 1/6 : 1/5 : 1/4), the highest subharmonic (6): the simpler reading wins.
    That order is the number of arms and lobes: one note or an octave is bilateral (2), a fifth three-fold, a major
    or minor triad six-fold, a seventh seven-fold; a cluster or a diminished chord runs to 20 or more arms and swirls.
    Major-like (harmonic) chords wind one way, minor-like (subharmonic) ones are their mirror image.  On a chord
    change every dot moves at most half a turn along its path to its place in the new figure (a permutation), so
    the old figure dissolves and the new one crystallises over about a second.
  * Consonance locks, dissonance swirls: each field's twist (how far its arms wind) is a spring whose rest point is
    low for a consonant chord and high for a dissonant one (``symmetry`` sets how hard consonance locks), and it
    breathes slowly around it.
  * Each note-on kicks the twist's rate (low notes the main field, high notes the mirror field), so playing winds the
    figure up and rests let it settle back into its chord's figure: phrases have a shape.  It also nudges the slide
    of all the dots along their path.
  * Each note-on sends a ring of dots blooming in its pitch-class colour (the circle of fifths through the palette):
    it starts at the note's place in the field (bass near the centre, treble at the rim; with low depth, the same
    stretch of every arm) and spreads outwards; velocity sets how far it travels, how wide and bright it is and how
    long it lasts (loud is big, soft is tender).  Bloom in over 0.09 s, ease out: never a hard flash.
  * Sounding notes (including pedal-held ones) keep a softer band glowing at their place; it eases in with the note.
  * The damper pedal lengthens the trails (x 2.6) and the rings' life, and lifts the glow: resonance.
  * Register (the room's, or the level-weighted mean pitch): low playing makes a wider, deeper nested figure, high
    playing a smaller, finer filigree.  Loudness swells the dots and the brightness.  The main field's ground colour
    follows the bass's pitch class, the mirror field's the top voice's.
  * The ensemble (sound.register) dims notes outside this screen's register window to its ``outside`` level; with a
    room segment (sound.segment) and span: mirror (the default) every other screen draws the figure mirror-imaged,
    so the pair reads as a diptych; each TV still draws the whole figure.  span: screen draws it the same on both.
  * No sound (None or not present): the figure walks a slow cycle of consonant figures (every 11 s) and keeps
    breathing, sliding and orbiting.

Macros: energy -> the slide rate, the breathing's rate and depth, the number of dots (x 0.7 .. 1.3), the brightness
and the twist's rest; pulse -> bloom: brightness, glow and dot size up, plus a nudge to the slide rate (rates and
amplitudes only); speed -> every motion rate; intensity -> output gain; palette / palette_period / drift_speed ->
colour (index = drift + the field's pitch-class hue + period x (0.42 x place + an arm term)).

Per frame: one small upload (the uniform arena: ~470 floats), then the splat (one thread per dot per field: up to
14 sub-steps along the dot's exact arc since the last frame, each a soft gaussian disc added atomically into a float
buffer), the trail pass (trail = max(trail x exp(-dt / tau), new), fused with a 1/4-resolution downsample), a
two-pass blur of that for the glow, the compose (background, tonemap, sRGB) and the upsample.  A chord change adds
one tiny pass that bakes every dot's current place on its path.
"""
from __future__ import annotations

import itertools
import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger(__name__)

MAXP = 4096                  # dots per field
MAXR = 48                    # ripples (note-on rings) in flight
QMAX = 24                    # arms at most
LMAX = 12                    # lobes at most
MAX_ONSETS = 64              # onsets taken per frame
S0, S1 = 0.06, 0.96          # the keyboard's span along the dot index (A0 .. C8)
HOLD_S = 5.0                 # a rest holds the last chord this long before the idle cycle resumes
IDLE_S = 11.0                # the idle cycle's step
STABLE_S = 0.12              # a chord must hold this long to be taken
MIN_GAP_S = 0.3              # and changes come at most this often
LEV_MIN = 0.02               # a note counts as sounding above this LEVEL
TWIST_W = 2.0 * math.pi / 4.2
TWIST_Z = 0.42
TWIST_MAX = 24.0
TWIST_VMAX = 14.0
# the idle cycle: (arms, step, chirality), all consonant-ish
IDLE = ((3, 1, 1), (6, 1, 1), (4, 3, -1), (5, 2, 1), (2, 1, 1), (8, 3, -1), (7, 2, 1), (6, 5, -1), (5, 1, 1),
        (9, 2, 1))
# just ratios per interval above the bass (semitones); alternatives where the tuning is a choice
JUST = {0: ((1, 1),), 1: ((16, 15),), 2: ((9, 8), (10, 9)), 3: ((6, 5), (7, 6)), 4: ((5, 4),), 5: ((4, 3),),
        6: ((45, 32), (10, 7)), 7: ((3, 2),), 8: ((8, 5),), 9: ((5, 3),), 10: ((7, 4), (9, 5), (16, 9)),
        11: ((15, 8),)}

# uniform arena layout (floats)
U_N, U_LAM, U_FIG, U_P = 0, 1, 2, 3
U_EPS = 4                    # epsA cur, prev, epsB cur, prev
U_PSI = 8                    # psiA cur, dpsiA, psiB cur, dpsiB
U_M = 12                     # morph cur, prev
U_ROT = 14                   # rho cur, prev
U_TILT = 16                  # tilt cur, prev
U_BOWL, U_R, U_CX, U_CY, U_FLIP = 18, 19, 20, 21, 22
U_SIG, U_BRI, U_WB = 23, 24, 25
U_PER, U_DRIFT, U_HUE = 26, 27, 28   # hue A, hue B at 28, 29
U_LEVG, U_MOVE = 30, 31
U_PAL = 32                   # 12
U_NRIP = 44
U_BG = 45                    # 3
U_EXPO, U_GLOW, U_GAIN = 48, 49, 50
U_PC = 52                    # 12 x 3
U_RIP = 88                   # MAXR x 8: s, width, amp, r, g, b, wA, wB
N_U = U_RIP + MAXR * 8

_SRC = HELPERS + PALETTE_CUDA + r"""
#define TWO_PI 6.2831853f
#define MAXR """ + str(MAXR) + r"""
#define U_N """ + str(U_N) + r"""
#define U_LAM """ + str(U_LAM) + r"""
#define U_FIG """ + str(U_FIG) + r"""
#define U_P """ + str(U_P) + r"""
#define U_EPS """ + str(U_EPS) + r"""
#define U_PSI """ + str(U_PSI) + r"""
#define U_M """ + str(U_M) + r"""
#define U_ROT """ + str(U_ROT) + r"""
#define U_TILT """ + str(U_TILT) + r"""
#define U_BOWL """ + str(U_BOWL) + r"""
#define U_R """ + str(U_R) + r"""
#define U_CX """ + str(U_CX) + r"""
#define U_CY """ + str(U_CY) + r"""
#define U_FLIP """ + str(U_FLIP) + r"""
#define U_SIG """ + str(U_SIG) + r"""
#define U_BRI """ + str(U_BRI) + r"""
#define U_WB """ + str(U_WB) + r"""
#define U_PER """ + str(U_PER) + r"""
#define U_DRIFT """ + str(U_DRIFT) + r"""
#define U_HUE """ + str(U_HUE) + r"""
#define U_LEVG """ + str(U_LEVG) + r"""
#define U_MOVE """ + str(U_MOVE) + r"""
#define U_PAL """ + str(U_PAL) + r"""
#define U_NRIP """ + str(U_NRIP) + r"""
#define U_BG """ + str(U_BG) + r"""
#define U_EXPO """ + str(U_EXPO) + r"""
#define U_GLOW """ + str(U_GLOW) + r"""
#define U_GAIN """ + str(U_GAIN) + r"""
#define U_PC """ + str(U_PC) + r"""
#define U_RIP """ + str(U_RIP) + r"""
#define KS0 """ + repr(S0) + r"""f
#define KS1 """ + repr(S1) + r"""f

__device__ __forceinline__ float wrap05(float x) { return x - floorf(x + 0.5f); }   // -> [-0.5, 0.5)
__device__ __forceinline__ float fracf(float x) { return x - floorf(x); }
__device__ __forceinline__ float cos_turns(float t) { return __cosf(TWO_PI * fracf(t)); }
__device__ __forceinline__ float sin_turns(float t) { return __sinf(TWO_PI * fracf(t)); }

// The path every dot travels, at phase ph (turns), lobes P (eased float), figure f in [0, 2]:
// 0 spiral (a circle; the nesting makes the spiral), 1 a P-lobed foil (rose), 2 two circular harmonics
// e^{i a} + e^{-i (P - 1) a} (a P-fold looping Lissajous / harmonograph curve).  Non-integer P blends the two
// neighbouring integers, so the path never has a seam.  |z| <= 1.
__device__ float2 wa_path(float ph, float P, float f) {
    const float c = cos_turns(ph), s = sin_turns(ph);
    const float P0 = floorf(P), fr = P - P0, P1 = P0 + 1.f;
    const float ws = clamp01(1.f - f), wr = clamp01(1.f - fabsf(f - 1.f)), wl = clamp01(f - 1.f);
    float2 z = make_float2(0.f, 0.f);
    if (ws > 0.f) { z.x += ws * c; z.y += ws * s; }
    if (wr > 0.f) {
        const float cr = (1.f - fr) * cos_turns(P0 * ph) + fr * cos_turns(P1 * ph);
        const float r = 0.56f + 0.44f * cr;
        z.x += wr * r * c; z.y += wr * r * s;
    }
    if (wl > 0.f) {
        const float t0 = (P0 - 1.f) * ph, t1 = (P1 - 1.f) * ph;
        const float hx = (1.f - fr) * cos_turns(t0) + fr * cos_turns(t1);
        const float hy = -((1.f - fr) * sin_turns(t0) + fr * sin_turns(t1));
        z.x += wl * (0.66f * c + 0.34f * hx);
        z.y += wl * (0.66f * s + 0.34f * hy);
    }
    return z;
}

// A dot's place on screen (internal pixels) and its perspective scale (.z).
__device__ float3 wa_project(float ph, float u, int F, float rho, float tilt, const float* __restrict__ U) {
    float2 z = wa_path(ph, U[U_P], U[U_FIG]);
    z.x *= u; z.y *= u;
    if (F) z.y = -z.y;                                    // the mirror field
    const float cr = cos_turns(rho), sr = sin_turns(rho);
    const float x = cr * z.x - sr * z.y, y = sr * z.x + cr * z.y;
    const float zz = U[U_BOWL] * (u * u - 0.45f);         // a shallow bowl: the rim rises
    float st, ct; __sincosf(tilt, &st, &ct);
    const float y2 = y * ct - zz * st, z2 = y * st + zz * ct;
    const float pz = 3.2f / fmaxf(3.2f + z2, 0.8f);
    return make_float3(U[U_CX] + U[U_FLIP] * x * pz * U[U_R], U[U_CY] + y2 * pz * U[U_R], pz);
}

__device__ __forceinline__ void wa_disc(float* __restrict__ H, int w, int h, float X, float Y, float sig, int rr,
                                        float3 c) {
    const int xi = (int)floorf(X), yi = (int)floorf(Y);
    if (xi + rr < 0 || xi - rr >= w || yi + rr < 0 || yi - rr >= h) return;
    const int WH = w * h;
    const float inv = 1.f / (2.f * sig * sig);
    for (int dy = -rr; dy <= rr; ++dy) {
        const int y = yi + dy;
        if (y < 0 || y >= h) continue;
        const float fy = (float)y + 0.5f - Y;
        for (int dx = -rr; dx <= rr; ++dx) {
            const int x = xi + dx;
            if (x < 0 || x >= w) continue;
            const float fx = (float)x + 0.5f - X;
            const float g = __expf(-(fx * fx + fy * fy) * inv);
            if (g < 0.01f) continue;
            const int i = y * w + x;
            atomicAdd(H + i, c.x * g);
            atomicAdd(H + WH + i, c.y * g);
            atomicAdd(H + 2 * WH + i, c.z * g);
        }
    }
}

// Bake every dot's current offset along the path (on a chord change), so the next morph starts from where it is.
extern "C" __global__ void wa_bake(float* __restrict__ off, const int n, const int q, const int j, const float m)
{
    const int k = blockIdx.x * blockDim.x + threadIdx.x;
    if (k >= n) return;
    const float tgt = (float)((k * j) % q) / (float)q;
    const float o = off[k];
    const float v = o + m * wrap05(tgt - o);
    off[k] = (v == v) ? fracf(v) : 0.f;
}

// One thread per dot (x) per field (y: 0 the main field, 1 its mirror).
extern "C" __global__ void wa_splat(const float* __restrict__ U, const float* __restrict__ off,
                                   const float* __restrict__ notes, const float* __restrict__ regw,
                                   float* __restrict__ H, const int w, const int h, const int nmax,
                                   const int q, const int j)
{
    const int k = blockIdx.x * blockDim.x + threadIdx.x;
    const int F = blockIdx.y;
    if (k >= nmax || F > 1) return;
    const float N = fmaxf(U[U_N], 1.f);
    const float wk = clamp01(N - (float)k);                 // the last dot fades with a fractional count
    const float wF = F ? U[U_WB] : 1.f;
    if (wk * wF < 1e-3f) return;
    const float s = ((float)k + 0.5f) / N;                  // the dot's place: 0 centre .. 1 rim (and the keyboard)
    const float u = 1.f - U[U_LAM] * (1.f - s) * 0.92f;     // its nested copy of the path
    // where it sits along the path: twist (k x eps: the differential motion) + the chord's offset + the slide
    const float tgt = (float)((k * j) % q) / (float)q;
    const float o0 = off[k];
    const float dof = wrap05(tgt - o0);
    const float m1 = U[U_M], m0 = U[U_M + 1];
    const float e1 = U[U_EPS + 2 * F], e0 = U[U_EPS + 2 * F + 1];
    const float ph1 = (float)k * e1 + o0 + m1 * dof + U[U_PSI + 2 * F];
    float dph = (float)k * (e1 - e0) + (m1 - m0) * dof + U[U_PSI + 2 * F + 1];
    if (!(fabsf(dph) < 0.35f)) dph = 0.f;                   // a skip (or NaN): no streak
    // colour: the field's pitch-class hue, its place, its arm
    const float arm = o0 + m1 * dof;
    const float ci = U[U_DRIFT] + U[U_HUE + F] + U[U_PER] * (0.42f * s + 0.12f * (1.f - cos_turns(arm)));
    float3 c = pal(ci, U + U_PAL);
    const float bri = U[U_BRI] * wk * wF * (0.6f + 0.4f * u);
    float3 col = make_float3(c.x * c.x * bri, c.y * c.y * bri, c.z * c.z * bri);
    // note-on rings
    const int nr = min(max((int)U[U_NRIP], 0), MAXR);
    for (int r = 0; r < nr; ++r) {
        const float* R = U + U_RIP + 8 * r;
        const float d = (s - R[0]) / fmaxf(R[1], 1e-3f);
        if (fabsf(d) > 3.f) continue;
        const float a = R[2] * R[6 + F] * __expf(-d * d) * wk * wF;
        col.x += a * R[3]; col.y += a * R[4]; col.z += a * R[5];
    }
    // sounding notes: a soft band at their place, eased in with the note
    const float pf = 21.f + (s - KS0) / (KS1 - KS0) * 87.f;
    const int p0 = (int)floorf(pf);
    for (int dp = -1; dp <= 2; ++dp) {
        const int pp = p0 + dp;
        if (pp < 21 || pp > 108) continue;
        const float lv = notes[pp * 4];
        if (!(lv > 0.003f)) continue;
        const float age = notes[pp * 4 + 2];
        const float ease = (age == age) ? smooth(0.f, 0.12f, age) : 0.f;
        const float dd = (pf - (float)pp) / 0.9f;
        const float a = U[U_LEVG] * fminf(lv, 1.f) * regw[pp] * ease * __expf(-dd * dd) * wk * wF;
        const float* pc = U + U_PC + 3 * (pp % 12);
        col.x += a * pc[0]; col.y += a * pc[1]; col.z += a * pc[2];
    }
    if (!(col.x + col.y + col.z > 1e-5f) || !(col.x + col.y + col.z < 1e4f)) return;
    // the arc since the last frame, in sub-steps
    const float r1 = U[U_ROT], r0 = U[U_ROT + 1], t1 = U[U_TILT], t0 = U[U_TILT + 1];
    const float3 A = wa_project(ph1, u, F, r1, t1, U);
    const float3 B = wa_project(ph1 - dph, u, F, r0, t0, U);
    if (!isfinite(A.x) || !isfinite(A.y)) return;
    const float sig = fminf(fmaxf(U[U_SIG] * A.z, 0.6f), 4.5f);
    const int rr = min(max((int)ceilf(2.4f * sig), 1), 5);
    const int fp = (2 * rr + 1) * (2 * rr + 1);
    const int smax = max(1, min(14, 360 / fp));
    const float dist = sqrtf((A.x - B.x) * (A.x - B.x) + (A.y - B.y) * (A.y - B.y));
    int S = 1;
    if (U[U_MOVE] > 0.5f && isfinite(dist) && dist < 0.4f * (float)h)
        S = min(max((int)ceilf(dist / (0.8f * sig)), 1), smax);
    const float wsub = 1.f / fminf((float)S, 2.5f);
    const float3 cs = make_float3(col.x * wsub * A.z, col.y * wsub * A.z, col.z * wsub * A.z);
    for (int i = 0; i < S; ++i) {
        float3 P = A;
        if (i < S - 1) {
            const float t = (float)(i + 1) / (float)S;     // samples end at the current place
            P = wa_project(ph1 - (1.f - t) * dph, u, F, r0 + (r1 - r0) * t, t0 + (t1 - t0) * t, U);
            if (!isfinite(P.x) || !isfinite(P.y)) continue;
        }
        wa_disc(H, w, h, P.x, P.y, sig, rr, cs);
    }
}

// trail = max(trail * f, new); new <- 0; fused with a 4x4 box downsample of the trail for the glow.
extern "C" __global__ void wa_trail(float* __restrict__ H, float* __restrict__ T, float4* __restrict__ G,
                                   const int w, const int h, const int gw, const int gh, const float f)
{
    const int gx = blockIdx.x * blockDim.x + threadIdx.x, gy = blockIdx.y * blockDim.y + threadIdx.y;
    if (gx >= gw || gy >= gh) return;
    const int WH = w * h;
    float ax = 0.f, ay = 0.f, az = 0.f;
    int n = 0;
    for (int iy = 0; iy < 4; ++iy) {
        const int y = gy * 4 + iy;
        if (y >= h) break;
        for (int ix = 0; ix < 4; ++ix) {
            const int x = gx * 4 + ix;
            if (x >= w) break;
            const int i = y * w + x;
            float v[3];
            #pragma unroll
            for (int ch = 0; ch < 3; ++ch) {
                const float hv = H[ch * WH + i];
                float tv = fmaxf(T[ch * WH + i] * f, hv);
                tv = (tv == tv) ? fminf(fmaxf(tv, 0.f), 64.f) : 0.f;
                T[ch * WH + i] = tv;
                H[ch * WH + i] = 0.f;
                v[ch] = tv;
            }
            ax += v[0]; ay += v[1]; az += v[2];
            ++n;
        }
    }
    const float inv = 1.f / (float)max(n, 1);
    G[gy * gw + gx] = make_float4(ax * inv, ay * inv, az * inv, 0.f);
}

// 9-tap gaussian along (dx, dy) on the quarter-resolution glow.
extern "C" __global__ void wa_blur(const float4* __restrict__ src, float4* __restrict__ dst, const int gw,
                                  const int gh, const int dx, const int dy)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= gw || y >= gh) return;
    const float wt[5] = {0.2041f, 0.1801f, 0.1238f, 0.0663f, 0.0277f};
    float4 a = src[y * gw + x];
    float sx = a.x * wt[0], sy = a.y * wt[0], sz = a.z * wt[0];
    #pragma unroll
    for (int o = 1; o < 5; ++o) {
        const int xa = min(max(x + o * dx, 0), gw - 1), ya = min(max(y + o * dy, 0), gh - 1);
        const int xb = min(max(x - o * dx, 0), gw - 1), yb = min(max(y - o * dy, 0), gh - 1);
        const float4 p = src[ya * gw + xa], m = src[yb * gw + xb];
        sx += wt[o] * (p.x + m.x); sy += wt[o] * (p.y + m.y); sz += wt[o] * (p.z + m.z);
    }
    dst[y * gw + x] = make_float4(sx, sy, sz, 0.f);
}

__device__ float4 wa_glow(const float4* __restrict__ G, float fx, float fy, int gw, int gh) {
    fx = fminf(fmaxf(fx, 0.f), (float)(gw - 1)); fy = fminf(fmaxf(fy, 0.f), (float)(gh - 1));
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const int x1 = min(x0 + 1, gw - 1), y1 = min(y0 + 1, gh - 1);
    const float tx = fx - x0, ty = fy - y0;
    const float4 a = G[y0 * gw + x0], b = G[y0 * gw + x1], c = G[y1 * gw + x0], d = G[y1 * gw + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 0.f);
}

extern "C" __global__ void wa_compose(const float* __restrict__ T, const float4* __restrict__ G,
                                     uchar4* __restrict__ out, const int w, const int h, const int gw,
                                     const int gh, const float* __restrict__ U, const unsigned int fseed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const int i = y * w + x, WH = w * h;
    const float4 g = wa_glow(G, ((float)x + 0.5f) * 0.25f - 0.5f, ((float)y + 0.5f) * 0.25f - 0.5f, gw, gh);
    const float rx = ((float)x - U[U_CX]) / (float)h, ry = ((float)y - U[U_CY]) / (float)h;
    const float wash = __expf(-(rx * rx + ry * ry) * 2.2f);
    const float ex = U[U_EXPO], gl = U[U_GLOW];
    float L[3];
    L[0] = U[U_BG] * wash + T[i] * ex + g.x * gl;
    L[1] = U[U_BG + 1] * wash + T[WH + i] * ex + g.y * gl;
    L[2] = U[U_BG + 2] * wash + T[2 * WH + i] * ex + g.z * gl;
    const float n = (hash1((unsigned int)x, (unsigned int)y, fseed) - 0.5f) * (1.2f / 255.f);
    float4 o;
    #pragma unroll
    for (int ch = 0; ch < 3; ++ch) {
        const float l = (L[ch] == L[ch]) ? fmaxf(L[ch], 0.f) : 0.f;
        const float tm = 1.f - __expf(-l);
        const float v = fminf(fmaxf(sqrtf(tm) * U[U_GAIN] + n, 0.f), 1.f) * 255.f;
        if (ch == 0) o.x = v; else if (ch == 1) o.y = v; else o.z = v;
    }
    o.w = 255.f;
    out[i] = pack(o);
}
"""
_OPTS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap05(x: float) -> float:
    return x - math.floor(x + 0.5)


def _fifths(pc: int) -> float:
    return ((pc * 7) % 12) / 12.0


def _dissonance(order: int) -> float:
    """0 for orders up to 5 (octave, fifth, fourth, thirds), ~0.1 a triad (6), ~0.5 a major seventh (15), 1 at 40+."""
    return min(max((math.log2(max(order, 1)) - math.log2(5.0)) / (math.log2(40.0) - math.log2(5.0)), 0.0), 1.0)


def chord_shape(bass_pc: int, pcs) -> tuple[int, int]:
    """Whitney's order of a chord (pitch classes ``pcs`` over the bass ``bass_pc``) and its chirality.

    Each interval above the bass is tuned as a just ratio (where the tuning is a choice, the one that makes the chord
    simplest), the chord is written as harmonics n_1 : n_2 : ... (a major triad 4:5:6) and as subharmonics
    1/u_1 : 1/u_2 : ... (a minor triad 10:12:15 = 1/6 : 1/5 : 1/4); the order is the smaller of max(n) and max(u).
    Returns (order, chirality): +1 harmonic (major-like), -1 subharmonic (minor-like), 0 both equal.  One pitch class
    (a note or octaves) is the octave, 2:1: order 2."""
    ivs = sorted({(int(pc) - int(bass_pc)) % 12 for pc in pcs} | {0})
    if len(ivs) == 1:
        return 2, 0
    best = None
    for combo in itertools.product(*(JUST[i] for i in ivs)):
        L = 1
        for _, d in combo:
            L = math.lcm(L, d)
        ns = [p * (L // d) for p, d in combo]
        g = 0
        for n in ns:
            g = math.gcd(g, n)
        ns = [n // g for n in ns]
        o_order = max(ns)
        K = 1
        for n in ns:
            K = math.lcm(K, n)
        u_order = max(K // n for n in ns)
        cand = (min(o_order, u_order), 0 if o_order == u_order else (1 if o_order < u_order else -1))
        if best is None or cand[0] < best[0]:
            best = cand
    return best


SPANS = ("screen", "mirror")  # mirror: every other screen of the room draws the figure mirror-imaged


class WhitneyArabesque(Source):
    name = "whitney_arabesque"
    label = "Whitney arabesque"
    description = ("Thousands of glowing dots in John Whitney's differential motion: the held chord's just ratios "
                   "lock them into symmetric figures, playing swirls them, each note blooms a coloured ring.")
    listens = ("notes",)
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.75, period=1.0, drift=0.02),
        "points": num(0.0, 1.0, 0.5, "Points", basic=True),
        "figure": num(0.0, 2.0, 1.0, "Figure (spiral, rose, loops)", basic=True),
        "trails": num(0.0, 1.0, 0.45, "Trails", basic=True),
        "symmetry": num(0.0, 1.0, 0.6, "Symmetry (how hard chords lock)"),
        "depth": num(0.0, 1.0, 0.6, "Depth (nested layers)"),
        "counter": num(0.0, 1.0, 1.0, "Mirror field"),
        "tilt": num(0.0, 1.0, 0.3, "Tilt (3D bowl)"),
        "dot_size": num(0.4, 2.5, 1.0, "Dot size"),
        "span": enum(SPANS, "mirror", "Pair (same on both TVs, or mirror-imaged)"),
    }

    # -- lifecycle -------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.mirror = params.get("span", "mirror") == "mirror"
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.gw, self.gh = (self.w + 3) // 4, (self.h + 3) // 4
        mod = raw_module(_SRC, _OPTS)
        self.k_bake = mod.get_function("wa_bake")
        self.k_splat = mod.get_function("wa_splat")
        self.k_trail = mod.get_function("wa_trail")
        self.k_blur = mod.get_function("wa_blur")
        self.k_comp = mod.get_function("wa_compose")
        self.H = cp.zeros(3 * self.w * self.h, cp.float32)
        self.T = cp.zeros(3 * self.w * self.h, cp.float32)
        self.G = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.G2 = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.full = (self.w, self.h) == (ctx.width, ctx.height)
        self.buf = None if self.full else cp.zeros((self.h, self.w, 4), cp.uint8)
        self.Uh = np.zeros(N_U, np.float32)
        self.Ug = cp.zeros(N_U, cp.float32)
        self.zero_notes = cp.zeros((128, 4), cp.float32)
        self.regw = cp.ones(128, cp.float32)
        self.regw_h = np.ones(128, np.float32)
        self._reg_key = None
        seed = int(params["seed"]) & 0x7FFFFFFF
        self.rng = np.random.default_rng(seed)
        # the figure: arms q, step j, morph m (0..1, eased in time), chirality, dissonance
        self.idle_i = int(self.rng.integers(len(IDLE)))
        q, j, chi = IDLE[self.idle_i]
        self.q, self.j = q, j
        k = np.arange(MAXP, dtype=np.int64)
        self.off = cp.asarray(((k * j) % q / q).astype(np.float32))
        self.m, self.m_dur = 1.0, 1.0
        self.m_prev = 1.0             # the eased morph at the last frame (0 right after a retarget)
        self.q_eff = float(q)
        self.P_eff = float(min(q, LMAX))
        self.P_t = float(min(q, LMAX))
        self.chi, self.chi_t = float(chi), float(chi)
        self.d, self.d_t = _dissonance(q), _dissonance(q)
        self.since_change = 10.0
        self.cand, self.cand_t = None, 0.0
        self.quiet = HOLD_S + 1.0
        self.idle_t = IDLE_S * 0.5
        self._shape_cache: dict = {}
        # motion
        self.th = [1.2, 1.4]
        self.thv = [0.0, 0.0]
        self.psi = [float(self.rng.random()), float(self.rng.random())]
        self.bph = [float(self.rng.random()), float(self.rng.random())]
        self.rho = float(self.rng.random())
        self.cam = float(self.rng.random())
        self.drift = 0.0
        self.N = None
        self.eps_prev = [None, None]
        self.rho_prev = self.rho
        self.tilt_prev = None
        self.flip = 1.0
        self.kenv = 0.0
        # listening state
        self.loud = 0.0
        self.ped = 0.0
        self.reg = 0.5
        self.hue = [float(self.rng.random()), float(self.rng.random())]
        self.hue_t = list(self.hue)
        sp = self.session_palette()
        self.pal = np.asarray(palette_coeffs(params["palette"]) if sp is None else np.asarray(sp).reshape(-1),
                              np.float64)
        # ripples (note-on rings)
        self.r_on = np.zeros(MAXR, bool)
        self.r_age = np.zeros(MAXR)
        self.r_s0 = np.zeros(MAXR)
        self.r_ds = np.zeros(MAXR)
        self.r_wid = np.zeros(MAXR)
        self.r_amp = np.zeros(MAXR)
        self.r_life = np.ones(MAXR)
        self.r_pc = np.zeros(MAXR, np.int64)
        self.r_wa = np.zeros(MAXR)
        self.r_wb = np.zeros(MAXR)
        self.r_env = np.zeros(MAXR)
        self.frame_n = 0

    # -- the chord -------------------------------------------------------------------------------------------------
    def _shape(self, bass_pc: int, pcs: tuple) -> tuple[int, int]:
        key = (bass_pc, pcs)
        v = self._shape_cache.get(key)
        if v is None:
            if len(self._shape_cache) > 4096:
                self._shape_cache.clear()
            v = self._shape_cache[key] = chord_shape(bass_pc, pcs)
        return v

    def _hear(self, fr):
        """(arms, step, chirality, dissonance, bass pc, top pc, mean register) of what sounds now, or None."""
        lev = np.nan_to_num(np.asarray(fr.notes[:, 0], np.float64), nan=0.0, posinf=0.0, neginf=0.0)
        mx = float(lev.max()) if lev.size else 0.0
        if mx <= LEV_MIN:
            return None
        snd = np.flatnonzero(lev > max(LEV_MIN, 0.12 * mx))
        if snd.size == 0:
            return None
        bass, top = int(snd[0]), int(snd[-1])
        pcw = np.bincount(snd % 12, weights=lev[snd], minlength=12)
        order = np.argsort(-pcw)
        keep = [int(pc) for pc in order[:5] if pcw[pc] >= 0.2 * pcw[order[0]]]
        pcs = tuple(sorted(set(keep) | {bass % 12}))
        o, chi = self._shape(bass % 12, pcs)
        q = int(min(max(o, 2), QMAX))
        if chi == 0:
            chi = 1 if self.chi_t >= 0 else -1
        j = 1 if chi > 0 else q - 1
        w = lev[snd]
        mreg = float(np.sum(w * (snd - 21.0)) / max(float(np.sum(w)), 1e-6)) / 87.0
        return q, j, chi, _dissonance(o), bass % 12, top % 12, min(max(mreg, 0.0), 1.0)

    def _retarget(self, q: int, j: int, kick: float) -> None:
        if (q, j) == (self.q, self.j):
            return
        sm = _ss(0.0, 1.0, self.m)
        n = MAXP
        self.k_bake(((n + 255) // 256, 1, 1), (256, 1, 1),
                    (self.off, np.int32(n), np.int32(self.q), np.int32(self.j), np.float32(sm)))
        self.q, self.j = q, j
        self.m = 0.0
        self.m_prev = 0.0
        self.P_t = float(min(q, LMAX))
        self.since_change = 0.0
        self.thv[0] = min(self.thv[0] + kick, TWIST_VMAX)
        self.thv[1] = min(self.thv[1] + kick, TWIST_VMAX)

    def _reg_weights(self, snd) -> None:
        """Per-pitch weights for the ensemble's register window (sound.register; outside it notes dim to its
        ``outside`` level, never hidden), cached per window, on the host (rings, kicks) and the GPU (sounding bands)."""
        reg = getattr(snd, "register", None) if snd is not None else None
        try:
            key = tuple(float(x) for x in reg) if reg else None
        except (TypeError, ValueError):
            key = None
        if key == self._reg_key:
            return
        self._reg_key = key
        w = np.ones(128, np.float32)
        if key:
            try:
                from stream_service.ensemble import note_weights
                nw = note_weights(reg, np.arange(128))
                if nw is not None:
                    w = np.nan_to_num(np.asarray(nw, np.float32), nan=1.0).clip(0.0, 1.0)
            except Exception:  # noqa: BLE001  (degrade: draw everything at full)
                log.debug("note_weights failed", exc_info=True)
        self.regw_h = w
        self.regw.set(w)

    # -- the frame -------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.25)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        sdt = dt * spd
        sym = float(p["symmetry"])
        self.frame_n += 1

        # palette (the session palette blends in)
        sp = self.session_palette()
        tp = np.asarray(palette_coeffs(p["palette"]) if sp is None else np.asarray(sp, np.float64).reshape(-1),
                        np.float64)
        if dt > 0 and tp.shape == self.pal.shape:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / 0.8))
        self.drift = (self.drift + float(p["drift_speed"]) * sdt) % 1000.0
        period = float(p["palette_period"])

        # ---- listening
        snd = self.sound
        fr = getattr(snd, "frame", None) if snd is not None else None
        heard = bool(snd is not None and fr is not None and getattr(snd, "present", False))
        self._reg_weights(snd)
        a = lambda tau: (1.0 - math.exp(-dt / tau)) if dt > 0 else 0.0  # noqa: E731
        self.since_change += dt
        heard_chord = None
        ped_t = loud_t = 0.0
        reg_t = 0.5
        if heard:
            try:
                ped_t = min(max(float(fr.pedals[0]), 0.0), 1.0)
            except Exception:  # noqa: BLE001
                ped_t = 0.0
            heard_chord = self._hear(fr)
            lev = np.asarray(fr.notes[:, 0], np.float64)
            loud_t = min(float(np.nansum(np.clip(lev, 0.0, 1.0))) * 0.35, 1.2)
        if heard_chord is not None:
            q, j, chi, d, bpc, tpc, mreg = heard_chord
            reg_t = mreg
            fr_reg = getattr(fr, "register", None)
            if fr_reg is not None:
                try:
                    v = float(fr_reg)
                    reg_t = min(max(v, 0.0), 1.0) if math.isfinite(v) else mreg
                except (TypeError, ValueError):
                    reg_t = mreg
            self.hue_t = [_fifths(bpc), _fifths(tpc)]
            self.quiet = 0.0
            key = (q, j)
            if key != self.cand:
                self.cand, self.cand_t = key, 0.0
            else:
                self.cand_t += dt
            if self.cand_t >= STABLE_S:
                self.d_t, self.chi_t = d, float(chi)
                if self.since_change >= MIN_GAP_S:
                    self._retarget(q, j, 0.6 + 1.2 * d)
        else:
            self.cand = None
            was_quiet = self.quiet > HOLD_S
            reg_t = 0.5 if was_quiet else self.reg      # a rest holds the register, the idle cycle centres it
            self.quiet += dt
            if self.quiet > HOLD_S:
                self.idle_t = self.idle_t + dt if was_quiet else IDLE_S   # the hold ends: the cycle resumes now
                if self.idle_t >= IDLE_S:
                    self.idle_t = 0.0
                    self.idle_i = (self.idle_i + 1) % len(IDLE)
                    q, j, chi = IDLE[self.idle_i]
                    self.d_t, self.chi_t = _dissonance(q), float(chi)
                    self._retarget(q, j, 0.5)
                for i in range(2):              # the ground colours wander
                    self.hue_t[i] = (self.hue_t[i] + (0.004 + 0.003 * i) * sdt) % 1.0
        self.ped += (ped_t - self.ped) * a(0.3)
        self.loud += (loud_t - self.loud) * a(0.25)
        self.reg += (reg_t - self.reg) * a(1.5)
        for i in range(2):
            self.hue[i] = (self.hue[i] + _wrap05(self.hue_t[i] - self.hue[i]) * a(1.2)) % 1.0

        # ---- note-ons: rings and kicks
        pcs_rgb = self._pc_colours(period)
        if heard:
            ons = list(getattr(fr, "onsets", ()) or ())[:MAX_ONSETS]
            regw = self.regw_h
            kgain = 1.0 / math.sqrt(max(len(ons), 1))     # a chord (or a burst) kicks like ~sqrt(n) notes
            for on in ons:
                try:
                    pitch, vel, age = int(on[0]), float(on[1]), float(on[2])
                except Exception:  # noqa: BLE001
                    continue
                if not (0 <= pitch < 128) or not math.isfinite(vel):
                    continue
                vel = min(max(vel, 0.0), 1.0)
                age = min(max(age, 0.0), 0.3) if math.isfinite(age) else 0.0
                rw = float(regw[pitch])
                hi = _ss(52.0, 76.0, float(pitch))
                self._ring(pitch, vel, age, rw, hi)
                kick = 1.6 * (0.6 + 0.8 * e) * vel * rw * kgain
                self.thv[0] = min(self.thv[0] + kick * (1.0 - 0.65 * hi), TWIST_VMAX)
                self.thv[1] = min(self.thv[1] + kick * (0.35 + 0.65 * hi), TWIST_VMAX)
                self.kenv = min(self.kenv + 0.45 * vel * rw, 1.5)
        self.kenv *= math.exp(-dt / 0.5) if dt > 0 else 1.0

        # ---- the figure's state
        self.m_dur = 1.25 - 0.45 * e
        self.m = min(self.m + dt / self.m_dur, 1.0)
        m_prev, m1 = self.m_prev, _ss(0.0, 1.0, self.m)
        self.m_prev = m1
        lq = math.log(self.q_eff) + (math.log(self.q) - math.log(self.q_eff)) * a(0.45)
        self.q_eff = math.exp(lq)
        self.P_eff += (self.P_t - self.P_eff) * a(0.5)
        self.chi += (self.chi_t - self.chi) * a(0.6)
        self.d += (self.d_t - self.d) * a(0.8)
        # twist springs: rest low for consonance, high for dissonance; breathing; kicks already in thv
        base = 0.85 + (1.0 - sym) * 1.6 + self.d * (1.5 + 4.5 * (1.0 - 0.5 * sym)) + 0.5 * e
        amp = 0.15 + 0.85 * (1.0 - sym) + 0.35 * e
        brate = (0.045 + 0.07 * e) * spd
        for i in range(2):
            self.bph[i] = (self.bph[i] + brate * (1.0 + 0.27 * i) * dt) % 1.0
            tgt = base * (1.0 + 0.12 * i) + amp * math.sin(2.0 * math.pi * self.bph[i])
            self.thv[i] = min(self.thv[i] + 2.0 * pulse * dt, TWIST_VMAX)   # the pulse leans on the twist's rate
            n_sub = min(8, max(1, int(math.ceil(sdt * 120.0)))) if sdt > 0 else 0
            h = sdt / n_sub if n_sub else 0.0
            th, v = self.th[i], self.thv[i]
            for _ in range(n_sub):
                acc = -TWIST_W * TWIST_W * (th - tgt) - 2.0 * TWIST_Z * TWIST_W * v
                v = min(max(v + acc * h, -TWIST_VMAX), TWIST_VMAX)
                th += v * h
                if th < 0.1:
                    th, v = 0.1, max(v, 0.0)
                elif th > TWIST_MAX:
                    th, v = TWIST_MAX, min(v, 0.0)
            self.th[i], self.thv[i] = th, v
        # dot count
        pts = float(p["points"])
        n_t = min(max((250.0 + 3800.0 * pts ** 1.6) * (0.7 + 0.6 * e), 64.0), float(MAXP))
        self.N = n_t if self.N is None else self.N + (n_t - self.N) * a(0.6)
        N = self.N
        eps = [self.chi * self.th[i] / (self.q_eff * N) for i in range(2)]
        if self.eps_prev[0] is None:
            self.eps_prev = list(eps)
        # slide along the path, rotation, camera
        rate = (0.010 + 0.03 * e) * (1.0 + 0.8 * self.kenv + 0.5 * pulse) * spd
        dpsi = [rate * dt, 0.91 * rate * dt]           # the mirror field: same sense of phase, so it counter-turns
        for i in range(2):
            self.psi[i] = (self.psi[i] + dpsi[i]) % 1.0
        rho_prev = self.rho
        self.rho = (self.rho + 0.0045 * (0.6 + 0.8 * e) * sdt) % 1.0
        self.cam = (self.cam + 0.021 * sdt) % 1.0
        tilt = float(p["tilt"]) * (0.62 + 0.12 * math.sin(2.0 * math.pi * self.cam))
        if self.tilt_prev is None:
            self.tilt_prev = tilt
        seg = getattr(snd, "segment", None) if snd is not None and self.mirror else None
        flip_t = -1.0 if (seg and len(seg) == 2 and int(seg[1]) >= 2 and int(seg[0]) % 2 == 1) else 1.0
        self.flip += (flip_t - self.flip) * (a(0.4) if dt > 0 else 1.0)

        # ---- ripples
        nr = self._ripples(dt, pcs_rgb)

        # ---- the arena
        U = self.Uh
        lam = min(max(float(p["depth"]) + 0.6 * (0.5 - self.reg), 0.0), 1.0)
        U[U_N], U[U_LAM], U[U_FIG], U[U_P] = N, lam, float(p["figure"]), max(self.P_eff, 1.0)
        U[U_EPS:U_EPS + 4] = (eps[0], self.eps_prev[0], eps[1], self.eps_prev[1])
        U[U_PSI:U_PSI + 4] = (self.psi[0], dpsi[0], self.psi[1], dpsi[1])
        U[U_M], U[U_M + 1] = m1, m_prev
        U[U_ROT], U[U_ROT + 1] = self.rho, self.rho - _wrap05(self.rho - rho_prev)
        U[U_TILT], U[U_TILT + 1] = tilt, self.tilt_prev
        U[U_BOWL] = 0.4 * float(p["tilt"])
        scale = 0.46 * (0.93 + 0.16 * (0.5 - self.reg))
        U[U_R], U[U_CX], U[U_CY], U[U_FLIP] = scale * self.h, 0.5 * self.w, 0.5 * self.h, self.flip
        U[U_SIG] = min(max(float(p["dot_size"]) * (1.0 + 0.45 * min(self.loud, 1.0) + 0.15 * pulse)
                           * 1.1 * self.h / 1080.0, 0.6), 4.5)
        U[U_BRI] = 0.42 * (1000.0 / N) ** 0.3 * (0.7 + 0.5 * e) * (0.75 + 0.5 * min(self.loud, 1.0))
        U[U_WB] = float(p["counter"])
        U[U_PER], U[U_DRIFT] = period, self.drift
        U[U_HUE], U[U_HUE + 1] = self.hue[0] * period, self.hue[1] * period
        U[U_LEVG] = 0.9
        U[U_MOVE] = 1.0 if dt > 0 else 0.0
        U[U_PAL:U_PAL + 12] = self.pal
        U[U_NRIP] = nr
        bg = self._pal(self.drift + self.hue[0] * period + 0.5) ** 2
        U[U_BG:U_BG + 3] = bg * (0.018 + 0.016 * e + 0.02 * self.ped)
        U[U_EXPO] = (0.85 + 0.5 * e) * (1.0 + 0.5 * pulse)
        U[U_GLOW] = 0.45 + 0.35 * self.ped + 0.5 * pulse + 0.15 * self.kenv
        U[U_GAIN] = float(p["intensity"])
        U[U_PC:U_PC + 36] = pcs_rgb.reshape(-1)
        np.nan_to_num(U, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.Ug.set(U)
        self.eps_prev = list(eps)
        self.tilt_prev = tilt

        # ---- draw
        notes = getattr(snd, "notes_gpu", None) if snd is not None else None
        if notes is None:
            notes = self.zero_notes
        nc = int(min(math.ceil(N), MAXP))
        self.k_splat(((nc + 127) // 128, 2, 1), (128, 1, 1),
                     (self.Ug, self.off, notes, self.regw, self.H, np.int32(self.w), np.int32(self.h), np.int32(nc),
                      np.int32(self.q), np.int32(self.j)))
        tau = (0.04 * 60.0 ** float(p["trails"])) * (1.0 + 1.6 * self.ped)
        f = math.exp(-dt / min(max(tau, 0.02), 6.0)) if dt > 0 else 1.0
        g2 = ((self.gw + 15) // 16, (self.gh + 15) // 16, 1)
        self.k_trail(g2, (16, 16, 1), (self.H, self.T, self.G, np.int32(self.w), np.int32(self.h),
                                       np.int32(self.gw), np.int32(self.gh), np.float32(f)))
        self.k_blur(g2, (16, 16, 1), (self.G, self.G2, np.int32(self.gw), np.int32(self.gh), np.int32(1), np.int32(0)))
        self.k_blur(g2, (16, 16, 1), (self.G2, self.G, np.int32(self.gw), np.int32(self.gh), np.int32(0), np.int32(1)))
        dst = out if self.full else self.buf
        self.k_comp(((self.w + 31) // 32, (self.h + 7) // 8, 1), (32, 8, 1),
                    (self.T, self.G, dst, np.int32(self.w), np.int32(self.h), np.int32(self.gw), np.int32(self.gh),
                     self.Ug, np.uint32(self.frame_n & 0xFFFF)))
        if not self.full:
            present(self.ctx, self.buf, out)

    # -- helpers ---------------------------------------------------------------------------------------------------
    def _pal(self, t) -> np.ndarray:
        P = self.pal
        t = np.asarray(t, np.float64)
        c = P[0:3] + P[3:6] * np.cos(2.0 * math.pi * (P[6:9] * t[..., None] + P[9:12]))
        return np.clip(c, 0.0, 1.0)

    def _pc_colours(self, period: float) -> np.ndarray:
        """Linear, unit-max colour per pitch class (circle of fifths through the palette)."""
        t = self.drift + np.array([_fifths(pc) for pc in range(12)]) * period
        c = self._pal(t) ** 2
        return (c / np.maximum(c.max(axis=1, keepdims=True), 0.05)).astype(np.float64)

    def _ring(self, pitch: int, vel: float, age: float, rw: float, hi: float) -> None:
        free = np.flatnonzero(~self.r_on)
        i = int(free[0]) if free.size else int(np.argmin(self.r_env))
        self.r_on[i] = True
        self.r_age[i] = age
        self.r_s0[i] = S0 + (min(max(pitch, 21), 108) - 21) / 87.0 * (S1 - S0)
        self.r_ds[i] = 0.07 + 0.30 * vel
        self.r_wid[i] = 0.02 + 0.03 * vel
        self.r_amp[i] = (0.5 + 1.4 * vel) * rw
        self.r_life[i] = (0.45 + 1.1 * vel) * (1.0 + 1.6 * self.ped)
        self.r_pc[i] = pitch % 12
        self.r_wa[i] = 1.0 - 0.6 * hi
        self.r_wb[i] = 0.4 + 0.6 * hi
        self.r_env[i] = 0.0

    def _ripples(self, dt: float, pcs_rgb: np.ndarray) -> int:
        on = np.flatnonzero(self.r_on)
        if on.size == 0:
            return 0
        self.r_age[on] += dt
        age = self.r_age[on]
        rise = np.clip(age / 0.09, 0.0, 1.0)
        rise = rise * rise * (3.0 - 2.0 * rise)
        env = rise * np.exp(-np.maximum(age - 0.09, 0.0) / np.maximum(self.r_life[on], 0.05))
        self.r_env[on] = env
        dead = on[(env < 0.004) & (age > 0.09)]
        self.r_on[dead] = False
        live = on[~np.isin(on, dead)][:MAXR]
        n = live.size
        if n == 0:
            return 0
        R = self.Uh[U_RIP:U_RIP + MAXR * 8].reshape(MAXR, 8)
        a = self.r_age[live]
        R[:n, 0] = self.r_s0[live] + self.r_ds[live] * (1.0 - np.exp(-a / 0.55))
        R[:n, 1] = self.r_wid[live] * (1.0 + 0.6 * (1.0 - np.exp(-a / 0.8)))
        R[:n, 2] = self.r_amp[live] * self.r_env[live]
        R[:n, 3:6] = pcs_rgb[self.r_pc[live]]
        R[:n, 6] = self.r_wa[live]
        R[:n, 7] = self.r_wb[live]
        return int(n)
