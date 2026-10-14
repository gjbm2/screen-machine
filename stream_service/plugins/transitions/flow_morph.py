"""Flow morph: a liquid crossfade.  Both pictures are pushed along one flow field, so the old one smears away
and the new one flows in along the same currents.  It spans heat-haze ink (low strength) to the new scene
pushing up through the old (high strength, colour-morph or luma map).

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/displacement.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/morph.glsl
Author: Travis Fischer (displacement); paniq (morph)
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the two shaders share one structure -- from sampled at uv + p * D,
  to sampled at uv - (1 - p) * D, mixed by p -- and differ only in the displacement map D: displacement.glsl
  reads an extra grayscale texture (x only), morph.glsl uses the mean of both pictures' colour vectors
  ((rg + b) / 2 * 2 - 1).  They are merged here with a continuous map_source knob: 0 = an in-kernel fbm map
  (the grayscale texture, with an fbm angle field for 2D), 0.5 = paniq's colour-vector morph, 1 = the gradient
  of the incoming picture's luma (new: its bright shapes swell and shove the old picture aside).  The colour and
  luma maps are box-filtered into a small mip pyramid and read at a blur radius set by noise_scale, so the field
  is smooth (no per-pixel tearing, HEVC friendly); anisotropy scales the vertical part (0 = displacement.glsl's
  horizontal-only push); flow drifts the noise domain and turns the field during the transition; smear splits
  the push into 1-3 steps that follow the field's streamlines (semi-Lagrangian), so pictures bend along curved
  currents instead of shifting rigidly; strength also shapes the mix so, when strong, the map's high regions
  turn over first; samples mirror at the frame edges instead of clamping (no edge streaks); y-down pixel frames
  (the colour vector's y is flipped to match the GLSL's y-up uv); the field is computed at 1/4 resolution.)

Displacement: field D(q) (pixels, full res) = amp * dir, amp = H * (0.02 + 0.5 * strength^1.5).  Output at q
shows a at Phi(q, +p) and b at Phi(q, -(1 - p)) where Phi follows D in `smear` steps; content of both pictures
therefore travels the same way (-D), the old one leaving, the new one arriving and settling to 1:1.

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

NLEV = 6          # pyramid levels: 1/4, 1/8, ..., 1/128 of full res

_SRC = HELPERS + r"""
#define NLEV 6
__device__ __forceinline__ float mirror_c(float v, float n) {      // reflect a continuous coord into [0, n]
    v = fabsf(v);
    if (v > n) { v = fmodf(v, 2.f * n); if (v > n) v = 2.f * n - v; }
    return v;
}

// 1) features at 1/4 res: exact 4x4 box average of both pictures -> (colour vector mean x, y (y-up), luma b, luma a)
extern "C" __global__
void fm_feat(const uchar4* __restrict__ a, const uchar4* __restrict__ b, float4* __restrict__ f,
             const int W, const int H, const int mw, const int mh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= mw || j >= mh) return;
    const float x0 = 4.f * i, y0 = 4.f * j;
    float4 sa = make_float4(0.f, 0.f, 0.f, 0.f), sb = sa;
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const float fx = x0 + 1.f + 2.f * (k & 1), fy = y0 + 1.f + 2.f * (k >> 1);
        const float4 ca = bilin(a, fx, fy, W, H), cb = bilin(b, fx, fy, W, H);
        sa.x += ca.x; sa.y += ca.y; sa.z += ca.z;
        sb.x += cb.x; sb.y += cb.y; sb.z += cb.z;
    }
    const float s = 1.f / (4.f * 255.f);
    sa.x *= s; sa.y *= s; sa.z *= s; sb.x *= s; sb.y *= s; sb.z *= s;
    // morph.glsl: o = ((c.rg + c.b) * 0.5) * 2 - 1, oc = mix(oa, ob, 0.5)
    const float ox = 0.5f * ((sa.x + sa.z) + (sb.x + sb.z)) - 1.f;
    const float oy = 0.5f * ((sa.y + sa.z) + (sb.y + sb.z)) - 1.f;
    const float lb = 0.2126f * sb.x + 0.7152f * sb.y + 0.0722f * sb.z;
    const float la = 0.2126f * sa.x + 0.7152f * sa.y + 0.0722f * sa.z;
    f[j * mw + i] = make_float4(ox, oy, lb, la);
}

