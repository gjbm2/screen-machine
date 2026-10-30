"""Tonal atlas: the piano's keys drawn as an antique hand-coloured map, one territory per key.

What the picture shows
  An old engraved map on parchment with a single island on it: the music played so far.  Every coloured territory
  is a key.  Its colour is the keyscape's key colour: the hue follows the circle of fifths, so neighbouring keys (C,
  G, F) get neighbouring hues and distant ones opposite hues; major keys are light washes, minor keys deeper ones.
  Dash-and-dot frontiers run between territories where both keys are clear.  Where the music was quiet the land
  turns to marsh (stipple and reed tufts) and then sea, so a rest in the playing becomes a bay.  The clearest keys
  rise as mountains: hachure strokes on their flanks and little molehill peaks on the high ground.  The biggest
  territories carry their key names in italic, in Sapp's convention (E♭ is E-flat major, c♯ is C-sharp minor).  The
  sea is engraved with fine lines that glint, and water lines follow the coast.  A compass rose in the corner turns
  its north point towards the key of the whole piece on a ring of the twelve keys, and faint rhumb lines radiate
  from it across the sheet.

How the analysis maps onto the island
  The island is a chart of the key field (stream_service/plugins/_lib/keyfield.py: for every moment and every
  listening span, the key, how clear it is and how much sound there was).
  * Going round the island clockwise is time.  The piece starts on the south bank of the sound (the inlet on the
    east side), runs round through south, west and north, and reaches "now" on the sound's north bank.
  * Going inland is the listening span.  The coast hears one second at a time (the moment's chord), half way in is a
    phrase, and the island's heart hears the whole piece.  So the heartland is the home key, and passing
    modulations are provinces nearer the coast.
  * A small capital marks the heart.  A river rises there: the seam between the start and now, widening into the
    sound.
  * The chart is bent by smooth noise, and its rim bulges into lobes with fractal coves and headlands, so it reads as
    land rather than a diagram.  Off the coast lies an archipelago.  Its islets take the keys of the one-second
    windows (the passing chords), so they appear only where the music was sounding.
  * As the piece grows, older music slides back round towards the start and out towards the coast, so the provinces
    drift slowly.
  * The newest land, on the north bank of the sound, is drawn in fresh ink: blue-black and glossy, with its colour
    wash spreading in behind the pen.  It dries to sepia over a few seconds.
  * ``centred`` morphs the chart from windows that end at each moment (what had been heard by then, so the island is
    whole) to Sapp's centred windows (the island becomes a horseshoe round a bay).
  * Until the first note the map shows the keyscape's example as a pencil draft.  The first note fades the draft and
    inks a new island from the start bank.  After ``piece_gap`` seconds of silence the map freezes, and the next note
    starts a new one.

The maths
  Per pixel, on the GPU:
  1. Screen -> map (pan, zoom) -> a two-level domain warp (a tiling noise texture) -> the chart's polar coordinates
     about the capital, on an ellipse whose radius is modulated by four seeded lobes.  The angle from the sound,
     outside the sound's half-gap (GAP x r^1.5, a funnel narrowing into a river), gives u (time); the radius gives v =
     1 - r^(1/0.6) (the span: the heart gets a fair share of the area).
  2. The key field's planes (a TRAILING and a CENTRED KeyField, 768 x 192, mixed by ``centred``) are sampled there.
     The territory's key is the bilinear majority of the four nearest texels.  The runner-up's weight gives a
     continuous distance to the frontier, so frontiers are smooth curves, not texel steps.
  3. Land = min(rim, inside, smoothstep(0.25, 0.55, 1 - fog)), or an islet where noise peaks just off the rim, plus
     fine coast noise, cut by the sound.  The rim's radius carries its own fractal noise.  The warp's amplitude
     (0.85 x ``warp``) keeps the map fold-free.  Marsh is the band 0.1-0.7 of 1 - fog.  Height is land x smoothstep(0.45, 0.92, clarity).  Frontiers are drawn where the
     diatonicity (|F5| / sum H) is high, i.e. where both sides are clearly keyed.  The wash colour is the key's
     colour (keyscape's OKLCh rule), eased to the runner-up's across a soft seam where the keys are unclear.
  4. Land and height are reduced 4x and blurred (Gaussian sigma 9 and 2.2 low-res pixels).  The water lines are
     iso-lines of the blurred land, and the hachures follow the fall line of the blurred height.
  On the host: one label job a second.  A 48 x 34 grid of map points goes through the same warp and chart, its
  windows go through KF.windows_keys (136 a frame), and KF.regions flood-fills the grid.  The four biggest clear
  territories get their names, which ease to their places.

Macros
  * energy: how quickly the view pans after the newest coast, the sea's shimmer (speed and sparkle), and the wet ink's
    bleed and gloss.
  * pulse: a kick, eased in, to the fresh ink's brightness.
  * speed: the shimmer, the drift and the breathing.
  * intensity: gain.
  No palette: the key colours are the content, and ``hue_shift`` turns them.  Alive with no notes: the view drifts and
  breathes, the sea glints, the washes breathe and the compass needle sways.

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, https://esf.ccarh.org/254-old/254_LiteraturePack1/Harmony/Sapp_VisualHierKeyAnal05.pdf, and by
        hand-coloured engraved maps.  The analysis is stream_service/plugins/_lib/keyfield.py.  No code borrowed.)
Author: smfx (original)
License: MIT (original)
license_class: permissive
"""
from __future__ import annotations

import logging
import math
import os

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import keyfield as KF
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.tonal_atlas")

FW, FH = 768, 192               # the key field's planes (u: time round the island, v: span inland)
NOISE = 256                     # the tiling noise texture
LOW = 4                         # the blurred land / height buffers are this much smaller
BLUR_R = 24                     # blur taps each side (low-res px)
SIG_W, SIG_N = 9.0, 2.2         # blur sigmas (low-res px): land (water lines), height (hachures)
RX, RY = 0.84, 0.46             # the island's ellipse (map units; the screen is 1.5 map units high at zoom 1)
HUB = (0.0, 0.0)                # the capital: the heart of the island
GAP = 0.22                      # the sound's half-gap at the rim (rad), narrowing as r^1.5 into a river
LOBE_K = (2, 3, 5, 7)           # the rim's lobe harmonics (the kernel's k < 2 ? k + 2 : 2k + 1)
GAMMA = 0.6                     # radius = (1 - v)^GAMMA: the long windows get a fair share of the area
TEX_S = 32.0                    # noise texels per map unit for the warp
GX, GY = 48, 34                 # the label grid (map points)
GRID_X, GRID_Y = 1.2, 0.85      # its half-extent (map units)
LABEL_PERIOD = 1.0              # s between label refreshes
LABEL_FRAMES = 12               # frames a refresh is spread over
N_LAB = 6                       # label slots drawn
LABEL_PX = 0.040                # a label's font size at scale 1 (fraction of the internal height)
FADE_S = 3.5                    # the old map fades out over this when a new piece starts
CLOCK_WRAP = 256.0 / 0.9        # the gloss noise drifts 0.9 texel/s and tiles every 256: wrap there, seamlessly
FONTS = ("/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")

# arena layout (floats)
AR = dict(W=0, H=1, CAMX=2, CAMY=3, PPM=4, A1=5, A2=6, RAG=7, HUBX=8, HUBY=9, RX=10, RY=11, FJ=12, GAP=13,
          GAMMA=14, MORPH=15, FW=16, FH=17, WASH=18, HS=19, FRESH=20, TNOW=21, TORG=22, TSPAN=23, CNOW=24,
          CORG=25, CSPAN=26, LMIN=27, LOGR=28, DRAFT=29, S=30, CLOCK=31, INK=32, EN=33, KICK=34, GAIN=35,
          SHIM=36, SC=37, LW=38, LH=39, GW=40, GH=41, CA=42, RCX=43, RCY=44, RR=45, RANG=46, FADE=47, OX=48, OY=49,
          SUMR=50, SUMG=51, SUMB=52, HUBSX=53, HUBSY=54, HUBA=55, BREATH=57, FR0=58, FR1=59, SUMA=60, GLOSS=61)
O_LAB = 64                      # labels: N_LAB x 8 (cx cy scale alpha glyph r g b)
O_KEYS = O_LAB + N_LAB * 8      # the 24 key colours at full clarity (sRGB)
O_GREY = O_KEYS + 72            # the major and the minor grey (chroma 0)
O_LOBE = O_GREY + 6             # four rim lobes: (amplitude, phase) for cos(k theta), k in LOBE_K
N_AR = O_LOBE + 8 + 2


def _gauss(sig: float, r: int) -> np.ndarray:
    w = np.exp(-np.arange(r + 1) ** 2 / (2.0 * sig * sig))
    return w / (w[0] + 2.0 * w[1:].sum())


_DEFS = "\n".join(f"#define TA_{k} {v}" for k, v in AR.items())
_BWS = ",".join(f"{v:.8e}f" for v in _gauss(SIG_W, BLUR_R))
_BNS = ",".join(f"{v:.8e}f" for v in _gauss(SIG_N, BLUR_R))

