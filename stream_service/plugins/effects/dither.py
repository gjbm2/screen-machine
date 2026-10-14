"""Retro Dither: posterise plus ordered dither.  Channels (source colours) or luma (palette mode) are quantised
against a Bayer 2/4/8 matrix or a host-generated blue-noise tile at a chunky virtual pixel scale, for 1-bit Mac,
Game Boy and CGA looks; the palette mode indexes the shared cosine palette by luma band and drifts / cycles it
(demoscene colour cycling), so still art moves.

Source: https://github.com/hughsk/glsl-dither
        https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_Dither.fx
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Dither-Bayer.fs
Author: Hugh Kennedy (glsl-dither); David Lublin (ISF Dither-Bayer); Garrett Gunnell (AcerolaFX Dither);
        blue-noise tile original (void-and-cluster on the host)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the Bayer 2/4/8 threshold matrices, the "add spread * (threshold - 0.5)
then floor-quantise to n levels" ordered dither and the virtual pixel downscale are kept from the sources.  New: the
matrix choice is a continuous morph (Bayer 2 -> 4 -> 8 -> blue noise, thresholds cross-blended); the blue-noise tile
is generated in setup by void-and-cluster (Ulichney) in numpy; virtual pixels are box-filtered with 4 bilinear taps
and anchored at the frame centre; a palette mode quantises luma and indexes pal() by band with an optional
brightness lock so the picture stays readable in any palette; levels ease continuously in log space.)

amount 0 copies the input.  m = smoothstep(amount) lowers the levels from 256 toward the target (log space), blends
in the dither spread, grows the virtual pixel from 1 real pixel, and fades the palette and contrast in, so 0 -> 1
is a smooth morph and tiny amounts are near-identity.  pulse gives a brief extra posterise crunch (levels / (1 +
kick)) and kicks the colour-cycle rate (integrated on the host, so colours never jump).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NP = 32
BN = 64                                   # blue-noise tile size (power of two)
T_B2, T_B4, T_B8, T_BN = 0, 4, 20, 84     # offsets into the threshold table
# P layout: 0..11 palette | 12 palette phase | 13 period | 14 S block px | 15 tap offset | 16 Lf (levels - 1)
#           17 spread | 18 contrast | 19 palette mix | 20 matrix 0..3 | 21 cx | 22 cy | 23 luma lock

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float lum3(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

__device__ __forceinline__ float thr_of(const float* __restrict__ T, int k, int ix, int iy) {
    if (k == 0) return T[0 + (iy & 1) * 2 + (ix & 1)];
    if (k == 1) return T[4 + (iy & 3) * 4 + (ix & 3)];
    if (k == 2) return T[20 + (iy & 7) * 8 + (ix & 7)];
    return T[84 + (iy & 63) * 64 + (ix & 63)];
}

__device__ __forceinline__ float quant(float v, float Lf, float t) {
    return clamp01(floorf(clamp01(v) * Lf + t) / Lf);
}

extern "C" __global__
void dither_k(const uchar4* __restrict__ src, uchar4* __restrict__ out, const float* __restrict__ T,
              const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float S = P[14], o = P[15], cx = P[21], cy = P[22];
    const float gx = floorf((x + 0.5f - cx) / S), gy = floorf((y + 0.5f - cy) / S);
    const float bx = cx + (gx + 0.5f) * S, by = cy + (gy + 0.5f) * S;
    float4 c;
    if (o > 0.01f) {
        const float4 a = bilin(src, bx - o, by - o, W, H), b = bilin(src, bx + o, by - o, W, H);
        const float4 d = bilin(src, bx - o, by + o, W, H), e = bilin(src, bx + o, by + o, W, H);
        c = make_float4(0.25f * (a.x + b.x + d.x + e.x), 0.25f * (a.y + b.y + d.y + e.y),
                        0.25f * (a.z + b.z + d.z + e.z), 255.f);
    } else {
        c = bilin(src, bx, by, W, H);
    }
    const float ct = P[18];
    float3 v = make_float3((c.x * (1.f / 255.f) - 0.5f) * ct + 0.5f, (c.y * (1.f / 255.f) - 0.5f) * ct + 0.5f,
                           (c.z * (1.f / 255.f) - 0.5f) * ct + 0.5f);

    // ordered-dither threshold: continuous morph Bayer2 -> Bayer4 -> Bayer8 -> blue noise
    const int ix = (int)gx, iy = (int)gy;
    const float mf = fminf(fmaxf(P[20], 0.f), 3.f);
    const int k0 = min((int)floorf(mf), 3), k1 = min(k0 + 1, 3);
    const float fr = mf - (float)k0;
    float th = thr_of(T, k0, ix, iy);
    if (fr > 1e-3f) th += (thr_of(T, k1, ix, iy) - th) * fr;
    const float t = 0.5f + P[17] * (th - 0.5f);
    const float Lf = P[16];

    // source mode: per-channel posterise
    const float3 qs = make_float3(quant(v.x, Lf, t), quant(v.y, Lf, t), quant(v.z, Lf, t));
    float3 col = qs;
    const float pm = P[19];
    if (pm > 1e-4f) {
        // palette mode: quantise luma, index the palette by band
        const float lq = quant(lum3(make_float3(clamp01(v.x), clamp01(v.y), clamp01(v.z))), Lf, t);
        float3 pc = pal(P[12] + 0.5f * P[13] * (1.f - lq), P);
        const float lk = P[23];
        if (lk > 1e-4f) {
            const float l = lum3(pc);
            float3 adj;
            if (lq < l) {
                const float s = lq / fmaxf(l, 1e-4f);
                adj = make_float3(pc.x * s, pc.y * s, pc.z * s);
            } else {
                const float s = (lq - l) / fmaxf(1.f - l, 1e-4f);
                adj = make_float3(pc.x + (1.f - pc.x) * s, pc.y + (1.f - pc.y) * s, pc.z + (1.f - pc.z) * s);
            }
            pc = make_float3(pc.x + (adj.x - pc.x) * lk, pc.y + (adj.y - pc.y) * lk, pc.z + (adj.z - pc.z) * lk);
        }
        col = make_float3(qs.x + (pc.x - qs.x) * pm, qs.y + (pc.y - qs.y) * pm, qs.z + (pc.z - qs.z) * pm);
    }
    out[y * W + x] = pack(make_float4(255.f * col.x, 255.f * col.y, 255.f * col.z, 255.f));
}
"""


