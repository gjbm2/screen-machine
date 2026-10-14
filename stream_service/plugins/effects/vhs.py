"""VHS: a worn tape on an old tube; rolling tracking bands, static, chroma bleed, washed colour, scanlines.

Source: https://godotshaders.com/shader/vhs-and-crt-monitor-effect/ ; https://github.com/Vidvox/ISF-Files/blob/master/ISF/Bad%20TV.fs
Author: pend00 (godotshaders "VHS and CRT monitor effect"); Felix Turner (Bad TV, ISF adaptation by VIDVOX)
License: CC0-1.0 (pend00, on the godotshaders page: "This shader is under CC0 license") / MIT (Bad TV: "The MIT License,
         Copyright (c) 2014 Felix Turner", in the ISF file header)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: rewritten as one CUDA pass; the ideas kept are pend00's rolling
  tracking band + tube curvature + scanlines + vignette and Bad TV's per-line horizontal distortion; the Ashima
  simplex noise is replaced by the shared hash/value noise; chroma bleed is done properly in YIQ (sharp luma,
  6-tap smeared and lagged I/Q); grain is elongated and >= 2 px for HEVC; a head-switching strip, dropout
  streaks and a cosine-palette tape colour cast with drift were added).

Pipeline (one 4K pass):
  1. tube: barrel curvature (partly zoomed back so the picture still fills the tube), soft black outside it;
  2. lines: horizontal displacement = slow tape wobble + the rolling tracking-error band (sharp lower edge, long
     soft tail, torn per ~3 px "line") + the head-switching skew in the bottom few percent;
  3. signal: luma sampled sharp (slightly softened), I/Q averaged over 6 taps trailing to the left, so colour
     smears and lags to the right; chroma noise blotches; washed-out saturation and lifted blacks;
  4. tape: elongated static grain, dropout streaks (sparse everywhere, dense inside the band), band flare;
  5. colour cast pal(drift + period * field) multiplied in, then scanlines (lines bend with the tube) + vignette.

Realtime mapping: ``amount`` scales noise, roll, discolouring and aberration together (0 = src exactly; each on its
own curve so the morph is not a volume knob).  ``pulse`` is a tracking-error burst: roll distortion amplitude and
band size swell, the band noise flares and the roll phase lurches (rate kick into a host-integrated phase).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

# Param block layout (floats): 0..11 palette | then the scalars below.
_I = {k: 12 + i for i, k in enumerate((
    "amt", "curv", "vig", "roll_pos", "roll_size", "roll_amp", "roll_noise", "wob_amp", "wob_ph", "tear_ph",
    "bleed", "cshift", "soft", "noise", "scan", "wash", "tint", "drift", "period", "hsw", "mix"))}
_NP = 12 + len(_I)

_DEFS = "".join(f"#define Q_{k.upper()} {v}\n" for k, v in _I.items())

_SRC = HELPERS + PALETTE_CUDA + _DEFS + r"""
#define TAU 6.2831853f

// Linear interpolation along x on one clamped row (cheap horizontal taps for the luma/chroma filters).
__device__ __forceinline__ float3 hrow(const uchar4* __restrict__ img, float fx, int row, int W, int H) {
    fx -= 0.5f;
    const int x0 = (int)floorf(fx);
    const float t = fx - x0;
    const float4 a = ld(img, x0, row, W, H), b = ld(img, x0 + 1, row, W, H);
    return make_float3((a.x + (b.x - a.x) * t) * (1.f / 255.f), (a.y + (b.y - a.y) * t) * (1.f / 255.f),
                       (a.z + (b.z - a.z) * t) * (1.f / 255.f));
}
__device__ __forceinline__ float yof(float3 c) { return 0.299f * c.x + 0.587f * c.y + 0.114f * c.z; }
__device__ __forceinline__ float iof(float3 c) { return 0.596f * c.x - 0.274f * c.y - 0.322f * c.z; }
__device__ __forceinline__ float qof(float3 c) { return 0.211f * c.x - 0.523f * c.y + 0.312f * c.z; }

