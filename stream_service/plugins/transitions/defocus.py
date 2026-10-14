"""Defocus: a cinematic rack focus.  The old scene drifts out of focus into soft bokeh (bright points bloom into
iris-shaped discs), the swap happens while the lens is defocused, and focus racks back in on the new scene.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/DefocusBlur.glsl
        https://github.com/spite/Wagner/blob/master/fragment-shaders/poisson-disc-blur-fs.glsl
Author: Sergey Kosarevsky (DefocusBlur, ported from https://gist.github.com/corporateshark/b9f8e5675c647e615419);
        Poisson-disc taps from Jaume Sanchez (spite), Wagner
License: MIT   (DefocusBlur.glsl header: "// License: MIT"; gl-transitions repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors"; Wagner is MIT-licensed per its repository)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: DefocusBlur's triangle blur ramp D(T) and global mix(C0, C1, T) become a
  rack-focus curve (smooth defocus to a held peak, smooth refocus) with the crossfade confined to a window around
  the peak, so the swap happens out of focus; the blur is done in (approximate, gamma-2) linear light with
  highlight weighting (bokeh_bloom) so bright points become bokeh discs; small radii gather 8 of Wagner's
  Poisson taps at full resolution with a hashed per-2x2 rotation (disk_rotation), large radii gather a
  24-tap golden-angle disc at quarter resolution from a 3-level prefiltered pyramid (trilinear, level picked by
  tap spacing so the disc has no gaps), optionally squared into a hexagonal iris; the two paths cross over
  smoothly by radius.  Added: focus breathing (the slight magnification change of a real lens while focusing)
  and a lateral colour fringe in the defocused image.  Crossfade and blur both run on the mixed light, since a
  uniform crossfade commutes with the blur.)

Endpoints: p <= 0 copies a, p >= 1 copies b; the blur, breathing and fringe all reach 0 at both ends, so the
transition is continuous into and out of the pure frames.

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
from stream_service.plugins._lib.params import num

N_DISC = 24                      # golden-angle taps in the quarter-res bokeh gather
_DISC_SPACING = math.sqrt(math.pi / N_DISC)   # mean tap spacing / radius
_Q_ON, _Q_FULL = 5.0, 12.0      # full-res radius (px) over which the quarter-res bokeh takes over

_SRC = HELPERS + r"""
#define TWO_PI 6.2831853f

// highlight weight on an sRGB-encoded colour (0..255): bright points dominate the disc average
__device__ __forceinline__ float hl_weight(float4 c, float bloom) {
    const float t = smooth(0.55f, 1.f, luma(c));
    return 1.f + bloom * 14.f * t * t * t;
}
__device__ __forceinline__ float4 to_lin(float4 c) {
    const float k = 1.f / 255.f;
    return make_float4(c.x * c.x * k * k, c.y * c.y * k * k, c.z * c.z * k * k, 1.f);
}
// mixed light at continuous pixel coords: only the images the crossfade needs are sampled
__device__ __forceinline__ float4 mixed_srgb(const uchar4* __restrict__ a, const uchar4* __restrict__ b,
                                             float fx, float fy, int W, int H, float m) {
    if (m <= 0.f) return bilin(a, fx, fy, W, H);
    if (m >= 1.f) return bilin(b, fx, fy, W, H);
    const float4 ca = bilin(a, fx, fy, W, H), cb = bilin(b, fx, fy, W, H);
    // mix in linear light, return sRGB-encoded for the highlight test
    const float4 la = to_lin(ca), lb = to_lin(cb);
    return make_float4(255.f * sqrtf(la.x + (lb.x - la.x) * m), 255.f * sqrtf(la.y + (lb.y - la.y) * m),
                       255.f * sqrtf(la.z + (lb.z - la.z) * m), 255.f);
}
__device__ __forceinline__ float4 ldf(const float4* __restrict__ img, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1);
    return img[y * W + x];
}
__device__ float4 bilinf(const float4* __restrict__ img, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0, u = 1.f - tx, v = 1.f - ty;
    const float4 p = ldf(img, x0, y0, W, H), q = ldf(img, x0 + 1, y0, W, H);
    const float4 r = ldf(img, x0, y0 + 1, W, H), s = ldf(img, x0 + 1, y0 + 1, W, H);
    return make_float4(v * (u * p.x + tx * q.x) + ty * (u * r.x + tx * s.x),
                       v * (u * p.y + tx * q.y) + ty * (u * r.y + tx * s.y),
                       v * (u * p.z + tx * q.z) + ty * (u * r.z + tx * s.z),
                       v * (u * p.w + tx * q.w) + ty * (u * r.w + tx * s.w));
}

