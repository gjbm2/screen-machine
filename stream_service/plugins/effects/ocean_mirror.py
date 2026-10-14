"""Ocean mirror: the picture becomes a giant sky / billboard standing on the horizon over a perspective ocean that
reflects it (wave-normal distortion, glossy vertical streaks, Fresnel, sun glitter).

Source: https://github.com/mrdoob/three.js/blob/dev/examples/jsm/objects/Water.js (flat mirror after Slayvin),
        https://github.com/mrdoob/three.js/blob/dev/LICENSE,
        https://godotshaders.com/shader/2d-reflective-water/
Author: three.js authors (Water.js, after Slayvin's flat mirror); Kitchen Games (godotshaders 2D Reflective Water)
License: MIT + CC0-1.0
license_class: permissive
Ported from GLSL by smfx (ideas/changes: rewritten from scratch on smfx/_lib/frame3d.py as an analytic ray-plane
mirror: the frame is a wall standing on the water at z = 0 (mirror-extended into a panoramic sky), the ocean is the
plane y = 0 with a host-integrated sum of choppy directional waves filtered by the pixel footprint (unresolved
slope variance widens the glitter lobe Toksvig-style and blurs / streaks the reflection), plus frame3d Water ripple
rings spawned on the beat; palette-tinted body colour with drift; amount solves the camera so the water line
rises from the bottom edge (identity) to the target horizon).

amount 0 is the untouched frame (copied).  As amount rises the camera lowers towards the water and dollies back so
the picture's base (the water line) rises from the bottom edge to `horizon` while the picture shrinks to
`frame_scale` of the space above it; the sides and top continue the picture mirror-wise into a blurred, dimmed
"sky" (`surround`).  Below the line a perspective ocean reflects the whole wall.  Distortion, glitter and the
palette tint all scale with m = smoothstep(amount).  pulse kicks the swell amplitude and rate, spawns ripple
rings and flares the glitter (all decaying with the beat envelope).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, Water, smoothstep01
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D, camera_pack

FOV = 40.0
NW = 10                     # waves (3 swell + 7 detail)
NP = 96
# P layout (floats)
#  0..12 camera | 13 A | 14 m | 15 surround | 16 f0 | 17 tint | 18 glitter | 19,20 sun x,y | 21 chop
#  22 drift | 23 period | 24 1/wave_scale | 25 ring k (world) | 26 ring gain | 27 mist | 28 mist k | 29 frame lod bias
#  30 reflection lod bias | 31 camera z | 32..43 palette | 44..93 waves (dx dz k a phase) x NW
_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define OM_NW 10
#define P_A 13
#define P_M 14
#define P_SUR 15
#define P_F0 16
#define P_TINT 17
#define P_GLIT 18
#define P_SUN 19
#define P_CHOP 21
#define P_DRIFT 22
#define P_PER 23
#define P_WSI 24
#define P_RK 25
#define P_RG 26
#define P_MIST 27
#define P_MISTK 28
#define P_BIAS 29
#define P_RBIAS 30
#define P_ZC 31
#define P_PAL 32
#define P_WAV 44

// The wall: the frame (x in [-A, A], y in [0, 2], z = 0) standing on the water, continued mirror-wise into a
// blurred, dimmed panoramic sky (seamless at the frame border, so the morph from full frame never shows an edge).
__device__ __forceinline__ float3 wall_col(const Tex& T, float x, float y, float lod, const float* __restrict__ P) {
    const float A = P[P_A];
    const float u = 0.5f + x / (2.f * A), v = 1.f - 0.5f * y;
    const float dout = fmaxf(fmaxf(fabsf(x) - A, y - 2.f), 0.f);
    const float uu = uv_mirror(u), vv = uv_mirror(v);
    float3 c = tex_sample(T, uu, vv, lod + 2.5f * dout);
    if (dout > 0.f) {
        c = mix(c, tex_blur(T, uu, vv), smoothstep(0.25f, 1.8f, dout));
        c = c * mix(1.f, P[P_SUR], smoothstep(0.f, 0.4f, dout));
    }
    return c;
}

__device__ __forceinline__ float wrap_tau(float th) { return th - F3D_TAU * rintf(th * (1.f / F3D_TAU)); }

// The ocean seen along rd (rd.y < 0): mirror of the wall through the wave normal, body colour, glitter, mist.
__device__ float3 water_col(const Tex& T, const Cam& cam, float3 rd, float pa, float texel,
                            const float* __restrict__ P, const float* __restrict__ Wr) {
    const float A = P[P_A];
    const float sb = fmaxf(-rd.y, 1e-5f);
    const float ts = cam.eye.y / sb;
    const float3 p = cam.eye + rd * ts;                      // p.y = 0
    const float fpt = ts * pa, fpr = fpt / fmaxf(sb, 0.015f); // footprint across / along the view
    float gx = 0.f, gz = 0.f, var = 0.f;
    const float chop = P[P_CHOP];
    const float* wv = P + P_WAV;
    #pragma unroll
    for (int i = 0; i < OM_NW; ++i, wv += 5) {
        const float dx = wv[0], dz = wv[1], k = wv[2], a = wv[3];
        float s, c; __sincosf(wrap_tau(k * (dx * p.x + dz * p.z) + wv[4]), &s, &c);
        const float fx = dx * fpt, fz = dz * fpr;
        const float f = __expf(-0.4f * k * k * (fx * fx + fz * fz));   // footprint filter (no shimmer far out)
        const float sl = a * k;
        const float g = sl * c * __expf(chop * (s - 1.f)) * f;          // choppy: slope gathers at the crests
        gx += dx * g; gz += dz * g;
        var += 0.5f * sl * sl * (1.f - f * f);                         // unresolved slope variance
    }
    if (P[P_RG] > 0.f) {                                                // beat ripple rings (frame3d Water)
        const float wsi = P[P_WSI];
        const WaterS w = water_eval(Wr, f2(p.x * wsi, p.z * wsi));
        const float kr = P[P_RK];
        const float fr = __expf(-0.4f * kr * kr * fpt * fpr) * P[P_RG];
        gx += w.g.x * fr; gz += w.g.y * fr;
    }
    const float3 n = normalize(f3(-gx, 1.f, -gz));
    float3 r = reflect(rd, n);
    const float3 rg = r;                                                // unclamped, for the glitter lobe
    r.y = fmaxf(fabsf(r.y), 0.002f);
    r.z = fminf(r.z, -0.02f);
    const float tr = fmaxf(p.z, 0.f) / -r.z;
    const float xr = p.x + r.x * tr, yr = r.y * tr;
    const float sig = 2.f * sqrtf(var), sw = sig * tr;                  // angular / on-wall blur of the reflection
    const float fp = (ts + tr) * pa;
    const float lod = log2f(fmaxf(fp + 0.5f * sw, 1e-9f) / texel) + P[P_RBIAS];
    const float dy = 0.9f * sw + 1.5f * fp;                             // vertical streak (stretched reflections)
    const float3 refl0 = wall_col(T, xr, yr, lod, P);
    const float3 refl1 = wall_col(T, xr, yr + dy, lod, P);
    const float3 refl2 = wall_col(T, xr, fmaxf(yr - dy, 0.f), lod, P);
    float3 refl = refl0 * 0.5f + (refl1 + refl2) * 0.25f;

    const float cosi = saturate(-dot(rd, n));
    const float F = fresnel_schlick(cosi, P[P_F0]);
    const float dn = saturate(p.z / P[P_ZC]);                           // 0 at the horizon .. 1 at the camera
    const float tint = P[P_TINT];
    const float3 pc = pal(P[P_PER] * (0.3f * dn + 0.05f * (gx - gz)) + P[P_DRIFT], P + P_PAL);
    const float3 imgc = tex_blur(T, uv_mirror(0.5f + xr / (2.f * A)), 0.92f);
    const float3 body = mix(imgc * 0.4f, pc * (0.2f + 0.25f * dn), tint);
    refl = refl * mix(f3(1.f), f3(0.6f) + pc * 0.55f, 0.4f * tint);
    float3 col = mix(body, refl, F);

    // sun glitter: Blinn-free mirror lobe towards a sun on the wall; unresolved slopes widen it (Toksvig)
    const float3 S = f3(P[P_SUN], P[P_SUN + 1], 0.f);
    const float3 ls = normalize(S - p);
    const float n0 = 260.f, ne = n0 / (1.f + 4.f * n0 * var);
    const float sp = __powf(saturate(dot(rg, ls)), ne) * sqrtf(ne / n0);
    float3 sc = tex_blur(T, 0.5f + P[P_SUN] / (2.f * A), 1.f - 0.5f * P[P_SUN + 1]);
    sc = sc / fmaxf(fmaxf(sc.x, fmaxf(sc.y, sc.z)), 0.25f);
    sc = mix(sc, f3(1.f), 0.35f);
    const float3 gl = saturate(sc * (2.5f * P[P_GLIT] * sp * (0.35f + 0.65f * F)));
    col = f3(1.f) - (f3(1.f) - col) * (f3(1.f) - gl);                  // screen: bright stills never clip hard

    // mist where the water meets the wall (softens the line, carries the picture's base colour out)
    const float mw = P[P_MIST] * __expf(-fmaxf(p.z, 0.f) * P[P_MISTK]);
    const float3 hc = tex_blur(T, uv_mirror(0.5f + p.x / (2.f * A)), 0.97f);
    return mix(col, hc, mw);
}

SDF_KERNEL
void ocean_mirror(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P,
                  const float* __restrict__ Wr)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const Cam cam = cam_load(P);
    const float px = x + 0.5f, py = y + 0.5f;
    const float3 rd = cam_dir(cam, px, py, w, h);
    const float pa = pixel_angle(cam, h), texel = frame_texel(T);
    const float cosa = dot(rd, cam.fwd);
    // the wall plane z = 0 (the camera looks down -z, so every pixel ray reaches it)
    const float tw = -cam.eye.z / fminf(rd.z, -1e-4f);
    const float xw = cam.eye.x + rd.x * tw, yw = cam.eye.y + rd.y * tw;
    // fraction of this pixel above the water line (the line is horizontal on screen: no roll, no yaw)
    const float cover = saturate(0.5f + yw / fmaxf(tw * pa, 1e-7f));
    float3 col = f3(0.f);
    if (cover > 0.f) col = wall_col(T, xw, yw, tex_lod(tw, pa, cosa, fabsf(rd.z), texel, P[P_BIAS]), P);
    if (cover < 1.f) {
        const float3 wc = water_col(T, cam, rd, pa, texel, P, Wr);
        col = cover > 0.f ? mix(wc, col, cover) : wc;
    }
    store_rgb(out, y * w + x, col);
}
"""


