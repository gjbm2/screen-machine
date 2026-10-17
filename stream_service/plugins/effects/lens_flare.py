"""Lens flare: a screen-space pseudo lens flare generated from the picture's own bright spots.  Whatever is bright
(a light in a scene, a window in an art still) throws a chain of prismatic ghosts through the centre of the frame,
a dispersed halo arc, a slowly rotating starburst and a glint of lens dirt.

Source: https://godotshaders.com/shader/screenspacelensflares/
        http://john-chapman-graphics.blogspot.com/2013/02/pseudo-lens-flare.html
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Lens%20Flare.fs
Author: Unhinged Dev (godotshaders Screenspacelensflares); technique John Chapman ('pseudo lens flare', idea); VIDVOX (ISF Lens Flare)
License: CC0-1.0 + MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Chapman's pipeline is kept -- downsample + threshold, N ghosts sampled
along the vector through the centre with per-channel (R/B) dispersion, a halo sampled at a fixed distance toward the
centre, a blur, then the result modulated by "lens dirt + starburst" and added back.  Changes: quarter-res threshold
and ghost pass with a soft knee; ghosts fade at the frame edge instead of fract() wrapping; aspect-correct halo and
dispersion directions; a fractional ghost count (cross-faded weights) so the count eases; ghost/halo colour via the
shared cosine palette (radial index + per-ghost offset + drift) mixed with the source colour; the starburst is a
procedural angular table (6 + 16 spokes + seamless angular-noise streaks) rotated by a host-integrated phase; the lens
dirt is a procedural bokeh-speck + smudge mask built once per seed; composite is a soft-clipped screen blend.)

amount 0 copies the input; the additive flare is scaled by m = smoothstep(amount), so 0 -> 1 is a smooth fade-in.
pulse kicks the flare gain, breathes the halo radius and reveals the lens dirt (all scaled by m), and lurches the
starburst rotation rate (never a position).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.beat import paced
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

MAXG = 10                    # ghost count ceiling (the knob is fractional)
NB = 4096                    # starburst angular table size
NP = 32
# P layout: 0..11 palette | 12 ghosts | 13 spacing | 14 halo width (pic heights) | 15 dispersion (pic heights)
#           16 tint | 17 period | 18 drift | 19 aspect | 20 glow | 21 ghost gain | 22 halo gain | 23 seed

_SRC = HELPERS + PALETTE_CUDA + r"""
#define MAXG """ + str(MAXG) + r"""
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
__device__ __forceinline__ float bl1(const float* __restrict__ b, float fx, float fy, const int w, const int h) {
    fx = fminf(fmaxf(fx - 0.5f, 0.f), (float)(w - 1));
    fy = fminf(fmaxf(fy - 0.5f, 0.f), (float)(h - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1);
    const float tx = fx - x0, ty = fy - y0;
    return (1.f - ty) * ((1.f - tx) * b[y0 * w + x0] + tx * b[y0 * w + x1])
         + ty * ((1.f - tx) * b[y1 * w + x0] + tx * b[y1 * w + x1]);
}
__device__ __forceinline__ float4 tuv(const float4* __restrict__ T, float u, float v, const int w, const int h) {
    return bl4(T, u * w, v * h, w, h);
}
__device__ __forceinline__ float edgew(float u, float v) {
    return smooth(0.f, 0.08f, u) * smooth(0.f, 0.08f, 1.f - u) * smooth(0.f, 0.08f, v) * smooth(0.f, 0.08f, 1.f - v);
}

// Pass 0 (quarter res): 4-tap downsample + soft-knee bright pass.
extern "C" __global__
void lf_thresh(const uchar4* __restrict__ src, float4* __restrict__ T, const int W, const int H, const int w,
               const int h, const float e0, const float e1)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (float)W / w, sy = (float)H / h;
    const float fx = (x + 0.5f) * sx, fy = (y + 0.5f) * sy, ox = 0.25f * sx, oy = 0.25f * sy;
    const float4 c0 = bilin(src, fx - ox, fy - oy, W, H), c1 = bilin(src, fx + ox, fy - oy, W, H);
    const float4 c2 = bilin(src, fx - ox, fy + oy, W, H), c3 = bilin(src, fx + ox, fy + oy, W, H);
    const float k = 1.f / (4.f * 255.f);
    const float r = (c0.x + c1.x + c2.x + c3.x) * k, g = (c0.y + c1.y + c2.y + c3.y) * k;
    const float b = (c0.z + c1.z + c2.z + c3.z) * k;
    const float l = 0.2126f * r + 0.7152f * g + 0.0722f * b;
    // emulate HDR: highlights well past the knee are boosted (~4x), so small lights throw bold ghosts
    const float xs = clamp01((l - e0) / fmaxf(1.f - e0, 1e-3f));
    const float wgt = smooth(e0, e1, l) * (0.5f + 3.5f * xs * xs);
    T[y * w + x] = make_float4(r * wgt, g * wgt, b * wgt, 0.f);
}

