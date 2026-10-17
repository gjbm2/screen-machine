"""JPEG crush: the over-compressed codec look.  A real 8x8 (or 16x16) DCT quantisation in shared memory on
luma, with 4:2:0 chroma blocks, gives genuine blocking, ringing and mosquito noise.  On top: per-block DC
corruption that runs along the block row (as a broken DC predictor does), flat and striped damaged blocks tinted
from the palette, a bit-depth crush, and an optional P-frame smear that drifts macroblocks from the previous frame.

Source: Original (inspired by https://github.com/Vidvox/ISF-Files/blob/master/ISF/JPEG%20Block%20Corruption.fs,
        https://github.com/pmndrs/postprocessing/blob/main/src/effects/ColorDepthEffect.js)
Author: smfx (DCT implementation; ideas from VIDVOX ISF JPEG Block Corruption, MIT, and pmndrs ColorDepthEffect, Zlib)
License: MIT (original)
license_class: permissive

How it works.  The frame is resampled onto a "codec" grid of ``block_scale`` px per sample (box filtered),
anchored so an MCU boundary passes through the frame centre.  One CUDA block per MCU (2N x 2N samples: four luma
blocks plus one Cb and one Cr block at half resolution) converts to JFIF YCbCr, runs an orthonormal separable
DCT in shared memory, quantises every coefficient with the standard Annex K tables scaled by the libjpeg quality
curve, injects the corruption in the coefficient domain, runs the IDCT and writes the decoded samples (with the
bit-depth crush) to a sample-resolution buffer.  A second pass upsamples it with a sharp bilinear (crisp block
edges, no shimmer at fractional scales), applies the P-frame smear from the previous output and mixes with src.

Signals.  ``amount`` -> m = smoothstep(amount): the quantiser crush c = m * (1 - quality), the codec grid grows
from 1 px to ``block_scale`` over the first 60 % of m, chroma subsampling, bit depth, corruption and smear all
scale with m, so amount 0 is identity (and an early-out copy).  ``pulse`` (raw) drives a burst envelope (fast
attack, ~200 ms decay): the quality collapses (blocks bloom, then recover) and DC corruption bursts; a rising
beat edge (120 ms refractory) reseeds the corruption at once.  Corrupted blocks reseed on an integrated tempo
counter, never the frame count.  The smear history resets when amount leaves 0 or after a long gap.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.beat import advance, paced
from stream_service.plugins._lib.cuda import HELPERS
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

_SRC = HELPERS + PALETTE_CUDA + r"""
#define PI_F 3.14159265f
__constant__ float QL[64] = {
    16, 11, 10, 16, 24, 40, 51, 61,   12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,   14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68,109,103, 77,   24, 35, 55, 64, 81,104,113, 92,
    49, 64, 78, 87,103,121,120,101,   72, 92, 95, 98,112,100,103, 99};
__constant__ float QC[64] = {
    17, 18, 24, 47, 99, 99, 99, 99,   18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,   47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,   99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,   99, 99, 99, 99, 99, 99, 99, 99};

__device__ __forceinline__ float hk(unsigned int id, unsigned int i) {
    return (hash_u(id + i * 0x9E3779B9u) >> 8) * (1.f / 16777216.f);
}

