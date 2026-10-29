"""Tonnetz walk: Euler's note lattice as an endless glass floor; every chord is a lit glass tile that flips over a
shared edge to the next chord, leaving footprints.

Source: Original (inspired by TonnetzViz, https://github.com/cifkao/tonnetz-viz (MIT; no code used); theory:
        Euler's Tonnetz, https://en.wikipedia.org/wiki/Tonnetz; Cohn, "Neo-Riemannian Operations, Parsimonious
        Trichords, and Their Tonnetz Representations" (1997); https://en.wikipedia.org/wiki/Neo-Riemannian_theory;
        https://en.wikipedia.org/wiki/Comma_pump)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
  A glossy floor of triangular glass tiles runs away from the camera to a fogged horizon.  The floor is the Tonnetz,
  the map of harmony Euler drew in 1739: every corner is a note, written with its proper spelling (C, E-flat, F-sharp,
  even D-double-flat), and every line between two notes is an interval.  Lines running left to right are perfect
  fifths (blue), lines rising to the right are major thirds (green-gold) and lines rising to the left are minor
  thirds (teal).  So every small triangle is three notes that are each a third or a fifth apart: a major or a minor
  chord.  Upward-pointing triangles are the major chords, downward-pointing ones the minor chords.

  The note names repeat as the floor goes on (the twelve notes come round again in every direction), so when a note
  sounds, all of its copies light up together and the lit notes form constellations that recede to the horizon.  Two
  notes sounding together light the line between them.

  The chord being played is a thick glass tile, lit from inside.  When the harmony moves, the tile does not jump: it
  turns over one of its edges, like a coaster flipped on a table, and lands on the neighbouring triangle.  Each flip
  keeps two notes and moves one, which is how smooth voice leading works; music theory calls the three possible flips
  P (C major <-> C minor: the third moves a semitone), L (C major <-> E minor: the root moves down a semitone) and R
  (C major <-> A minor: the fifth moves up a tone).  A chord change between distant chords is a quick cascade of flips
  along the shortest route (C to F-sharp takes four).  The tile's old places stay faintly scorched for about a minute
  (``footprints_s``), so the path of the harmony is drawn on the floor; the whole floor glows faintly wherever the
  chords just played live elsewhere on the lattice.

  The key's six everyday chords (I, IV and V, ii, iii and vi; in a minor key, those of its relative major) sit side by
  side in a warm strip lit from beneath: home.  Every copy of the key's home chord has a gold rim and every copy of
  its dominant (V) a crimson one.  A corner readout names the chord (spelled where the tile actually is), its Roman
  numeral in the key and how many flips it is from home.

  Because the floor is the unrolled lattice of pure (5-limit just) intervals, a progression can come home somewhere
  else.  I-vi-ii-V-I in C walks the tile four fifths left and a third up: it lands on "the same" C major chord a
  syntonic comma (21.5 cents) flat of where it started, the comma pump of tuning theory.  The P-L cycle C, c, A-flat,
  a-flat, F-flat, f-flat comes back as D-double-flat major, a diesis (41 cents) away.  With ``show_comma`` on, the
  readout says how many cents the tile sits from the home key's copy of its chord.

  Without notes it stays alive: caustic glints slide through the glass, the home strip breathes and the camera turns
  slowly; after a minute of silence a pale ghost tile walks the P-L cycle, one calm flip every 2.5 s.

How it works
  Host (per frame, plain Python on 12 numbers): the pitch-class energy of the notes table (tonal.pc_energy), eased; the
  chord by cosine against the 24 triad templates with hysteresis (tonal.ChordReader: best >= 0.8, leading by 0.05
  for 80 ms; the bass x1.5); the key from the room (frame.key / mode), else the scene's own Krumhansl-Kessler finder
  (tonal.KeyResolver).  A new triad is reached by the shortest P/L/R path (BFS table, tonal.PLR_NEXT), each step
  reflecting the tile on the lattice (tonal.tri_move), so placement is the true walk and drifts honestly; the flips
  queue at 0.25 s each (0.12 s when three or more wait; six or more and all but the last land at once).  A flip turns
  the free vertex about the hinge: v(phi) = m + cos(phi)(v - m) + sin(phi)|v - m| up, m its foot on the hinge, which
  lands exactly on the reflected vertex at phi = pi.  The camera follows the tile on a critically damped spring
  (omega 2/s) and yaws slowly; the lattice origin is rebased near the camera so float32 never runs out.
  Kernel (one pass at render_scale, then the shared upsample): a ray per pixel against the floor plane y = 0 and the
  tile triangle (Moller-Trumbore); on the floor the hit's lattice coordinates (b = Z / (sqrt3/2), a = X - b/2), the
  triangle (u + v < 1: up) and its barycentrics give the three edges (antialiased by their screen-space gradient),
  the nearest node (its label decal from a PIL-baked atlas with three mip levels, oriented to the camera), the fills
  (home strip, gold / crimson rims, the footprint texture of landing times 128 x 128 x 2), sky reflection with
  Fresnel, caustic bands, the tile's light pool and exponential fog.  The tile shades as glass: Fresnel reflection,
  a bevel rim, a specular glint and a back face tinted with the chord it is turning into, so the colour arrives as it
  lands.  The corner readout is drawn from a glyph atlas whose strings the host lays out only when they change.

Macros: energy -> caustic speed and brightness, the glow of the strip, nodes and tile, the camera's sway, how many
node copies carry names; pulse (bind note_on) -> the tile's, strip's, caustics', nodes' and lit lines' glow kick (eased in, never a
flash); speed -> camera yaw, sway, breathing, caustics and the ghost; intensity -> gain; palette / period / drift ->
the sky, the glass and 40% of the interval tints (default ice).
Cost: about 1.5-2.5 ms at 4K, render_scale 0.5 (the kernel + the upsample) on the P4000; host ~0.1-0.2 ms.
"""
from __future__ import annotations

import logging
import math
from collections import deque

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import flag, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.tonnetz_walk")

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SQ3_2 = math.sqrt(3.0) / 2.0
LOF_LO, LOF_HI = -15, 19                 # spelled names baked into the node atlas (35)
N_NAMES = LOF_HI - LOF_LO + 1
NAME_H = (64, 32, 16)                    # node-name atlas mip levels (cell height; width = 2 x)
FOOT = 128                               # footprint texture side (cells addressed (a mod 128, b mod 128, up/down))
MAXC = 40                                # characters per readout line
FOV_Y = math.radians(56.0)
TARGET_NDC = 0.42                        # the tile sits this far below the screen's centre (so the horizon shows)
OMEGA = 2.0                              # camera spring (critically damped), 1/s
FLIP_S, FLIP_FAST_S, GHOST_FLIP_S = 0.25, 0.12, 0.45
SKIP_AT = 6                              # this many queued flips or more: all but the last land at once
GHOST_AFTER_S, GHOST_FIRST_S, GHOST_STEP_S = 60.0, 4.0, 2.5
REANCHOR = 6.5                           # world units from home before the home strip moves to the tile
GLYPHS = (" !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~"
          "♭♯°ø¢−·")
_GI = {c: i for i, c in enumerate(GLYPHS)}

