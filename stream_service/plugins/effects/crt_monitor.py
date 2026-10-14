"""CRT Monitor: a premium arcade / broadcast tube.  Barrel curvature inside a rounded plastic bezel, Lottes-style
Gaussian scanlines that widen with brightness, a continuous aperture-grille -> slot -> shadow mask, phosphor
bloom (halation), corner falloff and a touch of RGB misconvergence.  The virtual line count (240-1080) keeps the
raster readable at 4K.  Pairs with sync_fault.

Source: https://github.com/libretro/glsl-shaders/blob/master/crt/shaders/crt-lottes.glsl
        https://godotshaders.com/shader/vhs-and-crt-monitor-effect/
        https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_CRT.fx
Author: Timothy Lottes (crt-lottes); pend00 (godotshaders VHS and CRT monitor); Garrett Gunnell (AcerolaFX CRT)
License: Public domain (Lottes: 'PUBLIC DOMAIN CRT STYLED SCAN-LINE SHADER') + CC0-1.0 + MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Lottes' warp (pos *= 1 + pos.yx^2 * warp) and Gaussian scanline
profile kept, but the beam is sampled from a pre-filtered "line buffer" (one row per virtual scanline, a 5x2-tap
Gaussian spot of the source) so each output pixel needs only 3 vertical taps; the beam sigma grows per channel
with brightness (thin dark lines, solid bright lines, like a real gun); the mask is analytic and continuous
(cos^2 aperture-grille stripes -> staggered slot gaps -> hex delta dots, cross-faded by a float type) with
host-computed brightness compensation; halation from a 1/16-res linear-light buffer, two Gaussian passes and a
B-spline upsample, screen-blended; misconvergence grows toward the edges (R right/up, B left/down); AcerolaFX-
style corner falloff; a rounded bezel whose inner bevel reflects the blurred picture; a slow rolling hum bar;
an optional palette-tinted monochrome phosphor cast (pal() with drift).  All terms scale with
m = smoothstep(amount), so amount 0 is a flat, clean copy.)

Realtime: amount scales curvature, scanline depth, mask, bloom, convergence, bezel inset and corner falloff.
pulse = beam overdrive (sigma and bloom gain swell), an HV-sag raster breathing kick (an under-damped spring on
the host, so it decays smoothly and never jumps) and a brightness surge.  Five passes, the main one full-res.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

LMAX = 1088          # line-buffer rows (max virtual lines + guard)
NU = 32              # uniform floats
BLOOM_DIV = 16       # bloom buffer = output / 16

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float lin1(float v) { return __powf(fmaxf(v, 0.f) * (1.f / 255.f), 2.2f); }
__device__ __forceinline__ float enc1(float v) { return 255.f * __powf(clamp01(v), 1.f / 2.2f); }

// ---- pass 1: one row per virtual scanline, a soft Gaussian beam spot of the source (linear light) ----
extern "C" __global__
void crt_lines(const uchar4* __restrict__ src, float4* __restrict__ lines, const int W, const int H,
               const int Wl, const int Lc, const float L)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, n = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= Wl || n >= Lc) return;
    const float cx = (i + 0.5f) * (float)W / (float)Wl;
    const float span = (float)H / L;
    const float cy = (n + 0.5f) * span;
    const float sh = fmaxf(0.42f * span, 0.5f * (float)W / (float)Wl);
    const float step = 0.8f * sh;
    float r = 0.f, g = 0.f, b = 0.f, ws = 0.f;
    #pragma unroll
    for (int j = -2; j <= 2; ++j) {
        const float w = __expf(-0.5f * (0.8f * j) * (0.8f * j));
        #pragma unroll
        for (int v = 0; v < 2; ++v) {
            const float yy = cy + (v == 0 ? -0.25f : 0.25f) * span;
            const float4 c = bilin(src, cx + j * step, yy, W, H);
            r += w * c.x; g += w * c.y; b += w * c.z; ws += w;
        }
    }
    const float iw = 1.f / ws;
    lines[n * Wl + i] = make_float4(lin1(r * iw), lin1(g * iw), lin1(b * iw), 0.f);
}

// ---- pass 2: bloom downsample (linear light, 1/16 res) ----
extern "C" __global__
void crt_bloom_down(const uchar4* __restrict__ src, float4* __restrict__ bl, const int W, const int H,
                    const int Wb, const int Hb)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Wb || y >= Hb) return;
    const float sx = (float)W / Wb, sy = (float)H / Hb;
    float r = 0.f, g = 0.f, b = 0.f;
    #pragma unroll
    for (int a = 0; a < 3; ++a) {
        #pragma unroll
        for (int c = 0; c < 3; ++c) {
            const float4 p = bilin(src, (x + (a + 0.5f) / 3.f) * sx, (y + (c + 0.5f) / 3.f) * sy, W, H);
            r += lin1(p.x); g += lin1(p.y); b += lin1(p.z);
        }
    }
    bl[y * Wb + x] = make_float4(r * (1.f / 9.f), g * (1.f / 9.f), b * (1.f / 9.f), 0.f);
}

// ---- passes 3/4: separable Gaussian on the bloom buffer ----
extern "C" __global__
void crt_blur(const float4* __restrict__ a, float4* __restrict__ o, const int w, const int h, const int dx, const int dy)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float r = 0.f, g = 0.f, b = 0.f, ws = 0.f;
    #pragma unroll
    for (int k = -6; k <= 6; ++k) {
        const float wk = __expf(-0.5f * (k * k) * (1.f / (2.6f * 2.6f)));
        const int xx = min(max(x + k * dx, 0), w - 1), yy = min(max(y + k * dy, 0), h - 1);
        const float4 c = a[yy * w + xx];
        r += wk * c.x; g += wk * c.y; b += wk * c.z; ws += wk;
    }
    o[y * w + x] = make_float4(r / ws, g / ws, b / ws, 0.f);
}

__device__ __forceinline__ float3 f4bl(const float4* __restrict__ b, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float x0f = floorf(fx), y0f = floorf(fy);
    const float tx = fx - x0f, ty = fy - y0f;
    const int x0 = min(max((int)x0f, 0), w - 1), x1 = min(max((int)x0f + 1, 0), w - 1);
    const int y0 = min(max((int)y0f, 0), h - 1), y1 = min(max((int)y0f + 1, 0), h - 1);
    const float4 p = b[y0 * w + x0], q = b[y0 * w + x1], r = b[y1 * w + x0], s = b[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float3(v * (u * p.x + tx * q.x) + ty * (u * r.x + tx * s.x),
                       v * (u * p.y + tx * q.y) + ty * (u * r.y + tx * s.y),
                       v * (u * p.z + tx * q.z) + ty * (u * r.z + tx * s.z));
}
// cubic B-spline upsample from 4 bilinear taps (smooth bloom, no diamonds)
__device__ float3 f4bs(const float4* __restrict__ b, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float ix = floorf(fx), iy = floorf(fy);
    const float tx = fx - ix, ty = fy - iy;
    const float ax0 = (1.f - tx) * (1.f - tx) * (1.f - tx) * (1.f / 6.f);
    const float ax1 = (4.f - 6.f * tx * tx + 3.f * tx * tx * tx) * (1.f / 6.f);
    const float ax3 = tx * tx * tx * (1.f / 6.f);
    const float ax2 = 1.f - ax0 - ax1 - ax3;
    const float ay0 = (1.f - ty) * (1.f - ty) * (1.f - ty) * (1.f / 6.f);
    const float ay1 = (4.f - 6.f * ty * ty + 3.f * ty * ty * ty) * (1.f / 6.f);
    const float ay3 = ty * ty * ty * (1.f / 6.f);
    const float ay2 = 1.f - ay0 - ay1 - ay3;
    const float gx0 = ax0 + ax1, gx1 = ax2 + ax3, gy0 = ay0 + ay1, gy1 = ay2 + ay3;
    const float hx0 = ix - 1.f + ax1 / fmaxf(gx0, 1e-6f) + 0.5f, hx1 = ix + 1.f + ax3 / fmaxf(gx1, 1e-6f) + 0.5f;
    const float hy0 = iy - 1.f + ay1 / fmaxf(gy0, 1e-6f) + 0.5f, hy1 = iy + 1.f + ay3 / fmaxf(gy1, 1e-6f) + 0.5f;
    const float3 a = f4bl(b, hx0, hy0, w, h), c = f4bl(b, hx1, hy0, w, h);
    const float3 d = f4bl(b, hx0, hy1, w, h), e = f4bl(b, hx1, hy1, w, h);
    return make_float3(gy0 * (gx0 * a.x + gx1 * c.x) + gy1 * (gx0 * d.x + gx1 * e.x),
                       gy0 * (gx0 * a.y + gx1 * c.y) + gy1 * (gx0 * d.y + gx1 * e.y),
                       gy0 * (gx0 * a.z + gx1 * c.z) + gy1 * (gx0 * d.z + gx1 * e.z));
}
// horizontal linear lookup in one line-buffer row (cx in texel units, centres at integers)
__device__ __forceinline__ float4 lrow(const float4* __restrict__ lines, int row, float cx, int Wl) {
    const float f = floorf(cx);
    const float t = cx - f;
    const int i0 = min(max((int)f, 0), Wl - 1), i1 = min(max((int)f + 1, 0), Wl - 1);
    const float4 a = lines[row * Wl + i0], b = lines[row * Wl + i1];
    return make_float4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, 0.f);
}
__device__ __forceinline__ float beamw(float d, float c, float slo, float shi) {
    const float s = slo + (shi - slo) * sqrtf(fmaxf(c, 0.f));
    return __expf(-0.5f * d * d / (s * s));
}

// ---- pass 5: the tube ----
extern "C" __global__
void crt_main(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
              const float4* __restrict__ lines, const int Wl, const int Lc,
              const float4* __restrict__ bl, const int Wb, const int Hb,
              const float* __restrict__ U, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float L = U[0], isc = U[1], wx = U[2], wy = U[3], rad = U[4];
    const float scan_mix = U[5], slo = U[6], shi = U[7], snorm = U[8];
    const float mmix = U[9], mtype = U[10], T = U[11], convh = U[12], convv = U[13];
    const float bgain = U[14], vig = U[15], tint = U[16], drift = U[17], period = U[18];
    const float bezel = U[19], surge = U[20], hum = U[21], humph = U[22], mcomp = U[24];
    const float fW = (float)W, fH = (float)H;

    // --- geometry: raster scale (bezel inset + HV-sag breathing), then Lottes barrel warp ---
    const float qx = ((x + 0.5f) / fW * 2.f - 1.f) * isc, qy = ((y + 0.5f) / fH * 2.f - 1.f) * isc;
    const float wqx = qx * (1.f + qy * qy * wx), wqy = qy * (1.f + qx * qx * wy);
    const float u = wqx * 0.5f + 0.5f, v = wqy * 0.5f + 0.5f;
    // rounded-rect screen edge, in (warped) pixels
    const float ex = fabsf(wqx) * 0.5f * fW - (0.5f * fW - rad), ey = fabsf(wqy) * 0.5f * fH - (0.5f * fH - rad);
    const float sd = sqrtf(fmaxf(ex, 0.f) * fmaxf(ex, 0.f) + fmaxf(ey, 0.f) * fmaxf(ey, 0.f))
                     + fminf(fmaxf(ex, ey), 0.f) - rad;
    const float alpha = clamp01(0.5f - sd * (0.8f / isc));

    float3 scr = make_float3(0.f, 0.f, 0.f);
    if (alpha > 0.f) {
        // flat (sharp) sample, linear light
        const float4 fl = bilin(src, u * fW, v * fH, W, H);
        float3 col = make_float3(lin1(fl.x), lin1(fl.y), lin1(fl.z));
        if (scan_mix > 1e-5f) {
            const float ly = v * L - 0.5f;
            const int n = (int)floorf(ly + 0.5f);
            const float cx = u * Wl - 0.5f;
            const float ch = convh * (0.35f + 0.65f * qx * qx);         // misconvergence grows to the edges
            const float cvv = convv * (0.35f + 0.65f * qy * qy);
            float ar = 0.f, ag = 0.f, ab = 0.f;
            #pragma unroll
            for (int k = -1; k <= 1; ++k) {
                const int row = n + k;
                if (row < 0 || row >= Lc) continue;
                const float d = ly - (float)row;
                const float4 cg = lrow(lines, row, cx, Wl);
                float cr = cg.x, cb = cg.z;
                if (ch > 1e-4f) {
                    cr = lrow(lines, row, cx - ch, Wl).x;
                    cb = lrow(lines, row, cx + ch, Wl).z;
                }
                ar += cr * beamw(d + cvv, cr, slo, shi);
                ag += cg.y * beamw(d, cg.y, slo, shi);
                ab += cb * beamw(d - cvv, cb, slo, shi);
            }
            col.x += (ar * snorm - col.x) * scan_mix;
            col.y += (ag * snorm - col.y) * scan_mix;
            col.z += (ab * snorm - col.z) * scan_mix;
        }
        // --- phosphor mask (on the glass, screen space) ---
        if (mmix > 1e-5f) {
            const float mx = x + 0.5f, my = y + 0.5f;
            const float wg = clamp01(1.f - mtype), ws = clamp01(1.f - fabsf(mtype - 1.f)), wd = clamp01(mtype - 1.f);
            float M[3] = {0.f, 0.f, 0.f};
            const float ph = mx / T;
            if (wg + ws > 1e-4f) {
                float gap = 1.f;
                if (ws > 1e-4f) {
                    const float j = floorf(ph + 1.f / 6.f);
                    const float par = j - 2.f * floorf(j * 0.5f);
                    const float yph = my / (1.5f * T) + 0.5f * par;
                    const float fy = yph - floorf(yph);
                    const float gw = fmaxf(0.12f, 0.9f / (1.5f * T));
                    gap = smooth(0.f, gw, fy) * smooth(0.f, gw, 1.f - fy);
                }
                #pragma unroll
                for (int k = 0; k < 3; ++k) {
                    const float s = 0.5f + 0.5f * __cosf(6.2831853f * (ph - k * (1.f / 3.f)));
                    const float g = s * s;
                    M[k] += wg * g + ws * g * gap;
                }
            }
            if (wd > 1e-4f) {
                const float Ty = 0.866f * T;
                const float r0 = floorf(my / Ty);
                const float isg = 1.f / (2.f * 0.2f * 0.2f * T * T);
                #pragma unroll
                for (int k = 0; k < 3; ++k) {
                    const float pxk = mx - k * T * (1.f / 3.f);
                    float d2 = 1e9f;
                    #pragma unroll
                    for (int rr = 0; rr < 2; ++rr) {
                        const float row = r0 + rr;
                        const float par = row - 2.f * floorf(row * 0.5f);
                        float dx = pxk - 0.5f * T * par;
                        dx -= T * rintf(dx / T);
                        const float dy = my - row * Ty;
                        d2 = fminf(d2, dx * dx + dy * dy);
                    }
                    M[k] += wd * __expf(-d2 * isg);
                }
            }
            const float dark = 0.18f;
            col.x *= 1.f + ((dark + (1.f - dark) * M[0]) * mcomp - 1.f) * mmix;
            col.y *= 1.f + ((dark + (1.f - dark) * M[1]) * mcomp - 1.f) * mmix;
            col.z *= 1.f + ((dark + (1.f - dark) * M[2]) * mcomp - 1.f) * mmix;
        }
        // --- rolling hum bar, surge, corner falloff ---
        float gain = surge;
        if (hum > 1e-5f) {
            float hv = v - humph;
            hv -= floorf(hv);
            const float hb = 0.5f + 0.5f * __cosf(6.2831853f * hv);
            const float hb2 = hb * hb, hb4 = hb2 * hb2;
            gain *= 1.f - hum * hb4 * hb2;
        }
        if (vig > 1e-5f) {
            const float uu = clamp01(u), vv = clamp01(v);
            const float f = clamp01(16.f * uu * (1.f - uu) * vv * (1.f - vv));
            gain *= 1.f - vig * (1.f - __powf(f, 0.3f));
        }
        col.x *= gain; col.y *= gain; col.z *= gain;
        // --- halation: screen-blend the blurred picture ---
        if (bgain > 1e-5f) {
            const float3 b = f4bs(bl, u * Wb, v * Hb, Wb, Hb);
            col.x = 1.f - (1.f - clamp01(col.x)) * (1.f - clamp01(b.x * bgain));
            col.y = 1.f - (1.f - clamp01(col.y)) * (1.f - clamp01(b.y * bgain));
            col.z = 1.f - (1.f - clamp01(col.z)) * (1.f - clamp01(b.z * bgain));
        }
        scr = make_float3(enc1(col.x), enc1(col.y), enc1(col.z));
        // --- monochrome phosphor cast through the palette ---
        if (tint > 1e-5f) {
            const float lum = (0.2126f * scr.x + 0.7152f * scr.y + 0.0722f * scr.z) * (1.f / 255.f);
            const float3 pc = pal(drift + period * 0.5f * lum, P);
            const float pl = fmaxf(0.2126f * pc.x + 0.7152f * pc.y + 0.0722f * pc.z, 0.12f);
            const float k = 255.f * lum / pl;
            scr.x += (fminf(pc.x * k, 255.f) - scr.x) * tint;
            scr.y += (fminf(pc.y * k, 255.f) - scr.y) * tint;
            scr.z += (fminf(pc.z * k, 255.f) - scr.z) * tint;
        }
    }
    float4 o;
    if (alpha < 1.f) {
        // --- bezel: dark plastic, top-lit, whose inner bevel reflects the blurred picture ---
        float3 bz = make_float3(0.f, 0.f, 0.f);
        if (bezel > 1e-5f) {
            const float sy = (y + 0.5f) / fH;
            const float base = 15.f + 16.f * (1.f - sy) * (1.f - sy);
            float ur = u < 0.f ? -u : (u > 1.f ? 2.f - u : u);
            float vr = v < 0.f ? -v : (v > 1.f ? 2.f - v : v);
            ur = clamp01(ur); vr = clamp01(vr);
            const float3 rb = f4bs(bl, ur * Wb, vr * Hb, Wb, Hb);
            const float refl = 0.55f * __expf(-fmaxf(sd, 0.f) / (0.018f * fH));
            const float lip = __expf(-fmaxf(sd, 0.f) / (0.0025f * fH));     // thin glossy lip at the glass edge
            bz.x = base + enc1(rb.x * refl) * 0.9f + 22.f * lip;
            bz.y = base * 0.97f + enc1(rb.y * refl) * 0.9f + 21.f * lip;
            bz.z = base * 0.94f + enc1(rb.z * refl) * 0.9f + 20.f * lip;
            bz.x *= bezel; bz.y *= bezel; bz.z *= bezel;
        }
        o = make_float4(bz.x + (scr.x - bz.x) * alpha, bz.y + (scr.y - bz.y) * alpha, bz.z + (scr.z - bz.z) * alpha, 255.f);
    } else {
        o = make_float4(scr.x, scr.y, scr.z, 255.f);
    }
    out[y * W + x] = pack(o);
}
"""


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(float(v), lo), hi)


