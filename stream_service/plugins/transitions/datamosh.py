"""Datamosh: a real datamosh between two scenes.  The old picture is frozen like a lost I-frame and the new
scene's motion (block-matched, codec style) drags its pixels block by block, with DCT-basis block noise,
until the blocks 'I-frame refresh' into the new scene.  An optional strip layer adds torn rows, chroma
split and scanlines on top.

Source: https://github.com/keijiro/KinoDatamosh/blob/master/Assets/Kino/Datamosh/Shader/Datamosh.shader
        https://github.com/keijiro/KinoDatamosh/blob/master/LICENSE
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/StripDatamoshGlitch.glsl
Author: Keijiro Takahashi (Kino/Datamosh); bread (StripDatamoshGlitch)
License: MIT (Datamosh.shader file header; the KinoDatamosh repo LICENSE is Unlicense) AND MIT
         (StripDatamoshGlitch.glsl header "// License: MIT"; gl-transitions repo LICENSE: MIT License,
         "Copyright (c) 2017-present gl-transitions contributors")
license_class: permissive
Ported by smfx (ideas/changes: Kino's two passes are kept -- a block-resolution "update" pass that turns
  motion vectors into per-block displacements with random diffusion, pixel-snapping and a motion accumulator
  that forces intra refresh, then a "mosh" pass that advects a persistent work buffer by those displacements
  and mixes in the source through DCT-like basis patterns, the result being fed back as the next work buffer.
  Unity's camera motion-vector texture does not exist here, so b's motion comes from 1/8-res SAD block matching
  of b(t) against b(t-1) (+-3 texels, parabolic sub-texel refinement, zero-motion bias and a confidence test),
  plus a coherent fbm flow scaled by velocity so stills mosh too.  Displacements use stochastic rounding (Kino
  rounds) so slow motion is not lost.  The DCT pattern is a true 8x8-style 2D basis cos(k)cos(l) per block,
  biased toward the new scene so blocks bleed in before they pop.  Refresh is progress-scheduled per block
  (hash + fbm clusters) and pulled earlier by accumulated motion, so the endpoints are exact: p<=0 gives a,
  p>=1 gives b, and every block is refreshed by p=0.95.  The work buffer is full-res uchar4 (half-res would
  soften the old picture at the first frame).  bread's strip layer is reduced to its torn rows (broken
  segments, x-tear, chroma split, row noise), scanlines and hair-line sparks under a burst envelope, with no
  full-frame strobe (HEVC).  State resets whenever progress decreases or restarts.)

MIT notice (gl-transitions; same terms as the Kino/Datamosh shader header): Permission is hereby granted, free of
charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to
deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge,
publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions: The above copyright notice and this permission notice
shall be included in all copies or substantial portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS",
WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE
FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num

LO = 8            # motion-estimation texel (full-res px)
CELL = 16         # motion-field cell (full-res px) = 2x2 low-res texels
BS_MIN = 4         # smallest block in frame px (block_size is in 4K px, scaled to the frame)

_SRC = HELPERS + r"""
#define SR 3
// 1) 1/8-res luma of b (4 bilinear taps, each a 2x2 average)
extern "C" __global__
void dm_down(const uchar4* __restrict__ b, float* __restrict__ lo, const int W, const int H, const int lw, const int lh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= lw || j >= lh) return;
    const float x0 = 8.f * i, y0 = 8.f * j;
    float s = 0.f;
    #pragma unroll
    for (int k = 0; k < 4; ++k) s += luma(bilin(b, x0 + 2.f + 4.f * (k & 1), y0 + 2.f + 4.f * (k >> 1), W, H));
    lo[j * lw + i] = 0.25f * s;
}

__device__ __forceinline__ float lo_at(const float* __restrict__ L, int x, int y, const int lw, const int lh) {
    x = min(max(x, 0), lw - 1); y = min(max(y, 0), lh - 1);
    return L[y * lw + x];
}

