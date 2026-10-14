"""Liquid warp: heat-haze, underwater or molten wobble over any picture, from a smooth flowing noise/curl field.

Source: Original (inspired by https://github.com/Vidvox/ISF-Files/blob/master/ISF/Noise%20Displace.fs and
        https://github.com/evanw/glfx.js/blob/master/src/filters/warp/swirl.js)
Author: smfx (ideas: VIDVOX "Noise Displace", Evan Wallace glfx.js "swirl"; flow field by smfx)
License: MIT
license_class: permissive

Written from scratch; no code copied.  A 3D gradient-noise potential (1-3 octaves, the fractional part faded in)
is evaluated every frame on a 1/8-resolution grid (480x270 at 4K).  Its gradient (push: lens-like pinch and bulge)
and its curl (swirl: divergence-free eddies) are mixed into a displacement vector field.  Anisotropy squeezes the
domain into rising horizontal bands and suppresses vertical motion (0 = heat haze), or leaves it round (1 = water).
The full-resolution pass upsamples the field bilinearly and takes one mirrored tap (three with refraction fringes:
R and B ride the same displacement scaled up and down).  Optional soft caustic light is a web of the potential's
zero crossings, so it moves with the water.

Realtime: amount = displacement depth (0 copies the input), pulse = amplitude swell plus a flow-rate kick into the
host-integrated phases (the field lurches forward, then eases out), flow_speed = rate.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + r"""
// Periodic (256) 3D gradient noise, ~[-1, 1].  Every lattice axis is masked to 256 so the host may wrap its phases.
__device__ __forceinline__ float lw_grad(int ix, int iy, int iz, float x, float y, float z) {
    const unsigned int h = hash_u(((unsigned int)(ix & 255)) * 73856093u
                                  ^ hash_u(((unsigned int)(iy & 255)) * 19349663u
                                           ^ hash_u(((unsigned int)(iz & 255)) + 0x9E3779B9u))) & 15u;
    const float u = h < 8u ? x : y;
    const float v = h < 4u ? y : ((h == 12u || h == 14u) ? x : z);
    return ((h & 1u) ? -u : u) + ((h & 2u) ? -v : v);
}
__device__ __forceinline__ float lw_fade(float t) { return t * t * t * (t * (t * 6.f - 15.f) + 10.f); }
__device__ float lw_noise(float x, float y, float z) {
    const float fx = floorf(x), fy = floorf(y), fz = floorf(z);
    const int ix = (int)fx, iy = (int)fy, iz = (int)fz;
    x -= fx; y -= fy; z -= fz;
    const float u = lw_fade(x), v = lw_fade(y), w = lw_fade(z);
    const float a0 = lw_grad(ix, iy, iz, x, y, z),             a1 = lw_grad(ix + 1, iy, iz, x - 1.f, y, z);
    const float b0 = lw_grad(ix, iy + 1, iz, x, y - 1.f, z),     b1 = lw_grad(ix + 1, iy + 1, iz, x - 1.f, y - 1.f, z);
    const float c0 = lw_grad(ix, iy, iz + 1, x, y, z - 1.f),     c1 = lw_grad(ix + 1, iy, iz + 1, x - 1.f, y, z - 1.f);
    const float d0 = lw_grad(ix, iy + 1, iz + 1, x, y - 1.f, z - 1.f);
    const float d1 = lw_grad(ix + 1, iy + 1, iz + 1, x - 1.f, y - 1.f, z - 1.f);
    const float lo = (1.f - v) * (a0 + (a1 - a0) * u) + v * (b0 + (b1 - b0) * u);
    const float hi = (1.f - v) * (c0 + (c1 - c0) * u) + v * (d0 + (d1 - d0) * u);
    return lo + (hi - lo) * w;
}
// Fractional-octave potential: octave k at frequency 2^k, amplitude 2^-k, morph rate (k+1) (all integers, so periodic).
__device__ float lw_fbm(float x, float y, float z, float c2, float c3) {
    float n = lw_noise(x, y, z);
    if (c2 > 0.f) n += 0.5f * c2 * lw_noise(2.f * x + 17.31f, 2.f * y + 5.17f, 2.f * z + 3.7f);
    if (c3 > 0.f) n += 0.25f * c3 * lw_noise(4.f * x + 41.9f, 4.f * y + 23.3f, 3.f * z + 9.1f);
    return n;
}

