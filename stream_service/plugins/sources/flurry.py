"""Flurry: luminous smoke streams spun off by orbiting sparks, the classic Mac OS X screensaver at 4K on the GPU.

Source: https://www.jwz.org/xscreensaver/ (xscreensaver-6.17 hacks/glx/flurry.c, flurry-smoke.c, flurry-spark.c,
        flurry-star.c; originally the Mac OS X "Flurry" screensaver)
Author: Calum Robinson (Flurry; xscreensaver flurry.c, flurry-smoke.c, flurry-spark.c)
License: BSD-3-Clause
license_class: permissive
Ported from C/OpenGL by smfx (ideas/changes: the star / spark orbits, puff launch, per-spark inverse-square gravity
        with the own-stream bias, drag, speed kill and the 2.5 s puff life are Flurry's maths in flurry units / 1000;
        the puffs live in a GPU ring buffer (spawn + update kernels, sub-frame emission times, substepped
        semi-implicit Euler, frame-rate independent); each puff draws its per-frame path as an additive splat3d
        segment into a float accumulator that fades like Flurry's black-quad persistence, and a bloom + wide halo
        stands in for the smoke texture; colour is the shared cosine palette (birth hue per stream / time / puff +
        age shift + drift) instead of Flurry's fixed colour modes; energy, pulse, stream easing, spark chaos and a
        dihedral mirror were added.)

Copyright (c) 2002, Calum Robinson
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

* Redistributions of source code must retain the above copyright notice, this
  list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright notice,
  this list of conditions and the following disclaimer in the documentation
  and/or other materials provided with the distribution.

* Neither the name of the author nor the names of its contributors may be used
  to endorse or promote products derived from this software without specific
  prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Model (units: Flurry units / 1000; camera at the origin looking down +z with Flurry's 4:3 vertical field):
  * the star (emitter) wanders near the centre at z ~ 2 on Flurry's Lissajous orbit (cos 11, sin 12, cos 23 of
    the field angle, two extra rotations);
  * up to 32 sparks orbit far wider on the same formula (field range 1, seraph distance 2);
  * each emission releases one puff per stream at the star, inheriting -0.1 x the star velocity and launched
    towards its own spark at the stream speed (with a little incoherence);
  * every puff falls towards every spark (inverse square, its own spark x8), drags (0.9965^(85 dt)), dies past
    5 units/s or at the end of its life.
  Puffs draw their path segment each frame (deposit per unit length) into a persistent accumulator; mirror copies
  are model-matrix redraws of the same segments.

Macros:
  energy -> stream count x(0.55 + 0.9 e^1.2) (slewed, the newest stream fading in), spark orbit rate
            x(0.45 + 1.1 e), star rate x(0.5 + e), emission rate x(0.5 + e^1.3), launch speed x(0.8 + 0.4 e),
            bloom x(0.8 + 0.4 e), exposure x(0.8 + 0.3 e).
  pulse  -> an emission burst (rate x(1 + 2 pulse), launch speed x(1 + 0.6 pulse), new puffs up to 1.8x as
            bright: a surge that travels down every ribbon) plus a <= +30% exposure kick.
  speed  -> sim time: orbits, puff motion and ageing, colour phase and trail fade all scale with it.
  palette, palette_period, drift_speed -> pal((birth hue + age shift) x period + drift); birth hue = per-stream
            hue + colour-mode time phase + per-puff jitter (colour_mode picks the weights).
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.splat3d import Splat3D, look_at, mat_mul, mat_rot, mat_scale
from stream_service.plugins._lib.upsample import internal_size

MAXS = 32                       # max streams (sparks)
N_RING = 1 << 15                # puff ring buffer (power of two)
OPTIONS = ("--std=c++14", "--use_fast_math")
RPS = 2.0 * math.pi * 12.0 / 16384.0          # Flurry's field rotation (rad / s)
K_GRAV = 1.5e6 * 42.5 * 1e-9                  # gravity x frame-rate modifier, in (units/1000)^3 / s^2
STREAM_BIAS = 7.0
DRAG = -85.0 * math.log(0.9965)               # per second
KILL_V2 = 25.0                                # (5000 units / s)^2
LAUNCH = 0.45                                 # stream speed (450 units / s)
INHERIT = -0.1                                # 5 x (old - new) star position per ~50 fps frame
BASE_RATE = 60.0                              # releases per second per stream (one per frame in the original)
TAN_HALF_V = 0.375                            # Flurry's 1024 x 768 view: sx = x W / z
I0 = 0.55                                     # segment intensity (energy per ref px of path)
WARM_S = 3.0                                  # setup prewarm (sim seconds)
COLOUR_MODES = ("classic", "tiedye", "rainbow", "insane")
# per mode: (hue phase rate /s, per-stream hue spread, per-puff jitter, age hue shift /s)
_MODE_W = {"classic": (0.10, 0.14, 0.03, 0.05), "tiedye": (0.035, 1.0, 0.04, 0.10),
           "rainbow": (0.65, 0.06, 0.02, 0.0), "insane": (0.30, 0.6, 0.75, 0.45)}

# P layout (floats)
#  0 dts | 1 substeps | 2 h | 3 gravity | 4 own-stream bias | 5 drag | 6 kill v^2 | 7 spark loop bound
#  8..10 star old | 11..13 star new | 14 launch speed | 15 incoherence | 16 emission counter frac | 17 counter delta
#  23 life | 24 stream hue spread | 26 puff jitter | 27 age hue rate | 28 hue phase old | 29 hue phase delta
#  30 birth brightness | 31 brightness incoherence | 32 inherit | 33 chaos fan-out
_SRC = HELPERS + r"""
#define MAXS %(maxs)d
#define NMASK %(nmask)du
__device__ __forceinline__ float frc(float x) { return x - floorf(x); }