// 2) SAD block matching per 16 px cell: content at q in cur came from q - d in prev; F = d in full-res px
extern "C" __global__
void dm_match(const float* __restrict__ cur, const float* __restrict__ prev, float2* __restrict__ F,
              const int lw, const int lh, const int mw, const int mh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= mw || j >= mh) return;
    const int px = 2 * i - 1, py = 2 * j - 1;                      // 4x4 patch origin (low-res texels)
    float P[16];
    #pragma unroll
    for (int k = 0; k < 16; ++k) P[k] = lo_at(cur, px + (k & 3), py + (k >> 2), lw, lh);
    float C[(2 * SR + 1) * (2 * SR + 1)];
    float best = 1e30f, sad0 = 0.f;
    int bx = 0, by = 0;
    #pragma unroll
    for (int dy = -SR; dy <= SR; ++dy) {
        #pragma unroll
        for (int dx = -SR; dx <= SR; ++dx) {
            float s = 0.f;
            #pragma unroll
            for (int k = 0; k < 16; ++k) s += fabsf(P[k] - lo_at(prev, px + (k & 3) - dx, py + (k >> 2) - dy, lw, lh));
            C[(dy + SR) * (2 * SR + 1) + dx + SR] = s;
            if (dx == 0 && dy == 0) sad0 = s;
            const float c = s + 0.004f * (abs(dx) + abs(dy));       // prefer small motion on ties
            if (c < best) { best = c; bx = dx; by = dy; }
        }
    }
    const float sb = C[(by + SR) * (2 * SR + 1) + bx + SR];
    float mx = 0.f, my = 0.f;
    if (sad0 > 0.03f && sad0 - sb > 0.12f * sad0 + 0.01f) {        // confident, textured, really moved
        mx = (float)bx; my = (float)by;
        if (bx > -SR && bx < SR) {                                  // parabolic sub-texel refinement
            const float cm = C[(by + SR) * (2 * SR + 1) + bx + SR - 1], cp = C[(by + SR) * (2 * SR + 1) + bx + SR + 1];
            const float den = cm - 2.f * sb + cp;
            if (den > 1e-5f) mx += fminf(fmaxf(0.5f * (cm - cp) / den, -0.5f), 0.5f);
        }
        if (by > -SR && by < SR) {
            const float cm = C[(by + SR - 1) * (2 * SR + 1) + bx + SR], cp = C[(by + SR + 1) * (2 * SR + 1) + bx + SR];
            const float den = cm - 2.f * sb + cp;
            if (den > 1e-5f) my += fminf(fmaxf(0.5f * (cm - cp) / den, -0.5f), 0.5f);
        }
    }
    F[j * mw + i] = make_float2(8.f * mx, 8.f * my);
}

// 3) Kino's update pass, per mosh block: displacement (+diffusion, snapped), accumulator, refresh, DCT weight
extern "C" __global__
void dm_blocks(const float2* __restrict__ F, float2* __restrict__ st, float4* __restrict__ bmv,
               const int W, const int H, const int bs, const int bw, const int bh, const int mw, const int mh,
               const float p, const float velocity, const float syn_px, const float diffusion,
               const float t_start, const float t_end, const float band, const float dct_amt, const float burst,
               const float phase, const unsigned int ep, const unsigned int stepi, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= bw || j >= bh) return;
    const unsigned int bid = (unsigned int)(j * bw + i);
    const float cx = (i + 0.5f) * bs, cy = (j + 0.5f) * bs;
    const int mi = min((int)(cx * (1.f / 16.f)), mw - 1), mj = min((int)(cy * (1.f / 16.f)), mh - 1);
    const float2 m = F[mj * mw + mi];
    // coherent flow so stills mosh too (sheets of blocks sliding along fbm currents)
    const float sc = 1.f / (0.42f * H);
    const float u = cx * sc + 0.31f * phase, v = cy * sc - 0.23f * phase;
    const float ang = 12.566370f * fbm(u, v, seed + 5u);
    const float mag = syn_px * (0.3f + 1.4f * fbm(1.7f * u + 3.1f, 1.7f * v - 1.3f, seed + 9u));
    float sn, cs; __sincosf(ang, &sn, &cs);
    float mx = velocity * m.x + mag * cs, my = velocity * m.y + mag * sn;
    // diffusion: random per-block jitter each step
    mx += (hash1(bid, stepi, seed + 21u) - 0.5f) * diffusion * bs * 0.2f;
    my += (hash1(bid, stepi, seed + 22u) - 0.5f) * diffusion * bs * 0.2f;
    // pixel snapping (stochastic, so slow motion survives)
    mx = floorf(mx + hash1(bid, stepi, seed + 23u));
    my = floorf(my + hash1(bid, stepi, seed + 24u));
    // accumulated real motion pulls the intra refresh earlier (moving parts of b arrive first)
    float2 S = st[bid];
    S.x += sqrtf(m.x * m.x + m.y * m.y) / (float)bs;
    const float cl = clamp01((fbm(cx / (0.3f * H), cy / (0.3f * H), seed + 33u) - 0.22f) / 0.56f);
    const float hb = 0.55f * hash1(i, j, seed + 31u) + 0.45f * cl;
    const float T = t_start + (t_end - t_start) * hb - fminf(0.1f * S.x, 0.3f);
    if (p >= T) S.y = 1.f;
    st[bid] = S;
    float dz = 0.f;
    if (S.y < 0.5f) {
        const float d = T - p;
        if (d < band) dz = 1.f - d / band;                          // DCT garbage just before refresh
        if (hash1(bid, ep, seed + 41u) < 0.09f * burst) dz = fmaxf(dz, 0.6f);   // sparse random blocks
        dz *= dct_amt;
    }
    bmv[bid] = make_float4(mx, my, dz, S.y);
}