// 2) 2x2 box downsample of one pyramid level into the next
extern "C" __global__
void fm_down(const float4* __restrict__ s, float4* __restrict__ d, const int sw, const int sh, const int dw, const int dh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= dw || j >= dh) return;
    const int x0 = min(2 * i, sw - 1), x1 = min(2 * i + 1, sw - 1);
    const int y0 = min(2 * j, sh - 1), y1 = min(2 * j + 1, sh - 1);
    const float4 p = s[y0 * sw + x0], q = s[y0 * sw + x1], r = s[y1 * sw + x0], t = s[y1 * sw + x1];
    d[j * dw + i] = make_float4(0.25f * (p.x + q.x + r.x + t.x), 0.25f * (p.y + q.y + r.y + t.y),
                                0.25f * (p.z + q.z + r.z + t.z), 0.25f * (p.w + q.w + r.w + t.w));
}

struct Pyr { const float4* lv[NLEV]; int w[NLEV]; int h[NLEV]; };

__device__ float4 lv_bilin(const float4* __restrict__ L, const int lw, const int lh, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const int x0 = (int)floorf(u), y0 = (int)floorf(v);
    const float tx = u - x0, ty = v - y0;
    const int xa = min(max(x0, 0), lw - 1), xb = min(max(x0 + 1, 0), lw - 1);
    const int ya = min(max(y0, 0), lh - 1), yb = min(max(y0 + 1, 0), lh - 1);
    const float4 p = L[ya * lw + xa], q = L[ya * lw + xb], r = L[yb * lw + xa], t = L[yb * lw + xb];
    const float a = (1.f - tx) * (1.f - ty), b = tx * (1.f - ty), c = (1.f - tx) * ty, d = tx * ty;
    return make_float4(a * p.x + b * q.x + c * r.x + d * t.x, a * p.y + b * q.y + c * r.y + d * t.y,
                       a * p.z + b * q.z + c * r.z + d * t.z, a * p.w + b * q.w + c * r.w + d * t.w);
}

// trilinear read of the pyramid at full-res pixel coords (fx, fy), level l0 + lt (texel size 4 * 2^l)
__device__ float4 pyr_read(const Pyr& P, const int l0, const float lt, float fx, float fy) {
    const float s0 = 1.f / (float)(4 << l0);
    float4 c = lv_bilin(P.lv[l0], P.w[l0], P.h[l0], fx * s0, fy * s0);
    if (lt > 0.f && l0 + 1 < NLEV) {
        const float s1 = 0.5f * s0;
        const float4 d = lv_bilin(P.lv[l0 + 1], P.w[l0 + 1], P.h[l0 + 1], fx * s1, fy * s1);
        c = make_float4(c.x + (d.x - c.x) * lt, c.y + (d.y - c.y) * lt, c.z + (d.z - c.z) * lt, c.w + (d.w - c.w) * lt);
    }
    return c;
}

// 3) the displacement field at 1/4 res: D (pixels) and the mix-order scalar s (0..1)
extern "C" __global__
void fm_field(float2* __restrict__ fd, float* __restrict__ fs,
              const float4* L0, const float4* L1, const float4* L2, const float4* L3, const float4* L4,
              const float4* L5, const int use_pyr,
              const int W, const int H, const int mw, const int mh,
              const float wn, const float wc, const float wl,             // map weights: noise, colour, luma
              const float amp, const float inv_scale, const float aniso,
              const float nox, const float noy, const float rc, const float rs,   // noise drift, field turn
              const int l0, const float lt, const float hstep, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= mw || j >= mh) return;
    const float fx = 4.f * i + 2.f, fy = 4.f * j + 2.f;                 // texel centre, full-res pixels
    Pyr P;
    P.lv[0] = L0; P.lv[1] = L1; P.lv[2] = L2; P.lv[3] = L3; P.lv[4] = L4; P.lv[5] = L5;
    #pragma unroll
    for (int k = 0; k < NLEV; ++k) { const int f = 4 << k; P.w[k] = (W + f - 1) / f; P.h[k] = (H + f - 1) / f; }
    float dx = 0.f, dy = 0.f, s = 0.f;
    if (wn > 0.f) {                                                    // displacement.glsl's map, as fbm
        const float u = fx * inv_scale + nox, v = fy * inv_scale + noy;
        const float n1 = clamp01((fbm(u, v, seed) - 0.18f) / 0.64f);
        const float n2 = clamp01((fbm(0.61f * u + 5.3f, 0.61f * v - 2.1f, seed + 101u) - 0.18f) / 0.64f);
        const float th = aniso * 3.14159265f * (2.f * n2 - 1.f) * 1.15f;     // 0: horizontal only (original)
        float sn, cs; __sincosf(th, &sn, &cs);
        dx += wn * n1 * cs; dy += wn * n1 * sn; s += wn * n1;
    }
    if (use_pyr) {
        const float4 c0 = pyr_read(P, l0, lt, fx, fy);
        const float4 xp = pyr_read(P, l0, lt, fx + hstep, fy), xm = pyr_read(P, l0, lt, fx - hstep, fy);
        const float4 yp = pyr_read(P, l0, lt, fx, fy + hstep), ym = pyr_read(P, l0, lt, fx, fy - hstep);
        if (wc > 0.f) {                                                // morph.glsl's colour vector (blurred)
            const float ox = 0.4f * c0.x + 0.15f * (xp.x + xm.x + yp.x + ym.x);
            const float oy = 0.4f * c0.y + 0.15f * (xp.y + xm.y + yp.y + ym.y);
            dx += wc * ox; dy -= wc * oy * aniso;                      // GLSL uv is y-up
            s += wc * clamp01(0.5f + 0.25f * (ox + oy));
        }
        if (wl > 0.f) {                                                // gradient of the incoming luma
            float gx = 2.2f * (xp.z - xm.z), gy = 2.2f * (yp.z - ym.z) * aniso;
            const float k = rsqrtf(1.f + gx * gx + gy * gy);           // soft saturation, |g| < 1
            dx += wl * gx * k; dy += wl * gy * k;
            s += wl * clamp01(0.4f * c0.z + 0.15f * (xp.z + xm.z + yp.z + ym.z));
        }
    }
    const float rx = rc * dx - rs * dy, ry = rs * dx + rc * dy;        // flow: the field turns over progress
    fd[j * mw + i] = make_float2(rx * amp, ry * amp);
    fs[j * mw + i] = clamp01(s);
}

