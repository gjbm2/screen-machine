"""Piano strings: looking down into a grand piano, each string played flashes where the hammer strikes and shimmers as it rings.

Source: Original
Author: smfx
License: MIT
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  The 88 courses run up the screen from a gold
pin block (tuning pins, the capo bar) over a dark spruce soundboard to a curved maple bridge, laid out like a
grand: the bass is single and paired copper-wound strings, long and thick, overstrung across the tenor; the tenor
and treble are steel trichords that shorten along the bridge's curve to C8.  A gentle perspective fans them.

A strike blooms a flash at the hammer's strike point (an eighth of the speaking length from the capo bar) with a
thin streak of light across the neighbouring strings (the soft pedal shifts the action, so it lands on two of
three strings, and softens it), and sends two glints running along the string; the string then rings, drawn as a
long exposure: the pixel-filtered arcsine of a sinusoid's position, a lens with bright edges, normalised so its
edges stay bright as it widens (a trichord's unisons beat slowly, so its swing breathes).  Its swing follows the note's sounding level from the notes table, so it decays as
the piano does and damps quickly at key-up unless the pedal holds it; partials ring briefly after the strike, so
the lens starts knotted and settles to the fundamental.  A trichord's three strings glow apart near their ends and
merge into one lens where they swing.  With the dampers up (pedal, a held key, or the undamped top keys) the
strings an octave, a twelfth, two octaves or a fifth from what sounds shimmer faintly in sympathy.  A faint glow
line marks the dampers and lifts with them.

Strings are drawn at the output resolution (line SDFs filtered for the pixel's footprint, so the thinnest treble
string neither aliases nor shimmers at 4K); the soundboard, its light and the strings' glow on it render at
``render_scale`` and are upsampled under them.  The static board (grain, plate, pins, bridges) is drawn once in
setup (supersampled) and lit each frame by a slow breathing light, so the scene is alive without notes; now and
then a glint of that light slides along a string.  Per-key shaping (levels, flashes, sympathy, damper lift) is a
few numpy operations over 128 keys on the host, uploaded with the params in one copy.

colours: gold (warm brass light on dark wood), hands (blue below middle C, amber above), pitch (the palette around
the circle of fifths) or register (the palette from A0 to C8).  Macros: energy -> ambient light, breathing pace,
string swing, halo size, idle glints; pulse (bind note_on) -> halo and swing kick, a flare of the board light;
speed -> breathing, light drift and glints; intensity -> output gain; palette / period / drift colour the pitch
and register modes and tint the light; room light dims the board for colour presets.  Geometry (perspective, the
overstrung bass's angle) is structural: a change redraws the board once.
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
from stream_service.plugins._lib.upsample import internal_size
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.piano_strings")

LOW, HIGH = 21, 108                    # A0 .. C8
BASS_TOP = 42                          # the overstrung bass: A0 .. F#2 (wound), then steel trichords
SINGLE_TOP = 31                        # one string per note up to G1, two to F#2, three above
NB = BASS_TOP - LOW + 1                # 22 bass courses
NT = HIGH - BASS_TOP                   # 66 tenor and treble courses
UNDAMPED_FROM = 89                     # no dampers from F6 up (sound_view.py)
COLOURS = ("gold", "hands", "pitch", "register")
V0 = 0.14                              # the capo bar's height on screen (fraction of the height from the bottom)
V_TOP = 0.955                          # the longest bass string's bridge end
REF_H = 2160.0                         # string radii are given in px at this output height

# sympathetic partners: (semitones, weight) -- an octave, a twelfth, two octaves, a fifth, a 17th, a fourth
_SYM = ((12, 0.55), (19, 0.32), (24, 0.26), (7, 0.2), (28, 0.12), (5, 0.07))

_N_PAR = 64
_N_KEY = 20                            # per key: sympathy, damper lift, swing, glow, flash, run, age, hot,
                                       # reach, active, partials 2 and 3, glint, its place, shimmer phase,
                                       # sympathy shimmer, colour (3), halo
_N_CRS = 16                            # per course (static): see _layout
_N_HD = 128 * _N_KEY
_RING = 16                             # pinned upload slots (frames in flight that may still be reading one)
_GRAPHS = 8                            # captured graphs kept, one per output buffer seen
_logged: set = set()
_NVRTC = ("--std=c++14", "--use_fast_math")   # no NaN logic in the kernels; output within 1/255 of IEEE


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def _layout(aspect: float, persp: float) -> tuple[np.ndarray, dict]:
    """The course table (128 x 16: X0, L, swing, n, r, ds, wound, hash, 1/L, hitch Z, -k X0 (screen slope),
    spread, three tuning-pin Zs, the lowest of them) in world units (screen heights at the capo bar; Z runs up the
    strings from it) and the family layout."""
    k = 0.42 * persp
    dv = V_TOP - V0
    z_top = dv / (1.0 - k * dv)
    margin = 0.045 * aspect
    width = aspect - 2.0 * margin
    sb_f, gap_f = 1.32, 1.6                        # bass spacing and the bass/tenor break, in tenor spacings
    spt = width / ((NB - 1) * sb_f + gap_f + (NT - 1) + 0.5 * (sb_f + 1.0))
    spb = sb_f * spt
    xb0 = -0.5 * width + 0.5 * spb
    xt0 = xb0 + (NB - 1) * spb + gap_f * spt
    crs = np.zeros((128, _N_CRS), np.float32)
    lmax = 0.975 * z_top
    rng = np.random.default_rng(1234)
    for p in range(LOW, HIGH + 1):
        if p <= BASS_TOP:
            i = p - LOW
            sb = i / (NB - 1)
            x0 = xb0 + i * spb
            ln = lmax - (lmax - 0.85 * z_top) * sb ** 1.25
            n = 1 if p <= SINGLE_TOP else 2
            r_px = (7.2 - 1.6 * sb) if n == 1 else (5.0 - 1.4 * (p - SINGLE_TOP - 1) / (BASS_TOP - SINGLE_TOP - 1))
            ds = 0.36 * spb if n == 2 else 0.0
            wound, sp = 1.0, spb
        else:
            i = p - BASS_TOP - 1
            s = (HIGH - p) / (NT - 1)              # 0 at C8 .. 1 at G2
            x0 = xt0 + i * spt
            lmin, lt = 0.13, 0.80 * z_top
            ln = lmin + (lt - lmin) * (0.3 * s + 0.7 * s ** 1.6)
            n = 3
            r_px = 1.0 + 0.8 * s
            ds = 0.27 * spt
            wound, sp = 0.0, spt
        swing = 0.52 * sp * (0.35 + 0.65 * math.sqrt(ln / lmax))
        pins = [-0.03 - 0.019 * ((p + j) % 3) for j in range(n)]
        zp = pins + [0.0] * (3 - n)
        crs[p] = (x0, ln, swing, n, r_px / REF_H, ds, wound, rng.random(), 1.0 / ln, ln + 0.036, -k * x0,
                  0.5 * (n - 1) * ds, *zp, min(pins))
    return crs, {"k": k, "z_top": z_top, "xt0": xt0, "spt": spt, "xb0": xb0, "spb": spb}


_SRC = HELPERS + r"""
#define NB """ + str(NB) + r"""
#define NT """ + str(NT) + r"""
#define PB0 """ + str(LOW) + r"""
#define PT0 """ + str(BASS_TOP + 1) + r"""
#define UNDAMPED """ + str(UNDAMPED_FROM) + r"""
#define NK """ + str(_N_KEY) + r"""
#define NC """ + str(_N_CRS) + r"""
#define PI_F 3.14159265f

