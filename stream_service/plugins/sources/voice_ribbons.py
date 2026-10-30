"""Voice ribbons: the playing drawn as luminous calligraphy in space, a light painting made by the piano.

What the picture shows
----------------------
A long-exposure photograph being taken in the dark while the music draws on it.  Two pens of light, the melody
(the top voice) and the bass, travel forward through space as time passes, rising and falling with the pitch they
play, and each leaves a glowing ribbon behind it, like an LED stick swept through the air in Stephen Orlando's
motion light paintings.  A third pen, the inner mass (the notes sounding between the melody and the bass), is a
soft translucent curtain of fine threads hanging between them: a chord thickens it, a single inner voice narrows
it.  The ribbons twist as they go, the strokes stay in the air for several seconds and fade like an exposure
running out, and each pen's tip glows like a lit nib.  The camera glides alongside the drawing and slowly orbits
it, so the strokes are seen from the side, then receding into depth.  Faint dust drifts in a deep, softly hazed
background.

How the music drives it
-----------------------
* Every note does something: the pen moves to its pitch (high is high), and the stroke swells: wider and brighter
  with the velocity, blooming in over ~50 ms and easing out, with a white-hot core when it is loud.  Soft playing
  draws thin, tender strokes; forte draws broad blazing ones.
* Legato is one continuous stroke curving from note to note; staccato lifts the pen between notes, so the line
  becomes a string of beads.  A rest longer than ~0.4 s lifts the pen and the next phrase starts at a new depth,
  so phrases become separate strokes layered in space.
* The ribbon turns with the melody: each new note rotates the ribbon's face by an amount set by the interval
  (a step barely turns it, an octave leap turns it through a large angle, up one way, down the other).
* The damper pedal is resonance: while it is down, a glowing thread runs through the stroke for as long as the
  note still sounds (so pedalled staccato is beads strung on light), the exposure lengthens (strokes linger
  longer), and the bloom grows.  The soft pedal narrows and cools the strokes.
* The voices are derived per frame from the sounding notes, as echo_arcs does: the melody is the highest held
  note if it is near the recent top line (a skyline falling 24 semitones/s, tolerance 9), the bass the lowest
  held note near the recent bottom line (a floor rising at the same rate); a lone note goes to the nearer line,
  and the rest are the inner mass.  With the room's register split (sound.register) a pen outside this screen's
  window dims to the window's ``outside`` level; with one picture across both TVs (sound.segment, span: room) the
  camera is shifted by one frame width per screen, so the drawing runs on from one TV into the other.  The
  default, span: screen, gives each TV the whole drawing.
* No notes: the pens keep travelling and draw faint ghost phrases on their own, the ribbons keep turning, the
  camera keeps orbiting and the dust drifts.  When the piano starts, the pens hand over to the playing at once.

Macros and knobs
----------------
energy -> pen speed (how fast time runs through the picture, and so how far the camera stands back), the bloom,
the pens' drift in depth, the ribbons' roll rate and the dust; pulse (note_on kick) -> stroke flare (brightness and
width) and a little extra pen speed, never a position; speed -> every rate; intensity -> exposure; palette /
palette_period / drift_speed -> the colour, which each stroke keeps from the moment it was drawn, so a ribbon
carries a gradient along its length (time) as in Orlando's photographs.  Regional: trail_length (seconds a stroke
lasts), twist, orbit (how far the camera swings round), pens (2 = melody and bass, 3 = with the inner-mass
curtain, morphing), stroke_width, stripes (colours across the stroke, the LED stick's spectrum), bloom, dust.

Rendering: per pen, one sample every 1/30 s goes into a GPU ring (plus the live tip every frame); one kernel turns
the visible samples into ribbon rails (centre +- width x the twisted normal, 4 colour bands x 3 strands per
voice, 16 strands for the curtain, 3 for each resonance thread) with linear colour that keeps a face-on ribbon's
surface brightness constant whatever its distance or twist; splat3d splats them additively with the dust and the
nibs, and resolves with bloom + ACES over a dithered background kernel at full size.  Cost (estimate, P4000, 4K):
~2.5 ms typical, ~3.5 ms with a 20 s pedalled trail.

Source: Original (inspired by Stephen Orlando, motion light paintings of musicians,
        https://mymodernmet.com/stephen-orlando-music-movement ; Norman McLaren, Begone Dull Care (1949),
        https://en.wikipedia.org/wiki/Begone_Dull_Care ; Dan Tepfer, Natural Machines (live piano with generative
        visuals), https://www.aeon.co/videos/as-a-pianist-strikes-a-chord-visualisations-of-his-notes-appear-in-real-time ;
        the top-voice / bass derivation follows this repo's echo_arcs.py; no code taken from any of them)
Author: smfx (original)
License: MIT (original)
license_class: permissive
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.splat3d import Splat3D, look_at, orbit
from stream_service.plugins._lib.upsample import internal_size
from stream_service.plugins.base import Source

LEVEL, VEL, AGE_ON, AGE_OFF = 0, 1, 2, 3        # sound_view columns
DAMPER, SOFT = 0, 1                              # SoundFrame.pedals

NPEN = 3                 # 0 melody, 1 bass, 2 inner mass (the curtain)
NF = 16                  # floats per sample (layout in _SRC)
K = 1536                 # samples kept per pen (ring); 30 Hz x 20 s trail x 1.7 pedal x 1.25 tail fits
SAMPLE_HZ = 30.0         # committed samples per second (the tip moves every frame)
NB, NS = 4, 3            # colour bands per voice ribbon x strands per band
NCORE = 2 * NB           # voice ribbons (melody bands, then bass bands)
NSF = 16                 # curtain strands
NST = 3                  # resonance-thread strands
NROW = NCORE + 1 + 2     # build rows: voice bands, the curtain, two threads
G_PEN = 64               # arena: per-pen live rows start here
NG = G_PEN + NPEN * NF
LCUT = 5.0               # life is 0 beyond this much accumulated decay (L units)
DECAY = 4.0              # L per effective trail length
PRE_S = 6.0              # pre-rolled idle drawing at setup (the scene never opens empty)
N_DUST = 4096
FOV = 46.0
PITCH_Y = 0.068          # world units per semitone
Y0 = 62.0                # the pitch at height 0
V0 = 0.9                 # pen speed (world units / s) at energy ~0.5
W0 = (0.085, 0.11)       # voice half widths (world) at full velocity
OMEGA = (34.0, 24.0, 12.0)   # pitch glide springs (critically damped, rad/s)
Z_BASE = (0.35, -0.35, 0.0)  # pens' depth lanes (the camera sits on +z)
HUE0 = (0.0, 0.5, 0.27)  # palette index per pen
ROLL = (1.0, -0.8)       # twist direction per voice
TW_K = 0.14              # ribbon turn per semitone of interval (rad, x twist)
FALL = 24.0              # skyline fall / floor rise (semitones / s)
SKY_TOL = 9.0
RECENT_S = 0.07          # a note struck this recently counts as held (staccato shorter than a frame)
LIFT_S = 0.4             # a lift this long is a phrase gap: the next phrase starts at a new depth
IDLE_INK = 0.3           # the ghost phrases drawn with no notes (faint, but clearly there)
IDLE_C = (76.0, 41.0)    # idle ghost melodies: centre pitch, amplitude, rate
IDLE_A = (6.0, 4.0)
IDLE_R = (0.11, 0.07)
PHR_R = (0.09, 0.06, 0.05)
I_CORE, I_FAN, I_THR = 0.75, 0.16, 0.5      # surface brightness (linear) of a face-on stroke at b = 1
LW_CORE, LW_FAN, LW_THR = 1.5, 1.2, 2.2      # strand widths (ref px)
REBASE_X, REBASE_L = 4096.0, 4096.0

_HDR = f"""
#define NPEN {NPEN}
#define NF {NF}
#define NB {NB}
#define NS {NS}
#define NCORE {NCORE}
#define NSF {NSF}
#define NST {NST}
#define NROW {NROW}
#define G_PEN {G_PEN}
"""

_SRC = HELPERS + PALETTE_CUDA + _HDR + r"""
// Sample (NF floats; hist[pen][slot][NF], pen rows of the arena G at G_PEN + pen * NF):
//  0 x | 1 y (curtain: low edge) | 2 z | 3 twist angle | 4 half width | 5 stroke brightness | 6 resonance thread
//  7 L (accumulated decay when drawn) | 8 palette field | 9 drift when drawn | 10 heat (white core)
//  11 curtain high edge | 12 curtain lean | 13 nib brightness | 14, 15 -
// Arena G (floats):
#define G_L 0
#define G_PERIOD 1
#define G_STRIPES 2
#define G_ICORE 3
#define G_IFAN 4
#define G_ITHR 5
#define G_FAN 6
#define G_S 7
#define G_EYE 8
#define G_FWD 11
#define G_FOC 14
#define G_LWC 15
#define G_LWF 16
#define G_LWT 17
#define G_LCUT 18
#define G_WCAP 19
#define G_PAL 20
#define G_DC 32
#define G_DBOX 35
#define G_DOFF 38
#define G_CLOCK 41
#define G_DESAT 43
#define G_COOL 44
#define G_BGTOP 48
#define G_BGBOT 51
#define G_HAZE 54
#define G_HAZEA 57
#define G_HAZEP 58
#define G_VIG 59
#define G_ASPECT 60
#define THREAD_W 0.24f