// Pass 1 (quarter res): ghosts through the centre with R/B dispersion, the halo, and a little glow.
extern "C" __global__
void lf_ghost(const float4* __restrict__ T, float4* __restrict__ G, const int w, const int h,
              const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float aspect = P[19];
    const float u = (x + 0.5f) / w, v = (y + 0.5f) / h;
    const float tu = 1.f - u, tv = 1.f - v;                       // flipped through the centre
    const float sp = P[13];
    const float gx = (0.5f - tu) * sp, gy = (0.5f - tv) * sp;     // ghost vector (uv)
    const float ax = (0.5f - tu) * aspect, ay = 0.5f - tv;        // toward the centre, aspect-correct
    const float dl = sqrtf(ax * ax + ay * ay);
    const float rmax = 0.5f * sqrtf(aspect * aspect + 1.f);
    const float inv = 1.f / fmaxf(dl, 1e-4f);
    const float nx = ax * inv / aspect, ny = ay * inv;            // unit direction (picture heights -> uv)
    const float rp = dl / rmax;                                   // 0 at the centre, 1 at the corners
    const float disp = P[15], tint = P[16], per = P[17], drift = P[18], ng = P[12];
    const unsigned int seed = (unsigned int)P[23];

    float ar = 0.f, ag = 0.f, ab = 0.f;
    #pragma unroll 1
    for (int i = 0; i < MAXG; ++i) {
        const float wi = clamp01(ng - (float)i);
        if (wi <= 0.f) break;
        const float ou = tu + gx * i, ov = tv + gy * i;
        const float ex = (ou - 0.5f) * aspect, ey = ov - 0.5f;
        const float fo = 1.f - clamp01(sqrtf(ex * ex + ey * ey) / rmax);
        const float wgt = (0.2f + 0.8f * fo * fo) * edgew(ou, ov) * wi * (0.55f + 0.45f * hash1(i, seed, 7u));
        if (wgt <= 1e-4f) continue;
        const float d = disp * (1.f + 0.2f * i);
        const float r = tuv(T, ou + nx * d, ov + ny * d, w, h).x;
        const float g = tuv(T, ou, ov, w, h).y;
        const float b = tuv(T, ou - nx * d, ov - ny * d, w, h).z;
        const float l = 0.2126f * r + 0.7152f * g + 0.0722f * b;
        const float3 pc = pal(drift + per * (0.5f * rp + 0.137f * i), P);
        ar += wgt * (r + (1.7f * pc.x * l - r) * tint);
        ag += wgt * (g + (1.7f * pc.y * l - g) * tint);
        ab += wgt * (b + (1.7f * pc.z * l - b) * tint);
    }
    ar *= P[21]; ag *= P[21]; ab *= P[21];

    // halo: sample a fixed distance toward the centre (a radially shrunk image -> arcs / a ring), wide dispersion
    {
        const float hw = P[14];
        const float hu = tu + nx * hw, hv = tv + ny * hw;
        const float ex = (hu - 0.5f) * aspect, ey = hv - 0.5f;
        const float fo = 1.f - clamp01(sqrtf(ex * ex + ey * ey) / rmax);
        const float wgt = fo * fo * fo * edgew(hu, hv) * P[22];
        if (wgt > 1e-4f) {
            const float d = disp * 2.2f;
            const float r = tuv(T, hu + nx * d, hv + ny * d, w, h).x;
            const float g = tuv(T, hu, hv, w, h).y;
            const float b = tuv(T, hu - nx * d, hv - ny * d, w, h).z;
            const float l = 0.2126f * r + 0.7152f * g + 0.0722f * b;
            const float3 pc = pal(drift + 0.5f + per * 0.3f * rp, P);
            ar += wgt * (r + (1.7f * pc.x * l - r) * tint);
            ag += wgt * (g + (1.7f * pc.y * l - g) * tint);
            ab += wgt * (b + (1.7f * pc.z * l - b) * tint);
        }
    }
    // glow: the bright pass itself (blooms after the blur)
    const float4 s = T[y * w + x];
    G[y * w + x] = make_float4(ar + P[20] * s.x, ag + P[20] * s.y, ab + P[20] * s.z, 0.f);
}