def _smooth01(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class CrtMonitor(Effect):
    name = "crt_monitor"
    stage = "stylise"
    label = "CRT Monitor"
    description = ("A premium arcade / broadcast CRT: curved glass in a rounded bezel, scanlines that widen on "
                   "bright lines, aperture-grille to shadow mask, phosphor bloom and slight misconvergence. "
                   "The beat overdrives the beam and makes the raster breathe.")
    params = {
        **standard_effect_params(),
        # the beat drives the HV-sag spring on the host: keep the envelope raw
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "lines": num(240.0, 1080.0, 480.0, "Virtual scanlines", step=1.0, tier="regional", basic=True),
        "curvature": num(0.0, 1.0, 0.5, "Tube curvature", tier="regional"),
        "mask_type": num(0.0, 2.0, 0.6, "Mask: grille ↔ slot ↔ shadow", tier="regional"),
        "mask_strength": num(0.0, 1.0, 0.55, "Mask strength", tier="regional"),
        "mask_pitch": num(0.5, 3.0, 1.0, "Mask pitch", unit="×", step=0.05, tier="regional"),
        "scanlines": num(0.0, 1.0, 0.75, "Scanline depth", tier="regional"),
        "bloom": num(0.0, 1.0, 0.45, "Phosphor bloom", tier="regional"),
        "convergence": num(0.0, 1.0, 0.3, "Convergence error", tier="regional"),
        "bezel": num(0.0, 1.0, 0.6, "Bezel", tier="regional"),
        "hum": num(0.0, 1.0, 0.2, "Hum bar", tier="regional"),
        "tint": num(0.0, 1.0, 0.12, "Phosphor tint", tier="regional"),
        "punch": num(0.0, 2.0, 0.8, "Beat overdrive", step=0.01, tier="regional"),
        **palette_params("ember", 0.6, 0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k_lines = mod.get_function("crt_lines")
        self._k_down = mod.get_function("crt_bloom_down")
        self._k_blur = mod.get_function("crt_blur")
        self._k_main = mod.get_function("crt_main")
        W, H = ctx.width, ctx.height
        self._Wl = max(16, (W + 1) // 2)
        self._lines = cp.zeros((LMAX, self._Wl, 4), dtype=cp.float32)
        self._Wb = max(4, (W + BLOOM_DIV - 1) // BLOOM_DIV)
        self._Hb = max(4, (H + BLOOM_DIV - 1) // BLOOM_DIV)
        self._bA = cp.zeros((self._Hb, self._Wb, 4), dtype=cp.float32)
        self._bB = cp.zeros((self._Hb, self._Wb, 4), dtype=cp.float32)
        self._U_h = np.zeros(NU, dtype=np.float32)
        self._U_d = cp.zeros(NU, dtype=cp.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "ember")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        self._drift = 0.0
        self._hum = 0.0
        self._x = 0.0      # HV-sag spring
        self._v = 0.0

    def _sag(self, pulse: float, dt: float) -> None:
        if dt > 0.5:
            self._x, self._v = 0.0, 0.0
            return
        dt = min(max(dt, 0.0), 0.1)
        if dt <= 0.0:
            return
        w = 2.0 * math.pi * 2.6
        z = 0.45
        nsub = max(1, int(math.ceil(dt * 120.0)))
        h = dt / nsub
        for _ in range(nsub):
            self._v += (w * w * (pulse - self._x) - 2.0 * z * w * self._v) * h
            self._x += self._v * h
        self._x = _clamp(self._x, -0.6, 1.6)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        pulse = _clamp(p["pulse"], 0.0, 1.0)
        self._sag(pulse, dt)
        dts = min(max(dt, 0.0), 0.1)
        self._drift = (self._drift + float(p["drift_speed"]) * dts) % 1.0
        self._hum = (self._hum + 0.11 * dts) % 1.0
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            k = 1.0 - math.exp(-dt / 1.0)
            self._pal += (tgt - self._pal) * k
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))

        amount = _clamp(p["amount"], 0.0, 1.0)
        if amount <= 1e-4:
            cp.copyto(out, src)
            return
        m = _smooth01(amount)
        punch = max(float(p["punch"]), 0.0)
        pe = pulse * punch

        # virtual raster (never finer than 2 output px per line)
        L = _clamp(p["lines"], 240.0, 1080.0)
        L = max(8.0, min(L, H / 2.0, LMAX - 2.0))
        Lc = min(LMAX, int(math.ceil(L)) + 1)

        curv = _clamp(p["curvature"], 0.0, 1.0)
        bez = _clamp(p["bezel"], 0.0, 1.0)
        inset = 0.075 * bez * m
        breath = 0.014 * m * punch * self._x
        isc = 1.0 / max((1.0 - inset) * (1.0 + breath), 0.5)
        wx = 0.085 * curv * m
        wy = 0.115 * curv * m
        rad = (0.012 + 0.05 * bez + 0.03 * curv) * H * m

        sl = _clamp(p["scanlines"], 0.0, 1.0)
        scan_mix = m * min(1.0, 0.25 + sl)
        slo = 0.30 + (0.13 - 0.30) * sl
        shi = 0.50 + (0.42 - 0.50) * sl
        od = 1.0 + 0.35 * pe * m                       # beam overdrive: bright lines widen
        slo *= od
        shi *= od
        sref = slo + (shi - slo) * 0.6
        snorm = 1.0 / (sref * math.sqrt(2.0 * math.pi))

        mt = _clamp(p["mask_type"], 0.0, 2.0)
        wg = _clamp(1.0 - mt, 0.0, 1.0)
        ws = _clamp(1.0 - abs(mt - 1.0), 0.0, 1.0)
        wd = _clamp(mt - 1.0, 0.0, 1.0)
        avg_m = wg * 0.375 + ws * 0.375 * 0.82 + wd * 0.29
        dark = 0.18
        mcomp = 1.0 / (dark + (1.0 - dark) * avg_m)
        mmix = _clamp(p["mask_strength"], 0.0, 1.0) * m
        T = max(3.0, 3.0 * float(p["mask_pitch"]) * H / 1080.0)

        conv = _clamp(p["convergence"], 0.0, 1.0) * m
        convh = conv * 4.0 * (H / 1080.0) * (self._Wl / W)     # line-buffer texels
        convv = conv * 0.22                                   # lines

        bgain = 0.9 * _clamp(p["bloom"], 0.0, 1.0) * m * (1.0 + 1.3 * pe)
        vig = (0.25 + 0.45 * curv) * m
        surge = 1.0 + 0.22 * pe * m
        hum = 0.10 * _clamp(p["hum"], 0.0, 1.0) * m
        tint = 0.85 * _clamp(p["tint"], 0.0, 1.0) * m

        U = self._U_h
        U[:] = 0.0
        U[0], U[1], U[2], U[3], U[4] = L, isc, wx, wy, rad
        U[5], U[6], U[7], U[8] = scan_mix, slo, shi, snorm
        U[9], U[10], U[11], U[12], U[13] = mmix, mt, T, convh, convv
        U[14], U[15], U[16], U[17], U[18] = bgain, vig, tint, self._drift, float(p["palette_period"])
        U[19], U[20], U[21], U[22], U[23], U[24] = bez * m ** 0.5, surge, hum, self._hum, m, mcomp
        self._U_d.set(U)

        Wl, Wb, Hb = self._Wl, self._Wb, self._Hb
        launch_2d(self._k_lines, Wl, Lc,
                  (src, self._lines, np.int32(W), np.int32(H), np.int32(Wl), np.int32(Lc), np.float32(L)))
        if bgain > 1e-5 or bez > 1e-5:
            launch_2d(self._k_down, Wb, Hb, (src, self._bA, np.int32(W), np.int32(H), np.int32(Wb), np.int32(Hb)))
            launch_2d(self._k_blur, Wb, Hb, (self._bA, self._bB, np.int32(Wb), np.int32(Hb), np.int32(1), np.int32(0)))
            launch_2d(self._k_blur, Wb, Hb, (self._bB, self._bA, np.int32(Wb), np.int32(Hb), np.int32(0), np.int32(1)))
        launch_2d(self._k_main, W, H,
                  (src, out, np.int32(W), np.int32(H), self._lines, np.int32(Wl), np.int32(Lc),
                   self._bA, np.int32(Wb), np.int32(Hb), self._U_d, self._P_d))