class OceanMirror(Effect):
    name = "ocean_mirror"
    stage = "projection"
    label = "Ocean mirror"
    description = "The picture stands on the horizon as a giant sky over a perspective ocean that mirrors it, with waves, Fresnel and sun glitter."
    params = {
        **standard_effect_params(),
        "horizon": num(0.3, 0.7, 0.56, "Horizon (water line)", tier="regional", basic=True),
        "frame_scale": num(0.35, 1.0, 0.85, "Frame size (billboard .. full sky)", tier="regional"),
        "wave_scale": num(0.3, 3.0, 1.0, "Wave scale", unit="×", tier="regional"),
        "choppiness": num(0.0, 1.0, 0.35, "Choppiness", tier="regional"),
        "fresnel_tint": num(0.0, 1.0, 0.4, "Fresnel / water tint", tier="regional"),
        "glitter": num(0.0, 1.5, 0.8, "Sun glitter", tier="regional"),
        "sun_x": num(-1.0, 1.0, 0.0, "Sun position", tier="regional"),
        "surround": num(0.0, 1.0, 0.7, "Sky surround", tier="regional"),
        "vantage": num(0.0, 1.0, 0.35, "Eye height", tier="regional"),
        "swell_speed": num(0.0, 3.0, 1.0, "Swell speed", unit="×", tier="realtime", bind="rate"),
        **palette_params("ocean", 1.0, 0.03),
        "seed": num(0, 2**31 - 1, 7, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("ocean_mirror")
        self.mips = FrameMips(ctx, levels=4, blur_sigma=2.5)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        seed = int(params.get("seed", 7) or 7) if isinstance(params, dict) else 7
        rng = np.random.default_rng(seed)
        # wave set: 3 long swells heading roughly at the camera, 7 detail waves scattered wider
        self.lam = np.asarray([1.1, 0.75, 0.52, 0.34, 0.23, 0.15, 0.1, 0.066, 0.043, 0.028], np.float64)
        ang = np.concatenate([rng.uniform(-0.55, 0.55, 3), rng.uniform(-1.5, 1.5, NW - 3)])
        self.dirs = np.stack([np.sin(ang), np.cos(ang)], 1)
        self.steep = np.asarray([0.034, 0.03, 0.026] + [0.02, 0.019, 0.018, 0.017, 0.016, 0.015, 0.014], np.float64)
        self.wph = rng.uniform(0.0, 2.0 * math.pi, NW)
        self.water = Water(ctx, n_waves=0, seed=seed + 1, ring_speed=0.8, ring_wavelength=0.16, ring_width=0.22,
                           ring_life=3.0)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "ocean")), np.float64)
        self.drift = 0.0
        self.A = ctx.width / ctx.height
        self.T = math.tan(math.radians(FOV) * 0.5)

    def _camera(self, m: float, hz: float, fs: float, gap: float):
        """Solve the camera so the water line sits at screen row v_b, the frame top at v_t and the eye-level
        horizon at v_h (all lerped from the flat pose at m = 0: v_b 1, v_t 0, v_h 0.5)."""
        T = self.T
        v_b = 1.0 + (hz - 1.0) * m
        v_t = hz * (1.0 - fs) * m
        v_h = 0.5 + (hz - gap - 0.5) * m
        phi = -math.atan((1.0 - 2.0 * v_h) * T)
        ab = phi + math.atan((1.0 - 2.0 * v_b) * T)
        at = phi + math.atan((1.0 - 2.0 * v_t) * T)
        zc = 2.0 / max(math.tan(at) - math.tan(ab), 1e-4)
        ye = -zc * math.tan(ab)
        eye = (0.0, ye, zc)
        tgt = (0.0, ye + math.sin(phi), zc - math.cos(phi))
        return camera_pack(eye, tgt, (0.0, 1.0, 0.0), FOV), zc, ye

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dts = min(max(float(dt), 0.0), 0.1)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        m = smoothstep01(a)
        pm = pulse * m
        ws = max(float(p["wave_scale"]), 0.05)
        rate = max(float(p["swell_speed"]), 0.0) * (1.0 + 0.5 * pm)
        kw = 2.0 * math.pi / (self.lam * ws)                          # world wavenumbers
        omega = 0.55 * np.sqrt(9.8 * kw)
        self.wph = np.mod(self.wph - omega * rate * dts, 2.0 * math.pi)
        self.drift = (self.drift + float(p["drift_speed"]) * dts) % 1.0
        tgt = np.array(palette_coeffs(p["palette"]), np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dt / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
        hz = float(p["horizon"])
        fs = float(p["frame_scale"])
        gap = 0.012 + 0.07 * float(p["vantage"])
        cam, zc, ye = self._camera(m, hz, fs, gap)
        # beat ripple rings land on the visible mid-water (Water coords = world / wave_scale)
        self.water.bounds = (-0.8 / ws, 0.4 * zc / ws, 0.8 / ws, max(0.45 * zc, zc - 0.8) / ws)
        self.water.step(dts, speed=max(float(p["swell_speed"]), 0.05), pulse=pulse, amp_scale=1.0,
                        ring_amp=0.006 * m)
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        chop = float(p["choppiness"])
        gain = m * (0.35 + 1.3 * chop) * (1.0 + 0.9 * pm)              # slopes scale with m; pulse kicks the swell
        A = self.A
        h = self.host
        h[0:13] = cam
        h[13] = A
        h[14] = m
        h[15] = p["surround"]
        h[16] = 0.85 + (0.02 - 0.85) * float(p["fresnel_tint"])
        h[17] = float(p["fresnel_tint"]) * min(1.0, 1.5 * m)
        h[18] = float(p["glitter"]) * m * (1.0 + 2.5 * pm)
        h[19] = float(p["sun_x"]) * 0.8 * A
        h[20] = 0.6
        h[21] = 2.2 * chop
        h[22] = self.drift
        h[23] = p["palette_period"]
        h[24] = 1.0 / ws
        h[25] = self.water.ring_k / ws
        h[26] = m
        h[27] = 0.22 * m
        h[28] = 1.0 / max(0.05 * zc, 1e-3)
        h[29] = -0.25
        h[30] = 0.75
        h[31] = zc
        h[32:44] = self._pal
        wv = np.empty((NW, 5), np.float64)
        wv[:, 0:2] = self.dirs
        wv[:, 2] = kw
        wv[:, 3] = self.steep * gain / kw
        wv[:, 4] = self.wph
        h[44:44 + 5 * NW] = wv.reshape(-1)
        self.P.set(h)
        self.mips.build(src)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *self.mips.args(src), self.P, self.water.buf))
