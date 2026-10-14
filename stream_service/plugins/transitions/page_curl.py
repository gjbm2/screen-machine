"""Page curl: the old picture peels off like a sheet of paper rolling around a cylinder.  The fold line sweeps
across the frame, the curled part shows the shaded back of the page (grey paper or a dimmed mirror image of the
old picture) lying flat over what is left, the roll catches a glossy highlight, and it casts a soft shadow onto
the new picture underneath.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/InvertedPageCurl.glsl
        https://www.mltframework.org/doxygen/webvfx/examples_2transition-shader-pagecurl_8html-example.html
Author: Hewlett-Packard (2010), adapted by Sergey Kosarevsky; based on Calyptus Life AB's pagecurl (MIT)
License: BSD-3-Clause   (file header: "// License: BSD 3 Clause"; full notice reproduced below)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the original's cylinder model -- a fold line moving along a fixed
  direction, the page wrapping round a cylinder of radius r, the far part flipped over and lying flat with its
  back side up, a greyed back side, a shadow under the roll and a shadow along the flipped part's edges -- is
  rebuilt as branchy fp32 scalar maths in place of its mat3 rotations, in aspect-correct (height-normalised)
  y-down coordinates.  Screen offset s from the cylinder axis maps to the page's arc length u by the exact roll
  geometry: s <= 0 is the flat page (u = s) under the flipped layer (u = pi r - s), 0 < s < r is the roll (front
  inner face u = r asin(s/r), back outer face u = r (pi - asin(s/r))); each layer samples the old picture at the
  page point it came from and is anti-aliased against the page outline, so the back side is the true mirror.
  Changes: the curl direction is a knob (curl_angle_deg: 0 peels from the right edge like a book page, 45 from
  the bottom-right corner, 90 from the bottom edge, 180 from the left; the original fixes it at 100 deg in its
  rotated frame); cylinder_radius is a knob (original 1/2pi of the frame); the back side morphs from grey paper
  with a faint show-through (original) to a dimmed mirrored image (back_tint), optionally tinted by the shared
  cosine palette (paper_colour); the roll and the inner curl are lit by a fixed key light from the upper left
  (Lambert + a gloss highlight that is zero on flat paper); the shadows (roll onto the new picture, flipped layer
  onto the old one) are offset along that light and scale with shadow_strength; the original's
  amount range -0.16..1.5 is replaced by a sweep that starts with the axis just outside the frame and ends with
  the roll and its shadow fully clear, so the endpoints are continuous, and they are also forced (progress <= 0
  copies a, >= 1 copies b).)

BSD-3-Clause notice (from InvertedPageCurl.glsl):
Copyright (c) 2010 Hewlett-Packard Development Company, L.P. All rights reserved.

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
following conditions are met:

   * Redistributions of source code must retain the above copyright notice, this list of conditions and the
     following disclaimer.
   * Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the
     following disclaimer in the documentation and/or other materials provided with the distribution.
   * Neither the name of Hewlett-Packard nor the names of its contributors may be used to endorse or promote
     products derived from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED
WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_AMB = 0.38            # ambient share of the paper lighting
_SPEC_EXP = 48.0

_SRC = HELPERS + PALETTE_CUDA + r"""
#define PI_F 3.14159265f
#define AMB """ + f"{_AMB:.4f}f" + r"""
#define SPEC_EXP """ + f"{_SPEC_EXP:.1f}f" + r"""

// signed distance (height units) from the page outline [0, A] x [0, 1]; < 0 inside
__device__ __forceinline__ float rect_sd(float x, float y, float A) {
    return fmaxf(fabsf(x - 0.5f * A) - 0.5f * A, fabsf(y - 0.5f) - 0.5f);
}
// arc length of the page on the back (upper / flipped) layer seen at screen offset s < r from the axis
__device__ __forceinline__ float u_back(float s, float r) {
    return s <= 0.f ? PI_F * r - s : r * (PI_F - asinf(fminf(s / r, 1.f)));
}
__device__ __forceinline__ float lit(float nd, float nz, float Ld, float Lz) {
    return (AMB + (1.f - AMB) * fmaxf(nd * Ld + nz * Lz, 0.f)) / (AMB + (1.f - AMB) * Lz);
}
__device__ __forceinline__ float spec(float nd, float nz, float Hd, float Hz, float base, float norm) {
    return fmaxf(__powf(fmaxf(nd * Hd + nz * Hz, 0.f), SPEC_EXP) - base, 0.f) * norm;
}

