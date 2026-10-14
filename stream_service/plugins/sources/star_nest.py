"""Star nest: a flight through a volumetric kaliset nebula (filaments of stars and coloured dust, dark voids).

Source: https://www.shadertoy.com/view/XlfGRj ("Star Nest", 16-Jun-2013)
        (port made from the xscreensaver 6.17 copy, hacks/glx/glsl/starnest.glsl; see also
        https://arcade3.readthedocs.io/en/latest/tutorials/shader_toy_glow/star_nest.html ,
        https://man.archlinux.org/man/starnest.6.en.txt )
Author: Pablo Roman Andrioli (Kali)
License: MIT
license_class: permissive
Licence seen: the shader header, "// Star Nest by Pablo Roman Andrioli" / "// License: MIT"
        (xscreensaver-6.17 hacks/glx/glsl/starnest.glsl).
Ported from GLSL by smfx (ideas/changes: the tiling fold, the kaliset "abs(p)/dot(p,p) - formuparam" loop with its
        absolute-sum-of-change accumulator, the dark-matter voids, distance fading and saturation mix are ported
        faithfully.  Changed: 16 volume steps x 15 iterations (accumulator rescaled by 17/15 so the original constants
        still hold); the volume slices are anchored in world space along the view axis (a host-integrated slice
        phase, with the nearest and farthest slice cross-faded) so flying forward never pops; the fixed
        (s, s^2, s^4) tint becomes a per-slice cosine-palette colour pal(depth * period + drift) with a small field
        term, and the grey dust is palette-tinted; the mouse rotation becomes a slow host-integrated view wander,
        roll and lateral drift; the camera position is wrapped modulo the 2*tile period for fp32 exactness; a soft
        shoulder replaces the hard clip.  New: energy / pulse / speed macros.)

Macros: energy (slewed, e' = smoothstep(0.1, 0.85, e)) -> forward fly rate x(0.4 + 1.2 e'), roll rate, brightness
gain, dark matter reduced by up to 40 %, the amplitude of a small formuparam wobble.  pulse (k = pulse(0.3 + 0.7 e))
-> star brightness and saturation flash (<= 35 %), a forward-velocity rate kick (fly_kick) and a FOV breath of up to
4 deg (40 ms attack).  speed scales the fly, roll, wander and wobble phases; palette / palette_period / drift_speed
colour the slices; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, smoothstep, wrap
from stream_service.plugins._lib.upsample import internal_size, present

VOLSTEPS = 16          # structural / quality: never changed live
ITERATIONS = 15
STEPSIZE = 0.11        # slice spacing in s (the original: 20 steps of 0.1)

# P layout (floats)
#  0..2 from (wrapped eye) | 3..5 fwd | 6..8 right | 9..11 up | 12 tan(half horizontal fov)
#  13..24 palette | 25 period | 26 drift | 27 star gain | 28 dust gain | 29 formuparam | 30 darkmatter
#  31 distfading | 32 tile | 33 saturation | 34 brightness | 35 slice phase 0..1 | 36 step | 37 output gain
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define VOLSTEPS %(volsteps)d
#define ITERS %(iters)d

SDF_KERNEL
void star_nest(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float3 from = load3(P), fw = load3(P + 3), rt = load3(P + 6), up = load3(P + 9);
    const float th2 = 2.f * P[12];
    const float invW = 1.f / (float)W;
    const float u = ((x + 0.5f) * invW - 0.5f) * th2;
    const float v = (0.5f * H - (y + 0.5f)) * invW * th2;
    const float3 dir = fw + rt * u + up * v;                 // unnormalised, like the original: slices are planes

    const float* PAL = P + 13;
    const float period = P[25], drift = P[26], starg = P[27], dustg = P[28];
    const float fp = P[29], dmat = P[30], dfade = P[31], tile = P[32], sat = P[33], bright = P[34];
    const float phase = P[35], stp = P[36], gain = P[37];
    const float tile2 = 2.f * tile;
    const float ascale = 17.f / (float)ITERS;                // keep the original constants valid at fewer iters

    float fd = __powf(dfade, -phase);                        // distance fading, continuous in the slice depth
    float dmprod = 1.f;                                      // cumulative dark-matter absorption
    float3 vstar = f3(0.f), vdust = f3(0.f);
    #pragma unroll 1
    for (int r = 0; r < VOLSTEPS; ++r) {
        const float s = stp * ((float)r + 1.f - phase);
        const float w = (r == 0) ? (1.f - phase) : ((r == VOLSTEPS - 1) ? phase : 1.f);
        float3 p = from + dir * (s * 0.5f);
        p = abs(f3(tile) - mod(p, tile2));                   // tiling fold
        float pa = 0.f, a = 0.f;
        float dd = dot(p, p);
        #pragma unroll
        for (int i = 0; i < ITERS; ++i) {
            p = abs(p) * (1.f / fmaxf(dd, 1e-7f)) - fp;    // the magic formula
            dd = dot(p, p);
            const float l = sqrtf(dd);
            a += fabsf(l - pa);                              // absolute sum of average change
            pa = l;
        }
        a *= ascale;
        const float dm = fmaxf(0.f, dmat - a * a * 0.001f);  // dark matter
        dmprod *= 1.f - dm * w * smoothstep(0.55f, 0.8f, s); // not rendered near the eye
        const float fade = fd * dmprod * w;
        float a3 = a * a * a;                                // contrast
        a3 = a3 / (1.f + a3 * (1.f / 91125.f));             // soft knee (a ~ 45): tames the fold-corner glare
        const float sw = (s + s * s + s * s * s * s) * (1.f / 3.f);
        const float fidx = 0.18f * a / (a + 25.f);
        const float3 cs = pal(s * period * 0.55f + drift + fidx, PAL);
        const float3 cd = pal(s * period * 0.55f + drift + 0.38f, PAL);
        vstar += cs * (a3 * bright * fade * sw);
        vdust += (f3(0.3f) + cd * 1.4f) * fade;
        fd *= dfade;
    }
    float3 col = (vstar * starg + vdust * dustg) * 0.01f;
    const float g = (col.x + col.y + col.z) * (1.f / 3.f);
    col = max(f3(g) + (col - f3(g)) * sat, 0.f);              // saturation (mean preserving)
    col = col * gain;
    col = f3(1.f) - exp(-col * 1.25f);                        // soft shoulder instead of a hard clip
    store_srgb(out, x, y, W, saturate(col));
}
""" % {"volsteps": VOLSTEPS, "iters": ITERATIONS}


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


