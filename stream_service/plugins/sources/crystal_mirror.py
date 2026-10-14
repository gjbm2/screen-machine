"""Crystal mirror: a tumbling glass Platonic solid with mirrored inner faces, an infinite jewel kaleidoscope.

Source: https://www.shadertoy.com/view/XfyXRV ("Let's self reflect", mrange, 11 May 2024; header "// CC0: Let's
        self reflect"; copies at https://github.com/bitslip6/rpi-gpu-hub75-matrix/blob/main/shaders/self_reflect.glsl
        and xscreensaver 6.17 hacks/glx/glsl/selfreflect.glsl);
        https://www.shadertoy.com/view/7tGcDG ("Nested transparent spheres", mrange, CC0; also
        https://github.com/mrange/windows-terminal-shader-gallery/tree/main/gallery/nested-transparent-sphere4);
        polyhedron fold: Original (inspired by https://www.shadertoy.com/view/MsKGzw, knighty)
Author: Mårten Rånge (mrange); polyhedron generator idea by knighty (reimplemented); smfx (CUDA port)
License: CC0-1.0 (mrange's shaders, quoted "// CC0: Let's self reflect").  knighty's MsKGzw states no licence
         ("License: Unknown" in mrange's comment), so none of its code is used: the Wythoff fold here is re-derived
         from the (2, 3, n) Coxeter-group construction.  mrange's hsv2rgb (WTFPL) and aces_approx (unknown licence)
         are not used; tonemapping is the sdf3d toolkit's (MIT).
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the outer glass shell + inner mirror-bounce structure, the 1/d edge
        glow and the "light leaking out through the faces" term follow mrange; new: a continuous truncation axis
        (Platonic -> truncated -> rectified -> truncated dual -> dual) plus a bevel towards the omnitruncated
        solid, tetra/octa/icosa families, a palette studio environment instead of the fixed blue box, per-bounce
        palette tint, per-channel IOR dispersion on the exit refraction and the tint, a glowing mirror core, a
        padded bounding sphere with a glow fade so the edge glow never shows the bound, and the macro wiring.)

Macros: energy -> rotation rate x(0.4 + 1.2 e'), inner edge glow and core light, outer edge glow gain, and lower
per-bounce attenuation (deeper reflections stay visible).  pulse -> an in-world edge glow flash (glow_kick) plus
a dispersion / tint punch and a small rotation-rate kick (rates only, nothing jumps).  speed scales every phase;
palette tints each bounce as pal(bounce * period + drift), the edges, the core and the studio lights.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, rot_matrix, smoothstep, wrap
from stream_service.plugins._lib.upsample import internal_size, present

FAMILY_N = {"tetra": 3, "octa": 4, "icosa": 5}
ZOOM = 2.0            # circumradius of the solid (world units)
PAD = 0.3             # bounding-sphere padding (fraction of ZOOM): the outer edge glow fades to 0 there
MAX_BOUNCES = 6
NP = 72

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28..36 rotation (row-major)
#  37 fold count n | 38..40 nc | 41..43 pbc | 44..46 pca | 47..49 vertex | 50 zoom | 51 edge radius (shape units)
#  52 corner radius (shape units) | 53 inner edge radius extra (world) | 54 core radius (world) | 55 outer glow
#  56 inner glow | 57 core light | 58 per-bounce attenuation | 59 tint strength | 60 dispersion | 61 bounces
#  62 bound radius | 63 1/glow fade distance | 64..66 sun dir | 67 core on (0/1)
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define CM_MAXB %(maxb)d

struct CmPoly {
    Mat3 R; float3 nc, pbc, pca, v;
    float zoom, inv_zoom, er, ec, ei, core_r; int n; bool core_on;
};
__device__ __forceinline__ CmPoly cm_load(const float* __restrict__ P) {
    CmPoly S;
    S.R = mat3_load(P + 28);
    S.n = (int)P[37];
    S.nc = load3(P + 38); S.pbc = load3(P + 41); S.pca = load3(P + 44); S.v = load3(P + 47);
    S.zoom = P[50]; S.inv_zoom = 1.f / P[50];
    S.er = P[51]; S.ec = P[52]; S.ei = P[53]; S.core_r = P[54];
    S.core_on = P[67] > 0.5f;
    return S;
}
// (face, edge, corner) distances in world units.  Fold into the fundamental triangle of the (2, 3, n) reflection
// group (mirrors x = 0, y = 0 and the plane with normal nc; n passes converge), then measure against the three
// face planes through the Wythoff vertex v (normals = the triangle's corners) and the three edges (half-lines from
// v towards its mirror images, i.e. along -x, -y and -nc).
__device__ __forceinline__ float3 cm_shape(float3 pw, const CmPoly& S) {
    float3 q = mul(S.R, pw) * S.inv_zoom;
    #pragma unroll
    for (int i = 0; i < 5; ++i) {
        if (i < S.n) {
            q.x = fabsf(q.x); q.y = fabsf(q.y);
            q = q - S.nc * (2.f * fminf(0.f, dot(q, S.nc)));
        }
    }
    q = q - S.v;
    const float df = fmaxf(fmaxf(q.z, dot(q, S.pbc)), dot(q, S.pca));
    const float ea = dot2(f3(fmaxf(q.x, 0.f), q.y, q.z));
    const float eb = dot2(f3(q.x, fmaxf(q.y, 0.f), q.z));
    const float ec = dot2(q - S.nc * fminf(0.f, dot(q, S.nc)));
    const float de = sqrtf(fminf(fminf(ea, eb), ec)) - S.er;
    const float dc = length(q) - S.ec;
    return f3(df, de, dc) * S.zoom;
}

__device__ __forceinline__ float3 cm_refract(float3 I, float3 N, float eta, bool& ok) {
    const float c = dot(N, I);
    const float k = 1.f - eta * eta * (1.f - c * c);
    ok = k >= 0.f;
    return ok ? I * eta - N * (eta * c + sqrtf(fmaxf(k, 0.f))) : f3(0.f);
}
__device__ __forceinline__ float3 cm_unit_hue(float3 c) { return c * (1.f / fmaxf(vmax(c), 0.05f)); }

// Palette "studio": a floor glow, an overhead soft box (the main light the glass reflects) and a back light behind
// the solid.  No environment map.
struct CmEnv { float3 clo, chi, chil, chis, csun, sun; };
__device__ __forceinline__ float3 cm_env(const CmEnv& E, float3 ro, float3 rd) {
    float3 col = f3(0.f);
    if (rd.y < 0.f) {
        const float tp = (ro.y + 6.f) / fmaxf(-rd.y, 1e-4f);
        const float3 q = ro + rd * tp;
        col += E.clo * (0.5f * expf(-0.5f * length(f2(q.x, q.z))));
    } else {
        const float tp = (6.f - ro.y) / fmaxf(rd.y, 1e-4f);
        const float3 q = ro + rd * tp;
        const float db = fBox2(f2(q.x, q.z), f2(5.f, 9.f)) - 3.f;
        col += E.chil * (rd.y * rd.y * smoothstep(0.25f, 0.f, db));
        col += E.chi * (0.2f * expf(-0.5f * fmaxf(db, 0.f)));
        col += E.chis * (0.05f * fminf(fmaxf(-db, 0.f), 6.f));
    }
    col += E.csun * (0.01f / fmaxf(1.001f - dot(E.sun, rd), 1e-3f));
    return col;
}

SDF_KERNEL
void crystal_mirror(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const CmPoly S = cm_load(P);
    const float* PAL = P + 13;
    const float period = P[25], drift = P[26];

    CmEnv E;
    E.clo = srgb_to_linear(pal(drift + 0.02f, PAL));
    E.chi = srgb_to_linear(pal(drift + 0.37f, PAL));
    E.chil = mix(E.chi, f3(dot(E.chi, f3(0.3333f))), 0.3f) * 1.25f;
    E.chis = sqrt(E.chi);
    E.csun = srgb_to_linear(pal(drift + 0.71f, PAL));
    E.sun = load3(P + 64);

    auto map_o = [&](float3 p, float2& g) -> float {
        const float3 s = cm_shape(p, S);
        g = min(g, f2(s.y, s.z));
        return fminf(s.x, fminf(s.y, s.z));
    };
    auto map_i = [&](float3 p, float2& g) -> float {
        const float3 s = cm_shape(p, S);
        const float d2 = s.y - S.ei;
        const float d1 = length(p) - S.core_r;
        g = min(g, f2(d2, d1));
        float d = fminf(-s.x, d2);
        if (S.core_on) d = fminf(d, d1);
        return d;
    };
    auto plain_o = [&](float3 p) -> float { float2 g = f2(1e3f); return map_o(p, g); };
    auto plain_i = [&](float3 p) -> float { float2 g = f2(1e3f); return map_i(p, g); };

    float3 col = cm_env(E, ro, rd);
    const float2 bs = iSphere(ro, rd, P[62]);
    if (bs.x < bs.y && bs.y > 0.f) {
        const float TOL = 5e-4f;
        float t = fmaxf(bs.x, 0.f);
        float2 g = f2(1e3f);
        bool hit = false;
        #pragma unroll 1
        for (int i = 0; i < 64; ++i) {
            const float d = map_o(ro + rd * t, g);
            if (d < TOL) { hit = true; break; }
            t += d;
            if (t > bs.y) break;
        }
        float fre1 = 0.f;
        if (hit) {
            const float3 p1 = ro + rd * t;
            const float3 n1 = calc_normal(plain_o, p1, 0.004f);
            const float3 r1 = reflect(rd, n1);
            bool ok;
            const float3 rr1 = cm_refract(rd, n1, 0.9f, ok);
            fre1 = 1.f + dot(rd, n1); fre1 *= fre1;
            col = cm_env(E, p1, r1) * (0.5f + 0.5f * fre1);
            if (g.x > TOL && g.y > TOL && ok) {
                // ---- inside: mirrored faces bounce the ray around the shell
                float3 agg = f3(0.f), T = f3(1.f);
                float ragg = 1.f, tagg = 0.f, db = g.x;
                float3 o2 = p1, d2v = rr1;
                const int nb = (int)P[61];
                const float ig = P[56], il = P[57], att = P[58], gt = P[59], dsp = P[60];
                const float3 ccore = cm_unit_hue(srgb_to_linear(pal(drift + 0.83f, PAL)));
                #pragma unroll 1
                for (int b = 0; b < CM_MAXB; ++b) {
                    if (b >= nb || ragg < 0.1f) break;
                    float2 gi = f2(1e3f);
                    float ti = fminf(db + 0.05f, 0.3f);
                    float bt = ti, bd = 1e10f;
                    int i;
                    #pragma unroll 1
                    for (i = 0; i < 40; ++i) {
                        const float d = map_i(o2 + d2v * ti, gi);
                        if (d < bd) { bd = d; bt = ti; }
                        if (d < TOL) break;
                        ti += d;
                    }
                    if (i == 40) ti = bt;                       // closed shell: back up to the closest approach
                    tagg += ti;
                    const float3 p2 = o2 + d2v * ti;
                    const float3 n2 = calc_normal(plain_i, p2, 0.005f);
                    const float3 r2 = reflect(d2v, n2);
                    const float fre2 = 1.f + dot(n2, d2v);
                    // per-bounce palette tint; dispersion splits the channels' palette index
                    const float idx = (float)b * 0.23f * period + drift + 0.15f;
                    const float3 tint = cm_unit_hue(srgb_to_linear(f3(pal(idx - dsp * 0.09f, PAL).x,
                                                                      pal(idx, PAL).y,
                                                                      pal(idx + dsp * 0.09f, PAL).z)));
                    T = T * mix(f3(1.f), tint, gt);
                    const float3 beer = T * (ragg * expf(-0.05f * tagg));
                    // inner edge glow (mrange's 1/d glow, growing with path length so deep echoes stay lit)
                    const float3 gc = cm_unit_hue(srgb_to_linear(pal(idx + 0.45f, PAL)));
                    agg += gc * beer * (ig * (1.f + tagg * tagg * 0.04f) * 6e-3f /
                                        fmaxf(gi.x, 5e-4f + tagg * tagg * 2e-4f / ragg));
                    // core light (a soft halo around the mirror core, or a point light when the core is off)
                    agg += ccore * beer * (il * 0.1f / (1.f + 80.f * square(fmaxf(gi.y, 0.f))));
                    if (S.core_on && gi.y <= TOL) {
                        ragg *= 1.f - 0.9f * fre2;
                    } else {
                        // light leaking out through the face: one IOR per channel (dispersion fire at TIR edges)
                        const float eta = 1.f / 0.9f;
                        bool okr, okg, okb;
                        const float3 rr = cm_refract(d2v, n2, eta * (1.f - 0.06f * dsp), okr);
                        const float3 rg = cm_refract(d2v, n2, eta, okg);
                        const float3 rb = cm_refract(d2v, n2, eta * (1.f + 0.06f * dsp), okb);
                        const float3 oc = f3(okr ? cm_env(E, p2, rr).x : 0.f,
                                             okg ? cm_env(E, p2, rg).y : 0.f,
                                             okb ? cm_env(E, p2, rb).z : 0.f);
                        agg += beer * oc * 0.2f;
                        ragg *= att;
                    }
                    o2 = p2; d2v = r2; db = gi.x;
                }
                col += agg * (1.f - 0.75f * fre1);
            }
        }
        // outer edge glow, faded to exactly 0 at the padded bound so the bounding sphere never shows
        const float3 gco = cm_unit_hue(srgb_to_linear(pal(drift + 0.1f, PAL)));
        const float gl = fmaxf(1.f / fmaxf(g.x, 3e-4f) - P[63], 0.f);
        col += gco * (P[55] * 1e-3f * gl * (1.f + fre1));
    }
    col = max(col - f3(0.004f), 0.f);
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"maxb": MAX_BOUNCES}


def _norm3(v):
    v = np.asarray(v, np.float64)
    return v / max(float(np.linalg.norm(v)), 1e-12)


class CrystalMirror(Source):
    name = "crystal_mirror"
    label = "Crystal mirror"
    description = ("A tumbling glass Platonic solid with mirrored inner faces: an infinite jewel kaleidoscope "
                   "with glowing edges and dispersion fire (3D raymarched).")
    params = {
        **standard_scene_params(palette="ice", render_scale=0.4, period=1.0, drift=0.03),
        "family": enum(list(FAMILY_N), "octa", "Solid family (tetra / octa / icosa)", tier="structural"),
        "truncation": num(0.0, 1.0, 0.7, "Truncation (solid -> rectified -> dual)", tier="regional", basic=True),
        "bevel": num(0.0, 1.0, 0.6, "Bevel (towards omnitruncated)", tier="regional"),
        "edge_thickness": num(0.0, 1.0, 0.35, "Edge glow / thickness", tier="regional"),
        "glass_tint": num(0.0, 1.0, 0.55, "Glass tint (clear -> palette)", tier="regional", basic=True),
        "dispersion": num(0.0, 1.0, 0.4, "Dispersion", tier="regional"),
        "core_size": num(0.0, 1.0, 0.6, "Mirror core size", tier="regional"),
        "camera_distance": num(3.6, 9.0, 5.4, "Camera distance", tier="regional"),
        "glow_kick": num(0.0, 2.0, 1.0, "Beat edge flash", tier="regional"),
        "bounce_depth": num(2, MAX_BOUNCES, 4, "Inner bounces", tier="quality", integer=True),
        "rotation_speed": num(0.0, 3.0, 1.0, "Rotation speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("crystal_mirror")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.rot = [float(a) for a in rng.uniform(0.0, 2.0 * math.pi, 3)]   # tumble phases (host float64)
        self.cam_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.drift = 0.0
        self.e_s = None

    @staticmethod
    def _geometry(n: int, trunc: float, bevel: float):
        cospin = math.cos(math.pi / n)
        scospin = math.sqrt(max(0.75 - cospin * cospin, 1e-9))
        nc = np.array([-0.5, -cospin, scospin])
        pab = np.array([0.0, 0.0, 1.0])                      # 2-fold axis
        pbc = _norm3([scospin, 0.0, 0.5])                    # n-fold axis
        pca = _norm3([0.0, scospin, cospin])                 # 3-fold axis
        # truncation path along the triangle's edges: n-fold (Platonic) -> 2-fold (rectified) -> 3-fold (dual)
        t = min(max(trunc, 0.0), 1.0)
        if t < 0.5:
            s = t * 2.0
            w = np.array([s, 1.0 - s, 0.0])                  # weights on (pab, pbc, pca)
        else:
            s = (t - 0.5) * 2.0
            w = np.array([1.0 - s, 0.0, s])
        w = w + (np.full(3, 1.0 / 3.0) - w) * (0.75 * min(max(bevel, 0.0), 1.0))
        v = _norm3(w[0] * pab + w[1] * pbc + w[2] * pca)
        inr = max(min(float(v @ pab), float(v @ pbc), float(v @ pca)), 0.05)
        return nc, pbc, pca, v, inr

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        ep = smoothstep(0.1, 0.85, self.e_s)
        k = pulse * (0.3 + 0.7 * e)

        # ---- rates (pulse kicks the rate, never the pose)
        rr = 0.25 * float(p["rotation_speed"]) * (0.4 + 1.2 * ep) * (1.0 + 0.6 * k) * spd * dt
        self.rot[0] += rr * 1.0
        self.rot[1] += rr * 0.71
        self.rot[2] += rr * 0.43
        self.cam_ph += 0.06 * spd * dt
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        R = (np.asarray(rot_matrix((0, 1, 0), self.rot[0])).reshape(3, 3)
             @ np.asarray(rot_matrix((1, 0, 0), self.rot[1])).reshape(3, 3)
             @ np.asarray(rot_matrix((0, 0, 1), self.rot[2])).reshape(3, 3))

        n = FAMILY_N.get(str(p["family"]), 4)
        nc, pbc, pca, v, inr = self._geometry(n, float(p["truncation"]), float(p["bevel"]))

        D = float(p["camera_distance"])
        yaw = 0.22 * math.sin(self.cam_ph)
        pitch = 0.2 + 0.07 * math.sin(0.7 * self.cam_ph + 1.3)
        eye = orbit_eye((0.0, 0.0, 0.0), D, yaw, pitch)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=48.0)
        sun = _norm3([-c for c in eye])                      # back light sits behind the solid

        et = float(p["edge_thickness"])
        er = 0.0008 + 0.0095 * et ** 1.5
        gscale = 0.6 + 1.4 * et
        flash = 1.0 + 0.35 * float(p["glow_kick"]) * k
        core = float(p["core_size"])
        core_r = core * 0.92 * inr * ZOOM

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 0.62
        Ph[28:37] = R.reshape(-1)
        Ph[37] = n
        Ph[38:41] = nc
        Ph[41:44] = pbc
        Ph[44:47] = pca
        Ph[47:50] = v
        Ph[50] = ZOOM
        Ph[51] = er
        Ph[52] = 4.0 * er + 0.004
        Ph[53] = 2.2 * er * ZOOM
        Ph[54] = core_r
        Ph[55] = gscale * (0.55 + 0.75 * ep) * flash
        Ph[56] = gscale * (0.5 + 0.8 * ep) * flash
        Ph[57] = 0.45 + 0.9 * ep
        Ph[58] = 0.66 + 0.2 * ep
        Ph[59] = min(0.92, float(p["glass_tint"]) * 0.6 * (1.0 + 0.5 * k))
        Ph[60] = float(p["dispersion"]) * (1.0 + 0.8 * k)
        Ph[61] = int(round(float(p["bounce_depth"])))
        Ph[62] = ZOOM * (1.0 + PAD)
        Ph[63] = 1.0 / (ZOOM * (PAD - 0.02))
        Ph[64:67] = sun
        Ph[67] = 1.0 if core_r > 0.01 else 0.0
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
