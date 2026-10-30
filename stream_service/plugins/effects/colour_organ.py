"""Colour organ: the playing lights the picture in two voices of coloured light, after Scriabin's luce.

WHAT YOU SEE
Over any scene or art still, coloured light plays across the picture the way a lighting desk plays a stage, but
the picture is never painted over: its brightness and detail stay as they were, and only its colour moves.  It
shows two lights, as in the light part (luce) that Scriabin wrote into Prometheus, plus a footlight:
  - The GROUND is a slow wash in the colour of the key.  The colour wheel follows the circle of fifths, by the same
    rule as keyscape_live: C is a warm rose-red, G a step on towards orange, D amber, and so round, with F# opposite
    C, a cool blue-green.  A move to a neighbouring key is a small, slow change of colour; a move to a distant key
    is a big one.  It glides to a new key over a few seconds, and a minor key shades the picture a touch deeper.
  - The VOICE is a faster light in the colour of the chord sounding now (its root, on the same wheel).
    - It arrives in soft folded veils that drift across the picture, like Thomas Wilfred's Lumia.
    - At every change of chord the new colour rolls out across the picture as a soft wave. The wave starts at the
      bottom edge under the bass, and its brighter crest fades as it travels.
    - With span: room (one picture over the two TVs; the default, span: screen, gives each TV the whole keyboard)
      the wave starts under the bass's place on the keyboard (low notes on the left screen) and rolls on into the
      other screen.
  - The FOOTLIGHT is a soft glow rising from the bottom edge under each sounding key, in that note's colour: broad
    and strong for the bass, small and faint for the treble.  So the bass lights its side of the room from below.

HOW THE MUSIC DRIVES IT
  - Every note makes the light breathe: a short swell, a quickening of the veils, and its own footlight. Each is
    eased in, never a flash.
  - Loud playing makes the light bigger, richer and higher-reaching; soft playing leaves a tender tint.
  - The damper pedal is resonance: the light lingers, the wave's edge softens and the footlights rise higher.
  - A melody with no chords tints the voice towards its notes' colours. This is Malinowski's harmonic colouring:
    colours are mixed around the circle of fifths, so a clear chord gives a rich colour and a cluster a pale one.
  - A new phrase after a rest sends a wave of its own.
  - Under the ensemble's register split (sound.register) each screen's voice and footlights follow its own register.
    The other notes count at the window's "outside" level, never zero.
With no music (no sound at all, or 20 s of silence) it becomes a slow lumia, alive but quiet: the ground drifts
round the colour wheel (one fifth every 25 s or so) and the veils keep folding in a second, drifting colour.

MACROS
  amount     0 is the input exactly (a copy).  Between 0 and 1 it dims both lights and the footlight together, a
             smooth morph from nothing to the full grade.
  pulse      the chord bloom (bind it to note_on). The voice swells and the wave's crest brightens, eased in.
  energy     the veils' and the wave's speed, the voice's strength and the footlight's reach.
  Regional:  ground (key light), voice (chord light), leak (footlight), saturation (how coloured the light is; low
             also pales the picture towards the light), veils (a flat wash, or Wilfred's folded veils), and
             hue_shift (turns the whole wheel, from 0 to 1 = 0 to 180 degrees).

THE GRADE (per pixel, in OKLab)
The pixel's own colour (a, b) is turned towards the light's hue by a smooth flow on the hue circle.  The flow is
tan(phi'/2) = tan(phi/2) exp(-k z), where phi is the pixel's hue offset from the light's and z the light's local
strength.  It is continuous everywhere: no seam at the opposite hue, which stays put, neutralised by the light.
The pixel's chroma is kept (scaled a little by saturation).  Then the light's own colour is added where the
picture has little colour of its own, faded out at black and white.  L, the lightness, is unchanged apart from a
minor key's slight shade and the footlight's soft lift L += lift (1 - L).  Out-of-gamut results move towards the
grey of the same L, never clipped per channel.

GPU: three launches.
  1. co_strip: 256 threads read notes_gpu straight from the GPU and build the footlight strip across the screen,
     each key a gaussian.
  2. co_field: the light field at 1/4 resolution (float4: the light's chroma vector, the strength z and the
     luminance lift).  It holds the veils (analytic, 7 __sinf), the ground, up to 8 chord waves and the footlight.
  3. co_grade: the full-size grade.  Per pixel: a 256-entry sRGB decode table, the bilinear field, the OKLab
     round trip (__powf cube roots), the hue flow and a gamut fit.
Estimated cost (P4000, 4K): about 1.2-1.5 ms, nearly all in co_grade.  Not yet measured: it has been checked
without a GPU only.  Pixels with no light on them are copied.  Host: about 0.15 ms of numpy on 12- and
128-vectors and a 112-float upload.  No feedback buffers.  dt = 0 changes nothing; NaN, negative or huge dt is
clamped.  Bursts of onsets are capped at 64 per frame (only the loudest velocity counts).  sound None is the idle
lumia.

Source: Original (inspired by Scriabin's luce for Prometheus and its clavier a lumieres,
        https://en.wikipedia.org/wiki/Clavier_%C3%A0_lumi%C3%A8res; Thomas Wilfred's Lumia,
        https://en.wikipedia.org/wiki/Thomas_Wilfred; Stephen Malinowski's Harmonic Coloring,
        http://www.musanim.com/HarmonicColoring.  No code from any of them; the key colour rule is keyscape_live's.)
Author: smfx (original)
License: MIT (original)
license_class: permissive
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import enum, num, standard_effect_params

NS = 256                      # footlight strip bins across the screen
NF = 8                        # chord waves in flight
DS = 4                        # light-field downscale
H0_DEG = 32.0                 # keyscape_live's hue of C (OKLCh degrees); G is +30, D +60 ...
C_KEY, C_VOICE, C_LEAK = 0.10, 0.13, 0.15     # OKLab chroma of the ground, the voice and the footlight
T_MAX = 0.2                   # the light's chroma never exceeds this
FLOW = 1.6                    # hue flow at full strength: exp(-1.6) = 0.2 of tan(phi / 2) is left (90 deg -> 23)
IDLE_S = 20.0                 # silence longer than this returns to the idle lumia
PHRASE_GAP_S = 1.5            # a note after this much quiet starts a phrase (a wave of its own)
MERGE_S = 0.25                # chord changes closer than this ride one wave
HURRY = 4.0                   # the oldest wave's speed-up when the slots run short
WANDER = math.radians(30.0) / 25.0            # idle ground drift (rad/s at energy 0.5)
IDLE_OFFSET = math.radians(150.0)             # idle voice hue, from the ground's
KEY_EASE_S = 2.5
VEIL_RATES = (0.11, 0.07, 0.38, 0.09, 0.29)  # rad/s (x the energy / kick multiplier)
GROUND_RATE = 0.05
TAU = 2.0 * math.pi

# P layout (floats)
P_A = 0                       # aspect W / H
P_GA, P_GB, P_GW = 1, 2, 3    # ground light vector (chroma x strength), ground strength
P_VS, P_RING = 4, 5           # voice strength, wave-crest gain
P_VEILS, P_WF = 6, 7          # veils 0..1, wave edge half-width (H units)
P_BA, P_BB = 8, 9             # the chord colour beyond every wave
P_PH = 10                     # 10..15 veil phases (5) + the ground's
P_LC, P_LW, P_LL, P_REACH = 16, 17, 18, 19   # footlight: chroma gain, strength, lift, reach (H units)
P_STRIP = 20                  # 1: the strip is valid
P_FLOW, P_SAT, P_SHADE, P_RL = 21, 22, 23, 24   # hue flow, source-chroma slope, minor shade, crest lift
P_X0, P_SPAN = 25, 26         # the strip's extent (local, H units)
P_LO, P_HI, P_OUT = 27, 28, 29                  # register window
P_SK, P_SN = 30, 31           # segment (k, n)
P_FLARE = 32                  # a strike's footlight flare
P_FR = 40                     # waves: NF x (ox, oy, rho, a, b, crest), rho < 0 = empty
P_HUE = P_FR + 6 * NF         # 12 x (cos, sin): each pitch class's hue
NP = P_HUE + 24

# host wave rows
F_OX, F_OY, F_RHO, F_A, F_B, F_RING, F_AGE, F_HURRY = range(8)

_DEFS = "\n".join(f"#define {k} {v}" for k, v in dict(
    CO_NS=NS, CO_NF=NF, CO_TMAX=f"{T_MAX}f", CP_A=P_A, CP_GA=P_GA, CP_GB=P_GB, CP_GW=P_GW, CP_VS=P_VS,
    CP_RING=P_RING, CP_VEILS=P_VEILS, CP_WF=P_WF, CP_BA=P_BA, CP_BB=P_BB, CP_PH=P_PH, CP_LC=P_LC, CP_LW=P_LW,
    CP_LL=P_LL, CP_REACH=P_REACH, CP_STRIP=P_STRIP, CP_FLOW=P_FLOW, CP_SAT=P_SAT, CP_SHADE=P_SHADE, CP_RL=P_RL,
    CP_X0=P_X0, CP_SPAN=P_SPAN, CP_LO=P_LO, CP_HI=P_HI, CP_OUT=P_OUT, CP_SK=P_SK, CP_SN=P_SN, CP_FLARE=P_FLARE,
    CP_FR=P_FR, CP_HUE=P_HUE).items())

_SRC = HELPERS + _DEFS + r"""

