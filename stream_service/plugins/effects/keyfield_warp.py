"""Key currents: the harmony becomes a current that moves whatever is on screen.

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, https://esf.ccarh.org/254-old/254_LiteraturePack1/Harmony/Sapp_VisualHierKeyAnal05.pdf, through the
        shared key field in _lib/keyfield.py (keyscape_live's maths); and the two-phase flow-map technique of
        A. Vlachos, "Water Flow in Portal 2", SIGGRAPH 2010 Advances in Real-Time Rendering, as published in the
        talk's slides: the idea only, written from scratch, here applied to a noise rather than to the picture)
Author: smfx (original)
License: MIT (original)
license_class: permissive

WHAT YOU SEE
Over any scene or art still, the picture ripples in currents, like a reflection on moving water: ripples run across
it, and the picture is combed and swirled along the way they run.  Each part of the screen flows its own way, and the
way it flows is a key:
  - Where the music sits in its home key, the ripples run calmly in one direction (to the right; in the whirlpool
    form, round the centre).
  - Where the music has moved to another key, the current there turns, and runs a little stronger the further it is
    from home.  A fifth up (C to G) turns it 30 degrees upward, a fifth down (C to F) 30 degrees downward, two fifths
    60 degrees, and the furthest key (the tritone, C to F sharp) runs backward against the home current.  A minor key
    flows like its relative major (A minor like C).
  - A clear key flows strongly; an ambiguous passage or a quiet one drifts more gently, in the home direction.
  - Where two keys meet, the seam between their currents is a ridge of glass: it bends the picture and catches a
    little light, and the water churns a little more around it.  The further apart the two keys, the stronger.
  - grade tints regions toward their key's colour (keyscape's colours: hue round the circle of fifths, major light,
    minor deep), gently, and mostly where the music has left home: the home key tints a quarter as much as the
    furthest key, so a modulation shows as a change of colour without recolouring the whole picture.

The map behind it
  Left to right is time: the last scale_span seconds, newest at the right edge, scrolling left as the music goes on.
  While the piece is shorter than that, the whole piece is stretched across the screen.  Bottom to top is how much
  music each point listens to: the bottom hears one second at a time (each chord: a braided, choppy current), the top
  the whole span (the home key: one broad current).  With polar at 1 the map wraps into a clock face: time runs
  clockwise from 12 o'clock (the newest music arrives just before 12), the rim hears one second and the centre the
  whole span.  The home key then becomes a whirlpool turning round the centre, and modulations spiral in or out of
  it.  polar 0..1 is a smooth morph between the two.

How the analysis drives it (all from _lib/keyfield.py; nothing re-derived here)
  The shared key timeline (KeyTimeline.shared: one per screen, so this effect continues the same piece as any
  key-field scene) is baked TRAILING into 256 x 128 planes: each cell is the key of what had been heard by its moment,
  over its window (Krumhansl-Kessler profiles, Pearson r, clarity = how far the best key beats the next, fog = how
  little sound there was).  Per cell:
    direction  = kf_key_angle(key) - kf_key_angle(summit key): the cell's place on the circle of fifths relative to
                 the piece's home (the summit: the key of the whole piece), so home flows along the frame, sharp
                 keys turn up (outward in the whirlpool), flat keys down (inward);
    strength   = clarity x (1 - fog) x inside x (1 + FAR_GAIN |sin(direction / 2)|); a cell with no clear key flows
                 at an idle 0.75 in the home direction;
    seam       = the key boundary between clear neighbours, 0.35 for a change of mode (C / a) up to 1 for the tritone,
                 eased, smoothed, and used as a height: its gradient refracts the picture (a magnifying glass rod)
                 and lights one flank; its wide blur adds churn (rapids) around it;
    grade      = plane B's key colour weighted by clarity x (1 - fog) x (GRADE_HOME + (1 - GRADE_HOME) |sin(dir / 2)|).
  The field is blurred (3 x 3 binomial) and eased on the GPU (0.6 s), so a modulation turns the current smoothly; the
  home direction eases over 3 s when the piece's summit key changes; a new piece (after 90 s of silence, the next
  note) dissolves the old currents into the new ones.

Motion: flow noise.  A band-limited noise tile (FFT-synthesised once at setup, so it is seamless; 2 x 1 frame heights,
features ~0.3 frame heights) is carried along a wide blur of the current with a two-phase flow map: two copies of the
NOISE slide along the current, each restarting (on a fresh part of the tile) while its weight is 0, blended with
variance-keeping weights so the ripples never pulse.  The picture is then read once, displaced by that noise: combed
along the current (the noise x the current) plus a small curl swirl.  One tap of the picture, so a sharp still never
shows a double image; the ripples visibly travel the way each region's key points.  A slow meander (+-15..30 degrees)
and a drift of the map keep the currents winding, and the depth breathes; none of that depends on notes.

Macros: amount 0 is the input untouched (every term scales with it; 0..1 is a smooth morph); energy -> flow speed
(x0.5..1.5), depth (x0.7..1.3) and meander; pulse (bound to note_on) -> a swell that rises from the one-second row
to the long rows (in the whirlpool: from the rim to the centre) and briefly deepens the current, plus a kick to the
flow rate (amplitudes and rates only; nothing jumps); flow_speed -> the flow rate.  Alive with no notes: before the
first note the shared cold-start example (C, a modulation to G, back to C) is the field; silence keeps the frozen
piece's currents running.

GPU: kf_bake (keyfield, 256 x 128), kw_target (blur + ease, 256 x 128), kw_flow (seam gradient + the wide current,
256 x 128), kw_field (a quarter of the frame each way, all of it smooth: three bilinear field samples and two of the
384 x 192 noise tile -> where to read the picture, the glint and the grade), kw_apply (full size: two bilinear samples
of those and one bilinear tap of the picture).  Benchmarked 1.4-1.9 ms at 4K on the P4000 (fxtest --bench, host
included).  Host: the timeline update (~20 us), the summit (a two-row numpy evaluation) and three small uploads; the
noise tile is built once (numpy FFT, ~40 ms) at setup.  dt = 0 changes nothing; NaN is guarded on the host and in
every kernel.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import keyfield as KF
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num, standard_effect_params

FW, FH = 256, 128               # the key field's planes (u = time, v = timescale)
LOG_ROWS = 0.9                  # mostly logarithmic rows: the lower half covers 1 s .. ~10 s
PIECE_GAP = 90.0                # s of silence that freeze the piece (keyscape_live's default)
MIN_SPAN = 16.0                 # bins: a new piece is stretched across at least 8 s
FIELD_TAU_S = 0.6               # the field's easing
HOME_TAU_S = 3.0                # the home direction's easing
IDLE = 0.75                     # the current of a cell with no clear key, in the home direction
FAR_GAIN = 0.9                  # a key's current x (1 + this x |sin(its turn / 2)|): the tritone flows 1.9x home
COMB_H = 0.016                  # combing along the current at full strength, x frame height (x noise, RMS 1)
CURL_H = 0.008                  # the curl swirl at full strength, x frame height (RMS)
ADV_H = 0.09                    # how far the noise travels in one flow cycle, x frame height
RATE_K = 1.5                    # flow cycles / s per unit of flow_speed (0.4 -> 0.6 cycles/s -> ~0.05 H/s)
RAPIDS = 1.2                    # the current x (1 + this x seam x 3 x the wide seam height): churn where keys meet
LENS_H = 0.03                   # the seams' refraction at full seam, x frame height (x the ridge's slope / texel)
DMAX_H = 0.035                  # displacement soft limit, x frame height
DRIFT_UV = 0.012                # the map's idle drift (u, v units)
RIPPLE_GAIN = 0.6               # pulse: the current's depth x (1 + this x kick x the rising swell)
GLINT = 1.6
GRADE_MAX = 0.6
GRADE_HOME = 0.25               # the home key's share of the grade (the tritone gets all of it)
NTW, NTH = 384, 192             # the noise tile (texels), covering NSX x NSY frame heights
NSX, NSY = 2.0, 1.0
NOISE_F0 = 3.2                  # the noise's main band, cycles per frame height
NOISE_OCT2 = 0.12               # a finer band at 2.3 x, this much of the main band's weight
CYC_STEP = (0.7548776662 * NSX, 0.5698402910 * NSY)   # the tile offset per flow cycle (the R2 sequence: no repeats)
LOW = 4                         # the displacement / glint / grade buffers are 1 / LOW of the frame each way
NQ = 8
NP = 32

# P layout (floats), kw_field
#  0 polar | 1 cos base | 2 sin base | 3 comb px | 4 flow phase | 5 lens px | 6 glint | 7 grade | 8 kick
#  9 ripple phase | 10 ripple gain | 11 drift phase u | 12 drift phase v | 13 drift amp | 14 R px | 15 cx | 16 cy
#  17 texel px | 18 meander rad | 19 light x | 20 light y | 21 max displacement px | 22 noise phase | 23 meander phase
#  24 curl px | 25 noise travel per cycle (H) | 26, 27 this cycle's tile offset (H) | 28 rapids | 29, 30 offset step
#  31 idle current
_SRC = HELPERS + KF.KEYFIELD_CUDA + r"""
#define KW_TAU 6.28318531f
#define KW_POL 0
#define KW_CB 1
#define KW_SB 2
#define KW_D 3
#define KW_PH 4
#define KW_LENS 5
#define KW_GLINT 6
#define KW_GRADE 7
#define KW_KICK 8
#define KW_RPH 9
#define KW_RGAIN 10
#define KW_D1 11
#define KW_D2 12
#define KW_DAMP 13
#define KW_R 14
#define KW_CX 15
#define KW_CY 16
#define KW_TPX 17
#define KW_MEAN 18
#define KW_LX 19
#define KW_LY 20
#define KW_DMAX 21
#define KW_NPH 22
#define KW_MPH 23
#define KW_CURL 24
#define KW_ADV 25
#define KW_OX 26
#define KW_OY 27
#define KW_RAP 28
#define KW_SX 29
#define KW_SY 30
#define KW_IDLE 31
#define KW_NTW """ + str(NTW) + r"""
#define KW_NTH """ + str(NTH) + r"""
#define KW_NSX """ + repr(NSX) + r"""f
#define KW_NSY """ + repr(NSY) + r"""f