extern "C" __global__
void fl_spawn(float4* __restrict__ POS, float4* __restrict__ VEL, float4* __restrict__ ATTR,
              const float* __restrict__ P, const float4* __restrict__ SP, const int n_new, const int S,
              const unsigned int head, const unsigned int gid0, const unsigned int seed)
{
    const int t = blockIdx.x * blockDim.x + threadIdx.x;
    if (t >= n_new) return;
    const int e = t / S, j = t - e * S;
    const unsigned int slot = (head + (unsigned int)t) & NMASK;
    const unsigned int id = gid0 + (unsigned int)t;
    const float dts = P[0];
    const float f = clamp01((1.f + (float)e - P[16]) / fmaxf(P[17], 1e-6f));     // sub-frame release time
    const float sox = P[8], soy = P[9], soz = P[10], snx = P[11], sny = P[12], snz = P[13];
    const float stx = sox + (snx - sox) * f, sty = soy + (sny - soy) * f, stz = soz + (snz - soz) * f;
    const float4 a = SP[j], b = SP[MAXS + j];
    const float wj = a.w + (b.w - a.w) * f;
    const float kx = a.x + (b.x - a.x) * f, ky = a.y + (b.y - a.y) * f, kz = a.z + (b.z - a.z) * f;
    const float h1 = hash1(id, 1u, seed), h2 = hash1(id, 2u, seed), h3 = hash1(id, 3u, seed);
    const float h4 = hash1(id, 4u, seed), h5 = hash1(id, 5u, seed), h6 = hash1(id, 6u, seed);
    const float h7 = hash1(id, 7u, seed), h8 = hash1(id, 8u, seed);
    const float inh = dts > 0.f ? P[32] / dts : 0.f;
    float vx = (snx - sox) * inh, vy = (sny - soy) * inh, vz = (snz - soz) * inh;
    const float dx = stx - kx, dy = sty - ky, dz = stz - kz;
    const float r = sqrtf(dx * dx + dy * dy + dz * dz) + 1e-4f;
    const float coh = fmaxf(0.f, 1.f + P[15] * (h1 + h2 - 1.f) * 1.5f);
    const float L = P[14] * coh / r;
    vx -= dx * L; vy -= dy * L; vz -= dz * L;
    const float fan = 2.f * P[14] * P[33];                                         // spark chaos: wispier fans
    vx += fan * (h3 - 0.5f); vy += fan * (h4 - 0.5f); vz += fan * (h5 - 0.5f);
    const float life = P[23] * (0.9f + 0.2f * h6);
    const float cidx = P[24] * frc((float)j * 0.6180340f + 0.11f) + P[28] + f * P[29] + P[26] * (h7 - 0.5f);
    const float br = wj * 0.85f * (1.f + P[31] * (h8 - 0.5f)) * P[30];
    POS[slot] = make_float4(stx, sty, stz, br);
    VEL[slot] = make_float4(vx, vy, vz, wj > 1e-3f ? -f * dts : 2e9f);         // age < 0: born mid-frame
    ATTR[slot] = make_float4((float)j, cidx, P[27], life);
}