// Pass 1 (1/8 res): displacement field (vx, vy in ~[-1.5, 1.5]) and caustic web (z, 0..1).
extern "C" __global__
void lw_field(float4* __restrict__ fld, const int fw, const int fh, const float A,
              const float fxs, const float fys, const float sx, const float sy, const float zph,
              const float c2, const float c3, const float curl, const float vyk, const float dnorm)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= fw || j >= fh) return;
    const float X = ((i + 0.5f) / fw - 0.5f) * A, Y = (j + 0.5f) / fh - 0.5f;
    const float x = X * fxs + sx, y = Y * fys + sy;
    const float e = 0.06f;
    const float n0 = lw_fbm(x, y, zph, c2, c3);
    const float nu = (lw_fbm(x + e, y, zph, c2, c3) - n0) * (1.f / e) * dnorm;
    const float nv = (lw_fbm(x, y + e, zph, c2, c3) - n0) * (1.f / e) * dnorm;
    // gradient push (pinch / bulge) vs curl (swirl)
    float vx = 0.55f * (nu + (nv - nu) * curl);
    float vy = 0.55f * (nv + (-nu - nv) * curl);
    const float sc = rsqrtf(1.f + 0.15f * (vx * vx + vy * vy));    // soft limit: no runaway tears
    vx *= sc; vy *= sc * vyk;
    // caustic web: zero crossings of the potential and of a turned twin layer (periodic domain kept)
    const float n2 = lw_noise(y + 31.7f, -x + 13.1f, -zph + 7.3f);
    const float web = fminf(fabsf(n0) * 1.4f, fabsf(n2) * 1.4f);
    float c = 1.f - smooth(0.f, 0.2f, web);
    c = c * c;
    fld[j * fw + i] = make_float4(vx, vy, c, 0.f);
}

__device__ __forceinline__ float lw_fold(float v, float n) {          // mirror at the frame edges
    v = fabsf(v);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}
__device__ __forceinline__ float4 lw_fld(const float4* __restrict__ f, float gx, float gy, int fw, int fh) {
    gx -= 0.5f; gy -= 0.5f;
    const int x0 = (int)floorf(gx), y0 = (int)floorf(gy);
    const float tx = gx - x0, ty = gy - y0;
    const int xa = min(max(x0, 0), fw - 1), xb = min(max(x0 + 1, 0), fw - 1);
    const int ya = min(max(y0, 0), fh - 1), yb = min(max(y0 + 1, 0), fh - 1);
    const float4 a = f[ya * fw + xa], b = f[ya * fw + xb], c = f[yb * fw + xa], d = f[yb * fw + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 0.f);
}