__device__ __forceinline__ float kw_fold(float v, float n) {     // mirror at the frame's edges
    v = fabsf(v);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}

// the noise tile, bilinear and wrapping; x, y in frame heights
__device__ __forceinline__ float4 kw_ntex(const float4* __restrict__ N, float x, float y) {
    float fx = kf_fin(x) * ((float)KW_NTW / KW_NSX) - 0.5f;
    float fy = kf_fin(y) * ((float)KW_NTH / KW_NSY) - 0.5f;
    fx -= floorf(fx * (1.f / (float)KW_NTW)) * (float)KW_NTW;
    fy -= floorf(fy * (1.f / (float)KW_NTH)) * (float)KW_NTH;
    const int x0 = min(max((int)fx, 0), KW_NTW - 1), y0 = min(max((int)fy, 0), KW_NTH - 1);
    const float tx = clamp01(fx - (float)x0), ty = clamp01(fy - (float)y0);
    const int x1 = x0 + 1 < KW_NTW ? x0 + 1 : 0, y1 = y0 + 1 < KW_NTH ? y0 + 1 : 0;
    const float4 a = __ldg(N + y0 * KW_NTW + x0), b = __ldg(N + y0 * KW_NTW + x1);
    const float4 c = __ldg(N + y1 * KW_NTW + x0), d = __ldg(N + y1 * KW_NTW + x1);
    const float sx = 1.f - tx, sy = 1.f - ty;
    return make_float4(sy * (sx * a.x + tx * b.x) + ty * (sx * c.x + tx * d.x),
                       sy * (sx * a.y + tx * b.y) + ty * (sx * c.y + tx * d.y),
                       sy * (sx * a.z + tx * b.z) + ty * (sx * c.z + tx * d.z),
                       sy * (sx * a.w + tx * b.w) + ty * (sx * c.w + tx * d.w));
}

