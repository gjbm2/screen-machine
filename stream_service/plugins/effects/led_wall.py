"""LED wall: the picture shown on a stadium / festival LED panel.  The image is box-averaged per LED and drawn as
round or square emitters (or as R/G/B sub-LED triads) with dark unlit gaps, a soft halo bleeding into the gaps,
module seams every N LEDs with per-module calibration drift, per-LED brightness and colour-temperature scatter and
driver colour-depth quantisation.  The emitters overdrive and a refresh band rolls down the panel on the beat.

Source: Original (inspired by https://github.com/BradLarson/GPUImage and
        https://github.com/pmndrs/postprocessing/blob/main/src/effects/DotScreenEffect.js)
Author: smfx (original; ideas from GPUImage PolkaDot, BSD-3-Clause, and pmndrs DotScreen, Zlib)
License: MIT (original)
license_class: permissive

Written from scratch; no code copied (the idea only: sample the cell average, draw a dot per cell).  Pass 1 (only
for pitches >= 8 px) is a quarter-res exact 4x4 box downsample.  Pass 2 computes one value per LED on a lattice
centred on the screen: the box average (bilinear taps on src for small pitches, on the quarter-res buffer for large
ones), an optional luma-preserving palette tint (shared cosine palette, drift integrated on the host), driver
colour-depth quantisation, per-LED hashed brightness and colour-temperature scatter, per-module calibration offsets
(module_size eases by cross-blending the neighbouring integers), the beat overdrive and the rolling refresh band
(quantised per LED row).  Pass 3 (4K) draws the emitter (round -> square morph, hot core) or the triad of R/G/B
sub-LEDs, a dim gap fill, module seam lines and a halo from the 2x2 nearest LEDs (windowed so the neighbourhood
switch is seamless), screened over the emitters; channel overdrive above 1 spills into white.

Realtime: amount -> m = smoothstep(amount): the pitch grows from ~1 px (invisible) to `pitch`, the gaps darken, the
triads, scatter, quantisation, seams and halo all scale with m, and the result is mixed over src with weight
min(1, 2.5 m); amount <= 1e-4 copies src.  pulse -> emitter overdrive (x(1 + 0.55 beat_kick pulse)), halo
widening, and a refresh band rolling down the panel whose brightness is gated by the envelope (its position is a
host-integrated phase, so nothing jumps).  drift_speed -> palette drift rate.
Regional: pitch (basic), emitter_shape, emitter_size, subpixel, gap_darkness, glow, module_size, colour_bits,
variation, tint, beat_kick, refresh_band, palette, palette_period.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NP = 48
S_MIN = 2.0          # smallest LED pitch the lattice buffer is sized for (px at output res)
# P layout: 0..11 palette | 12 drift | 13 period | 14 tint | 15 levels | 16 variation | 17 N0 | 18 N1 | 19 Nfrac
#           20 gain | 21 band amount | 22 band pos | 23 band width | 24 S (px) | 25 cx | 26 cy | 27 emitter radius
#           28 shape | 29 subpixel | 30 gap darkness | 31 hot core | 32 halo amp | 33 halo sigma | 34 seam strength
#           35 mix | 36 H | 37 triad gain | 38 gap brightness compensation

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float4 bl4(const float4* __restrict__ b, float fx, float fy, const int w, const int h) {
    fx = fminf(fmaxf(fx - 0.5f, 0.f), (float)(w - 1));
    fy = fminf(fmaxf(fy - 0.5f, 0.f), (float)(h - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1);
    const float tx = fx - x0, ty = fy - y0, u = 1.f - tx, v = 1.f - ty;
    const float4 a = b[y0 * w + x0], c = b[y0 * w + x1], d = b[y1 * w + x0], e = b[y1 * w + x1];
    return make_float4(v * (u * a.x + tx * c.x) + ty * (u * d.x + tx * e.x),
                       v * (u * a.y + tx * c.y) + ty * (u * d.y + tx * e.y),
                       v * (u * a.z + tx * c.z) + ty * (u * d.z + tx * e.z), 0.f);
}
__device__ __forceinline__ float lum3(float r, float g, float b) { return 0.2126f * r + 0.7152f * g + 0.0722f * b; }
// round (0) -> square (1) distance metric
__device__ __forceinline__ float shp(float fx, float fy, float s) {
    const float ax = fabsf(fx), ay = fabsf(fy);
    const float dc = sqrtf(ax * ax + ay * ay), ds = fmaxf(ax, ay);
    return dc + (ds - dc) * s;
}
__device__ __forceinline__ float seam1(float g, float N, float sw, float S) {
    const float dm = fabsf(g - N * rintf(g / N));
    return clamp01((sw - dm) * S + 0.5f);
}
__device__ __forceinline__ float mod_hash(int i, int j, float N, unsigned int seed, unsigned int salt) {
    const int mi = (int)floorf((float)i / N), mj = (int)floorf((float)j / N);
    return hash1((unsigned int)(mi + 65536), (unsigned int)(mj + 65536), seed ^ salt) - 0.5f;
}

// Quarter-res exact box downsample (each texel = mean of a 4x4 block), 0..1.
extern "C" __global__
void lw_down(const uchar4* __restrict__ src, float4* __restrict__ Q, const int W, const int H, const int w4, const int h4)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w4 || y >= h4) return;
    const float bx = 4.f * x, by = 4.f * y;
    const float4 a = bilin(src, bx + 1.f, by + 1.f, W, H), b = bilin(src, bx + 3.f, by + 1.f, W, H);
    const float4 c = bilin(src, bx + 1.f, by + 3.f, W, H), d = bilin(src, bx + 3.f, by + 3.f, W, H);
    const float k = 1.f / (4.f * 255.f);
    Q[y * w4 + x] = make_float4((a.x + b.x + c.x + d.x) * k, (a.y + b.y + c.y + d.y) * k,
                                (a.z + b.z + c.z + d.z) * k, 0.f);
}

// One value per LED: box average -> tint -> colour depth -> scatter / module calibration -> overdrive + band.
extern "C" __global__
void lw_cells(const uchar4* __restrict__ src, const float4* __restrict__ Q, float4* __restrict__ C,
              const int W, const int H, const int w4, const int h4, const int nhx, const int nhy,
              const int NHX, const int NHY, const int useQ, const unsigned int seed, const float* __restrict__ P)
{
    const int ix = blockIdx.x * blockDim.x + threadIdx.x, iy = blockIdx.y * blockDim.y + threadIdx.y;
    if (ix > 2 * nhx || iy > 2 * nhy) return;
    const int i = ix - nhx, j = iy - nhy;
    const float S = P[24];
    const float cx = P[25] + i * S, cy = P[26] + j * S;
    float r = 0.f, g = 0.f, b = 0.f;
    if (useQ) {
        const int k = min(16, max(2, (int)ceilf(S * 0.25f)));
        const float st = S / k;
        for (int ty = 0; ty < k; ++ty) {
            const float py = cy - 0.5f * S + (ty + 0.5f) * st;
            for (int tx = 0; tx < k; ++tx) {
                const float px = cx - 0.5f * S + (tx + 0.5f) * st;
                const float4 v = bl4(Q, px * 0.25f, py * 0.25f, w4, h4);
                r += v.x; g += v.y; b += v.z;
            }
        }
        const float inv = 1.f / (float)(k * k);
        r *= inv; g *= inv; b *= inv;
    } else {
        const int k = min(4, max(1, (int)ceilf(S * 0.5f)));
        const float st = S / k;
        for (int ty = 0; ty < k; ++ty) {
            const float py = cy - 0.5f * S + (ty + 0.5f) * st;
            for (int tx = 0; tx < k; ++tx) {
                const float px = cx - 0.5f * S + (tx + 0.5f) * st;
                const float4 v = bilin(src, px, py, W, H);
                r += v.x; g += v.y; b += v.z;
            }
        }
        const float inv = 1.f / (255.f * (float)(k * k));
        r *= inv; g *= inv; b *= inv;
    }
    const float l = lum3(r, g, b);

    // palette tint (luma preserving)
    const float tint = P[14];
    if (tint > 0.f) {
        const float3 pc = pal(l * P[13] + P[12], P);
        const float pl = fmaxf(lum3(pc.x, pc.y, pc.z), 0.15f);
        const float s = l / pl;
        r += (fminf(pc.x * s, 1.f) - r) * tint;
        g += (fminf(pc.y * s, 1.f) - g) * tint;
        b += (fminf(pc.z * s, 1.f) - b) * tint;
    }
    // driver colour depth
    const float L = P[15];
    if (L < 254.5f) {
        r = rintf(clamp01(r) * L) / L; g = rintf(clamp01(g) * L) / L; b = rintf(clamp01(b) * L) / L;
    }
    // per-LED scatter + per-module calibration (module size eased by cross-blending neighbouring integers)
    const float var = P[16];
    float gain = P[20] * P[38];
    if (var > 0.f) {
        const unsigned int ui = (unsigned int)(i + 65536), uj = (unsigned int)(j + 65536);
        const float hb = hash1(ui, uj, seed) - 0.5f, ht = hash1(ui, uj, seed ^ 0x5bd1e995u) - 0.5f;
        const float fr = P[19];
        const float mb = (1.f - fr) * mod_hash(i, j, P[17], seed, 0x1234567u) + fr * mod_hash(i, j, P[18], seed, 0x1234567u);
        const float mt = (1.f - fr) * mod_hash(i, j, P[17], seed, 0x7654321u) + fr * mod_hash(i, j, P[18], seed, 0x7654321u);
        gain *= 1.f + var * (0.22f * hb + 0.14f * mb);
        const float tt = var * (0.20f * ht + 0.12f * mt);
        r *= 1.f + tt; g *= 1.f + 0.15f * tt; b *= 1.f - tt;
    }
    // rolling refresh band (per LED row), gated by the beat envelope
    const float ba = P[21];
    if (ba > 0.f) {
        const float dy = (cy / P[36] - P[22]) / P[23];
        gain *= 1.f + ba * __expf(-dy * dy);
    }
    C[(j + NHY) * (2 * NHX + 1) + (i + NHX)] = make_float4(r * gain, g * gain, b * gain, l);
}

// 4K composite: emitter / triads + gap fill + seams + halo, mixed over src.
extern "C" __global__
void lw_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const float4* __restrict__ C,
             const int W, const int H, const int nhx, const int nhy, const int NHX, const int NHY,
             const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int NX = 2 * NHX + 1;
    const float S = P[24];
    const float gx = (x + 0.5f - P[25]) / S + 0.5f, gy = (y + 0.5f - P[26]) / S + 0.5f;
    const int i = (int)floorf(gx), j = (int)floorf(gy);
    const float fx = gx - i - 0.5f, fy = gy - j - 0.5f;
    const int ci = min(max(i, -nhx), nhx), cj = min(max(j, -nhy), nhy);
    const float4 own = C[(cj + NHY) * NX + (ci + NHX)];

    const float re = P[27], sh = P[28], sub = P[29];
    const float d = shp(fx, fy, sh);
    const float mk = clamp01((re - d) * S + 0.5f);
    const float hot = P[31];
    const float q = d / fmaxf(re, 1e-3f);
    const float prof = 1.f - hot * (fminf(q * q, 1.f) - 0.35f);
    float er = own.x * prof * mk, eg = own.y * prof * mk, eb = own.z * prof * mk;
    float lit = mk;
    if (sub > 0.f) {
        // R top-left, G top-right, B bottom (a DIP cluster)
        const float ro = re * 0.53f, rs = re * 0.44f, tg = P[37];
        const float mR = clamp01((rs - shp(fx + 0.866f * ro, fy + 0.5f * ro, sh)) * S + 0.5f);
        const float mG = clamp01((rs - shp(fx - 0.866f * ro, fy + 0.5f * ro, sh)) * S + 0.5f);
        const float mB = clamp01((rs - shp(fx, fy - ro, sh)) * S + 0.5f);
        er += (own.x * tg * mR - er) * sub;
        eg += (own.y * tg * mG - eg) * sub;
        eb += (own.z * tg * mB - eb) * sub;
        lit += (fmaxf(mR, fmaxf(mG, mB)) - lit) * sub;
    }

    // gap fill (dims with gap darkness) and module seams
    const float gd = P[30];
    const float sw = fmaxf(0.6f / S, 0.03f), fr = P[19];
    const float seam = (1.f - fr) * fmaxf(seam1(gx, P[17], sw, S), seam1(gy, P[17], sw, S))
                     + fr * fmaxf(seam1(gx, P[18], sw, S), seam1(gy, P[18], sw, S));
    const float gk = (1.f - gd) * (1.f - lit) * (1.f - seam * P[34]);
    float r = er + own.x * gk, g = eg + own.y * gk, b = eb + own.z * gk;

    // halo from the 2x2 nearest LEDs, windowed so the neighbourhood switch at cell centres is seamless
    const float ha = P[32];
    if (ha > 0.f) {
        const int i0 = (int)floorf(gx - 0.5f), j0 = (int)floorf(gy - 0.5f);
        const float sg = P[33], rin = re * 0.6f;
        float hr = 0.f, hg = 0.f, hb = 0.f;
        #pragma unroll
        for (int k = 0; k < 4; ++k) {
            const int ii = i0 + (k & 1), jj = j0 + (k >> 1);
            const float dx = gx - (ii + 0.5f), dy = gy - (jj + 0.5f);
            const float win = clamp01((1.f - fmaxf(fabsf(dx), fabsf(dy))) * 3.f);
            const float e = fmaxf(shp(dx, dy, sh) - rin, 0.f) / sg;
            const float w = __expf(-e * e) * win;
            const int ki = min(max(ii, -nhx), nhx), kj = min(max(jj, -nhy), nhy);
            const float4 v = C[(kj + NHY) * NX + (ki + NHX)];
            hr += v.x * w; hg += v.y * w; hb += v.z * w;
        }
        const float hk = ha * (1.f - 0.6f * lit) * (1.f - seam * P[34]);
        hr = clamp01(hr * hk); hg = clamp01(hg * hk); hb = clamp01(hb * hk);
        // overdrive spill first, then screen the halo
        const float ex = fmaxf(fmaxf(r, fmaxf(g, b)) - 1.f, 0.f) * 0.6f;
        r = clamp01(r + ex); g = clamp01(g + ex); b = clamp01(b + ex);
        r = 1.f - (1.f - r) * (1.f - hr); g = 1.f - (1.f - g) * (1.f - hg); b = 1.f - (1.f - b) * (1.f - hb);
    } else {
        const float ex = fmaxf(fmaxf(r, fmaxf(g, b)) - 1.f, 0.f) * 0.6f;
        r = clamp01(r + ex); g = clamp01(g + ex); b = clamp01(b + ex);
    }

    const uchar4 s8 = src[y * W + x];
    const float m = P[35];
    const float sr = s8.x * (1.f / 255.f), sgc = s8.y * (1.f / 255.f), sb = s8.z * (1.f / 255.f);
    out[y * W + x] = pack(make_float4(255.f * (sr + (r - sr) * m), 255.f * (sgc + (g - sgc) * m),
                                      255.f * (sb + (b - sb) * m), 255.f));
}
"""