extern "C" __global__
void vhs(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
         const float* __restrict__ Q, const unsigned int tick, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float sc = H * (1.f / 2160.f);                 // everything is tuned in 4K pixels
    const float aspect = (float)W / (float)H;

    // ---- 1. tube curvature (barrel), zoomed back a little so the picture still fills most of the tube
    float u = (x + 0.5f) / W - 0.5f, v = (y + 0.5f) / H - 0.5f;
    const float r2 = (u * u * aspect * aspect + v * v) / (0.25f * aspect * aspect + 0.25f);   // 1 at the corners
    const float curv = Q[Q_CURV];
    const float bend = (1.f + curv * r2) / (1.f + curv * 0.3f);
    u *= bend; v *= bend;
    const float outside = fmaxf((fabsf(u) - 0.5f) * aspect, fabsf(v) - 0.5f);
    const float edge = 1.f - smooth(0.f, 0.004f, outside);
    float sx = (u + 0.5f) * W;
    const float sy = (v + 0.5f) * H;
    const float vn = v + 0.5f;                           // 0 top .. 1 bottom, in tube space

    // ---- 2. horizontal line displacement
    float db = vn - Q[Q_ROLL_POS];
    db -= floorf(db + 0.5f);                             // wrapped distance to the band centre
    const float rs = Q[Q_ROLL_SIZE];
    const float bl = db < 0.f ? -db / (rs + 1e-4f) : db / (0.22f * rs + 1e-4f);   // long tail above, sharp lower edge
    const float band = __expf(-bl * bl);
    const float row = sy / (3.f * sc);                   // tape "lines", ~3 px tall at 4K
    const float wob_ph = Q[Q_WOB_PH], tear_ph = Q[Q_TEAR_PH];
    const float wob = 2.f * (vnoise(sy / (260.f * sc), wob_ph, seed) - 0.5f)
                    + 0.45f * (vnoise(row * 0.35f, wob_ph * 5.f, seed + 11u) - 0.5f);
    float dx = Q[Q_WOB_AMP] * sc * wob;
    const float tear = vnoise(row, tear_ph, seed + 23u);
    const float tear2 = vnoise(sy / (45.f * sc), tear_ph * 0.6f, seed + 29u);
    dx += Q[Q_ROLL_AMP] * sc * band * (0.25f + 1.3f * (tear - 0.5f) + 0.9f * tear2);
    const float hs = smooth(0.955f, 0.992f, vn);         // head-switching strip at the bottom
    dx += Q[Q_HSW] * sc * hs * hs * (0.6f + 0.8f * tear);
    sx += dx;

    // ---- 3. signal: sharp (slightly soft) luma, smeared + lagged chroma
    const int ri = min(max((int)sy, 0), H - 1);
    const float4 c0 = bilin(src, sx, sy, W, H);
    const float3 cc = make_float3(c0.x * (1.f / 255.f), c0.y * (1.f / 255.f), c0.z * (1.f / 255.f));
    const float soft = Q[Q_SOFT] * sc;
    const float Yc = yof(cc);
    float Y = 0.5f * Yc + 0.25f * (yof(hrow(src, sx - soft, ri, W, H)) + yof(hrow(src, sx + soft, ri, W, H)));
    const float bleed = Q[Q_BLEED] * sc, cshift = Q[Q_CSHIFT] * sc;
    float I = 0.f, Qc = 0.f, wi = 0.f, wq = 0.f;
    #pragma unroll
    for (int i = 0; i < 6; ++i) {
        const float f = i * (1.f / 5.f);
        const float3 t = hrow(src, sx - cshift - bleed * f, ri, W, H);
        const float a = 1.f - 0.62f * f, b = 0.45f + 0.55f * f;      // I leans early, Q late -> colour fringes
        I += a * iof(t); wi += a;
        Qc += b * qof(t); wq += b;
    }
    I /= wi; Qc /= wq;

    const unsigned int ts = seed * 7919u + tick * 2654435761u;
    const float noise = Q[Q_NOISE], rn = Q[Q_ROLL_NOISE], wash = Q[Q_WASH];
    // chroma noise: wide horizontal blotches
    const float cn = noise * 0.07f + 0.05f * band * rn;
    I += cn * (vnoise(sx / (80.f * sc), sy / (5.f * sc), ts + 101u) - 0.5f);
    Qc += cn * (vnoise(sx / (80.f * sc), sy / (5.f * sc), ts + 202u) - 0.5f);
    const float sat = (1.f - 0.45f * wash) * (1.f - 0.55f * fminf(band * rn, 1.f));
    I *= sat; Qc *= sat;

    // ---- 4. tape: elongated grain (>= 2 px), dropout streaks, band flare
    const float gn = vnoise(sx / (5.f * sc), sy / (2.5f * sc), ts) - 0.5f;
    Y += gn * (0.16f * noise + 0.28f * band * rn);
    const unsigned int rg = (unsigned int)max((int)(sy / (3.f * sc)), 0);
    const float hr = hash1(rg, tick, seed + 7u);
    const float pr = 0.006f * noise + 0.22f * band * fminf(rn, 1.5f);
    if (hr < pr) {
        const float s = smooth(0.55f, 0.85f, vnoise(sx / (140.f * sc) + hr * 977.f, (float)rg, ts + 31u));
        Y += s * (0.35f + 0.4f * hash1(rg, tick, seed + 9u)) * fminf(0.6f + 0.6f * rn + 2.f * noise, 1.4f);
    }
    Y += 0.05f * band * rn;
    Y = Y * (1.f - 0.10f * wash) + 0.045f * wash;        // compressed highlights, lifted blacks

    float3 c;
    c.x = Y + 0.956f * I + 0.621f * Qc;
    c.y = Y - 0.272f * I - 0.647f * Qc;
    c.z = Y - 1.106f * I + 1.703f * Qc;

    // ---- 5. colour cast (cosine palette with drift), scanlines, vignette, tube edge
    const float field = 0.6f * vn + 0.4f * vnoise(sx / (900.f * sc), sy / (700.f * sc), seed + 5u);
    const float3 tc = pal(Q[Q_DRIFT] + Q[Q_PERIOD] * field, Q);
    const float tm = Q[Q_TINT];
    c.x = c.x * (1.f - tm) + tm * c.x * (0.35f + 1.3f * tc.x) + 0.06f * wash * tc.x;
    c.y = c.y * (1.f - tm) + tm * c.y * (0.35f + 1.3f * tc.y) + 0.06f * wash * tc.y;
    c.z = c.z * (1.f - tm) + tm * c.z * (0.35f + 1.3f * tc.z) + 0.06f * wash * tc.z;

    const float period = fmaxf(6.f * sc, 3.f);
    const float sl = 0.5f + 0.5f * __cosf(TAU * sy / period);
    const float Yo = fminf(fmaxf(Y, 0.f), 1.f);
    const float m = (1.f - Q[Q_SCAN] * 0.38f * sl * (1.f - 0.55f * Yo))
                  * (1.f - Q[Q_VIG] * smooth(0.2f, 1.15f, r2)) * edge;
    c.x *= m; c.y *= m; c.z *= m;

    // morph from the untouched source so amount -> 0 is exact
    const float4 s0 = ld(src, x, y, W, H);
    const float k = Q[Q_MIX];
    out[y * W + x] = pack(make_float4(s0.x + (c.x * 255.f - s0.x) * k, s0.y + (c.y * 255.f - s0.y) * k,
                                      s0.z + (c.z * 255.f - s0.z) * k, 255.f));
}
"""


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class Vhs(Effect):
    name = "vhs"
    label = "VHS"
    stage = "stylise"
    description = ("Worn VHS tape on an old tube: rolling tracking bands, static, chroma bleed, washed colour, "
                   "scanlines; the beat kicks a tracking-error burst.")
    params = {
        **standard_effect_params(),
        "noise_level": num(0.0, 1.0, 0.45, "Static noise", tier="regional", basic=True),
        "roll_size": num(0.0, 1.0, 0.35, "Tracking band size", tier="regional"),
        "roll_speed": num(0.0, 1.0, 0.12, "Tracking roll speed", unit="/s", tier="regional"),
        "colour_bleed": num(0.0, 1.0, 0.6, "Colour bleed", tier="regional"),
        "tape_wobble": num(0.0, 1.0, 0.4, "Tape wobble", tier="regional"),
        "scanlines": num(0.0, 1.0, 0.5, "Scanlines", tier="regional"),
        "curvature": num(0.0, 0.3, 0.12, "Tube curvature", tier="regional"),
        "tint_mix": num(0.0, 1.0, 0.35, "Tape colour cast", tier="regional"),
        "burst_kick": num(0.0, 2.0, 1.0, "Beat tracking burst", tier="regional"),
        **palette_params("dusk", period=0.6, drift=0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("vhs")
        self._Qh = np.zeros(_NP, np.float32)
        self._Q = cp.zeros(_NP, cp.float32)
        seed = int(self.easer.values().get("seed", 1)) if "seed" in self.params else 1
        rng = np.random.default_rng(seed)
        self._seed = np.uint32(rng.integers(1, 2**31 - 1))
        self._roll = float(rng.uniform(0.25, 0.55))      # start with the band on screen
        self._wob = float(rng.uniform(0.0, 100.0))
        self._tear = float(rng.uniform(0.0, 100.0))
        self._drift = 0.0
        self._nt = 0.0
        self._pc = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        if amount <= 1e-4:
            cp.copyto(out, src)
            return
        dt = max(float(dt), 0.0)
        kick = float(p["burst_kick"])
        k = pulse * (0.3 + 0.7 * amount) * kick
        a = min(max(amount * (1.0 + 0.35 * pulse * kick), 0.0), 1.0)

        # fan amount out onto separate curves
        s_roll = _ss(0.08, 0.8, a)
        s_noise = a ** 1.2
        s_chroma = _ss(0.0, 0.75, a)
        s_scan = a ** 0.7

        # host-integrated phases (kicks add to rates)
        self._roll = (self._roll + float(p["roll_speed"]) * (0.4 + 1.2 * a) * (1.0 + 2.2 * k) * dt) % 1.0
        self._wob = (self._wob + 0.45 * (0.4 + 1.2 * a) * dt) % 4096.0
        self._tear = (self._tear + 2.2 * (0.5 + a) * (1.0 + 2.0 * k) * dt) % 4096.0
        self._drift = (self._drift + float(p["drift_speed"]) * dt) % 64.0
        self._nt = (self._nt + 30.0 * dt) % 16777216.0   # static refreshes at ~30 Hz (kinder to HEVC than 60)

        # palette: ease coefficients towards the chosen palette so a change never snaps
        tgt = np.asarray(palette_coeffs(p["palette"]), np.float32)
        if self._pc is None:
            self._pc = tgt.copy()
        else:
            self._pc += (tgt - self._pc) * (1.0 - math.exp(-dt / 0.8) if dt > 0 else 0.0)

        Q = self._Qh
        Q[0:12] = self._pc
        rsz = float(p["roll_size"])
        Q[_I["amt"]] = a
        Q[_I["curv"]] = float(p["curvature"]) * a
        Q[_I["vig"]] = a * (0.22 + 1.3 * float(p["curvature"]))
        Q[_I["roll_pos"]] = self._roll
        Q[_I["roll_size"]] = (0.015 + 0.28 * rsz) * (1.0 + 0.6 * k)
        Q[_I["roll_amp"]] = (10.0 + 70.0 * s_roll) * (0.4 + 0.6 * rsz) * (1.0 + 1.8 * k)
        Q[_I["roll_noise"]] = 0.25 + 0.55 * s_roll + 1.6 * k
        Q[_I["wob_amp"]] = float(p["tape_wobble"]) * (4.0 + 16.0 * a)
        Q[_I["wob_ph"]] = self._wob
        Q[_I["tear_ph"]] = self._tear
        cb = float(p["colour_bleed"])
        Q[_I["bleed"]] = cb * s_chroma * 34.0
        Q[_I["cshift"]] = cb * s_chroma * 9.0
        Q[_I["soft"]] = 2.5 * a
        Q[_I["noise"]] = float(p["noise_level"]) * s_noise
        Q[_I["scan"]] = float(p["scanlines"]) * s_scan
        Q[_I["wash"]] = 0.9 * a
        Q[_I["tint"]] = float(p["tint_mix"]) * _ss(0.0, 0.8, a)
        Q[_I["drift"]] = self._drift
        Q[_I["period"]] = float(p["palette_period"])
        Q[_I["hsw"]] = 34.0 * s_roll
        Q[_I["mix"]] = _ss(0.0, 0.12, a)
        self._Q.set(Q)

        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._Q,
                                  np.uint32(int(self._nt)), self._seed))
