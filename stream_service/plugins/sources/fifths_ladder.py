"""Fifths ladder: a live harmonic analysis of the piano, drawn as a ladder of fifths with the key's home on the floor.

Source: Original (inspired by D. Temperley, "The Line of Fifths", Music Analysis 19 (2000) and The Cognition of
        Basic Musical Structures (MIT 2001); B. Pardo & W. Birmingham, "Algorithms for Chordal Analysis", CMJ 26
        (2002) (template scoring); E. Chew & Y.-C. Chen, "Real-time pitch spelling using the Spiral Array", CMJ 29
        (2005) https://music.informatics.indiana.edu/courses/I546/pdf/chew_chen.pdf (spelling from context);
        F. Lerdahl, Tonal Pitch Space (OUP 2001); D. Meredith, https://research.gold.ac.uk/1006/1/COMP_Meredith_2007a.pdf)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
----------------------
A dark, warm page ruled like manuscript paper, except that each ruled line is a step on the LINE OF FIFTHS rather
than a pitch: going up one line is going up a perfect fifth (C, G, D, A, E, B, F#...), going down is going down a
fifth (C, F, Bb, Eb...).  The note names sit at the left end of the lines, spelled from the line itself, so G# and
Ab are different lines (eight fifths apart) as they are in a score.

The key's home note is the gold FLOOR line, with the key's name at the left.  One line above it, the crimson
ledge, is the dominant (V); one line below, the teal ledge, is the subdominant (IV).  The key's seven notes sit
together on the line of fifths, so the key is a translucent BAND seven lines tall (IV up to vii in major; in
minor the band sits three lines lower, with dashed ledges for the raised sixth and seventh).  Anything outside the
band is chromatic and visibly sticks out.

Time scrolls from right to left; "now" is the faint vertical line at 84% of the width.  Each chord the piano plays
is a glowing PILL at the height of its root, as long as it lasts, coloured by its harmonic function: gold for
tonic chords (I, vi, iii), teal for pre-dominants (ii, IV, iv, bVI...), crimson for dominants (V, V7, vii°, and
every secondary dominant such as V/V), violet for anything chromatic.  Smaller, fainter pills sit at the heights
of the chord's other notes, joined to the root by a thin stem: the fifth is one line up, a major third four lines
up, a minor third three lines DOWN, a minor seventh two lines down, so major and minor chords have different
shapes.  The chord's letter name is written inside its pill.  A luminous curve, the fundamental bass, joins
successive roots, so a ii-V-I is a staircase of three steps down to the floor: the height of a chord above the
floor literally counts how many dominant steps it is from home (V +1, ii +2, vi +3, iii +4; IV -1, bVII -2...).

Along the top runs a lane of Roman numerals, one per chord, tinted by function, with the inversion figures
(6, 6/4, 6/5, 4/3, 4/2) beside them.  When the key changes, the floor and the band step to the new home at the
moment of the change, the last chord that belongs to both keys (the pivot) shows its second reading after an "=",
and the view glides to the new floor over two seconds.  The panel on the left repeats the current numeral large,
names its function and says how many fifths it is from home.

Expectation is drawn too.  While a dominant sounds, a hollow ghost ring breathes just ahead of "now" at the height
where it wants to go (one line down for a V-type chord, the tonic for a leading-tone chord).  If the music obeys,
the ghost fills with gold over the chord it predicted; if it deceives (V to vi), the ghost fades and a pale arc
bends from it to where the music actually went.

When the piano plays single lines (two or fewer notes sounding) each note is a small dot on its line instead, so
a melody traces the scale inside the band and its tonic notes land on the floor.

Theory
------
* Chord reading (Pardo & Birmingham style templates, via the shared tonal helper): 12 roots x 11 qualities, score
  = notes in the chord - 0.6 x notes outside - 0.05 x size + 0.3 if the bass is the root + 0.1 if the root is in
  the key; a new chord must lead by 0.1 for 0.15 s, and may only change near an onset (or when the room's
  harmony detector reports a change), so arpeggios and pedalled decays do not flicker.
* Height: the root's interval above the tonic in fifths, -5..+6 (fifths_of), then shifted by 12 fifths
  (an enharmonic respelling) to whichever copy is nearer a 6 s moving average of recent roots, so a passage in
  sharps stays in sharps (Chew & Chen's idea of spelling from context).
* Function, numerals, secondary dominants and inversions follow the textbook tables (tonal.py).
* Key: the room's key detector when it has one, else a Krumhansl-Kessler tracker on the notes (tonal.KeyResolver).

Macros: energy -> glow, band brightness and breathing, dust density; pulse (note_on) -> pill and line glow kick;
speed -> idle motion only (dust drifting along the lines, a soft pool of lamplight wandering over the page, the
band's breathing, the haze, the cold-start example's scroll), the analysis itself scrolls in true time; intensity -> gain; palette -> the page, the band, the ruled
lines and the dust (function colours stay fixed).  Alive without notes: on a cold start a dim example progression
(I vi ii6 V7 I IV vii° iii vi ii V I in C) scrolls by, labelled "example"; after ten seconds of silence the time
axis parks, so the last progression stays readable, the band breathes and dust drifts along the lines; the next
note eases the scroll back in over a second.

Host diet: the chord reader runs at 10 Hz (one small matmul); records, dots and key segments live in one
persistent float32 arena (~13 KB) copied per frame from a pinned ring slot with one async H2D copy; one kernel
(block-culled record, dot and label lists built in shared memory by the block prologue) plus the shared present.
All text is baked once per render size with PIL into one coverage atlas (~950 strings, ~0.7 s, cached per size;
the numerals also as Gaussian-blurred halos for a soft bloom) and sampled in-kernel.  Chords open only when at
least three pitch classes really sound (raw levels), so scales and single lines stay dots.  Measured on the P4000
at 4K, render_scale 0.5: 2.4 ms (kernel + present).
"""
from __future__ import annotations

import logging
import math
import re

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.fifths_ladder")

SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# layout (fractions of the internal frame)
X_NOW, X_LEFT = 0.84, 0.16
Y_FLOOR = 0.62
LANE_Y, LANE_F = 0.18, 0.068          # numeral lane centre and font size
PANEL_F = 0.09                        # the panel's numeral
SMALL_F, NAME_F = 0.036, 0.030        # baked sizes of small text and chord names
K_NAME0, N_NAMES = -20, 41            # line names baked for lof -20..20
K_CHORD0, N_CHORD = -16, 33           # chord names baked for root lof -16..16
ONSET_WINDOW = 0.6                    # a chord may change this long after an onset (0.3 s + the reader's hold + its 10 Hz step)
GHOST_AHEAD = 0.05                    # the ghost sits this far (x W) ahead of the dominant's end

# chord tones as fifths above the root (signed lof intervals; spelled exactly)
LOF_IV = {"maj": (4, 1), "min": (-3, 1), "dim": (-3, -6), "aug": (4, 8), "7": (4, 1, -2), "maj7": (4, 1, 5),
          "m7": (-3, 1, -2), "hdim7": (-3, -6, -2), "dim7": (-3, -6, -9), "sus4": (-1, 1), "5": (1,)}
DIM_KIND = ("dim", "hdim7", "dim7")
FN_WORDS = ("tonic", "pre-dominant", "dominant", "chromatic")
DEMO = ((9, "min", 9), (2, "min", 5), (7, "7", 7), (0, "maj", 0), (5, "maj", 5), (11, "dim", 11),
        (4, "min", 4), (9, "min", 9), (2, "min", 2), (7, "maj", 7), (0, "maj", 0))
DEMO_DUR = 1.5

NREC, RW = 64, 24
NDOT, DTW = 256, 6
NSEG, SGW = 8, 4
NP_ = 128
O_REC = NP_
O_DOT = O_REC + NREC * RW
O_SEG = O_DOT + NDOT * DTW
N_AR = O_SEG + NSEG * SGW
DW = 28
# record row: 0 t0  1 t1  2 open  3 s_abs  4 fn  5 numeral glyph  6 figure glyph  7 name glyph  8 n_sat  9-11 sats
#             12 status (0 -, 1 pending, 2 fulfilled, 3 deceptive, 4 unresolved)  13 resolve real t  14 expected s
#             15 next s  16 created real  17 ended real  18 demo  19 pivot glyph  20 conf  21 halo glyph  22-23 spare
# dot row:    t s vel created weight colour(0 tonic, 1 in key, 2 outside)
# key seg:    t_start tonic_abs minor known

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NREC """ + str(NREC) + r"""
#define RW """ + str(RW) + r"""
#define NDOT """ + str(NDOT) + r"""
#define DTW """ + str(DTW) + r"""
#define O_REC """ + str(O_REC) + r"""
#define O_DOT """ + str(O_DOT) + r"""
#define O_SEG """ + str(O_SEG) + r"""
#define NSHARED """ + str(O_DOT) + r"""
#define DW """ + str(DW) + r"""
#define TWO_PI 6.2831853f

__constant__ float FNC[12] = {1.00f, 0.76f, 0.30f,   0.22f, 0.80f, 0.74f,   0.96f, 0.25f, 0.30f,   0.68f, 0.47f, 1.00f};