// 1. The footlight strip: one thread per bin across this screen; every key a gaussian of light under its place on
//    the keyboard (spanning the ensemble's screens), coloured by its pitch class, broad and strong in the bass.
extern "C" __global__
void co_strip(const float* __restrict__ notes, float4* __restrict__ strip, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= CO_NS) return;
    const float A = P[CP_A];
    const float x = P[CP_X0] + ((float)i + 0.5f) * (P[CP_SPAN] / (float)CO_NS);
    const float lo = P[CP_LO], hi = P[CP_HI], outside = P[CP_OUT];
    const float sk = P[CP_SK], sn = P[CP_SN], flare = P[CP_FLARE];
    float sa = 0.f, sb = 0.f, si = 0.f;
    for (int p = 21; p <= 108; ++p) {                     // bounded: the 88 keys
        const float lv = notes[p * 4 + 0], vel = notes[p * 4 + 1];
        float age = notes[p * 4 + 2];
        if (!isfinite(lv) || !isfinite(vel)) continue;
        if (!(age >= 0.f)) age = isinf(age) ? 1e6f : 0.f;   // never struck (inf) / NaN / negative
        age = fminf(age, 1e6f);
        const float bloom = smooth(0.f, 0.1f, age);       // bloom in: the strike lands late, never a flash
        const float amp = (fmaxf(lv, 0.f) + flare * clamp01(vel) * __expf(-2.5f * age)) * bloom;
        if (!(amp > 1e-4f)) continue;
        const float r = (float)(p - 21) * (1.f / 87.f);   // register 0 (A0) .. 1 (C8)
        const float br = 1.f - r;
        const float sig = 0.05f + 0.2f * br;              // H units: the bass broad, the treble fine
        const float xn = (r * sn - sk - 0.5f) * A;        // the key's place on this screen
        const float u = (x - xn) / sig;
        if (fabsf(u) > 4.f) continue;
        const float rw = (r >= lo && r <= hi) ? 1.f : outside;
        const float g = amp * rw * (0.3f + 0.7f * br * sqrtf(br)) * __expf(-0.5f * u * u);
        const int pc = p % 12;
        sa += g * P[CP_HUE + 2 * pc];
        sb += g * P[CP_HUE + 2 * pc + 1];
        si += g;
    }
    strip[i] = make_float4(sa, sb, si, 0.f);
}

