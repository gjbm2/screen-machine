"""Across the room: every note the piano plays throws a glowing shape from its key, high over the room in a luminous
parabola, to land further along as a softer echo that ripples out across a dark floor.  Across both TVs it is one
wide picture: low notes fly from the left TV over the gap to the right one, high notes from the right to the left.

What you see.  A dark night stage: a faint haze breathing over the horizon, a dark reflective floor, and along the
bottom a faint row of key marks, one per piano key (A0 on the left, C8 on the right; across both TVs the keyboard
spans the pair, the bass on the south TV and the treble on the north).  Each note launches a soft glowing shape (a
dot, a ring or a spinning flower: the `shape` knob morphs between them) from its key.  It flies in a true ballistic
arc, leaving a luminous trail, lands further along with a little squash, and bounces once more as a smaller, dimmer
echo; where it lands, rings ripple out over the floor and a soft imprint glows and fades.  Arcs near the floor
reflect in it.  Bass notes launch from the left and fly right, treble notes from the right and fly left, so the
arcs cross over the middle (over the gap between the TVs) and weave; notes near the middle of the keyboard rise in
tall narrow fountains, the extremes throw long and low across the room.  With no notes the scene stays alive: the
haze drifts, the floor line breathes, and a slow ghost melody of dim arcs keeps launching (it fades away as soon
as real notes come and returns after a few quiet seconds).

How the music drives it.  Each strike (``sound.frame.onsets``) launches one shape: loud notes throw higher and
further, with bigger, brighter shapes; soft ones are small, low and tender.  Register sets the launch place, the
direction and the weight (low notes are larger and slower, high ones lighter and quicker).  A chord launches a
volley that fans out.  The key marks glow while their notes sound (the notes table's level, so the pedal's
sustain keeps them lit).  The damper pedal is resonance: shapes launched with it down leave long trails that
hang as whole luminous parabolas, and their landing ripples and imprints linger; lifting it damps the trails
(they fade over a fraction of a second; pressing it later never relights an old trail).  Flights bloom in over
~0.1 s (the strike always lands late at the TV: no hard flash) and ease out.

One picture across the room (``sound.segment``, ``(k, n)``): the world is n screens wide and this screen draws
x in [k, k + 1] of it, so a note thrown on the south TV lands on the north one.  Every screen hears the same
onsets at the same photon time, so a flight is a pure function of its onset: its shape, height, length, spin and
depth come from a hash of (pitch, onset time in ms), and its position from its age on the shared photon clock.
No per-screen randomness.  The idle ghost melody is scheduled on the same clock.  With ``segment`` None the
whole picture is on this screen, and the ensemble's register window (``sound.register``) dims notes outside it.

Macros: energy -> trail glow and halo size, the haze's brightness and drift, the head halos, the ghost melody's
rate, the launch sparkle's density; pulse (bind note_on) -> launch sparkle (glints around shapes just thrown) and
a flare of the head and the floor line; speed -> flight pace of new launches (positions never jump), haze and
breathing; intensity -> output gain; palette / period / drift colour the shapes (register plus a circle-of-fifths
offset, so a chord is several hues) and tint the haze and floor.  Regional knobs: arc height, reach (how far a
note flies), flight time, shape (dot -> ring -> petal), shape size, trails, echo (the second bounce, the ripples
and the imprint), floor reflection.

Rendering: one kernel at ``render_scale`` (0.75), then ``present``.  Flights live in a host ring of 160 records
(a launch and its echo bounce are two), recomputed each frame in numpy and uploaded in one copy with the globals.
Each 32 x 8 tile first culls the records against its own rectangle (bounding box, then the exact distance from
the tile centre to the trail's parabola, and its mirror image in the floor) into a shared list; each pixel then
lights only those: the closest point on the parabola is an exact cubic solve (no iterations), the head is an SDF
glow, the ripples are perspective ellipses.

Source: Original (inspired by Toshio Iwai, "Piano - as image media" (1995), https://en.wikipedia.org/wiki/Toshio_Iwai ,
        and Iwai x Ryuichi Sakamoto, "Music Plays Images x Images Play Music" (Prix Ars Electronica 1997),
        https://webarchive.ars.electronica.art/en/archives/prix_archive/prix_projekt.asp%3FiProjectID=2494.html ;
        the idea only, from descriptions of the works: nothing copied)
Author: smfx
License: MIT
license_class: permissive
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.iwai_parabolas")

LOW, HIGH = 21, 108                    # A0 .. C8
N_MAX = 160                            # flight records (a launch and its echo bounce are two); <= a tile's threads
NF = 36                                # floats per record (see the kernel's table)
NG = 40                                # globals
Y_G = 0.12                             # the keyboard line, screen heights from the bottom
MARGIN = 0.03                          # the key row's inset, a fraction of the world's width
SLOT_S = 0.4                           # the idle ghost melody's schedule (s, on the shared clock)
IDLE_AFTER_S = 5.0                     # quiet this long and the ghost melody returns
GHOST = 0.9                            # ghost brightness
GHOST_LINGER = 3.0                     # ghost trails hang this much longer (a dreamy idle)
MAX_ONSETS = 64                        # per frame (a burst beyond this keeps the newest)
JUMP_MS = 3000.0                       # a clock jump this big rebases the flights instead of dropping them

# static record columns (host)
(C_P0X, C_P0Y, C_AX, C_AY, C_BX, C_BY, C_T, C_SIZE, C_BRIGHT, C_CI, C_PET, C_SPIN, C_PHASE, C_BLOOM, C_RAMP,
 C_VR, C_IMP, C_XL, C_VEL) = range(19)
NS = 19
_LIN9 = np.linspace(0.0, 1.0, 9)

_OPTS = ("--std=c++14", "--use_fast_math")

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NF 36
#define NMAX 160
#define REFL_D 0.2f

// Record layout (F, NF floats each; world units = screen heights, x across the whole room, y up from the bottom):
//  [0..5]  P0.xy, A.xy, B.xy: the parabola B(t) = P0 + 2tA + t^2 B, t in 0..1 (A = P1 - P0, B = P0 - 2P1 + P2)
//  [6] trail start t  [7] head t  [8] flight time T (s)  [9] age (s)  [10] trail fade rate (1/s)
//  [11] size  [12] brightness  [13] trail alpha  [14] petals  [15] cos spin  [16] head alpha  [17] squash (1..0.4)
//  [18] time since landing (-1: none)  [19] ripple amplitude  [20] ripple speed  [21] ground fade rate
//  [22] imprint alpha  [23] landing x  [24..27] arc box x0 y0 x1 y1  [28] ground half width (0: none)
//  [29] ground half height  [30] arc cull radius  [31] sparkle  [32..34] colour  [35] sin spin
// Globals (G): [0] count [1] this screen's left x [2] world width [3] aspect [4] keyboard line y [5] first key x
//  [6] key pitch (x per semitone) [7..18] palette [19] period [20] drift [21] energy [22] kick [23] intensity
//  [24] twinkle phase [25] haze phase [26] breath 0..1 [27] shape 0..2 [28] trail glow [29] floor reflection
//  [30] star density [31] idle

__device__ __forceinline__ float sq(float v) { return v * v; }
__device__ __forceinline__ float win3(float x) {            // 1 at 0, smoothly 0 from 1 (compact: exact culling)
    const float a = 1.f - x * x;
    return a > 0.f ? a * a * a : 0.f;
}

// The closest point of B(t), t in [ta, tb], to (px, py): d^2, and t in *tout.  |B(t) - P|^2 is a quartic in t;
// its critical points solve a cubic, solved in closed form (Cardano, or the trigonometric form for three roots).
// The minimum over [ta, tb] is at a root clamped to the range or at an end.
__device__ float closest(const float* __restrict__ r, float px, float py, float ta, float tb, float* tout) {
    const float Ax = r[2], Ay = r[3], Bx = r[4], By = r[5];
    const float Dx = r[0] - px, Dy = r[1] - py;
    const float ib = 1.f / fmaxf(Bx * Bx + By * By, 1e-8f);
    const float a = 3.f * (Ax * Bx + Ay * By) * ib;
    const float b = (Dx * Bx + Dy * By + 2.f * (Ax * Ax + Ay * Ay)) * ib;
    const float c = (Dx * Ax + Dy * Ay) * ib;
    const float p = b - a * a * (1.f / 3.f);
    const float q = a * (2.f * a * a - 9.f * b) * (1.f / 27.f) + c;
    const float disc = 0.25f * q * q + p * p * p * (1.f / 27.f);
    const float sh = -a * (1.f / 3.f);
    float r0, r1, r2;
    if (disc >= 0.f) {
        const float sd = sqrtf(disc);
        r0 = cbrtf(-0.5f * q + sd) + cbrtf(-0.5f * q - sd) + sh;
        r1 = r0; r2 = r0;
    } else {
        const float m = 2.f * sqrtf(fmaxf(-p * (1.f / 3.f), 0.f));
        const float pm = p * m;
        const float arg = pm < -1e-30f ? fminf(fmaxf(3.f * q / pm, -1.f), 1.f) : 0.f;
        const float th = acosf(arg) * (1.f / 3.f);
        r0 = m * cosf(th) + sh;
        r1 = m * cosf(th - 2.0943951f) + sh;
        r2 = m * cosf(th - 4.1887902f) + sh;
    }
    float best = 3.0e38f, bt = ta;
    const float cand[5] = {ta, tb, r0, r1, r2};
    #pragma unroll
    for (int i = 0; i < 5; ++i) {
        const float t = fminf(fmaxf(cand[i], ta), tb);
        const float dx = r[0] + t * (2.f * Ax + t * Bx) - px;
        const float dy = r[1] + t * (2.f * Ay + t * By) - py;
        const float d2 = dx * dx + dy * dy;
        if (d2 < best) { best = d2; bt = t; }
    }
    *tout = bt;
    return best;
}

// The flight's light at (x, y): its trail (lit where the head passed, fading with the time since) and its head
// (a dot / ring / petal glow, squashed as it lands), plus glints of the star field around a head just thrown.
__device__ void arc_light(const float* __restrict__ r, float x, float y, float e, float tg, float wd, float wr,
                          float wp, float star, float& lc, float& lw) {
    const float size = r[11];
    const float bright = r[12];
    if (r[13] > 0.f) {
        float t;
        const float d2 = closest(r, x, y, r[6], r[7], &t);
        const float since = fmaxf(r[9] - t * r[8], 0.f);
        // fresh near the head, settling to a softer glow where it lingers (pedal-held arcs never stack to white)
        const float tb = __expf(-since * r[10]) * (0.45f + 0.55f * __expf(-2.5f * since)) * r[13];
        const float wc = size * (0.12f + 0.22f * tb);
        const float rh = size * (2.2f + 1.6f * e);
        lc += bright * tb * ((0.7f + 0.35f * tg) * __expf(-d2 / (wc * wc)) + (0.08f + 0.2f * tg) * win3(sqrtf(d2) / rh));
        lw += bright * tb * tb * 0.22f * __expf(-d2 / (0.3f * wc * wc));
    }
    if (r[16] > 0.f) {
        const float u = r[7];
        const float hx = r[0] + u * (2.f * r[2] + u * r[4]);
        const float hy = r[1] + u * (2.f * r[3] + u * r[5]);
        const float sqz = r[17];
        const float dx = (x - hx) / (size * (1.f + 0.6f * (1.f - sqz)));
        const float dy = (y - hy) / (size * sqz);
        const float lx = r[15] * dx + r[35] * dy, ly = -r[35] * dx + r[15] * dy;
        const float rho2 = lx * lx + ly * ly;
        const float hr = 4.2f * (1.f + 0.3f * e);
        if (rho2 < hr * hr) {
            const float rho = sqrtf(rho2);
            float g = wd * __expf(-2.2f * rho2);
            if (wr > 0.f) g += wr * (__expf(-sq((rho - 0.85f) * 6.f)) + 0.15f * __expf(-3.f * rho2));
            if (wp > 0.f) {
                const float th = atan2f(ly, lx);
                const float cp = fabsf(cosf(0.5f * r[14] * th));
                const float rp = 0.34f + 0.66f * cp * cp;
                g += wp * (__expf(-2.4f * rho2 / (rp * rp)) + 0.22f * __expf(-9.f * rho2));
            }
            const float halo = win3(rho / hr) * (0.12f + 0.16f * e);
            const float a = bright * r[16];
            lc += a * (g + halo);
            lw += a * __expf(-7.f * rho2) * (0.75f - 0.55f * wr);
            if (r[31] > 0.f) lc += r[31] * star * win3(rho / 3.6f);
        }
    }
}

// Where it landed: rings spreading over the floor (perspective ellipses) and a soft imprint that widens and fades.
__device__ float ground_light(const float* __restrict__ r, float x, float y) {
    const float tau = r[18];
    if (tau < 0.f) return 0.f;
    const float size = r[11];
    const float dx = x - r[23], dy = (y - r[1]) * (1.f / 0.22f);
    const float dist = sqrtf(dx * dx + dy * dy);
    float L = 0.f;
    #pragma unroll
    for (int j = 0; j < 3; ++j) {
        const float tj = tau - 0.17f * (float)j;
        if (tj > 0.f) {
            const float R = size + r[20] * tj;
            const float w = size * 0.3f + 0.01f * tj;
            const float amp = r[19] * __expf(-tj * r[21]) * smooth(0.f, 0.12f, tj) * (1.f - 0.3f * (float)j);
            L += amp * __expf(-sq((dist - R) / w));
        }
    }
    const float s = size * (1.f + 1.3f * (1.f - __expf(-2.5f * tau)));
    L += r[22] * __expf(-2.f * dist * dist / (s * s));
    return L;
}

// A world-fixed field of glints (lit by heads passing with the pulse).
__device__ float starfield(float x, float y, unsigned int seed, float ph, float dens) {
    const float gx = x * (1.f / 0.016f), gy = y * (1.f / 0.016f);
    const float fx = floorf(gx), fy = floorf(gy);
    const unsigned int ix = (unsigned int)(int)fx, iy = (unsigned int)(int)fy;
    if (hash1(ix, iy, seed + 101u) > dens) return 0.f;
    const float ox = 0.2f + 0.6f * hash1(ix, iy, seed + 102u), oy = 0.2f + 0.6f * hash1(ix, iy, seed + 103u);
    const float d2 = sq(gx - fx - ox) + sq(gy - fy - oy);
    const float tw = 0.5f + 0.5f * __sinf(6.2831853f * (hash1(ix, iy, seed + 104u) + ph));
    return __expf(-d2 * 90.f) * tw;
}

__device__ __forceinline__ float knee(float v) {           // linear to 0.62, then a soft shoulder to 1
    v = fmaxf(v, 0.f);
    return v < 0.62f ? v : 0.62f + 0.38f * (1.f - __expf(-(v - 0.62f) * (1.f / 0.38f)));
}

extern "C" __global__ void ip_render(uchar4* __restrict__ out, const float* __restrict__ G,
                                     const float* __restrict__ F, const float* __restrict__ notes,
                                     int W, int H, unsigned int seed)
{
    __shared__ int s_idx[NMAX];
    __shared__ unsigned char s_m[NMAX];
    __shared__ int s_n;
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    const int nth = blockDim.x * blockDim.y;
    if (tid == 0) s_n = 0;
    __syncthreads();

    const float invH = 1.f / (float)H;
    const float kA = G[1], yg = G[4];
    const float e = G[21];
    const float floorR = G[29];
    // this tile, in world units; cull the flights against it (and its mirror image in the floor)
    const float tx0 = kA + (float)(blockIdx.x * blockDim.x) * invH;
    const float tx1 = tx0 + (float)blockDim.x * invH;
    const float ty1 = 1.f - (float)(blockIdx.y * blockDim.y) * invH;
    const float ty0 = ty1 - (float)blockDim.y * invH;
    const float cx = 0.5f * (tx0 + tx1), cy = 0.5f * (ty0 + ty1);
    const float hd = 0.5f * sqrtf(sq(tx1 - tx0) + sq(ty1 - ty0));
    const int count = min(max((int)G[0], 0), NMAX);
    for (int i = tid; i < count; i += nth) {
        const float* r = F + i * NF;
        unsigned int m = 0u;
        const float gy = r[1];
        const bool direct = !(r[24] > tx1 || r[26] < tx0 || r[25] > ty1 || r[27] < ty0);
        const bool fl = floorR > 0.f && ty0 < gy && ty1 > gy - REFL_D;
        const float my0 = 2.f * gy - ty1, my1 = 2.f * gy - ty0;
        const bool mir = fl && !(r[24] > tx1 || r[26] < tx0 || r[25] > my1 || r[27] < my0);
        if (direct || mir) {
            float t, d2 = 3.0e38f;
            if (direct) d2 = closest(r, cx, cy, r[6], r[7], &t);
            if (mir) d2 = fminf(d2, closest(r, cx, 2.f * gy - cy, r[6], r[7], &t));
            const float rr = r[30] + hd;
            if (d2 < rr * rr) m |= 1u;
        }
        if (r[28] > 0.f && fabsf(cx - r[23]) < r[28] + hd && fabsf(cy - gy) < r[29] + hd) m |= 2u;
        if (m) {
            const int j = atomicAdd(&s_n, 1);
            s_idx[j] = i;
            s_m[j] = (unsigned char)m;
        }
    }
    __syncthreads();

    const int px = blockIdx.x * blockDim.x + threadIdx.x;
    const int py = blockIdx.y * blockDim.y + threadIdx.y;
    if (px >= W || py >= H) return;
    const float x = kA + ((float)px + 0.5f) * invH;
    const float y = 1.f - ((float)py + 0.5f) * invH;
    const float* P = G + 7;
    const float period = G[19], drift = G[20], kick = G[22];
    const float Wd = fmaxf(G[2], 1e-3f);
    const float breath = G[26];
    const float hz = G[25];

    // ---- the ground and the air: a dark night, a breathing haze over the horizon, a darker reflective floor
    const float3 gc = pal(0.12f * period + 0.3f * drift, P);
    const float3 hc = pal(0.55f * period + 0.3f * drift, P);
    const float lift = sq(1.f - clamp01(y));
    float cr = 0.004f + gc.x * (0.012f + 0.03f * lift);
    float cg = 0.005f + gc.y * (0.012f + 0.03f * lift);
    float cb = 0.010f + gc.z * (0.012f + 0.03f * lift);
    const float nz = 0.62f * vnoise(x * 1.25f + hz, y * 2.6f - 0.4f * hz, seed)
                   + 0.38f * vnoise(x * 2.9f - 0.7f * hz, y * 5.3f + 0.25f * hz, seed + 7u);
    const float band = smooth(0.42f, 0.86f, nz) * (0.016f + 0.05f * e) * (0.3f + 0.7f * (1.f - smooth(yg, 0.9f, y)));
    cr += hc.x * band; cg += hc.y * band; cb += hc.z * band;
    if (y < yg) {
        const float sheen = 0.025f * __expf(-(yg - y) * 33.f);
        cr = 0.5f * cr + hc.x * sheen; cg = 0.5f * cg + hc.y * sheen; cb = 0.5f * cb + hc.z * sheen;
    }
    const float ends = smooth(0.f, 0.05f * Wd, x) * smooth(0.f, 0.05f * Wd, Wd - x);
    const float line = __expf(-sq((y - yg) * 450.f)) * (0.05f + 0.03f * breath + 0.07f * kick) * ends;
    cr += 0.80f * line; cg += 0.86f * line; cb += 1.0f * line;
    // the key marks, faint, glowing in the note's colour while it sounds
    if (y > yg - 0.05f && y < yg + 0.09f) {
        const float kx0 = G[5], kdx = fmaxf(G[6], 1e-5f);
        const int i0 = (int)floorf((x - kx0) / kdx + 0.5f);
        for (int o = -1; o <= 1; ++o) {
            const int j = i0 + o;
            if (j < 0 || j > 87) continue;
            const int pitch = 21 + j;
            const int pc = pitch % 12;
            const bool black = pc == 1 || pc == 3 || pc == 6 || pc == 8 || pc == 10;
            const float xj = kx0 + (float)j * kdx;
            const float ln = black ? 0.013f : 0.024f;
            const float ytop = yg - 0.005f, ybot = ytop - ln;
            const float tick = __expf(-sq((x - xj) * 700.f)) * smooth(ybot - 0.002f, ybot + 0.002f, y)
                             * (1.f - smooth(ytop - 0.0015f, ytop + 0.0015f, y));
            const float base = (0.045f + 0.02f * breath) * (black ? 0.65f : 1.f) * tick;
            cr += 0.80f * base; cg += 0.86f * base; cb += 1.0f * base;
            const float lvl = clamp01(notes[pitch * 4]);
            if (lvl > 0.002f) {
                const float ci = 0.62f * (float)j * (1.f / 87.f) + 0.38f * (float)((pitch * 7) % 12) * (1.f / 12.f);
                const float3 kc = pal(ci * period + drift, P);
                const float pool = __expf(-sq((x - xj) / (0.6f * kdx))) * __expf(-fabsf(y - yg) * 55.f) * 0.22f;
                const float k = lvl * (1.1f * tick + pool);
                cr += kc.x * k; cg += kc.y * k; cb += kc.z * k;
            }
        }
    }

    // ---- the flights this tile kept
    const float tg = G[28];
    const float sh = fminf(fmaxf(G[27], 0.f), 2.f);
    const float wd = clamp01(1.f - sh), wr = clamp01(1.f - fabsf(sh - 1.f)), wp = clamp01(sh - 1.f);
    const float star = kick > 0.01f ? starfield(x, y, seed, G[24], G[30]) : 0.f;
    float ar = 0.f, ag = 0.f, ab = 0.f, aw = 0.f;
    const int nl = s_n;
    for (int j = 0; j < nl; ++j) {
        const float* r = F + s_idx[j] * NF;
        const unsigned int m = s_m[j];
        float lc = 0.f, lw = 0.f, lg = 0.f;
        if (m & 1u) {
            arc_light(r, x, y, e, tg, wd, wr, wp, star, lc, lw);
            if (floorR > 0.f && y < r[1] && r[1] - y < REFL_D) {
                float lc2 = 0.f, lw2 = 0.f;
                arc_light(r, x, 2.f * r[1] - y, e, tg, wd, wr, wp, 0.f, lc2, lw2);
                const float att = floorR * 0.42f * __expf(-(r[1] - y) * 22.f);
                lc += att * lc2; lw += att * lw2;
            }
        }
        if (m & 2u) lg = ground_light(r, x, y);
        const float k = lc + 0.85f * lg;
        ar += r[32] * k + 0.1f * lg;
        ag += r[33] * k + 0.1f * lg;
        ab += r[34] * k + 0.1f * lg;
        aw += lw;
    }

    // ---- compose: a vignette over the whole room (the seam between screens stays continuous), gain, shoulder
    const float vx = fabsf(2.f * x / Wd - 1.f);
    const float vig = (1.f - 0.28f * vx * vx * vx) * (1.f - 0.18f * smooth(0.78f, 1.02f, y));
    const float gain = G[23] * vig;
    const float R = (cr + ar + 1.00f * aw) * gain;
    const float Gc = (cg + ag + 0.97f * aw) * gain;
    const float B = (cb + ab + 0.92f * aw) * gain;
    out[py * W + px] = make_uchar4((unsigned char)(255.f * knee(R) + 0.5f), (unsigned char)(255.f * knee(Gc) + 0.5f),
                                   (unsigned char)(255.f * knee(B) + 0.5f), 255);
}
"""