_SRC = HELPERS + KF.KEYFIELD_CUDA + r"""
// ---- tonal_atlas ----
#define TA_NOISE """ + str(NOISE) + r"""
#define TA_PI 3.14159265f
#define TA_TAU 6.28318531f
#define TA_NAR """ + str(N_AR) + r"""
#define TA_NLAB """ + str(N_LAB) + r"""
#define TA_OLAB """ + str(O_LAB) + r"""
#define TA_OKEYS """ + str(O_KEYS) + r"""
#define TA_OGREY """ + str(O_GREY) + r"""
#define TA_OLOBE """ + str(O_LOBE) + r"""
#define TA_BLUR_R """ + str(BLUR_R) + r"""
#define TA_LOW """ + str(LOW) + r"""
#define TA_BIN_S """ + repr(KF.BIN_S) + r"""f
""" + _DEFS + r"""

__constant__ float TA_BW[TA_BLUR_R + 1] = {""" + _BWS + r"""};
__constant__ float TA_BN[TA_BLUR_R + 1] = {""" + _BNS + r"""};

// the tiling noise texture, bilinear (texel units)
__device__ __forceinline__ float ta_tn(const float* __restrict__ T, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = ((int)fu) & (TA_NOISE - 1), y0 = ((int)fv) & (TA_NOISE - 1);
    const int x1 = (x0 + 1) & (TA_NOISE - 1), y1 = (y0 + 1) & (TA_NOISE - 1);
    const float tx = u - fu, ty = v - fv;
    const float a = T[y0 * TA_NOISE + x0], b = T[y0 * TA_NOISE + x1];
    const float c = T[y1 * TA_NOISE + x0], d = T[y1 * TA_NOISE + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}
__device__ __forceinline__ float ta_fb3(const float* __restrict__ T, float x, float y) {
    return 0.6f * ta_tn(T, x, y) + 0.3f * ta_tn(T, 2.1f * x + 37.f, 2.1f * y + 11.f)
         + 0.1f * ta_tn(T, 4.3f * x + 91.f, 4.3f * y + 53.f);
}
__device__ __forceinline__ float3 ta_mix3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
// an anti-aliased line of half-width hw at signed distance d (px)
__device__ __forceinline__ float ta_line(float d, float hw) { return 1.f - smooth(hw - 0.6f, hw + 0.6f, fabsf(d)); }
__device__ __forceinline__ unsigned char ta_q8(float x) { return (unsigned char)(255.f * clamp01(x) + 0.5f); }

// the domain warp: a map point -> its chart point (two levels of smooth noise)
__device__ __forceinline__ float2 ta_warp(const float* P, const float* __restrict__ T, float mx, float my) {
    const float S = P[TA_S], ox = P[TA_OX], oy = P[TA_OY], a1 = P[TA_A1], a2 = P[TA_A2];
    float qx = mx * S + ox, qy = my * S + oy;
    const float m1x = mx + a2 * (ta_fb3(T, qx + 13.f, qy) - 0.5f);
    const float m1y = my + a2 * (ta_fb3(T, qx, qy + 71.f) - 0.5f);
    qx = m1x * S + ox; qy = m1y * S + oy;
    return make_float2(mx + a1 * (ta_fb3(T, qx + 101.f, qy + 7.f) - 0.5f),
                       my + a1 * (ta_fb3(T, qx + 59.f, qy + 143.f) - 0.5f));
}

struct TaGeo { float u, v, rn, rs, gb, ga, gr, isl; };
// the island's chart: a chart point -> the field's (u, v), its radius (1 at the rim; rs: map units per unit of
// radius) and its distance outside the sound's banks (gb, map units).  u: clockwise from the sound's south bank (0) round to its
// north bank (1); v: 1 at the capital.  ga (outside the sound), gr (inside the rim) and isl (an islet) are set by
// the caller, once the coast noise is known.
__device__ __forceinline__ TaGeo ta_geo(const float* P, float wx, float wy) {
    TaGeo g;
    const float rx = (wx - P[TA_HUBX]) / P[TA_RX], ry = (wy - P[TA_HUBY]) / P[TA_RY];
    const float r = sqrtf(rx * rx + ry * ry);
    const float th = atan2f(ry, rx);
    float rho = 1.f;
    #pragma unroll
    for (int k = 0; k < 4; ++k)
        rho += P[TA_OLOBE + 2 * k] * __cosf((float)(k < 2 ? k + 2 : 2 * k + 1) * th + P[TA_OLOBE + 2 * k + 1]);
    rho = fmaxf(rho, 0.4f);
    const float rn = r / rho;
    float d = th - P[TA_FJ];
    d -= TA_TAU * floorf(d * (1.f / TA_TAU));
    const float rc = fminf(rn, 1.2f);
    const float gap = P[TA_GAP] * rc * sqrtf(rc);
    const float arc = fmaxf(TA_TAU - 2.f * gap, 1e-3f);
    const float rm = sqrtf(P[TA_RX] * P[TA_RY]);
    g.u = (d - gap) / arc;
    g.v = 1.f - __powf(fmaxf(rn, 1e-12f), 1.f / P[TA_GAMMA]);
    g.rn = rn;
    g.rs = rm * rho;
    g.gb = fminf(d - gap, TA_TAU - gap - d) * r * rm;
    g.ga = g.gr = g.isl = 0.f;
    return g;
}

struct FV { float land, mar, hgt, wet, cov, del, fs; float3 col; int k1, k2; };

// a key's wash colour at clarity cf: the grey of its mode -> its full key colour (host table, sRGB)
__device__ __forceinline__ float3 ta_keycol(const float* P, int k, float cf) {
    const float* F = P + TA_OKEYS + 3 * k;
    const float* G = P + TA_OGREY + (k >= 12 ? 3 : 0);
    return make_float3(G[0] + (F[0] - G[0]) * cf, G[1] + (F[1] - G[1]) * cf, G[2] + (F[2] - G[2]) * cf);
}

// one key field at the chart's (u, v): land, marsh, height, wet ink, wash coverage, colour, the frontier's keys
__device__ void ta_eval(const float* P, const float4* __restrict__ A, const float4* __restrict__ B,
                        const float4* __restrict__ C, const TaGeo& g, float cn, float now, float org, float span,
                        int centred, FV& o)
{
    const int fw = max((int)P[TA_FW], 2), fh = max((int)P[TA_FH], 2);
    const float fx = fminf(fmaxf(g.u * (float)fw - 0.5f, 0.f), (float)(fw - 1));
    const float fy = fminf(fmaxf(g.v * (float)fh - 0.5f, 0.f), (float)(fh - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, fw - 1), y1 = min(y0 + 1, fh - 1);
    const float tx = fx - (float)x0, ty = fy - (float)y0;
    float wt[4];
    int id[4], kk[4];
    wt[0] = (1.f - tx) * (1.f - ty); wt[1] = tx * (1.f - ty); wt[2] = (1.f - tx) * ty; wt[3] = tx * ty;
    id[0] = y0 * fw + x0; id[1] = y0 * fw + x1; id[2] = y1 * fw + x0; id[3] = y1 * fw + x1;
    float clar = 0.f, fog = 0.f, ins = 0.f, cmax = 0.f, dia = 0.f;
    float3 rgb = make_float3(0.f, 0.f, 0.f);
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        const float4 a = A[id[i]], b = B[id[i]];
        const float cz = C[id[i]].z;
        kk[i] = kf_key_id(a.x);
        clar += wt[i] * a.y; fog += wt[i] * a.z; ins += wt[i] * a.w; cmax = fmaxf(cmax, a.y);
        rgb.x += wt[i] * b.x; rgb.y += wt[i] * b.y; rgb.z += wt[i] * b.z; dia += wt[i] * cz;
    }
    clar = clamp01(clar); fog = clamp01(fog); ins = clamp01(ins); cmax = clamp01(cmax); dia = clamp01(dia);
    rgb = make_float3(clamp01(rgb.x), clamp01(rgb.y), clamp01(rgb.z));
    // the territory: the key with the most bilinear weight; the runner-up's weight sets the frontier distance
    int k1 = -2; float w1 = -1.f;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        float s = 0.f;
        #pragma unroll
        for (int j = 0; j < 4; ++j) s += kk[j] == kk[i] ? wt[j] : 0.f;
        if (s > w1) { w1 = s; k1 = kk[i]; }
    }
    int k2 = -2; float w2 = 0.f;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        if (kk[i] == k1) continue;
        float s = 0.f;
        #pragma unroll
        for (int j = 0; j < 4; ++j) s += kk[j] == kk[i] ? wt[j] : 0.f;
        if (s > w2) { w2 = s; k2 = kk[i]; }
    }
    const float den = 1.f - fog;
    const float lf = smooth(0.25f, 0.55f, den);
    const float base = fmaxf(fminf(fminf(g.gr, ins), lf), g.isl * fminf(ins, lf));   // the island, or an islet
    o.land = fminf(clamp01(base + (cn - 0.5f) * 8.f * P[TA_RAG] * base * (1.f - base)), g.ga);
    o.mar = g.gr * g.ga * ins * smooth(0.10f, 0.28f, den) * (1.f - smooth(0.42f, 0.70f, den));
    o.hgt = o.land * smooth(0.45f, 0.92f, clar);
    float lw = 0.f;
    if (centred) {
        const float lmin = fmaxf(P[TA_LMIN], 1e-3f), vv = clamp01(g.v);
        const float llin = lmin + (span - lmin) * vv;
        const float llog = lmin * __powf(fmaxf(span / lmin, 1.f), vv);
        lw = llin + (llog - llin) * clamp01(P[TA_LOGR]);
    }
    const float age = fmaxf(now - (org + g.u * span + 0.5f * lw), 0.f) * TA_BIN_S;    // s since the window ended
    const float fr = clamp01(P[TA_FRESH]);
    o.wet = fr * __expf(-age * (1.f / 2.2f));
    o.cov = 1.f - fr * (1.f - smooth(0.2f, 1.6f, age + (cn - 0.5f) * 1.6f));          // the wash spreads in
    const float cf = 0.3f + 0.7f * clar;
    const float3 c1 = k1 >= 0 ? ta_keycol(P, k1, cf) : rgb;
    float3 col = c1;
    float del = 1.f, fs = 0.f;
    if (k2 != -2) {
        const float3 c2 = k2 >= 0 ? ta_keycol(P, k2, cf) : rgb;
        del = clamp01(w1 - w2);
        if (k1 >= 0 && k2 >= 0)
            fs = smooth(0.15f, 0.35f, dia) * smooth(0.02f, 0.12f, g.v) * smooth(0.05f, 0.2f, cmax);
        const float t = 0.5f + 0.5f * smooth(0.f, 0.12f + 0.5f * (1.f - fs), del);
        col = ta_mix3(c2, c1, t);
    }
    col = ta_mix3(rgb, col, smooth(0.08f, 0.4f, clar));
    o.col = col; o.del = del; o.fs = fs; o.k1 = k1; o.k2 = k2;
}

__device__ __forceinline__ void ta_clear(FV& f) {
    f.land = f.mar = f.hgt = f.wet = f.del = f.fs = 0.f; f.cov = 1.f;
    f.col = make_float3(0.f, 0.f, 0.f); f.k1 = f.k2 = -2;
}

// pass 1: every pixel's chart point and its key field -> P0 (land, height, wet, marsh), P1 (key, runner-up,
// frontier weight gap, frontier strength), P2 (wash colour, wash coverage)
extern "C" __global__
void ta_field(uchar4* __restrict__ P0, uchar4* __restrict__ P1, uchar4* __restrict__ P2,
              const float* __restrict__ Ar, const float* __restrict__ Tx,
              const float4* __restrict__ AT, const float4* __restrict__ BT, const float4* __restrict__ CT,
              const float4* __restrict__ AC, const float4* __restrict__ BC, const float4* __restrict__ CC)
{
    __shared__ float P[TA_NAR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < TA_NAR; i += blockDim.x * blockDim.y) P[i] = Ar[i];
    __syncthreads();
    const int W = (int)P[TA_W], H = (int)P[TA_H];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int idx = y * W + x;
    const float ppm = fmaxf(P[TA_PPM], 1e-3f);
    const float mx = P[TA_CAMX] + ((float)x + 0.5f - 0.5f * (float)W) / ppm;
    const float my = P[TA_CAMY] + ((float)y + 0.5f - 0.5f * (float)H) / ppm;
    const float2 wp = ta_warp(P, Tx, mx, my);
    TaGeo g = ta_geo(P, wp.x, wp.y);
    // the coast: fractal noise on the rim's radius (coves and headlands), then a soft 0.08 map-unit band in which
    // the land's own noise works; off the rim an archipelago (islets take the one-second windows' keys)
    const float qx = mx * P[TA_S] + P[TA_OX], qy = my * P[TA_S] + P[TA_OY];
    const float cn = 0.5f * ta_tn(Tx, qx * 9.f + 17.f, qy * 9.f + 211.f)
                   + 0.3f * ta_tn(Tx, qx * 21.f + 151.f, qy * 21.f + 23.f)
                   + 0.2f * ta_tn(Tx, qx * 47.f + 83.f, qy * 47.f + 131.f);
    const float rnc = g.rn * (1.f + (cn - 0.5f) * P[TA_CA]);
    g.gr = clamp01((1.f - rnc) * g.rs * 12.5f + 0.5f);
    // the sound's banks: ragged towards the mouth, clean up the river (a crisp 0.012 map-unit edge)
    g.ga = clamp01((g.gb + (cn - 0.5f) * 0.14f * P[TA_CA] * smooth(0.3f, 0.8f, g.rn)) * 83.3f + 0.5f);
    if (rnc > 0.97f && rnc < 1.32f) {
        const float ni = 0.6f * ta_tn(Tx, qx * 40.f + 77.f, qy * 40.f + 191.f) + 0.4f * ta_tn(Tx, qx * 90.f + 5.f, qy * 90.f + 59.f);
        const float t0 = 0.64f + 0.7f * fmaxf(rnc - 1.f, 0.f);
        g.isl = smooth(t0, t0 + 0.04f, ni) * smooth(0.985f, 1.02f, rnc);
    }
    if (!(g.ga > 0.f) || (!(g.gr > 0.f) && !(g.isl > 0.f))) {   // open sea (or the sound): nothing to sample
        P0[idx] = make_uchar4(0, 0, 0, 0);
        P1[idx] = make_uchar4(255, 255, 255, 0);
        P2[idx] = make_uchar4(0, 0, 0, 255);
        return;
    }
    const float m = clamp01(P[TA_MORPH]);
    const bool useT = m < 0.999f, useC = m > 0.001f;
    FV a, b;
    ta_clear(a); ta_clear(b);
    if (useT) ta_eval(P, AT, BT, CT, g, cn, P[TA_TNOW], P[TA_TORG], P[TA_TSPAN], 0, a);
    if (useC) ta_eval(P, AC, BC, CC, g, cn, P[TA_CNOW], P[TA_CORG], P[TA_CSPAN], 1, b);
    FV r = useT ? a : b;
    if (useT && useC) {                                   // the morph: continuous fields mix, keys follow the larger
        r.land = a.land + (b.land - a.land) * m; r.mar = a.mar + (b.mar - a.mar) * m;
        r.hgt = a.hgt + (b.hgt - a.hgt) * m; r.wet = a.wet + (b.wet - a.wet) * m;
        r.cov = a.cov + (b.cov - a.cov) * m; r.col = ta_mix3(a.col, b.col, m);
        const FV& d = m >= 0.5f ? b : a;
        r.k1 = d.k1; r.k2 = d.k2; r.del = d.del;
        r.fs = d.fs * smooth(0.05f, 0.35f, fabsf(m - 0.5f));    // frontiers fade out and in around the switch
    }
    P0[idx] = make_uchar4(ta_q8(r.land), ta_q8(r.hgt), ta_q8(r.wet), ta_q8(r.mar));
    P1[idx] = make_uchar4((unsigned char)(r.k1 >= 0 ? r.k1 : 254),
                          (unsigned char)(r.k2 >= 0 ? r.k2 : (r.k2 == -1 ? 254 : 255)), ta_q8(r.del), ta_q8(r.fs));
    P2[idx] = make_uchar4(ta_q8(r.col.x), ta_q8(r.col.y), ta_q8(r.col.z), ta_q8(r.cov));
}

// pass 2: land and height, LOW x LOW box-averaged
extern "C" __global__
void ta_down(const uchar4* __restrict__ P0, float2* __restrict__ D, const int W, const int H, const int lw,
             const int lh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= lw || y >= lh) return;
    float sl = 0.f, sh = 0.f, c = 0.f;
    #pragma unroll
    for (int j = 0; j < TA_LOW; ++j) {
        const int yy = y * TA_LOW + j;
        if (yy >= H) break;
        #pragma unroll
        for (int i = 0; i < TA_LOW; ++i) {
            const int xx = x * TA_LOW + i;
            if (xx >= W) break;
            const uchar4 q = P0[yy * W + xx];
            sl += (float)q.x; sh += (float)q.y; c += 1.f;
        }
    }
    const float inv = c > 0.f ? 1.f / (255.f * c) : 0.f;
    D[y * lw + x] = make_float2(sl * inv, sh * inv);
}

// passes 3-4: separable Gaussian blur, wide on land (x), narrow on height (y)
extern "C" __global__
void ta_blur(const float2* __restrict__ src, float2* __restrict__ dst, const int lw, const int lh, const int dir)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= lw || y >= lh) return;
    float sw = 0.f, sn = 0.f;
    for (int k = -TA_BLUR_R; k <= TA_BLUR_R; ++k) {
        const int xx = dir == 0 ? min(max(x + k, 0), lw - 1) : x;
        const int yy = dir == 0 ? y : min(max(y + k, 0), lh - 1);
        const float2 s = src[yy * lw + xx];
        const int ak = k < 0 ? -k : k;
        sw += TA_BW[ak] * s.x; sn += TA_BN[ak] * s.y;
    }
    dst[y * lw + x] = make_float2(sw, sn);
}

// the blurred buffer, bilinear, at low-res pixel coordinates (centres at i + 0.5), clamped
__device__ __forceinline__ float2 ta_lb(const float2* __restrict__ L, int lw, int lh, float u, float v) {
    const float fx = fminf(fmaxf(u - 0.5f, 0.f), (float)(lw - 1));
    const float fy = fminf(fmaxf(v - 0.5f, 0.f), (float)(lh - 1));
    const int x0 = (int)fx, y0 = (int)fy, x1 = min(x0 + 1, lw - 1), y1 = min(y0 + 1, lh - 1);
    const float tx = fx - (float)x0, ty = fy - (float)y0;
    const float2 a = L[y0 * lw + x0], b = L[y0 * lw + x1], c = L[y1 * lw + x0], d = L[y1 * lw + x1];
    return make_float2((1.f - ty) * ((1.f - tx) * a.x + tx * b.x) + ty * ((1.f - tx) * c.x + tx * d.x),
                       (1.f - ty) * ((1.f - tx) * a.y + tx * b.y) + ty * ((1.f - tx) * c.y + tx * d.y));
}
// distance from p to the segment ab
__device__ __forceinline__ float ta_seg(float px, float py, float ax, float ay, float bx, float by) {
    const float ex = bx - ax, ey = by - ay, wx = px - ax, wy = py - ay;
    const float t = clamp01((wx * ex + wy * ey) / fmaxf(ex * ex + ey * ey, 1e-12f));
    const float dx = wx - t * ex, dy = wy - t * ey;
    return sqrtf(dx * dx + dy * dy);
}
// signed distance to the triangle abc (negative inside)
__device__ float ta_tri(float px, float py, float ax, float ay, float bx, float by, float cx, float cy) {
    const float d = fminf(fminf(ta_seg(px, py, ax, ay, bx, by), ta_seg(px, py, bx, by, cx, cy)),
                          ta_seg(px, py, cx, cy, ax, ay));
    const float c1 = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
    const float c2 = (cx - bx) * (py - by) - (cy - by) * (px - bx);
    const float c3 = (ax - cx) * (py - cy) - (ay - cy) * (px - cx);
    const bool in = (c1 >= 0.f && c2 >= 0.f && c3 >= 0.f) || (c1 <= 0.f && c2 <= 0.f && c3 <= 0.f);
    return in ? -d : d;
}
// one compass point (a kite from the centre to its tip), split into its dark and light halves
__device__ __forceinline__ void ta_kite(float qx, float qy, float b, float len, float sr, float half,
                                        float& dd, float& dl) {
    const float tx = len * __sinf(b), ty = -len * __cosf(b);
    dd = ta_tri(qx, qy, 0.f, 0.f, tx, ty, sr * __sinf(b + half), -sr * __cosf(b + half));
    dl = ta_tri(qx, qy, 0.f, 0.f, tx, ty, sr * __sinf(b - half), -sr * __cosf(b - half));
}
// a key name from the atlas (fill, halo), bilinear
__device__ __forceinline__ float2 ta_glyph(const uchar2* __restrict__ atlas, int g, float u, float v, int GW, int GH) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = (int)fu, y0 = (int)fv;
    const float tx = u - fu, ty = v - fv;
    float2 r = make_float2(0.f, 0.f);
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int xx = x0 + (k & 1), yy = y0 + (k >> 1);
        if (xx < 0 || yy < 0 || xx >= GW || yy >= GH) continue;
        const float w = ((k & 1) ? tx : 1.f - tx) * ((k >> 1) ? ty : 1.f - ty);
        const uchar2 q = atlas[((size_t)g * GH + yy) * GW + xx];
        r.x += w * q.x; r.y += w * q.y;
    }
    return make_float2(r.x * (1.f / 255.f), r.y * (1.f / 255.f));
}

// pass 5: the sheet: parchment, sea, washes, marsh, mountains, frontiers, coast, fresh ink, rhumb lines, the
// capital, key names, the compass rose, the neat frame, the vignette; the old map fading out over it
extern "C" __global__
void ta_compose(uchar4* __restrict__ out, const uchar4* __restrict__ P0, const uchar4* __restrict__ P1,
                const uchar4* __restrict__ P2, const float2* __restrict__ LB, const float* __restrict__ Ar,
                const float* __restrict__ Tx, const uchar2* __restrict__ atlas, const uchar4* __restrict__ old,
                const unsigned int seed)
{
    __shared__ float P[TA_NAR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < TA_NAR; i += blockDim.x * blockDim.y) P[i] = Ar[i];
    __syncthreads();
    const int W = (int)P[TA_W], H = (int)P[TA_H];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int idx = y * W + x;
    const float px = (float)x + 0.5f, py = (float)y + 0.5f, sc = fmaxf(P[TA_SC], 0.05f);
    const float en = clamp01(P[TA_EN]), ink = clamp01(P[TA_INK]), draft = clamp01(P[TA_DRAFT]);
    const float3 sepia = make_float3(0.165f, 0.108f, 0.068f);

    // ---- the parchment (fixed to the sheet) ----
    float3 paper;
    {
        const float s1 = ta_tn(Tx, px / (40.f * sc) + 11.f, py / (40.f * sc) + 97.f);
        const float s2 = ta_tn(Tx, px / (12.f * sc) + 140.f, py / (12.f * sc) + 33.f);
        const float fb = ta_tn(Tx, px / (1.3f * sc) + 61.f, py / (6.f * sc) + 5.f);
        const float st = smooth(0.62f, 0.9f, s1);
        const float k = (0.965f + 0.07f * s2) * (0.985f + 0.03f * fb);
        paper = make_float3(0.928f * k * (1.f - 0.07f * st), 0.866f * k * (1.f - 0.10f * st),
                            0.735f * k * (1.f - 0.16f * st));
        const float cs = 46.f * sc;                       // foxing: sparse soft brown spots
        const float fx = floorf(px / cs), fy = floorf(py / cs);
        const unsigned int hx = (unsigned int)(int)fx, hy = (unsigned int)(int)fy;
        if (hash1(hx, hy, seed + 5u) < 0.11f) {
            const float sx = (fx + 0.25f + 0.5f * hash1(hx, hy, seed + 6u)) * cs;
            const float sy = (fy + 0.25f + 0.5f * hash1(hx, hy, seed + 7u)) * cs;
            const float rr = (2.f + 7.f * hash1(hx, hy, seed + 8u)) * sc;
            const float d = sqrtf((px - sx) * (px - sx) + (py - sy) * (py - sy));
            const float f = 0.13f * (1.f - smooth(0.2f * rr, rr, d)) * (0.7f + 0.6f * fb);
            paper.x *= 1.f - 0.6f * f; paper.y *= 1.f - 0.9f * f; paper.z *= 1.f - 1.4f * f;
        }
    }
    float3 o = paper;

    // ---- the neat frame's two rules (centre lines inset e0, e1 from the edges) ----
    const float e0 = P[TA_FR0], e1 = P[TA_FR1];
    const float ax = fabsf(px - 0.5f * (float)W), ay = fabsf(py - 0.5f * (float)H);
    const float dout = fmaxf(ax - (0.5f * (float)W - e0), ay - (0.5f * (float)H - e0));
    const float din = fmaxf(ax - (0.5f * (float)W - e1), ay - (0.5f * (float)H - e1));

    if (din < 1.5f * sc) {
        const float ppm = fmaxf(P[TA_PPM], 1e-3f), S = P[TA_S];
        const float cxm = P[TA_CAMX], cym = P[TA_CAMY];
        const float mx = cxm + (px - 0.5f * (float)W) / ppm, my = cym + (py - 0.5f * (float)H) / ppm;
        // land: its coast distance (px, + inland), the wet ink nearby, marsh
        const uchar4 c0 = P0[idx];
        const uchar4 cl = P0[y * W + max(x - 1, 0)], cr = P0[y * W + min(x + 1, W - 1)];
        const uchar4 cu = P0[max(y - 1, 0) * W + x], cd = P0[min(y + 1, H - 1) * W + x];
        const float land = c0.x * (1.f / 255.f);
        const float lgx = ((float)cr.x - (float)cl.x) * (0.5f / 255.f), lgy = ((float)cd.x - (float)cu.x) * (0.5f / 255.f);
        const float dc = (land - 0.5f) / fmaxf(sqrtf(lgx * lgx + lgy * lgy), 0.0015f);
        const float lc = clamp01(dc + 0.5f);
        const float wet = (float)max(max((int)c0.z, max((int)cl.z, (int)cr.z)), max((int)cu.z, (int)cd.z)) * (1.f / 255.f);
        const float mar = c0.w * (1.f / 255.f);
        // the blurred land (x: the water lines) and height (y: the mountains), and their gradients (per px)
        const int lw = max((int)P[TA_LW], 1), lh = max((int)P[TA_LH], 1);
        const float il = 1.f / (float)TA_LOW;
        const float2 b0 = ta_lb(LB, lw, lh, px * il, py * il);
        const float2 bxs = ta_lb(LB, lw, lh, (px + 2.f) * il, py * il);
        const float2 bys = ta_lb(LB, lw, lh, px * il, (py + 2.f) * il);
        const float bgx = 0.5f * (bxs.x - b0.x), bgy = 0.5f * (bys.x - b0.x);
        const float bg = sqrtf(bgx * bgx + bgy * bgy);
        const float hgx = 0.5f * (bxs.y - b0.y), hgy = 0.5f * (bys.y - b0.y);
        const float hg = sqrtf(hgx * hgx + hgy * hgy);
        // the ink: sepia; pencil grey for the draft (the example); blue-black while still wet
        float3 inkc = make_float3(sepia.x + 0.19f * draft, sepia.y + 0.24f * draft, sepia.z + 0.27f * draft);
        inkc = ta_mix3(inkc, make_float3(0.045f, 0.055f, 0.13f), wet);
        const float ia = (0.55f + 0.45f * ink) * (1.f - 0.35f * draft);

        // ---- the sea: a cool tint, a coloured coastal band, engraved lines that glint, water lines ----
        float3 sea = make_float3(paper.x * 0.950f, paper.y * 0.972f, paper.z * 0.990f);
        {
            const float band = smooth(0.05f, 0.42f, b0.x);
            const float wa = 0.55f * band * (0.3f + 0.7f * clamp01(P[TA_WASH]));
            sea.x *= 1.f - 0.30f * wa; sea.y *= 1.f - 0.13f * wa; sea.z *= 1.f - 0.08f * wa;
            const float sp = 0.0097f;                     // engraved line spacing (map units)
            const float yy = my / sp + 0.6f * (ta_tn(Tx, mx * S * 0.8f + 300.f, my * S * 0.8f + 40.f) - 0.5f);
            const float fl = floorf(yy);
            const float dpx = fabsf(yy - fl - 0.5f) * sp * ppm;
            const float brk = smooth(0.30f, 0.42f, ta_tn(Tx, mx * S * 2.6f + 17.f, fl * 5.3f + 91.f));
            float el = ta_line(dpx, (0.30f + 0.25f * ink) * sc) * brk * 0.42f * (1.f - smooth(0.18f, 0.40f, b0.x));
            const float gp = mx * 11.f + my * 3.5f - TA_TAU * P[TA_SHIM]
                           + 5.f * ta_tn(Tx, mx * S * 0.5f + 210.f, my * S * 0.5f + 33.f);
            const float gl = __powf(0.5f + 0.5f * __sinf(gp), 10.f) * (0.25f + 0.75f * en);
            el *= 1.f - 0.8f * gl;
            sea = ta_mix3(sea, inkc, el * ia);
            sea.x += gl * 0.05f; sea.y += gl * 0.05f; sea.z += gl * 0.045f;
            const float off = smooth(1.f, 3.5f, -dc);     // the water lines start clear of the coast line
            const float rw = (0.35f + 0.3f * ink) * sc;
            const float lev[4] = {0.40f, 0.29f, 0.19f, 0.11f};
            const float lal[4] = {0.75f, 0.58f, 0.42f, 0.28f};
            #pragma unroll
            for (int i = 0; i < 4; ++i)
                sea = ta_mix3(sea, inkc, ta_line((b0.x - lev[i]) / fmaxf(bg, 1e-4f), rw) * lal[i] * ia * off);
        }

        // ---- the land: the key's watercolour wash, granulating, pooling at its edges ----
        const uchar4 q0 = P1[idx];
        const uchar4 w2 = P2[idx];
        const float fsv = q0.w * (1.f / 255.f);
        float df = 1e4f, gfx = 0.f, gfy = 0.f;
        if (q0.x < 24 && q0.y < 24) {                     // two keys meet near here: the signed frontier field
            const uchar4 ql = P1[y * W + max(x - 1, 0)], qr = P1[y * W + min(x + 1, W - 1)];
            const uchar4 qu = P1[max(y - 1, 0) * W + x], qd = P1[min(y + 1, H - 1) * W + x];
            const float s0 = (q0.x < q0.y ? 1.f : -1.f) * (float)q0.z;
#define TA_SN(q) ((((q).x == q0.x && (q).y == q0.y) || ((q).x == q0.y && (q).y == q0.x)) \
                  ? ((q).x < (q).y ? 1.f : -1.f) * (float)(q).z : s0)
            gfx = 0.5f * (TA_SN(qr) - TA_SN(ql)) * (1.f / 255.f);
            gfy = 0.5f * (TA_SN(qd) - TA_SN(qu)) * (1.f / 255.f);
#undef TA_SN
            df = (q0.z * (1.f / 255.f)) / fmaxf(sqrtf(gfx * gfx + gfy * gfy), 0.004f);
        }
        float3 lnd;
        {
            const float3 pig = make_float3(w2.x * (1.f / 255.f), w2.y * (1.f / 255.f), w2.z * (1.f / 255.f));
            const float cov = w2.w * (1.f / 255.f);
            const float gran = 0.84f + 0.32f * ta_tn(Tx, mx * S * 5.f + 71.f, my * S * 5.f + 13.f);
            const float ep = fsv * __expf(-df / (5.f * sc)), ec = __expf(-fmaxf(dc, 0.f) / (4.f * sc));
            float dn = clamp01(P[TA_WASH]) * (0.46f + 0.30f * ep + 0.22f * ec) * gran * cov * (1.f - 0.45f * draft)
                     * P[TA_BREATH] * (1.f + 0.35f * wet);
            dn = fminf(fmaxf(dn, 0.f), 1.2f);
            lnd = make_float3(paper.x * (1.f - dn * (1.f - pig.x)), paper.y * (1.f - dn * (1.f - pig.y)),
                              paper.z * (1.f - dn * (1.f - pig.z)));
        }
        o = ta_mix3(sea, lnd, lc);

        // ---- marsh: stipple and reed tufts where the windows were thinly filled ----
        if (mar > 0.02f) {
            float a = 0.f;
            const float mc = 0.0068f;
            const float fx = floorf(mx / mc), fy = floorf(my / mc);
            const unsigned int ix = (unsigned int)(int)fx, iy = (unsigned int)(int)fy;
            if (hash1(ix, iy, seed + 11u) < mar * 0.85f) {
                const float ox = (fx + 0.25f + 0.5f * hash1(ix, iy, seed + 12u)) * mc;
                const float oy = (fy + 0.25f + 0.5f * hash1(ix, iy, seed + 13u)) * mc;
                const float d = sqrtf((mx - ox) * (mx - ox) + (my - oy) * (my - oy)) * ppm;
                const float r = (0.45f + 0.35f * hash1(ix, iy, seed + 14u)) * sc * (0.8f + 0.4f * ink);
                a = 1.f - smooth(r - 0.6f, r + 0.6f, d);
            }
            const float tc = 0.026f;
            const float gx = floorf(mx / tc), gy = floorf(my / tc);
            const unsigned int jx = (unsigned int)(int)gx, jy = (unsigned int)(int)gy;
            if (hash1(jx, jy, seed + 15u) < mar * 1.1f - 0.2f) {
                const float ox = (gx + 0.3f + 0.4f * hash1(jx, jy, seed + 16u)) * tc;
                const float oy = (gy + 0.55f + 0.2f * hash1(jx, jy, seed + 17u)) * tc;
                const float lx = (mx - ox) * ppm, ly = (my - oy) * ppm;
                const float hw = 0.30f * tc * ppm, lwd = 0.45f * sc;
                float t = (1.f - smooth(hw - 0.8f, hw + 0.8f, fabsf(lx))) * ta_line(ly, lwd);
                #pragma unroll
                for (int bl = -1; bl <= 1; ++bl) {
                    const float bh = (bl == 0 ? 0.55f : 0.38f) * hw;
                    if (ly < 0.f && ly > -bh)
                        t = fmaxf(t, ta_line(lx - (float)bl * 0.36f * hw + (float)bl * 0.25f * ly, lwd));
                }
                a = fmaxf(a, t);
            }
            o = ta_mix3(o, inkc, a * 0.65f * ia * smooth(0.02f, 0.2f, mar));
        }

        // ---- mountains: form lines, hachures down the fall line, molehill peaks on the high ground ----
        if (lc > 0.01f && b0.y > 0.06f) {
            float a = 0.f;
            const float clv[3] = {0.30f, 0.55f, 0.80f};
            #pragma unroll
            for (int i = 0; i < 3; ++i) a = fmaxf(a, ta_line((b0.y - clv[i]) / fmaxf(hg, 1e-4f), 0.35f * sc) * 0.30f);
            // a brick grid of cells in map space, one stroke each
            const float cs = 0.0153f;
            const float ry = floorf(my / cs);
            const float sh = (((int)ry) & 1) ? 0.5f : 0.f;
            const float rx = floorf(mx / cs + sh);
            const unsigned int ux = (unsigned int)(int)rx, uy = (unsigned int)(int)ry;
            const float ccx = (rx + 0.5f - sh + 0.16f * (hash1(ux, uy, seed + 21u) - 0.5f)) * cs;
            const float ccy = (ry + 0.5f + 0.16f * (hash1(ux, uy, seed + 22u) - 0.5f)) * cs;
            const float spx = (ccx - cxm) * ppm + 0.5f * (float)W, spy = (ccy - cym) * ppm + 0.5f * (float)H;
            const float h0 = ta_lb(LB, lw, lh, spx * il, spy * il).y;
            const float hx = ta_lb(LB, lw, lh, (spx + 3.f) * il, spy * il).y;
            const float hy = ta_lb(LB, lw, lh, spx * il, (spy + 3.f) * il).y;
            const float gx = (hx - h0) * (1.f / 3.f), gy = (hy - h0) * (1.f / 3.f);
            const float sl = sqrtf(gx * gx + gy * gy);
            if (h0 > 0.08f && sl > 1e-4f) {
                const float dx = gx / sl, dy = gy / sl;            // uphill
                const float st = smooth(0.0012f, 0.006f, sl);
                const float hl = 0.38f * cs * ppm * st;
                const float rxp = px - spx, ryp = py - spy;
                const float t = fminf(fmaxf(rxp * dx + ryp * dy, -hl), hl);
                const float ex = rxp - t * dx, ey = ryp - t * dy;
                const float wd = (0.30f + 0.45f * st) * sc * (0.7f + 0.6f * ink);
                const float shd = 0.55f + 0.45f * clamp01(0.5f - 0.5f * (0.6f * dx + 0.8f * dy));   // away from the NW light
                a = fmaxf(a, ta_line(sqrtf(ex * ex + ey * ey), wd) * st * shd * smooth(0.08f, 0.25f, h0) * 0.9f);
            }
            o = ta_mix3(o, inkc, a * ia * lc);
            // molehills: a sparser brick grid; each is an arch, its eastern flank shaded with fine strokes
            const float mcs = 0.034f;
            const float my2 = floorf(my / mcs);
            const float sh2 = (((int)my2) & 1) ? 0.5f : 0.f;
            const float mx2 = floorf(mx / mcs + sh2);
            const unsigned int vx = (unsigned int)(int)mx2, vy = (unsigned int)(int)my2;
            if (hash1(vx, vy, seed + 31u) < 0.42f) {
                const float bcx = (mx2 + 0.5f - sh2 + 0.2f * (hash1(vx, vy, seed + 32u) - 0.5f)) * mcs;
                const float bcy = (my2 + 0.74f) * mcs;
                const float hpx = (bcx - cxm) * ppm + 0.5f * (float)W, hpy = (bcy - 0.25f * mcs - cym) * ppm + 0.5f * (float)H;
                const float hh0 = ta_lb(LB, lw, lh, hpx * il, hpy * il).y;
                if (hh0 > 0.78f) {
                    const float k = smooth(0.78f, 0.97f, hh0);
                    const float hw = 0.36f * mcs * ppm * (0.75f + 0.25f * hash1(vx, vy, seed + 33u));
                    const float hh = hw * (0.55f + 0.55f * k);
                    const float lx = (mx - bcx) * ppm, ly = (my - bcy) * ppm;
                    if (fabsf(lx) < hw + 2.f && ly > -hh - 2.f && ly < 2.f) {
                        const float xx = lx / hw;
                        const float yc = -hh * fmaxf(1.f - xx * xx, 0.f);
                        const float slope = 2.f * hh * xx / hw;
                        const float d = (ly - yc) * rsqrtf(1.f + slope * slope);
                        const bool under = fabsf(xx) < 1.f && ly > yc && ly < 0.f;
                        const float kin = under ? clamp01(-d) * clamp01(-ly) : 0.f;
                        o = ta_mix3(o, lnd, kin * 0.85f * k);
                        const float sp2 = 2.4f * sc;
                        const float hat = (xx > 0.12f) ? ta_line((lx / sp2 - floorf(lx / sp2) - 0.5f) * sp2, 0.35f * sc) : 0.f;
                        o = ta_mix3(o, inkc, kin * hat * 0.55f * k * ia);
                        const float outl = fabsf(xx) < 1.05f ? ta_line(d, (0.4f + 0.3f * ink) * sc) : 0.f;
                        o = ta_mix3(o, inkc, outl * 0.9f * k * ia * lc);
                    }
                }
            }
        }

        // ---- frontiers: dash-and-dot lines between two clear keys ----
        if (fsv > 0.01f && df < 8.f * sc) {
            const float fwd = (0.45f + 0.75f * ink) * sc;
            float a = ta_line(df, fwd);
            const float along = (fabsf(gfx) > fabsf(gfy) ? my : mx) * ppm;     // along the line, roughly
            const float per = 14.f * sc;
            float t = along / per;
            t -= floorf(t);
            const float aa = 0.8f / per;
            const float on1 = smooth(-aa, aa, t) * (1.f - smooth(0.48f - aa, 0.48f + aa, t));
            const float on2 = smooth(0.63f - aa, 0.63f + aa, t) * (1.f - smooth(0.71f - aa, 0.71f + aa, t));
            a *= fmaxf(on1, on2) * fsv * lc;
            const float3 fc = ta_mix3(inkc, make_float3(0.42f, 0.12f, 0.10f), 0.25f * (1.f - draft) * (1.f - wet));
            o = ta_mix3(o, fc, a * 0.9f * ia);
        }

        // ---- the coast: a heavier line; wet ink bleeds into the paper's fibres ----
        {
            const float cw = (0.75f + 1.6f * ink) * sc * (1.f + 0.5f * wet);
            float a = ta_line(dc, 0.5f * cw);
            if (wet > 0.01f) {
                const float fib = ta_tn(Tx, px / (1.7f * sc) + 13.f, py / (6.f * sc) + 77.f);
                a = fmaxf(a, wet * 0.45f * __expf(-fabsf(dc) / (2.4f * sc)) * (0.5f + 0.8f * fib) * (0.4f + 0.6f * en));
            }
            o = ta_mix3(o, inkc, clamp01(a) * 0.95f * ia);
        }
        // fresh ink catches the light; a pulse brightens it
        if (wet > 0.01f) {
            const float gn = ta_tn(Tx, mx * S * 1.7f + P[TA_CLOCK] * 0.9f, my * S * 1.7f + 19.f);
            const float g = clamp01(wet * P[TA_GLOSS] * (0.5f + 0.5f * gn) * fmaxf(lc, 0.5f * clamp01(1.f - fabsf(dc) / (4.f * sc))));
            o.x += (1.00f - o.x) * g; o.y += (0.93f - o.y) * g; o.z += (0.78f - o.z) * g;
        }

        // ---- rhumb lines: sixteen faint rays from the compass rose ----
        const float rcx = P[TA_RCX], rcy = P[TA_RCY], R = fmaxf(P[TA_RR], 1.f), ph = P[TA_RANG];
        const float rdx = px - rcx, rdy = py - rcy;
        const float rho = sqrtf(rdx * rdx + rdy * rdy);
        {
            const float st = TA_PI / 8.f;
            const float ga = atan2f(rdx, -rdy) - ph;
            const float j = rintf(ga / st);
            const float d = rho * fabsf(__sinf(ga - j * st));
            const int jj = (((int)j) % 16 + 16) % 16;
            const bool card = (jj & 3) == 0;
            const float a = ta_line(d, (card ? 0.45f : 0.3f) * sc) * (card ? 0.30f : 0.17f)
                          * smooth(1.2f * R, 1.5f * R, rho) * (1.f - 0.3f * draft);
            o = ta_mix3(o, card ? make_float3(0.50f, 0.17f, 0.12f) : sepia, a);
        }
        // ---- the capital: the island's heart, where the windows hear the whole piece ----
        if (P[TA_HUBA] > 0.004f) {
            const float dx = px - P[TA_HUBSX], dy = py - P[TA_HUBSY];
            const float d = sqrtf(dx * dx + dy * dy);
            const float r = 4.5f * sc;
            o = ta_mix3(o, make_float3(paper.x * 1.02f, paper.y * 1.02f, paper.z * 1.02f),
                        (1.f - smooth(r + 1.5f * sc, r + 3.f * sc, d)) * 0.7f * P[TA_HUBA]);
            const float a = fmaxf(ta_line(d - r, 0.6f * sc), 1.f - smooth(1.6f * sc, 2.4f * sc, d));
            o = ta_mix3(o, sepia, a * P[TA_HUBA] * ia);
        }
        // ---- the key names: italic ink with a paper halo ----
        const int GW = (int)P[TA_GW], GH = (int)P[TA_GH];
        if (GW > 0 && GH > 0) {
            for (int i = 0; i < TA_NLAB; ++i) {
                const float* L = P + TA_OLAB + 8 * i;
                const float la = L[3];
                if (!(la > 0.004f)) continue;
                const float s = fmaxf(L[2], 0.05f);
                const float u = (px - L[0]) / s + 0.5f * (float)GW, v = (py - L[1]) / s + 0.5f * (float)GH;
                if (u < -1.f || v < -1.f || u > (float)GW + 1.f || v > (float)GH + 1.f) continue;
                const int gi = min(max((int)L[4], 0), 23);
                const float2 q = ta_glyph(atlas, gi, u, v, GW, GH);
                o = ta_mix3(o, make_float3(paper.x * 1.03f, paper.y * 1.03f, paper.z * 1.03f), clamp01(la * 0.75f * q.y));
                o = ta_mix3(o, make_float3(L[5], L[6], L[7]), clamp01(la * 0.95f * q.x));
            }
        }
        // ---- the compass rose: a ring of the twelve keys (fixed), the star turned to the piece's key ----
        if (rho < 1.32f * R) {
            o = ta_mix3(o, make_float3(0.945f, 0.90f, 0.79f), (1.f - smooth(1.18f * R, 1.26f * R, rho)) * 0.8f);
            o = ta_mix3(o, sepia, fmaxf(ta_line(rho - 1.16f * R, 0.9f * sc), ta_line(rho - 1.00f * R, 0.6f * sc)) * 0.9f);
            const float ang = atan2f(rdx, -rdy);
            const float ji = rintf(ang / (TA_PI / 6.f));
            const int ki = (((int)ji) % 12 + 12) % 12;
            const float ca = ji * (TA_PI / 6.f);
            const float ddx = rdx - 1.08f * R * __sinf(ca), ddy = rdy + 1.08f * R * __cosf(ca);
            const float dd = sqrtf(ddx * ddx + ddy * ddy);
            const float rr = 0.055f * R;
            const int k = (7 * ki) % 12;                  // the major key at this place on the circle of fifths
            o = ta_mix3(o, make_float3(P[TA_OKEYS + 3 * k], P[TA_OKEYS + 3 * k + 1], P[TA_OKEYS + 3 * k + 2]),
                        1.f - smooth(rr - 0.7f, rr + 0.7f, dd));
            o = ta_mix3(o, sepia, ta_line(dd - rr, 0.5f * sc) * 0.9f);
            const float cph = __cosf(ph), sph = __sinf(ph);
            const float qx = rdx * cph + rdy * sph, qy = -rdx * sph + rdy * cph;      // the rose's own frame
            const float gq = atan2f(qx, -qy);
            const float3 light = make_float3(0.965f, 0.935f, 0.85f);
            float d1, d2;
            const float bd = (floorf(gq / (0.5f * TA_PI)) + 0.5f) * (0.5f * TA_PI);    // the nearest diagonal
            ta_kite(qx, qy, bd, 0.62f * R, 0.11f * R, TA_PI / 8.f, d1, d2);
            o = ta_mix3(o, sepia, clamp01(0.5f - d1) * 0.85f);
            o = ta_mix3(o, light, clamp01(0.5f - d2));
            o = ta_mix3(o, sepia, ta_line(fminf(d1, d2), 0.45f * sc) * 0.9f);
            const float jc = rintf(gq / (0.5f * TA_PI));                                // the nearest cardinal
            ta_kite(qx, qy, jc * 0.5f * TA_PI, 0.95f * R, 0.16f * R, 0.25f * TA_PI, d1, d2);
            const bool north = ((((int)jc) % 4) + 4) % 4 == 0;
            const float3 dark = north ? ta_mix3(sepia, make_float3(P[TA_SUMR], P[TA_SUMG], P[TA_SUMB]), clamp01(P[TA_SUMA]))
                                      : sepia;
            o = ta_mix3(o, dark, clamp01(0.5f - d1) * 0.9f);
            o = ta_mix3(o, light, clamp01(0.5f - d2));
            o = ta_mix3(o, sepia, ta_line(fminf(d1, d2), 0.5f * sc) * 0.95f);
            o = ta_mix3(o, sepia, fmaxf(ta_line(rho - 0.07f * R, 0.5f * sc), 1.f - smooth(0.03f * R, 0.03f * R + 1.f, rho)));
        }
    }

    // ---- the neat frame: two rules, a graduated band between them, plain paper outside ----
    if (din > 0.f) {
        o = paper;
        if (dout < 0.f) {
            const float bxm = ax - (0.5f * (float)W - e1), bym = ay - (0.5f * (float)H - e1);
            const bool corner = bxm > 0.f && bym > 0.f;
            const float seg = 0.034f * (float)H;
            const float along = bym > bxm ? px - e0 : py - e0;
            const int si = (int)floorf(along / seg);
            const float inset = fminf(din, -dout);
            const float fb = (((si & 1) == 0) || corner) ? smooth(1.0f * sc, 2.0f * sc, inset) : 0.f;
            o = ta_mix3(o, sepia, fb * 0.82f);
        } else {
            o.x *= 0.965f; o.y *= 0.955f; o.z *= 0.94f;
        }
    }
    o = ta_mix3(o, sepia, fmaxf(ta_line(dout, 1.1f * sc), ta_line(din, 0.6f * sc)) * 0.92f);
    // ---- the vignette: the sheet's edges browned ----
    {
        const float vx = (px / (float)W - 0.5f) * 2.f, vy = (py / (float)H - 0.5f) * 2.f;
        const float r = sqrtf(0.8f * vx * vx + vy * vy);
        const float burn = ta_tn(Tx, px / (25.f * sc) + 7.f, py / (25.f * sc) + 150.f);
        const float vg = smooth(0.6f, 1.5f, r + 0.25f * (burn - 0.5f));
        o.x *= 1.f - 0.30f * vg; o.y *= 1.f - 0.33f * vg; o.z *= 1.f - 0.40f * vg;
    }
    const float gn = 255.f * fmaxf(P[TA_GAIN], 0.f);
    float cr8 = gn * o.x, cg8 = gn * o.y, cb8 = gn * o.z;
    const float fd = clamp01(P[TA_FADE]);
    if (fd > 0.f) {                                       // the previous piece's map, fading
        const uchar4 q = old[idx];
        cr8 += ((float)q.x - cr8) * fd; cg8 += ((float)q.y - cg8) * fd; cb8 += ((float)q.z - cb8) * fd;
    }
    const float dth = (hash_u((unsigned int)idx ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    out[idx] = pack(make_float4(cr8 + dth, cg8 + dth, cb8 + dth, 255.f));
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")


# ------------------------------------------------------------------------------------------------- host helpers
def _fin(x, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _c01(x) -> float:
    return min(max(_fin(x), 0.0), 1.0)


def _ease(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau) if dt > 0.0 and tau > 0.0 else 0.0


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _noise_texture(seed: int, n: int = NOISE) -> np.ndarray:
    """A tiling smooth noise (a sum of random low-frequency waves), normalised to mean 0.5 and sd 1/6, in 0..1."""
    rng = np.random.default_rng(seed + 4242)
    yy, xx = np.mgrid[0:n, 0:n] / n
    f = np.zeros((n, n))
    for octave, amp in ((1, 1.0), (2, 0.65), (3, 0.45), (4, 0.32), (6, 0.22), (9, 0.14), (13, 0.09)):
        for _ in range(3):
            kx, ky = rng.integers(-octave, octave + 1, 2)
            if kx == 0 and ky == 0:
                kx = octave
            f += amp * np.cos(2 * np.pi * (kx * xx + ky * yy) + rng.uniform(0, 2 * np.pi))
    f = 0.5 + (f - f.mean()) / (6.0 * max(float(f.std()), 1e-9))
    return np.clip(f, 0.0, 1.0).astype(np.float32)


def _tn_np(T: np.ndarray, u, v):
    """The kernel's ta_tn (bilinear, tiling) over arrays."""
    u = np.asarray(u, np.float64) - 0.5
    v = np.asarray(v, np.float64) - 0.5
    fu, fv = np.floor(u), np.floor(v)
    x0 = fu.astype(np.int64) & (NOISE - 1)
    y0 = fv.astype(np.int64) & (NOISE - 1)
    x1, y1 = (x0 + 1) & (NOISE - 1), (y0 + 1) & (NOISE - 1)
    tx, ty = u - fu, v - fv
    return ((1 - ty) * ((1 - tx) * T[y0, x0] + tx * T[y0, x1]) + ty * ((1 - tx) * T[y1, x0] + tx * T[y1, x1]))


