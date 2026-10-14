"""Glow tunnel: a luminous liquid-neon tunnel rushing past, pure volumetric light with no hard surfaces.

Source: https://www.shadertoy.com/view/33cGDj ("Clearly a bug");
        mirrored as https://github.com/bitslip6/rpi-gpu-hub75-matrix/blob/main/shaders/tube2.glsl
Author: Mårten Rånge (mrange)
License: CC0-1.0 (the original's header comment reads "// CC0: Clearly a bug")
license_class: permissive
Ported from GLSL by smfx (ideas/changes: re-derived from the technique rather than the golfed source: a ray is
        stepped through a twisted, turbulence-warped tube field by a fixed ratio of the distance to the tube wall,
        accumulating 1/d glow on every step, with no surface ever shaded.  Added for this renderer: helical neon
        strands on the wall, a bending path, radius breathing, flowing contour bands from the turbulence
        displacement, host-integrated world-attached phases (no wrap pops), energy/pulse wiring, shared palette.)

Each pixel takes 64 fixed-ratio steps (step = 0.018 + 0.55 * |r - R|).  At every step the sample point is moved
into tunnel space (path bend subtracted, then twisted about the axis), warped by 1-4 octaves of sine turbulence
(the last octave fades in fractionally), and the distance to the breathing wall gives a glow weight
(eps / (d + eps)) ^ falloff.  The six helical strands on the wall glow with a sharper falloff.  Colour comes from
pal(depth * period + drift); depth is the warped world depth, so colour bands ride with the flight.

Macros: energy -> flight speed and spin rate (0.4 + 1.2 e'), turbulence amplitude and detail (slewed), glow gain,
contour-band contrast, view depth and a slight FOV breath.  pulse -> wall glow kick (<= 35 %, weighted to the
walls) and a radius-breathing swell, both scaled by glow_kick.  speed scales every rate; palette/period/drift
colour the depth field; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap, smoothstep
from stream_service.plugins._lib.upsample import internal_size, present

TAU = 2.0 * math.pi
N_OCT = 4
OCT_MUL = 1.73          # turbulence frequency ratio between octaves
BEND_W = (0.21, 0.15)   # path bend frequencies (x, y), world units
BREATH_W = 1.1          # radius breathing frequency along the tunnel
COL_Z = 0.05            # palette index per world unit of depth (times palette_period)
LOOK_AHEAD = 2.5

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 colour index per unit depth | 26 drift (wrapped 10) | 27 exposure
#  28 radius | 29 twist (rad/unit) | 30 twist phase | 31 turbulence base freq | 32 turbulence amplitude
#  33 octaves (fractional) | 34..37 octave phase x | 38..41 octave phase y (world-z) | 42..45 octave phase z
#  46 falloff int part | 47 falloff frac | 48 strand mix | 49 bend amp | 50 bend phase x | 51 bend phase y
#  52 breath amp | 53 breath phase | 54 wall kick | 55 depth fade | 56 band phase | 57 band depth
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define GT_STEPS 64
#define GT_ZMAX 40.f
#define GT_EPS 0.03f
#define GT_NSTRAND 6.f
#define GT_BWX (%(bwx)ff)
#define GT_BWY (%(bwy)ff)
#define GT_BREATH_W (%(brw)ff)
#define GT_OCT_MUL (%(om)ff)

// b^n * (1 - f + f b): a smooth stand-in for b^(n + f) without log/exp (n in 1..4, bounded loop)
__device__ __forceinline__ float pow_mix(float b, int n, float f) {
    float bn = b;
    for (int i = 1; i < 4; ++i) if (i < n) bn *= b;
    return bn * (1.f - f + f * b);
}

SDF_KERNEL
void glow_tunnel(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);

    const float* PAL = P + 13;
    const float cz = P[25], drift = P[26];
    const float R0 = P[28], tw = P[29], twph = P[30];
    const float f0 = P[31], A0 = P[32], oct = P[33];
    const int fn = (int)P[46];
    const float ff = P[47], smix = P[48];
    const float bendA = P[49], bpx = P[50], bpy = P[51];
    const float bcx = sinf(bpx), bcy = sinf(bpy);
    const float bamp = P[52], bph = P[53], kick = P[54], fade = P[55];
    const float bandph = P[56], bandd = P[57];
    const float near_z = 0.3f * R0;

    float3 acc = f3(0.f);
    float z = 0.02f;
    for (int i = 0; i < GT_STEPS; ++i) {
        const float3 q = rd * z;
        // tunnel space: subtract the bending path (camera sits on it at z = 0), then twist about the axis
        const float qx = q.x - bendA * (sinf(q.z * GT_BWX + bpx) - bcx);
        const float qy = q.y - bendA * (sinf(q.z * GT_BWY + bpy) - bcy);
        float sa, ca;
        __sincosf(tw * q.z + twph, &sa, &ca);
        float3 p = f3(ca * qx - sa * qy, sa * qx + ca * qy, q.z);
        const float3 p0 = p;
        // turbulence: sine octaves on a rotating swizzle; fractional last octave, faded by pixel footprint
        const float foot = z * pix;
        float fk = f0, ak = A0;
        #pragma unroll
        for (int k = 0; k < 4; ++k) {
            const float w = saturate(oct - (float)k) * saturate(1.6f - 1.4f * fk * foot);
            const float a = ak * w;
            const float dx = sinf(p.y * fk + P[34 + k]);
            const float dy = sinf(p.z * fk + P[38 + k]);
            const float dz = sinf(p.x * fk + P[42 + k]);
            p.x += a * dx; p.y += a * dy; p.z += a * dz;
            fk *= GT_OCT_MUL; ak *= 0.55f;
        }
        const float r = sqrtf(p.x * p.x + p.y * p.y);
        const float R = R0 * (1.f + bamp * sinf(p.z * GT_BREATH_W + bph));
        const float dw = fabsf(r - R);

        // wall glow, contour bands from the turbulence displacement, kick weighted to the wall
        const float b = GT_EPS / (dw + GT_EPS);
        float g = pow_mix(b, fn, ff);
        const float3 dp = p - p0;
        const float barg = (dp.x * 2.1f + dp.y * 1.7f + dp.z * 1.3f) * (6.f / R0) + p.z * 0.35f + bandph;
        g *= 1.f - bandd * (0.5f - 0.5f * __cosf(barg));
        g *= 1.f + kick * b;

        // helical strands lying on the wall (sharper falloff, widened with distance against shimmer)
        const float sc = atan2f(p.y, p.x) * (GT_NSTRAND / SDF_TAU);
        const float arc = (sc - floorf(sc) - 0.5f) * (SDF_TAU / GT_NSTRAND) * r;
        const float ds = sqrtf(dw * dw + arc * arc);
        const float es = 0.03f + 0.8f * foot;
        const float gs = smix * 4.5f * pow_mix(es / (ds + es), fn + 1, ff) * (1.f + kick);

        const float wz = __expf(-z * fade) * smoothstep(0.02f, near_z, z);
        const float idx = p.z * cz + drift;
        float3 cw = pal(idx, PAL);
        cw = cw * cw;                                        // ~sRGB -> linear (gamma 2), cheap
        float3 contrib = cw * g;
        if (gs > 2e-3f) {
            float3 cs = pal(idx + 0.3f, PAL);
            cs = mix(cs * cs, f3(1.f), 0.25f);
            contrib += cs * gs;
        }
        acc += contrib * wz;

        z += 0.018f + 0.55f * dw;
        if (z > GT_ZMAX) break;
    }
    float3 col = acc * P[27];
    col = vignette(col, x, y, W, H, 0.25f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col)));
}
""" % {"bwx": BEND_W[0], "bwy": BEND_W[1], "brw": BREATH_W, "om": OCT_MUL}