// One CUDA block (256 threads) per MCU of 2N x 2N codec samples.  N = 8 or 16.
extern "C" __global__ __launch_bounds__(256)
void jpeg_mcu(const uchar4* __restrict__ src, uchar4* __restrict__ dec,
              const int W, const int H, const int stride, const int N,
              const float x0, const float y0, const float g, const int ntap,
              const float qmul, const float csub, const float csub2, const float lv,
              const unsigned int seed, const float frac, const float dcProb, const float dcAmt, const float tintW,
              const float* __restrict__ P, const float drift, const float period)
{
    __shared__ float A[2048];
    __shared__ float B[2048];
    __shared__ float Ca[256];
    __shared__ float sDC[4], sStA[4], sTi[4];
    __shared__ int sMode[4], sStU[4], sStV[4];

    const int tid = threadIdx.x;
    const int M = 2 * N, MM = M * M, NN = N * N;
    const int mx = blockIdx.x, my = blockIdx.y;
    const float invN = 1.f / (float)N;

    // orthonormal DCT basis: Ca[x*N+u] = a(u) cos((2x+1) u pi / 2N)
    for (int i = tid; i < NN; i += 256) {
        const int xx = i / N, u = i - xx * N;
        const float a = u == 0 ? sqrtf(invN) : sqrtf(2.f * invN);
        Ca[i] = a * cosf((2.f * xx + 1.f) * (float)u * PI_F * 0.5f * invN);
    }

    // ---- corruption state for the 4 luma blocks: the nearest damaged block to the left whose run reaches us ----
    if (tid < 4) {
        const int bx = mx * 2 + (tid & 1), by = my * 2 + (tid >> 1);
        float dc = 0.f, sa = 0.f, ti = -1.f;
        int mode = 0, su = 0, sv = 0;
        for (int k = 0; k < 12; ++k) {
            const unsigned int id = hash_u((unsigned)(bx - k) * 0x8da6b343u ^ (unsigned)by * 0xd8163841u ^ seed);
            if (hk(id, 0u) < dcProb) {
                const int run = 1 + (int)(hk(id, 1u) * 12.f);
                if (k < run && frac < 0.3f + 0.7f * hk(id, 2u)) {
                    const float hm = hk(id, 3u);
                    mode = hm < 0.55f ? 1 : (hm < 0.8f ? 2 : 3);          // 1 DC shift, 2 flat, 3 stripe
                    dc = (hk(id, 4u) * 2.f - 1.f) * dcAmt * 110.f * (float)N;
                    if (mode == 2) dc *= 0.5f;
                    const int f = N / 8;
                    su = (1 + (int)(hk(id, 5u) * 3.f)) * f;
                    sv = ((int)(hk(id, 6u) * 4.f)) * f;
                    if (hk(id, 7u) < 0.5f) { const int t = su; su = sv; sv = t; }
                    sa = (hk(id, 8u) < 0.5f ? -1.f : 1.f) * (50.f + 70.f * hk(id, 9u)) * dcAmt * (float)N * 0.5f;
                    ti = hk(id, 10u);
                    break;
                }
            }
        }
        sDC[tid] = dc; sMode[tid] = mode; sStU[tid] = su; sStV[tid] = sv; sStA[tid] = sa; sTi[tid] = ti;
    }

    // ---- load: box-filtered samples -> YCbCr ----
    const float sx0 = x0 + (float)(mx * M) * g, sy0 = y0 + (float)(my * M) * g;
    const float inv = 1.f / (float)ntap;
    float dCb[4], dCr[4];
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        dCb[k] = 0.f; dCr[k] = 0.f;
        const int s = tid + k * 256;
        if (s < MM) {
            const int sx = s % M, sy = s / M;
            const float cx = sx0 + (sx + 0.5f) * g, cy = sy0 + (sy + 0.5f) * g;
            float r = 0.f, gg = 0.f, b = 0.f;
            for (int j = 0; j < ntap; ++j) {
                const float fy = cy + ((j + 0.5f) * inv - 0.5f) * g;
                for (int i = 0; i < ntap; ++i) {
                    const float4 c = bilin(src, cx + ((i + 0.5f) * inv - 0.5f) * g, fy, W, H);
                    r += c.x; gg += c.y; b += c.z;
                }
            }
            const float w = inv * inv;
            r *= w; gg *= w; b *= w;
            const float Y = 0.299f * r + 0.587f * gg + 0.114f * b;
            const float Cb = -0.168736f * r - 0.331264f * gg + 0.5f * b + 128.f;
            const float Cr = 0.5f * r - 0.418688f * gg - 0.081312f * b + 128.f;
            const int lb = (sy / N) * 2 + (sx / N);
            A[lb * NN + (sy % N) * N + (sx % N)] = Y - 128.f;
            B[s] = Cb;
            B[MM + s] = Cr;
        }
    }
    __syncthreads();

    // ---- 4:2:0 chroma subsample (csub2 > 0 coarsens further towards 4x4 chroma cells) ----
    for (int i = tid; i < NN; i += 256) {
        const int cx = i % N, cy = i / N;
        const int s = (2 * cy) * M + 2 * cx;
        float cb = 0.25f * (B[s] + B[s + 1] + B[s + M] + B[s + M + 1]);
        float cr = 0.25f * (B[MM + s] + B[MM + s + 1] + B[MM + s + M] + B[MM + s + M + 1]);
        if (csub2 > 0.f) {
            const int s4 = (4 * (cy >> 1)) * M + 4 * (cx >> 1);
            float cb4 = 0.f, cr4 = 0.f;
            for (int j = 0; j < 4; ++j)
                for (int k = 0; k < 4; ++k) { cb4 += B[s4 + j * M + k]; cr4 += B[MM + s4 + j * M + k]; }
            cb += (cb4 * 0.0625f - cb) * csub2;
            cr += (cr4 * 0.0625f - cr) * csub2;
        }
        A[4 * NN + i] = cb - 128.f;
        A[5 * NN + i] = cr - 128.f;
    }
    __syncthreads();
    // chroma detail lost by subsampling (restored by (1 - csub) at decode)
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int s = tid + k * 256;
        if (s < MM) {
            const int sx = s % M, sy = s / M;
            const int ci = (sy >> 1) * N + (sx >> 1);
            dCb[k] = B[s] - (A[4 * NN + ci] + 128.f);
            dCr[k] = B[MM + s] - (A[5 * NN + ci] + 128.f);
        }
    }
    __syncthreads();

    const int NE = 6 * NN;
    // ---- forward DCT, rows: B[b][y][u] = sum_x A[b][y][x] Ca[x][u] ----
    for (int e = tid; e < NE; e += 256) {
        const int bb = e / NN, r = e - bb * NN, yy = r / N, u = r - yy * N;
        const float* a = A + bb * NN + yy * N;
        float acc = 0.f;
        for (int x = 0; x < N; ++x) acc += a[x] * Ca[x * N + u];
        B[e] = acc;
    }
    __syncthreads();
    // ---- forward DCT, columns + quantise + corrupt: A[b][v][u] ----
    for (int e = tid; e < NE; e += 256) {
        const int bb = e / NN, r = e - bb * NN, v = r / N, u = r - v * N;
        float F = 0.f;
        for (int yy = 0; yy < N; ++yy) F += B[bb * NN + yy * N + u] * Ca[yy * N + v];
        const int ti = ((v * 8) / N) * 8 + (u * 8) / N;
        // baseline JPEG clamps quantiser entries to 255, so colour and tone survive even at quality 1
        const float q = fmaxf(fminf((bb < 4 ? QL[ti] : QC[ti]) * qmul, 255.f) * (float)N * 0.125f, 0.02f);
        F = rintf(F / q) * q;
        if (bb < 4) {
            const int md = sMode[bb];
            if (md != 0) {
                if (u == 0 && v == 0) F += sDC[bb];
                else if (md == 2) F = 0.f;
                else if (md == 3 && u == sStU[bb] && v == sStV[bb]) F += sStA[bb];
            }
        } else if (u == 0 && v == 0) {
            float tv = -1.f;
            for (int j = 0; j < 4; ++j) if (sTi[j] >= 0.f) { tv = sTi[j]; break; }
            if (tv >= 0.f) {
                const float3 pc = pal(tv * period + drift, P);
                const float R = pc.x * 255.f, G = pc.y * 255.f, Bc = pc.z * 255.f;
                const float tc = bb == 4 ? (-0.168736f * R - 0.331264f * G + 0.5f * Bc)
                                         : (0.5f * R - 0.418688f * G - 0.081312f * Bc);
                F += (1.3f * tc * (float)N - F) * tintW;
            }
        }
        A[e] = F;
    }
    __syncthreads();
    // ---- inverse DCT, rows: B[b][v][x] = sum_u A[b][v][u] Ca[x][u] ----
    for (int e = tid; e < NE; e += 256) {
        const int bb = e / NN, r = e - bb * NN, v = r / N, x = r - v * N;
        const float* a = A + bb * NN + v * N;
        float acc = 0.f;
        for (int u = 0; u < N; ++u) acc += a[u] * Ca[x * N + u];
        B[e] = acc;
    }
    __syncthreads();
    // ---- inverse DCT, columns: A[b][y][x] = sum_v B[b][v][x] Ca[y][v] ----
    for (int e = tid; e < NE; e += 256) {
        const int bb = e / NN, r = e - bb * NN, yy = r / N, x = r - yy * N;
        float acc = 0.f;
        for (int v = 0; v < N; ++v) acc += B[bb * NN + v * N + x] * Ca[yy * N + v];
        A[e] = acc;
    }
    __syncthreads();

    // ---- decode: YCbCr -> RGB, bit-depth crush, write the sample buffer ----
    const float ilv = 1.f / lv;
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        const int s = tid + k * 256;
        if (s < MM) {
            const int sx = s % M, sy = s / M;
            const int lb = (sy / N) * 2 + (sx / N);
            const float Y = A[lb * NN + (sy % N) * N + (sx % N)] + 128.f;
            const int ci = (sy >> 1) * N + (sx >> 1);
            const float cb = A[4 * NN + ci] + (1.f - csub) * dCb[k];
            const float cr = A[5 * NN + ci] + (1.f - csub) * dCr[k];
            float R = Y + 1.402f * cr, G = Y - 0.344136f * cb - 0.714136f * cr, Bc = Y + 1.772f * cb;
            if (lv < 254.5f) {
                R = rintf(clamp01(R * (1.f / 255.f)) * lv) * ilv * 255.f;
                G = rintf(clamp01(G * (1.f / 255.f)) * lv) * ilv * 255.f;
                Bc = rintf(clamp01(Bc * (1.f / 255.f)) * lv) * ilv * 255.f;
            }
            dec[(my * M + sy) * stride + mx * M + sx] = pack(make_float4(R, G, Bc, 255.f));
        }
    }
}

