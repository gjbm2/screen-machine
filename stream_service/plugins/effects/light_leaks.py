"""Light leaks: analogue film light leaks.  Large soft orange, red and magenta blooms creep in from the frame
edges, flow slowly, breathe and flicker, and are screen-composited over the picture with a gentle burn (the hot
cores bleach toward warm white).  Optional end-of-roll fogging: a fogged edge strip plus occasional big edge
flashes.

Source: Original (inspired by https://github.com/Vidvox/ISF-Files/blob/master/ISF/v002%20Light%20Leak.fs)
Author: smfx (original; the v002 Light Leak is CC-BY-NC-SA-3.0, idea only)
License: MIT (original)
license_class: permissive
Implementation: original.  No v002 code was read or ported; only the idea (warm edge blooms screened over the
picture) is shared.  See http://v002.info/licenses/ for the v002 terms.

How it works.  A 1/8-res light field is built each frame from (a) 6 big anisotropic blobs anchored on a rounded
frame boundary (they slide slowly round the frame, breathe in and out, and are shaped by low-frequency domain-warped
value noise), (b) a noise layer whose presence is biased toward the edges, (c) an end-of-roll fog strip and edge
flash.  Colour comes from a fixed warm "film" cosine palette (amber -> coral -> magenta -> crimson -> orange)
mixed toward the shared palette by `warmth`, both indexed by field * palette_period + integrated drift.  The full-res
pass bilinear-upsamples the field, bleaches hot cores (burn), soft-clips and screen-blends it over the input.

Realtime.  amount: leak gain is scaled by m = smoothstep(amount), so 0 is identity (and amount <= 1e-4 copies).
pulse: a flash from the edge (gain +80%, edge-weighted extra kick, reach and blob size spread) plus a flow-rate
kick; all pulse terms are scaled by m and only ever add to gains, sizes and rates.  energy: flow rate
(0.3 + 0.7e), blob count (1.5 + 4.5e^1.1, faded, never popped), coverage, brightness and film flicker.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NB = 6                       # blobs
BOFF = 48                    # blob block offset in P (7 floats each)
NP = BOFF + 7 * NB
DS = 8                       # field downscale (1/8 res)

# Warm film palette (cosine form): amber -> coral -> magenta -> crimson -> orange -> amber.
FILM = [0.94, 0.30, 0.20,
        0.06, 0.40, 0.42,
        1.00, 1.00, 1.00,
        0.00, 0.00, 0.60]

# P layout: 0..11 palette | 12..23 film palette | 24 aspect | 25,26 flow offset | 27,28 warp offset | 29 reach
#           30 edge_bias | 31 warmth | 32 period | 33 drift | 34 seed | 35 presence threshold | 36 noise-layer gain
#           37 fog | 38,39 fog dir | 40 flash | 41,42 flash dir | 43 pulse edge kick | 44 gain | 45 noise freq
#           48.. blobs: cx, cy, nx, ny, radius, colour index, strength

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float3 lcol(float t, const float* __restrict__ P) {
    const float ti = t * P[32] + P[33];
    const float3 f = pal(ti, P + 12);
    const float3 q = pal(ti, P);
    const float k = P[31];
    return make_float3(q.x + (f.x - q.x) * k, q.y + (f.y - q.y) * k, q.z + (f.z - q.z) * k);
}

// 1/8-res light field: rgb = leak light (sRGB-ish, may exceed 1; already scaled by the amount gain).
extern "C" __global__
void ll_field(float4* __restrict__ L, const int w, const int h, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float A = P[24];
    const float u = ((x + 0.5f) / (float)w * 2.f - 1.f) * A;     // x in [-A, A]
    const float v = (y + 0.5f) / (float)h * 2.f - 1.f;           // y in [-1, 1] (2 units = one frame height)
    const unsigned int seed = (unsigned int)P[34];

    // distance to the nearest frame edge (>= 0) and the edge falloff
    const float de = fmaxf(fminf(A - fabsf(u), 1.f - fabsf(v)), 0.f);
    const float reach = fmaxf(P[29], 1e-3f);
    const float fall = __expf(-de / reach);

    // low-frequency domain-warped noise
    const float wx = fbm(u * 0.55f + P[27], v * 0.55f + P[28], seed + 101u) - 0.5f;
    const float wy = fbm(u * 0.55f - P[28] + 5.2f, v * 0.55f + P[27] + 1.3f, seed + 202u) - 0.5f;
    const float qx = u + 1.4f * wx, qy = v + 1.4f * wy;
    const float F = P[45];
    const float n = fbm(qx * F + P[25], qy * F + P[26], seed);
    const float n2 = vnoise(qx * 1.9f - P[26], qy * 1.9f + P[25], seed + 7u);

    const float kick = 1.f + P[43] * (0.35f + 0.65f * fall);     // beat flash, strongest at the edge
    float3 acc = make_float3(0.f, 0.f, 0.f);

    // (a) noise layer, biased toward the edges
    {
        const float eb = P[30];
        const float edge = eb * fall + (1.f - eb) * (0.55f + 0.45f * fall);
        const float pres = smooth(P[35], P[35] + 0.26f, n);
        const float wN = P[36] * pres * edge;
        const float3 c = lcol(0.15f + 0.9f * n + 0.25f * wx, P);
        acc.x += wN * c.x; acc.y += wN * c.y; acc.z += wN * c.z;
    }

    // (b) big blobs anchored on the rounded frame boundary
    #pragma unroll
    for (int i = 0; i < """ + str(NB) + r"""; ++i) {
        const float* B = P + """ + str(BOFF) + r""" + 7 * i;
        if (B[6] <= 1e-4f) continue;
        const float dx = u - B[0], dy = v - B[1];
        const float tn = dx * B[2] + dy * B[3];                  // along the outward normal
        const float tt = -dx * B[3] + dy * B[2];                 // along the edge
        const float ir = 1.f / fmaxf(B[4], 1e-3f);
        const float r2 = (0.42f * tt * tt + tn * tn) * ir * ir;
        const float shape = __expf(-r2 * (0.7f + 0.9f * n2));
        const float wb = B[6] * shape * (0.35f + 1.3f * n);
        const float3 c = lcol(B[5] + 0.45f * (n - 0.5f), P);
        acc.x += wb * c.x; acc.y += wb * c.y; acc.z += wb * c.z;
    }

    // (c) end-of-roll: fogged edge strip
    if (P[37] > 1e-4f) {
        const float fx = P[38], fy = P[39];
        const float ext = fabsf(fx) * A + fabsf(fy);
        const float ds = fmaxf(ext - (u * fx + v * fy), 0.f);
        const float wf = P[37] * __expf(-ds / (0.07f + 0.12f * n)) * (0.5f + 0.9f * n2);
        const float3 c = lcol(0.9f + 0.25f * n, P);
        acc.x += wf * c.x; acc.y += wf * c.y; acc.z += wf * c.z;
    }
    // (c') end-of-roll: big edge flash (hot, near white at the edge)
    if (P[40] > 1e-4f) {
        const float fx = P[41], fy = P[42];
        const float ext = fabsf(fx) * A + fabsf(fy);
        const float ds = fmaxf(ext - (u * fx + v * fy), 0.f);
        const float fl = P[40];
        const float wf = fl * (__expf(-ds / (0.35f + 1.3f * fl)) * (0.7f + 0.6f * n) + 0.12f);
        const float3 c = lcol(0.02f + 0.2f * n, P);
        const float hw = 0.45f * __expf(-ds * 2.5f);
        acc.x += wf * (c.x + (1.f - c.x) * hw);
        acc.y += wf * (c.y + (1.f - c.y) * hw);
        acc.z += wf * (c.z + (1.f - c.z) * hw);
    }

    const float g = P[44] * kick;
    L[y * w + x] = make_float4(acc.x * g, acc.y * g, acc.z * g, 0.f);
}

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

// Full res: upsample, burn (hot cores bleach toward warm white), soft clip, screen over the input.
extern "C" __global__
void ll_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const float4* __restrict__ L,
             const int W, const int H, const int w, const int h, const float burn)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 r = bl4(L, (x + 0.5f) * w / (float)W, (y + 0.5f) * h / (float)H, w, h);
    const float lm = fmaxf(fmaxf(r.x, r.y), fmaxf(r.z, 0.f));
    const float hot = 0.85f * burn * smooth(0.35f, 1.6f, lm);
    const float lx = fmaxf(r.x + (1.00f * lm - r.x) * hot, 0.f);
    const float ly = fmaxf(r.y + (0.90f * lm - r.y) * hot, 0.f);
    const float lz = fmaxf(r.z + (0.72f * lm - r.z) * hot, 0.f);
    const float gx = 1.f - __expf(-1.1f * lx), gy = 1.f - __expf(-1.1f * ly), gz = 1.f - __expf(-1.1f * lz);
    const uchar4 s = src[y * W + x];
    const float sr = s.x * (1.f / 255.f), sg = s.y * (1.f / 255.f), sb = s.z * (1.f / 255.f);
    // screen, then a gentle exposure burn on the picture under the leak
    const float bk = 0.45f * burn;
    float cr = sr + (1.f - sr) * gx, cg = sg + (1.f - sg) * gy, cb = sb + (1.f - sb) * gz;
    cr += bk * gx * sr * (1.f - cr); cg += bk * gy * sg * (1.f - cg); cb += bk * gz * sb * (1.f - cb);
    // static (not per-frame) dither against banding in the soft gradients, faded in with the light
    const float dth = (hash1((unsigned int)x, (unsigned int)y, 911u) - 0.5f) * fminf(lm * 12.f, 1.f);
    out[y * W + x] = pack(make_float4(255.f * cr + dth, 255.f * cg + dth, 255.f * cb + dth, 255.f));
}
"""

