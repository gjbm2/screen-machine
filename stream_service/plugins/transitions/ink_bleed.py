"""Ink bleed: the new picture blooms through several pools of ink soaking into paper.  Revealed areas arrive
first as paper-toned greyscale ink, then the colour floods in after a lag, with a pale fibrous wet edge and
dry-brush texture riding the front.  Lovely on art stills.

Source: https://github.com/nanfengw0w/varyloom/blob/main/src/transitions/ink-reveal.ts
Author: Varyloom contributors (ink-reveal.ts)
License: MIT   (varyloom README, "Accessibility, support, and license": "Licensed under MIT.")
license_class: permissive
Ported from GLSL by smfx (ideas/changes: ink-reveal's revealTime field, the min of a diagonal front and radial
  blooms warped by two fbm layers plus fibre noise and a couple of delaying "holes", is kept, but the four
  hard-coded blooms, the front direction and the holes are host-seeded per transition (1-8 blooms, farthest-point
  spread, staggered start times, random anisotropy/rotation), in aspect-correct height units.  The field, the fine
  noise and the dry-brush noise are precomputed into a half-resolution float4 buffer whenever the seed/layout
  changes and sampled bilinearly per frame.  The greyscale ink stage is tinted by the shared cosine palette
  (index = reveal time x period + a drift integrated over seconds = progress x duration), colour lag is in seconds
  (like the original's colorLag / duration), the always-on vignette is weighted by sin(pi p) and the inner-ink
  darkening and wet rim fade out before p = 1, so both endpoints are pure.  "Cover"-fit and debug modes dropped.)
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

MAX_BLOOMS = 8
_BSTRIDE = 12          # floats per bloom: cx, cy, t0, k, cos, sin, sx, sy, wb, wm, pad, pad
_GLOBALS = 12          # front dir x/y, half-extent m, hole1 x/y/amp/k, hole2 x/y/amp/k, pad

_SRC = HELPERS + PALETTE_CUDA + r"""
// 5-octave fbm with a rotate-and-scale between octaves (as in ink-reveal), ~[0, 0.97]
__device__ float fbm5(float x, float y, unsigned int seed) {
    float v = 0.f, amp = 0.5f;
    #pragma unroll
    for (int o = 0; o < 5; ++o) {
        v += amp * vnoise(x, y, seed + 31u * (unsigned int)o);
        const float nx = 1.62f * x - 1.18f * y + 4.17f;
        const float ny = 1.18f * x + 1.62f * y + 7.31f;
        x = nx; y = ny; amp *= 0.5f;
    }
    return v;
}

// Precompute (half resolution): x = reveal time, y = fine noise, z = dry-brush noise.
extern "C" __global__
void ink_field(float4* __restrict__ F, const int FW, const int FH, const float aspect, const float fs,
               const float* __restrict__ blooms, const int nb, const float* __restrict__ G,
               const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= FW || y >= FH) return;
    const float X = (x + 0.5f) / FW * aspect, Y = (y + 0.5f) / FH;        // height units, y down

    const float broad = fbm5(X * 1.25f * fs + 2.6f, Y * 2.8f * fs + 6.4f, seed);
    const float medium = fbm5(X * 3.2f * fs + 8.1f, Y * 7.2f * fs + 1.7f, seed + 101u);
    const float fibers = vnoise(X * 11.f * fs + 5.f, Y * 27.f * fs + 9.f, seed + 202u);
    const float bw = (broad - 0.5f) * 0.24f, mw = (medium - 0.5f) * 0.12f;

    // the diagonal front: 0 at the leading corner, 1 at the far one
    const float s = ((X - 0.5f * aspect) * G[0] + (Y - 0.5f) * G[1] + G[2]) / (2.f * G[2]);
    float f = 0.14f + 0.80f * s + bw + mw * 0.56f;

    for (int i = 0; i < nb; ++i) {
        const float* B = blooms + i * 12;
        const float ox = X - B[0], oy = Y - B[1];
        const float rx = (ox * B[4] + oy * B[5]) * B[6];
        const float ry = (-ox * B[5] + oy * B[4]) * B[7];
        const float g = B[2] + sqrtf(rx * rx + ry * ry) * B[3] + bw * B[8] + mw * B[9];
        f = fminf(f, g);
    }
    f += (fibers - 0.5f) * 0.026f;
    // late-soaking holes: islands of the old picture that linger
    {
        const float dx = X - G[3], dy = Y - G[4];
        f += G[5] * __expf(-G[6] * (dx * dx + dy * dy));
        const float ex = X - G[7], ey = Y - G[8];
        f += G[9] * __expf(-G[10] * (ex * ex + ey * ey));
    }
    f = fminf(fmaxf(f, 0.145f), 0.955f);

    const float fine = fbm5(X * 5.6f * fs + 13.f, Y * 13.f * fs + 3.f, seed + 303u);
    const float dry = vnoise(X * 20.f * fs, Y * 52.f * fs, seed + 404u);
    F[y * FW + x] = make_float4(f, fine, dry, 0.f);
}