# ---- the arena (floats): params, then per-frame tables -------------------------------------------------------------
P_PAL, P_W, P_H = 0, 12, 13
P_CAM, P_RT, P_UP, P_FW = 14, 17, 20, 23
P_TANH, P_ASP, P_BA, P_BB = 26, 27, 28, 29
P_E, P_KICK, P_GAIN, P_NOW, P_CPH, P_BR = 30, 31, 32, 33, 34, 35
P_GLASS, P_LAB, P_LRAD, P_FX, P_FZ = 36, 37, 38, 39, 40
P_FOGD, P_FOG0, P_SPELL, P_TAU, P_NST, P_SEED = 41, 42, 43, 44, 45, 46
P_SKYT, P_HOR, P_GT, P_WARM = 47, 50, 53, 56
P_I5, P_IM3, P_IN3 = 59, 62, 65
P_GHOST, P_TGLOW = 68, 69
P_TV = 70                                # tile: V0 V1 V2 (xyz each)
P_TFR, P_TBK = 79, 82
P_POOL = 85                              # x, z, strength
P_RA = 88
P_STRIP = 89                             # 2 slots x 8: a0 b0 weight tonic dominant mask_major mask_minor home_tonic_up
P_TXB = 105                              # big cell w, h, small cell w, h
P_LCH, P_NODE, P_LW = 109, 110, 111
P_NLC, P_SCO, P_GOLD, P_CRIM = 112, 115, 118, 121
P_FAR, P_GHC = 124, 125                  # mean pc level; ghost colour (3)
NP_ = 128
O_PC = NP_                               # 12 pc levels
O_AGE = O_PC + 12                        # 24 triad ages (s since last visit)
O_ST = O_AGE + 24                        # 8 footprint stamps (cell, time)
O_TL = O_ST + 16                         # 3 readout lines x 6 (n, x0, y0, big, alpha, width)
O_TC = O_TL + 18                         # 3 x MAXC x 2 (glyph, x offset)
N_AR = O_TC + 3 * MAXC * 2


def _defs() -> str:
    names = {k: v for k, v in globals().items() if (k.startswith("P_") or k.startswith("O_")) and isinstance(v, int)}
    names.update(N_AR=N_AR, MAXC=MAXC, FOOT=FOOT, N_NAMES=N_NAMES, LOF_LO=LOF_LO, LOF_HI=LOF_HI)
    return "".join(f"#define {k} {v}\n" for k, v in names.items())


