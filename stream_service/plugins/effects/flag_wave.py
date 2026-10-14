"""Flag / Banner: the frame becomes cloth pinned along one edge, rippling in the wind with fold shading and a silk sheen.

Source: https://godotshaders.com/shader/2d-simple-animated-flag-shader
Author: mreliptik (godotshaders 2D simple animated flag); 3D fold shading via frame3d light_frame by smfx
License: CC0-1.0
license_class: permissive
Ported from GLSL (Godot shading language) by smfx (ideas/changes: the 2D "sine grows with distance from the pole"
vertex wave becomes a real 3D cloth surface (3 oblique travelling sines under a polynomial pin envelope, in-plane
sway, analytic foreshortening of the folds, perspective from the frame3d flat camera), inverted per pixel with 3
analytic Newton steps; pin left / top / corner; fold relighting (frame3d light_frame) + cavity darkening with a
palette-tinted shadow; silk sheen (Blinn + softbox) tinted by pal() with drift; margin shrinks the cloth over a
darkened blurred backdrop with a soft drop shadow and a metal pole / rod / pin; the beat launches a wave packet
from the pinned edge that travels at the wave speed (a pulse delay line) and decays).

amount 0 is the untouched frame (copied).  Every 3D term (wave amplitude, sway, foreshortening, margin, shading,
sheen, pole) scales with m = smoothstep(amount), so the first frames differ from the copy by < 1/255 and 0->1 is a
smooth morph from the flat picture to a waving flag.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, flat_distance, smoothstep01
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

FOV = 40.0
NG = 32                 # pulse-packet gain table entries over a = 0..1
HN = 640                # pulse delay-line history (frames)
P_G = 64                # gain table offset
NP = P_G + NG

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define NG 32
#define P_A 0
#define P_D 1
#define P_M 2
#define P_SC 3
#define P_Z 4
#define P_SW 5
#define P_SH 6
#define P_MODE 7
#define P_K 8
#define P_Q 11
#define P_PH 14
#define P_GW 17
#define P_FOLD 20
#define P_SHEEN 21
#define P_BG 22
#define P_SHADOW 23
#define P_L 24
#define P_DRIFT 27
#define P_PER 28
#define P_DIAG 29
#define P_ROD0 30
#define P_ROD1 32
#define P_RODR 34
#define P_BALLR 35
#define P_RODOP 36
#define P_BALL0 37
#define P_SHOFF 38
#define P_GNORM 40
#define P_PAL 44
#define P_G 64

struct CEv { float3 p, pu, pv; float wzn, e, a; };

// pulse-packet gain G(a) (linear table) and its slope
__device__ __forceinline__ void gain_at(const float* __restrict__ P, float a, float& g, float& gd) {
    const float x = saturate(a) * (float)(NG - 1);
    const int i = min((int)x, NG - 2);
    const float f = x - (float)i;
    const float g0 = P[P_G + i], g1 = P[P_G + i + 1];
    g = g0 + (g1 - g0) * f;
    gd = (g1 - g0) * (float)(NG - 1);
}

// The cloth surface at material coords (u, v) (image uv): world position and its partials (frame3d world: the
// frame quad is x in [-A, A], y in [-1, 1] at z = 0, y up).  Pin-aligned coords: a = distance from the pin (0..1),
// b = across.  Waves travel away from the pin; the envelope is zero along the pinned edge.
__device__ __forceinline__ CEv cloth(const float* __restrict__ P, float u, float v) {
    const float A = P[P_A], sc = P[P_SC];
    const int mode = (int)P[P_MODE];
    float a, b, au, av, bu, bv, Dx, Dy, Nx, Ny;
    if (mode == 0) {            // pole on the left
        a = u; b = v; au = 1.f; av = 0.f; bu = 0.f; bv = 1.f; Dx = 1.f; Dy = 0.f; Nx = 0.f; Ny = -1.f;
    } else if (mode == 1) {     // banner hanging from the top
        a = v; b = u; au = 0.f; av = 1.f; bu = 1.f; bv = 0.f; Dx = 0.f; Dy = -1.f; Nx = 1.f; Ny = 0.f;
    } else {                    // pinned at the top-left corner: waves radiate as arcs
        const float X = fmaxf(u, 0.f) * A, Y = fmaxf(v, 0.f);
        const float r2 = X * X + Y * Y + 1e-6f, r = sqrtf(r2), L = P[P_DIAG], ir = 1.f / r;
        a = r / L; au = X * A * ir / L; av = Y * ir / L;
        const float k2pi = 0.63661977f;
        b = atan2f(Y, X) * k2pi; bu = -Y * A / r2 * k2pi; bv = X / r2 * k2pi;
        Dx = X * ir; Dy = -Y * ir; Nx = -Y * ir; Ny = -X * ir;
    }
    // three oblique travelling sines: wz (out of plane), wn (in-plane sway, quarter phase)
    float wz = 0.f, wza = 0.f, wzb = 0.f, wn = 0.f, wna = 0.f, wnb = 0.f, s1 = 0.f, c1 = 1.f;
    #pragma unroll
    for (int i = 0; i < 3; ++i) {
        const float k = P[P_K + i], q = P[P_Q + i], g = P[P_GW + i];
        float s, c; __sincosf(k * a + q * b - P[P_PH + i], &s, &c);
        wz += g * s; wza += g * k * c; wzb += g * q * c;
        wn += g * c; wna -= g * k * s; wnb -= g * q * s;
        if (i == 0) { s1 = s; c1 = c; }
    }
    // pin envelope (zero and calm at the pin, growing to the free edge) times the travelling pulse packet
    const float ac = fmaxf(a, 0.f);
    const float e = ac * (0.3f + 0.7f * ac), ea = a > 0.f ? 0.3f + 1.4f * ac : 0.f;
    float G, Gd; gain_at(P, ac, G, Gd);
    const float E = e * G, Ea = ea * G + e * Gd;
    const float Z = P[P_Z], S = P[P_SW];
    const float za = Z * (Ea * wz + E * wza), zb = Z * E * wzb;
    const float n = S * E * wn, na = S * (Ea * wn + E * wna), nb = S * E * wnb;
    // foreshortening of the main fold: d = -K E^2 k1 (k1 a / 2 + sin(2 th1) / 4)  (cloth bunches where it is steep)
    const float k1 = P[P_K], q1 = P[P_Q], K = P[P_SH];
    const float s2 = 2.f * s1 * c1, c2 = c1 * c1 - s1 * s1;
    const float I = 0.5f * k1 * ac + 0.25f * s2;
    const float d = -K * E * E * k1 * I;
    const float da = -K * (2.f * E * Ea * k1 * I + E * E * k1 * k1 * 0.5f * (1.f + c2));
    const float db = -K * E * E * k1 * (0.5f * q1 * c2);
    const float ddu = da * au + db * bu, ddv = da * av + db * bv;
    const float nu = na * au + nb * bu, nv = na * av + nb * bv;
    CEv o;
    o.p = f3((2.f * u - 1.f) * A * sc + Dx * d + Nx * n, (1.f - 2.f * v) * sc + Dy * d + Ny * n, Z * E * wz);
    o.pu = f3(2.f * A * sc + Dx * ddu + Nx * nu, Dy * ddu + Ny * nu, za * au + zb * bu);
    o.pv = f3(Dx * ddv + Nx * nv, -2.f * sc + Dy * ddv + Ny * nv, za * av + zb * bv);
    o.wzn = wz * P[P_GNORM];
    o.e = E;
    o.a = ac;
    return o;
}

// perspective of the flat camera (eye on +z at distance D): screen point on the z = 0 plane and its Jacobian
__device__ __forceinline__ void project(const CEv& c, float D, float2& pr, float2& Ju, float2& Jv) {
    const float f = D / fmaxf(D - c.p.z, 0.25f), g = f * f / D;
    pr = f2(c.p.x * f, c.p.y * f);
    Ju = f2(c.pu.x * f + c.p.x * g * c.pu.z, c.pu.y * f + c.p.y * g * c.pu.z);
    Jv = f2(c.pv.x * f + c.p.x * g * c.pv.z, c.pv.y * f + c.p.y * g * c.pv.z);
}

__device__ __forceinline__ float3 metal(float3 n, float3 L, float3 base) {
    const float3 hv = normalize(L + f3(0.f, 0.f, 1.f));
    const float spc = powf(saturate(dot(n, hv)), 40.f);
    const float3 r = reflect(f3(0.f, 0.f, -1.f), n);
    return base * (0.22f + 0.78f * saturate(dot(n, L))) + f3(0.85f * spc + 0.3f * env_softbox(r));
}

SDF_KERNEL
void flag_wave(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const float A = P[P_A], Dd = P[P_D], sc = P[P_SC], m = P[P_M];
    const float su = (x + 0.5f) / w, sv = (y + 0.5f) / h;
    const float stx = (2.f * su - 1.f) * A, sty = 1.f - 2.f * sv;
    const float pw = 2.f / (float)h;                    // world size of one output pixel on the frame plane
    const float3 L = load3(P + P_L);

    // ---- invert the cloth map: rest-pose guess + 3 Newton steps
    float u = 0.5f + (su - 0.5f) / sc, v = 0.5f + (sv - 0.5f) / sc;
    CEv c; float2 pr, Ju, Jv;
    #pragma unroll 1
    for (int it = 0; it < 3; ++it) {
        c = cloth(P, u, v);
        project(c, Dd, pr, Ju, Jv);
        const float Fx = pr.x - stx, Fy = pr.y - sty;
        const float det = Ju.x * Jv.y - Ju.y * Jv.x;
        const float id = 1.f / (fabsf(det) > 1e-6f ? det : 1e-6f);
        u -= clamp((Jv.y * Fx - Jv.x * Fy) * id, -0.2f, 0.2f);
        v -= clamp((Ju.x * Fy - Ju.y * Fx) * id, -0.2f, 0.2f);
    }
    c = cloth(P, u, v);
    project(c, Dd, pr, Ju, Jv);
    float det = Ju.x * Jv.y - Ju.y * Jv.x;
    det = fabsf(det) > 1e-6f ? det : 1e-6f;
    const float id = 1.f / det;
    // gradients of u and v w.r.t. the screen point (world units)
    const float iux = Jv.y * id, iuy = -Jv.x * id, ivx = -Ju.y * id, ivy = Ju.x * id;

    // ---- coverage of the cloth (screen-pixel distance to its border) and the texture LOD
    const float gu = sqrtf(iux * iux + iuy * iuy) * pw, gv = sqrtf(ivx * ivx + ivy * ivy) * pw;
    const float eu = fminf(u, 1.f - u) / fmaxf(gu, 1e-9f), ev = fminf(v, 1.f - v) / fmaxf(gv, 1e-9f);
    const float cov = saturate(fminf(eu, ev) + 0.5f);
    const float Wf = (float)W, Hf = (float)H;
    const float fx = sqrtf(iux * iux * Wf * Wf + ivx * ivx * Hf * Hf) * pw;
    const float fy = sqrtf(iuy * iuy * Wf * Wf + ivy * ivy * Hf * Hf) * pw;
    const float lod = __log2f(fmaxf(fmaxf(fx, fy), 1e-6f)) - 0.4f;

    // ---- backdrop: darkened blurred input, a breath of palette, soft drop shadow of the cloth
    float3 bg = bg_blur(T, su, sv, 1.06f, P[P_BG], 0.9f);
    bg = mix(bg, bg_gradient(P + P_PAL, su, sv, P[P_DRIFT], P[P_BG] * 0.8f), 0.25f);
    {
        const float qx = fabsf(stx - P[P_SHOFF]) - A * sc, qy = fabsf(sty - P[P_SHOFF + 1]) - sc;
        const float sd = sqrtf(fmaxf(qx, 0.f) * fmaxf(qx, 0.f) + fmaxf(qy, 0.f) * fmaxf(qy, 0.f)) + fminf(fmaxf(qx, qy), 0.f);
        bg = bg * (1.f - P[P_SHADOW] * (1.f - smoothstep(-0.04f, 0.09f, sd)));
    }

    float3 col = bg;
    if (cov > 0.f) {
        float3 cc = tex_sample(T, saturate(u), saturate(v), lod);
        float3 nrm = normalize(cross(c.pu, c.pv));
        if (nrm.z < 0.f) nrm = nrm * -1.f;
        const float3 eye = f3(0.f, 0.f, Dd);
        const float3 rd = normalize(c.p - eye);
        const float k = P[P_FOLD];
        const float pidx = P[P_PER] * (c.a + 0.12f * c.wzn) + P[P_DRIFT];
        const float3 pc = pal(pidx, P + P_PAL);
        // fold relighting (identity for a head-on surface) + cavity darkening with a palette-tinted shadow
        cc = light_frame(cc, nrm, L, f3(0.f, 0.f, -1.f), 0.28f, k);
        const float cav = k * (0.5f - 0.5f * c.wzn) * saturate(c.e * 2.5f);
        cc = cc * (1.f - 0.22f * cav);
        cc = cc * mix(f3(1.f), pc * 0.5f + f3(0.5f), 0.35f * cav);
        // silk sheen: broad Blinn lobe + the studio softbox, screen-blended so bright stills do not clip
        const float3 hv = normalize(L - rd);
        const float sp = powf(saturate(dot(nrm, hv)), 28.f) * 0.75f + env_softbox(reflect(rd, nrm)) * 0.45f;
        const float3 shc = mix(f3(1.f), pc, 0.5f) * (sp * P[P_SHEEN]);
        cc = f3(1.f) - (f3(1.f) - saturate(cc)) * (f3(1.f) - saturate(shc));
        col = cov < 1.f ? mix(bg, cc, cov) : cc;
    }

    // ---- pole / rod / pin along the pinned edge (only once the margin has opened room for it)
    const float op = P[P_RODOP];
    if (op > 0.f) {
        const float r = P[P_RODR], rb = P[P_BALLR];
        const float3 base = mix(f3(0.62f, 0.55f, 0.42f), pal(P[P_DRIFT] + 0.5f, P + P_PAL), 0.35f);
        const float p0x = P[P_ROD0], p0y = P[P_ROD0 + 1], p1x = P[P_ROD1], p1y = P[P_ROD1 + 1];
        const float pax = stx - p0x, pay = sty - p0y, bax = p1x - p0x, bay = p1y - p0y;
        const float hh = saturate((pax * bax + pay * bay) / fmaxf(bax * bax + bay * bay, 1e-9f));
        const float qx = pax - bax * hh, qy = pay - bay * hh, dq = sqrtf(qx * qx + qy * qy);
        const float cr = saturate((r - dq) / pw + 0.5f) * op;
        if (cr > 0.f) {
            const float s = fminf(dq / fmaxf(r, 1e-6f), 1.f), il = 1.f / fmaxf(dq, 1e-6f);
            const float3 n3 = f3(qx * il * s, qy * il * s, sqrtf(fmaxf(1.f - s * s, 0.f)));
            col = mix(col, metal(n3, L, base), cr);
        }
        // finial balls (p1 always; p0 for the banner rod)
        #pragma unroll
        for (int j = 0; j < 2; ++j) {
            if (j == 0 && P[P_BALL0] < 0.5f) continue;
            const float bx = j == 0 ? p0x : p1x, by = j == 0 ? p0y : p1y;
            const float ex = stx - bx, ey = sty - by, de = sqrtf(ex * ex + ey * ey);
            const float cb = saturate((rb - de) / pw + 0.5f) * op;
            if (cb > 0.f) {
                const float irb = 1.f / fmaxf(rb, 1e-6f);
                const float nx = ex * irb, ny = ey * irb;
                const float3 n3 = f3(nx, ny, sqrtf(fmaxf(1.f - nx * nx - ny * ny, 0.f)));
                col = mix(col, metal(n3, L, base), cb);
            }
        }
    }
    store_rgb(out, y * w + x, col);
}
"""

