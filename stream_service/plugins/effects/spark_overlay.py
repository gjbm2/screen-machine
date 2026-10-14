"""Spark shower: sparks and embers thrown off the picture.  Highlights literally throw sparks on the kick.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive

How it works.  A pool of ~49k particles lives on the GPU (state buffers allocated in setup).  Each frame:
  1. ``spark_luma`` reduces the input to a 1/16-res luma map (mean + max of a 4x4 tap grid per cell, so small
     highlights count) and its maximum (warp-reduced atomicMax, never read back to the host).
  2. ``spark_update`` (one thread per particle) integrates live sparks with semi-implicit Euler: gravity (fall) or
     buoyancy (rise), linear drag that relaxes the velocity toward a curl-noise wind (2 octaves of the stream
     function's curl, divergence free, evolving with a host-integrated phase), and kills sparks that age out or
     leave the frame.  Dead slots respawn by an in-kernel hash (no cupy.random): a per-frame hazard set on the host
     so ``density`` is the steady-state live fraction of the pool, plus a burst on a host-detected beat edge.  The
     emitter is a continuous morph across three sources: the bottom edge (a fountain / fire from below), the
     picture's highlights (rejection sampling of the luma map normalised to its maximum, 8 tries) and the whole
     frame uniformly.  It then writes each spark's motion streak (p - v * streak .. p, a comet ramp in w) and its
     colour: white-hot at birth, cooling through the palette (index drifts with age) to dark.
  3. splat3d ``lines`` draws the streaks (three size classes: fine dust, sparks, big embers) into a half-res HDR
     accumulator, and ``resolve`` glows, tonemaps and screen-blends them straight onto the 4K input.

amount: emission rate and splat alpha scale with m = smoothstep(amount); amount <= 1e-4 copies the input exactly
(existing sparks keep simulating invisibly and die out).  pulse: a rising edge spawns a burst from the emitters
with a launch-velocity kick (and lifts brightness ~35%); it never moves existing sparks.  dt = 0: no integration;
dt > 0.5 s: the pool resets and prewarms (sparks seeded mid-flight on their analytic ballistic paths).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.splat3d import SPLAT3D_OPTIONS, SPLAT3D_SRC, Splat3D, look_at
from stream_service.plugins._lib.upsample import internal_size

N_POOL = 49152
CAM_D = 4.0                      # eye at (0, 0, D) looking -z: the z = 0 plane spans x in [-A, A], y in [-1, 1]
REF_UNIT = 540.0                 # ref px (1080-line) per world unit (screen half height = 1 unit)
NP = 48
# P layout (floats)
#  0 A | 1 dt | 2 gravity accel (+ down) | 3 drag k | 4 wind strength | 5 noise scale | 6 noise phase
#  7 spawn prob per dead slot | 8 burst fraction | 9 launch speed | 10 burst launch mul | 11 launch mul
#  12 life | 13..15 emitter weights (edge, highlights, uniform) | 16 streak s | 17 min len | 18 norm len
#  19 palette period | 20 drift | 21 prewarm fraction | 22 reset | 23 lmax floor | 24 highlight threshold
#  25 brightness | 26..37 palette | 38 rise | 39 cooling shift | 40 tail weight

_SRC = PALETTE_CUDA + SPLAT3D_SRC + r"""
__device__ __forceinline__ float2 spk_wind(float x, float y, float ph, float sc, unsigned int seed) {
    const float4 a = sp_vnoise_d(sp_f3(x * sc, y * sc, ph), seed);
    const float4 b = sp_vnoise_d(sp_f3(x * sc * 2.13f + 5.2f, y * sc * 2.13f - 3.1f, ph * 1.7f + 11.f), seed + 77u);
    // curl of a scalar stream function psi: (dpsi/dy, -dpsi/dx)
    return make_float2(a.z + 0.5f * b.z, -(a.y + 0.5f * b.y));
}

