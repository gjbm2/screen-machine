"""Colour distance: pixels switch to the new picture in order of how alike the two pictures already are there.
Matching regions change first, the most different last, then a soft tail sweeps up the rest; the switching
front can carry a faint palette glow.  A content-aware dissolve.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/ColourDistance.glsl
        (ported by gre from https://gist.github.com/P-Seebauer/2a5fa2f77c883dd661f9)
Author: P-Seebauer (ColourDistance); gre (gl-transitions port)
License: MIT   (file header says "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the original is m = step(distance(from, to), progress) followed by
  mix(mix(from, to, m), to, pow(progress, power)).  Kept: the per-pixel distance threshold swept by progress and
  the pow(progress, power) tail.  Changed/added: the distance is normalised to 0..1 and square-root spread so
  every pixel switches during the sweep (the original's RGBA distance runs to sqrt(3), so very different pixels
  only arrive with the tail); a continuous metric axis (0 RGB, 0.5 luma, 1 hue angle in an opponent chroma
  plane, falling back to chroma/luma difference for greys); a softness band (smoothstep instead of step); dither
  of the threshold with two-scale value noise (>= 4 px grain, not fract(sin()) hash, so HEVC stays clean);
  region smoothing that mixes the per-pixel distance with a 16 px cell-averaged distance field (bilinear), so
  whole regions switch together as bold forms instead of speckle; an optional glow on the switching front,
  coloured by the shared cosine palette indexed by distance with drift.  Pure endpoints by early-out.)

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

_CELL = 16          # px per cell of the smoothed distance field
_SUB = 8            # samples per cell side (stride _CELL // _SUB)

_SRC = HELPERS + PALETTE_CUDA + r"""
// Normalised colour distance 0..1 on a continuous metric axis: 0 RGB, 0.5 luma, 1 hue.
__device__ __forceinline__ float cdist(const float4 a, const float4 b, const float metric) {
    const float s = 1.f / 255.f;
    const float ar = a.x * s, ag = a.y * s, ab = a.z * s;
    const float br = b.x * s, bg = b.y * s, bb = b.z * s;
    const float dr = ar - br, dg = ag - bg, db = ab - bb;
    const float d_rgb = sqrtf(dr * dr + dg * dg + db * db) * 0.57735027f;
    const float la = 0.2126f * ar + 0.7152f * ag + 0.0722f * ab;
    const float lb = 0.2126f * br + 0.7152f * bg + 0.0722f * bb;
    const float d_lum = fabsf(la - lb);
    float d;
    if (metric <= 0.5f) {
        d = d_rgb + (d_lum - d_rgb) * (metric * 2.f);
    } else {
        // opponent chroma plane: hue angle difference, weighted by how saturated both pixels are
        const float ua = ar - 0.5f * (ag + ab), va = 0.8660254f * (ag - ab);
        const float ub = br - 0.5f * (bg + bb), vb = 0.8660254f * (bg - bb);
        const float ca = sqrtf(ua * ua + va * va), cb = sqrtf(ub * ub + vb * vb);
        const float cosd = (ua * ub + va * vb) / (ca * cb + 1e-5f);
        const float hd = 0.5f * (1.f - fminf(fmaxf(cosd, -1.f), 1.f));
        const float w = smooth(0.03f, 0.2f, fminf(ca, cb));
        const float d_hue = w * hd + (1.f - w) * clamp01(0.8f * fabsf(ca - cb) + d_lum);
        d = d_lum + (d_hue - d_lum) * ((metric - 0.5f) * 2.f);
    }
    return sqrtf(clamp01(d));          // spread: most real differences sit low on the linear scale
}

// One thread per cell: the mean distance over an _SUB x _SUB grid of samples in the cell.
extern "C" __global__
void cd_cells(const uchar4* __restrict__ a, const uchar4* __restrict__ b, float* __restrict__ cells,
              const int W, const int H, const int GW, const int GH, const int cell, const int sub,
              const float metric)
{
    const int cx = blockIdx.x * blockDim.x + threadIdx.x, cy = blockIdx.y * blockDim.y + threadIdx.y;
    if (cx >= GW || cy >= GH) return;
    const int stride = cell / sub;
    const int x0 = cx * cell + stride / 2, y0 = cy * cell + stride / 2;
    float acc = 0.f;
    for (int j = 0; j < 8; ++j) {
        if (j >= sub) break;
        for (int i = 0; i < 8; ++i) {
            if (i >= sub) break;
            const int x = x0 + i * stride, y = y0 + j * stride;
            acc += cdist(ld(a, x, y, W, H), ld(b, x, y, W, H), metric);
        }
    }
    cells[cy * GW + cx] = acc / (float)(sub * sub);
}

__device__ __forceinline__ float cell_at(const float* __restrict__ c, int x, int y, int GW, int GH) {
    x = min(max(x, 0), GW - 1); y = min(max(y, 0), GH - 1);
    return c[y * GW + x];
}

