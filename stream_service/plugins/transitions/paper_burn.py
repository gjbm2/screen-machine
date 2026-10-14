"""Paper burn: the old picture burns away like paper.  A ragged burn front spreads from an origin (faster
upward, as flame climbs), led by a brown scorch, a widening black char band flecked with smouldering specks, a
flickering ember band and a hot yellow-white core line; rising sparks and fire-lit smoke drift over the unburnt
paper, and the new picture lies behind in the soft shadow of the burning edge.

Source: https://github.com/nanfengw0w/varyloom/blob/main/src/transitions/burn-through.ts
        https://www.npmjs.com/package/varyloom
        https://godotshaders.com/shader/burn-dissolve/   (burn-band colour ramp)
Author: Varyloom contributors (burn-through.ts); burn-band colours after KingToot (godotshaders burn-dissolve)
License: MIT + CC0-1.0   (varyloom README: "Licensed under MIT."; godotshaders burn-dissolve page: CC0)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: burn-through's radial front (distance to the origin + a three-scale
  roughness of broad fbm, detail fbm and paper fibres - radius), its char band, ember band and hot core line,
  its rising smoke fbm over the unburnt side and its scrolling spark cells are kept as the core, with these
  changes: the three static noise fields (plus an ember grain) are computed once per seed in setup into a
  half-res float4 buffer and range-normalised, so the per-frame kernel only does lookups and the composite;
  the fibres are two crossed anisotropic noises; the front climbs faster upward; the radius runs from just
  off-screen to just past the farthest corner so the burn fills the whole transition; the bands are made
  asymmetric (brown scorch -> black char that widens with progress -> ember -> core on the paper side, a soft
  cast shadow on the new picture) and the char carries smouldering specks; the ember/core/spark/glow colours
  come from the shared cosine palette (ember_hue index, drifting with progress) and are added with an
  exponential screen instead of a clipped add; smoke is a precomputed periodic fbm tile sampled at two scales,
  domain-warped by the broad field, lit by the fire near the front and reaching further above the front;
  sparks are two depth layers of host-timed rising, swaying, twinkling streaks (time crossfades between
  hashed seeds, no per-frame white noise), gated to a band that reaches further upward; ember flicker is a
  time-crossfaded value noise; uTime is progress * duration_s; every overlay is weighted by an envelope of
  sin(pi p), and p <= 1e-4 copies a, p >= 0.9999 copies b.)

MIT notice (varyloom): Permission is hereby granted, free of charge, to any person obtaining a copy of this
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

_TILE = 256           # periodic smoke tile (power of two)
_NU = 32              # per-frame uniform floats
_UPK = 0.8            # the front climbs 1/0.8 faster upward
# roughness weights of the three normalised fields (broad fbm, detail fbm, fibres); their sum bounds |rough|/2
_RW = (0.22, 0.07, 0.02)

_SRC = HELPERS + PALETTE_CUDA + r"""
#define TILE_N TILEN

__device__ __forceinline__ float3 f3(float a, float b, float c) { return make_float3(a, b, c); }
__device__ __forceinline__ float3 lerp3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}

