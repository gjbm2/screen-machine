"""Luma landscape: the frame's blurred luminance becomes a heightfield world textured by the frame itself; the camera
tilts down from the flat view into a low flyover with fog, a sun and glowing bright peaks.

Source: Original (inspired by https://iquilezles.org/articles/terrainmarching/)
Author: smfx (original; heightfield marching after iq's terrainmarching article, idea only)
License: MIT
license_class: permissive

The input frame lies on the ground plane (xz, y up; x in [-A, A], z in [-1, 1], image top at -z) and repeats with
mirrored tiling, so the ground is endless and seamless.  Height = luma of a FrameMips level (``smoothing`` picks the
mip, trilinear so the knob is continuous) shaped so bright areas rise into peaks.  amount 0 is the untouched frame
(copied); as amount rises the flat top-down camera (which reproduces the frame exactly) pitches towards the
horizon, dollies down and the heights grow, so the picture lifts into a 3D landscape seen from low altitude.  The
flight position is integrated (frame3d.HomingAngle with the mirror period, so it springs home as amount falls), the
path meanders and banks gently.  Lighting: a directional sun (light_angle) with ridge/valley cavity shading and a
sun glint, exponential distance fog plus valley mist, a sky made of the blurred top of the frame tinted by pal()
with colour drift, a sun disc with in-scattered glow, and bright peaks that glow in palette colours.  pulse kicks
the height scale (it decays with the envelope), the peak glow and the flight rate; it never moves the camera.

Performance: the march runs at render_scale (default 0.5) into an internal buffer; a full-res composite writes
out = mix(src, upsampled render, smoothstep(0, 0.25, amount)) so the near-flat early amounts stay crisp.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import (FRAME3D, FRAME3D_OPTIONS, FrameMips, HomingAngle, flat_distance,
                                                 smoothstep01, tex_buffer, tex_object)
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D, camera_pack
from stream_service.plugins._lib.upsample import internal_size

FOV = 40.0
NP = 64
MIP_LEVELS = 6
# P layout (floats)
#  0..12 camera | 13 A | 14 height scale | 15 height lod | 16 flight (v offset) | 17 sway (u offset) | 18 m
#  19 light k | 20..22 sun dir | 23 fog density | 24 fog start | 25 t far | 26 glow | 27 sky tint | 28 drift
#  29 palette period | 30 colour lod bias | 31 sun strength | 32 mist | 48..59 palette coeffs
_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define P_A 13
#define P_HS 14
#define P_HLOD 15
#define P_FLY 16
#define P_SWAY 17
#define P_M 18
#define P_LIGHT 19
#define P_SUN 20
#define P_FOG 23
#define P_FSTART 24
#define P_TFAR 25
#define P_GLOW 26
#define P_TINT 27
#define P_DRIFT 28
#define P_PERIOD 29
#define P_BIAS 30
#define P_SUNK 31
#define P_MIST 32
#define P_PAL 48
#define LT_STEPS 72

// The ground: the frame with mirrored tiling, scrolled by the integrated flight and the meander sway.
struct Ground {
    Tex T; float A, hs, lod, fly, sway;
    __device__ __forceinline__ float2 uv(float x, float z) const {
        return f2(uv_mirror(0.5f + x * (0.5f / A) + sway), uv_mirror(0.5f + 0.5f * z - fly));
    }
    __device__ __forceinline__ float lum_at(float x, float z, float l) const {
        const float2 q = uv(x, z);
        return luma3(tex_sample(T, q.x, q.y, l));
    }
    // bright areas rise into peaks (convex shaping keeps dark ground low and flat)
    __device__ __forceinline__ float shape(float l) const { return hs * l * (0.3f + 0.7f * l); }
    __device__ __forceinline__ float height(float x, float z) const { return shape(lum_at(x, z, lod)); }
};

// Sky: the blurred top band of the frame spread round the horizon, tinted towards the drifting palette, deepening
// upwards, with a sun disc and a wide in-scattered glow (screen-blended so bright stills never clip).
__device__ float3 lt_sky(const Tex& T, float3 rd, const float* __restrict__ P) {
    const float3 L = load3(P + P_SUN);
    const float az = atan2f(rd.x, -rd.z);
    const float el = fmaxf(rd.y, 0.f);
    float3 img = tex_blur(T, uv_mirror(0.5f + az * 0.45f), saturate(0.4f - el * 1.2f));
    const float3 pc = pal(P[P_DRIFT] + P[P_PERIOD] * (0.1f + 0.8f * el), P + P_PAL);
    float3 s = mix(img, pc, P[P_TINT]);
    s = s * (1.f - 0.45f * saturate(el * 2.5f));
    const float sd = saturate(dot(rd, L));
    const float3 sc = mix(f3(1.f, 0.94f, 0.82f), pal(P[P_DRIFT] + 0.5f * P[P_PERIOD], P + P_PAL), 0.3f);
    const float sun = (__powf(sd, 800.f) * 1.1f + __powf(sd, 36.f) * 0.28f + __powf(sd, 5.f) * 0.12f) * P[P_SUNK];
    const float3 e = saturate(sc * sun);
    return s + e - s * e;
}

SDF_KERNEL
void luma_terrain(uchar4* __restrict__ bo, const int w, const int h, const int bp, FRAME_ARGS,
                  const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    Ground G; G.T = T; G.A = P[P_A]; G.hs = P[P_HS]; G.lod = P[P_HLOD]; G.fly = P[P_FLY]; G.sway = P[P_SWAY];
    const Cam cam = cam_load(P);
    const float pa = pixel_angle(cam, h);
    const float3 ro = cam.eye;
    const float3 rd = cam_dir(cam, x + 0.5f, y + 0.5f, w, h);
    const float m = P[P_M], tfar = P[P_TFAR];

    // march only inside the slab 0 <= y <= hmax (heights are >= 0), clipped at the fog far distance
    const float hmax = G.hs + 1e-4f;
    float t_in = 0.f, t_out = -1.f;
    if (rd.y < -1e-6f) {
        t_in = ro.y > hmax ? (ro.y - hmax) / -rd.y : 0.f;
        t_out = fmaxf(ro.y, 0.f) / -rd.y;
    } else if (ro.y < hmax) {
        t_out = (hmax - ro.y) / fmaxf(rd.y, 1e-6f);
    }
    t_out = fminf(t_out, tfar);
    float th = -1.f;
    if (t_in < t_out) {
        float t = t_in;
        float3 p = ro + rd * t;
        float f = p.y - G.height(p.x, p.z);
        if (f <= 0.f) {
            th = t;
        } else {
            float tp = t, fp = f;
            bool done = false;
            for (int i = 0; i < LT_STEPS && !done; ++i) {
                float st = fminf(fmaxf(0.45f * f, 0.0015f + 3.f * pa * t), 0.15f + 0.06f * t);
                if (t + st >= t_out) { st = t_out - t; done = true; }
                t += st;
                p = ro + rd * t;
                f = p.y - G.height(p.x, p.z);
                if (f <= 0.f) {
                    float a = tp, fa = fp, b = t, fb = f;
                    #pragma unroll
                    for (int j = 0; j < 4; ++j) {
                        const float tm = a + (b - a) * fa / fmaxf(fa - fb, 1e-7f);
                        const float3 pm = ro + rd * tm;
                        const float fm = pm.y - G.height(pm.x, pm.z);
                        if (fm <= 0.f) { b = tm; fb = fm; } else { a = tm; fa = fm; }
                    }
                    th = a + (b - a) * fa / fmaxf(fa - fb, 1e-7f);
                    break;
                }
                tp = t; fp = f;
                if (i == LT_STEPS - 1 && !done) th = t;          // out of steps grazing a slope: shade it
            }
        }
    }

    const float3 L = load3(P + P_SUN);
    const float3 fogc = lt_sky(T, normalize(f3(rd.x, 0.f, rd.z) + f3(0.f, 1e-3f, 0.f)), P);
    float3 col;
    if (th < 0.f) {
        col = rd.y >= 0.f ? lt_sky(T, rd, P) : fogc;
    } else {
        const float3 hp = ro + rd * th;
        const float2 q = G.uv(hp.x, hp.z);
        // shading normal from the height map (finite differences at the height lod's texel scale)
        const float e = fmaxf(frame_texel(T) * exp2f(G.lod) * 0.7f, 1.5f * pa * th);
        const float hx0 = G.height(hp.x - e, hp.z), hx1 = G.height(hp.x + e, hp.z);
        const float hz0 = G.height(hp.x, hp.z - e), hz1 = G.height(hp.x, hp.z + e);
        const float3 n = normalize(f3(hx0 - hx1, 2.f * e, hz0 - hz1));
        const float cosa = dot(rd, cam.fwd), ndv = fabsf(dot(rd, n));
        const float lod = tex_lod(th, pa, cosa, ndv, frame_texel(T), P[P_BIAS]);
        float3 c = tex_sample(T, q.x, q.y, lod);
        const float lh = luma3(tex_sample(T, q.x, q.y, G.lod));
        const float lc = luma3(tex_sample(T, q.x, q.y, G.lod + 2.f));
        // relight: flat ground keeps its brightness, sun-facing slopes brighten, ridges lift, valleys sink
        const float k = P[P_LIGHT];
        const float dif = saturate(dot(n, L));
        float lit = fminf(0.42f + 0.58f * dif / fmaxf(L.y, 0.2f), 1.7f);
        lit *= fminf(fmaxf(1.f + 1.4f * (lh - lc), 0.45f), 1.3f);
        const float glint = __powf(saturate(dot(reflect(rd, n), L)), 24.f) * 0.3f;
        c = c * mix(1.f, lit, k);
        const float3 sc = mix(f3(1.f, 0.94f, 0.82f), pal(P[P_DRIFT] + 0.5f * P[P_PERIOD], P + P_PAL), 0.3f);
        float3 g = saturate(sc * (glint * k));
        c = c + g - c * g;
        // glowing peaks: bright heights emit in the drifting palette
        const float gk = P[P_GLOW] * smoothstep(0.5f, 0.95f, lh);
        g = saturate(mix(c, pal(P[P_DRIFT] + lh * P[P_PERIOD], P + P_PAL), 0.55f) * gk);
        c = c + g - c * g;
        // distance fog (with sun in-scatter) and low valley mist
        float fo = 1.f - __expf(-P[P_FOG] * fmaxf(th - P[P_FSTART], 0.f));
        fo = fmaxf(fo, m * smoothstep(0.7f * tfar, tfar, th));
        const float mist = P[P_MIST] * __expf(-hp.y / (0.2f * G.hs + 1e-3f)) * (1.f - __expf(-0.5f * th));
        fo = saturate(fo + (1.f - fo) * mist);
        c = mix(c, fogc, fo);
        col = c;
    }
    store_rgb_dither(bo, x, y, bp, col, m);
}

// Full res: out = mix(src, upsampled terrain, wmix) (hardware bilinear upsample of the internal render).
extern "C" __global__
void luma_terrain_comp(uchar4* __restrict__ out, const uchar4* __restrict__ src, const int W, const int H,
                       const unsigned long long rt, const float wmix)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const float3 r = tex_rgb_at(rt, (x + 0.5f) / W, (y + 0.5f) / H);
    if (wmix >= 0.9999f) { store_rgb(out, i, r); return; }
    const uchar4 s = src[i];
    const float3 c = make_float3(s.x, s.y, s.z) * (1.f / 255.f);
    store_rgb(out, i, mix(c, r, wmix));
}
"""


