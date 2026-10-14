"""Sketchbook: the picture as a hand drawing.  XDoG ink lines, up to four procedural hatch layers shading the
darks, paper tint and tooth, and an optional 8-12 fps 'boil' of the strokes.  The style morphs continuously from
graphite through hard ink to coloured pencil.

Source: https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_DifferenceOfGaussians.fx
        (licence: https://github.com/GarrettGunnell/AcerolaFX/blob/main/LICENSE, MIT)
        https://github.com/BradLarson/GPUImage (Sketch and Crosshatch filters; ideas only; licence: repository
        License.txt, BSD-3-Clause)
Author: Garrett Gunnell (AcerolaFX DifferenceOfGaussians, after Winnemoller XDoG); Brad Larson et al. (GPUImage
        Sketch and Crosshatch, ideas)
License: MIT + BSD-3-Clause
license_class: permissive
Ported from HLSL by smfx (ideas/changes: the AcerolaFX separable two-sigma Gaussian (sigma_e and k*sigma_e, k = 1.6)
and its tanh soft threshold are kept, run at half res on luma; only the DoG term is thresholded (normalised by the
local level so dark content still draws), the flat-tone part of XDoG is left to the hatching.  AcerolaFX's hatch
textures are replaced by procedural strokes: per layer a rotated line family with per-stroke pressure, staggered
breaks, tapered ends and a slow hand wobble; layers switch on by tone thresholds (as in GPUImage Crosshatch) and
thicken as the tone darkens.  Paper is a precomputed tooth + mottle field; graphite and coloured pencil skip on
the tooth.  The boil reseeds a low-res displacement field and the hatch phases from floor(integrated counter) and
crossfades the last 18% of each step, so it reads as a redraw at boil_rate fps without per-frame noise.  Colour:
ink tint and coloured-pencil strokes come from the shared cosine palette (index = tone x period + drift).)

Realtime: amount (0 = src exactly; lines fade in first, then the hatching, then paper replaces the photographic
base); pulse (line darkness + weight kick, one extra hatch layer, a boil-rate kick; all scaled by the amount).
Regional: line_weight (basic), hatch_density, hatch_layers (1-4, continuous), style (0 graphite, 1 ink,
2 coloured pencil), paper_tone, boil_rate, ink_tint, palette, palette_period, drift_speed.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NPRM = 48
# prm: 0..11 palette | 12 drift | 13 period | 14..17 hatch phase | 18..21 layer weight | 22..25 cos | 26..29 sin
#      30 spacing px | 31 hatchA | 32 lineA | 33 paperA | 34 wg | 35 wi | 36 wc | 37 paper_tone | 38 ink_tint
#      39 line gain | 40 boil disp px | 41 aa (spacing units) | 42 seg len | 43 gap | 44 pressure var | 45 toothy
#      46 stroke gain | 47 wobble amp
FIELD = 16                                    # boil/wobble field cell (output px)
ANGLES = (0.785, -0.62, 0.17, 1.40)           # hatch layer directions (rad)
PHASES = (0.13, 0.57, 0.31, 0.79)

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float2 f2bl(const float2* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float2 a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float2(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y));
}
__device__ __forceinline__ float4 f4bl(const float4* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float4 a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z),
                       v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w));
}
__device__ __forceinline__ float3 mul3(float3 o, float3 c, float a) {     // o *= mix(1, c, a)
    return make_float3(o.x * (1.f + (c.x - 1.f) * a), o.y * (1.f + (c.y - 1.f) * a), o.z * (1.f + (c.z - 1.f) * a));
}

// setup: paper tooth (fine grain) + mottle (soft fibre clouds), resolution-independent via sc.
extern "C" __global__
void sk_paper(uchar2* __restrict__ T, const int W, const int H, const float sc)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = (x + 0.5f) / sc, py = (y + 0.5f) / sc;
    const float g = 0.5f * vnoise(px / 1.9f, py / 2.3f, 101u) + 0.3f * vnoise(px / 4.7f, py / 3.9f, 202u)
                  + 0.2f * vnoise(px / 13.f, py / 11.f, 303u);
    const float m = fbm(px / 420.f, py / 420.f, 404u);
    T[y * W + x] = make_uchar2((unsigned char)(clamp01(g) * 255.f), (unsigned char)(clamp01(m) * 255.f));
}

// 4K src -> half-res colour (rgb 0..1, w = luma) and luma, as 2x2 boxes.
extern "C" __global__
void sk_down(const uchar4* __restrict__ src, float4* __restrict__ C, float* __restrict__ L,
             const int W, const int H, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float4 c = bilin(src, (x + 0.5f) * W / w, (y + 0.5f) * H / h, W, H);
    const float l = luma(c);
    C[y * w + x] = make_float4(c.x * (1.f / 255.f), c.y * (1.f / 255.f), c.z * (1.f / 255.f), l);
    L[y * w + x] = l;
}

// Horizontal pass of the two Gaussians (sigma s1 and 1.6 s1).
extern "C" __global__
void sk_blur_h(const float* __restrict__ L, float2* __restrict__ T, const int w, const int h, const float s1)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float s2 = 1.6f * s1;
    const int R = min((int)ceilf(2.5f * s2), 12);
    const float i1 = -0.5f / (s1 * s1), i2 = -0.5f / (s2 * s2);
    float a1 = 0.f, a2 = 0.f, n1 = 0.f, n2 = 0.f;
    for (int i = -R; i <= R; ++i) {
        const float l = L[y * w + min(max(x + i, 0), w - 1)];
        const float g1 = __expf(i * i * i1), g2 = __expf(i * i * i2);
        a1 += g1 * l; n1 += g1; a2 += g2 * l; n2 += g2;
    }
    T[y * w + x] = make_float2(a1 / n1, a2 / n2);
}

// Vertical pass + XDoG soft threshold on the DoG term -> (line 0..1, tone 0..1).
extern "C" __global__
void sk_blur_v(const float2* __restrict__ T, float2* __restrict__ D, const int w, const int h, const float s1,
               const float gain, const float eps, const float phi)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float s2 = 1.6f * s1;
    const int R = min((int)ceilf(2.5f * s2), 12);
    const float i1 = -0.5f / (s1 * s1), i2 = -0.5f / (s2 * s2);
    float a1 = 0.f, a2 = 0.f, n1 = 0.f, n2 = 0.f;
    for (int i = -R; i <= R; ++i) {
        const float2 t = T[min(max(y + i, 0), h - 1) * w + x];
        const float g1 = __expf(i * i * i1), g2 = __expf(i * i * i2);
        a1 += g1 * t.x; n1 += g1; a2 += g2 * t.y; n2 += g2;
    }
    const float G1 = a1 / n1, G2 = a2 / n2;
    const float e = (G2 - G1) / (0.15f + 0.85f * fmaxf(G2, 0.f)) * gain;      // > 0 on the dark side of an edge
    const float line = e > eps ? tanhf(phi * (e - eps)) : 0.f;
    D[y * w + x] = make_float2(line, clamp01(G2));
}

// Low-res boil displacement (xy, output px units before amp) + hand wobble (z), crossfaded between two seeds.
extern "C" __global__
void sk_field(float4* __restrict__ F, const int fw, const int fh, const float cell, const float sc,
              const unsigned int sa, const unsigned int sb, const float ease)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= fw || y >= fh) return;
    const float px = (x + 0.5f) * cell / sc, py = (y + 0.5f) * cell / sc;
    const float qx = px / 230.f, qy = py / 230.f;
    const float ax = vnoise(qx, qy, sa * 3u + 11u) - 0.5f, ay = vnoise(qx, qy, sa * 3u + 12u) - 0.5f;
    const float bx = vnoise(qx, qy, sb * 3u + 11u) - 0.5f, by = vnoise(qx, qy, sb * 3u + 12u) - 0.5f;
    const float wa = vnoise(px / 520.f, py / 520.f, sa * 3u + 13u), wb = vnoise(px / 520.f, py / 520.f, sb * 3u + 13u);
    const float wob = (fbm(px / 900.f, py / 700.f, 77u) - 0.5f) * 1.6f + (wa + (wb - wa) * ease - 0.5f) * 0.35f;
    F[y * fw + x] = make_float4(2.f * (ax + (bx - ax) * ease), 2.f * (ay + (by - ay) * ease), wob, 0.f);
}

// Full-res composite: base (src -> paper), washes, hatch layers, ink lines, chalk highlights.
extern "C" __global__
void sk_comp(const uchar4* __restrict__ src, const float4* __restrict__ C, const float2* __restrict__ D,
             const float4* __restrict__ F, const uchar2* __restrict__ PT, uchar4* __restrict__ out,
             const int W, const int H, const int w1, const int h1, const int fw, const int fh, const float cell,
             const float* __restrict__ prm)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hx = (float)w1 / W, hy = (float)h1 / H;
    const float fx = x + 0.5f, fy = y + 0.5f;
    const float4 bf = f4bl(F, fx / cell, fy / cell, fw, fh);
    const float amp = prm[40];
    const float dx = bf.x * amp, dy = bf.y * amp;
    const float2 lt = f2bl(D, (fx + dx) * hx, (fy + dy) * hy, w1, h1);
    const float4 cc = f4bl(C, fx * hx, fy * hy, w1, h1);
    const uchar2 pt = PT[y * W + x];
    const float tooth = pt.x * (1.f / 255.f), mottle = pt.y * (1.f / 255.f);
    const uchar4 sp = src[y * W + x];
    const float3 s = make_float3(sp.x * (1.f / 255.f), sp.y * (1.f / 255.f), sp.z * (1.f / 255.f));

    const float wg = prm[34], wi = prm[35], wc = prm[36], ptone = prm[37], tint = prm[38];
    const float hatchA = prm[31], lineA = prm[32], paperA = prm[33];
    const float tone = lt.y;

    // paper: white -> cream -> toned tan, with tooth grain and soft mottle
    float3 paper;
    if (ptone < 0.5f) {
        const float t = 2.f * ptone;
        paper = make_float3(0.975f + (0.94f - 0.975f) * t, 0.97f + (0.895f - 0.97f) * t, 0.955f + (0.79f - 0.955f) * t);
    } else {
        const float t = 2.f * ptone - 1.f;
        paper = make_float3(0.94f + (0.72f - 0.94f) * t, 0.895f + (0.655f - 0.895f) * t, 0.79f + (0.56f - 0.79f) * t);
    }
    const float grain = 1.f + 0.075f * (tooth - 0.55f) + 0.06f * (mottle - 0.5f);
    paper.x *= grain; paper.y *= grain; paper.z *= grain;

    float3 o = make_float3(s.x + (paper.x - s.x) * paperA, s.y + (paper.y - s.y) * paperA, s.z + (paper.z - s.z) * paperA);

    // stroke colours
    const float u = fx / W, v = fy / H;
    const float idx = tone * prm[13] + 0.18f * (0.7f * u + 0.3f * v) + prm[12];
    const float3 pc = pal(idx, prm);
    const float cl = 0.2126f * cc.x + 0.7152f * cc.y + 0.0722f * cc.z;
    float3 ss = make_float3(clamp01(cl + (cc.x - cl) * 1.7f), clamp01(cl + (cc.y - cl) * 1.7f), clamp01(cl + (cc.z - cl) * 1.7f));
    const float3 gr = make_float3(0.30f + (pc.x * 0.6f - 0.30f) * tint * 0.5f, 0.31f + (pc.y * 0.6f - 0.31f) * tint * 0.5f,
                                  0.34f + (pc.z * 0.6f - 0.34f) * tint * 0.5f);
    const float3 ik = make_float3(0.045f + (pc.x * 0.3f - 0.045f) * tint * 0.7f, 0.045f + (pc.y * 0.3f - 0.045f) * tint * 0.7f,
                                  0.06f + (pc.z * 0.3f - 0.06f) * tint * 0.7f);

    // washes once the paper is down: graphite smudge in the darks, coloured-pencil colour wash
    const float dk = 1.f - tone;
    const float smudge = paperA * wg * 0.5f * dk * sqrtf(dk);
    o = mul3(o, make_float3(0.47f, 0.475f, 0.5f), smudge);
    o = mul3(o, make_float3(0.12f + 0.88f * ss.x, 0.12f + 0.88f * ss.y, 0.12f + 0.88f * ss.z), paperA * wc * 0.5f);
    o = mul3(o, ik, paperA * wi * 0.12f * dk * dk);

    // hatching
    const float sat = fmaxf(cc.x, fmaxf(cc.y, cc.z)) - fminf(cc.x, fminf(cc.y, cc.z));
    const float th = tone - wc * 0.45f * sat;
    const float spacing = prm[30], aa = prm[41], seglen = prm[42], gap = prm[43], pvar = prm[44];
    const float toothy = prm[45], sgain = prm[46], wob = bf.z * prm[47];
    const float qx = fx + 0.6f * dx, qy = fy + 0.6f * dy;
    const float THR[4] = {0.84f, 0.64f, 0.44f, 0.25f};
    if (hatchA > 1e-4f) {
        #pragma unroll
        for (int k = 0; k < 4; ++k) {
            const float lw = prm[18 + k];
            if (lw <= 1e-4f) continue;
            const float thr = THR[k];
            const float cov = smooth(thr + 0.07f, thr - 0.07f, th) * lw * hatchA;
            if (cov <= 1e-3f) continue;
            const float dark = clamp01((thr - th) * 3.3f);
            const float cs = prm[22 + k], sn = prm[26 + k];
            const float along = qx * cs + qy * sn, across = -qx * sn + qy * cs;
            const float sv = across / spacing + prm[14 + k] + wob;
            const float idf = floorf(sv);
            const int id = (int)idf;
            const float d = fabsf(sv - idf - 0.5f);
            const float tt = along / (spacing * seglen) + hash1((unsigned)id, (unsigned)k, 11u) * 7.f;
            const float sgf = floorf(tt);
            const int sg = (int)sgf;
            const float fs = tt - sgf;
            const float keep = hash1((unsigned)id, (unsigned)sg, (unsigned)(k * 31 + 5)) > gap ? 1.f : 0.f;
            const float taper = smooth(0.f, 0.12f, fs) * smooth(1.f, 0.8f, fs);
            const float hw = 0.5f * (0.2f + 0.45f * dark) * (0.55f + 0.45f * taper);
            const float st = 1.f - smooth(hw - aa, hw + aa, d);
            const float pr = (1.f - pvar + pvar * hash1((unsigned)id, (unsigned)sg, (unsigned)(k + 77))) * (0.8f + 0.2f * (1.f - fs));
            float a = clamp01(st * keep * taper * cov * pr * sgain);
            a *= 1.f + (smooth(0.1f, 0.8f, tooth * 0.9f + a * 0.5f) - 1.f) * toothy;
            const float3 pk = pal(idx + 0.17f * k, prm);
            const float dkk = 1.f - 0.12f * k;
            const float3 cp3 = make_float3((ss.x + (pk.x - ss.x) * tint) * dkk, (ss.y + (pk.y - ss.y) * tint) * dkk,
                                           (ss.z + (pk.z - ss.z) * tint) * dkk);
            const float3 col = make_float3(wg * gr.x + wi * ik.x + wc * cp3.x, wg * gr.y + wi * ik.y + wc * cp3.y,
                                           wg * gr.z + wi * ik.z + wc * cp3.z);
            o = mul3(o, col, a);
        }
    }

    // ink lines
    float la = clamp01(lt.x * lineA * prm[39]);
    la *= 1.f + (smooth(0.05f, 0.7f, tooth * 0.8f + la * 0.6f) - 1.f) * toothy * 0.8f;
    const float3 lc = make_float3(wg * gr.x * 0.8f + wi * ik.x + wc * (ss.x * 0.5f + (pc.x * 0.45f - ss.x * 0.5f) * tint),
                                  wg * gr.y * 0.8f + wi * ik.y + wc * (ss.y * 0.5f + (pc.y * 0.45f - ss.y * 0.5f) * tint),
                                  wg * gr.z * 0.8f + wi * ik.z + wc * (ss.z * 0.5f + (pc.z * 0.45f - ss.z * 0.5f) * tint));
    o = mul3(o, lc, la);

    // white chalk on toned paper
    const float chalk = paperA * smooth(0.45f, 1.f, ptone) * smooth(0.68f, 0.95f, tone) * (0.4f + 0.6f * tooth) * 0.85f;
    o.x += (0.99f - o.x) * chalk; o.y += (0.98f - o.y) * chalk; o.z += (0.95f - o.z) * chalk;

    out[y * W + x] = pack(make_float4(o.x * 255.f, o.y * 255.f, o.z * 255.f, 255.f));
}
"""


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _rnd(n: int, k: int) -> float:
    h = (n * 2654435761 + k * 40503 + 0x9E3779B1) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x7FEB352D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x846CA68B) & 0xFFFFFFFF
    h ^= h >> 16
    return h / 4294967296.0