def _bayer(n: int) -> np.ndarray:
    m = np.array([[0, 2], [3, 1]], np.int64)
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m.astype(np.float64) + 0.5) / (n * n)


_BN_CACHE: dict = {}


def _blue_noise(n: int = BN, sigma: float = 1.5, seed: int = 7) -> np.ndarray:
    """Void-and-cluster blue-noise rank matrix (Ulichney 1993), thresholds in (0, 1).  ~0.3 s, cached."""
    key = (n, sigma, seed)
    if key in _BN_CACHE:
        return _BN_CACHE[key]
    rng = np.random.default_rng(seed)
    N = n * n
    d = np.minimum(np.arange(n), n - np.arange(n)).astype(np.float64)
    g = np.exp(-d * d / (2.0 * sigma * sigma))
    K = np.outer(g, g)                                         # toroidal Gaussian centred at (0, 0)
    Kf = np.fft.fft2(K)

    def bump(i: int) -> np.ndarray:
        yy, xx = divmod(int(i), n)
        return np.roll(K, (yy, xx), axis=(0, 1))

    ones = max(1, N // 10)
    B = np.zeros(N, bool)
    B[rng.choice(N, ones, replace=False)] = True
    E = np.real(np.fft.ifft2(np.fft.fft2(B.reshape(n, n).astype(np.float64)) * Kf)).ravel()
    for _ in range(N):                                         # relax the initial pattern (bounded)
        i = int(np.argmax(np.where(B, E, -np.inf)))
        B[i] = False
        E -= bump(i).ravel()
        j = int(np.argmin(np.where(B, np.inf, E)))
        B[j] = True
        E += bump(j).ravel()
        if j == i:
            break
    rank = np.zeros(N, np.int64)
    B1, E1, c = B.copy(), E.copy(), ones
    while c > 0:                                               # phase 1: remove tightest clusters
        i = int(np.argmax(np.where(B1, E1, -np.inf)))
        B1[i] = False
        E1 -= bump(i).ravel()
        c -= 1
        rank[i] = c
    B2, E2, c = B.copy(), E.copy(), ones
    while c < N:                                               # phases 2+3: fill the largest voids
        j = int(np.argmin(np.where(B2, np.inf, E2)))
        B2[j] = True
        E2 += bump(j).ravel()
        rank[j] = c
        c += 1
    out = ((rank.astype(np.float64) + 0.5) / N).reshape(n, n)
    _BN_CACHE[key] = out
    return out


def _smoothstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class Dither(Effect):
    name = "dither"
    label = "Retro Dither"
    stage = "stylise"
    description = ("Posterise plus ordered dither (Bayer 2/4/8 or blue noise) at a chunky virtual pixel scale; "
                   "a palette mode bands the picture into the colour palette for 1-bit Mac, Game Boy and CGA "
                   "looks, with demoscene colour cycling that kicks on the beat.")
    params = {
        **standard_effect_params(),
        **palette_params("candy", 1.0, 0.02),
        "levels": num(2.0, 32.0, 4.0, "Levels", step=1.0, tier="regional", basic=True),
        "pixel_scale": num(1.0, 8.0, 3.0, "Pixel size", step=0.5, unit="px", tier="regional", basic=True),
        "matrix": num(0.0, 3.0, 1.0, "Pattern (Bayer 2 -> 4 -> 8 -> blue noise)", step=0.05, tier="regional"),
        "palette_mix": num(0.0, 1.0, 0.65, "Colours (source -> palette)", tier="regional"),
        "cycle_speed": num(-2.0, 2.0, 0.12, "Colour cycling", unit="/s", tier="realtime", bind="rate"),
        "contrast": num(0.5, 2.5, 1.25, "Contrast", tier="regional"),
        "luma_lock": num(0.0, 1.0, 0.6, "Palette keeps picture brightness", tier="regional"),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat crunch / cycle kick", step=0.05, tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("dither_k")
        table = np.concatenate([_bayer(2).ravel(), _bayer(4).ravel(), _bayer(8).ravel(),
                                _blue_noise().ravel()]).astype(np.float32)
        assert table.size == T_BN + BN * BN
        self.T = cp.asarray(table)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.drift = 0.0
        self.cycle = 0.0
        self.pal = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = pulse * float(p["beat_kick"])

        # host-integrated colour phases: the beat kicks the cycle rate, never the phase directly
        cs = float(p["cycle_speed"])
        sgn = 1.0 if cs >= 0.0 else -1.0
        self.cycle = math.fmod(self.cycle + (cs * (1.0 + 2.0 * k) + sgn * 0.25 * k) * dt, 10.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 10.0)
        pc = np.asarray(palette_coeffs(p["palette"]), np.float32)
        ap = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.pal = pc if self.pal is None else self.pal + (pc - self.pal) * ap

        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        m = _smoothstep(amount)
        W, H = self.ctx.width, self.ctx.height
        levels = min(max(float(p["levels"]), 2.0), 32.0)
        Lf = math.exp(math.log(255.0) + (math.log(levels - 1.0) - math.log(255.0)) * m)
        Lf = max(1.0, Lf / (1.0 + 1.2 * k * m))                 # beat crunch
        S_t = max(float(p["pixel_scale"]), 1.0) * H / 1080.0
        S = max(1.0, 1.0 + (S_t - 1.0) * m)

        hp = self.host
        hp[0:12] = self.pal
        hp[12] = math.fmod(self.drift + self.cycle + 20.0, 10.0)
        hp[13] = float(p["palette_period"])
        hp[14] = S
        hp[15] = 0.25 * (S - 1.0)
        hp[16] = Lf
        hp[17] = m
        hp[18] = 1.0 + (float(p["contrast"]) - 1.0) * m
        hp[19] = min(max(float(p["palette_mix"]), 0.0), 1.0) * m
        hp[20] = min(max(float(p["matrix"]), 0.0), 3.0)
        hp[21] = 0.5 * W
        hp[22] = 0.5 * H
        hp[23] = min(max(float(p["luma_lock"]), 0.0), 1.0)
        self.P.set(hp)
        launch_2d(self._k, W, H, (src, out, self.T, np.int32(W), np.int32(H), self.P))