// Field pass 1 (FW x FH): each cell's current in the home frame (x along home, y toward sharper keys), its key seam,
// and its key colour (premultiplied by its grade weight), blurred 1-2-1 and eased toward the target.
// Q: [0] ease share 0..1 (1: snap) [1] the home key's angle (rad) [2] idle current [3] far gain [4] grade at home
extern "C" __global__
void kw_target(const float4* __restrict__ A, const float4* __restrict__ B, float4* __restrict__ E,
               float4* __restrict__ G, const int fw, const int fh, const float* __restrict__ Q)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= fw || j >= fh) return;
    const float alpha = clamp01(kf_fin(Q[0])), home = kf_fin(Q[1]), idle = clamp01(kf_fin(Q[2]));
    const float far = fminf(fmaxf(kf_fin(Q[3]), 0.f), 4.f), gh0 = clamp01(kf_fin(Q[4]));
    const int idx = j * fw + i;
    const float4 ac = A[idx];
    const int kc = kf_key_id(ac.x);
    const float wc = kc >= 0 ? clamp01(kf_fin(ac.y)) * clamp01(1.f - kf_fin(ac.z)) * clamp01(kf_fin(ac.w)) : 0.f;
    float vx = 0.f, vy = 0.f, gr = 0.f, gg = 0.f, gb = 0.f, gw = 0.f, edge = 0.f;
    #pragma unroll
    for (int dj = -1; dj <= 1; ++dj) {                    // bounded: the 3 x 3 neighbourhood
        #pragma unroll
        for (int di = -1; di <= 1; ++di) {
            const int ii = min(max(i + di, 0), fw - 1), jj = min(max(j + dj, 0), fh - 1);
            const float4 a = A[jj * fw + ii];
            const float4 b = B[jj * fw + ii];
            const int k = kf_key_id(a.x);
            const float w = k >= 0 ? clamp01(kf_fin(a.y)) * clamp01(1.f - kf_fin(a.z)) * clamp01(kf_fin(a.w)) : 0.f;
            const float wt = (di == 0 ? 2.f : 1.f) * (dj == 0 ? 2.f : 1.f) * (1.f / 16.f);
            float s, c;
            __sincosf(kf_key_angle(a.x) - home, &s, &c);
            const float hd = sqrtf(clamp01(0.5f - 0.5f * c));      // |sin(turn / 2)|: 0 at home .. 1 the tritone
            const float g = 1.f + far * hd;
            vx += wt * (w * c * g + (1.f - w) * idle);
            vy += wt * (w * s * g);
            const float wg = w * (gh0 + (1.f - gh0) * hd);
            gr += wt * wg * clamp01(kf_fin(b.x));
            gg += wt * wg * clamp01(kf_fin(b.y));
            gb += wt * wg * clamp01(kf_fin(b.z));
            gw += wt * wg;
            if ((di == 0) != (dj == 0) && kc >= 0 && k >= 0 && k != kc) {   // a key boundary between clear cells
                int d = abs(kf_fifths_pos(k) - kf_fifths_pos(kc));
                d = min(d, 12 - d);
                edge += fminf(w, wc) * (0.35f + 0.65f * (float)d * (1.f / 6.f));
            }
        }
    }
    edge = clamp01(edge * 2.f);                            // a fifth's seam ~0.6, the tritone's 1
    float4 e = E[idx], g = G[idx];
    if (!(e.x == e.x && e.y == e.y && e.z == e.z && fabsf(e.x) < 1e6f && fabsf(e.y) < 1e6f && fabsf(e.z) < 1e6f))
        e = make_float4(vx, vy, edge, 0.f);              // never let NaN stick
    if (!(g.x == g.x && g.y == g.y && g.z == g.z && g.w == g.w && fabsf(g.w) < 1e6f))
        g = make_float4(gr, gg, gb, gw);
    e.x += (vx - e.x) * alpha;
    e.y += (vy - e.y) * alpha;
    e.z += (edge - e.z) * alpha;
    e.w = 0.f;
    g.x += (gr - g.x) * alpha;
    g.y += (gg - g.y) * alpha;
    g.z += (gb - g.z) * alpha;
    g.w += (gw - g.w) * alpha;
    E[idx] = e;
    G[idx] = g;
}