class SketchHatch(Effect):
    name = "sketch_hatch"
    stage = "stylise"
    label = "Sketchbook"
    description = ("The picture as a hand drawing: XDoG ink lines, up to four hatch layers in the darks, paper "
                   "tint and tooth, and an optional stroke boil; graphite -> ink -> coloured pencil.")
    params = {
        **standard_effect_params(),
        **palette_params("dusk", 0.8, 0.02),
        "line_weight": num(0.0, 1.0, 0.5, "Line weight", basic=True),
        "hatch_density": num(0.0, 1.0, 0.5, "Hatch density"),
        "hatch_layers": num(1.0, 4.0, 3.0, "Hatch layers", step=0.05),
        "style": num(0.0, 2.0, 1.4, "Style (0 graphite, 1 ink, 2 coloured pencil)", step=0.02),
        "paper_tone": num(0.0, 1.0, 0.35, "Paper tone (white -> cream -> toned)"),
        "boil_rate": num(0.0, 12.0, 8.0, "Boil (redraws per second)", step=0.5, unit="fps"),
        "ink_tint": num(0.0, 1.0, 0.45, "Ink tint (palette)"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kp = mod.get_function("sk_paper")
        self._kd = mod.get_function("sk_down")
        self._kh = mod.get_function("sk_blur_h")
        self._kv = mod.get_function("sk_blur_v")
        self._kf = mod.get_function("sk_field")
        self._kc = mod.get_function("sk_comp")
        W, H = ctx.width, ctx.height
        self._sc = H / 2160.0
        self._w1, self._h1 = max(1, (W + 1) // 2), max(1, (H + 1) // 2)
        self._fw, self._fh = W // FIELD + 2, H // FIELD + 2
        w1, h1 = self._w1, self._h1
        self._C = cp.zeros((h1, w1, 4), dtype=cp.float32)
        self._L = cp.zeros((h1, w1), dtype=cp.float32)
        self._T = cp.zeros((h1, w1, 2), dtype=cp.float32)
        self._D = cp.zeros((h1, w1, 2), dtype=cp.float32)
        self._F = cp.zeros((self._fh, self._fw, 4), dtype=cp.float32)
        self._PT = cp.zeros((H, W, 2), dtype=cp.uint8)
        launch_2d(self._kp, W, H, (self._PT, np.int32(W), np.int32(H), np.float32(max(self._sc, 0.2))))
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._h = np.zeros(NPRM, dtype=np.float32)
        self._pal_name = None
        self._drift = 0.0
        self._boil = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        amt = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        m = amt * amt * (3.0 - 2.0 * amt)
        pk = pulse * m

        # host-integrated counters: colour drift and the boil (pulse kicks the boil *rate*)
        self._drift = math.fmod(self._drift + float(p["drift_speed"]) * dt, 1.0)
        self._boil = math.fmod(self._boil + float(p["boil_rate"]) * (1.0 + 1.5 * pk) * dt, 1.0e6)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._h[:12] = palette_coeffs(p["palette"])
        W, H = self.ctx.width, self.ctx.height
        sc = self._sc
        st = min(max(float(p["style"]), 0.0), 2.0)
        wg, wi, wc = max(0.0, 1.0 - st), max(0.0, 1.0 - abs(st - 1.0)), max(0.0, st - 1.0)

        lineA = _ss(0.0, 0.45, m)
        hatchA = _ss(0.2, 0.7, m)
        paperA = _ss(0.45, 1.0, m)

        n = int(math.floor(self._boil))
        f = self._boil - n
        ease = _ss(0.82, 1.0, f)
        layers = min(float(p["hatch_layers"]) + pk, 4.0)
        dens = float(p["hatch_density"])
        spacing = max(sc * (26.0 - 17.0 * dens), 3.5)

        h = self._h
        h[12] = self._drift
        h[13] = float(p["palette_period"])
        for k in range(4):
            jit = (_rnd(n, k) + (_rnd(n + 1, k) - _rnd(n, k)) * ease) - 0.5
            h[14 + k] = PHASES[k] + 0.16 * jit
            h[18 + k] = min(max(layers - k, 0.0), 1.0)
            h[22 + k] = math.cos(ANGLES[k])
            h[26 + k] = math.sin(ANGLES[k])
        h[30] = spacing
        h[31] = hatchA
        h[32] = lineA
        h[33] = paperA
        h[34], h[35], h[36] = wg, wi, wc
        h[37] = float(p["paper_tone"])
        h[38] = float(p["ink_tint"])
        h[39] = (0.8 * wg + 1.0 * wi + 0.9 * wc) * (1.0 + 0.6 * pk)
        h[40] = max(sc, 0.25) * 3.0
        h[41] = 0.8 / spacing
        h[42] = 7.0 + 5.0 * wi
        h[43] = 0.15 * wg + 0.05 * wi + 0.12 * wc
        h[44] = 0.8 * wg + 0.25 * wi + 0.6 * wc
        h[45] = 0.85 * wg + 0.1 * wi + 0.75 * wc
        h[46] = 0.8 * wg + 0.97 * wi + 0.88 * wc
        h[47] = 0.55
        self._prm.set(h)

        lw = float(p["line_weight"])
        s1 = max((0.6 + 1.8 * lw + 0.8 * pk) * sc * 2.0, 0.6)       # half-res px
        gain = 22.0 + 16.0 * lw
        eps = (0.30 * wg + 0.50 * wi + 0.40 * wc) * (1.0 - 0.35 * lw)
        phi = 1.6 * wg + 6.0 * wi + 3.0 * wc

        i32, f32 = np.int32, np.float32
        w1, h1, fw, fh = self._w1, self._h1, self._fw, self._fh
        launch_2d(self._kd, w1, h1, (src, self._C, self._L, i32(W), i32(H), i32(w1), i32(h1)))
        launch_2d(self._kh, w1, h1, (self._L, self._T, i32(w1), i32(h1), f32(s1)))
        launch_2d(self._kv, w1, h1, (self._T, self._D, i32(w1), i32(h1), f32(s1), f32(gain), f32(eps), f32(phi)))
        launch_2d(self._kf, fw, fh, (self._F, i32(fw), i32(fh), f32(FIELD), f32(max(sc, 0.2)),
                                     np.uint32(n & 0xFFFFFF), np.uint32((n + 1) & 0xFFFFFF), f32(ease)))
        launch_2d(self._kc, W, H, (src, self._C, self._D, self._F, self._PT, out, i32(W), i32(H), i32(w1), i32(h1),
                                   i32(fw), i32(fh), f32(FIELD), self._prm))
