"""Spectrum bars: the room's real-time FFT drawn over the scene as glassy, glowing bars with peak caps.

Source: Original
Author: smfx (original)
License: MIT
license_class: permissive

Listens to the fast feed (``listens = ("fft",)``, CONVENTIONS §2b): ``self.sound.fft`` (48 log bands, 40 Hz-16 kHz,
0..1, instant attack, release tau 0.12 s), ``fft_peak`` (peak hold) and ``level``.  The host copies are read, not
the GPU arrays: the per-frame work on them is tiny and lives on the host, which also lets a silent room skip the
kernels altogether (the scene passes through untouched).

Host, once per frame (preallocated numpy, no device sync):
  * floor: ``floor`` is taken off every band and the rest stretched back to 0..1, so a quiet room's noise shows
    nothing.  Bands and peaks then ease: attack tau 35 ms (a strike blooms in over ~2 frames), release tau 80 ms
    on top of the feed's own, so bars fall smoothly and never flicker.
  * bars: ``bars`` (16..96, eased as a float) cells tile the row; the last cell is partial while the count eases,
    so a change morphs (cells stretch, the partial bar grows in) instead of jumping.  Each bar takes 4 Catmull-Rom
    samples of the smoothed bands across its frequency span (0.6 max + 0.4 mean), its cap the max of the peaks.
  * one small param upload (scalars, palette, 48 bands, 48 peaks, 96 bars, 96 caps) with ``.set()``.

Device, two passes:
  1. ``sb_draw`` at ``render_scale`` (default 0.5): every pixel finds its place analytically.  The baseline is a
     circular arc of curvature kappa and length L (exact as kappa -> 0, so ``radial`` bends a straight row into a
     ring around the frame centre continuously; the ring keeps a small gap at the bottom where treble meets bass,
     and the whole layer fades to 0 at the gap's centre, so no halo is cut off at the seam); each pixel gets u
     (0..1 along it) and d (signed distance, + up / outward).  Its cell is floor(u * bars); it shades that bar and both neighbours (glow windowed to the
     3-cell neighbourhood, so no seams): a rounded-top glass body (AA coverage, brighter towards the top), a crisp
     rim, a vertical specular streak, a soft halo, a peak cap riding above, and an ambient bloom over the envelope.
     ``style`` cross-fades to a smooth spectrum curve (Catmull-Rom through the 48 bands, slope-corrected distance)
     with a thin line, soft fill below and a faint peak-hold ghost line.  Below the baseline (inside the ring) a
     faint, fading reflection that ``mirror`` turns into a full symmetric copy.  Colour by band through the cosine
     palette (bass -> treble, folded to its bright side), brightness by the bar's height and the room's level.
     Output: a tonemapped glow (rgb) and a glass absorption (a), uint8.  The whole layer fades to 0 before its
     early-out band (no step where the glow tails stop), and ``height`` is soft-capped so the row, the mirrored
     row and the ring always fit inside the frame's margins.
  2. ``sb_comp`` at full resolution: bilinear-samples that layer, darkens the scene a little under the glass
     (absorption) and screen-blends the glow on top in sRGB, scaled by amount x opacity.  Pixels the layer does
     not touch are copied untouched.

amount 0 (or no sound, or a silent room): the output is exactly ``src``.  pulse: lifts glow and brightness (up to
+50 %), never the geometry.  dt = 0: nothing eases.  No history of the picture, so a content change is harmless.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size
from stream_service.scope_feed import FFT_BANDS

NB = FFT_BANDS                 # 48 log bands
MAXBARS = 96
NS = 64                        # scalar slots
OB = NS                        # smoothed bands
OPK = OB + NB                  # smoothed peaks
OBAR = OPK + NB                # bar values
OBPK = OBAR + MAXBARS          # bar caps
NPT = OBPK + MAXBARS
RING_R = 0.2                   # ring baseline radius at radial = 1 (frame heights)
RING_FILL = 0.95               # the ring leaves a small gap at the bottom where treble meets bass
END_U = 0.5 * (1.0 / RING_FILL - 1.0)   # everything fades out this far (in u) past either end: the gap's centre
ROW_SPAN = 0.9                 # the straight row spans this fraction of the frame width
MARGIN = 0.04
ATTACK_S, RELEASE_S, PEAK_REL_S = 0.035, 0.08, 0.05

# P layout (floats)
#  0 aspect | 1 bars (float) | 2 cells (int) | 3 gap | 4 height scale | 5 arc apex y | 6 kappa | 7 arc length
#  8 mirror | 9 faint reflection | 10 reflection length | 11 style | 12 glow | 13 gain | 14 colour phase
#  15 palette period | 16 px (frame heights per internal pixel) | 17 baseline visibility | 18 absorb
#  19 glow radius | 20 cap thickness | 21 cap gap | 22 line width | 23 early-out pad | 24..35 palette
#  36 end fade (u past either end where the layer reaches 0)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define NB %(nb)d
#define OB %(ob)d
#define OPK %(opk)d
#define OBAR %(obar)d
#define OBPK %(obpk)d

__device__ __forceinline__ float sq(float v) { return v * v; }

// the palette folded to its bright side: a dark phase keeps its hue but still glows
__device__ __forceinline__ float3 sb_pal(float t, const float* __restrict__ PAL) {
    const float3 c = pal(t, PAL);
    const float m = fmaxf(c.x, fmaxf(c.y, c.z));
    const float k = 0.95f / fmaxf(m, 0.4f);
    return make_float3(fminf(c.x * k, 1.f), fminf(c.y * k, 1.f), fminf(c.z * k, 1.f));
}

// Catmull-Rom through the NB band values at band position x (band centres at integers): value (>= 0), d/dx
__device__ __forceinline__ float2 sb_cr(const float* __restrict__ B, float x) {
    x = fminf(fmaxf(x, 0.f), (float)(NB - 1));
    const int i1 = min((int)x, NB - 2);
    const float t = x - (float)i1;
    const float p0 = B[max(i1 - 1, 0)], p1 = B[i1], p2 = B[i1 + 1], p3 = B[min(i1 + 2, NB - 1)];
    const float a = -0.5f * p0 + 1.5f * p1 - 1.5f * p2 + 0.5f * p3;
    const float b = p0 - 2.5f * p1 + 2.f * p2 - 0.5f * p3;
    const float c = 0.5f * (p2 - p0);
    return make_float2(fmaxf(((a * t + b) * t + c) * t + p1, 0.f), (3.f * a * t + 2.f * b) * t + c);
}

// rounded box centred on the baseline: half width hw, half height hh, corner radius rc
__device__ __forceinline__ float sb_box(float ax, float ay, float hw, float hh, float rc) {
    const float qx = ax - (hw - rc), qy = ay - (hh - rc);
    const float mx = fmaxf(qx, 0.f), my = fmaxf(qy, 0.f);
    return sqrtf(mx * mx + my * my) + fminf(fmaxf(qx, qy), 0.f) - rc;
}

__device__ __forceinline__ float sb_tone(float c) {      // linear to 0.6, then a soft shoulder to 1
    return c < 0.6f ? c : 0.6f + 0.4f * (1.f - __expf(-(c - 0.6f) / 0.4f));
}

extern "C" __global__
void sb_draw(uchar4* __restrict__ ov, const int w, const int h, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float px = P[16];
    const float X = (x + 0.5f - 0.5f * w) / (float)h, Y = (0.5f * h - (y + 0.5f)) / (float)h;   // frame heights, y up
    // arc coordinates: d = signed distance from the baseline (outward +), s = arc length from its apex
    const float kap = P[6], L = P[7];
    const float qx = X, qy = Y - P[5];
    const float kq = kap * qy + 1.f;
    const float d = (kap * (qx * qx + qy * qy) + 2.f * qy) / (sqrtf(kap * kap * qx * qx + kq * kq) + 1.f);
    const float s = kap > 1e-5f ? atan2f(kap * qx, kq) / kap : qx;
    const float u = 0.5f + s / L;
    const float hs = P[4];
    const float yy = fabsf(d);
    const float eu = P[36];
    if (yy > hs + P[23] || u <= -eu || u >= 1.f + eu) { ov[y * w + x] = make_uchar4(0, 0, 0, 0); return; }
    // past either end the whole layer fades to 0 at u = -eu / 1 + eu: on a closed ring that is the gap's centre,
    // where the two ends meet, so halos never cut off at the seam
    const float endw = smooth(eu, 0.f, fmaxf(fmaxf(-u, u - 1.f), 0.f));
    // ... and fades to 0 before the early-out above (glow tails would otherwise stop in a visible step); the
    // bodies and caps end by hs + 0.015, inside the fade's start (hs + 0.35 pad >= hs + 0.019)
    const float fo = smooth(hs + P[23], hs + 0.35f * P[23], yy);
    const float gs = fmaxf(1.f + kap * d, 0.05f);                 // lateral stretch at this radius (ring)
    const float* PAL = P + 24;
    const float period = P[15], phase = P[14];
    const float glow = P[12], gr = P[19], wm = P[11];
    // below the baseline / inside the ring: a faint fading reflection, or (mirror) a full copy
    float rg = 1.f;
    if (d < 0.f) {
        rg = P[8] + (1.f - P[8]) * P[9] * __expf(d / P[10]);
        if (kap > 1e-5f) rg *= clamp01((0.92f / kap + d) / (0.3f / kap));      // fade out before the centre
    }
    const float endf = smooth(-0.004f, 0.012f, u) * smooth(-0.004f, 0.012f, 1.f - u);
    // the smooth envelope through the 48 bands (curve style, bloom, baseline colour)
    const float bpos = u * NB - 0.5f;
    const float2 hv = sb_cr(P + OB, bpos);
    const float Hc = hv.x * hs;
    const float Pc = fmaxf(sb_cr(P + OPK, bpos).x * hs, Hc);
    const float3 cc = sb_pal(clamp01(u) * period + phase, PAL);

    float3 acc = make_float3(0.f, 0.f, 0.f);
    float absorb = 0.f;
    // ------------------------------------------------------------------------------------------ bars
    if (wm < 0.999f && rg > 0.002f) {
        const float nf = P[1], gap = P[3];
        const int ni = (int)P[2];
        const float pitch = L / nf;
        const float aN = u * nf;
        const int jc = (int)floorf(aN);
        const float capth = P[20], capgap = P[21];
        float3 b3 = make_float3(0.f, 0.f, 0.f);
        float ab = 0.f;
        for (int j = jc - 1; j <= jc + 1; ++j) {
            if (j < 0 || j >= ni) continue;
            const float wf = (j == ni - 1) ? fmaxf(nf - (float)(ni - 1), 0.f) : 1.f;   // partial last cell
            const float v = P[OBAR + j], pk = P[OBPK + j];
            const float wvis = smooth(0.f, 0.6f, wf);
            const float vis = smooth(0.f, 0.035f, v) * wvis;
            const float pvis = smooth(0.012f, 0.06f, pk) * wvis;
            if (vis <= 0.f && pvis <= 0.f) continue;
            const float dl = aN - ((float)j + 0.5f * wf);
            const float perp = dl * pitch * gs;
            const float ax = fabsf(perp);
            const float hw = fmaxf(0.5f * (1.f - gap) * pitch * wf, 0.75f * px);
            const float hb = v * hs;
            const float sdf = sb_box(ax, yy, hw, hb, fminf(hw, hb));
            const float cov = clamp01(0.5f - sdf / px) * vis;
            const float ty = clamp01(yy / fmaxf(hb, 1e-4f));
            const float body = cov * (0.2f + 0.55f * ty * ty);
            const float rim = __expf(-sq(sdf / (1.3f * px))) * vis * (0.3f + 0.7f * ty);
            const float spec = cov * __expf(-sq((perp / hw + 0.45f) / 0.2f)) * (0.1f + 0.55f * ty);
            const float wwin = smooth(1.5f, 1.0f, fabsf(dl));
            const float halo = __expf(-fmaxf(sdf, 0.f) / gr) * wwin * vis * glow * 0.5f;
            // the peak cap rides above the bar
            const float yk = fmaxf(pk * hs, hb) + capgap + 0.5f * capth;
            const float sdk = sb_box(ax, fabsf(yy - yk), hw, 0.5f * capth, 0.5f * capth);
            const float cap = clamp01(0.5f - sdk / px) * pvis;
            const float capg = __expf(-fmaxf(sdk, 0.f) / (0.6f * gr)) * wwin * pvis * glow * 0.3f;
            const float3 bc = sb_pal(((float)j + 0.5f * wf) / nf * period + phase, PAL);
            const float br = 0.55f + 0.65f * v;
            const float kc = (body + halo + 0.55f * rim) * br;
            const float kw = (0.45f * spec + 0.35f * rim) * br + 0.95f * cap + capg;
            b3.x += kc * bc.x + kw * (0.4f + 0.6f * bc.x);
            b3.y += kc * bc.y + kw * (0.4f + 0.6f * bc.y);
            b3.z += kc * bc.z + kw * (0.4f + 0.6f * bc.z);
            ab += cov + 0.6f * cap;
        }
        // ambient bloom over the envelope: a soft haze between and above the bars
        const float bl = 0.16f * glow * endf * smooth(0.f, 0.03f, Hc / hs)
                         * (yy < Hc ? 0.25f + 0.75f * yy / fmaxf(Hc, 1e-4f) : __expf(-(yy - Hc) / (2.5f * gr)));
        b3.x += bl * cc.x; b3.y += bl * cc.y; b3.z += bl * cc.z;
        const float kb = rg * (1.f - wm);
        acc.x += kb * b3.x; acc.y += kb * b3.y; acc.z += kb * b3.z;
        absorb += kb * rg * fminf(ab, 1.f);         // a faint reflection glows, it barely shades
    }
    // ------------------------------------------------------------------------------------------ curve
    if (wm > 0.001f && rg > 0.002f) {
        const float slope = hv.y * NB * hs / (L * gs);
        const float lw = P[22];
        const float present = smooth(0.f, 0.03f, Hc / hs);
        const float vis = fmaxf(present, 0.6f * P[17]);
        const float dl = (yy - Hc) * rsqrtf(1.f + slope * slope);
        const float line = __expf(-sq(dl / lw)) * vis;
        const float lg = __expf(-fabsf(dl) / (1.5f * gr)) * glow * 0.4f * vis;
        const float tyc = clamp01(yy / fmaxf(Hc, 1e-4f));
        const float inside = clamp01(0.5f - dl / px) * present;
        const float fill = inside * (0.06f + 0.34f * tyc * tyc);
        const float dp = yy - (Pc + 2.f * lw);
        const float ghost = __expf(-sq(dp / (0.8f * lw))) * 0.5f * smooth(0.004f, 0.025f, Pc - Hc);
        const float br = 0.6f + 0.6f * clamp01(Hc / hs);
        const float kc = (0.9f * line + lg + fill) * br;
        const float kw = 0.35f * line * br + ghost;
        const float kb = rg * wm * endf;
        acc.x += kb * (kc * cc.x + kw * (0.45f + 0.55f * cc.x));
        acc.y += kb * (kc * cc.y + kw * (0.45f + 0.55f * cc.y));
        acc.z += kb * (kc * cc.z + kw * (0.45f + 0.55f * cc.z));
        absorb += kb * fminf(inside * (0.3f + 0.5f * tyc) + 0.5f * line, 1.f);
    }
    // ------------------------------------------------------------------------------------------ baseline
    {
        const float blw = 0.0009f + 1.1f * px;
        const float base = P[17] * endf * (0.3f * __expf(-sq(d / blw)) + 0.07f * glow * __expf(-yy / (1.2f * gr)));
        acc.x += base * (0.3f + 0.7f * cc.x); acc.y += base * (0.3f + 0.7f * cc.y); acc.z += base * (0.3f + 0.7f * cc.z);
    }
    const float g = P[13] * endw * fo;
    const float a = clamp01(absorb) * P[18] * endw * fo;
    ov[y * w + x] = make_uchar4((unsigned char)(sb_tone(acc.x * g) * 255.f + 0.5f),
                                (unsigned char)(sb_tone(acc.y * g) * 255.f + 0.5f),
                                (unsigned char)(sb_tone(acc.z * g) * 255.f + 0.5f),
                                (unsigned char)(a * 255.f + 0.5f));
}

// full resolution: glass absorption, then a screen blend of the glow (sRGB), both scaled by k = amount x opacity
extern "C" __global__
void sb_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const uchar4* __restrict__ ov,
             const int W, const int H, const int w, const int h, const float k)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const size_t i = (size_t)y * W + x;
    const float fx = (x + 0.5f) * (float)w / (float)W - 0.5f, fy = (y + 0.5f) * (float)h / (float)H - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const unsigned int* ou = reinterpret_cast<const unsigned int*>(ov);
    const unsigned int ra = ou[ya * w + xa], rb = ou[ya * w + xb], rc = ou[yb * w + xa], rd = ou[yb * w + xb];
    if ((ra | rb | rc | rd) == 0u) { out[i] = src[i]; return; }
    const uchar4 A = ov[ya * w + xa], B = ov[ya * w + xb], C = ov[yb * w + xa], D = ov[yb * w + xb];
    const float u0 = (1.f - tx) * (1.f - ty), u1 = tx * (1.f - ty), u2 = (1.f - tx) * ty, u3 = tx * ty;
    const float kr = k * (1.f / 255.f);
    const float gr_ = (u0 * A.x + u1 * B.x + u2 * C.x + u3 * D.x) * kr;
    const float gg_ = (u0 * A.y + u1 * B.y + u2 * C.y + u3 * D.y) * kr;
    const float gb_ = (u0 * A.z + u1 * B.z + u2 * C.z + u3 * D.z) * kr;
    const float ka = (u0 * A.w + u1 * B.w + u2 * C.w + u3 * D.w) * kr;
    const uchar4 sp = src[i];
    float r = sp.x * (1.f - ka), g = sp.y * (1.f - ka), b = sp.z * (1.f - ka);
    r += gr_ * (255.f - r); g += gg_ * (255.f - g); b += gb_ * (255.f - b);
    out[i] = pack(make_float4(r, g, b, 255.f));
}
""" % {"nb": NB, "ob": OB, "opk": OPK, "obar": OBAR, "obpk": OBPK}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _cr(B: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Host Catmull-Rom through B (band centres at integers) at positions x, clamped >= 0 (matches sb_cr)."""
    x = np.clip(x, 0.0, NB - 1.0)
    i1 = np.minimum(x.astype(np.intp), NB - 2)
    t = x - i1
    p0, p1, p2, p3 = B[np.maximum(i1 - 1, 0)], B[i1], B[i1 + 1], B[np.minimum(i1 + 2, NB - 1)]
    a = -0.5 * p0 + 1.5 * p1 - 1.5 * p2 + 0.5 * p3
    b = p0 - 2.5 * p1 + 2.0 * p2 - 0.5 * p3
    c = 0.5 * (p2 - p0)
    return np.maximum(((a * t + b) * t + c) * t + p1, 0.0)


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _softcap(x: float, cap: float) -> float:
    """x up to 0.75 cap, then a smooth (C1) shoulder that never passes cap."""
    knee = 0.75 * cap
    if x <= knee:
        return x
    r = max(cap - knee, 1e-6)
    return knee + r * (1.0 - math.exp(-(x - knee) / r))


class SpectrumBars(Effect):
    name = "spectrum_bars"
    stage = "overlay"
    label = "Spectrum bars"
    description = ("The room's real-time FFT over the scene: glassy glowing bars with peak caps and a faint "
                   "reflection, coloured bass to treble, that bend from a row into a ring or melt into a smooth "
                   "spectrum curve.")
    listens = ("fft",)
    params = {
        **standard_effect_params(),
        "bars": num(16.0, 96.0, 48.0, "Bars", step=1.0, tier="regional"),
        "radial": num(0.0, 1.0, 0.0, "Shape (row -> ring)", tier="regional", basic=True),
        "style": num(0.0, 1.0, 0.0, "Style (bars -> curve)", tier="regional", basic=True),
        "mirror": num(0.0, 1.0, 0.0, "Mirror (reflection -> symmetric)", tier="regional"),
        "height": num(0.08, 0.6, 0.3, "Height", tier="regional"),
        "opacity": num(0.1, 1.0, 0.85, "Opacity", tier="regional"),
        "gap": num(0.0, 0.85, 0.32, "Gap between bars", tier="regional"),
        "position": num(0.0, 1.0, 0.0, "Position (bottom -> top)", tier="regional"),
        "glow": num(0.0, 1.5, 0.7, "Glow", tier="regional"),
        "floor": num(0.0, 0.6, 0.18, "Noise floor", tier="regional"),
        "hue": num(0.0, 0.75, 0.0, "Hue shift", tier="regional"),
        **palette_params("rainbow", period=0.7, drift=0.02),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, _OPTIONS)
        self._kd = mod.get_function("sb_draw")
        self._kc = mod.get_function("sb_comp")
        self.w, self.h = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.layer = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.P = cp.zeros(NPT, cp.float32)
        self.host = np.zeros(NPT, np.float32)
        self.sm = np.zeros(NB, np.float32)          # eased bands (after the floor)
        self.spk = np.zeros(NB, np.float32)         # eased peaks
        self._t = np.zeros(NB, np.float32)
        self._d = np.zeros(NB, np.float32)
        self._c = np.zeros(NB, np.float32)
        self._m = np.zeros(NB, bool)
        self._j = np.arange(MAXBARS, dtype=np.float64)[:, None]
        self._kk = ((np.arange(4, dtype=np.float64) + 0.5) / 4.0)[None, :]
        self.level = 0.0
        self.drift = 0.0

    # -- host side ----------------------------------------------------------------------------------------------
    def _palette(self, name: str) -> np.ndarray:
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        if rows is not None:
            try:
                rows = np.asarray(rows, np.float32).reshape(-1)
            except (TypeError, ValueError):
                rows = None
            if rows is not None and (rows.size < 12 or not np.isfinite(rows[:12]).all()):
                rows = None                                  # malformed session palette: use our own
        return np.asarray(PALETTES.get(name, PALETTES["rainbow"]) if rows is None else rows, np.float32).reshape(-1)[:12]

    def _ease(self, state: np.ndarray, target: np.ndarray, ka: float, kr: float) -> None:
        np.subtract(target, state, out=self._d)
        np.greater(self._d, 0.0, out=self._m)
        self._c.fill(kr)
        self._c[self._m] = ka
        np.multiply(self._d, self._c, out=self._d)
        state += self._d

    def _listen(self, dt: float, floor: float) -> float:
        """Ease the bands / peaks / level toward the sound (zeros without one).  Returns the floor-cut level."""
        s = getattr(self, "sound", None)
        fft = pk = None
        lvl = 0.0
        if s is not None:
            try:
                fft, pk, lvl = s.fft, s.fft_peak, float(s.level)
            except Exception:
                fft = pk = None
        inv = 1.0 / max(1.0 - floor, 1e-3)
        ka = 1.0 - math.exp(-dt / ATTACK_S)
        kr = 1.0 - math.exp(-dt / RELEASE_S)
        kp = 1.0 - math.exp(-dt / PEAK_REL_S)
        for src, state, rel in ((fft, self.sm, kr), (pk, self.spk, kp)):
            t = self._t
            if src is not None and getattr(src, "shape", None) == (NB,):
                np.subtract(src, floor, out=t)
                np.multiply(t, inv, out=t)
                np.nan_to_num(t, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
                np.clip(t, 0.0, 1.0, out=t)
            else:
                t.fill(0.0)
            self._ease(state, t, ka, rel)
        np.maximum(self.spk, self.sm, out=self.spk)
        if not math.isfinite(lvl):
            lvl = 0.0
        lt = min(max((lvl - floor) * inv, 0.0), 1.0)
        self.level += (lt - self.level) * (1.0 - math.exp(-dt / (0.05 if lt > self.level else 0.4)))
        return self.level

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dtc = min(max(float(dt), 0.0), 1.0)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        floor = min(max(float(p["floor"]), 0.0), 0.95)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 1000.0)
        lvl = self._listen(dtc, floor)
        k = a * min(max(float(p["opacity"]), 0.0), 1.0)
        top = max(float(self.sm.max()), float(self.spk.max()))
        if k <= 1e-4 or top < 1e-3:                      # nothing to show: the scene, untouched
            cp.copyto(out, src)
            return

        W, H = self.ctx.width, self.ctx.height
        A = W / H
        radial = min(max(float(p["radial"]), 0.0), 1.0)
        mirror = min(max(float(p["mirror"]), 0.0), 1.0)
        # the graphic fits the frame at any height: the row's bars (+8 % cap headroom) and what lies below the
        # baseline (reflection / mirror) inside the margins; the ring's bars between its radius and the margin
        fit_row = (1.0 - 2.0 * MARGIN) / (1.38 + 0.7 * mirror)
        fit_ring = (0.5 - MARGIN - RING_R) / 1.08
        hs = _softcap(float(p["height"]) * (1.0 - 0.4 * radial), fit_row + (fit_ring - fit_row) * radial)
        L = ROW_SPAN * A + (2.0 * math.pi * RING_R * RING_FILL - ROW_SPAN * A) * radial
        theta = max(2.0 * math.pi * RING_FILL * radial, 1e-6)
        kap = theta / L
        # the row: the graphic (bars above, reflection / mirror below) placed by position inside the margins
        hd = hs * (0.3 + 0.7 * mirror)
        avail = 1.0 - 2.0 * MARGIN
        lo = -0.5 + MARGIN + min(max(float(p["position"]), 0.0), 1.0) * max(0.0, avail - hs * 1.08 - hd)
        base_row = lo + hd
        apex = base_row + (RING_R - base_row) * radial
        nf = min(max(float(p["bars"]), 16.0), float(MAXBARS))
        ni = min(MAXBARS, max(1, int(math.ceil(nf - 1e-3))))
        px = 1.0 / self.h
        pitch = L / nf
        hw = 0.5 * (1.0 - float(p["gap"])) * pitch
        gr = 0.006 + 0.2 * min(hw, 0.03) + 0.012 * float(p["glow"])

        # bars: 4 Catmull-Rom samples across each cell's frequency span
        wf = np.ones((ni, 1))
        wf[-1, 0] = max(nf - (ni - 1), 1e-3)
        pos = (self._j[:ni] + self._kk * wf) / nf * NB - 0.5
        sv = _cr(self.sm, pos)
        bars = 0.6 * sv.max(axis=1) + 0.4 * sv.mean(axis=1)
        caps = np.maximum(_cr(self.spk, pos).max(axis=1), bars)

        hst = self.host
        hst[0] = A
        hst[1] = nf
        hst[2] = ni
        hst[3] = float(p["gap"])
        hst[4] = hs
        hst[5] = apex
        hst[6] = kap
        hst[7] = L
        hst[8] = mirror
        hst[9] = 0.3 * (1.0 - 0.5 * radial)          # the floor reflection; quieter inside the ring
        hst[10] = 0.4 * hs + 1e-3
        hst[11] = min(max(float(p["style"]), 0.0), 1.0)
        hst[12] = float(p["glow"])
        hst[13] = (0.7 + 0.45 * lvl) * (1.0 + 0.5 * pulse)
        hst[14] = self.drift + float(p["hue"])
        hst[15] = float(p["palette_period"])
        hst[16] = px
        hst[17] = _ss(0.03, 0.18, float(self.sm.max()))
        hst[18] = 0.3
        hst[19] = gr
        hst[20] = min(max(0.25 * hw, 1.6 * px), 0.009)
        hst[21] = max(1.5 * px, 0.12 * hw)
        hst[22] = 0.0012 + 1.1 * px
        hst[23] = 4.0 * gr + 0.03
        hst[24:36] = self._palette(p["palette"])
        hst[36] = END_U
        hst[OB:OB + NB] = self.sm
        hst[OPK:OPK + NB] = self.spk
        hst[OBAR:OBAR + ni] = bars
        hst[OBAR + ni:OBAR + MAXBARS] = 0.0
        hst[OBPK:OBPK + ni] = caps
        hst[OBPK + ni:OBPK + MAXBARS] = 0.0
        self.P.set(hst)

        launch_2d(self._kd, self.w, self.h, (self.layer, np.int32(self.w), np.int32(self.h), self.P))
        launch_2d(self._kc, W, H, (src, out, self.layer, np.int32(W), np.int32(H), np.int32(self.w),
                                   np.int32(self.h), np.float32(k)))

    def close(self) -> None:
        self.layer = None
        self.P = None