__device__ __forceinline__ float2 fld(const float2* __restrict__ F, const int mw, const int mh, float fx, float fy) {
    float u = fx * 0.25f - 0.5f, v = fy * 0.25f - 0.5f;
    const int x0 = (int)floorf(u), y0 = (int)floorf(v);
    const float tx = u - x0, ty = v - y0;
    const int xa = min(max(x0, 0), mw - 1), xb = min(max(x0 + 1, 0), mw - 1);
    const int ya = min(max(y0, 0), mh - 1), yb = min(max(y0 + 1, 0), mh - 1);
    const float2 p = F[ya * mw + xa], q = F[ya * mw + xb], r = F[yb * mw + xa], t = F[yb * mw + xb];
    const float a = (1.f - tx) * (1.f - ty), b = tx * (1.f - ty), c = (1.f - tx) * ty, d = tx * ty;
    return make_float2(a * p.x + b * q.x + c * r.x + d * t.x, a * p.y + b * q.y + c * r.y + d * t.y);
}
__device__ __forceinline__ float fsc(const float* __restrict__ F, const int mw, const int mh, float fx, float fy) {
    float u = fx * 0.25f - 0.5f, v = fy * 0.25f - 0.5f;
    const int x0 = (int)floorf(u), y0 = (int)floorf(v);
    const float tx = u - x0, ty = v - y0;
    const int xa = min(max(x0, 0), mw - 1), xb = min(max(x0 + 1, 0), mw - 1);
    const int ya = min(max(y0, 0), mh - 1), yb = min(max(y0 + 1, 0), mh - 1);
    return (1.f - ty) * ((1.f - tx) * F[ya * mw + xa] + tx * F[ya * mw + xb])
         + ty * ((1.f - tx) * F[yb * mw + xa] + tx * F[yb * mw + xb]);
}