// Separable 9-tap Gaussian (sigma 3 px, taps every 1.5 px) at quarter res.
extern "C" __global__
void lf_blur(const float4* __restrict__ in, float4* __restrict__ out, const int w, const int h,
             const float sx, const float sy)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float4 acc = make_float4(0.f, 0.f, 0.f, 0.f);
    float ws = 0.f;
    #pragma unroll
    for (int k = -4; k <= 4; ++k) {
        const float wk = __expf(-0.125f * (float)(k * k));
        const float4 c = bl4(in, x + 0.5f + k * sx, y + 0.5f + k * sy, w, h);
        acc.x += wk * c.x; acc.y += wk * c.y; acc.z += wk * c.z; ws += wk;
    }
    const float iw = 1.f / ws;
    out[y * w + x] = make_float4(acc.x * iw, acc.y * iw, acc.z * iw, 0.f);
}

// Starburst angular table: 6 broad + 16 thin spokes + seamless angular-noise streaks.
extern "C" __global__
void lf_burst(float* __restrict__ B, const int n, const float rot, const float ox, const float oy,
              const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n || blockIdx.y * blockDim.y + threadIdx.y > 0) return;
    const float a = ((i + 0.5f) / n) * 6.2831853f - 3.1415927f;
    const float s1 = powf(fabsf(cosf(3.f * a + rot)), 28.f);
    const float s2 = powf(fabsf(cosf(8.f * a - 2.f * rot + 0.7f)), 70.f);
    const float nz = fbm(cosf(a) * 6.f + ox, sinf(a) * 6.f + oy, seed);
    const float st = smooth(0.42f, 0.78f, nz);
    B[i] = 0.9f * s1 + 0.55f * s2 + 0.6f * st;
}

// Lens dirt (built once per seed): soft smudges, fine grime and bokeh-like specks with brighter rims.
extern "C" __global__
void lf_dirt(float* __restrict__ D, const int w, const int h, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float px = (x + 0.5f) / h, py = (y + 0.5f) / h;
    const float smudge = 0.3f * smooth(0.45f, 0.8f, fbm(px * 2.5f + 13.1f, py * 2.5f + 7.7f, seed));
    const float grime = 0.12f * smooth(0.55f, 0.9f, fbm(px * 14.f, py * 14.f, seed + 101u));
    float spk = 0.f;
    #pragma unroll 1
    for (int L = 0; L < 3; ++L) {
        const float cs = L == 0 ? 0.045f : (L == 1 ? 0.09f : 0.17f);
        const float prob = L == 0 ? 0.5f : (L == 1 ? 0.35f : 0.25f);
        const float qx = px / cs, qy = py / cs;
        const int cx = (int)floorf(qx), cy = (int)floorf(qy);
        const unsigned int sl = seed * 7u + 131u * (unsigned int)L;
        for (int j = -1; j <= 1; ++j) {
            for (int i = -1; i <= 1; ++i) {
                const int ix = cx + i, iy = cy + j;
                if (hash1(ix, iy, sl) > prob) continue;
                const float oxx = hash1(ix, iy, sl + 11u), oyy = hash1(ix, iy, sl + 23u);
                const float rad = 0.18f + 0.3f * hash1(ix, iy, sl + 37u);
                const float br = 0.35f + 0.65f * hash1(ix, iy, sl + 51u);
                const float dx = qx - (ix + oxx), dy = qy - (iy + oyy);
                const float d = sqrtf(dx * dx + dy * dy) / rad;
                const float disc = smooth(1.f, 0.8f, d) * (0.6f + 0.4f * smooth(0.55f, 0.92f, d)) * br;
                spk = fmaxf(spk, disc);
            }
        }
    }
    D[y * w + x] = smudge + grime + 0.9f * spk;
}