// 2. The light field at 1 / DS: (light chroma a, b; strength z; luminance lift).
extern "C" __global__
void co_field(const float4* __restrict__ strip, float4* __restrict__ field, const int fw, const int fh,
              const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= fw || j >= fh) return;
    const float A = P[CP_A];
    const float px = (((float)i + 0.5f) / (float)fw - 0.5f) * A;     // H units, centred
    const float py = ((float)j + 0.5f) / (float)fh - 0.5f;           // down; the bottom edge is +0.5
    const float* ph = P + CP_PH;
    // Wilfred's veils: two folded curtains drifting through each other
    const float v1 = 0.5f + 0.5f * __sinf(2.2f * (0.85f * px + 0.3f * __sinf(1.6f * py + ph[0]))
                                          + 0.8f * __sinf(1.3f * py - ph[1]) + ph[2]);
    const float v2 = 0.5f + 0.5f * __sinf(1.7f * (1.1f * py - 0.45f * px) + 1.1f * __sinf(1.9f * px + ph[3]) - ph[4]);
    const float vs = smooth(0.15f, 0.95f, 0.6f * v1 + 0.4f * v2);
    const float veils = clamp01(P[CP_VEILS]);
    const float wv = 1.f - veils + veils * (0.1f + 1.25f * vs);
    const float wg = (0.82f + 0.16f * __sinf(0.9f * px - 0.7f * py + ph[5]) - 0.12f * py)
                     * (1.f + 0.25f * veils * (vs - 0.5f));
    // the chord waves, oldest first: inside each its colour, at its edge a crest
    float ca = P[CP_BA], cb = P[CP_BB], crest = 0.f;
    const float wf = fmaxf(P[CP_WF], 1e-3f), iwf = 1.f / wf;
    #pragma unroll
    for (int f = 0; f < CO_NF; ++f) {                     // bounded: NF slots
        const float* F = P + CP_FR + 6 * f;
        const float rho = F[2];
        if (!(rho >= 0.f)) continue;
        const float dx = px - F[0], dy = py - F[1];
        const float d = sqrtf(dx * dx + dy * dy);
        const float ins = 1.f - smooth(rho - wf, rho + wf, d);
        ca += (F[3] - ca) * ins;
        cb += (F[4] - cb) * ins;
        const float q = (d - rho) * iwf;
        crest += F[5] * __expf(-q * q);
    }
    const float vstr = P[CP_VS] * wv + P[CP_RING] * crest;
    float ta = P[CP_GA] * wg + ca * vstr, tb = P[CP_GB] * wg + cb * vstr;
    float z = P[CP_GW] * wg + vstr;
    float ll = P[CP_RL] * crest;
    // the footlights
    if (P[CP_STRIP] > 0.5f) {
        const float sx = (px - P[CP_X0]) / fmaxf(P[CP_SPAN], 1e-3f) * (float)CO_NS - 0.5f;
        const float cx = fminf(fmaxf(sx, 0.f), (float)(CO_NS - 1));
        const int s0 = (int)cx, s1 = min(s0 + 1, CO_NS - 1);
        const float t = cx - (float)s0;
        const float4 a = strip[s0], b = strip[s1];
        const float I = a.z + (b.z - a.z) * t;
        if (I > 1e-5f) {
            const float fy = __expf(-(0.5f - py) / fmaxf(P[CP_REACH], 0.02f));
            const float ic = (1.f - __expf(-1.2f * I)) * fy;
            const float k = P[CP_LC] * ic / I;            // the notes' mixed colour (|.| <= 1) x chroma x light
            ta += (a.x + (b.x - a.x) * t) * k;
            tb += (a.y + (b.y - a.y) * t) * k;
            z += P[CP_LW] * ic;
            ll += P[CP_LL] * ic;
        }
    }
    const float tc = sqrtf(ta * ta + tb * tb);
    if (tc > CO_TMAX) { const float k = CO_TMAX / tc; ta *= k; tb *= k; }
    if (!isfinite(ta + tb + z + ll)) { ta = tb = z = ll = 0.f; }
    field[j * fw + i] = make_float4(ta, tb, clamp01(z), fminf(fmaxf(ll, 0.f), 0.5f));
}

