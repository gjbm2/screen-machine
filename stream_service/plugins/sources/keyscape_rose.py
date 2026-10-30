"""Key rose: the piano's live key analysis as a turning rose window of stained glass, a clock hand laying new glass.

What the picture shows
  A round stained-glass window, a rose, glowing on a dark night.  Each piece of glass is coloured by the key of a
  stretch of the music you have just played, with the same colours as Keyscape live: the hue follows the circle of
  fifths (C rose-red, G next to it, and so on, so related keys are neighbouring hues), major keys are light glass,
  minor keys a deeper shade of the same hue, a clear key glows in full colour, an ambiguous stretch fades to grey
  glass and a near-silent one is pale mist.  Thin dark lead lines run only where the key really changes between
  neighbouring pieces of music (both sides clearly keyed, neighbours in time, the pieces not slivers); where the
  petals meet the glass only shades softly, and where the newest glass meets the oldest it dusks gently.

  Going round the window is time, and the window is always full.  While the piece is young, one turn is the whole
  piece so far, stretched round the window (a new piece opens round the turn over the old glass in its first three
  seconds); once the piece is longer than 0.6 x ``revolution_s`` the turn eases to exactly the last
  ``revolution_s`` seconds (reached at 1.4 x).  A soft bright clock hand sweeps round while you play (one turn per
  ``revolution_s``), with the newest glass behind it; on a long piece the glass stays where it was laid until the
  hand comes round again, and the oldest glass dusks just before it is overwritten.  When the piano rests the hand
  stops; the window keeps turning slowly and breathing.  With no piece yet the window shows the key field's example
  spread over the whole turn.

  Going in from the rim to the centre is how long each piece of glass listens.  At the rim every piece hears one
  second (the moment: busy, small pieces that shimmer); half-way in, a few bars; next to the centre, the whole turn.
  The round jewel in the middle (the oculus) is the key of the whole piece so far, and the glow around the window is
  tinted with it.  So the rim is the harmony moment to moment and the centre is the home key; where the rim's
  colours differ from the centre's, the music is wandering from home.

  ``folds`` repeats the turn round the window into petals (2..12), and ``mirror`` makes each petal mirror-symmetric,
  so the window becomes a kaleidoscopic rose with several hands sweeping and meeting; between them the morph blends
  neighbouring fold counts.  ``centred`` morphs to Sapp's own windows (centred on each moment rather than ending
  there): his keyscape mountain wrapped round so its summit is the centre; the stretches whose windows still reach
  into the future (or, while the turn is the whole piece, back before it began) are dim glass: leaves either side
  of the hand.  ``wave_mix`` recolours the glass the Wavescapes way
  (hue from the phase of the fifth Fourier coefficient, saturation from its size: how diatonic the stretch is).

How the analysis maps onto it
  The analysis is the shared key field (``_lib/keyfield.py``, Keyscape live's maths: 0.5 s pitch-class bins from the
  piano's notes, Krumhansl-Kessler keys by Pearson's r, clarity, fog, the F5 coefficient).  Each frame the shared
  timeline is advanced and one or two KeyFields are baked over the view [now - turn, now] (turn = the whole piece,
  at least 3 s, eased C1 to ``revolution_s``: ``_turn_span``): the TRAILING field (window ending at each moment)
  and, when ``centred`` > 0, the CENTRED one.  Pixel (r, theta): theta minus the slow rotation, folded (``folds`` /
  ``mirror``), gives a coordinate g in 0..1; the age a = frac(hand - g) is how many turns ago that angle was swept,
  and the field is sampled at u = 1 - a (u 1 = now); v = (R - r) / (R - r0) is the window length, logarithmic from
  1 s at the rim to the whole turn at the oculus edge.  The hand (in turns) advances by the music's clock over
  ``revolution_s``, so on a long piece glass is anchored where it was laid (while the turn stretches, the glass
  stretches with it).  A trailing window that would reach back before the piece began shows the piece's first full
  window of that length instead (else every ring there shows the same first notes).  Validity (the part of a turn
  before the piece began, while it opens) is computed analytically per pixel with a one-pixel soft edge; centred
  windows that reach into the future or back before the piece are dim glass.  Lead lines compare the key ids
  (nearest texel) a pixel or so either side, along and across the circle, along it only between probes that are
  neighbours in time.  The previous piece's planes are kept in two preallocated buffers (copied once when a new
  piece starts; seeded with the example in setup) and shown, dimmed and without lead lines, where the new piece has
  not reached yet.

Macros: energy -> the backlight, the sheen's speed and brightness, the rim's shimmer, the rotation rate, the hand's
glow and the halo; pulse -> a kick (eased in, never a flash) to the rim's brightness, the hand and the halo, and a
little to the rotation rate; speed -> every rate (rotation, sheen, shimmer, breathing); intensity -> gain.  No
palette: the key colours are the content; ``hue_shift`` turns them (0..1 = 0..180 degrees).  Alive with no notes:
the window turns, breathes, the sheen sweeps, the rim shimmers and the oculus glows (the cold start shows the key
field's synthetic example: I-IV-V-I in C, a modulation to G and back).

Host diet: per frame the shared timeline's update (once per screen), the KeyField bake(s) (one 128-byte arena and one
small launch each, nothing when unchanged), one arena upload (~0.2 KB) and two launches (the window, the upsample).

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, https://esf.ccarh.org/254-old/254_LiteraturePack1/Harmony/Sapp_VisualHierKeyAnal05.pdf; C. Viaccoz,
        D. Harasim, F. C. Moss, M. Rohrmeier, "Wavescapes: a visual hierarchical analysis of tonal music using
        discrete Fourier transform", Musicae Scientiae 2022, https://infoscience.epfl.ch/record/291958 (their
        wavescapes code is GPL-3.0: nothing of it is used, only the published formulas, via _lib/keyfield.py);
        Gothic rose windows.  No code borrowed from other plug-ins.)
Author: smfx (original)
License: MIT (original)
license_class: permissive
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import keyfield as KF
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

FW, FH = 576, 192               # key-field planes: u (time round a turn) x v (window length)
LOGR = 0.9                      # window lengths nearly logarithmic in v
R_FRAC = 0.455                  # the rim radius / internal height
R0_FRAC = 0.09                  # the oculus radius / rim radius
RIM_W = 0.024                   # the stone rim's width / rim radius
V_POW = 0.8                     # v = ((R - r) / (R - r0))^V_POW: the shortest windows take less of the rim
EX_DIM = 0.88                   # the cold-start example is shown slightly dimmed
GHOST_DIM = 0.6                 # the previous piece's glass settles to this while the new one covers it
UNFURL = 6.0                    # bins (3 s): a new piece's glass opens round the whole turn over the old glass
STRETCH_A, STRETCH_B = 0.6, 1.4  # the turn is the whole piece up to 0.6 revolutions of music, one revolution from 1.4
SEAM_FIXED, SEAM_STRETCH = 0.34, 0.10   # the dusk before the hand: a turn of fixed length / the whole piece
MULL_W = 0.16                   # the soft valley where the petals meet (darkening at its middle)

# arena (floats): names -> indices, shared by the host and the kernel
_AR = ("W", "H", "CX", "CY", "R", "R0", "ROT", "HAND", "F", "MIR", "WT", "WC", "WAVE", "LEAD", "HUE", "EN", "KICK",
       "GAIN", "SHEEN", "BREATH", "BL", "SHEENA", "SHP", "SHA", "SC", "FW", "FH", "SPAN", "ORG", "NOW", "LO", "LOGR",
       "LMIN", "GW", "DH", "SR", "SG", "SB", "HANDS", "DIM", "RIMK", "HALO", "SEAM", "SDX", "SDY", "SFQ", "MULL",
       "SKY")
I = {k: i for i, k in enumerate(_AR)}
N_AR = 64
assert len(_AR) <= N_AR

_SRC = HELPERS + KF.KEYFIELD_CUDA + "".join(f"#define RK_{k} {i}\n" for k, i in I.items()) + r"""
#define RK_NAR """ + str(N_AR) + r"""
#define RK_TAU 6.2831853f
#define RK_NOISE 256
#define RK_VPOW """ + repr(V_POW) + r"""f

