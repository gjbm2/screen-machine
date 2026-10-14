"""Edge neon: both pictures pass through a neon-outline state.  The old picture's fill fades to black while its
Sobel edges light up as palette-tinted neon tubes with a two-radius glow; the outlines cross-morph (through a
soft fbm dissolve) into the new picture's outlines; then the new picture's fill returns.  An optional white
overexposure flash fires at the crossover.  On art stills it reads as line art.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/EdgeTransition.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/Overexposure.glsl
Author: Woohyun Kim (gl-transitions EdgeTransition); Ben Zhang (Overexposure)
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c) 2017-present
                gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: EdgeTransition's 3x3 colour-difference edge detector (2|c7-c1| +
  |c2-c6| + |c8-c0| and the transposed dy, delta = length(0.25 (dx + dy) / 2), times edge_brightness) is kept,
  run at half resolution for both inputs with a soft noise knee; edge_thickness is the tap spacing in 4K pixels.
  The edges are coloured through the shared cosine palette (index from luma + a diagonal ramp + drift, the new
  picture's outlines offset by a third of the palette) and neon_tint morphs that to a saturated version of the
  source colour.  Instead of EdgeTransition's global mix the schedule is: a's fill fades out over the first
  ~40%, b's fill returns over the last ~40%, the neon layer is weighted by min(1, 1.8 sin(pi p)), and the
  a->b outline swap is a soft fbm dissolve around p = 0.5.  A two-radius glow (quarter- and eighth-res
  Gaussian blurs of the neon buffer) and a white-hot tube core are added and screen-blended over the fills.
  Overexposure's sin(pi p)-weighted gain is reused as a short flash around p = 0.5 (gain plus white veil).
  Endpoints are forced: p <= 1e-4 copies a, p >= 0.9999 copies b.)

MIT notice (gl-transitions): Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float4 ldf(const float4* __restrict__ buf, int x, int y, int w, int h) {
    x = min(max(x, 0), w - 1); y = min(max(y, 0), h - 1);
    return buf[y * w + x];
}
// Bilinear fetch from a float4 buffer at continuous texel coords (centres at i + 0.5), clamped.
__device__ float4 bilf(const float4* __restrict__ buf, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0, ux = 1.f - tx, uy = 1.f - ty;
    const float4 a = ldf(buf, x0, y0, w, h), b = ldf(buf, x0 + 1, y0, w, h);
    const float4 c = ldf(buf, x0, y0 + 1, w, h), d = ldf(buf, x0 + 1, y0 + 1, w, h);
    return make_float4(uy * (ux * a.x + tx * b.x) + ty * (ux * c.x + tx * d.x),
                       uy * (ux * a.y + tx * b.y) + ty * (ux * c.y + tx * d.y),
                       uy * (ux * a.z + tx * b.z) + ty * (ux * c.z + tx * d.z), 0.f);
}

// EdgeTransition's detector on one input around full-res pixel (cx, cy) with tap spacing t; returns the
// neon colour (0..1, premultiplied by edge strength).  Tap layout c[3*i + j], i along x, j along y.
__device__ float3 neon_of(const uchar4* __restrict__ img, int cx, int cy, int t, int W, int H,
                          float bright, float tint, const float* __restrict__ P, float idx)
{
    const float4 c0 = ld(img, cx - t, cy - t, W, H), c1 = ld(img, cx - t, cy, W, H), c2 = ld(img, cx - t, cy + t, W, H);
    const float4 c3 = ld(img, cx, cy - t, W, H),     c4 = ld(img, cx, cy, W, H),     c5 = ld(img, cx, cy + t, W, H);
    const float4 c6 = ld(img, cx + t, cy - t, W, H), c7 = ld(img, cx + t, cy, W, H), c8 = ld(img, cx + t, cy + t, W, H);
    const float dxr = 2.f * fabsf(c7.x - c1.x) + fabsf(c2.x - c6.x) + fabsf(c8.x - c0.x);
    const float dxg = 2.f * fabsf(c7.y - c1.y) + fabsf(c2.y - c6.y) + fabsf(c8.y - c0.y);
    const float dxb = 2.f * fabsf(c7.z - c1.z) + fabsf(c2.z - c6.z) + fabsf(c8.z - c0.z);
    const float dyr = 2.f * fabsf(c3.x - c5.x) + fabsf(c6.x - c8.x) + fabsf(c0.x - c2.x);
    const float dyg = 2.f * fabsf(c3.y - c5.y) + fabsf(c6.y - c8.y) + fabsf(c0.y - c2.y);
    const float dyb = 2.f * fabsf(c3.z - c5.z) + fabsf(c6.z - c8.z) + fabsf(c0.z - c2.z);
    const float k = 0.125f / 255.f;
    const float sr = (dxr + dyr) * k, sg = (dxg + dyg) * k, sb = (dxb + dyb) * k;
    const float delta = sqrtf(sr * sr + sg * sg + sb * sb);
    float e = clamp01((bright * delta - 0.1f) * (1.f / 0.9f));     // soft knee drops texture noise
    if (e <= 0.f) return make_float3(0.f, 0.f, 0.f);
    e = 1.f - (1.f - e) * (1.f - e);

    const float r = c4.x / 255.f, g = c4.y / 255.f, bl = c4.z / 255.f;
    const float lum = 0.2126f * r + 0.7152f * g + 0.0722f * bl;
    // palette neon, normalised so the tube is always bright
    float3 pc = pal(idx + 0.55f * P[12] * lum, P);
    const float pm = fmaxf(fmaxf(pc.x, pc.y), fmaxf(pc.z, 0.35f));
    pc.x /= pm; pc.y /= pm; pc.z /= pm;
    // source neon: the source colour pushed to full value and extra saturation
    const float sm = fmaxf(fmaxf(r, g), fmaxf(bl, 0.2f));
    float sx = r / sm, sy = g / sm, sz = bl / sm;
    const float sl = 0.2126f * sx + 0.7152f * sy + 0.0722f * sz;
    sx = clamp01(sl + (sx - sl) * 1.5f); sy = clamp01(sl + (sy - sl) * 1.5f); sz = clamp01(sl + (sz - sl) * 1.5f);
    return make_float3(e * (pc.x + (sx - pc.x) * tint), e * (pc.y + (sy - pc.y) * tint),
                       e * (pc.z + (sz - pc.z) * tint));
}

extern "C" __global__
void en_edges(const uchar4* __restrict__ a, const uchar4* __restrict__ b, float4* __restrict__ N,
              const float* __restrict__ P, const int W, const int H, const int W2, const int H2, const int t,
              const float bright, const float tint, const float period, const float drift, const float wmid,
              const float rough, const unsigned int seed, const float aspect)
{
    const int hx = blockIdx.x * blockDim.x + threadIdx.x, hy = blockIdx.y * blockDim.y + threadIdx.y;
    if (hx >= W2 || hy >= H2) return;
    const float u = (hx + 0.5f) / W2, v = (hy + 0.5f) / H2;
    const int cx = min((int)(u * W), W - 1), cy = min((int)(v * H), H - 1);
    const float n = fbm(u * 2.2f * aspect, v * 2.2f, seed);
    const float w = smooth(0.f, 1.f, wmid + (n - 0.5f) * rough);   // a -> b outline dissolve
    const float ramp = period * 0.45f * (0.6f * u + 0.4f * v);
    float3 acc = make_float3(0.f, 0.f, 0.f);
    if (w < 0.999f) {
        const float3 c = neon_of(a, cx, cy, t, W, H, bright, tint, P, ramp + drift);
        acc.x += (1.f - w) * c.x; acc.y += (1.f - w) * c.y; acc.z += (1.f - w) * c.z;
    }
    if (w > 0.001f) {
        const float3 c = neon_of(b, cx, cy, t, W, H, bright, tint, P, ramp + drift + 0.33f);
        acc.x += w * c.x; acc.y += w * c.y; acc.z += w * c.z;
    }
    N[hy * W2 + hx] = make_float4(acc.x, acc.y, acc.z, 0.f);
}

extern "C" __global__
void en_down(const float4* __restrict__ src, const int ws, const int hs, float4* __restrict__ dst,
             const int wd, const int hd)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= wd || y >= hd) return;
    const float4 p = ldf(src, 2 * x, 2 * y, ws, hs), q = ldf(src, 2 * x + 1, 2 * y, ws, hs);
    const float4 r = ldf(src, 2 * x, 2 * y + 1, ws, hs), s = ldf(src, 2 * x + 1, 2 * y + 1, ws, hs);
    dst[y * wd + x] = make_float4(0.25f * (p.x + q.x + r.x + s.x), 0.25f * (p.y + q.y + r.y + s.y),
                                  0.25f * (p.z + q.z + r.z + s.z), 0.f);
}

extern "C" __global__
void en_blur(const float4* __restrict__ src, float4* __restrict__ dst, const int w, const int h,
             const int dx, const int dy, const float inv2s2)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float sx = 0.f, sy = 0.f, sz = 0.f, sw = 0.f;
    #pragma unroll
    for (int k = -7; k <= 7; ++k) {
        const float wt = __expf(-(float)(k * k) * inv2s2);
        const float4 c = ldf(src, x + k * dx, y + k * dy, w, h);
        sx += wt * c.x; sy += wt * c.y; sz += wt * c.z; sw += wt;
    }
    const float iw = 1.f / sw;
    dst[y * w + x] = make_float4(sx * iw, sy * iw, sz * iw, 0.f);
}

extern "C" __global__
void en_comp(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
             const float4* __restrict__ N, const int W2, const int H2,
             const float4* __restrict__ Q, const int W4, const int H4,
             const float4* __restrict__ E, const int W8, const int H8,
             const int W, const int H, const float fa, const float fb, const float L,
             const float g1, const float g2, const float corek, const float hotk, const float fl)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fx = (x + 0.5f) / W, fy = (y + 0.5f) / H;
    float br = 0.f, bg = 0.f, bb = 0.f;
    if (fa > 0.f) { const uchar4 c = a[y * W + x]; br += fa * c.x; bg += fa * c.y; bb += fa * c.z; }
    if (fb > 0.f) { const uchar4 c = b[y * W + x]; br += fb * c.x; bg += fb * c.y; bb += fb * c.z; }
    br *= (1.f / 255.f); bg *= (1.f / 255.f); bb *= (1.f / 255.f);

    const float4 n = bilf(N, fx * W2, fy * H2, W2, H2);
    float nr = corek * n.x, ng = corek * n.y, nb = corek * n.z;
    const float hot = hotk * smooth(0.45f, 1.05f, fmaxf(fmaxf(n.x, n.y), n.z));   // white-hot tube core
    nr += hot; ng += hot; nb += hot;
    if (g1 > 0.f || g2 > 0.f) {
        const float4 q = bilf(Q, fx * W4, fy * H4, W4, H4);
        const float4 e = bilf(E, fx * W8, fy * H8, W8, H8);
        nr += g1 * q.x + g2 * e.x; ng += g1 * q.y + g2 * e.y; nb += g1 * q.z + g2 * e.z;
    }
    nr = clamp01(nr * L); ng = clamp01(ng * L); nb = clamp01(nb * L);
    // screen blend so bright stills do not clip
    float orr = 1.f - (1.f - br) * (1.f - nr);
    float og = 1.f - (1.f - bg) * (1.f - ng);
    float ob = 1.f - (1.f - bb) * (1.f - nb);
    if (fl > 0.f) {                                                // overexposure flash at the crossover
        const float gain = 1.f + 2.5f * fl, veil = 0.55f * fl;
        orr = orr * gain + veil; og = og * gain + veil; ob = ob * gain + veil;
    }
    out[y * W + x] = pack(make_float4(orr * 255.f, og * 255.f, ob * 255.f, 255.f));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hash01(a: int, b: int) -> float:
    h = (a * 2654435761 ^ (b + 0x9E3779B9) * 2246822519) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 2654435761) & 0xFFFFFFFF
    h ^= h >> 13
    return (h & 0xFFFFFF) / 16777216.0


class EdgeNeon(Transition):
    name = "edge_neon"
    label = "Edge neon"
    description = ("The old picture fades to black as its outlines light up as glowing neon; the outlines morph "
                   "into the new picture's, then its fill returns. Optional white flash at the crossover.")
    default_duration_s = 2.0
    params = {
        "neon_tint": num(0.0, 1.0, 0.3, "Neon colour (palette -> source)", tier="regional", basic=True),
        "edge_thickness": num(1.0, 10.0, 3.0, "Edge thickness", step=0.5, unit="px", tier="regional"),
        "edge_brightness": num(1.0, 20.0, 8.0, "Edge brightness", step=0.1, tier="regional"),
        "glow": num(0.0, 2.0, 1.0, "Glow", tier="regional"),
        "flash": num(0.0, 1.0, 0.3, "Crossover flash", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Palette", tier="regional"),
        "palette_period": num(0.1, 4.0, 1.2, "Palette period", tier="regional"),
        "colour_drift": num(0.0, 1.0, 0.2, "Colour drift", unit="/s", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k_edges = mod.get_function("en_edges")
        self._k_down = mod.get_function("en_down")
        self._k_blur = mod.get_function("en_blur")
        self._k_comp = mod.get_function("en_comp")
        w, h = ctx.width, ctx.height
        self._w2, self._h2 = (w + 1) // 2, (h + 1) // 2
        self._w4, self._h4 = (self._w2 + 1) // 2, (self._h2 + 1) // 2
        self._w8, self._h8 = (self._w4 + 1) // 2, (self._h4 + 1) // 2
        f = cp.float32
        self._N = cp.zeros((self._h2, self._w2, 4), dtype=f)
        self._Q = cp.zeros((self._h4, self._w4, 4), dtype=f)
        self._Qt = cp.zeros((self._h4, self._w4, 4), dtype=f)
        self._E = cp.zeros((self._h8, self._w8, 4), dtype=f)
        self._Et = cp.zeros((self._h8, self._w8, 4), dtype=f)
        # 12 palette coefficients plus the palette period at P[12]
        self._P = cp.zeros(13, dtype=cp.float32)
        self._pal_key = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._drift0 = _hash01(self._seed, 7)

    def _sync_palette(self, period: float) -> None:
        name = self.params.get("palette", "neon")
        if name not in PALETTES:
            name = "neon"
        key = (name, period)
        if key != self._pal_key:
            self._P.set(np.asarray(palette_coeffs(name) + [period], np.float32))
            self._pal_key = key

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        q = float(progress)
        if q <= 1e-4:
            cp.copyto(out, a)
            return
        if q >= 0.9999:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        w2, h2, w4, h4, w8, h8 = self._w2, self._h2, self._w4, self._h4, self._w8, self._h8
        f32, i32 = np.float32, np.int32

        period = float(p["palette_period"])
        self._sync_palette(period)
        env = math.sin(math.pi * q)
        L = min(1.0, 1.8 * env)                                      # neon layer (zero at both ends)
        fa = 1.0 - _smooth(0.0, 0.42, q)                             # a's fill fades to black
        fb = _smooth(0.58, 1.0, q)                                   # b's fill returns
        wmid = (q - 0.5) * 2.6 + 0.5                                 # outline cross-morph centre
        fl = float(p["flash"]) * env * math.exp(-((q - 0.5) / 0.075) ** 2)
        secs = q * max(self.duration_s, 0.05)
        drift = (self._drift0 + float(p["colour_drift"]) * secs) % 1.0
        t = max(1, int(round(float(p["edge_thickness"]) * w / 3840.0)))
        glow = float(p["glow"])

        launch_2d(self._k_edges, w2, h2,
                  (a, b, self._N, self._P, i32(w), i32(h), i32(w2), i32(h2), i32(t),
                   f32(p["edge_brightness"]), f32(p["neon_tint"]), f32(period), f32(drift), f32(wmid),
                   f32(1.4), np.uint32(self._seed & 0xFFFFFFFF), f32(w / max(h, 1))))
        if glow > 0.0:
            # radius 1: quarter res, sigma 2.5 texels (~10 px at 4K); radius 2: eighth res, sigma 3 (~24 px)
            launch_2d(self._k_down, w4, h4, (self._N, i32(w2), i32(h2), self._Q, i32(w4), i32(h4)))
            s1 = f32(1.0 / (2.0 * 2.5 * 2.5))
            launch_2d(self._k_blur, w4, h4, (self._Q, self._Qt, i32(w4), i32(h4), i32(1), i32(0), s1))
            launch_2d(self._k_blur, w4, h4, (self._Qt, self._Q, i32(w4), i32(h4), i32(0), i32(1), s1))
            launch_2d(self._k_down, w8, h8, (self._Q, i32(w4), i32(h4), self._E, i32(w8), i32(h8)))
            s2 = f32(1.0 / (2.0 * 3.0 * 3.0))
            launch_2d(self._k_blur, w8, h8, (self._E, self._Et, i32(w8), i32(h8), i32(1), i32(0), s2))
            launch_2d(self._k_blur, w8, h8, (self._Et, self._E, i32(w8), i32(h8), i32(0), i32(1), s2))
        launch_2d(self._k_comp, w, h,
                  (a, b, out, self._N, i32(w2), i32(h2), self._Q, i32(w4), i32(h4), self._E, i32(w8), i32(h8),
                   i32(w), i32(h), f32(fa), f32(fb), f32(L), f32(2.4 * glow), f32(3.2 * glow), f32(1.15),
                   f32(0.55), f32(fl)))