_TAU = 2.0 * math.pi


def _wrap(v: float, period: float) -> float:
    return math.fmod(v, period) if abs(v) > period else v


def _ss(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _hash01(*k: int) -> float:
    h = 2166136261
    for v in k:
        h ^= v & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 0x5BD1E995) & 0xFFFFFFFF
        h ^= h >> 15
    return (h & 0xFFFFFF) / float(1 << 24)


_DIRS = ((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0))


class LightLeaks(Effect):
    name = "light_leaks"
    label = "Light leaks"
    stage = "light"
    description = ("Analogue film light leaks: big soft orange, red and magenta blooms creep in from the frame "
                   "edges and flow slowly, screened over the picture with a gentle burn; the beat flashes them.")
    params = {
        **standard_effect_params(),
        "energy": num(0.0, 1.0, 0.5, "Energy", tier="realtime", bind="level"),
        "leak_size": num(0.1, 1.0, 0.55, "Leak size", tier="regional", basic=True),
        "edge_bias": num(0.0, 1.0, 0.75, "Edge bias", tier="regional"),
        "warmth": num(0.0, 1.0, 0.8, "Warm film <-> palette (1 = film)", tier="regional"),
        "burn": num(0.0, 1.0, 0.45, "Burn", tier="regional"),
        "roll_flash": num(0.0, 1.0, 0.15, "End-of-roll fog/flashes", tier="regional"),
        "flow_speed": num(0.0, 3.0, 1.0, "Flow speed", unit="×", tier="realtime", bind="rate"),
        **palette_params("ember", 1.0, 0.03),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kf = mod.get_function("ll_field")
        self._kc = mod.get_function("ll_comp")
        W, H = ctx.width, ctx.height
        self.w, self.h = max(16, W // DS), max(16, H // DS)
        self.L = cp.zeros((self.h, self.w, 4), cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.aspect = W / H
        seed = int(params.get("seed", 1) or 1)
        rng = np.random.default_rng(seed)
        self.seed_i = int(rng.integers(0, 1 << 16))
        self.seed = float(self.seed_i)
        # per-blob constants: angular rate (rad/s, random sign), breathe rate, size and offset jitter, colour
        self.b_ang = rng.uniform(0.0, _TAU, NB)
        self.b_w = rng.uniform(0.012, 0.045, NB) * rng.choice([-1.0, 1.0], NB)
        self.b_ph = rng.uniform(0.0, _TAU, NB)
        self.b_br = _TAU * rng.uniform(0.025, 0.075, NB)
        self.b_sz = rng.uniform(0.7, 1.3, NB)
        self.b_off = rng.uniform(-0.12, 0.12, NB)
        self.b_col = rng.uniform(0.0, 1.0, NB)
        self.fog_dir = _DIRS[int(rng.integers(0, 4))]
        self.flow = float(rng.uniform(0.0, _TAU))
        self.warp = float(rng.uniform(0.0, _TAU))
        self.flick = rng.uniform(0.0, _TAU, 3)
        self.fog_ph = 0.0
        self.roll = 0.0
        self.drift = 0.0
        self.pal = None

    def _boundary(self, ang: float) -> tuple[float, float, float, float]:
        """Point on a rounded (superellipse p=6) frame boundary in direction ang, and its outward normal."""
        A, pw = self.aspect, 6.0
        c, s = math.cos(ang), math.sin(ang)
        r = ((abs(c) / A) ** pw + abs(s) ** pw) ** (-1.0 / pw)
        px, py = r * c, r * s
        nx = math.copysign(abs(px / A) ** (pw - 1.0) / A, px)
        ny = math.copysign(abs(py) ** (pw - 1.0), py)
        nl = math.hypot(nx, ny) + 1e-9
        return px, py, nx / nl, ny / nl

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        m = _ss(amount)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        pk = pulse * m
        e = min(max(float(p["energy"]), 0.0), 1.0)

        # host-integrated phases (pulse adds to rates only; positions never jump)
        fm = float(p["flow_speed"]) * (0.3 + 0.7 * e) * (1.0 + 2.0 * pulse)
        self.flow = _wrap(self.flow + 0.035 * fm * dt, _TAU)
        self.warp = _wrap(self.warp + 0.022 * fm * dt, _TAU)
        self.b_ang = np.fmod(self.b_ang + self.b_w * fm * dt, _TAU)
        self.b_ph = np.fmod(self.b_ph + self.b_br * (0.5 + 0.5 * fm) * dt, _TAU)
        self.flick = np.fmod(self.flick + _TAU * np.array([0.9, 2.3, 3.7]) * dt, _TAU)
        self.fog_ph = _wrap(self.fog_ph + _TAU * 0.11 * dt, _TAU)
        self.roll += dt / 3.5
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * dt, 100.0)
        pc = np.asarray(palette_coeffs(p["palette"]), np.float32)
        ap = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.pal = pc if self.pal is None else self.pal + (pc - self.pal) * ap

        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        A = self.aspect
        size = float(p["leak_size"])
        eb = float(p["edge_bias"])
        roll = float(p["roll_flash"])
        hp = self.host
        hp[0:12] = self.pal
        hp[12:24] = FILM
        hp[24] = A
        hp[25] = 5.0 * math.cos(self.flow) + 11.0
        hp[26] = 5.0 * math.sin(self.flow) + 7.0
        hp[27] = 3.0 * math.cos(self.warp) - 4.0
        hp[28] = 3.0 * math.sin(self.warp) + 2.0
        hp[29] = (0.22 + 0.9 * size) * (1.0 + 0.4 * pk)
        hp[30] = eb
        hp[31] = float(p["warmth"])
        hp[32] = float(p["palette_period"])
        hp[33] = self.drift
        hp[34] = self.seed
        hp[35] = 0.60 - 0.14 * e
        hp[36] = 0.6
        # end-of-roll fog strip (always there when roll_flash > 0, slowly breathing)
        hp[37] = 0.9 * roll * (0.75 + 0.25 * math.sin(self.fog_ph))
        hp[38], hp[39] = self.fog_dir
        # end-of-roll flash: one 3.5 s slot at a time, a flash in it with probability ~ roll_flash
        slot = int(math.floor(self.roll))
        f = self.roll - slot
        r = _hash01(self.seed_i, slot)
        prob = 0.55 * roll ** 1.3
        gate = _ss((prob - r) / 0.08)
        env = _ss(f / 0.05) * math.exp(-max(f - 0.05, 0.0) * 6.0)
        hp[40] = gate * env * (0.6 + 0.6 * roll)
        hp[41], hp[42] = _DIRS[int(_hash01(self.seed_i, slot, 77) * 4.0) % 4]
        hp[43] = 0.9 * pk
        flick = 1.0 + (0.04 + 0.14 * e) * (0.6 * math.sin(self.flick[0]) + 0.5 * math.sin(self.flick[1])
                                           + 0.3 * math.sin(self.flick[2])) / 1.4
        hp[44] = m * (0.7 + 0.5 * e) * flick * (1.0 + 0.8 * pk)
        hp[45] = 0.85
        # blobs: count eases with energy (fading weights), each breathing and sliding round the frame
        count = 1.5 + 4.5 * e ** 1.1
        off_base = -0.55 + 0.85 * eb
        for i in range(NB):
            act = min(max(count - i, 0.0), 1.0)
            breathe = 0.3 + 0.7 * _ss(0.5 + 0.5 * math.sin(float(self.b_ph[i])))
            bx, by, nx, ny = self._boundary(float(self.b_ang[i]))
            off = off_base + float(self.b_off[i])
            o = BOFF + 7 * i
            hp[o + 0] = bx + nx * off
            hp[o + 1] = by + ny * off
            hp[o + 2] = nx
            hp[o + 3] = ny
            hp[o + 4] = (0.3 + 0.95 * size) * float(self.b_sz[i]) * (1.0 + 0.3 * pk)
            hp[o + 5] = float(self.b_col[i])
            hp[o + 6] = act * breathe
        self.P.set(hp)

        W, H = self.ctx.width, self.ctx.height
        w, h = self.w, self.h
        launch_2d(self._kf, w, h, (self.L, np.int32(w), np.int32(h), self.P))
        launch_2d(self._kc, W, H, (src, out, self.L, np.int32(W), np.int32(H), np.int32(w), np.int32(h),
                                   np.float32(float(p["burn"]))))
