"""Ocean flyover: a low skim over open sea, exp-sine waves with Fresnel sky reflection and sun glitter.

Source: https://www.shadertoy.com/view/MdXyzX ("Very fast procedural ocean");
        https://github.com/Zygo/xscreensaver/blob/master/hacks/glx/glsl/prococean.glsl ;
        https://github.com/shader-slang/slang-rhi/blob/HEAD/examples/shader-toy/ocean.slang
Author: afl_ext (Adrian Chlubek)
License: MIT  ("// afl_ext 2017-2024 // MIT License" in the header of xscreensaver's prococean.glsl and of
         slang-rhi's ocean.slang)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: per-wave phases integrated and wrapped on the host, so wave clock and
        camera travel never lose fp32 precision or jump; the wave field is evaluated in a camera-local frame (travel
        enters as a per-wave phase); the length(p)*0.1 phase scramble is replaced by seeded per-wave phases;
        the wave mean is centred on y=0 so wave height changes keep the sea level; Lipschitz-aware step through
        the two bounding planes (32 steps x 12 waves); 24-wave normal with per-wave pixel-footprint fade
        (anti-shimmer); extra_cheap_atmosphere kept, clamped for sun below the horizon, plus its Mie sun glow;
        cosine-palette sky/water tint, sea mist, storm foam, Narkowicz ACES + sRGB store from the toolkit.)

Macros: energy -> wave height and choppiness (slewed, floor ~50%), wave clock rate x(0.4+1.2e'), flight speed,
heading wander rate, slightly lower altitude, glitter gain, storm foam.  pulse -> swell kick on the wave height and a
lurch of the wave clock (rates/amplitudes only), plus a flare confined to the sun disc, its glow and the glitter.
speed scales the wave clock, flight and wander; palette tints sky and water by elevation (pal(elev*period+drift)),
mixed in by water_tint; intensity is output gain.
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

NWM = 12          # waves in the height march
NWN = 24          # waves in the normal
SUMW_M = sum(0.8 ** i for i in range(NWM))
SUMW_N = sum(0.8 ** i for i in range(NWN))
WMEAN = 0.47      # mean of the normalised wave sum: the surface is (w - WMEAN) * D, so sea level stays at y = 0
SUN_AZ = 0.28     # sun azimuth relative to the mean heading (radians, right of forward)
_SKY_BASE = (5.5 / 22.4, 13.0 / 22.4, 1.0)

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 mist density (water) | 29 wave height D
#  30 wave scale | 31 drag | 32..34 sun dir | 35 sun/glitter gain | 36 Mie glow gain | 37 tint | 38 foam
#  39..41 sun colour | 42 sky mist amount | 48..71 wave phases | 72..119 wave directions (x, z)
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define NWM %(nwm)d
#define NWN %(nwn)d
#define INV_SUMW_M (%(ism).8ff)
#define INV_SUMW_N (%(isn).8ff)
#define WMEAN (%(wmean)ff)
#define TMAX 900.f

__device__ __forceinline__ float lum(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

// afl_ext's exp-sine wave sum with drag (each wave pulls the sample point along its slope).  q in wave units.
__device__ __forceinline__ float wave_march(float2 q, const float* __restrict__ P, float drag) {
    float freq = 1.f, w = 1.f, sv = 0.f;
    #pragma unroll 4
    for (int i = 0; i < NWM; ++i) {
        const float dx = P[72 + 2 * i], dz = P[73 + 2 * i];
        float s, c;
        __sincosf((dx * q.x + dz * q.y) * freq + P[48 + i], &s, &c);
        const float wv = __expf(s - 1.f);
        const float k = wv * c * w * drag;
        q.x -= dx * k; q.y -= dz * k;
        sv += wv * w;
        w *= 0.8f; freq *= 1.18f;
    }
    return sv * INV_SUMW_M;
}
// Same sum with more waves for the normal; waves above the pixel's Nyquist fade out (fp = footprint, wave units).
// The faded mean is constant across the taps, so it is dropped (only differences are used).
__device__ __forceinline__ float wave_normal(float2 q, const float* __restrict__ P, float drag, float fp) {
    float freq = 1.f, w = 1.f, sv = 0.f;
    for (int i = 0; i < NWN; ++i) {
        const float fade = saturate((1.2f - freq * fp) * 1.4286f);
        if (fade <= 0.f) break;
        const float dx = P[72 + 2 * i], dz = P[73 + 2 * i];
        float s, c;
        __sincosf((dx * q.x + dz * q.y) * freq + P[48 + i], &s, &c);
        const float wv = __expf(s - 1.f);
        const float k = wv * c * w * drag * fade;
        q.x -= dx * k; q.y -= dz * k;
        sv += wv * w * fade;
        w *= 0.8f; freq *= 1.18f;
    }
    return sv * INV_SUMW_N;
}

// afl_ext's extra_cheap_atmosphere (sun colour precomputed on the host, clamped below the horizon), the palette
// tint on the sky part (luminance matched), and the Mie sun glow added untinted.
__device__ float3 sky(float3 d, float3 sd, float3 sunc, const float* __restrict__ P) {
    const float dy = fmaxf(d.y, 0.f);
    const float st = 1.f / (dy + 0.1f);
    const float rs = dot(sd, d);
    const float3 base = f3(0.24553571f, 0.58035714f, 1.f);
    float3 b2 = max(base * sunc - f3(5.5f, 13.f, 22.4f) * (0.002f * (st - 6.f * sd.y * sd.y)), 0.f);
    b2 = b2 * (st * (0.24f + rs * rs * 0.24f));
    const float om = 1.f - dy;
    float3 a = b2 * ((1.f + om * om * om) * 0.5f) + f3(0.003f, 0.004f, 0.008f);
    const float3 pc = srgb_to_linear(pal(dy * P[25] + P[26], P + 13));
    a = mix(a, pc * (lum(a) / fmaxf(lum(pc), 0.03f)), P[37]);
    const float rp = fmaxf(rs, 0.f), r2 = rp * rp, r4 = r2 * r2;
    return a + sunc * (r4 * r4 * st * 0.1f * P[36]);
}
__device__ __forceinline__ float sun_disc(float3 d, float3 sd) {
    return powf(fmaxf(dot(d, sd), 0.f), 720.f) * 210.f;
}

SDF_KERNEL
void ocean_flyover(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float3 sd = load3(P + 32), sunc = load3(P + 39);
    const float sung = P[35] * smoothstep(-0.035f, 0.01f, sd.y);
    const float D = P[29], ws = P[30], drag = P[31];

    // sea mist colour: the sky just above the horizon in this direction, a little desaturated
    const float3 hc = sky(normalize(f3(rd.x, 0.015f, rd.z)), sd, sunc, P);
    const float3 mist = mix(hc, f3(lum(hc)), 0.3f);

    float3 col;
    if (rd.y >= 0.f) {
        col = sky(rd, sd, sunc, P) + sunc * (sun_disc(rd, sd) * sung);
        col = mix(col, mist, P[42] * __expf(-rd.y * 10.f));
    } else {
        // march between the two bounding planes (crest top / trough bottom), Lipschitz-aware step
        const float top = (1.f - WMEAN) * D, bot = -WMEAN * D;
        const float t0 = fminf((top - ro.y) / rd.y, TMAX);
        const float t1 = fminf((bot - ro.y) / rd.y, TMAX);
        const float invL = 1.f / (-rd.y + 0.55f * sqrtf(fmaxf(1.f - rd.y * rd.y, 0.f)));
        const float iws = 1.f / ws;
        float t = t0;
        for (int i = 0; i < 32; ++i) {
            const float3 p = ro + rd * t;
            const float hgt = (wave_march(f2(p.x * iws, p.z * iws), P, drag) - WMEAN) * D;
            const float gap = p.y - hgt;
            if (gap < 0.002f * D + 0.0004f * t) break;
            t += gap * invL;
            if (t >= t1) { t = t1; break; }
        }
        const float3 pos = ro + rd * t;
        // normal: 24 waves, faded by pixel footprint (geometric mean of the along/across-view footprints)
        const float fp = t * pix * rsqrtf(fmaxf(-rd.y, 0.02f)) * iws;
        const float ew = clamp(0.5f * fp, 0.002f, 0.25f);
        const float2 q = f2(pos.x * iws, pos.z * iws);
        const float h0 = wave_normal(q, P, drag, fp);
        const float hx = wave_normal(q + f2(ew, 0.f), P, drag, fp);
        const float hz = wave_normal(q + f2(0.f, ew), P, drag, fp);
        float3 N = normalize(f3((h0 - hx) * D, ew * ws, (h0 - hz) * D));
        N = normalize(mix(N, f3(0.f, 1.f, 0.f), 0.7f * fminf(1.f, sqrtf(t * iws * 0.01f) * 1.1f)));

        const float fres = 0.04f + 0.96f * powf(1.f - fmaxf(0.f, dot(-N, rd)), 5.f);
        float3 R = reflect(rd, N);
        R.y = fabsf(R.y);
        R = normalize(R);
        const float3 refl = sky(R, sd, sunc, P) + sunc * (sun_disc(R, sd) * sung);

        // subsurface: afl_ext's deep-water colour, palette-tinted, brighter on the crests
        const float hn = saturate((pos.y - bot) / D);
        const float3 sb = f3(0.0293f, 0.0698f, 0.1717f);
        const float3 pw = srgb_to_linear(pal(P[26] - 0.2f * P[25], P + 13));
        const float3 sc = mix(sb, pw * (lum(sb) / fmaxf(lum(pw), 0.02f)), P[37]);
        const float daylight = 0.25f + 0.75f * smoothstep(-0.08f, 0.25f, sd.y);
        col = refl * fres + sc * (0.25f * (0.2f + hn) * daylight);

        // storm foam on the crests (fades with footprint so it never sparkles)
        if (P[38] > 0.f) {
            const float fo = smoothstep(0.6f, 0.86f, hn) * P[38] * saturate(1.f - fp * 6.f);
            const float3 fc = mix(f3(1.f), sunc, 0.4f) * (0.08f + 0.35f * daylight);
            col = mix(col, fc, fo * 0.7f);
        }
        col = mix(col, mist, 1.f - __expf(-P[28] * t));
    }
    col = vignette(col, x, y, W, H, 0.25f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"nwm": NWM, "nwn": NWN, "ism": 1.0 / SUMW_M, "isn": 1.0 / SUMW_N, "wmean": WMEAN}


def _sun_colour(sy: float) -> tuple[float, float, float]:
    """extra_cheap_atmosphere's suncolor, with its extrapolation clamped for a sun below the horizon."""
    st2 = min(1.0 / max(sy * 11.0 + 1.0, 0.05), 1.8)
    return tuple(max(0.0, 1.0 + (max(0.0, 1.0 - b) - 1.0) * st2) for b in _SKY_BASE)