__device__ __forceinline__ float4 ldF(const float4* __restrict__ F, int x, int y, int FW, int FH) {
    x = min(max(x, 0), FW - 1); y = min(max(y, 0), FH - 1);
    return F[y * FW + x];
}

extern "C" __global__
void ink_bleed(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
               const float4* __restrict__ F, const int FW, const int FH, const int W, const int H,
               const float p, const float lag, const float edge, const float tint,
               const float* __restrict__ P, const float period, const float drift,
               const float endk, const float vig_k)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;

    // bilinear lookup of the precomputed half-res field
    float fx = (x + 0.5f) * FW / (float)W - 0.5f, fy = (y + 0.5f) * FH / (float)H - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const float4 f00 = ldF(F, x0, y0, FW, FH), f10 = ldF(F, x0 + 1, y0, FW, FH);
    const float4 f01 = ldF(F, x0, y0 + 1, FW, FH), f11 = ldF(F, x0 + 1, y0 + 1, FW, FH);
    const float u = 1.f - tx, v = 1.f - ty;
    const float field = v * (u * f00.x + tx * f10.x) + ty * (u * f01.x + tx * f11.x);
    const float fine = v * (u * f00.y + tx * f10.y) + ty * (u * f01.y + tx * f11.y);
    const float dry = v * (u * f00.z + tx * f10.z) + ty * (u * f01.z + tx * f11.z);

    const float feather = 0.018f + (0.045f - 0.018f) * clamp01(fine);
    const float alpha = smooth(field - feather, field + feather, p);
    const float4 A = ld(a, x, y, W, H);
    if (field - p >= 0.08f && vig_k <= 0.f) { out[i] = a[i]; return; }   // well ahead of the wet edge

    const float4 Bc = ld(b, x, y, W, H);
    float chroma = smooth(field - feather * 0.72f, field + feather * 1.08f, p - lag);
    chroma = fmaxf(chroma, smooth(0.92f, 1.f, p));

    // paper-toned greyscale ink, tinted by the palette (normalised so it tones rather than darkens)
    float3 pc = pal(field * period + drift, P);
    const float pm = fmaxf(fmaxf(pc.x, fmaxf(pc.y, pc.z)), 0.25f);
    pc.x /= pm; pc.y /= pm; pc.z /= pm;
    const float tr = 0.96f + (pc.x - 0.96f) * tint, tg = 0.975f + (pc.y - 0.975f) * tint,
                tb = 0.97f + (pc.z - 0.97f) * tint;
    const float L = luma(Bc) * 255.f;
    float gr = L * tr, gg = L * tg, gb = L * tb;
    gr += (Bc.x - gr) * 0.10f; gg += (Bc.y - gg) * 0.10f; gb += (Bc.z - gb) * 0.10f;
    gr = 119.85f + (gr - 119.85f) * 0.90f; gg = 119.85f + (gg - 119.85f) * 0.90f;
    gb = 119.85f + (gb - 119.85f) * 0.90f;
    const float rr = gr + (Bc.x - gr) * chroma, rg = gg + (Bc.y - gg) * chroma, rb = gb + (Bc.z - gb) * chroma;
    float cr = A.x + (rr - A.x) * alpha, cg = A.y + (rg - A.y) * alpha, cb = A.z + (rb - A.z) * alpha;

    // pale fibrous wet edge on the front, with dry-brush breakup
    float rim = 1.f - smooth(0.008f, 0.070f, fabsf(field - p));
    rim *= 0.56f + 0.44f * smooth(0.22f, 0.82f, fine);
    rim *= smooth(0.08f, 0.18f, p) * endk;
    rim *= 0.52f + 0.48f * smooth(0.26f, 0.76f, dry);
    const float fn = clamp01(fine);
    float wr = (0.76f + 0.24f * fn) * 255.f, wg = (0.72f + 0.24f * fn) * 255.f, wb = (0.64f + 0.20f * fn) * 255.f;
    const float wt = 0.35f * tint;                                       // the wet edge picks up a little tint
    wr *= 1.f + (tr - 1.f) * wt; wg *= 1.f + (tg - 1.f) * wt; wb *= 1.f + (tb - 1.f) * wt;
    const float rk = fminf(rim * 0.58f * edge, 0.95f);
    cr += (wr - cr) * rk; cg += (wg - cg) * rk; cb += (wb - cb) * rk;

    // the freshly soaked ink just behind the front is a touch darker
    const float inner = (1.f - smooth(0.f, 0.10f, p - field)) * alpha * endk;
    float k = 1.f - inner * 0.08f * edge;

    // the paper vignette (weighted by sin(pi p), so the endpoints stay pure)
    if (vig_k > 0.f) {
        const float cx = (x + 0.5f) / W - 0.5f, cy = (y + 0.5f) / H - 0.5f;
        const float vg = smooth(0.92f, 0.30f, cx * cx + cy * cy);
        k *= 1.f - vig_k * (1.f - vg);
    }
    out[i] = pack(make_float4(cr * k, cg * k, cb * k, 255.f));
}
"""


class InkBleed(Transition):
    name = "ink_bleed"
    label = "Ink bleed"
    description = ("The new picture blooms through pools of ink soaking into paper: greyscale ink first, then the "
                   "colour floods in behind a pale fibrous wet edge.")
    default_duration_s = 4.0
    params = {
        "bloom_count": num(1, MAX_BLOOMS, 4, "Ink pools", tier="regional", integer=True, basic=True),
        "edge_strength": num(0.0, 1.5, 0.75, "Wet edge", tier="regional"),
        "colour_lag": num(0.0, 2.0, 0.6, "Colour lag", step=0.05, unit="s", tier="regional"),
        "field_scale": num(0.4, 2.5, 1.0, "Bleed grain", step=0.05, unit="×", tier="regional"),
        "paper_tint": num(0.0, 1.0, 0.45, "Ink tint", tier="regional", basic=True),
        "vignette": num(0.0, 0.5, 0.17, "Paper vignette", tier="regional"),
        "palette": enum(PALETTES, "ember", "Ink palette", tier="regional"),
        "palette_period": num(0.05, 4.0, 0.8, "Palette period", tier="regional"),
        "drift_speed": num(-1.0, 1.0, 0.06, "Colour drift", unit="/s", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k_field = mod.get_function("ink_field")
        self._k = mod.get_function("ink_bleed")
        self._fw = (ctx.width + 1) // 2
        self._fh = (ctx.height + 1) // 2
        self._F = cp.zeros((self._fh, self._fw, 4), dtype=cp.float32)
        self._blooms = cp.zeros(MAX_BLOOMS * _BSTRIDE, dtype=cp.float32)
        self._G = cp.zeros(_GLOBALS, dtype=cp.float32)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._key = None
        self._nb = 0
        self._ensure_field()

    # ------------------------------------------------------------------ host-side layout + field (cached)
    def _ensure_field(self) -> None:
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        n = int(min(max(int(round(float(p.get("bloom_count", 4)))), 1), MAX_BLOOMS))
        fs = float(min(max(float(p.get("field_scale", 1.0)), 0.2), 4.0))
        key = (w, h, n, fs, self._seed)
        if key == self._key:
            return
        aspect = w / h
        rng = np.random.default_rng(self._seed)

        g = np.zeros(_GLOBALS, np.float32)
        ang = rng.uniform(0.0, 2.0 * math.pi)
        dx, dy = math.cos(ang), math.sin(ang)
        g[0], g[1] = dx, dy
        g[2] = 0.5 * (aspect * abs(dx) + abs(dy))                   # max of the projection over the corners
        for j in range(2):                                           # two lingering islands
            g[3 + 4 * j] = rng.uniform(0.2, 0.8) * aspect
            g[4 + 4 * j] = rng.uniform(0.2, 0.8)
            g[5 + 4 * j] = rng.uniform(0.08, 0.13)
            g[6 + 4 * j] = rng.uniform(14.0, 26.0)

        cand = np.stack([rng.uniform(0.06 * aspect, 0.94 * aspect, 256), rng.uniform(0.08, 0.92, 256)], 1)
        picks = [int(rng.integers(0, len(cand)))]
        dmin = np.hypot(cand[:, 0] - cand[picks[0], 0], cand[:, 1] - cand[picks[0], 1])
        for _ in range(1, n):                                        # farthest-point spread (a little jittered)
            score = dmin * rng.uniform(0.8, 1.0, len(cand))
            j = int(np.argmax(score))
            picks.append(j)
            dmin = np.minimum(dmin, np.hypot(cand[:, 0] - cand[j, 0], cand[:, 1] - cand[j, 1]))
        bl = np.zeros((MAX_BLOOMS, _BSTRIDE), np.float32)
        for i, j in enumerate(picks):
            t0 = 0.15 if i == 0 else 0.15 + 0.27 * i / max(n - 1, 1) + rng.uniform(-0.03, 0.03)
            th = rng.uniform(0.0, math.pi)
            r = rng.uniform(0.72, 1.35)
            bl[i, :10] = (cand[j, 0], cand[j, 1], t0, rng.uniform(0.62, 0.92), math.cos(th), math.sin(th),
                          r, 1.0 / r, rng.uniform(0.25, 0.7), rng.uniform(0.4, 0.8))

        self._G.set(g)
        self._blooms.set(bl.ravel())
        launch_2d(self._k_field, self._fw, self._fh,
                  (self._F, np.int32(self._fw), np.int32(self._fh), np.float32(aspect), np.float32(fs),
                   self._blooms, np.int32(n), self._G, np.uint32(self._seed & 0xFFFFFFFF)))
        self._nb = n
        self._key = key

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 1e-4:
            cp.copyto(out, a)
            return
        if progress >= 0.9999:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        self._ensure_field()
        name = str(p.get("palette", "ember"))
        if name not in PALETTES:
            name = "ember"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

        dur = max(self.duration_s, 0.1)
        lag = min(max(float(p["colour_lag"]), 0.0) / dur, 0.5)
        t_s = progress * dur
        drift = (float(p["drift_speed"]) * t_s) % 1.0
        endk = 1.0 - min(max((progress - 0.90) / 0.10, 0.0), 1.0)
        endk = endk * endk * (3.0 - 2.0 * endk) if endk < 1.0 else 1.0
        vig_k = float(p["vignette"]) * math.sin(math.pi * progress)
        launch_2d(self._k, w, h,
                  (a, b, out, self._F, np.int32(self._fw), np.int32(self._fh), np.int32(w), np.int32(h),
                   np.float32(progress), np.float32(lag), np.float32(p["edge_strength"]),
                   np.float32(p["paper_tint"]), self._P, np.float32(p["palette_period"]), np.float32(drift),
                   np.float32(endk), np.float32(vig_k)))
