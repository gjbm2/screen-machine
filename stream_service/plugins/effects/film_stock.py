"""Film Stock: process-film emulation on one continuous stock axis.  Bleach bypass (silver retained in the print:
crunchy, desaturated contrast) morphs through Technicolor two-strip (red / cyan record) to three-strip dye transfer
(saturated candy colour), with optional red halation round the highlights and drifting silver / dye grain.

Source: https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/BleachBypassShader.js
        https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/TechnicolorShader.js
        (licence: https://github.com/mrdoob/three.js/blob/dev/LICENSE, MIT)
Author: three.js authors (BleachBypassShader after the NVIDIA Shader Library; TechnicolorShader); three-strip dye model smfx (v002 Film Effects is CC-BY-NC-SA, not used)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: one CUDA pass at 4K on sRGB values.  Bleach bypass = three.js's
luma overlay (multiply below 0.45 luma, screen above) plus a silver-retention desaturation; two-strip = three.js's
(r, (g+b)/2, (g+b)/2) with a slight teal bias and a dye-saturation push; three-strip = original smfx log-density
model: optical densities per record with dye crosstalk sharpening d' = d(1+k) - k*mean(other two) (neutrals stay
neutral), back to transmittance, partial luma restore and a print S-curve.  The three looks blend continuously on
the stock axis.  Halation = quarter-res soft-threshold highlight buffer, separable Gaussian, screened back in
red-orange.  Grain = two-octave value noise at ~1.6 px (4K), reseeded on a host-integrated 24 fps counter and
crossfaded between seeds (variance-normalised), midtone-weighted, with dye-cloud chroma grain on the colour stocks.
A print split-tone takes shadow / highlight tints from the shared cosine palette (host twin of pal()) with drift.)

Realtime: amount (m = smoothstep(amount) mixes the print over src; 0 copies src), pulse (dye saturation +
contrast punch and a halation bloom kick, all scaled by m), drift_speed (rate).
Regional: stock (basic: bleach .. 2-strip .. 3-strip), dye_strength, halation (basic), grain, warmth, print_tone,
punch, palette, palette_period.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NPRM = 24       # 0..2 shadow tone | 3..5 highlight tone | 6 m | 7 stock | 8 dye | 9 contrast+ | 10 halation gain
                # 11 grain | 12 warmth | 13 print_tone | 14 grain xfade | 15 chroma grain | 16 grain scale
                # 17..19 halation tint
HALO_R = 24     # blur taps each side (quarter res)

_SRC = HELPERS + r"""
__device__ __forceinline__ float lum3(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }
__device__ __forceinline__ float3 mix3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
__device__ __forceinline__ float3 sat3(float3 c, float s) {
    const float l = lum3(c);
    return make_float3(l + (c.x - l) * s, l + (c.y - l) * s, l + (c.z - l) * s);
}
__device__ __forceinline__ float3 clamp3(float3 c) { return make_float3(clamp01(c.x), clamp01(c.y), clamp01(c.z)); }

// Quarter-res highlight buffer: 4 bilinear taps over the 4x4 footprint, soft threshold per tap.
extern "C" __global__
void fs_hi(const uchar4* __restrict__ src, float* __restrict__ hi, const int W, const int H, const int w, const int h,
           const float thr)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (float)W / w, sy = (float)H / h;
    const float cx = (x + 0.5f) * sx, cy = (y + 0.5f) * sy, ox = 0.25f * sx, oy = 0.25f * sy;
    float acc = 0.f;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        const float4 s = bilin(src, cx + ((i & 1) ? ox : -ox), cy + ((i & 2) ? oy : -oy), W, H);
        const float l = luma(s);
        acc += smooth(thr, thr + 0.3f, l) * l;
    }
    hi[y * w + x] = 0.25f * acc;
}

// Separable Gaussian (dx, dy) = (1, 0) or (0, 1), bounded taps.
extern "C" __global__
void fs_blur(const float* __restrict__ s, float* __restrict__ d, const int w, const int h, const int dx, const int dy,
             const float sigma)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float k = -0.5f / fmaxf(sigma * sigma, 1e-3f);
    float acc = 0.f, ws = 0.f;
    for (int i = -""" + str(HALO_R) + r"""; i <= """ + str(HALO_R) + r"""; ++i) {
        const int xx = min(max(x + i * dx, 0), w - 1), yy = min(max(y + i * dy, 0), h - 1);
        const float wt = __expf(k * (float)(i * i));
        acc += wt * s[yy * w + xx]; ws += wt;
    }
    d[y * w + x] = acc / fmaxf(ws, 1e-6f);
}