class OceanFlyover(Source):
    name = "ocean_flyover"
    label = "Ocean flyover"
    description = "A low skim over open sea: exp-sine waves, Fresnel sky reflection and sun glitter, glassy to storm."
    params = {
        **standard_scene_params(palette="ocean", render_scale=0.5, period=1.2, drift=0.02),
        "sea_state": num(0.0, 1.0, 0.5, "Sea state (glassy -> storm)", tier="regional", basic=True),
        "wave_scale": num(0.5, 2.0, 1.0, "Wave scale", tier="regional"),
        "camera_altitude": num(0.5, 6.0, 1.6, "Camera altitude", tier="regional"),
        "sun_elevation": num(-5.0, 60.0, 9.0, "Sun elevation (time of day)", step=0.5, unit="deg",
                             tier="regional", basic=True),
        "water_tint": num(0.0, 1.0, 0.45, "Palette tint", tier="regional"),
        "fog": num(0.0, 1.0, 0.3, "Sea mist", tier="regional"),
        "swell_kick": num(0.0, 1.0, 0.6, "Beat swell", tier="regional"),
        "flare_kick": num(0.0, 1.0, 0.6, "Beat sun flare", tier="regional"),
        "flight_speed": num(0.0, 4.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("ocean_flyover")
        self.P = cp.zeros(128, cp.float32)
        self.Ph = np.zeros(128, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        j = np.arange(NWN, dtype=np.float64)
        it = j * 1232.399963 + rng.uniform(0.0, 2.0 * math.pi)       # afl_ext's direction scramble, seeded spin
        self.dirs = np.stack([np.sin(it), np.cos(it)], axis=1)
        self.freq = 1.18 ** j
        self.tmul = 2.0 * 1.07 ** j
        self.ph = rng.uniform(0.0, 2.0 * math.pi, NWN)              # per-wave phase (clock + travel), wrapped
        self.Ph[72:72 + 2 * NWN] = self.dirs.reshape(-1)
        self.cam_ph = float(rng.uniform(0.0, 60.0))                  # heading-wander phase (host only)
        self.drift = 0.0
        self.e_s = None
        self.roll = 0.0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = smoothstep(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.2) if dt > 0 else 1.0
        self.e_s = ep if self.e_s is None else self.e_s + (ep - self.e_s) * a
        es = self.e_s
        k = pulse * (0.3 + 0.7 * e)

        ss = float(p["sea_state"])
        ws = float(p["wave_scale"])
        swell = float(p["swell_kick"])
        flare = float(p["flare_kick"])

        # ---- wave height / choppiness (slewed energy; pulse swells the height multiplier)
        d_s = ws * (0.55 + 0.9 * es) * (0.4 + 1.2 * ss)
        D = d_s * (1.0 + 0.45 * swell * k)
        drag = min(0.7, (0.1 + 0.5 * ss) * (0.65 + 0.6 * es))

        # ---- camera: wandering heading, banking into turns, low skim
        wr = spd * (0.6 + 0.8 * ep)
        self.cam_ph += wr * dt
        c = self.cam_ph
        heading = 0.5 * math.sin(0.083 * c) + 0.22 * math.sin(0.031 * c + 1.3)
        h_rate = (0.5 * 0.083 * math.cos(0.083 * c) + 0.22 * 0.031 * math.cos(0.031 * c + 1.3)) * wr
        roll_t = max(-0.08, min(0.08, -1.2 * h_rate)) + (0.004 + 0.012 * es) * math.sin(0.29 * c)
        self.roll += (roll_t - self.roll) * (1.0 - math.exp(-dt / 0.6) if dt > 0 else 1.0)
        alt = float(p["camera_altitude"]) * (1.0 - 0.18 * es)
        crest = (1.0 - WMEAN) * d_s * (1.0 + 0.45 * swell)
        eye_y = max(alt, crest + 0.25 + 0.1 * d_s)
        pitch = -min(0.3, 0.07 + 0.025 * eye_y) + 0.012 * math.sin(0.37 * c)
        fwd = (math.sin(heading) * math.cos(pitch), math.sin(pitch), math.cos(heading) * math.cos(pitch))
        eye = (0.0, eye_y, 0.0)
        cam = camera_pack(eye, (fwd[0], eye_y + fwd[1], fwd[2]), fov_deg=50.0, roll=self.roll)

        # ---- integrate per-wave phases: wave clock (pulse lurches it) + camera travel, in wave units
        v = 2.0 * ws * (0.45 + 1.2 * ep) * float(p["flight_speed"]) * spd
        vel_w = np.array([math.sin(heading), math.cos(heading)]) * (v / ws)
        clock = (0.4 + 1.2 * ep) * spd * (1.0 + 0.6 * swell * k)
        self.ph += (self.tmul * clock + self.freq * (self.dirs @ vel_w)) * dt
        np.mod(self.ph, 2.0 * math.pi, out=self.ph)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- sun
        el = math.radians(float(p["sun_elevation"]))
        sd = (math.cos(el) * math.sin(SUN_AZ), math.sin(el), math.cos(el) * math.cos(SUN_AZ))

        fog = float(p["fog"])
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = 2.0 * float(p["intensity"]) * (0.92 + 0.16 * ep)
        Ph[28] = (0.0004 + 0.012 * fog * fog) / ws
        Ph[29] = D
        Ph[30] = ws
        Ph[31] = drag
        Ph[32:35] = sd
        Ph[35] = (0.55 + 0.9 * ep) * (1.0 + 2.0 * flare * k)
        Ph[36] = 1.0 + 0.35 * flare * k
        Ph[37] = p["water_tint"]
        Ph[38] = smoothstep(0.45, 1.0, ss) * (0.25 + 0.75 * es) * 0.8
        Ph[39:42] = _sun_colour(sd[1])
        Ph[42] = 0.8 * fog
        Ph[48:48 + NWN] = self.ph
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