extern "C" __global__
void colour_distance(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                     const float* __restrict__ cells, const float* __restrict__ P,
                     const int W, const int H, const int GW, const int GH, const int cell,
                     const float progress, const float metric, const float smoothing, const float soft,
                     const float dither, const float tail, const float glow_k, const float glow_w,
                     const float period, const float drift, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 fa = ld(a, x, y, W, H), fb = ld(b, x, y, W, H);

    // distance: per pixel, mixed toward the cell-averaged field (bold regions switch together)
    float d = cdist(fa, fb, metric);
    if (smoothing > 0.f) {
        const float gx = (x + 0.5f) / cell - 0.5f, gy = (y + 0.5f) / cell - 0.5f;
        const int ix = (int)floorf(gx), iy = (int)floorf(gy);
        const float tx = gx - ix, ty = gy - iy;
        const float c00 = cell_at(cells, ix, iy, GW, GH), c10 = cell_at(cells, ix + 1, iy, GW, GH);
        const float c01 = cell_at(cells, ix, iy + 1, GW, GH), c11 = cell_at(cells, ix + 1, iy + 1, GW, GH);
        const float dc = (1.f - ty) * ((1.f - tx) * c00 + tx * c10) + ty * ((1.f - tx) * c01 + tx * c11);
        d += (dc - d) * smoothing;
    }

    // threshold: distance, dithered by two-scale value noise (fine >= 4 px grain + soft clouds)
    const float fx = (float)x, fy = (float)y;
    const float n = 0.5f * vnoise(fx * 0.25f, fy * 0.25f, seed) + 0.5f * vnoise(fx * (1.f / 56.f), fy * (1.f / 56.f), seed + 7u);
    const float th = d * (1.f - dither) + dither * n;

    // sweep: the front s runs from -soft to 1 + soft so every pixel completes
    const float s = progress * (1.f + 2.f * soft) - soft;
    const float m = smooth(th - soft, th + soft, s);
    float4 c = mix4(mix4(fa, fb, m), fb, tail);

    // optional palette glow riding the switching front (localised, fades to 0 at both ends)
    if (glow_k > 0.f) {
        const float z = (s - th) / glow_w;
        const float band = __expf(-z * z);
        const float k = glow_k * band;
        if (k > 1e-3f) {
            const float3 g = pal(d * period + drift, P);
            c.x += (255.f - c.x) * k * g.x;
            c.y += (255.f - c.y) * k * g.y;
            c.z += (255.f - c.z) * k * g.z;
        }
    }
    out[y * W + x] = pack(c);
}
"""


class ColourDistance(Transition):
    name = "colour_distance"
    label = "Colour distance"
    description = ("Pixels switch to the new picture in order of how alike they already are: matching regions "
                   "first, the most different last, then a soft tail. A content-aware dissolve.")
    default_duration_s = 2.5
    params = {
        "metric": num(0.0, 1.0, 0.0, "Metric (RGB -> luma -> hue)", tier="regional", basic=True),
        "power": num(1.0, 10.0, 5.0, "Tail curve", step=0.1, tier="regional"),
        "softness": num(0.0, 0.2, 0.06, "Softness", step=0.005, tier="regional"),
        "dither": num(0.0, 0.2, 0.06, "Dither", step=0.005, tier="regional"),
        "smoothing": num(0.0, 1.0, 0.55, "Region smoothing", tier="regional", basic=True),
        "glow": num(0.0, 1.0, 0.25, "Front glow", tier="regional"),
        "palette": enum(PALETTES, "dusk", "Glow palette", tier="regional"),
        "palette_period": num(0.05, 10.0, 1.0, "Palette period", tier="regional"),
        "drift_speed": num(-2.0, 2.0, 0.1, "Colour drift", unit="/s", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k_cells = mod.get_function("cd_cells")
        self._k = mod.get_function("colour_distance")
        self._gw = (ctx.width + _CELL - 1) // _CELL
        self._gh = (ctx.height + _CELL - 1) // _CELL
        self._cells = cp.zeros(self._gw * self._gh, dtype=cp.float32)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s

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
        name = str(p.get("palette", "dusk"))
        if name not in PALETTES:
            name = "dusk"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

        metric = min(max(float(p["metric"]), 0.0), 1.0)
        smoothing = min(max(float(p["smoothing"]), 0.0), 1.0)
        soft = max(float(p["softness"]), 0.002)
        dither = min(max(float(p["dither"]), 0.0), 0.5)
        tail = float(progress) ** max(float(p["power"]), 1.0)
        # glow envelope: zero at both ends, gone once the tail takes over
        env = math.sin(math.pi * progress) ** 0.5 * (1.0 - tail)
        glow_k = float(p["glow"]) * 0.6 * env
        glow_w = max(soft, 0.03)
        t_s = progress * self.duration_s
        drift = (float(p["drift_speed"]) * t_s) % 1.0

        if smoothing > 0.0:
            launch_2d(self._k_cells, self._gw, self._gh,
                      (a, b, self._cells, np.int32(w), np.int32(h), np.int32(self._gw), np.int32(self._gh),
                       np.int32(_CELL), np.int32(_SUB), np.float32(metric)))
        launch_2d(self._k, w, h,
                  (a, b, out, self._cells, self._P, np.int32(w), np.int32(h), np.int32(self._gw),
                   np.int32(self._gh), np.int32(_CELL), np.float32(progress), np.float32(metric),
                   np.float32(smoothing), np.float32(soft), np.float32(dither), np.float32(tail),
                   np.float32(glow_k), np.float32(glow_w), np.float32(p["palette_period"]),
                   np.float32(drift), np.uint32(self._seed & 0xFFFFFFFF)))