// the seam height at a cell (edge-clamped), and smoothed over its 4-neighbourhood
__device__ __forceinline__ float kw_h(const float4* __restrict__ E, int fw, int fh, int i, int j) {
    i = min(max(i, 0), fw - 1);
    j = min(max(j, 0), fh - 1);
    return E[j * fw + i].z;
}
__device__ __forceinline__ float kw_hs(const float4* __restrict__ E, int fw, int fh, int i, int j) {
    return (4.f * kw_h(E, fw, fh, i, j) + kw_h(E, fw, fh, i - 1, j) + kw_h(E, fw, fh, i + 1, j)
            + kw_h(E, fw, fh, i, j - 1) + kw_h(E, fw, fh, i, j + 1)) * 0.125f;
}

// Field pass 2 (FW x FH): F = (current x, current y, d seam / du, d seam / dv) (per unit u and v), and
// V = the current blurred wide (7 x 7 binomial taps, 2 texels apart) with the seam height blurred the same way: the
// noise rides V, so where the currents turn it shears gently; V's height is where the water churns.
extern "C" __global__
void kw_flow(const float4* __restrict__ E, float4* __restrict__ F, float4* __restrict__ V, const int fw, const int fh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= fw || j >= fh) return;
    const float4 e = E[j * fw + i];
    const float gu = (kw_hs(E, fw, fh, i + 1, j) - kw_hs(E, fw, fh, i - 1, j)) * 0.5f * (float)fw;
    const float gv = (kw_hs(E, fw, fh, i, j + 1) - kw_hs(E, fw, fh, i, j - 1)) * 0.5f * (float)fh;
    F[j * fw + i] = make_float4(kf_fin(e.x), kf_fin(e.y), kf_fin(gu), kf_fin(gv));
    const float bw[7] = {1.f, 6.f, 15.f, 20.f, 15.f, 6.f, 1.f};
    float vx = 0.f, vy = 0.f, vh = 0.f;
    #pragma unroll
    for (int dj = -3; dj <= 3; ++dj) {                    // bounded: 49 taps
        #pragma unroll
        for (int di = -3; di <= 3; ++di) {
            const int ii = min(max(i + 2 * di, 0), fw - 1), jj = min(max(j + 2 * dj, 0), fh - 1);
            const float4 q = E[jj * fw + ii];
            const float wt = bw[di + 3] * bw[dj + 3] * (1.f / 4096.f);
            vx += wt * kf_fin(q.x);
            vy += wt * kf_fin(q.y);
            vh += wt * kf_fin(q.z);
        }
    }
    V[j * fw + i] = make_float4(vx, vy, clamp01(vh), 0.f);
}

