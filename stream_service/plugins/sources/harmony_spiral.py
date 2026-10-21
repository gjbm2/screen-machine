"""Harmony spiral: every piano key has its place on a spiral seen from above; chords light up as shapes of light.

Source: Original (the circle of fifths and the pitch spiral of music theory, e.g.
        https://en.wikipedia.org/wiki/Circle_of_fifths and https://en.wikipedia.org/wiki/Pitch_helix)
Author: smfx (original)
License: MIT (original)
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  Every key A0..C8 has a fixed place on one
spiral: its angle is its pitch class around the circle of fifths (C at the top, G next to it clockwise;
``layout: chromatic`` puts the semitones in order instead) and each octave (C to B) is one turn further out, so
low notes sit near the centre, high ones at the edge and every C on one spoke.  Within a turn the radius grows a
twelfth of a turn per step around, so the keys trace a single spiral from A0 to C8 (in the chromatic layout that
is exactly pitch).  The spiral, the twelve spokes, a faint dot at every key's place, the rim and the twelve note
names (a glyph atlas baked with PIL at setup) are the quiet guide.

A struck note blooms as a glowing node at its place: its size from the velocity, its brightness following its
sounding level from the notes table (so a pedal-held note keeps glowing and a damped one dims), a ring rippling
out from the strike.  The notes sounding together (LEVEL >= THREAD_LEVEL; the TOP loudest of them) are joined by
threads of light in the colours of their two ends: the chord's outline (the notes in order around the centre) at
full weight, the diagonals fainter, so a chord appears as a shape.  Notes of one octave share a turn, so a major
triad is always (nearly) the same triangle, transposed by rotation, and a minor triad is its mirror image.  The
shape fills with a faint glow.  The rim lights each pitch class that sounds and its name brightens.

Time is the depth (``history: tunnel``, the default): past shapes recede behind the current one as a tunnel, so a
progression is seen as a sequence while it is played and the newest shape stays crisp at the front.  A feedback
buffer at render resolution holds the past: every frame it is drawn again a little smaller (``depth`` per second)
and turned (``twist``) about a vanishing point that circles slowly just off the centre, so the past spirals away
as a helix, and it fades with ``linger`` seconds (one bilinear read per pixel; two buffers ping-ponged by frame
parity, so the pointers, and the graph, never change).  Each chord is stamped into it once, in full, when its
newest note has bloomed (STAMP_AGE; at most STAMP_GAP apart in a fast run), and a faint trace of the front goes in
every frame, which draws the tunnel's walls.  ``history: flat`` keeps the past in place instead: a max-hold
afterimage that fades with ``linger``, so a progression draws itself over the same spot.

Continuity (no flicker, no pops): a note's thread weight ramps with its level above the cut (the threshold, or the
level of the first note the cap leaves out), so a note leaves or joins the loudest TOP at weight 0; a fill corner
with a low weight sits on the edge between its neighbours, so the shape grows a new corner out of an edge.  Strikes
bloom in over ~0.12 s (they land late, CONVENTIONS §2b) and ease out.

Colours: ``fifths`` (the palette around the circle of fifths: harmony becomes colour), ``accent`` (one colour, the
palette's most vivid, moved by the drift, on deep blue-black with a cool grey guide) or ``register`` (the palette
from A0 to C8).  Macros: energy -> node bloom, thread glow, ring size, glint speed, dust, haze and the guide's
breathing depth, a little of the tunnel's speed; pulse (bind note_on) -> node, thread, rim, guide and centre-glow
kick; speed -> breathing, the light gliding out along the spiral, the glints running along the threads, the tunnel's
depth and twist, dust and haze; intensity -> output gain; palette / period / drift as usual.  Alive without notes:
the guide breathes, light glides out along the spiral and a sparse dust orbits the centre.

Host diet (as piano_waterfall): per frame one H2D copy of a small arena from a pinned ring slot, one event record
(the slot's fence) and one graph launch (the kernel + the present), the graph captured once per output buffer.  The
host only chooses and orders the notes (plain Python over the few that sound or were just struck) and writes ten
raw floats per node; the kernel's prologue derives every node, thread, fill corner and class colour into shared
memory in each block.  Cost on the P4000 at 4K, render_scale 0.5 (the kernel + the upsample), before the tunnel and
with the GPU otherwise idle: ~1.0 ms quiet, ~1.5 ms for a chord, ~1.7-2.5 ms for a pedalled passage with all 66
threads.  With the tunnel full (a fast passage, 12 snapshots) 3.8 ms, measured while the same GPU also rendered a
live 4K60 stream (an upper bound).  Host ~0.3-0.5 ms per frame (build, stamp, tunnel) on a loaded CPU.
"""
from __future__ import annotations

import logging
import math
from collections import OrderedDict

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.harmony_spiral")