// value of the new work buffer at pixel (x, y): Kino's mosh pass
__device__ __forceinline__ float4 mosh_at(int x, int y, const uchar4* __restrict__ wold, const uchar4* __restrict__ a,
        const uchar4* __restrict__ b, const float4* __restrict__ bmv, const int W, const int H, const int bs,
        const int bw, const float live_k, const unsigned int ep, const unsigned int seed, const float tint)
{
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1);
    const int bx = x / bs, by = y / bs;
    const float4 B = bmv[by * bw + bx];
    if (B.w > 0.5f) return ld(b, x, y, W, H);                        // I-frame: the new scene, live
    float4 c = ld(wold, x - (int)B.x, y - (int)B.y, W, H);           // P-frame: motion-compensated old picture
    if (live_k > 0.f) c = mix4(c, ld(a, x, y, W, H), live_k);
    if (B.z > 0.f) {
        const unsigned int bid = (unsigned int)(by * bw + bx);
        const float r1 = hash1(bid, ep, seed + 51u), r2 = hash1(bid, ep, seed + 52u), r3 = hash1(bid, ep, seed + 53u);
        int k = (int)(r1 * 4.f), l = (int)(r2 * 4.f);
        if (k + l == 0) k = 1 + (int)(r3 * 3.f);
        const float s = 3.14159265f / (2.f * bs);
        const float lx = (float)(x - bx * bs), ly = (float)(y - by * bs);
        const float basis = __cosf(s * (2.f * lx + 1.f) * k) * __cosf(s * (2.f * ly + 1.f) * l);
        const float w = B.z * (0.45f + 0.55f * r3) * (0.25f + 0.75f * basis);
        const float4 cb = ld(b, x, y, W, H);
        const float tr = (hash1(bid, ep, seed + 54u) - 0.5f) * tint, tg = (hash1(bid, ep, seed + 55u) - 0.5f) * tint,
                    tb = (hash1(bid, ep, seed + 56u) - 0.5f) * tint;
        c.x += w * (cb.x - c.x + tr); c.y += w * (cb.y - c.y + tg); c.z += w * (cb.z - c.z + tb);
    }
    return c;
}

__device__ __forceinline__ float tear_mask(const int x, const int y, const int W, const int H, const unsigned int g,
                                           const unsigned int seed, int* row)
{
    const int r1 = (int)((float)y * 14.f / (float)H), r2 = (int)((float)y * 42.f / (float)H);
    if (hash1(r1, g, seed + 61u) > 0.64f) {
        const int segs = 1 + (int)(hash1(r1, g, seed + 62u) * 6.f);
        const int sg = (int)((float)x * segs / (float)W);
        if (hash1(sg, r1 + 977u * g, seed + 63u) > 0.2f) { *row = r1; return 1.f; }
    }
    if (hash1(r2, g, seed + 64u) > 0.6f) {
        const int segs = 1 + (int)(hash1(r2, g, seed + 65u) * 8.f);
        const int sg = (int)((float)x * segs / (float)W);
        if (hash1(sg, r2 + 733u * g, seed + 66u) > 0.25f) { *row = 1000 + r2; return 0.7f; }
    }
    return 0.f;
}