__device__ __forceinline__ float co_cbrt(float v) { return v > 0.f ? __powf(v, 0.333333333f) : 0.f; }
__device__ __forceinline__ float co_enc(float v) {
    v = clamp01(v);
    return v <= 0.0031308f ? 12.92f * v : 1.055f * __powf(v, 1.f / 2.4f) - 0.055f;
}
// how far the colour may go from the grey of its own L before channel v leaves [0, 1]
__device__ __forceinline__ float co_fit(float v, float yg, float k) {
    if (v < 0.f) k = fminf(k, yg / fmaxf(yg - v, 1e-9f));
    else if (v > 1.f) k = fminf(k, (1.f - yg) / fmaxf(v - yg, 1e-9f));
    return fmaxf(k, 0.f);
}

// 3. The grade at full size: keep L, flow the hue towards the light, add the light's colour where the picture has
//    little of its own.
extern "C" __global__
void co_grade(const uchar4* __restrict__ src, const float4* __restrict__ field, const float* __restrict__ lut,
              uchar4* __restrict__ out, const int W, const int H, const int fw, const int fh,
              const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int idx = y * W + x;
    const uchar4 q = src[idx];
    const float fx = fminf(fmaxf(((float)x + 0.5f) * ((float)fw / (float)W) - 0.5f, 0.f), (float)(fw - 1));
    const float fy = fminf(fmaxf(((float)y + 0.5f) * ((float)fh / (float)H) - 0.5f, 0.f), (float)(fh - 1));
    const int x0 = (int)fx, y0 = (int)fy, x1 = min(x0 + 1, fw - 1), y1 = min(y0 + 1, fh - 1);
    const float tx = fx - (float)x0, ty = fy - (float)y0, u = 1.f - tx, v = 1.f - ty;
    const float4 f00 = __ldg(field + y0 * fw + x0), f01 = __ldg(field + y0 * fw + x1);
    const float4 f10 = __ldg(field + y1 * fw + x0), f11 = __ldg(field + y1 * fw + x1);
    const float ta = v * (u * f00.x + tx * f01.x) + ty * (u * f10.x + tx * f11.x);
    const float tb = v * (u * f00.y + tx * f01.y) + ty * (u * f10.y + tx * f11.y);
    const float z = v * (u * f00.z + tx * f01.z) + ty * (u * f10.z + tx * f11.z);
    const float ll = v * (u * f00.w + tx * f01.w) + ty * (u * f10.w + tx * f11.w);
    if (!(z > 1e-4f) && !(ll > 1e-5f)) { out[idx] = make_uchar4(q.x, q.y, q.z, 255); return; }   // no light here

    const float r = __ldg(lut + q.x), g = __ldg(lut + q.y), b = __ldg(lut + q.z);
    const float l_ = co_cbrt(0.4122214708f * r + 0.5363325363f * g + 0.0514459929f * b);
    const float m_ = co_cbrt(0.2119034982f * r + 0.6806995451f * g + 0.1073969566f * b);
    const float s_ = co_cbrt(0.0883024619f * r + 0.2817188376f * g + 0.6299787005f * b);
    float L = 0.2104542553f * l_ + 0.7936177850f * m_ - 0.0040720468f * s_;
    const float a = 1.9779984951f * l_ - 2.4285922050f * m_ + 0.4505937099f * s_;
    const float bb = 0.0259040371f * l_ + 0.7827717662f * m_ - 0.8086757660f * s_;
    const float C = sqrtf(a * a + bb * bb);
    const float tc = sqrtf(ta * ta + tb * tb);
    float na = a, nb = bb;
    if (tc > 1e-6f) {
        const float itc = 1.f / tc, ux = ta * itc, uy = tb * itc;          // the light's hue
        if (C > 1e-6f) {
            // hue flow: tan(phi'/2) = tan(phi/2) exp(-FLOW z), by half angles (phi: the pixel's hue from the light's)
            const float iC = 1.f / C, sa = a * iC, sb = bb * iC;
            const float cphi = ux * sa + uy * sb, sphi = ux * sb - uy * sa;
            const float num = __expf(-z * P[CP_FLOW]) * sphi, den = 1.f + cphi;
            const float r2 = num * num + den * den;
            float c2 = -1.f, s2 = 0.f;                    // the opposite hue stays put
            if (r2 > 1e-12f) { const float ir = 1.f / r2; c2 = (den * den - num * num) * ir; s2 = 2.f * num * den * ir; }
            const float Cg = C * fmaxf(1.f + z * P[CP_SAT], 0.f);
            na = (ux * c2 - uy * s2) * Cg;
            nb = (uy * c2 + ux * s2) * Cg;
        }
        // the light's own colour, where the picture has little; faded out at black and white
        const float guard = smooth(0.f, 0.22f, L) * (1.f - smooth(0.88f, 1.f, L));
        const float add = guard * (1.f - 0.6f * smooth(0.02f, 0.18f, C));
        na += ta * add;
        nb += tb * add;
    }
    L = L * (1.f - P[CP_SHADE] * z);
    L += ll * (1.f - L);
    const float l2 = L + 0.3963377774f * na + 0.2158037573f * nb;
    const float m2 = L - 0.1055613458f * na - 0.0638541728f * nb;
    const float s2 = L - 0.0894841775f * na - 1.2914855480f * nb;
    const float l3 = l2 * l2 * l2, m3 = m2 * m2 * m2, s3 = s2 * s2 * s2;
    float R = 4.0767416621f * l3 - 3.3077115913f * m3 + 0.2309699292f * s3;
    float G = -1.2684380046f * l3 + 2.6097574011f * m3 - 0.3413193965f * s3;
    float B = -0.0041960863f * l3 - 0.7034186147f * m3 + 1.7076147010f * s3;
    const float Lc = clamp01(L), yg = Lc * Lc * Lc;                    // the grey of the same L
    float k = 1.f;
    k = co_fit(R, yg, k); k = co_fit(G, yg, k); k = co_fit(B, yg, k);
    R = yg + (R - yg) * k; G = yg + (G - yg) * k; B = yg + (B - yg) * k;
    if (!isfinite(R + G + B)) { out[idx] = make_uchar4(q.x, q.y, q.z, 255); return; }
    out[idx] = make_uchar4((unsigned char)(255.f * co_enc(R) + 0.5f), (unsigned char)(255.f * co_enc(G) + 0.5f),
                           (unsigned char)(255.f * co_enc(B) + 0.5f), 255);
}
"""
_OPTS = ("--std=c++14", "--use_fast_math")


def _srgb_lut() -> np.ndarray:
    c = np.arange(256, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def _fin(x, d: float = 0.0) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return d
    return x if math.isfinite(x) else d


def _c01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _wrap(a: float) -> float:
    """An angle into (-pi, pi]."""
    return a - TAU * math.floor((a + math.pi) / TAU)


def _ar(x: float, tgt: float, dt: float, att: float, rel: float) -> float:
    """Ease ``x`` toward ``tgt``: time constant ``att`` rising, ``rel`` falling (dt = 0: no change)."""
    return x + (tgt - x) * T.ema_alpha(dt, att if tgt > x else rel)


SPANS = ("screen", "room")    # screen: each TV the whole picture (default; owner, 30 Oct: the TVs aren't adjacent);
                              # room: one picture across the room's screens (sound.segment)


class ColourOrgan(Effect):
    name = "colour_organ"
    stage = "colour"
    label = "Colour organ"
    description = ("Scriabin's two lights over any picture: a slow wash in the key's colour and folded veils in the "
                   "chord's, rolling out as a wave at each chord change, with footlights under the sounding keys; "
                   "the picture's brightness and detail are kept.")
    listens = ("notes",)
    tags = {"family": ["colour"], "dims": ["2d"], "technique": ["colour", "light"]}
    params = {
        **standard_effect_params(),
        "energy": num(0.0, 1.0, 0.5, "Energy", tier="realtime", bind="level"),
        "ground": num(0.0, 1.0, 0.5, "Key light (the slow ground)", tier="regional", basic=True),
        "voice": num(0.0, 1.0, 0.7, "Chord light (the fast voice)", tier="regional", basic=True),
        "leak": num(0.0, 1.0, 0.45, "Footlight (the bass's light leak)", tier="regional"),
        "saturation": num(0.2, 1.6, 1.0, "Light saturation", tier="regional"),
        "veils": num(0.0, 1.0, 0.6, "Veils (flat wash · folded lumia veils)", tier="regional"),
        "hue_shift": num(0.0, 1.0, 0.0, "Hue shift (0..1 = 0..180 degrees)", tier="regional"),
        "span": enum(SPANS, "screen", "Span (this screen, or one picture across the room)"),
    }
    params["amount"] = {**params["amount"], "default": 0.7}

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.span_room = params.get("span") == "room"
        mod = raw_module(_SRC, _OPTS)
        self._ks = mod.get_function("co_strip")
        self._kf = mod.get_function("co_field")
        self._kg = mod.get_function("co_grade")
        W, H = int(ctx.width), int(ctx.height)
        self.fw, self.fh = max((W + DS - 1) // DS, 1), max((H + DS - 1) // DS, 1)
        self.field = cp.zeros((self.fh, self.fw, 4), cp.float32)
        self.strip = cp.zeros((NS, 4), cp.float32)
        self.lut = cp.asarray(_srgb_lut())
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        # listening
        self.lv = np.zeros(128, np.float32)
        self.e = np.zeros(12, np.float32)                 # this screen's pc energy (register-weighted)
        self.e_all = np.zeros(12, np.float32)             # the room's (the key)
        self.ease = T.AttackRelease(12, 0.08, 0.35)
        self.keys = T.KeyResolver(hold_s=0.5)
        self.chords = T.ChordReader()
        self._pc_ang = np.radians(H0_DEG + 30.0 * ((7 * np.arange(12)) % 12))
        self.hue_u = np.zeros((12, 2))
        self._reg_key = None
        self._reg_w = None
        self._seen = None
        self._reset()

    def _reset(self) -> None:
        """The listening state from cold (also after a NaN)."""
        self.ease.reset()
        self.quiet_s = IDLE_S + 1.0
        self.ever = False
        self.damp = self.loud = self.kick = self.kick_raw = self.bloom = self.bloom_raw = 0.0
        self.gh = math.radians(H0_DEG)                    # the ground's hue (rad)
        self.gpres = 0.7
        self.minor = 0.0
        self.kconf = 0.5
        self.vt = np.array([math.cos(self.gh + IDLE_OFFSET), math.sin(self.gh + IDLE_OFFSET)]) * C_VOICE * 0.7
        self.vcol = self.vt.copy()                        # the voice colour now (eased; unsaturated chroma)
        self.base = self.vcol.copy()                      # beyond every wave
        self.fr = np.zeros((NF, 8))
        self.nf = 0
        self.ph = np.zeros(6)
        self.breath = 0.0

    # ------------------------------------------------------------------------------------------------ host side
    def _reg_weights(self, snd):
        reg = getattr(snd, "register", None) if snd is not None else None
        if not reg:
            return None
        if self._reg_key != reg:
            try:
                from stream_service.ensemble import note_weights
                w = np.asarray(note_weights(reg, np.arange(128)), np.float32)
                self._reg_w = w if w.shape == (128,) and np.all(np.isfinite(w)) else None
            except Exception:  # noqa: BLE001  (a malformed window draws everything at full)
                self._reg_w = None
            self._reg_key = reg
        return self._reg_w

    def _segment(self, snd) -> tuple[int, int]:
        seg = getattr(snd, "segment", None) if snd is not None and self.span_room else None
        try:
            k, n = (int(seg[0]), int(seg[1])) if seg is not None else (0, 1)
        except Exception:  # noqa: BLE001
            k, n = 0, 1
        return (k, n) if n >= 1 and 0 <= k < n else (0, 1)

    @staticmethod
    def _register(snd) -> tuple[float, float, float]:
        reg = getattr(snd, "register", None) if snd is not None else None
        try:
            lo, hi, out = (float(reg[0]), float(reg[1]), float(reg[2])) if reg else (0.0, 1.0, 1.0)
        except Exception:  # noqa: BLE001
            return 0.0, 1.0, 1.0
        if not (math.isfinite(lo) and math.isfinite(hi) and math.isfinite(out)):
            return 0.0, 1.0, 1.0
        return lo, hi, _c01(out)

    def _spawn(self, ox: float, oy: float, crest: float) -> None:
        """A chord change: a new wave from under the bass (or the newest, if it is younger than MERGE_S, swells)."""
        F = self.fr
        if self.nf > 0 and F[self.nf - 1, F_AGE] < MERGE_S:
            F[self.nf - 1, F_RING] = max(F[self.nf - 1, F_RING], crest)
            return
        if self.nf >= NF:                                 # full (rare): the oldest is folded into the base
            self._retire(0)
        if self.nf >= NF - 2:
            F[0, F_HURRY] = HURRY                         # short of slots: the oldest sweeps out quickly
        row = F[self.nf]
        row[:] = 0.0
        row[F_OX], row[F_OY], row[F_RING], row[F_HURRY] = ox, oy, crest, 1.0
        row[F_A], row[F_B] = self.vcol                    # starts as the colour now; eases to the new chord
        self.nf += 1

    def _retire(self, j: int) -> None:
        """Waves 0..j are gone: wave j covers the screen, so its colour is the base now."""
        F = self.fr
        self.base[:] = F[j, F_A:F_B + 1]
        n = self.nf - (j + 1)
        if n > 0:
            F[:n] = F[j + 1:self.nf].copy()
        self.nf = max(n, 0)

    def _listen(self, p: dict, dt: float, e: float, pulse: float, A: float) -> bool:
        """Advance the listening state; returns whether the footlight strip has notes to read."""
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        heard = bool(fr is not None and getattr(snd, "present", False))
        notes = getattr(fr, "notes", None) if heard else None
        if notes is not None and getattr(notes, "shape", None) != (128, 4):
            notes = None
        damper, kick_v, level_sum, bass = 0.0, 0.0, 0.0, -1
        if notes is not None:
            lv = self.lv
            lv[:] = notes[:, 0]
            np.nan_to_num(lv, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            np.maximum(lv, 0.0, out=lv)
            T.pc_energy(lv, self.e_all)
            w = self._reg_weights(snd)
            if w is not None:
                lv *= w
            T.pc_energy(lv, self.e)
            level_sum = float(lv.sum())
            bass = T.bass_pitch(notes, 0.02)
            try:
                damper = _c01(_fin(fr.pedals[0]))
            except (AttributeError, IndexError, TypeError):
                damper = 0.0
            ons = getattr(fr, "onsets", None)
            if ons is not None and ons is not self._seen:
                for i, ev in enumerate(ons):
                    if i >= 64:                           # bounded: a burst counts by its loudest
                        break
                    try:
                        kick_v = max(kick_v, _c01(_fin(ev[1])))
                    except (TypeError, IndexError):
                        continue
            self._seen = ons
        else:
            self.e.fill(0.0)
            self.e_all.fill(0.0)
        self.damp += (damper - self.damp) * T.ema_alpha(dt, 0.25)
        self.ease.release_s = 0.35 + 1.4 * self.damp
        c = self.ease.step(self.e, dt)
        sc = float(c.sum())
        self.loud = _ar(self.loud, 1.0 - math.exp(-0.7 * level_sum), dt, 0.08, 0.5 + 1.2 * self.damp)
        self.kick_raw = max(self.kick_raw * (math.exp(-dt / 0.45) if dt > 0.0 else 1.0), kick_v)
        self.kick = _ar(self.kick, self.kick_raw, dt, 0.06, 0.3)
        # silence, phrases, idle
        quiet_before = self.quiet_s
        if level_sum > 1e-3:
            self.quiet_s = 0.0
            self.ever = True
        else:
            self.quiet_s += dt
        playing = self.ever and self.quiet_s <= IDLE_S
        phrase = kick_v > 0.0 and quiet_before > PHRASE_GAP_S
        # the key: the room's (else this effect's own tracker), on the circle of fifths
        if notes is not None:
            self.keys.update(self.e_all, dt, getattr(fr, "key", None), getattr(fr, "mode", None))
        hs = math.radians(180.0 * _c01(_fin(p["hue_shift"])))
        if playing and self.keys.known:
            tgt = math.radians(H0_DEG + 30.0 * ((7 * self.keys.tonic) % 12)) + hs
            self.gh += _wrap(tgt - self.gh) * T.ema_alpha(dt, KEY_EASE_S)
            self.minor += ((1.0 if self.keys.minor else 0.0) - self.minor) * T.ema_alpha(dt, 2.0)
            self.kconf += (_c01(_fin(self.keys.conf)) - self.kconf) * T.ema_alpha(dt, 1.0)
        else:
            self.gh += WANDER * (0.5 + e) * dt
            self.minor -= self.minor * T.ema_alpha(dt, 4.0)
            self.kconf += (0.5 - self.kconf) * T.ema_alpha(dt, 4.0)
        self.gh = math.fmod(self.gh, TAU)
        self.gpres += ((1.0 if playing else 0.7) - self.gpres) * T.ema_alpha(dt, 1.5 if playing else 5.0)
        # the voice: the chord's root (as sure as the reader is), else the notes' colours mixed (Malinowski)
        ang = self._pc_ang + hs
        self.hue_u[:, 0] = np.cos(ang)
        self.hue_u[:, 1] = np.sin(ang)
        changed = False
        if notes is not None:
            changed = self.chords.update(self.e, dt, bass % 12 if bass >= 0 else -1)
        tau_v = 0.12
        if playing:
            if sc > 0.02:
                m5 = (c @ self.hue_u) / sc
                ch = self.chords
                conf = _c01(_fin(ch.conf)) if ch.chord >= 0 and ch.sounding else 0.0
                tv = m5 * (1.0 - conf)
                if conf > 0.0:
                    tv = tv + conf * self.hue_u[ch.root]
                mag = math.hypot(float(tv[0]), float(tv[1]))
                if mag > 1e-3:
                    self.vt[:] = tv * (C_VOICE * (0.5 + 0.5 * min(mag, 1.0)) / mag)
        else:
            a = self.gh + IDLE_OFFSET + 0.7 * math.sin(0.37 * self.breath)
            self.vt[:] = (math.cos(a) * C_VOICE * 0.7, math.sin(a) * C_VOICE * 0.7)
            tau_v = 4.0
        self.vcol += (self.vt - self.vcol) * T.ema_alpha(dt, tau_v)
        # the waves: born under the bass, just below the bottom edge; the newest wears the colour now
        k, n = self._segment(snd)
        if playing and (changed or phrase):
            u = min(max((bass - 21) / 87.0, 0.0), 1.0) if bass >= 0 else 0.5
            crest = 0.55 + 0.45 * max(kick_v, self.kick_raw)
            self._spawn((u * n - k - 0.5) * A, 0.54, crest)
            self.bloom_raw = max(self.bloom_raw, crest)
        wf = 0.10 + 0.12 * self.damp
        speed = (0.7 + 0.9 * e + 0.4 * self.kick) * dt
        F = self.fr
        if self.nf > 0:
            act = F[:self.nf]
            act[:, F_RHO] += speed * act[:, F_HURRY]
            act[:, F_AGE] += dt
            if dt > 0.0:
                act[:, F_RING] *= math.exp(-dt / (0.9 + 0.9 * self.damp))
            act[-1, F_A:F_B + 1] = self.vcol
            hx = 0.5 * A
            mx = np.maximum(np.abs(act[:, F_OX] - hx), np.abs(act[:, F_OX] + hx))
            my = np.maximum(np.abs(act[:, F_OY] - 0.5), np.abs(act[:, F_OY] + 0.5))
            done = np.flatnonzero(act[:, F_RHO] - 2.0 * wf > np.hypot(mx, my))
            if done.size:
                self._retire(int(done[-1]))
        else:
            self.base += (self.vcol - self.base) * T.ema_alpha(dt, tau_v)
        # bloom (the chord change and the pulse), veils, breath
        self.bloom_raw = max(self.bloom_raw * (math.exp(-dt / (0.8 + 0.8 * self.damp)) if dt > 0.0 else 1.0),
                             0.8 * pulse)
        self.bloom = _ar(self.bloom, self.bloom_raw, dt, 0.1, 0.25)
        rate = (0.35 + 0.9 * e + 1.4 * self.kick) * (1.0 + 0.3 * self.damp) * dt
        for i, r in enumerate(VEIL_RATES):
            self.ph[i] = math.fmod(self.ph[i] + r * rate, TAU)
        self.ph[5] = math.fmod(self.ph[5] + GROUND_RATE * (0.6 + 0.8 * e) * dt, TAU)
        self.breath = math.fmod(self.breath + TAU / 9.0 * (0.6 + 0.8 * e) * dt, TAU * 100.0)
        self._wf = wf
        self._seg = (k, n)
        if not (math.isfinite(self.loud + self.kick + self.bloom + self.gh + self.damp)
                and np.all(np.isfinite(self.vcol)) and np.all(np.isfinite(self.base))
                and np.all(np.isfinite(F))):
            self._reset()                                 # never let NaN stick
        return notes is not None and getattr(snd, "notes_gpu", None) is not None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = _fin(dt)
        dt = min(dt, 1.0) if dt > 0.0 else 0.0
        W, H = int(self.ctx.width), int(self.ctx.height)
        A = W / max(H, 1)
        amount = _c01(_fin(p["amount"]))
        e = _c01(_fin(p["energy"], 0.5))
        pulse = _c01(_fin(p["pulse"]))
        strip_ok = self._listen(p, dt, e, pulse, A)        # always: a fade-in finds the state current
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = amount * amount * (3.0 - 2.0 * amount)
        ground = _c01(_fin(p["ground"], 0.5))
        voice = _c01(_fin(p["voice"], 0.7))
        leak = _c01(_fin(p["leak"], 0.45))
        sat = min(max(_fin(p["saturation"], 1.0), 0.0), 2.0)
        h = self.host
        h[P_A] = A
        g = ground * m * self.gpres * (1.0 + 0.1 * math.sin(self.breath))
        gch = C_KEY * sat * (0.75 + 0.25 * self.kconf) * (1.0 + 0.15 * self.minor)
        h[P_GA], h[P_GB], h[P_GW] = math.cos(self.gh) * gch * g, math.sin(self.gh) * gch * g, g
        vs = voice * m * (0.25 + 0.55 * self.loud + 0.45 * self.bloom + 0.2 * self.kick) * (0.8 + 0.4 * e)
        h[P_VS] = min(vs, 1.3)
        h[P_RING] = voice * m * (0.5 + 0.5 * e) * (0.6 + 0.6 * self.bloom)
        h[P_VEILS] = _c01(_fin(p["veils"], 0.6))
        h[P_WF] = self._wf
        h[P_BA], h[P_BB] = self.base * sat
        h[P_PH:P_PH + 6] = self.ph
        h[P_LC] = C_LEAK * sat * leak * m
        h[P_LW] = leak * m
        h[P_LL] = 0.10 * leak * m
        h[P_REACH] = 0.16 + 0.3 * self.loud + 0.2 * self.damp + 0.12 * e
        h[P_STRIP] = 1.0 if strip_ok else 0.0
        h[P_FLOW] = FLOW
        h[P_SAT] = 0.35 * (sat - 0.8)
        h[P_SHADE] = 0.06 * self.minor * ground * m
        h[P_RL] = 0.05 * voice * m
        h[P_X0], h[P_SPAN] = -0.5 * A, A
        snd = getattr(self, "sound", None)
        h[P_LO], h[P_HI], h[P_OUT] = self._register(snd)
        h[P_SK], h[P_SN] = self._seg
        h[P_FLARE] = 0.6
        fr = h[P_FR:P_FR + 6 * NF].reshape(NF, 6)
        fr[:, 2] = -1.0
        if self.nf > 0:
            act = self.fr[:self.nf]
            fr[:self.nf, 0] = act[:, F_OX]
            fr[:self.nf, 1] = act[:, F_OY]
            fr[:self.nf, 2] = act[:, F_RHO]
            fr[:self.nf, 3] = act[:, F_A] * sat
            fr[:self.nf, 4] = act[:, F_B] * sat
            fr[:self.nf, 5] = act[:, F_RING]
        h[P_HUE:P_HUE + 24] = self.hue_u.reshape(-1)
        if not np.all(np.isfinite(h)):
            np.nan_to_num(h, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.P.set(h)
        fw, fh = np.int32(self.fw), np.int32(self.fh)
        if strip_ok:
            self._ks((1, 1, 1), (NS, 1, 1), (snd.notes_gpu, self.strip, self.P))
        launch_2d(self._kf, self.fw, self.fh, (self.strip, self.field, fw, fh, self.P))
        launch_2d(self._kg, W, H, (src, self.field, self.lut, out, np.int32(W), np.int32(H), fw, fh, self.P))