# ------------------------------------------------------------------------------------------------- host helpers
_M64 = 0xFFFFFFFFFFFFFFFF


def _mix(*vals: int) -> int:
    """A 64-bit hash of integers (splitmix64 steps): the same on every screen for the same onset."""
    x = 0x243F6A8885A308D3
    for v in vals:
        x = (x ^ ((int(v) & _M64) + 0x9E3779B97F4A7C15 + ((x << 6) & _M64) + (x >> 2))) & _M64
        x ^= x >> 30
        x = (x * 0xBF58476D1CE4E5B9) & _M64
        x ^= x >> 27
        x = (x * 0x94D049BB133111EB) & _M64
        x ^= x >> 31
    return x


def _units(key: int, n: int) -> list[float]:
    return [(_mix(key, i) >> 11) / float(1 << 53) for i in range(n)]


def _smooth01(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _pal_np(t: np.ndarray, P: np.ndarray) -> np.ndarray:
    """The cosine palette on the host: [n] -> [n, 3] (the kernel's ``pal``)."""
    a, b, c, d = P[0:3], P[3:6], P[6:9], P[9:12]
    return np.clip(a + b * np.cos(2.0 * math.pi * (c * t[:, None] + d)), 0.0, 1.0)


def _segment(snd) -> tuple[int, int]:
    """(k, n): this screen's place among the room's n screens (left to right), else (0, 1)."""
    seg = getattr(snd, "segment", None) if snd is not None else None
    try:
        k, n = int(seg[0]), int(seg[1])
    except (TypeError, ValueError, IndexError):
        return 0, 1
    return (k, n) if n >= 2 and 0 <= k < n else (0, 1)


def _clip01(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return min(max(v, 0.0), 1.0) if v == v else 0.0


class IwaiParabolas(Source):
    """Per frame: the host steps a 160-record flight ring in numpy (launch, age, cull boxes, colours), uploads it
    with the globals in one copy, and issues one kernel launch plus the present."""
    name = "iwai_parabolas"
    label = "Across the room"
    description = ("Every note throws a glowing shape from its key in a luminous parabola that lands further along "
                   "as a rippling echo; across both TVs one picture, bass flying left to right and treble right to "
                   "left over the gap.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="dusk", render_scale=0.75, period=1.0, drift=0.02),
            "arc_height": num(0.3, 1.4, 1.0, "Arc height"),
            "reach": num(0.4, 1.25, 0.85, "Reach"),
            "flight": num(0.7, 3.0, 1.6, "Flight time", unit="s"),
            "shape": num(0.0, 2.0, 1.0, "Shape (dot, ring, petal)", basic=True),
            "size": num(0.5, 2.0, 1.0, "Shape size"),
            "trail": num(0.0, 1.0, 0.5, "Trails", basic=True),
            "echo": num(0.0, 1.0, 0.6, "Echo"),
            "floor": num(0.0, 1.0, 0.6, "Floor reflection"),
        }

    # -- setup ----------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.A = self.w / float(self.h)
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, _OPTS).get_function("ip_render")
        self.arena = cp.zeros(NG + N_MAX * NF, cp.float32)
        self.G_d, self.F_d = self.arena[:NG], self.arena[NG:]
        self.host = np.zeros(NG + N_MAX * NF, np.float32)
        self.Gh, self.Fh = self.host[:NG], self.host[NG:].reshape(N_MAX, NF)
        self.quiet = cp.zeros((128, 4), cp.float32)          # the notes table when nothing is heard
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        # the flight ring
        self.used = np.zeros(N_MAX, bool)
        self.ghost = np.zeros(N_MAX, bool)
        self.t0 = np.zeros(N_MAX)                           # launch time on the shared clock (ms)
        self.S = np.zeros((N_MAX, NS))
        self.S[:, C_T] = 1.0                                # unused rows stay finite in the vector maths
        self.S[:, C_BLOOM] = 1.0
        self.k_tr = np.ones(N_MAX)                          # fade rates: only ever rise (a lifted pedal damps)
        self.k_gr = np.ones(N_MAX)
        self.lng = np.ones(N_MAX)                           # linger factor (ghosts hang longer)
        # clocks and eased state
        self.own_ms = 0.0
        self.last_now = None
        self.last_slot = None
        self.t_last_on = -math.inf
        snd = getattr(self, "sound", None)                 # mid-performance: no ghost melody to fade first
        self.idle = 0.0 if snd is not None and getattr(snd, "present", False) else 1.0
        self.ped = 0.0
        self.kick = 0.0
        self.haze = 0.0
        self.breath = 0.0
        self.twinkle = 0.0
        self.drift = 0.0
        sp = self.session_palette()
        self.pal = np.asarray(sp if sp is not None else palette_coeffs(params["palette"]), np.float64).reshape(12)
        self.Wd, self.kA = self.A, 0.0
        self._reg_key, self._reg_w = None, None
        self._ktr_now = self._kgr_now = 1.0

    # -- the flights ----------------------------------------------------------------------------------------------
    def _rates(self, p: dict) -> tuple[float, float]:
        """Fade rates (1/s) for a launch now: trails (the trail knob, the pedal) and ripples (echo, pedal)."""
        trail = min(max(float(p["trail"]), 0.0), 1.0)
        echo = min(max(float(p["echo"]), 0.0), 1.0)
        tau_tr = (0.06 + 0.75 * trail * trail) * (1.0 + 3.0 * self.ped)
        tau_gr = (0.45 + 0.9 * echo) * (1.0 + 2.0 * self.ped)
        return 1.0 / tau_tr, 1.0 / tau_gr

    def _slot(self) -> int:
        free = np.flatnonzero(~self.used)
        if free.size:
            return int(free[0])
        return int(np.argmin(self.t0))                     # full: the oldest launch makes way

    def _add(self, t0: float, x0: float, y0: float, dx: float, hgt: float, T: float, size: float, bright: float,
             ci: float, pet: float, spin: float, phase: float, bloom: float, ramp: float, vr: float, imp: float,
             vel: float, ghost: bool) -> None:
        i = self._slot()
        row = self.S[i]
        row[C_P0X], row[C_P0Y] = x0, y0
        row[C_AX], row[C_AY] = 0.5 * dx, 2.0 * hgt             # P1 = (x0 + dx / 2, y0 + 2 hgt): apex hgt at t = 0.5
        row[C_BX], row[C_BY] = 0.0, -4.0 * hgt
        row[C_T], row[C_SIZE], row[C_BRIGHT], row[C_CI] = T, size, bright, ci
        row[C_PET], row[C_SPIN], row[C_PHASE], row[C_BLOOM] = pet, spin, phase, bloom
        row[C_RAMP], row[C_VR], row[C_IMP], row[C_XL], row[C_VEL] = ramp, vr, imp, x0 + dx, vel
        lng = GHOST_LINGER if ghost else 1.0
        self.k_tr[i], self.k_gr[i], self.lng[i] = self._ktr_now / lng, self._kgr_now, lng
        self.ghost[i] = ghost
        self.t0[i] = t0
        self.used[i] = True

    def _launch(self, pitch: int, vel: float, t_on: float, ghost: bool, p: dict, w_reg: float) -> None:
        """One note: the main flight and its echo bounce, a pure function of (pitch, vel, onset ms) and the knobs."""
        h = _units(_mix(pitch, int(round(t_on)), 0x1A1), 8)
        r = min(max((pitch - LOW) / float(HIGH - LOW), 0.0), 1.0)
        Wd = self.Wd
        xk = Wd * (MARGIN + (1.0 - 2.0 * MARGIN) * r)
        d = 1.0 if (r - 0.5) + 0.07 * (h[0] - 0.5) < 0.0 else -1.0          # bass flies right, treble left
        tf = 0.22 + 0.78 * min(1.0, abs(2.0 * r - 1.0))                    # the extremes throw furthest
        travel = Wd * 0.8 * float(p["reach"]) * tf * (0.55 + 0.45 * vel) * (0.88 + 0.24 * h[1])
        xl = min(max(xk + d * max(travel, 0.12), 0.015 * Wd), 0.985 * Wd)
        zf = h[2]                                                          # depth: further is smaller, dimmer
        zs = 1.08 - 0.3 * zf
        y0 = Y_G + 0.03 * zf
        hgt = float(p["arc_height"]) * (0.22 + 0.78 * vel ** 0.85) * (0.8 + 0.2 * tf) * (0.9 + 0.2 * h[3]) * zs
        top, kn = 0.95 - y0, 0.7 * (0.95 - y0)                             # a soft ceiling near the top
        hgt = max(hgt if hgt < kn else kn + (top - kn) * math.tanh((hgt - kn) / (top - kn)), 0.05)
        spd = max(float(p["speed"]), 0.25)
        T = min(max(float(p["flight"]) * math.sqrt(hgt / 0.5) * (1.18 - 0.36 * r) / spd, 0.35), 6.0)
        size = float(p["size"]) * (0.008 + 0.02 * vel) * (1.2 - 0.4 * r) * zs
        bright = (0.32 + 0.68 * vel ** 0.8) * (1.0 - 0.3 * zf) * w_reg * (GHOST if ghost else 1.0)
        ci = 0.62 * r + 0.38 * (((pitch * 7) % 12) / 12.0)
        echo = min(max(float(p["echo"]), 0.0), 1.0)
        pet = 4.0 + math.floor(h[4] * 3.0)
        spin, phase = (h[5] - 0.5) * 2.4, h[6] * 2.0 * math.pi
        ramp = bright * (0.3 + 0.7 * vel) * (0.35 + 0.65 * echo)
        vr = (0.1 + 0.12 * vel) * zs
        imp = bright * (0.18 + 0.32 * echo)
        self._add(t_on, xk, y0, xl - xk, hgt, T, size, bright, ci, pet, spin, phase, 0.09, ramp, vr, imp, vel, ghost)
        if echo > 0.05:                                                    # the softer bounce, from the landing
            h2 = hgt * (0.12 + 0.24 * echo)
            if h2 >= 0.025:
                x2 = min(max(xl + d * abs(xl - xk) * (0.12 + 0.2 * echo), 0.01 * Wd), 0.99 * Wd)
                T2 = max(T * math.sqrt(h2 / hgt), 0.25)
                self._add(t_on + 1000.0 * T, xl, y0, x2 - xl, h2, T2, 0.72 * size, bright * (0.3 + 0.35 * echo), ci,
                          pet, spin, phase + 1.0, 0.03, 0.5 * ramp, 0.8 * vr, 0.6 * imp, 0.6 * vel, ghost)

    def _ghosts(self, now: float, e: float, p: dict) -> None:
        """The idle melody: a slow wandering line of dim arcs on the shared clock (the same on every screen)."""
        slot = int(math.floor(now / (SLOT_S * 1000.0)))
        last = self.last_slot
        self.last_slot = slot
        if self.idle < 0.02:
            return
        if last is None:                                                   # first frame: arcs already in the air
            last = slot - 9
        prob = 0.3 + 0.5 * e                                               # 0.75 .. 2 launches a second
        for s in range(max(last + 1, slot - 8), slot + 1):                 # at most 9 slots (a skip)
            h = _units(_mix(0x6057, s), 6)
            if h[0] >= prob:
                continue
            centre = 0.5 + 0.4 * math.sin(s * 0.047 + 1.3) * math.cos(s * 0.0131 + 0.4)
            pitch = LOW + int(round((HIGH - LOW) * min(max(centre + (h[1] - 0.5) * 0.36, 0.0), 1.0)))
            vel = 0.3 + 0.45 * h[2]
            t = (s + 0.8 * h[3]) * SLOT_S * 1000.0                         # may be ahead: it waits (pending)
            self._launch(pitch, vel, t, True, p, 1.0)
            if h[4] < 0.2:                                                 # now and then a two-note volley
                p2 = pitch + (7 if h[5] < 0.5 else -5)
                if LOW <= p2 <= HIGH:
                    self._launch(p2, 0.85 * vel, t + 35.0, True, p, 1.0)

    def _records(self, now: float, e: float, kick: float, period: float, a_damp: float) -> int:
        """Age every record, retire the finished ones, and pack the visible ones (with cull boxes) into Fh."""
        used = self.used
        if not used.any():
            return 0
        S = self.S
        age = (now - self.t0) * 0.001
        live = used & (age >= 0.0)
        ag = np.where(live, age, 0.0)
        T = S[:, C_T]
        if a_damp > 0.0:                                   # fade rates only rise: lifting the pedal damps
            for k, k_now in ((self.k_tr, self._ktr_now / self.lng), (self.k_gr, self._kgr_now)):
                np.copyto(k, k + (np.maximum(k, k_now) - k) * a_damp, where=live)
        k = self.k_tr
        kg = self.k_gr
        uh = np.clip(ag / T, 0.0, 1.0)
        land = ag - T
        lt = np.maximum(land, 0.0)
        ut = np.minimum(np.clip((ag - 5.5 / k) / T, 0.0, 1.0), uh)
        bl = _smooth01(ag / S[:, C_BLOOM])
        gm = np.where(self.ghost, self.idle, 1.0)
        bright = S[:, C_BRIGHT] * gm
        head = bl * np.exp(-lt / 0.16) * (1.0 + 0.5 * kick * np.exp(-ag / 0.4))
        trail_end = bl * np.exp(-lt * k)
        arc = live & (np.maximum(head, trail_end) * bright > 0.004)
        landed = live & (land >= 0.0)
        ramp = S[:, C_RAMP] * gm
        imp0 = S[:, C_IMP] * gm
        gv = landed & (np.maximum(ramp * np.exp(-lt * kg), imp0 * np.exp(-1.3 * lt * kg)) > 0.003)
        dead = live & (land > 0.0) & ~arc & ~gv
        used[dead] = False
        idx = np.flatnonzero(live & (arc | gv))[:N_MAX]
        c = int(idx.size)
        if c == 0:
            return 0
        F = self.Fh
        Si = S[idx]
        size = Si[:, C_SIZE]
        arc_i, gv_i, lt_i, ag_i = arc[idx], gv[idx], lt[idx], ag[idx]
        ut_i, uh_i = ut[idx], uh[idx]
        F[:c, 0:6] = Si[:, 0:6]
        F[:c, 6] = ut_i
        F[:c, 7] = uh_i
        F[:c, 8] = Si[:, C_T]
        F[:c, 9] = ag_i
        F[:c, 10] = k[idx]
        F[:c, 11] = size
        F[:c, 12] = bright[idx]
        F[:c, 13] = np.where(arc_i, bl[idx], 0.0)
        F[:c, 14] = Si[:, C_PET]
        spin = Si[:, C_PHASE] + Si[:, C_SPIN] * ag_i
        F[:c, 15] = np.cos(spin)
        F[:c, 35] = np.sin(spin)
        F[:c, 16] = np.where(arc_i, head[idx], 0.0)
        sqz = 1.0 - 0.6 * _smooth01((land[idx] + 0.05) / 0.17)
        F[:c, 17] = sqz
        F[:c, 18] = np.where(gv_i, lt_i, -1.0)
        F[:c, 19] = ramp[idx]
        F[:c, 20] = Si[:, C_VR]
        F[:c, 21] = kg[idx]
        F[:c, 22] = np.where(gv_i, imp0[idx] * np.exp(-1.3 * lt_i * kg[idx]) * _smooth01(lt_i / 0.1), 0.0)
        F[:c, 23] = Si[:, C_XL]
        # cull boxes: the visible trail sampled at 9 points (a parabola bulges <= hgt / 64 between them), + radius
        R = size * np.maximum(4.2 * (1.0 + 0.3 * e) * (1.0 + 0.6 * (1.0 - sqz)), 2.2 + 1.6 * e) + 0.004
        ts = ut_i[:, None] + (uh_i - ut_i)[:, None] * _LIN9[None, :]
        X = Si[:, C_P0X, None] + ts * (2.0 * Si[:, C_AX, None] + ts * Si[:, C_BX, None])
        Y = Si[:, C_P0Y, None] + ts * (2.0 * Si[:, C_AY, None] + ts * Si[:, C_BY, None])
        m = R + 0.02
        F[:c, 24] = np.where(arc_i, X.min(1) - m, 1e9)
        F[:c, 25] = np.where(arc_i, Y.min(1) - m, 1e9)
        F[:c, 26] = np.where(arc_i, X.max(1) + m, -1e9)
        F[:c, 27] = np.where(arc_i, Y.max(1) + m, -1e9)
        F[:c, 30] = R
        ring = size + Si[:, C_VR] * lt_i + 3.0 * (0.3 * size + 0.01 * lt_i)
        gx = np.maximum(ring, 4.0 * size)
        F[:c, 28] = np.where(gv_i, gx, 0.0)
        F[:c, 29] = 0.22 * gx + 0.003
        F[:c, 31] = np.where(arc_i, 0.9 * kick * np.exp(-ag_i / 0.6) * (0.5 + Si[:, C_VEL]) * gm[idx], 0.0)
        F[:c, 32:35] = _pal_np(Si[:, C_CI] * period + self.drift, self.pal)
        np.nan_to_num(F[:c], copy=False, nan=0.0, posinf=1e9, neginf=-1e9)
        return c

    def _register_w(self, snd, n: int):
        """Per-pitch weights for the ensemble's register window (only when this screen shows the whole picture)."""
        reg = getattr(snd, "register", None) if snd is not None and n == 1 else None
        if not reg:
            return None
        key = tuple(reg) if isinstance(reg, (list, tuple)) else reg
        if key != self._reg_key:
            try:
                from stream_service.ensemble import note_weights
                w = note_weights(reg, np.arange(128))
                self._reg_w = None if w is None else np.asarray(w, np.float64)
            except Exception:                              # a bad window draws everything at full
                self._reg_w = None
            self._reg_key = key
        return self._reg_w

    # -- render ---------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = float(dt) if (dt == dt and 0.0 < dt < 10.0) else 0.0
        e = _clip01(p["energy"])
        pulse = _clip01(p["pulse"])
        spd = max(float(p["speed"]), 0.0)
        if dt > 0.0:
            tau = 0.05 if pulse > self.kick else 0.3      # kicks ease in (they land late anyway), then fall away
            self.kick += (pulse - self.kick) * (1.0 - math.exp(-dt / tau))
        self.haze += (0.03 + 0.07 * e) * spd * dt
        self.breath += 0.11 * spd * dt
        self.twinkle = (self.twinkle + (1.6 + 1.4 * e) * spd * dt) % 1.0
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 1000.0
        sp = self.session_palette()
        tp = np.asarray(sp if sp is not None else palette_coeffs(p["palette"]), np.float64).reshape(12)
        if dt > 0.0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / 0.8))
        self.own_ms += dt * 1000.0

        # the shared clock: the sound's photon time (the same on every screen), else our own
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        now = self.own_ms
        if f is not None:
            try:
                tm = float(f.t_ms)
                if math.isfinite(tm):
                    now = tm
            except (TypeError, ValueError, AttributeError):
                pass
        if self.last_now is not None:
            jump = now - (self.last_now + dt * 1000.0)
            if abs(jump) > JUMP_MS:                        # a clock change: keep the flights where they were
                self.t0 += jump
                self.t_last_on += jump
                self.last_slot = None
        self.last_now = now

        k, n = _segment(snd)
        self.Wd, self.kA = n * self.A, k * self.A
        w_reg = self._register_w(snd, n)
        ped_t = 0.0
        if f is not None:
            try:
                ped_t = _clip01(f.pedals[0])
            except (TypeError, IndexError, AttributeError):
                ped_t = 0.0
        if dt > 0.0:
            tau = 0.12 if ped_t > self.ped else 0.35
            self.ped += (ped_t - self.ped) * (1.0 - math.exp(-dt / tau))
        self._ktr_now, self._kgr_now = self._rates(p)

        # this frame's strikes: one flight each (a burst keeps the newest MAX_ONSETS)
        ons = list(getattr(f, "onsets", None) or ()) if f is not None else []
        heard = False
        for o in ons[-MAX_ONSETS:]:
            try:
                pitch, vel, age = int(o[0]), float(o[1]), float(o[2])
            except (TypeError, ValueError, IndexError):
                continue
            if not (LOW <= pitch <= HIGH) or not (math.isfinite(vel) and math.isfinite(age)) or age > 10.0:
                continue
            vel = min(max(vel, 0.0), 1.0)
            wr = 1.0 if w_reg is None else float(w_reg[pitch])
            self._launch(pitch, vel, now - max(age, 0.0) * 1000.0, False, p, wr)
            heard = True
        if heard:
            self.t_last_on = now
        target = 1.0 if now - self.t_last_on > IDLE_AFTER_S * 1000.0 else 0.0
        if dt > 0.0:
            tau = 0.5 if target < self.idle else 2.5
            self.idle += (target - self.idle) * (1.0 - math.exp(-dt / tau))
        self._ghosts(now, e, p)

        period = float(p["palette_period"])
        a_damp = 1.0 - math.exp(-dt / 0.3) if dt > 0.0 else 0.0
        count = self._records(now, e, self.kick, period, a_damp)

        G = self.Gh
        G[0] = count
        G[1], G[2], G[3], G[4] = self.kA, self.Wd, self.A, Y_G
        G[5] = MARGIN * self.Wd
        G[6] = (1.0 - 2.0 * MARGIN) * self.Wd / float(HIGH - LOW)
        G[7:19] = self.pal
        G[19], G[20] = period, self.drift
        G[21], G[22], G[23] = e, self.kick, float(p["intensity"])
        G[24], G[25] = self.twinkle, self.haze % 1000.0
        G[26] = 0.5 + 0.5 * math.sin(2.0 * math.pi * self.breath)
        G[27] = float(p["shape"])
        G[28] = 0.45 + 1.0 * e
        G[29] = min(max(float(p["floor"]), 0.0), 1.0)
        G[30] = 0.25 + 0.45 * e
        G[31] = self.idle
        np.nan_to_num(G, copy=False, nan=0.0, posinf=1e9, neginf=-1e9)
        self.arena.set(self.host)
        notes = getattr(snd, "notes_gpu", None) if snd is not None else None
        if notes is None:
            notes = self.quiet
        launch_2d(self.k, self.w, self.h, (self.buf, self.G_d, self.F_d, notes, np.int32(self.w), np.int32(self.h),
                                           np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.arena = self.G_d = self.F_d = self.quiet = None