__device__ __forceinline__ float3 v3(float x, float y, float z) { return make_float3(x, y, z); }
__device__ __forceinline__ float3 vadd(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 vsub(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 vmul(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float vdot(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 vcross(float3 a, float3 b) {
    return make_float3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
__device__ __forceinline__ float3 vmix(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
__device__ __forceinline__ float3 vnorm(float3 a, float3 fb) {
    const float l = sqrtf(vdot(a, a));
    return (l > 1e-6f && l < 1e30f) ? vmul(a, 1.f / l) : fb;
}
// palette colour, linear light
__device__ __forceinline__ float3 vr_col(const float* __restrict__ G, float t) {
    const float3 c = pal(t, G + G_PAL);
    return make_float3(__powf(fmaxf(c.x, 1e-4f), 2.2f), __powf(fmaxf(c.y, 1e-4f), 2.2f), __powf(fmaxf(c.z, 1e-4f), 2.2f));
}
// the ring slot of chronological vertex j of a Kd-vertex window ending at the live slot `cur`
__device__ __forceinline__ int vr_slot(int cur, int Kd, int j, int K) {
    return ((cur - (Kd - 1 - j)) % K + 2 * K) % K;
}

// The live rows (one per pen) into the ring at `slot`, and the nibs (tip points + their colours).
extern "C" __global__ void vr_push(float* __restrict__ hist, const float* __restrict__ G, const int K, const int slot,
                                   float4* __restrict__ nib, float* __restrict__ nibc)
{
    const int p = threadIdx.x;
    if (p >= NPEN || slot < 0 || slot >= K) return;
    const float* s = G + G_PEN + p * NF;
    float* h = hist + ((size_t)p * K + slot) * NF;
    for (int f = 0; f < NF; ++f) h[f] = s[f];
    const float y = (p == 2) ? 0.5f * (s[1] + s[11]) : s[1];
    nib[p] = make_float4(s[0], y, s[2], 1.f);
    float3 c = vr_col(G, s[8] * G[G_PERIOD] + s[9]);
    c = vmix(c, v3(1.f, 1.f, 1.f), 0.45f + 0.4f * clamp01(s[10]));
    float k = s[13];
    if (!(k >= 0.f) || k > 100.f) k = 0.f;
    nibc[3 * p] = c.x * k; nibc[3 * p + 1] = c.y * k; nibc[3 * p + 2] = c.z * k;
}

// Ribbon rails + linear colours for the visible window (Kd chronological vertices per row).
// Rows: 0..NCORE-1 voice bands (pen = m / NB), NCORE the curtain, NCORE+1.. the two resonance threads.
// A strand deposits (intensity x colour) per px of its length, so the colour carries max(projected width,
// strand width) / (strands x s): a face-on ribbon then has the same surface brightness near or far; edge-on it
// gathers into a line about 1.6x brighter (a sheen) instead of strands-times brighter.
extern "C" __global__ void vr_build(const float* __restrict__ hist, const float* __restrict__ G,
                                    const int K, const int Kd, const int cur,
                                    float4* __restrict__ cA, float4* __restrict__ cB, float* __restrict__ cC,
                                    float4* __restrict__ fA, float4* __restrict__ fB, float* __restrict__ fC,
                                    float4* __restrict__ tA, float4* __restrict__ tB, float* __restrict__ tC)
{
    const int j = blockIdx.x * blockDim.x + threadIdx.x, m = blockIdx.y * blockDim.y + threadIdx.y;
    if (j >= Kd || m >= NROW || Kd > K) return;
    int pen, band = 0, kind;
    if (m < NCORE) { pen = m / NB; band = m - pen * NB; kind = 0; }
    else if (m == NCORE) { pen = 2; kind = 1; }
    else { pen = m - NCORE - 1; kind = 2; }
    const float* S = hist + ((size_t)pen * K + vr_slot(cur, Kd, j, K)) * NF;
    const float* P = hist + ((size_t)pen * K + vr_slot(cur, Kd, max(j - 1, 0), K)) * NF;
    const float* Q = hist + ((size_t)pen * K + vr_slot(cur, Kd, min(j + 1, Kd - 1), K)) * NF;
    float yc = S[1], yp = P[1], yq = Q[1];
    if (kind == 1) { yc = 0.5f * (S[1] + S[11]); yp = 0.5f * (P[1] + P[11]); yq = 0.5f * (Q[1] + Q[11]); }
    const float3 c = v3(S[0], yc, S[2]);
    const float3 T = vnorm(vsub(v3(Q[0], yq, Q[2]), v3(P[0], yp, P[2])), v3(1.f, 0.f, 0.f));
    // a frame across the stroke: U = world z made perpendicular to the tangent, V = T x U; N turns by the twist
    float3 U = vsub(v3(0.f, 0.f, 1.f), vmul(T, T.z));
    if (vdot(U, U) < 1e-6f) U = vsub(v3(0.f, 1.f, 0.f), vmul(T, T.y));
    U = vnorm(U, v3(0.f, 1.f, 0.f));
    const float3 V = vcross(T, U);
    float sn, cs;
    __sincosf(S[3], &sn, &cs);
    const float3 N = vnorm(vadd(vmul(U, cs), vmul(V, sn)), v3(0.f, 1.f, 0.f));
    float life = 1.f - smooth(0.f, G[G_LCUT], G[G_L] - S[7]);
    if (j == 0 || !(life > 0.f)) life = 0.f;
    const float3 eye = v3(G[G_EYE], G[G_EYE + 1], G[G_EYE + 2]), fwd = v3(G[G_FWD], G[G_FWD + 1], G[G_FWD + 2]);
    const float3 d = vsub(c, eye);
    const float zc = fmaxf(vdot(d, fwd), 0.05f);
    const float3 vd = vnorm(d, fwd);
    const float foc = G[G_FOC], s = fmaxf(G[G_S], 1e-3f), wcap = G[G_WCAP];
    const float cool = G[G_COOL] * (1.f - life);
    float3 A, B, col;
    float k;
    int o;
    if (kind == 0) {                                    // a colour band of a voice ribbon
        const float w = fmaxf(S[4], 0.f);
        const float del = 2.f / (float)(NB * NS);
        const float ua = -1.f + del * (band * NS + 0.5f), ub = -1.f + del * (band * NS + NS - 0.5f);
        A = vadd(c, vmul(N, w * ua));
        B = vadd(c, vmul(N, w * ub));
        const float ndv = vdot(N, vd);
        const float ef = sqrtf(fmaxf(1.f - ndv * ndv, 0.f));
        const float wpx = fminf(2.f * w * foc / zc * ef, wcap);
        const float q = fmaxf(wpx, G[G_LWC]) / ((float)(NB * NS) * s);
        const float um = (band + 0.5f) / (float)NB - 0.5f;
        col = vr_col(G, (S[8] + G[G_STRIPES] * 0.6f * um) * G[G_PERIOD] + S[9]);
        col = vmix(col, v3(1.f, 1.f, 1.f), clamp01(S[10] * (1.f - 1.6f * fabsf(um))));
        k = G[G_ICORE] * S[5] * q * (1.f + 0.6f * (1.f - ef));
        o = m * Kd + j;
    } else if (kind == 1) {                             // the inner-mass curtain, low edge to high edge
        const float lean = S[12];
        A = v3(S[0], S[1], S[2] - lean);
        B = v3(S[0], S[11], S[2] + lean);
        const float3 sv = vsub(B, A);
        const float sl = sqrtf(vdot(sv, sv));
        const float3 sd = vnorm(sv, v3(0.f, 1.f, 0.f));
        const float sdv = vdot(sd, vd);
        const float ef = sqrtf(fmaxf(1.f - sdv * sdv, 0.f));
        const float spx = fminf(sl * foc / zc * ef, wcap);
        const float q = fmaxf(spx, G[G_LWF]) / ((float)NSF * s);
        col = vr_col(G, S[8] * G[G_PERIOD] + S[9]);
        const float lum = 0.2126f * col.x + 0.7152f * col.y + 0.0722f * col.z;
        col = vmix(col, v3(lum, lum, lum), G[G_DESAT]);
        col = vmix(col, v3(1.f, 1.f, 1.f), clamp01(S[10]));
        k = G[G_IFAN] * G[G_FAN] * S[5] * q;
        o = j;
    } else {                                            // a resonance thread through a voice ribbon
        const float w = fmaxf(S[4], 0.f) * THREAD_W;
        A = vsub(c, vmul(N, w));
        B = vadd(c, vmul(N, w));
        const float ndv = vdot(N, vd);
        const float ef = sqrtf(fmaxf(1.f - ndv * ndv, 0.f));
        const float wpx = fminf(2.f * w * foc / zc * ef, wcap);
        const float q = fmaxf(wpx, G[G_LWT]) / ((float)NST * s);
        col = vr_col(G, S[8] * G[G_PERIOD] + S[9]);
        col = vmix(col, v3(1.f, 1.f, 1.f), 0.3f + 0.3f * clamp01(S[10]));
        k = G[G_ITHR] * S[6] * q;
        o = pen * Kd + j;
    }
    col = vmix(col, make_float3(col.x * col.x, col.y * col.y, col.z * col.z), cool);   // old strokes deepen
    k *= life;
    if (!(k >= 0.f) || k > 1e4f) k = 0.f;
    if (!(fabsf(A.x) + fabsf(A.y) + fabsf(A.z) + fabsf(B.x) + fabsf(B.y) + fabsf(B.z) < 1e7f)) {
        A = c; B = c; k = 0.f;
    }
    float4* ra = kind == 0 ? cA : (kind == 1 ? fA : tA);
    float4* rb = kind == 0 ? cB : (kind == 1 ? fB : tB);
    float* rc = kind == 0 ? cC : (kind == 1 ? fC : tC);
    ra[o] = make_float4(A.x, A.y, A.z, 1.f);
    rb[o] = make_float4(B.x, B.y, B.z, 1.f);
    rc[3 * o] = col.x * k; rc[3 * o + 1] = col.y * k; rc[3 * o + 2] = col.z * k;
}

// Dust: fixed points in world space, wrapped into a box round the view; w = brightness (edge fade x twinkle).
extern "C" __global__ void vr_dust(const float4* __restrict__ base, float4* __restrict__ pos,
                                   const float* __restrict__ G, const int n)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float4 b = base[i];
    const float bx = fmaxf(G[G_DBOX], 1e-2f), by = fmaxf(G[G_DBOX + 1], 1e-2f), bz = fmaxf(G[G_DBOX + 2], 1e-2f);
    float ux = b.x + (G[G_DOFF] - G[G_DC]) / bx + 0.5f;
    float uy = b.y + (G[G_DOFF + 1] - G[G_DC + 1]) / by + 0.5f;
    float uz = b.z + (G[G_DOFF + 2] - G[G_DC + 2]) / bz + 0.5f;
    ux -= floorf(ux); uy -= floorf(uy); uz -= floorf(uz);
    const float e = fminf(fminf(0.5f - fabsf(ux - 0.5f), 0.5f - fabsf(uy - 0.5f)), 0.5f - fabsf(uz - 0.5f));
    const float tw = 0.55f + 0.45f * __sinf(G[G_CLOCK] * (0.4f + 1.3f * hash1((unsigned int)i, 7u))
                                            + 6.2831853f * hash1((unsigned int)i, 11u));
    float w = b.w * smooth(0.f, 0.1f, e) * tw;
    if (!(w >= 0.f)) w = 0.f;
    pos[i] = make_float4(G[G_DC] + bx * (ux - 0.5f), G[G_DC + 1] + by * (uy - 0.5f), G[G_DC + 2] + bz * (uz - 0.5f), w);
}

// The deep background at full output size: a dim gradient, a faint drifting haze in the palette's colour,
// a vignette, and a static +-0.5 LSB triangular dither (no banding in the near-black gradient).
extern "C" __global__ void vr_bg(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ G,
                                 const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float u = (x + 0.5f) / W, v = (y + 0.5f) / H;
    const float g = smooth(0.f, 1.f, v);
    float3 c = vmix(v3(G[G_BGTOP], G[G_BGTOP + 1], G[G_BGTOP + 2]), v3(G[G_BGBOT], G[G_BGBOT + 1], G[G_BGBOT + 2]), g);
    const float asp = G[G_ASPECT], ph = G[G_HAZEP];
    const float n1 = vnoise(u * 2.6f * asp + ph, v * 2.6f - 0.37f * ph, seed);
    const float n2 = vnoise(u * 5.3f * asp - 0.6f * ph, v * 5.3f + 0.21f * ph, seed + 7u);
    const float hz = smooth(0.38f, 0.85f, 0.65f * n1 + 0.35f * n2) * (1.f - 0.6f * clamp01(fabsf(v - 0.55f) * 1.6f));
    c = vadd(c, vmul(v3(G[G_HAZE], G[G_HAZE + 1], G[G_HAZE + 2]), G[G_HAZEA] * hz));
    const float dx = (u - 0.5f) * 1.5f, dy = (v - 0.5f) * 1.3f;
    c = vmul(c, fmaxf(1.f - G[G_VIG] * (dx * dx + dy * dy), 0.f));
    const unsigned int h = hash_u((unsigned int)x * 1973u + (unsigned int)y * 9277u + 0x2545F491u);
    const float dth = 0.5f * (((h & 0xffffu) + (h >> 16)) * (1.f / 65536.f) - 1.f);
    out[y * W + x] = pack(make_float4(c.x * 255.f + dth, c.y * 255.f + dth, c.z * 255.f + dth, 255.f));
}

// Keep the ring's x and L small (float32): subtract the host's rebase from every sample.
extern "C" __global__ void vr_rebase(float* __restrict__ hist, const int n, const float dx, const float dl)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    hist[(size_t)i * NF] -= dx;
    hist[(size_t)i * NF + 7] -= dl;
}
"""
_OPTS = ("--std=c++14", "--use_fast_math")


# ------------------------------------------------------------------------------------------------ host helpers
def _fin(x, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _c01(x) -> float:
    return min(max(_fin(x), 0.0), 1.0)


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ease(x: float, tgt: float, tau: float, dt: float) -> float:
    if dt <= 0.0:
        return x
    return x + (tgt - x) * (1.0 - math.exp(-dt / max(tau, 1e-4)))


def _ease2(x: float, tgt: float, up: float, dn: float, dt: float) -> float:
    return _ease(x, tgt, up if tgt > x else dn, dt)


def _spring(x: float, v: float, tgt: float, w: float, dt: float) -> tuple[float, float]:
    """Critically damped spring, solved exactly (stable for any dt)."""
    if dt <= 0.0:
        return x, v
    x0 = x - tgt
    b = v + w * x0
    e = math.exp(-w * dt)
    return tgt + (x0 + b * dt) * e, (v - w * b * dt) * e


def _hash01(*a) -> float:
    h = 2166136261
    for v in a:
        h = ((h ^ (int(v) & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    return (h & 0xFFFFFF) / float(0x1000000)


def _pal_host(coeffs, t: float) -> tuple[float, float, float]:
    """Cosine palette on the host (sRGB 0..1), for the background and dust tints."""
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return tuple(min(max(a[i] + b[i] * math.cos(2 * math.pi * (c[i] * t + d[i])), 0.0), 1.0) for i in range(3))


def _segment(v) -> tuple[int, int]:
    """sound.segment as (k, n), or (0, 1) (the whole picture on this screen)."""
    if isinstance(v, (list, tuple)) and len(v) == 2:
        k, n = v
        if (isinstance(k, int) and isinstance(n, int) and not isinstance(k, bool) and not isinstance(n, bool)
                and 2 <= n <= 8 and 0 <= k < n):
            return k, n
    return 0, 1


class _Pen:
    """One pen's host state (the curtain uses y as its low edge and y2 as its high edge)."""

    def __init__(self, y: float) -> None:
        self.y = self.y_tgt = self.note_y = y
        self.yv = 0.0
        self.y2 = self.y2_tgt = self.note_y2 = y
        self.y2v = 0.0
        self.ink = 0.0
        self.vel = self.vel_tgt = 0.5
        self.fk = self.flare = 0.0
        self.th = self.th_tgt = 0.5 * math.pi       # face-on to the camera's side view
        self.res = 0.0
        self.z = 0.0
        self.z_lift = self.z_lift_tgt = 0.0
        self.cur = None                             # the pitch being drawn (None: lifted)
        self.last = -1                              # the last pitch drawn
        self.last_t = -1e9
        self.lifted = 0.0
        self.reg = 1.0
        self.lean = 0.1
        self.hue = 0.0


SPANS = ("screen", "room")    # screen: each TV the whole picture (default; owner, 30 Oct: the TVs aren't adjacent);
                              # room: one picture across the room's screens (sound.segment)


# ------------------------------------------------------------------------------------------------- the scene
class VoiceRibbons(Source):
    """Per frame: one arena upload (~450 bytes) and four small kernels (push, build, dust, background), then
    splat3d's ribbons / points and its resolve.  Host: a few numpy calls over the 128-key table."""
    name = "voice_ribbons"
    label = "Voice ribbons"
    description = ("A light painting made by the piano: the melody, the bass and the inner voices draw glowing "
                   "ribbons through 3D space as the camera glides alongside; loud is broad and bright, legato "
                   "flows, staccato beads, the pedal leaves a long-exposure glow.")
    listens = ("notes",)
    tags = {"family": "light", "dims": "3d", "toolkit": "splat3d", "technique": ["light", "wireframe"],
            "moods": ["dreamy", "elegant", "hypnotic"], "style": "gentle", "energy": [0.0, 0.9],
            "modes": ["piano"], "reads": ["spectacle"]}
    params = {
        **standard_scene_params(palette="rainbow", render_scale=0.5, period=0.8, drift=0.04),
        "trail_length": num(3.0, 20.0, 9.0, "Trail length", unit="s", step=0.5, tier="regional", basic=True),
        "twist": num(0.0, 1.0, 0.5, "Twist", tier="regional", basic=True),
        "orbit": num(0.0, 1.0, 0.5, "Camera orbit", tier="regional"),
        "pens": num(2.0, 3.0, 3.0, "Pens (melody + bass, + inner mass)", tier="regional"),
        "stroke_width": num(0.4, 2.0, 1.0, "Stroke width", tier="regional"),
        "stripes": num(0.0, 1.0, 0.6, "Colours across the stroke", tier="regional"),
        "bloom": num(0.0, 2.0, 0.9, "Bloom", tier="regional"),
        "dust": num(0.0, 2.0, 1.0, "Dust", tier="regional"),
        "span": enum(SPANS, "screen", "Span (this screen, or one picture across the room)"),
    }

    # ------------------------------------------------------------------------------------------------ setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.span_room = params.get("span") == "room"
        f32 = cp.float32
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.s = self.h / 1080.0
        self.sp = Splat3D(ctx, self.w, self.h, max_points=N_DUST + 16, max_lines=4096, pool=1 << 20)
        mod = raw_module(_SRC, _OPTS)
        self.k_push = mod.get_function("vr_push")
        self.k_build = mod.get_function("vr_build")
        self.k_dust = mod.get_function("vr_dust")
        self.k_bg = mod.get_function("vr_bg")
        self.k_rebase = mod.get_function("vr_rebase")
        self.hist = cp.zeros(NPEN * K * NF, f32)
        self.cA, self.cB, self.cC = cp.zeros((NCORE * K, 4), f32), cp.zeros((NCORE * K, 4), f32), \
            cp.zeros((NCORE * K, 3), f32)
        self.fA, self.fB, self.fC = cp.zeros((K, 4), f32), cp.zeros((K, 4), f32), cp.zeros((K, 3), f32)
        self.tA, self.tB, self.tC = cp.zeros((2 * K, 4), f32), cp.zeros((2 * K, 4), f32), cp.zeros((2 * K, 3), f32)
        self.nib = cp.zeros((NPEN, 4), f32)
        self.nibc = cp.zeros((NPEN, 3), f32)
        rng = np.random.default_rng(self.seed)
        base = rng.random((N_DUST, 4)).astype(np.float32)
        base[:, 3] = (0.2 + 0.8 * rng.random(N_DUST) ** 2).astype(np.float32)
        self.dbase = cp.asarray(base)
        self.dpos = cp.zeros((N_DUST, 4), f32)
        self.G = cp.zeros(NG, f32)
        self.G_h = np.zeros(NG, np.float32)
        self.rows = self.G_h[G_PEN:G_PEN + NPEN * NF].reshape(NPEN, NF)
        self.nbuf = np.zeros((128, 4), np.float32)
        self.Lh = np.zeros(K, np.float64)
        # state
        self.now = 0.0
        t0 = 0.0
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        tms = _fin(getattr(fr, "t_ms", 0.0)) if fr is not None else 0.0
        if 0.0 < tms < 1e13:
            t0 = tms / 1000.0          # the room's clock: two screens sharing one picture orbit in step
        self.clock = t0 - PRE_S
        self.head_x = 0.0
        self.L = 0.0
        self.drift = 0.0
        self.kick = self.damper = self.soft = 0.0
        self.quiet = 1e3
        self.idle = 1.0
        self.v = V0
        self.t_eff = float(params["trail_length"])
        self.sky, self.sky_t = 72.0, -1e9
        self.flo, self.flo_t = 48.0, -1e9
        self.pens = [_Pen((IDLE_C[0] - Y0) * PITCH_Y), _Pen((IDLE_C[1] - Y0) * PITCH_Y), _Pen((58.0 - Y0) * PITCH_Y)]
        f = self.pens[2]
        f.y = f.y_tgt = f.note_y = (55.5 - Y0) * PITCH_Y
        f.y2 = f.y2_tgt = f.note_y2 = (60.5 - Y0) * PITCH_Y
        self.D = None
        self.cy = None
        self.doff = [0.0, 0.0, 0.0]
        self.haze = 0.0
        self.tgt = (0.0, 0.0, 0.0)
        self._seen = None
        self._reg_key, self._reg_w = None, None
        # the ring: `cur` is the live slot (rewritten every frame), `head` the newest committed one
        self.cur, self.head, self.n_comm, self.since = 0, K - 1, 0, 0.0
        self._preroll(params)

    def _preroll(self, params: dict) -> None:
        """A few seconds of the idle drawing, so the scene opens on strokes already in the air."""
        hist_h = np.zeros((NPEN, K, NF), np.float32)
        dt = 1.0 / SAMPLE_HZ
        for _ in range(int(PRE_S * SAMPLE_HZ)):
            self._advance(dt, params, None)
            self._fill_rows(params)
            hist_h[:, self.cur, :] = self.rows
            self._commit()
        self.hist.set(hist_h.reshape(-1))

    # ------------------------------------------------------------------------------------------------ hearing
    def _reg_weights(self, snd):
        """Per-pitch weights (128) for the ensemble's register window (sound.register), cached; None: all full."""
        reg = getattr(snd, "register", None) if snd is not None else None
        if not reg:
            return None
        if self._reg_key != reg:
            self._reg_key = reg
            try:
                from stream_service.ensemble import note_weights
                w = note_weights(reg, np.arange(128))
                self._reg_w = None if w is None else np.clip(np.nan_to_num(np.asarray(w, np.float64)), 0.0, 1.0)
            except Exception:                 # a bad window never breaks a frame: draw everything at full
                self._reg_w = None
        return self._reg_w

    def _hear(self):
        """-> ((notes or None, damper, soft, {onset pitch: vel}, register weights or None), (k, n))."""
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        if fr is None:
            return None, (0, 1)
        n = getattr(fr, "notes", None)
        nn = None
        if isinstance(n, np.ndarray) and n.shape == (128, 4):
            np.copyto(self.nbuf, n, casting="unsafe")
            np.nan_to_num(self.nbuf, copy=False, nan=0.0, posinf=1e9, neginf=-1e9)
            nn = self.nbuf
        damper = soft = 0.0
        ped = getattr(fr, "pedals", None)
        try:
            damper, soft = _c01(ped[DAMPER]), _c01(ped[SOFT])
        except (TypeError, IndexError, KeyError):
            pass
        ons: dict = {}
        o = getattr(fr, "onsets", None)
        if o is not None and o is not self._seen:      # each onset exactly once (a new list every frame)
            self._seen = o
            try:
                evs = list(o)[:256]
            except TypeError:
                evs = []
            for ev in evs:
                try:
                    q, v = int(ev[0]), float(ev[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if 0 <= q < 128 and math.isfinite(v):
                    ons[q] = max(ons.get(q, 0.0), min(max(v, 0.0), 1.0))
        seg = getattr(snd, "segment", None) if self.span_room else None
        return (nn, damper, soft, ons, self._reg_weights(snd)), _segment(seg)

    def _assign(self, n):
        """Melody and bass pitches this frame (None: that pen is lifted), with echo_arcs' skyline / floor."""
        if n is None:
            return None, None
        act = np.flatnonzero((n[:, AGE_OFF] < 0.0) | (n[:, AGE_ON] < RECENT_S))
        if act.size == 0:
            return None, None
        now = self.now
        sky_d = max(self.sky - FALL * min(now - self.sky_t, 10.0), 0.0)
        flo_r = min(self.flo + FALL * min(now - self.flo_t, 10.0), 127.0)
        top, low = int(act[-1]), int(act[0])
        mel = bas = None
        if act.size == 1:
            if abs(top - sky_d) <= abs(top - flo_r):
                mel = top
            else:
                bas = top
        else:
            if top >= sky_d - SKY_TOL:
                mel = top
            if low <= flo_r + SKY_TOL:
                bas = low
            if mel is None and bas is None:
                mel, bas = top, low
        if mel is not None:
            self.sky, self.sky_t = float(mel), now
        if bas is not None:
            self.flo, self.flo_t = float(bas), now
        return mel, bas

    # ------------------------------------------------------------------------------------------------ motion
    def _advance(self, dt: float, p: dict, heard) -> None:
        e = _c01(p["energy"])
        spd = max(_fin(p["speed"], 1.0), 0.0)
        tw = _c01(p["twist"])
        if heard is None:
            n, damper, soft, ons, reg = None, 0.0, 0.0, {}, None
        else:
            n, damper, soft, ons, reg = heard
        self.now += dt
        self.clock += dt * spd
        self.kick = _ease2(self.kick, _c01(p["pulse"]), 0.03, 0.3, dt)
        self.damper = _ease(self.damper, damper, 0.15, dt)
        self.soft = _ease(self.soft, soft, 0.2, dt)
        sounding = n is not None and bool(((n[:, AGE_OFF] < 0.0) | (n[:, LEVEL] > 0.05)).any())
        self.quiet = 0.0 if sounding else min(self.quiet + dt, 1e6)
        idle = self.idle = _smooth(1.5, 5.0, self.quiet)
        # time runs forward through the picture; the exposure (trail) lengthens with the damper pedal
        self.v = V0 * (0.55 + 0.9 * e) * spd * (1.0 + 0.25 * self.kick)
        self.head_x += self.v * dt
        trail = min(max(_fin(p["trail_length"], 9.0), 1.0), 60.0)
        self.t_eff = trail * (1.0 + 0.7 * self.damper)
        self.L += DECAY * dt / self.t_eff
        self.drift = math.fmod(self.drift + _fin(p["drift_speed"]) * spd * dt, 1000.0)
        mel, bas = self._assign(n)
        c = self.clock
        for i, q in ((0, mel), (1, bas)):
            iy = (IDLE_C[i] + IDLE_A[i] * math.sin(2 * math.pi * (c * IDLE_R[i] + 0.13 * i))
                  + 0.5 * IDLE_A[i] * math.sin(2 * math.pi * (c * 2.7 * IDLE_R[i] + 0.3 + 0.2 * i)))
            iink = IDLE_INK * _smooth(0.3, 0.75, 0.5 + 0.5 * math.sin(2 * math.pi * (c * PHR_R[i] + 0.37 * i)))
            self._pen(i, q, n, ons, reg, dt, e, spd, tw, idle, (iy - Y0) * PITCH_Y, iink)
        self._curtain(n, ons, reg, dt, e, tw, idle)

    def _pen(self, i, q, n, ons, reg, dt, e, spd, tw, idle, idle_y, idle_ink) -> None:
        pen = self.pens[i]
        now = self.now
        if q is not None:
            if q != pen.cur or q in ons:              # a new note on this pen: turn, swell
                if pen.last >= 0 and now - pen.last_t < 3.0:
                    dq = max(-24, min(24, q - pen.last))
                    pen.th_tgt += tw * TW_K * dq * ROLL[i]
                v = ons.get(q)
                if v is None and n is not None:
                    v = float(n[q, VEL])
                v = _c01(v if v is not None else 0.6)
                pen.vel_tgt = v
                pen.fk = max(pen.fk, 0.35 + 0.65 * v)
                pen.last = q
            pen.cur = q
            pen.last_t = now
            pen.note_y = (q - Y0) * PITCH_Y
            pen.lifted = 0.0
            ink_t = 1.0
        else:
            pen.cur = None
            was = pen.lifted
            pen.lifted = min(pen.lifted + dt, 1e6)
            if was < LIFT_S <= pen.lifted:          # a phrase gap: the next phrase starts at a new depth
                pen.z_lift_tgt = (_hash01(self.seed, i, int(self.clock * 4.0)) - 0.5) * 1.1
            ink_t = 0.0
        pen.y_tgt = pen.note_y + (idle_y - pen.note_y) * idle
        pen.y, pen.yv = _spring(pen.y, pen.yv, pen.y_tgt, OMEGA[i], dt)
        pen.ink = _ease2(pen.ink, max(ink_t, idle_ink * idle), 0.022, 0.075, dt)
        pen.vel = _ease2(pen.vel, pen.vel_tgt, 0.04, 0.3, dt)
        if dt > 0.0:
            pen.fk *= math.exp(-dt / 0.09)
        pen.flare = _ease2(pen.flare, pen.fk, 0.03, 0.35, dt)
        res_t = _c01(n[pen.last, LEVEL]) if (n is not None and pen.last >= 0) else 0.0
        pen.res = _ease2(pen.res, res_t, 0.03, 0.08, dt)
        pen.reg = 1.0 if (reg is None or pen.last < 0) else _c01(reg[pen.last])
        pen.th_tgt += tw * (0.15 + 0.45 * e) * spd * dt * ROLL[i]
        pen.th = _ease(pen.th, pen.th_tgt, 0.3, dt)
        if abs(pen.th_tgt) > 2000.0:                  # keep the angles small (whole turns: nothing moves)
            sh = math.copysign(600.0 * math.pi, pen.th_tgt)
            pen.th_tgt -= sh
            pen.th -= sh
        pen.z_lift = _ease(pen.z_lift, pen.z_lift_tgt, 1.5, dt)
        pen.z = Z_BASE[i] + pen.z_lift + (0.22 + 0.3 * e) * math.sin(2 * math.pi * (self.clock * 0.047 + 0.31 * i))

    def _curtain(self, n, ons, reg, dt, e, tw, idle) -> None:
        """The inner mass: what sounds strictly between the voices' pitches, as a curtain low edge -> high edge."""
        f = self.pens[2]
        now = self.now
        m0, m1 = self.pens[0], self.pens[1]
        mel = m0.last if (m0.last >= 0 and now - m0.last_t < 3.0) else 128
        bas = m1.last if (m1.last >= 0 and now - m1.last_t < 3.0) else -1
        b_t = 0.0
        if n is not None and mel - bas >= 2:
            lo_i, hi_i = max(bas + 1, 0), min(mel, 128)
            seg = n[lo_i:hi_i, LEVEL]
            idx = np.flatnonzero(seg > 0.04)
            if idx.size:
                lv = seg[idx]
                strong = idx[lv >= 0.2 * float(lv.max())] + lo_i
                lo_t, hi_t = float(strong[0]) - 0.6, float(strong[-1]) + 0.6
                if hi_t - lo_t < 1.6:
                    cm = 0.5 * (lo_t + hi_t)
                    lo_t, hi_t = cm - 0.8, cm + 0.8
                f.note_y, f.note_y2 = (lo_t - Y0) * PITCH_Y, (hi_t - Y0) * PITCH_Y
                mass = float(np.dot(lv, 0.5 + 0.5 * np.clip(n[idx + lo_i, VEL], 0.0, 1.0)))
                b_t = min(1.3, 0.6 * mass) if math.isfinite(mass) else 0.0
                f.last = int(0.5 * (strong[0] + strong[-1]))
                for q, v in ons.items():
                    if bas < q < mel:
                        f.fk = max(f.fk, 0.3 + 0.7 * v)
        c = self.clock
        ic = 58.0 + 3.0 * math.sin(2 * math.pi * (c * 0.05 + 0.6))
        ih = 2.5 + 1.2 * math.sin(2 * math.pi * (c * 0.08 + 0.2))
        lo_y, hi_y = (ic - ih - Y0) * PITCH_Y, (ic + ih - Y0) * PITCH_Y
        f.y_tgt = f.note_y + (lo_y - f.note_y) * idle
        f.y2_tgt = f.note_y2 + (hi_y - f.note_y2) * idle
        f.y, f.yv = _spring(f.y, f.yv, f.y_tgt, OMEGA[2], dt)
        f.y2, f.y2v = _spring(f.y2, f.y2v, f.y2_tgt, OMEGA[2], dt)
        if f.y2 < f.y + 0.02:
            f.y2 = f.y + 0.02
        phr = _smooth(0.3, 0.75, 0.5 + 0.5 * math.sin(2 * math.pi * (c * PHR_R[2] + 0.74)))
        b_t = max(b_t, idle * IDLE_INK * 0.8 * phr)
        f.ink = _ease2(f.ink, b_t, 0.05, 0.35, dt)
        if dt > 0.0:
            f.fk *= math.exp(-dt / 0.09)
        f.flare = _ease2(f.flare, f.fk, 0.04, 0.4, dt)
        f.lean = _ease(f.lean, 0.08 + 0.32 * tw * math.sin(2 * math.pi * (c * 0.031 + 0.4)), 1.0, dt)
        f.reg = 1.0 if (reg is None or f.last < 0) else _c01(reg[min(max(f.last, 0), 127)])
        was = f.lifted
        f.lifted = f.lifted + dt if f.ink < 0.04 else 0.0
        if was < 0.8 <= f.lifted:
            f.z_lift_tgt = (_hash01(self.seed, 2, int(self.clock * 4.0)) - 0.5) * 0.8
        f.z_lift = _ease(f.z_lift, f.z_lift_tgt, 1.5, dt)
        f.z = Z_BASE[2] + f.z_lift + (0.22 + 0.3 * e) * math.sin(2 * math.pi * (c * 0.039 + 0.77))

    def _fill_rows(self, p: dict) -> None:
        R = self.rows
        R.fill(0.0)
        sw = min(max(_fin(p["stroke_width"], 1.0), 0.2), 3.0)
        soft, idle, kick = self.soft, self.idle, 0.6 * self.kick
        for i in range(2):
            pen = self.pens[i]
            vel = pen.vel + (0.5 - pen.vel) * idle
            fl = pen.flare + kick
            pitch = pen.y / PITCH_Y + Y0
            R[i, 0] = self.head_x
            R[i, 1] = pen.y
            R[i, 2] = pen.z
            R[i, 3] = pen.th
            R[i, 4] = W0[i] * sw * (0.3 + 0.7 * vel) * (0.3 + 0.7 * pen.ink) * (1.0 + 0.4 * fl) * (1.0 - 0.2 * soft)
            R[i, 5] = pen.ink * (0.2 + 0.8 * vel ** 1.3) * (1.0 + 0.8 * fl) * pen.reg * (1.0 - 0.25 * soft)
            R[i, 6] = pen.res * (0.3 + 0.7 * self.damper) * (0.5 + 0.5 * vel) * pen.reg
            R[i, 7] = self.L
            R[i, 8] = HUE0[i] + (pitch - 60.0) / 88.0 * 0.35
            R[i, 9] = self.drift
            R[i, 10] = min(0.85, 0.55 * vel * vel + 0.45 * fl) * (1.0 - 0.5 * soft)
            R[i, 13] = (0.1 + 0.9 * pen.ink * (0.4 + 0.6 * vel)) * (1.0 + fl) * pen.reg
        f = self.pens[2]
        mid = 0.5 * (f.y + f.y2) / PITCH_Y + Y0
        R[2, 0] = self.head_x
        R[2, 1] = f.y
        R[2, 2] = f.z
        R[2, 5] = f.ink * (1.0 + 0.6 * (f.flare + kick)) * f.reg * (1.0 - 0.2 * soft)
        R[2, 7] = self.L
        R[2, 8] = HUE0[2] + (mid - 60.0) / 88.0 * 0.3
        R[2, 9] = self.drift
        R[2, 10] = 0.25 * f.flare
        R[2, 11] = f.y2
        R[2, 12] = f.lean
        np.nan_to_num(R, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

    def _commit(self) -> None:
        self.Lh[self.cur] = self.L
        self.head = self.cur
        self.cur = (self.cur + 1) % K
        self.n_comm += 1

    def _alive(self) -> int:
        """Committed samples still lit (L is monotonic along the ring, so a bounded binary search)."""
        lo, hi = 0, min(self.n_comm, K - 2)
        L, Lh, head = self.L, self.Lh, self.head
        for _ in range(16):
            if lo >= hi:
                break
            mid = (lo + hi + 1) >> 1
            if L - Lh[(head - mid + 1) % K] < LCUT:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _rebase(self) -> None:
        dx = REBASE_X if self.head_x > REBASE_X else 0.0
        dl = REBASE_L if self.L > REBASE_L else 0.0
        if dx == 0.0 and dl == 0.0:
            return
        self.head_x -= dx
        self.doff[0] -= dx
        self.L -= dl
        self.Lh -= dl
        n = NPEN * K
        self.k_rebase(((n + 255) // 256,), (256,), (self.hist, np.int32(n), np.float32(dx), np.float32(dl)))

    def _camera(self, p: dict, dt: float, seg: tuple[int, int]):
        orb = _c01(p["orbit"])
        k, nseg = seg
        tan_half = math.tan(math.radians(FOV * 0.5))
        aspect = self.w / max(self.h, 1)
        trail = min(max(_fin(p["trail_length"], 9.0), 1.0), 60.0)
        d_t = min(max(3.2 + 0.36 * self.v * trail / nseg, 4.2), 10.0)
        self.D = d_t if self.D is None else _ease(self.D, d_t, 2.5, dt)
        fan = min(max(_fin(p["pens"], 3.0) - 2.0, 0.0), 1.0)
        m0, m1, f = self.pens
        ws = (0.25 + m0.ink, 0.25 + m1.ink, fan * (0.15 + f.ink))
        ys = (m0.y, m1.y, 0.5 * (f.y + f.y2))
        yt = sum(a * b for a, b in zip(ws, ys)) / max(sum(ws), 1e-6)
        yt = min(max(yt, -1.6), 2.0)
        self.cy = yt if self.cy is None else _ease(self.cy, yt, 2.5, dt)
        wf = 2.0 * self.D * tan_half * aspect
        tgt = (self.head_x - 0.2 * nseg * wf, self.cy, 0.0)
        c = self.clock
        yaw = math.pi + orb * (0.62 * math.sin(2 * math.pi * (c / 53.0 + 0.17))
                               + 0.28 * math.sin(2 * math.pi * (c / 23.0 + 0.61)))
        pitch = 0.07 + orb * 0.15 * math.sin(2 * math.pi * (c / 41.0 + 0.33))
        roll = orb * 0.035 * math.sin(2 * math.pi * (c / 37.0 + 0.5))
        eye = orbit(tgt, self.D, yaw, pitch)
        cam = look_at(eye, tgt, fov_deg=FOV, roll=roll)
        if nseg > 1:                                  # one picture across the screens: shift by frame widths
            sh = (k - 0.5 * (nseg - 1)) * wf
            r = cam.right
            eye = tuple(eye[a] + r[a] * sh for a in range(3))
            tgt = tuple(tgt[a] + r[a] * sh for a in range(3))
            cam = look_at(eye, tgt, fov_deg=FOV, roll=roll)
        self.tgt = tgt
        return cam

    # ------------------------------------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, out) -> None:
        dt = _fin(dt)
        dt = min(dt, 0.25) if dt > 0.0 else 0.0
        p = self.step_params(dt)
        heard, seg = self._hear()
        self._rebase()
        self._advance(dt, p, heard)
        self._fill_rows(p)
        cam = self._camera(p, dt, seg)
        e = _c01(p["energy"])
        spd = max(_fin(p["speed"], 1.0), 0.0)
        fan = min(max(_fin(p["pens"], 3.0) - 2.0, 0.0), 1.0)
        sess = self.session_palette()
        pname = p["palette"] if p.get("palette") in PALETTES else "rainbow"
        coeffs = np.asarray(PALETTES[pname] if sess is None else sess, np.float64).reshape(-1)[:12]
        if coeffs.size != 12 or not np.isfinite(coeffs).all():
            coeffs = np.asarray(PALETTES["rainbow"], np.float64).reshape(-1)
        tint = _pal_host(coeffs, 0.5 * self.drift + 0.55)
        if dt > 0.0:
            self.doff[0] += 0.05 * spd * (0.5 + e) * dt
            self.doff[1] += 0.02 * spd * (0.5 + e) * dt
            self.doff[2] += 0.03 * spd * (0.5 + e) * dt
            self.haze = math.fmod(self.haze + 0.012 * spd * dt, 1000.0)

        G = self.G_h
        G[0] = self.L
        G[1] = _fin(p["palette_period"], 1.0)
        G[2] = _c01(p["stripes"])
        G[3], G[4], G[5] = I_CORE, I_FAN, I_THR
        G[6] = fan
        G[7] = self.s
        G[8:11] = cam.eye
        G[11:14] = cam.fwd
        G[14] = 0.5 * self.h / max(cam.tan_half, 1e-3)
        G[15] = max(1.0, LW_CORE * self.s)
        G[16] = max(1.0, LW_FAN * self.s)
        G[17] = max(1.0, LW_THR * self.s)
        G[18] = LCUT
        G[19] = 0.6 * self.h
        G[20:32] = coeffs
        G[32:35] = self.tgt
        G[35:38] = (18.0, 11.0, 16.0)
        G[38:41] = self.doff
        G[41] = math.fmod(self.clock, 1000.0)
        G[43] = 0.35
        G[44] = 0.45
        G[48:51] = (0.008 + 0.03 * tint[0], 0.010 + 0.03 * tint[1], 0.024 + 0.03 * tint[2])
        G[51:54] = (0.003, 0.003, 0.007)
        G[54:57] = tint
        G[57] = 0.03 * (0.6 + 0.5 * e) * (1.0 + 0.6 * self.damper)
        G[58] = self.haze
        G[59] = 0.35
        G[60] = self.w / max(self.h, 1)
        np.nan_to_num(G, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.G.set(G)

        self.k_push((1,), (32,), (self.hist, self.G, np.int32(K), np.int32(self.cur), self.nib, self.nibc))
        kd = min(self._alive() + 1, K - 1)
        if kd >= 2:
            launch_2d(self.k_build, kd, NROW, (self.hist, self.G, np.int32(K), np.int32(kd), np.int32(self.cur),
                                               self.cA, self.cB, self.cC, self.fA, self.fB, self.fC,
                                               self.tA, self.tB, self.tC))
        dust = min(max(_fin(p["dust"], 1.0), 0.0), 2.0)
        if dust > 1e-3:
            self.k_dust(((N_DUST + 255) // 256,), (256,), (self.dbase, self.dpos, self.G, np.int32(N_DUST)))
        if dt > 0.0:                                  # commit the live sample at 30 Hz (it stays in the ring)
            self.since += dt
            if self.since >= 1.0 / SAMPLE_HZ - 1e-6:
                self._commit()
                self.since = min(self.since - 1.0 / SAMPLE_HZ, 1.0 / SAMPLE_HZ)

        sp = self.sp
        sp.clear()
        far = (self.D + 6.0, self.D + 22.0)
        near = (0.25, 1.0)
        if dust > 1e-3:
            dcol = (0.6 + 0.25 * tint[0], 0.65 + 0.25 * tint[1], 0.8 + 0.2 * tint[2])
            sp.points(self.dpos, cam=cam, intensity=0.06 * dust * (0.7 + 0.5 * e), size_px=1.3, size_range=(0.6, 2.2),
                      weight_in_w=True, colour=dcol, near_fade=(0.3, 1.5), far_fade=far)
        if kd >= 2:
            line = dict(cam=cam, intensity=1.0, size_range=(0.6, 2.4), near_fade=near, far_fade=far)
            if fan > 1e-3:
                sp.ribbons(self.fA, self.fB, strands=NSF, k=kd, n_ribbons=1, edge=0.5, width_px=LW_FAN,
                           rgb=self.fC, **line)
            sp.ribbons(self.tA, self.tB, strands=NST, k=kd, n_ribbons=2, edge=1.0, width_px=LW_THR, rgb=self.tC,
                       **line)
            sp.ribbons(self.cA, self.cB, strands=NS, k=kd, n_ribbons=NCORE, edge=1.0, width_px=LW_CORE,
                       rgb=self.cC, **line)
        sp.points(self.nib, n=2, cam=cam, intensity=1.6, size_px=2.2, size_range=(0.6, 4.0), rgb=self.nibc,
                  near_fade=near)
        sp.points(self.nib, n=2, cam=cam, intensity=3.0, size_px=8.0, size_range=(0.6, 8.0), rgb=self.nibc,
                  near_fade=near)
        Wo, Ho = int(out.shape[1]), int(out.shape[0])
        launch_2d(self.k_bg, Wo, Ho, (out, np.int32(Wo), np.int32(Ho), self.G, np.uint32(self.seed)))
        bloom = min(max(_fin(p["bloom"], 0.9), 0.0), 2.0)
        sp.resolve(out, exposure=max(_fin(p["intensity"], 1.0), 0.0), tonemap="aces", saturation=1.08,
                   glow=bloom * (0.45 + 0.45 * e) * (1.0 + 0.6 * self.damper) * (1.0 + 0.3 * self.kick),
                   glow_radius_px=20.0, glow_wide=0.6, background=out)

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        for k in ("hist", "cA", "cB", "cC", "fA", "fB", "fC", "tA", "tB", "tC", "nib", "nibc", "dbase", "dpos", "G"):
            setattr(self, k, None)