// pick an emitter and a launch; false if the highlight sampler found nothing bright enough this frame
__device__ bool spk_emit(unsigned int key, const float* __restrict__ P, const float* __restrict__ lmap, float lnorm,
                         int lw, int lh, float lmul, float& x, float& y, float& vx, float& vy)
{
    const float A = P[0];
    const float wE = P[13], wH = P[14], wU = P[15];
    const float sel = sp_hashf(key, 1u) * (wE + wH + wU + 1e-6f);
    float spd = P[9] * lmul * (0.45f + 0.9f * sp_hashf(key, 2u));
    float ang;
    if (sel < wE) {                                       // bottom edge: a fountain / fire from below
        x = (sp_hashf(key, 3u) * 2.f - 1.f) * A;
        y = -0.995f;
        ang = 1.5707963f + (sp_hashf(key, 4u) - 0.5f) * 0.8f;
        spd *= 1.2f + 0.5f * P[38];
    } else if (sel < wE + wH) {                           // highlights: rejection-sample the luma map
        bool ok = false;
        for (int k = 0; k < 8; ++k) {
            const float3 h = sp_hash3(key, 100u + (unsigned int)k);
            const int cx = min((int)(h.x * lw), lw - 1), cy = min((int)(h.y * lh), lh - 1);
            const float l = lmap[cy * lw + cx] * lnorm;
            const float acc = sp_smooth(P[24], 1.f, l);
            if (h.z < acc * acc) {
                const float3 j = sp_hash3(key, 200u + (unsigned int)k);
                x = ((cx + j.x) / (float)lw * 2.f - 1.f) * A;
                y = 1.f - (cy + j.y) / (float)lh * 2.f;
                ok = true;
                break;
            }
        }
        if (!ok) return false;
        ang = 1.5707963f + (sp_hashf(key, 5u) - 0.5f) * 2.7f;
    } else {                                              // anywhere: a spray in every direction
        x = (sp_hashf(key, 6u) * 2.f - 1.f) * A;
        y = sp_hashf(key, 7u) * 2.f - 1.f;
        ang = sp_hashf(key, 8u) * 6.2831853f;
        spd *= 0.75f;
    }
    float s, c;
    __sincosf(ang, &s, &c);
    vx = c * spd; vy = s * spd;
    return true;
}

extern "C" __global__ void spark_luma(const uchar4* __restrict__ src, const int W, const int H,
                                      float* __restrict__ lmap, const int lw, const int lh, unsigned int* lmax)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    float l = 0.f;
    if (x < lw && y < lh) {
        const float bx = (float)W / lw, by = (float)H / lh;
        float sum = 0.f, mx = 0.f;
        for (int j = 0; j < 4; ++j) {
            const int py = min((int)((y + (j + 0.5f) * 0.25f) * by), H - 1);
            for (int i = 0; i < 4; ++i) {
                const int px = min((int)((x + (i + 0.5f) * 0.25f) * bx), W - 1);
                const uchar4 c = src[(size_t)py * W + px];
                const float v = (0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z) * (1.f / 255.f);
                sum += v; mx = fmaxf(mx, v);
            }
        }
        l = 0.5f * sum * (1.f / 16.f) + 0.5f * mx;
        lmap[y * lw + x] = l;
    }
    for (int o = 16; o > 0; o >>= 1) l = fmaxf(l, __shfl_xor_sync(0xffffffffu, l, o));
    if ((threadIdx.x & 31) == 0 && l > 0.f) atomicMax(lmax, __float_as_uint(l));
}