__device__ __forceinline__ float line_cov(float d, float hw) { return clamp01(hw + 0.5f - d); }
__device__ __forceinline__ float3 f3(float a, float b, float c) { return make_float3(a, b, c); }
__device__ __forceinline__ float3 fnc(int f) { f = min(max(f, 0), 3); return f3(FNC[3 * f], FNC[3 * f + 1], FNC[3 * f + 2]); }
__device__ __forceinline__ void add(float3& c, float3 v, float k) { c.x += v.x * k; c.y += v.y * k; c.z += v.z * k; }

// coverage of baked glyph g with its left edge at x0 and baseline at base, drawn at font size fpx (screen px)
__device__ float text_cov(const unsigned char* __restrict__ A, const float* __restrict__ GT, const int* __restrict__ GO,
                          int g, float px, float py, float x0, float base, float fpx) {
    if (g < 0 || fpx < 1.f) return 0.f;
    const float* q = GT + 8 * g;
    const float s = q[2] / fpx;
    const float u = (px - x0) * s + q[4] - 0.5f, v = (py - base) * s + q[3] - 0.5f;
    const int W = (int)q[0], H = (int)q[1];
    if (u < -1.f || v < -1.f || u > (float)W || v > (float)H) return 0.f;
    const float fu = floorf(u), fv = floorf(v);
    const int xi = (int)fu, yi = (int)fv;
    const float tx = u - fu, ty = v - fv;
    const unsigned char* B = A + GO[g];
    float r = 0.f;
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int xx = xi + (k & 1), yy = yi + (k >> 1);
        if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue;
        r += ((k & 1) ? tx : 1.f - tx) * ((k >> 1) ? ty : 1.f - ty) * (float)B[yy * W + xx];
    }
    return r * (1.f / 255.f);
}
__device__ __forceinline__ float text_w(const float* __restrict__ GT, int g, float fpx) {
    if (g < 0) return 0.f;
    return GT[8 * g] * fpx / GT[8 * g + 2];
}
// distance from p to the smoothstep curve a -> b (8 segments)
__device__ float curve_dist(float px, float py, float ax, float ay, float bx, float by) {
    float best = 1e30f, x0 = ax, y0 = ay;
    #pragma unroll
    for (int k = 1; k <= 8; ++k) {
        const float u = k * 0.125f;
        const float x1 = ax + (bx - ax) * u, y1 = ay + (by - ay) * u * u * (3.f - 2.f * u);
        const float ex = x1 - x0, ey = y1 - y0, wx = px - x0, wy = py - y0;
        const float h = clamp01((wx * ex + wy * ey) / fmaxf(ex * ex + ey * ey, 1e-6f));
        const float qx = wx - ex * h, qy = wy - ey * h;
        best = fminf(best, qx * qx + qy * qy);
        x0 = x1; y0 = y1;
    }
    return sqrtf(best);
}
// distance from p to the quadratic Bezier a, c, b (8 segments)
__device__ float bez_dist(float px, float py, float ax, float ay, float cx, float cy, float bx, float by) {
    float best = 1e30f, x0 = ax, y0 = ay;
    #pragma unroll
    for (int k = 1; k <= 8; ++k) {
        const float u = k * 0.125f, v = 1.f - u;
        const float x1 = v * v * ax + 2.f * u * v * cx + u * u * bx, y1 = v * v * ay + 2.f * u * v * cy + u * u * by;
        const float ex = x1 - x0, ey = y1 - y0, wx = px - x0, wy = py - y0;
        const float h = clamp01((wx * ex + wy * ey) / fmaxf(ex * ex + ey * ey, 1e-6f));
        const float qx = wx - ex * h, qy = wy - ey * h;
        best = fminf(best, qx * qx + qy * qy);
        x0 = x1; y0 = y1;
    }
    return sqrtf(best);
}
// a record's overall visibility (bloom in, ease to 70% after it ends, confidence, the cold-start example)
__device__ __forceinline__ float rec_alpha(const float* r, const float* P) {
    const float real = P[22];
    const float age = real - r[16];
    const float bloom = smooth(0.f, 0.12f, age);
    const float endf = r[2] > 0.5f ? 1.f : 1.f - 0.3f * smooth(0.f, 1.2f, real - r[17]);
    float a = bloom * endf * (0.6f + 0.4f * r[20]);
    if (r[18] > 0.5f) a *= 0.4f * P[32];
    return a;
}