// Field pass 3 (lw x lh, a quarter of the frame each way: everything here is smooth): map the texel's pixel onto the
// field (cartesian or polar), turn the cell's current into the screen frame, carry the flow noise along it, and write
// D = (where to read the picture, relative to the pixel, in pixels; the glint as scale and lift) and
// C = (the grade's target colour / its luminance x its weight, the weight).
extern "C" __global__
void kw_field(float4* __restrict__ D, float4* __restrict__ C, const int lw, const int lh, const int W, const int H,
              const float4* __restrict__ F, const float4* __restrict__ V, const float4* __restrict__ G,
              const float4* __restrict__ N, const int fw, const int fh, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= lw || j >= lh) return;
    const float fW = (float)W, fH = (float)H;
    const float px = ((float)i + 0.5f) * fW / (float)lw, py = ((float)j + 0.5f) * fH / (float)lh;
    const float pol = clamp01(__ldg(P + KW_POL));
    // cartesian: u = time (newest at the right), v = timescale (1 s at the bottom); the frame along the base direction
    float u = px / fW, v = 1.f - py / fH;
    float tx = __ldg(P + KW_CB), ty = -__ldg(P + KW_SB);
    float jux = 1.f / fW, juy = 0.f, jvx = 0.f, jvy = -1.f / fH;  // d(u, v) / d(x, y)
    float fade = 1.f;
    if (pol > 1e-3f) {
        // polar: angle = time, clockwise from 12 o'clock (the newest just before 12); radius = timescale (the rim
        // hears 1 s, the centre the whole span); the frame along the clockwise tangent
        const float dx = px - __ldg(P + KW_CX), dy = py - __ldg(P + KW_CY);
        const float r = sqrtf(dx * dx + dy * dy);
        const float R = fmaxf(__ldg(P + KW_R), 1.f);
        float up = atan2f(dx, -dy) * (1.f / KW_TAU);
        up += up < 0.f ? 1.f : 0.f;
        const float vp = clamp01(1.f - r / R);
        u += (up - u) * pol;
        v += (vp - v) * pol;
        const float ir = 1.f / fmaxf(r, 0.02f * fH);   // the tangent shrinks to 0 inside 2% of the height
        tx += (-dy * ir - tx) * pol;
        ty += (dx * ir - ty) * pol;
        const float iu = ir * ir * (1.f / KW_TAU);
        jux += (-dy * iu - jux) * pol;
        juy += (dx * iu - juy) * pol;
        jvx += (-dx * ir / R - jvx) * pol;
        jvy += (-dy * ir / R - jvy) * pol;
        // the clock's 12 o'clock line joins the newest to the oldest: along it the currents ease to the calm home
        // current (the same on both sides, so the water keeps turning without a tear), and the seams and grade fade
        fade = 1.f - pol * (1.f - smooth(0.f, 0.03f, up) * (1.f - smooth(0.97f, 1.f, up)));
    }
    // the map drifts a little (alive with no notes); whole cycles in u, so the clock face has no seam in it
    const float da = __ldg(P + KW_DAMP);
    const float us = u + da * __sinf(KW_TAU * (1.3f * v + __ldg(P + KW_D1)));
    const float vs = v + 0.8f * da * __sinf(KW_TAU * (2.f * u + __ldg(P + KW_D2)));
    const float4 f = kf_tex(F, fw, fh, us, vs);
    const float4 a = kf_tex(V, fw, fh, us, vs);
    float cx_ = kf_fin(f.x), cy_ = kf_fin(f.y), ax_ = kf_fin(a.x), ay_ = kf_fin(a.y);
    {   // a slow meander winds the current (and the wide current the noise rides)
        float sm, cm;
        __sincosf(__ldg(P + KW_MEAN) * __sinf(KW_TAU * (us + 0.55f * vs + __ldg(P + KW_MPH))), &sm, &cm);
        float t = cx_ * cm - cy_ * sm;
        cy_ = cx_ * sm + cy_ * cm;
        cx_ = t;
        t = ax_ * cm - ay_ * sm;
        ay_ = ax_ * sm + ay_ * cm;
        ax_ = t;
        const float idle = __ldg(P + KW_IDLE);
        cx_ = idle + (cx_ - idle) * fade;
        cy_ *= fade;
        ax_ = idle + (ax_ - idle) * fade;
        ay_ *= fade;
    }
    // into the screen: along the frame t, and to its left n = (ty, -tx) (up, in the cartesian frame)
    const float cxs = cx_ * tx + cy_ * ty, cys = cx_ * ty - cy_ * tx;
    const float axs = ax_ * tx + ay_ * ty, ays = ax_ * ty - ay_ * tx;
    // the pulse's swell rises through the timescales and deepens the current as it passes; churn where keys meet
    const float rip = 1.f + __ldg(P + KW_RGAIN) * __ldg(P + KW_KICK)
                      * (0.5f + 0.5f * __cosf(KW_TAU * (2.5f * vs - __ldg(P + KW_RPH))));
    const float amp = rip * (1.f + __ldg(P + KW_RAP) * fade * clamp01(3.f * a.z));
    // the flow noise: two copies slide along the wide current, each restarting on a fresh part of the tile while
    // its weight is 0; a smooth per-pixel phase offset keeps the restarts from happening everywhere at once
    const float xn = px / fH, yn = py / fH, nph = __ldg(P + KW_NPH);
    const float psi = 0.37f * __sinf(2.1f * xn + nph + 1.7f * __sinf(1.6f * yn - 0.4f * xn + 0.5f * nph))
                    + 0.29f * __sinf(3.3f * yn - 1.1f * xn + 0.7f + nph);
    const float cyc = __ldg(P + KW_PH) + psi;
    const float k1 = floorf(cyc), k2 = floorf(cyc + 0.5f);
    const float f1 = cyc - k1, f2 = cyc + 0.5f - k2;
    const float w1 = 1.f - fabsf(2.f * f1 - 1.f), w2 = 1.f - w1;
    const float L = __ldg(P + KW_ADV), ox0 = __ldg(P + KW_OX), oy0 = __ldg(P + KW_OY);
    const float sx = __ldg(P + KW_SX), sy = __ldg(P + KW_SY);
    const float4 n1 = kw_ntex(N, xn - axs * L * f1 + ox0 + k1 * sx, yn - ays * L * f1 + oy0 + k1 * sy);
    const float4 n2 = kw_ntex(N, xn - axs * L * f2 + ox0 + k2 * sx + 0.71f, yn - ays * L * f2 + oy0 + k2 * sy + 0.43f);
    const float nr = rsqrtf(fmaxf(w1 * w1 + w2 * w2, 0.25f));
    const float na = (w1 * n1.w + w2 * n2.w) * nr;        // the combing noise
    const float ndx = (w1 * n1.y + w2 * n2.y) * nr;       // the swirl's stream function's gradient
    const float ndy = (w1 * n1.z + w2 * n2.z) * nr;
    // the displacement: combed along the current, swirled across it (curl: no compression), soft-limited
    const float Dc = __ldg(P + KW_D) * amp, Ds = __ldg(P + KW_CURL) * amp;
    const float m = sqrtf(cxs * cxs + cys * cys);
    float dxs = Dc * na * cxs + Ds * m * ndy;
    float dys = Dc * na * cys - Ds * m * ndx;
    const float dm = fmaxf(__ldg(P + KW_DMAX), 1.f);
    float m2 = dxs * dxs + dys * dys;
    if (m2 == m2 && m2 < 1e12f) {
        const float s = rsqrtf(1.f + m2 / (dm * dm));     // soft: ~1 for small, -> dm for large
        dxs *= s;
        dys *= s;
    } else {
        dxs = 0.f;
        dys = 0.f;
    }
    // the seams: their height's gradient in pixels (x a field texel) refracts toward the ridge, and lights a flank
    const float tp = __ldg(P + KW_TPX);
    float gx = kf_fin((f.z * jux + f.w * jvx) * tp), gy = kf_fin((f.z * juy + f.w * jvy) * tp);
    gx = fminf(fmaxf(gx, -2.f), 2.f);
    gy = fminf(fmaxf(gy, -2.f), 2.f);
    const float lk = __ldg(P + KW_LENS) * fade;
    float ox = lk * gx, oy = lk * gy;
    m2 = ox * ox + oy * oy;
    if (m2 > dm * dm) {
        const float s = dm * rsqrtf(m2);
        ox *= s;
        oy *= s;
    }
    // glint: the flank facing the light (upper left) brightens a little (toward white), the other darkens a little
    const float gl = __ldg(P + KW_GLINT) * fade
                     * fminf(fmaxf(-(gx * __ldg(P + KW_LX) + gy * __ldg(P + KW_LY)), -1.f), 1.f);
    const float gs = gl > 0.f ? 1.f - 0.3f * gl : 1.f + 0.25f * gl;
    const float gb = gl > 0.f ? 0.3f * gl : 0.f;
    const int o = j * lw + i;
    D[o] = make_float4(kf_fin(ox - dxs), kf_fin(oy - dys), kf_fin(gs), kf_fin(gb));
    // grade: toward the region's key colour at the picture's own luminance (weighted toward keys away from home)
    float4 cg = make_float4(0.f, 0.f, 0.f, 0.f);
    const float gr = __ldg(P + KW_GRADE) * fade;
    if (gr > 1e-4f) {
        const float4 g = kf_tex(G, fw, fh, us, vs);
        const float wv = clamp01(kf_fin(g.w));
        if (wv > 1e-3f) {
            const float iw = 1.f / fmaxf(g.w, 1e-3f);
            const float tr = clamp01(kf_fin(g.x) * iw), tg = clamp01(kf_fin(g.y) * iw), tb = clamp01(kf_fin(g.z) * iw);
            const float tl = fmaxf(0.2126f * tr + 0.7152f * tg + 0.0722f * tb, 0.08f);
            const float mk = clamp01(gr * wv);
            cg = make_float4(tr / tl * mk, tg / tl * mk, tb / tl * mk, mk);
        }
    }
    C[o] = cg;
}