extern "C" __global__ void spark_update(float4* __restrict__ S0, float4* __restrict__ S1, float4* __restrict__ Q0,
                                        float4* __restrict__ Q1, float* __restrict__ RGB,
                                        const float* __restrict__ lmap, const unsigned int* __restrict__ lmax,
                                        const int lw, const int lh, const int N, const unsigned int seed,
                                        const unsigned int tick, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= N) return;
    const float A = P[0], dt = P[1], k = P[3], g = P[2];
    const float4 s = S0[i];
    float4 q = S1[i];
    float x = s.x, y = s.y, vx = s.z, vy = s.w, age = q.x, life = q.y;
    // size classes (must match the host draw split): fine dust 55 %, sparks 30 %, embers 15 %
    const int cls = (i * 20 < N * 11) ? 0 : ((i * 20 < N * 17) ? 1 : 2);
    const float lifeMul = cls == 0 ? 0.75f : (cls == 1 ? 1.0f : 1.5f);
    const float spdMul = cls == 0 ? 1.15f : (cls == 1 ? 1.0f : 0.72f);
    bool alive = life > 0.f && age < life && P[22] < 0.5f;
    if (alive && dt > 0.f) {
        const float2 w = spk_wind(x, y, P[6], P[5], seed);
        const float wx = w.x * P[4], wy = w.y * P[4];
        const float id = 1.f / (1.f + k * dt);          // drag relaxes v toward the wind (implicit)
        vx = (vx + dt * k * wx) * id;
        vy = (vy + dt * (k * wy - g)) * id;
        x += vx * dt; y += vy * dt; age += dt;
        if (age >= life || y < -1.2f || y > 1.25f || fabsf(x) > A + 0.2f) alive = false;
    }
    if (!alive) {
        const unsigned int key = sp_hash(seed * 0x9E3779B1u ^ sp_hash((unsigned int)i * 0x85EBCA77u + tick * 0xC2B2AE3Du));
        const float r = sp_hashf(key, 11u);
        const bool warm = P[21] > 0.f;
        const bool norm = r < (warm ? P[21] : P[7]);
        const bool burst = !norm && !warm && P[8] > 0.f && sp_hashf(key, 12u) < P[8];
        if (norm || burst) {
            const float lnorm = 1.f / fmaxf(__uint_as_float(lmax[0]), P[23]);
            const float lmul = spdMul * (burst ? P[10] : P[11]);
            if (spk_emit(key, P, lmap, lnorm, lw, lh, lmul, x, y, vx, vy)) {
                life = P[12] * lifeMul * (0.55f + 0.9f * sp_hashf(key, 13u));
                age = 0.f;
                q.z = sp_hashf(key, 14u);                 // colour index
                if (warm) {                               // seed mid-flight on the drag + gravity path
                    const float ta = life * sp_hashf(key, 15u), kk = fmaxf(k, 1e-3f);
                    const float e = __expf(-kk * ta), ay = -g, vt = ay / kk;
                    x += vx * (1.f - e) / kk;
                    y += vt * ta + (vy - vt) * (1.f - e) / kk;
                    vx *= e; vy = vy * e + vt * (1.f - e);
                    age = ta;
                }
                alive = !(y < -1.2f || y > 1.25f || fabsf(x) > A + 0.2f);
            }
        }
    }
    S0[i] = make_float4(x, y, vx, vy);
    S1[i] = make_float4(age, alive ? life : 0.f, q.z, q.w);
    if (!alive) {                                         // behind the camera: the line binner returns at once
        const float4 off = make_float4(0.f, 0.f, 1.0e4f, 0.f);
        Q0[i] = off; Q1[i] = off;
        RGB[3 * i] = 0.f; RGB[3 * i + 1] = 0.f; RGB[3 * i + 2] = 0.f;
        return;
    }
    const float u = fminf(age / fmaxf(life, 1e-4f), 1.f), h = 1.f - u;
    const float3 ps = pal(q.z * P[19] + P[20] + u * P[39], P + 26);
    float3 c = sp_f3(sp_lin(ps.x), sp_lin(ps.y), sp_lin(ps.z));
    const float hot = sp_smooth(0.6f, 1.f, h);
    c = sp_mix3(c, sp_f3(1.f, 0.93f, 0.82f), 0.85f * hot);              // white-hot at birth
    c = sp_mix3(sp_mul3(c, 0.6f), c, sqrtf(h));                         // dims as it cools
    const float b = P[25] * sp_smooth(0.f, 0.03f, u) * (__powf(h, 1.4f) + 1.6f * hot * hot);
    float sx = vx * P[16], sy = vy * P[16];
    float L = sqrtf(sx * sx + sy * sy);
    if (L < P[17]) {                                      // minimum streak so slow embers still deposit light
        const float sp = sqrtf(vx * vx + vy * vy);
        if (sp > 1e-5f) { sx = vx / sp * P[17]; sy = vy / sp * P[17]; } else { sx = P[17]; sy = 0.f; }
        L = P[17];
    }
    const float nl = sqrtf(P[18] / fmaxf(L, P[18]));      // long streaks are dimmer per pixel (motion blur)
    Q1[i] = make_float4(x, y, 0.f, 1.f);
    Q0[i] = make_float4(x - sx, y - sy, 0.f, P[40]);      // comet ramp: dim tail -> bright head
    const float bb = b * nl;
    RGB[3 * i] = c.x * bb; RGB[3 * i + 1] = c.y * bb; RGB[3 * i + 2] = c.z * bb;
}
"""


def _smoothstep01(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class SparkOverlay(Effect):
    name = "spark_overlay"
    stage = "overlay"
    label = "Spark shower"
    description = ("Sparks and embers thrown off the picture: streaked additive particles spawn from its "
                   "highlights, the bottom edge or anywhere, fall or rise through curl-noise drift and cool from "
                   "white through the palette to dark; highlights throw a burst on the kick.")
    params = {
        **standard_effect_params(),
        "density": num(0.05, 1.0, 0.5, "Density", tier="regional", basic=True),
        "gravity": num(-1.0, 1.0, 0.55, "Gravity (− rise, + fall)", step=0.01, tier="regional", basic=True),
        "launch": num(0.2, 3.0, 1.3, "Launch speed", tier="regional"),
        "turbulence": num(0.0, 1.0, 0.35, "Turbulence", tier="regional"),
        "size": num(0.5, 4.0, 1.8, "Spark size", unit="px", step=0.05, tier="regional"),
        "streak_length": num(0.0, 0.12, 0.035, "Streak length", unit="s", step=0.001, tier="regional"),
        "emitter": num(0.0, 1.0, 0.5, "Emitter (edge · highlights · anywhere)", tier="regional"),
        "life": num(0.3, 3.0, 1.2, "Spark life", unit="s", tier="regional"),
        "kick": num(0.0, 2.0, 1.0, "Beat launch kick", tier="regional"),
        "glow": num(0.0, 1.5, 0.6, "Glow", tier="regional"),
        **palette_params("ember", period=0.3, drift=0.02),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, SPLAT3D_OPTIONS)
        self._kl = mod.get_function("spark_luma")
        self._ku = mod.get_function("spark_update")
        W, H = ctx.width, ctx.height
        self.A = W / H
        self.N = N_POOL
        w, h = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.sp = Splat3D(ctx, w, h, max_points=0, max_lines=self.N, pool=1 << 21)
        self.S0 = cp.zeros((self.N, 4), cp.float32)
        self.S1 = cp.zeros((self.N, 4), cp.float32)
        self.Q0 = cp.zeros((self.N, 4), cp.float32)
        self.Q1 = cp.zeros((self.N, 4), cp.float32)
        self.Q0[:, 2] = 1.0e4
        self.Q1[:, 2] = 1.0e4
        self.RGB = cp.zeros((self.N, 3), cp.float32)
        self.lw, self.lh = max(4, W // 16), max(4, H // 16)
        self.lmap = cp.zeros((self.lh, self.lw), cp.float32)
        self.lmax = cp.zeros(4, cp.uint32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        # draw classes (slices are views: no per-frame allocation on the device)
        n0, n1 = (self.N * 11 + 19) // 20, (self.N * 17 + 19) // 20
        cuts = [(0, n0, 0.55, 2.2), (n0, n1, 1.0, 3.0), (n1, self.N, 1.6, 3.6)]
        self.classes = [(self.Q0[a:b], self.Q1[a:b], self.RGB[a:b], ws, ie) for a, b, ws, ie in cuts]
        fov = math.degrees(2.0 * math.atan(1.0 / CAM_D))
        self.cam = look_at((0.0, 0.0, CAM_D), (0.0, 0.0, 0.0), fov_deg=fov)
        self.seed = np.uint32(int(params.get("seed", 1)) & 0xFFFFFFFF)
        self.tick = 0
        self.clock = 0.0
        self.noise_ph = 0.0
        self.drift = 0.0
        self.prev_pulse = 0.0
        self.last_kick = -1.0
        self.warm = True

    def _palette(self, name: str):
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        return np.asarray(PALETTES[name] if rows is None else rows, np.float32).reshape(-1)[:12]

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        m = _smoothstep01(a)
        dtc = min(max(float(dt), 0.0), 0.1)
        reset = dt > 0.5
        if reset:
            self.warm = True
        self.clock += max(float(dt), 0.0)
        self.tick = (self.tick + 1) & 0x7FFFFFFF
        self.noise_ph = math.fmod(self.noise_ph + 0.22 * dtc, 4096.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 10.0)

        burst = 0.0
        burst_mul = 1.0
        if pulse - self.prev_pulse > 0.15 and self.clock - self.last_kick > 0.12:
            self.last_kick = self.clock
            burst = m * float(p["density"]) * (0.12 + 0.2 * pulse)
            burst_mul = 1.0 + float(p["kick"]) * (0.6 + 0.6 * pulse)
        self.prev_pulse = pulse

        g = float(p["gravity"])
        rise = max(0.0, -g)
        turb = float(p["turbulence"])
        life = float(p["life"])
        f_t = min(0.92, float(p["density"]) * m)
        mean_life = life * 0.95
        hazard = f_t / ((1.0 - f_t) * mean_life) if f_t > 0.0 else 0.0
        pr = 1.0 - math.exp(-hazard * dtc)
        warm = 0.0
        if self.warm and m > 1e-3:
            warm = max(f_t, 1e-4)
            self.warm = False
        em = float(p["emitter"])

        hst = self.host
        hst[0] = self.A
        hst[1] = dtc
        hst[2] = g * 2.4 if g > 0.0 else g * 1.4
        hst[3] = 0.45 + 1.6 * rise + 0.5 * turb
        hst[4] = turb * (0.75 + 0.5 * rise)
        hst[5] = 1.4
        hst[6] = self.noise_ph
        hst[7] = pr
        hst[8] = burst
        hst[9] = float(p["launch"]) * (1.0 - 0.45 * rise)
        hst[10] = burst_mul * (0.75 + 0.25 * m)
        hst[11] = (0.75 + 0.25 * m) * (1.0 + 0.3 * float(p["kick"]) * pulse * m)
        hst[12] = life
        hst[13] = max(0.0, 1.0 - 2.0 * em)
        hst[14] = 1.0 - abs(2.0 * em - 1.0)
        hst[15] = max(0.0, 2.0 * em - 1.0)
        hst[16] = float(p["streak_length"])
        hst[17] = 2.0 / REF_UNIT
        hst[18] = 12.0 / REF_UNIT
        hst[19] = float(p["palette_period"])
        hst[20] = self.drift
        hst[21] = warm
        hst[22] = 1.0 if reset else 0.0
        hst[23] = 0.2
        hst[24] = 0.5
        hst[25] = 1.0
        hst[26:38] = self._palette(p["palette"])
        hst[38] = rise
        hst[39] = 0.35
        hst[40] = 0.12
        self.P.set(hst)

        W, H = self.ctx.width, self.ctx.height
        emitting = pr > 0.0 or burst > 0.0 or warm > 0.0
        if emitting:
            self.lmax.data.memset_async(0, self.lmax.nbytes)
            launch_2d(self._kl, self.lw, self.lh, (src, np.int32(W), np.int32(H), self.lmap, np.int32(self.lw),
                                                   np.int32(self.lh), self.lmax))
        self._ku(((self.N + 255) // 256,), (256,),
                 (self.S0, self.S1, self.Q0, self.Q1, self.RGB, self.lmap, self.lmax, np.int32(self.lw),
                  np.int32(self.lh), np.int32(self.N), self.seed, np.uint32(self.tick), self.P))

        if a <= 1e-4:
            cp.copyto(out, src)
            return

        alpha = m * (2.0 - m)
        gain = 1.0 + 0.35 * pulse * m
        size = float(p["size"])
        sp = self.sp
        sp.clear()
        for q0, q1, rgb, ws, ie in self.classes:
            sp.lines(q0, q1, cam=self.cam, rgb=rgb, weight_in_w=True, intensity=ie * gain,
                     width_px=min(4.0, max(0.6, size * ws)))
        sp.resolve(out, exposure=alpha, tonemap="aces", saturation=1.15, glow=float(p["glow"]) * (0.4 + 0.6 * m),
                   glow_radius_px=10.0 + 3.0 * size, glow_wide=0.35, background=src, blend="screen")

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        self.S0 = self.S1 = self.Q0 = self.Q1 = self.RGB = self.lmap = self.lmax = self.P = None
        self.classes = []
