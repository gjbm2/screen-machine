"""Block glitch: digital macroblock damage on a multi-scale hashed grid.  Chosen blocks displace, freeze from a
held frame (or smear its edge row/column into streaks), swap channels, invert or quantise to a palette false
colour, with an RGB jitter split, torn horizontal bands and digital sparkle.  Damage reseeds on an integrated
"glitch tempo", so it is rhythmic, not white noise.

Source: https://github.com/pmndrs/postprocessing/blob/main/src/effects/GlitchEffect.js,
        https://github.com/keijiro/KinoGlitch
Author: Raoul van Ruschen (pmndrs postprocessing GlitchEffect); Keijiro Takahashi (KinoGlitch); three.js DigitalGlitch (ideas only; its staffantans upstream is unlicensed)
License: Zlib + MIT
license_class: permissive
Ported by smfx (ideas/changes: rewritten from scratch as one CUDA pass; quadtree block grid instead of a
perturbation texture; hold buffer for frozen blocks; tempo-integrated seeds; palette false colour; sparkle).

How it works.  A coarse grid (``block_size`` px at 4K, widened by ``stretch``) is offset per seed and each cell
may split into quarters twice (``subdivide``), giving 3 block scales at once.  Every block hashes (block, level,
seed) into a damage roll (``< prob``), a duty window inside the tempo step (blocks drop out as the step ages, so
each reseed lands as a hit), a mode and its offsets.  Horizontal bands tear sideways underneath.  Samples are
nearest-neighbour (crisp, digital) and wrap horizontally.

Signals.  ``amount`` -> m = smoothstep(amount); damage probability = m * density (x a per-step weight), the
displacement, split and sparkle all scale with m, and the damaged colour ops fade in over the first ~30 % of m.
amount 0 is an exact copy.  ``pulse`` (raw) drives a burst envelope (fast attack, ~200 ms decay) that spikes the
probability, displacement and split and kicks the reseed rate; a rising beat edge (refractory 120 ms) forces an
immediate reseed.  Seeds are floor(integrated tempo phase), never the frame count.  The hold buffer (allocated in
setup) refreshes every other step and resets when amount leaves 0 or after a long gap.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float hk(unsigned int id, unsigned int i) {
    return (hash_u(id + i * 0x9E3779B9u) >> 8) * (1.f / 16777216.f);
}
// nearest sample, wrapping horizontally (digital), clamped vertically
__device__ __forceinline__ uchar4 ldw(const uchar4* __restrict__ img, float fx, float fy, int W, int H) {
    int ix = (int)floorf(fx), iy = (int)floorf(fy);
    ix %= W; if (ix < 0) ix += W;
    iy = min(max(iy, 0), H - 1);
    return img[iy * W + ix];
}

extern "C" __global__
void block_glitch(const uchar4* __restrict__ src, const uchar4* __restrict__ hold, uchar4* __restrict__ out,
                  const int W, const int H, const float S0w, const float S0h, const float subdiv,
                  const unsigned int seed, const unsigned int seed2, const float frac,
                  const float prob, const float bandProb, const float D, const float split, const float gsplit,
                  const float holdShare, const float wgt, const float sparkP, const float* __restrict__ P,
                  const float drift, const float period, const float sc)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const uchar4 o = src[y * W + x];

    // ---- multi-scale block grid (quadtree, offset per seed) ----
    const float ox = hash1(seed, 11u) * S0w, oy = hash1(seed, 12u) * S0h;
    const float gx = px + ox, gy = py + oy;
    float bw = S0w, bh = S0h;
    int cx = (int)floorf(gx / bw), cy = (int)floorf(gy / bh);
    unsigned int lvl = 0u;
    #pragma unroll
    for (int l = 0; l < 2; ++l) {
        if (hash1((unsigned)cx * 3u + lvl * 7919u, (unsigned)cy, seed ^ 0xA511E9B3u) < subdiv) {
            bw *= 0.5f; bh *= 0.5f; ++lvl;
            cx = (int)floorf(gx / bw); cy = (int)floorf(gy / bh);
        } else break;
    }
    const unsigned int bid = hash_u((unsigned)cx * 0x8da6b343u ^ (unsigned)cy * 0xd8163841u ^ (lvl + 1u) * 0xcb1ab31fu ^ seed);
    const bool blk = hk(bid, 0u) < prob && frac < 0.3f + 0.7f * hk(bid, 1u);

    // ---- torn horizontal bands ----
    const float bandH = fmaxf(S0h * (0.2f + 0.5f * hash1(seed, 21u)), 2.f);
    const int bi = (int)floorf((py + hash1(seed, 22u) * bandH) / bandH);
    const unsigned int bnd = hash_u((unsigned)bi * 0x2c1b3c6du ^ seed ^ 0x51ED270Bu);
    const bool band = hk(bnd, 0u) < bandProb && frac < 0.25f + 0.75f * hk(bnd, 1u);

    float4 c;
    bool dmg = false;
    if (!blk && !band) {
        if (gsplit > 0.05f) {
            c = make_float4(ldw(src, px + gsplit, py, W, H).x, o.y, ldw(src, px - gsplit, py, W, H).z, 255.f);
        } else {
            c = make_float4(o.x, o.y, o.z, 255.f);
        }
    } else {
        dmg = true;
        float sx = px, sy = py, sp = gsplit;
        if (band) {
            sx += (hk(bnd, 2u) * 2.f - 1.f) * D * 0.6f;
            sp += split * 0.6f * (hk(bnd, 3u) < 0.5f ? -1.f : 1.f);
        }
        int mode = -1;                                 // -1 band only, 0 freeze, 1 displace, 2 swap, 3 invert, 4 quantise
        const uchar4* img = src;
        if (blk) {
            const float hm = hk(bid, 2u);
            if (hm < holdShare) mode = 0;
            else {
                const float u = (hm - holdShare) / fmaxf(1.f - holdShare, 1e-4f);
                mode = u < 0.4f ? 1 : (u < 0.58f ? 2 : (u < 0.74f ? 3 : 4));
            }
            const float h3 = hk(bid, 3u) * 2.f - 1.f, h4 = hk(bid, 4u);
            const float x0 = cx * bw - ox, y0 = cy * bh - oy;
            if (mode == 1) {
                sx += h3 * D;
                if (h4 < 0.25f) sy += (h4 * 8.f - 1.f) * D * 0.3f;
            } else if (mode == 0) {
                img = hold;
                sx += h3 * D * 0.35f;
                const float h5 = hk(bid, 5u);
                if (h5 < 0.35f) sy = y0 + 0.5f;                          // vertical streaks from the top row
                else if (h5 < 0.6f) sx = x0 + 0.5f + h3 * D * 0.35f;     // horizontal streaks from the left column
            } else {
                sx += h3 * D * 0.2f;
            }
            sp += split * (0.4f + hk(bid, 6u)) * (hk(bid, 7u) < 0.5f ? -1.f : 1.f);
        }
        c = make_float4(ldw(img, sx + sp, sy, W, H).x, ldw(img, sx, sy, W, H).y, ldw(img, sx - sp, sy, W, H).z, 255.f);
        if (mode >= 2) {
            const float h8 = hk(bid, 8u), h9 = hk(bid, 9u);
            if (mode == 2) {
                const int pm = (int)(h8 * 3.f);
                c = pm == 0 ? make_float4(c.y, c.z, c.x, 255.f) : (pm == 1 ? make_float4(c.z, c.x, c.y, 255.f)
                                                                           : make_float4(c.z, c.y, c.x, 255.f));
            } else if (mode == 3) {
                c = make_float4(255.f - c.x, 255.f - c.y, 255.f - c.z, 255.f);
                if (h8 < 0.45f) {
                    const float3 t = pal(luma(c) * period + drift + h9 * 0.5f, P);
                    c = make_float4(c.x * (0.35f + 0.65f * t.x), c.y * (0.35f + 0.65f * t.y), c.z * (0.35f + 0.65f * t.z), 255.f);
                }
            } else {
                const float lv = 2.f + floorf(h8 * 3.f);
                const float qx = floorf(c.x / 255.f * lv + 0.5f) / lv, qy = floorf(c.y / 255.f * lv + 0.5f) / lv;
                const float qz = floorf(c.z / 255.f * lv + 0.5f) / lv;
                const float L = floorf(luma(c) * lv + 0.5f) / lv;
                const float3 t = pal(L * period + drift + h9 * 0.3f, P);
                const float f = h9 > 0.35f ? 1.f : 0.5f;
                c = make_float4(255.f * (qx + (t.x - qx) * f), 255.f * (qy + (t.y - qy) * f), 255.f * (qz + (t.z - qz) * f), 255.f);
            }
        }
        c = mix4(make_float4(o.x, o.y, o.z, 255.f), c, wgt);
    }

    // ---- digital sparkle (short dashes, twinkling on a faster sub-step of the tempo) ----
    if (sparkP > 0.f) {
        const float spw = fmaxf(7.f * sc, 1.f), sph = fmaxf(2.f * sc, 1.f);
        const int qx = (int)floorf(px / spw), qy = (int)floorf(py / sph);
        const float hs = hash1((unsigned)qx, (unsigned)qy, seed2);
        const float thr = sparkP * (dmg ? 8.f : 1.f);
        if (hs < thr) {
            const float r = hs / thr;
            float3 t = pal(r * 2.f * period + drift, P);
            if (r < 0.25f) t = make_float3(1.f, 1.f, 1.f);
            const float a = 0.95f * wgt;
            c = make_float4(255.f - (255.f - c.x) * (1.f - a * t.x), 255.f - (255.f - c.y) * (1.f - a * t.y),
                            255.f - (255.f - c.z) * (1.f - a * t.z), 255.f);
        }
    }
    out[y * W + x] = pack(c);
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


class BlockGlitch(Effect):
    name = "block_glitch"
    label = "Block glitch"
    stage = "glitch"
    description = ("Digital macroblock damage on a multi-scale grid: blocks displace, freeze, smear, swap "
                   "channels, invert or posterise to palette false colour, with RGB jitter, torn bands and "
                   "sparkle, reseeding rhythmically on a glitch tempo and bursting on the beat.")
    tags = {"family": ["glitch"], "dims": ["2d"], "technique": ["glitch", "tiling"]}
    params = {
        **standard_effect_params(),
        # the beat envelope drives our own burst envelope and edge detector: keep it raw
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "block_size": num(16.0, 256.0, 112.0, "Block size (px at 4K)", step=1.0, unit="px", tier="regional",
                          basic=True),
        "density": num(0.0, 1.0, 0.45, "Damage density", tier="regional", basic=True),
        "displacement": num(0.0, 1.0, 0.5, "Displacement", tier="regional"),
        "rgb_split": num(0.0, 1.0, 0.5, "RGB jitter split", tier="regional"),
        "hold_share": num(0.0, 1.0, 0.3, "Frozen / smeared block share", tier="regional"),
        "tempo": num(0.5, 16.0, 4.0, "Glitch tempo (reseeds per second)", step=0.1, unit="/s", tier="regional"),
        "subdivide": num(0.0, 1.0, 0.55, "Multi-scale (block subdivision)", tier="regional"),
        "stretch": num(0.0, 1.0, 0.35, "Block stretch (wide macroblocks)", tier="regional"),
        "sparkle": num(0.0, 1.0, 0.4, "Digital sparkle", tier="regional"),
        **palette_params("neon", 1.0, 0.05),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("block_glitch")
        self._hold = cp.zeros((ctx.height, ctx.width, 4), dtype=cp.uint8)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        self._tphase = 0.0      # integrated glitch tempo (steps)
        self._env = 0.0         # burst envelope
        self._prev_pulse = 0.0
        self._refract = 0.0
        self._drift = 0.0
        self._hold_step = None  # tempo step index the hold buffer was last taken at
        self._was_on = False

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        raw_dt = float(dt)
        dt = min(max(raw_dt, 0.0), 0.25)

        # burst envelope (fast attack, ~200 ms decay to 5 %) and rising-edge reseed; dt = 0 means no step
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

        tempo = float(p["tempo"]) * (1.0 + 1.5 * env)
        if kick:
            self._tphase = math.floor(self._tphase) + 1.0
        self._tphase += tempo * dt
        if self._tphase > 1.0e6:
            self._tphase -= 1.0e6
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
            cp.copyto(out, src)
            return
        m = _smoothstep(amount)

        step = int(math.floor(self._tphase))
        useed = int(p["seed"]) & 0x7FFFFFFF
        # hold buffer: reset when amount leaves 0 / after a gap; refresh every other step (or on the beat)
        hstep = step // 2
        if (not self._was_on) or raw_dt > 0.5 or self._hold_step is None or (hstep != self._hold_step and
                                                                             _hash01(useed, hstep, 7) < 0.8):
            cp.copyto(self._hold, src)
            self._hold_step = hstep
        self._was_on = True

        sc = W / 3840.0
        S0h = max(float(p["block_size"]) * sc, 4.0)
        S0w = S0h * (1.0 + 3.0 * float(p["stretch"]))
        step_w = 0.55 + 0.9 * _hash01(useed, step, 3)        # some steps heavy, some light
        density = float(p["density"])
        prob = min(m * density * step_w * (1.0 + 1.3 * env), 0.97)
        band_prob = min(0.45 * prob, 0.6)
        D = float(p["displacement"]) * m * (1.0 + 1.5 * env) * W * 0.08
        split = float(p["rgb_split"]) * m * (1.0 + env) * 16.0 * sc
        gsplit = float(p["rgb_split"]) * m * env * 6.0 * sc
        wgt = _smoothstep(m, 0.0, 0.3)
        spark = float(p["sparkle"]) * m * (0.0015 + 0.006 * density) * (1.0 + 3.0 * env)
        frac = self._tphase - math.floor(self._tphase)
        seed = (useed * 0x9E3779B1 + step * 0x85EBCA77 + 0x165667B1) & 0xFFFFFFFF
        seed2 = (useed * 0x27D4EB2F + int(math.floor(self._tphase * 3.0)) * 0x9E3779B1 + 0x5BD1E995) & 0xFFFFFFFF
        launch_2d(self._k, W, H, (src, self._hold, out, np.int32(W), np.int32(H), np.float32(S0w), np.float32(S0h),
                                  np.float32(p["subdivide"]), np.uint32(seed), np.uint32(seed2), np.float32(frac),
                                  np.float32(prob), np.float32(band_prob), np.float32(D), np.float32(split),
                                  np.float32(gsplit), np.float32(p["hold_share"]), np.float32(wgt),
                                  np.float32(spark), self._P_d, np.float32(self._drift),
                                  np.float32(p["palette_period"]), np.float32(sc)))

    def close(self) -> None:
        self._hold = None
        self._P_d = None