// a tiling smooth-noise texture (host-made), bilinear, wrapping
__device__ __forceinline__ float rk_noise(const float* __restrict__ Tx, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = ((int)fu) & (RK_NOISE - 1), y0 = ((int)fv) & (RK_NOISE - 1);
    const int x1 = (x0 + 1) & (RK_NOISE - 1), y1 = (y0 + 1) & (RK_NOISE - 1);
    const float tx = u - fu, ty = v - fv;
    const float a = Tx[y0 * RK_NOISE + x0], b = Tx[y0 * RK_NOISE + x1];
    const float c = Tx[y1 * RK_NOISE + x0], d = Tx[y1 * RK_NOISE + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

// a plane sampled bilinearly with each texel-to-texel blend squeezed into the middle of the gap: crisper pieces
// of glass where texels are large on screen, still anti-aliased (clamped to the edge, texel centres at (i + 0.5) / n)
__device__ __forceinline__ float4 rk_tex_sharp(const float4* __restrict__ T, int fw, int fh, float u, float v) {
    const float fx = fminf(fmaxf(u * (float)fw - 0.5f, 0.f), (float)(fw - 1));
    const float fy = fminf(fmaxf(v * (float)fh - 0.5f, 0.f), (float)(fh - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, fw - 1), y1 = min(y0 + 1, fh - 1);
    const float tx = smooth(0.15f, 0.85f, fx - (float)x0), ty = smooth(0.15f, 0.85f, fy - (float)y0);
    const float4 a = T[y0 * fw + x0], b = T[y0 * fw + x1], c = T[y1 * fw + x0], d = T[y1 * fw + x1];
    const float sx = 1.f - tx, sy = 1.f - ty;
    return make_float4(sy * (sx * a.x + tx * b.x) + ty * (sx * c.x + tx * d.x),
                       sy * (sx * a.y + tx * b.y) + ty * (sx * c.y + tx * d.y),
                       sy * (sx * a.z + tx * b.z) + ty * (sx * c.z + tx * d.z),
                       sy * (sx * a.w + tx * b.w) + ty * (sx * c.w + tx * d.w));
}

// the age (0 at the hand .. 1 a turn ago) of the turn fraction tc, folded Fi times (mirrored or repeated)
__device__ __forceinline__ float rk_age(float tc, float Fi, int mir, float hand) {
    const float x = tc * Fi;
    const float p = x - floorf(x);
    const float g = mir ? 1.f - fabsf(2.f * p - 1.f) : p;
    const float q = hand - g;
    return q - floorf(q);
}

// the window length (bins) at v, as the bake (L_MIN at v 0 .. span at v 1)
__device__ __forceinline__ float rk_len(const float* P, float v, float span) {
    const float Lmin = fmaxf(P[RK_LMIN], 1e-3f);
    span = fmaxf(span, 2.f * Lmin);
    const float lin = Lmin + (span - Lmin) * v, lg = Lmin * __powf(span / Lmin, v);
    return lin + (lg - lin) * P[RK_LOGR];
}

// a lead line between two plane-A texels: both keyed, different keys, both clear
__device__ __forceinline__ float rk_edge(float4 p, float4 q) {
    const int i = kf_key_id(p.x), j = kf_key_id(q.x);
    if (i < 0 || j < 0 || i == j) return 0.f;
    return smooth(0.3f, 0.5f, fminf(p.y, q.y));
}

// one field's glass at (u, v): sRGB colour (key, or the wavescape mixed in), home, and the lead line from the key
// ids at the probes either side along the circle (uL, uR; weighted by adj, 0 where the probes are not neighbours in
// time: the seam where the newest glass meets the oldest, a fold's wrap) and across it (vD, vU)
__device__ void rk_glass(const float* P, const float4* __restrict__ A, const float4* __restrict__ B,
                         const float4* __restrict__ C, float wave, float u, float v, float uL, float uR, float adj,
                         float uD, float vD, float uU, float vU, bool lon, float3& col, float& home, float& line)
{
    const int fw = (int)P[RK_FW], fh = (int)P[RK_FH];
    const float4 b = rk_tex_sharp(B, fw, fh, u, v);
    col = make_float3(b.x, b.y, b.z);
    home = b.w;
    if (wave > 0.001f) {
        const float4 c = rk_tex_sharp(C, fw, fh, u, v);
        const float dn = clamp01(c.w);
        const float3 w = kf_oklab_srgb(kf_mist(kf_wave_lab(c, P[RK_HUE]), 1.f - dn * dn * (3.f - 2.f * dn)));
        col.x += (w.x - col.x) * wave; col.y += (w.y - col.y) * wave; col.z += (w.z - col.z) * wave;
    }
    line = 0.f;
    if (lon) {                                                     // leads wanted here (not on slivers)
        line = fmaxf(adj > 0.001f ? adj * rk_edge(kf_tex_nearest(A, fw, fh, uL, v), kf_tex_nearest(A, fw, fh, uR, v))
                                  : 0.f,
                     rk_edge(kf_tex_nearest(A, fw, fh, uD, vD), kf_tex_nearest(A, fw, fh, uU, vU)));
    }
}

struct RkS { float3 c; float line; float emit; float mull; float home; };

// one mapping (Fi folds, mirrored or not) of the pixel (tc, r, v): the glass from the fields (and the previous
// piece's where this one has not reached), the hand's light, the petals' stone mullions
__device__ RkS rk_sample(const float* P,
                         const float4* __restrict__ TA, const float4* __restrict__ TB, const float4* __restrict__ TC,
                         const float4* __restrict__ CA, const float4* __restrict__ CB, const float4* __restrict__ CC,
                         const float4* __restrict__ GA, const float4* __restrict__ GB,
                         float tc, float r, float v, float vD, float vU, float Lw, float LwD, float LwU,
                         float Fi, int mir)
{
    RkS s;
    const float sc = P[RK_SC], hand = P[RK_HAND];
    const float a = rk_age(tc, Fi, mir, hand);
    const float circ = RK_TAU * fmaxf(r, 0.5f);
    const float dudpx = (mir ? 2.f * Fi : Fi) / circ;              // u per pixel along the circle
    const float dl = fmaxf(1.25f * sc, 0.9f);                      // the lead probes' half distance (px)
    const float dtc = dl / circ;
    const float aL = rk_age(tc - dtc, Fi, mir, hand), aR = rk_age(tc + dtc, Fi, mir, hand);
    const float u = 1.f - a, uL = 1.f - aL, uR = 1.f - aR;
    const float eda = 2.f * dl * dudpx;                            // the probes' age gap where the glass is continuous
    const float adj = 1.f - smooth(2.5f * eda, 5.f * eda, fabsf(aR - aL));
    const float span = fmaxf(P[RK_SPAN], 1e-3f), now = P[RK_NOW], lo = P[RK_LO], org = P[RK_ORG];
    const float pos = org + u * span;                              // the bin this angle shows
    const float ppx = 1.f / fmaxf(dudpx * span, 1e-6f);            // px per bin along the circle (soft edges)
    const float lf = smooth(8.f * sc, 22.f * sc, Lw * ppx);        // no lead on slivers (the rim's shimmer)
    const bool lon = P[RK_LEAD] > 0.f && lf > 0.002f;
    // valid once the piece began (no soft edge when the whole turn is inside the piece: no dark line at the seam)
    const float kin = org >= lo - 0.01f ? 1.f : clamp01((pos - lo) * ppx + 0.5f);
    float3 col = make_float3(0.f, 0.f, 0.f);
    float line = 0.f, home = 0.f, known = 0.f;
    const float wT = P[RK_WT], wC = P[RK_WC], wave = P[RK_WAVE];
    float3 c;
    float h, l;
    if (wT > 0.f) {                                                // windows ending here
        // a window that would reach back before the piece began shows the piece's first full window of its length
        // instead (otherwise every ring there shows the same first notes: a dull wedge at the piece's start)
        const float ul = lo + Lw, isp = 1.f / span;
        const float ut = (fminf(fmaxf(pos, ul), now) - org) * isp;
        const float utL = (fminf(fmaxf(org + uL * span, ul), now) - org) * isp;
        const float utR = (fminf(fmaxf(org + uR * span, ul), now) - org) * isp;
        const float utD = (fminf(fmaxf(pos, lo + LwD), now) - org) * isp;
        const float utU = (fminf(fmaxf(pos, lo + LwU), now) - org) * isp;
        rk_glass(P, TA, TB, TC, wave, ut, v, utL, utR, adj, utD, vD, utU, vU, lon, c, h, l);
        const float k = wT * kin;
        col.x += k * c.x; col.y += k * c.y; col.z += k * c.z;
        line += k * l; home += k * h; known += k;
    }
    if (wC > 0.f) {                                                // windows centred here (Sapp's)
        rk_glass(P, CA, CB, CC, wave, u, v, uL, uR, adj, u, vD, u, vU, lon, c, h, l);
        const float hl = 0.5f * Lw;
        const float k = wC * kin;
        // dim where the window still reaches into the future or back before the piece: Sapp's triangle glows
        const float lit = 0.42f + 0.58f * clamp01(fminf(now - pos - hl, pos - hl - lo) * ppx + 0.5f);
        col.x += k * lit * c.x; col.y += k * lit * c.y; col.z += k * lit * c.z;
        line += k * lit * l; home += k * h; known += k;
    }
    const float rest = clamp01(1.f - known);
    if (rest > 0.002f) {                                           // before this piece: the last one's glass, or unlit
        float3 gc = make_float3(0.030f, 0.033f, 0.045f);
        float gh = 1.f;
        const float gw = P[RK_GW];
        if (gw > 0.f) {
            const float dH = P[RK_DH];
            float ag = a - dH;
            ag -= floorf(ag);
            // the old glass without its lead lines: a soft, dimmed memory under the new piece as it opens
            const float ug = 1.f - ag;
            rk_glass(P, GA, GB, GB, 0.f, ug, v, ug, ug, 0.f, ug, vD, ug, vU, false, c, h, l);
            gc.x += (c.x * gw - gc.x) * fminf(gw * 2.f, 1.f);
            gc.y += (c.y * gw - gc.y) * fminf(gw * 2.f, 1.f);
            gc.z += (c.z * gw - gc.z) * fminf(gw * 2.f, 1.f);
            gh = h;
        }
        col.x += rest * gc.x; col.y += rest * gc.y; col.z += rest * gc.z;
        home += rest * gh;
    }
    // the hand: the newest glass glows; the oldest dusks softly just before the hand comes round to it (a gentle
    // valley, a little on the new side too, never a black line)
    const float dn = a / dudpx, dold = (1.f - a) / dudpx;
    const float sw = 64.f * sc;
    const float fo = 1.f - smooth(0.f, sw, dold), fn = 1.f - smooth(0.f, 0.25f * sw, dn);
    const float fade = 1.f - P[RK_SEAM] * (fo * fo * (0.6f + 0.4f * fo) + 0.35f * fn * fn);
    s.c = make_float3(col.x * fade, col.y * fade, col.z * fade);
    s.emit = P[RK_HANDS] * (__expf(-fminf(dn, dold) / (2.2f * sc)) + 0.5f * __expf(-dn / (24.f * sc)));
    s.line = line * lf;
    s.home = home;
    // where the petals meet: a soft shaded valley in the glass (none for a single fold)
    const float x = tc * Fi, p = x - floorf(x);
    const float dm = fminf(p, 1.f - p) / Fi * circ;
    const float mq = dm / fmaxf(fminf(9.f * sc, 0.12f * circ / Fi), 0.5f);    // narrower near the oculus
    s.mull = P[RK_MULL] * smooth(1.4f, 2.2f, Fi) * __expf(-mq * mq);
    return s;
}

// P: see _AR in the host code.  T*: the TRAILING field's planes, C*: the CENTRED field's, G*: the previous piece's.
extern "C" __global__
void rk_main(uchar4* __restrict__ out, const float* __restrict__ Ar, const float* __restrict__ tex,
             const float4* __restrict__ TA, const float4* __restrict__ TB, const float4* __restrict__ TC,
             const float4* __restrict__ CA, const float4* __restrict__ CB, const float4* __restrict__ CC,
             const float4* __restrict__ GA, const float4* __restrict__ GB, const unsigned int seed)
{
    __shared__ float P[RK_NAR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < RK_NAR; i += blockDim.x * blockDim.y) P[i] = Ar[i];
    __syncthreads();
    const int W = (int)P[RK_W], Hh = (int)P[RK_H];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= Hh) return;
    const float px = x + 0.5f, py = y + 0.5f, sc = P[RK_SC];
    const float dx = px - P[RK_CX], dy = py - P[RK_CY];
    const float r = sqrtf(dx * dx + dy * dy) / fmaxf(P[RK_BREATH], 0.5f);
    const float R = fmaxf(P[RK_R], 8.f), r0 = fminf(fmaxf(P[RK_R0], 1.f), 0.5f * R);
    float tc = atan2f(dy, dx) * (1.f / RK_TAU) - P[RK_ROT];
    tc -= floorf(tc);
    const float q = r / R;
    const float3 sk = make_float3(P[RK_SR], P[RK_SG], P[RK_SB]);
    const float rimw = RIM_W_C * R;

    // the night behind: blue-black, the summit key's glow round the window, faint rays from it
    float3 o = make_float3(0.f, 0.f, 0.f);
    if (r > R - 2.f) {
        const float vy = py / (float)Hh;
        o = make_float3(0.007f + 0.010f * vy, 0.008f + 0.010f * vy, 0.018f + 0.016f * vy);
        const float d = fmaxf(q - 1.f, 0.f);
        const float halo = P[RK_HALO] * (0.75f * __expf(-d * 5.f) + 0.25f * __expf(-d * 1.3f));
        const float ray = smooth(0.42f, 0.85f, rk_noise(tex, tc * 256.f, 60.f + P[RK_SKY]));
        const float rays = 0.45f * P[RK_HALO] * ray * __expf(-d * 1.8f);
        o.x += (halo + rays) * sk.x; o.y += (halo + rays) * sk.y; o.z += (halo + rays) * sk.z;
    }

    if (r < R + rimw + 3.f * sc) {
        const float iw0 = 1.f / fmaxf(R - r0, 1.f), dl = fmaxf(1.25f * sc, 0.9f);
        const float v = __powf(clamp01((R - r) * iw0), RK_VPOW);
        const float vD = __powf(clamp01((R - r - dl) * iw0), RK_VPOW);
        const float vU = __powf(clamp01((R - r + dl) * iw0), RK_VPOW);
        const float spn = P[RK_SPAN];                              // the windows (bins) there, the same for every fold
        const float Lw = rk_len(P, v, spn), LwD = rk_len(P, vD, spn), LwU = rk_len(P, vU, spn);
        float3 gc = make_float3(0.f, 0.f, 0.f);
        float line = 0.f, emit = 0.f, mull = 0.f, home = 1.f;
        if (r > r0 - 2.f) {
            const float F = fminf(fmaxf(P[RK_F], 1.f), 12.f);
            const float F0 = floorf(F);
            const float wf = F0 >= 12.f ? 0.f : smooth(0.35f, 0.65f, F - F0);     // blend to the next fold count
            const float wm = smooth(0.25f, 0.75f, clamp01(P[RK_MIR]));
            float wsum = 0.f;
            home = 0.f;
            #pragma unroll 1
            for (int i = 0; i < 4; ++i) {
                const float w = ((i & 1) ? wf : 1.f - wf) * ((i & 2) ? wm : 1.f - wm);
                if (w < 1e-3f) continue;
                const RkS s = rk_sample(P, TA, TB, TC, CA, CB, CC, GA, GB, tc, r, v, vD, vU, Lw, LwD, LwU,
                                         F0 + (float)(i & 1), (i >> 1) & 1);
                gc.x += w * s.c.x; gc.y += w * s.c.y; gc.z += w * s.c.z;
                line += w * s.line; emit += w * s.emit; mull += w * s.mull; home += w * s.home;
                wsum += w;
            }
            const float iw = wsum > 1e-6f ? 1.f / wsum : 0.f;
            gc.x *= iw; gc.y *= iw; gc.z *= iw;
            line *= iw; emit *= iw; mull *= iw; home = wsum > 1e-6f ? home * iw : 1.f;
        }
        // light through the glass, in the glass's own (turning) frame: mottle, the rim's shimmer, the backlight
        const float cs = __cosf(RK_TAU * tc), sn = __sinf(RK_TAU * tc);
        const float xr = r * cs / sc, yr = r * sn / sc;
        const float mot = 1.f + 0.12f * (rk_noise(tex, xr / 9.f + 40.f, yr / 9.f + 70.f) - 0.5f)
                              + 0.06f * (rk_noise(tex, xr / 2.3f, yr / 2.3f + 130.f) - 0.5f);
        const float rim2 = (1.f - v) * (1.f - v);
        const float sp = P[RK_SHP];
        const float tw = rk_noise(tex, xr / 3.1f + sp, yr / 3.1f + 200.f)          // two layers drifting apart:
                       + rk_noise(tex, xr / 2.7f - sp + 90.f, yr / 2.7f + sp) - 1.f;  // a twinkle (sp wraps at 256)
        const float shim = 1.f + P[RK_SHA] * rim2 * 2.f * tw;
        const float bl = P[RK_BL] * mot * shim * (1.06f - 0.12f * clamp01(home)) * (1.f + P[RK_RIMK] * rim2);
        float3 g = make_float3(gc.x * bl, gc.y * bl, gc.z * bl);
        // the sheen: a narrow band and a broad one sweeping across, and soft rings travelling outward
        const float d = (px * P[RK_SDX] + py * P[RK_SDY]) * P[RK_SFQ];
        const float s1 = __powf(fmaxf(0.f, __sinf(d - RK_TAU * P[RK_SHEEN])), 40.f);
        const float s2 = __powf(fmaxf(0.f, __sinf(0.5f * d - RK_TAU * 0.61f * P[RK_SHEEN] + 1.3f)), 6.f);
        const float s3 = __powf(fmaxf(0.f, __sinf(RK_TAU * (2.f * (1.f - v) - 0.4f * P[RK_SHEEN]))), 12.f);
        const float sh = P[RK_SHEENA] * (s1 + 0.3f * s2 + 0.35f * s3);
        g.x += sh * 0.95f + emit; g.y += sh * 0.97f + emit * 0.86f; g.z += sh + emit * 0.68f;
        const float dim = P[RK_DIM];
        g.x *= dim; g.y *= dim; g.z *= dim;
        // the petals' soft valleys, then the lead lines (only at real key boundaries)
        const float mk = 1.f - clamp01(mull);
        g.x *= mk; g.y *= mk; g.z *= mk;
        const float lw = clamp01(P[RK_LEAD] * 0.9f * line);
        g.x += (0.018f - g.x) * lw; g.y += (0.017f - g.y) * lw; g.z += (0.022f - g.z) * lw;
        // the oculus: the key of the whole piece
        if (r < r0 + 2.f) {
            const float qq = r / r0;
            const float ob = P[RK_BL] * mot * (1.22f - 0.42f * qq * qq) * dim;
            const float3 oc = make_float3(sk.x * ob + 0.6f * sh, sk.y * ob + 0.6f * sh, sk.z * ob + 0.6f * sh);
            const float cov = clamp01(r0 - r + 0.5f);
            g.x += (oc.x - g.x) * cov; g.y += (oc.y - g.y) * cov; g.z += (oc.z - g.z) * cov;
        }
        // the stone ring round the oculus
        {
            const float hw = fmaxf(0.011f * R, 1.5f * sc);
            const float dd = fabsf(r - r0);
            const float st = 1.f - smooth(hw - 0.75f, hw + 0.75f, dd);
            const float hi = 0.10f * (1.f - clamp01(dd / hw));
            const float3 sc3 = make_float3(0.030f + hi * sk.x, 0.028f + hi * sk.y, 0.032f + hi * sk.z);
            g.x += (sc3.x - g.x) * st; g.y += (sc3.y - g.y) * st; g.z += (sc3.z - g.z) * st;
        }
        const float cov = clamp01(R - r + 0.5f);
        o.x += (g.x - o.x) * cov; o.y += (g.y - o.y) * cov; o.z += (g.z - o.z) * cov;
        // the stone rim, its outer edge lit by the halo (and the pulse)
        {
            const float mid = R + 0.5f * rimw - 0.5f;
            const float dd = fabsf(r - mid);
            const float st = 1.f - smooth(0.5f * rimw, 0.5f * rimw + 1.f, dd);
            const float edge = clamp01((r - R) / fmaxf(rimw, 1.f));
            const float hi = (0.05f + 0.6f * P[RK_HALO] + 0.25f * P[RK_RIMK]) * edge * edge;
            const float3 sc3 = make_float3(0.034f + hi * sk.x, 0.031f + hi * sk.y, 0.036f + hi * sk.z);
            o.x += (sc3.x - o.x) * st; o.y += (sc3.y - o.y) * st; o.z += (sc3.z - o.z) * st;
        }
    }

    const float gn = 255.f * P[RK_GAIN];
    const float dth = (hash_u((unsigned int)(y * W + x) ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    float4 c4 = make_float4(gn * o.x + dth, gn * o.y + dth, gn * o.z + dth, 255.f);
    if (!(c4.x == c4.x)) c4.x = 0.f;
    if (!(c4.y == c4.y)) c4.y = 0.f;
    if (!(c4.z == c4.z)) c4.z = 0.f;
    out[y * W + x] = pack(c4);
}
""".replace("RIM_W_C", repr(RIM_W) + "f")

_OPTS = ("--std=c++14", "--use_fast_math")


# ------------------------------------------------------------------------------------------------- host helpers
def _fin(x, default: float = 0.0) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return x if x == x and abs(x) < 1e30 else default


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _smooth(e0: float, e1: float, x: float) -> float:
    t = _clamp((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ease(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau) if dt > 0.0 else 0.0


def _turn_span(now: float, rev_bins: float, example: bool) -> float:
    """The bins one turn shows: the whole example; for a live piece the whole piece (at least UNFURL) while it is
    shorter than STRETCH_A revolutions, then eased (C1) to exactly one revolution at STRETCH_B."""
    if example:
        return max(now, 2.0 * KF.L_MIN)
    x = max(now, UNFURL) / max(rev_bins, 1.0)
    if x <= STRETCH_A:
        f = x
    elif x >= STRETCH_B:
        f = 1.0
    else:
        f = x - (x - STRETCH_A) ** 2 / (2.0 * (STRETCH_B - STRETCH_A))
    return rev_bins * f


def _noise_texture(seed: int, n: int = 256) -> np.ndarray:
    """A tiling smooth noise in 0..1: random waves with whole wave numbers across the tile."""
    rng = np.random.default_rng(seed + 4711)
    y, x = np.mgrid[0:n, 0:n] / n
    f = np.zeros((n, n))
    for octave, amp in ((1, 1.0), (2, 0.6), (3, 0.45), (5, 0.3), (8, 0.18), (13, 0.1), (21, 0.06)):
        for _ in range(3):
            kx, ky = (int(k) for k in rng.integers(-octave, octave + 1, 2))
            if kx == 0 and ky == 0:
                kx = octave
            f += amp * np.cos(2 * np.pi * (kx * x + ky * y) + rng.uniform(0, 2 * np.pi))
    f -= f.min()
    f /= max(f.max(), 1e-9)
    return np.ascontiguousarray(f.astype(np.float32))


# ------------------------------------------------------------------------------------------------- the scene
class KeyscapeRose(Source):
    """Per frame: the shared timeline's update, one or two KeyField bakes, one arena upload, the window kernel and
    the upsample.  Everything is allocated in setup."""
    name = "keyscape_rose"
    label = "Key rose"
    description = ("The piano's live key analysis as a turning stained-glass rose: the rim is the key of each moment, "
                   "the centre the key of the whole piece, and a clock hand lays new glass as you play.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette=None, render_scale=0.75),
            "folds": num(1.0, 12.0, 4.0, "Petals (folds)", step=0.1, basic=True),
            "mirror": num(0.0, 1.0, 1.0, "Mirrored petals"),
            "centred": num(0.0, 1.0, 0.0, "Sapp's mountain", basic=True),
            "revolution_s": num(20.0, 300.0, 60.0, "One turn", unit="s"),
            "wave_mix": num(0.0, 1.0, 0.0, "Wavescape colours"),
            "lead_lines": num(0.0, 1.0, 1.0, "Lead lines"),
            "hue_shift": num(0.0, 1.0, 0.0, "Hue shift"),
            "piece_gap": num(30.0, 300.0, 90.0, "New piece after", unit="s", tier="structural"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sc = self.h / 1080.0
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.k_main = raw_module(_SRC, _OPTS).get_function("rk_main")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.fT = KF.KeyField(ctx, FW, FH)            # windows ending at each moment (the default)
        self.fC = KF.KeyField(ctx, FW, FH)            # windows centred on each moment (``centred``)
        self.gA = cp.zeros((FH, FW, 4), cp.float32)   # the previous piece's planes A and B
        self.gB = cp.zeros((FH, FW, 4), cp.float32)
        self.tex = cp.asarray(_noise_texture(self.seed))
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        self._private = KF.KeyTimeline(self.seed)     # when there is no sound object
        self._tl = None                               # the timeline seen last frame
        self._tl_key = None                           # its (pieces, example)
        self._last_now = None
        self._dom = None                              # the field baked last frame that dominated the picture
        self.hand = 0.0                               # the clock hand (turns, 0..1)
        self.g_on, self.g_w, self.g_hand = False, 0.0, 0.0
        self.rot = self.sheen = self.shim = self.sky = self.breath = self.kick = 0.0
        self.dim = None                               # the example's dimming (snapped on the first frame)
        self.sum_rgb = None
        # a piece already starting on the first frame opens over the example's glass, not over black
        rev_b = _clamp(_fin(params.get("revolution_s"), 60.0), 20.0, 300.0) / KF.BIN_S
        hue = 180.0 * _clamp(_fin(params.get("hue_shift")), 0.0, 1.0)
        ex = self._private
        self.fT.bake(ex, KF.TRAILING, _turn_span(float(ex.now()), rev_b, True), LOGR, hue)
        cp.copyto(self.gA, self.fT.A)
        cp.copyto(self.gB, self.fT.B)
        self.g_on, self.g_w, self.g_hand = True, 1.0, 0.0

    def _timeline(self):
        snd = self.sound
        return self._private if snd is None else KF.KeyTimeline.shared(snd, self.seed)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = _fin(dt)
        dt = dt if 0.0 < dt < 10.0 else 0.0
        e = _clamp(_fin(p["energy"], 0.5), 0.0, 1.0)
        pulse = _clamp(_fin(p["pulse"]), 0.0, 1.0)
        spd = _clamp(_fin(p["speed"], 1.0), 0.0, 4.0)
        gain = _clamp(_fin(p["intensity"], 1.0), 0.0, 2.0)
        folds = _clamp(_fin(p["folds"], 4.0), 1.0, 12.0)
        mirror = _clamp(_fin(p["mirror"], 1.0), 0.0, 1.0)
        centred = _clamp(_fin(p["centred"]), 0.0, 1.0)
        rev = _clamp(_fin(p["revolution_s"], 60.0), 20.0, 300.0)
        wave = _clamp(_fin(p["wave_mix"]), 0.0, 1.0)
        lead = _clamp(_fin(p["lead_lines"], 1.0), 0.0, 1.0)
        hue = 180.0 * _clamp(_fin(p["hue_shift"]), 0.0, 1.0)
        gap = _clamp(_fin(p["piece_gap"], 90.0), 1.0, 3600.0)

        # motion (integrated; kicks ease in and fall away, and add to rates only)
        tau = 0.08 if pulse > self.kick else 0.35
        self.kick += (pulse - self.kick) * _ease(dt, tau)
        k = self.kick
        self.rot = (self.rot - (0.0025 + 0.010 * e + 0.006 * k) * spd * dt) % 1.0
        self.sheen = (self.sheen + (0.05 + 0.12 * e + 0.10 * k) * spd * dt) % 1000.0
        self.shim = (self.shim + (0.6 + 2.2 * e) * spd * dt) % 256.0
        self.sky = (self.sky + (0.4 + 0.8 * e) * spd * dt) % 256.0
        self.breath = (self.breath + 0.065 * spd * dt) % 1.0
        bre = math.sin(2.0 * math.pi * self.breath)

        # the analysis: the screen's shared timeline
        tl = self._timeline()
        tl.update(self.sound, dt, gap)
        key = (tl.pieces, bool(tl.example))
        if tl is not self._tl or key != self._tl_key:
            if self._tl is not None and self._dom is not None:      # a new piece (or timeline): keep the old glass
                cp = self.ctx.cp
                cp.copyto(self.gA, self._dom.A)
                cp.copyto(self.gB, self._dom.B)
                self.g_on, self.g_w, self.g_hand = True, 1.0, self.hand
            self._tl, self._tl_key, self._last_now = tl, key, None
        rev_b = rev / KF.BIN_S
        now = float(tl.now())
        span = _turn_span(now, rev_b, bool(tl.example))   # the whole piece round the turn until it outgrows one
        if self._last_now is not None:                 # the hand moves with the music's own clock, a turn a revolution
            d = now - self._last_now
            if 0.0 < d < rev_b:
                self.hand = (self.hand + d / rev_b) % 1.0
        self._last_now = now
        if self.g_on:
            self.g_w += (GHOST_DIM - self.g_w) * _ease(dt, 4.0)
            if tl.started and now >= UNFURL + 2.0:       # the new piece has covered a whole turn
                self.g_on, self.g_w = False, 0.0
        fixed = 0.0 if tl.example else _smooth(0.9, STRETCH_B, now / rev_b)

        use_t, use_c = centred < 0.999, centred > 0.001
        if use_t:
            self.fT.bake(tl, KF.TRAILING, span, LOGR, hue)
        if use_c:
            self.fC.bake(tl, KF.CENTRED, span, LOGR, hue)
        f0 = self.fT if use_t else self.fC
        self._dom = self.fC if (use_c and (centred >= 0.5 or not use_t)) else self.fT
        wt = 0.0 if not use_t else (1.0 if not use_c else 1.0 - centred)
        wc = 1.0 - wt

        # the oculus and the halo: the key of the whole piece, eased
        sk, scl = int(f0.summit_key), _clamp(_fin(f0.summit_clarity), 0.0, 1.0)
        tgt = np.array(KF.key_rgb(sk, hue, KF.C_KEY * (0.4 + 0.6 * scl)))
        if self.sum_rgb is None:
            self.sum_rgb = tgt.copy()
        else:
            self.sum_rgb += (tgt - self.sum_rgb) * _ease(dt, 2.0)
        dim_t = EX_DIM if tl.example else 1.0
        self.dim = dim_t if self.dim is None else self.dim + (dim_t - self.dim) * _ease(dt, 1.0)
        fresh = tl.started and not tl.frozen
        glow = (0.22 + 0.35 * e + 0.40 * k) * (1.0 - _smooth(0.5, 3.0, _fin(tl.quiet, 1e9))) if fresh else 0.0

        A = self.A
        A.fill(0.0)
        R = R_FRAC * self.h
        A[I["W"]], A[I["H"]] = self.w, self.h
        A[I["CX"]], A[I["CY"]] = 0.5 * self.w, 0.5 * self.h
        A[I["R"]], A[I["R0"]] = R, R0_FRAC * R
        A[I["ROT"]], A[I["HAND"]] = self.rot, self.hand
        A[I["F"]], A[I["MIR"]] = folds, mirror
        A[I["WT"]], A[I["WC"]] = wt, wc
        A[I["WAVE"]], A[I["LEAD"]], A[I["HUE"]] = wave, lead, hue
        A[I["EN"]], A[I["KICK"]], A[I["GAIN"]] = e, k, gain
        A[I["SHEEN"]] = self.sheen
        A[I["BREATH"]] = 1.0 + 0.010 * bre
        A[I["BL"]] = (0.80 + 0.30 * e + 0.12 * k) * (1.0 + 0.045 * bre)
        A[I["SHEENA"]] = 0.10 + 0.14 * e + 0.06 * k
        A[I["SHP"]], A[I["SHA"]] = self.shim, 0.05 + 0.16 * e + 0.05 * k
        A[I["SC"]] = self.sc
        A[I["FW"]], A[I["FH"]] = f0.fw, f0.fh
        A[I["SPAN"]], A[I["ORG"]], A[I["NOW"]], A[I["LO"]] = f0.span, f0.org, f0.now, f0.lo
        A[I["LOGR"]], A[I["LMIN"]] = LOGR, KF.L_MIN
        A[I["GW"]] = self.g_w if self.g_on else 0.0
        A[I["DH"]] = (self.hand - self.g_hand) % 1.0
        A[I["SR"]], A[I["SG"]], A[I["SB"]] = self.sum_rgb
        A[I["HANDS"]] = 0.05 + glow
        A[I["DIM"]] = self.dim
        A[I["RIMK"]] = 0.55 * k
        A[I["HALO"]] = (0.12 + 0.10 * e + 0.12 * k) * (1.0 + 0.06 * bre)
        A[I["SEAM"]] = SEAM_STRETCH + (SEAM_FIXED - SEAM_STRETCH) * fixed
        ang = math.radians(-64.0)
        A[I["SDX"]], A[I["SDY"]] = math.cos(ang), math.sin(ang)
        A[I["SFQ"]] = 2.0 * math.pi / (1.15 * self.w)
        A[I["MULL"]] = MULL_W
        A[I["SKY"]] = self.sky
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.arena.set(A)

        launch_2d(self.k_main, self.w, self.h,
                  (self.buf, self.arena, self.tex, self.fT.A, self.fT.B, self.fT.C, self.fC.A, self.fC.B, self.fC.C,
                   self.gA, self.gB, np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        for f in (getattr(self, "fT", None), getattr(self, "fC", None)):
            if f is not None:
                f.close()
        self.fT = self.fC = None
        self.buf = self.gA = self.gB = self.tex = self.arena = None
        self._tl = self._dom = self._private = None
