"""Film burn: warm analogue light leaks drift and bloom over a softly defocused, gently jittering blend, burn
white-hot at their cores, and the new picture emerges through them as they fade.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/FilmBurn.glsl
Author: Anastasia Dunbar (FilmBurn)
License: MIT   (file header says "// License: MIT"; repo LICENSE: "MIT License, Copyright (c) 2017-present
                gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: FilmBurn's leak field (13 products of slow sines in x and y plus 13
  wandering blobs, offset +4, /11, tinted (1, .7, .6) and raised per channel to (1, 2 - sin(pi p), 1.3), scaled
  by sin(pi p)) is kept as the core, with these changes: the blob paths, sizes and flicker are host-side seeded
  1D value noise (no fract(sin) hashing), blobs are larger, soft-cored and bloom with the envelope, the wave
  rates vary per leak, leak_density 4-13 fades extra leaks in continuously (variance-normalised so brightness
  holds), a contrast curve gives bold leak shapes with clean image between them, a white-hot core is added and
  the leak is composited with an exponential screen (1 - (1 - base) e^-leak) instead of a clipped add; the colour
  can morph from the warm film ramp to the shared cosine palette with progress drift (leak_tint).  The 50-tap
  random-ring blur is replaced by a quarter-res blended buffer with a separable Gaussian (sigma follows
  blur_amount * sin(pi p)) crossfaded against the sharp blend; the a->b switch is FilmBurn's steep sigmoid,
  optionally pulled forward where the leaks burn hottest (burn_through), so b burns in through them; zoom jitter
  is a zoom-IN wobble plus a small gate weave (no edge smear).  Endpoints are forced: p <= 0 copies a,
  p >= 1 copies b, removing the original's sin(pi) residue.)

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
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

MAX_LEAKS = 13
_STRIDE = 12          # floats per leak: cx, cy (uv), inv radius (height units), blob gain, fx, phx, fy, phy, w, pad
_LAT = 12             # value-noise lattice length per leak path (covers q*6+3 without wrapping)
_TAPS = 12            # Gaussian taps per side (separable, quarter res)

_SRC = HELPERS + PALETTE_CUDA + r"""
// Leak field in [0, 1] (bold, contrasty) at uv (u, v) in [0, 1]^2.
__device__ __forceinline__ float leak_field(float u, float v, const float* __restrict__ L, const int nl,
                                            const float aspect, const float wnorm)
{
    float sw = 0.f, sb = 0.f;
    for (int i = 0; i < nl; ++i) {
        const float* D = L + i * 12;
        const float w = D[8];
        if (w <= 0.f) continue;
        sw += w * __sinf(u * D[4] + D[5]) * __sinf(v * D[6] + D[7]);
        const float dx = (u - D[0]) * aspect, dy = v - D[1];
        const float d = sqrtf(dx * dx + dy * dy) * D[2];
        float core = clamp01(1.f - d);
        core = core * core * (3.f - 2.f * core);
        sb += D[3] * (core + 0.3f * __expf(-0.35f * d * d));
    }
    const float f = 0.36f + sw * wnorm + 0.22f * sb;
    return smooth(0.18f, 0.80f, f);
}

// Quarter-res blended buffer: 4x4 box of a and b, mixed by the (leak-shaped) reveal mask.
extern "C" __global__
void fb_down(const uchar4* __restrict__ a, const uchar4* __restrict__ b, float4* __restrict__ S,
             const float* __restrict__ L, const int nl, const int W, const int H, const int Wq, const int Hq,
             const float aspect, const float wnorm, const float tp, const float btk)
{
    const int X = blockIdx.x * blockDim.x + threadIdx.x, Y = blockIdx.y * blockDim.y + threadIdx.y;
    if (X >= Wq || Y >= Hq) return;
    float4 sa = make_float4(0.f, 0.f, 0.f, 0.f), sb = sa;
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int y = min(4 * Y + j, H - 1);
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            const int x = min(4 * X + i, W - 1);
            const uchar4 pa = a[y * W + x], pb = b[y * W + x];
            sa.x += pa.x; sa.y += pa.y; sa.z += pa.z;
            sb.x += pb.x; sb.y += pb.y; sb.z += pb.z;
        }
    }
    const float u = (4.f * X + 2.f) / W, v = (4.f * Y + 2.f) / H;
    const float fc = leak_field(u, v, L, nl, aspect, wnorm);
    const float m = smooth(-0.3f, 0.3f, tp + btk * (fc - 0.35f));
    S[Y * Wq + X] = make_float4((sa.x + (sb.x - sa.x) * m) * 0.0625f, (sa.y + (sb.y - sa.y) * m) * 0.0625f,
                                (sa.z + (sb.z - sa.z) * m) * 0.0625f, 255.f);
}