extern "C" __global__
void fl_update(float4* __restrict__ POS, float4* __restrict__ VEL, const float4* __restrict__ ATTR,
               float4* __restrict__ P0, float4* __restrict__ P1, float* __restrict__ WT, float* __restrict__ IX,
               const float* __restrict__ P, const float4* __restrict__ SP, const int N)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= N) return;
    const float4 v4 = VEL[i];
    float age = v4.w;
    const float4 dead = make_float4(0.f, 0.f, -1.f, 0.f);
    if (!(age < 1e8f)) { P0[i] = dead; P1[i] = dead; WT[i] = 0.f; return; }
    const float4 at = ATTR[i];
    const float4 p4 = POS[i];
    float x = p4.x, y = p4.y, z = p4.z, vx = v4.x, vy = v4.y, vz = v4.z;
    const float x0 = x, y0 = y, z0 = z;
    const float dts = P[0], h = P[2], G = P[3], bias = 1.f + P[4], drag = P[5];
    const int ns = (int)P[1], nk = (int)P[7], js = (int)at.x;
    const float tb = -age;                                       // birth time within this frame (<= 0: earlier)
    const float idts = dts > 0.f ? 1.f / dts : 0.f;
    for (int s = 0; s < ns; ++s) {
        const float t0 = s * h, t1 = t0 + h;
        const float he = fminf(t1 - fmaxf(t0, tb), h);
        if (he <= 0.f) continue;
        const float u = fminf(t1 * idts, 1.f);
        float ax = 0.f, ay = 0.f, az = 0.f;
        for (int j = 0; j < nk; ++j) {
            const float4 a = SP[j], b = SP[MAXS + j];
            const float wj = a.w + (b.w - a.w) * u;
            if (wj <= 0.f) continue;
            const float dx = x - (a.x + (b.x - a.x) * u), dy = y - (a.y + (b.y - a.y) * u);
            const float dz = z - (a.z + (b.z - a.z) * u);
            const float ir = rsqrtf(dx * dx + dy * dy + dz * dz + 2.5e-4f);
            float fk = G * wj * ir * ir * ir;
            if (j == js) fk *= bias;
            ax -= dx * fk; ay -= dy * fk; az -= dz * fk;
        }
        vx += ax * he; vy += ay * he; vz += az * he;                 // semi-implicit Euler
        const float dr = __expf(-drag * he);
        vx *= dr; vy *= dr; vz *= dr;
        x += vx * he; y += vy * he; z += vz * he;
    }
    age += dts;
    const float v2 = vx * vx + vy * vy + vz * vz;
    if (age > at.w || !(v2 < P[6])) {                               // end of life, or flung off a spark
        VEL[i] = make_float4(0.f, 0.f, 0.f, 2e9f);
        P0[i] = dead; P1[i] = dead; WT[i] = 0.f;
        return;
    }
    POS[i] = make_float4(x, y, z, p4.w);
    VEL[i] = make_float4(vx, vy, vz, age);
    P0[i] = make_float4(x0, y0, z0, 1.f);
    P1[i] = make_float4(x, y, z, 1.f);
    const float a = clamp01(age / at.w);
    // Flurry's brightness falls as the puff widens (1.375 - width / max width); smooth birth and death
    WT[i] = p4.w * smooth(-0.001f, 0.06f, age) * (1.f - 0.55f * a) * smooth(1.f, 0.78f, a);
    IX[i] = at.y + at.z * fmaxf(age, 0.f);
}
""" % {"maxs": MAXS, "nmask": N_RING - 1}


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _orbit(theta: float, mys, amp: float, cf_off: float) -> np.ndarray:
    """Flurry's star / spark position (UpdateStar / UpdateSpark) for field angle theta; mys: (n,) -> (n, 3)."""
    mys = np.asarray(mys, np.float64)
    m = 2.0 * math.pi * mys / 1800.0
    cf = (math.cos(7.0 * theta) + math.cos(3.0 * theta) + math.cos(13.0 * theta)) / 6.0 + cf_off
    x = amp * cf * np.cos(11.0 * (m + 3.0 * theta))
    y = amp * cf * np.sin(12.0 * (m + 4.0 * theta))
    z = amp * np.cos(23.0 * (m + 12.0 * theta))
    r = theta * 0.501 + 5.01 * mys / 1800.0
    c, s = np.cos(r), np.sin(r)
    x1, y1, z1 = x * c - y * s, y * c + x * s, z
    x2, y2, z2 = x1 * c - z1 * s, y1, z1 * c + x1 * s
    x3, y3, z3 = x2, y2 * c - z2 * s, z2 * c + y2 * s + 2000.0
    r = theta * 2.501 + 85.01 * mys / 1800.0
    c, s = np.cos(r), np.sin(r)
    return np.stack([x3 * c - y3 * s, y3 * c + x3 * s, z3], axis=-1) * 1e-3