__device__ __forceinline__ float3 mk(float a, float b, float c) { return make_float3(a, b, c); }
__device__ __forceinline__ float3 operator+(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 operator*(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 operator*(float3 a, float3 b) { return make_float3(a.x * b.x, a.y * b.y, a.z * b.z); }
__device__ __forceinline__ float3 lerp3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
__device__ __forceinline__ float tmap(float v) { return 1.f - __expf(-1.25f * fmaxf(v, 0.f)); }

// P (what the kernels read): [12]w [13]h (the soft layer)  [14]W [15]H (output)  [16]v0 (capo bar height)
//    [17]k (perspective)  [18]bass tilt  [19]xt0 [20]spt [21]xb0 [22]spb (the two families' course spacing)
//    [27]energy  [29]gain  [35]soft pedal  [37]breath  [38,39]pool 1 (sx, v)  [40,41]pool 2  [44]aspect
//    [45..47]light colour  [48]ambient  [49]damper line  [50]pool gain  [51]1 / halo width^2  [52]1 / streak height
//    (the rest are the host's: palette, colour mode, phases)
// hd (per key, NK floats): [0]sympathy [1]damper lift [2]swing [3]glow level [4]flash [5]run [6]age (s) [7]hot
//    [8]reach (world) [9]active [10,11]partials 2, 3 [12]idle glint [13]its place (0-1 along) [14]shimmer phase
//    [15]sympathy shimmer [16..18]colour [19]halo

// screen (sx in screen heights from the left, v from the bottom) -> world (X across from the centre, Z up the
// strings from the capo bar); returns s = 1 + kZ (the across scale: px per world unit = H / s)
__device__ __forceinline__ float to_world(float sx, float v, const float* P, float& X, float& Z) {
    const float dv = v - P[16];
    const float den = fmaxf(1.f - P[17] * dv, 0.1f);
    Z = dv / den;
    X = (sx - 0.5f * P[44]) / den;
    return 1.f / den;
}

// the room light: a breathing pool and a smaller one wandering over the piano
__device__ __forceinline__ float pool(float sx, float v, const float* P) {
    const float ax = sx - P[38], ay = v - P[39], bx = sx - P[40], by = v - P[41];
    return P[50] * (P[37] * __expf(-(ax * ax + ay * ay) * 5.0f) + 0.45f * __expf(-(bx * bx + by * by) * 12.f));
}
__device__ __forceinline__ float front(float v) { return smooth(0.24f, 0.02f, v); }   // a soft fill from the keys' side
__device__ __forceinline__ float ambient(float sx, float v, const float* P) {
    const float cx = sx - 0.5f * P[44], cy = v - 0.5f;
    return P[48] * (0.3f + 0.7f * __expf(-(cx * cx * 1.1f + cy * cy * 2.2f)) + 1.1f * front(v));
}

// ---------------------------------------------------------------------------------------------------- the board
__device__ float3 spruce(float X, float Z, unsigned int seed) {
    const float gu = X * 0.83f - Z * 0.56f, gv = X * 0.56f + Z * 0.83f;      // across / along the grain
    const float u = gu + (fbm(gu * 2.1f, gv * 0.6f, seed) - 0.5f) * 0.05f
                       + (vnoise(gu * 34.f, gv * 2.5f, seed + 5u) - 0.5f) * 0.006f;
    const float strip = floorf(u / 0.083f);
    const float hs = hash1((int)strip + 4096, 7, seed + 3u);
    const float rings = u * (190.f + 70.f * hs) + 3.f * vnoise(gu * 12.f, gv * 0.8f, seed + 13u);
    const float f = rings - floorf(rings);
    const float late = smooth(0.55f, 0.74f, f) * smooth(1.0f, 0.84f, f);
    const float var = 0.86f + 0.28f * vnoise(gu * 7.f, gv * 0.9f, seed + 9u);
    const float3 early = mk(0.50f, 0.33f, 0.16f) * ((0.92f + 0.12f * hs) * var);
    return lerp3(early, mk(0.33f, 0.19f, 0.085f), late * (0.25f + 0.35f * vnoise(gu * 30.f, gv * 2.f, seed + 11u)));
}

__device__ __forceinline__ float course_L(const float* crs, int p0, int n, float cf) {
    const float c = fminf(fmaxf(cf, 0.f), (float)(n - 1));
    const int i = min((int)floorf(c), n - 2);
    const float t = c - i;
    return crs[NC * (p0 + i) + 1] * (1.f - t) + crs[NC * (p0 + i + 1) + 1] * t;
}

// distance (world) to the nearest of a course's tuning pins (0), bridge pins (1) or hitch pins (2); dx, dz: its offset
__device__ float pin_dist(const float* crs, int p, float X, float Z, float tilt, int which, float& ox, float& oz) {
    const float* C = crs + NC * p;
    const int n = (int)C[3];
    float best = 1e9f;
    for (int j = 0; j < 3; ++j) {
        if (j >= n) break;
        const float off = (j - 0.5f * (n - 1)) * C[5];
        float zp;
        if (which == 0) zp = -0.03f - 0.019f * ((p + j) % 3);
        else if (which == 1) zp = C[1] + ((j & 1) ? 0.0026f : -0.0026f);
        else zp = C[1] + 0.036f;
        const float dx = X - (C[0] + off + tilt * zp), dz = Z - zp;
        const float d = sqrtf(dx * dx + dz * dz);
        if (d < best) { best = d; ox = dx; oz = dz; }
    }
    return best;
}

// a polished steel pin of radius rp seen from above at offset (ox, oz): albedo and specular
__device__ __forceinline__ void steel_pin(float d, float ox, float oz, float rp, float3& alb, float& spec) {
    const float rr = d / rp;
    const float hx = ox / rp + 0.35f, hz = oz / rp - 0.35f;           // the highlight towards the light
    const float hl = __expf(-(hx * hx + hz * hz) * 6.f);
    alb = mk(0.42f, 0.42f, 0.43f) * (0.6f + 0.4f * (1.f - rr * rr));
    spec = 0.25f + 0.75f * hl;
}

__device__ void material(float sx, float v, const float* P, const float* crs, unsigned int seed,
                         float3& alb, float& spec) {
    float X, Z;
    to_world(sx, v, P, X, Z);
    const float tilt = P[18], spt = P[20], spb = P[22];
    const float cf = (X - P[19]) / spt;                  // tenor/treble course coordinate
    const float cb = (X - tilt * Z - P[21]) / spb;       // bass course coordinate
    alb = spruce(X, Z, seed) * 0.72f;
    spec = 0.04f;
    const float3 gold = mk(0.64f, 0.47f, 0.19f);
    const float brush = 0.9f + 0.1f * vnoise(X * 900.f, Z * 25.f, seed + 21u);
    float ox = 0.f, oz = 0.f;
    if (Z < -0.0055f) {                                  // the plate over the pin block, with the tuning pins
        alb = gold * brush;
        spec = 0.32f;
        const float edge = smooth(-0.105f, -0.098f, Z);  // the plate's front edge, then the dark key slip
        alb = lerp3(mk(0.03f, 0.026f, 0.024f), alb, edge);
        spec *= edge;
        const int pc = PT0 + min(max((int)floorf(cf + 0.5f), 0), NT - 1);
        const int pb = PB0 + min(max((int)floorf(cb + 0.5f), 0), NB - 1);
        float d = 1e9f, bx = 0.f, bz = 0.f;
        for (int j = -1; j <= 1; ++j) {
            const int a = pc + j, b = pb + j;
            if (a >= PT0 && a <= PT0 + NT - 1) { const float q = pin_dist(crs, a, X, Z, 0.f, 0, ox, oz); if (q < d) { d = q; bx = ox; bz = oz; } }
            if (b >= PB0 && b <= PB0 + NB - 1) { const float q = pin_dist(crs, b, X, Z, tilt, 0, ox, oz); if (q < d) { d = q; bx = ox; bz = oz; } }
        }
        const float rp = 0.0046f;
        alb = alb * (0.62f + 0.38f * smooth(rp * 0.95f, rp * 2.f, sqrtf((bx + 0.0012f) * (bx + 0.0012f) + (bz + 0.0016f) * (bz + 0.0016f))));
        if (d < rp) steel_pin(d, bx, bz, rp, alb, spec);
        return;
    }
    if (Z < 0.003f) {                                    // the capo bar
        const float t = (Z + 0.0055f) / 0.0085f;
        alb = mk(0.62f, 0.47f, 0.21f) * (0.7f + 0.3f * sinf(PI_F * t));
        spec = 0.5f * sinf(PI_F * t);
        return;
    }
    const float lt = course_L(crs, PT0, NT, cf);
    const float lb = course_L(crs, PB0, NB, cb);
    const bool in_t = cf > -0.7f && cf < NT - 0.3f;
    const bool in_b = cb > -0.7f && cb < NB - 0.3f;
    if (cf > -0.7f) {                                    // the tenor/treble bridge and the plate rim beyond it
        const float slope = (course_L(crs, PT0, NT, cf + 0.5f) - course_L(crs, PT0, NT, cf - 0.5f)) / spt;
        const float dz = (Z - lt) * rsqrtf(1.f + slope * slope);
        const bool under_bass = cb < NB + 0.6f;
        const float rim0 = 0.021f, rim1 = 0.060f;
        if (!under_bass && dz > rim0 && dz < rim1) {
            alb = gold * brush * (0.75f + 0.25f * smooth(rim0, rim0 + 0.008f, dz)) * (0.8f + 0.2f * smooth(rim1, rim1 - 0.006f, dz));
            spec = 0.22f;
            const int pc = PT0 + min(max((int)floorf(cf + 0.5f), 0), NT - 1);
            float d = 1e9f, bx = 0.f, bz = 0.f;
            for (int j = -1; j <= 1; ++j) {
                const int a = pc + j;
                if (a >= PT0 && a <= PT0 + NT - 1) { const float q = pin_dist(crs, a, X, Z, 0.f, 2, ox, oz); if (q < d) { d = q; bx = ox; bz = oz; } }
            }
            alb = alb * (0.5f + 0.5f * smooth(0.0026f, 0.005f, d));
            if (d < 0.0026f) steel_pin(d, bx, bz, 0.0026f, alb, spec);
            return;
        }
        if (!under_bass && dz >= rim1) {                 // a shadowed lip of the rim, then the soundboard again
            alb = alb * (0.55f + 0.45f * smooth(rim1, rim1 + 0.02f, dz));
        }
        if (in_t && fabsf(dz) < 0.0072f) {               // the maple bridge, lit along its crest
            const float t = dz / 0.0072f;
            alb = mk(0.62f, 0.36f, 0.16f) * (0.55f + 0.45f * sqrtf(fmaxf(1.f - t * t, 0.f)));
            spec = 0.10f + 0.25f * __expf(-(t + 0.3f) * (t + 0.3f) * 8.f);
            const int pc = PT0 + min(max((int)floorf(cf + 0.5f), 0), NT - 1);
            float d = 1e9f, bx = 0.f, bz = 0.f;
            for (int j = -1; j <= 1; ++j) {
                const int a = pc + j;
                if (a >= PT0 && a <= PT0 + NT - 1) { const float q = pin_dist(crs, a, X, Z, 0.f, 1, ox, oz); if (q < d) { d = q; bx = ox; bz = oz; } }
            }
            if (d < 0.0017f) steel_pin(d, bx, bz, 0.0017f, alb, spec);
            return;
        }
        if (in_t && dz > 0.f && dz < 0.016f) alb = alb * (0.45f + 0.55f * smooth(0.007f, 0.016f, dz));   // its shadow
    }
    if (in_b) {                                          // the bass bridge
        const float slope = (course_L(crs, PB0, NB, cb + 0.5f) - course_L(crs, PB0, NB, cb - 0.5f)) / spb;
        const float dz = (Z - lb) * rsqrtf(1.f + slope * slope);
        if (fabsf(dz) < 0.0085f) {
            const float t = dz / 0.0085f;
            alb = mk(0.62f, 0.36f, 0.16f) * (0.55f + 0.45f * sqrtf(fmaxf(1.f - t * t, 0.f)));
            spec = 0.10f + 0.25f * __expf(-(t + 0.3f) * (t + 0.3f) * 8.f);
            const int pb = PB0 + min(max((int)floorf(cb + 0.5f), 0), NB - 1);
            float d = 1e9f, bx = 0.f, bz = 0.f;
            for (int j = -1; j <= 1; ++j) {
                const int b = pb + j;
                if (b >= PB0 && b <= PB0 + NB - 1) { const float q = pin_dist(crs, b, X, Z, tilt, 1, ox, oz); if (q < d) { d = q; bx = ox; bz = oz; } }
            }
            if (d < 0.0019f) steel_pin(d, bx, bz, 0.0019f, alb, spec);
        } else if (dz > 0.f && dz < 0.018f) {
            alb = alb * (0.45f + 0.55f * smooth(0.0085f, 0.018f, dz));
        }
    }
}

extern "C" __global__
void board(uchar4* __restrict__ out, const float* __restrict__ P, const float* __restrict__ crs, const unsigned int seed)
{
    const int w = (int)P[12], h = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float3 a = mk(0.f, 0.f, 0.f);
    float s = 0.f;
    #pragma unroll
    for (int j = 0; j < 3; ++j) {
        #pragma unroll
        for (int i = 0; i < 3; ++i) {
            float3 c; float sp;
            material((x + (i + 0.5f) / 3.f) / h, 1.f - (y + (j + 0.5f) / 3.f) / h, P, crs, seed, c, sp);
            a = a + c; s += sp;
        }
    }
    a = a * (1.f / 9.f);
    out[y * w + x] = make_uchar4((unsigned char)(fminf(a.x, 1.f) * 255.f + 0.5f), (unsigned char)(fminf(a.y, 1.f) * 255.f + 0.5f),
                                 (unsigned char)(fminf(a.z, 1.f) * 255.f + 0.5f), (unsigned char)(fminf(s / 9.f, 1.f) * 255.f + 0.5f));
}

// ------------------------------------------------------------------------------------------- the soft layer
// one course's light on the board: a halo along its speaking length and the strike's bloom
__device__ __forceinline__ void course_soft(int p, float X, float Z, float tilt, float isp, const float* P,
                                            const float* hd, const float* crs, float3& acc) {
    const float* D = hd + NK * p;
    if (D[19] < 0.004f && D[4] < 1e-3f) return;
    const float* C = crs + NC * p;
    const float q = (X - C[0] - tilt * Z) * isp;         // across, in course spacings
    if (fabsf(q) > 2.6f) return;
    const float L = C[1], fl = D[4];
    const float xi = Z * C[8];
    const float3 col = mk(D[16], D[17], D[18]);
    const float e = P[27];
    const float along = smooth(-0.03f, 0.03f, xi) * smooth(1.03f, 0.97f, xi) * (0.5f + 0.5f * __sinf(PI_F * clamp01(xi)));
    const float hl = D[19] * along * (0.7f * __expf(-q * q * P[51]) + 0.6f * __expf(-q * q * 30.f));
    acc = acc + col * hl;
    if (fl > 1e-3f) {
        const float qs = q - 0.18f * P[35] * C[5] * isp;  // una corda: the action shifts, the hammer hits two of three
        const float dz = (Z - 0.125f * L) * isp;
        const float r2 = qs * qs + dz * dz * 0.45f;
        const float big = (0.9f + 0.3f * e) * (1.f - 0.3f * P[35]);
        const float b = fl * (1.1f * __expf(-r2 / (0.22f * big)) + 0.42f * __expf(-r2 / (2.2f * big)));
        const float hot = D[7] / fmaxf(fl, 1e-3f) * b;
        const float sz = (Z - 0.125f * L) * P[52];                       // the streak: a thin line across the strings
        const float streak = D[7] * 2.4f * __expf(-sz * sz - qs * qs * 0.45f) * smooth(2.6f, 1.8f, fabsf(q));
        acc = acc + col * (b + streak) + mk(1.f, 0.92f, 0.8f) * (1.2f * hot + 0.8f * streak);
    }
}

// the damper line of one family at continuous course coordinate cf (dampers end below UNDAMPED)
__device__ __forceinline__ void damper_line(int p0, int n, float cf, float Z, const float* P, const float* hd,
                                            const float* crs, float3& acc) {
    const float cl = (float)(UNDAMPED - 1 - p0);         // the last damped course
    if (cf < -0.5f || cf > fminf((float)n - 0.5f, cl + 0.5f)) return;
    const float c = fminf(fmaxf(cf, 0.f), (float)(n - 1));
    const int i = min((int)floorf(c), n - 2);
    const float t = c - i;
    const float L = crs[NC * (p0 + i) + 1] * (1.f - t) + crs[NC * (p0 + i + 1) + 1] * t;
    const float lift = hd[NK * (p0 + i) + 1] * (1.f - t) + hd[NK * (p0 + i + 1) + 1] * t;
    const float zd = 0.125f * L + 0.026f + 0.006f * lift;
    const float wd = 0.0016f + 0.0016f * lift;
    const float dz = (Z - zd) / wd;
    const float g = P[49] * (0.03f + 0.05f * lift) * __expf(-dz * dz) * smooth(-0.5f, -0.1f, cf)
                    * smooth(cl + 0.5f, cl + 0.1f, cf);
    acc = acc + mk(1.0f, 0.72f, 0.40f) * g;
}

extern "C" __global__
void soft(uchar4* __restrict__ out, const float* __restrict__ P, const float* __restrict__ hd,
          const float* __restrict__ crs, const uchar4* __restrict__ brd)
{
    const int w = (int)P[12], h = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (x + 0.5f) / h, v = 1.f - (y + 0.5f) / h;
    float X, Z;
    to_world(sx, v, P, X, Z);
    const uchar4 b = brd[y * w + x];
    const float3 alb = mk(b.x, b.y, b.z) * (1.f / 255.f);
    const float spec = b.w * (1.f / 255.f);
    const float pl = pool(sx, v, P);
    const float3 lc = mk(P[45], P[46], P[47]);
    float3 acc = alb * lc * (ambient(sx, v, P) + pl) + lc * (spec * (0.06f + 1.1f * pl + 0.35f * front(v)));
    const float tilt = P[18];
    if (Z > -0.02f) {
        const float cf = (X - P[19]) / P[20];
        const int i0 = (int)floorf(cf + 0.5f);
        for (int j = -2; j <= 2; ++j) {
            const int c = i0 + j;
            if (c < 0 || c >= NT) continue;
            course_soft(PT0 + c, X, Z, 0.f, 1.f / P[20], P, hd, crs, acc);
        }
        const float cb = (X - tilt * Z - P[21]) / P[22];
        const int j0 = (int)floorf(cb + 0.5f);
        for (int j = -2; j <= 2; ++j) {
            const int c = j0 + j;
            if (c < 0 || c >= NB) continue;
            course_soft(PB0 + c, X, Z, tilt, 1.f / P[22], P, hd, crs, acc);
        }
        damper_line(PT0, NT, cf, Z, P, hd, crs, acc);
        damper_line(PB0, NB, cb, Z, P, hd, crs, acc);
    }
    out[y * w + x] = make_uchar4((unsigned char)(255.f * tmap(acc.x) + 0.5f), (unsigned char)(255.f * tmap(acc.y) + 0.5f),
                                 (unsigned char)(255.f * tmap(acc.z) + 0.5f), 255);
}

// ------------------------------------------------------------------------------------------------- the strings
// Coverage of a string of radius r (px) swinging with amplitude a (px), at perpendicular offset d (px): the
// position of a sinusoid sampled uniformly in phase is arcsine-distributed; integrated over the string's width and
// the pixel's footprint (plus half a pixel of blur, so a still string is a filtered line, never a staircase).
__device__ __forceinline__ float asin_fast(float x) {        // Abramowitz & Stegun 4.4.45 (|err| < 7e-5)
    const float ax = fminf(fabsf(x), 1.f);
    const float r = 1.5707963f - sqrtf(1.f - ax) * (1.5707288f + ax * (-0.2121144f + ax * (0.0742610f - 0.0187293f * ax)));
    return copysignf(r, x);
}
__device__ __forceinline__ float vib_cov(float d, float r, float a) {
    if (a < 0.05f) {                                     // still: the same filter's edge, without the arcsines
        const float ad = fabsf(d);
        return 1.f - smooth(r, r + 1.f, ad);
    }
    const float ae = sqrtf(a * a + 0.25f);
    const float lo = (d - 0.5f - r) / ae, hi = (d + 0.5f + r) / ae;
    return (asin_fast(hi) - asin_fast(lo)) * 0.31830989f;
}

__device__ __forceinline__ float fold01(float x) {        // a triangle wave: a pulse reflecting off both ends
    const float m = x - 2.f * floorf(x * 0.5f);
    return 1.f - fabsf(1.f - m);
}

__device__ __forceinline__ float3 screen(float3 c, float3 e, float k) {   // add light (k: its coverage)
    return mk(1.f - (1.f - c.x) * __expf(-1.25f * k * e.x), 1.f - (1.f - c.y) * __expf(-1.25f * k * e.y),
              1.f - (1.f - c.z) * __expf(-1.25f * k * e.z));
}

// one string's metal: a lit cylinder when still, smeared out as it swings; returns its coverage
__device__ __forceinline__ float metal(const float* C, float d, float Z, float zp, float zh, float rp0, float ap,
                                       float glint, float& pl, float& amb, float sx, float v, const float* P,
                                       float zpx, float3 lc, float3& c) {
    const float L = C[1];
    const bool wound = C[6] > 0.5f;
    const bool wnd = wound && Z > 0.012f && Z < L - 0.012f;   // the winding stops short of the capo bar and bridge
    const float rp = wound && !wnd ? 0.5f * rp0 : rp0;
    if (fabsf(d) > ap + rp + 1.5f) return 0.f;
    float cov = vib_cov(d, rp, ap) * clamp01(3.f * rp / (2.f * rp + 1.f));
    cov *= smooth(zp - 0.0012f, zp + 0.0012f, Z) * smooth(zh + 0.0015f, zh - 0.0015f, Z);
    if (cov < 1e-4f) return 0.f;
    if (pl < 0.f) { pl = pool(sx, v, P); amb = ambient(sx, v, P); }   // the room light, once per pixel, when needed
    const float m = smooth(0.3f * rp, rp + 1.f, ap);
    const float u = fminf(fmaxf(d / fmaxf(rp, 0.5f), -1.f), 1.f);
    float diff = 0.5f + 0.5f * sqrtf(fmaxf(1.f - u * u, 0.f));
    float spc = __expf(-(u + 0.3f) * (u + 0.3f) * 14.f);
    diff = diff + (0.82f - diff) * m;
    spc = spc + (0.3f - spc) * m;
    float3 mat;
    if (wnd) {                                           // copper winding: fine diagonal turns
        mat = mk(0.80f, 0.44f, 0.22f);
        const float pw = 0.0021f;
        const float tw = 0.5f - 0.5f * __cosf(6.2831853f * (Z * (1.f / pw) + 0.35f * u));
        const float ff = smooth(2.2f, 4.f, pw * zpx) * (1.f - m);
        diff *= 1.f - 0.5f * ff * (1.f - tw);
        spc *= 1.f - ff + ff * 1.5f * tw * tw;
    } else {
        mat = mk(0.56f, 0.56f, 0.58f);
    }
    const float3 lit = mat * lc * ((amb + pl) * diff) + lc * (spc * (0.06f + 0.7f * pl + glint));
    cov = clamp01(cov);
    c = lerp3(c, mk(tmap(lit.x), tmap(lit.y), tmap(lit.z)), cov);
    return cov;
}

__device__ __forceinline__ void course_strings(int p, float X, float Z, float tilt, float ppx, float px3, float& pl,
                                               float& amb, float sx, float v, float zpx, const float* P, const float* hd, const float* crs,
                                               float3& c) {
    const float* C = crs + NC * p;
    const float* D = hd + NK * p;
    const float dX = X - C[0] - tilt * Z;
    if (fabsf(dX) > D[8] + px3) return;                 // out of this course's reach (its swing included)
    const float zh = C[9];                               // the hitch pin
    if (Z > zh + 0.003f || Z < C[15] - 0.003f) return;
    const int n = (int)C[3];
    const float ds = C[5];
    const float slope = tilt + C[10];                    // the string's screen slope dx/dv
    const float kpx = ppx * rsqrtf(1.f + slope * slope); // world across -> px perpendicular to the string
    const float rp0 = C[4] * ppx;                        // radius (px)
    const float3 lc = mk(P[45], P[46], P[47]);
    const float xi = Z * C[8];
    const bool speak = xi > 0.f && xi < 1.f;
    const float glint = (speak && D[12] > 0.f) ? D[12] * __expf(-(xi - D[13]) * (xi - D[13]) * 180.f) : 0.f;
    const float amp = D[2];
    if (amp <= 1e-4f && D[9] < 0.5f) {                   // still and dark: only the nearest string can reach
        const int j = n > 1 ? min(max((int)floorf(dX / ds + 0.5f * n), 0), n - 1) : 0;
        const float d = (dX - (j - 0.5f * (n - 1)) * ds) * kpx;
        if (fabsf(d) > rp0 + 1.5f) return;
        metal(C, d, Z, C[12 + j], zh, rp0, 0.f, glint, pl, amb, sx, v, P, zpx, lc, c);
        return;
    }
    const float sx1 = speak ? __sinf(PI_F * xi) : 0.f;
    // the swing (world): the fundamental's lens plus partials that ring briefly after the strike
    float a = 0.f;
    if (speak && amp > 1e-4f) {
        const float c2 = D[10], c3 = D[11];
        const float env = (sx1 + c2 * fabsf(__sinf(2.f * PI_F * xi)) + c3 * fabsf(__sinf(3.f * PI_F * xi)))
                          / (1.f + 0.45f * c2 + 0.3f * c3);
        a = C[2] * amp * env;
    }
    const float ap = a * kpx;                            // swing (px)
    const float dsp = ds * kpx;
    int j0 = 0, j1 = n - 1;
    if (n > 1 && ap < 0.5f * dsp - rp0 - 2.f) {          // still enough that only the nearest string can reach
        j0 = j1 = min(max((int)floorf(dX / ds + 0.5f * n), 0), n - 1);
    }
    float own = 0.f;
    for (int j = j0; j <= j1; ++j) {
        const float zp = C[12 + j];
        if (Z < zp - 0.003f) continue;
        own += metal(C, (dX - (j - 0.5f * (n - 1)) * ds) * kpx, Z, zp, zh, rp0, ap, glint, pl, amb, sx, v, P, zpx, lc, c);
    }
    if (!speak || D[9] < 0.5f) return;
    // light the course emits: its sounding level, the strike, the two glints running from it, sympathy; each string
    // glows on its own while apart from the others (near the ends, or quietly), as one lens where they swing together
    const float L = C[1], fl = D[4], run = D[5], age = D[6];
    float em = 1.5f * D[3] * (0.62f + 0.38f * sx1) * (0.88f + 0.12f * __sinf(6.2831853f * (3.f * xi - D[14]))) + D[15];
    float hot = 0.f;
    if (fl > 1e-3f) {
        const float zs = 0.125f * L, sg = 0.010f + 0.035f * L;
        const float g = __expf(-(Z - zs) * (Z - zs) / (sg * sg));
        em += 2.4f * fl * g;
        hot = 2.0f * D[7] * g;
    }
    if (run > 1e-3f) {
        const float x1 = fold01(0.125f + 1.3f * age), x2 = fold01(0.125f - 1.3f * age + 2.f);
        em += run * (__expf(-(xi - x1) * (xi - x1) * 900.f) + __expf(-(xi - x2) * (xi - x2) * 900.f));
    }
    if (em + hot <= 1e-4f) return;
    const float3 ec = mk(D[16], D[17], D[18]) * em + mk(1.f, 0.94f, 0.84f) * hot;
    const float merge = smooth(0.25f * dsp, 1.2f * dsp + 1.f, ap);
    if (merge < 0.999f) c = screen(c, ec, fminf(own, 1.f) * (1.f - merge) * 1.15f);
    if (merge > 1e-3f) {
        // the ringing lens: one per course, its edges kept bright (the long exposure's turning points)
        const float A = ap + C[11] * kpx * (1.f - merge);
        const float rl = fmaxf(rp0, 0.6f);
        const float dd = dX * kpx;
        if (fabsf(dd) < A + rl + 1.5f) {
            float lc2 = vib_cov(dd, rl, A);
            lc2 = fminf(1.f, lc2 * 2.2f * sqrtf(fmaxf(A, 1.f) / (1.f + 2.f * rl)));
            lc2 *= 0.3f + 0.7f * lc2;                      // the turning points bright, the inside a veil
            c = screen(c, ec, lc2 * merge * 1.3f);
        }
    }
}

extern "C" __global__
void compose(uchar4* __restrict__ out, const uchar4* __restrict__ buf, const float* __restrict__ P,
             const float* __restrict__ hd, const float* __restrict__ crs)
{
    const int W = (int)P[14], H = (int)P[15], w = (int)P[12], h = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 b = (w == W && h == H) ? ld(buf, x, y, w, h) : bilin(buf, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h);
    float3 c = mk(b.x, b.y, b.z) * (1.f / 255.f);
    const float sx = (x + 0.5f) / H, v = 1.f - (y + 0.5f) / H;
    float X, Z;
    const float s = to_world(sx, v, P, X, Z);
    if (Z > -0.075f) {
        const float ppx = H / s;                          // px per world unit across
        const float px3 = 3.f * s / H;                    // three px (world)
        const float zpx = H / (s * s);                    // px per world unit along the strings
        float pl = -1.f, amb = 0.f;                       // the room light: computed on a string's first pixel
        const float tilt = P[18];
        const int i0 = (int)floorf((X - P[19]) / P[20] + 0.5f);
        for (int j = -1; j <= 1; ++j) {
            const int k = i0 + j;
            if (k < 0 || k >= NT) continue;
            course_strings(PT0 + k, X, Z, 0.f, ppx, px3, pl, amb, sx, v, zpx, P, hd, crs, c);
        }
        const int j0 = (int)floorf((X - tilt * Z - P[21]) / P[22] + 0.5f);
        for (int j = -1; j <= 1; ++j) {
            const int k = j0 + j;
            if (k < 0 || k >= NB) continue;
            course_strings(PB0 + k, X, Z, tilt, ppx, px3, pl, amb, sx, v, zpx, P, hd, crs, c);
        }
    }
    const float g = 255.f * P[29];
    out[y * W + x] = make_uchar4((unsigned char)fminf(g * c.x + 0.5f, 255.f), (unsigned char)fminf(g * c.y + 0.5f, 255.f),
                                 (unsigned char)fminf(g * c.z + 0.5f, 255.f), 255);
}
"""


class PianoStrings(Source):
    """Per frame the host issues: one H2D copy of the arena (params + per-key shaping, from a pinned ring slot), one
    event record (the slot's reuse fence) and one graph launch (the soft layer + the strings).  The graph is
    captured once per output buffer.  The board is redrawn (outside the graph) only when the geometry changes.  If
    capture fails the scene launches directly (logged once)."""
    name = "piano_strings"
    label = "Piano strings"
    description = "Looking down into a grand piano: each string played flashes where the hammer strikes and shimmers as it rings."
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        overstrung = num(0.0, 1.0, 0.5, "Overstrung bass", tier="structural")
        overstrung["x-ease"] = False
        persp = num(0.0, 1.0, 0.35, "Perspective", tier="structural")
        persp["x-ease"] = False
        return {
            **standard_scene_params(palette="ember", render_scale=0.5, period=1.0, drift=0.02),
            "colours": enum(COLOURS, "gold", "Colours", basic=True),
            "vibration": num(0.2, 2.0, 1.0, "String swing", basic=True),
            "glow": num(0.0, 2.0, 1.0, "Glow"),
            "flash": num(0.0, 2.0, 1.0, "Strike flash"),
            "sympathy": num(0.0, 2.0, 1.0, "Sympathetic shimmer"),
            "dampers": num(0.0, 2.0, 1.0, "Damper line"),
            "room_light": num(0.2, 1.6, 1.0, "Room light"),
            "overstrung": overstrung,
            "perspective": persp,
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.W, self.H = int(ctx.width), int(ctx.height)
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.brd = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, _NVRTC)
        self.k_board = mod.get_function("board")
        self.k_soft = mod.get_function("soft")
        self.k_comp = mod.get_function("compose")
        self.crs = cp.zeros((128, _N_CRS), cp.float32)
        # the uniform arena: [P (64 floats) | per key (128 x 8)], one copy per frame
        self.arena = cp.zeros(_N_PAR + _N_HD, cp.float32)
        self.P, self.hd = self.arena[:_N_PAR], self.arena[_N_PAR:]
        try:
            self._pin = cp.cuda.alloc_pinned_memory(_RING * (_N_PAR + _N_HD) * 4)
            host = np.frombuffer(self._pin, np.float32, _RING * (_N_PAR + _N_HD)).reshape(_RING, -1)
        except Exception as exc:                          # pageable works too (the driver stages it at once)
            _log_once("pin", "piano_strings: no pinned memory (%s); uploading from pageable memory", exc)
            self._pin = None
            host = np.zeros((_RING, _N_PAR + _N_HD), np.float32)
        host[:] = 0.0
        self._spare = np.zeros(_N_PAR + _N_HD, np.float32)
        self._host = [host[i] for i in range(_RING)] + [self._spare]
        self._views = [(hb, hb[:_N_PAR], hb[_N_PAR:].reshape(128, _N_KEY)) for hb in self._host]
        self._ev = [cp.cuda.Event(block=False, disable_timing=True, interprocess=False) for _ in range(_RING)]
        self._slot = 0
        self._graphs: OrderedDict = OrderedDict()
        self._cap = None
        self._mode = "graph"                              # "direct" after a failed capture
        self.calls = 0
        self._host_init(params)
        self._geometry(params, self._views[0][1])         # the course table and the board, drawn once here

    def _host_init(self, params: dict) -> None:
        """The host-side state (no GPU): per-key tables, clocks, the layout key."""
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self._geo = None
        self._lay: dict = {}
        self._crs = np.zeros((128, _N_CRS), np.float32)
        self._pal_name = None
        self._pal = np.zeros(12, np.float32)
        self._host_tables()
        self.clock = 0.0
        self.breath = 0.0
        self.drift = 0.0
        self.wander = 0.0
        self.glint = 0.0
        self.shimmer = 0.0
        self.kick = 0.0
        self.soft = 0.0
        self.flare = 0.0

    # -- geometry -----------------------------------------------------------------------------------------------
    def _geometry(self, p: dict, Ph: np.ndarray) -> None:
        """Rebuild the course table and redraw the board when the geometry changes (structural params; rare)."""
        if not self._relayout(p):
            return
        self.crs.set(self._crs)
        self._fill_geo(Ph)
        self.P.set(np.array(Ph, np.float32))              # the board kernel reads the geometry from P
        launch_2d(self.k_board, self.w, self.h, (self.brd, self.P, self.crs, np.uint32(self.seed)))

    def _relayout(self, p: dict) -> bool:
        """Recompute the host course table if the geometry params changed; True if they did."""
        key = (round(float(p["perspective"]), 4), round(float(p["overstrung"]), 4))
        if key == self._geo:
            return False
        crs, lay = _layout(self.W / self.H, key[0])
        lay["tilt"] = 0.22 * key[1]
        self._crs[:] = crs
        self._key_tables()
        self._lay = lay
        self._geo = key
        return True

    def _fill_geo(self, Ph: np.ndarray) -> None:
        lay = self._lay
        Ph[12], Ph[13], Ph[14], Ph[15] = self.w, self.h, self.W, self.H
        Ph[16], Ph[17], Ph[18] = V0, lay["k"], lay["tilt"]
        Ph[19], Ph[20], Ph[21], Ph[22] = lay["xt0"], lay["spt"], lay["xb0"], lay["spb"]
        Ph[44] = self.W / self.H

    # -- the sound ----------------------------------------------------------------------------------------------
    def _host_tables(self) -> None:
        """Static per-key tables and scratch for _shape (setup; the course hashes after each layout)."""
        keys = np.arange(128)
        self.sym = np.zeros(128)
        self.lift = np.zeros(128)
        self._top = (keys >= UNDAMPED_FROM).astype(np.float64)    # no dampers: always free to ring
        self._symM = np.zeros((128, 128))                 # sympathy: partners a partial apart, both ways
        for o, wgt in _SYM:
            self._symM[keys[o:], keys[:-o]] = wgt
            self._symM[keys[:-o], keys[o:]] = wgt
        self._silent = np.zeros((128, 4), np.float32)     # the notes table when nothing is heard
        self._silent[:, 2:] = np.inf
        self._K = np.zeros((_N_KEY, 128))                 # the per-key terms, one row each (copied to S as K.T)
        self._x = np.zeros((5, 128))
        self._ph = np.zeros((3, 128))
        self._tv = np.zeros((3, 1))
        self._rates = np.array([1.0 / 0.09, 1.0 / 0.5, 1.8, 3.5])[:, None]
        self._a, self._b, self._c, self._d = (np.zeros(128) for _ in range(4))
        self._t_fifths = ((keys * 7) % 12) / 12.0
        self._t_register = (keys - 21) / 87.0
        g = np.clip(self._t_register, 0.0, 1.0)[:, None]
        self._gold = ((1.0 - g) * np.array([1.00, 0.52, 0.20]) + g * np.array([1.00, 0.80, 0.48])).T.copy()
        self._hands = np.where((keys < 60)[:, None], np.array([0.30, 0.72, 1.00]), np.array([1.00, 0.74, 0.26])).T.copy()

    def _key_tables(self) -> None:
        """Per-course hashes as phase rates (after each layout): unison beating, idle glints, sympathy flicker."""
        hs = self._crs[:, 7].astype(np.float64)
        tau = 2.0 * np.pi
        self._pa = np.stack([(0.5 + 1.1 * hs), (0.05 + 0.04 * hs), (1.1 + 0.6 * hs)]) * tau
        self._pb = np.stack([5.0 * hs, 7.3 * hs, 3.0 * hs]) * tau
        self._ga, self._gb = 0.05 + 0.05 * hs, 5.1 * hs           # where the idle glint is along its string
        self._sa = 0.9 + 0.3 * hs                                  # the glow's ripple phase rate
        c = self._crs
        self._r0 = (c[:, 11] + 1.2 * c[:, 4]).astype(np.float64)   # reach: strings + margin ...
        self._r1 = (1.3 * c[:, 2]).astype(np.float64)              # ... + swing

    def _shape(self, dt: float, S: np.ndarray, p: dict, e: float, mode: int, Ph: np.ndarray) -> float:
        """Every per-key term into ``S`` (128 x _N_KEY): a few dozen numpy calls over the 128 keys, no Python loop;
        returns the soft pedal (0-1)."""
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        a_sym = 1.0 - math.exp(-dt / 0.12) if dt > 0 else 1.0
        a_lift = 1.0 - math.exp(-dt / 0.05) if dt > 0 else 1.0
        if f is None:
            n, damper, soft = self._silent, 0.0, 0.0
        else:
            n, damper, soft = f.notes, float(f.pedals[0]), float(f.pedals[1])
        K, x, a, b, c, d = self._K, self._x, self._a, self._b, self._c, self._d
        lv, und, s = a, b, c
        np.maximum(n[:, 0], 0.0, out=lv)
        np.maximum(n[:, 3] < 0.0, damper, out=und)       # dampers up: the pedal or the key held
        # sympathy: partners a partial apart; undamped strings answer, a string sounding itself is busy
        np.matmul(self._symM, lv, out=s)
        np.minimum(s, 1.0, out=s)
        np.maximum(und, self._top, out=d)
        s *= d
        np.multiply(lv, -3.0, out=d)
        d += 1.0
        np.maximum(d, 0.0, out=d)
        s *= d
        s -= self.sym
        s *= a_sym
        self.sym += s
        np.subtract(und, self.lift, out=d)
        d *= a_lift
        self.lift += d
        sym = K[0]
        np.multiply(self.sym, float(p["sympathy"]), out=sym)
        K[1] = self.lift
        # the strike: a flash that blooms in (the strike lands late), its hot core, the glints running from it, the
        # partials ringing briefly
        age, vel = K[6], b
        np.minimum(n[:, 2], 1e3, out=age)
        vel[:] = n[:, 1]
        np.multiply(vel, 0.22, out=x[0])
        x[0] += 0.10
        np.divide(age, x[0], out=x[0])
        np.multiply(self._rates, age, out=x[1:])
        np.negative(x, out=x)
        np.exp(x, out=x)
        np.multiply(age, 1.0 / 0.03, out=c)              # the bloom: smoothstep over 30 ms
        np.minimum(c, 1.0, out=c)
        np.multiply(c, -2.0, out=d)
        d += 3.0
        d *= c
        c *= d                                           # c = bloom
        np.multiply(vel, 0.7, out=d)
        d += 0.3                                         # d = 0.3 + 0.7 vel
        fl = float(p["flash"])
        np.multiply(d, c, out=K[5])
        K[5] *= x[2]
        K[5] *= 0.8 * fl * (1.0 - 0.4 * soft)
        np.multiply(d, 0.5, out=K[4])
        K[4] += 0.5
        K[4] *= d
        K[4] *= c
        K[4] *= x[0]
        K[4] *= 1.4 * fl * (1.0 - 0.5 * soft)
        np.multiply(K[4], x[1], out=K[7])
        np.multiply(x[3], 0.55, out=K[10])
        np.multiply(x[4], 0.40, out=K[11])
        # swing and glow follow the sounding level; the unison's beating breathes the swing
        tv = self._tv
        tv[0, 0], tv[1, 0], tv[2, 0] = self.clock, float(Ph[42]), float(Ph[43])
        ph = self._ph
        np.multiply(self._pa, tv, out=ph)
        ph += self._pb
        np.sin(ph, out=ph)
        glow = K[3]
        np.sqrt(lv, out=glow)
        np.multiply(ph[0], 0.09, out=d)
        d += 1.0
        d *= glow
        d *= float(p["vibration"]) * 1.1 * (0.8 + 0.4 * e) * (1.0 + 0.25 * self.kick)
        np.sqrt(sym, out=K[2])
        K[2] *= 0.10
        K[2] += d
        np.multiply(glow, 0.30 * (1.0 + 0.5 * self.kick), out=K[19])
        np.multiply(sym, 0.2, out=d)
        K[19] += d
        K[19] *= float(p["glow"])
        # idle glints sliding along the strings, the glow's ripple, the sympathetic flicker
        np.subtract(ph[1], 0.8, out=d)
        np.maximum(d, 0.0, out=d)
        d *= d
        np.multiply(d, 25.0 * float(Ph[36]), out=K[12])
        np.multiply(self._ga, tv[1, 0], out=d)
        d += self._gb
        np.mod(d, 2.0, out=d)
        d -= 1.0
        np.abs(d, out=d)
        np.subtract(1.0, d, out=K[13])
        np.multiply(self._sa, tv[2, 0], out=d)
        np.mod(d, 1.0, out=K[14])
        np.multiply(ph[2], 0.3, out=K[15])
        K[15] += 0.3
        K[15] *= sym
        K[16:19] = self._colours(mode, float(p["palette_period"]), float(Ph[25]))
        # each course's reach (world) and whether it emits any light
        np.multiply(self._r1, K[2], out=K[8])
        K[8] += self._r0
        np.add(sym, glow, out=d)
        d += K[4]
        d += K[5]
        np.greater(d, 1e-3, out=K[9])
        S[:] = K.T
        return soft

    def _colours(self, mode: int, period: float, drift: float) -> np.ndarray:
        """Each key's light colour (3 x 128): gold, hands, or the palette around the fifths / up the register."""
        if mode == 0:
            return self._gold
        if mode == 1:
            return self._hands
        t = (self._t_fifths if mode == 2 else self._t_register) * period + drift
        c = self._pal[:, None]
        col = np.cos(2.0 * np.pi * (c[6:9] * t + c[9:12]))
        col *= c[3:6]
        col += c[0:3]
        np.minimum(np.maximum(col, 0.0, out=col), 1.0, out=col)
        col /= np.maximum(col.max(axis=0), 0.25)          # full brightness: the glow carries the level
        return col

    # -- launches -----------------------------------------------------------------------------------------------
    def _launch(self, out) -> None:
        """The frame's static launch sequence (captured into a graph, or issued directly).  Tall, narrow blocks:
        the strings are near-vertical, so a warp's lanes (4 x 8 px for the strings) mostly agree on whether they are
        on a string, and the string shading diverges far less than in 32-px-wide warps."""
        self.k_soft(((self.w + 7) // 8, (self.h + 31) // 32, 1), (8, 32, 1),
                    (self.buf, self.P, self.hd, self.crs, self.brd))
        self.k_comp(((self.W + 3) // 4, (self.H + 63) // 64, 1), (4, 64, 1),
                    (out, self.buf, self.P, self.hd, self.crs))

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
                s.begin_capture()
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
            _log_once("capture", "piano_strings: CUDA graph capture failed (%s: %s); launching directly",
                      type(exc).__name__, exc)
            return None
        self._graphs[key] = g
        if len(self._graphs) > _GRAPHS:
            self._graphs.popitem(last=False)
        return g

    # -- render -------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        cp = self.ctx.cp
        p = self.step_params(dt)
        i = self._slot
        ev = self._ev[i]
        calls = 1
        if ev.done:
            self._slot = (i + 1) % _RING
        else:
            ev, i = None, _RING
        hb, Ph, S = self._views[i]

        self._geometry(p, Ph)
        self._host_frame(dt, p, Ph, S)

        stream = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.arena.data.ptr, hb.ctypes.data, hb.nbytes, rt.memcpyHostToDevice, stream.ptr)
        calls += 1
        if ev is not None:
            ev.record(stream)
            calls += 1
        g = self._graph(out) if self._mode == "graph" else None
        if g is not None:
            g.launch(stream)
            calls += 1
        else:
            self._launch(out)
            calls += 2
        self.calls = calls

    def _host_frame(self, dt: float, p: dict, Ph: np.ndarray, S: np.ndarray) -> None:
        """This frame's params (Ph) and per-key terms (S), host only."""
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt
        a = 1.0 - math.exp(-dt / 0.12) if dt > 0 else 1.0
        self.kick += (pulse - self.kick) * a
        self.flare += (pulse - self.flare) * (1.0 - math.exp(-dt / 0.6) if dt > 0 else 1.0)
        self.breath += (0.16 + 0.22 * e) * spd * dt
        self.wander += (0.018 + 0.03 * e) * spd * dt
        self.glint += spd * dt
        self.shimmer += (0.8 + 0.8 * e) * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt

        name = p["palette"]
        if name != self._pal_name:
            self._pal[:] = palette_coeffs(name)
            self._pal_name = name
        mode = COLOURS.index(p["colours"])
        Ph[0:12] = self._pal
        self._fill_geo(Ph)
        Ph[23] = mode
        Ph[24] = float(p["palette_period"])
        Ph[25] = self.drift % 10.0
        Ph[26] = self.clock % 1000.0
        Ph[27] = e
        Ph[28] = self.kick
        Ph[29] = float(p["intensity"])
        Ph[30] = float(p["glow"])
        Ph[36] = 0.5 + 0.7 * e
        Ph[37] = 0.72 + 0.28 * math.sin(2.0 * math.pi * self.breath / 6.0)
        w = self.wander
        Ph[38] = 0.5 * Ph[44] + 0.5 * math.sin(w * 1.7 + 0.4)
        Ph[39] = 0.55 + 0.25 * math.sin(w * 1.13 + 1.9)
        Ph[40] = 0.5 * Ph[44] + 0.7 * math.sin(w * 0.77 + 3.1)
        Ph[41] = 0.45 + 0.3 * math.cos(w * 1.41 + 0.2)
        Ph[42] = self.glint % 1000.0
        Ph[43] = self.shimmer % 1000.0
        if mode == 0:
            lc = (1.0, 0.80, 0.58)
        else:                                             # the room light leans towards the palette
            c = self._pal
            tint = [min(max(c[k] + c[3 + k] * math.cos(2 * math.pi * (c[6 + k] * Ph[25] + c[9 + k])), 0.0), 1.0)
                    for k in range(3)]
            lc = tuple(0.7 * b + 0.3 * (0.4 + 0.6 * t) for b, t in zip((1.0, 0.82, 0.64), tint))
        Ph[45], Ph[46], Ph[47] = lc
        room = float(p["room_light"])
        Ph[48] = (0.10 + 0.08 * e) * room
        Ph[49] = float(p["dampers"])
        Ph[50] = (0.62 + 0.4 * e) * (1.0 + 0.3 * self.flare) * room
        sg = 0.55 * (0.8 + 0.5 * e) * (1.0 + 0.35 * self.kick)
        Ph[51] = 1.0 / (sg * sg)
        Ph[52] = 1.0 / 0.0035                            # the strike streak's half height (world)
        soft = self._shape(dt, S, p, e, mode, Ph)
        self.soft += (soft - self.soft) * a
        Ph[35] = self.soft

    def close(self) -> None:
        self._graphs = OrderedDict()
        self._cap = self._ev = None
        self.buf = self.brd = self.P = self.hd = self.arena = self.crs = None