// Separable Gaussian on the quarter-res buffer (linear-interpolated taps).
extern "C" __global__
void fb_blur(const float4* __restrict__ src, float4* __restrict__ dst, const int Wq, const int Hq,
             const int horiz, const float step, const float inv2s2)
{
    const int X = blockIdx.x * blockDim.x + threadIdx.x, Y = blockIdx.y * blockDim.y + threadIdx.y;
    if (X >= Wq || Y >= Hq) return;
    const int n = horiz ? Wq : Hq;
    const float c = horiz ? (float)X : (float)Y;
    float4 acc = make_float4(0.f, 0.f, 0.f, 0.f);
    float ws = 0.f;
    for (int k = -TAPS; k <= TAPS; ++k) {
        const float off = k * step;
        const float w = __expf(-off * off * inv2s2);
        const float pos = c + off;
        const float fl = floorf(pos);
        const float t = pos - fl;
        const int i0 = min(max((int)fl, 0), n - 1), i1 = min(max((int)fl + 1, 0), n - 1);
        const float4 s0 = horiz ? src[Y * Wq + i0] : src[i0 * Wq + X];
        const float4 s1 = horiz ? src[Y * Wq + i1] : src[i1 * Wq + X];
        acc.x += w * (s0.x + (s1.x - s0.x) * t);
        acc.y += w * (s0.y + (s1.y - s0.y) * t);
        acc.z += w * (s0.z + (s1.z - s0.z) * t);
        ws += w;
    }
    const float iw = 1.f / ws;
    dst[Y * Wq + X] = make_float4(acc.x * iw, acc.y * iw, acc.z * iw, 255.f);
}

__device__ float4 bilinf4(const float4* __restrict__ img, float fx, float fy, const int w, const int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float4 a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 255.f);
}

extern "C" __global__
void fb_final(const uchar4* __restrict__ a, const uchar4* __restrict__ b, const float4* __restrict__ S,
              uchar4* __restrict__ out, const float* __restrict__ L, const float* __restrict__ P, const int nl,
              const int W, const int H, const int Wq, const int Hq, const float aspect, const float wnorm,
              const float tp, const float btk, const float zs, const float ox, const float oy, const float bmix,
              const float gexp, const float lgain, const float tint, const float period, const float drift)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    // projector wobble: zoom-in + gate weave (the leaks are on the film, so they ride with it)
    const float su = ((x + 0.5f) / W - 0.5f) * zs + 0.5f + ox;
    const float sv = ((y + 0.5f) / H - 0.5f) * zs + 0.5f + oy;
    const float sx = su * W, sy = sv * H;
    const float fc = leak_field(su, sv, L, nl, aspect, wnorm);
    const float m = smooth(-0.3f, 0.3f, tp + btk * (fc - 0.35f));

    float4 base = make_float4(0.f, 0.f, 0.f, 255.f);
    if (bmix < 1.f) {
        if (m <= 0.f) base = bilin(a, sx, sy, W, H);
        else if (m >= 1.f) base = bilin(b, sx, sy, W, H);
        else base = mix4(bilin(a, sx, sy, W, H), bilin(b, sx, sy, W, H), m);
    }
    if (bmix > 0.f) {
        const float4 bl = bilinf4(S, sx * 0.25f, sy * 0.25f, Wq, Hq);
        base = (bmix >= 1.f) ? bl : mix4(base, bl, bmix);
    }

    // leak colour: warm film ramp (FilmBurn's per-channel powers) <-> shared palette, plus a white-hot core
    const float wr = fc;
    const float wg = __powf(fmaxf(0.7f * fc, 1e-6f), gexp);
    const float wb = __powf(fmaxf(0.6f * fc, 1e-6f), 1.6f);
    const float3 pc = pal(fc * period + drift, P);
    const float pk = 1.25f * fc;
    float lr = wr + (pc.x * pk - wr) * tint;
    float lg = wg + (pc.y * pk - wg) * tint;
    float lb = wb + (pc.z * pk - wb) * tint;
    const float hot = 1.2f * smooth(0.7f, 1.0f, fc);
    lr += hot * (1.00f + (0.55f + 0.45f * pc.x - 1.00f) * tint);
    lg += hot * (0.85f + (0.55f + 0.45f * pc.y - 0.85f) * tint);
    lb += hot * (0.60f + (0.55f + 0.45f * pc.z - 0.60f) * tint);
    lr *= lgain; lg *= lgain; lb *= lgain;

    const float k = 1.f / 255.f;
    out[y * W + x] = pack(make_float4(255.f * (1.f - (1.f - base.x * k) * __expf(-lr)),
                                      255.f * (1.f - (1.f - base.y * k) * __expf(-lg)),
                                      255.f * (1.f - (1.f - base.z * k) * __expf(-lb)), 255.f));
}
""".replace("TAPS", str(_TAPS))


def _vnoise(lat: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Smooth 1D value noise per row of ``lat`` (shape (n, K)) at positions ``x`` (shape (n,)), in [0, 1]."""
    k = lat.shape[1]
    x = np.mod(x, k - 1)
    i0 = np.floor(x).astype(np.int64)
    t = x - i0
    t = t * t * (3.0 - 2.0 * t)
    rows = np.arange(lat.shape[0])
    return lat[rows, i0] * (1.0 - t) + lat[rows, np.minimum(i0 + 1, k - 1)] * t