class StarNest(Source):
    name = "star_nest"
    label = "Star nest"
    description = ("A flight through a volumetric kaliset nebula: endless filaments of stars and coloured dust "
                   "receding into depth, broken by dark-matter voids (3D volumetric).")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.03),
        "formuparam": num(0.45, 0.60, 0.53, "Structure (filament to cloud)", step=0.001, tier="regional",
                          basic=True),
        "darkmatter": num(0.0, 0.6, 0.3, "Dark matter", tier="regional"),
        "distfading": num(0.6, 0.85, 0.73, "Depth (distance fading)", step=0.005, tier="regional"),
        "tile": num(0.7, 0.9, 0.85, "Cell size", step=0.005, tier="regional"),
        "saturation": num(0.0, 1.0, 0.85, "Saturation", tier="regional", basic=True),
        "fov": num(40.0, 100.0, 55.0, "Field of view", step=1.0, unit="deg", tier="regional"),
        "roll_drift": num(-12.0, 12.0, 2.0, "Roll drift", step=0.1, unit="deg/s", tier="regional"),
        "fly_kick": num(0.0, 3.0, 1.2, "Beat fly kick", tier="regional"),
        "fly_speed": num(0.0, 4.0, 1.0, "Fly speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("star_nest")
        self.P = cp.zeros(40, cp.float32)
        self.Ph = np.zeros(40, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.pos = [1.0 + float(rng.uniform(0, 1.7)), 0.5 + float(rng.uniform(0, 1.7)), 0.5]
        self.yaw0 = 0.5 + float(rng.uniform(0.0, 1.0))        # a non-axis-aligned base heading, like the original
        self.pitch0 = 0.25 + float(rng.uniform(0.0, 0.2))
        self.yaw_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.pitch_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.lat_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.wob_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.roll = float(rng.uniform(-0.3, 0.3))
        self.zdist = 0.0
        self.drift = 0.0
        self.fk = 0.0
        self.e_fast = None
        self.e_slow = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        if self.e_fast is None:
            self.e_fast = self.e_slow = e
        if dt > 0:
            self.e_fast += (e - self.e_fast) * (1.0 - math.exp(-dt / 0.3))
            self.e_slow += (e - self.e_slow) * (1.0 - math.exp(-dt / 1.5))
        ef = smoothstep(0.1, 0.85, self.e_fast)
        es = smoothstep(0.1, 0.85, self.e_slow)
        k = pulse * (0.3 + 0.7 * e)

        # FOV breath envelope: 40 ms attack, 250 ms release
        if dt > 0:
            tau = 0.04 if k > self.fk else 0.25
            self.fk += (k - self.fk) * (1.0 - math.exp(-dt / tau))

        # ---- orientation: slow bounded wander around a seeded base heading, plus roll
        self.yaw_ph = wrap(self.yaw_ph + 0.045 * spd * (0.6 + 0.6 * ef) * dt, 2.0 * math.pi)
        self.pitch_ph = wrap(self.pitch_ph + 0.031 * spd * (0.6 + 0.6 * ef) * dt, 2.0 * math.pi)
        self.lat_ph = wrap(self.lat_ph + 0.07 * spd * dt, 2.0 * math.pi)
        self.roll = wrap(self.roll + math.radians(float(p["roll_drift"])) * spd * (0.4 + 1.2 * ef) * dt,
                         2.0 * math.pi)
        yaw = self.yaw0 + 0.55 * math.sin(self.yaw_ph)
        pitch = self.pitch0 + 0.3 * math.sin(self.pitch_ph)
        fwd = (math.cos(pitch) * math.sin(yaw), math.sin(pitch), math.cos(pitch) * math.cos(yaw))
        fov = max(20.0, float(p["fov"]) - 4.0 * self.fk)
        cam = camera_pack((0.0, 0.0, 0.0), fwd, up=(0.0, 1.0, 0.0), fov_deg=60.0, roll=self.roll)
        right, upv = cam[6:9], cam[9:12]

        # ---- travel: forward with a slowly rotating lateral drift; pulse kicks the rate only
        lat = tuple(math.cos(self.lat_ph) * r_ + math.sin(self.lat_ph) * u_ for r_, u_ in zip(right, upv))
        trav = _norm(tuple(f_ + 0.45 * l_ for f_, l_ in zip(fwd, lat)))
        vel = 0.075 * float(p["fly_speed"]) * spd * (0.4 + 1.2 * ef) * (1.0 + float(p["fly_kick"]) * k)
        tile = float(p["tile"])
        tile2 = 2.0 * tile
        self.pos = [wrap(c + t_ * vel * dt, tile2) for c, t_ in zip(self.pos, trav)]
        half_step = 0.5 * STEPSIZE
        self.zdist = wrap(self.zdist + vel * dt * sum(t_ * f_ for t_, f_ in zip(trav, fwd)), half_step)
        phase = min(max(self.zdist / half_step, 0.0), 0.99999)

        # ---- structure + colour
        self.wob_ph = wrap(self.wob_ph + 0.21 * spd * dt, 2.0 * math.pi * 10.0)
        wob = (0.003 + 0.008 * es) * (0.7 * math.sin(self.wob_ph) + 0.3 * math.sin(2.3 * self.wob_ph + 1.1))
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)
        sat = min(float(p["saturation"]) * (1.0 + 0.35 * k) + 0.08 * k, 1.3)

        Ph = self.Ph
        Ph[0:3] = self.pos
        Ph[3:6] = cam[3:6]
        Ph[6:9] = right
        Ph[9:12] = upv
        Ph[12] = math.tan(math.radians(fov) * 0.5)
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = 1.0 + 0.35 * k
        Ph[28] = 1.0 + 0.08 * k
        Ph[29] = float(p["formuparam"]) + wob
        Ph[30] = float(p["darkmatter"]) * (1.0 - 0.4 * es)
        Ph[31] = p["distfading"]
        Ph[32] = tile
        Ph[33] = sat
        Ph[34] = 0.0015 * 2.8                                    # soft-kneed accumulator: lift the filaments
        Ph[35] = phase
        Ph[36] = STEPSIZE
        Ph[37] = float(p["intensity"]) * (0.7 + 0.5 * es)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