__device__ __forceinline__ float4 ldd(const uchar4* __restrict__ d, int u, int v, int Ws, int Hs, int stride) {
    u = min(max(u, 0), Ws - 1); v = min(max(v, 0), Hs - 1);
    const uchar4 p = d[v * stride + u];
    return make_float4(p.x, p.y, p.z, 255.f);
}

// Per output pixel: sharp-bilinear upsample of the decoded samples, P-frame smear, mix with src.
extern "C" __global__
void jpeg_out(const uchar4* __restrict__ src, const uchar4* __restrict__ dec, const uchar4* __restrict__ prev,
              uchar4* __restrict__ out, const int W, const int H, const int stride, const int Ws, const int Hs,
              const float x0, const float y0, const float g, const float wmix, const float mcuPx,
              const float smearProb, const unsigned int seedS, const float fracS, const float mvPx, const float leak,
              const int havePrev)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float u = (px - x0) / g - 0.5f, v = (py - y0) / g - 0.5f;
    const float u0 = floorf(u), v0 = floorf(v);
    const float fu = clamp01((u - u0 - 0.5f) * g + 0.5f), fv = clamp01((v - v0 - 0.5f) * g + 0.5f);
    const int iu = (int)u0, iv = (int)v0;
    const float4 a = ldd(dec, iu, iv, Ws, Hs, stride), b = ldd(dec, iu + 1, iv, Ws, Hs, stride);
    const float4 c = ldd(dec, iu, iv + 1, Ws, Hs, stride), d = ldd(dec, iu + 1, iv + 1, Ws, Hs, stride);
    float4 col = mix4(mix4(a, b, fu), mix4(c, d, fu), fv);

    if (havePrev && smearProb > 0.f) {
        const int bx = (int)floorf((px - x0) / mcuPx), by = (int)floorf((py - y0) / mcuPx);
        const unsigned int id = hash_u((unsigned)bx * 0x2c1b3c6du ^ (unsigned)by * 0x297a2d39u ^ seedS);
        if (hk(id, 0u) < smearProb && fracS < 0.35f + 0.65f * hk(id, 1u)) {
            // codec-ish motion vectors: one of 8 directions, integer pixels (no blur build-up)
            const float ang = floorf(hk(id, 2u) * 8.f) * (PI_F * 0.25f);
            const float mag = mvPx * (0.35f + 0.65f * hk(id, 3u));
            const int ox = (int)rintf(cosf(ang) * mag), oy = (int)rintf(sinf(ang) * mag);
            const float4 p = ld(prev, x - ox, y - oy, W, H);
            col = mix4(p, col, leak);
        }
    }
    const uchar4 o = src[y * W + x];
    out[y * W + x] = pack(mix4(make_float4(o.x, o.y, o.z, 255.f), col, wmix));
}
"""


def _smoothstep(x: float, e0: float = 0.0, e1: float = 1.0) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hash01(*vals: int) -> float:
    h = 0x811C9DC5
    for v in vals:
        h = ((h ^ (v & 0xFFFFFFFF)) * 0x01000193) & 0xFFFFFFFF
        h ^= h >> 15
        h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
        h ^= h >> 12
    return (h >> 8) / 16777216.0


class JpegCrush(Effect):
    name = "jpeg_crush"
    label = "JPEG crush"
    stage = "glitch"
    description = ("The over-compressed codec look: a real DCT quantisation with 4:2:0 chroma gives blocking, "
                   "ringing and mosquito noise, plus DC-corruption runs tinted from the palette, bit-depth crush "
                   "and a P-frame smear; quality collapses and corruption bursts on the beat.")
    tags = {"family": ["glitch"], "dims": ["2d"], "technique": ["glitch", "tiling"]}
    params = {
        **standard_effect_params(),
        # the beat envelope drives our own burst envelope and edge detector: keep it raw
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "quality": num(0.0, 1.0, 0.2, "Quality (higher = cleaner)", tier="regional", basic=True),
        "block_scale": num(1.0, 8.0, 4.0, "Block scale (px per codec sample at 4K)", step=0.1, unit="px",
                           tier="regional"),
        "chroma_subsample": num(0.0, 1.0, 0.85, "Chroma subsampling (4:2:0)", tier="regional"),
        "bit_depth": num(0.0, 1.0, 0.35, "Bit-depth crush", tier="regional"),
        "dc_corruption": num(0.0, 1.0, 0.4, "DC corruption", tier="regional", basic=True),
        "pframe_smear": num(0.0, 1.0, 0.25, "P-frame smear", tier="regional"),
        "tempo": paced(num(0.5, 12.0, 3.0, "Corruption tempo (reseeds per second)", step=0.1, unit="/s",
                           tier="regional")),
        "block_size": enum(["8", "16"], "8", "DCT block size", tier="structural"),
        **palette_params("neon", 1.0, 0.05),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k1 = mod.get_function("jpeg_mcu")
        self._k2 = mod.get_function("jpeg_out")
        W, H = ctx.width, ctx.height
        self._stride = W + 64
        self._dec = cp.zeros((H + 64, self._stride, 4), dtype=cp.uint8)
        self._prev = cp.zeros((H, W, 4), dtype=cp.uint8)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        self._cphase = 0.0      # integrated corruption tempo (steps)
        self._sphase = 0.0      # integrated smear tempo
        self._env = 0.0
        self._prev_pulse = 0.0
        self._refract = 0.0
        self._drift = 0.0
        self._have_prev = False
        self._was_on = False

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        raw_dt = float(dt)
        dt = min(max(raw_dt, 0.0), 0.25)

        # burst envelope (fast attack, ~200 ms decay) and rising-edge reseed; dt = 0 means no step
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        kick = False
        if dt > 0.0:
            if pulse > self._env:
                self._env += (pulse - self._env) * (1.0 - math.exp(-dt / 0.012))
            else:
                self._env *= math.exp(-dt * 15.0)
            self._refract = max(self._refract - dt, 0.0)
            if pulse - self._prev_pulse > 0.15 and self._refract <= 0.0:
                kick = True
                self._refract = 0.12
        self._prev_pulse = pulse
        env = self._env

        tempo = float(p["tempo"])
        if kick:
            self._cphase = math.floor(self._cphase) + 1.0
        self._cphase = advance(self._cphase, tempo * (1.0 + 1.5 * env), dt, getattr(self, "beat_clock", None),
                               "tempo") % 1.0e6           # on the beat grid while the clock is sure
        self._sphase = (self._sphase + tempo * 0.4 * dt) % 1.0e6
        self._drift = (self._drift + float(p["drift_speed"]) * dt) % 1.0

        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dt / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))

        amount = min(max(float(p["amount"]), 0.0), 1.0)
        if amount <= 1e-4:
            self._was_on = False
            self._have_prev = False
            cp.copyto(out, src)
            return
        m = _smoothstep(amount)
        have_prev = self._have_prev and self._was_on and raw_dt <= 0.5
        self._was_on = True

        # ---- codec grid: grows from 1 px to block_scale over the first 60 % of m ----
        sc = W / 3840.0
        N = 16 if str(p["block_size"]) == "16" else 8
        M = 2 * N
        g_full = max(float(p["block_scale"]) * sc, 1.0)
        g = 1.0 + (g_full - 1.0) * _smoothstep(m, 0.0, 0.6)
        nhx = int(math.ceil((W * 0.5) / (M * g) - 1e-6))
        nhy = int(math.ceil((H * 0.5) / (M * g) - 1e-6))
        x0 = W * 0.5 - nhx * M * g
        y0 = H * 0.5 - nhy * M * g
        gx, gy = 2 * nhx, 2 * nhy
        Ws, Hs = gx * M, gy * M
        ntap = max(1, min(4, int(math.ceil(g - 1e-3))))

        # ---- quantiser: crush -> libjpeg quality -> table multiplier; pulse collapses quality ----
        quality = float(p["quality"])
        crush = m * (1.0 - quality) * (1.0 + 1.2 * env) + 0.45 * m * env * (1.0 - 0.5 * quality)
        crush = min(max(crush, 0.0), 0.985)
        Q = max(100.0 * (1.0 - crush) ** 2.5, 1.5)
        S = 5000.0 / Q if Q < 50.0 else 200.0 - 2.0 * Q
        qmul = max(S / 100.0, 0.0)
        cs = float(p["chroma_subsample"]) * _smoothstep(m, 0.0, 0.3)
        csub, csub2 = min(cs * 2.0, 1.0), min(max(cs * 2.0 - 1.0, 0.0), 1.0)
        bd = float(p["bit_depth"])
        bits = max(8.0 - 6.5 * (bd ** 0.7 if bd > 0 else 0.0) * m * (1.0 + 0.3 * env), 1.5)
        lv = 2.0 ** bits - 1.0

        # ---- corruption ----
        useed = int(p["seed"]) & 0x7FFFFFFF
        step = int(math.floor(self._cphase))
        frac = self._cphase - step
        step_w = 0.4 + 0.9 * _hash01(useed, step, 3)
        dcc = float(p["dc_corruption"])
        dc_prob = min(dcc * m * step_w * (1.0 + 2.5 * env) * 0.065, 0.15)
        dc_amt = (0.4 + 0.6 * dcc) * (1.0 + 0.5 * env)
        wgt = _smoothstep(m, 0.0, 0.3)
        seed = (useed * 0x9E3779B1 + step * 0x85EBCA77 + 0x165667B1) & 0xFFFFFFFF

        self._k1((gx, gy, 1), (256, 1, 1),
                 (src, self._dec, np.int32(W), np.int32(H), np.int32(self._stride), np.int32(N),
                  np.float32(x0), np.float32(y0), np.float32(g), np.int32(ntap),
                  np.float32(qmul), np.float32(csub), np.float32(csub2), np.float32(lv),
                  np.uint32(seed), np.float32(frac), np.float32(dc_prob), np.float32(dc_amt), np.float32(wgt),
                  self._P_d, np.float32(self._drift), np.float32(p["palette_period"])))

        # ---- P-frame smear ----
        smear = float(p["pframe_smear"])
        sstep = int(math.floor(self._sphase))
        fracS = self._sphase - sstep
        smear_prob = min(smear * m * 0.6 * (1.0 + 0.5 * env), 0.85)
        seedS = (useed * 0x27D4EB2F + sstep * 0x9E3779B1 + 0x5BD1E995) & 0xFFFFFFFF
        mv_px = 260.0 * sc * (0.5 + 0.5 * smear) * (1.0 + env) * dt
        leak = 1.0 - math.exp(-dt * 2.5)
        wmix = _smoothstep(m, 0.0, 0.15)
        bw, bh = 32, 8
        self._k2(((W + bw - 1) // bw, (H + bh - 1) // bh, 1), (bw, bh, 1),
                 (src, self._dec, self._prev, out, np.int32(W), np.int32(H), np.int32(self._stride),
                  np.int32(Ws), np.int32(Hs), np.float32(x0), np.float32(y0), np.float32(g), np.float32(wmix),
                  np.float32(M * g), np.float32(smear_prob), np.uint32(seedS), np.float32(fracS),
                  np.float32(mv_px), np.float32(leak), np.int32(1 if have_prev else 0)))

        if smear > 1e-3:
            cp.copyto(self._prev, out)
            self._have_prev = True
        else:
            self._have_prev = False

    def close(self) -> None:
        self._dec = None
        self._prev = None
        self._P_d = None