// Composite at full res: flare x (base + dirt + starburst), soft-clipped, screen-blended over the input.
extern "C" __global__
void lf_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const float4* __restrict__ F,
             const float* __restrict__ D, const float* __restrict__ B, const int W, const int H, const int w,
             const int h, const int dw, const int dh, const int nb, const float gain, const float dirtk,
             const float burstk, const float aspect)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s = src[y * W + x];
    const float4 f = bl4(F, (x + 0.5f) * w / (float)W, (y + 0.5f) * h / (float)H, w, h);
    if (f.x + f.y + f.z < 1e-5f) { out[y * W + x] = make_uchar4(s.x, s.y, s.z, 255); return; }
    const float dirt = bl1(D, (x + 0.5f) * dw / (float)W, (y + 0.5f) * dh / (float)H, dw, dh);
    const float ux = ((x + 0.5f) / W - 0.5f) * aspect, uy = (y + 0.5f) / H - 0.5f;
    const float r = sqrtf(ux * ux + uy * uy);
    const float a = atan2f(uy, ux);
    const float fi = (a * 0.15915494f + 0.5f) * nb - 0.5f;
    int i0 = (int)floorf(fi);
    const float t = fi - i0;
    if (i0 < 0) i0 += nb;
    if (i0 >= nb) i0 -= nb;
    const int i1 = (i0 + 1 == nb) ? 0 : i0 + 1;
    const float sb = (B[i0] * (1.f - t) + B[i1] * t) * (0.3f + 0.7f * __expf(-2.2f * r));
    const float k = gain * (0.45f + dirtk * dirt + burstk * sb);
    const float gx = 1.f - __expf(-fmaxf(f.x, 0.f) * k);
    const float gy = 1.f - __expf(-fmaxf(f.y, 0.f) * k);
    const float gz = 1.f - __expf(-fmaxf(f.z, 0.f) * k);
    const float sr = s.x * (1.f / 255.f), sg = s.y * (1.f / 255.f), sbb = s.z * (1.f / 255.f);
    out[y * W + x] = pack(make_float4(255.f * (sr + (1.f - sr) * gx), 255.f * (sg + (1.f - sg) * gy),
                                      255.f * (sbb + (1.f - sbb) * gz), 255.f));
}
"""


def _wrap(v: float, period: float) -> float:
    return math.fmod(v, period) if abs(v) > period else v


class LensFlare(Effect):
    name = "lens_flare"
    label = "Lens flare"
    stage = "light"
    description = ("Pseudo lens flare from the picture's own bright spots: prismatic ghosts through the centre, a "
                   "dispersed halo, a rotating starburst and lens dirt; the beat kicks the flare and reveals the dirt.")
    params = {
        **standard_effect_params(),
        **palette_params("rainbow", 0.8, 0.02),
        "threshold": num(0.0, 1.0, 0.55, "Bright threshold", tier="regional", basic=True),
        "ghost_count": num(1.0, float(MAXG), 6.0, "Ghosts", step=0.1, tier="regional"),
        "ghost_spacing": num(0.1, 0.7, 0.36, "Ghost spacing", tier="regional"),
        "halo_width": num(0.1, 0.7, 0.42, "Halo radius", tier="regional"),
        "halo": num(0.0, 1.5, 0.75, "Halo strength", tier="regional"),
        "dispersion": num(0.0, 1.0, 0.45, "Dispersion", tier="regional"),
        "tint": num(0.0, 1.0, 0.55, "Tint (source -> palette)", tier="regional"),
        "glow": num(0.0, 1.0, 0.5, "Glow", tier="regional"),
        "dirt": num(0.0, 1.0, 0.5, "Lens dirt", tier="regional"),
        "starburst": num(0.0, 1.0, 0.6, "Starburst", tier="regional"),
        "starburst_speed": paced(num(-0.3, 0.3, 0.02, "Starburst spin", unit="rev/s", step=0.005, tier="realtime",
                                     bind="rate")),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat kick", step=0.05, tier="regional"),
        "seed": num(0, 2**31 - 1, 1, "Seed (dirt pattern)", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kt = mod.get_function("lf_thresh")
        self._kg = mod.get_function("lf_ghost")
        self._kb = mod.get_function("lf_blur")
        self._ks = mod.get_function("lf_burst")
        self._kd = mod.get_function("lf_dirt")
        self._kc = mod.get_function("lf_comp")
        W, H = ctx.width, ctx.height
        self.w, self.h = max(16, W // 4), max(16, H // 4)
        self.dw, self.dh = max(16, W // 2), max(16, H // 2)
        self.T = cp.zeros((self.h, self.w, 4), cp.float32)
        self.G = cp.zeros((self.h, self.w, 4), cp.float32)
        self.G2 = cp.zeros((self.h, self.w, 4), cp.float32)
        self.D = cp.zeros((self.dh, self.dw), cp.float32)
        self.B = cp.zeros(NB, cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.aspect = W / H
        self.seed_param = None
        self.seed = 1
        self.rot = 0.0
        self.streak = 0.0
        self.drift = 0.0
        self.pal = None
        self._build_dirt(int(params.get("seed", 1) or 1))

    def _build_dirt(self, seed_param: int) -> None:
        self.seed_param = seed_param
        self.seed = int(np.random.default_rng(seed_param).integers(1, 1 << 16))
        launch_2d(self._kd, self.dw, self.dh, (self.D, np.int32(self.dw), np.int32(self.dh), np.uint32(self.seed)))

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = pulse * float(p["beat_kick"])
        k1 = min(k, 1.0)

        # host-integrated phases (the beat lurches rates, never positions)
        self.rot = _wrap(self.rot + 2.0 * math.pi * float(p["starburst_speed"]) * (1.0 + 0.8 * k) * dt, 2.0 * math.pi)
        self.streak = _wrap(self.streak + 0.04 * (1.0 + 1.0 * k) * dt, 2.0 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * (1.0 + 0.5 * k) * dt, 10.0)
        pc = np.asarray(palette_coeffs(p["palette"]), np.float32)
        ap = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.pal = pc if self.pal is None else self.pal + (pc - self.pal) * ap

        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = a * a * (3.0 - 2.0 * a)

        sp = int(round(float(p["seed"])))
        if sp != self.seed_param:
            self._build_dirt(sp)

        thr = float(p["threshold"])
        hp = self.host
        hp[0:12] = self.pal
        hp[12] = float(p["ghost_count"])
        hp[13] = float(p["ghost_spacing"])
        hp[14] = float(p["halo_width"]) * (1.0 + 0.2 * k1 * m)             # halo breathes on the beat
        hp[15] = 0.025 * float(p["dispersion"])
        hp[16] = float(p["tint"])
        hp[17] = float(p["palette_period"])
        hp[18] = self.drift
        hp[19] = self.aspect
        hp[20] = float(p["glow"])
        hp[21] = 0.8
        hp[22] = float(p["halo"])
        hp[23] = float(self.seed)
        self.P.set(hp)

        gain = m * 2.0 * (1.0 + 0.6 * k1)
        dirtk = 1.6 * float(p["dirt"]) * (1.0 + 1.5 * k1)                 # lens-dirt reveal on the beat
        burstk = 1.3 * float(p["starburst"])

        W, H = self.ctx.width, self.ctx.height
        w, h = self.w, self.h
        iw, ih = np.int32(w), np.int32(h)
        launch_2d(self._kt, w, h, (src, self.T, np.int32(W), np.int32(H), iw, ih,
                                   np.float32(thr - 0.08), np.float32(thr + 0.14)))
        launch_2d(self._kg, w, h, (self.T, self.G, iw, ih, self.P))
        launch_2d(self._kb, w, h, (self.G, self.G2, iw, ih, np.float32(1.5), np.float32(0.0)))
        launch_2d(self._kb, w, h, (self.G2, self.G, iw, ih, np.float32(0.0), np.float32(1.5)))
        launch_2d(self._ks, NB, 1, (self.B, np.int32(NB), np.float32(self.rot),
                                    np.float32(2.0 * math.cos(self.streak) + 5.0),
                                    np.float32(2.0 * math.sin(self.streak) + 5.0), np.uint32(self.seed)))
        launch_2d(self._kc, W, H, (src, out, self.G, self.D, self.B, np.int32(W), np.int32(H), iw, ih,
                                   np.int32(self.dw), np.int32(self.dh), np.int32(NB), np.float32(gain),
                                   np.float32(dirtk), np.float32(burstk), np.float32(self.aspect)))