// Two-octave value noise, centred, crossfaded between two reseeds and variance-normalised.
__device__ __forceinline__ float grain2(float gx, float gy, unsigned int s0, unsigned int s1, float f) {
    const float n0 = 0.62f * vnoise(gx, gy, s0) + 0.38f * vnoise(gx * 0.47f + 13.1f, gy * 0.47f + 7.7f, s0 + 911u) - 0.5f;
    const float n1 = 0.62f * vnoise(gx, gy, s1) + 0.38f * vnoise(gx * 0.47f + 13.1f, gy * 0.47f + 7.7f, s1 + 911u) - 0.5f;
    return ((1.f - f) * n0 + f * n1) * rsqrtf((1.f - f) * (1.f - f) + f * f);
}
__device__ __forceinline__ float grain1(float gx, float gy, unsigned int s0, unsigned int s1, float f) {
    const float n0 = vnoise(gx, gy, s0) - 0.5f, n1 = vnoise(gx, gy, s1) - 0.5f;
    return ((1.f - f) * n0 + f * n1) * rsqrtf((1.f - f) * (1.f - f) + f * f);
}

extern "C" __global__
void film_stock(const uchar4* __restrict__ src, const float* __restrict__ halo, uchar4* __restrict__ out,
                const int W, const int H, const int w, const int h, const float* __restrict__ P,
                const unsigned int s0, const unsigned int s1)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 px = src[y * W + x];
    const float3 s = make_float3(px.x * (1.f / 255.f), px.y * (1.f / 255.f), px.z * (1.f / 255.f));
    const float m = P[6], stock = P[7], dye = P[8], warm = P[12];

    // camera-side warmth (lens / stock balance)
    float3 c = clamp3(make_float3(s.x * (1.f + 0.11f * warm), s.y * (1.f + 0.015f * warm), s.z * (1.f - 0.13f * warm)));

    // --- bleach bypass (three.js BleachBypassShader) + silver-retention desaturation ---
    const float lb = 0.25f * c.x + 0.65f * c.y + 0.1f * c.z;
    const float L = clamp01(10.f * (lb - 0.45f));
    float3 bb;
    bb.x = (1.f - L) * (2.f * c.x * lb) + L * (1.f - 2.f * (1.f - lb) * (1.f - c.x));
    bb.y = (1.f - L) * (2.f * c.y * lb) + L * (1.f - 2.f * (1.f - lb) * (1.f - c.y));
    bb.z = (1.f - L) * (2.f * c.z * lb) + L * (1.f - 2.f * (1.f - lb) * (1.f - c.z));
    bb = mix3(c, bb, fminf(0.55f + 0.45f * dye, 1.4f));
    bb = clamp3(sat3(bb, fmaxf(0.82f - 0.3f * dye, 0.f)));

    // --- Technicolor two-strip (three.js TechnicolorShader) with a dye push ---
    const float gb = 0.5f * (c.y + c.z);
    float3 ts = make_float3(c.x, gb * 1.02f, gb * 0.9f);
    ts = clamp3(sat3(ts, 1.f + 0.4f * dye));
    ts = mix3(ts, make_float3(smooth(0.f, 1.f, ts.x), smooth(0.f, 1.f, ts.y), smooth(0.f, 1.f, ts.z)), 0.25f * dye);

    // --- three-strip dye transfer (smfx log-density crosstalk model) ---
    float3 t3;
    {
        const float dr = -__logf(fmaxf(c.x, 0.02f)), dg = -__logf(fmaxf(c.y, 0.02f)), db = -__logf(fmaxf(c.z, 0.02f));
        const float k = 0.5f * dye;
        t3.x = __expf(-fmaxf(dr * (1.f + k) - k * 0.5f * (dg + db), 0.f));
        t3.y = __expf(-fmaxf(dg * (1.f + k) - k * 0.5f * (dr + db), 0.f));
        t3.z = __expf(-fmaxf(db * (1.f + k) - k * 0.5f * (dr + dg), 0.f));
        const float li = lum3(c), lo = lum3(t3);
        const float r = fminf(fmaxf(__powf(li / fmaxf(lo, 1e-3f), 0.6f), 0.6f), 1.8f);
        t3 = clamp3(make_float3(t3.x * r, t3.y * r, t3.z * r));
        t3 = clamp3(sat3(t3, 1.f + 0.22f * dye));
        t3 = mix3(t3, make_float3(smooth(0.f, 1.f, t3.x), smooth(0.f, 1.f, t3.y), smooth(0.f, 1.f, t3.z)), 0.3f + 0.15f * dye);
    }

    // --- the stock axis: 0 bleach .. 0.5 two-strip .. 1 three-strip ---
    float3 f = stock < 0.5f ? mix3(bb, ts, 2.f * stock) : mix3(ts, t3, 2.f * stock - 1.f);

    // beat contrast punch (around mid grey)
    const float ce = P[9];
    f = clamp3(make_float3(0.5f + (f.x - 0.5f) * (1.f + ce), 0.5f + (f.y - 0.5f) * (1.f + ce), 0.5f + (f.z - 0.5f) * (1.f + ce)));

    // print split-tone from the palette (tints are luma-normalised; shadows and highlights weighted)
    const float tone = P[13];
    if (tone > 0.f) {
        const float l = lum3(f);
        const float ws = tone * (1.f - l) * (1.f - l), wh = tone * l * l;
        f.x *= 1.f + (P[0] - 1.f) * ws + (P[3] - 1.f) * wh;
        f.y *= 1.f + (P[1] - 1.f) * ws + (P[4] - 1.f) * wh;
        f.z *= 1.f + (P[2] - 1.f) * ws + (P[5] - 1.f) * wh;
        f = clamp3(f);
    }

    // halation: screened red-orange glow from the quarter-res highlight buffer
    const float hg = P[10];
    if (hg > 0.f) {
        float fx = (x + 0.5f) * w / W - 0.5f, fy = (y + 0.5f) * h / H - 0.5f;
        const float flx = floorf(fx), fly = floorf(fy), tx = fx - flx, ty = fy - fly;
        const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
        const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
        const float hv = (1.f - ty) * ((1.f - tx) * halo[y0 * w + x0] + tx * halo[y0 * w + x1])
                       + ty * ((1.f - tx) * halo[y1 * w + x0] + tx * halo[y1 * w + x1]);
        const float g = 1.f - __expf(-hg * hv);
        const float3 hc = make_float3(P[17] * g, P[18] * g, P[19] * g);
        f = make_float3(f.x + hc.x - f.x * hc.x, f.y + hc.y - f.y * hc.y, f.z + hc.z - f.z * hc.z);
    }

    // grain: silver (mono) + dye-cloud chroma on the colour stocks, midtone weighted
    const float ga = P[11];
    if (ga > 0.f) {
        const float gsc = P[16], xf = P[14];
        const float gx = (x + 0.5f) * gsc, gy = (y + 0.5f) * gsc;
        const float l = lum3(f);
        const float prof = 0.35f + 2.6f * l * (1.f - l);
        const float n = grain2(gx, gy, s0, s1, xf) * ga * 0.55f * prof;
        float nr = 0.f, nb = 0.f;
        const float cg = P[15];
        if (cg > 0.f) {
            nr = grain1(gx * 0.8f + 31.7f, gy * 0.8f + 3.3f, s0 + 4049u, s1 + 4049u, xf) * cg * ga * 0.45f * prof;
            nb = grain1(gx * 0.8f + 5.9f, gy * 0.8f + 47.1f, s0 + 7919u, s1 + 7919u, xf) * cg * ga * 0.45f * prof;
        }
        f = clamp3(make_float3(f.x + n + nr, f.y + n - 0.5f * (nr + nb), f.z + n + nb));
    }

    const float3 o = mix3(s, f, m);
    out[y * W + x] = pack(make_float4(o.x * 255.f, o.y * 255.f, o.z * 255.f, 255.f));
}
"""

_LW = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)


def _pal_host(t: float, P: np.ndarray) -> np.ndarray:
    """Host twin of PALETTE_CUDA pal(): a + b cos(2 pi (c t + d)), clamped."""
    return np.clip(P[0:3] + P[3:6] * np.cos(2.0 * math.pi * (P[6:9] * t + P[9:12])), 0.0, 1.0)


def _tone(c: np.ndarray) -> np.ndarray:
    """Luma-normalised tint multiplier (luma 1), clamped so a dark palette stop cannot blow out a channel."""
    return np.clip(c / max(float(np.dot(c, _LW)), 0.08), 0.0, 2.2)


class FilmStock(Effect):
    name = "film_stock"
    stage = "colour"
    label = "Film stock"
    description = ("Process-film emulation on one stock axis: bleach bypass (crunchy, desaturated silver) through "
                   "Technicolor two-strip red / cyan to three-strip dye-transfer candy colour, with red halation "
                   "round highlights and drifting grain.")
    tags = {"family": ["colour"], "dims": ["2d"], "technique": ["colour", "retro"]}
    params = {
        **standard_effect_params(),
        **palette_params("ember", 1.0, 0.02),
        "stock": num(0.0, 1.0, 0.85, "Stock (bleach .. 2-strip .. 3-strip)", basic=True),
        "dye_strength": num(0.0, 2.0, 1.0, "Dye / silver strength", step=0.05),
        "halation": num(0.0, 1.0, 0.4, "Red halation", basic=True),
        "grain": num(0.0, 1.0, 0.35, "Grain"),
        "warmth": num(-1.0, 1.0, 0.2, "Warmth (cool .. warm)"),
        "print_tone": num(0.0, 1.0, 0.2, "Print split-tone (palette)"),
        "punch": num(0.0, 2.0, 1.0, "Beat punch (dye, contrast, halation)", step=0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k = mod.get_function("film_stock")
        self._khi = mod.get_function("fs_hi")
        self._kblur = mod.get_function("fs_blur")
        W, H = ctx.width, ctx.height
        self._w, self._h = max(1, (W + 3) // 4), max(1, (H + 3) // 4)
        self._hi = cp.zeros((self._h, self._w), dtype=cp.float32)
        self._tmp = cp.zeros((self._h, self._w), dtype=cp.float32)
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._hp = np.zeros(NPRM, dtype=np.float32)
        p = self.easer.values()
        self._pal = np.asarray(palette_coeffs(p["palette"]), dtype=np.float64)
        self._drift = 0.0
        self._gclock = 0.0
        self._seed = 1

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)

        # host-integrated clocks (wrapped); palette coefficients ease
        self._drift = (self._drift + float(p["drift_speed"]) * dt) % 1.0
        self._gclock = (self._gclock + 24.0 * dt) % 65536.0
        a_pal = 1.0 - math.exp(-dt / 0.9) if dt > 0 else 0.0
        self._pal += (np.asarray(palette_coeffs(p["palette"]), dtype=np.float64) - self._pal) * a_pal

        amt = min(max(float(p["amount"]), 0.0), 1.0)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = amt * amt * (3.0 - 2.0 * amt)
        pk = min(max(float(p["pulse"]), 0.0), 1.0) * max(float(p["punch"]), 0.0) * m

        stock = min(max(float(p["stock"]), 0.0), 1.0)
        dye = max(float(p["dye_strength"]), 0.0) * (1.0 + 0.5 * pk)
        warm = min(max(float(p["warmth"]), -1.0), 1.0)
        hal = max(float(p["halation"]), 0.0)
        hgain = 3.2 * hal * (1.0 + 1.6 * pk)
        W, H = self.ctx.width, self.ctx.height

        period = float(p["palette_period"])
        sh = _tone(_pal_host(self._drift, self._pal))
        hl = _tone(_pal_host(self._drift + 0.5 * period, self._pal))
        hp = self._hp
        hp[0:3] = sh
        hp[3:6] = hl
        hp[6] = m
        hp[7] = stock
        hp[8] = dye
        hp[9] = 0.22 * pk
        hp[10] = hgain
        hp[11] = max(float(p["grain"]), 0.0)
        hp[12] = warm
        hp[13] = min(max(float(p["print_tone"]), 0.0), 1.0)
        n0 = int(math.floor(self._gclock))
        hp[14] = self._gclock - n0
        hp[15] = 0.35 * stock
        hp[16] = 1.0 / (1.6 * max(H, 1) / 2160.0)
        # halation tint: red-orange, a touch warmer / cooler with warmth
        hp[17] = 1.0
        hp[18] = 0.30 + 0.06 * warm
        hp[19] = 0.12 - 0.05 * warm
        self._prm.set(hp)

        if hgain > 0.0:
            thr = 0.62 - 0.1 * pk
            sigma = 7.0 + 2.0 * min(pk, 1.0)
            launch_2d(self._khi, self._w, self._h, (src, self._hi, np.int32(W), np.int32(H), np.int32(self._w),
                                                    np.int32(self._h), np.float32(thr)))
            launch_2d(self._kblur, self._w, self._h, (self._hi, self._tmp, np.int32(self._w), np.int32(self._h),
                                                      np.int32(1), np.int32(0), np.float32(sigma)))
            launch_2d(self._kblur, self._w, self._h, (self._tmp, self._hi, np.int32(self._w), np.int32(self._h),
                                                      np.int32(0), np.int32(1), np.float32(sigma)))

        base = (self._seed * 7919) & 0x7FFFFFFF
        s0 = np.uint32((base + n0 * 2654435761) & 0xFFFFFFFF)
        s1 = np.uint32((base + (n0 + 1) * 2654435761) & 0xFFFFFFFF)
        launch_2d(self._k, W, H, (src, self._hi, out, np.int32(W), np.int32(H), np.int32(self._w), np.int32(self._h),
                                  self._prm, s0, s1))

    def close(self) -> None:
        self._hi = self._tmp = self._prm = None
