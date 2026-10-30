"""Key country: a slow flight along a valley through a landscape the music builds as it is played.

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, https://esf.ccarh.org/254-old/254_LiteraturePack1/Harmony/Sapp_VisualHierKeyAnal05.pdf, and
        I. Quilez's articles on heightfield marching and fog, https://iquilezles.org/articles/terrainmarching/ ,
        https://iquilezles.org/articles/fog/ : ideas only, nothing copied.  The key analysis is
        stream_service/plugins/_lib/keyfield.py, keyscape_live's maths.)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
  A low flight along a winding valley at dusk.  The valley is the piece being played, laid out as land: forward
  is time, so the land under the camera is a few seconds ago and the land at the far end is now.  While the piano
  plays, fresh land rises there, two strips a second, out of a calm lake of mist that mirrors the sky (the music not
  yet played), with a far skyline beyond it; the camera follows at a steady few seconds behind.  So the flight moves
  at the speed of the music, and you see the last few seconds of the piece ahead of you as country.

  Across the valley is the listening span.  The valley floor is the key of each moment (what the last second
  sounded like), so its colour changes chord by chord, like a river of colour.  Climbing the valley walls, each
  point listens to a longer stretch: halfway up a few bars, the ridgelines the last half minute or so.  The far
  ranges beyond the ridges, and the sun, wear the key of the whole piece.  So a passage in G inside a piece in C
  is a G-coloured stretch of valley floor under hills that stay C.

  The colours are keyscape's: the hue is the key's place on the circle of fifths (C, G and F are neighbours,
  C and F# opposite), major keys light, minor keys deeper, full colour where the key is clear, grey where it is
  ambiguous (clarity is lifted a little, so a fairly clear key already reads as colour, and a near tie's thin line of
  low clarity along every boundary takes its neighbours' clarity, so bands meet cleanly).  Where the music is far
  from the piece's home key (counted round the circle of fifths) the walls break into crags, the sharper the
  clearer that foreign key is; ambiguous stretches sink low and lie under drifting mist; silence is mist over water.
  The sky and the sun are tinted, lightly, by the piece's key.

  The light: a low sun on the left and a little ahead (side light, so the relief reads: sunlit slopes warm, the
  others in cool sky light, a slightly wrapped terminator), soft shadows, a two-tap occlusion, a faint grain near the camera; a soft sun disc with
  a halo, never clipped to white.  The air is clear for the first third of the way to now and thickens beyond
  (thinner high up), so near land keeps its colour and the far ranges fall away in layers of haze.

  When the playing stops, the flight slows to a hover over the land and the camera looks slowly around.  After a
  long rest (``piece_gap``) the next note begins a new piece: the flight carries on, and the new country rises
  beyond a misty saddle where the old one ended (the old land is held up to the junction and crossfades into the
  new).  Before any note has been played the country is keyscape's example (eight bars in C, a turn to G and back),
  and a scene that comes on screen during a young piece shows the example as the country before it, so it never
  starts over an empty plain.

How the analysis maps onto the land
  The shared KeyField is baked every frame in TRAILING mode (each cell is the window ending at its moment, so the
  field is a rectangle, not Sapp's triangle) over the bins from just behind the camera to now.  Field u is the
  forward axis (0.5 world units a bin, so the flight runs 1 unit a second), field v is the distance from the
  valley's winding centreline (v = |x - axis| / 8 units, mirrored, so both walls carry the same timescales).  A
  derive pass turns the planes into a terrain plane (ridge profile x the land weight, lowered by fog; crag
  amplitude = home distance x clarity x (1 - fog); detail amplitude; land weight = inside x the rise at now), read
  through a 5 x 5 binomial neighbourhood so a key change makes a slope, not a terrace, and a colour plane (the
  key's colour from plane A, texel-sharp; sampled with a cubic B-spline so band edges are curves, not the texels'
  staircase).  Height = ridge profile + far ranges beyond v = 1 + ridged crags + noise detail (and finer ridged
  spurs on the walls); the land weight fades to the lake beyond now and before the piece began.  The longest window
  the field holds is the view's span (a few tens of seconds), so the far ranges take the summit key (the whole
  piece) instead.

Macros: energy -> the flight's surges around the music's pace, turbulence (the camera's roll and bob, the mist's
churn), the sun's glare and the glow of the newest land; pulse (note_on) -> a glow kick on the newest land and a
rate kick to the mist and the flight; speed -> the idle motions (the hover's look-around, bob, sway, clouds, mist;
the flight itself keeps the music's pace); intensity -> gain.  No palette: the keys are the colours; ``hue_shift``
turns them (0..1 = 0..180 degrees).  Alive with no notes: the camera hovers, looks around, bobs and sways, clouds
and mist drift and the sun wanders.

Cost: per frame one 332-byte arena upload, the key-field bake (one launch over 384 x 192), the derive pass (same
size), a one-block camera guard (the land under the camera lifts it, eased on the GPU: no host round trip), the
height grid (one launch over 1024 x 1024: the land on a camera-aligned grid, rows log-spaced in distance, columns
widening with it), the raymarch at render_scale 0.5 (96 steps max over the grid, regula falsi onto the surface,
4-tap central-difference normals over a grid texel, a soft shadow marched toward the sun in at most 14 steps,
2-tap occlusion; the full terrain only at the hit, for colour) and the upsample.  fxtest --bench at 4K on the
P4000: 5.5 ms (defaults), 5.8 ms (the high preset).  (With the whole procedural terrain at every lookup, as before
the grid: 13 ms.)
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import keyfield as KF
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.keyscape_flight")

FW, FH = 384, 192               # the key field's planes
ZS = 0.5                        # world units per bin (the flight runs 1 unit per second of music)
WH = 8.0                        # the valley's half-width (world units): |x - axis| / WH -> field v
NTN = 256                       # the noise tile (texels)
TPU = 2.0                       # noise texels per world unit at the base scale
NP = NTN / TPU                  # the noise tile's period in world units (128)
MZF = 2.0 * math.pi * 6.0 / NP  # the mist drift's z frequency (whole cycles per noise period, so the wrap is seamless)
KMA = 2.0 * math.pi / 90.0      # the valley's bends: radians per bin (a 45 s wave and a 26.5 s one)
KMB = 2.0 * math.pi / 53.0
RISE_B = 6.0                    # bins over which fresh land rises at now (and the first land of a piece)
CRAG = 0.6                      # crag height at full home distance and clarity (x relief)
VFOV = 50.0                     # vertical field of view (degrees)
MAX_STEPS = 96
SHADOW_TAPS = 14
GUARD_N = 64                    # the camera guard's probes (one block)
GS, GA = 1024, 1024              # the height grid: rows (distance ahead, log-spaced), columns (across)
G_DMIN, G_LK = -6.0, 3.0         # the grid starts this far behind the eye; its log spacing
G_W0, G_WA = 6.0, 1.5            # its half-width at G_DMIN, and how fast it widens with distance
SUN_EL, SUN_AZ = 12.0, -46.0    # the sun's mean elevation and azimuth (degrees; the valley runs along +z)
KSUN = 2.0                      # the sun's strength on the land (against the sky's fill)
HAZE = 0.5                      # the haze's density: optical depth per front distance beyond the clear air
SUN0 = np.array([1.0, 0.80, 0.60])                   # colours (linear) before the piece's key tints them
ZEN0 = np.array([0.034, 0.068, 0.20])
HOR0 = np.array([0.27, 0.30, 0.42])

# the arena (floats), shared by the derive pass, the guard and the raymarch
_IDX = dict(W=0, H=1, FW=2, FH=3, EYE=4, FWD=7, RGT=10, UP=13, TANH=16, ASP=17, FIRST=18, DT=19,
            CAMB=20, ORG=21, ISPAN=22, NOW=23, LO=24, RH=25, MA=26, PHA=27, KA=28, PHB=29, KB=30, ZOFF=31,
            HMAX=32, TMAX=33, HZ0=34, MIST=35, MPH=36, GLOW=37, GAIN=38, GLARE=39, SUND=40, SUNC=43,
            ZEN=46, HOR=49, SALB=52, CLX=55, CLZ=56, CLEAR=57, MTOP=58, CRAG=59,
            RISE=60, LOWK=61, HOMES=62, SNAP=63, ORGS=64, SPANS=65, NOWS=66, SHIFT=67, SEED=68, SPAN=69,
            SLOPE=70, HSH=71, HZK=72, HZH=73, KSUN=74, RNGK=75, PXA=76, RNG0=77, GFX=78, GFZ=79, GDC=80, GIDC=81, EMIN=82)
N_P = 83
_DEFS = "\n".join(f"#define I_{k} {v}" for k, v in _IDX.items())

_SRC = HELPERS + KF.KEYFIELD_CUDA + r"""
// ---- keyscape_flight ----
""" + _DEFS + r"""
#define N_P """ + str(N_P) + r"""
#define ZS """ + repr(ZS) + r"""f
#define IZS """ + repr(1.0 / ZS) + r"""f
#define WH """ + repr(WH) + r"""f
#define NTN """ + str(NTN) + r"""
#define NTM """ + str(NTN - 1) + r"""
#define TPU """ + repr(TPU) + r"""f
#define MZF """ + repr(MZF) + r"""f
#define MAX_STEPS """ + str(MAX_STEPS) + r"""
#define SHADOW_TAPS """ + str(SHADOW_TAPS) + r"""
#define GUARD_N """ + str(GUARD_N) + r"""
#define GS """ + str(GS) + r"""
#define GA """ + str(GA) + r"""
#define G_DMIN """ + repr(G_DMIN) + r"""f
#define G_LK """ + repr(G_LK) + r"""f
#define G_W0 """ + repr(G_W0) + r"""f
#define G_WA """ + repr(G_WA) + r"""f