def _fb3_np(T, x, y):
    return 0.6 * _tn_np(T, x, y) + 0.3 * _tn_np(T, 2.1 * x + 37.0, 2.1 * y + 11.0) + 0.1 * _tn_np(T, 4.3 * x + 91.0,
                                                                                               4.3 * y + 53.0)


def _warp_np(T, mx, my, a1: float, a2: float, s: float, ox: float, oy: float):
    """The kernel's ta_warp over arrays: map points -> chart points."""
    mx = np.asarray(mx, np.float64)
    my = np.asarray(my, np.float64)
    qx, qy = mx * s + ox, my * s + oy
    m1x = mx + a2 * (_fb3_np(T, qx + 13.0, qy) - 0.5)
    m1y = my + a2 * (_fb3_np(T, qx, qy + 71.0) - 0.5)
    qx, qy = m1x * s + ox, m1y * s + oy
    return mx + a1 * (_fb3_np(T, qx + 101.0, qy + 7.0) - 0.5), my + a1 * (_fb3_np(T, qx + 59.0, qy + 143.0) - 0.5)


def _tn_s(T: list, u: float, v: float) -> float:
    """ta_tn for one point, in plain Python (T as nested lists): the per-frame camera and capital."""
    u -= 0.5
    v -= 0.5
    fu, fv = math.floor(u), math.floor(v)
    x0, y0 = int(fu) & (NOISE - 1), int(fv) & (NOISE - 1)
    x1, y1 = (x0 + 1) & (NOISE - 1), (y0 + 1) & (NOISE - 1)
    tx, ty = u - fu, v - fv
    r0, r1 = T[y0], T[y1]
    return (1 - ty) * ((1 - tx) * r0[x0] + tx * r0[x1]) + ty * ((1 - tx) * r1[x0] + tx * r1[x1])


