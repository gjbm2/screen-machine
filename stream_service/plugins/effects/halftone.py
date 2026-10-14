"""Halftone: print and pop-art halftone.  Rotated per-channel screens of dots, diamonds or squares are sized by the
picture's intensity, printed in CMYK on tinted paper, as additive RGB dots on dark paper, or as mono palette ink on
palette paper.  The dots swell on the beat.

Source: https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/HalftoneShader.js
        https://github.com/pmndrs/postprocessing/blob/main/src/effects/DotScreenEffect.js
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/CMYK%20Halftone.fs
Author: three.js authors (HalftoneShader); Raoul van Rueschen (pmndrs DotScreenEffect); zoidberg (ISF CMYK Halftone)
License: MIT (three.js, "The MIT License" in the repository LICENSE; Vidvox ISF-Files, MIT) /
         Zlib (pmndrs/postprocessing, "zlib License" in the repository LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the per-channel rotated screens (C 15, M 75, Y 0, K 45 degrees), the
RGB -> CMYK split and the sample-the-cell-centre-then-draw-a-dot-sized-by-tone idea are kept from the sources.
Rebuilt for a 4K realtime path: (1) a quarter-res box downsample of src, (2) a small per-screen cell lattice that
box-filters each cell from it (9 taps, so cells never alias however large), (3) one 4K pass that unions the 2x2
nearest dots of every screen (so swollen dots overlap their neighbours without seams).  The dot is an area-correct
morph circle -> diamond -> square (tone = ink area, reaching full cover at tone 1).  colour_mode morphs mono ->
RGB -> CMYK continuously; paper and mono ink come from the shared cosine palette with drift.)

amount 0 copies the input.  amount fades the halftone in (mix) and raises the dot contrast (from continuous tone
to crisp dots), so 0 -> 1 is a smooth morph.  pulse swells every dot (x(1 + 0.4 dot_kick pulse)) and lurches the
screen rotation and colour drift rates; nothing ever jumps.
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
# P layout: 0..11 palette | 12 drift | 13 ink_paper | 14 period | 15 S cell px | 16 shape | 17 A (dot area const)
#           18 rc (full-cover radius) | 19 wm | 20 wr | 21 wc | 22 contrast | 23 mix | 24 swell
#           25..28 cos | 29..32 sin | 33..40 per-screen offsets (ox, oy) | 41 cx | 42 cy | 43 H | 44 AA gain
BASE_ANGLES = (15.0, 75.0, 0.0, 45.0)                 # C/R, M/G, Y/B, K/mono
OFFSETS = ((0.13, 0.31), (0.57, 0.11), (0.29, 0.73), (0.0, 0.0))

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
__device__ __forceinline__ float lum3(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }
__device__ __forceinline__ float3 lift(float3 p, float target) {
    const float l = lum3(p);
    const float s = fmaxf(0.f, (target - l) / fmaxf(1.f - l, 1e-4f));
    return make_float3(p.x + (1.f - p.x) * s, p.y + (1.f - p.y) * s, p.z + (1.f - p.z) * s);
}
// Dot coverage: tone (ink area 0..1) -> radius in the morphing metric (area-correct, full cover at tone 1).
__device__ __forceinline__ float dotcov(float tone, float d, const float* __restrict__ P) {
    tone = clamp01(tone);
    const float A = P[17], rc = P[18], S = P[15];
    const float r0 = sqrtf(tone / A);
    float r = r0 + (rc - sqrtf(1.f / A)) * tone * tone * tone;
    r = fminf(r * (1.f + P[24]), 0.99f);
    return clamp01((r - d) * S * P[44] + 0.5f) * clamp01(r * S * 2.f);
}
__device__ __forceinline__ float cmyk_tone(float4 v, int c) {
    const float k = 1.f - fmaxf(v.x, fmaxf(v.y, v.z));
    if (c == 3) return k;
    const float ch = c == 0 ? v.x : (c == 1 ? v.y : v.z);
    return clamp01((1.f - ch - k) / fmaxf(1.f - k, 1e-3f));
}

// Quarter-res box downsample (each texel = mean of a 4x4 block), 0..1.
extern "C" __global__
void ht_down(const uchar4* __restrict__ src, float4* __restrict__ Q, const int W, const int H, const int w4, const int h4)
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

// Per-screen cell lattice: the box-filtered colour of every cell (rgb + luma).  Launched over (n, 4n), n = 2nh+1.
extern "C" __global__
void ht_lattice(const float4* __restrict__ Q, float4* __restrict__ L, const int w4, const int h4,
                const int nh, const int NL, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const int n = 2 * nh + 1;
    if (x >= n || y >= 4 * n) return;
    const int c = y / n, jy = y - c * n;
    const float S = P[15], cs = P[25 + c], sn = P[29 + c];
    const float gi = (float)(x - nh) + P[33 + 2 * c], gj = (float)(jy - nh) + P[34 + 2 * c];
    float r = 0.f, g = 0.f, b = 0.f;
    #pragma unroll
    for (int bb = -1; bb <= 1; ++bb) {
        #pragma unroll
        for (int aa = -1; aa <= 1; ++aa) {
            const float gx = gi + aa * (1.f / 3.f), gy = gj + bb * (1.f / 3.f);
            const float px = P[41] + S * (cs * gx - sn * gy), py = P[42] + S * (sn * gx + cs * gy);
            const float4 v = bl4(Q, px * 0.25f, py * 0.25f, w4, h4);
            r += v.x; g += v.y; b += v.z;
        }
    }
    r *= (1.f / 9.f); g *= (1.f / 9.f); b *= (1.f / 9.f);
    const int N2 = (NL - 1) / 2;
    const int li = x - nh + N2, lj = jy - nh + N2;
    L[(c * NL + lj) * NL + li] = make_float4(r, g, b, 0.2126f * r + 0.7152f * g + 0.0722f * b);
}

// 4K composite: union of the 2x2 nearest dots on each active screen, inked per colour mode, mixed over src.
extern "C" __global__
void ht_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const float4* __restrict__ L,
             const int W, const int H, const int NL, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s8 = src[y * W + x];
    const float4 sv = make_float4(s8.x * (1.f / 255.f), s8.y * (1.f / 255.f), s8.z * (1.f / 255.f), 0.f);
    const float slum = 0.2126f * sv.x + 0.7152f * sv.y + 0.0722f * sv.z;
    const float px = x + 0.5f, py = y + 0.5f, dx = px - P[41], dy = py - P[42];
    const float invS = 1.f / P[15];
    const float sh = P[16];
    const float wm = P[19], wr = P[20], wc = P[21];
    const int N2 = (NL - 1) / 2;

    float covM = 0.f, lumM = slum, bestM = -1.f;
    float covR[3] = {0.f, 0.f, 0.f};
    float covC[4] = {0.f, 0.f, 0.f, 0.f};
    #pragma unroll
    for (int c = 0; c < 4; ++c) {
        const bool need = (c == 3) ? (wm > 0.f || wc > 0.f) : (wr > 0.f || wc > 0.f);
        if (!need) continue;
        const float cs = P[25 + c], sn = P[29 + c];
        const float gx = (cs * dx + sn * dy) * invS - P[33 + 2 * c];
        const float gy = (-sn * dx + cs * dy) * invS - P[34 + 2 * c];
        const int i0 = (int)floorf(gx), j0 = (int)floorf(gy);
        #pragma unroll
        for (int q = 0; q < 4; ++q) {
            const int ii = i0 + (q & 1), jj = j0 + (q >> 1);
            const float fx = gx - (float)ii, fy = gy - (float)jj;
            const float ax = fabsf(fx), ay = fabsf(fy);
            const float dc = sqrtf(fx * fx + fy * fy), dd = ax + ay, ds = fmaxf(ax, ay);
            const float d = sh < 0.5f ? dc + (dd - dc) * (2.f * sh) : dd + (ds - dd) * (2.f * sh - 1.f);
            const int li = min(max(ii + N2, 0), NL - 1), lj = min(max(jj + N2, 0), NL - 1);
            const float4 v = L[(c * NL + lj) * NL + li];
            if (c == 3 && wm > 0.f) {
                const float cv = dotcov(1.f - v.w, d, P);
                if (cv > bestM) { bestM = cv; lumM = v.w; }
                covM = fmaxf(covM, cv);
            }
            if (c < 3 && wr > 0.f) {
                const float t = c == 0 ? v.x : (c == 1 ? v.y : v.z);
                covR[c] = fmaxf(covR[c], dotcov(t, d, P));
            }
            if (wc > 0.f) covC[c] = fmaxf(covC[c], dotcov(cmyk_tone(v, c), d, P));
        }
    }

    // contrast: continuous tone (per pixel) -> crisp dots
    const float ct = P[22];
    const float drift = P[12], inkp = P[13], period = P[14];
    float3 pp = pal(drift + inkp + 0.5f + period * 0.2f * (py / P[43] - 0.5f), P);
    float3 col = make_float3(0.f, 0.f, 0.f);
    if (wm > 0.f) {
        const float cv = (1.f - slum) + (covM - (1.f - slum)) * ct;
        float3 ink = pal(drift + inkp + period * 0.35f * (lumM - 0.5f), P);
        const float il = lum3(ink);
        const float ik = fminf(1.f, 0.36f / fmaxf(il, 1e-3f));
        ink = make_float3(ink.x * ik, ink.y * ik, ink.z * ik);
        const float3 pm = lift(pp, 0.84f);
        col.x += wm * (pm.x + (ink.x - pm.x) * cv);
        col.y += wm * (pm.y + (ink.y - pm.y) * cv);
        col.z += wm * (pm.z + (ink.z - pm.z) * cv);
    }
    if (wr > 0.f) {
        const float pl = lum3(pp);
        const float dk = fminf(1.f, 0.07f / fmaxf(pl, 1e-3f));
        const float3 pd = make_float3(pp.x * dk, pp.y * dk, pp.z * dk);
        const float r = sv.x + (covR[0] - sv.x) * ct;
        const float g = sv.y + (covR[1] - sv.y) * ct;
        const float b = sv.z + (covR[2] - sv.z) * ct;
        col.x += wr * (pd.x + (1.f - pd.x) * r);
        col.y += wr * (pd.y + (1.f - pd.y) * g);
        col.z += wr * (pd.z + (1.f - pd.z) * b);
    }
    if (wc > 0.f) {
        const float3 pw = lift(pp, 0.93f);
        float cc[4];
        #pragma unroll
        for (int c = 0; c < 4; ++c) { const float t = cmyk_tone(sv, c); cc[c] = t + (covC[c] - t) * ct; }
        // process inks multiplied onto the paper (C, M, Y, K)
        const float r = pw.x * (1.f - cc[0]) * (1.f - 0.07f * cc[1]) * (1.f - 0.86f * cc[3]);
        const float g = pw.y * (1.f - 0.32f * cc[0]) * (1.f - cc[1]) * (1.f - 0.05f * cc[2]) * (1.f - 0.88f * cc[3]);
        const float b = pw.z * (1.f - 0.06f * cc[0]) * (1.f - 0.45f * cc[1]) * (1.f - cc[2]) * (1.f - 0.87f * cc[3]);
        col.x += wc * r; col.y += wc * g; col.z += wc * b;
    }
    const float m = P[23];
    out[y * W + x] = pack(make_float4(255.f * (sv.x + (col.x - sv.x) * m), 255.f * (sv.y + (col.y - sv.y) * m),
                                      255.f * (sv.z + (col.z - sv.z) * m), 255.f));
}
"""