LOW, HIGH = 21, 108                    # A0 .. C8
N_KEYS = HIGH - LOW + 1
LAYOUTS = ("fifths", "chromatic")
COLOURS = ("fifths", "accent", "register")
HISTORIES = ("flat", "tunnel")
STAMP_AGE, STAMP_GAP = 0.15, 0.3       # a shape enters the tunnel once its newest note has bloomed (or every 0.3 s)
TRACE = 0.05                           # ... and a faint trace of it every frame (the tunnel's walls)
KS, KS_LIVE = 12, 10                   # tunnel snapshots drawn crisp (slots; past KS_LIVE the oldest fades out fast)
TOP = 12                               # notes joined by threads (the loudest sounding): up to 66 threads
NN = 32                                # nodes drawn
NS = TOP * (TOP - 1) // 2
THREAD_LEVEL = 0.035                   # a note "sounds" (joins the threads) at this level and above ...
THREAD_RAMP = 0.06                     # ... reaching full weight this much above the cut
DIAGONAL = 0.4                         # a thread across the shape, against one on its outline
NODE_LEVEL = 0.004
R_FRAC, R0 = 0.415, 0.12               # C8's radius as a fraction of the height; A0's as a fraction of that
RIM, LABEL = 1.045, 1.115              # rim and name radii (x R)
NAMES = ("C", "D♭", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FIFTHS_POS = (7 * np.arange(12)) % 12           # pitch class -> place around the circle of fifths

# the host's arena (floats): params, then per node its raw values, the class colour indices and the rim's levels
NP_ = 80
REC_R = 10                             # raw node: x y level vel age bloom weight thread_weight colour_t -
O_RN = NP_
O_CT = O_RN + NN * REC_R               # class colour indices (palette t), by pitch class
O_H = O_CT + 12                        # rim levels, by pitch class
O_SH = O_H + 12                        # tunnel snapshots: KS x 8 (affine a b c d tx ty, fade, corners)
O_SV = O_SH + KS * 8                   # ... their corners: KS x TOP x 4 (x y colour_t weight, at the front)
N_AR = O_SV + KS * TOP * 4
# derived per block into shared memory by the kernel's prologue
D_N = 0                                # node (16)
D_S = D_N + NN * 16                    # thread (16)
D_Y = D_S + NS * 16                    # fill corner (4)
D_C = D_Y + TOP * 4                    # class colours (12 x 3)
D_B = D_C + 36                         # the fill's bounding box
D_SV = D_B + 4                         # snapshot corners where they are now (KS x TOP x 6: x y r g b weight)
D_SB = D_SV + KS * TOP * 6             # snapshot boxes (KS x 10: x0 y0 x1 y1 scale fade r g b -)
N_D = D_SB + KS * 10

_SRC = HELPERS + r"""
#define NN """ + str(NN) + r"""
#define O_RN """ + str(O_RN) + r"""
#define O_CT """ + str(O_CT) + r"""
#define O_H """ + str(O_H) + r"""
#define N_AR """ + str(N_AR) + r"""
#define D_N """ + str(D_N) + r"""
#define D_S """ + str(D_S) + r"""
#define D_Y """ + str(D_Y) + r"""
#define D_C """ + str(D_C) + r"""
#define D_B """ + str(D_B) + r"""
#define N_D """ + str(N_D) + r"""
#define O_SH """ + str(O_SH) + r"""
#define O_SV """ + str(O_SV) + r"""
#define D_SV """ + str(D_SV) + r"""
#define D_SB """ + str(D_SB) + r"""
#define KS """ + str(KS) + r"""
#define SV """ + str(TOP) + r"""
#define DIAGONAL """ + repr(float(DIAGONAL)) + r"""f
#define TWO_PI 6.2831853f
#define NOISE 256

// bilinear sample of the tiling noise texture (NOISE x NOISE floats 0..1)
__device__ __forceinline__ float noise_tex(const float* __restrict__ T, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = ((int)fu) & (NOISE - 1), y0 = ((int)fv) & (NOISE - 1);
    const int x1 = (x0 + 1) & (NOISE - 1), y1 = (y0 + 1) & (NOISE - 1);
    const float tx = u - fu, ty = v - fv;
    const float a = T[y0 * NOISE + x0], b = T[y0 * NOISE + x1], c = T[y1 * NOISE + x0], d = T[y1 * NOISE + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}
// bilinear sample of a float4 buffer at continuous texel coords (texel centres at integers), zero outside
__device__ __forceinline__ float4 sample4(const float4* __restrict__ B, float fx, float fy, int W, int H) {
    const float x0f = floorf(fx), y0f = floorf(fy);
    const int x0 = (int)x0f, y0 = (int)y0f;
    const float tx = fx - x0f, ty = fy - y0f;
    float4 r = make_float4(0.f, 0.f, 0.f, 0.f);
    if (x0 < -1 || y0 < -1 || x0 >= W || y0 >= H) return r;
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int xx = x0 + (k & 1), yy = y0 + (k >> 1);
        if (xx < 0 || yy < 0 || xx >= W || yy >= H) continue;
        const float w = ((k & 1) ? tx : 1.f - tx) * ((k >> 1) ? ty : 1.f - ty);
        if (w <= 0.f) continue;
        const float4 v = B[yy * W + xx];
        r.x += w * v.x; r.y += w * v.y; r.z += w * v.z;
    }
    return r;
}
// coverage of a line of half width hw at distance d (a 1 px box filter: antialiased at any width)
__device__ __forceinline__ float line_cov(float d, float hw) { return clamp01(hw + 0.5f - d); }
__device__ __forceinline__ float3 pal3(float t, const float* c) {
    return make_float3(clamp01(c[0] + c[3] * cosf(TWO_PI * (c[6] * t + c[9]))),
                       clamp01(c[1] + c[4] * cosf(TWO_PI * (c[7] * t + c[10]))),
                       clamp01(c[2] + c[5] * cosf(TWO_PI * (c[8] * t + c[11]))));
}

// P: [0..11]palette [12]W [13]H [14]cx [15]cy [16]R px [17]r0 [18]Q0 (A0's turn) [19]rotation (rad) [20]nodes
//    [21]thread notes m (nodes 0..m-1, in order around the centre) [22]glint travel (1080p px) [23]fill gain
//    [24]energy [25]kick [26]gain [27]afterimage decay [28]afterimage gain [29]guide [30]names [31]breath 0..1
//    [32]flow phase [33]dust angle [34]haze angle [35]clock s [36]thread core half width [37]thread halo px
//    [38]px scale (render px per 1080p px)
//    [39..41]guide tint [42..44]background [49]rim px [50]name radius px [51]name w [52]name h [53]dust threshold
//    [54]colour mode [55]rim kick [56]Q1 [57]layout (0 fifths, 1 chromatic) [58]node size [59]thread gain
//    [60, 61]haze cos, sin [62, 63]dust cos, sin [64]history parity (0: read A, write B) [65, 66]vanishing point
//    [67]history sample scale (1 / this frame's shrink) [68, 69]cos, sin of this frame's twist [70]this frame's stamp
//    [71]tunnel snapshots
// raw node (10): x y level vel age bloom weight thread_weight colour_t -
// derived node (16): x y rc lum halo halo_sigma bloom bloom_sigma ring_r ring_a reach hot r g b thread_q
// derived thread (16): ax ay bax bay 1/|ba|^2 w reach ia ib x0 y0 x1 y1 length glint_offset -
// derived fill corner (4): x y node w
extern "C" __global__
void spiral(uchar4* __restrict__ out, float4* __restrict__ accA, float4* __restrict__ accB, const float* __restrict__ A,
            const unsigned char* __restrict__ atlas, const float* __restrict__ tex, const unsigned int seed)
{
    __shared__ float S[N_AR];
    __shared__ float D[N_D];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    const int nth = blockDim.x * blockDim.y;
    for (int i = tid; i < N_AR; i += nth) S[i] = A[i];
    __syncthreads();
    // ---- the prologue: every block derives the frame's records from the raw arena (cheap; no host maths) ---------
    {
        const float* P = S;
        const float sc = P[38], e = P[24], kick = P[25];
        const int nn = (int)P[20], m = (int)P[21];
        if (tid < nn) {
            const float* q = S + O_RN + 10 * tid;
            float* d = D + D_N + 16 * tid;
            const float l = q[2], v = q[3], ag = q[4], b = q[5];
            const float vis = b * q[6];
            const float sq = sqrtf(l), flash = expf(-ag / 0.35f) * b;
            const float rc = sc * 14.f * P[58] * (0.45f + 0.55f * v) * (0.7f + 0.3f * sq) * (0.5f + 0.5f * b);
            const float halo = vis * (0.15f + 0.7f * l + 0.35f * flash) * (1.f + 0.6f * kick);
            const float hs = 5.5f * sc * (0.8f + 0.5f * e);
            const float bl = vis * (0.08f + 0.36f * l + 0.3f * flash) * (0.6f + 0.8f * e) * (1.f + 0.5f * kick);
            const float bs = 26.f * sc * (0.8f + 0.5f * e) * (1.f + 0.3f * kick);
            const float ring_r = rc + sc * (12.f + 30.f * v) * (0.8f + 0.6f * e) * (1.f - expf(-ag / 0.3f));
            const float ring_a = ag < 2.f ? vis * (0.2f + 0.5f * v) * expf(-ag / 0.45f) : 0.f;
            float reach = rc + fmaxf(hs * fmaxf(logf(fmaxf(halo, 1e-4f) * 500.f), 0.f),
                                     bs * fmaxf(logf(fmaxf(bl, 1e-4f) * 500.f), 0.f));
            if (ring_a > 1e-3f) reach = fmaxf(reach, ring_r + 14.f * sc);
            const float3 c = pal3(q[8], P);
            d[0] = q[0]; d[1] = q[1]; d[2] = rc; d[3] = vis * (0.3f + sq + 0.4f * flash) * (1.f + 0.35f * kick);
            d[4] = halo; d[5] = hs; d[6] = bl; d[7] = bs; d[8] = ring_r; d[9] = ring_a; d[10] = reach + 2.f;
            d[11] = vis * (0.2f + 0.5f * sq + 0.45f * flash);
            d[12] = c.x; d[13] = c.y; d[14] = c.z;
            d[15] = sqrtf(sq) * q[7];                      // a thread's end: level^(1/4) x its ramp
        } else if (tid >= 64 && tid < 76) {
            const int pc = tid - 64;
            const float3 c = pal3(S[O_CT + pc], P);
            D[D_C + 3 * pc] = c.x; D[D_C + 3 * pc + 1] = c.y; D[D_C + 3 * pc + 2] = c.z;
        } else if (tid >= 96 && tid < 96 + m && m >= 3) {  // a weak corner sits on the edge between its neighbours
            const int c = tid - 96;
            const float* q = S + O_RN + 10 * c;
            const float* qp = S + O_RN + 10 * ((c + m - 1) % m);
            const float* qn = S + O_RN + 10 * ((c + 1) % m);
            const float mx = 0.5f * (qp[0] + qn[0]), my = 0.5f * (qp[1] + qn[1]);
            const float w = q[7], ww = w * w * (3.f - 2.f * w);
            float* d = D + D_Y + 4 * c;
            d[0] = mx + (q[0] - mx) * ww; d[1] = my + (q[1] - my) * ww; d[2] = (float)c; d[3] = w;
        }
        const int nsn = (int)P[71];
        for (int i = tid - 128; i >= 0 && i < nsn * SV; i += nth - 128) {   // snapshot corners, moved to now
            const int k = i / SV;
            const float* h = S + O_SH + 8 * k;
            if (i - k * SV >= (int)h[7]) continue;
            const float* q = S + O_SV + 4 * i;
            const float3 c = pal3(q[2], P);
            float* d = D + D_SV + 6 * i;
            d[0] = h[0] * q[0] + h[1] * q[1] + h[4]; d[1] = h[2] * q[0] + h[3] * q[1] + h[5];
            d[2] = c.x; d[3] = c.y; d[4] = c.z; d[5] = q[3];
        }
        __syncthreads();
        const int npair = P[59] > 0.f ? m * (m - 1) / 2 : 0;
        if (tid < npair) {                                  // every pair of the thread notes
            int i = 0, rem = tid;
            while (rem >= m - 1 - i) { rem -= m - 1 - i; ++i; }
            const int j = i + 1 + rem;
            const float* Na = D + D_N + 16 * i;
            const float* Nb = D + D_N + 16 * j;
            const bool outline = (j == i + 1) || (i == 0 && j == m - 1);
            const float w = Na[15] * Nb[15] * P[59] * (outline ? 1.f : DIAGONAL * fminf(1.f, 4.5f / (float)m));
            const float reach = sc + P[37] * fmaxf(logf(fmaxf(w, 1e-4f) * 500.f), 0.f);
            const float bax = Nb[0] - Na[0], bay = Nb[1] - Na[1];
            float* G = D + D_S + 16 * tid;
            G[0] = Na[0]; G[1] = Na[1]; G[2] = bax; G[3] = bay; G[4] = 1.f / fmaxf(bax * bax + bay * bay, 1e-3f);
            G[5] = w; G[6] = reach; G[7] = (float)i; G[8] = (float)j;
            G[9] = fminf(Na[0], Nb[0]) - reach; G[10] = fminf(Na[1], Nb[1]) - reach;
            G[11] = fmaxf(Na[0], Nb[0]) + reach; G[12] = fmaxf(Na[1], Nb[1]) + reach;
            G[13] = sqrtf(bax * bax + bay * bay);
            G[14] = (float)((i * 7 + j * 13) % 17) * (1.f / 17.f);   // each thread's glint starts elsewhere
        } else if (tid == nth - 1 && m >= 3) {
            float x0 = 1e30f, y0 = 1e30f, x1 = -1e30f, y1 = -1e30f;
            for (int c = 0; c < m; ++c) {
                const float* d = D + D_Y + 4 * c;
                x0 = fminf(x0, d[0]); y0 = fminf(y0, d[1]); x1 = fmaxf(x1, d[0]); y1 = fmaxf(y1, d[1]);
            }
            D[D_B] = x0 - 2.f; D[D_B + 1] = y0 - 2.f; D[D_B + 2] = x1 + 2.f; D[D_B + 3] = y1 + 2.f;
        } else if (tid >= 128 && tid < 128 + nsn) {         // a snapshot's box, scale, fade and mean colour
            const int k = tid - 128;
            const float* h = S + O_SH + 8 * k;
            const int n = (int)h[7];
            const float scl = sqrtf(fabsf(h[0] * h[3] - h[1] * h[2]));
            const float pad = 3.f + sc * (6.f + 12.f * scl);
            float x0 = 1e30f, y0 = 1e30f, x1 = -1e30f, y1 = -1e30f, r = 0.f, g = 0.f, b = 0.f;
            for (int c = 0; c < n; ++c) {
                const float* d = D + D_SV + 6 * (k * SV + c);
                x0 = fminf(x0, d[0]); y0 = fminf(y0, d[1]); x1 = fmaxf(x1, d[0]); y1 = fmaxf(y1, d[1]);
                r += d[2]; g += d[3]; b += d[4];
            }
            float* B = D + D_SB + 10 * k;
            B[0] = x0 - pad; B[1] = y0 - pad; B[2] = x1 + pad; B[3] = y1 + pad; B[4] = scl;
            B[5] = n > 0 ? h[6] * smooth(0.f, 0.12f, 1.f - scl) : 0.f;   // it emerges from behind the front
            const float inv = 1.f / fmaxf((float)n, 1.f);
            B[6] = r * inv; B[7] = g * inv; B[8] = b * inv;
        }
        __syncthreads();
    }
    const float* P = S;
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float dx = px - P[14], dy = py - P[15];
    const float r = sqrtf(dx * dx + dy * dy);
    const float R = P[16], r0 = P[17], sc = P[38], kick = P[25];
    const float rn = r / R;
    const float* CC = D + D_C;
    const int m = (int)P[21];

    // ---- the harmony: fill, threads, nodes (these leave the afterimage) ------------------------------------------
    float3 E = make_float3(0.f, 0.f, 0.f);
    const int npoly = m >= 3 && P[23] > 0.f ? m : 0;
    if (npoly && px >= D[D_B] && px <= D[D_B + 2] && py >= D[D_B + 1] && py <= D[D_B + 3]) {
        bool inside = false;
        float dmin2 = 1e30f;
        for (int i = 0, j = npoly - 1; i < npoly; j = i++) {
            const float* Vi = D + D_Y + 4 * i;
            const float* Vj = D + D_Y + 4 * j;
            if ((Vi[1] > py) != (Vj[1] > py)) {
                const float xi = (Vj[0] - Vi[0]) * (py - Vi[1]) / (Vj[1] - Vi[1]) + Vi[0];
                if (px < xi) inside = !inside;
            }
            const float ex = Vj[0] - Vi[0], ey = Vj[1] - Vi[1];
            const float wx = px - Vi[0], wy = py - Vi[1];
            const float t = clamp01((wx * ex + wy * ey) / fmaxf(ex * ex + ey * ey, 1e-6f));
            const float qx = wx - ex * t, qy = wy - ey * t;
            dmin2 = fminf(dmin2, qx * qx + qy * qy);
        }
        const float dd = sqrtf(dmin2);
        const float sd = inside ? -dd : dd;
        const float cov = clamp01(0.5f - sd);
        if (cov > 0.f) {
            float3 c = make_float3(0.f, 0.f, 0.f);
            float ws = 1e-6f;
            for (int i = 0; i < npoly; ++i) {
                const float* Vi = D + D_Y + 4 * i;
                const float ux = px - Vi[0], uy = py - Vi[1];
                const float* Nd = D + D_N + 16 * (int)Vi[2];
                const float wgt = Vi[3] / (ux * ux + uy * uy + 400.f * sc * sc);
                c.x += wgt * Nd[12]; c.y += wgt * Nd[13]; c.z += wgt * Nd[14]; ws += wgt;
            }
            const float edge = expf(-fmaxf(-sd, 0.f) / (16.f * sc));
            const float k = P[23] * cov * (0.3f + 0.7f * edge) / ws;
            E.x += k * c.x; E.y += k * c.y; E.z += k * c.z;
        }
    }
    float3 Rg = make_float3(0.f, 0.f, 0.f);         // glints and ripples: drawn fresh, never in the afterimage
    const int ns = P[59] > 0.f ? m * (m - 1) / 2 : 0;
    const float thw = P[36], tsig = P[37];
    for (int s = 0; s < ns; ++s) {
        const float* G = D + D_S + 16 * s;
        if (px < G[9] || px > G[11] || py < G[10] || py > G[12]) continue;
        const float pax = px - G[0], pay = py - G[1];
        const float h = clamp01((pax * G[2] + pay * G[3]) * G[4]);
        const float qx = pax - G[2] * h, qy = pay - G[3] * h;
        const float d = sqrtf(qx * qx + qy * qy);
        if (d > G[6]) continue;
        const float* Na = D + D_N + 16 * (int)G[7];
        const float* Nb = D + D_N + 16 * (int)G[8];
        // threads leave from the nodes' rims, not their centres
        const float da = sqrtf(pax * pax + pay * pay);
        const float bx = pax - G[2], by = pay - G[3];
        const float db = sqrtf(bx * bx + by * by);
        const float m = smooth(0.6f * Na[2], 1.2f * Na[2] + sc, da) * smooth(0.6f * Nb[2], 1.2f * Nb[2] + sc, db);
        const float core = line_cov(d, thw);
        const float w = G[5] * m;
        const float glow = w * (0.85f * core + 0.4f * expf(-d / tsig));
        const float cr = Na[12] + (Nb[12] - Na[12]) * h, cg = Na[13] + (Nb[13] - Na[13]) * h;
        const float cb = Na[14] + (Nb[14] - Na[14]) * h;
        const float wh = 0.3f * w * core;
        E.x += glow * cr + wh; E.y += glow * cg + wh; E.z += glow * cb + wh;
        // a glint of light travelling along the thread, a to b, again and again
        const float L = G[13];
        float sp = P[22] * sc + G[14] * L;
        sp -= L * floorf(sp / fmaxf(L, 1.f));
        const float ds = h * L - sp, gs = 7.f * sc;
        const float gl = 0.55f * w * __expf(-(ds * ds) / (2.f * gs * gs) - d * d / (2.f * 2.2f * sc * 2.2f * sc));
        Rg.x += gl * (0.35f + cr); Rg.y += gl * (0.35f + cg); Rg.z += gl * (0.35f + cb);
    }
    const int nn = (int)P[20];
    for (int i = 0; i < nn; ++i) {
        const float* Nd = D + D_N + 16 * i;
        const float ux = px - Nd[0], uy = py - Nd[1];
        const float d2 = ux * ux + uy * uy;
        if (d2 > Nd[10] * Nd[10]) continue;
        const float d = sqrtf(d2);
        const float rc = Nd[2];
        const float core = clamp01(rc - d + 0.5f);
        const float out_ = fmaxf(d - rc, 0.f);
        const float hot = Nd[11] * core * expf(-d2 / (0.25f * rc * rc + 1.f));
        const float halo = Nd[4] * expf(-out_ / Nd[5]) + Nd[6] * expf(-out_ / Nd[7]);
        const float k = Nd[3] * core * (0.7f + 0.3f * d / fmaxf(rc, 1e-3f)) + halo;
        E.x += k * Nd[12] + hot; E.y += k * Nd[13] + hot; E.z += k * Nd[14] + hot;
        if (Nd[9] > 1e-3f) {
            const float rd = fabsf(d - Nd[8]);
            const float ring = Nd[9] * (line_cov(rd, 0.6f * sc) + 0.35f * expf(-rd / (2.5f * sc)));
            Rg.x += ring * (0.25f + Nd[12]); Rg.y += ring * (0.25f + Nd[13]); Rg.z += ring * (0.25f + Nd[14]);
        }
    }
    // the afterimage: max-hold, fading; shown at a fraction of its strength under what sounds now
    // the history: last frame's buffer, drawn smaller and turned about the vanishing point (tunnel) or in place
    // (flat), faded, under this frame's shape (stamped in full once per chord, a faint trace otherwise)
    float4 a;
    {
        const float4* __restrict__ src = P[64] < 0.5f ? accA : accB;
        const float qx = px - P[65], qy = py - P[66];
        const float sx = P[65] + (qx * P[68] - qy * P[69]) * P[67] - 0.5f;
        const float sy = P[66] + (qx * P[69] + qy * P[68]) * P[67] - 0.5f;
        a = sample4(src, sx, sy, W, H);
    }
    const float dk = P[27], inj = P[70];
    a.x = fmaxf(a.x * dk, inj * E.x); a.y = fmaxf(a.y * dk, inj * E.y); a.z = fmaxf(a.z * dk, inj * E.z);
    (P[64] < 0.5f ? accB : accA)[y * W + x] = a;
    const float ag = P[28];
    float3 col = make_float3(fmaxf(E.x, ag * a.x) + Rg.x, fmaxf(E.y, ag * a.y) + Rg.y, fmaxf(E.z, ag * a.z) + Rg.z);

    // ---- the tunnel: the past chords, crisp at their depth (they move with the history, drawn fresh each frame) ---
    const int nsn = (int)P[71];
    if (nsn > 0) {
        float3 T = make_float3(0.f, 0.f, 0.f), A3 = make_float3(0.f, 0.f, 0.f);
        for (int k = 0; k < nsn; ++k) {
            const float* B = D + D_SB + 10 * k;
            if (B[5] <= 0.f || px < B[0] || px > B[2] || py < B[1] || py > B[3]) continue;
            const int n = (int)S[O_SH + 8 * k + 7];
            const float* V = D + D_SV + 6 * SV * k;
            const float sk = B[4], fk = B[5];
            const float hw = fmaxf(0.4f, 0.75f * sc * sqrtf(sk)), sig = 0.8f + 2.8f * sc * sk;
            const float rd = fmaxf(1.f, 6.f * sc * sk);
            const int ne = n >= 3 ? n : n - 1;
            bool inside = false;
            for (int i = 0; i < n; ++i) {
                const float* Vi = V + 6 * i;
                const float ux = px - Vi[0], uy = py - Vi[1];
                const float dn = sqrtf(ux * ux + uy * uy);
                const float dt_ = Vi[5] * (clamp01(rd - dn + 0.5f) + 0.45f * __expf(-dn / (rd + 2.f * sig)));
                T.x += dt_ * (0.25f + Vi[2]); T.y += dt_ * (0.25f + Vi[3]); T.z += dt_ * (0.25f + Vi[4]);
                if (i >= ne) continue;
                const float* Vj = V + 6 * ((i + 1) % n);
                const float ex = Vj[0] - Vi[0], ey = Vj[1] - Vi[1];
                if (n >= 3 && (Vi[1] > py) != (Vj[1] > py)) {
                    if (px < ex * (py - Vi[1]) / (Vj[1] - Vi[1]) + Vi[0]) inside = !inside;
                }
                const float h = clamp01((ux * ex + uy * ey) / fmaxf(ex * ex + ey * ey, 1e-6f));
                const float qx = ux - ex * h, qy = uy - ey * h;
                const float d = sqrtf(qx * qx + qy * qy);
                const float w = 0.5f * (Vi[5] + Vj[5]) * (0.85f * line_cov(d, hw) + 0.35f * __expf(-d / sig));
                T.x += w * (Vi[2] + (Vj[2] - Vi[2]) * h); T.y += w * (Vi[3] + (Vj[3] - Vi[3]) * h);
                T.z += w * (Vi[4] + (Vj[4] - Vi[4]) * h);
            }
            if (inside) { T.x += 0.12f * B[6]; T.y += 0.12f * B[7]; T.z += 0.12f * B[8]; }
            // this snapshot's share, dimmer the deeper it is
            const float g = fk * (0.35f + 0.65f * sk);
            A3.x += g * T.x; A3.y += g * T.y; A3.z += g * T.z;
            T = make_float3(0.f, 0.f, 0.f);
        }
        const float front = 1.f - clamp01(1.3f * fmaxf(E.x, fmaxf(E.y, E.z)));   // the front shape stays on top
        col.x += front * A3.x; col.y += front * A3.y; col.z += front * A3.z;
    }

    // ---- the background: deep blue-black, a faint glow at the centre, a slow haze ---------------------------------
    {
        const float e = P[24], br = P[31];
        const float cg = __expf(-rn * rn * 1.6f) * (0.6f + 0.4f * br + 0.8f * kick);
        col.x += P[42] * (1.f + 0.6f * cg); col.y += P[43] * (1.f + 0.7f * cg); col.z += P[44] * (1.f + 0.9f * cg);
        const float ca = P[60], sa = P[61];
        const float hu = (dx * ca - dy * sa) * (70.f / R), hv = (dx * sa + dy * ca) * (70.f / R);
        const float n1 = noise_tex(tex, hu + 512.f, hv + 512.f);
        const float n2 = noise_tex(tex, hu * 2.3f + 71.f, hv * 2.3f + 133.f);
        const float hz = n1 * (0.6f + 0.4f * n2);
        const float k = (0.010f + 0.028f * e) * hz * hz * (0.35f + 0.65f * __expf(-rn * rn * 0.9f));
        col.x += k * P[39]; col.y += k * P[40]; col.z += k * P[41];
    }

    // ---- the guide: spokes, the spiral, every key's place, the rim and the names (only near the disc) -------------
    if (rn < 1.26f) {
        float tp = (atan2f(dx, -dy) - P[19]) * (12.f / TWO_PI);   // 0 at the top, clockwise, in spokes
        tp -= 12.f * floorf(tp * (1.f / 12.f));            // 0..12
        const float kf = floorf(tp + 0.5f);
        const float dkp = tp - kf;                          // -0.5..0.5 spokes from the nearest
        const int k = ((int)kf) % 12;
        const bool fifths = P[57] < 0.5f;
        const int pck = fifths ? (7 * k) % 12 : k;          // the pitch class on spoke k
        const float g = P[29] * (0.72f + 0.28f * P[31]) * (1.f + 0.6f * kick);   // the guide answers each note
        if (g > 0.f && rn < 1.04f) {
            const float Q0 = P[18], Q1 = P[56];
            const float tsp = R * (1.f - r0) / (Q1 - Q0);    // px per turn
            const float* ck = CC + 3 * pck;
            // a spoke: tinted by its class in the fifths colours, the guide's tint otherwise
            const float3 tint = P[54] < 0.5f
                ? make_float3(0.4f + 0.6f * ck[0], 0.4f + 0.6f * ck[1], 0.4f + 0.6f * ck[2])
                                             : make_float3(P[39], P[40], P[41]);
            const float dth = dkp * (TWO_PI / 12.f);
            float sn, cs;
            __sincosf(dth, &sn, &cs);
            const float dsp = r * fabsf(sn);
            const float sm = smooth(r0 * 0.5f, r0 * 1.0f, rn) * (1.f - smooth(1.0f, 1.03f, rn));
            const float sp = sm * (line_cov(dsp, 0.35f * sc) * 0.10f + 0.03f * __expf(-dsp / (3.f * sc)));
            // the spiral: turn - spoke / 12 = 0 (mod 1)
            const float qf = Q0 + (rn - r0) / (1.f - r0) * (Q1 - Q0);
            const float f = qf - tp * (1.f / 12.f);
            const float df = f - floorf(f + 0.5f);
            const float slope = tsp / fmaxf(r * TWO_PI, 1.f);
            const float darm = fabsf(df) * tsp * rsqrtf(1.f + slope * slope);
            const float am = smooth(r0 * 0.7f, r0 * 1.1f, rn) * (1.f - smooth(0.995f, 1.03f, rn));
            const float fs = 0.5f + 0.5f * __sinf(TWO_PI * (qf * 0.5f - P[32]));   // light gliding out along it
            const float fs2 = fs * fs, fs4 = fs2 * fs2;
            const float flow = 0.6f + 0.25f * fs + 1.1f * fs4 * fs4 * fs4;
            const float ar = am * flow * (line_cov(darm, 0.45f * sc) * 0.17f + 0.05f * __expf(-darm / (4.f * sc)));
            // the key's place: a faint dot (the nearest key on this spoke)
            int p = 12 * (int)rintf(qf - kf * (1.f / 12.f)) + pck;
            p = p < 21 ? p + 12 * ((32 - p) / 12) : p;
            p = p > 108 ? p - 12 * ((p - 97) / 12) : p;
            const float q = (float)(p / 12) + kf * (1.f / 12.f);
            const float rp = R * (r0 + (1.f - r0) * (q - Q0) / (Q1 - Q0));
            const float dd2 = fmaxf(r * r + rp * rp - 2.f * r * rp * cs, 0.f);
            const float dot = clamp01(1.3f * sc - sqrtf(dd2) + 0.5f) * 0.34f + 0.09f * __expf(-dd2 / (10.f * sc * sc));
            const float gk = g * (sp + ar);
            col.x += gk * tint.x + g * dot * (0.5f + 0.5f * tint.x);
            col.y += gk * tint.y + g * dot * (0.5f + 0.5f * tint.y);
            col.z += gk * tint.z + g * dot * (0.5f + 0.5f * tint.z);
        }
        // the rim: a thin circle; each sounding pitch class lights its arc
        const float rr = fabsf(r - P[49]);
        if (rr < 40.f * sc) {
            const float* H12 = S + O_H;
            float3 arc = make_float3(0.f, 0.f, 0.f);
            const int kn = dkp >= 0.f ? (k + 1) % 12 : (k + 11) % 12;
            for (int q = 0; q < 2; ++q) {
                const int kk = q == 0 ? k : kn;
                const int pc = fifths ? (7 * kk) % 12 : kk;
                float dq = tp - (float)kk;
                dq -= 12.f * floorf(dq * (1.f / 12.f) + 0.5f);
                const float lv = H12[pc] * __expf(-dq * dq * (1.f / 0.045f));
                arc.x += lv * CC[3 * pc]; arc.y += lv * CC[3 * pc + 1]; arc.z += lv * CC[3 * pc + 2];
            }
            const float ln = line_cov(rr, 0.45f * sc);
            const float base = g * (0.10f * ln + 0.03f * __expf(-rr / (3.f * sc)));
            const float ak = (1.f + 0.5f * P[55]) * (1.1f * ln + 0.55f * __expf(-rr / (6.f * sc)));
            col.x += base * P[39] + ak * arc.x; col.y += base * P[40] + ak * arc.y; col.z += base * P[41] + ak * arc.z;
        }
        // the names, upright beyond the rim (a glyph atlas baked at this render size)
        if (P[30] > 0.f && P[51] > 0.f && rn > 1.0f) {
            float ls, lc;
            __sincosf(kf * (TWO_PI / 12.f) + P[19], &ls, &lc);
            const float lx = P[14] + P[50] * ls, ly = P[15] - P[50] * lc;
            const float lw = P[51], lh = P[52];
            const float u = px - lx + 0.5f * lw, v = py - ly + 0.5f * lh;
            if (u >= 0.f && v >= 0.f && u < lw && v < lh) {
                const float cov = atlas[(pck * (int)lh + (int)v) * (int)lw + (int)u] * (1.f / 255.f);
                const float lit = clamp01(S[O_H + pck]);
                const float k2 = P[30] * cov * (0.2f + 0.8f * lit);
                const float* ck = CC + 3 * pck;
                const float wm = 0.6f - 0.3f * lit;
                col.x += k2 * (wm + (1.f - wm) * ck[0]); col.y += k2 * (wm + (1.f - wm) * ck[1]);
                col.z += k2 * (wm + (1.f - wm) * ck[2]);
            }
        }
    }
    // a sparse dust, slowly orbiting the centre
    {
        const float ux = dx * P[62] - dy * P[63] + 4096.f, uy = dx * P[63] + dy * P[62] + 4096.f;
        const float ics = 1.f / (4.f * sc);
        const float cx_ = ux * ics, cy_ = uy * ics;
        const int ix = (int)floorf(cx_), iy = (int)floorf(cy_);
        const unsigned int hh = hash_u(((unsigned int)ix * 73856093u) ^ ((unsigned int)iy * 19349663u) ^ (seed + 21u));
        if ((hh >> 8) * (1.f / 16777216.f) > P[53]) {
            const float fx = cx_ - ix - 0.5f, fy = cy_ - iy - 0.5f;
            const float tw = 0.5f + 0.5f * __sinf(TWO_PI * ((hh & 255u) * (1.f / 256.f) + 0.5f * P[35]));
            const float k2 = (0.28f + 0.2f * kick) * __expf(-(fx * fx + fy * fy) * 10.f) * tw * tw;
            col.x += k2 * (0.55f + 0.45f * P[39]); col.y += k2 * (0.55f + 0.45f * P[40]);
            col.z += k2 * (0.6f + 0.4f * P[41]);
        }
    }

    const float gn = 255.f * P[26];
    // a fixed dither: no banding in the dark, no shimmer
    const float dth = (hash_u((unsigned int)(y * W + x) ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    out[y * W + x] = pack(make_float4(gn * (1.f - expf(-1.2f * col.x)) + dth, gn * (1.f - expf(-1.2f * col.y)) + dth,
                                      gn * (1.f - expf(-1.2f * col.z)) + dth, 255.f));
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")
_RING = 16                             # pinned upload slots
_GRAPHS = 8                            # captured graphs kept, one per output buffer seen
_logged: set = set()


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def _pal(t: float, c) -> tuple[float, float, float]:
    """The cosine palette ``c`` (12 floats) at index ``t``, clamped 0..1."""
    tp = 2.0 * math.pi
    return tuple(min(max(c[i] + c[3 + i] * math.cos(tp * (c[6 + i] * t + c[9 + i])), 0.0), 1.0) for i in range(3))


_ACCENT: dict = {}


def accent_t(name: str) -> float:
    """The palette index of a palette's most vivid bright colour (the accent of ``colours: accent``), cached."""
    t = _ACCENT.get(name)
    if t is None:
        c = palette_coeffs(name)
        best, t = -1.0, 0.0
        for i in range(96):
            r, g, b = _pal(i / 96.0, c)
            hi, lo = max(r, g, b), min(r, g, b)
            score = hi * (hi - lo) + 0.35 * hi
            if score > best:
                best, t = score, i / 96.0
        _ACCENT[name] = t
    return t


def key_places(layout: str) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Every key's spoke (0..11 clockwise from C at the top) and turn (its octave + spoke / 12), A0..C8; and the
    turns of A0 and C8."""
    keys = np.arange(LOW, HIGH + 1)
    pc = keys % 12
    spoke = _FIFTHS_POS[pc] if layout == "fifths" else pc
    turn = keys // 12 + spoke / 12.0
    return spoke.astype(np.float64), turn.astype(np.float64), float(turn[0]), float(turn[-1])


def bake_names(height: float) -> tuple[np.ndarray, int, int]:
    """The twelve note names (by pitch class) as a uint8 atlas [12, lh, lw]; (empty, 0, 0) without a font."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(FONT, max(8, int(round(height * 0.62))))
        lh = max(8, int(round(height)))
        lw = max(8, int(math.ceil(max(font.getlength(n) for n in NAMES))) + 4)
        out = np.zeros((12, lh, lw), np.uint8)
        for i, name in enumerate(NAMES):
            img = Image.new("L", (lw, lh), 0)
            ImageDraw.Draw(img).text((lw / 2, lh / 2), name, fill=255, font=font, anchor="mm")
            out[i] = np.asarray(img, np.uint8)
        return out, lw, lh
    except Exception as exc:                          # degrade: no names
        _log_once("font", "harmony_spiral: cannot bake the note names (%s); drawing none", exc)
        return np.zeros((12, 1, 1), np.uint8), 0, 0


def _noise_texture(seed: int, n: int = 256) -> np.ndarray:
    """A tiling smooth noise in 0..1 (a sum of random low-frequency waves)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n] / n
    f = np.zeros((n, n))
    for octave, amp in ((1, 1.0), (2, 0.6), (3, 0.45), (5, 0.3), (8, 0.18)):
        for _ in range(3):
            kx, ky = rng.integers(-octave, octave + 1, 2)
            if kx == 0 and ky == 0:
                kx = octave
            f += amp * np.cos(2 * np.pi * (kx * x + ky * y) + rng.uniform(0, 2 * np.pi))
    f -= f.min()
    f /= max(f.max(), 1e-9)
    return f.astype(np.float32)


class HarmonySpiral(Source):
    """Per frame the host issues one H2D copy of the arena (from a pinned ring slot), one event record (the slot's
    fence) and one graph launch (the kernel + the present), captured once per output buffer.  If capture fails the
    scene launches directly (logged once)."""
    name = "harmony_spiral"
    label = "Harmony spiral"
    description = ("Every key has its place on a spiral of fifths; the notes sounding together are joined by threads "
                   "of light, so each chord appears as a glowing shape that lingers as the harmony moves on.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.0),
            "layout": enum(LAYOUTS, "fifths", "Layout"),
            "history": enum(HISTORIES, "tunnel", "History", tier="regional", basic=True),
            "colours": enum(COLOURS, "fifths", "Colours", basic=True),
            "linger": num(0.0, 8.0, 3.0, "History fade", unit="s", basic=True),
            "threads": num(0.0, 1.5, 0.8, "Threads"),
            "fill": num(0.0, 1.0, 0.4, "Chord fill"),
            "guide": num(0.0, 1.5, 0.7, "Guide"),
            "names": num(0.0, 1.0, 0.5, "Note names"),
            "node_size": num(0.5, 2.0, 1.0, "Node size"),
            "depth": num(0.0, 1.0, 0.4, "Depth speed", unit="/s"),
            "twist": num(-1.0, 1.0, 0.18, "Helix twist", unit="rad/s"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        # the history, ping-ponged by frame parity (fixed pointers: the graph never changes)
        self.acc = cp.zeros((self.h, self.w, 4), cp.float32)
        self.acc2 = cp.zeros((self.h, self.w, 4), cp.float32)
        self.parity = 0
        self.stamped_on, self.pending = -1e9, None        # the strike last stamped; the first one waiting (s)
        self.snaps: list = []                             # the tunnel's crisp past: [affine, fade, evicted, corners]
        self.snap_v = np.zeros((KS, TOP, 4), np.float32)  # ... their corners at the front (x y colour_t weight)
        self._front: list = []
        self.k = raw_module(_SRC, _OPTS).get_function("spiral")
        self.layout = params["layout"]
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.tex = cp.asarray(_noise_texture(self.seed))
        self.sc = self.h / 1080.0
        self.R = R_FRAC * self.h
        atlas, self.lw, self.lh = bake_names(0.030 * self.h)
        self.atlas = cp.asarray(np.ascontiguousarray(atlas).ravel())
        self._places()
        # the arena on the device, one copy per frame from a pinned ring slot
        self.arena = cp.zeros(N_AR, cp.float32)
        try:
            self._pin = cp.cuda.alloc_pinned_memory(_RING * N_AR * 4)
            host = np.frombuffer(self._pin, np.float32, _RING * N_AR).reshape(_RING, N_AR)
        except Exception as exc:                          # pageable works too
            _log_once("pin", "harmony_spiral: no pinned memory (%s); uploading from pageable memory", exc)
            self._pin = None
            host = np.zeros((_RING, N_AR), np.float32)
        host[:] = 0.0
        self._spare = np.zeros(N_AR, np.float32)
        self._host = [host[i] for i in range(_RING)] + [self._spare]
        self._ev = [cp.cuda.Event(block=False, disable_timing=True, interprocess=False) for _ in range(_RING)]
        self._slot = 0
        self._graphs: OrderedDict = OrderedDict()
        self._cap = None
        self._mode = "graph"
        self.calls = 0
        self._quiet = np.zeros((128, 4), np.float32)
        self._quiet[:, 2] = np.inf
        self._quiet[:, 3] = np.inf
        self.clock = self.breath = self.flow = self.dust = self.haze = self.vp = self.drift = self.kick = 0.0
        self.glint = 0.0
        self.n_nodes = self.n_threads = self.n_poly = 0     # the last frame's counts (tests, stats)

    def _places(self) -> None:
        """Every key's fixed place (spoke, radius as a fraction of R) and the per-key constants of the build."""
        self.spoke, turn, self.q0, self.q1 = key_places(self.layout)
        self.rad = R0 + (1.0 - R0) * (turn - self.q0) / (self.q1 - self.q0)
        self.key_pc = np.arange(LOW, HIGH + 1) % 12
        self.fifths_t = _FIFTHS_POS[self.key_pc] / 12.0      # colour index around the circle of fifths
        self.reg_t = np.arange(N_KEYS) / float(N_KEYS - 1)
        self.order_key = (self.spoke * 1000.0 + np.arange(N_KEYS)).tolist()
        # plain lists for the per-frame build (Python floats: no numpy call per note)
        ang = self.spoke * (np.pi / 6.0)
        self.sin_l, self.cos_l, self.rad_l = np.sin(ang).tolist(), np.cos(ang).tolist(), self.rad.tolist()
        self.fifths_tl, self.reg_tl = self.fifths_t.tolist(), self.reg_t.tolist()
        self.cls_fifths = (_FIFTHS_POS / 12.0).tolist()
        self.cls_reg = ((np.arange(12) + 60 - LOW) / float(N_KEYS - 1)).tolist()

    # -- the notes -> nodes, threads, the fill polygon ------------------------------------------------------------
    def _notes(self) -> tuple[np.ndarray, float]:
        """The notes table and the sound clock (s) of this frame; the quiet table without sound."""
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        n = getattr(f, "notes", None) if f is not None else None
        if n is None or getattr(n, "shape", None) != (128, 4):
            return self._quiet, self.clock
        t = getattr(f, "t_ms", None)
        return n, (float(t) / 1000.0 if isinstance(t, (int, float)) and math.isfinite(t) else self.clock)

    def _build(self, hb, notes, p: dict, rot: float) -> float:
        """Choose and order the frame's notes; write their raw values, the class colour indices and the rim's levels
        into the host slot ``hb``.  Plain Python over the few notes that matter (sounding, or struck in the last two
        seconds); the kernel's prologue derives sizes, glows, colours, threads and the fill from these.  Returns the age
        (s) of the newest strike in view (inf: none)."""
        Ph = hb[:NP_]
        X = notes[LOW:HIGH + 1]
        cand = np.flatnonzero((X[:, 0] > NODE_LEVEL) | (X[:, 2] < 2.0))      # NaN / inf compare False: left out
        rows = X[cand, :3].tolist() if cand.size else []
        items = []                                        # (key index, level, vel, age, bloom, score)
        for k, (l, v, ag) in zip(cand.tolist(), rows):
            l = (l if l < 1.0 else 1.0) if l > 0.0 else 0.0                 # NaN -> 0
            v = (v if v < 1.0 else 1.0) if v > 0.0 else 0.0
            ag = (ag if ag < 1e3 else 1e3) if ag > 0.0 else 0.0
            b = ag / 0.12
            if b <= 0.0:
                continue                                  # struck this instant: it blooms in from the next frame
            b = 1.0 if b >= 1.0 else b * b * (3.0 - 2.0 * b)
            items.append((k, l, v, ag, b, l + 0.5 * math.exp(-ag / 0.5) * v))

        # threads: the loudest TOP of the notes sounding; weight ramps from the cut (no pop at the cap)
        snd = [it for it in items if it[1] >= THREAD_LEVEL]
        cut = THREAD_LEVEL
        if len(snd) > TOP:
            snd.sort(key=lambda it: -it[1])
            cut = snd[TOP][1]
            snd = snd[:TOP]
        th = []
        for it in snd:
            x = (it[1] - cut) / THREAD_RAMP
            x = 1.0 if x >= 1.0 else (x * x * (3.0 - 2.0 * x) if x > 0.0 else 0.0)
            w = x * it[4]
            if w > 1e-3:
                th.append((it, w))
        order = self.order_key
        th.sort(key=lambda t: order[t[0][0]])            # around the centre: the chord's outline order
        m = len(th)

        # nodes: the thread notes first, then the rest by level and recency
        nodes = [(it, 1.0, w) for it, w in th]
        taken = {it[0] for it, _w in th}
        rest = [it for it in items if it[0] not in taken and it[5] > NODE_LEVEL]
        room = NN - m
        if len(rest) > room:
            rest.sort(key=lambda it: -it[5])
            ncut = rest[room][5]
            for it in rest[:room]:
                x = min((it[5] - ncut) / 0.02, 1.0)
                nodes.append((it, x * x * (3.0 - 2.0 * x), 0.0))
        else:
            nodes += [(it, 1.0, 0.0) for it in rest]
        n = len(nodes)

        mode = p["colours"]
        period = float(p["palette_period"])
        drift = self.drift
        tkey = self.fifths_tl if mode == "fifths" else (self.reg_tl if mode == "register" else None)
        if tkey is None:
            drift += accent_t(p["palette"])
        cx, cy, R = 0.5 * self.w, 0.5 * self.h, self.R
        sin_a, cos_a, rad = self.sin_l, self.cos_l, self.rad_l
        cr, sr = math.cos(rot), math.sin(rot)
        out = []
        for it, nw, tw in nodes:
            k = it[0]
            rr = R * rad[k]
            s0, c0 = sin_a[k], cos_a[k]                   # the spoke's angle, then the sway
            out.append((cx + rr * (s0 * cr + c0 * sr), cy - rr * (c0 * cr - s0 * sr), it[1], it[2], it[3], it[4],
                        nw, tw, tkey[k] * period + drift if tkey is not None else drift, 0.0))
        if n:
            hb[O_RN:O_RN + n * REC_R].reshape(n, REC_R)[:] = out
        self._front = [(r_[0], r_[1], r_[8], r_[7] * math.sqrt(math.sqrt(r_[2]))) for r_ in out[:m]]
        self.n_nodes, self.n_threads, self.n_poly = n, m * (m - 1) // 2, m if m >= 3 else 0
        Ph[20], Ph[21] = n, m
        fill = float(p["fill"])
        if m >= 3 and fill > 0.0:
            acc = sum(w * math.sqrt(it[1]) for it, w in th) / m
            Ph[23] = fill * 0.6 * min(1.0, 1.6 * acc) * (1.0 + 0.2 * self.kick)

        # class colour indices and the rim's levels by pitch class
        cls = self.cls_fifths if mode == "fifths" else (self.cls_reg if mode == "register" else None)
        hb[O_CT:O_CT + 12] = [c * period + drift for c in cls] if cls is not None else drift
        rim = [0.0] * 12
        for it in items:
            pc = (it[0] + LOW) % 12
            v = it[1] * it[4]
            if v > rim[pc]:
                rim[pc] = v
        hb[O_H:O_H + 12] = [math.sqrt(v) for v in rim]
        return min((it[3] for it in items), default=math.inf)

    def _tunnel(self, hb, Ph, dt: float, tau: float, stamp: bool) -> int:
        """Move the snapshots with this frame's history transform (smaller and turned about the vanishing point),
        fade them, take a new one on a stamp; write them into the arena.  Returns how many to draw."""
        vx, vy, s = float(Ph[65]), float(Ph[66]), 1.0 / float(Ph[67])
        c, sn = float(Ph[68]), float(Ph[69])
        # the kernel samples at VP + R(th) (x - VP) / s, so content moves to VP + s R(-th) (x - VP)
        r00, r01, r10, r11 = s * c, s * sn, -s * sn, s * c
        fade_dt = math.exp(-dt / tau) if tau > 0.02 else 0.0
        go = math.exp(-dt / 0.15)                         # an evicted snapshot goes, quickly
        snaps, V = self.snaps, self.snap_v
        k = 0
        while k < len(snaps):
            sp = snaps[k]
            a, b, cc, d, tx, ty = sp[0]
            sp[0] = (r00 * a + r01 * cc, r00 * b + r01 * d, r10 * a + r11 * cc, r10 * b + r11 * d,
                     vx + r00 * (tx - vx) + r01 * (ty - vy), vy + r10 * (tx - vx) + r11 * (ty - vy))
            sp[1] *= fade_dt * (go if sp[2] else 1.0)
            if sp[1] > 0.01:
                k += 1
            else:
                del snaps[k]
                V[k:-1] = V[k + 1:].copy()                # keep the corners in step with the list
        front = self._front
        if stamp and front:
            live = [sp for sp in snaps if not sp[2]]
            if len(live) >= KS_LIVE:
                live[0][2] = True
            if len(snaps) >= KS:
                del snaps[0]
                V[:-1] = V[1:].copy()
            V[len(snaps), :len(front)] = front
            snaps.append([(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 0.7, False, len(front)])
        ns = len(snaps)
        if ns:
            hb[O_SH:O_SH + ns * 8].reshape(ns, 8)[:] = [(*sp[0], sp[1], sp[3]) for sp in snaps]
            hb[O_SV:O_SV + KS * TOP * 4] = V.ravel()
        return ns

    def _stamp(self, newest: float, t_snd: float) -> float:
        """How much of this frame's shape goes into the tunnel: all of it once per chord (when its newest strike has
        bloomed, or STAMP_GAP after the first strike not yet stamped, in a fast run), else a faint trace."""
        on = t_snd - newest                               # the newest strike, on the sound clock (s)
        if not on > self.stamped_on + 1e-3:
            return TRACE                                  # nothing new since the last stamp
        if self.pending is None:
            self.pending = on
        if newest >= STAMP_AGE or t_snd - self.pending >= STAMP_GAP:
            self.stamped_on, self.pending = on, None
            return 1.0
        return TRACE

    # -- launches -------------------------------------------------------------------------------------------------
    def _launch(self, out) -> None:
        launch_2d(self.k, self.w, self.h, (self.buf, self.acc, self.acc2, self.arena, self.atlas, self.tex,
                                           np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def _graph(self, out):
        key = out.data.ptr
        g = self._graphs.get(key)
        if g is not None:
            self._graphs.move_to_end(key)
            return g
        cp = self.ctx.cp
        try:
            if self._cap is None:
                self._cap = cp.cuda.Stream(non_blocking=True)
            s = self._cap
            with s:
                s.begin_capture()                         # relaxed mode: other threads' CUDA calls are unaffected
                try:
                    self._launch(out)
                except BaseException:
                    try:
                        s.end_capture()
                    except Exception:
                        pass
                    raise
                g = s.end_capture()
        except Exception as exc:                          # degrade, never refuse: direct launches
            self._mode = "direct"
            _log_once("capture", "harmony_spiral: CUDA graph capture failed (%s: %s); launching directly",
                      type(exc).__name__, exc)
            return None
        self._graphs[key] = g
        if len(self._graphs) > _GRAPHS:
            self._graphs.popitem(last=False)              # an in-flight launch of it still completes
        return g

    # -- render ---------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        cp = self.ctx.cp
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        dt = max(float(dt), 0.0)
        self.clock += dt
        a = 1.0 - math.exp(-dt / 0.12) if dt > 0 else 1.0     # kicks ease in (they land late anyway)
        self.kick += (pulse - self.kick) * a
        self.breath += (0.11 + 0.05 * e) * spd * dt
        self.flow += (0.03 + 0.04 * e) * spd * dt
        self.dust += (0.012 + 0.02 * e) * spd * dt
        self.haze += (0.01 + 0.012 * e) * spd * dt
        self.vp += 0.05 * spd * dt                           # the vanishing point's slow orbit
        self.glint += (110.0 + 140.0 * e) * spd * dt            # px/s (at 1080p) along the threads
        self.drift += float(p["drift_speed"]) * spd * dt
        coeffs = palette_coeffs(p["palette"])
        colours = p["colours"]
        if p["layout"] != self.layout:                    # structural: the keys jump to their new places
            self.layout = p["layout"]
            self._places()

        # this frame's host slot: a ring slot whose last copy has run, else the pageable spare
        i = self._slot
        ev = self._ev[i]
        calls = 1
        if ev.done:
            self._slot = (i + 1) % _RING
        else:
            ev, i = None, _RING
        hb = self._host[i]
        Ph = hb[:NP_]
        Ph[:] = 0.0

        rot = 0.0
        notes, t_snd = self._notes()
        newest = self._build(hb, notes, p, rot)

        if colours == "accent":                           # a cool blue-grey guide, touched by the accent
            ac = _pal(self.drift + accent_t(p["palette"]), coeffs)
            tint = [0.65 * g + 0.35 * c for g, c in zip((0.42, 0.52, 0.78), ac)]
        else:
            tint = [0.45 + 0.55 * c for c in _pal(self.drift + 0.62, coeffs)]
        Ph[0:12] = coeffs
        Ph[12], Ph[13] = self.w, self.h
        Ph[14], Ph[15] = 0.5 * self.w, 0.5 * self.h
        Ph[16], Ph[17], Ph[18] = self.R, R0, self.q0
        Ph[19] = rot
        Ph[24], Ph[25], Ph[26] = e, self.kick, float(p["intensity"])
        tau = float(p["linger"])
        Ph[27] = math.exp(-dt / tau) if tau > 0.02 else 0.0
        Ph[28] = 0.6
        # the history: flat (in place) or a tunnel (each frame smaller and turned about a vanishing point that
        # circles slowly off the centre, so the past recedes as a helix); stamp a shape once its chord has bloomed
        tunnel = p["history"] == "tunnel"
        Ph[64] = self.parity
        self.parity ^= 1
        if tunnel:
            vr = 0.07 * self.R
            Ph[65] = 0.5 * self.w + vr * math.sin(self.vp)
            Ph[66] = 0.5 * self.h - vr * math.cos(self.vp)
            Ph[67] = math.exp(float(p["depth"]) * (0.8 + 0.4 * e) * spd * dt)
            th = float(p["twist"]) * spd * dt
            Ph[68], Ph[69] = math.cos(th), math.sin(th)
            Ph[70] = self._stamp(newest, t_snd)
            Ph[71] = self._tunnel(hb, Ph, dt, tau, Ph[70] == 1.0)
        else:
            Ph[65], Ph[66], Ph[67], Ph[68], Ph[69], Ph[70] = 0.5 * self.w, 0.5 * self.h, 1.0, 1.0, 0.0, 1.0
            self.snaps.clear()
        Ph[29] = float(p["guide"]) * (1.0 + 0.15 * e)
        Ph[30] = float(p["names"])
        depth = 0.6 + 0.4 * e                            # the guide breathes deeper with energy
        Ph[31] = 1.0 - depth * (0.5 - 0.5 * math.sin(2 * math.pi * self.breath))
        Ph[32] = self.flow % 1000.0
        Ph[33] = self.dust % (2 * math.pi)
        Ph[34] = self.haze % (2 * math.pi)
        Ph[35] = self.clock % 1000.0
        Ph[38] = self.sc
        Ph[39:42] = tint
        Ph[42:45] = (0.012, 0.017, 0.040)
        Ph[49], Ph[50] = RIM * self.R, LABEL * self.R
        Ph[51], Ph[52] = self.lw, self.lh
        Ph[53] = 0.9972 - 0.0018 * e - 0.0008 * self.kick
        Ph[54] = COLOURS.index(colours)
        Ph[55] = self.kick
        Ph[56] = self.q1
        Ph[57] = LAYOUTS.index(self.layout)
        Ph[58] = float(p["node_size"])
        Ph[59] = float(p["threads"]) * (1.0 + 0.3 * self.kick)
        Ph[36] = 0.75 * self.sc
        Ph[37] = 3.2 * self.sc * (0.8 + 0.5 * e)
        Ph[22] = self.glint % 1e4
        Ph[60], Ph[61] = math.cos(self.haze), math.sin(self.haze)
        Ph[62], Ph[63] = math.cos(self.dust), math.sin(self.dust)

        stream = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.arena.data.ptr, hb.ctypes.data, hb.nbytes, rt.memcpyHostToDevice, stream.ptr)
        calls += 1
        if ev is not None:
            ev.record(stream)                             # the slot is free again once the copy has run
            calls += 1
        g = self._graph(out) if self._mode == "graph" else None
        if g is not None:
            g.launch(stream)
            calls += 1
        else:
            self._launch(out)
            calls += 2
        self.calls = calls

    def close(self) -> None:
        self._graphs = OrderedDict()
        self._cap = self._ev = None
        self.buf = self.acc = self.acc2 = self.arena = self.atlas = self.tex = None