// 4) mosh pass (feeds back into the work buffer) + bread's strip layer on the output only
extern "C" __global__
void dm_mosh(const uchar4* __restrict__ wold, uchar4* __restrict__ wnew, const uchar4* __restrict__ a,
             const uchar4* __restrict__ b, uchar4* __restrict__ out, const float4* __restrict__ bmv,
             const int W, const int H, const int bs, const int bw, const float live_k, const unsigned int ep,
             const unsigned int seed, const float tint, const float sb, const unsigned int g)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float4 c = mosh_at(x, y, wold, a, b, bmv, W, H, bs, bw, live_k, ep, seed, tint);
    wnew[y * W + x] = pack(c);
    if (sb > 0.002f) {
        const float Wf = (float)W, Hf = (float)H;
        int row = 0;
        const float h = tear_mask(x, y, W, H, g, seed, &row);
        if (h > 0.f) {
            const float rr = hash1(row, g, seed + 71u) - 0.5f;
            const int xs = x + (int)(rr * 2.f * 0.18f * Wf * sb * h);
            const int cs = (int)(Wf * (0.002f + 0.012f * h) * sb);
            const float4 cr = mosh_at(xs + cs, y, wold, a, b, bmv, W, H, bs, bw, live_k, ep, seed, tint);
            const float4 cg = mosh_at(xs, y, wold, a, b, bmv, W, H, bs, bw, live_k, ep, seed, tint);
            const float4 cbl = mosh_at(xs - cs, y, wold, a, b, bmv, W, H, bs, bw, live_k, ep, seed, tint);
            c = make_float4(cr.x, cg.y, cbl.z, 255.f);
            const int nc = max(2, H / 216);                         // >= 2 px grain (HEVC)
            const float n = (hash1(x / nc, y / nc, seed + 31u * g) - 0.5f) * 0.22f * 255.f * sb * h;
            const float l = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z, ds = 0.3f * sb * h;
            c.x += (l - c.x) * ds + n; c.y += (l - c.y) * ds + n; c.z += (l - c.z) * ds + n;
        }
        const float per = fmaxf(Hf / 360.f, 2.f);
        const float scn = 0.5f + 0.5f * __sinf((float)y * 6.2831853f / per);
        const float k = 1.f - 0.14f * sb * scn;
        c.x *= k; c.y *= k; c.z *= k;
        const int hr = (int)((float)y * 540.f / Hf);                  // hair-line sparks (~4 px rows at 4K)
        if (hash1(hr, g, seed + 81u) > 0.985f && hash1((int)((float)x * 5.f / Wf), hr, seed + 82u + g) > 0.35f) {
            const float e = 0.3f * 255.f * sb;
            c.x += 0.72f * e; c.y += 0.9f * e; c.z += e;
        }
    }
    out[y * W + x] = pack(c);
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class Datamosh(Transition):
    name = "datamosh"
    label = "Datamosh"
    description = ("A real datamosh: the new scene's motion drags the frozen old picture block by block, with "
                   "DCT block noise, until blocks I-frame refresh into the new scene.")
    default_duration_s = 2.0
    params = {
        "quality": num(0.0, 1.0, 0.35, "Quality (low = more mosh)", tier="regional", basic=True),
        "velocity": num(0.0, 4.0, 1.5, "Motion gain", unit="×", tier="regional"),
        "block_size": num(8, 64, 32, "Block size", unit="px @4K", step=1, tier="regional"),
        "diffusion": num(0.0, 2.0, 0.6, "Diffusion (block jitter)", tier="regional"),
        "dct_noise": num(0.0, 1.0, 0.5, "DCT block noise", tier="regional"),
        "strip_overlay": num(0.0, 1.0, 0.35, "Strip tears / chroma / scan", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        W, H = ctx.width, ctx.height
        s = int(self.params.get("seed", -1))
        self._seed = np.uint32(np.random.default_rng().integers(0, 2**31) if s < 0 else s)
        mod = raw_module(_SRC)
        self._kdown, self._kmatch = mod.get_function("dm_down"), mod.get_function("dm_match")
        self._kblk, self._kmosh = mod.get_function("dm_blocks"), mod.get_function("dm_mosh")
        self._lw, self._lh = (W + LO - 1) // LO, (H + LO - 1) // LO
        self._mw, self._mh = (W + CELL - 1) // CELL, (H + CELL - 1) // CELL
        self._lo = [cp.zeros((self._lh, self._lw), dtype=cp.float32) for _ in range(2)]
        self._F = cp.zeros((self._mh, self._mw, 2), dtype=cp.float32)
        # block grids sized for the smallest block (block size is in 4K px and scaled to the frame)
        nb = ((W + BS_MIN - 1) // BS_MIN + 1) * ((H + BS_MIN - 1) // BS_MIN + 1)
        self._st = cp.zeros((nb, 2), dtype=cp.float32)
        self._bmv = cp.zeros((nb, 4), dtype=cp.float32)
        self._w = [ctx.new_frame(), ctx.new_frame()]
        self._wi = 0
        self._fresh = True
        self._last_p = 0.0
        self._step = 0
        self._bs = 0

    def _block_px(self) -> int:
        bs = float(self.params.get("block_size", 32)) * self.ctx.height / 2160.0
        return int(max(4, min(128, round(bs))))

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            self._fresh = True
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            self._fresh = True
            return
        p = self.params
        W, H = self.ctx.width, self.ctx.height
        pr = float(progress)
        bs = self._block_px()
        reset = self._fresh or pr < self._last_p - 1e-6 or bs != self._bs
        cur, prev = self._lo
        launch_2d(self._kdown, self._lw, self._lh,
                  (b, cur, np.int32(W), np.int32(H), np.int32(self._lw), np.int32(self._lh)))
        if reset:
            cp.copyto(self._w[0], a)                      # the "lost I-frame": the old picture, frozen
            cp.copyto(prev, cur)
            self._st.fill(0.0)
            self._wi, self._step, self._bs, self._fresh = 0, 0, bs, False
            nfr = 1.0
        else:
            nfr = min(max((pr - self._last_p) * self.duration_s * float(self.ctx.fps or 60), 0.0), 3.0)
        launch_2d(self._kmatch, self._mw, self._mh,
                  (cur, prev, self._F, np.int32(self._lw), np.int32(self._lh), np.int32(self._mw), np.int32(self._mh)))

        q = float(min(max(p["quality"], 0.0), 1.0))
        vel = float(max(p["velocity"], 0.0))
        burst = _smooth(0.0, 0.12, pr) * (1.0 - _smooth(0.72, 0.96, pr))
        t_start = 0.1 + 0.55 * (1.0 - q)
        bw, bh = (W + bs - 1) // bs, (H + bs - 1) // bs
        ep = np.uint32(self._step // 3)
        launch_2d(self._kblk, bw, bh,
                  (self._F, self._st, self._bmv, np.int32(W), np.int32(H), np.int32(bs), np.int32(bw), np.int32(bh),
                   np.int32(self._mw), np.int32(self._mh), np.float32(pr), np.float32(vel),
                   np.float32(H * 0.0015 * vel * nfr), np.float32(p["diffusion"]),
                   np.float32(t_start), np.float32(0.95), np.float32(0.12), np.float32(p["dct_noise"]),
                   np.float32(burst), np.float32(pr * 1.5), ep, np.uint32(self._step), self._seed))
        live_k = 0.5 * (1.0 - _smooth(0.0, 0.22, pr))
        wold, wnew = self._w[self._wi], self._w[1 - self._wi]
        launch_2d(self._kmosh, W, H,
                  (wold, wnew, a, b, out, self._bmv, np.int32(W), np.int32(H), np.int32(bs), np.int32(bw),
                   np.float32(live_k), ep, self._seed, np.float32(50.0 * float(p["dct_noise"])),
                   np.float32(float(p["strip_overlay"]) * burst), np.uint32(int(pr * 24.0))))
        self._wi = 1 - self._wi
        self._lo = [prev, cur]
        self._last_p = pr
        self._step += 1

    def close(self) -> None:
        self._w = []
        self._lo = []