_MODES = {"left": 0, "top": 1, "corner": 2}


class FlagWave(Effect):
    name = "flag_wave"
    stage = "projection"
    label = "Flag / Banner"
    description = "The picture as cloth pinned along one edge: travelling waves, fold shading, silk sheen."
    params = {
        **standard_effect_params(),
        "pin": enum(["left", "top", "corner"], "left", "Pinned edge"),
        "wind_strength": num(0.0, 1.5, 0.7, "Wind strength", tier="regional", basic=True),
        "wavelength": num(0.15, 1.2, 0.45, "Wavelength", unit="× flag", step=0.01, tier="regional"),
        "wind_speed": num(0.0, 3.0, 0.8, "Wind speed", unit="Hz", tier="realtime", bind="rate"),
        "fold_shading": num(0.0, 1.0, 0.75, "Fold shading", tier="regional"),
        "sheen": num(0.0, 1.0, 0.45, "Silk sheen", tier="regional"),
        "margin": num(0.0, 0.3, 0.08, "Margin", tier="regional"),
        "kick": num(0.0, 2.0, 1.0, "Beat wave kick", tier="regional"),
        "backdrop": num(0.0, 1.0, 0.4, "Backdrop", tier="regional"),
        **palette_params("dusk", 1.0, 0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("flag_wave")
        self.mips = FrameMips(ctx, levels=3, blur_sigma=2.5)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.A = ctx.width / ctx.height
        self.D = flat_distance(FOV)
        self.ph = [0.0, 1.7, 4.1]
        self.g1 = 0.0
        self.g2 = 2.0
        self.drift = 0.0
        # pulse delay line: (travel distance, pulse) ring, oldest first from index hi
        self.hs = np.linspace(-6.0, 0.0, HN).astype(np.float64)
        self.hp = np.zeros(HN, np.float64)
        self.hi = 0
        self.stot = 0.0
        self._ar = np.arange(HN)
        self._agrid = np.linspace(0.0, 1.0, NG)
        self._pal_name = None
        self._pal = np.zeros(12, np.float32)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dtc = min(max(float(dt), 0.0), 0.1)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        m = smoothstep01(a)
        lam = max(float(p["wavelength"]), 0.05)
        k1 = 2.0 * math.pi / lam
        ks = (k1, 1.73 * k1, 2.9 * k1)
        sf = (1.0, 0.82, 1.18)
        c = max(float(p["wind_speed"]), 0.0) * lam * (1.0 + 0.35 * pulse * m)     # a-units / s
        if dtc > 0.0:
            for i in range(3):
                self.ph[i] = math.fmod(self.ph[i] + ks[i] * c * sf[i] * dtc, 2.0 * math.pi)
            self.g1 = math.fmod(self.g1 + 0.37 * dtc, 2.0 * math.pi)
            self.g2 = math.fmod(self.g2 + 0.23 * dtc, 2.0 * math.pi)
            self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 1000.0)
            self.stot += max(c, 0.12) * dtc
            self.hs[self.hi] = self.stot
            self.hp[self.hi] = pulse
            self.hi = (self.hi + 1) % HN
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        idx = (self.hi + self._ar) % HN
        hist = np.interp(self.stot - self._agrid, self.hs[idx], self.hp[idx])
        gain = 1.0 + float(p["kick"]) * m * hist * np.exp(-0.9 * self._agrid)

        A = self.A
        mode = _MODES.get(str(p["pin"]), 0)
        margin = float(p["margin"]) * m
        sc = 1.0 - margin
        gust = 1.0 + 0.16 * math.sin(self.g1) + 0.1 * math.sin(1.7 * self.g2 + 1.0)
        ws = float(p["wind_strength"]) * m * gust * min(1.0, lam / 0.45)   # short waves: same slope, no fold-over
        Z = 0.18 * ws * sc
        S = 0.055 * ws * sc
        diag = math.sqrt(A * A + 1.0)
        La = (2.0 * A * sc, 2.0 * sc, 2.0 * sc * diag)[mode]
        K = 0.5 * Z * Z / La * 1.4
        K = min(K, 0.45 * La / (1.69 * k1 * k1))          # keep the steepest local compression < ~45%
        gw = (1.0, 0.42, 0.18)
        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._pal[:] = palette_coeffs(str(p["palette"]))
        h = self.host
        h[:] = 0.0
        h[0] = A
        h[1] = self.D
        h[2] = m
        h[3] = sc
        h[4] = Z
        h[5] = S
        h[6] = K
        h[7] = mode
        h[8:11] = ks
        h[11:14] = (1.6, -2.4, 3.3)
        h[14:17] = self.ph
        h[17:20] = gw
        h[20] = float(p["fold_shading"]) * m
        h[21] = float(p["sheen"]) * m * (1.0 + 0.5 * pulse)
        h[22] = float(p["backdrop"])
        h[23] = 0.55 * m
        Lv = np.asarray([-0.5, 0.55, 0.65])
        h[24:27] = Lv / np.linalg.norm(Lv)
        h[27] = self.drift
        h[28] = float(p["palette_period"])
        h[29] = diag
        # pole geometry (world units on the frame plane)
        room = smoothstep01(margin / 0.06)
        r = 0.02 * room
        if mode == 0:
            xp = -A * sc - 0.5 * r
            h[30:34] = (xp, -1.6, xp, sc + 0.04)
            h[35] = 1.8 * r
            h[37] = 0.0
        elif mode == 1:
            yp = sc + 0.5 * r
            h[30:34] = (-A * sc - 0.06, yp, A * sc + 0.06, yp)
            h[35] = 1.6 * r
            h[37] = 1.0
        else:
            h[30:34] = (-A * sc, sc, -A * sc, sc)
            h[35] = 2.2 * r
            h[37] = 0.0
        h[34] = r
        h[36] = m * room
        h[38:40] = (0.035 * m, -0.05 * m)
        h[40] = 1.0 / sum(gw)
        h[44:56] = self._pal
        h[P_G:P_G + NG] = gain
        self.P.set(h)
        self.mips.build(src)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *self.mips.args(src), self.P))