// the two quarter-size buffers, bilinear at the full-size pixel (u, v in buffer texels), clamped to the edge
__device__ __forceinline__ float4 kw_lerp4(float4 a, float4 b, float4 c, float4 d, float tx, float ty) {
    const float sx = 1.f - tx, sy = 1.f - ty;
    return make_float4(sy * (sx * a.x + tx * b.x) + ty * (sx * c.x + tx * d.x),
                       sy * (sx * a.y + tx * b.y) + ty * (sx * c.y + tx * d.y),
                       sy * (sx * a.z + tx * b.z) + ty * (sx * c.z + tx * d.z),
                       sy * (sx * a.w + tx * b.w) + ty * (sx * c.w + tx * d.w));
}
__device__ __forceinline__ void kw_low2(const float4* __restrict__ D, const float4* __restrict__ C, int lw, int lh,
                                        float u, float v, float4& d, float4& c) {
    const float fx = fminf(fmaxf(u - 0.5f, 0.f), (float)(lw - 1));
    const float fy = fminf(fmaxf(v - 0.5f, 0.f), (float)(lh - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, lw - 1), y1 = min(y0 + 1, lh - 1);
    const float tx = fx - (float)x0, ty = fy - (float)y0;
    const int i00 = y0 * lw + x0, i10 = y0 * lw + x1, i01 = y1 * lw + x0, i11 = y1 * lw + x1;
    d = kw_lerp4(__ldg(D + i00), __ldg(D + i10), __ldg(D + i01), __ldg(D + i11), tx, ty);
    c = kw_lerp4(__ldg(C + i00), __ldg(C + i10), __ldg(C + i01), __ldg(C + i11), tx, ty);
}

// Full size: read the picture once where D says, glint, grade.
extern "C" __global__
void kw_apply(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
              const float4* __restrict__ D, const float4* __restrict__ C, const int lw, const int lh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = (float)x + 0.5f, py = (float)y + 0.5f;
    const float lu = px * (float)lw / (float)W, lv = py * (float)lh / (float)H;
    float4 d, g;
    kw_low2(D, C, lw, lh, lu, lv, d, g);
    float4 c = bilin(src, kw_fold(px + kf_fin(d.x), (float)W), kw_fold(py + kf_fin(d.y), (float)H), W, H);
    const float s = kf_fin(d.z), b = 255.f * kf_fin(d.w);
    c = make_float4(c.x * s + b, c.y * s + b, c.z * s + b, 255.f);
    const float mk = clamp01(kf_fin(g.w));
    if (mk > 1e-4f) {
        const float l = luma(c) * 255.f, k = 1.f - mk;
        c = make_float4(c.x * k + kf_fin(g.x) * l, c.y * k + kf_fin(g.y) * l, c.z * k + kf_fin(g.z) * l, 255.f);
    }
    out[y * W + x] = pack(c);
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")


def _fin(x, d: float = 0.0) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return d
    return x if math.isfinite(x) else d


def _c01(x) -> float:
    return min(max(_fin(x), 0.0), 1.0)


def noise_tile(seed: int) -> np.ndarray:
    """The flow noise (NTH, NTW, 4) float32 over NSX x NSY frame heights, seamless (FFT-synthesised, band-limited):
    (stream function psi, d psi / dx, d psi / dy, an independent combing noise), the gradient and the combing noise
    each scaled to RMS 1 (per frame height)."""
    rng = np.random.default_rng(int(seed) + 991)
    kx = np.fft.fftfreq(NTW, d=NSX / NTW)                 # cycles per frame height
    ky = np.fft.fftfreq(NTH, d=NSY / NTH)
    KX, KY = np.meshgrid(kx, ky)
    K = np.hypot(KX, KY)
    S = np.exp(-((K - NOISE_F0) / (0.45 * NOISE_F0)) ** 2) \
        + NOISE_OCT2 * np.exp(-((K - 2.3 * NOISE_F0) / (0.6 * NOISE_F0)) ** 2)
    S[0, 0] = 0.0
    z = np.fft.fft2(rng.standard_normal((NTH, NTW))) * S
    psi = np.real(np.fft.ifft2(z))
    gx = np.real(np.fft.ifft2(z * 2j * np.pi * KX))
    gy = np.real(np.fft.ifft2(z * 2j * np.pi * KY))
    comb = np.real(np.fft.ifft2(np.fft.fft2(rng.standard_normal((NTH, NTW))) * S))
    g = float(np.sqrt(np.mean(gx * gx + gy * gy))) or 1.0
    cs = float(np.std(comb)) or 1.0
    out = np.stack([psi / g, gx / g, gy / g, comb / cs], -1).astype(np.float32)
    return np.nan_to_num(out, copy=False)


class KeyfieldWarp(Effect):
    """Per frame: the timeline update, one key-field bake, two small field passes, a quarter-size pass, a full one."""
    name = "keyfield_warp"
    stage = "warp"
    label = "Key currents"
    description = ("The harmony becomes a current that ripples the picture: each region flows in the direction of "
                   "its key on the circle of fifths, home running calmly one way and modulations turning the "
                   "current, with glass seams where keys meet.")
    listens = ("notes",)
    params = {
        **standard_effect_params(),
        "energy": num(0.0, 1.0, 0.5, "Energy", tier="realtime", bind="level"),
        "strength": num(0.0, 1.0, 0.6, "Current strength", tier="regional", basic=True),
        "flow_speed": num(0.0, 2.0, 0.4, "Flow speed", unit="/s", tier="realtime", bind="rate"),
        "scale_span": num(10.0, 900.0, 90.0, "Seconds shown", step=1.0, unit="s", tier="regional"),
        "polar": num(0.0, 1.0, 0.0, "Whirlpool (time round a clock)", tier="regional", basic=True),
        "grade": num(0.0, 1.0, 0.35, "Key colour grade", tier="regional"),
        "seam": num(0.0, 1.0, 0.5, "Glass seams between keys", tier="regional"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, _OPTS)
        self._kt = mod.get_function("kw_target")
        self._kfl = mod.get_function("kw_flow")
        self._kw = mod.get_function("kw_field")
        self._ka = mod.get_function("kw_apply")
        self.seed = int(_fin(params.get("seed", 1), 1.0)) & 0x7FFFFFFF
        self.kf = KF.KeyField(ctx, FW, FH)
        self.E = cp.zeros((FH, FW, 4), cp.float32)        # the eased current (x, y, seam, -)
        self.G = cp.zeros((FH, FW, 4), cp.float32)        # the eased key colour, premultiplied (r, g, b, weight)
        self.F = cp.zeros((FH, FW, 4), cp.float32)        # the current and the seam's gradient
        self.V = cp.zeros((FH, FW, 4), cp.float32)        # the wide current (x, y) and the wide seam height
        self.N = cp.asarray(noise_tile(self.seed))        # the flow noise tile
        self.lw = self.lh = 0
        self.D = self.C = None                            # quarter size: where to read, glint; the grade
        self.Q = cp.zeros(NQ, cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self._q = np.zeros(NQ, np.float32)
        self._p = np.zeros(NP, np.float32)
        self._own = KF.KeyTimeline(self.seed)             # used while there is no sound object
        self._snap = True                                 # the next field pass snaps to its target
        self.home_v = np.array([1.0, 0.0])
        self._home_seen = False
        self.home = 0.0
        rng = np.random.default_rng(self.seed + 77)
        self.phase = float(rng.uniform(0.0, 1.0))         # the flow noise's phase (cycles)
        self.cycle = int(rng.integers(0, 1 << 16))        # whole cycles so far: picks the tile offset
        self.rph = 0.0                                    # the swell's phase (cycles)
        self.d1, self.d2, self.mph = (float(v) for v in rng.uniform(0.0, 1.0, 3))
        self.breath = float(rng.uniform(0.0, 1.0))
        self.wander = float(rng.uniform(0.0, 1.0))
        self.nph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.kick = 0.0

    def _timeline(self) -> KF.KeyTimeline:
        snd = getattr(self, "sound", None)
        return self._own if snd is None else KF.KeyTimeline.shared(snd, self.seed)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = _fin(dt)
        dt = dt if dt > 0.0 else 0.0
        dta = min(dt, 1.0)                                # our own motion; the timeline gets the real dt
        snd = getattr(self, "sound", None)
        tl = self._timeline()
        tl.update(snd, dt, PIECE_GAP)

        amount = _c01(p["amount"])
        pulse = _c01(p["pulse"])
        e = _c01(p["energy"])
        # motion: integrated phases (pulse kicks the rate, never a position)
        tau = 0.08 if pulse > self.kick else 0.5
        self.kick += (pulse - self.kick) * T.ema_alpha(dta, tau)
        rate = max(_fin(p["flow_speed"], 0.4), 0.0) * RATE_K * (0.5 + e) * (1.0 + 0.8 * self.kick)
        ph = self.phase + min(rate * dta, 4.0)
        whole = math.floor(ph)
        self.phase = ph - whole
        self.cycle = (self.cycle + int(whole)) % (1 << 20)
        self.rph = math.fmod(self.rph + 0.6 * dta, 1.0)
        self.d1 = math.fmod(self.d1 + 0.019 * dta, 1.0)
        self.d2 = math.fmod(self.d2 + 0.013 * dta, 1.0)
        self.mph = math.fmod(self.mph + 0.023 * dta, 1.0)
        self.breath = math.fmod(self.breath + 0.065 * dta, 1.0)
        self.wander = math.fmod(self.wander + 0.009 * dta, 1.0)
        if amount <= 1e-4:
            self._snap = True                             # the field is stale when the effect comes back
            self.ctx.cp.copyto(out, src)
            return

        W, H = self.ctx.width, self.ctx.height
        # the view: the last scale_span seconds, or the whole piece stretched across while it is shorter
        now = _fin(tl.now())
        lo = max(0.0, now - KF.CAP)
        want = min(max(_fin(p["scale_span"], 90.0) / KF.BIN_S, 2.0 * KF.L_MIN), float(KF.CAP))
        span = min(want, max(now - lo, MIN_SPAN))
        self.kf.bake(tl, KF.TRAILING, span, LOG_ROWS, 0.0)

        # home: the summit key's angle on the circle of fifths, eased as a unit vector (no wrap-around spin)
        sk = int(self.kf.summit_key)
        if sk >= 0:
            a = KF.key_angle(sk)
            tgt = np.array([math.cos(a), math.sin(a)])
            if not self._home_seen:
                self.home_v[:] = tgt
                self._home_seen = True
            else:
                self.home_v += (tgt - self.home_v) * T.ema_alpha(dta, HOME_TAU_S)
        if float(np.hypot(*self.home_v)) > 1e-6 and np.all(np.isfinite(self.home_v)):
            self.home = math.atan2(float(self.home_v[1]), float(self.home_v[0]))
        else:
            self.home_v[:] = (math.cos(self.home), math.sin(self.home))

        q = self._q
        q.fill(0.0)
        q[0] = 1.0 if self._snap else T.ema_alpha(dta, FIELD_TAU_S)
        q[1] = self.home
        q[2] = IDLE
        q[3] = FAR_GAIN
        q[4] = GRADE_HOME
        self._snap = False
        self.Q.set(q)
        launch_2d(self._kt, FW, FH, (self.kf.A, self.kf.B, self.E, self.G, np.int32(FW), np.int32(FH), self.Q))
        launch_2d(self._kfl, FW, FH, (self.E, self.F, self.V, np.int32(FW), np.int32(FH)))

        strength = _c01(p["strength"])
        seam = _c01(p["seam"])
        grade = _c01(p["grade"])
        base = 0.3 * math.sin(2.0 * math.pi * self.wander)   # the home current's direction wanders +-17 degrees
        depth = strength * amount * (0.7 + 0.6 * e) * (1.0 + 0.12 * math.sin(2.0 * math.pi * self.breath))
        P = self._p
        P.fill(0.0)
        P[0] = _c01(p["polar"])
        P[1], P[2] = math.cos(base), math.sin(base)
        P[3] = COMB_H * H * depth
        P[4] = self.phase
        P[5] = LENS_H * H * seam * amount
        P[6] = GLINT * seam * amount
        P[7] = GRADE_MAX * grade * amount
        P[8] = self.kick
        P[9] = self.rph
        P[10] = RIPPLE_GAIN
        P[11], P[12], P[13] = self.d1, self.d2, DRIFT_UV
        P[14] = 0.5 * math.hypot(W, H)
        P[15], P[16] = 0.5 * W, 0.5 * H
        P[17] = H / FH
        P[18] = 0.25 + 0.3 * e
        P[19], P[20] = -0.6, -0.8
        P[21] = DMAX_H * H
        P[22] = self.nph
        P[23] = self.mph
        P[24] = CURL_H * H * depth
        P[25] = ADV_H
        P[26] = math.fmod(self.cycle * CYC_STEP[0], NSX)
        P[27] = math.fmod(self.cycle * CYC_STEP[1], NSY)
        P[28] = RAPIDS * seam
        P[29], P[30] = CYC_STEP
        P[31] = IDLE
        if not np.all(np.isfinite(P)):                     # never hand a kernel NaN
            np.nan_to_num(P, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.P.set(P)
        lw, lh = max((W + LOW - 1) // LOW, 2), max((H + LOW - 1) // LOW, 2)
        if self.D is None or (lw, lh) != (self.lw, self.lh):
            cp = self.ctx.cp
            self.lw, self.lh = lw, lh
            self.D = cp.zeros((lh, lw, 4), cp.float32)
            self.C = cp.zeros((lh, lw, 4), cp.float32)
        launch_2d(self._kw, lw, lh, (self.D, self.C, np.int32(lw), np.int32(lh), np.int32(W), np.int32(H), self.F,
                                     self.V, self.G, self.N, np.int32(FW), np.int32(FH), self.P))
        launch_2d(self._ka, W, H, (src, out, np.int32(W), np.int32(H), self.D, self.C, np.int32(lw), np.int32(lh)))

    def close(self) -> None:
        if getattr(self, "kf", None) is not None:
            self.kf.close()
        self.kf = None
        self.E = self.G = self.F = self.V = self.N = self.D = self.C = self.Q = self.P = None