__device__ __forceinline__ float3 operator+(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 operator-(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 operator*(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 operator*(float s, float3 a) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 operator*(float3 a, float3 b) { return make_float3(a.x * b.x, a.y * b.y, a.z * b.z); }
__device__ __forceinline__ float dot3(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 nrm3(float3 a) { return a * rsqrtf(fmaxf(dot3(a, a), 1e-20f)); }
__device__ __forceinline__ float3 ld3(const float* p) { return make_float3(p[0], p[1], p[2]); }
__device__ __forceinline__ float3 lerp3(float3 a, float3 b, float t) { return a + (b - a) * t; }
__device__ __forceinline__ float s2l(float x) {
    x = clamp01(x);
    return x <= 0.04045f ? x * (1.f / 12.92f) : __powf((x + 0.055f) * (1.f / 1.055f), 2.4f);
}
// the periodic noise tile, plain bilinear; u, v in texels, wrapping.  (A Hermite blend here made every texel a flat
// step: the slopes showed the tile's grid.  The tile is smooth at texel scale, and the normals' central differences
// span a texel, so linear is both smoother and cheaper.)
__device__ __forceinline__ float ntex(const float* __restrict__ T, float u, float v) {
    const float fu = floorf(u), fv = floorf(v);
    const float tx = u - fu, ty = v - fv;
    const int iu = ((int)fu) & NTM, iv = ((int)fv) & NTM;
    const int iu1 = (iu + 1) & NTM, iv1 = (iv + 1) & NTM;
    const float a = T[iv * NTN + iu], b = T[iv * NTN + iu1], c = T[iv1 * NTN + iu], d = T[iv1 * NTN + iu1];
    return (1.f - ty) * (a + (b - a) * tx) + ty * (c + (d - c) * tx);
}

// a cubic B-spline sample of a plane (16 taps): the colour bands' edges come out as smooth curves instead of the
// texels' staircase when the land is close
__device__ float4 tex_bspline(const float4* __restrict__ P, int fw, int fh, float u, float v) {
    const float fx = u * (float)fw - 0.5f, fy = v * (float)fh - 0.5f;
    const float bx = floorf(fx), by = floorf(fy);
    const float tx = fx - bx, ty = fy - by;
    float wx[4], wy[4];
    wx[0] = (1.f - tx) * (1.f - tx) * (1.f - tx) * (1.f / 6.f);
    wx[1] = (3.f * tx * tx * tx - 6.f * tx * tx + 4.f) * (1.f / 6.f);
    wx[2] = (-3.f * tx * tx * tx + 3.f * tx * tx + 3.f * tx + 1.f) * (1.f / 6.f);
    wx[3] = tx * tx * tx * (1.f / 6.f);
    wy[0] = (1.f - ty) * (1.f - ty) * (1.f - ty) * (1.f / 6.f);
    wy[1] = (3.f * ty * ty * ty - 6.f * ty * ty + 4.f) * (1.f / 6.f);
    wy[2] = (-3.f * ty * ty * ty + 3.f * ty * ty + 3.f * ty + 1.f) * (1.f / 6.f);
    wy[3] = ty * ty * ty * (1.f / 6.f);
    const int x0 = (int)bx - 1, y0 = (int)by - 1;
    float4 acc = make_float4(0.f, 0.f, 0.f, 0.f);
    for (int j = 0; j < 4; ++j) {
        const int yy = min(max(y0 + j, 0), fh - 1);
        for (int i = 0; i < 4; ++i) {
            const int xx = min(max(x0 + i, 0), fw - 1);
            const float w = wx[i] * wy[j];
            const float4 c = P[yy * fw + xx];
            acc.x += w * c.x; acc.y += w * c.y; acc.z += w * c.z; acc.w += w * c.w;
        }
    }
    return acc;
}

// the valley's winding centreline (world x) at zr world units ahead of the camera
__device__ __forceinline__ float axis_x(const float* P, float zr) {
    return P[I_MA] * (__sinf(P[I_PHA] + zr * P[I_KA]) + 0.35f * __sinf(P[I_PHB] + zr * P[I_KB]));
}

struct Ter { float h, land, v, md, u, n; };

// the land at world (x, zr) (zr relative to the camera's place in the piece): height, land weight, field v,
// mist density (ambiguous land and the plain), field u
__device__ Ter terrain(const float* P, const float4* __restrict__ TER, const float* __restrict__ NT, float x, float zr) {
    Ter r;
    const int fw = (int)P[I_FW], fh = (int)P[I_FH];
    const float pb = P[I_CAMB] + zr * IZS;                       // bins into the piece
    const float u = (pb - P[I_ORG]) * P[I_ISPAN];
    const float v = fabsf(x - axis_x(P, zr)) * (1.f / WH);
    float4 t = make_float4(0.f, 0.f, 0.f, 0.f);                  // beyond now there is no land
    if (u <= 1.f) t = kf_tex(TER, fw, fh, u, fminf(v, 1.f));
    const float nx = x * TPU, nz = (zr + P[I_ZOFF]) * TPU;
    const float nh = 0.67f * ntex(NT, nx * 4.f + 37.f, nz * 4.f + 11.f) + 0.33f * ntex(NT, nx * 8.f + 101.f, nz * 8.f + 59.f);
    float h = t.x + 0.14f * t.z * (2.f * nh - 1.f);              // the ridge profile + detail
    if (t.w > 1e-3f) {                                           // finer relief on the walls (a few units down to 0.3)
        const float d2 = 1.f - fabsf(2.f * ntex(NT, nx * 24.f + 5.f, nz * 24.f + 300.f) - 1.f);   // ridged: spurs
        h += 0.06f * t.z * smooth(0.08f, 0.55f, v) * (d2 * d2 - 0.4f);
    }
    if (v > 1.f && t.w > 1e-3f)                                  // the far ranges beyond the ridges
        h += t.w * smooth(1.f, 1.9f, v) * (0.45f + 0.9f * ntex(NT, nx + 211.f, nz + 97.f));
    if (t.y > 1e-3f) {                                           // crags far from home
        const float cr = 1.f - fabsf(2.f * ntex(NT, nx * 4.f + 173.f, nz * 4.f + 7.f) - 1.f);
        h += P[I_CRAG] * t.y * cr * cr * cr;
    }
    const float bl = 1.f - t.w;
    if (bl > 1e-3f) {
        // the music not yet played: a lake of mist, then a far skyline that keeps its distance from the camera
        // (so it never looms, whatever the lag) and stays a little beyond now
        const float dz = (pb - P[I_NOW]) * ZS;
        const float env = smooth(2.f, 10.f, dz) * smooth(P[I_RNG0], P[I_RNG0] + 18.f, zr) * P[I_RNGK];
        if (env > 1e-3f) {
            const float rx = x * TPU, rz = zr * TPU;
            const float n1 = ntex(NT, rx * 0.55f + 401.f, rz * 0.55f + 77.f);
            const float n2 = ntex(NT, rx * 1.7f + 23.f, rz * 1.7f + 191.f);
            const float rg = 1.f - fabsf(2.f * n1 - 1.f);
            h += bl * env * (0.25f + 1.25f * rg * rg + 0.35f * n2);
        }
        h += bl * (0.012f * nh - 0.09f);                         // the lake, a little under the valley floor
    }
    r.h = kf_fin(P[I_RH] * h);
    r.land = t.w; r.v = v; r.u = u; r.n = nh;
    r.md = clamp01(1.f - t.w + (t.w - t.z) * (1.f / 0.65f));    // det = land (1 - 0.65 amb): land x amb back out
    return r;
}

// the land's colour: the key's hue on the circle of fifths (keyscape's rule), major light / minor deep, chroma by
// clarity (lifted, so a fairly clear key already reads as colour), OKLab
__device__ __forceinline__ float3 land_lab(float key, float clar, float hs) {
    const int k = kf_key_id(key);
    if (k < 0) return make_float3(0.5f, -0.004f, -0.02f);
    const float hue = (KF_H0 + kf_fin(hs) + 30.f * (float)((7 * (k % 12)) % 12)) * KF_DEG;
    const float c = __powf(fmaxf(clamp01(clar), 1e-4f), 0.65f);
    const float ch = 0.16f * c;
    const float L = 0.62f + ((k >= 12 ? 0.53f : 0.71f) - 0.62f) * (0.3f + 0.7f * c);   // ambiguous: a mid grey
    return make_float3(L, ch * __cosf(hue), ch * __sinf(hue));
}

// the key field's planes -> the terrain plane TER (ridge base, crag amplitude, detail amplitude, land) and the
// colour plane COL (sRGB, clarity); the previous piece's planes (TS, CS) fill in before this piece's start.
// The shape reads a 5 x 5 binomial neighbourhood (so a key change makes a slope, not a terrace); the colour reads
// the texel itself (so the bands stay sharp).
extern "C" __global__
void kc_derive(float4* __restrict__ TER, float4* __restrict__ COL, const float4* __restrict__ A,
               const float4* __restrict__ B, const float4* __restrict__ TS, const float4* __restrict__ CS,
               const float* __restrict__ Pg)
{
    const int fw = (int)Pg[I_FW], fh = (int)Pg[I_FH];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= fw || y >= fh) return;
    const int i = y * fw + x;
    const float u = ((float)x + 0.5f) / (float)fw, v = ((float)y + 0.5f) / (float)fh;
    const float span = fmaxf(Pg[I_SPAN], 1e-3f), now = Pg[I_NOW], lo = Pg[I_LO], R = fmaxf(Pg[I_RISE], 0.1f);
    const float p = Pg[I_ORG] + u * span;
    const float4 a = A[i];
    const float inside = clamp01(kf_fin(a.w)), clar = clamp01(kf_fin(a.y));
    const float fog = clamp01(a.z == a.z ? a.z : 1.f);
    const float bw[5] = {1.f, 4.f, 6.f, 4.f, 1.f};
    float ws = 0.f, fb = 0.f, cb = 0.f, ab = 0.f, kb = 0.f;
    for (int dy = -2; dy <= 2; ++dy) {
        const int yy = min(max(y + 2 * dy, 0), fh - 1);
        for (int dx = -2; dx <= 2; ++dx) {
            const int xx = min(max(x + 2 * dx, 0), fw - 1);
            const float w = bw[dx + 2] * bw[dy + 2];
            const float4 an = A[yy * fw + xx];
            const float hn = B[yy * fw + xx].w;
            const float fg = clamp01(an.z == an.z ? an.z : 1.f), cl = clamp01(kf_fin(an.y));
            const float hm = clamp01(hn == hn ? hn : 1.f);
            const float am = 1.f - cl;
            fb += w * fg;
            kb += w * cl;
            cb += w * hm * cl * (1.f - fg);
            ab += w * (fg + (1.f - fg) * 0.55f * am * am);       // ambiguity: silence, or no clear key
            ws += w;
        }
    }
    const float iw = 1.f / ws;
    fb *= iw; cb *= iw; ab = clamp01(ab * iw); kb *= iw;
    const float land = inside * smooth(0.f, R, now - p) * smooth(0.f, R, p - lo);
    const float pr = v * v * (3.f - 2.f * v);
    const float low = 1.f - Pg[I_LOWK] * fb;
    float4 t = make_float4(land * pr * low,
                           land * Pg[I_HOMES] * cb * smooth(0.06f, 0.45f, v),
                           land * (1.f - 0.65f * ab),
                           land);
    // a near tie between two keys is a thin line of low clarity along every boundary: let the neighbourhood's
    // clarity carry across it, so the bands meet cleanly instead of through a grey rim (wide ambiguity stays grey)
    const float clc = fmaxf(clar, 0.85f * kb);
    const float3 rgb = kf_oklab_srgb(kf_mist(land_lab(a.x, clc, Pg[I_HSH]), 0.8f * fog));
    float4 col = make_float4(rgb.x, rgb.y, rgb.z, clar);
    if (Pg[I_SNAP] > 0.5f && p <= lo + R) {                     // the previous piece, before this one has risen
        // its land is held at its last full column up to the junction, so the two pieces crossfade instead of
        // leaving a trench between them
        const float po = fminf(p + Pg[I_SHIFT], Pg[I_NOWS] - R);
        const float us = (po - Pg[I_ORGS]) / fmaxf(Pg[I_SPANS], 1e-3f);
        if (us >= 0.f && us <= 1.f) {
            const float4 ts = kf_tex(TS, fw, fh, us, v);
            const float4 cs = kf_tex(CS, fw, fh, us, v);
            const float wo = clamp01(kf_fin(ts.w)) * (1.f - land) * smooth(0.f, R, now - p);
            t.x += kf_fin(ts.x) * (1.f - land); t.y += kf_fin(ts.y) * (1.f - land);
            t.z += kf_fin(ts.z) * (1.f - land); t.w = land + wo;
            const float sw = land + wo;
            if (sw > 1e-4f) {
                const float k = wo / sw;
                col.x += (kf_fin(cs.x) - col.x) * k; col.y += (kf_fin(cs.y) - col.y) * k;
                col.z += (kf_fin(cs.z) - col.z) * k; col.w += (kf_fin(cs.w) - col.w) * k;
            }
        }
    }
    {                                                            // a misty saddle where a piece begins
        const float j = smooth(2.f * R, 0.f, fabsf(p - lo));
        const float k = 1.f - 0.3f * j;
        t.x *= 1.f - 0.5f * j; t.y *= 1.f - 0.5f * j; t.z *= k; t.w *= k;
    }
    TER[i] = t;
    COL[i] = col;
}

// the camera guard: one block probes the land around and ahead of the eye; ST[0] (the lift) eases up fast and
// down slowly, so the camera never enters a hill and never jumps
extern "C" __global__
void kc_guard(float* __restrict__ ST, const float* __restrict__ Pg, const float4* __restrict__ TER,
              const float* __restrict__ NT)
{
    __shared__ float P[N_P];
    __shared__ float red[GUARD_N];
    const int i = threadIdx.x;
    for (int k = i; k < N_P; k += blockDim.x) P[k] = Pg[k];
    __syncthreads();
    if (i < GUARD_N) {
        const float zr = P[I_EYE + 2] - 1.5f + 0.4f * (float)(i & 15);
        const float x = P[I_EYE] + 0.6f * ((float)(i >> 4) - 1.5f);
        red[i] = terrain(P, TER, NT, x, zr).h;
    }
    __syncthreads();
    if (i == 0) {
        float hm = -1e9f;
        for (int k = 0; k < GUARD_N; ++k) hm = fmaxf(hm, red[k]);
        const float need = fmaxf(hm + P[I_CLEAR] - P[I_EYE + 1], 0.f);
        float cur = kf_fin(ST[0]);
        const float dt = P[I_DT];
        if (P[I_FIRST] > 0.5f) cur = need;
        else if (dt > 0.f) cur += (need - cur) * (1.f - __expf(-dt / (need > cur ? 0.35f : 2.5f)));
        ST[0] = fminf(fmaxf(kf_fin(cur), 0.f), 100.f);
    }
}

// ---- the height grid: the land baked once per frame on a camera-aligned grid (rows: distance ahead, log-spaced from
// G_DMIN to the march's reach; columns: across, widening with distance), so the march, normals, shadows and occlusion
// read four texels instead of the whole procedural terrain.  (h, mist density) per texel.
__device__ __forceinline__ float grid_d(const float* P, int i) {
    return G_DMIN + P[I_GDC] * (__expf(G_LK * (float)i * (1.f / (float)(GS - 1))) - 1.f);
}
extern "C" __global__
void kc_grid(float2* __restrict__ HG, const float* __restrict__ Pg, const float4* __restrict__ TER,
             const float* __restrict__ NT)
{
    __shared__ float P[N_P];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int k = tid; k < N_P; k += blockDim.x * blockDim.y) P[k] = Pg[k];
    __syncthreads();
    const int j = blockIdx.x * blockDim.x + threadIdx.x, i = blockIdx.y * blockDim.y + threadIdx.y;
    if (j >= GA || i >= GS) return;
    const float d = grid_d(P, i);
    const float l = ((float)j * (2.f / (float)(GA - 1)) - 1.f) * (G_W0 + G_WA * (d - G_DMIN));
    const float fx = P[I_GFX], fz = P[I_GFZ];
    const Ter t = terrain(P, TER, NT, P[I_EYE] + d * fx + l * fz, P[I_EYE + 2] + d * fz - l * fx);
    HG[i * GA + j] = make_float2(t.h, t.md);
}
// (h, mist density) at world (x, zr): the grid, bilinear; outside it the full terrain
__device__ __forceinline__ float2 hgt(const float* P, const float2* __restrict__ HG, const float4* __restrict__ TER,
                                      const float* __restrict__ NT, float x, float z) {
    const float dx = x - P[I_EYE], dz = z - P[I_EYE + 2];
    const float fx = P[I_GFX], fz = P[I_GFZ];
    const float d = dx * fx + dz * fz, l = dx * fz - dz * fx;
    const float q = (d - G_DMIN) * P[I_GIDC];
    const float sg = __logf(1.f + fmaxf(q, 0.f)) * (1.f / G_LK);
    const float a = l / (G_W0 + G_WA * fmaxf(d - G_DMIN, 0.f));
    if (q < 0.f || sg > 1.f || fabsf(a) > 1.f) {
        const Ter t = terrain(P, TER, NT, x, z);
        return make_float2(t.h, t.md);
    }
    const float fi = sg * (float)(GS - 1), fj = (a + 1.f) * 0.5f * (float)(GA - 1);
    const int i0 = min((int)fi, GS - 2), j0 = min((int)fj, GA - 2);
    const float ti = fi - (float)i0, tj = fj - (float)j0;
    const float2 A = HG[i0 * GA + j0], B = HG[i0 * GA + j0 + 1];
    const float2 C = HG[(i0 + 1) * GA + j0], D = HG[(i0 + 1) * GA + j0 + 1];
    const float ui = 1.f - ti, uj = 1.f - tj;
    return make_float2(ui * (uj * A.x + tj * B.x) + ti * (uj * C.x + tj * D.x),
                       ui * (uj * A.y + tj * B.y) + ti * (uj * C.y + tj * D.y));
}
// the grid's texel spacing (world units) at distance d ahead: the normals' differences span at least that
__device__ __forceinline__ float grid_step(const float* P, float d) {
    const float fw = (P[I_GDC] + fmaxf(d - G_DMIN, 0.f)) * G_LK * (1.f / (float)(GS - 1));
    const float lt = 2.f * (G_W0 + G_WA * fmaxf(d - G_DMIN, 0.f)) * (1.f / (float)(GA - 1));
    return fmaxf(fw, lt);
}

// the sky: horizon to zenith, brighter and warmer toward the sun
__device__ __forceinline__ float3 sky_base(const float* P, float3 rd, float mu) {
    const float e = fmaxf(rd.y, 0.f);
    const float3 c = lerp3(ld3(P + I_HOR), ld3(P + I_ZEN), __powf(e, 0.38f));
    return c + ld3(P + I_SUNC) * (P[I_GLARE] * (0.14f * __powf(mu, 4.f) + 0.30f * __powf(mu, 36.f)));
}

extern "C" __global__ void __launch_bounds__(256)
kc_render(uchar4* __restrict__ out, const float* __restrict__ Pg, const float4* __restrict__ TER,
          const float4* __restrict__ COL, const float* __restrict__ NT, const float* __restrict__ ST,
          const float2* __restrict__ HG)
{
    __shared__ float P[N_P];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int k = tid; k < N_P; k += blockDim.x * blockDim.y) P[k] = Pg[k];
    __syncthreads();
    const int W = (int)P[I_W], H = (int)P[I_H];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int fw = (int)P[I_FW], fh = (int)P[I_FH];
    const float3 ro = make_float3(P[I_EYE], P[I_EYE + 1] + kf_fin(ST[0]), P[I_EYE + 2]);
    const float sx = (((float)x + 0.5f) / (float)W * 2.f - 1.f) * P[I_TANH] * P[I_ASP];
    const float sy = (1.f - ((float)y + 0.5f) / (float)H * 2.f) * P[I_TANH];
    const float3 rd = nrm3(ld3(P + I_FWD) + ld3(P + I_RGT) * sx + ld3(P + I_UP) * sy);
    const float3 sd = ld3(P + I_SUND), sunc = ld3(P + I_SUNC), zen = ld3(P + I_ZEN), hor = ld3(P + I_HOR);
    const float glare = P[I_GLARE];
    const float mu = fmaxf(dot3(rd, sd), 0.f);
    // the haze is the sky at the horizon in this direction (so far land melts into it), warmer toward the sun
    const float3 hazec = hor + sunc * (glare * (0.14f * __powf(mu, 4.f) + 0.30f * __powf(mu, 36.f)));
    // the mist is lit: a little brighter than the haze, warmer on the sun's side
    const float3 mistc = hor * 0.85f + sunc * (0.06f + 0.2f * mu * mu) + make_float3(0.01f, 0.012f, 0.02f);

    // march the heightfield (bounded steps; regula falsi back onto the surface when a step crosses it)
    const float Hmax = P[I_HMAX], tmax = P[I_TMAX];
    float t = 0.02f;
    bool skyray = false, hit = false;
    if (ro.y > Hmax) {
        if (rd.y > -1e-4f) skyray = true;
        else t = (ro.y - Hmax) / (-rd.y);
    }
    if (t > tmax) skyray = rd.y >= 0.f;
    float od = 0.f;                                              // the mist's optical depth along the ray
    if (!skyray) {
        const float hz = sqrtf(fmaxf(1.f - rd.y * rd.y, 0.f));
        const float invL = 1.f / (fmaxf(-rd.y, 0.f) + P[I_SLOPE] * hz + 0.05f);   // the land's steepest slope
        const float mtop = P[I_MTOP], mk = P[I_MIST];
        float tp = t, gp = -1.f;
        bool done = false;
        for (int i = 0; i < MAX_STEPS; ++i) {
            const float3 q = ro + rd * t;
            const float2 tr = hgt(P, HG, TER, NT, q.x, q.z);
            const float g = q.y - tr.x;
            if (g < 0.f) {
                if (gp > 0.f) {                                  // refine between the last two samples
                    float ta = tp, ga = gp, tb = t, gb = g;
                    for (int k = 0; k < 3; ++k) {
                        const float tm = ta + (tb - ta) * ga / fmaxf(ga - gb, 1e-6f);
                        const float3 qm = ro + rd * tm;
                        const float gm = qm.y - hgt(P, HG, TER, NT, qm.x, qm.z).x;
                        if (gm > 0.f) { ta = tm; ga = gm; } else { tb = tm; gb = gm; }
                    }
                    t = ta + (tb - ta) * ga / fmaxf(ga - gb, 1e-6f);
                }
                hit = true; done = true; break;
            }
            if (g < 0.0012f * t + 0.002f) { hit = true; done = true; break; }
            const float st = fmaxf(g * invL, 0.012f + 0.0065f * t);
            if (mk > 0.f && q.y < mtop) {                        // drifting mist over ambiguous land and the plain
                const float band = smooth(mtop, 0.15f * mtop - 0.1f, q.y);
                const float drift = 0.5f + 0.5f * __sinf(q.x * 0.37f + P[I_MPH])
                                          * __sinf((q.z + P[I_ZOFF]) * MZF - 0.6f * P[I_MPH] + 1.1f);
                od += mk * tr.y * band * drift * fminf(st, 1.5f);
            }
            tp = t; gp = g;
            t += st;
            if (t > tmax || (rd.y > 0.f && q.y > Hmax)) { done = true; break; }
        }
        // out of steps skimming a crest, or out of reach going down: the land (far, in the haze)
        if (!hit && (rd.y < 0.f || (!done && gp < 0.02f * t + 0.05f))) { hit = true; t = fminf(t, tmax); }
    }

    float3 col;
    if (hit) {
        const float3 hp = ro + rd * t;
        const Ter t0 = terrain(P, TER, NT, hp.x, hp.z);
        // central differences over a grid texel (C0 normals: no facets)
        const float dh = (hp.x - P[I_EYE]) * P[I_GFX] + (hp.z - P[I_EYE + 2]) * P[I_GFZ];
        const float e = fmaxf(P[I_EMIN], 1.2f * grid_step(P, dh));   // and over the key field's texels
        const float hxp = hgt(P, HG, TER, NT, hp.x + e, hp.z).x, hxm = hgt(P, HG, TER, NT, hp.x - e, hp.z).x;
        const float hzp = hgt(P, HG, TER, NT, hp.x, hp.z + e).x, hzm = hgt(P, HG, TER, NT, hp.x, hp.z - e).x;
        const float3 n = nrm3(make_float3(hxm - hxp, 2.f * e, hzm - hzp));
        float4 c4 = make_float4(0.42f, 0.44f, 0.48f, 0.f);
        if (t0.u <= 1.f) c4 = tex_bspline(COL, fw, fh, t0.u, fminf(t0.v, 1.f));
        float3 alb = make_float3(s2l(c4.x), s2l(c4.y), s2l(c4.z));
        const float3 salb = ld3(P + I_SALB);
        alb = lerp3(alb, salb, smooth(1.f, 1.6f, t0.v));                       // the far ranges wear the piece's key
        alb = lerp3(salb * 0.6f + make_float3(0.012f, 0.016f, 0.026f), alb, clamp01(t0.land));   // the skyline
        alb = alb * (0.86f + 0.28f * t0.n);                       // patches: the detail noise, faintly
        {                                                        // a fine grain near the camera (faded by footprint)
            const float fp = t * P[I_PXA];
            const float gf = 1.f - smooth(0.012f, 0.04f, fp);
            if (gf > 0.f) {
                const float gx = hp.x * TPU * 40.f, gz = (hp.z + P[I_ZOFF]) * TPU * 40.f;
                const float g1 = ntex(NT, gx + 13.f, gz + 177.f), g2 = ntex(NT, gx * 2.3f + 91.f, gz * 2.3f + 41.f);
                alb = alb * (1.f + gf * (0.18f * (g1 - 0.5f) + 0.12f * (g2 - 0.5f)));
            }
        }
        const float slope = 1.f - n.y;
        alb = lerp3(alb, alb * 0.6f + make_float3(0.012f, 0.012f, 0.014f), 0.5f * smooth(0.35f, 0.8f, slope));
        // aerial haze: none near the camera, thicker low down
        const float hzd = fmaxf(t - P[I_HZ0], 0.f) * P[I_HZK] * (0.45f + 0.55f * __expf(-fmaxf(hp.y, 0.f) * P[I_HZH]));
        const float fa = 1.f - __expf(-hzd);
        float sh = 1.f, ao = 1.f;
        if (fa < 0.96f) {
            if (sd.y > 0.f) {                                    // soft shadow: a short march toward the sun
                float ts = 0.06f;                                // (stepping by the clearance, so no ridge is
                const float3 o = hp + n * 0.04f;                 // stepped over: no light leaks)
                const float hzs = sqrtf(fmaxf(1.f - sd.y * sd.y, 0.f));
                const float invLs = 1.f / (sd.y + P[I_SLOPE] * hzs + 0.05f);
                for (int j = 0; j < SHADOW_TAPS; ++j) {
                    const float3 sp = o + sd * ts;
                    if (sp.y > Hmax) break;
                    const float g = sp.y - hgt(P, HG, TER, NT, sp.x, sp.z).x;
                    sh = fminf(sh, 4.5f * g / ts);
                    if (sh <= 0.f) break;
                    ts += fminf(fmaxf(g * invLs, 0.04f + 0.05f * ts), 3.f);
                }
                sh = clamp01(sh);
                sh = sh * sh * (3.f - 2.f * sh);
            }
            float occ = 0.f;                                     // two-tap occlusion along the normal
            const float3 q1 = hp + n * 0.3f, q2 = hp + n * 1.0f;
            occ += fmaxf(0.3f - (q1.y - hgt(P, HG, TER, NT, q1.x, q1.z).x), 0.f) * (1.f / 0.3f);
            occ += fmaxf(1.0f - (q2.y - hgt(P, HG, TER, NT, q2.x, q2.z).x), 0.f) * (0.6f / 1.0f);
            ao = clamp01(1.f - 0.45f * occ);
        }
        const float nl = dot3(n, sd);                            // a little wrap: a soft terminator
        const float dif = clamp01((nl + 0.18f) * (1.f / 1.18f)) * clamp01((nl + 0.18f) * (1.f / 0.36f)) * sh;
        const float3 skyl = (zen * 1.1f + hor * 0.45f + make_float3(0.035f, 0.03f, 0.025f)) * ((0.55f + 0.45f * n.y) * ao);
        const float3 bounce = (hor * 0.10f + sunc * 0.05f) * ((1.f - n.y) * ao);
        col = alb * (sunc * (P[I_KSUN] * dif) + skyl + bounce);
        const float vn = clamp01(-dot3(n, rd));                  // a sheen on slopes against the sun
        col = col + sunc * (glare * 0.06f * __powf(mu, 3.f) * (1.f - vn) * (1.f - vn) * sh * clamp01(t0.land));
        const float pb = P[I_CAMB] + hp.z * IZS;                 // the newest land glows as it rises
        const float fg = P[I_GLOW] * smooth(P[I_NOW] - 6.f, P[I_NOW], pb) * clamp01(t0.land);
        col = col + alb * (1.1f * fg);
        // the lake (where no land has risen yet): calm water mirroring the sky, rippled faintly near the camera
        const float plh = -0.09f * P[I_RH];
        const float ww = smooth(0.55f, 0.95f, 1.f - t0.land) * smooth(plh + 0.05f * P[I_RH], plh + 0.012f * P[I_RH], t0.h);
        if (ww > 0.01f) {
            const float fp = t * P[I_PXA];
            const float rk = 0.06f * (1.f - smooth(0.01f, 0.05f, fp));
            float3 wn = make_float3(0.f, 1.f, 0.f);
            if (rk > 0.f) {
                const float wx = hp.x * TPU * 12.f + 0.7f * P[I_MPH], wz = (hp.z + P[I_ZOFF]) * TPU * 12.f;
                const float w0 = ntex(NT, wx, wz), w1 = ntex(NT, wx + 0.5f, wz), w2 = ntex(NT, wx, wz + 0.5f);
                wn = nrm3(make_float3((w0 - w1) * 2.f * rk * 24.f, 1.f, (w0 - w2) * 2.f * rk * 24.f));
            }
            const float3 rr = rd - wn * (2.f * dot3(rd, wn));
            const float cosi = clamp01(-dot3(rd, wn));
            const float f1 = 1.f - cosi, f2 = f1 * f1;
            const float F = 0.05f + 0.95f * f2 * f2 * f1;
            const float mur = fmaxf(dot3(rr, sd), 0.f);
            const float3 refl = sky_base(P, make_float3(rr.x, fabsf(rr.y), rr.z), mur)
                              + sunc * (0.8f * __powf(mur, 600.f) + 0.18f * __powf(mur, 48.f));
            const float3 deep = (salb * 0.28f + make_float3(0.015f, 0.03f, 0.055f))
                              * (sunc * (P[I_KSUN] * 0.4f * fmaxf(sd.y, 0.f)) + zen + hor * 0.5f);
            col = lerp3(col, lerp3(deep, refl, F), ww);
        }
        col = lerp3(col, mistc, 1.f - __expf(-od));
        col = lerp3(col, hazec, fa);
    } else {
        float3 c = sky_base(P, rd, mu);
        if (rd.y > 0.01f) {                                      // a high cloud layer, lit from the sun's side
            const float k = 1.f / (rd.y + 0.04f);
            const float cu = rd.x * k * 11.f + P[I_CLX], cv = rd.z * k * 11.f + P[I_CLZ];
            const float nn = 0.62f * ntex(NT, cu, cv) + 0.38f * ntex(NT, cu * 2.3f + 71.f, cv * 2.3f + 13.f);
            const float cov = smooth(0.57f, 0.82f, nn) * smooth(0.01f, 0.12f, rd.y) * 0.6f;
            const float3 cc = hor * 0.72f + zen * 0.35f + sunc * (0.08f + 0.55f * __powf(mu, 6.f));
            c = lerp3(c, cc, cov);
        }
        // a soft sun: a warm disc with a soft limb and a halo, kept under white
        const float dm = 1.f - mu;
        c = c + sunc * (0.42f * smooth(3.2e-4f, 1.1e-4f, dm) + 0.22f * __expf(-dm * 320.f) + 0.08f * __expf(-dm * 30.f));
        col = lerp3(c, mistc, 1.f - __expf(-od));
    }
    // gain, a soft shoulder (linear below 0.75), sRGB, a static dither
    const float gn = P[I_GAIN];
    float3 v3 = make_float3(fmaxf(kf_fin(col.x) * gn, 0.f), fmaxf(kf_fin(col.y) * gn, 0.f), fmaxf(kf_fin(col.z) * gn, 0.f));
    v3.x = v3.x < 0.75f ? v3.x : 0.75f + 0.25f * (1.f - __expf(-(v3.x - 0.75f) * 4.f));
    v3.y = v3.y < 0.75f ? v3.y : 0.75f + 0.25f * (1.f - __expf(-(v3.y - 0.75f) * 4.f));
    v3.z = v3.z < 0.75f ? v3.z : 0.75f + 0.25f * (1.f - __expf(-(v3.z - 0.75f) * 4.f));
    const float dth = hash1((unsigned int)x, (unsigned int)y, (unsigned int)P[I_SEED]) - 0.5f;
    out[y * W + x] = pack(make_float4(255.f * kf_enc(v3.x) + dth, 255.f * kf_enc(v3.y) + dth, 255.f * kf_enc(v3.z) + dth, 255.f));
}
"""
_OPTS = ("--std=c++14", "--use_fast_math")

_logged: set = set()


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def _noise_tile(seed: int, n: int = NTN) -> np.ndarray:
    """A tiling smooth noise in 0..1: random low-frequency cosines with integer wave vectors (seamless on the tile)."""
    rng = np.random.default_rng(seed + 77)
    y, x = np.mgrid[0:n, 0:n] / n
    f = np.zeros((n, n))
    for octave, amp in ((1, 1.0), (2, 0.62), (3, 0.45), (5, 0.32), (8, 0.2), (13, 0.12), (21, 0.07)):
        for _ in range(3):
            kx, ky = rng.integers(-octave, octave + 1, 2)
            if kx == 0 and ky == 0:
                kx = octave
            f += amp * np.cos(2 * np.pi * (kx * x + ky * y) + rng.uniform(0, 2 * np.pi))
    f -= f.min()
    f /= max(f.max(), 1e-9)
    return np.ascontiguousarray(f.astype(np.float32))


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _fin(x, default: float = 0.0) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _lin(rgb) -> np.ndarray:
    """sRGB 0..1 -> linear."""
    c = np.clip(np.asarray(rgb, np.float64), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


class KeyscapeFlight(Source):
    """Per frame: tl.update (shared), the key-field bake, the derive pass, the camera guard (one block), the
    raymarch, the upsample; one arena upload.  Everything is allocated in setup."""

    name = "keyscape_flight"
    label = "Key country (flight)"
    description = ("A flight along a valley the music builds as it plays: the floor is the key of each moment, the "
                   "walls the key of longer and longer spans, the far ranges and the sun the key of the whole piece.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette=None, render_scale=0.5),
            "altitude": num(0.0, 1.0, 0.35, "Altitude", basic=True),
            "ridge_height": num(0.2, 2.0, 1.0, "Relief", basic=True),
            "lag": num(4.0, 40.0, 14.0, "Behind the music", unit="s"),
            "meander": num(0.0, 1.0, 0.5, "Valley bends"),
            "mist": num(0.0, 1.0, 0.5, "Mist"),
            "log_rows": num(0.0, 1.0, 1.0, "Log timescales"),
            "hue_shift": num(0.0, 1.0, 0.0, "Hue shift"),
            "piece_gap": num(30.0, 300.0, 90.0, "New country after", unit="s", tier="structural"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        mod = raw_module(_SRC, _OPTS)
        self.k_derive = mod.get_function("kc_derive")
        self.k_guard = mod.get_function("kc_guard")
        self.k_render = mod.get_function("kc_render")
        self.k_grid = mod.get_function("kc_grid")
        self.kf = KF.KeyField(ctx, FW, FH)
        fw, fh = self.kf.fw, self.kf.fh
        self.TER = cp.zeros((fh, fw, 4), cp.float32)
        self.COL = cp.zeros((fh, fw, 4), cp.float32)
        self.TS = cp.zeros((fh, fw, 4), cp.float32)          # the previous piece's planes (a new piece's first seconds)
        self.CS = cp.zeros((fh, fw, 4), cp.float32)
        self.NT = cp.asarray(_noise_tile(self.seed))
        self.ST = cp.zeros(4, cp.float32)                     # the camera guard's state (the lift)
        self.HG = cp.zeros((GS, GA, 2), cp.float32)          # the height grid (8 MB)
        self.Pd = cp.zeros(N_P, cp.float32)
        self.Ph = np.zeros(N_P, np.float32)
        self.Pe = cp.zeros(N_P, cp.float32)                   # the backdrop's derive arena (a first frame's only)
        self.Peh = np.zeros(N_P, np.float32)
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self._own_tl = KF.KeyTimeline(self.seed)             # used while there is no sound object
        self._ex_tl = KF.KeyTimeline(self.seed)              # never advanced: the example, the backdrop of a young piece
        self._backdrop = False
        self._tl = None
        self._pieces = 0
        self._last_now = 0.0
        self._first = True
        # the flight (bins in the current piece's coordinates; abs_off carries the land across pieces and re-bases)
        self.cam_b = 0.0
        self.v_cam = 0.0
        self.abs_off = 0.0
        self.r_front = 0.0
        self.hw = 1.0                                         # the hover weight
        self.roll = 0.0
        self.view_org, self.view_span = 0.0, KF.V_MIN
        # the previous piece (planes TS, CS): on, its view, its now, and the shift p_old = p + shift
        self.snap_on = False
        self.snap = (0.0, KF.V_MIN, 0.0, 0.0)
        # idle phases (host float64, never wrapped unless uploaded)
        self.ph = {"surge": 0.0, "pan": 0.0, "bob": 0.0, "sway": 0.0, "turb": 0.0, "sun": 0.0, "mist": 0.0,
                   "clx": 0.0, "clz": 0.0}
        # colours (linear), eased toward the piece's key
        self.sun, self.zen, self.hor = SUN0.copy(), ZEN0.copy(), HOR0.copy()
        self.salb = np.array([0.3, 0.3, 0.32])

    def _timeline(self):
        snd = self.sound
        if snd is None:
            return self._own_tl
        try:
            return KF.KeyTimeline.shared(snd, self.seed)
        except Exception as exc:  # noqa: BLE001  (degrade: a private timeline)
            _log_once("shared", "keyscape_flight: no shared key timeline (%s); using its own", exc)
            return self._own_tl

    # -- the piece's bookkeeping and the flight ------------------------------------------------------------------
    def _track(self, tl, now: float, dt: float, lag_b: float) -> None:
        if tl is not self._tl:                                # first frame, or another timeline: start afresh
            self._tl = tl
            self._pieces = tl.pieces
            self.cam_b = now - lag_b
            self.v_cam = 2.0 if (tl.started and not tl.frozen) else 0.0
            self.r_front = self.v_cam
            self.hw = 0.0 if self.v_cam > 0 else 1.0
            self.snap_on = False
            self._last_now = now
            # a piece younger than the view: the land before it is the example (as if the scene had been on screen
            # before the first note), so the flight never starts over an empty plain
            self._backdrop = bool(tl.started) and now < 2.0 * lag_b + 60.0
            return
        if tl.pieces != self._pieces:                         # a new piece (or the first note after the example)
            self._pieces = tl.pieces
            self.ctx.cp.copyto(self.TS, self.TER)
            self.ctx.cp.copyto(self.CS, self.COL)
            last = self._last_now
            self.snap = (self.view_org, self.view_span, last, last)
            self.snap_on = True
            self.cam_b -= last                                # the new piece starts where the old one's now was
            self.abs_off += last
            self._last_now = 0.0
        elif now < self._last_now - 0.5 * KF.DROP:            # the timeline dropped its oldest rows: re-base
            self.cam_b -= KF.DROP
            self.abs_off += KF.DROP
            self._last_now -= KF.DROP
            if self.snap_on:
                o, s, nw, sh = self.snap
                self.snap = (o, s, nw, sh + KF.DROP)
        if dt > 0.0:
            r = min(max(now - self._last_now, 0.0) / dt, 8.0)
            self.r_front += (r - self.r_front) * (1.0 - math.exp(-dt / 1.2))
        self._last_now = now

    def _fly(self, tl, now: float, dt: float, p: dict, lag_b: float) -> None:
        en = min(max(_fin(p["energy"], 0.5), 0.0), 1.0)
        pu = min(max(_fin(p["pulse"]), 0.0), 1.0)
        sp = max(_fin(p["speed"], 1.0), 0.0)
        hov = 1.0 if (tl.frozen or not tl.started or self.r_front < 0.6) else 0.0
        a = 1.0 - math.exp(-dt / 2.5) if dt > 0 else 0.0
        self.hw += (hov - self.hw) * a
        min_gap = max(6.0, 0.45 * lag_b)
        gap = now - self.cam_b
        vt = self.r_front * (1.0 + 0.2 * en * math.sin(self.ph["surge"])) + (gap - lag_b) * 0.12 + 0.6 * pu
        vt = max(vt, 0.3 * _smooth(min_gap, lag_b, gap))     # always creep on, easing to a stop near the front
        if gap < min_gap:
            vt = min(vt, max(gap - 0.7 * min_gap, 0.0) * 0.3)
        vt = min(max(vt, 0.0), 9.0)
        if dt > 0:
            self.v_cam += (vt - self.v_cam) * (1.0 - math.exp(-dt / 1.0))
            self.cam_b += self.v_cam * dt
        tau = 2.0 * math.pi
        self.ph["surge"] += dt * sp * tau / 17.0
        self.ph["pan"] += dt * sp * tau / 40.0
        self.ph["bob"] += dt * sp * tau / 9.0
        self.ph["sway"] += dt * sp * tau / 23.0
        self.ph["turb"] += dt * sp * (0.6 + 0.8 * en)
        self.ph["sun"] += dt * sp * tau / 300.0
        self.ph["mist"] += dt * (sp * (0.15 + 0.25 * en) + 1.0 * pu)
        self.ph["clx"] += dt * sp * 0.6
        self.ph["clz"] += dt * (sp * 0.3 + 0.8 * self.v_cam * ZS)

    def _colours(self, dt: float, hs_deg: float) -> None:
        sk, sc = int(self.kf.summit_key), _fin(self.kf.summit_clarity)
        if 0 <= sk < 24:
            klin = _lin(KF.key_rgb(sk, hs_deg, KF.C_KEY))
            lum = float(0.2126 * klin[0] + 0.7152 * klin[1] + 0.0722 * klin[2])
            kn = klin / max(lum, 1e-3)
            kw = 0.22 * min(1.0, 0.3 + sc)
            salb = _lin(KF.key_rgb(sk, hs_deg, KF.C_KEY * max(sc, 0.35)))
        else:
            kn, kw = np.ones(3), 0.0
            salb = _lin(KF.key_rgb(-1))
        kn = np.clip(kn, 0.0, 3.0)
        sun = (1.0 - kw) * SUN0 + kw * kn * float(SUN0.mean())
        zen = (1.0 - 0.3 * kw) * ZEN0 + 0.3 * kw * kn * 0.075
        hor = (1.0 - 0.4 * kw) * HOR0 + 0.4 * kw * kn * 0.31
        a = 1.0 if self._first else (1.0 - math.exp(-dt / 4.0) if dt > 0 else 0.0)
        self.sun += (np.nan_to_num(sun) - self.sun) * a
        self.zen += (np.nan_to_num(zen) - self.zen) * a
        self.hor += (np.nan_to_num(hor) - self.hor) * a
        self.salb += (np.nan_to_num(salb) - self.salb) * a

    def _fill_derive(self, P: np.ndarray, kf, mist: float, hs_deg: float) -> None:
        """The derive pass's part of an arena, for kf's current view."""
        I = _IDX
        P[I["FW"]], P[I["FH"]] = kf.fw, kf.fh
        P[I["ORG"]] = 0.0
        P[I["ISPAN"]] = 1.0 / max(kf.span, 1e-3)
        P[I["SPAN"]] = kf.span
        P[I["NOW"]] = kf.now - kf.org
        P[I["LO"]] = kf.lo - kf.org
        P[I["RISE"]] = RISE_B
        P[I["LOWK"]] = 0.45 + 0.4 * mist
        P[I["HOMES"]] = 1.0 if kf.summit_key >= 0 else 0.5
        P[I["HSH"]] = hs_deg

    def _derive_example(self, span: float, lr: float, hs_deg: float, mist: float) -> None:
        """Bake the example over the same view and derive it into TS / CS as the 'previous piece', ending where the
        current piece begins.  Once, on a first frame that finds a young piece."""
        kf = self.kf
        kf.bake(self._ex_tl, KF.TRAILING, span, lr, hs_deg)
        P = self.Peh
        P.fill(0.0)
        self._fill_derive(P, kf, mist, hs_deg)
        np.nan_to_num(P, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.Pe.set(P)
        launch_2d(self.k_derive, kf.fw, kf.fh, (self.TS, self.CS, kf.A, kf.B, self.TER, self.COL, self.Pe))
        self.snap = (float(kf.org), float(kf.span), float(kf.now), float(kf.now))
        self.snap_on = True

    # -- the frame ---------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = _fin(dt)
        dt = dt if 0.0 < dt < 10.0 else 0.0
        tl = self._timeline()
        tl.update(self.sound, dt, _fin(p["piece_gap"], 90.0))
        now = float(tl.now())
        lag_b = 2.0 * min(max(_fin(p["lag"], 14.0), 4.0), 40.0)
        self._track(tl, now, dt, lag_b)
        self._fly(tl, now, dt, p, lag_b)
        gap = now - self.cam_b
        if gap > KF.CAP - 20.0:                               # far out of reach (never in practice): rejoin
            self.cam_b = now - lag_b
            gap = lag_b
        span = min(max(lag_b * 1.3 + 16.0, gap + 14.0), float(KF.CAP))
        hs_deg = 180.0 * min(max(_fin(p["hue_shift"]), 0.0), 1.0)
        kf = self.kf
        lr = min(max(_fin(p["log_rows"], 1.0), 0.0), 1.0)
        mist = min(max(_fin(p["mist"], 0.5), 0.0), 1.0)
        if self._backdrop:
            self._backdrop = False
            self._derive_example(span, lr, hs_deg, mist)
        kf.bake(tl, KF.TRAILING, span, lr, hs_deg)
        self.view_org, self.view_span = float(kf.org), float(kf.span)
        if self.snap_on and kf.org > (self.snap[2] - self.snap[3]) + 1.0:
            self.snap_on = False                              # the previous piece is out of view
        self._colours(dt, hs_deg)

        en = min(max(_fin(p["energy"], 0.5), 0.0), 1.0)
        pu = min(max(_fin(p["pulse"]), 0.0), 1.0)
        alt = min(max(_fin(p["altitude"], 0.35), 0.0), 1.0)
        rh = 3.2 * min(max(_fin(p["ridge_height"], 1.0), 0.2), 2.0)
        ma = 4.0 * min(max(_fin(p["meander"], 0.5), 0.0), 1.0)
        hw, ph = self.hw, self.ph
        tb = 0.15 + 0.85 * en

        # the camera: over the valley's centreline, looking along it, banking into its bends
        abs_cam = self.abs_off + self.cam_b
        pha = (abs_cam * KMA) % (2.0 * math.pi)
        phb = (abs_cam * KMB) % (2.0 * math.pi)
        ka, kb = KMA / ZS, KMB / ZS

        def ax(zr: float) -> float:
            return ma * (math.sin(pha + zr * ka) + 0.35 * math.sin(phb + zr * kb))

        alt_u = 0.5 + 7.0 * alt ** 1.3
        sway = (0.06 + 0.10 * hw) * WH * math.sin(ph["sway"])
        ez = hw * 1.2 * math.sin(0.61 * ph["pan"] + 0.4)
        cam_x = ax(ez) + sway
        bob = alt_u * 0.04 * math.sin(ph["bob"]) * (0.5 + hw) \
            + 0.04 * tb * (math.sin(1.7 * ph["turb"]) + 0.5 * math.sin(3.1 * ph["turb"] + 1.0))
        cam_y = alt_u * (1.0 + 0.25 * hw) + bob
        la = min(max(0.5 * gap * ZS, 3.0), 20.0)
        yaw = math.atan2(ax(ez + la) - cam_x, la) + hw * 0.42 * math.sin(ph["pan"]) \
            + 0.04 * math.sin(0.37 * ph["pan"] + 1.0)
        # close behind the music (a short lag) there is little land ahead: look down at it as it forms
        pitch = -(0.10 + 0.32 * alt) - 0.05 * hw - 0.22 * _smooth(20.0, 8.0, lag_b) \
            + 0.015 * tb * math.sin(2.3 * ph["turb"])
        d = 0.5 * la
        kappa = (ax(ez + d + 2.0) - 2.0 * ax(ez + d) + ax(ez + d - 2.0)) / 4.0
        roll_t = min(max(-3.0 * kappa * (0.4 + self.v_cam * ZS), -0.22), 0.22)
        self.roll += (roll_t - self.roll) * (1.0 - math.exp(-dt / 0.8) if dt > 0 else (1.0 if self._first else 0.0))
        roll = self.roll + 0.02 * tb * math.sin(1.3 * ph["turb"] + 2.0)
        cp_, sp_ = math.cos(pitch), math.sin(pitch)
        fwd = np.array([math.sin(yaw) * cp_, sp_, math.cos(yaw) * cp_])
        rgt = np.array([math.cos(yaw), 0.0, -math.sin(yaw)])
        up = np.cross(fwd, rgt)
        cr, sr = math.cos(roll), math.sin(roll)
        rgt, up = rgt * cr + up * sr, up * cr - rgt * sr

        # the sun: low on the left, a little ahead (side light, so the relief reads), wandering slowly
        el = math.radians(SUN_EL + 3.0 * math.sin(ph["sun"]))
        az = math.radians(SUN_AZ + 9.0 * math.sin(0.7 * ph["sun"] + 1.0))
        sund = np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)])

        # the haze: clear air to a third of the way to now, then thickening (less from high up)
        front = max(lag_b * ZS, 10.0)
        tmax = gap * ZS + 55.0
        hmax = rh * (1.0 + 0.14 + 1.35 + CRAG) + 0.2
        I = _IDX
        P = self.Ph
        P.fill(0.0)
        P[I["W"]], P[I["H"]], P[I["FW"]], P[I["FH"]] = self.w, self.h, kf.fw, kf.fh
        P[I["EYE"]:I["EYE"] + 3] = (cam_x, cam_y, ez)
        P[I["FWD"]:I["FWD"] + 3] = fwd
        P[I["RGT"]:I["RGT"] + 3] = rgt
        P[I["UP"]:I["UP"] + 3] = up
        P[I["TANH"]] = math.tan(math.radians(0.5 * VFOV))
        P[I["ASP"]] = self.w / max(self.h, 1)
        P[I["FIRST"]] = 1.0 if self._first else 0.0
        P[I["DT"]] = dt
        P[I["CAMB"]] = self.cam_b - kf.org                    # bins measured from the view's origin (small floats)
        P[I["ORG"]] = 0.0
        P[I["ISPAN"]] = 1.0 / max(kf.span, 1e-3)
        P[I["SPAN"]] = kf.span
        P[I["NOW"]] = kf.now - kf.org
        P[I["LO"]] = kf.lo - kf.org
        P[I["RH"]] = rh
        P[I["MA"]], P[I["PHA"]], P[I["KA"]], P[I["PHB"]], P[I["KB"]] = ma, pha, ka, phb, kb
        P[I["ZOFF"]] = (abs_cam * ZS) % NP
        P[I["HMAX"]], P[I["TMAX"]] = hmax, tmax
        P[I["HZ0"]] = 0.3 * front
        P[I["HZK"]] = HAZE / front * (1.0 - 0.35 * alt) * (0.8 + 0.4 * mist)
        P[I["HZH"]] = 0.6 / rh
        P[I["KSUN"]] = KSUN
        P[I["RNGK"]] = 1.0
        P[I["RNG0"]] = max(gap * ZS + 8.0, 22.0)
        fh2 = math.hypot(fwd[0], fwd[2])
        P[I["GFX"]], P[I["GFZ"]] = (fwd[0] / fh2, fwd[2] / fh2) if fh2 > 1e-6 else (0.0, 1.0)
        gdc = (tmax + 4.0 - G_DMIN) / (math.exp(G_LK) - 1.0)
        P[I["GDC"]], P[I["GIDC"]] = gdc, 1.0 / gdc
        P[I["EMIN"]] = 1.5 * max(kf.span * ZS / kf.fw, WH / kf.fh)
        P[I["PXA"]] = 2.0 * math.tan(math.radians(0.5 * VFOV)) / max(self.h, 1)
        P[I["MIST"]] = 0.55 * mist ** 1.3
        P[I["MPH"]] = ph["mist"] % (10.0 * math.pi)
        P[I["GLOW"]] = (0.12 + 0.25 * en) + 0.9 * pu
        P[I["GAIN"]] = min(max(_fin(p["intensity"], 1.0), 0.0), 2.0)
        P[I["GLARE"]] = 0.35 + 1.1 * en
        P[I["SUND"]:I["SUND"] + 3] = sund
        P[I["SUNC"]:I["SUNC"] + 3] = self.sun
        P[I["ZEN"]:I["ZEN"] + 3] = self.zen
        P[I["HOR"]:I["HOR"] + 3] = self.hor
        P[I["SALB"]:I["SALB"] + 3] = self.salb
        P[I["CLX"]] = ph["clx"] % NTN
        P[I["CLZ"]] = ph["clz"] % NTN
        P[I["CLEAR"]] = 0.45
        P[I["MTOP"]] = rh * (0.06 + 0.3 * mist)
        P[I["CRAG"]] = CRAG
        P[I["SLOPE"]] = 0.5 + 0.4 * rh / 3.2
        self._fill_derive(P, kf, mist, hs_deg)
        if self.snap_on:
            o, s, nw, sh = self.snap
            P[I["SNAP"]] = 1.0
            P[I["ORGS"]], P[I["SPANS"]], P[I["NOWS"]] = o, s, nw
            P[I["SHIFT"]] = sh + kf.org                       # p is measured from the view's origin here
        P[I["SEED"]] = float(self.seed & 0xFFFF)
        np.nan_to_num(P, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.Pd.set(P)

        launch_2d(self.k_derive, kf.fw, kf.fh, (self.TER, self.COL, kf.A, kf.B, self.TS, self.CS, self.Pd))
        self.k_guard((1, 1, 1), (GUARD_N, 1, 1), (self.ST, self.Pd, self.TER, self.NT))
        launch_2d(self.k_grid, GA, GS, (self.HG, self.Pd, self.TER, self.NT))
        launch_2d(self.k_render, self.w, self.h, (self.buf, self.Pd, self.TER, self.COL, self.NT, self.ST, self.HG))
        present(self.ctx, self.buf, out)
        self._first = False

    def close(self) -> None:
        if getattr(self, "kf", None) is not None:
            self.kf.close()
        self.kf = None
        self.TER = self.COL = self.TS = self.CS = self.NT = self.ST = self.Pd = self.Pe = self.HG = self.buf = None
        self._tl = None
