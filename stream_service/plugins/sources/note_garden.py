"""Note garden: the piece grows a garden that stays, under a sky that dawns and dusks with the music.

Source: Original (inspired by MIDI Forest, https://midiforest.pages.dev/ (every note plants a seed that grows an
        L-system tree), and Kazushi Mukaiyama's Music Plant,
        https://www.asahi-net.or.jp/~HB9T-Ktd/music/English/Research/MediaArt/music_plant.html (each key press grows
        a branch, duration sets its length); ideas only, nothing of either is copied)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
  A garden on gentle grassy hills, seen from the side, in three depth layers with mist between them, under a wide
  sky, with a meadow of tall grass, reeds and seed heads in silhouette across the bottom of the frame.  The plants
  are large (the front ones stand up to about two thirds of the frame): the front layer reads as dark silhouettes
  with a back-lit rim, the layers behind are coloured by the low sun and softened by the mist, and the flowers
  glow like lanterns in every layer.  The sky follows the room's energy slowly (about 20 s): quiet music brings
  dusk and night (deep blue, stars, a pale moon), lively music brings a golden dawn and then day.  Fireflies
  drift over the garden whenever nothing is being played and fade back when the music returns.  Everything sways
  in the breeze; the camera drifts slowly sideways, so the near hills slide past the far ones.

  Each musical phrase grows one plant.  The garden keeps them: by the end of a piece it is the piece's portrait,
  one plant per phrase, standing where its bass was among the piece's recent basses (low on the left, high on the
  right, spread across the width; a new plant takes the gap nearest its place).  It starts from a garden of eight
  plants grown from the seed.  When the garden is full, the oldest plant sinks back into its hill to make room.

How the music drives it
  * A phrase is a run of notes between rests longer than 1.2 s (a phrase without rests is cut after 12 s or 36
    notes; a plant still smaller than 8 segments keeps growing across rests up to 6 s, so slow music grows plants,
    not seedlings).  Its first chord plants a new stem at the place its lowest note sets; loud phrases are planted
    in the front layer, soft ones further back.  Each plant aims for a height: below it the stem is pulled upright
    and grows long segments, near it the segments shorten and arch over, so a long phrase fills out into a crown.
  * Each melody note (the top of what is played, kept apart from the accompaniment like a skyline) grows the stem
    one segment: the segment grows while the key is held, so long notes make long internodes; louder notes make
    it thicker; a rising line bends the stem one way and a falling line the other, always pulled back upright.
  * A leap of a fourth or more away from every growing arm forks the plant: a side branch shoots off from where
    the melody leapt (upward leaps nearly upright, downward leaps wide) and the melody carries on along it.  When
    the melody comes back to a register an arm already holds, that arm grows again, so a tune that moves between
    registers grows several arms (up to six).
  * The other notes of a chord open leaves along the new segment; the accompaniment adds leaves lower down.
  * A chord the hands hold (three keys for 0.8 s, or two for 1.3 s; at most one such flower per plant every
    2.5 s), or a cadence (a pause of 0.75 s after a consonant chord or a single note), opens a flower at the
    growing tip.  Its colour is the key's place on the circle of fifths (C, G, D ... walk round the
    colour wheel, minor keys deeper), its size is how loud the chord was, its petals how many notes it had.  A
    pause on a dissonance leaves a closed bud instead, and the next consonant cadence on that plant opens it.  A
    phrase that never paused (a fast etude) still ends in a flower at its tip when the next plant starts.
  * While a note sounds, the segment it grew glows (pedal-held notes keep glowing); the growing tip shines as it
    grows; a flower brightens whenever its chord's notes sound again.
  * The damper pedal is a breeze: holding it raises the wind, so the whole garden and the meadow sway more while
    the pedal holds the sound, and each press sends a gust across the room from left to right (about 2.6 s): the
    grass and the plants bow as it passes.  The breeze also carries the fireflies.

Look and regional knobs
  ``style`` morphs the plants from fern (curling fronds with small paired leaflets) through willow (arching
  branches with long hanging leaves) to blossom (stiff angular twigs covered in round petals in the key's colour);
  each plant keeps most of the style it was planted with, so a garden grown across style changes is mixed.
  ``density``: leaves per segment and how many plants the garden keeps (10 to 32).  ``wind``: the breeze.
  ``depth``: how far apart the three layers are (parallax, scale and mist; 0 is flat).  ``glow``: ink to light,
  from coloured strokes lit by the sun to dark stems with luminous edges.  ``sky``: night to day, around which the
  energy moves the light.

Macros: energy -> growth speed (a segment blooms in over 0.85 s at 0, 0.35 s at 1), sway, gust strength, the
glow of the strokes, the camera's drift and the sky's dawn; pulse (bind note_on) -> flower bloom brightness and
the growing tips (eased in, an amplitude, never a position); speed -> every motion rate; intensity -> gain; palette /
palette_period / drift_speed colour the stems, leaves, sky tint and fireflies, and tint the flowers (whose hue is
the key).  Alive with no notes: a small garden grown from the seed is there from the start, it sways, the camera
drifts, clouds pass, stars twinkle and fireflies come out.

Rendering: the host keeps every plant as a list of primitives in preallocated arrays (40 plants x 160 primitives:
stem segments, leaves, flowers), each stored relative to its parent (attach point, angle, length, radius, strike
and release times).  A plant's block is uploaded only when it changes (a note, a release, a flower), plus one
small arena per frame (the params and the plant table).  Kernel 1 (one thread per plant) walks each plant's
primitives in order, forward kinematics: growth, the release-dependent length, style shaping, the sway (each
segment bends a little more, so the wind bends whole stems) and the colours, into screen-space records with
bounding boxes, back layer first, and each plant's box.  Kernel 2 renders 32 x 32 pixel tiles (16 x 16 threads,
2 x 2 pixels each): sky, then the plants whose box touches the tile, in draw order, each by an ordered ballot
compaction of its records overlapping the tile into shared memory, drawn in order as SDF capsules, leaves and
flowers composited over the hills of their layer (mist, crest grass); then the meadow, fireflies and a soft
shoulder.  The grass and meadow bend space by the breeze (taller stalks bend more), so each pixel tests five
near-straight blades; the clouds, grass and fireflies share one set of per-cell randoms per thread.
render_scale 0.7 (2D) by default, then present(); the garden keeps at most ~1900 living primitives at density
0.55 (1100 + 1500 x density), which bounds the draw: 5.3 ms at 4K on the P4000 with 22 full plants.  The
phrase is read from the notes' own rests, not the beat clock's phrase (sources do not see the beat clock).
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

try:                                    # the ensemble's register window (Sound.register): per-pitch weights
    from stream_service.ensemble import note_weights as _note_weights
except Exception:                       # pragma: no cover - older engines
    _note_weights = None

log = logging.getLogger("stream_service.plugins.note_garden")

# -- budgets and layout ------------------------------------------------------------------------------------------
NP = 40                       # plant slots
NPP = 160                     # primitives per plant
PF = 20                       # floats per primitive (host model, uploaded per plant block)
PLF = 16                      # floats per plant (uploaded every frame)
RF = 20                       # floats per screen-space record (kernel 1 -> kernel 2)
PB = 8                        # plants per block in kernel 1 (its forward-kinematics scratch is in shared memory)
BOXF = 8                      # floats per plant box (kernel 1 -> kernel 2: bbox, record offset, count)
BUDGET_MAX = NP - 8           # plants kept at density 1 (the rest of the slots hold withering ones)
PRIM_BUDGET0, PRIM_BUDGET1 = 1100, 1500   # living primitives kept at density 0 .. +1 (the draw cost's bound)

# primitive fields
K_KIND, K_PAR, K_AT, K_REL, K_SIZE, K_R0, K_R1, K_TON, K_TOFF, K_VEL = range(10)
K_HUE, K_FLEX, K_PITCH, K_X13, K_OPEN, K_OPREV, K_TOPEN, K_P2, K_P3, K_X19 = range(10, 20)
STEM, LEAF, FLOWER = 0, 1, 2

# the arena: params (P_*) then the plant table
_PIDX = dict(P_W=12, P_H=13, P_NOW=14, P_E=15, P_KICK=16, P_GAIN=17, P_WIND=18, P_WPH=19, P_STYLE=20, P_DENS=21,
             P_DEPTH=22, P_GLOW=23, P_DAY=24, P_DRIFT=25, P_PERIOD=26, P_CAMX=27, P_CAMY=28, P_V0=29, P_RW=30,
             P_GROW=31, P_FLY=32, P_SKYPH=33, P_FLYAMT=34, P_N=35, P_NPL=36, P_CLOUD=37, P_FLYW=38,
             P_GA0=40, P_GX0=41)           # the three gusts: amplitude, front (room x / H) at 40 + 2k, 41 + 2k
NPAR = 48
NGUST = 3

# -- the music model ---------------------------------------------------------------------------------------------
GROUP_S = 0.045               # onsets this close are one event (a chord)
PHRASE_GAP_S = 1.2            # a rest longer than this ends the phrase: the next note plants a new plant
CADENCE_S = 0.75              # a pause this long after an event is a cadence
HELD_S = 0.8                  # a chord the hands still hold this long opens a flower (3+ notes; 2 notes 1.3 s)
HELD_GAP_S = 2.5              # at most one held-chord flower per plant this often
PHRASE_MAX_S = 12.0           # a phrase with no rest is cut into a new plant after this long ...
MAX_STEMS = 36                # ... or this many stem segments
MIN_STEMS = 8                 # a plant smaller than this keeps growing across rests (slow music grows plants, ...
LONG_REST_S = 6.0             # ... not seedlings), unless the rest is this long
PHRASE_MAX_SLOW_S = 30.0      # ... or it has grown this long
LEAP = 5                      # semitones: a leap forks the plant
MAX_ORDER = 4                 # branch orders at most
MAX_AXES = 6                  # growing arms per plant (a melody between registers grows several)
SKY_TOL, SKY_FALL = 9.0, 24.0 # the melody skyline (as echo_arcs): within 9 semitones of a line falling 24 st/s
LEAVES_PER_EVENT = 3
FLOWERS_MAX = 8
RESERVE = 12                  # primitives kept free per plant for flowers and spurs
WITHER_S = 6.0
SEG0, SEG1 = 0.050, 0.040     # a stem segment's length (plant units, ~H in the front layer) at velocity 0 .. +1
SEED_PLANTS = 8               # the garden grown from the seed before the first note
T_HELD = 1.0e9                # t_off while the key is down
ONSET_W = 0.3                 # a strike's share in the key tracker's pitch-class energy

_TRIADS = [(1 << r) | (1 << ((r + 4) % 12)) | (1 << ((r + 7) % 12)) for r in range(12)] + \
          [(1 << r) | (1 << ((r + 3) % 12)) | (1 << ((r + 7) % 12)) for r in range(12)]

_OPTS = ("--std=c++14", "--use_fast_math")

_DEFS = "".join(f"#define {k} {v}\n" for k, v in _PIDX.items())

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NP """ + str(NP) + r"""
#define NPP """ + str(NPP) + r"""
#define PF """ + str(PF) + r"""
#define PLF """ + str(PLF) + r"""
#define RF """ + str(RF) + r"""
#define PB """ + str(PB) + r"""
#define BOXF """ + str(BOXF) + r"""
#define NMAX (NP * NPP)
#define LEAF_HALO 0.12f                               // a leaf glows beyond its edge only above this (a struck note)
""" + _DEFS + r"""

__device__ __forceinline__ float3 f3(float x, float y, float z) { return make_float3(x, y, z); }
__device__ __forceinline__ float3 add3(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 scl3(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 madd3(float3 c, float3 a, float s) {
    return make_float3(c.x + a.x * s, c.y + a.y * s, c.z + a.z * s);
}
__device__ __forceinline__ float3 lerp3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
// a vivid hue wheel (0..1), the flowers' key colour
__device__ float3 hue_rgb(float h) {
    const float t = 6.2831853f * h;
    float3 c = f3(0.5f + 0.5f * __cosf(t), 0.5f + 0.5f * __cosf(t - 2.0943951f), 0.5f + 0.5f * __cosf(t + 2.0943951f));
    const float m = (c.x + c.y + c.z) * (1.f / 3.f);
    return f3(clamp01(m + 1.3f * (c.x - m)), clamp01(m + 1.3f * (c.y - m)), clamp01(m + 1.3f * (c.z - m)));
}
// cheap per-cell randoms: one integer hash gives four 8-bit uniforms
__device__ __forceinline__ unsigned int hcell(const int c, const unsigned int s) {
    return hash_u((unsigned int)c * 0x9E3779B1U + s * 0x85EBCA77U);
}
__device__ __forceinline__ float hb8(const unsigned int h, const int k) {
    return (float)((h >> (8 * k)) & 255u) * (1.f / 255.f);
}
__device__ __forceinline__ float vn1(float x, unsigned int s) {
    const float i = floorf(x);
    float f = x - i;
    f = f * f * (3.f - 2.f * f);
    const int ii = (int)i;
    const float a = (float)(hcell(ii, s) >> 8) * (1.f / 16777216.f);
    const float b = (float)(hcell(ii + 1, s) >> 8) * (1.f / 16777216.f);
    return a + (b - a) * f;
}

// layers: 0 front, 1 middle, 2 back; depth (0..1) separates them
__device__ __forceinline__ float lay_par(int L, float dp) { return 1.f - dp * 0.32f * L; }
__device__ __forceinline__ float lay_scale(int L, float dp) { return 1.f - dp * 0.21f * L; }
__device__ __forceinline__ float lay_ground(int L, float dp) { return 0.912f - dp * 0.085f * L; }

// the hill line of layer L at screen x (internal px), y in px: one landscape across the room's screens
__device__ float hill_y(const int L, const float sx, const float* __restrict__ P, const unsigned int seed) {
    const float H = P[P_H], dp = clamp01(P[P_DEPTH]);
    const float par = lay_par(L, dp);
    const float rx = (sx + P[P_V0] + P[P_CAMX] * par) / H;
    const float q = rx / lay_scale(L, dp);
    const unsigned int s = seed + 977u * (unsigned int)L;
    const float amp = (0.020f + 0.024f * L) * (0.45f + 0.55f * dp);
    const float h = 0.62f * vn1(0.85f * q, s) + 0.28f * vn1(2.3f * q, s + 7u) + 0.10f * vn1(6.1f * q, s + 13u);
    return H * (lay_ground(L, dp) - amp * (h - 0.4f)) + P[P_CAMY] * par;
}

// the breeze.  gust_at: the pedal's gust fronts crossing the room at room x (in H).  breeze: how far the tip of a
// 0.25 H stalk standing at room x is pushed (in H): the wind's waves roll left to right, the gusts sweep through.
__device__ __forceinline__ float gust_at(const float rx, const float* __restrict__ P) {
    float g = 0.f;
    #pragma unroll
    for (int k = 0; k < 3; ++k) {
        const float dx = rx - P[P_GX0 + 2 * k];
        g += P[P_GA0 + 2 * k] * __expf(-dx * dx * 7.f);
    }
    return g;
}
__device__ __forceinline__ float breeze(const float rx, const float* __restrict__ P) {
    const float w = fminf(fmaxf(P[P_WIND], 0.f), 2.f);
    const float wv = 0.7f * __sinf(P[P_WPH] - 1.3f * rx) + 0.3f * __sinf(2.3f * P[P_WPH] - 3.7f * rx + 1.3f);
    return w * (0.020f * wv + 0.010f * w) + 0.045f * gust_at(rx, P);
}

// ------------------------------------------------------------------------------------------------------------
// Kernel 1: one thread per plant walks its primitives (parents first) and writes screen-space records, and the
// plant's box (by draw rank) so kernel 2 can skip whole plants per tile.
// record: 0 ax 1 ay 2 bx 3 by 4 ra|R 5 rb|rot 6-8 rgb 9 alpha 10 kind+4*layer 11 e0 12 e1 13 x0 14 x1 15 fog
//         16-19 bbox (minx miny maxx maxy; empty when min > max)
// box:    0-3 bbox of the plant's visible records, 4 first record, 5 record count
// ------------------------------------------------------------------------------------------------------------
extern "C" __global__ void ng_prep(const float* __restrict__ P, const float* __restrict__ plants,
                                   const float* __restrict__ prims, const float* __restrict__ notes,
                                   float* __restrict__ rec, float* __restrict__ pbox, const unsigned int seed)
{
    __shared__ float4 fk[PB * NPP];                   // per primitive: base x, y, angle, length (or flower centre)
    const int lt = threadIdx.x, i = blockIdx.x * PB + lt;
    if (lt >= PB || i >= NP) return;                  // no barriers in this kernel
    const float* pl = plants + i * PLF;
    if (!(pl[0] > 0.5f)) return;
    const int rank = (int)(pl[12] + 0.5f);
    if (rank < 0 || rank >= NP) return;
    float* pb = pbox + rank * BOXF;
    pb[0] = 1.f; pb[1] = 1.f; pb[2] = 0.f; pb[3] = 0.f; pb[4] = 0.f; pb[5] = 0.f;
    const int n = min(max((int)pl[1], 0), NPP);
    const int off = (int)pl[8];
    if (off < 0 || off + n > NMAX) return;
    const float H = P[P_H], now = P[P_NOW], e = clamp01(P[P_E]), kick = clamp01(P[P_KICK]);
    const float dp = clamp01(P[P_DEPTH]);
    const int L = min(max((int)(pl[3] + 0.5f), 0), 2);
    const float par = lay_par(L, dp);
    const float lsc = lay_scale(L, dp) * fmaxf(pl[4], 0.1f) * H;
    const float fade = clamp01(pl[5]);
    const float live = fade * fade * (3.f - 2.f * fade);
    const float shrink = 0.3f + 0.7f * live;
    const float st = fminf(fmaxf(pl[6] + (P[P_STYLE] - pl[6]) * 0.35f, 0.f), 2.f);
    const float wf = clamp01(1.f - st), ww = clamp01(1.f - fabsf(st - 1.f)), wb = clamp01(st - 1.f);
    const float rootx = pl[2] * P[P_RW] - P[P_V0] - P[P_CAMX] * par;
    const float rxh = pl[2] * P[P_RW] / H;
    const float rooty = hill_y(L, rootx, P, seed) + (0.012f + 0.16f * (1.f - live)) * lsc;   // sinks as it withers
    // the breeze: the wind's waves (the garden sways nearly together, so the wind reads) and the pedal's gusts
    const float wind = fminf(fmaxf(P[P_WIND], 0.f), 2.f);
    const float wave = 0.7f * __sinf(P[P_WPH] - 1.3f * rxh + 0.25f * pl[9])
                       + 0.3f * __sinf(2.3f * P[P_WPH] - 3.7f * rxh + 1.3f + 6.2831853f * pl[11]);
    const float bend = wind * (0.024f * wave + 0.010f * wind) + 0.042f * gust_at(rxh, P);
    const float grow_s = fmaxf(P[P_GROW], 0.05f);
    const float glow = clamp01(P[P_GLOW] + 0.25f * (0.5f - clamp01(P[P_DAY])));
    const float gl = L == 0 ? 0.45f : 1.f;            // the front layer is a silhouette: its own glow is low
    const float per = P[P_PERIOD], dr = P[P_DRIFT];
    const float fogk = (L == 0 ? 0.f : (L == 1 ? 0.26f : 0.48f)) * (0.25f + 0.75f * dp);
    float bx0 = 1e30f, by0 = 1e30f, bx1 = -1e30f, by1 = -1e30f;
    float4* F = fk + lt * NPP;
    for (int j = 0; j < n; ++j) {
        const float* q = prims + ((size_t)i * NPP + j) * PF;
        float* R = rec + (size_t)(off + j) * RF;
        const int kind = min(max((int)(q[0] + 0.5f), 0), 2);
        const int pj = (int)floorf(q[1] + 0.5f);
        const float age = fmaxf(now - q[7], 0.f);
        float bx, by, pang;
        if (pj >= 0 && pj < j) {
            const float4 pf = F[pj];
            const float at = clamp01(q[2]);
            bx = pf.x + __sinf(pf.z) * pf.w * at;
            by = pf.y - __cosf(pf.z) * pf.w * at;
            pang = pf.z;
        } else {
            bx = rootx; by = rooty; pang = 0.f;
        }
        // every primitive bends a little more than its parent, so whole stems arch in the wind
        float ang = pang + q[11] * bend;
        const float pitch = q[12];
        float lit = 0.f;                              // this strike, while it sounds (the pedal keeps it)
        if (pitch >= 0.f && pitch < 128.f) {
            const int pi = (int)pitch;
            const float lv = notes[4 * pi], aon = notes[4 * pi + 2];
            if (fabsf(age - aon) < 0.35f) lit = clamp01(lv) * smooth(0.f, 0.12f, age);
        }
        const float pn = (pitch >= 0.f) ? clamp01((pitch - 21.f) / 87.f) : 0.5f;
        const float vel = clamp01(q[9]);
        float3 col;
        float ax = bx, ay = by, ex = bx, ey = by, ra = 0.f, rb = 0.f, e0 = 0.f, e1 = 0.f, x0 = 0.f, x1 = 0.f;
        float pad = 1.f, size = 0.f;
        if (kind == 0) {                              // a stem segment: a tapered capsule
            ang += q[3] * (1.f + 0.25f * wb);
            if (q[13] > 0.5f) {                       // branches: willow arches over, fern curls in
                const float sg = tanhf(4.f * ang);
                ang += sg * (0.20f * ww - 0.10f * wf);
                ang = fminf(fmaxf(ang, -2.8f), 2.8f);
            }
            const float g = smooth(0.f, grow_s, age);
            const float dur = fminf(fmaxf(fminf(now, q[8]) - q[7], 0.f), 4.f);
            const float Lc = q[4] * lsc * shrink * g * (0.42f + 0.58f * (1.f - __expf(-dur / 0.55f)));
            ra = fmaxf(q[5] * lsc * shrink, 0.9f);
            rb = fmaxf(q[6] * lsc * shrink * (0.35f + 0.65f * g), 0.8f);
            ex = bx + __sinf(ang) * Lc;
            ey = by - __cosf(ang) * Lc;
            const float3 pc = pal((0.04f + 0.12f * pn + pl[10]) * per + dr, P);
            col = f3(0.06f + 0.85f * pc.x, 0.06f + 0.85f * pc.y, 0.06f + 0.85f * pc.z);
            e0 = (gl * glow * (0.16f + 0.22f * e) * (0.5f + 0.5f * vel) + 1.25f * lit) * live;
            e1 = vel * smooth(0.f, 0.1f, age) * __expf(-age / (0.6f + 0.5f * (1.f - e))) * (1.f + 0.6f * kick) * 1.6f * live;
            x0 = 2.6f * rb + 0.004f * H;              // the growing tip's glow radius
            const float rm = fmaxf(ra, rb);
            pad = rm + 1.5f;
            if (e0 > 0.003f) pad = fmaxf(pad, rm + 2.5f * (0.9f * rm + 1.5f));
            if (e1 > 0.003f) pad = fmaxf(pad, 2.6f * x0);
            size = Lc + rm;
            F[j] = make_float4(bx, by, ang, Lc);
        } else if (kind == 1) {                       // a leaf: unfolds outwards as it grows
            const float side = q[13] >= 0.f ? 1.f : -1.f;
            const float g = smooth(0.04f, 0.04f + 1.6f * grow_s, age);
            ang += q[3] * (0.25f + 0.75f * g);
            ang += (side * 2.6f - ang) * (0.55f * ww); // willow leaves hang
            ang -= side * 0.10f * wf;                 // fern leaflets point a little up the frond
            const float lenm = 1.2f * wf + 1.55f * ww + 0.75f * wb;
            const float Lc = q[4] * lsc * shrink * g * lenm;
            ex = bx + __sinf(ang) * Lc;
            ey = by - __cosf(ang) * Lc;
            x0 = 0.32f * wf + 0.16f * ww + 0.44f * wb;  // half width / length
            x1 = side;
            float3 lc = pal((0.20f + 0.16f * pn + pl[10] + 0.06f * q[19]) * per + dr, P);
            const float3 bc = lerp3(hue_rgb(q[10]), f3(1.f, 0.84f, 0.90f), 0.45f);
            col = lerp3(lc, bc, 0.85f * wb);          // blossom: the leaves become petals
            e0 = (gl * glow * 0.20f * (0.5f + 0.5f * vel) + 0.8f * lit) * live;
            pad = 1.25f * x0 * Lc + 1.5f + (e0 > LEAF_HALO ? 3.f * (0.25f * x0 * Lc + 1.5f) : 0.f);
            size = Lc;
            F[j] = make_float4(bx, by, ang, Lc);
        } else {                                      // a flower (or a bud) at a tip
            ang += q[3];
            const float g = smooth(0.f, 0.7f, age);
            const float to = smooth(0.f, 1.6f, fmaxf(now - q[16], 0.f));
            const float op = clamp01(q[15] + (q[14] - q[15]) * to);
            const float Rr = q[4] * lsc * shrink * g * (0.4f + 0.6f * op);
            float res = 0.f;                          // its chord still sounding, or played again
            #pragma unroll
            for (int k = 0; k < 3; ++k) {
                const float pp = (k == 0) ? q[12] : q[16 + k];
                if (pp >= 0.f && pp < 128.f) res = fmaxf(res, notes[4 * (int)pp]);
            }
            res = clamp01(res);
            const float fresh = __expf(-age / 12.f);
            const float bloom = (0.26f + 0.24f * glow + (0.40f + 0.70f * fresh) * res) * (1.f + 0.8f * kick) * op * live;
            float3 fc = lerp3(hue_rgb(q[10]), pal(q[10] * per + dr, P), 0.3f);
            if (q[19] > 0.5f) fc = f3(fc.x * 0.80f, fc.y * 0.72f, fc.z * 0.92f + 0.06f);   // minor: deeper
            col = fc;
            const float cdx = __sinf(ang), cdy = -__cosf(ang);
            ax = ex = bx + cdx * 0.35f * Rr;
            ay = ey = by + cdy * 0.35f * Rr;
            ra = Rr;
            rb = ang - 1.5707963f;                    // the petals' rotation: along the stem
            e1 = bloom;
            x0 = fminf(fmaxf(q[13], 3.f), 9.f);       // petals
            x1 = op;
            pad = Rr + 3.f * (0.6f * Rr + 3.f);
            size = Rr;
            F[j] = make_float4(ax, ay, ang, 0.f);
        }
        R[0] = ax; R[1] = ay; R[2] = ex; R[3] = ey; R[4] = ra; R[5] = rb;
        R[6] = col.x; R[7] = col.y; R[8] = col.z; R[9] = live;
        R[10] = (float)(kind + 4 * L); R[11] = e0; R[12] = e1; R[13] = x0; R[14] = x1; R[15] = fogk;
        const bool vis = live > 0.003f && size > 0.3f && ax == ax && ay == ay && ex == ex && ey == ey;
        if (vis) {
            R[16] = fminf(ax, ex) - pad; R[17] = fminf(ay, ey) - pad;
            R[18] = fmaxf(ax, ex) + pad; R[19] = fmaxf(ay, ey) + pad;
            bx0 = fminf(bx0, R[16]); by0 = fminf(by0, R[17]); bx1 = fmaxf(bx1, R[18]); by1 = fmaxf(by1, R[19]);
        } else {
            R[16] = 1.f; R[17] = 1.f; R[18] = 0.f; R[19] = 0.f;
        }
    }
    pb[4] = (float)off; pb[5] = (float)n;
    if (bx0 <= bx1) { pb[0] = bx0; pb[1] = by0; pb[2] = bx1; pb[3] = by1; }
}

// ------------------------------------------------------------------------------------------------------------
// Kernel 2: 32 x 32 pixel tiles, 16 x 16 threads, 2 x 2 pixels per thread (they share the per-cell randoms of
// the clouds, grass and fireflies).
// ------------------------------------------------------------------------------------------------------------
struct Look {
    float3 zen, hor, fogc, sunc, g0, g1, g2, fly, sunv, rimc, silc;
    float sunx, suny, sunk, disc, stars, m0, m1, m2, bodyk, rim, cloud, sil;
};

__device__ Look make_look(const float* __restrict__ P) {
    Look K;
    const float d = clamp01(P[P_DAY]), dp = clamp01(P[P_DEPTH]);
    const float3 Zn = f3(0.012f, 0.018f, 0.055f), Zd = f3(0.10f, 0.11f, 0.27f), Zy = f3(0.30f, 0.50f, 0.80f);
    const float3 Hn = f3(0.05f, 0.055f, 0.13f), Hd = f3(0.88f, 0.50f, 0.36f), Hy = f3(0.78f, 0.85f, 0.92f);
    if (d < 0.5f) {
        const float a = smooth(0.f, 0.5f, d);
        K.zen = lerp3(Zn, Zd, a); K.hor = lerp3(Hn, Hd, a);
    } else {
        const float a = smooth(0.5f, 1.f, d);
        K.zen = lerp3(Zd, Zy, a); K.hor = lerp3(Hd, Hy, a);
    }
    const float3 tint = pal(P[P_DRIFT] + 0.55f, P);
    K.zen = lerp3(K.zen, scl3(tint, 0.3f + 0.5f * d), 0.12f);
    K.hor = lerp3(K.hor, tint, 0.10f);
    K.fogc = lerp3(K.hor, K.zen, 0.3f);
    const float3 Sn = f3(0.62f, 0.68f, 0.90f), Sd = f3(1.0f, 0.60f, 0.32f), Sy = f3(1.0f, 0.94f, 0.82f);
    K.sunc = d < 0.5f ? lerp3(Sn, Sd, smooth(0.f, 0.5f, d)) : lerp3(Sd, Sy, smooth(0.5f, 1.f, d));
    // the moon rides high at night, glides down to a low sunset at dusk, and the sun climbs with the day
    K.sunx = P[P_RW] * (0.30f + 0.38f * smooth(0.2f, 0.55f, d)) - P[P_V0] - P[P_CAMX] * 0.12f;
    const float sy = d < 0.5f ? 0.22f + 0.40f * smooth(0.f, 0.5f, d) : 0.62f - 0.42f * smooth(0.5f, 1.f, d);
    K.suny = P[P_H] * sy + P[P_CAMY] * 0.1f;
    K.sunk = 0.10f + 0.32f * d;
    K.disc = 0.45f + 0.55f * d;
    K.stars = 1.f - smooth(0.2f, 0.6f, d);
    K.cloud = clamp01(P[P_CLOUD]);
    float lx = (K.sunx - 0.5f * P[P_W]) / fmaxf(P[P_W], 1.f);
    lx = fminf(fmaxf(lx, -0.8f), 0.8f);
    const float ln = rsqrtf(lx * lx + 0.49f + 0.30f);
    K.sunv = f3(lx * ln, -0.7f * ln, 0.548f * ln);
    const float3 fol = pal(P[P_DRIFT] + 0.3f, P);
    const float3 G = lerp3(f3(0.010f, 0.014f, 0.024f), add3(f3(0.12f, 0.17f, 0.10f), scl3(fol, 0.06f)), d);
    const float fs = 0.25f + 0.75f * dp;
    K.g0 = G;
    K.g1 = lerp3(G, K.fogc, 0.34f * fs);
    K.g2 = lerp3(G, K.fogc, 0.58f * fs);
    const float dusk = 0.6f + 0.4f * (1.f - fabsf(d - 0.5f) * 2.f);
    K.m0 = 0.28f * dp * dusk; K.m1 = 0.38f * dp * dusk; K.m2 = 0.46f * dp * dusk;
    const float glow = clamp01(P[P_GLOW] + 0.25f * (0.5f - d));
    K.bodyk = (1.f - 0.68f * glow) * (0.5f + 0.5f * d) + 0.08f;
    K.rim = 0.06f + 0.10f * d;
    K.fly = lerp3(f3(1.0f, 0.86f, 0.42f), pal(P[P_DRIFT] + 0.15f, P), 0.25f);
    K.sil = clamp01(0.85f - 0.55f * d);               // the front layer and the meadow read as silhouettes
    K.rimc = lerp3(K.hor, K.sunc, 0.45f);             // the back light on their edges
    K.silc = add3(scl3(G, 0.40f), f3(0.004f, 0.005f, 0.012f));
    return K;
}

// the sky: gradient, sun or moon, stars (per pixel); cl is the thread's cloud noise (clouds are soft)
__device__ float3 sky_at(const float px, const float py, const Look& K, const float* __restrict__ P,
                         const unsigned int seed, const float cl) {
    const float H = P[P_H];
    const float v = py / H;
    const float t = clamp01(v / 0.86f);
    float3 c = lerp3(K.zen, K.hor, t * sqrtf(t));
    {   // the sun, or the moon
        const float dx = (px - K.sunx) / H, dy = (py - K.suny) / H;
        const float dd = sqrtf(dx * dx + dy * dy);
        const float halo = K.sunk * (0.55f * __expf(-dd / 0.20f) + 0.45f * __expf(-dd / 0.05f));
        const float disc = (1.f - smooth(0.026f, 0.031f, dd)) * K.disc;
        c = madd3(c, K.sunc, halo + disc);
    }
    if (K.stars > 0.01f && v < 0.7f) {
        const float cs = fmaxf(0.0026f * H, 1.5f);
        const float sx = (px + P[P_V0] + 0.05f * P[P_CAMX]) / cs, sy = py / cs;
        const int ix = (int)floorf(sx), iy = (int)floorf(sy);
        const unsigned int h = hcell(ix * 7919 + iy, seed + 401u);
        const float hs = (float)(h >> 8) * (1.f / 16777216.f);
        if (hs > 0.9965f) {
            const float fx = sx - ix - 0.5f, fy = sy - iy - 0.5f;
            const float tw = 0.55f + 0.45f * __sinf(P[P_FLY] * (1.3f + 2.f * hb8(h, 0)) + 6.2831853f * hb8(hcell(ix, iy + seed), 1));
            const float b = __expf(-(fx * fx + fy * fy) * 9.f) * tw * K.stars * (1.f - smooth(0.35f, 0.7f, v))
                            * (0.4f + 1.6f * (hs - 0.9965f) / 0.0035f);
            c = add3(c, f3(0.85f * b, 0.9f * b, b));
        }
    }
    if (cl > 0.f) {
        const float band = smooth(0.06f, 0.25f, v) * (1.f - smooth(0.5f, 0.72f, v));
        if (band > 0.f) {
            const float3 cc = madd3(lerp3(scl3(K.zen, 1.5f), K.hor, 0.55f + 0.3f * t), K.sunc, 0.12f);
            c = lerp3(c, cc, 0.55f * cl * band);
        }
    }
    return c;
}

__device__ __forceinline__ void hill_px(float3& c, const float py, const float hy, const float gcov, const float grim,
                                        const float mist, const float3 gc, const Look& K, const float H) {
    if (py < hy && mist > 0.f) c = lerp3(c, K.fogc, mist * __expf(-(hy - py) / (0.07f * H)));
    const float cov = fmaxf(clamp01(py - hy + 0.5f), gcov);
    if (cov > 0.f) {
        const float below = fmaxf(py - hy, 0.f) / H;
        float3 g = scl3(gc, 1.f - 0.45f * clamp01(below * 5.f));
        g = madd3(g, K.sunc, K.rim * (__expf(-fmaxf(py - hy, 0.f) / (0.004f * H + 1.f)) + 1.4f * grim));
        c = lerp3(c, g, cov);
    }
}

// the hill of layer L under four pixels (columns a, b; rows a, b): the mist rising behind it, the grass on its
// crest (blades rooted along the line, bent by the breeze) and the ground itself
__device__ __forceinline__ void comp_hill4(const int L, const float pxa, const float pya, const float hya, const float hyb,
                           float3& c0, float3& c1, float3& c2, float3& c3, const Look& K,
                           const float* __restrict__ P, const unsigned int seed) {
    const float H = P[P_H], dp = clamp01(P[P_DEPTH]);
    const float mist = L == 0 ? K.m0 : (L == 1 ? K.m1 : K.m2);
    const float3 gc = L == 0 ? K.g0 : (L == 1 ? K.g1 : K.g2);
    const float ls = lay_scale(L, dp);
    const float lpx = ls * H;                         // px per hill unit
    const float gmax = L == 0 ? 0.085f : 0.062f;      // the tallest grass, in hill units
    // the heights of the four pixels above the line, in hill units
    float y[4] = {(hya - pya) / lpx, (hyb - pya) / lpx, (hya - pya - 1.f) / lpx, (hyb - pya - 1.f) / lpx};
    float gcov[4] = {0.f, 0.f, 0.f, 0.f}, grim[4] = {0.f, 0.f, 0.f, 0.f};
    if (fmaxf(y[0], y[1]) > -0.002f && fminf(y[2], y[3]) < gmax) {
        const float par = lay_par(L, dp);
        const float qa = (pxa + P[P_V0] + P[P_CAMX] * par) / lpx;     // hill units (as hill_y's q)
        const float qb = qa + 1.f / lpx;
        const float Ba = breeze(qa * ls, P) * 16.f * ls, Bb = breeze(qb * ls, P) * 16.f * ls;  // per unit height^2
        float u[4];
        u[0] = qa - Ba * y[0] * y[0]; u[1] = qb - Bb * y[1] * y[1];
        u[2] = qa - Ba * y[2] * y[2]; u[3] = qb - Bb * y[3] * y[3];
        const float cw = 0.0058f;
        const int ic = (int)floorf(u[0] / cw);
        const unsigned int sd = seed + 701u + 131u * (unsigned int)L;
        #pragma unroll
        for (int k = -2; k <= 2; ++k) {
            const int ci = ic + k;
            const unsigned int h = hcell(ci, sd);
            const float h1 = hb8(h, 0), h2 = hb8(h, 1), h3 = hb8(h, 2), h4 = hb8(h, 3);
            const float tall = smooth(0.45f, 0.85f, vn1((float)ci * 0.06f, sd + 5u));
            const float hb = (L == 0 ? 1.35f : 1.f) * (0.010f + 0.016f * h2 + (0.012f + 0.030f * h3) * tall);
            const float rx = (ci + 0.1f + 0.8f * h1) * cw;
            const float lean = (h4 - 0.5f) * 0.12f;
            const float wid = cw * (0.30f + 0.25f * h3);
            #pragma unroll
            for (int p = 0; p < 4; ++p) {
                const float yy = y[p];
                if (yy < -0.002f || yy > hb) continue;
                const float uu = fmaxf(yy, 0.f) / hb;
                const float d = fabsf(u[p] - rx - lean * yy) * lpx - wid * lpx * (1.f - uu) - 0.3f;
                const float a = clamp01(0.5f - d);
                gcov[p] = fmaxf(gcov[p], a);
                grim[p] = fmaxf(grim[p], a * uu);
            }
        }
    }
    hill_px(c0, pya, hya, gcov[0], grim[0], mist, gc, K, H);
    hill_px(c1, pya, hyb, gcov[1], grim[1], mist, gc, K, H);
    hill_px(c2, pya + 1.f, hya, gcov[2], grim[2], mist, gc, K, H);
    hill_px(c3, pya + 1.f, hyb, gcov[3], grim[3], mist, gc, K, H);
}

__device__ __forceinline__ float halo_win(float d, float hl) {
    if (d <= 0.f) return 1.f;
    const float w = clamp01(1.f - d / (3.f * hl));
    return __expf(-d / hl) * w * w;
}

__device__ void draw_prim(const float* __restrict__ R, const int kind, const int L, const float px, const float py,
                          const float hyL, float3& c, const Look& K) {
    const float mask = clamp01(hyL + 1.f - py);       // nothing below its own ground line
    if (mask <= 0.f) return;
    const float alpha = R[9] * mask;
    const float fog = R[15];
    const float3 base = f3(R[6], R[7], R[8]);
    const float sil = L == 0 ? K.sil : 0.f;           // the front layer: a silhouette with a lit rim
    const float lift = L == 0 ? 1.f : (L == 1 ? 1.5f : 1.3f);   // the layers behind: coloured, lit by the low sun
    float cov = 0.f, halo = 0.f, emis = 0.f, rim = 0.f;
    float3 body = base;
    float3 ecol = f3(1.15f * base.x + 0.08f, 1.15f * base.y + 0.08f, 1.15f * base.z + 0.08f);
    if (kind == 0) {
        const float ax = R[0], ay = R[1];
        const float bx = R[2] - ax, by = R[3] - ay, pxa = px - ax, pya = py - ay;
        const float bb = bx * bx + by * by;
        const float h = bb > 1e-6f ? clamp01((pxa * bx + pya * by) / bb) : 0.f;
        const float dx = pxa - bx * h, dy = pya - by * h;
        const float dist = sqrtf(dx * dx + dy * dy);
        const float r = R[4] + (R[5] - R[4]) * h;
        const float d = dist - r;
        float tip = 0.f;
        if (R[12] > 0.003f) {                         // the growing tip
            const float tdx = px - R[2], tdy = py - R[3], tr = fmaxf(R[13], 0.5f);
            tip = R[12] * __expf(-(tdx * tdx + tdy * tdy) / (tr * tr));
        }
        if (d > (R[11] > 0.003f ? 2.5f * (0.9f * r + 1.5f) : 0.5f) && tip < 0.002f) return;
        cov = clamp01(0.5f - d);
        const float qn = clamp01(dist / fmaxf(r, 0.5f));
        const float nz = sqrtf(fmaxf(1.f - qn * qn, 0.f));
        if (cov > 0.f) {
            const float inv = qn / fmaxf(dist, 1e-4f);
            const float lam = fmaxf(0.f, dx * inv * K.sunv.x + dy * inv * K.sunv.y + nz * K.sunv.z);
            body = scl3(base, K.bodyk * (1.f + 0.3f * (lift - 1.f)) * (0.35f + 0.65f * lam));
            rim = smooth(0.5f, 1.f, qn);
        }
        emis = R[11] * (0.35f + 0.65f * nz);
        halo = R[11] * 0.45f * halo_win(d, 0.9f * r + 1.5f) + tip;
    } else if (kind == 1) {
        const float tx = R[2] - R[0], ty = R[3] - R[1];
        const float Lc = sqrtf(tx * tx + ty * ty);
        if (Lc < 0.75f) return;
        const float iL = 1.f / Lc;
        const float ux = tx * iL, uy = ty * iL;
        const float pxa = px - R[0], pya = py - R[1];
        const float uL = pxa * ux + pya * uy, vL = pya * ux - pxa * uy;
        const float wr = R[13];
        const bool hal = R[11] > LEAF_HALO;
        const float marg = 1.f + (hal ? 3.f * (0.25f * Lc * wr + 1.5f) : 0.f);
        if (uL < -marg || uL > Lc + marg || fabsf(vL) > 1.2f * wr * Lc + marg) return;
        const float u = uL * iL, v = vL * iL;
        const float uc = clamp01(u);
        const float hw = wr * __powf(fmaxf(__sinf(3.14159265f * uc), 1e-4f), 0.75f) * (1.f + 0.4f * (0.5f - uc));
        float d;
        if (u <= 0.f || u >= 1.f) {
            const float du = (u <= 0.f ? u : u - 1.f) * Lc, dv = v * Lc;
            d = sqrtf(du * du + dv * dv);
        } else {
            d = (fabsf(v) - hw) * Lc * 0.92f;
        }
        cov = clamp01(0.5f - d);
        if (cov > 0.f) {
            const float rib = 1.f - smooth(0.f, 0.7f / Lc + 0.06f * hw + 1e-3f, fabsf(v));
            const float nx = (v >= 0.f ? -uy : uy), ny = (v >= 0.f ? ux : -ux);    // the leaf's outward side
            const float lam = 0.72f + 0.28f * fmaxf(0.f, nx * K.sunv.x + ny * K.sunv.y);
            body = scl3(base, K.bodyk * lift * lam * (0.62f + 0.38f * uc) * (1.f - 0.3f * rib));
            rim = smooth(-2.5f, 0.f, d);
        }
        emis = R[11];
        halo = hal ? R[11] * 0.3f * halo_win(d, 0.25f * Lc * wr + 1.5f) : 0.f;
    } else {                                          // a flower: a lantern in every layer
        const float Rr = R[4];
        if (Rr < 0.3f) return;
        const float pxa = px - R[0], pya = py - R[1];
        const float cr = __cosf(R[5]), sr = __sinf(R[5]);
        const float al = pxa * cr + pya * sr, ac = -pxa * sr + pya * cr;
        const float op = R[14];
        const float sq = 0.5f + 0.5f * op;            // a bud is slimmer across
        const float acs = ac / sq;
        const float rr = sqrtf(al * al + acs * acs);
        float d, rn = 1.f;
        if (rr > Rr + 1.f) {                          // outside the petals: the halo only
            d = (rr - Rr) * sq;
        } else {
            const float th = atan2f(acs, al);
            const float pet = fabsf(__cosf(0.5f * R[13] * th));
            const float lobe = __powf(fmaxf(pet, 1e-4f), 0.6f);
            const float edge = Rr * (1.f - 0.5f * op * (1.f - lobe));
            d = (rr - edge) * sq;
            cov = clamp01(0.5f - d);
            rn = clamp01(rr / fmaxf(edge, 0.5f));
            if (cov > 0.f) {
                const float rc = 0.24f * Rr * (0.3f + 0.7f * op);
                const float inC = (1.f - smooth(rc - 0.8f, rc + 0.8f, rr)) * op;
                float3 petal = scl3(base, (0.66f + 0.34f * rn) * (0.84f + 0.16f * pet) * fmaxf(K.bodyk, 0.85f));
                petal = add3(petal, scl3(f3(1.f, 1.f, 1.f), 0.26f * (1.f - rn) * op));
                body = lerp3(petal, f3(1.0f, 0.82f, 0.36f), inC);
            }
        }
        emis = R[12] * (0.35f + 0.65f * (1.f - rn));
        halo = R[12] * 0.5f * halo_win(d, 0.6f * Rr + 3.f);
    }
    if (cov <= 0.f && halo < 1e-4f) return;
    if (kind != 2) body = lerp3(body, add3(scl3(base, 0.08f), K.silc), sil);
    body = lerp3(body, K.fogc, fog);
    const float ef = alpha * (1.f - 0.5f * fog);
    c = lerp3(c, body, cov * alpha);
    if (kind != 2) c = madd3(c, K.rimc, rim * cov * alpha * (0.08f + 0.34f * sil) * (1.f - fog));
    c = madd3(c, ecol, (emis * cov + halo) * ef);
}

// the foreground meadow: grass, reeds and seed heads in silhouette, rooted below the frame and nearer than the
// front layer (more parallax).  Space is bent by the breeze (taller stalks bend more), so the blades themselves
// are near-straight and each pixel looks at five cells; the cells' randoms are shared by the thread's 4 pixels.
#define FG_MAX 0.31f
__device__ __forceinline__ void meadow4(const float pxa, const float pya, float3& c0, float3& c1, float3& c2, float3& c3,
                        const Look& K, const float* __restrict__ P, const unsigned int seed) {
    const float H = P[P_H];
    const float yroot = 1.03f * H;
    if (pya + 1.f < yroot - FG_MAX * H) return;
    const float off = P[P_V0] + 1.25f * P[P_CAMX];
    const float rxa = (pxa + off) / H, rxb = rxa + 1.f / H;
    const float Ba = breeze(rxa, P) * 16.f, Bb = breeze(rxb, P) * 16.f;
    const float ya = (yroot - pya) / H, yb = ya - 1.f / H;
    float y[4] = {ya, ya, yb, yb};
    float u[4] = {rxa - Ba * ya * ya, rxb - Bb * ya * ya, rxa - Ba * yb * yb, rxb - Bb * yb * yb};
    const float cw = 0.0066f;
    const int ic = (int)floorf(u[0] / cw);
    float cv[4] = {0.f, 0.f, 0.f, 0.f}, rm[4] = {0.f, 0.f, 0.f, 0.f};
    #pragma unroll
    for (int k = -2; k <= 2; ++k) {
        const int ci = ic + k;
        const unsigned int h = hcell(ci, seed + 601u);
        const float h1 = hb8(h, 0), h2 = hb8(h, 1), h3 = hb8(h, 2), h4 = hb8(h, 3);
        const float tall = smooth(0.50f, 0.86f, vn1((float)ci * 0.045f, seed + 611u));
        const float hb = 0.035f + 0.045f * h2 + (0.04f + 0.19f * h3) * tall;     // its height, in H
        if (yb > hb + 0.012f) continue;
        const float rx = (ci + 0.1f + 0.8f * h1) * cw;
        const float lean = (h4 - 0.5f) * 0.08f;
        const float wid = cw * (0.24f + 0.26f * h2) * H;  // half width at the root, px
        const bool head = h3 > 0.78f && tall > 0.3f;      // a reed with a seed head
        #pragma unroll
        for (int p = 0; p < 4; ++p) {
            const float yy = y[p];
            const float uu = yy / hb;
            if (uu > 1.1f) continue;
            const float dxp = fabsf(u[p] - rx - lean * yy * (0.6f + 0.4f * uu)) * H;
            float hw = wid * fmaxf(1.f - uu, 0.f) + 0.35f;
            if (head) {                               // an ellipse riding the top of the stalk
                const float eh = (uu - 0.93f) / 0.16f;
                if (fabsf(eh) < 1.f) hw = fmaxf(hw, 1.7f * wid * sqrtf(1.f - eh * eh));
                else if (uu > 1.f) continue;
            } else if (uu > 1.f) continue;
            const float d = dxp - hw;
            const float a = clamp01(0.5f - d);
            cv[p] = fmaxf(cv[p], a);
            rm[p] = fmaxf(rm[p], a * smooth(-2.4f, 0.f, d) * (0.35f + 0.65f * uu));
        }
    }
    if (cv[0] > 0.f) c0 = madd3(lerp3(c0, K.silc, cv[0]), K.rimc, 0.30f * rm[0]);
    if (cv[1] > 0.f) c1 = madd3(lerp3(c1, K.silc, cv[1]), K.rimc, 0.30f * rm[1]);
    if (cv[2] > 0.f) c2 = madd3(lerp3(c2, K.silc, cv[2]), K.rimc, 0.30f * rm[2]);
    if (cv[3] > 0.f) c3 = madd3(lerp3(c3, K.silc, cv[3]), K.rimc, 0.30f * rm[3]);
}

// fireflies for the thread's four pixels: the four cells around them, drifting with the breeze
__device__ __forceinline__ void fireflies4(const float pxa, const float pya, const float* __restrict__ P, const unsigned int seed,
                           float* f) {
    f[0] = f[1] = f[2] = f[3] = 0.f;
    const float amt = P[P_FLYAMT];
    if (amt < 0.005f) return;
    const float H = P[P_H];
    if (pya + 1.f < 0.28f * H) return;
    const float cs = 0.09f * H, fly = P[P_FLY];
    const float ox = P[P_V0] + 1.15f * P[P_CAMX] + P[P_FLYW] * H, oy = 0.35f * fly * cs;
    const float fx = (pxa + ox) / cs, fy = (pya + oy) / cs;
    const int ix0 = (int)floorf(fx - 0.5f), iy0 = (int)floorf(fy - 0.5f);
    const float sr2 = (0.0058f * H) * (0.0058f * H), hr2 = (0.024f * H) * (0.024f * H);
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int ix = ix0 + (k & 1), iy = iy0 + (k >> 1);
        const unsigned int h = hash_u((unsigned int)ix * 0x9E3779B1U ^ hash_u((unsigned int)iy * 0x85EBCA77U + seed + 101u));
        if (hb8(h, 0) > 0.42f * amt) continue;
        const unsigned int h2 = hash_u(h + 0x27d4eb2dU);
        const float a1 = hb8(h, 1), a2 = hb8(h, 2), a3 = hb8(h, 3), a4 = hb8(h2, 0), a5 = hb8(h2, 1), a6 = hb8(h2, 2);
        const float cx = (ix + 0.5f + 0.3f * __sinf(fly * (0.45f + 0.5f * a1) + 6.2831853f * a2)) * cs - ox;
        const float cy = (iy + 0.5f + 0.3f * __sinf(fly * (0.35f + 0.45f * a3) + 6.2831853f * a4)) * cs - oy;
        const float bl = smooth(0.25f, 0.9f, 0.5f + 0.5f * __sinf(fly * (0.7f + 0.8f * a5) + 6.2831853f * a6));
        if (bl <= 0.f) continue;
        #pragma unroll
        for (int p = 0; p < 4; ++p) {
            const float dx = pxa + (p & 1) - cx, dy = pya + (p >> 1) - cy;
            const float r2 = dx * dx + dy * dy;
            f[p] += bl * (__expf(-r2 / sr2) + 0.22f * __expf(-r2 / hr2));
        }
    }
    const float a = fminf(amt, 1.f);
    f[0] *= a * smooth(0.28f * H, 0.55f * H, pya + 0.5f);
    f[1] *= a * smooth(0.28f * H, 0.55f * H, pya + 0.5f);
    f[2] *= a * smooth(0.28f * H, 0.55f * H, pya + 1.5f);
    f[3] *= a * smooth(0.28f * H, 0.55f * H, pya + 1.5f);
}

__device__ __forceinline__ float shoulder(float x) {
    x = fmaxf(x, 0.f);
    return x < 0.8f ? x : 0.8f + 0.2f * (1.f - __expf(-(x - 0.8f) / 0.2f));
}

__device__ __forceinline__ float pick3(const int L, const float a, const float b, const float c) {
    return L == 0 ? a : (L == 1 ? b : c);
}

extern "C" __global__ void __launch_bounds__(256)
ng_draw(uchar4* __restrict__ out, const float* __restrict__ P, const float* __restrict__ rec,
        const float* __restrict__ pbox, const unsigned int seed)
{
    __shared__ float s_rec[256 * RF];
    __shared__ int s_wc[8];
    __shared__ int s_pl[NP];
    const int W = (int)P[P_W], H = (int)P[P_H];
    const float Hf = (float)H;
    const int tid = threadIdx.y * 16 + threadIdx.x, lane = tid & 31, warp = tid >> 5;
    const int bx0 = blockIdx.x * 32, by0 = blockIdx.y * 32;
    const int x0 = bx0 + 2 * threadIdx.x, y0 = by0 + 2 * threadIdx.y;
    const Look K = make_look(P);
    const float pxa = x0 + 0.5f, pxb = x0 + 1.5f, pya = y0 + 0.5f, pyb = y0 + 1.5f;
    const float ha0 = hill_y(0, pxa, P, seed), ha1 = hill_y(1, pxa, P, seed), ha2 = hill_y(2, pxa, P, seed);
    const float hb0 = hill_y(0, pxb, P, seed), hb1 = hill_y(1, pxb, P, seed), hb2 = hill_y(2, pxb, P, seed);
    float cl = 0.f;                                   // the clouds: one noise per thread
    if (K.cloud > 0.01f && pya < 0.73f * Hf && pyb > 0.05f * Hf) {
        const float cx = (pxb + P[P_V0] + 0.15f * P[P_CAMX]) / Hf * 1.3f + P[P_SKYPH];
        const float cy = pyb / Hf * 5.5f;
        const float nn = 0.6f * vnoise(cx, cy, seed + 501u) + 0.3f * vnoise(2.3f * cx + 1.7f, 2.1f * cy, seed + 502u)
                         + 0.1f * vnoise(5.1f * cx, 4.3f * cy, seed + 503u);
        cl = smooth(0.52f, 0.8f, nn) * K.cloud;
    }
    float3 c0 = sky_at(pxa, pya, K, P, seed, cl), c1 = sky_at(pxb, pya, K, P, seed, cl);
    float3 c2 = sky_at(pxa, pyb, K, P, seed, cl), c3 = sky_at(pxb, pyb, K, P, seed, cl);
    int cur = 3;                                      // layers composited so far: 3 = none (sky only)
    const float fx0 = (float)bx0, fy0 = (float)by0, fx1 = fx0 + 32.f, fy1 = fy0 + 32.f;
    // the plants touching this tile, in draw order (back layer first)
    const int npl = min(max((int)P[P_NPL], 0), NP);
    int nhit = 0;
    {
        bool ph = false;
        if (tid < npl) {
            const float* b = pbox + tid * BOXF;
            ph = (b[0] <= b[2]) && (b[1] <= b[3]) && (b[0] <= fx1) && (b[2] >= fx0) && (b[1] <= fy1) && (b[3] >= fy0);
        }
        const unsigned int m = __ballot_sync(0xffffffffu, ph);
        if (lane == 0) s_wc[warp] = __popc(m);
        __syncthreads();
        int off = 0;
        #pragma unroll
        for (int w = 0; w < 8; ++w) {
            const int cw = s_wc[w];
            off += (w < warp) ? cw : 0;
            nhit += cw;
        }
        if (ph) s_pl[off + __popc(m & ((1u << lane) - 1u))] = tid;
        __syncthreads();
    }
    for (int qp = 0; qp < nhit; ++qp) {
        const float* b = pbox + s_pl[qp] * BOXF;
        const int roff = (int)b[4], rn = min(max((int)b[5], 0), NPP);
        for (int base = 0; base < rn; base += 256) {  // one batch per plant (NPP <= 256)
            const int g = roff + base + tid;
            bool hit = false;
            if (base + tid < rn && g >= 0 && g < NMAX) {
                const float* r = rec + (size_t)g * RF;
                const float mnx = r[16], mny = r[17], mxx = r[18], mxy = r[19];
                hit = (mnx <= mxx) && (mny <= mxy) && (mnx <= fx1) && (mxx >= fx0) && (mny <= fy1) && (mxy >= fy0);
            }
            const unsigned int m = __ballot_sync(0xffffffffu, hit);
            if (lane == 0) s_wc[warp] = __popc(m);
            __syncthreads();
            int off = 0, tot = 0;
            #pragma unroll
            for (int w = 0; w < 8; ++w) {
                const int cw = s_wc[w];
                off += (w < warp) ? cw : 0;
                tot += cw;
            }
            if (hit) {                                // ordered compaction: draw order is kept
                const int slot = off + __popc(m & ((1u << lane) - 1u));
                const float* r = rec + (size_t)g * RF;
                #pragma unroll
                for (int f = 0; f < RF; ++f) s_rec[slot * RF + f] = r[f];
            }
            __syncthreads();
            // which of them touch this warp's 32 x 4 strip: the warp walks only those, in order
            const float wy0 = fy0 + 4.f * warp, wy1 = wy0 + 4.f;
            unsigned int wm[(NPP + 31) / 32];
            #pragma unroll
            for (int w = 0; w < (NPP + 31) / 32; ++w) {
                const int k = w * 32 + lane;
                bool h = false;
                if (k < tot) h = s_rec[k * RF + 17] <= wy1 && s_rec[k * RF + 19] >= wy0;
                wm[w] = __ballot_sync(0xffffffffu, h);
            }
            #pragma unroll
            for (int w = 0; w < (NPP + 31) / 32; ++w) {
                unsigned int mm = wm[w];
                while (mm) {
                    const int k = w * 32 + __ffs(mm) - 1;
                    mm &= mm - 1u;
                    const float* R = s_rec + k * RF;
                    const int kl = (int)(R[10] + 0.5f);
                    const int L = min(max(kl >> 2, 0), 2), kind = kl & 3;
                    while (cur > L) {                 // the hills (mist, grass) of the layers in between
                        --cur;
                        comp_hill4(cur, pxa, pya, pick3(cur, ha0, ha1, ha2), pick3(cur, hb0, hb1, hb2),
                                   c0, c1, c2, c3, K, P, seed);
                    }
                    if (x0 + 2.f < R[16] || (float)x0 > R[18] || y0 + 2.f < R[17] || (float)y0 > R[19]) continue;
                    const float hla = pick3(L, ha0, ha1, ha2), hlb = pick3(L, hb0, hb1, hb2);
                    draw_prim(R, kind, L, pxa, pya, hla, c0, K);
                    draw_prim(R, kind, L, pxb, pya, hlb, c1, K);
                    draw_prim(R, kind, L, pxa, pyb, hla, c2, K);
                    draw_prim(R, kind, L, pxb, pyb, hlb, c3, K);
                }
            }
            __syncthreads();
        }
    }
    while (cur > 0) {
        --cur;
        comp_hill4(cur, pxa, pya, pick3(cur, ha0, ha1, ha2), pick3(cur, hb0, hb1, hb2), c0, c1, c2, c3, K, P, seed);
    }
    meadow4(pxa, pya, c0, c1, c2, c3, K, P, seed);
    float ff[4];
    fireflies4(pxa, pya, P, seed, ff);
    const float gain = 255.f * fmaxf(P[P_GAIN], 0.f);
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int x = x0 + (k & 1), y = y0 + (k >> 1);
        if (x >= W || y >= H) continue;
        float3 c = k == 0 ? c0 : (k == 1 ? c1 : (k == 2 ? c2 : c3));
        c = madd3(c, K.fly, 0.9f * ff[k]);
        const float vy = (y + 0.5f) / Hf - 0.5f;
        const float vig = 1.f - 0.35f * vy * vy;     // top and bottom only: the picture runs on across the TVs
        out[y * W + x] = pack(make_float4(gain * shoulder(c.x * vig), gain * shoulder(c.y * vig),
                                          gain * shoulder(c.z * vig), 255.f));
    }
}
"""