def _rot_y(v, a):
    c, s = math.cos(a), math.sin(a)
    return (v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c)


class LumaTerrain(Effect):
    name = "luma_terrain"
    stage = "projection"
    label = "Luma landscape"
    description = "The picture's brightness rises into a fog-filled 3D landscape textured by the picture, seen in a low flyover."
    params = {
        **standard_effect_params(),
        "height": num(0.0, 1.0, 0.45, "Mountain height", tier="regional", basic=True),
        "smoothing": num(1.0, 5.5, 3.0, "Terrain smoothing (mip level)", step=0.05, tier="regional"),
        "camera_pitch": num(30.0, 86.0, 76.0, "Camera pitch (towards horizon)", unit="°", step=0.5,
                            tier="regional"),
        "flight_speed": num(0.0, 0.6, 0.08, "Flight speed", unit="frames/s", step=0.005, tier="regional",
                            bind="rate"),
        "fog": num(0.0, 1.0, 0.45, "Fog", tier="regional"),
        "light_angle": num(-150.0, 150.0, 30.0, "Sun direction", unit="°", step=1.0, tier="regional"),
        "glow": num(0.0, 1.0, 0.45, "Peak glow", tier="regional"),
        "kick": num(0.0, 1.0, 0.5, "Beat height kick", tier="regional"),
        "sky_tint": num(0.0, 1.0, 0.45, "Sky & fog palette tint", tier="regional"),
        "palette": enum(PALETTES, "dusk", "Palette", tier="regional"),
        "palette_period": num(0.05, 4.0, 0.8, "Palette period", tier="regional"),
        "drift_speed": num(-0.5, 0.5, 0.02, "Colour drift", unit="/s", step=0.005, tier="realtime", bind="rate"),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, FRAME3D_OPTIONS)
        self._k = mod.get_function("luma_terrain")
        self._kc = mod.get_function("luma_terrain_comp")
        self.mips = FrameMips(ctx, levels=MIP_LEVELS, blur_sigma=2.0)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.fly = HomingAngle(period=2.0, spring=3.0)       # mirror tiling repeats every 2 frame heights
        self.kick = 0.0
        self.drift = 0.0
        self.ph1 = 0.0
        self.ph2 = 0.7
        self.ph3 = 0.0
        self.A = ctx.width / ctx.height
        self._size = None
        self._alloc(float(self.easer.values().get("render_scale", 0.5)))

    def _alloc(self, rs: float) -> None:
        size = internal_size(self.ctx, min(max(rs, 0.25), 1.0))
        if size == self._size:
            return
        self._size = size
        self.bw, self.bh = size
        self.buf = tex_buffer(self.ctx.cp, self.bw, self.bh)
        self.bt = tex_object(self.buf)
        self.bp = np.int32(self.buf.strides[0] // 4)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        self.kick = max(pulse, self.kick * math.exp(-dt / 0.3))
        pk = self.kick
        m = smoothstep01(a / 0.6)
        gate = smoothstep01((a - 0.05) / 0.4)
        fs = float(p["flight_speed"])
        fly = self.fly.step(dt, fs * (1.0 + 0.6 * pk * m), gate)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 64.0)
        self.ph1 = math.fmod(self.ph1 + (0.05 + 0.6 * fs) * dt, 2.0 * math.pi)
        self.ph2 = math.fmod(self.ph2 + 0.23 * dt, 2.0 * math.pi)
        self.ph3 = math.fmod(self.ph3 + (0.03 + 0.4 * fs) * dt, 2.0 * math.pi)
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        self._alloc(float(p["render_scale"]))
        W, H = self.ctx.width, self.ctx.height
        A = self.A
        D = flat_distance(FOV)
        height = float(p["height"])
        kick = float(p["kick"])
        hs_cam = height * m
        hs = hs_cam * (1.0 + 0.8 * kick * pk * m)
        hmax_cam = hs_cam * (1.0 + 0.8 * kick)                 # camera clears the tallest possible (kicked) peak
        th = math.radians(float(p["camera_pitch"])) * m
        dist = D * (1.0 - 0.42 * m)
        ty = max(0.5 * hs_cam, hmax_cam + 0.14 * m - dist * math.cos(th))
        ty += 0.025 * m * math.sin(self.ph2)
        psi = 0.09 * m * math.sin(self.ph1)
        target = (0.0, ty, 0.0)
        off = _rot_y((0.0, dist * math.cos(th), dist * math.sin(th)), psi)
        up = _rot_y((0.0, math.sin(th), -math.cos(th)), psi)
        eye = (target[0] + off[0], target[1] + off[1], target[2] + off[2])
        cam = camera_pack(eye, target, up, FOV, -0.04 * m * math.cos(self.ph1))
        az = math.radians(float(p["light_angle"]))
        el = math.radians(24.0)
        sun = (math.cos(el) * math.sin(az), math.sin(el), -math.cos(el) * math.cos(az))
        fog = float(p["fog"])
        dens = m * (0.015 + 0.5 * fog ** 1.5)
        fstart = 0.6 * dist
        tfar = min(fstart + 4.5 / max(dens, 1e-3), 60.0)
        hh = self.host
        hh[0:13] = cam
        hh[13] = A
        hh[14] = hs
        hh[15] = float(p["smoothing"])
        hh[16] = fly - 2.0 * math.floor(fly / 2.0)
        hh[17] = 0.12 * m * math.sin(self.ph3)
        hh[18] = m
        hh[19] = 0.85 * m
        hh[20:23] = sun
        hh[23] = dens
        hh[24] = fstart
        hh[25] = tfar
        hh[26] = float(p["glow"]) * m * (0.7 + 0.6 * pk)
        hh[27] = float(p["sky_tint"])
        hh[28] = self.drift
        hh[29] = float(p["palette_period"])
        hh[30] = -0.35
        hh[31] = m
        hh[32] = 0.45 * fog * m
        hh[48:60] = palette_coeffs(p["palette"]) if p["palette"] in PALETTES else palette_coeffs("dusk")
        self.P.set(hh)
        self.mips.build(src)
        launch_2d(self._k, self.bw, self.bh, (self.buf, np.int32(self.bw), np.int32(self.bh), self.bp,
                                              *self.mips.args(src), self.P))
        wmix = smoothstep01(a / 0.25)
        launch_2d(self._kc, W, H, (out, src, np.int32(W), np.int32(H), self.bt, np.float32(wmix)))
