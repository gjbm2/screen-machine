"""God rays: whatever is bright in the picture throws volumetric cathedral shafts from a light point that orbits
slowly and follows the lighting rig (pan/tilt).

Source: https://github.com/pmndrs/postprocessing/blob/main/src/effects/GodRaysEffect.js
        https://developer.nvidia.com/gpugems/gpugems3/part-ii-light-and-shadows/chapter-13-volumetric-light-scattering-post-process
Author: Raoul van Rueschen (pmndrs GodRaysEffect); technique Kenny Mitchell, GPU Gems 3 ch.13
License: Zlib (pmndrs/postprocessing, "zlib License" in the repository LICENSE file)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the light-scattering march (sample toward the light, exponential decay,
weight/exposure) is kept, but run as a 3-pass hierarchical radial march (8 taps x 3 passes = 512 effective samples,
exact geometric spacing, so no banding) at quarter resolution; the occlusion mask is built from the input itself
(bright pass, palette tint, a sun disc the dark parts of the picture occlude, and drifting angular "shaft" gaps),
then screen-composited at 4K through a soft clip.  The ISF/ofxPostProcessing versions were not used.)

amount 0 copies the input.  amount scales the ray exposure (0 -> 1 is a smooth morph from the clean frame).
pulse pushes the decay toward 1 and kicks the exposure (+35% max), so the shafts shoot further and brighter on the
beat; it also lurches the orbit and shaft-drift rates (never positions).  pan / tilt (levels, 0.5 = centre) move
the light from Maestro, smoothed ~150 ms on the host.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

TAPS = 8                     # taps per pass; 3 passes -> TAPS**3 = 512 effective samples
NEFF = TAPS ** 3
REF_TAPS = 40.0              # decay is quoted per 1/40 of the march (GPU Gems style, 40 taps)
NP = 32
# P layout: 0..11 palette | 12 Lx | 13 Ly (low-res px) | 14 thr e0 | 15 thr e1 | 16 tint mix | 17 period | 18 drift
#           19 shafts | 20 shaft ox | 21 shaft oy | 22 sun | 23 seed | 24 sat punch

_SRC = HELPERS + PALETTE_CUDA + r"""
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

// Pass 0 (quarter res): the emission / occlusion mask.  Bright parts of the input emit (own colour mixed toward a
// palette tint); a sun disc at the light shines through the bright parts and is blocked by the dark ones; slow
// angular noise around the light carves the emission into discrete shafts.
extern "C" __global__
void gr_mask(const uchar4* __restrict__ src, float4* __restrict__ m, const int W, const int H, const int w, const int h,
             const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float* Q = P + 12;
    const float sx = (float)W / w, sy = (float)H / h;
    const float fx = (x + 0.5f) * sx, fy = (y + 0.5f) * sy, ox = 0.25f * sx, oy = 0.25f * sy;
    const float4 c0 = bilin(src, fx - ox, fy - oy, W, H), c1 = bilin(src, fx + ox, fy - oy, W, H);
    const float4 c2 = bilin(src, fx - ox, fy + oy, W, H), c3 = bilin(src, fx + ox, fy + oy, W, H);
    const float k = 1.f / (4.f * 255.f);
    const float3 c = make_float3((c0.x + c1.x + c2.x + c3.x) * k, (c0.y + c1.y + c2.y + c3.y) * k,
                                 (c0.z + c1.z + c2.z + c3.z) * k);
    const float l = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
    const float bright = smooth(Q[2], Q[3], l);

    const float dx = x + 0.5f - Q[0], dy = y + 0.5f - Q[1];
    const float r = sqrtf(dx * dx + dy * dy) / (float)h;          // distance from the light, in picture heights
    const float inv = 1.f / fmaxf(r * (float)h, 1e-3f);
    const float ca = dx * inv, sa = dy * inv;                      // direction from the light (cos, sin)

    // emission: source colour -> palette tint (index from luma + a gentle seamless angular term + drift)
    const float idx = Q[6] + Q[5] * (0.45f * l + 0.10f * (2.f * ca * sa));
    float3 pc = pal(idx, P);
    const float pg = 0.2126f * pc.x + 0.7152f * pc.y + 0.0722f * pc.z;
    const float sat = 1.f + Q[12];                                 // beat saturation punch
    pc = make_float3(clamp01(pg + (pc.x - pg) * sat), clamp01(pg + (pc.y - pg) * sat), clamp01(pg + (pc.z - pg) * sat));
    const float tb = 0.45f + 0.75f * l;
    const float t = Q[4];
    float3 e = make_float3((c.x + (pc.x * tb - c.x) * t) * bright, (c.y + (pc.y * tb - c.y) * t) * bright,
                           (c.z + (pc.z * tb - c.z) * t) * bright);

    // sun disc: palette-coloured with a white-hot core, occluded by the dark parts of the picture
    const float r2 = r * r;
    const float trans = 0.22f + 0.78f * smooth(0.03f, 0.45f, l);
    const float sun = Q[10] * (1.4f * __expf(-r2 * 400.f) + 0.45f * __expf(-r2 * 30.f)) * trans;
    const float3 sc0 = pal(Q[6] + 0.08f, P);
    const float core = __expf(-r2 * 1600.f);
    e.x += sun * (sc0.x + (1.f - sc0.x) * core);
    e.y += sun * (sc0.y + (1.f - sc0.y) * core);
    e.z += sun * (sc0.z + (1.f - sc0.z) * core);

    // shafts: angular value-noise on a circle round the light, its domain drifting on a closed loop
    const float n = fbm(ca * 2.6f + Q[8], sa * 2.6f + Q[9], (unsigned int)Q[11]);
    const float g = smooth(0.34f, 0.66f, n);
    const float sh = 1.f + Q[7] * smooth(0.f, 0.06f, r) * (0.08f + 1.2f * g - 1.f);
    m[y * w + x] = make_float4(e.x * sh, e.y * sh, e.z * sh, 0.f);
}