_SRC = HELPERS + _defs() + r"""
#define SQ3_2 0.86602540f
#define TWO_PI 6.2831853f
__device__ __forceinline__ float3 operator+(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 operator-(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 operator*(float s, float3 a) { return make_float3(s * a.x, s * a.y, s * a.z); }
__device__ __forceinline__ float3 operator*(float3 a, float3 b) { return make_float3(a.x * b.x, a.y * b.y, a.z * b.z); }
__device__ __forceinline__ float dot3(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 cross3(float3 a, float3 b) {
    return make_float3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
__device__ __forceinline__ float3 norm3(float3 a) { return rsqrtf(fmaxf(dot3(a, a), 1e-12f)) * a; }
__device__ __forceinline__ float3 V3(const float* S, int o) { return make_float3(S[o], S[o + 1], S[o + 2]); }
__device__ __forceinline__ float3 mix3(float3 a, float3 b, float t) { return a + t * (b - a); }
__device__ __forceinline__ int pcof(int a, int b) { return (((7 * a + 4 * b) % 12) + 12) % 12; }

// bilinear sample of a uint8 atlas cell (w x h texels, texel centres at i + 0.5), 0 outside
__device__ float tex8(const unsigned char* __restrict__ A, int w, int h, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = (int)fu, y0 = (int)fv;
    const float tx = u - fu, ty = v - fv;
    float s = 0.f;
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int xx = x0 + (k & 1), yy = y0 + (k >> 1);
        if (xx < 0 || yy < 0 || xx >= w || yy >= h) continue;
        s += ((k & 1) ? tx : 1.f - tx) * ((k >> 1) ? ty : 1.f - ty) * (float)A[yy * w + xx];
    }
    return s * (1.f / 255.f);
}

__device__ __forceinline__ float3 ray_dir(const float* S, float px, float py) {
    const float sx = (2.f * px / S[P_W] - 1.f) * S[P_TANH] * S[P_ASP];
    const float sy = (1.f - 2.f * py / S[P_H]) * S[P_TANH];
    return norm3(V3(S, P_FW) + sx * V3(S, P_RT) + sy * V3(S, P_UP));
}

__device__ __forceinline__ float3 sky(const float* S, float el) {
    const float3 hor = V3(S, P_HOR), top = V3(S, P_SKYT);
    float3 c = mix3(hor, top, smooth(0.f, 0.55f, el));
    const float band = __expf(-fabsf(el) * 16.f);
    return c + (0.35f * band) * hor;
}

// Moller-Trumbore: t and barycentrics (u, v) of the ray against triangle (a, b, c); false on a miss
__device__ bool tri_hit(float3 o, float3 d, float3 a, float3 b, float3 c, float& t, float& u, float& v) {
    const float3 e1 = b - a, e2 = c - a;
    const float3 p = cross3(d, e2);
    const float det = dot3(e1, p);
    if (fabsf(det) < 1e-9f) return false;
    const float inv = 1.f / det;
    const float3 s = o - a;
    u = dot3(s, p) * inv;
    if (u < 0.f || u > 1.f) return false;
    const float3 q = cross3(s, e1);
    v = dot3(d, q) * inv;
    if (v < 0.f || u + v > 1.f) return false;
    t = dot3(e2, q) * inv;
    return t > 1e-4f;
}

// floor hit point (X, Z) of the ray through pixel (px, py); false if it misses the floor
__device__ __forceinline__ bool floor_at(const float* S, float px, float py, float3 C, float& X, float& Z) {
    const float3 d = ray_dir(S, px, py);
    if (d.y > -1e-5f) return false;
    const float t = -C.y / d.y;
    X = C.x + t * d.x; Z = C.z + t * d.z;
    return true;
}

// coverage of a line: d pixels from it, half width hw pixels (thin lines fade instead of aliasing)
__device__ __forceinline__ float line_cov(float d, float hw) {
    const float h = fmaxf(hw, 0.5f);
    return clamp01(h + 0.5f - d) * fminf(hw * 2.f, 1.f);
}

extern "C" __global__
void tonnetz(uchar4* __restrict__ out, const float* __restrict__ A, const unsigned char* __restrict__ nm0,
             const unsigned char* __restrict__ nm1, const unsigned char* __restrict__ nm2,
             const unsigned char* __restrict__ gbig, const unsigned char* __restrict__ gsmall,
             float* __restrict__ foot)
{
    __shared__ float S[N_AR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    const int nth = blockDim.x * blockDim.y;
    for (int i = tid; i < N_AR; i += nth) S[i] = A[i];
    __syncthreads();
    if (blockIdx.x == 0 && blockIdx.y == 0 && tid < (int)S[P_NST] && tid < 8) {
        const int cell = (int)S[O_ST + 2 * tid];
        if (cell >= 0 && cell < FOOT * FOOT * 2) foot[cell] = S[O_ST + 2 * tid + 1];
    }
    const int W = (int)S[P_W], H = (int)S[P_H];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float3 C = V3(S, P_CAM);
    const float3 d = ray_dir(S, px, py);
    const float e = S[P_E], kick = S[P_KICK], glass = S[P_GLASS];
    const float* PC = S + O_PC;
    const float3 hor = V3(S, P_HOR);

    float tf = 1e30f;
    const bool on_floor = d.y < -1e-5f;
    if (on_floor) tf = -C.y / d.y;
    float3 col;
    if (!on_floor) {
        col = sky(S, d.y);
    } else {
        // ---- the floor: lattice, triangle, barycentrics, screen-space gradients -------------------------------
        const float X = C.x + tf * d.x, Z = C.z + tf * d.z;
        float X1, Z1, X2, Z2;
        float gxx = 10.f, gzx = 10.f, gxy = 10.f, gzy = 10.f;
        if (floor_at(S, px + 1.f, py, C, X1, Z1)) { gxx = X1 - X; gzx = Z1 - Z; }
        if (floor_at(S, px, py + 1.f, C, X2, Z2)) { gxy = X2 - X; gzy = Z2 - Z; }
        const float fpw = fminf(sqrtf(fmaxf(gxx * gxx + gzx * gzx, gxy * gxy + gzy * gzy)), 10.f);  // world / px
        const float bf = Z * (1.f / SQ3_2), af = X - 0.5f * bf;
        const float dbx = gzx * (1.f / SQ3_2), dby = gzy * (1.f / SQ3_2);
        const float dax = gxx - 0.5f * dbx, day = gxy - 0.5f * dby;
        const float fi = floorf(af), fj = floorf(bf);
        const int i = (int)fi, j = (int)fj;
        const float u = af - fi, v = bf - fj;
        const bool up = u + v < 1.f;
        const int ia = i + (int)S[P_BA], ja = j + (int)S[P_BB];
        // barycentrics and vertices (absolute lattice)
        float w0, w1, w2; int a0, b0, a1, b1, a2, b2;
        if (up) { w0 = 1.f - u - v; w1 = u; w2 = v; a0 = ia; b0 = ja; a1 = ia + 1; b1 = ja; a2 = ia; b2 = ja + 1; }
        else { w0 = 1.f - v; w1 = 1.f - u; w2 = u + v - 1.f; a0 = ia + 1; b0 = ja; a1 = ia; b1 = ja + 1; a2 = ia + 1; b2 = ja + 1; }
        const float mb = fminf(w0, fminf(w1, w2));               // 0 on the triangle's edges, 1/3 at its centre
        const float det = 1.f - smooth(0.05f, 0.17f, fpw);       // fine detail fades before it aliases
        const float det2 = 1.f - smooth(0.12f, 0.4f, fpw);
        const float fpm = sqrtf(sqrtf(fmaxf(gxx * gxx + gzx * gzx, 1e-12f) * fmaxf(gxy * gxy + gzy * gzy, 1e-12f)));   // isotropic
        const float lv_far = S[P_FAR];

        // the glass itself: a dark body, the sky reflected (Fresnel), per-tile gloss
        const float cosi = fminf(-d.y, 1.f);
        const float om = 1.f - cosi, om2 = om * om;
        const float F = 0.04f + 0.96f * om2 * om2 * om;
        const float3 gt = V3(S, P_GT);
        const unsigned int th = hash_u((unsigned int)(ia * 73856093) ^ (unsigned int)(ja * 19349663) ^ (up ? 0x5bd1e995u : 0u));
        const float facet = 0.85f + 0.3f * (th >> 8) * (1.f / 16777216.f);
        col = (0.03f + 0.02f * glass) * facet * gt;
        col = col + ((0.2f + 0.8f * glass) * F * (0.8f + 0.2f * facet)) * sky(S, -d.y * 0.6f);
        // caustic glints: two slow sin-warped bands crossing under the glass
        {
            const float ph = S[P_CPH];
            const float c1 = __sinf(1.31f * X + 0.83f * Z + 1.6f * __sinf(0.61f * Z - 0.47f * X + 0.7f * ph) + ph);
            const float c2 = __sinf(-0.57f * X + 1.19f * Z + 1.4f * __sinf(0.53f * X + 0.71f * Z - 0.9f * ph) - 1.3f * ph);
            float b1 = 0.5f + 0.5f * c1, b2 = 0.5f + 0.5f * c2;
            b1 *= b1; b1 *= b1; b1 *= b1; b2 *= b2; b2 *= b2; b2 *= b2;
            const float k = glass * (0.05f + 0.09f * e) * (1.f + 0.9f * kick) * (0.4f * (b1 + b2) + 1.6f * b1 * b2) * det2;
            col = col + k * (0.5f * gt + make_float3(0.5f, 0.5f, 0.5f));
        }

        // ---- fills: the home strip, rims, footprints, visited triads ------------------------------------------
        {
            const int pr = up ? pcof(ia, ja) : (pcof(ia, ja) + 4) % 12;
            const int tri = up ? pr : 12 + pr;
            const float centre = smooth(0.f, 0.3f, mb);
            const float dw = mb * SQ3_2;                         // world distance to the triangle's edges
            float strip = 0.f, gold = 0.f, crim = 0.f;
            #pragma unroll
            for (int s = 0; s < 2; ++s) {
                const float* Q = S + P_STRIP + 8 * s;
                const float wgt = Q[2];
                if (wgt <= 1e-3f) continue;
                const int ha = (int)Q[0], hb = (int)Q[1];
                const bool exact = ja == hb && (up ? (ia >= ha - 1 && ia <= ha + 1) : (ia >= ha - 2 && ia <= ha));
                const unsigned int mask = (unsigned int)(up ? Q[5] : Q[6]);
                const bool dia = (mask >> pr) & 1u;
                strip += wgt * (exact ? 1.f : (dia ? 0.12f : 0.f));
                const bool home_t = exact && (Q[7] > 0.5f ? (up && ia == ha) : (!up && ia == ha - 1));
                if (tri == (int)Q[3]) gold += wgt * (home_t ? 1.f : 0.2f);
                if (tri == (int)Q[4]) crim += wgt * (exact ? 0.8f : 0.18f);
            }
            const float breath = (0.75f + 0.25f * S[P_BR]) * (1.f + 0.45f * kick);
            const float3 warm = V3(S, P_WARM);
            col = col + (strip * breath * (0.26f + 0.08f * e) * (0.35f + 0.65f * centre) * det2) * warm;
            const float rimk = __expf(-dw / 0.035f) + 0.6f * line_cov(dw / fpw, 0.012f / fpw);
            col = col + (gold * rimk * 0.55f * det2) * V3(S, P_GOLD) + (crim * rimk * 0.55f * det2) * V3(S, P_CRIM);
            // footprints: landing times in a 128 x 128 x 2 texture (absolute lattice, wrapped)
            const int cell = (((ia & (FOOT - 1)) * FOOT) + (ja & (FOOT - 1))) * 2 + (up ? 0 : 1);
            const float age = S[P_NOW] - foot[cell];
            const float tau = S[P_TAU];
            const float heat = age >= 0.f ? __expf(-age / tau) : 0.f;
            const float sc = heat * (0.12f + 0.38f * centre + 0.25f * __expf(-dw / 0.05f)) * det2;
            col = col + sc * V3(S, P_SCO);
            const float vis = __expf(-S[O_AGE + tri] / tau);
            col = col + (0.05f * vis * (0.3f + 0.7f * centre) * det2) * V3(S, P_SCO);
        }

        // ---- the tile's light pool on the glass ------------------------------------------------------------------
        {
            const float qx = X - S[P_POOL], qz = Z - S[P_POOL + 1];
            const float pool = S[P_POOL + 2] * __expf(-(qx * qx + qz * qz) * 0.9f);
            col = col + (0.5f * pool) * mix3(V3(S, P_TFR), V3(S, P_TBK), 0.5f);
        }

        // ---- the nearest node: its light and its name ------------------------------------------------------------
        int na, nb;
        if (w0 >= w1 && w0 >= w2) { na = a0; nb = b0; } else if (w1 >= w2) { na = a1; nb = b1; } else { na = a2; nb = b2; }
        const float nrx = (float)(na - (int)S[P_BA]), nrz = (float)(nb - (int)S[P_BB]);
        const float ox = X - (nrx + 0.5f * nrz), oz = Z - nrz * SQ3_2;
        const float dn = sqrtf(ox * ox + oz * oz);
        const int npc = pcof(na, nb);
        const float lvn = PC[npc];
        // labels near the focus (more of them with energy)
        float la = 0.f;
        {
            const float fx = X - S[P_FX], fz = Z - S[P_FZ];
            const float R = S[P_LRAD];
            la = S[P_LAB] * (1.f - smooth(0.6f * R, R, sqrtf(fx * fx + fz * fz)));
            const float cellpx = S[P_LCH] / fpm;
            la *= smooth(3.5f, 7.f, cellpx);
        }
        // the lines: fifths, major thirds, minor thirds (each lit at the quieter of its two notes)
        {
            const float gap = 0.07f + 0.17f * la;
            const float ng = smooth(gap, gap + 0.07f, dn);
            const float gv = fmaxf(sqrtf(dbx * dbx + dby * dby), 1e-6f);
            const float gu = fmaxf(sqrtf(dax * dax + day * day), 1e-6f);
            const float guv = fmaxf(sqrtf((dax + dbx) * (dax + dbx) + (day + dby) * (day + dby)), 1e-6f);
            float eb[3], eg[3]; int ea[3], eb_[3], ec[3], ed[3];
            if (up) {
                eb[0] = v; eg[0] = gv; ea[0] = ia; eb_[0] = ja; ec[0] = ia + 1; ed[0] = ja;
                eb[1] = u; eg[1] = gu; ea[1] = ia; eb_[1] = ja; ec[1] = ia; ed[1] = ja + 1;
                eb[2] = 1.f - u - v;
            } else {
                eb[0] = 1.f - v; eg[0] = gv; ea[0] = ia; eb_[0] = ja + 1; ec[0] = ia + 1; ed[0] = ja + 1;
                eb[1] = 1.f - u; eg[1] = gu; ea[1] = ia + 1; eb_[1] = ja; ec[1] = ia + 1; ed[1] = ja + 1;
                eb[2] = u + v - 1.f;
            }
            eg[2] = guv; ea[2] = ia + 1; eb_[2] = ja; ec[2] = ia; ed[2] = ja + 1;
            const float lw = S[P_LW];
            float3 L = make_float3(0.f, 0.f, 0.f);
            #pragma unroll
            for (int k = 0; k < 3; ++k) {
                const float lit = fminf(PC[pcof(ea[k], eb_[k])], PC[pcof(ec[k], ed[k])]);
                const float hwp = lw * (1.f + 1.6f * lit) / SQ3_2 / eg[k];
                const float dp = eb[k] / eg[k];
                const float cov = line_cov(dp, hwp);
                const float glow = lit * __expf(-eb[k] * SQ3_2 / 0.07f);
                const float kk = (0.22f + 1.5f * lit * (1.f + 0.5f * kick)) * cov + (0.55f + 0.35f * e) * glow * (1.f + 0.4f * kick);
                L = L + kk * V3(S, P_I5 + 3 * k);
            }
            col = col + (ng * det) * L;
            // far away: the lines' average light instead of aliasing
            col = col + ((1.f - det) * det2 * (0.03f + 0.12f * lv_far)) * (0.33f * (V3(S, P_I5) + V3(S, P_IM3) + V3(S, P_IN3)));
        }
        // the node: a glint, its glow pool and its engraved name
        {
            const float3 nlc = V3(S, P_NLC);
            const float rn = S[P_NODE] * (1.f + 0.5f * lvn);
            const float core = clamp01(rn / fpw - dn / fpw + 0.5f) * fminf(rn / fpw, 1.f);
            const float halo = lvn * (0.55f * __expf(-dn / 0.09f) + 0.25f * __expf(-dn / 0.3f)) * (0.85f + 0.35f * e) * (1.f + 0.4f * kick);
            const float dot_ = core * (0.25f + 1.6f * lvn) * (1.f - 0.85f * la);
            col = col + (det * (halo + dot_)) * nlc + (0.25f * lvn * (1.f - det) * det2) * nlc;
            if (la > 0.004f) {
                // a faint frosted plate under the name (an engraved coin in the glass)
                col = col + (la * 0.07f * __expf(-dn * dn / 0.05f) * det) * (0.5f * V3(S, P_GT) + make_float3(0.25f, 0.25f, 0.25f));
                // camera-aligned glyph frame on the floor: u along the camera's right, v along its forward
                const float rx = S[P_RT], rz = S[P_RT + 2];
                float fxh = S[P_FW], fzh = S[P_FW + 2];
                const float fl = rsqrtf(fmaxf(fxh * fxh + fzh * fzh, 1e-8f));
                fxh *= fl; fzh *= fl;
                const float lu = ox * rx + oz * rz, lv = ox * fxh + oz * fzh;
                const float ch = S[P_LCH], cw = 2.f * ch;
                const float tu = lu / cw + 0.5f, tv = 0.5f - lv / ch;
                if (tu > 0.f && tu < 1.f && tv > 0.f && tv < 1.f) {
                    int lof = na + 4 * nb - (int)S[P_SPELL];
                    if (lof < LOF_LO || lof > LOF_HI) lof -= 12 * (int)floorf((float)(lof - 2) / 12.f + 0.5f);
                    lof = min(max(lof, LOF_LO), LOF_HI);
                    const int g = lof - LOF_LO;
                    const float lod = fminf(fmaxf(log2f(fmaxf(fpm * 64.f / ch, 1e-6f)), 0.f), 2.f);
                    const int l0 = min((int)lod, 1);
                    const float fr = lod - (float)l0;
                    float c0, c1;
                    if (l0 == 0) {
                        c0 = tex8(nm0 + g * 64 * 128, 128, 64, tu * 128.f, tv * 64.f);
                        c1 = tex8(nm1 + g * 32 * 64, 64, 32, tu * 64.f, tv * 32.f);
                    } else {
                        c0 = tex8(nm1 + g * 32 * 64, 64, 32, tu * 64.f, tv * 32.f);
                        c1 = tex8(nm2 + g * 16 * 32, 32, 16, tu * 32.f, tv * 16.f);
                    }
                    const float cov = c0 + (c1 - c0) * fr;
                    const float k = la * cov * (0.85f + 1.1f * lvn * (1.f + 0.3f * kick));
                    col = col + k * mix3(make_float3(0.85f, 0.88f, 0.92f), nlc, 0.25f + 0.5f * lvn);
                }
            }
        }
        // fog
        const float fog = 1.f - __expf(-fmaxf(tf - S[P_FOG0], 0.f) * S[P_FOGD]);
        col = mix3(col, hor + (0.35f) * hor, fog);
    }

    // ---- the tile: lit glass, turning over its hinge ------------------------------------------------------------
    {
        const float3 V0 = V3(S, P_TV), V1 = V3(S, P_TV + 3), V2 = V3(S, P_TV + 6);
        float tt, tu, tv;
        if (tri_hit(C, d, V0, V1, V2, tt, tu, tv) && tt < tf) {
            const float3 n = norm3(cross3(V1 - V0, V2 - V0));
            const float dn = dot3(n, d);
            const float ghost = S[P_GHOST];
            float3 body = dn < 0.f ? V3(S, P_TFR) : V3(S, P_TBK);
            body = mix3(body, V3(S, P_GHC), ghost);
            const float mb = fminf(1.f - tu - tv, fminf(tu, tv));
            const float dw = mb * SQ3_2;
            const float rim = __expf(-dw / 0.03f);
            const float inner = 0.55f + 0.45f * smooth(0.f, 0.3f, mb);
            const float g = S[P_TGLOW];
            const float cv = fabsf(dn), omv = 1.f - cv, omv2 = omv * omv;
            const float Fr = 0.04f + 0.96f * omv2 * omv2 * omv;
            const float3 nn = dn < 0.f ? n : (-1.f) * n;
            const float3 r = d - (2.f * dot3(d, nn)) * nn;
            const float3 Ld = norm3(make_float3(-0.4f, 0.8f, 0.45f));
            const float sp = fmaxf(dot3(r, Ld), 0.f);
            const float sp2 = sp * sp, sp4 = sp2 * sp2, sp8 = sp4 * sp4, sp16 = sp8 * sp8;
            float3 tc = (g * (0.95f * inner + 1.3f * rim)) * body + (0.35f * rim * g) * make_float3(1.f, 1.f, 1.f);
            tc = tc + ((0.25f + 0.5f * glass) * Fr) * sky(S, r.y) + (0.8f * sp16 * sp8) * make_float3(1.f, 0.97f, 0.92f);
            const float alpha = (0.82f - 0.35f * ghost);
            float3 tile = mix3(col, tc + (0.3f) * col, alpha);
            const float fog = 1.f - __expf(-fmaxf(tt - S[P_FOG0], 0.f) * S[P_FOGD]);
            col = mix3(tile, hor + 0.35f * hor, fog);
        }
    }

    // ---- the corner readout ---------------------------------------------------------------------------------------
    {
        const float ra = S[P_RA];
        if (ra > 0.004f) {
            // a soft shade in the corner so the text reads over a bright floor
            const float vx = px / (float)W, vy = 1.f - py / (float)H;
            const float sh = __expf(-(vx * vx * 3.f + vy * vy * 10.f));
            col = (1.f - 0.45f * ra * sh) * col;
            float tcov = 0.f, scov = 0.f;
            for (int L = 0; L < 3; ++L) {
                const float* Lh = S + O_TL + 6 * L;
                const int n = (int)Lh[0];
                if (n <= 0) continue;
                const bool big = Lh[3] > 0.5f;
                const int cw = (int)(big ? S[P_TXB] : S[P_TXB + 2]), ch = (int)(big ? S[P_TXB + 1] : S[P_TXB + 3]);
                const float x0 = Lh[1], y0 = Lh[2];
                if (py < y0 || py >= y0 + ch + 2.f || px < x0 || px > x0 + Lh[5] + cw) continue;
                const unsigned char* G = big ? gbig : gsmall;
                const int vy0 = (int)(py - y0), so = big ? 2 : 1;
                float c = 0.f, s = 0.f;
                for (int k = 0; k < n && k < MAXC; ++k) {
                    const int gi = (int)S[O_TC + (L * MAXC + k) * 2];
                    const float xo = S[O_TC + (L * MAXC + k) * 2 + 1];
                    const int uu = (int)(px - x0 - xo);
                    if (uu >= 0 && uu < cw && vy0 < ch) c = fmaxf(c, G[(gi * ch + vy0) * cw + uu] * (1.f / 255.f));
                    const int us = uu - so, vs = vy0 - so;
                    if (us >= 0 && us < cw && vs >= 0 && vs < ch) s = fmaxf(s, G[(gi * ch + vs) * cw + us] * (1.f / 255.f));
                }
                tcov += c * Lh[4]; scov += s * Lh[4];
            }
            col = (1.f - 0.7f * ra * clamp01(scov)) * col;
            col = col + (ra * tcov * 1.15f) * make_float3(1.f, 0.95f, 0.86f);
        }
    }

    // vignette, gain, soft tone curve, dither
    const float vx = px / (float)W - 0.5f, vy = py / (float)H - 0.5f;
    col = (1.f - 0.28f * smooth(0.2f, 0.75f, vx * vx + vy * vy * 1.6f)) * col;
    const float gn = 255.f * S[P_GAIN];
    const float dth = (hash_u((unsigned int)(y * W + x) ^ ((unsigned int)S[P_SEED] * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    out[y * W + x] = pack(make_float4(gn * (1.f - __expf(-1.25f * fmaxf(col.x, 0.f))) + dth,
                                      gn * (1.f - __expf(-1.25f * fmaxf(col.y, 0.f))) + dth,
                                      gn * (1.f - __expf(-1.25f * fmaxf(col.z, 0.f))) + dth, 255.f));
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")
_RING = 16
_logged: set = set()


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def _pal(t: float, c) -> tuple[float, float, float]:
    tp = 2.0 * math.pi
    return tuple(min(max(c[i] + c[3 + i] * math.cos(tp * (c[6 + i] * t + c[9 + i])), 0.0), 1.0) for i in range(3))


def _at_luma(c, L: float) -> list[float]:
    """Colour ``c`` rescaled to luma ``L`` (a grey of L if it is black), each channel clamped to 1.5."""
    y = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    if y < 0.02:
        return [L, L, L]
    return [min(v * L / y, 1.5) for v in c]


def _sat(c, k: float) -> list[float]:
    """Saturation x k about the colour's luma (clamped 0..1.5)."""
    y = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    return [min(max(y + (v - y) * k, 0.0), 1.5) for v in c]