// Periodic value noise (lattice wraps every `per` cells).
__device__ float vnoise_p(float x, float y, int per, unsigned int seed) {
    const float fx = floorf(x), fy = floorf(y);
    float tx = x - fx, ty = y - fy;
    tx = tx * tx * (3.f - 2.f * tx); ty = ty * ty * (3.f - 2.f * ty);
    const int i0 = (((int)fx % per) + per) % per, j0 = (((int)fy % per) + per) % per;
    const int i1 = (i0 + 1) % per, j1 = (j0 + 1) % per;
    const float a = hash1(i0, j0, seed), b = hash1(i1, j0, seed);
    const float c = hash1(i0, j1, seed), d = hash1(i1, j1, seed);
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

// Static fields at half res: x broad fbm, y detail fbm, z paper fibres, w ember grain (normalised on the host).
extern "C" __global__
void pb_fields(float4* __restrict__ F, const int Wh, const int Hh, const unsigned int seed,
               const float c1, const float s1, const float c2, const float s2, const float ox, const float oy)
{
    const int X = blockIdx.x * blockDim.x + threadIdx.x, Y = blockIdx.y * blockDim.y + threadIdx.y;
    if (X >= Wh || Y >= Hh) return;
    const float u = (X + 0.5f) / Hh + ox, v = (Y + 0.5f) / Hh + oy;      // height units
    // broad: 5-octave fbm with a rotation per octave (burn-through's turn matrix)
    float bx = u * 3.4f, by = v * 3.4f, s = 0.f, amp = 0.5f, nrm = 0.f;
    #pragma unroll
    for (int o = 0; o < 5; ++o) {
        s += amp * vnoise(bx, by, seed + 31u * o); nrm += amp;
        const float nx = 1.61f * bx - 1.18f * by + 4.7f, ny = 1.18f * bx + 1.61f * by + 4.7f;
        bx = nx; by = ny; amp *= 0.5f;
    }
    const float broad = s / nrm;
    const float detail = fbm(u * 11.f + 9.1f, v * 11.f + 1.7f, seed + 1000u);
    // fibres: two crossed, stretched noises
    const float n1 = vnoise((c1 * u + s1 * v) * 36.f, (-s1 * u + c1 * v) * 150.f, seed + 2000u);
    const float n2 = vnoise((c2 * u + s2 * v) * 36.f, (-s2 * u + c2 * v) * 150.f, seed + 2100u);
    const float fib = 0.5f * (n1 + n2) + 0.35f * fabsf(n1 - n2);
    const float grain = 0.6f * vnoise(u * 28.f, v * 28.f, seed + 3000u) + 0.4f * vnoise(u * 61.f, v * 61.f, seed + 3100u);
    F[Y * Wh + X] = make_float4(broad, detail, fib, grain);
}

// Periodic smoke tile (period 1 in tile coordinates).
extern "C" __global__
void pb_tile(float* __restrict__ T, const unsigned int seed)
{
    const int X = blockIdx.x * blockDim.x + threadIdx.x, Y = blockIdx.y * blockDim.y + threadIdx.y;
    if (X >= TILE_N || Y >= TILE_N) return;
    const float u = (X + 0.5f) / TILE_N, v = (Y + 0.5f) / TILE_N;
    float s = 0.f, amp = 0.5f, nrm = 0.f;
    int per = 4;
    #pragma unroll
    for (int o = 0; o < 5; ++o) {
        s += amp * vnoise_p(u * per, v * per, per, seed + 57u * o); nrm += amp; amp *= 0.5f; per *= 2;
    }
    T[Y * TILE_N + X] = s / nrm;
}

__device__ __forceinline__ float tile(const float* __restrict__ T, float u, float v) {
    const float x = u * TILE_N - 0.5f, y = v * TILE_N - 0.5f;
    const float fx = floorf(x), fy = floorf(y);
    const float tx = x - fx, ty = y - fy;
    const int i0 = ((int)fx) & (TILE_N - 1), j0 = ((int)fy) & (TILE_N - 1);
    const int i1 = (i0 + 1) & (TILE_N - 1), j1 = (j0 + 1) & (TILE_N - 1);
    const float a = T[j0 * TILE_N + i0], b = T[j0 * TILE_N + i1], c = T[j1 * TILE_N + i0], d = T[j1 * TILE_N + i1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
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
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z),
                       v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w));
}