class GlowTunnel(Source):
    name = "glow_tunnel"
    label = "Glow tunnel"
    description = "Rush through a twisting, turbulent tunnel of liquid neon light (volumetric 1/d glow, 3D raymarched)."
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.04),
        "twist": num(0.0, 2.0, 0.8, "Twist", tier="regional", basic=True),
        "tunnel_radius": num(0.45, 2.2, 1.0, "Tunnel radius", tier="regional", basic=True),
        "turbulence_frequency": num(0.4, 3.0, 1.3, "Turbulence frequency", tier="regional"),
        "turbulence_octaves": num(1.0, 4.0, 2.6, "Turbulence detail (octaves)", step=0.05, tier="regional"),
        "glow_falloff": num(1.0, 3.0, 1.7, "Glow falloff (haze -> crisp)", tier="regional"),
        "strand_mix": num(0.0, 1.0, 0.6, "Neon strands", tier="regional"),
        "bend": num(0.0, 1.0, 0.45, "Tunnel bend", tier="regional"),
        "glow_kick": num(0.0, 2.0, 1.0, "Beat glow kick", tier="regional"),
        "travel_speed": num(0.0, 4.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("glow_tunnel")
        self.P = cp.zeros(64, cp.float32)
        self.Ph = np.zeros(64, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        r = rng.uniform(0.0, TAU, 16)
        self.ph_x = r[0:4].copy()          # turbulence time phases (x component)
        self.ph_y = r[4:8].copy()          # turbulence world-z phases (y component, ride with the flight)
        self.ph_z = r[8:12].copy()         # turbulence time phases (z component)
        self.twph = float(r[12])           # twist + spin phase
        self.bpx, self.bpy = float(r[13]), float(r[14])
        self.bph = float(r[15])            # breathing phase
        self.bandph = 0.0
        self.drift = 0.0
        self.colph = 0.0                   # colour phase riding with the flight
        self.es = None                     # slewed energy (complexity / amplitude targets)
        self.look = None                   # spring-damped look target

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = smoothstep(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        self.es = ep if self.es is None else self.es + (ep - self.es) * a
        es = self.es
        k = pulse * (0.3 + 0.7 * e)
        kick = min(1.0, float(p["glow_kick"]) * k)

        R0 = float(p["tunnel_radius"])
        rate = 0.4 + 1.2 * ep
        tdt = spd * dt
        dtravel = 2.6 * float(p["travel_speed"]) * rate * tdt
        period = float(p["palette_period"])

        # ---- world-attached phases advance with the flight; time phases with energy-scaled rates
        tw = 0.6 * float(p["twist"])
        self.twph = wrap(self.twph + tw * dtravel + 0.18 * rate * tdt, TAU)
        self.bpx = wrap(self.bpx + BEND_W[0] * dtravel, TAU)
        self.bpy = wrap(self.bpy + BEND_W[1] * dtravel, TAU)
        self.bph = wrap(self.bph + BREATH_W * dtravel + 1.2 * rate * tdt, TAU)
        self.bandph = wrap(self.bandph + 0.6 * rate * tdt, TAU)
        f0 = float(p["turbulence_frequency"])
        for i in range(N_OCT):
            fk = f0 * OCT_MUL ** i
            tr = 0.55 * rate * (1.0 + 0.35 * i) * tdt
            self.ph_x[i] = wrap(self.ph_x[i] + tr, TAU)
            self.ph_y[i] = wrap(self.ph_y[i] + fk * dtravel + 0.5 * tr, TAU)
            self.ph_z[i] = wrap(self.ph_z[i] + 0.8 * tr, TAU)
        cz = COL_Z * period
        self.colph = wrap(self.colph + cz * dtravel, 10.0)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)

        # ---- camera on the path, looking (spring-damped) along its tangent
        bendA = float(p["bend"]) * 1.5 * R0
        L = LOOK_AHEAD
        tgt = (bendA * (math.sin(L * BEND_W[0] + self.bpx) - math.sin(self.bpx)),
               bendA * (math.sin(L * BEND_W[1] + self.bpy) - math.sin(self.bpy)), L)
        if self.look is None or dt <= 0:
            self.look = tgt
        else:
            s = 1.0 - math.exp(-dt / 0.4)
            self.look = tuple(c + (t - c) * s for c, t in zip(self.look, tgt))
        cam = camera_pack((0.0, 0.0, 0.0), self.look, fov_deg=70.0 + 4.0 * es)

        fall = float(p["glow_falloff"])
        fn = min(int(math.floor(fall)), 3)
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = cz
        Ph[26] = wrap(self.drift + self.colph, 10.0)
        Ph[27] = 0.22 * float(p["intensity"]) * (0.7 + 0.6 * ep)
        Ph[28] = R0
        Ph[29] = tw
        Ph[30] = self.twph
        Ph[31] = f0
        Ph[32] = 0.2 * R0 * (0.5 + 0.8 * es)
        Ph[33] = min(max(float(p["turbulence_octaves"]) - 0.5 + 0.8 * es ** 1.5, 1.0), 4.0)
        Ph[34:38] = self.ph_x
        Ph[38:42] = self.ph_y
        Ph[42:46] = self.ph_z
        Ph[46] = fn
        Ph[47] = fall - fn
        Ph[48] = p["strand_mix"]
        Ph[49] = bendA
        Ph[50] = self.bpx
        Ph[51] = self.bpy
        Ph[52] = (0.035 + 0.03 * ep) * (1.0 + 2.0 * kick)
        Ph[53] = self.bph
        Ph[54] = 0.35 * kick
        Ph[55] = 0.045 - 0.018 * es
        Ph[56] = self.bandph
        Ph[57] = 0.45 + 0.3 * es ** 1.5
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