class Halftone(Effect):
    name = "halftone"
    label = "Halftone"
    stage = "stylise"
    description = ("Print / pop-art halftone: rotated screens of dots, diamonds or squares sized by the picture, in "
                   "CMYK on tinted paper, RGB on dark, or palette ink on palette paper; dots swell on the beat.")
    params = {
        **standard_effect_params(),
        **palette_params("ember", 0.6, 0.02),
        "cell_size": num(4.0, 48.0, 14.0, "Cell size", step=0.5, unit="px", tier="regional", basic=True),
        "grid_angle_deg": num(0.0, 90.0, 0.0, "Screen angle", step=0.5, unit="deg", tier="regional"),
        "rotate_speed": num(-20.0, 20.0, 1.5, "Screen rotation speed", step=0.1, unit="deg/s", tier="regional"),
        "shape": num(0.0, 1.0, 0.0, "Dot shape (dot -> diamond -> square)", tier="regional"),
        "colour_mode": num(0.0, 1.0, 1.0, "Colour (mono -> RGB -> CMYK)", tier="regional"),
        "ink_paper": num(0.0, 1.0, 0.0, "Ink / paper palette stop", tier="regional"),
        "dot_kick": num(0.0, 2.0, 1.0, "Beat dot swell", step=0.05, tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kd = mod.get_function("ht_down")
        self._kl = mod.get_function("ht_lattice")
        self._kc = mod.get_function("ht_comp")
        W, H = ctx.width, ctx.height
        self.w4, self.h4 = max(1, (W + 3) // 4), max(1, (H + 3) // 4)
        self.Q = cp.zeros((self.h4, self.w4, 4), cp.float32)
        self.diag = math.hypot(W, H)
        s_min = 4.0 * H / 1080.0
        self.nh_max = int(math.ceil(0.5 * self.diag / s_min)) + 4
        self.NL = 2 * self.nh_max + 1
        self.L = cp.zeros((4, self.NL, self.NL, 4), cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.rot = 0.0
        self.drift = 0.0
        self.pal = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = pulse * float(p["dot_kick"])                   # beat strength 0..2

        # host-integrated phases: the beat lurches the rates, never the positions
        self.rot = math.fmod(self.rot + float(p["rotate_speed"]) * (1.0 + 1.2 * min(k, 1.5)) * dt, 360.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * (1.0 + 1.0 * min(k, 1.5)) * dt, 10.0)
        pc = np.asarray(palette_coeffs(p["palette"]), np.float32)
        ap = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.pal = pc if self.pal is None else self.pal + (pc - self.pal) * ap

        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        W, H = self.ctx.width, self.ctx.height
        S = max(float(p["cell_size"]), 4.0) * H / 1080.0
        sh = min(max(float(p["shape"]), 0.0), 1.0)
        if sh < 0.5:
            t = 2.0 * sh
            A, rc, g = math.pi + (2.0 - math.pi) * t, 0.7071 + (1.0 - 0.7071) * t, 1.0 + (0.7071 - 1.0) * t
        else:
            t = 2.0 * sh - 1.0
            A, rc, g = 2.0 + 2.0 * t, 1.0 - 0.5 * t, 0.7071 + (1.0 - 0.7071) * t
        m = min(max(float(p["colour_mode"]), 0.0), 1.0)
        if m < 0.5:
            wm, wr, wc = 1.0 - 2.0 * m, 2.0 * m, 0.0
        else:
            wm, wr, wc = 0.0, 2.0 - 2.0 * m, 2.0 * m - 1.0

        hp = self.host
        hp[0:12] = self.pal
        hp[12] = self.drift
        hp[13] = float(p["ink_paper"])
        hp[14] = float(p["palette_period"])
        hp[15] = S
        hp[16] = sh
        hp[17] = A
        hp[18] = rc
        hp[19], hp[20], hp[21] = wm, wr, wc
        hp[22] = min(1.0, a * 1.6) ** 0.7                    # dot contrast
        hp[23] = min(1.0, a * 2.5)                           # mix over src
        hp[24] = min(0.8, 0.4 * k)                           # dot swell on the beat
        base = float(p["grid_angle_deg"]) + self.rot
        for c in range(4):
            th = math.radians(base + BASE_ANGLES[c])
            hp[25 + c] = math.cos(th)
            hp[29 + c] = math.sin(th)
            hp[33 + 2 * c] = OFFSETS[c][0]
            hp[34 + 2 * c] = OFFSETS[c][1]
        hp[41] = 0.5 * W
        hp[42] = 0.5 * H
        hp[43] = float(H)
        hp[44] = 0.9 * g
        self.P.set(hp)

        nh = min(int(math.ceil(0.5 * self.diag / S)) + 3, self.nh_max)
        n = 2 * nh + 1
        iW, iH, w4, h4 = np.int32(W), np.int32(H), np.int32(self.w4), np.int32(self.h4)
        launch_2d(self._kd, self.w4, self.h4, (src, self.Q, iW, iH, w4, h4))
        launch_2d(self._kl, n, 4 * n, (self.Q, self.L, w4, h4, np.int32(nh), np.int32(self.NL), self.P))
        launch_2d(self._kc, W, H, (src, out, self.L, iW, iH, np.int32(self.NL), self.P))