extern "C" __global__
void page_curl(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
               const int W, const int H, const float A, const float dx, const float dy, const float pos,
               const float r, const float shadow, const float tint, const float gloss, const float pcol,
               const float Ld, const float Lz, const float Hd, const float Hz, const float sbase, const float snorm,
               const float offx, const float offy, const float wds, const float shift, const float wsh,
               const float* __restrict__ P, const float drift)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const float invH = 1.f / H, fH = (float)H;
    const float qx = (x + 0.5f) * invH, qy = (y + 0.5f) * invH;
    const float s = qx * dx + qy * dy - pos;                       // signed offset from the cylinder axis
    if (s >= r + shift + wsh) { out[i] = b[i]; return; }            // well past the roll: the new picture

    // ---- the new picture with the roll's soft shadow (offset away from the light)
    float4 res = ld(b, x, y, W, H);
    {
        const float e = s - r - shift;
        if (e < wsh && shadow > 0.f) {
            const float q0x = qx - s * dx, q0y = qy - s * dy;                       // contact line
            const float c = 0.5f * PI_F * r - s;
            const float qtx = qx + c * dx, qty = qy + c * dy;                         // crest of the roll
            const float sd = fminf(rect_sd(q0x, q0y, A), rect_sd(qtx, qty, A));
            const float pres = 1.f - smooth(-0.1f * r, 0.45f * r, sd);               // roll exists along here
            const float k = shadow * 0.72f * (1.f - smooth(-0.4f * r, wsh, e)) * pres;
            res.x *= 1.f - k; res.y *= 1.f - k; res.z *= 1.f - k;
        }
    }
    if (s >= r + invH) { out[i] = pack(res); return; }
    const float sil = s > 0.f ? clamp01((r - s) * fH + 0.5f) : 1.f;                 // the roll's silhouette edge
    const float sn = fmaxf(s, 0.f) / r, cn = sqrtf(fmaxf(1.f - sn * sn, 0.f));      // sin/cos of the roll angle

    // ---- front layer: the flat old picture (s <= 0) running into the inside of the curl (0 < s < r)
    {
        float4 cf;
        float covf = 1.f, sh = 1.f, add = 0.f;
        if (s <= 0.f) {
            cf = ld(a, x, y, W, H);
        } else {
            const float uf = r * asinf(fminf(sn, 1.f));
            const float fx = qx + (uf - s) * dx, fy = qy + (uf - s) * dy;
            covf = clamp01(0.5f - rect_sd(fx, fy, A) * fH) * sil;
            cf = bilin(a, fx * fH, fy * fH, W, H);
            sh = lit(-sn, cn, Ld, Lz) * (1.f - 0.32f * sn * sn);                      // inside of the curl
            add = 120.f * gloss * spec(-sn, cn, Hd, Hz, sbase, snorm);
        }
        if (covf > 0.f) {
            if (shadow > 0.f) {                                                       // the flipped layer's drop shadow
                const float sx = qx + offx, sy = qy + offy;
                const float s2 = sx * dx + sy * dy - pos;
                if (s2 < r) {
                    const float u2 = u_back(s2, r);
                    const float d = rect_sd(sx + (u2 - s2) * dx, sy + (u2 - s2) * dy, A);
                    sh *= 1.f - shadow * 0.5f * (1.f - smooth(-0.3f * wds, wds, d));
                }
            }
            cf = make_float4(cf.x * sh + add, cf.y * sh + add, cf.z * sh + add, 255.f);
            res = covf >= 1.f ? cf : mix4(res, cf, covf);
        }
    }

    // ---- back layer: the outside of the roll (0 < s < r) and the flipped page lying flat (s <= 0)
    {
        const float ub = u_back(s, r);
        const float bx = qx + (ub - s) * dx, by = qy + (ub - s) * dy;
        const float covb = clamp01(0.5f - rect_sd(bx, by, A) * fH) * sil;
        if (covb > 0.f) {
            const float4 ca = bilin(a, bx * fH, by * fH, W, H);
            const float lu = luma(ca);
            const float grey = 0.60f + 0.24f * lu;                                    // paper with a faint show-through
            const float3 pc = pal(0.32f * (bx - by) + 0.45f * ub + drift, P);
            const float tr = 1.f + (0.35f + 0.75f * pc.x - 1.f) * pcol;              // white -> palette paper
            const float tg = 1.f + (0.35f + 0.75f * pc.y - 1.f) * pcol;
            const float tb = 1.f + (0.35f + 0.75f * pc.z - 1.f) * pcol;
            const float nd = sn, nz = cn;                                             // outward normal of the roll
            const float l = lit(nd, nz, Ld, Lz) * (1.f - 0.18f * sn * sn * sn);
            const float add = 230.f * gloss * spec(nd, nz, Hd, Hz, sbase, snorm);
            const float m0 = 255.f * grey * (1.f - tint), m1 = 0.55f * tint;
            float4 cb2;
            cb2.x = ((m0 + m1 * ca.x + 18.f * tint) * tr) * l + add;
            cb2.y = ((m0 + m1 * ca.y + 18.f * tint) * tg) * l + add;
            cb2.z = ((m0 + m1 * ca.z + 18.f * tint) * tb) * l + add * 0.97f;
            cb2.w = 255.f;
            res = covb >= 1.f ? cb2 : mix4(res, cb2, covb);
        }
    }
    out[i] = pack(res);
}
"""


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


_L = _norm((-0.38, -0.55, 0.74))                 # key light from the upper left (y-down screen)
_HV = _norm((_L[0], _L[1], _L[2] + 1.0))         # half vector, viewer straight above
_SPEC_BASE = _HV[2] ** _SPEC_EXP                  # flat paper gives no highlight
_SPEC_NORM = 1.0 / (1.0 - _SPEC_BASE)


class PageCurl(Transition):
    name = "page_curl"
    label = "Page curl"
    description = ("The old picture peels off like a page rolling round a cylinder, showing its shaded back and "
                   "casting a soft shadow on the new picture.")
    default_duration_s = 2.0
    params = {
        "curl_angle_deg": num(0.0, 180.0, 35.0, "Curl angle (0 right edge, 45 corner, 90 bottom)", step=1.0,
                              unit="deg", tier="regional", basic=True),
        "cylinder_radius": num(0.05, 0.3, 0.12, "Cylinder radius", step=0.005, unit="× height", tier="regional"),
        "shadow_strength": num(0.0, 1.0, 0.6, "Shadow strength", tier="regional"),
        "back_tint": num(0.0, 1.0, 0.3, "Back side (grey paper -> mirrored image)", tier="regional"),
        "gloss": num(0.0, 1.0, 0.45, "Gloss", tier="regional"),
        "paper_colour": num(0.0, 1.0, 0.15, "Paper colour (white -> palette)", tier="regional"),
        "palette": enum(list(PALETTES), "dusk", "Paper palette", tier="regional"),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("page_curl")
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        self._sync_palette()

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "dusk")
        if name not in PALETTES:
            name = "dusk"
        if name != self._pal:                                    # regional: follow palette changes
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
        self._sync_palette()
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        aspect = w / h
        ang = math.radians(min(max(float(p["curl_angle_deg"]), 0.0), 180.0))
        dx, dy = math.cos(ang), math.sin(ang)                    # direction the fold travels away from
        r = min(max(float(p["cylinder_radius"]), 0.02), 0.5)
        proj = [0.0, aspect * dx, dy, aspect * dx + dy]
        ld = _L[0] * dx + _L[1] * dy                             # light along the curl axis normal
        hd = _HV[0] * dx + _HV[1] * dy
        shift = max(-ld, 0.0) / _L[2] * 0.8 * r                  # the roll's shadow falls away from the light
        wsh = 0.5 * r + 0.04
        wds = 0.25 * r + 3.0 / h
        off = 0.6 * r / _L[2]                                    # flipped layer's shadow: occluder toward the light
        px = 1.0 / h
        start = max(proj) + 0.3 * r + 2.0 * px                   # axis just outside the frame: page flat
        end = min(proj) - (r + shift + wsh) - 2.0 * px           # roll and its shadow fully clear
        pos = start + (end - start) * float(progress)
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h), np.float32(aspect), np.float32(dx), np.float32(dy),
                   np.float32(pos), np.float32(r), np.float32(p["shadow_strength"]), np.float32(p["back_tint"]),
                   np.float32(p["gloss"]), np.float32(p["paper_colour"]),
                   np.float32(ld), np.float32(_L[2]), np.float32(hd), np.float32(_HV[2]),
                   np.float32(_SPEC_BASE), np.float32(_SPEC_NORM),
                   np.float32(_L[0] * off), np.float32(_L[1] * off), np.float32(wds), np.float32(shift),
                   np.float32(wsh), self._P, np.float32(0.25 * progress)))