// 1) quarter-res prefilter of the (breathed, crossfaded) frame: premultiplied (lin * w, w), a 4x4 box
extern "C" __global__
void df_prefilter(const uchar4* __restrict__ a, const uchar4* __restrict__ b, float4* __restrict__ Q,
                  const int W, const int H, const int Wq, const int Hq, const float m, const float zs,
                  const float bloom)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Wq || y >= Hq) return;
    const float cx = 0.5f * W, cy = 0.5f * H;
    float4 acc = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const float px = 4.f * x + ((j & 1) ? 3.f : 1.f), py = 4.f * y + ((j & 2) ? 3.f : 1.f);
        const float sx = cx + (px - cx) * zs, sy = cy + (py - cy) * zs;
        const float4 c = mixed_srgb(a, b, sx, sy, W, H, m);
        const float w = hl_weight(c, bloom);
        const float4 l = to_lin(c);
        acc.x += l.x * w; acc.y += l.y * w; acc.z += l.z * w; acc.w += w;
    }
    Q[y * Wq + x] = make_float4(0.25f * acc.x, 0.25f * acc.y, 0.25f * acc.z, 0.25f * acc.w);
}

// 2) 2x2 box downsample (pyramid level)
extern "C" __global__
void df_down2(const float4* __restrict__ src, float4* __restrict__ dst, const int Ws, const int Hs,
              const int Wd, const int Hd)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Wd || y >= Hd) return;
    const float4 p = ldf(src, 2 * x, 2 * y, Ws, Hs), q = ldf(src, 2 * x + 1, 2 * y, Ws, Hs);
    const float4 r = ldf(src, 2 * x, 2 * y + 1, Ws, Hs), s = ldf(src, 2 * x + 1, 2 * y + 1, Ws, Hs);
    dst[y * Wd + x] = make_float4(0.25f * (p.x + q.x + r.x + s.x), 0.25f * (p.y + q.y + r.y + s.y),
                                  0.25f * (p.z + q.z + r.z + s.z), 0.25f * (p.w + q.w + r.w + s.w));
}

__device__ __forceinline__ float4 lvl_sample(int k, const float4* __restrict__ Q, const float4* __restrict__ E,
                                             const float4* __restrict__ S, float qx, float qy,
                                             int Wq, int Hq, int We, int He, int Ws, int Hs) {
    if (k == 0) return bilinf(Q, qx, qy, Wq, Hq);
    if (k == 1) return bilinf(E, 0.5f * qx, 0.5f * qy, We, He);
    return bilinf(S, 0.25f * qx, 0.25f * qy, Ws, Hs);
}

// 3) quarter-res bokeh gather: golden-angle disc, per-pixel hashed rotation, optional hexagonal iris
extern "C" __global__
void df_gather(const float4* __restrict__ Q, const float4* __restrict__ E, const float4* __restrict__ S,
               float4* __restrict__ G, const float2* __restrict__ taps, const int n,
               const int Wq, const int Hq, const int We, const int He, const int Ws, const int Hs,
               const float rq, const int l0, const float lf, const float rot_amt, const float iris,
               const float spin, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Wq || y >= Hq) return;
    float sn, cs;
    __sincosf(spin + TWO_PI * rot_amt * hash1(x, y, seed), &sn, &cs);
    const float ap = 0.8660254f;                                   // hexagon apothem (circumradius 1)
    const float qx0 = x + 0.5f, qy0 = y + 0.5f;
    const int l1 = min(l0 + 1, 2);
    float4 acc = make_float4(0.f, 0.f, 0.f, 0.f);
    for (int i = 0; i < n; ++i) {
        const float2 t = taps[i];
        float vx = t.x * cs - t.y * sn, vy = t.x * sn + t.y * cs;
        if (iris > 0.f) {                                          // square the disc into a fixed hexagon
            const float rr = sqrtf(vx * vx + vy * vy) + 1e-6f;
            const float ux = vx / rr, uy = vy / rr;
            const float d = fmaxf(fabsf(uy), fmaxf(fabsf(0.8660254f * ux + 0.5f * uy),
                                                   fabsf(0.8660254f * ux - 0.5f * uy)));
            const float k = 1.f + iris * (ap / fmaxf(d, ap) - 1.f);
            vx *= k; vy *= k;
        }
        const float sx = qx0 + vx * rq, sy = qy0 + vy * rq;
        float4 c = lvl_sample(l0, Q, E, S, sx, sy, Wq, Hq, We, He, Ws, Hs);
        if (lf > 0.f) {
            const float4 c1 = lvl_sample(l1, Q, E, S, sx, sy, Wq, Hq, We, He, Ws, Hs);
            c.x += (c1.x - c.x) * lf; c.y += (c1.y - c.y) * lf; c.z += (c1.z - c.z) * lf; c.w += (c1.w - c.w) * lf;
        }
        acc.x += c.x; acc.y += c.y; acc.z += c.z; acc.w += c.w;
    }
    G[y * Wq + x] = acc;                                           // premultiplied; normalised on composite
}