def _fb3_s(T, x: float, y: float) -> float:
    return 0.6 * _tn_s(T, x, y) + 0.3 * _tn_s(T, 2.1 * x + 37.0, 2.1 * y + 11.0) + 0.1 * _tn_s(T, 4.3 * x + 91.0,
                                                                                             4.3 * y + 53.0)


def _warp_s(T, mx: float, my: float, a1: float, a2: float, s: float, ox: float, oy: float) -> tuple[float, float]:
    qx, qy = mx * s + ox, my * s + oy
    m1x = mx + a2 * (_fb3_s(T, qx + 13.0, qy) - 0.5)
    m1y = my + a2 * (_fb3_s(T, qx, qy + 71.0) - 0.5)
    qx, qy = m1x * s + ox, m1y * s + oy
    return mx + a1 * (_fb3_s(T, qx + 101.0, qy + 7.0) - 0.5), my + a1 * (_fb3_s(T, qx + 59.0, qy + 143.0) - 0.5)


def _rho_np(th, lobes: np.ndarray):
    """The rim's radius factor at chart angles ``th``: 1 + sum a_k cos(LOBE_K[k] th + phi_k), at least 0.4."""
    th = np.asarray(th, np.float64)
    r = np.ones_like(th)
    for k in range(4):
        r = r + lobes[2 * k] * np.cos(LOBE_K[k] * th + lobes[2 * k + 1])
    return np.maximum(r, 0.4)