def _pretty(name: str) -> str:
    """'Bb' -> 'B♭', 'Dbb' -> 'D♭♭', 'F#' -> 'F♯' (a double sharp stays 'x')."""
    return name[0] + name[1:].replace("b", "♭").replace("#", "♯")


def bake_names() -> list[np.ndarray]:
    """The 35 spelled note names (lof LOF_LO..LOF_HI) as uint8 atlases at three mip levels [35, h, 2h]."""
    out = []
    try:
        from PIL import Image, ImageDraw, ImageFont
        for h in NAME_H:
            w = 2 * h
            font = ImageFont.truetype(FONT_BOLD, max(6, int(round(h * 0.64))))
            a = np.zeros((N_NAMES, h, w), np.uint8)
            for i in range(N_NAMES):
                s = _pretty(T.lof_name(LOF_LO + i))
                img = Image.new("L", (w, h), 0)
                ImageDraw.Draw(img).text((w / 2, h / 2), s, fill=255, font=font, anchor="mm")
                a[i] = np.asarray(img, np.uint8)
            out.append(a)
    except Exception as exc:                              # degrade: no names
        _log_once("font", "tonnetz_walk: cannot bake the note names (%s); drawing none", exc)
        out = [np.zeros((N_NAMES, h, 2 * h), np.uint8) for h in NAME_H]
    return out