// P: [0..11] palette [12] W [13] H [14] now (view s) [15] px per view s [16] x now [17] x left [18] y floor
//    [19] line spacing px [20] s_view [21] records [22] real clock [23] energy [24] kick [25] gain [26] labels
//    [27] satellites [28] ghost [29] band breath (-1..1) [30] dust phase [31] dust threshold [32] example alpha
//    [33] key segments [34] haze phase [35] drift [36] period [37] ghost breath 0..1 [38] key step half width (s)
//    [39..41] page top [42..44] page bottom [45] lane baseline [46] lane font px [47] panel font px
//    [48] small font px [49] panel numeral [50] its previous [51] crossfade [52] function word [53] previous
//    [54] distance text [55] previous [56] key label [57] previous [58] key crossfade [59] key known now
//    [60] 'example' glyph [61] function now [62] function before [63] first line-name glyph [64] '=' glyph
//    [65] minor now [66] tonic now (abs lof) [67] now-marker alpha [69] the panel numeral's halo
//    [70, 71] the lamplight's centre
extern "C" __global__
void ladder(uchar4* __restrict__ out, const float* __restrict__ A, const unsigned char* __restrict__ atlas,
            const float* __restrict__ GT, const int* __restrict__ GO, const unsigned int seed)
{
    __shared__ float S[NSHARED];
    __shared__ float SG[32];
    __shared__ float D[NREC * DW];
    __shared__ int RL[NREC], LL[NREC], DL[NDOT];
    __shared__ int nRL, nLL, nDL;
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    const int nth = blockDim.x * blockDim.y;
    for (int i = tid; i < NSHARED; i += nth) S[i] = A[i];
    if (tid < 32) SG[tid] = A[O_SEG + tid];
    if (tid == 0) { nRL = 0; nLL = 0; nDL = 0; }
    __syncthreads();
    const float* P = S;
    const float Wf = P[12], Hf = P[13];
    const float now = P[14], pps = fmaxf(P[15], 1e-3f), XN = P[16], XL = P[17], Y0 = P[18], SP = P[19], sv = P[20];
    const int nrec = min((int)P[21], NREC);
    const float bx0 = (float)(blockIdx.x * blockDim.x), bx1 = bx0 + blockDim.x;
    const float by0 = (float)(blockIdx.y * blockDim.y), by1 = by0 + blockDim.y;
    const float lane_top = P[45] - 1.15f * P[46], lane_bot = P[45] + 1.05f * P[46];
    // ---- prologue: every record's place, colour, ghost and label, then block culling ----------------------------
    if (tid < nrec) {
        const float* r = S + O_REC + RW * tid;
        float* d = D + DW * tid;
        const bool open = r[2] > 0.5f;
        const float hh = 0.34f * SP;
        float xa = XN - (now - r[0]) * pps;
        const float xe = open ? XN : XN - (now - r[1]) * pps;          // the record's true end
        float xb = xe;
        const float gap = fminf(0.12f * SP, 0.2f * fmaxf(xb - xa, 0.f));
        xa += gap; xb -= gap;
        if (xb < xa) xb = xa;
        const float y = Y0 - (r[3] - sv) * SP;
        const float a = rec_alpha(r, P);
        const float3 c = fnc((int)r[4]);
        const float age = P[22] - r[16];
        d[0] = xa; d[1] = xb; d[2] = y; d[3] = a; d[4] = c.x; d[5] = c.y; d[6] = c.z;
        d[22] = smooth(0.f, 0.12f, age) * __expf(-age / 0.6f);         // bloom flash of a new chord
        d[23] = hh;
        float ymin = y, ymax = y;
        const int ns = min((int)r[8], 3);
        for (int j = 0; j < ns; ++j) {
            const float ys = Y0 - (r[9 + j] - sv) * SP;
            ymin = fminf(ymin, ys); ymax = fmaxf(ymax, ys);
        }
        float xmax = xb;
        // the next record: the fundamental-bass connector, the label clip, a deceptive arc's end
        float nx = 1e9f, ny = y, nxl = Wf + 1e4f, na = 0.f;
        if (tid + 1 < nrec) {
            const float* rn = r + RW;
            const float nxa = XN - (now - rn[0]) * pps;
            const bool nopen = rn[2] > 0.5f;
            const float nxe = nopen ? XN : XN - (now - rn[1]) * pps;
            const float ngap = fminf(0.12f * SP, 0.2f * fmaxf(nxe - nxa, 0.f));
            nx = nxa + ngap + hh; ny = Y0 - (rn[3] - sv) * SP;
            nxl = fmaxf(nxa, XL + 0.006f * Wf);
            na = rec_alpha(rn, P);
            const float gapt = rn[0] - (open ? now : r[1]);
            d[14] = fminf(a, na) * (1.f - smooth(1.5f, 4.f, gapt)) * ((r[18] > 0.5f) == (rn[18] > 0.5f) ? 1.f : 0.f);
            xmax = fmaxf(xmax, nx);
            ymin = fminf(ymin, ny); ymax = fmaxf(ymax, ny);
        } else {
            d[14] = 0.f;
        }
        d[12] = nx; d[13] = ny;
        // ghost: where a dominant wants to go
        const int st = (int)r[12];
        float ring = 0.f, fill = 0.f, arc = 0.f, pull = 0.f;
        const float gx = xe + GHOST_AHEAD_W * Wf, gy = Y0 - (r[14] - sv) * SP;
        if (st >= 1 && P[28] > 0.f) {
            const float since = P[22] - r[13];
            const float demo = r[18] > 0.5f ? 0.4f * P[32] : 1.f;
            const float bl = smooth(0.f, 0.25f, age);
            if (st == 1) {
                ring = (0.32f + 0.2f * P[37]) * bl;
                pull = (open || r[18] > 0.5f) ? (0.6f + 0.4f * P[37]) * bl : 0.f;
            } else if (st == 2) {
                ring = 0.5f - 0.2f * smooth(0.3f, 3.f, since);
                fill = smooth(0.f, 0.3f, since) * (0.8f - 0.4f * smooth(0.3f, 4.f, since));
            } else {
                ring = 0.45f * (1.f - smooth(0.f, 0.8f, since));
                if (st == 3) arc = smooth(0.f, 0.3f, since) * (0.55f - 0.3f * smooth(1.f, 5.f, since));
            }
            const float k = P[28] * demo * (r[18] > 0.5f ? 1.f : 1.f - 0.3f * smooth(0.f, 1.2f, P[22] - r[17]) * (st == 1 ? 1.f : 0.f));
            ring *= k; fill *= k; arc *= k; pull *= k;
            xmax = fmaxf(xmax, gx + SP);
            ymin = fminf(ymin, gy); ymax = fmaxf(ymax, gy);
            if (arc > 0.f) { ymin = fminf(ymin, ny); ymax = fmaxf(ymax, ny); xmax = fmaxf(xmax, gx + 0.6f * fabsf(gy - ny) + SP); }
            if (pull > 0.f) { xmax = Wf; ymin = fminf(ymin, gy - 3.5f * SP); ymax = fmaxf(ymax, gy + 3.5f * SP); }
        }
        d[7] = gx; d[8] = gy; d[9] = ring; d[10] = fill; d[11] = arc; d[21] = pull;
        // lane label: sticky at the left edge until the next label pushes it out
        const float xl = fmaxf(xa - gap, XL + 0.006f * Wf);
        const float lw = text_w(GT, (int)r[5], P[46]) + text_w(GT, (int)r[6], P[46]);
        d[15] = xl; d[16] = nxl - 0.008f * Wf;
        // a label that cannot mostly fit before the next one fades out whole (no broken glyphs)
        const float fit = smooth(0.62f, 0.9f, (d[16] - xl) / fmaxf(lw, 1.f));
        d[24] = a * P[26] * smooth(XL + 0.004f * Wf, XL + 0.04f * Wf, xe) * fit;
        d[25] = text_w(GT, (int)r[5], P[46]);
        // bounding boxes
        const float m = 1.7f * SP;
        d[17] = xa - m; d[18] = xmax + m; d[19] = ymin - m; d[20] = ymax + m;
        if (d[3] > 0.002f && d[17] < bx1 && d[18] > bx0 && d[19] < by1 && d[20] > by0 && d[18] > XL - 0.02f * Wf) {
            const int k = atomicAdd(&nRL, 1); RL[k] = tid;
        }
        const float lx1 = fminf(xl + lw + 4.f, d[16]) + 2.f;
        const float lbot = r[19] >= 0.f ? lane_bot + 0.75f * P[46] : lane_bot;
        if (d[24] > 0.002f && xl - 2.f < bx1 && lx1 > bx0 && lane_top < by1 && lbot > by0) {
            const int k = atomicAdd(&nLL, 1); LL[k] = tid;
        }
    }
    {   // dots
        const float* q = A + O_DOT + DTW * tid;
        if (tid < NDOT && q[3] > -1e8f) {
            const float x = XN - (now - q[0]) * pps, y = Y0 - (q[1] - sv) * SP;
            const float m = 0.9f * SP;
            if (x + m > bx0 && x - m < bx1 && y + m > by0 && y - m < by1 && x > XL - m) {
                const int k = atomicAdd(&nDL, 1); DL[k] = tid;
            }
        }
    }
    __syncthreads();

    const int x = blockIdx.x * blockDim.x + threadIdx.x, yy = blockIdx.y * blockDim.y + threadIdx.y;
    const int W = (int)Wf, H = (int)Hf;
    if (x >= W || yy >= H) return;
    const float px = x + 0.5f, py = yy + 0.5f;
    const float sc = Hf / 1080.f;
    const float e = P[23], kick = P[24], real = P[22];
    const float labels = P[26];

    // ---- the key in force at this pixel's time (steps at modulations), the band, the ladder ----------------------
    const float tpx = fminf(now - (XN - px) / pps, now);
    const float tf = smooth(XL - 0.004f * Wf, XL + 0.03f * Wf, px);          // time content fades in by the panel
    const int nseg = min((int)P[33], 8);
    float tonic_t = SG[1], minor_t = SG[2], known_t = SG[3];
    for (int k = 1; k < nseg; ++k) {
        const float w = smooth(SG[4 * k] - P[38], SG[4 * k] + P[38], tpx);
        tonic_t += (SG[4 * k + 1] - SG[4 * k - 3]) * w;
        minor_t += (SG[4 * k + 2] - SG[4 * k - 2]) * w;
        known_t += (SG[4 * k + 3] - SG[4 * k - 1]) * w;
    }
    const float tonic_d = P[66] + (tonic_t - P[66]) * tf;
    const float minor_d = P[65] + (minor_t - P[65]) * tf;
    const float known_d = P[59] + (known_t - P[59]) * tf;
    const float s_px = sv + (Y0 - py) / SP;
    const float vf = smooth(0.27f * Hf, 0.34f * Hf, py) * (1.f - smooth(0.93f * Hf, 0.995f * Hf, py));
    const float fut = px > XN ? 0.7f : 1.f;

    // page: a palette-dark vertical gradient, a slow haze and a fixed paper grain
    const float yn = py / Hf;
    float3 col = f3(P[39] + (P[42] - P[39]) * yn, P[40] + (P[43] - P[40]) * yn, P[41] + (P[44] - P[41]) * yn);
    const float3 tint = pal(P[35] + 0.35f * P[36], P);
    const float3 tintw = f3(0.45f + 0.55f * tint.x, 0.45f + 0.55f * tint.y, 0.45f + 0.55f * tint.z);
    const float tmx = 1.f / fmaxf(fmaxf(tint.x, fmaxf(tint.y, tint.z)), 0.2f);   // the palette's hue at full value,
    const float3 lampc = f3(0.5f * tint.x * tmx + 0.5f, 0.5f * tint.y * tmx + 0.38f, 0.5f * tint.z * tmx + 0.25f);  // warmed
    {
        const float hz = vnoise(px / (260.f * sc) + P[34], py / (200.f * sc) - 0.6f * P[34], seed + 11u);
        const float hz2 = vnoise(px / (90.f * sc) - 1.7f * P[34], py / (90.f * sc), seed + 23u);
        const float k = (0.06f + 0.05f * e) * hz * hz * (0.5f + 0.5f * hz2);
        add(col, lampc, 0.6f * k);
        // a soft pool of lamplight wandering slowly over the page (idle motion)
        const float lx = px - P[70], ly = (py - P[71]) * 1.35f;
        add(col, lampc, (0.040f + 0.025f * e) * __expf(-(lx * lx + ly * ly) / (2.f * 0.26f * Wf * 0.26f * Wf)));
        const float g = hash1((unsigned)x, (unsigned)yy, seed) - 0.5f;
        col.x += 0.010f * g; col.y += 0.009f * g; col.z += 0.008f * g;
    }
    // the key band
    const float rel = s_px - tonic_d;
    const float lo = -1.5f - 3.f * minor_d, hi = 5.5f - 3.f * minor_d;
    const float inb = smooth(lo - 0.35f, lo + 0.35f, rel) * (1.f - smooth(hi - 0.35f, hi + 0.35f, rel));
    {
        const float bk = 0.10f * (0.75f + 0.5f * e) * (1.f + (0.03f + 0.05f * e) * P[29]) * (1.f + 0.25f * kick)
                         * (0.45f + 0.55f * known_d) * vf;
        add(col, tint, inb * bk * (1.f + 0.35f * kick));
        const float ed = fminf(fabsf(rel - lo), fabsf(rel - hi)) * SP;
        add(col, tintw, 0.10f * __expf(-ed / (2.5f * sc)) * vf * (0.4f + 0.6f * known_d) * (1.f + 0.25f * kick));
        if (minor_d > 0.01f) {                                                   // raised 6th and 7th: dashed
            const float dash = smooth(0.35f, 0.45f, fabsf(fmodf(px / (18.f * sc), 1.f) - 0.5f) * 2.f);
            const float dd = fminf(fabsf(rel - 3.f), fabsf(rel - 5.f)) * SP;
            add(col, tintw, line_cov(dd, 1.0f * sc) * 0.5f * minor_d * (1.f - dash) * vf);
        }
    }
    // ruled lines (one per fifth), then the floor (tonic), the V ledge and the IV ledge
    const float kf = floorf(s_px + 0.5f);
    const float dl = fabsf(s_px - kf) * SP;
    const float xs = smooth(0.152f * Wf, 0.168f * Wf, px);
    const float3 lt = pal(P[35] + P[36] * (0.3f + 0.045f * (kf - P[66])), P);
    {
        const float la = (0.10f + 0.08f * inb) * vf * xs * fut;
        add(col, f3(0.35f + 0.65f * lt.x, 0.35f + 0.65f * lt.y, 0.35f + 0.65f * lt.z), la * line_cov(dl, 0.5f * sc));
        const float kk = (0.5f + 0.5f * known_d) * (1.f + 0.45f * kick) * vf;
        const float df = fabsf(rel) * SP, dv = fabsf(rel - 1.f) * SP, d4 = fabsf(rel + 1.f) * SP;
        add(col, fnc(0), kk * (0.95f * line_cov(df, 1.5f * sc) + 0.22f * __expf(-df / (5.f * sc))));
        add(col, fnc(2), kk * (0.62f * line_cov(dv, 0.9f * sc) + 0.10f * __expf(-dv / (4.f * sc))));
        add(col, fnc(1), kk * (0.58f * line_cov(d4, 0.9f * sc) + 0.10f * __expf(-d4 / (4.f * sc))));
    }
    // dust drifting along the lines
    if (dl < 7.f * sc && px > XL) {
        const int k = (int)kf;
        const float hk = hash1((unsigned)(k + 4096), 7u, seed);
        const float vk = (0.35f + 0.9f * hk) * ((k & 1) ? 1.f : -1.f);
        const float cw = 90.f * sc;
        const float u = px / cw + P[30] * vk + 13.f * hk;
        const float ci = floorf(u);
        const unsigned int cu = (unsigned)((int)ci + 1000000);
        if (hash1((unsigned)(k + 4096), cu, seed + 3u) > P[31]) {
            const float f = 0.2f + 0.6f * hash1((unsigned)(k + 4096), cu, seed + 5u);
            const float dx = (u - ci - f) * cw;
            const float tw = 0.6f + 0.4f * __sinf(TWO_PI * (hash1(cu, (unsigned)(k + 4096), seed + 9u) + 0.25f * P[30]));
            const float m = __expf(-(dx * dx + dl * dl) / (2.f * 3.2f * sc * sc)) * 0.6f * tw * (1.f + 0.4f * kick);
            add(col, f3(0.75f + 0.25f * lt.x, 0.72f + 0.25f * lt.y, 0.65f + 0.25f * lt.z), m * vf * fut);
        }
    }
    // the now line
    {
        const float dn = fabsf(px - XN);
        add(col, f3(0.95f, 0.82f, 0.62f), P[67] * vf * (0.20f * line_cov(dn, 0.5f * sc) + 0.05f * __expf(-dn / (10.f * sc))));
    }

    // chords and dots fade out above the ladder (they never reach into the numeral lane) and at the bottom edge
    const float tfr = tf * smooth(0.255f * Hf, 0.315f * Hf, py) * (1.f - smooth(0.95f * Hf, 1.0f * Hf, py));
    // ---- the chords: pills, chord tones, the fundamental bass, ghosts -------------------------------------------
    const float gsig = 0.36f * SP * (0.8f + 0.5f * e) * (1.f + 0.3f * kick);
    const float gk = (0.7f + 0.6f * e) * (1.f + 0.9f * kick);
    const int nr = nRL;
    for (int ii = 0; ii < nr; ++ii) {
        const int i = RL[ii];
        const float* d = D + DW * i;
        if (px < d[17] || px > d[18] || py < d[19] || py > d[20]) continue;
        const float* r = S + O_REC + RW * i;
        const float a = d[3] * tfr;
        const float3 c = f3(d[4], d[5], d[6]);
        const float hh = d[23];
        // the root pill
        {
            const float cx = fminf(fmaxf(px, d[0] + hh), fmaxf(d[1] - hh, d[0] + hh));
            const float dx = px - cx, dy = py - d[2];
            const float dd = sqrtf(dx * dx + dy * dy) - hh;
            const float core = clamp01(0.5f - dd);
            const float g = __expf(-fmaxf(dd, 0.f) / gsig);
            float k1 = core * (0.80f + 0.3f * clamp01(0.5f - 0.5f * dy / hh));
            const int gn = (int)r[7];
            if (gn >= 0 && core > 0.f && labels > 0.f) {                   // the chord's name, inked into it
                const float fpx = 1.5f * hh;
                const float ink = text_cov(atlas, GT, GO, gn, px, py, d[0] + 0.7f * hh, d[2] + 0.37f * fpx, fpx)
                                  * (1.f - smooth(d[1] - 1.1f * hh, d[1] - 0.35f * hh, px)) * labels;
                k1 *= 1.f - 0.86f * ink;
            }
            const float k2 = g * (0.30f + 0.45f * d[22]) * gk;
            add(col, c, a * (k1 + k2));
            add(col, f3(1.f, 0.95f, 0.85f), a * 0.35f * d[22] * core);
        }
        // the chord's other tones: smaller, fainter, on their own lines, joined by a stem
        const int ns = min((int)r[8], 3);
        if (P[27] > 0.f && ns > 0) {
            const float hs = 0.68f * hh;
            const float xs0 = d[0] + hh, xs1 = fmaxf(d[1] - 0.2f * hh, xs0 + 1.f);
            const float3 cs = f3(0.7f * c.x + 0.3f, 0.7f * c.y + 0.3f, 0.7f * c.z + 0.3f);
            float ylo = d[2], yhi = d[2];
            for (int j = 0; j < ns; ++j) {
                const float ys = Y0 - (r[9 + j] - sv) * SP;
                ylo = fminf(ylo, ys); yhi = fmaxf(yhi, ys);
                const float cx = fminf(fmaxf(px, xs0 + hs), fmaxf(xs1 - hs, xs0 + hs));
                const float dx = px - cx, dy = py - ys;
                const float dd = sqrtf(dx * dx + dy * dy) - hs;
                const float k = clamp01(0.5f - dd) * 0.55f + __expf(-fmaxf(dd, 0.f) / (0.7f * gsig)) * 0.22f * gk;
                add(col, cs, a * P[27] * k);
            }
            if (py >= ylo && py <= yhi) {
                const float ds = fabsf(px - xs0);
                add(col, cs, a * P[27] * (0.35f * line_cov(ds, 0.5f * sc) + 0.08f * __expf(-ds / (3.f * sc))));
            }
        }
        // the fundamental bass: a curve from this root to the next
        if (d[14] > 0.f) {
            const float ax = d[1] - hh, ay = d[2], bx = d[12], by = d[13];
            if (px > fminf(ax, bx) - SP && px < fmaxf(ax, bx) + SP) {
                const float dc = curve_dist(px, py, ax, ay, bx, by);
                const float k = d[14] * tfr * (0.75f * line_cov(dc, 1.1f * sc) + 0.30f * __expf(-dc / (0.16f * SP)) * gk);
                add(col, f3(1.f, 0.86f, 0.62f), k);
            }
        }
        // ghost: a hollow ring where the dominant wants to go; gold when it goes there, an arc when it deceives
        if (d[9] > 0.f || d[10] > 0.f || d[11] > 0.f || d[21] > 0.f) {
            const float gx = d[7], gy = d[8], rg = 0.55f * SP;
            const float ux = px - gx, uy = py - gy;
            const float dg = sqrtf(ux * ux + uy * uy);
            const float dr = fabsf(dg - rg);
            const int st = (int)r[12];
            const float3 rc = st == 2 ? fnc(0) : f3(1.f, 0.9f, 0.78f);
            add(col, rc, tfr * d[9] * (0.9f * line_cov(dr, 1.0f * sc) + 0.35f * __expf(-dr / (3.f * sc))));
            if (d[10] > 0.f) add(col, fnc(0), tfr * d[10] * (clamp01(rg - dg + 0.5f) * 0.8f + 0.3f * __expf(-fmaxf(dg - rg, 0.f) / (0.3f * SP))));
            if (d[11] > 0.f) {
                const float bx = gx, by = d[13];
                const float cxx = gx + 0.55f * fabsf(by - gy) + 0.5f * SP, cyy = 0.5f * (gy + by);
                const float da = bez_dist(px, py, gx + rg, gy, cxx, cyy, bx + 0.5f * hh, by);
                add(col, f3(0.82f, 0.80f, 1.f), tfr * d[11] * (0.6f * line_cov(da, 0.7f * sc) + 0.2f * __expf(-da / (4.f * sc))));
            }
            if (d[21] > 0.f && px > gx - SP) {                              // the pull: a warm wash ahead of now
                const float fx = smooth(gx - SP, gx + 0.5f * SP, px) * (1.f - 0.6f * smooth(gx, Wf, px));
                const float k = 0.09f * __expf(-uy * uy / (2.f * 0.30f * SP * 0.30f * SP))
                              + 0.22f * __expf(-uy * uy / (2.f * 1.9f * SP * 1.9f * SP));
                add(col, f3(1.f, 0.82f, 0.55f), d[21] * k * fx * (0.7f + 0.6f * e));
            }
        }
    }
    // ---- melody dots ---------------------------------------------------------------------------------------------
    const int nd = nDL;
    for (int ii = 0; ii < nd; ++ii) {
        const float* q = A + O_DOT + DTW * DL[ii];
        const float dx = px - (XN - (now - q[0]) * pps), dy = py - (Y0 - (q[1] - sv) * SP);
        const float rr = (0.11f + 0.09f * q[2]) * SP;
        const float dd = sqrtf(dx * dx + dy * dy) - rr;
        const float age = real - q[3];
        const float k = smooth(0.f, 0.12f, age) * q[4] * tfr * (clamp01(0.5f - dd) * 0.9f + 0.35f * __expf(-fmaxf(dd, 0.f) / (0.25f * SP)) * gk);
        const int cc = (int)q[5];
        add(col, cc == 0 ? fnc(0) : (cc == 1 ? f3(0.96f, 0.90f, 0.80f) : f3(0.78f, 0.66f, 1.f)), k);
    }
    // ---- the numeral lane --------------------------------------------------------------------------------------
    if (labels > 0.f && py > lane_top - 2.f) {
        const int nl = nLL;
        for (int ii = 0; ii < nl; ++ii) {
            const int i = LL[ii];
            const float* d = D + DW * i;
            const float* r = S + O_REC + RW * i;
            const float xl = d[15];
            if (px < xl - 2.f || px > d[16] + 2.f) continue;
            const float fade = 1.f - smooth(d[16] - 0.022f * Wf, d[16], px);
            const float fp = P[46], base = P[45];
            float cov = text_cov(atlas, GT, GO, (int)r[5], px, py, xl, base, fp)
                      + text_cov(atlas, GT, GO, (int)r[6], px, py, xl + d[25], base, fp);
            // a soft bloom around the numeral (a blurred copy baked into the atlas), so it carries across a room
            const float halo = text_cov(atlas, GT, GO, (int)r[21], px, py, xl, base, fp);
            if (r[19] >= 0.f) {                                               // the pivot: "= its reading in the new key"
                const float f2 = 0.62f * fp, b2 = base + 0.78f * fp;
                const float we = text_w(GT, (int)P[64], f2);
                cov += text_cov(atlas, GT, GO, (int)P[64], px, py, xl, b2, f2)
                     + text_cov(atlas, GT, GO, (int)r[19], px, py, xl + we + 0.1f * f2, b2, f2);
            }
            const float3 c = f3(d[4] * 0.62f + 0.38f, d[5] * 0.62f + 0.38f, d[6] * 0.62f + 0.38f);
            add(col, c, (1.35f * fminf(cov, 1.f) + 0.30f * halo) * d[24] * fade);
        }
    }
    // ---- the panel: current numeral, its function, its distance from home, the key; the lines' names ----------------
    if (labels > 0.f && px < 0.16f * Wf) {
        const float x0 = 0.024f * Wf;
        const float fp = P[47], fs = P[48];
        const float bnum = 0.192f * Hf;
        const float cf = P[51];
        const float3 c1 = fnc((int)P[61]), c0 = fnc((int)P[62]);
        const float colw = 0.128f * Wf;
        const float fp1 = fp * fminf(1.f, colw / fmaxf(text_w(GT, (int)P[49], fp), 1.f));
        const float fp0 = fp * fminf(1.f, colw / fmaxf(text_w(GT, (int)P[50], fp), 1.f));
        const float n1 = text_cov(atlas, GT, GO, (int)P[49], px, py, x0, bnum, fp1) * cf;
        const float n0 = text_cov(atlas, GT, GO, (int)P[50], px, py, x0, bnum, fp0) * (1.f - cf);
        const float h1 = text_cov(atlas, GT, GO, (int)P[69], px, py, x0, bnum, fp1) * cf;   // its baked bloom
        add(col, f3(0.7f * c1.x + 0.3f, 0.7f * c1.y + 0.3f, 0.7f * c1.z + 0.3f), labels * (1.2f * n1 + 0.30f * h1));
        add(col, f3(0.7f * c0.x + 0.3f, 0.7f * c0.y + 0.3f, 0.7f * c0.z + 0.3f), 1.2f * labels * n0);
        const float fw = 0.62f * fs;
        add(col, c1, labels * cf * text_cov(atlas, GT, GO, (int)P[52], px, py, x0 + 0.04f * fs, bnum + 1.2f * fw, fw));
        add(col, c0, labels * (1.f - cf) * text_cov(atlas, GT, GO, (int)P[53], px, py, x0 + 0.04f * fs, bnum + 1.2f * fw, fw));
        const float bd = bnum + 2.35f * fw;
        add(col, f3(0.80f, 0.76f, 0.70f), labels * 0.85f * (cf * text_cov(atlas, GT, GO, (int)P[54], px, py, x0 + 0.04f * fs, bd, fw)
                                              + (1.f - cf) * text_cov(atlas, GT, GO, (int)P[55], px, py, x0 + 0.04f * fs, bd, fw)));
        if (P[32] > 0.f)
            add(col, f3(0.75f, 0.70f, 0.62f), labels * 0.55f * P[32] * text_cov(atlas, GT, GO, (int)P[60], px, py, x0 + 0.04f * fs, bd + 1.3f * fw, fw));
        // the key's name on the floor
        const float yk = Y0 - (P[66] - sv) * SP - 0.32f * SP;
        const float kc = P[58];
        const float kl = text_cov(atlas, GT, GO, (int)P[56], px, py, 0.02f * Wf, yk, fs) * kc
                       + text_cov(atlas, GT, GO, (int)P[57], px, py, 0.02f * Wf, yk, fs) * (1.f - kc);
        add(col, fnc(0), labels * (0.55f + 0.45f * P[59]) * 1.1f * kl);
        // each line's name, right-aligned at the start of the lines
        if (px > 0.09f * Wf && P[63] >= 0.f) {
            const int k = (int)kf;
            const int g = (int)P[63] + min(max(k + 20, 0), 40);
            const float fl = 0.66f * fs;
            const float wn = text_w(GT, g, fl);
            const float yl = Y0 - (kf - sv) * SP;
            const float cv = text_cov(atlas, GT, GO, g, px, py, 0.147f * Wf - wn, yl + 0.36f * fl, fl);
            if (cv > 0.f) {
                const int kr = k - (int)rintf(P[66]);
                const float3 nc = kr == 0 ? fnc(0) : (kr == 1 ? fnc(2) : (kr == -1 ? fnc(1)
                                : f3(0.45f + 0.4f * lt.x, 0.45f + 0.4f * lt.y, 0.45f + 0.4f * lt.z)));
                add(col, nc, cv * labels * vf * (kr >= -1 && kr <= 1 ? 1.0f : 0.7f));
            }
        }
    }

    const float gn = 255.f * P[25];
    const float dth = (hash_u((unsigned int)(yy * W + x) ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    out[yy * W + x] = pack(make_float4(gn * (1.f - __expf(-1.25f * fmaxf(col.x, 0.f))) + dth,
                                       gn * (1.f - __expf(-1.25f * fmaxf(col.y, 0.f))) + dth,
                                       gn * (1.f - __expf(-1.25f * fmaxf(col.z, 0.f))) + dth, 255.f));
}
""".replace("GHOST_AHEAD_W", repr(float(GHOST_AHEAD)) + "f")

_OPTS = ("--std=c++14", "--use_fast_math")
_RING = 16
_logged: set = set()


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def _pal(t: float, c) -> tuple:
    tp = 2.0 * math.pi
    return tuple(min(max(c[i] + c[3 + i] * math.cos(tp * (c[6 + i] * t + c[9 + i])), 0.0), 1.0) for i in range(3))


def name_u(k: int) -> str:
    """A line-of-fifths index as a note name with real accidentals: 0 C, 1 G, -2 B♭, 6 F♯, -9 B♭♭."""
    k = int(k)
    letter = "FCGDAEB"[(k + 1) % 7]
    acc = (k + 1) // 7
    return letter + ("♯" * acc if acc > 0 else "♭" * (-acc))


_NUM_RE = re.compile(r"^([b#]?)([IViv]+)([^/]*)(?:/([b#]?)([IViv]+))?$")


def strip7(numeral: str) -> str:
    """A seventh chord's numeral without its '7', for use with the inversion figures (V7 + 6/5 -> V 6/5)."""
    m = _NUM_RE.match(numeral)
    if not m:
        return numeral
    acc, letters, mark, tacc, target = m.groups()
    if mark.endswith("7"):
        mark = mark[:-1]
    return acc + letters + mark + (f"/{tacc or ''}{target}" if target else "")


def numeral_runs(txt: str) -> list:
    """Typeset a Roman numeral: accidental, the numeral (serif), quality marks raised small, then '/target'."""
    m = _NUM_RE.match(txt)
    if not m:
        return [(txt, SERIF, 1.0, 0.0)]
    acc, letters, mark, tacc, target = m.groups()
    runs = []
    if acc:
        runs.append(("♭" if acc == "b" else "♯", SANS, 0.8, -0.06))
    runs.append((letters, SERIF, 1.0, 0.0))
    for ch in mark:
        runs.append((ch, SANS if ch in "°ø+" else SERIF, 0.52, -0.40))
    if target:
        runs.append(("/", SERIF, 0.82, 0.0))
        if tacc:
            runs.append(("♭" if tacc == "b" else "♯", SANS, 0.6, -0.02))
        runs.append((target, SERIF, 0.82, 0.0))
    return runs


_ATLAS: dict = {}


def bake_atlas(h: int):
    """Every string the scene draws, baked once per internal height (cached).  Returns (atlas uint8[N], table
    float32[NG, 4] (w, h, font px, baseline), offsets int32[NG], ids dict)."""
    hit = _ATLAS.get(h)
    if hit is not None:
        return hit
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    fonts: dict = {}

    def font(path, px):
        key = (path, px)
        f = fonts.get(key)
        if f is None:
            f = fonts[key] = ImageFont.truetype(path, max(6, int(round(px))))
        return f

    chunks, table, offs, ids = [], [], [], {}
    off = [0]

    def put(key, runs, fpx, stacked=False, blur=0.0):
        if key in ids:
            return
        fpx = max(6.0, float(fpx))
        widths = [font(p, fpx * s).getlength(t) for t, p, s, _ in runs]
        tw = max(widths) if stacked else sum(widths)
        pad = int(math.ceil(3.0 * blur))
        cw, ch = int(math.ceil(tw)) + 6 + 2 * pad, int(math.ceil(1.34 * fpx)) + 2 + 2 * pad
        base = int(round(1.02 * fpx)) + pad
        img = Image.new("L", (cw, ch), 0)
        dr = ImageDraw.Draw(img)
        x = 3.0 + pad
        for (t, p, s, dy), w in zip(runs, widths):
            dr.text((x, base + dy * fpx), t, fill=255, font=font(p, fpx * s), anchor="ls")
            if not stacked:
                x += w
        if blur > 0.0:                                   # a soft halo, normalised to full strength
            img = img.filter(ImageFilter.GaussianBlur(blur))
            a = np.asarray(img, np.float32)
            a = np.clip(a * (255.0 / max(float(a.max()), 1.0)), 0, 255).astype(np.uint8)
            a = a.ravel()
        else:
            a = np.asarray(img, np.uint8).ravel()
        ids[key] = len(table)
        table.append((cw, ch, fpx, base, pad, 0.0, 0.0, 0.0))
        offs.append(off[0])
        chunks.append(a)
        off[0] += a.size

    fn_ = PANEL_F * h
    for k in (0, 12):
        for r in range(12):
            for q in T.QUALITIES:
                s = T.roman(r, q, k)
                variants = [s] + ([strip7(s)] if len(T.QUALITY_INTERVALS[q]) == 4 else [])
                for v in variants:
                    put(("n", v), numeral_runs(v), fn_)
                    put(("h", v), numeral_runs(v), fn_, blur=0.07 * fn_)
    put(("f", "6"), [("6", SERIF, 0.5, -0.40)], fn_)
    for fig in ("6/4", "6/5", "4/3", "4/2"):
        a_, b_ = fig.split("/")
        put(("f", fig), [(a_, SERIF, 0.46, -0.42), (b_, SERIF, 0.46, 0.04)], fn_, stacked=True)
    put(("n", "="), [("=", SERIF, 0.8, -0.05)], fn_)
    fs = SMALL_F * h
    for k in range(-12, 13):
        put(("k", k, 0), [(name_u(k) + " major", SANS, 1.0, 0.0)], fs)
        put(("k", k, 1), [(name_u(k).lower() + " minor", SANS, 1.0, 0.0)], fs)
    for k in range(K_NAME0, K_NAME0 + N_NAMES):
        put(("l", k), [(name_u(k), SANS, 1.0, 0.0)], fs)
    put(("d", 0), [("home", SANS, 1.0, 0.0)], fs)
    for n in range(1, 13):
        for sg in (1, -1):
            txt = f"{'+' if sg > 0 else '−'}{n} fifth{'s' if n > 1 else ''} from home"
            put(("d", sg * n), [(txt, SANS, 1.0, 0.0)], fs)
    for i, w in enumerate(FN_WORDS):
        put(("w", i), [(w, SANS, 1.0, 0.0)], fs)
    put(("x",), [("example", SANS, 1.0, 0.0)], fs)
    fc = NAME_F * h
    for k in range(K_CHORD0, K_CHORD0 + N_CHORD):
        for q in T.QUALITIES:
            put(("c", k, q), [(name_u(k) + T.QUALITY_SUFFIX[q], SANS_B, 1.0, 0.0)], fc)
    atlas = np.concatenate(chunks) if chunks else np.zeros(1, np.uint8)
    out = (atlas, np.asarray(table, np.float32).reshape(-1, 8), np.asarray(offs, np.int32), ids)
    if len(_ATLAS) > 4:
        _ATLAS.clear()
    _ATLAS[h] = out
    return out


class FifthsLadder(Source):
    """Host: chord reader at 10 Hz, records / dots / key segments kept in one persistent arena, copied per frame
    from a pinned ring slot; one kernel + present."""
    name = "fifths_ladder"
    label = "Fifths ladder"
    description = ("A live harmonic analysis: each chord is drawn at its height on the line of fifths above the gold "
                   "tonic floor, with its Roman numeral, so cadences visibly step home and a dominant shows a ghost "
                   "of where it wants to resolve.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ember", render_scale=0.5, period=1.0, drift=0.0),
            "window_s": num(8.0, 40.0, 14.0, "Time window", unit="s", basic=True),
            "labels": num(0.0, 1.0, 1.0, "Labels"),
            "satellites": num(0.0, 1.0, 0.85, "Chord tones"),
            "ghost": num(0.0, 1.0, 0.9, "Expectation ghosts"),
            "spacing": num(0.03, 0.06, 0.042, "Line spacing"),
        }

    # -- setup ------------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.k = raw_module(_SRC, _OPTS).get_function("ladder")
        try:
            atlas, table, offs, self.gid = bake_atlas(self.h)
        except Exception as exc:                          # degrade: no text
            _log_once("font", "fifths_ladder: cannot bake text (%s); drawing none", exc)
            atlas, table, offs, self.gid = np.zeros(1, np.uint8), np.zeros((1, 8), np.float32), np.zeros(1, np.int32), {}
        self.atlas = cp.asarray(atlas)
        self.gt = cp.asarray(table.ravel())
        self.go = cp.asarray(offs)
        # the persistent arena (records, dots, key segments) and the pinned upload ring
        self.A = np.zeros(N_AR, np.float32)
        self.REC = self.A[O_REC:O_DOT].reshape(NREC, RW)
        self.DOT = self.A[O_DOT:O_SEG].reshape(NDOT, DTW)
        self.SEG = self.A[O_SEG:N_AR].reshape(NSEG, SGW)
        self.DOT[:, 0] = -1e9
        self.DOT[:, 3] = -1e9
        self.arena = cp.zeros(N_AR, cp.float32)
        try:
            self._pin = cp.cuda.alloc_pinned_memory(_RING * N_AR * 4)
            host = np.frombuffer(self._pin, np.float32, _RING * N_AR).reshape(_RING, N_AR)
        except Exception as exc:
            _log_once("pin", "fifths_ladder: no pinned memory (%s); uploading from pageable memory", exc)
            self._pin = None
            host = np.zeros((_RING, N_AR), np.float32)
        self._spare = np.zeros(N_AR, np.float32)
        self._host = [host[i] for i in range(_RING)] + [self._spare]
        self._ev = [cp.cuda.Event(block=False, disable_timing=True, interprocess=False) for _ in range(_RING)]
        self._slot = 0
        # music state
        self.pce = np.zeros(12, np.float32)
        self.ease = T.AttackRelease(12, 0.12, 0.4)
        self.keyr = T.KeyResolver(hold_s=0.5)
        self.chords = T.ChordReader(T.QUALITIES, "score", rate_hz=10)
        self._quiet = np.zeros((128, 4), np.float32)
        self._quiet[:, 2] = np.inf
        self._quiet[:, 3] = np.inf
        self.meta: list = []                              # per record row: dict(root, q, key, bass, s, demo)
        self.pan_halo: dict = {}                          # numeral glyph -> its halo glyph
        self.n = 0
        self.ndot = 0
        self.nseg = 0
        self.real = 0.0
        self.tv = 0.0                                     # the view clock (parks after silence)
        self.rate = 1.0
        self.live = False
        self.real_chords = False
        self.live_t = 0.0
        self.live_key = False
        self.cur_key = 0
        self.demo_a = 1.0
        self.demo_i = 0
        self.demo_next = 0.0
        self.last_sound = -1e9
        self.last_onset = -1e9
        self.last_loud_tv = 0.0
        self.quiet_s = 0.0
        self.tonic_abs = 0
        self.minor = 0
        self.known = 1.0
        self.ema = 1.0
        self.sv = 0.0
        self.sv_from, self.sv_to, self.sv_t = 0.0, 0.0, -1e9
        self.minor_e = 0.0
        self.known_e = 1.0
        self.kick = self.breath = self.gbreath = self.dust = self.haze = 0.0
        self.drift = 0.0
        self.lamp = (self.seed % 1000) * 0.0063
        self.pan = [-1, -1, -1e9, 0, 0, -1, -1]          # numeral now, before, changed at, fn now, fn before, dist now, before
        self.key_g = [self._g(("k", 0, 0)), -1, -1e9]
        self._seg_reset(0, 0, 1.0)
        # the cold-start example: already scrolling across the whole window
        self.demo_i = 7                                  # so the chord sounding at t 0 is the V7 (a waiting ghost)
        t = -42.7
        while t <= 0.0:
            self._demo_step(t, -10.0)
            t += DEMO_DUR
        self.demo_next = t

    def _g(self, key) -> int:
        return self.gid.get(key, -1)

    # -- key segments -------------------------------------------------------------------------------------------------
    def _seg_reset(self, tonic: int, minor: int, known: float) -> None:
        self.SEG[:] = 0.0
        self.SEG[0] = (-1e9, tonic, minor, known)
        self.nseg = 1

    def _seg_push(self, t: float, tonic: int, minor: int, known: float) -> None:
        if self.nseg >= NSEG:
            self.SEG[:-1] = self.SEG[1:].copy()
            self.nseg = NSEG - 1
        last = self.SEG[self.nseg - 1]
        t = max(t, float(last[0]) + 0.05)
        self.SEG[self.nseg] = (t, tonic, minor, known)
        self.nseg += 1

    # -- records ------------------------------------------------------------------------------------------------------
    def _height(self, root: int, key: int, tonic_abs: int) -> int:
        s0 = tonic_abs + T.fifths_of(root - key % 12)
        best, bd = s0, abs(s0 - self.ema)
        for c in (s0 - 12, s0 + 12):
            dd = abs(c - self.ema)
            if dd < bd and abs(c - tonic_abs) <= 7:
                best, bd = c, dd
        return best

    def _label(self, i: int) -> None:
        """(Re)compute record i's numeral, function, chord tones, name and expectation from its meta."""
        m = self.meta[i]
        R = self.REC[i]
        root, q, key, s = m["root"], m["q"], m["key"], m["s"]
        fn = T.function_class(root, q, key)
        numeral = T.roman(root, q, key)
        fig = T.inversion(q, root, m["bass"])
        if fig and len(T.QUALITY_INTERVALS[q]) == 4:
            numeral = strip7(numeral)
        R[3] = s
        R[4] = fn
        R[5] = self._g(("n", numeral))
        R[21] = self._g(("h", numeral))
        R[6] = self._g(("f", fig)) if fig else -1
        R[7] = self._g(("c", s, q))
        sats = [s + v for v in LOF_IV[q]][:3]
        R[8] = len(sats)
        R[9:12] = 0.0
        R[9:9 + len(sats)] = sats
        if int(R[12]) in (0, 1):                          # resolved ones keep their outcome
            if fn == T.FN_D:
                R[12] = 1
                R[14] = s - 5 if q in DIM_KIND else s - 1
            else:
                R[12] = 0
                R[14] = s
        m["fn"] = fn
        m["numeral"] = int(R[5])
        self.pan_halo[int(R[5])] = int(R[21])

    def _close(self, t1: float) -> None:
        if self.n and self.REC[self.n - 1, 2] > 0.5:
            R = self.REC[self.n - 1]
            R[1] = max(t1, float(R[0]))
            R[2] = 0.0
            R[17] = self.real

    def _open(self, root: int, q: str, bass: int, conf: float, demo: bool, t: float, created: float,
              key: int, tonic_abs: int) -> None:
        self._close(t)
        s = self._height(root, key, tonic_abs)
        if self.n:                                         # how the last dominant went on
            P_ = self.REC[self.n - 1]
            pm = self.meta[self.n - 1]
            if int(P_[12]) == 1 and bool(P_[18] > 0.5) == demo:
                if pm["q"] in DIM_KIND:
                    st = 2 if (root - pm["root"]) % 12 == 1 else 4
                else:
                    res = T.resolution(pm["root"], root)
                    st = 2 if res == T.RES_FULFILLED else (3 if res == T.RES_DECEPTIVE else 4)
                P_[12] = st
                P_[13] = self.real
                P_[15] = s
        if self.n >= NREC:
            self.REC[:-1] = self.REC[1:].copy()
            del self.meta[0]
            self.n = NREC - 1
        i = self.n
        R = self.REC[i]
        R[:] = 0.0
        R[0] = t
        R[1] = t
        R[2] = 1.0
        R[16] = created
        R[17] = 1e9
        R[18] = 1.0 if demo else 0.0
        R[19] = -1
        R[20] = conf
        self.meta.append({"root": root % 12, "q": q, "key": key, "bass": bass, "s": s, "demo": demo})
        self.n += 1
        self._label(i)
        self.ema = float(s)
        if not demo:
            self.real_chords = True
        if not demo or not self.live:
            self._panel(i)

    def _panel(self, i: int) -> None:
        m = self.meta[i]
        g = int(self.REC[i, 5])
        d = int(round(m["s"] - self.tonic_abs))
        d = max(-12, min(12, d))
        pn = self.pan
        if g != pn[0] or m["fn"] != pn[3]:
            self.pan = [g, pn[0], self.real, m["fn"], pn[3], self._g(("d", d)), pn[5]]
        else:
            pn[5] = self._g(("d", d))

    def _demo_step(self, t: float, created: float) -> None:
        root, q, bass = DEMO[self.demo_i % len(DEMO)]
        self.demo_i += 1
        self._open(root, q, bass, 0.9, True, t, created, 0, 0)

    def _drop_demo(self) -> None:
        keep = [i for i in range(self.n) if self.REC[i, 18] < 0.5]
        rows = self.REC[keep].copy() if keep else None
        self.meta = [self.meta[i] for i in keep]
        self.REC[:] = 0.0
        if keep:
            self.REC[:len(keep)] = rows
        self.n = len(keep)

    # -- keys ------------------------------------------------------------------------------------------------------------
    def _key_change(self, ch) -> None:
        k = ch.to
        base = T.key_tonic_lof(k)
        if ch.first or not self.live_key:
            new = base
        else:
            cands = [c for c in (base - 12, base, base + 12) if abs(c) <= 7] or [base]
            new = min(cands, key=lambda c: abs(c - self.tonic_abs))
        minor = 1 if k >= 12 else 0
        old_key = self.cur_key
        if ch.first or not self.live_key:
            self.live_key = True
            self.tonic_abs, self.minor, self.cur_key = new, minor, k
            self._seg_reset(new, minor, 1.0)
            self.ema = new + 1.0
            for i in range(self.n):                       # the first chords, read before the key was known
                m = self.meta[i]
                if m["demo"]:
                    continue
                m["key"] = k
                m["s"] = self._height(m["root"], k, new)
                self._label(i)
        else:
            # the pivot: the last chord in both keys; the chords after it are read in the new key
            piv = -1
            for i in range(self.n - 1, max(-1, self.n - 9), -1):
                m = self.meta[i]
                if m["demo"] or self.tv - float(self.REC[i, 0]) > 10.0:
                    break
                pcs = T.chord_pcs(m["root"], m["q"])
                if all(T.SCALE_MASK[old_key, p] and T.SCALE_MASK[k, p] for p in pcs):
                    piv = i
                    break
            if piv >= 0:
                R = self.REC[piv]
                t1 = self.tv if R[2] > 0.5 else float(R[1])
                t_seg = 0.5 * (float(R[0]) + t1)
                pm = self.meta[piv]
                second = T.roman(pm["root"], pm["q"], k)
                R[19] = self._g(("n", second))
            else:
                t_seg = self.tv - 0.6
            self.tonic_abs, self.minor, self.cur_key = new, minor, k
            for i in range(piv + 1 if piv >= 0 else self.n - 1, self.n):
                if i < 0:
                    continue
                m = self.meta[i]
                if m["demo"]:
                    continue
                m["key"] = k
                self._label(i)
            self._seg_push(t_seg, new, minor, 1.0)
        self.sv_from, self.sv_to, self.sv_t = self.sv, float(new), self.real
        self.key_g = [self._g(("k", max(-12, min(12, new)), minor)), self.key_g[0], self.real]
        if self.n and not self.meta[-1]["demo"]:
            self._panel(self.n - 1)

    # -- per frame ----------------------------------------------------------------------------------------------------
    def _frame(self):
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        n = getattr(f, "notes", None) if f is not None else None
        if n is None or getattr(n, "shape", None) != (128, 4):
            return None, self._quiet
        return f, n

    def _listen(self, dt: float, f, notes) -> None:
        T.pc_energy(notes, self.pce)
        np.nan_to_num(self.pce, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        peds = getattr(f, "pedals", None) if f is not None else None
        try:
            damper = float(peds[0]) if peds is not None and len(peds) else 0.0
        except Exception:
            damper = 0.0
        self.ease.release_s = 0.8 if damper > 0.5 else 0.4
        e = self.ease.step(self.pce, dt)
        onsets = getattr(f, "onsets", None) or [] if f is not None else []
        top = float(self.pce.max())
        sounding = top > 0.02
        if onsets:
            self.last_onset = self.real
        if sounding or onsets:
            self.last_sound = self.real
        if not self.live and (onsets or sounding):
            self.live = True
            self.live_t = self.real
            self.live_key = False
            self.cur_key = 0
            self._close(self.tv)
            self.ema = 1.0
        if not self.live:
            return
        key_s = getattr(f, "key", None) if f is not None else None
        mode_s = getattr(f, "mode", None) if f is not None else None
        ch = self.keyr.update(self.pce, dt, key_s, mode_s)
        if ch is not None:
            self._key_change(ch)
        bass = T.bass_pitch(notes)
        bass_pc = bass % 12 if bass >= 0 else -1
        # pcs really sounding now (raw levels: a scale's eased tail must not count as a chord; the damper still
        # holds a pedalled arpeggio's notes in LEVEL, so those read as chords)
        n_pcs = int(np.count_nonzero(self.pce > 0.2 * top)) if top > 1e-4 else 0
        cc = getattr(f, "chord_change", None) if f is not None else None
        near = self.real - self.last_onset < ONSET_WINDOW or (isinstance(cc, (int, float)) and cc > 0.12)
        allow = near and n_pcs >= 3
        key = self.cur_key if self.live_key else 0
        c = self.chords
        changed = c.update(e, dt, bass_pc, key if self.live_key else -1, allow)
        open_ = self.n > 0 and self.REC[self.n - 1, 2] > 0.5 and self.REC[self.n - 1, 18] < 0.5
        if changed and c.chord >= 0:
            self._open(c.root, c.quality, bass_pc, c.conf, False, self.tv, self.real, key, self.tonic_abs)
            open_ = True
        elif not open_ and c.chord >= 0 and allow and sounding and c.score >= c.min_score:
            self._open(c.root, c.quality, bass_pc, c.conf, False, self.tv, self.real, key, self.tonic_abs)
            open_ = True
        if sounding:
            self.quiet_s = 0.0
            self.last_loud_tv = self.tv
        else:
            self.quiet_s += dt
        if open_:
            R = self.REC[self.n - 1]
            if self.quiet_s > 0.6:
                self._close(self.last_loud_tv)
            else:
                R[20] = max(float(R[20]) * 0.98, c.conf) if c.sounding else R[20]
                self.ema += (float(R[3]) - self.ema) * T.ema_alpha(dt, 6.0)
        # melody dots
        if onsets:
            weak = n_pcs <= 2
            cur = self.meta[-1] if (self.n and open_) else None
            for on in onsets[:16]:
                try:
                    pitch, vel = int(on[0]), float(on[1])
                except Exception:
                    continue
                if not 0 <= pitch < 128:
                    continue
                pc = pitch % 12
                s = None
                if cur is not None:
                    for v in (0,) + LOF_IV[cur["q"]]:
                        if (cur["root"] + 7 * v) % 12 == pc:
                            s = cur["s"] + v
                            break
                if s is None:
                    ref = self.tonic_abs + (-0.5 if self.minor else 1.5)
                    s = T.spell(pc, ref)
                rel = s - self.tonic_abs
                inside = (-4 <= rel <= 2 or rel in (3, 5)) if self.minor else (-1 <= rel <= 5)
                colour = 0 if rel == 0 else (1 if inside else 2)
                vel = vel if 0.0 <= vel <= 1.0 else 0.6
                self.DOT[self.ndot % NDOT] = (self.tv, s, vel, self.real, 1.0 if weak else 0.22, colour)
                self.ndot += 1

    def render(self, t_local: float, dt: float, out) -> None:
        cp = self.ctx.cp
        p = self.step_params(dt)
        dt = float(dt) if dt is not None and math.isfinite(dt) and dt > 0.0 else 0.0
        dt = min(dt, 1.0)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = min(max(float(p["speed"]), 0.0), 8.0)
        self.real += dt
        self.kick += (pulse - self.kick) * (1.0 - math.exp(-dt / 0.12) if dt > 0 else 0.0)
        self.breath += (0.10 + 0.06 * e) * spd * dt
        self.gbreath += 0.35 * spd * dt
        self.dust += (0.20 + 0.35 * e) * spd * dt
        self.haze += (0.14 + 0.06 * e) * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt
        self.lamp += 0.13 * spd * dt

        f, notes = self._frame()
        self._listen(dt, f, notes)

        # the view clock: time-true while music sounds, parked 10 s after the last sound, eased back in at a note
        if self.live:
            target = 1.0 if self.real - self.last_sound < 10.0 else 0.0
            tau = 0.3 if target > self.rate else 0.8
        else:
            target, tau = spd, 0.3
        self.rate += (target - self.rate) * T.ema_alpha(dt, tau)
        self.tv += self.rate * dt
        if not self.live:
            while self.tv >= self.demo_next:
                self._demo_step(self.demo_next, self.real)
                self.demo_next += DEMO_DUR
        else:
            if self.demo_a > 0.0 and (self.real_chords or self.real - self.live_t > 1.5):
                self.demo_a *= math.exp(-dt / 1.4) if dt > 0 else 1.0
                if self.demo_a < 0.004:
                    self.demo_a = 0.0
                    self._drop_demo()
        # the view eases to the new home over 2 s
        u = T.smootherstep(0.0, 2.0, self.real - self.sv_t)
        self.sv = self.sv_from + (self.sv_to - self.sv_from) * u
        a = T.ema_alpha(dt, 0.6)
        self.minor_e += (self.minor - self.minor_e) * a
        kn = 1.0 if (not self.live or self.live_key) else 0.0
        self.known_e += (kn - self.known_e) * a

        # this frame's host slot
        i = self._slot
        ev = self._ev[i]
        if ev.done:
            self._slot = (i + 1) % _RING
        else:
            ev, i = None, _RING
        P = self.A
        P[:NP_] = 0.0
        coeffs = palette_coeffs(p["palette"])
        period = float(p["palette_period"])
        P[0:12] = coeffs
        W, H = self.w, self.h
        win = min(max(float(p["window_s"]), 4.0), 60.0)
        sp = min(max(float(p["spacing"]), 0.02), 0.08)
        P[12], P[13] = W, H
        P[14] = self.tv
        P[15] = (X_NOW - X_LEFT) * W / win
        P[16], P[17], P[18], P[19], P[20] = X_NOW * W, X_LEFT * W, Y_FLOOR * H, sp * H, self.sv
        P[21] = self.n
        P[22] = self.real
        P[23], P[24], P[25] = e, self.kick, float(p["intensity"])
        P[26] = min(max(float(p["labels"]), 0.0), 1.0)
        P[27] = min(max(float(p["satellites"]), 0.0), 1.0)
        P[28] = min(max(float(p["ghost"]), 0.0), 1.0)
        P[29] = math.sin(2.0 * math.pi * self.breath)
        P[30] = self.dust % 4096.0
        P[31] = 1.0 - (0.16 + 0.30 * e)
        P[32] = self.demo_a
        P[33] = self.nseg
        P[34] = self.haze % 4096.0
        P[35] = self.drift % 64.0
        P[36] = period
        P[37] = 0.5 + 0.5 * math.sin(2.0 * math.pi * self.gbreath)
        P[38] = 0.35
        top = _pal(self.drift + 0.05 * period, coeffs)
        bot = _pal(self.drift + 0.22 * period, coeffs)
        P[39:42] = [0.010 + 0.030 * c for c in top]
        P[42:45] = [0.022 + 0.060 * c for c in bot]
        P[45] = (LANE_Y + 0.36 * LANE_F) * H
        P[46] = LANE_F * H
        P[47] = PANEL_F * H
        P[48] = SMALL_F * H
        pn = self.pan
        P[49], P[50] = pn[0], pn[1]
        P[51] = T.smoothstep(0.0, 0.25, self.real - pn[2])
        P[52], P[53] = self._g(("w", pn[3])) if pn[0] >= 0 else -1, self._g(("w", pn[4])) if pn[1] >= 0 else -1
        P[54], P[55] = pn[5], pn[6]
        P[56], P[57] = self.key_g[0], self.key_g[1]
        P[58] = T.smoothstep(0.0, 0.6, self.real - self.key_g[2])
        P[59] = self.known_e
        P[60] = self._g(("x",))
        P[61], P[62] = pn[3], pn[4]
        P[63] = self._g(("l", K_NAME0))
        P[64] = self._g(("n", "="))
        P[65] = self.minor_e
        P[66] = self.sv
        P[67] = 0.6 + 0.4 * min(self.rate, 1.0)
        P[69] = self.pan_halo.get(pn[0], -1)
        P[70] = (0.52 + 0.30 * math.sin(self.lamp)) * W
        P[71] = (0.58 + 0.20 * math.sin(1.31 * self.lamp + 1.0)) * H

        hb = self._host[i]
        hb[:] = self.A
        stream = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.arena.data.ptr, hb.ctypes.data, hb.nbytes, rt.memcpyHostToDevice, stream.ptr)
        if ev is not None:
            ev.record(stream)
        launch_2d(self.k, W, H, (self.buf, self.arena, self.atlas, self.gt, self.go, np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self._ev = None
        self.buf = self.arena = self.atlas = self.gt = self.go = None