__device__ __forceinline__ float g_chan(const float4* __restrict__ G, float fx, float fy, int Wq, int Hq, int ch) {
    const float4 c = bilinf(G, fx, fy, Wq, Hq);
    const float inv = 1.f / fmaxf(c.w, 1e-6f);
    return (ch == 0 ? c.x : (ch == 1 ? c.y : c.z)) * inv;
}

// 4) full-res composite: sharp / small-radius Poisson gather, crossing over to the upsampled bokeh
extern "C" __global__
void df_composite(const uchar4* __restrict__ a, const uchar4* __restrict__ b, const float4* __restrict__ G,
                  uchar4* __restrict__ out, const int W, const int H, const int Wq, const int Hq,
                  const float m, const float zs, const float r_px, const float wq, const float bloom,
                  const float rot_amt, const float fringe, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = 0.5f * W, cy = 0.5f * H;
    const float px = x + 0.5f, py = y + 0.5f;
    float3 lin = make_float3(0.f, 0.f, 0.f);

    if (wq < 1.f) {
        const float sx = cx + (px - cx) * zs, sy = cy + (py - cy) * zs;
        float4 d;
        if (r_px < 0.35f) {
            d = to_lin(mixed_srgb(a, b, sx, sy, W, H, m));
        } else {
            // centre + 8 Poisson taps (Wagner), rotated per 2x2 block, highlight-weighted, in linear light
            const float tx[8] = {-0.326f, -0.840f, -0.696f, -0.203f, 0.962f, 0.473f, 0.519f, 0.185f};
            const float ty[8] = {-0.406f, -0.074f, 0.457f, 0.621f, -0.195f, -0.480f, 0.767f, -0.893f};
            float sn, cs;
            __sincosf(TWO_PI * rot_amt * hash1(x >> 1, y >> 1, seed ^ 0x51ed27u), &sn, &cs);
            float4 c = mixed_srgb(a, b, sx, sy, W, H, m);
            float w = hl_weight(c, bloom);
            float4 l = to_lin(c);
            float4 acc = make_float4(l.x * w, l.y * w, l.z * w, w);
            #pragma unroll
            for (int i = 0; i < 8; ++i) {
                const float ox = (tx[i] * cs - ty[i] * sn) * r_px, oy = (tx[i] * sn + ty[i] * cs) * r_px;
                c = mixed_srgb(a, b, sx + ox, sy + oy, W, H, m);
                w = hl_weight(c, bloom);
                l = to_lin(c);
                acc.x += l.x * w; acc.y += l.y * w; acc.z += l.z * w; acc.w += w;
            }
            const float inv = 1.f / acc.w;
            d = make_float4(acc.x * inv, acc.y * inv, acc.z * inv, 1.f);
        }
        lin = make_float3(d.x * (1.f - wq), d.y * (1.f - wq), d.z * (1.f - wq));
    }
    if (wq > 0.f) {
        const float qx = 0.25f * px, qy = 0.25f * py;
        float r, g, bl;
        if (fringe > 0.f) {                                        // lateral colour: red out, blue in
            const float qcx = 0.25f * cx, qcy = 0.25f * cy;
            const float kr = 1.f / (1.f + fringe), kb = 1.f / (1.f - fringe);
            r = g_chan(G, qcx + (qx - qcx) * kr, qcy + (qy - qcy) * kr, Wq, Hq, 0);
            const float4 cg = bilinf(G, qx, qy, Wq, Hq);
            g = cg.y / fmaxf(cg.w, 1e-6f);
            bl = g_chan(G, qcx + (qx - qcx) * kb, qcy + (qy - qcy) * kb, Wq, Hq, 2);
        } else {
            const float4 c = bilinf(G, qx, qy, Wq, Hq);
            const float inv = 1.f / fmaxf(c.w, 1e-6f);
            r = c.x * inv; g = c.y * inv; bl = c.z * inv;
        }
        lin.x += r * wq; lin.y += g * wq; lin.z += bl * wq;
    }
    out[y * W + x] = pack(make_float4(255.f * sqrtf(fmaxf(lin.x, 0.f)), 255.f * sqrtf(fmaxf(lin.y, 0.f)),
                                      255.f * sqrtf(fmaxf(lin.z, 0.f)), 255.f));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class Defocus(Transition):
    name = "defocus"
    label = "Defocus (rack focus)"
    description = ("A cinematic rack focus: the old scene blurs out into soft bokeh, the swap happens while "
                   "defocused, and focus racks back in on the new scene.")
    default_duration_s = 2.5
    params = {
        "blur_size": num(0.0, 0.05, 0.022, "Blur size", step=0.001, unit="× height", tier="regional", basic=True),
        "bokeh_bloom": num(0.0, 1.0, 0.55, "Bokeh bloom (highlight weight)", tier="regional"),
        "disk_rotation": num(0.0, 1.0, 0.7, "Tap scramble (hides the pattern)", tier="regional"),
        "crossfade_window": num(0.1, 1.0, 0.35, "Crossfade window", tier="regional"),
        "iris_shape": num(0.0, 1.0, 0.0, "Iris (round -> hexagonal)", tier="regional"),
        "focus_breath": num(0.0, 1.0, 0.35, "Focus breathing", tier="regional"),
        "fringe": num(0.0, 1.0, 0.0, "Colour fringe", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        w, h = ctx.width, ctx.height
        self._wq, self._hq = (w + 3) // 4, (h + 3) // 4
        self._we, self._he = (self._wq + 1) // 2, (self._hq + 1) // 2
        self._ws, self._hs = (self._we + 1) // 2, (self._he + 1) // 2
        self._Q = cp.zeros((self._hq, self._wq, 4), dtype=cp.float32)
        self._E = cp.zeros((self._he, self._we, 4), dtype=cp.float32)
        self._S = cp.zeros((self._hs, self._ws, 4), dtype=cp.float32)
        self._G = cp.zeros((self._hq, self._wq, 4), dtype=cp.float32)
        i = np.arange(N_DISC, dtype=np.float64)
        rr = np.sqrt((i + 0.5) / N_DISC)
        th = i * 2.399963229728653
        taps = np.stack([rr * np.cos(th), rr * np.sin(th)], 1).astype(np.float32)
        self._taps = cp.asarray(taps.ravel())
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._spin = float(np.random.default_rng(self._seed).uniform(0.0, 2.0 * math.pi))
        mod = raw_module(_SRC)
        self._k_pre = mod.get_function("df_prefilter")
        self._k_down = mod.get_function("df_down2")
        self._k_gather = mod.get_function("df_gather")
        self._k_comp = mod.get_function("df_composite")

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        win = min(max(float(p["crossfade_window"]), 0.1), 1.0)
        # crossfade inside the window; defocus ramps to its peak by the window's core and holds through it
        m = _smooth(0.5 - 0.5 * win, 0.5 + 0.5 * win, progress)
        hh = 0.25 * win
        f = _smooth(0.0, 0.5 - hh, progress) if progress <= 0.5 else 1.0 - _smooth(0.5 + hh, 1.0, progress)
        r_px = float(p["blur_size"]) * h * f
        zs = 1.0 / (1.0 + 0.06 * float(p["focus_breath"]) * f)       # the lens magnifies a little as it defocuses
        bloom = float(p["bokeh_bloom"])
        rot = float(p["disk_rotation"])
        fringe = 0.012 * float(p["fringe"]) * f
        wq = _smooth(_Q_ON, _Q_FULL, r_px)
        seed = np.uint32(self._seed & 0xFFFFFFFF)

        if wq > 0.0:
            wq4, hq4 = self._wq, self._hq
            launch_2d(self._k_pre, wq4, hq4,
                      (a, b, self._Q, np.int32(w), np.int32(h), np.int32(wq4), np.int32(hq4),
                       np.float32(m), np.float32(zs), np.float32(bloom)))
            rq = r_px / 4.0
            spacing = rq * _DISC_SPACING
            lod = min(max(math.log2(max(spacing / 1.25, 1e-6)), 0.0), 2.0)
            l0 = min(int(lod), 2)
            lf = lod - l0 if l0 < 2 else 0.0
            if lod > 0.0:
                launch_2d(self._k_down, self._we, self._he,
                          (self._Q, self._E, np.int32(wq4), np.int32(hq4), np.int32(self._we), np.int32(self._he)))
            if lod > 1.0:
                launch_2d(self._k_down, self._ws, self._hs,
                          (self._E, self._S, np.int32(self._we), np.int32(self._he),
                           np.int32(self._ws), np.int32(self._hs)))
            launch_2d(self._k_gather, wq4, hq4,
                      (self._Q, self._E, self._S, self._G, self._taps, np.int32(N_DISC),
                       np.int32(wq4), np.int32(hq4), np.int32(self._we), np.int32(self._he),
                       np.int32(self._ws), np.int32(self._hs), np.float32(rq), np.int32(l0), np.float32(lf),
                       np.float32(rot), np.float32(p["iris_shape"]), np.float32(self._spin), seed))

        launch_2d(self._k_comp, w, h,
                  (a, b, self._G, out, np.int32(w), np.int32(h), np.int32(self._wq), np.int32(self._hq),
                   np.float32(m), np.float32(zs), np.float32(r_px), np.float32(wq), np.float32(bloom),
                   np.float32(rot), np.float32(fringe), seed))