// One level of the hierarchical march: out(p) = sum_i d^i in(L + (p - L) s^i), i = 0..TAPS-1.  Three levels with
// (s, d), (s^8, d^8), (s^64, d^64) compose exactly into a 512-sample march with per-sample decay d.
extern "C" __global__
void gr_radial(const float4* __restrict__ in, float4* __restrict__ out, const int w, const int h,
               const float Lx, const float Ly, const float s, const float d)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float dx = x + 0.5f - Lx, dy = y + 0.5f - Ly;
    float sc = 1.f, wt = 1.f;
    float4 acc = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int i = 0; i < """ + str(TAPS) + r"""; ++i) {
        const float4 v = bl4(in, Lx + dx * sc, Ly + dy * sc, w, h);
        acc.x += wt * v.x; acc.y += wt * v.y; acc.z += wt * v.z;
        sc *= s; wt *= d;
    }
    out[y * w + x] = acc;
}

// Composite at full res: upsample the rays, soft-clip (1 - exp(-x)), screen over the input.
extern "C" __global__
void gr_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const float4* __restrict__ R,
             const int W, const int H, const int w, const int h, const float gain)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 r = bl4(R, (x + 0.5f) * w / (float)W, (y + 0.5f) * h / (float)H, w, h);
    const uchar4 s = src[y * W + x];
    const float gx = 1.f - __expf(-fmaxf(r.x, 0.f) * gain);
    const float gy = 1.f - __expf(-fmaxf(r.y, 0.f) * gain);
    const float gz = 1.f - __expf(-fmaxf(r.z, 0.f) * gain);
    const float sr = s.x * (1.f / 255.f), sg = s.y * (1.f / 255.f), sb = s.z * (1.f / 255.f);
    out[y * W + x] = pack(make_float4(255.f * (sr + (1.f - sr) * gx), 255.f * (sg + (1.f - sg) * gy),
                                      255.f * (sb + (1.f - sb) * gz), 255.f));
}
"""


def _wrap(v: float, period: float) -> float:
    return math.fmod(v, period) if abs(v) > period else v


class GodRays(Effect):
    name = "god_rays"
    label = "God rays"
    stage = "stylise"
    description = ("Volumetric light shafts: whatever is bright throws cathedral rays from a light point that orbits "
                   "and follows the rig; the beat shoots them further and brighter.")
    params = {
        **standard_effect_params(),
        **palette_params("ember", 1.0, 0.03),
        "density": num(0.3, 1.0, 0.8, "Ray length (density)", tier="regional", basic=True),
        "decay": num(0.9, 0.99, 0.96, "Decay", step=0.001, tier="regional"),
        "threshold": num(0.0, 1.0, 0.45, "Bright threshold", tier="regional"),
        "tint_mix": num(0.0, 1.0, 0.55, "Tint (source -> palette)", tier="regional"),
        "shafts": num(0.0, 1.0, 0.6, "Shaft gaps", tier="regional"),
        "sun": num(0.0, 1.0, 0.45, "Sun glow", tier="regional"),
        "light_x": num(0.0, 1.0, 0.5, "Light X", tier="regional"),
        "light_y": num(0.0, 1.0, 0.28, "Light Y", tier="regional"),
        "orbit_radius": num(0.0, 0.4, 0.12, "Orbit radius", tier="regional"),
        "orbit_speed": num(-0.5, 0.5, 0.05, "Orbit speed", unit="rev/s", step=0.005, tier="regional"),
        "ray_kick": num(0.0, 2.0, 1.0, "Beat kick", step=0.05, tier="regional"),
        "pan": num(0.0, 1.0, 0.5, "Pan (light X offset)", tier="realtime", bind="level"),
        "tilt": num(0.0, 1.0, 0.5, "Tilt (light Y offset)", tier="realtime", bind="level"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._km = mod.get_function("gr_mask")
        self._kr = mod.get_function("gr_radial")
        self._kc = mod.get_function("gr_comp")
        W, H = ctx.width, ctx.height
        self.w, self.h = max(16, W // 4), max(16, H // 4)
        self.M = cp.zeros((self.h, self.w, 4), cp.float32)
        self.A = cp.zeros((self.h, self.w, 4), cp.float32)
        self.B = cp.zeros((self.h, self.w, 4), cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.aspect = W / H
        self.seed = float(np.random.default_rng(int(params.get("seed", 1) or 1)).integers(0, 1 << 16))
        self.orbit = 0.0
        self.shaft = 0.0
        self.drift = 0.0
        self.pan = None
        self.tilt = None
        self.pal = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = pulse * float(p["ray_kick"])                     # beat strength (0..2)
        k1 = min(k, 1.0)

        # host-integrated phases (rates kicked by the beat, positions never jump)
        self.orbit = _wrap(self.orbit + 2.0 * math.pi * float(p["orbit_speed"]) * (1.0 + 0.6 * k) * dt, 2.0 * math.pi)
        self.shaft = _wrap(self.shaft + 0.035 * (1.0 + 1.2 * k) * dt, 2.0 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * (1.0 + 0.5 * k) * dt, 10.0)
        al = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0
        self.pan = float(p["pan"]) if self.pan is None else self.pan + (float(p["pan"]) - self.pan) * al
        self.tilt = float(p["tilt"]) if self.tilt is None else self.tilt + (float(p["tilt"]) - self.tilt) * al
        pc = np.asarray(palette_coeffs(p["palette"]), np.float32)
        ap = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.pal = pc if self.pal is None else self.pal + (pc - self.pal) * ap

        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        w, h = self.w, self.h
        rad = float(p["orbit_radius"])
        lx = float(p["light_x"]) + (self.pan - 0.5) * 0.8 + rad * math.cos(self.orbit) / self.aspect
        ly = float(p["light_y"]) + (self.tilt - 0.5) * 0.8 + rad * math.sin(self.orbit)
        lx = min(max(lx, -0.3), 1.3) * w
        ly = min(max(ly, -0.3), 1.3) * h

        thr = float(p["threshold"])
        e0 = 0.85 * thr
        hp = self.host
        hp[0:12] = self.pal
        hp[12] = lx
        hp[13] = ly
        hp[14] = e0
        hp[15] = e0 + 0.15
        hp[16] = float(p["tint_mix"])
        hp[17] = float(p["palette_period"])
        hp[18] = self.drift
        hp[19] = float(p["shafts"])
        hp[20] = 3.0 * math.cos(self.shaft) + 7.0
        hp[21] = 3.0 * math.sin(self.shaft) + 3.0
        hp[22] = float(p["sun"]) * (1.0 + 0.25 * k1)
        hp[23] = self.seed
        hp[24] = 0.3 * k1
        self.P.set(hp)

        # march geometry: the full march covers 0.9 * density of the way to the light
        dens = float(p["density"])
        s = (1.0 - 0.9 * dens) ** (1.0 / (NEFF - 1))
        decay = float(p["decay"])
        dk = decay + (0.995 - decay) * min(1.0, 0.8 * k)     # beat: rays shoot further
        d = dk ** (REF_TAPS / NEFF)
        # partial normalisation by the base decay's 40-tap sum, so the decay knob changes length more than level
        sbase = (1.0 - decay ** REF_TAPS) / (1.0 - decay)
        norm = math.sqrt(20.3 / sbase)
        gain = a * 0.32 * (REF_TAPS / NEFF) * norm * (1.0 + 0.35 * k1)

        W, H = self.ctx.width, self.ctx.height
        iw, ih = np.int32(w), np.int32(h)
        flx, fly = np.float32(lx), np.float32(ly)
        launch_2d(self._km, w, h, (src, self.M, np.int32(W), np.int32(H), iw, ih, self.P))
        launch_2d(self._kr, w, h, (self.M, self.A, iw, ih, flx, fly, np.float32(s), np.float32(d)))
        launch_2d(self._kr, w, h, (self.A, self.B, iw, ih, flx, fly, np.float32(s ** TAPS), np.float32(d ** TAPS)))
        launch_2d(self._kr, w, h, (self.B, self.A, iw, ih, flx, fly, np.float32(s ** (TAPS * TAPS)),
                                   np.float32(d ** (TAPS * TAPS))))
        launch_2d(self._kc, W, H, (src, out, self.A, np.int32(W), np.int32(H), iw, ih, np.float32(gain)))