def _chart_np(wx, wy, fj: float, lobes: np.ndarray):
    """The kernel's ta_geo over arrays: chart points -> (u, v, well inside the island)."""
    rx, ry = (np.asarray(wx) - HUB[0]) / RX, (np.asarray(wy) - HUB[1]) / RY
    r = np.hypot(rx, ry)
    th = np.arctan2(ry, rx)
    rn = r / _rho_np(th, lobes)
    d = th - fj
    d = d - 2 * np.pi * np.floor(d / (2 * np.pi))
    rc = np.minimum(rn, 1.2)
    gap = GAP * rc * np.sqrt(rc)
    arc = np.maximum(2 * np.pi - 2 * gap, 1e-3)
    u = (d - gap) / arc
    v = 1.0 - np.maximum(rn, 1e-12) ** (1.0 / GAMMA)
    ok = (rn < 0.97) & (d > gap + 0.01) & (d < 2 * np.pi - gap - 0.01)
    return u, v, ok


def _bake_labels(font_px: int) -> tuple[np.ndarray, int, int]:
    """The 24 key names (Sapp's case, italic serif) as a uint8 atlas [24, GH, GW, 2]: fill, soft halo."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        path = next((f for f in FONTS if os.path.exists(f)), None)
        if path is None:
            raise OSError("no serif font")
        font = ImageFont.truetype(path, max(8, int(font_px)))
        names = [KF.label(k) for k in range(24)]
        gh = max(12, int(round(font_px * 1.6)))
        gw = max(12, int(math.ceil(max(font.getlength(s) for s in names) + 0.9 * font_px)))
        out = np.zeros((24, gh, gw, 2), np.uint8)
        for i, s in enumerate(names):
            img = Image.new("L", (gw, gh), 0)
            ImageDraw.Draw(img).text((gw / 2, gh / 2), s, fill=255, font=font, anchor="mm")
            halo = img.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(max(1.2, gh * 0.08)))
            out[i, :, :, 0] = np.asarray(img, np.uint8)
            out[i, :, :, 1] = np.minimum(np.asarray(halo, np.float32) * 2.0, 255).astype(np.uint8)
        return out, gw, gh
    except Exception as exc:                          # degrade: no names
        log.warning("tonal_atlas: cannot bake the key names (%s); drawing none", exc)
        return np.zeros((24, 1, 1, 2), np.uint8), 0, 0


# ------------------------------------------------------------------------------------------------- the scene
class TonalAtlas(Source):
    """Per frame: two key-field bakes at most (each skipped when unchanged), one arena upload, five launches (the
    field, the 4x reduction, two blur passes, the sheet) and the upsample.  Host: the shared timeline's update, the
    camera, and a label job (136 windows a frame, one flood fill a second)."""
    name = "tonal_atlas"
    label = "Tonal atlas"
    description = ("The piano's keys drawn as an antique hand-coloured map: each key a territory, the home key the "
                   "heartland, passing modulations coastal provinces, rests bays, the newest coast in fresh ink.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette=None, render_scale=0.75),
            "warp": num(0.0, 1.0, 0.55, "Wild coastline", basic=True),
            "wash": num(0.0, 1.0, 0.7, "Colour wash", basic=True),
            "ink": num(0.0, 1.0, 0.6, "Ink weight"),
            "zoom": num(0.0, 1.0, 0.3, "Zoom on the newest coast"),
            "centred": num(0.0, 1.0, 0.0, "Sapp's centred windows"),
            "log_rows": num(0.0, 1.0, 0.75, "Long spans inland (log rows)"),
            "hue_shift": num(0.0, 1.0, 0.0, "Hue shift"),
            "piece_gap": num(30.0, 300.0, 90.0, "New map after", unit="s", tier="structural"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sc = self.h / 1080.0
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        rng = np.random.default_rng(self.seed + 77)
        mod = raw_module(_SRC, _OPTS)
        self.k_field = mod.get_function("ta_field")
        self.k_down = mod.get_function("ta_down")
        self.k_blur = mod.get_function("ta_blur")
        self.k_comp = mod.get_function("ta_compose")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.old = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.P0 = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.P1 = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.P2 = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.lw, self.lh = (self.w + LOW - 1) // LOW, (self.h + LOW - 1) // LOW
        self.D = cp.zeros((self.lh, self.lw, 2), cp.float32)
        self.E = cp.zeros((self.lh, self.lw, 2), cp.float32)
        self.LB = cp.zeros((self.lh, self.lw, 2), cp.float32)
        self.T = _noise_texture(self.seed)
        self.Tl = self.T.astype(np.float64).tolist()
        self.tex = cp.asarray(self.T)
        atlas, self.gw, self.gh = _bake_labels(int(round(LABEL_PX * 1.3 * self.h)))
        self.atlas = cp.asarray(np.ascontiguousarray(atlas))
        self.fT = KF.KeyField(ctx, FW, FH)
        self.fC = KF.KeyField(ctx, FW, FH)
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        # the island's seeded geography
        self.fj = float(rng.uniform(-0.35, 0.35))                  # the sound's axis (rad; 0 = east, + = south)
        self.ox, self.oy = float(rng.uniform(0, NOISE)), float(rng.uniform(0, NOISE))
        self.lobes0 = np.zeros(8)
        for k in range(4):
            self.lobes0[2 * k] = rng.uniform(0.05, 0.12)
            self.lobes0[2 * k + 1] = rng.uniform(0.0, 2 * np.pi)
        # the timeline (shared per screen; a private one while there is no sound object)
        self._own_tl = None
        self._tl_key = None
        # motion and state
        self.frames = 0
        self.kick = self.shim = self.drift = self.breath = self.clock = 0.0
        self.fade = 0.0
        self.draft = 1.0
        self.fresh = 0.0
        self.cam = [0.0, 0.0]
        self.rang = 0.0
        self.suma = 0.0
        self.sum_rgb = np.array(KF.key_rgb(0))
        self._keys_hs = None
        self._grid_cache = None
        self._hub_cache = None
        self.hub_px = (0.0, 0.0)
        # labels: {(generation, key): state}; the refresh job
        self.labs: dict = {}
        self.gen = 0
        self.job = None
        self.job_t = 0.0

    # -- the timeline ---------------------------------------------------------------------------------------------
    def _timeline(self, dt: float, gap: float):
        snd = getattr(self, "sound", None)
        if snd is None:
            if self._own_tl is None:
                self._own_tl = KF.KeyTimeline(self.seed)
            tl = self._own_tl
        else:
            tl = KF.KeyTimeline.shared(snd, self.seed)
        tl.update(snd, dt, gap)
        return tl

    def _new_generation(self) -> None:
        for st in self.labs.values():
            st["ta"] = 0.0
        self.gen += 1
        self.job = None
        self.job_t = 0.25

    # -- geography -------------------------------------------------------------------------------------------------
    def _lobes(self, warp: float) -> np.ndarray:
        lb = self.lobes0.copy()
        lb[0::2] *= 0.75 + 0.55 * warp
        return lb

    def _rho(self, th: float, lobes: np.ndarray) -> float:
        r = 1.0
        for k in range(4):
            r += lobes[2 * k] * math.cos(LOBE_K[k] * th + lobes[2 * k + 1])
        return max(r, 0.4)

    def _grid(self, a1: float, a2: float, lobes: np.ndarray):
        """The label grid's map points and their chart (u, v, inside the island), cached per warp."""
        key = (round(a1, 3), round(a2, 3), round(float(lobes[0]), 4))
        if self._grid_cache is not None and self._grid_cache[0] == key:
            return self._grid_cache[1]
        xs = HUB[0] + (np.arange(GX) + 0.5) / GX * 2.0 * GRID_X - GRID_X
        ys = HUB[1] + (np.arange(GY) + 0.5) / GY * 2.0 * GRID_Y - GRID_Y
        MY, MX = np.meshgrid(ys, xs, indexing="ij")
        wx, wy = _warp_np(self.T, MX.ravel(), MY.ravel(), a1, a2, TEX_S, self.ox, self.oy)
        u, v, ok = _chart_np(wx, wy, self.fj, lobes)
        res = (u, v, ok)
        self._grid_cache = (key, res)
        return res

    def _hub_screen(self, a1: float, a2: float) -> tuple[float, float]:
        """The capital's map point: the warp inverted at the hub by a damped fixed-point iteration (cached)."""
        key = (round(a1, 3), round(a2, 3))
        if self._hub_cache is not None and self._hub_cache[0] == key:
            return self._hub_cache[1]
        mx, my = HUB
        for _ in range(8):
            wx, wy = _warp_s(self.Tl, mx, my, a1, a2, TEX_S, self.ox, self.oy)
            mx += 0.8 * (HUB[0] - wx)
            my += 0.8 * (HUB[1] - wy)
        res = (mx, my) if math.isfinite(mx) and math.isfinite(my) else HUB
        self._hub_cache = (key, res)
        return res

    def _camera(self, dt: float, e: float, zoom: float, fd, a1: float, a2: float, lobes: np.ndarray):
        """The view follows the newest coast (the sound's north bank, or the growing tip early in a piece)."""
        Z = (1.0 + 0.75 * zoom) * (1.0 + 0.025 * math.sin(2 * math.pi * self.breath))
        ppm = self.h / 1.5 * Z
        span = fd.span if fd.span > 1e-6 else KF.V_MIN
        u_now = min(max((fd.now - fd.org) / span, 0.0), 1.0)
        rn = 0.62
        g = GAP * rn ** 1.5
        th = self.fj + g + u_now * (2 * math.pi - 2 * g)
        rho = self._rho(th, lobes)
        nx, ny = HUB[0] + RX * rn * rho * math.cos(th), HUB[1] + RY * rn * rho * math.sin(th)
        wx, wy = _warp_s(self.Tl, nx, ny, a1, a2, TEX_S, self.ox, self.oy)
        nx, ny = 2.0 * nx - wx, 2.0 * ny - wy                       # one step back through the warp
        k = min(max((Z - 1.0) / 0.75, 0.0), 1.0)
        tx = nx * (0.18 + 0.7 * k) + 0.035 * math.sin(2 * math.pi * self.drift)
        ty = ny * (0.18 + 0.7 * k) + 0.022 * math.sin(2 * math.pi * 0.73 * self.drift + 1.1)
        hx, hy = 0.5 * self.w / ppm, 0.5 * self.h / ppm
        lx = max(RX * 1.35 + 0.1 - 0.85 * hx, 0.0)
        ly = max(RY * 1.35 + 0.1 - 0.85 * hy, 0.0)
        tx, ty = min(max(tx, -lx), lx), min(max(ty, -ly), ly)
        if not (math.isfinite(tx) and math.isfinite(ty)):
            tx, ty = 0.0, 0.0
        if self.frames == 0:
            self.cam = [tx, ty]
        else:
            a = _ease(dt, 10.0 / (0.4 + 1.4 * e))
            self.cam[0] += (tx - self.cam[0]) * a
            self.cam[1] += (ty - self.cam[1]) * a
        return Z, ppm

    # -- labels ------------------------------------------------------------------------------------------------------
    def _label_job(self, tl, dt: float, mode: int, logr: float, a1: float, a2: float, lobes: np.ndarray) -> None:
        """Every LABEL_PERIOD: the grid's windows (spread over LABEL_FRAMES frames), then the regions."""
        if self.job is None:
            self.job_t -= dt
            if self.job_t > 0.0:
                return
            self.job_t = LABEL_PERIOD
            n = int(tl.n)
            if n < 3:
                return
            org, span, now, lo = KF.view_geometry(tl, None)
            u, v, ok = self._grid(a1, a2, lobes)
            L = KF.window_len(np.clip(v, 0.0, 1.0), span, logr)
            pos = org + u * span
            if mode == KF.CENTRED:
                s, e = pos - 0.5 * L, pos + 0.5 * L
                inside = (s >= lo - 1e-6) & (e <= now + 1e-6)
            else:
                e, s = pos, pos - L
                inside = (pos >= lo) & (pos <= now)
            e = np.clip(e, lo, float(n))
            s = np.clip(s, lo, e)
            keep = ok & inside & (e - s > 0.5)
            self.job = {"tl": tl, "n": n, "pieces": tl.pieces, "ex": tl.example, "gen": self.gen, "s": s, "e": e,
                        "L": np.maximum(e - s, 1e-3), "keep": keep, "i": 0, "ids": -np.ones(s.size, np.int64),
                        "clar": np.zeros(s.size)}
            return
        j = self.job
        if (tl is not j["tl"] or tl.n < j["n"] or tl.pieces != j["pieces"] or tl.example != j["ex"]
                or j["gen"] != self.gen):
            self.job = None
            return
        size = j["s"].size
        step = -(-size // LABEL_FRAMES)
        i = j["i"]
        if i < size:
            sl = slice(i, min(i + step, size))
            ids, clar, _fog = KF.windows_keys(tl.S, j["n"], j["s"][sl], j["e"][sl], j["L"][sl])
            j["ids"][sl] = np.where(j["keep"][sl], ids, -1)
            j["clar"][sl] = clar
            j["i"] = i + step
            return
        self.job = None
        regs = KF.regions(j["ids"].reshape(GY, GX), j["clar"].reshape(GY, GX), top=4)
        keep = set()
        cw, ch = 2.0 * GRID_X / GX, 2.0 * GRID_Y / GY
        for k, _area, row, col, depth in regs:
            key = (self.gen, int(k))
            keep.add(key)
            x = HUB[0] - GRID_X + (col + 0.5) * cw
            y = HUB[1] - GRID_Y + (row + 0.5) * ch
            s = min(max(0.62 + 0.16 * depth, 0.62), 1.15)
            st = self.labs.get(key)
            if st is None:
                st = {"x": x, "y": y, "vx": 0.0, "vy": 0.0, "s": s, "a": 0.0}
                self.labs[key] = st
            st.update(tx=x, ty=y, ts=s, ta=1.0)
        for key, st in self.labs.items():
            if key[0] == self.gen and key not in keep:
                st["ta"] = 0.0

    def _ease_labels(self, dt: float) -> None:
        a = _ease(dt, 0.5)
        af = _ease(dt, 0.6)
        w = 2.6                                       # a critically damped spring (rad/s)
        h = min(dt, 0.1)
        dead = []
        for key, st in self.labs.items():
            if "tx" not in st:
                continue
            for q, vq in (("x", "vx"), ("y", "vy")):
                x, vel = st[q], st[vq]
                acc = w * w * (st["t" + q] - x) - 2.0 * w * vel
                vel += acc * h
                st[vq], st[q] = vel, x + vel * h
            st["s"] += (st["ts"] - st["s"]) * a
            st["a"] += (st["ta"] - st["a"]) * af
            if st["ta"] == 0.0 and st["a"] < 0.01:
                dead.append(key)
        for key in dead:
            del self.labs[key]

    # -- render ------------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = float(dt) if dt == dt and 0.0 < dt < math.inf else 0.0
        e = _c01(p["energy"])
        pulse = _c01(p["pulse"])
        spd = min(max(_fin(p["speed"], 1.0), 0.0), 8.0)
        warp, wash, ink = _c01(p["warp"]), _c01(p["wash"]), _c01(p["ink"])
        zoom, morph, logr = _c01(p["zoom"]), _c01(p["centred"]), _c01(p["log_rows"])
        hs = 180.0 * _c01(p["hue_shift"])
        gain = min(max(_fin(p["intensity"], 1.0), 0.0), 4.0)
        gap = _fin(p["piece_gap"], 90.0)

        tau = 0.08 if pulse > self.kick else 0.35     # kicks ease in, then fall away
        self.kick += (pulse - self.kick) * _ease(dt, tau)
        self.shim = (self.shim + (0.05 + 0.25 * e + 0.08 * self.kick) * spd * dt) % 1.0
        self.drift = (self.drift + 0.011 * spd * dt) % 1000.0
        self.breath = (self.breath + 0.06 * spd * dt) % 1.0
        self.clock = (self.clock + spd * dt) % CLOCK_WRAP

        # the timeline; a new piece (or the first note after the example) turns the page
        tl = self._timeline(dt, gap)
        tkey = (id(tl), tl.pieces, tl.example)
        if tkey != self._tl_key:
            if self.frames > 0:
                self.ctx.cp.copyto(self.old, self.buf)
                self.fade = 1.0
                self._new_generation()
            self._tl_key = tkey
        elif dt > 0.0:
            self.fade = max(0.0, self.fade - dt / FADE_S)
        dtarget = 1.0 if tl.example else 0.0
        self.draft = dtarget if self.frames == 0 else self.draft + (dtarget - self.draft) * _ease(dt, 0.8)
        ftarget = (1.0 if (tl.started and not tl.frozen) else 0.0) * (1.0 - _smoothstep(1.0, 4.0, float(tl.quiet)))
        self.fresh += (ftarget - self.fresh) * _ease(dt, 0.7)

        # the key fields: TRAILING (the whole island) and CENTRED (Sapp's), as the morph needs them
        if morph < 0.999:
            self.fT.bake(tl, KF.TRAILING, None, logr, hs)
        if morph > 0.001:
            self.fC.bake(tl, KF.CENTRED, None, logr, hs)
        fd = self.fC if morph >= 0.5 else self.fT
        mode = KF.CENTRED if morph >= 0.5 else KF.TRAILING

        a1, a2 = 0.85 * warp, 0.3 * warp              # fold-free for every seed tried, even at warp 1
        lobes = self._lobes(warp)
        Z, ppm = self._camera(dt, e, zoom, fd, a1, a2, lobes)
        self._label_job(tl, dt, mode, logr, a1, a2, lobes)
        self._ease_labels(dt)

        # the compass: its north turns to the whole piece's key
        sk, sclar = tl.summit()
        if sk >= 0:
            d = (KF.key_angle(sk) - self.rang + math.pi) % (2 * math.pi) - math.pi
            self.rang += d * _ease(dt, 2.5)
            self.sum_rgb += (np.array(KF.key_rgb(sk, hs)) - self.sum_rgb) * _ease(dt, 1.0)
        self.suma += ((0.3 + 0.7 * sclar if sk >= 0 else 0.0) - self.suma) * _ease(dt, 1.0)
        sway = 0.02 * math.sin(2 * math.pi * 1.7 * self.breath) * (0.6 + 0.8 * e)

        A = self.A
        if self._keys_hs is None or abs(hs - self._keys_hs) > 0.05:
            self._keys_hs = hs
            for k in range(24):
                A[O_KEYS + 3 * k:O_KEYS + 3 * k + 3] = KF.key_rgb(k, hs)
            A[O_GREY:O_GREY + 3] = KF.key_rgb(0, hs, 0.0)
            A[O_GREY + 3:O_GREY + 6] = KF.key_rgb(12, hs, 0.0)
        R = AR
        A[R["W"]], A[R["H"]] = self.w, self.h
        A[R["CAMX"]], A[R["CAMY"]], A[R["PPM"]] = self.cam[0], self.cam[1], ppm
        A[R["A1"]], A[R["A2"]], A[R["RAG"]], A[R["CA"]] = a1, a2, 0.18 + 0.4 * warp, 0.15 + 0.35 * warp
        A[R["HUBX"]], A[R["HUBY"]], A[R["RX"]], A[R["RY"]] = HUB[0], HUB[1], RX, RY
        A[R["FJ"]], A[R["GAP"]], A[R["GAMMA"]], A[R["MORPH"]] = self.fj, GAP, GAMMA, morph
        A[R["FW"]], A[R["FH"]], A[R["WASH"]], A[R["HS"]] = FW, FH, wash, hs
        A[R["FRESH"]] = self.fresh
        A[R["TNOW"]], A[R["TORG"]], A[R["TSPAN"]] = self.fT.now, self.fT.org, self.fT.span
        A[R["CNOW"]], A[R["CORG"]], A[R["CSPAN"]] = self.fC.now, self.fC.org, self.fC.span
        A[R["LMIN"]], A[R["LOGR"]], A[R["DRAFT"]], A[R["S"]] = KF.L_MIN, logr, self.draft, TEX_S
        A[R["CLOCK"]], A[R["INK"]], A[R["EN"]], A[R["KICK"]] = self.clock, ink, e, self.kick
        A[R["GAIN"]], A[R["SHIM"]], A[R["SC"]] = gain, self.shim, self.sc
        A[R["LW"]], A[R["LH"]], A[R["GW"]], A[R["GH"]] = self.lw, self.lh, self.gw, self.gh
        rr = 0.072 * self.h
        fr0, fr1 = 0.022 * self.h, 0.035 * self.h
        A[R["RCX"]], A[R["RCY"]], A[R["RR"]], A[R["RANG"]] = fr1 + 1.45 * rr, self.h - fr1 - 1.45 * rr, rr, \
            self.rang + sway
        A[R["FADE"]], A[R["OX"]], A[R["OY"]] = self.fade, self.ox, self.oy
        A[R["SUMR"]:R["SUMB"] + 1] = self.sum_rgb
        A[R["SUMA"]] = self.suma
        hx, hy = self._hub_screen(a1, a2)
        A[R["HUBSX"]] = (hx - self.cam[0]) * ppm + 0.5 * self.w
        A[R["HUBSY"]] = (hy - self.cam[1]) * ppm + 0.5 * self.h
        A[R["HUBA"]] = 0.9 * _smoothstep(2.0, 10.0, float(fd.now)) * (1.0 - 0.4 * self.draft)
        A[R["BREATH"]] = 1.0 + 0.04 * math.sin(2 * math.pi * self.breath + 0.7)
        A[R["FR0"]], A[R["FR1"]] = fr0, fr1
        A[R["GLOSS"]] = 0.04 + 0.32 * self.kick + 0.06 * e
        A[O_LOBE:O_LOBE + 8] = lobes
        # the key names, by alpha (the newest generation's first)
        A[O_LAB:O_LAB + 8 * N_LAB] = 0.0
        i = 0
        zs = Z ** 0.6 / 1.3
        ink_rgb = np.array((0.15, 0.095, 0.06))
        for (gen, k), st in sorted(self.labs.items(), key=lambda kv: -kv[1]["a"]):
            if i >= N_LAB:
                break
            a = st["a"] * (1.0 - 0.25 * self.draft)
            if a < 0.004 or not self.gw:
                continue
            col = 0.7 * ink_rgb + 0.3 * 0.5 * np.array(KF.key_rgb(k, hs))
            A[O_LAB + 8 * i:O_LAB + 8 * i + 8] = ((st["x"] - self.cam[0]) * ppm + 0.5 * self.w,
                                                  (st["y"] - self.cam[1]) * ppm + 0.5 * self.h,
                                                  max(st["s"] * zs, 0.05), a, k, col[0], col[1], col[2])
            i += 1
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        self.arena.set(A)
        launch_2d(self.k_field, self.w, self.h, (self.P0, self.P1, self.P2, self.arena, self.tex,
                                                 self.fT.A, self.fT.B, self.fT.C, self.fC.A, self.fC.B, self.fC.C))
        launch_2d(self.k_down, self.lw, self.lh, (self.P0, self.D, np.int32(self.w), np.int32(self.h),
                                                  np.int32(self.lw), np.int32(self.lh)))
        launch_2d(self.k_blur, self.lw, self.lh, (self.D, self.E, np.int32(self.lw), np.int32(self.lh), np.int32(0)))
        launch_2d(self.k_blur, self.lw, self.lh, (self.E, self.LB, np.int32(self.lw), np.int32(self.lh), np.int32(1)))
        launch_2d(self.k_comp, self.w, self.h, (self.buf, self.P0, self.P1, self.P2, self.LB, self.arena, self.tex,
                                                self.atlas, self.old, np.uint32(self.seed)))
        present(self.ctx, self.buf, out)
        self.frames += 1

    def close(self) -> None:
        for f in (getattr(self, "fT", None), getattr(self, "fC", None)):
            if f is not None:
                f.close()
        self.buf = self.old = self.P0 = self.P1 = self.P2 = self.D = self.E = self.LB = None
        self.arena = self.tex = self.atlas = None
        self.fT = self.fC = None
        self._own_tl = None