class FilmBurn(Transition):
    name = "film_burn"
    label = "Film burn"
    description = ("Warm analogue light leaks drift and bloom over a softly defocused, jittering blend; the new "
                   "picture burns in through them as they fade.")
    default_duration_s = 2.5
    params = {
        "leak_intensity": num(0.0, 1.5, 1.0, "Leak intensity", tier="regional", basic=True),
        "leak_density": num(4.0, 13.0, 9.0, "Leak density", step=0.1, tier="regional"),
        "leak_tint": num(0.0, 1.0, 0.3, "Leak tint (warm film -> palette)", tier="regional"),
        "palette": enum(list(PALETTES), "ember", "Leak palette", tier="regional"),
        "palette_period": num(0.1, 3.0, 0.8, "Palette period", tier="regional"),
        "blur_amount": num(0.0, 0.05, 0.03, "Defocus", step=0.001, tier="regional"),
        "zoom_jitter": num(0.0, 0.1, 0.04, "Zoom jitter", step=0.001, tier="regional"),
        "burn_through": num(0.0, 1.0, 0.5, "Burn-through reveal", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        w, h = ctx.width, ctx.height
        self._wq, self._hq = (w + 3) // 4, (h + 3) // 4
        mod = raw_module(_SRC)
        self._k_down = mod.get_function("fb_down")
        self._k_blur = mod.get_function("fb_blur")
        self._k_final = mod.get_function("fb_final")
        self._s0 = cp.zeros((self._hq, self._wq, 4), dtype=cp.float32)
        self._s1 = cp.zeros((self._hq, self._wq, 4), dtype=cp.float32)
        self._leaks = cp.zeros(MAX_LEAKS * _STRIDE, dtype=cp.float32)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None

        s = int(self.params.get("seed", -1))
        seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        rng = np.random.default_rng(seed)
        n = MAX_LEAKS
        # leak paths, sizes and flicker (seeded value-noise lattices; FilmBurn's smooth_random, host side)
        self._lat_x = rng.uniform(0.0, 1.0, (n, _LAT))
        self._lat_y = rng.uniform(0.0, 1.0, (n, _LAT))
        self._lat_f = rng.uniform(0.0, 1.0, (n, _LAT))
        self._lat_j = rng.uniform(0.0, 1.0, (3, _LAT * 4))
        self._off = rng.uniform(0.0, 3.0, (n, 3))
        self._rate_x = rng.uniform(0.8, 1.8, n)
        self._rate_y = rng.uniform(0.5, 1.4, n)
        self._radius = rng.uniform(0.06, 0.15, n)                     # height units
        self._gain = rng.uniform(0.7, 1.3, n)
        self._fx = rng.uniform(1.5, 7.5, n)                           # rad per screen width
        self._fy = rng.uniform(1.5, 7.5, n)
        self._ph_x = rng.uniform(-math.pi, math.pi, n)
        self._ph_y = rng.uniform(-math.pi, math.pi, n)
        self._wr_x = 8.0 * rng.uniform(0.6, 1.4, n)                   # FilmBurn's progress*8 / progress*6
        self._wr_y = 6.0 * rng.uniform(0.6, 1.4, n)
        self._drift0 = float(rng.uniform(0.0, 1.0))
        self._buf = np.zeros((n, _STRIDE), np.float32)

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ember")
        if name not in PALETTES:
            name = "ember"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        wq, hq = self._wq, self._hq
        q = float(progress)
        env = math.sin(math.pi * q)
        self._sync_palette()

        # ---- leak table for this progress
        dens = min(max(float(p["leak_density"]), 4.0), float(MAX_LEAKS))
        wts = np.clip(dens - np.arange(MAX_LEAKS), 0.0, 1.0)
        nl = int(math.ceil(dens - 1e-6))
        wn = float(wts.sum()) / MAX_LEAKS
        wnorm = 1.0 / (11.0 * math.sqrt(max(wn, 1e-3)))
        cx = -0.05 + 1.1 * _vnoise(self._lat_x, q * self._rate_x + self._off[:, 0])
        cy = -0.05 + 1.1 * _vnoise(self._lat_y, q * self._rate_y + self._off[:, 1])
        flick = 0.75 + 0.25 * _vnoise(self._lat_f, q * 6.0 + self._off[:, 2])
        bloom = 0.6 + 0.6 * env
        L = self._buf
        L[:, 0] = cx
        L[:, 1] = cy
        L[:, 2] = 1.0 / (self._radius * bloom)
        L[:, 3] = self._gain * flick * wts
        L[:, 4] = self._fx
        L[:, 5] = np.mod(self._ph_x + q * self._wr_x + math.pi, 2 * math.pi) - math.pi
        L[:, 6] = self._fy
        L[:, 7] = np.mod(self._ph_y + q * self._wr_y + math.pi, 2 * math.pi) - math.pi
        L[:, 8] = wts
        self._leaks.set(L.ravel())

        # ---- projector wobble (zoom-in + gate weave), defocus, reveal
        zj = float(p["zoom_jitter"]) * env
        jx = np.array([q * 5.0, q * 19.0, q * 7.0])
        jn = _vnoise(self._lat_j, jx)
        z = zj * (0.7 * jn[0] + 0.3 * jn[1])
        zs = 1.0 / (1.0 + z)
        ox = (jn[2] - 0.5) * 0.6 * z
        oy = (_vnoise(self._lat_j[2:3], np.array([q * 7.0 + 11.0]))[0] - 0.5) * 0.6 * z
        tp = (q - 0.5) * 2.0
        btk = float(p["burn_through"]) * 1.2 * env
        aspect = w / h
        sigma = 0.4 * float(p["blur_amount"]) * env * w               # full-res px
        bmix = min(max(sigma / 6.0, 0.0), 1.0) if sigma >= 0.5 else 0.0

        if bmix > 0.0:
            launch_2d(self._k_down, wq, hq,
                      (a, b, self._s0, self._leaks, np.int32(nl), np.int32(w), np.int32(h), np.int32(wq),
                       np.int32(hq), np.float32(aspect), np.float32(wnorm), np.float32(tp), np.float32(btk)))
            sq = max(sigma * 0.25, 0.3)
            step = np.float32(max(3.0 * sq / _TAPS, 0.25))
            inv2s2 = np.float32(1.0 / (2.0 * sq * sq))
            launch_2d(self._k_blur, wq, hq, (self._s0, self._s1, np.int32(wq), np.int32(hq), np.int32(1), step, inv2s2))
            launch_2d(self._k_blur, wq, hq, (self._s1, self._s0, np.int32(wq), np.int32(hq), np.int32(0), step, inv2s2))

        lgain = 1.6 * env * float(p["leak_intensity"])
        gexp = 2.2 - 0.9 * env
        drift = (self._drift0 + 0.6 * q) % 1.0
        launch_2d(self._k_final, w, h,
                  (a, b, self._s0, out, self._leaks, self._P, np.int32(nl), np.int32(w), np.int32(h),
                   np.int32(wq), np.int32(hq), np.float32(aspect), np.float32(wnorm), np.float32(tp),
                   np.float32(btk), np.float32(zs), np.float32(ox), np.float32(oy), np.float32(bmix),
                   np.float32(gexp), np.float32(lgain), np.float32(p["leak_tint"]),
                   np.float32(p["palette_period"]), np.float32(drift)))