// Pass 2 (full res): one mirrored tap through the upsampled field (three with refraction fringes) + caustic light.
extern "C" __global__
void lw_warp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
             const float4* __restrict__ fld, const int fw, const int fh,
             const float amp, const float cs, const float caus)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float4 f = lw_fld(fld, px * fw / W, py * fh / H, fw, fh);
    const float dx = f.x * amp, dy = f.y * amp;
    const float fW = (float)W, fH = (float)H;
    float4 col;
    if (cs > 1e-4f) {
        const float kr = 1.f + cs, kb = 1.f - cs;
        col.x = bilin(src, lw_fold(px + dx * kr, fW), lw_fold(py + dy * kr, fH), W, H).x;
        col.y = bilin(src, lw_fold(px + dx, fW), lw_fold(py + dy, fH), W, H).y;
        col.z = bilin(src, lw_fold(px + dx * kb, fW), lw_fold(py + dy * kb, fH), W, H).z;
        col.w = 255.f;
    } else {
        col = bilin(src, lw_fold(px + dx, fW), lw_fold(py + dy, fH), W, H);
    }
    if (caus > 1e-4f) {
        const float l = caus * f.z;
        const float g = 1.f + 0.6f * l;
        col = make_float4(col.x * g + 20.f * l, col.y * g + 22.f * l, col.z * g + 18.f * l, 255.f);
    }
    out[y * W + x] = pack(col);
}
"""


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


class LiquidWarp(Effect):
    name = "liquid_warp"
    stage = "stylise"
    label = "Liquid warp"
    description = "Heat-haze, underwater or molten wobble: a flowing noise/curl field displaces the picture, with refraction fringes."
    params = {
        **standard_effect_params(),
        "scale": num(0.5, 8.0, 2.5, "Ripple scale", unit="per height", step=0.05, tier="regional", basic=True),
        "complexity": num(1.0, 3.0, 2.0, "Complexity (octaves)", step=0.05, tier="regional"),
        "anisotropy": num(0.0, 1.0, 0.7, "Haze ↔ water", tier="regional", basic=True),
        "curl_mix": num(0.0, 1.0, 0.6, "Push ↔ swirl", tier="regional"),
        "depth": num(0.003, 0.08, 0.03, "Depth at amount 1", unit="× height", step=0.001, tier="regional"),
        "refraction_chroma": num(0.0, 1.0, 0.3, "Refraction fringes", tier="regional"),
        "caustics": num(0.0, 1.0, 0.25, "Caustic light", tier="regional"),
        "kick": num(0.0, 2.0, 1.0, "Beat kick", step=0.05, tier="regional"),
        "flow_speed": num(0.0, 2.0, 0.35, "Flow speed", unit="/s", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kf = mod.get_function("lw_field")
        self._kw = mod.get_function("lw_warp")
        W, H = ctx.width, ctx.height
        self.fw, self.fh = max(8, (W + 7) // 8), max(8, (H + 7) // 8)
        self.fld = cp.zeros((self.fh, self.fw, 4), cp.float32)
        rng = np.random.default_rng(int(params.get("seed", 1)) if isinstance(params, dict) else 1)
        self.sx = float(rng.uniform(0, 256))
        self.sy = float(rng.uniform(0, 256))
        self.zph = float(rng.uniform(0, 256))
        self.A = W / H

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        a = _clamp01(p["amount"])
        pulse = _clamp01(p["pulse"])
        kick = float(p["kick"])
        aniso = _clamp01(p["anisotropy"])
        # flow: rate kick into host-integrated phases (lurch, then ease out); wrapped to the noise period
        rate = float(p["flow_speed"]) * (1.0 + 2.0 * kick * pulse)
        self.zph = math.fmod(self.zph + rate * 0.55 * dt, 256.0)
        self.sy = math.fmod(self.sy + rate * (0.15 + 0.85 * (1.0 - aniso)) * dt, 256.0)   # rising haze
        self.sx = math.fmod(self.sx + rate * 0.3 * aniso * dt, 256.0)
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        W, H = self.ctx.width, self.ctx.height
        s = float(p["scale"])
        fxs = s * (0.3 + 0.7 * aniso)
        fys = s * (1.9 - 0.9 * aniso)
        cx = float(p["complexity"])
        c2 = _clamp01(cx - 1.0)
        c3 = _clamp01(cx - 2.0)
        dnorm = 1.0 / math.sqrt(1.0 + c2 * c2 + c3 * c3)
        vyk = 0.1 + 0.9 * aniso
        launch_2d(self._kf, self.fw, self.fh,
                  (self.fld, np.int32(self.fw), np.int32(self.fh), np.float32(self.A),
                   np.float32(fxs), np.float32(fys), np.float32(self.sx), np.float32(self.sy), np.float32(self.zph),
                   np.float32(c2), np.float32(c3), np.float32(_clamp01(p["curl_mix"])), np.float32(vyk),
                   np.float32(dnorm)))
        amp = float(p["depth"]) * a * (1.0 + 0.5 * kick * pulse) * H
        cs = 0.6 * _clamp01(p["refraction_chroma"])
        caus = _clamp01(p["caustics"]) * min(1.0, 1.6 * a) * (1.0 + min(0.35, 0.35 * kick * pulse))
        launch_2d(self._kw, W, H,
                  (src, out, np.int32(W), np.int32(H), self.fld, np.int32(self.fw), np.int32(self.fh),
                   np.float32(amp), np.float32(cs), np.float32(caus)))