// One layer of rising, swaying, twinkling spark streaks (cells of `cs` height units).
__device__ float spark_layer(float px, float py, float cs, float scroll, float sway, float dens,
                             unsigned int seed, unsigned int ti, float tf)
{
    const float sx = px / cs + 0.3f * __sinf(sway + py * 9.f);
    const float sy = (py + scroll) / cs;
    const float cx = floorf(sx), cy = floorf(sy);
    const int ix = (int)cx, iy = (int)cy;
    if (hash1(ix, iy, seed) > dens) return 0.f;
    const float ox = 0.35f + 0.3f * hash1(ix, iy, seed + 1u), oy = 0.35f + 0.3f * hash1(ix, iy, seed + 2u);
    const float lx = sx - cx - ox, ly = sy - cy - oy;
    const float r = 0.05f + 0.03f * hash1(ix, iy, seed + 3u);
    const float d2 = (lx * lx + 0.25f * ly * ly) / (r * r);
    if (d2 > 9.f) return 0.f;
    const float w0 = hash1(ix, iy + 7919 * ti, seed + 4u), w1 = hash1(ix, iy + 7919 * (ti + 1u), seed + 4u);
    return __expf(-d2) * (0.3f + 0.7f * (w0 + (w1 - w0) * tf));
}

// U: 0 ox 1 oy 2 radius 3 roughness 4 edge 5 ember_w 6 char_w 7 scorch_w 8 char_depth 9 env 10 smoke
//    11 spark density 12 hue 13 period 14 upk 15 aa 16 flicker i 17 flicker f 18-21 smoke scrolls
//    22-23 spark scrolls 24 sway 25 twinkle i 26 twinkle f 27 glow_w 28 smoke_len 29 far_out 30 far_in
extern "C" __global__
void pb_main(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
             const float4* __restrict__ F, const float* __restrict__ T, const float* __restrict__ U,
             const float* __restrict__ P, const int W, const int H, const int Wh, const int Hh,
             const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int idx = y * W + x;
    const float invH = 1.f / H;
    const float px = (x + 0.5f) * invH, py = (y + 0.5f) * invH;
    const float4 fl = bilinf4(F, (x + 0.5f) * ((float)Wh / W), (y + 0.5f) * ((float)Hh / H), Wh, Hh);
    const float dx = px - U[0], dy0 = py - U[1];
    const float dy = dy0 < 0.f ? dy0 * U[14] : dy0;
    const float dist = sqrtf(dx * dx + dy * dy);
    const float f = dist + U[3] * ((fl.x - 0.5f) * RW0 + (fl.y - 0.5f) * RW1 + (fl.z - 0.5f) * RW2) - U[2];
    if (f > U[29]) { out[idx] = a[idx]; return; }
    if (f < -U[30]) { out[idx] = b[idx]; return; }

    const float edge = U[4], ew = U[5], cw = U[6], sw = U[7], cd = U[8], env = U[9];
    const uchar4 ua = a[idx], ub = b[idx];
    float3 pa = f3(ua.x, ua.y, ua.z), pb = f3(ub.x, ub.y, ub.z);

    // paper side: brown scorch, then the black char band (irregular, widening with progress)
    float sc = 1.f - smooth(0.f, sw, f); sc *= sc;
    const float scm = sc * 0.75f * (0.35f + 0.65f * cd);
    pa.x *= 1.f - scm * 0.18f; pa.y *= 1.f - scm * 0.42f; pa.z *= 1.f - scm * 0.66f;
    const float irr = 0.62f + 0.56f * fl.y;
    const float charm = 1.f - smooth(0.4f * ew, cw, f);
    const float ch = clamp01(charm * cd * irr * 1.1f);
    pa = lerp3(pa, f3(14.f, 10.f, 8.f), ch);
    // burnt side: the new picture in the soft shadow of the burning edge
    const float shd = (1.f - smooth(0.f, 3.5f * edge, -f)) * 0.55f * env;
    pb.x *= 1.f - shd; pb.y *= 1.f - shd; pb.z *= 1.f - shd;
    const float aa = U[15];
    float3 base = lerp3(pa, pb, 1.f - smooth(-aa, aa, f));

    // how much this pixel lies above the origin (smoke and sparks reach further upward)
    const float up = fmaxf(-dy0, 0.f) / fmaxf(sqrtf(dx * dx + dy0 * dy0), 1e-4f);

    // ember flicker: value noise crossfaded between time seeds
    const unsigned int fi = (unsigned int)U[16];
    const float fk0 = vnoise(px * 7.f, py * 7.f, seed + 7u * fi);
    const float fk1 = vnoise(px * 7.f, py * 7.f, seed + 7u * (fi + 1u));
    const float fk = fk0 + (fk1 - fk0) * U[17];
    const float hue = U[12], per = U[13];
    const float3 c_hot = pal(hue, P);

    // smoke over the unburnt side, lit by the fire close to the front
    const float smk = U[10] * env;
    if (smk > 0.f) {
        const float Z = U[28] * (1.f + 2.5f * up);
        const float zone = smooth(-edge, edge, f) * (1.f - smooth(0.25f * Z, Z, f));
        if (zone > 0.f) {
            const float s = 0.62f * tile(T, px + U[18] + 0.7f * fl.x, py + U[19] + 0.4f * fl.y)
                          + 0.38f * tile(T, 2.3f * px + U[20], 2.3f * py + U[21]);
            const float al = smooth(0.36f, 0.78f, s) * zone * smk * 0.65f;
            const float lit = (1.f - smooth(0.f, 8.f * edge, f)) * env;
            const float3 cs = f3(42.f + 150.f * lit * c_hot.x, 38.f + 150.f * lit * c_hot.y,
                                 36.f + 150.f * lit * c_hot.z);
            base = lerp3(base, cs, al);
        }
    }

    // light, added with an exponential screen
    float3 L = f3(0.f, 0.f, 0.f);
    // ember band (mostly on the paper side), grainy and flickering, hot -> deep along the band
    const float sx = clamp01(f / ew);
    const float eb = (1.f - smooth(-0.3f * edge, ew, f)) * smooth(-0.6f * edge, 0.f, f);
    const float tex = 0.45f + 0.9f * fl.w * (0.55f + 0.45f * fl.z);
    const float eg = eb * tex * (0.55f + 0.9f * fk) * 1.7f * env;
    const float3 c_em = pal(hue + 0.2f * per * sx, P);
    L.x += c_em.x * eg; L.y += c_em.y * eg; L.z += c_em.z * eg;
    // hot yellow-white core line at the paper's edge
    const float co = 1.f - smooth(0.f, 0.45f * edge, fabsf(f - 0.12f * edge));
    const float cg = co * (0.8f + 0.4f * fk) * 2.4f * env;
    L.x += (c_hot.x + (1.00f - c_hot.x) * 0.65f) * cg;
    L.y += (c_hot.y + (0.93f - c_hot.y) * 0.65f) * cg;
    L.z += (c_hot.z + (0.72f - c_hot.z) * 0.65f) * cg;
    // smouldering specks in the char
    const float spk = smooth(0.66f, 0.9f, 0.5f * (fl.w + fl.z)) * charm * (0.3f + 0.7f * fk) * 1.4f * env;
    if (spk > 0.f) {
        const float3 c_sm = pal(hue + 0.1f * per, P);
        L.x += c_sm.x * spk; L.y += c_sm.y * spk; L.z += c_sm.z * spk;
    }
    // warm glow on both sides of the front
    const float gl = 1.f - smooth(0.f, U[27], fabsf(f));
    const float gg = gl * gl * 0.45f * env * (0.75f + 0.5f * fk);
    if (gg > 0.f) {
        const float3 c_gl = pal(hue + 0.05f * per, P);
        L.x += c_gl.x * gg; L.y += c_gl.y * gg; L.z += c_gl.z * gg;
    }
    // rising sparks (two depth layers), mostly above the front
    if (U[11] > 0.f) {
        const float Zs = 16.f * edge * (0.4f + 1.6f * up);
        const float sz = smooth(-1.5f * edge, 0.5f * edge, f) * (1.f - smooth(0.2f * Zs, Zs, f)) * env;
        if (sz > 0.f) {
            const unsigned int ti = (unsigned int)U[25];
            const float d1 = 0.04f + 0.22f * U[11], d2 = 0.05f + 0.25f * U[11];
            const float sp = spark_layer(px, py, 1.f / 45.f, U[22], U[24], d1, seed + 11u, ti, U[26])
                           + 0.75f * spark_layer(px, py, 1.f / 80.f, U[23], 1.3f * U[24] + 1.f, d2, seed + 23u, ti, U[26]);
            const float g = sp * sz * 2.6f;
            L.x += (c_hot.x + (1.00f - c_hot.x) * 0.5f) * g;
            L.y += (c_hot.y + (0.90f - c_hot.y) * 0.5f) * g;
            L.z += (c_hot.z + (0.60f - c_hot.z) * 0.5f) * g;
        }
    }
    const float k = 1.f / 255.f;
    out[idx] = pack(make_float4(255.f * (1.f - (1.f - base.x * k) * __expf(-L.x)),
                                255.f * (1.f - (1.f - base.y * k) * __expf(-L.y)),
                                255.f * (1.f - (1.f - base.z * k) * __expf(-L.z)), 255.f));
}
""".replace("TILEN", str(_TILE)).replace("RW0", f"{_RW[0]}f").replace("RW1", f"{_RW[1]}f").replace("RW2", f"{_RW[2]}f")


class PaperBurn(Transition):
    name = "paper_burn"
    label = "Paper burn"
    description = ("The old picture burns away like paper: a ragged front of char, embers and a white-hot edge "
                   "spreads from an origin with rising sparks and smoke, revealing the new picture behind.")
    default_duration_s = 3.5
    params = {
        "roughness": num(0.0, 2.5, 1.0, "Edge roughness", tier="regional", basic=True),
        "origin_x": num(0.0, 1.0, 0.5, "Origin x", tier="regional"),
        "origin_y": num(0.0, 1.0, 0.68, "Origin y", tier="regional"),
        "burn_width": num(0.0, 1.0, 0.5, "Burn width", tier="regional"),
        "char_depth": num(0.0, 1.0, 0.75, "Char depth", tier="regional"),
        "smoke": num(0.0, 1.0, 0.6, "Smoke", tier="regional"),
        "spark_density": num(0.0, 1.0, 0.5, "Sparks", tier="regional"),
        "ember_hue": num(0.0, 1.0, 0.03, "Ember hue (palette index)", tier="regional"),
        "palette": enum(list(PALETTES), "ember", "Fire palette", tier="regional"),
        "palette_period": num(0.1, 3.0, 1.0, "Palette period", tier="regional"),
        "colour_drift": num(0.0, 1.0, 0.12, "Colour drift over the burn", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        w, h = ctx.width, ctx.height
        self._wh, self._hh = max((w + 1) // 2, 1), max((h + 1) // 2, 1)
        mod = raw_module(_SRC)
        self._k_fields = mod.get_function("pb_fields")
        self._k_tile = mod.get_function("pb_tile")
        self._k = mod.get_function("pb_main")

        s = int(self.params.get("seed", -1))
        seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        rng = np.random.default_rng(seed)
        self._seed = np.uint32(int(rng.integers(1, 2**31)))
        th1 = float(rng.uniform(0.0, math.pi))
        th2 = th1 + float(rng.uniform(0.7, 1.4))

        # static noise fields (half res), range-normalised to [0, 1] per channel
        self._F = cp.empty((self._hh, self._wh, 4), dtype=cp.float32)
        launch_2d(self._k_fields, self._wh, self._hh,
                  (self._F, np.int32(self._wh), np.int32(self._hh), self._seed,
                   np.float32(math.cos(th1)), np.float32(math.sin(th1)),
                   np.float32(math.cos(th2)), np.float32(math.sin(th2)),
                   np.float32(rng.uniform(0.0, 50.0)), np.float32(rng.uniform(0.0, 50.0))))
        flat = self._F.reshape(-1, 4)
        mn = flat.min(axis=0)
        rg = cp.maximum(flat.max(axis=0) - mn, 1e-6)
        self._F -= mn
        self._F /= rg

        # periodic smoke tile, normalised
        self._T = cp.empty((_TILE, _TILE), dtype=cp.float32)
        launch_2d(self._k_tile, _TILE, _TILE, (self._T, np.uint32(self._seed ^ np.uint32(0x5bd1e995))))
        tmn = self._T.min()
        self._T -= tmn
        self._T /= cp.maximum(self._T.max(), 1e-6)

        self._U = cp.zeros(_NU, dtype=cp.float32)
        self._Uh = np.zeros(_NU, np.float32)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ember")
        if name not in PALETTES:
            name = "ember"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        q = float(progress)
        if q <= 1e-4:
            cp.copyto(out, a)
            return
        if q >= 0.9999:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        aspect = w / h
        self._sync_palette()

        R = min(max(float(p["roughness"]), 0.0), 2.5)
        bw = min(max(float(p["burn_width"]), 0.0), 1.0)
        cd = min(max(float(p["char_depth"]), 0.0), 1.0)
        edge = 0.006 + 0.014 * bw                              # height units
        ember_w = 1.6 * edge
        char_base = edge * (2.0 + 5.0 * cd)
        char_w = char_base * (0.6 + 0.8 * q)                  # the char band widens as the burn goes on
        scorch_w = 1.9 * char_w + 2.0 * edge
        glow_w = 7.0 * edge
        smoke_len = 11.0 * edge
        aa = max(1.2 / h, 0.08 * edge)

        # origin and the radius sweep: just off-screen at p=0, just past the farthest corner at p=1
        ox = min(max(float(p["origin_x"]), 0.0), 1.0) * aspect
        oy = min(max(float(p["origin_y"]), 0.0), 1.0)
        maxd = 0.0
        for cx, cy in ((0.0, 0.0), (aspect, 0.0), (0.0, 1.0), (aspect, 1.0)):
            ddy = cy - oy
            ddy = ddy * _UPK if ddy < 0.0 else ddy
            maxd = max(maxd, math.hypot(cx - ox, ddy))
        ramp = 0.5 * R * sum(_RW)
        scorch0 = 1.9 * char_base * 0.6 + 2.0 * edge
        r0 = -ramp - scorch0 - 0.005
        r1 = maxd + ramp + 0.6 * edge + aa + 0.005
        radius = r0 + (r1 - r0) * q

        env = min(1.0, 3.0 * math.sin(math.pi * q))
        ts = q * self.duration_s                               # seconds into the burn (burn-through's uTime)
        fl = ts * 7.0
        tw = ts * 5.0
        hue = float(p["ember_hue"]) + float(p["colour_drift"]) * q
        far_out = max(scorch_w, smoke_len * 3.5, 16.0 * edge * 2.0, glow_w) + 0.002
        far_in = max(glow_w, 3.5 * edge, 1.5 * edge) + 0.002

        U = self._Uh
        U[:] = 0.0
        U[0], U[1], U[2], U[3] = ox, oy, radius, R
        U[4], U[5], U[6], U[7] = edge, ember_w, char_w, scorch_w
        U[8], U[9], U[10], U[11] = cd, env, float(p["smoke"]), float(p["spark_density"])
        U[12], U[13], U[14], U[15] = hue, float(p["palette_period"]), _UPK, aa
        U[16], U[17] = math.floor(fl), fl - math.floor(fl)
        U[18], U[19] = (ts * 0.02) % 1.0, (ts * 0.11) % 1.0       # smoke rises (scrolls wrap: periodic tile)
        U[20], U[21] = (-ts * 0.015) % 1.0, (ts * 0.17 * 2.3) % 1.0
        U[22], U[23] = ts * 0.16, ts * 0.10                        # spark rise (height units)
        U[24] = (ts * 1.3) % (2.0 * math.pi)
        U[25], U[26] = math.floor(tw), tw - math.floor(tw)
        U[27], U[28], U[29], U[30] = glow_w, smoke_len, far_out, far_in
        self._U.set(U)

        launch_2d(self._k, w, h,
                  (a, b, out, self._F, self._T, self._U, self._P, np.int32(w), np.int32(h),
                   np.int32(self._wh), np.int32(self._hh), self._seed))