_logged: set = set()


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def _clamp(v, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return lo
    if v != v:
        return lo
    return lo if v < lo else hi if v > hi else v


def _consonant(mask: int) -> bool:
    """A single pitch class, or a subset of a major or minor triad."""
    if mask <= 0:
        return False
    if mask & (mask - 1) == 0:
        return True
    return any(mask & ~tr == 0 for tr in _TRIADS)


class NoteGarden(Source):
    """A garden grown by the piece: phrases plant, notes grow, leaps branch, cadences flower, the pedal blows."""
    name = "note_garden"
    label = "Note garden"
    description = ("The piece grows a garden that stays: each phrase plants a stem where its bass sits, every note "
                   "grows it, leaps branch it, cadences open flowers in the key's colour and the pedal is a breeze.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="dusk", render_scale=0.7, period=1.0, drift=0.01),
            "style": num(0.0, 2.0, 0.9, "Plant style (fern · willow · blossom)", basic=True),
            "density": num(0.0, 1.0, 0.55, "Density", basic=True),
            "wind": num(0.0, 1.5, 0.5, "Wind"),
            "depth": num(0.0, 1.0, 0.7, "Depth (layers and mist)"),
            "glow": num(0.0, 1.0, 0.6, "Glow (ink · light)"),
            "sky": num(0.0, 1.0, 0.45, "Sky (night · day)"),
        }

    # -- setup -------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        mod = raw_module(_SRC, _OPTS)
        self.k_prep = mod.get_function("ng_prep")
        self.k_draw = mod.get_function("ng_draw")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.arena_g = cp.zeros(NPAR + NP * PLF, cp.float32)
        self.P_g = self.arena_g[:NPAR]
        self.pl_g = self.arena_g[NPAR:]
        self.prims_g = cp.zeros((NP, NPP, PF), cp.float32)
        self.rec_g = cp.zeros((NP * NPP, RF), cp.float32)
        self.pbox_g = cp.zeros(NP * BOXF, cp.float32)
        self.quiet_g = cp.zeros((128, 4), cp.float32)
        self.arena = np.zeros(NPAR + NP * PLF, np.float32)
        self.plants = self.arena[NPAR:].reshape(NP, PLF)
        self.prims = np.zeros((NP, NPP, PF), np.float32)
        # the model
        self.active = np.zeros(NP, bool)
        self.withering = np.zeros(NP, bool)
        self.n = np.zeros(NP, np.int64)
        self.gen = np.zeros(NP, np.int64)
        self.birth = np.zeros(NP)
        self.fade = np.zeros(NP)
        self.layer = np.zeros(NP, np.int64)
        self.wx = np.zeros(NP)
        self.tip = np.full(NP, -1, np.int64)          # the growing axis's newest stem segment
        self.nax = np.zeros(NP, np.int64)            # growing axes per plant
        self.ax_tip = np.zeros((NP, MAX_AXES), np.int64)
        self.ax_ang = np.zeros((NP, MAX_AXES))       # the axis's absolute angle (rest, no wind)
        self.ax_ord = np.zeros((NP, MAX_AXES), np.int64)
        self.ax_pitch = np.zeros((NP, MAX_AXES))     # the last pitch it grew
        self.ax_k = np.zeros((NP, MAX_AXES), np.int64)
        self.cur_ax = np.zeros(NP, np.int64)
        self.held_t = np.full(NP, -1e9)              # the last held-chord flower
        self.stems = np.zeros(NP, np.int64)
        self.side = np.ones(NP)
        self.flowers = np.zeros(NP, np.int64)
        self.flowered: list[set] = [set() for _ in range(NP)]
        self.hmax = np.full(NP, 0.55)               # the height the plant aims for (plant units)
        self.sy0 = np.zeros((NP, NPP))               # each stem segment's rest height at its base and tip ...
        self.sy1 = np.zeros((NP, NPP))
        self.sang = np.zeros((NP, NPP))              # ... its rest angle and length (host only: the height model)
        self.ssz = np.zeros((NP, NPP))
        self.bass_hist: list = []                   # the recent phrases' basses: the garden's register spread
        self._x_hint = None
        self._grid = np.linspace(0.04, 0.96, 47)
        self.dirty: set = set()
        self.cur = -1
        self.last_t = -1e9
        self.sky, self.sky_t = -1.0, -1e9
        self.group: list = []
        self.pending: list = []
        self.armed = False
        self.last_mask = 0
        self.last_loud = 0.0
        self.last_pitches: tuple = ()
        self.note_prim: dict = {}                      # pitch -> (slot, gen, prim) of a segment still growing
        self.rng = np.random.default_rng(self.seed)
        self.key_hue = float(self.rng.random())
        self.key_minor = False
        self.keyr = T.KeyResolver()
        self.pc_e = np.zeros(12, np.float32)
        self._pc_tmp = np.zeros(12, np.float32)
        self._seen_on = None
        self._seen_rel = None
        self._wts = None
        self._reg = None
        self._style = _clamp(params["style"], 0.0, 2.0)
        self._dens = _clamp(params["density"])
        # clocks and eased signals
        self.clock = 0.0
        self.last_onset = -1e9
        self.kick = 0.0
        self.e_slow = None
        self.day = None
        self.wind_e = 0.0
        self.fly_amt = 0.6
        self.damper = 0.0
        self.ped_armed = True
        self.g_t0 = [-1e9] * NGUST                  # the pedal's gusts: start time, strength
        self.g_amp = [0.0] * NGUST
        self.flyw = 0.0
        self.v0 = None
        self.vw = 1.0
        self.wph = self.fly = self.skyph = self.camph = self.drift = 0.0
        self._seed_garden()
        self.prims_g.set(self.prims)
        self.dirty.clear()

    def _seed_garden(self) -> None:
        """A garden grown from the seed (made-up phrases, long ago), spread across the width, so the scene is a
        garden from the first frame; real phrases join it and, being newer, outlast it."""
        rng = self.rng
        t = -40.0 * SEED_PLANTS - 80.0
        order = rng.permutation(SEED_PLANTS)
        vels = rng.permutation([0.72, 0.46, 0.30, 0.66, 0.50, 0.33, 0.78, 0.44])   # all three layers
        for k in range(SEED_PLANTS):
            bass = int(rng.integers(28, 60))
            top = bass + int(rng.integers(14, 27))
            self.key_hue = float(rng.random())
            vel0 = float(vels[k % len(vels)]) + 0.04 * (float(rng.random()) - 0.5)
            self.cur = -1
            self._x_hint = (float(order[k]) + 0.5) / SEED_PLANTS
            for j in range(int(rng.integers(16, 27))):
                vel = vel0 if j == 0 else min(1.0, vel0 * (0.8 + 0.4 * float(rng.random())))
                notes = [(t, top, vel, 1.0)]
                if j == 0:
                    notes.append((t, bass, vel, 1.0))
                elif rng.random() < 0.4:
                    notes.append((t, top - int(rng.choice([3, 4, 5, 7, 8, 9])), 0.8 * vel, 1.0))
                self._event(t, notes)
                self._release(top, t + 0.25 + 0.7 * float(rng.random()))
                step = int(rng.choice([-2, -1, 1, 2, 2, 3, -3, 5, -5, 7, -7, 9, -9]))
                top = int(min(max(top + step, bass + 7), 100))
                t += 0.25 + 0.15 * float(rng.random())
            s = self.cur
            if s >= 0 and self.nax[s] > 0:            # a flower at the tip, and maybe on one more arm
                arms = [int(self.cur_ax[s])] + [a for a in range(int(self.nax[s])) if a != int(self.cur_ax[s])][:1]
                for a in arms[:1 + int(rng.random() < 0.6)]:
                    self._flower(s, t, int(self.ax_tip[s, a]), 0.45 + 0.4 * float(rng.random()), (top,), 1.0)
            t += 40.0
        self._x_hint = None
        self.cur = -1
        self.last_t = -1e9
        self.sky, self.sky_t = -1.0, -1e9
        self.pending = []
        self.armed = False
        self.note_prim = {}
        self.key_hue = float(rng.random())

    # -- the plants ------------------------------------------------------------------------------------------------
    def _kill(self, s: int) -> None:
        self.active[s] = False
        self.withering[s] = False
        self.n[s] = 0
        self.gen[s] += 1
        self.tip[s] = -1
        self.flowered[s] = set()
        self.plants[s, :] = 0.0
        if self.cur == s:
            self.cur = -1

    def _free_slot(self) -> int:
        free = np.flatnonzero(~self.active)
        if free.size:
            return int(free[0])
        w = np.flatnonzero(self.withering)
        if w.size:
            s = int(w[np.argmin(self.fade[w])])
        else:
            cand = [i for i in range(NP) if i != self.cur]
            s = min(cand, key=lambda i: self.birth[i])
        self._kill(s)
        return s

    def _budget(self, limit_one: bool) -> None:
        """Wither the oldest living plants beyond the garden's budget, in plants and in primitives (they sink back
        into their hill)."""
        budget = int(round(10 + (BUDGET_MAX - 10) * self._dens))
        pbud = PRIM_BUDGET0 + PRIM_BUDGET1 * self._dens
        for _ in range(NP):
            alive = np.flatnonzero(self.active & ~self.withering)
            if alive.size <= budget and int(self.n[alive].sum()) <= pbud:
                return
            cand = [int(i) for i in alive if i != self.cur]
            if not cand:
                return
            s = min(cand, key=lambda i: self.birth[i])
            self.withering[s] = True
            if limit_one:
                return

    def _plant(self, t: float, bass: int, vel: float) -> int:
        self._budget(limit_one=False)
        s = self._free_slot()
        rng = self.rng
        if self._x_hint is not None:
            wx0 = float(self._x_hint)
        else:                                         # low basses left, high right, across the piece's own range
            hist = self.bass_hist
            lo, hi = (min(hist) - 3.0, max(hist) + 3.0) if hist else (bass - 8.0, bass + 8.0)
            if hi - lo < 16.0:
                c = 0.5 * (hi + lo)
                lo, hi = c - 8.0, c + 8.0
            mm = min(max((bass - lo) / (hi - lo), 0.0), 1.0)
            hb = hist + [float(bass)]                 # its rank among the recent basses (a piece that keeps
            rk = (sum(b < bass for b in hb) + 0.5 * sum(b == bass for b in hb)) / len(hb)   # one bass spreads)
            wx0 = 0.07 + 0.86 * (0.6 * rk + 0.4 * mm)
            hist.append(float(bass))
            del hist[:-12]
        wx0 += 0.03 * (float(rng.random()) - 0.5)
        lp = 0 if vel > 0.58 else (1 if vel > 0.36 else 2)
        alive = self.active & ~self.withering
        alive[s] = False
        g = self._grid
        xa = self.wx[alive]
        da = np.min(np.abs(g[:, None] - xa[None, :]), axis=1) if xa.size else np.full(g.size, 0.5)
        best, best_sc = None, -1e9
        for L in range(3):                            # the place nearest the register with room to grow
            xl = self.wx[alive & (self.layer == L)]
            dl = np.min(np.abs(g[:, None] - xl[None, :]), axis=1) if xl.size else np.full(g.size, 0.5)
            sc = np.minimum(dl, 0.16) + 0.4 * np.minimum(da, 0.06) - 0.35 * np.abs(g - wx0) - 0.05 * abs(L - lp)
            k = int(np.argmax(sc))
            if sc[k] > best_sc:
                best_sc, best = float(sc[k]), (float(g[k]), L)
        x, L = best
        self.active[s] = True
        self.withering[s] = False
        self.n[s] = 0
        self.birth[s] = t
        self.fade[s] = 1.0
        self.layer[s] = L
        self.wx[s] = x
        self.tip[s] = -1
        self.nax[s] = 0
        self.cur_ax[s] = 0
        self.held_t[s] = -1e9
        self.stems[s] = 0
        self.side[s] = 1.0 if rng.random() < 0.5 else -1.0
        self.flowers[s] = 0
        self.flowered[s] = set()
        pl = self.plants[s]
        pl[:] = 0.0
        pl[0], pl[2], pl[3], pl[4], pl[5], pl[6] = 1.0, x, L, 0.92 + 0.25 * vel, 1.0, self._style
        self.hmax[s] = (0.45 + 0.20 * vel) * (0.8 + 0.4 * float(rng.random()))
        pl[7], pl[9], pl[10], pl[11] = t, 6.2831853 * float(rng.random()), 0.1 * float(rng.random()), float(rng.random())
        self.cur = s
        return s

    def _add(self, s: int, kind: int, parent: int, attach: float, rel: float, size: float, r0: float, r1: float,
             t_on: float, vel: float, hue: float, flex: float, pitch: float, x13: float, x14: float = 0.0,
             x15: float = 0.0, x16: float = 0.0, p2: float = -1.0, p3: float = -1.0, x19: float = 0.0) -> int:
        j = int(self.n[s])
        if j >= NPP:
            return -1
        self.prims[s, j] = (kind, parent, attach, rel, size, r0, r1, t_on, T_HELD, vel, hue, flex, pitch, x13, x14,
                            x15, x16, p2, p3, x19)
        self.n[s] = j + 1
        self.dirty.add(s)
        return j

    def _leaf(self, s: int, t: float, parent: int, attach: float, side: float, vel: float, pitch: float) -> int:
        if parent < 0 or self.n[s] >= NPP - RESERVE // 2:
            return -1
        rng = self.rng
        return self._add(s, LEAF, parent, attach, side * (0.7 + 0.4 * float(rng.random())),
                         (0.026 + 0.026 * vel) * (0.8 + 0.4 * float(rng.random())), 0.0, 0.0, t, vel, self.key_hue,
                         1.6, pitch, side, x19=float(rng.random()))

    def _auto_leaves(self, s: int, t: float, seg: int, vel: float) -> None:
        """Leaves every segment grows by itself (style and density say how many)."""
        st, d, rng = self._style, self._dens, self.rng
        wf, ww, wb = max(0.0, 1.0 - st), max(0.0, 1.0 - abs(st - 1.0)), max(0.0, st - 1.0)
        want = wf * (1.2 + 1.6 * d) + ww * (0.9 + 1.6 * d) + wb * (1.0 + 1.8 * d)
        k = int(want) + (1 if rng.random() < want - int(want) else 0)
        if wf > 0.5 and k >= 2:                       # a fern's leaflets come in opposite pairs
            at = 0.45 + 0.4 * float(rng.random())
            self._leaf(s, t + 0.05, seg, at, 1.0, vel, -1.0)
            self._leaf(s, t + 0.05, seg, at, -1.0, vel, -1.0)
            k -= 2
        for i in range(min(k, 3)):
            sd = float(self.side[s])
            self.side[s] = -sd
            self._leaf(s, t + 0.06 * (i + 1), seg, 0.35 + 0.6 * float(rng.random()), sd, vel, -1.0)

    def _grow(self, s: int, t: float, pitch: int, vel: float) -> int:
        """The melody note grows the plant one segment: on the axis whose register it continues, or, after a leap
        away from every axis, on a new branch forked from the axis it leapt from.  A melody that alternates
        between registers so grows two arms.  The plant knows the height it aims for: below it the stems are pulled
        upright and grow long; near it they grow shorter and arch over, so a long phrase fills the plant out into
        a crown instead of a pole."""
        rng = self.rng
        nax = int(self.nax[s])
        seg = SEG0 + SEG1 * vel
        hm = max(float(self.hmax[s]), 0.1)
        if nax == 0:
            a, k, order = 0, 0, 0
            rel = 0.2 * (float(rng.random()) - 0.5)
            parent, attach = -1, 0.0
            r0 = 0.0064 + 0.0068 * vel
            abs_ang = rel
            size = seg
            y0 = 0.0
            self.nax[s] = 1
        else:
            dist = np.abs(self.ax_pitch[s, :nax] - pitch)
            a = int(np.argmin(dist))
            ca = int(self.cur_ax[s])
            fork = (dist[a] >= LEAP and nax < MAX_AXES and self.ax_ord[s, ca] < MAX_ORDER
                    and self.n[s] < NPP - RESERVE - 4)
            if fork:                                  # a branch from the axis the melody leapt from
                dp = pitch - float(self.ax_pitch[s, ca])
                tip = int(self.ax_tip[s, ca])
                par_abs = float(self.ax_ang[s, ca])
                sd = float(self.side[s])
                self.side[s] = -sd
                mag = 0.40 + 0.028 * min(abs(dp), 19.0)
                rel = sd * mag * (0.78 if dp > 0 else 1.12)
                parent, attach = tip, 0.72
                order = int(self.ax_ord[s, ca]) + 1
                k = 0
                r0 = float(self.prims[s, tip, K_R1]) * 0.88
                y0 = float(self.sy0[s, tip] + 0.72 * (self.sy1[s, tip] - self.sy0[s, tip]))
                size = seg * (0.86 ** order) * (0.9 + 0.04 * min(abs(dp), 12.0))
                a = nax
                self.nax[s] = nax + 1
            else:                                     # the axis continues, bending with the melody
                dp = pitch - float(self.ax_pitch[s, a])
                tip = int(self.ax_tip[s, a])
                par_abs = float(self.ax_ang[s, a])
                rel = 0.05 * max(-7.0, min(7.0, dp))
                parent, attach = tip, 1.0
                order = int(self.ax_ord[s, a])
                k = int(self.ax_k[s, a])
                r0 = float(self.prims[s, tip, K_R1])
                y0 = float(self.sy1[s, tip])
                size = seg * (0.97 ** k) * (0.86 ** order)
            h = y0 / hm                               # how far up the plant's aim this segment starts
            size *= min(1.0, max(0.32, 1.12 - 0.85 * h * h))
            rel -= 0.16 * min(1.0, max(0.0, (1.0 - h) / 0.3)) * (par_abs + rel)   # pulled back upright ...
            if h > 0.72:                              # ... until near the top, where it arches over
                tot = par_abs + rel
                sgn = math.copysign(1.0, tot) if abs(tot) > 0.04 else float(self.side[s])
                rel += sgn * 0.13 * min((h - 0.72) / 0.28, 1.4)
            lim = 1.15 if h < 0.72 else 2.0
            if abs(par_abs + rel) > lim:
                rel = math.copysign(lim, par_abs + rel) - par_abs
            abs_ang = par_abs + rel
        r1 = min(max(r0 * 0.94 + 0.0012 * (vel - 0.45), 0.0018), 0.018)
        j = self._add(s, STEM, parent, attach, rel, size, r0, r1, t, vel, self.key_hue,
                      0.8 + 0.25 * order, float(pitch), float(order), x19=float(rng.random()))
        if j < 0:
            return -1
        self.sy0[s, j] = y0
        self.sang[s, j] = abs_ang
        self.ssz[s, j] = size
        self.sy1[s, j] = y0 + size * 0.77 * math.cos(abs_ang)    # until the key is released
        self.ax_tip[s, a] = j
        self.ax_ang[s, a] = abs_ang
        self.ax_ord[s, a] = order
        self.ax_pitch[s, a] = pitch
        self.ax_k[s, a] = k + 1
        self.cur_ax[s] = a
        self.tip[s] = j
        self.stems[s] += 1
        old = self.note_prim.get(pitch)
        if old is not None:
            self._end_note(old, t)
        self.note_prim[pitch] = (s, int(self.gen[s]), j)
        self._auto_leaves(s, t, j, vel)
        return j

    def _flower(self, s: int, t: float, seg: int, loud: float, pitches: tuple, open_to: float) -> int:
        if s < 0 or seg < 0 or seg in self.flowered[s] or self.flowers[s] >= FLOWERS_MAX:
            return -1
        ps = [float(p) for p in pitches[:3]] + [-1.0] * max(0, 3 - len(pitches))
        pcs = len({int(p) % 12 for p in pitches}) if pitches else 1
        j = self._add(s, FLOWER, seg, 1.0, 0.25 * (float(self.rng.random()) - 0.5), 0.017 + 0.030 * _clamp(loud),
                      0.0, 0.0, t, _clamp(loud), self.key_hue, 1.2, ps[0], float(min(max(pcs + 2, 4), 8)),
                      x14=open_to, x15=0.0, x16=t, p2=ps[1], p3=ps[2], x19=1.0 if self.key_minor else 0.0)
        if j >= 0:
            self.flowered[s].add(seg)
            self.flowers[s] += 1
        return j

    def _open_buds(self, s: int, t: float) -> None:
        n = int(self.n[s])
        q = self.prims[s, :n]
        buds = np.flatnonzero((q[:, K_KIND] == FLOWER) & (q[:, K_OPEN] < 0.5))
        for j in buds:
            q[j, K_OPREV] = q[j, K_OPEN]
            q[j, K_OPEN] = 1.0
            q[j, K_TOPEN] = t
        if buds.size:
            self.dirty.add(s)

    # -- notes -------------------------------------------------------------------------------------------------
    def _end_note(self, ref: tuple, t: float) -> None:
        s, g, j = ref
        if 0 <= s < NP and self.active[s] and self.gen[s] == g and j < self.n[s]:
            q = self.prims[s, j]
            if q[K_TOFF] >= T_HELD * 0.5:
                q[K_TOFF] = max(t, float(q[K_TON]))
                self.dirty.add(s)
                if q[K_KIND] == STEM:
                    dur = min(max(float(q[K_TOFF] - q[K_TON]), 0.0), 4.0)
                    lf = 0.42 + 0.58 * (1.0 - math.exp(-dur / 0.55))
                    self.sy1[s, j] = self.sy0[s, j] + self.ssz[s, j] * lf * math.cos(self.sang[s, j])

    def _release(self, pitch: int, t: float) -> None:
        if self.group and any(g[1] == pitch for g in self.group):
            self._close_group()
        ref = self.note_prim.pop(pitch, None)
        if ref is not None:
            self._end_note(ref, t)

    def _onset(self, t: float, pitch: int, vel: float, w: float) -> None:
        if self.group and t - self.group[0][0] > GROUP_S:
            self._close_group()
        self.group.append((t, pitch, vel, w))
        self.last_onset = max(self.last_onset, t)
        self.pc_e[pitch % 12] += ONSET_W * vel

    def _close_group(self) -> None:
        g, self.group = self.group, []
        if g:
            self._event(g[0][0], g)

    def _event(self, t: float, notes: list) -> None:
        """One event (a note or a chord): plant, grow, branch and leaf."""
        loudest: dict = {}
        for _, p, v, w in notes:
            if p not in loudest or v > loudest[p][0]:
                loudest[p] = (v, w)
        ps = sorted(loudest)
        if not ps:
            return
        top, low = ps[-1], ps[0]
        vmax = max(v for v, _ in loudest.values())
        cur = self.cur
        rest = t - self.last_t
        small = cur >= 0 and self.stems[cur] < MIN_STEMS
        new = (cur < 0 or not self.active[cur] or self.withering[cur]
               or (rest > PHRASE_GAP_S and (not small or rest > LONG_REST_S))
               or self.n[cur] >= NPP - RESERVE or self.stems[cur] >= MAX_STEMS
               or t - self.birth[cur] > (PHRASE_MAX_SLOW_S if small else PHRASE_MAX_S))
        decayed = self.sky - SKY_FALL * max(t - self.sky_t, 0.0)
        top_ok = top >= decayed - SKY_TOL
        self.sky, self.sky_t = max(float(top), decayed), t
        if new:
            old = self.cur                            # a phrase that never paused still ends in a flower
            if (0 <= old < NP and self.active[old] and not self.withering[old] and self.flowers[old] == 0
                    and self.stems[old] >= 6 and self.tip[old] >= 0 and self._x_hint is None):
                self._flower(old, t, int(self.tip[old]), min(1.0, 0.25 + self.last_loud), self.last_pitches, 1.0)
            cur = self._plant(t, low, vmax)
            top_ok = True
        self.last_t = t
        self.armed = True
        self.last_mask = 0
        for p in ps:
            self.last_mask |= 1 << (p % 12)
        self.last_loud = sum(v for v, _ in loudest.values()) / len(loudest)
        self.last_pitches = tuple(ps)
        seg = -1
        if top_ok:
            vt, wt = loudest[top]
            seg = self._grow(cur, t, top, vt * wt)
            rest = [p for p in ps if p != top]
        else:
            rest = list(ps)
        at_seg = seg if seg >= 0 else int(self.tip[cur])
        rest.sort(key=lambda p: -loudest[p][0])
        for i, p in enumerate(rest[:LEAVES_PER_EVENT]):
            v, w = loudest[p]
            sd = float(self.side[cur])
            self.side[cur] = -sd
            at = 0.3 + 0.6 * (p - low) / max(top - low, 1) if top_ok else 0.25 + 0.5 * float(self.rng.random())
            self._leaf(cur, t + 0.05 * i, at_seg, at, sd, v * w, float(p))
        if len({p % 12 for p in ps}) >= 2 and at_seg >= 0:
            self.pending.append((t, tuple(ps), cur, int(self.gen[cur]), at_seg, vmax))
            if len(self.pending) > 8:
                self.pending.pop(0)

    # -- per frame ---------------------------------------------------------------------------------------------
    def _listen(self, now: float, dt: float):
        """Fold this frame's strikes and releases into the garden; returns (frame or None, notes on the GPU)."""
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        notes_g = getattr(snd, "notes_gpu", None) if snd is not None else None
        self.pc_e.fill(0.0)
        if f is None:
            return None, notes_g
        reg = getattr(snd, "register", None)
        if reg != self._reg:
            self._reg = reg
            self._wts = None
            if reg and _note_weights is not None:
                try:
                    self._wts = np.asarray(_note_weights(reg, np.arange(128)), np.float64)
                except Exception:                     # degrade: everything at full
                    self._wts = None
        ons = getattr(f, "onsets", None)
        if ons is not None and ons is not self._seen_on:
            self._seen_on = ons
            evs = []
            for o in list(ons)[:256]:
                try:
                    p, v, a = int(o[0]), float(o[1]), float(o[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if not 0 <= p < 128 or v != v:
                    continue
                a = a if a == a and 0.0 <= a < 30.0 else 0.0
                evs.append((now - a, p, min(max(v, 0.0), 1.0)))
            evs.sort()
            for t, p, v in evs:
                w = float(self._wts[p]) if self._wts is not None else 1.0
                self._onset(t, p, v, w)
        rel = getattr(f, "releases", None)
        if rel is not None and rel is not self._seen_rel:
            self._seen_rel = rel
            for o in list(rel)[:256]:
                try:
                    p, a = int(o[0]), float(o[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if 0 <= p < 128:
                    self._release(p, now - (a if a == a and 0.0 <= a < 30.0 else 0.0))
        notes = getattr(f, "notes", None)
        if isinstance(notes, np.ndarray) and notes.shape == (128, 4):
            # heal: a key that went up without a release we saw
            grouped = {g[1] for g in self.group}
            for p in [p for p in self.note_prim if p not in grouped]:
                a = float(notes[p, 3])
                if a == a and 0.0 <= a < 1e6:
                    self._end_note(self.note_prim.pop(p), now - a)
            try:
                lv = T.pc_energy(notes, self._pc_tmp)
                self.pc_e += np.nan_to_num(lv, nan=0.0, posinf=0.0)
            except Exception:
                pass
        try:
            ped = getattr(f, "pedals", None)
            self.damper = _clamp(ped[0]) if ped is not None and len(ped) > 0 else 0.0
        except (TypeError, IndexError):
            self.damper = 0.0
        try:
            self.keyr.update(self.pc_e, dt, getattr(f, "key", None), getattr(f, "mode", None))
            if self.keyr.known:
                self.key_hue = ((self.keyr.tonic * 7) % 12) / 12.0
                self.key_minor = bool(self.keyr.minor)
        except Exception as exc:                      # the key is decoration: never fail a frame for it
            _log_once("key", "note_garden: key tracking failed (%s); flowers keep the last colour", exc)
        return f, notes_g

    def _tick(self, now: float, dt: float, f) -> None:
        if self.group and now - self.group[0][0] > GROUP_S:
            self._close_group()
        notes = getattr(f, "notes", None) if f is not None else None
        has = isinstance(notes, np.ndarray) and notes.shape == (128, 4)
        # chords the hands hold open flowers (3+ keys held HELD_S, or 2 keys 1.3 s; the pedal alone does not count)
        keep = []
        for item in self.pending:
            t, ps, s, g, seg, loud = item
            if not self.active[s] or self.gen[s] != g or not has:
                continue
            age = now - t
            held = sum(1 for p in ps if notes[p, 3] < 0.0)
            if held < 2 or age > 1.8:
                continue
            if (held >= 3 and age >= HELD_S) or age >= 1.3:
                if now - self.held_t[s] >= HELD_GAP_S:
                    if self._flower(s, now, seg, min(1.0, loud * (0.75 + 0.08 * len(ps))), ps, 1.0) >= 0:
                        self.held_t[s] = now
                continue
            keep.append(item)
        self.pending = keep
        # a cadence: a pause after the last event
        if self.armed and now - self.last_t >= CADENCE_S:
            self.armed = False
            s = self.cur
            if 0 <= s < NP and self.active[s] and self.tip[s] >= 0:
                mask = self.last_mask
                pitches = list(self.last_pitches)
                if has:
                    for p in np.flatnonzero(notes[:, 0] > 0.12)[:12]:
                        mask |= 1 << (int(p) % 12)
                if _consonant(mask):
                    self._flower(s, now, int(self.tip[s]), min(1.0, self.last_loud * 1.15), tuple(pitches), 1.0)
                    self._open_buds(s, now)
                else:
                    self._flower(s, now, int(self.tip[s]), 0.6 * self.last_loud, tuple(pitches), 0.12)
        # withering
        for s in np.flatnonzero(self.withering):
            self.fade[s] -= dt / WITHER_S
            if self.fade[s] <= 0.0:
                self._kill(int(s))
        self._budget(limit_one=True)

    def _gust_env(self, k: int, now: float) -> float:
        age = now - self.g_t0[k]
        if not 0.0 <= age < 9.0:
            return 0.0
        return self.g_amp[k] * min(age / 0.35, 1.0) ** 2 * math.exp(-age / 1.8)

    # -- render ------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        cp = self.ctx.cp
        p = self.step_params(dt)
        dt = float(dt) if dt == dt and 0.0 < dt < math.inf else 0.0
        dt = min(dt, 0.5)
        e = _clamp(p["energy"])
        pulse = _clamp(p["pulse"])
        spd = _clamp(p["speed"], 0.0, 4.0)
        self._style = _clamp(p["style"], 0.0, 2.0)
        self._dens = _clamp(p["density"])
        self.clock += dt
        now = self.clock
        try:
            f, notes_g = self._listen(now, dt)
            self._tick(now, dt, f)
        except Exception as exc:                      # degrade, never refuse: the garden keeps swaying
            _log_once("model", "note_garden: listening failed (%s: %s); the garden stops growing this frame",
                      type(exc).__name__, exc)
            f, notes_g = None, None
        if notes_g is None or getattr(notes_g, "shape", None) != (128, 4):
            notes_g = self.quiet_g

        # eased signals
        def ease(cur, tgt, tau):
            return tgt if cur is None else cur + (tgt - cur) * (1.0 - math.exp(-dt / tau) if dt > 0 else 0.0)
        tau_k = 0.08 if pulse > self.kick else 0.35
        self.kick = ease(self.kick, pulse, tau_k)
        self.e_slow = ease(self.e_slow, e, 18.0)
        day_t = _clamp(_clamp(p["sky"]) + 0.6 * (self.e_slow - 0.45))
        self.day = ease(self.day, day_t, 3.0)
        wind_t = _clamp(p["wind"], 0.0, 1.5) * (0.3 + 0.7 * e) + 0.9 * self.damper + 0.04
        self.wind_e = ease(self.wind_e, wind_t, 0.9 if wind_t > self.wind_e else 1.6)
        # a pedal press sends a gust across the garden (a gust slot is reused only once its gust has blown out)
        if self.ped_armed and self.damper > 0.6:
            self.ped_armed = False
            env = [self._gust_env(k, now) for k in range(NGUST)]
            k = int(np.argmin(env))
            if env[k] < 0.15 or now - self.g_t0[k] > 2.5:
                self.g_t0[k] = now
                self.g_amp[k] = 0.75 + 0.5 * e
        elif self.damper < 0.3:
            self.ped_armed = True
        quiet = min(max((now - self.last_onset - 1.5) / 4.5, 0.0), 1.0)
        quiet = quiet * quiet * (3.0 - 2.0 * quiet)
        fly_t = (0.25 + 0.75 * quiet) * (0.3 + 0.7 * (1.0 - self.day)) * (0.6 + 0.4 * self._dens)
        self.fly_amt = ease(self.fly_amt, fly_t, 2.0)
        # the room's picture: this screen's share of one garden across the ensemble's screens
        k, nseg = 0, 1
        seg = getattr(getattr(self, "sound", None), "segment", None)
        try:
            if seg is not None and int(seg[1]) >= 2 and 0 <= int(seg[0]) < int(seg[1]):
                k, nseg = int(seg[0]), int(seg[1])
        except (TypeError, ValueError, IndexError):
            k, nseg = 0, 1
        first = self.v0 is None
        self.v0 = ease(None if first else self.v0, k / nseg, 1.2)
        self.vw = ease(None if first else self.vw, 1.0 / nseg, 1.2)
        # phases (rates only: nothing jumps)
        self.wph = (self.wph + dt * spd * (0.8 + 0.9 * self.wind_e + 0.5 * e)) % (20.0 * math.pi)
        self.fly = (self.fly + dt * spd * (0.6 + 0.4 * e)) % 4096.0
        self.skyph = (self.skyph + dt * spd * (0.004 + 0.006 * e)) % 4096.0
        self.camph = (self.camph + dt * spd * (0.010 + 0.016 * e)) % 4096.0
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 64.0
        self.flyw = (self.flyw + dt * spd * 0.02 * self.wind_e) % 200.0     # the breeze carries the fireflies

        # the plant table, back layer first
        pl = self.plants
        idx = [int(i) for i in np.flatnonzero(self.active & (self.n > 0))]
        idx.sort(key=lambda i: (-int(self.layer[i]), self.birth[i]))
        pl[:, 0] = 0.0
        off = 0
        for r, i in enumerate(idx):
            pl[i, 0] = 1.0
            pl[i, 1] = self.n[i]
            pl[i, 5] = max(self.fade[i], 0.0) if self.withering[i] else 1.0
            pl[i, 8] = off
            pl[i, 12] = r                             # its draw rank: kernel 2 culls whole plants per tile
            off += int(self.n[i])

        A = self.arena
        try:
            A[0:12] = palette_coeffs(p["palette"])
        except (KeyError, TypeError):
            A[0:12] = palette_coeffs("dusk")
        w, h = self.w, self.h
        vw = max(self.vw, 1e-3)
        A[_PIDX["P_W"]], A[_PIDX["P_H"]] = w, h
        A[_PIDX["P_NOW"]] = now
        A[_PIDX["P_E"]] = e
        A[_PIDX["P_KICK"]] = self.kick
        A[_PIDX["P_GAIN"]] = _clamp(p["intensity"], 0.0, 2.0)
        A[_PIDX["P_WIND"]] = self.wind_e
        A[_PIDX["P_WPH"]] = self.wph
        A[_PIDX["P_STYLE"]] = self._style
        A[_PIDX["P_DENS"]] = self._dens
        A[_PIDX["P_DEPTH"]] = _clamp(p["depth"])
        A[_PIDX["P_GLOW"]] = _clamp(p["glow"])
        A[_PIDX["P_DAY"]] = self.day
        A[_PIDX["P_DRIFT"]] = self.drift
        A[_PIDX["P_PERIOD"]] = _clamp(p["palette_period"], 0.05, 10.0)
        cph = 2.0 * math.pi * self.camph
        A[_PIDX["P_CAMX"]] = w * (0.03 * math.sin(cph) + 0.012 * math.sin(0.43 * cph + 1.3))
        A[_PIDX["P_CAMY"]] = h * 0.006 * math.sin(0.61 * cph + 0.4)
        A[_PIDX["P_RW"]] = w / vw
        A[_PIDX["P_V0"]] = self.v0 / vw * w
        A[_PIDX["P_GROW"]] = 0.85 - 0.5 * e
        A[_PIDX["P_FLY"]] = self.fly
        A[_PIDX["P_SKYPH"]] = self.skyph
        A[_PIDX["P_FLYAMT"]] = self.fly_amt
        A[_PIDX["P_N"]] = off
        A[_PIDX["P_NPL"]] = len(idx)
        A[_PIDX["P_CLOUD"]] = 0.45 + 0.35 * (1.0 - abs(self.day - 0.5) * 2.0)
        A[_PIDX["P_FLYW"]] = self.flyw
        room_w = (w / vw) / max(h, 1)                 # the room's width in screen heights
        for k in range(NGUST):                        # the gust fronts cross the room left to right in ~2.6 s
            age = now - self.g_t0[k]
            A[_PIDX["P_GA0"] + 2 * k] = self._gust_env(k, now)
            A[_PIDX["P_GX0"] + 2 * k] = -0.5 + (room_w + 1.0) / 2.6 * min(max(age, 0.0), 30.0)
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        self.arena_g.set(A)
        if len(self.dirty) > 8:
            self.prims_g.set(self.prims)
        else:
            for s in self.dirty:
                self.prims_g[s].set(self.prims[s])
        self.dirty.clear()
        sd = np.uint32(self.seed)
        self.k_prep(((NP + PB - 1) // PB, 1, 1), (PB, 1, 1),
                    (self.P_g, self.pl_g, self.prims_g, notes_g, self.rec_g, self.pbox_g, sd))
        self.k_draw(((w + 31) // 32, (h + 31) // 32, 1), (16, 16, 1), (self.buf, self.P_g, self.rec_g, self.pbox_g, sd))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.arena_g = self.P_g = self.pl_g = self.prims_g = self.rec_g = self.quiet_g = None
        self.pbox_g = None