class Flurry(Source):
    name = "flurry"
    label = "Flurry"
    description = "Luminous smoke streams spun off by orbiting sparks: thousands of glowing puffs drawn as ribbons of light."
    params = {
        "stream_count": num(1, 16, 5, "Streams", step=0.1, tier="regional", basic=True),
        "spark_chaos": num(0.0, 1.0, 0.15, "Spark chaos", tier="regional"),
        "puff_size": num(0.4, 2.5, 1.0, "Puff size (smoke width)", step=0.05, tier="regional"),
        "fade_time": num(0.8, 6.0, 2.5, "Fade time (puff life)", step=0.05, unit="s", tier="regional"),
        "colour_mode": enum(COLOUR_MODES, "classic", "Colour mode", tier="regional"),
        "mirror_symmetry": num(1.0, 6.0, 1.0, "Mirror symmetry", step=0.05, tier="regional", basic=True),
        "stream_speed": num(0.4, 2.5, 1.0, "Stream speed", step=0.05, unit="×", tier="regional"),
        "gravity": num(0.3, 3.0, 1.0, "Spark gravity", step=0.05, unit="×", tier="regional"),
        "glow": num(0.0, 2.5, 1.2, "Smoke glow", step=0.05, tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        seed = int(params["seed"])
        self.kseed = np.uint32(seed * 2654435761 % (1 << 32))
        rng = np.random.default_rng(seed)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=1024, max_lines=N_RING)
        self.cam = look_at((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0),
                           fov_deg=2.0 * math.degrees(math.atan(TAN_HALF_V)), near=0.02)
        f4 = lambda: cp.zeros((N_RING, 4), cp.float32)   # noqa: E731
        self.POS, self.ATTR, self.P0, self.P1 = f4(), f4(), f4(), f4()
        vel = np.zeros((N_RING, 4), np.float32)
        vel[:, 3] = 2e9                                   # all dead
        self.VEL = cp.asarray(vel)
        self.P0[:, 2] = -1.0
        self.P1[:, 2] = -1.0
        self.WT = cp.zeros(N_RING, cp.float32)
        self.IX = cp.zeros(N_RING, cp.float32)
        self.Ph = np.zeros(64, np.float32)
        self.P = cp.zeros(64, cp.float32)
        self.SPh = np.zeros((2, MAXS, 4), np.float32)
        self.SP = cp.zeros((2, MAXS, 4), cp.float32)
        mod = raw_module(_SRC, OPTIONS)
        self.k_spawn = mod.get_function("fl_spawn")
        self.k_update = mod.get_function("fl_update")
        # Flurry state: random start time, star mystery, spark mysteries 1800 (i + 1) / 13
        t0 = float(rng.uniform(0.0, 300.0))
        self.th_field = RPS * t0
        self.th_star = RPS * t0
        self.star_mys = np.array([rng.uniform(0.0, 10.0)])
        self.spark_mys = 1800.0 * (np.arange(MAXS) + 1.0) / 13.0
        # spark chaos: smooth per-spark wobble (3 sines per axis, host-integrated clock)
        self.ch_w = rng.uniform(0.5, 1.7, (MAXS, 3, 3))
        self.ch_p = rng.uniform(0.0, 2.0 * math.pi, (MAXS, 3, 3))
        self.ph_chaos = float(rng.uniform(0.0, 100.0))
        self.cphase = float(rng.uniform(0.0, 1.0))
        self.drift = 0.0
        self.e_slow = float(params["energy"])
        self.em_acc = 0.0
        self.gid = 0
        self.sim_t = 0.0
        self.hist: deque = deque()
        self.dts = 0.0
        self.n_eff = 1.0
        self.copies: list = []
        self.star = _orbit(self.th_star, self.star_mys, 250.0, 0.75)[0]
        self.SPh[1] = self._sparks(params, self.e_slow)
        self.SPh[0] = self.SPh[1]
        # prewarm: run the flurry for a few seconds so the scene arrives already streaming
        n = int(WARM_S * 60)
        for k in range(n):
            self._advance(params, 1.0 / 60.0)
            if k >= n - 50:
                self._draw(params)
                self.sp.flush()
        cp.cuda.get_current_stream().synchronize()

    # ------------------------------------------------------------------ host maths
    def _sparks(self, p: dict, es: float) -> np.ndarray:
        """Spark positions (k-units) + weights at the current field angle -> (MAXS, 4)."""
        out = np.zeros((MAXS, 4), np.float32)
        pos = _orbit(self.th_field, self.spark_mys, 1000.0, 2.0)
        chaos = float(p["spark_chaos"])
        if chaos > 0.0:
            wob = np.sin(self.ch_w * self.ph_chaos + self.ch_p).sum(axis=2) / 3.0     # (MAXS, 3)
            pos = pos + 0.9 * chaos * wob
        n_eff = min(max(float(p["stream_count"]) * (0.55 + 0.9 * es ** 1.2), 1.0), float(MAXS))
        self.n_eff = n_eff
        out[:, :3] = pos
        out[:, 3] = np.clip(n_eff - np.arange(MAXS), 0.0, 1.0)
        return out

    def _advance(self, p: dict, dt: float) -> None:
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        dt = min(max(float(dt), 0.0), 0.1)
        dts = dt * spd
        self.dts = dts
        if dt > 0.0:
            self.e_slow += (e - self.e_slow) * (1.0 - math.exp(-dt / 0.8))
        es = self.e_slow
        # integrated phases (rates only)
        star_old = self.star
        self.th_star += RPS * (0.5 + 1.0 * e) * dts
        self.th_field += RPS * (0.45 + 1.1 * e) * dts
        self.ph_chaos = _wrap(self.ph_chaos + (0.35 + 0.5 * e) * dts, 2.0e4)
        mode = _MODE_W.get(str(p["colour_mode"]), _MODE_W["classic"])
        dc = mode[0] * dts
        c_old = self.cphase
        self.cphase = _wrap(self.cphase + dc, 1000.0)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * dts, 1000.0)
        self.star = _orbit(self.th_star, self.star_mys, 250.0, 0.75)[0]
        self.SPh[0] = self.SPh[1]
        self.SPh[1] = self._sparks(p, es)
        nk = int(max(np.count_nonzero(self.SPh[0, :, 3] > 0.0), np.count_nonzero(self.SPh[1, :, 3] > 0.0)))
        S = int(math.ceil(self.n_eff - 1e-6))
        # emission: integrated counter, one puff per stream per release
        life = float(p["fade_time"])
        rate = BASE_RATE * (0.5 + 1.0 * e ** 1.3) * (1.0 + 2.0 * pulse)
        rate = min(rate, 0.9 * N_RING / (S * life * 1.1))
        dacc = rate * dts
        frac0 = self.em_acc
        tot = frac0 + dacc
        n_em = int(math.floor(tot))
        self.em_acc = tot - n_em
        n_em = min(n_em, 48)
        n_new = n_em * S
        # sub-steps (<= 1/120 s)
        ns = 0 if dts <= 0.0 else min(12, max(1, int(math.ceil(dts * 120.0 - 1e-6))))
        P = self.Ph
        P[0] = dts
        P[1] = ns
        P[2] = dts / ns if ns else 0.0
        P[3] = K_GRAV * float(p["gravity"])
        P[4] = STREAM_BIAS
        P[5] = DRAG
        P[6] = KILL_V2
        P[7] = nk
        P[8:11] = star_old
        P[11:14] = self.star
        P[14] = LAUNCH * float(p["stream_speed"]) * (0.8 + 0.4 * e) * (1.0 + 0.6 * pulse)
        chaos = float(p["spark_chaos"])
        P[15] = 0.07 + 0.8 * chaos
        P[16] = frac0
        P[17] = dacc
        P[23] = life
        P[24], P[26], P[27] = mode[1], mode[2], mode[3]
        P[28] = c_old
        P[29] = self.cphase - c_old if self.cphase >= c_old else dc
        P[30] = 1.0 + 0.8 * pulse
        P[31] = 0.3 + 0.4 * chaos
        P[32] = INHERIT
        P[33] = 0.12 * chaos
        self.P.set(P)
        self.SP.set(self.SPh)
        if n_new > 0:
            self.k_spawn(((n_new + 127) // 128,), (128,),
                         (self.POS, self.VEL, self.ATTR, self.P, self.SP, np.int32(n_new), np.int32(S),
                          np.uint32(self.gid & (N_RING - 1)), np.uint32(self.gid), self.kseed))
        self.k_update(((N_RING + 255) // 256,), (256,),
                      (self.POS, self.VEL, self.ATTR, self.P0, self.P1, self.WT, self.IX, self.P, self.SP,
                       np.int32(N_RING)))
        # alive window of the ring (puffs older than their life are dead): spawn history of (time, gid, life)
        if n_new > 0:
            self.hist.append((self.sim_t, self.gid, life * 1.15 + 0.1))
            self.gid = (self.gid + n_new) & 0xFFFFFFFF
        self.sim_t += dts
        while self.hist and self.hist[0][0] + self.hist[0][2] < self.sim_t:
            self.hist.popleft()
        # symmetry copies: rotations at 2 pi i / s (last one fading in) + mirror twins fading in over s in [1, 2]
        s = min(max(float(p["mirror_symmetry"]), 1.0), 6.0)
        nr = int(math.ceil(s - 1e-6))
        mw = min(max(s - 1.0, 0.0), 1.0)
        mirror = mat_scale((-1.0, 1.0, 1.0))
        cops = []
        for i in range(nr):
            w = 1.0 if i < int(math.floor(s + 1e-6)) else s - math.floor(s)
            if w <= 1e-3:
                continue
            R = mat_rot((0.0, 0.0, 1.0), 2.0 * math.pi * i / s)
            cops.append((None if i == 0 else R, w))
            if mw > 1e-3:
                cops.append((mat_mul(R, mirror), w * mw))
        self.copies = cops

    def _draw(self, p: dict) -> None:
        e = min(max(float(p["energy"]), 0.0), 1.0)
        sp = self.sp
        tau = 0.08 * float(p["fade_time"])
        sp.fade(math.exp(-self.dts / tau) if self.dts > 0.0 else 1.0)
        live = len(self.hist)
        if not live:
            return
        lo = self.hist[0][1]
        cnt = (self.gid - lo) & 0xFFFFFFFF
        cnt = min(cnt, N_RING)
        if cnt <= 0:
            return
        a = lo & (N_RING - 1)
        spans = [(a, a + cnt)] if a + cnt <= N_RING else [(a, N_RING), (0, a + cnt - N_RING)]
        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        rate_n = 0.5 + 1.0 * e ** 1.3
        wsum = sum(w for _, w in self.copies) or 1.0
        inten = (I0 / math.sqrt(rate_n) / math.sqrt(max(tau, 0.02) / 0.2)
                 * (max(self.n_eff, 1.0) / 5.0) ** -0.3 / math.sqrt(wsum))
        width = min(1.3 * float(p["puff_size"]), 3.0)
        for model, w in self.copies:
            for s0, s1 in spans:
                sp.lines(self.P0[s0:s1], self.P1[s0:s1], n=s1 - s0, cam=self.cam, model=model,
                         intensity=inten * w, width_px=width, size_range=(0.6, 4.0), depth_ref=2.0,
                         index=self.IX[s0:s1], weight=self.WT[s0:s1], palette=coeffs,
                         period=float(p["palette_period"]), drift=self.drift, near_fade=(0.06, 0.35))

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._advance(p, dt)
        self._draw(p)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        exposure = float(p["intensity"]) * (0.8 + 0.3 * e) * (1.0 + 0.3 * pulse)
        ps = float(p["puff_size"])
        self.sp.resolve(out, exposure=exposure, tonemap="aces", saturation=1.12,
                        glow=float(p["glow"]) * (0.8 + 0.4 * e), glow_radius_px=20.0 * ps, glow_wide=0.55)

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        self.POS = self.VEL = self.ATTR = self.P0 = self.P1 = self.WT = self.IX = self.P = self.SP = None