def bake_glyphs(px: int) -> tuple[np.ndarray, int, int, list[float]]:
    """The readout's glyphs at pixel size ``px``: atlas [n, ch, cw], cell w, cell h, advances."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(FONT, max(6, px))
        adv = [float(font.getlength(c)) for c in GLYPHS]
        pad = max(2, px // 8)
        cw = int(math.ceil(max(adv))) + 2 * pad
        ch = int(round(px * 1.3)) + pad
        a = np.zeros((len(GLYPHS), ch, cw), np.uint8)
        for i, c in enumerate(GLYPHS):
            img = Image.new("L", (cw, ch), 0)
            ImageDraw.Draw(img).text((pad, int(round(px * 1.02))), c, fill=255, font=font, anchor="ls")
            a[i] = np.asarray(img, np.uint8)
        return a, cw, ch, [x - 0.0 for x in adv]
    except Exception as exc:
        _log_once("glyphs", "tonnetz_walk: cannot bake the readout glyphs (%s); no readout", exc)
        return np.zeros((len(GLYPHS), 2, 2), np.uint8), 2, 2, [0.0] * len(GLYPHS)


def tri_world(a: int, b: int, up: bool, ba: int, bb: int, lift: float = 0.004) -> list[np.ndarray]:
    """The triangle's three vertices in world coordinates relative to the base (x, y, z), ordered so the normal
    points up."""
    vs = [np.array([(va - ba) + 0.5 * (vb - bb), lift, (vb - bb) * SQ3_2]) for va, vb in T.tri_vertices(a, b, up)]
    return _order_up(vs)


def _order_up(vs: list) -> list:
    e1, e2 = vs[1] - vs[0], vs[2] - vs[0]
    if e1[2] * e2[0] - e1[0] * e2[2] < 0.0:              # cross(e1, e2).y
        vs[1], vs[2] = vs[2], vs[1]
    return vs


class TonnetzWalk(Source):
    """Per frame: one H2D copy of a small arena from a pinned ring slot, one event record, the kernel and the
    upsample.  The host reads the chord and key (tonal.py) and moves the tile and camera; no host round trips."""
    name = "tonnetz_walk"
    label = "Tonnetz walk"
    description = ("An endless glass floor of Euler's Tonnetz where each chord is a lit triangle that flips over a "
                   "shared edge at every chord change, leaving footprints, with the key's home strip glowing and the "
                   "distance from home counted in moves.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ice", render_scale=0.5, period=1.0, drift=0.0),
            "tile_size": num(0.06, 0.2, 0.11, "Tile size", unit="× height", basic=True),
            "tilt": num(25.0, 70.0, 36.0, "Camera tilt", unit="°"),
            "glass": num(0.0, 1.0, 0.7, "Glass", basic=True),
            "labels": num(0.0, 1.0, 0.75, "Note names"),
            "footprints_s": num(10.0, 120.0, 50.0, "Footprints", unit="s"),
            "show_comma": flag(False, "Show the comma"),
        }

    # -- setup -----------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, _OPTS).get_function("tonnetz")
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        n0, n1, n2 = bake_names()
        self.nm = [cp.asarray(np.ascontiguousarray(a).ravel()) for a in (n0, n1, n2)]
        gb, self.cwb, self.chb, self.advb = bake_glyphs(int(round(0.06 * self.h)))
        gs, self.cws, self.chs, self.advs = bake_glyphs(int(round(0.026 * self.h)))
        self.gbig = cp.asarray(np.ascontiguousarray(gb).ravel())
        self.gsmall = cp.asarray(np.ascontiguousarray(gs).ravel())
        self.foot = cp.full(FOOT * FOOT * 2, -1e6, cp.float32)
        self.arena = cp.zeros(N_AR, cp.float32)
        try:
            self._pin = cp.cuda.alloc_pinned_memory(_RING * N_AR * 4)
            host = np.frombuffer(self._pin, np.float32, _RING * N_AR).reshape(_RING, N_AR)
        except Exception as exc:
            _log_once("pin", "tonnetz_walk: no pinned memory (%s); uploading from pageable memory", exc)
            self._pin = None
            host = np.zeros((_RING, N_AR), np.float32)
        host[:] = 0.0
        self._spare = np.zeros(N_AR, np.float32)
        self._host = [host[i] for i in range(_RING)] + [self._spare]
        self._ev = [cp.cuda.Event(block=False, disable_timing=True, interprocess=False) for _ in range(_RING)]
        self._slot = 0
        # music
        self.e = np.zeros(12, np.float32)
        self.ease = T.AttackRelease(12, 0.08, 0.45)
        self.keyr = T.KeyResolver()
        self.chords = T.ChordReader(rate_hz=15)
        self.lv = np.zeros(12)
        # the walk: the tile (absolute lattice), the queue of P/L/R ops, the flip in progress
        self.tile = (0, 0, True)
        self.tid = 0
        self.q: deque = deque()
        self.q_end = 0                                   # the triad at the end of the queue
        self.flip = None                                 # (from, to, hinge0, hinge1, free, t, dur, from_tid, to_tid)
        self.stamps: list = []
        self.visit = np.full(24, -1e6)
        self.ghost = 0.0
        self.ghost_t = 0.0
        self.ghost_op = 0
        self.ghost_walk = False
        # the key and its home strip(s)
        self.key = 0
        self.known = False
        self.home = (0, 0)                               # the home strip's major-tonic up triangle (a0, b0)
        self.slots = [self._slot_of(0, (0, 0), 0.0, 0.45), None]
        self.spell = 0
        # the camera: target (world, float64, absolute), its velocity, yaw
        cx, cz = self._centroid_world(self.tile)
        self.cam = np.array([cx, cz])
        self.cam_v = np.zeros(2)
        self.base = (0, 0)
        self.yaw = 0.35
        self.sway = self.breath = self.cph = self.drift = self.kick = self.clock = 0.0
        self.last_sound = -1e9
        self.heard = False
        self.conf = 0.0
        self.tglow_on = 0.0
        self.ra = 0.0
        self.ra_flash = 0.0
        self._text_key = None
        self._text = np.zeros(18 + 3 * MAXC * 2, np.float32)
        self._first = True

    # -- lattice helpers -------------------------------------------------------------------------------------------
    @staticmethod
    def _centroid_world(tri) -> tuple[float, float]:
        ca, cb = T.tri_centroid(*tri)
        return ca + 0.5 * cb, cb * SQ3_2

    @staticmethod
    def _slot_of(key: int, home, weight: float, target: float) -> dict:
        t = key % 12
        maj = (t + 3) % 12 if key >= 12 else t
        mask_maj = sum(1 << ((maj + k) % 12) for k in (0, 5, 7))
        mask_min = sum(1 << ((maj + k) % 12) for k in (2, 4, 9))
        return {"key": key, "home": home, "w": weight, "target": target, "tonic": t + (12 if key >= 12 else 0),
                "dom": (t + 7) % 12, "mmaj": mask_maj, "mmin": mask_min, "minor": key >= 12}

    def _anchor(self, key: int, known: bool) -> None:
        """Put the key's home strip at the copy of its (relative) major tonic nearest the camera target and cross-fade
        to it over 2 s; set the spelling so that home is spelled as the key is."""
        t = key % 12
        maj = (t + 3) % 12 if key >= 12 else t
        ta, tb = T.tri_centroid(*self.tile)               # near the tile (the camera converges on it)
        a0, b0, _up = T.nearest_copy(maj, ta, tb)
        self.home = (a0, b0)
        old = self.slots[0]
        new = self._slot_of(key, (a0, b0), 0.0, 1.0 if known else 0.45)
        if old is not None and (old["key"] != key or old["home"] != (a0, b0)):
            old["target"] = 0.0
            self.slots = [new, old]
        else:
            new["w"] = old["w"] if old is not None else 0.0
            self.slots = [new, self.slots[1]]
        # the home tonic spelled as the key spells it (majors Db..F#; a minor key by its relative major)
        want = T.key_tonic_lof(maj)
        self.spell = (a0 + 4 * b0) - want
        self.key, self.known = key, known

    def _cam_world_abs(self) -> tuple[float, float]:
        return float(self.cam[0]), float(self.cam[1])

    # -- the walk --------------------------------------------------------------------------------------------------
    def _enqueue(self, tid: int) -> None:
        if tid < 0 or tid == self.q_end:
            return
        for op in T.plr_path(self.q_end, tid):
            self.q.append(op)
        self.q_end = tid

    def _stamp(self, tri) -> None:
        a, b, up = tri
        cell = (((a % FOOT) * FOOT) + (b % FOOT)) * 2 + (0 if up else 1)
        if len(self.stamps) >= 8:
            self.stamps.pop(0)
        self.stamps.append((cell, self.clock))

    def _land_now(self, op: int, ghost: bool) -> None:
        if not ghost:
            self._stamp(self.tile)
        self.tile = T.tri_move(*self.tile, op)
        self.tid = T.plr(self.tid, op)

    def _start_flip(self, ghost: bool) -> None:
        if len(self.q) >= SKIP_AT:
            while len(self.q) > 1:
                self._land_now(self.q.popleft(), ghost)
        op = self.q.popleft()
        frm = self.tile
        to = T.tri_move(*frm, op)
        vf = set(T.tri_vertices(*frm))
        vt = set(T.tri_vertices(*to))
        hinge = sorted(vf & vt)
        free = (vf - vt).pop()
        if not ghost:
            self._stamp(frm)
        dur = GHOST_FLIP_S if ghost else (FLIP_FAST_S if len(self.q) + 1 >= 3 else FLIP_S)
        to_tid = T.plr(self.tid, op)
        self.flip = [frm, to, hinge[0], hinge[1], free, 0.0, dur, self.tid, to_tid]
        self.tile, self.tid = to, to_tid                 # the walk's state is where it lands

    def _advance(self, dt: float, ghost: bool) -> None:
        for _ in range(SKIP_AT + 2):                     # bounded: a few landings per frame at most
            if self.flip is not None:
                self.flip[5] += dt
                if self.flip[5] < self.flip[6]:
                    return
                dt = self.flip[5] - self.flip[6]
                self.flip = None
            if not self.q:
                return
            self._start_flip(ghost)

    def _tile_vertices(self) -> tuple[list, float]:
        """The tile's three vertices (world, relative to the base) and the flip's progress phi / pi."""
        ba, bb = self.base
        if self.flip is None:
            return tri_world(*self.tile, ba, bb), 0.0
        frm, _to, h0, h1, fv, t, dur = self.flip[:7]
        x = min(max(t / dur, 0.0), 1.0)
        s = x * x * x * (x * (x * 6.0 - 15.0) + 10.0)
        phi = math.pi * s

        def wp(n):
            return np.array([(n[0] - ba) + 0.5 * (n[1] - bb), 0.004, (n[1] - bb) * SQ3_2])
        A, B, V = wp(h0), wp(h1), wp(fv)
        e1, e2 = B - A, V - A
        if e1[2] * e2[0] - e1[0] * e2[2] < 0.0:          # at rest the normal points up (front = the old chord)
            A, B = B, A
        ed = (B - A) / max(float(np.linalg.norm(B - A)), 1e-9)
        m = A + float(np.dot(V - A, ed)) * ed
        r = V - m
        L = float(np.linalg.norm(r))
        Vp = m + math.cos(phi) * r + np.array([0.0, math.sin(phi) * L, 0.0])
        return [A, B, Vp], s

    # -- colours ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _chord_colour(tid: int, coeffs, drift: float) -> list[float]:
        warm, cool = (1.0, 0.80, 0.50), (0.55, 0.70, 1.0)
        base = cool if tid >= 12 else warm
        pc_ = _at_luma(_pal(drift + 0.2, coeffs), 0.6)
        return [0.78 * b + 0.22 * c for b, c in zip(base, pc_)]

    # -- the readout -----------------------------------------------------------------------------------------------
    def _layout(self, L: int, s: str, x0: float, y0: float, big: bool, alpha: float, buf: np.ndarray) -> None:
        adv = self.advb if big else self.advs
        idx, xs, x = [], [], 0.0
        for ch in s[:MAXC]:
            g = _GI.get(ch, _GI["?"])
            idx.append(g)
            xs.append(x)
            x += adv[g]
        n = len(idx)
        buf[6 * L:6 * L + 6] = (n, x0, y0, 1.0 if big else 0.0, alpha, x)
        o = 18 + L * MAXC * 2
        if n:
            buf[o:o + 2 * n] = np.array([v for pair in zip(idx, xs) for v in pair], np.float32)

    def _readout(self, show_comma: bool) -> None:
        """Lay out the corner's three lines when their text changes (cached)."""
        a, b, up = self.tile
        tid = self.tid
        root = T.tri_vertices(a, b, up)[0]
        name = _pretty(T.lof_name(root[0] + 4 * root[1] - self.spell)) + ("m" if tid >= 12 else "")
        tonic = self.slots[0]["tonic"]
        n = int(T.PLR_DIST[tid, tonic])
        roman = T.roman(tid % 12, "min" if tid >= 12 else "maj", self.key, unicode=True)
        moves = "home" if n == 0 else f"{n} move{'s' if n > 1 else ''} from home"
        line2 = f"{roman}   ·   {moves}"
        line3 = T.key_label(self.key, unicode=True) if self.known else ""
        if show_comma:
            h0 = self.home
            ca = h0[0] + 1.0 / 3.0
            cb = h0[1] + 1.0 / 3.0
            na, nb, _ = T.nearest_copy(tid, ca, cb)
            da, db = a - na, b - nb
            if da or db:
                c = T.lattice_cents(da, db)
                cs = f"{abs(c):.1f} ¢ {'sharp' if c > 0 else 'flat'} of home"
                line3 = f"{line3}   ·   {cs}" if line3 else cs
        key = (name, line2, line3)
        if key == self._text_key:
            return
        self._text_key = key
        buf = self._text
        buf[:] = 0.0
        m = 0.05 * self.h
        x0 = 0.045 * self.w
        y3 = self.h - m - self.chs
        y2 = y3 - 1.15 * self.chs
        y1 = y2 - 1.02 * self.chb
        self._layout(0, name, x0, y1, True, 1.0, buf)
        self._layout(1, line2, x0 + 0.04 * self.chb, y2, False, 0.85, buf)
        self._layout(2, line3, x0 + 0.04 * self.chb, y3, False, 0.6, buf)

    # -- render ----------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        cp = self.ctx.cp
        p = self.step_params(dt)
        dt = float(dt) if isinstance(dt, (int, float)) and math.isfinite(dt) and dt > 0.0 else 0.0
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt
        a_k = 1.0 - math.exp(-dt / 0.12) if dt > 0 else 0.0
        self.kick += (pulse - self.kick) * a_k
        self.drift += float(p["drift_speed"]) * spd * dt
        self.breath += 0.07 * spd * dt
        self.sway += 0.045 * spd * dt
        self.cph += (0.25 + 0.55 * e) * spd * dt
        self.yaw += math.radians(2.0) * spd * dt

        # ---- the music: pc energy, key, chord -----------------------------------------------------------------
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        notes = getattr(f, "notes", None) if f is not None else None
        bass = -1
        if notes is not None and getattr(notes, "shape", None) == (128, 4):
            T.pc_energy(notes, self.e)
            np.nan_to_num(self.e, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
            bass = T.bass_pitch(notes)
        else:
            self.e.fill(0.0)
        ev = self.ease.step(self.e, dt)
        sounding = float(self.e.max()) > 0.02
        if sounding:
            self.last_sound = self.clock
            self.heard = True
        ch = self.keyr.update(self.e, dt, getattr(f, "key", None), getattr(f, "mode", None))
        if self._first:
            self._anchor(self.keyr.key, self.keyr.known)
            self._first = False
        elif ch is not None or self.keyr.known != self.known:
            self._anchor(self.keyr.key, self.keyr.known)
        silent_for = self.clock - self.last_sound
        ghost_on = silent_for > (GHOST_AFTER_S if self.heard else GHOST_FIRST_S)
        if self.chords.update(ev, dt, bass % 12 if bass >= 0 else -1, self.keyr.key) and not ghost_on:
            if self.ghost_walk:                          # the music is back: drop the ghost's pending steps
                self.q.clear()
                self.q_end = self.tid
                self.ghost_walk = False
            self._enqueue(self.chords.triad())
        self.conf += (self.chords.conf - self.conf) * (1.0 - math.exp(-dt / 0.3) if dt > 0 else 0.0)
        np.copyto(self.lv, ev)
        np.multiply(self.lv, -1.6, out=self.lv)
        np.exp(self.lv, out=self.lv)
        np.subtract(1.0, self.lv, out=self.lv)          # 1 - exp(-1.6 e): 0..1 per pc

        # ---- the ghost: after a minute of silence a pale tile walks the P-L cycle -----------------------------------
        a_g = 1.0 - math.exp(-dt / 1.2) if dt > 0 else 0.0
        self.ghost += ((1.0 if ghost_on else 0.0) - self.ghost) * a_g
        if ghost_on:
            self.ghost_t += spd * dt
            if self.ghost_t >= GHOST_STEP_S and self.flip is None and not self.q:
                self.ghost_t = 0.0
                self.q.append(self.ghost_op)
                self.ghost_walk = True
                self.ghost_op = 1 - self.ghost_op       # P, L, P, L ...
                self.q_end = T.plr(self.tid, self.q[-1])
        else:
            self.ghost_t = 0.0
            self.ghost_op = 0
        self._advance(dt, ghost_on)
        if not ghost_on:
            self.visit[self.tid] = self.clock

        # ---- the camera: a critically damped spring on the tile's centroid; slow yaw and sway ---------------------
        tx, tz = self._centroid_world(self.tile)
        if dt > 0:
            y = self.cam - (tx, tz)
            ex = math.exp(-OMEGA * dt)
            k = self.cam_v + OMEGA * y
            self.cam = np.array([tx, tz]) + (y + k * dt) * ex
            self.cam_v = (self.cam_v - OMEGA * k * dt) * ex
        # the home strip follows when the walk has wandered off it (same key, cross-faded)
        hx, hz = self._centroid_world((self.home[0], self.home[1], True))
        if math.hypot(tx - hx, tz - hz) > REANCHOR and self.flip is None:
            self._anchor(self.key, self.known)
        for s in self.slots:
            if s is not None:
                s["w"] += (s["target"] - s["w"]) * (1.0 - math.exp(-dt / 0.65) if dt > 0 else 0.0)
        if self.slots[1] is not None and self.slots[1]["w"] < 1e-3 and self.slots[1]["target"] == 0.0:
            self.slots[1] = None
        # rebase the lattice origin near the camera (float32 never runs out)
        ca, cb = T.xy_lattice(*self._cam_world_abs())
        if abs(ca - self.base[0]) > 24 or abs(cb - self.base[1]) > 24:
            self.base = (int(round(ca)), int(round(cb)))
        ba, bb = self.base
        ox, oz = ba + 0.5 * bb, bb * SQ3_2                # the base's world position
        # spelling drift: keep the tile's names within a sane range (an enharmonic respelling, rarely)
        root = T.tri_vertices(*self.tile)[0]
        rl = root[0] + 4 * root[1] - self.spell
        if rl < -11 or rl > 13:
            self.spell += 12 * int(math.floor((rl - 1) / 12.0 + 0.5))

        tilt = math.radians(min(max(float(p["tilt"]), 10.0), 85.0))
        tanh = math.tan(0.5 * FOV_Y)
        D = 1.0 / (2.0 * max(float(p["tile_size"]), 0.02) * tanh)
        delta = math.atan(TARGET_NDC * tanh)
        pitch = tilt - delta
        yaw = self.yaw + (0.03 + 0.10 * e) * math.sin(2 * math.pi * self.sway)
        sy, cy_ = math.sin(yaw), math.cos(yaw)
        fwd = (sy * math.cos(pitch), -math.sin(pitch), cy_ * math.cos(pitch))
        right = (cy_, 0.0, -sy)
        upv = (fwd[1] * right[2] - fwd[2] * right[1], fwd[2] * right[0] - fwd[0] * right[2],
               fwd[0] * right[1] - fwd[1] * right[0])
        txr, tzr = float(self.cam[0]) - ox, float(self.cam[1]) - oz
        cam = (txr - sy * D * math.cos(tilt), D * math.sin(tilt), tzr - cy_ * D * math.cos(tilt))

        # ---- this frame's host slot ---------------------------------------------------------------------------------
        i = self._slot
        evn = self._ev[i]
        if evn.done:
            self._slot = (i + 1) % _RING
        else:
            evn, i = None, _RING
        hb = self._host[i]
        hb[:] = 0.0
        coeffs = palette_coeffs(p["palette"])
        per = float(p["palette_period"])
        dr = 0.3 * self.drift
        hb[0:12] = coeffs
        hb[P_W], hb[P_H] = self.w, self.h
        hb[P_CAM:P_CAM + 3] = cam
        hb[P_RT:P_RT + 3] = right
        hb[P_UP:P_UP + 3] = upv
        hb[P_FW:P_FW + 3] = fwd
        hb[P_TANH], hb[P_ASP] = tanh, self.w / float(self.h)
        hb[P_BA], hb[P_BB] = ba, bb
        hb[P_E], hb[P_KICK], hb[P_GAIN] = e, self.kick, float(p["intensity"])
        hb[P_NOW] = self.clock
        hb[P_CPH] = self.cph % 6283.0
        hb[P_BR] = 0.5 + 0.5 * math.sin(2 * math.pi * self.breath)
        hb[P_GLASS] = float(p["glass"])
        hb[P_LAB] = float(p["labels"])
        hb[P_LRAD] = (1.6 + 4.5 * e) * (0.4 + 0.8 * float(p["labels"]))
        hb[P_FX], hb[P_FZ] = txr, tzr
        hb[P_FOG0], hb[P_FOGD] = 1.2 * D, 0.4 / D
        hb[P_SPELL] = self.spell
        hb[P_TAU] = max(float(p["footprints_s"]), 1.0)
        hb[P_NST] = len(self.stamps)
        for k, (cell, tm) in enumerate(self.stamps[:8]):
            hb[O_ST + 2 * k], hb[O_ST + 2 * k + 1] = cell, tm
        self.stamps.clear()
        hb[P_SEED] = self.seed & 0xFFFF
        # colours from the palette (luma-normalised, so every palette gives a dark sky and a bright horizon)
        hb[P_SKYT:P_SKYT + 3] = _sat(_at_luma(_pal(dr + 0.12 * per, coeffs), 0.02), 1.6)
        hb[P_HOR:P_HOR + 3] = _sat(_at_luma(_pal(dr + 0.04 * per, coeffs), 0.11), 1.5)
        hb[P_GT:P_GT + 3] = _at_luma(_pal(dr + 0.3 * per, coeffs), 0.55)
        hb[P_WARM:P_WARM + 3] = (1.0, 0.6, 0.26)
        for k, base in enumerate(((0.32, 0.55, 1.0), (0.78, 0.86, 0.32), (0.2, 0.85, 0.78))):
            pc_ = _at_luma(_pal(dr + (0.15 + 0.33 * k) * per, coeffs), 0.55)
            hb[P_I5 + 3 * k:P_I5 + 3 * k + 3] = [0.6 * b + 0.4 * c for b, c in zip(base, pc_)]
        hb[P_LCH], hb[P_NODE], hb[P_LW] = 0.4, 0.035, 0.0075
        hb[P_NLC:P_NLC + 3] = [0.6 + 0.4 * c for c in _at_luma(_pal(dr + 0.45 * per, coeffs), 0.8)]
        hb[P_SCO:P_SCO + 3] = (1.0, 0.36, 0.1)
        hb[P_GOLD:P_GOLD + 3] = (1.0, 0.78, 0.32)
        hb[P_CRIM:P_CRIM + 3] = (0.95, 0.12, 0.2)
        hb[P_FAR] = float(self.lv.mean())
        hb[P_GHC:P_GHC + 3] = (0.78, 0.82, 0.95)
        hb[O_PC:O_PC + 12] = self.lv
        hb[O_AGE:O_AGE + 24] = np.minimum(self.clock - self.visit, 1e6)
        # the tile
        vs, s = self._tile_vertices()
        for k in range(3):
            hb[P_TV + 3 * k:P_TV + 3 * k + 3] = vs[k]
        if self.flip is not None:
            c_from = self._chord_colour(self.flip[7], coeffs, dr)
            c_to = self._chord_colour(self.flip[8], coeffs, dr)
        else:
            c_from = c_to = self._chord_colour(self.tid, coeffs, dr)
        hb[P_TFR:P_TFR + 3] = c_from
        hb[P_TBK:P_TBK + 3] = c_to
        hb[P_GHOST] = self.ghost
        tg = (0.7 + 0.45 * self.conf) * (0.85 + 0.3 * e) * (1.0 + 0.6 * self.kick) * (1.0 - 0.5 * self.ghost)
        hb[P_TGLOW] = tg
        cxw = sum(v[0] for v in vs) / 3.0
        czw = sum(v[2] for v in vs) / 3.0
        hb[P_POOL], hb[P_POOL + 1], hb[P_POOL + 2] = cxw, czw, 0.45 * tg
        # the strips
        for k, sl in enumerate(self.slots):
            if sl is None:
                continue
            o = P_STRIP + 8 * k
            hb[o:o + 8] = (sl["home"][0], sl["home"][1], sl["w"], sl["tonic"], sl["dom"], sl["mmaj"], sl["mmin"],
                           0.0 if sl["minor"] else 1.0)
        # the readout: blooms in on a chord change, dims while the ghost walks
        if self.chords.changed:
            self.ra_flash = 1.0
        self.ra_flash *= math.exp(-dt / 0.35) if dt > 0 else 1.0
        tgt = (0.95 if self.heard else 0.6) * (1.0 - 0.6 * self.ghost) * (1.0 - 0.45 * self.ra_flash)
        self.ra += (tgt - self.ra) * (1.0 - math.exp(-dt / 0.25) if dt > 0 else 0.0)
        hb[P_RA] = self.ra
        hb[P_TXB:P_TXB + 4] = (self.cwb, self.chb, self.cws, self.chs)
        self._readout(bool(p["show_comma"]))
        hb[O_TL:] = self._text

        stream = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.arena.data.ptr, hb.ctypes.data, hb.nbytes, rt.memcpyHostToDevice, stream.ptr)
        if evn is not None:
            evn.record(stream)
        launch_2d(self.k, self.w, self.h, (self.buf, self.arena, self.nm[0], self.nm[1], self.nm[2],
                                           self.gbig, self.gsmall, self.foot))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self._ev = None
        self.buf = self.arena = self.foot = self.gbig = self.gsmall = None
        self.nm = []