// 4) the morph: a along +p * D, b along -(1 - p) * D (streamlines in `steps` steps), mixed by a shaped progress
extern "C" __global__
void fm_main(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
             const float2* __restrict__ F, const float* __restrict__ S, const int W, const int H,
             const int mw, const int mh, const float p, const int steps, const float shape)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float qx = x + 0.5f, qy = y + 0.5f;
    // shaped local progress: high-s regions turn over first (shape 0 = the originals' uniform progress)
    float lp = p;
    if (shape > 0.f) {
        const float s = fsc(S, mw, mh, qx, qy);
        lp = clamp01((p - shape * (1.f - s)) / (1.f - shape));
    }
    const float ka = lp / steps, kb = (1.f - lp) / steps;
    float ax = qx, ay = qy, bx = qx, by = qy;
    for (int k = 0; k < steps; ++k) {
        const float2 da = fld(F, mw, mh, ax, ay);
        ax += ka * da.x; ay += ka * da.y;
        const float2 db = k == 0 ? da : fld(F, mw, mh, bx, by);
        bx -= kb * db.x; by -= kb * db.y;
    }
    const float Wf = (float)W, Hf = (float)H;
    const float4 ca = bilin(a, mirror_c(ax, Wf), mirror_c(ay, Hf), W, H);
    const float4 cb = bilin(b, mirror_c(bx, Wf), mirror_c(by, Hf), W, H);
    out[y * W + x] = pack(mix4(ca, cb, lp));
}
"""


class FlowMorph(Transition):
    name = "flow_morph"
    label = "Flow morph"
    description = ("A liquid crossfade: both pictures are pushed along one flow field, so the old one smears away "
                   "and the new one flows into its shapes.")
    default_duration_s = 2.0
    params = {
        "strength": num(0.0, 1.0, 0.45, "Strength", tier="regional", basic=True),
        "map_source": num(0.0, 1.0, 0.0, "Map (noise / colour morph / luma push)", tier="regional"),
        "noise_scale": num(0.05, 1.5, 0.45, "Map scale", unit="× height", tier="regional"),
        "anisotropy": num(0.0, 1.0, 0.6, "Anisotropy (horizontal -> 2D)", tier="regional"),
        "flow": num(0.0, 1.0, 0.35, "Flow (map drift)", tier="regional"),
        "smear": num(1, 3, 2, "Smear steps", tier="structural", integer=True),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        W, H = ctx.width, ctx.height
        s = int(self.params["seed"])
        self._seed = np.uint32(np.random.default_rng().integers(0, 2**31) if s < 0 else s)
        mod = raw_module(_SRC)
        self._kf, self._kd = mod.get_function("fm_feat"), mod.get_function("fm_down")
        self._kfield, self._km = mod.get_function("fm_field"), mod.get_function("fm_main")
        self._mw, self._mh = (W + 3) // 4, (H + 3) // 4
        self._levels = []
        for L in range(NLEV):
            f = 4 << L
            self._levels.append(cp.zeros(((H + f - 1) // f, (W + f - 1) // f, 4), dtype=cp.float32))
        self._fd = cp.zeros((self._mh, self._mw, 2), dtype=cp.float32)
        self._fs = cp.zeros((self._mh, self._mw), dtype=cp.float32)

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        W, H, mw, mh = self.ctx.width, self.ctx.height, self._mw, self._mh
        pr = float(progress)
        st = float(min(max(p["strength"], 0.0), 1.0))
        ms = float(min(max(p["map_source"], 0.0), 1.0))
        wn, wc, wl = max(0.0, 1.0 - 2.0 * ms), 1.0 - abs(2.0 * ms - 1.0), max(0.0, 2.0 * ms - 1.0)
        scale = float(max(p["noise_scale"], 0.01))
        flow = float(p["flow"])
        amp = H * (0.02 + 0.5 * st ** 1.5)
        use_pyr = wc + wl > 0.0
        if use_pyr:
            launch_2d(self._kf, mw, mh, (a, b, self._levels[0], np.int32(W), np.int32(H), np.int32(mw), np.int32(mh)))
            for L in range(NLEV - 1):
                s_, d_ = self._levels[L], self._levels[L + 1]
                launch_2d(self._kd, d_.shape[1], d_.shape[0],
                          (s_, d_, np.int32(s_.shape[1]), np.int32(s_.shape[0]),
                           np.int32(d_.shape[1]), np.int32(d_.shape[0])))
        # blur radius for the colour / luma maps, and the pyramid level whose texel is ~ half of it
        r = 0.25 * scale * H
        lc = min(max(math.log2(max(r, 1.0) / 8.0), 0.0), NLEV - 1.0)
        l0 = min(int(lc), NLEV - 2)
        lt = lc - l0
        ang = flow * pr * 1.4                                     # field turn over progress (rad)
        drift = flow * pr * 1.6                                   # noise domain drift (noise units)
        launch_2d(self._kfield, mw, mh,
                  (self._fd, self._fs, *self._levels, np.int32(use_pyr), np.int32(W), np.int32(H), np.int32(mw),
                   np.int32(mh), np.float32(wn), np.float32(wc), np.float32(wl), np.float32(amp),
                   np.float32(1.0 / (scale * H)), np.float32(p["anisotropy"]),
                   np.float32(drift * 0.8), np.float32(-drift * 0.45),
                   np.float32(math.cos(ang)), np.float32(math.sin(ang)),
                   np.int32(l0), np.float32(lt), np.float32(0.5 * r), self._seed))
        shape = 0.55 * st ** 1.2
        launch_2d(self._km, W, H,
                  (a, b, out, self._fd, self._fs, np.int32(W), np.int32(H), np.int32(mw), np.int32(mh),
                   np.float32(pr), np.int32(p["smear"]), np.float32(shape)))
