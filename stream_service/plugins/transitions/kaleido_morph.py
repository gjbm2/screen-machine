"""Kaleido morph: the frame folds into a spinning mirror kaleidoscope, the two scenes crossfade while it is fully
folded, then it unfolds into the new scene.  Optional palette-tinted glow traces the mirror seams at full fold.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/powerKaleido.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/kaleidoscope.glsl
Author: Boundless (powerKaleido); nwoeanhinnogaehr (kaleidoscope)
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the two shaders are merged into one plug-in with a continuous fold_mix
  (0 = kaleidoscope.glsl's rotate + abs(mod(p, 2) - 1) fold, 1 = powerKaleido's iterated mirror-polygon fold) that
  blends their folded sample coordinates; powerKaleido's per-line inequality test is rewritten as the equivalent
  "reflect across the line p.n = dist when outside it", with the line normals precomputed per thread; its fixed
  rad = 120 becomes the mirror_angle_deg knob (60 = hexagon ... 180 = parallel mirror bands), and the same
  angle is kaleidoscope.glsl's per-iteration rotation step (its `angle` uniform); scale / z / speed become
  fold_scale / zoom / spin and act on both folds; the integer iteration counts (10 and 7) become a continuous
  fold_iterations whose last iteration is faded in by its fractional part; both shaders' progress curves are
  replaced by one envelope: fold in over 0 -> 0.4, hold at full fold 0.4 -> 0.6 while the scenes crossfade
  (smoothstep 0.3 -> 0.7), unfold 0.6 -> 1; the abs-mod fold is aspect-corrected and its cell count is capped so
  the whole picture is never minified into shimmer; added seam_glow: a thin palette-tinted (shared cosine
  palette, drifting with progress) screen-blend line on the mirror seams, faded in with the fold, so it is
  localised (never a full-frame flash); samples mirror-wrap instead of clamping; uv is the gl-transitions 0..1,
  y-up convention on our y-down pixel frames; fract(sin()) is not used; endpoints are forced on the host.)

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

Binder: style lively to wild, 2-4 beats, starting on a downbeat; any symmetric easing keeps the full fold centred.
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
__device__ __forceinline__ float mirror_wrap(float v, float n) {
    float t = fmodf(fabsf(v), 2.f * n);
    return t > n ? 2.f * n - t : t;
}
// gl-transitions uv (0..1, y up) -> our y-down pixel frame, mirrored at the borders, bilinear.
__device__ __forceinline__ float4 samp(const uchar4* __restrict__ img, float u, float v, int W, int H) {
    return bilin(img, mirror_wrap(u * W, (float)W), mirror_wrap((1.f - v) * H, (float)H), W, H);
}
// kaleidoscope.glsl: p = (sin t x + cos t y, sin t y - cos t x); p = abs(mod(p, 2) - 1)
__device__ __forceinline__ void fold_absmod(float& x, float& y, float t) {
    float s, c;
    __sincosf(t, &s, &c);
    const float nx = s * x + c * y, ny = s * y - c * x;
    x = fabsf(nx - 2.f * floorf(nx * 0.5f) - 1.f);
    y = fabsf(ny - 2.f * floorf(ny * 0.5f) - 1.f);
}
// powerKaleido: for each mirror line (normal n, distance d from the origin), reflect across it when outside.
__device__ __forceinline__ void fold_poly(float& x, float& y, const float* nx, const float* ny, int L, float d) {
    #pragma unroll
    for (int k = 0; k < 6; ++k) {
        if (k < L) {
            const float s = x * nx[k] + y * ny[k] - d;
            if (s > 0.f) { x -= 2.f * s * nx[k]; y -= 2.f * s * ny[k]; }
        }
    }
}

extern "C" __global__
void kaleido_morph(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                   const int W, const int H, const float ratio,
                   const float f, const float wmix,               // fold envelope, crossfade weight
                   const float ca, const float sa,                // spin rotation
                   const float zoom, const float dist, const float sclA, const float ang, const float tA,
                   const int nfull, const float nfrac, const int L, const float deg,
                   const float fmix, const float glow, const float glow_w, const float drift,
                   const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float u0 = (x + 0.5f) / W, v0 = 1.f - (y + 0.5f) / H;
    const float qx = (u0 - 0.5f) * ratio, qy = v0 - 0.5f;           // aspect-correct, height-normalised, y up
    const float rx = ca * qx - sa * qy, ry = sa * qx + ca * qy;     // spun fold space
    float uA = u0, vA = v0, uB = u0, vB = v0, dA = 1.f, dB = 1.f;
    if (fmix < 1.f) {                                                // --- abs-mod fold (kaleidoscope.glsl)
        float px = rx * sclA, py = ry * sclA, t = tA;
        for (int i = 0; i < nfull; ++i) { fold_absmod(px, py, t); t += ang; }
        if (nfrac > 0.f) {
            float ex = px, ey = py;
            fold_absmod(ex, ey, t);
            px += (ex - px) * nfrac; py += (ey - py) * nfrac;
        }
        uA = px; vA = py;
        dA = fabsf(fminf(fminf(px, 1.f - px), fminf(py, 1.f - py))) / sclA;    // seam distance, screen units
    }
    if (fmix > 0.f) {                                                // --- mirror-polygon fold (powerKaleido)
        float nx[6], ny[6];
        #pragma unroll
        for (int k = 0; k < 6; ++k) { float s, c; __sincosf(k * deg, &s, &c); nx[k] = -s; ny[k] = c; }
        float px = rx * zoom, py = ry * zoom;
        for (int i = 0; i < nfull; ++i) fold_poly(px, py, nx, ny, L, dist);
        if (nfrac > 0.f) {
            float ex = px, ey = py;
            fold_poly(ex, ey, nx, ny, L, dist);
            px += (ex - px) * nfrac; py += (ey - py) * nfrac;
        }
        float dm = 1e9f;
        #pragma unroll
        for (int k = 0; k < 6; ++k)
            if (k < L) dm = fminf(dm, fabsf(dist - (px * nx[k] + py * ny[k])));
        dB = dm / zoom;
        const float bx = ca * px + sa * py, by = -sa * px + ca * py;  // un-spin, back to uv
        uB = bx / ratio + 0.5f; vB = by + 0.5f;
    }
    const float uf = uA + (uB - uA) * fmix, vf = vA + (vB - vA) * fmix;
    const float u = u0 + (uf - u0) * f, v = v0 + (vf - v0) * f;     // morph identity <-> full fold
    float4 c = mix4(samp(a, u, v, W, H), samp(b, u, v, W, H), wmix);
    if (glow > 0.f) {
        const float ds = dA + (dB - dA) * fmix;
        const float g = glow * ((1.f - smooth(0.f, glow_w, ds)) + 0.35f * (1.f - smooth(0.f, 4.f * glow_w, ds)));
        if (g > 0.f) {
            const float3 pc = pal(1.6f * sqrtf(qx * qx + qy * qy) + drift, P);
            const float gg = fminf(g, 1.f);
            c.x += (255.f - c.x) * gg * pc.x;
            c.y += (255.f - c.y) * gg * pc.y;
            c.z += (255.f - c.z) * gg * pc.z;
        }
    }
    out[y * W + x] = pack(c);
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class KaleidoMorph(Transition):
    name = "kaleido_morph"
    label = "Kaleido morph"
    description = ("The frame folds into a spinning mirror kaleidoscope, the scenes crossfade at full fold, and it "
                   "unfolds into the new one.")
    default_duration_s = 1.8
    params = {
        "fold_mix": num(0.0, 1.0, 1.0, "Fold style (abs-mod -> mirror polygon)", tier="regional", basic=True),
        "mirror_angle_deg": num(60.0, 180.0, 120.0, "Mirror angle", step=1.0, unit="°", tier="regional"),
        "fold_scale": num(0.5, 4.0, 2.0, "Fold scale (cell size)", step=0.05, tier="regional"),
        "zoom": num(1.0, 3.0, 1.5, "Zoom (more cells)", step=0.05, unit="×", tier="regional"),
        "spin": num(0.0, 10.0, 5.0, "Spin", step=0.1, unit="rad", tier="regional"),
        "fold_iterations": num(2.0, 10.0, 8.0, "Fold iterations", step=0.1, tier="regional"),
        "seam_glow": num(0.0, 1.0, 0.35, "Seam glow", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Seam palette", tier="regional"),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("kaleido_morph")
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        self._sync_palette()

    def _sync_palette(self) -> None:
        name = self.params["palette"]
        if name != self._pal:                                   # regional: follow palette changes
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        P = float(progress)
        w, h = self.ctx.width, self.ctx.height
        f = _smooth(0.0, 0.4, P) * _smooth(0.0, 0.4, 1.0 - P)     # fold in, hold 0.4..0.6, unfold
        wmix = _smooth(0.3, 0.7, P)                               # crossfade while folded
        fmix = min(max(float(p["fold_mix"]), 0.0), 1.0)
        deg_d = min(max(float(p["mirror_angle_deg"]), 60.0), 180.0)
        deg = math.radians(deg_d)
        L = int(min(max(math.ceil(360.0 / deg_d - 1e-4), 2), 6))  # powerKaleido: lines at i = 0, deg, ... < 2 pi
        zoom = max(float(p["zoom"]), 1e-3)
        fs = max(float(p["fold_scale"]), 1e-3)
        dist = fs / 10.0                                           # powerKaleido: dist = scale / 10
        sclA = min(zoom * 2.0 / fs, 4.5)                           # abs-mod cells per unit (capped: no shimmer)
        spin = float(p["spin"])
        rot = spin * P
        tA = 0.8 + 0.25 * spin * P                                 # kaleidoscope.glsl's t, morphs the fold
        it = min(max(float(p["fold_iterations"]), 1.0), 10.0)
        nfull = int(math.floor(it))
        nfrac = it - nfull
        if nfrac < 1e-4:
            nfrac = 0.0
        glow = float(p["seam_glow"]) * f * f
        self._sync_palette()
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h), np.float32(w / h),
                   np.float32(f), np.float32(wmix),
                   np.float32(math.cos(rot)), np.float32(math.sin(rot)),
                   np.float32(zoom), np.float32(dist), np.float32(sclA), np.float32(deg),
                   np.float32(math.fmod(tA, 2.0 * math.pi)),
                   np.int32(nfull), np.float32(nfrac), np.int32(L), np.float32(deg),
                   np.float32(fmix), np.float32(glow), np.float32(0.006), np.float32(0.6 * P),
                   self._P))