class LedWall(Effect):
    name = "led_wall"
    label = "LED wall"
    stage = "stylise"
    description = ("Stadium / festival LED panel: per-LED box-averaged emitters (round, square or R/G/B triads) with "
                   "dark gaps, soft halo, module seams and colour-depth banding; overdrives on the beat.")
    params = {
        **standard_effect_params(),
        **palette_params("neon", 0.8, 0.03),
        "pitch": num(8.0, 48.0, 18.0, "LED pitch", step=0.5, unit="px", tier="regional", basic=True),
        "emitter_shape": num(0.0, 1.0, 0.12, "Emitter shape (round -> square)", tier="regional"),
        "emitter_size": num(0.4, 1.0, 0.74, "Emitter size (of pitch)", tier="regional"),
        "subpixel": num(0.0, 1.0, 0.0, "Sub-LEDs (full colour -> RGB triads)", tier="regional", basic=True),
        "gap_darkness": num(0.0, 1.0, 0.88, "Gap darkness", tier="regional"),
        "glow": num(0.0, 1.0, 0.5, "Halo glow", tier="regional"),
        "module_size": num(2.0, 32.0, 12.0, "Module size", step=1.0, unit="LEDs", tier="regional"),
        "colour_bits": num(2.0, 8.0, 6.0, "Colour depth", step=0.1, unit="bits", tier="regional"),
        "variation": num(0.0, 1.0, 0.5, "LED / module variation", tier="regional"),
        "tint": num(0.0, 1.0, 0.15, "Palette tint", tier="regional"),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat overdrive", step=0.05, tier="regional"),
        "refresh_band": num(0.0, 1.0, 0.6, "Beat refresh band", tier="regional"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kd = mod.get_function("lw_down")
        self._kcell = mod.get_function("lw_cells")
        self._kc = mod.get_function("lw_comp")
        W, H = ctx.width, ctx.height
        self.w4, self.h4 = max(1, (W + 3) // 4), max(1, (H + 3) // 4)
        self.Q = cp.zeros((self.h4, self.w4, 4), cp.float32)
        self.NHX = int(math.ceil(0.5 * W / S_MIN)) + 2
        self.NHY = int(math.ceil(0.5 * H / S_MIN)) + 2
        self.C = cp.zeros((2 * self.NHY + 1, 2 * self.NHX + 1, 4), cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.drift = 0.0
        self.band = 0.0
        self.pal = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.25)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)

        # host-integrated phases (always, so nothing jumps when the effect fades back in)
        bw = 0.075
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 10.0)
        self.band = math.fmod(self.band + 0.85 * (1.0 + 1.5 * pulse) * dt, 1.0 + 4.0 * bw)
        pc = np.asarray(palette_coeffs(p["palette"]), np.float32)
        ap = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.pal = pc if self.pal is None else self.pal + (pc - self.pal) * ap

        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        m = a * a * (3.0 - 2.0 * a)
        W, H = self.ctx.width, self.ctx.height
        S_full = max(float(p["pitch"]), 8.0) * H / 1080.0
        S = max(S_MIN, 1.0 + (S_full - 1.0) * m)
        N = min(max(float(p["module_size"]), 2.0), 32.0)
        N0 = math.floor(N)
        gd = min(max(float(p["gap_darkness"]), 0.0), 1.0) * m
        sub = min(max(float(p["subpixel"]), 0.0), 1.0) * m
        kick = float(p["beat_kick"]) * pulse * m
        bits = 8.0 - (8.0 - min(max(float(p["colour_bits"]), 2.0), 8.0)) * m
        glow = min(max(float(p["glow"]), 0.0), 1.0)
        re = 0.5 * min(max(float(p["emitter_size"]), 0.4), 1.0)

        hp = self.host
        hp[0:12] = self.pal
        hp[12] = self.drift
        hp[13] = float(p["palette_period"])
        hp[14] = min(max(float(p["tint"]), 0.0), 1.0) * m
        hp[15] = 2.0 ** bits - 1.0
        hp[16] = min(max(float(p["variation"]), 0.0), 1.0) * m
        hp[17] = float(N0)
        hp[18] = float(N0 + 1)
        hp[19] = N - N0
        hp[20] = 1.0 + 0.55 * kick
        hp[21] = 0.9 * min(max(float(p["refresh_band"]), 0.0), 1.0) * pulse * m
        hp[22] = self.band - 2.0 * bw
        hp[23] = bw
        hp[24] = S
        hp[25] = 0.5 * W
        hp[26] = 0.5 * H
        hp[27] = re
        hp[28] = min(max(float(p["emitter_shape"]), 0.0), 1.0)
        hp[29] = sub
        hp[30] = gd
        hp[31] = 0.22 * m
        hp[32] = 0.85 * glow * m
        hp[33] = 0.06 + 0.26 * glow * (1.0 + 0.8 * min(kick, 1.5))
        hp[34] = 0.85 * m
        hp[35] = min(1.0, 2.5 * m)
        hp[36] = float(H)
        hp[37] = 1.0 + 0.8 * sub / max(m, 1e-3)              # triad sub-LED gain (full 1.8 at subpixel 1)
        hp[38] = 1.0 + 0.3 * gd                               # brightness compensation for the dark gaps
        self.P.set(hp)

        nhx = min(int(math.ceil(0.5 * W / S)) + 1, self.NHX)
        nhy = min(int(math.ceil(0.5 * H / S)) + 1, self.NHY)
        useQ = 1 if S >= 8.0 else 0
        iW, iH, w4, h4 = np.int32(W), np.int32(H), np.int32(self.w4), np.int32(self.h4)
        if useQ:
            launch_2d(self._kd, self.w4, self.h4, (src, self.Q, iW, iH, w4, h4))
        seed = np.uint32(int(p.get("seed", 1)) & 0xFFFFFFFF)
        launch_2d(self._kcell, 2 * nhx + 1, 2 * nhy + 1,
                  (src, self.Q, self.C, iW, iH, w4, h4, np.int32(nhx), np.int32(nhy),
                   np.int32(self.NHX), np.int32(self.NHY), np.int32(useQ), seed, self.P))
        launch_2d(self._kc, W, H, (src, out, self.C, iW, iH, np.int32(nhx), np.int32(nhy),
                                   np.int32(self.NHX), np.int32(self.NHY), self.P))
