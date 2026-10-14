"""Mercury blobs: free-floating liquid-chrome metaballs that merge and split on Lissajous orbits.

Source: https://www.shadertoy.com/view/WsKSDt ("Glossy Blobs", Blackle Mori; header "CC0 1.0 Universal ... Blackle
        Mori has waived all copyright and related or neighboring rights to this work.");
        https://www.shadertoy.com/view/wsXBWS ("Comic Blobs", Blackle Mori, CC0)
Author: Blackle Mori; smfx (CUDA port and new scene)
License: CC0-1.0
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Blackle's chrome look -- a smooth-min blob field, mirror reflection with
        Fresnel, and the cheap "refcomp" self-occlusion of the reflected ray by sampling the SDF along it -- kept;
        new: 6-14 spheres on seeded Lissajous orbits (radius fade for a continuous blob count), an analytic studio
        environment of coloured softboxes (box / disc / neon-strip shapes from the palette, rotating rig) that morphs
        studio -> sunset -> neon, roughness, palette-tinted metal, an analytic rim halo, a per-ray blob cull mask
        and a bounding sphere so the march stays within 64 steps, and the macro wiring.)

Macros: energy -> orbit speed, active blob count (radius fade), smin k (more merging), orbit amplitude, light-rig
rotation rate, camera orbit rate and highlight gain.  pulse -> a radius swell, a small merge kick, a highlight and
halo flash and a kick to the orbit rate (rates and amplitudes only, so nothing jumps).  speed scales every rate;
drift rotates the softbox palette; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, rot_matrix, smoothstep, wrap
from stream_service.plugins._lib.upsample import internal_size, present

MAXB = 14
NL = 6
LOFF = 48            # lights: NL x 16 floats
BOFF = LOFF + 16 * NL  # blobs: MAXB x (x, y, z, r)
IOFF = BOFF + 4 * MAXB  # blob palette index: MAXB
NP = IOFF + MAXB + 2

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 blob count (int as float) | 29 smin k
#  30 bound radius | 31..33 cluster centre | 34 roughness | 35 metal tint | 36..38 env weights (studio, sunset, neon)
#  39 backdrop level | 40 highlight gain | 41 pulse | 42 rim halo gain | 43 light saturation | 44 backdrop glow
#  LOFF + 16 i: light dir(3) right(3) up(3) half_u half_v roundness intensity pal_index halo softness
#  BOFF + 4 i: blob centre + radius | IOFF + i: blob palette index
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MAXB %(maxb)d
#define NL %(nl)d
#define LOFF %(loff)d
#define BOFF %(boff)d
#define IOFF %(ioff)d

__device__ __forceinline__ float blobs_d(float3 p, const float* __restrict__ P, unsigned int mask) {
    const int n = (int)P[28];
    const float k = P[29];
    float d = 1e9f;
    for (int i = 0; i < n; ++i) {
        if (!((mask >> i) & 1u)) continue;
        const float* b = P + BOFF + 4 * i;
        d = smin(d, length(p - load3(b)) - b[3], k);
    }
    return d;
}
// palette index of the metal at pos: radius-weighted inverse-square blend over the blobs (no pops as blobs fade)
__device__ __forceinline__ float blob_index(float3 pos, const float* __restrict__ P, int n) {
    float wsum = 1e-6f, idx = 0.f;
    for (int i = 0; i < n; ++i) {
        const float* b = P + BOFF + 4 * i;
        const float di = fmaxf(length(pos - load3(b)) - b[3], 0.f) + 0.06f;
        const float wi = b[3] / (di * di);
        wsum += wi; idx += wi * P[IOFF + i];
    }
    return idx / wsum;
}

// ---- analytic environment: a gradient backdrop (studio / sunset / neon blend) ...
__device__ float3 env_grad(float3 r, const float* __restrict__ P) {
    const float y = r.y, drift = P[26];
    float3 c = f3(0.f);
    if (P[36] > 0.f) {                                           // studio: grey cyclorama with a soft horizon
        const float3 s = mix(f3(0.004f, 0.004f, 0.005f), f3(0.055f, 0.057f, 0.064f), smoothstep(-0.7f, 0.9f, y))
                       + f3(0.03f) * expf(-y * y * 18.f);
        c += s * P[36];
    }
    if (P[37] > 0.f) {                                           // sunset: palette sky over a dark ground
        const float3 hz = srgb_to_linear(pal(drift + 0.04f, P + 13)) * 0.5f;
        const float3 hi = srgb_to_linear(pal(drift + 0.6f, P + 13)) * 0.05f;
        const float3 s = y > 0.f ? mix(hz, hi, sqrtf(saturate(y * 1.4f))) : mix(hz, f3(0.003f), saturate(-y * 3.5f));
        c += s * P[37];
    }
    if (P[38] > 0.f) {                                           // neon: near-black with a faint palette haze
        const float3 ne = srgb_to_linear(pal(drift + 0.45f + 0.15f * y, P + 13)) * 0.012f;
        c += ne * (0.4f + 0.6f * saturate(1.f - fabsf(y) * 2.f)) * P[38];
    }
    return c;
}
// ... plus NL coloured softboxes (box / disc / thin strip) seen along r; gbox scales the panels, ghalo their glow
__device__ float3 env_lights(float3 r, const float* __restrict__ P, float gbox, float ghalo) {
    float3 acc = f3(0.f);
    const float sat = P[43], period = P[25], drift = P[26];
    for (int i = 0; i < NL; ++i) {
        const float* L = P + LOFF + 16 * i;
        const float w = dot(r, load3(L));
        if (w < 0.05f) continue;
        float v = ghalo * L[14] * expf(-(1.f - w) * 10.f);
        if (gbox > 0.f && w > 0.12f) {
            const float iw = 1.f / w;
            const float qx = fabsf(dot(r, load3(L + 3))) * iw / L[9];
            const float qy = fabsf(dot(r, load3(L + 6))) * iw / L[10];
            const float dd = mix(fmaxf(qx, qy), sqrtf(qx * qx + qy * qy), L[11]);
            const float s = L[15];
            const float m = (1.f - smoothstep(1.f - s, 1.f + s, dd)) * (0.72f + 0.28f * saturate(1.f - dd * dd));
            v += gbox * L[12] * m;
        }
        if (v > 1e-4f) {
            const float3 lc = mix(f3(1.f), srgb_to_linear(pal(L[13] * period + drift, P + 13)), sat);
            acc += lc * v;
        }
    }
    return acc;
}

SDF_KERNEL
void mercury_blobs(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float drift = P[26], period = P[25];
    const int n = (int)P[28];
    const float k = P[29];

    // backdrop: gradient plus the lights' glow (no panels: the panels live only in the chrome)
    float3 col = env_grad(rd, P) * P[39] + env_lights(rd, P, 0.f, P[44]);

    // rim halo: closest approach of the ray to each blob (analytic, smooth, independent of the march)
    float3 halo = f3(0.f);
    for (int i = 0; i < n; ++i) {
        const float* b = P + BOFF + 4 * i;
        const float3 c = load3(b);
        const float tl = dot(c - ro, rd);
        if (tl <= 0.f) continue;
        const float r2 = b[3] * b[3];
        const float dl2 = dot2(ro + rd * tl - c);
        float g = r2 / (r2 + 4.f * fmaxf(dl2 - r2, 0.f) + 1e-4f);
        g = g * g;
        if (g > 1e-3f) halo += srgb_to_linear(pal(P[IOFF + i] * period + drift + 0.5f, P + 13)) * g;
    }
    halo *= P[42];

    const float2 bi = iSphere(ro - load3(P + 31), rd, P[30]);
    bool hit = false;
    if (bi.y > 0.f && bi.x < bi.y) {
        const float t0 = fmaxf(bi.x, 0.f), t1 = bi.y;
        // per-ray cull: a blob whose sphere grown by 1.25 k never comes near the ray cannot change the zero set
        // on this ray (quadratic smin), so the march only evaluates the blobs it can actually meet
        unsigned int mask = 0u;
        for (int i = 0; i < n; ++i) {
            const float* b = P + BOFF + 4 * i;
            const float3 c = load3(b);
            const float tc = clamp(dot(c - ro, rd), t0, t1);
            const float rr = b[3] + 1.25f * k + 0.05f;
            if (b[3] > 1e-4f && dot2(ro + rd * tc - c) < rr * rr) mask |= 1u << i;
        }
        if (mask) {
            auto map = [&](float3 p) { return blobs_d(p, P, mask); };
            const Hit h = march(map, ro, rd, t0, t1, 64, 0.6f * pix);
            hit = h.hit || (h.steps >= 64 && h.t < t1 && h.d < 0.03f);
            if (hit) {
                const float3 pos = ro + rd * h.t;
                const float3 nrm = calc_normal(map, pos, fmaxf(0.5f * pix * h.t, 5e-4f));
                const float cosv = saturate(dot(nrm, -rd));
                const float3 r = reflect(rd, nrm);
                const unsigned int all = 0xffffffffu;
                // Blackle's refcomp: sample the field along the reflected ray; where it dips, another blob is in
                // the way and the mirror shows a dark inter-reflection instead of the environment
                const float dn = fmaxf(dot(r, nrm), 0.3f);
                float occ = 0.25f * saturate(blobs_d(pos + r * 0.15f, P, all) / (0.15f * dn));
                occ += 0.35f * saturate(blobs_d(pos + r * 0.45f, P, all) / (0.45f * dn));
                occ += 0.40f * saturate(blobs_d(pos + r * 1.1f, P, all) / (1.1f * dn));
                occ = smoothstep(0.15f, 0.95f, occ);
                const float ao = saturate(0.35f + 0.65f * blobs_d(pos + nrm * 0.3f, P, all) * (1.f / 0.3f));

                const float rough = P[34];
                const float idx = blob_index(pos, P, n);
                const float3 tc = srgb_to_linear(pal(idx * period + drift + 0.3f, P + 13));
                const float3 tint = tc * (0.95f / fmaxf(vmax(tc), 0.05f));
                const float3 F0 = mix(f3(0.93f, 0.91f, 0.87f), tint, P[35]);
                const float f1 = 1.f - cosv, f5 = f1 * f1 * f1 * f1 * f1;
                const float3 F = F0 + (f3(1.f) - F0) * f5;
                const float3 envr = env_grad(r, P) + env_lights(r, P, P[40], 1.f + 2.f * rough);
                const float3 inter = envr * 0.1f + tint * 0.012f;
                const float3 refl = mix(inter, envr, occ);
                const float3 dif = env_grad(nrm, P) * (0.4f + 1.6f * rough) + env_lights(nrm, P, 0.f, 0.6f + 3.f * rough);
                col = (F * refl + F0 * dif * (0.12f + 0.3f * rough)) * ao;
                col += halo * (f1 * f1 * f1);                 // halo matches the backdrop at the silhouette
            }
        }
    }
    if (!hit) col += halo;
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"maxb": MAXB, "nl": NL, "loff": LOFF, "boff": BOFF, "ioff": IOFF}

# Light layouts per environment style: (dir, half_u, half_v, roundness, roll, intensity, pal_index, halo_extra).
# The dirs are in rig space (the rig rotates about y); styles blend continuously, so the image is fine mid-ease.
_STUDIO = [
    ((-0.65, 0.55, -0.55), 0.55, 0.40, 0.15, 0.0, 3.2, 0.00, 0.00),   # key softbox
    ((0.85, 0.25, -0.35), 0.40, 0.65, 0.15, 0.0, 1.7, 0.18, 0.00),    # fill panel
    ((0.05, 1.00, 0.15), 0.80, 0.80, 0.60, 0.0, 1.4, 0.36, 0.00),     # overhead
    ((0.45, 0.30, 0.85), 0.16, 0.90, 0.00, 0.2, 2.6, 0.55, 0.00),     # rim strip
    ((-0.75, 0.05, 0.65), 0.16, 0.90, 0.00, -0.2, 2.2, 0.72, 0.00),   # rim strip 2
    ((0.15, -0.75, -0.45), 0.60, 0.18, 0.30, 0.0, 0.9, 0.88, 0.00),   # floor bounce
]
_SUNSET = [
    ((-0.55, 0.10, 0.82), 0.16, 0.16, 1.00, 0.0, 9.0, 0.04, 0.30),    # sun
    ((0.85, 0.25, -0.35), 0.90, 0.50, 1.00, 0.0, 0.8, 0.12, 0.08),
    ((0.05, 1.00, 0.15), 1.20, 1.20, 1.00, 0.0, 0.5, 0.62, 0.08),     # sky dome
    ((0.45, 0.30, 0.85), 0.30, 0.30, 1.00, 0.0, 1.2, 0.08, 0.05),
    ((-0.75, 0.05, 0.65), 0.90, 0.12, 0.50, 0.0, 1.5, 0.02, 0.05),    # horizon band
    ((0.15, -0.75, -0.45), 0.60, 0.60, 1.00, 0.0, 0.25, 0.50, 0.00),
]
_NEON = [
    ((-0.65, 0.55, -0.55), 1.40, 0.035, 0.0, 0.5, 5.0, 0.00, 0.08),
    ((0.85, 0.25, -0.35), 1.40, 0.035, 0.0, -0.4, 5.0, 0.20, 0.08),
    ((0.05, 1.00, 0.15), 1.40, 0.035, 0.0, 0.9, 4.0, 0.40, 0.08),
    ((0.45, 0.30, 0.85), 1.40, 0.035, 0.0, 1.3, 5.0, 0.60, 0.08),
    ((-0.75, 0.05, 0.65), 1.40, 0.035, 0.0, 0.1, 5.0, 0.80, 0.08),
    ((0.15, -0.75, -0.45), 1.40, 0.035, 0.0, -1.0, 3.5, 0.90, 0.08),
]
_STYLES = (_STUDIO, _SUNSET, _NEON)
# per style: light saturation, rim halo, backdrop glow
_STYLE_GLOBALS = ((0.30, 0.05, 0.25), (0.85, 0.12, 1.00), (1.00, 0.35, 0.60))


def _norm(v):
    v = np.asarray(v, np.float64)
    return v / max(float(np.linalg.norm(v)), 1e-9)


def _lerp(a, b, t):
    return a + (b - a) * t


class MercuryBlobs(Source):
    name = "mercury_blobs"
    label = "Mercury blobs"
    description = ("Free-floating liquid-chrome metaballs merging and splitting on Lissajous orbits, mirrored in "
                   "coloured studio softboxes (3D raymarched).")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.02),
        "blob_count": num(6.0, 14.0, 10.0, "Blobs (at full energy)", step=0.1, tier="regional", basic=True),
        "smoothness": num(0.1, 1.2, 0.55, "Melt (smooth-min k)", tier="regional"),
        "spread": num(0.5, 1.6, 1.0, "Spread", tier="regional"),
        "env_style": num(0.0, 2.0, 0.4, "Environment (studio -> sunset -> neon)", tier="regional", basic=True),
        "roughness": num(0.0, 1.0, 0.12, "Roughness", tier="regional"),
        "metal_tint": num(0.0, 1.0, 0.2, "Metal tint (silver -> palette)", tier="regional"),
        "flash": num(0.0, 2.0, 1.0, "Beat highlight flash", tier="regional"),
        "orbit_speed": num(0.0, 3.0, 1.0, "Orbit speed", unit="×", tier="realtime", bind="rate"),
        "light_speed": num(0.0, 3.0, 1.0, "Light rig rotation", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("mercury_blobs")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.freq = rng.uniform(0.35, 1.05, (MAXB, 3)) * rng.choice([-1.0, 1.0], (MAXB, 3))
        self.phs = rng.uniform(0.0, 2.0 * math.pi, (MAXB, 3))
        self.amp = rng.uniform(0.45, 1.0, (MAXB, 3)) * np.array([1.35, 0.8, 0.95])
        self.rnd = rng.uniform(0.0, 1.0, (MAXB, 3))
        self.pidx = np.arange(MAXB) * 0.071 + rng.uniform(0.0, 0.35, MAXB)
        self.orb = float(rng.uniform(0.0, 40.0))    # Lissajous phase (host float64, never uploaded)
        self.light_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.cam_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.breath = 0.0
        self.drift = 0.0
        self.e_s = None

    def _lights(self, style: float, rough: float, hl: float) -> np.ndarray:
        s = min(max(style, 0.0), 2.0)
        if s <= 1.0:
            A, B, t = _STYLES[0], _STYLES[1], s
        else:
            A, B, t = _STYLES[1], _STYLES[2], s - 1.0
        R = np.asarray(rot_matrix((0.0, 1.0, 0.0), self.light_ph), np.float64).reshape(3, 3)
        tilt = 0.22 * math.sin(0.37 * self.light_ph)
        R = R @ np.asarray(rot_matrix((1.0, 0.0, 0.0), tilt), np.float64).reshape(3, 3)
        out = np.zeros((NL, 16), np.float64)
        for i in range(NL):
            a, b = A[i], B[i]
            d = _norm(_lerp(np.asarray(a[0]), np.asarray(b[0]), t))
            hu, hv, rnd, roll, inten, pidx, hx = (_lerp(a[j], b[j], t) for j in range(1, 8))
            right = np.cross(d, (0.0, 1.0, 0.0))
            if np.linalg.norm(right) < 1e-3:
                right = np.cross(d, (0.0, 0.0, 1.0))
            right = _norm(right)
            up = np.cross(right, d)
            c, sn = math.cos(roll), math.sin(roll)
            right, up = c * right + sn * up, -sn * right + c * up
            out[i, 0:3] = R @ d
            out[i, 3:6] = R @ right
            out[i, 6:9] = R @ up
            out[i, 9] = hu
            out[i, 10] = hv
            out[i, 11] = rnd
            out[i, 12] = inten / (1.0 + 1.2 * rough)
            out[i, 13] = pidx
            out[i, 14] = inten * (0.015 + 0.08 * rough + hx) * hl
            out[i, 15] = min(0.9, 0.03 + 0.6 * rough)
        return out

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrate rates (pulse kicks rates only)
        self.orb += float(p["orbit_speed"]) * spd * (0.16 + 0.6 * e + 0.35 * pulse) * dt
        self.light_ph += float(p["light_speed"]) * spd * (0.03 + 0.2 * e) * dt
        self.cam_ph += spd * (0.04 + 0.14 * e) * dt
        self.breath += spd * (0.5 + 1.2 * e) * dt
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- blobs: Lissajous orbits about the origin; the blob count fades radii continuously
        spread = float(p["spread"])
        amp = spread * (0.85 + 0.25 * es)
        n_eff = float(p["blob_count"]) * (0.6 + 0.4 * es)
        n = min(MAXB, max(1, int(math.ceil(n_eff - 1e-3))))
        rscale = (10.0 / max(n_eff, 1.0)) ** 0.25 * (0.8 + 0.2 * spread) * (1.0 + 0.16 * pulse)
        k = float(p["smoothness"]) * (0.7 + 0.6 * es) * (1.0 + 0.25 * pulse)
        ph = self.orb * self.freq + self.phs
        cen = self.amp * amp * np.sin(ph)
        blobs = np.zeros((MAXB, 4), np.float32)
        bound = 0.0
        for i in range(n):
            fade = smoothstep(0.0, 1.0, n_eff - i)
            br = 1.0 + (0.04 + 0.08 * es) * math.sin(self.breath * (0.7 + 0.6 * self.rnd[i, 1]) + 6.283 * self.rnd[i, 2])
            rad = (0.36 + 0.3 * self.rnd[i, 0]) * rscale * br * fade
            blobs[i, :3] = cen[i]
            blobs[i, 3] = rad
            bound = max(bound, float(np.linalg.norm(cen[i])) + rad)
        bound += 0.3 * k + 0.1

        # ---- camera: slow orbit around the sculpture
        D = 3.4 + 2.6 * spread
        pitch = 0.12 + 0.14 * math.sin(0.61 * self.cam_ph + 1.0)
        eye = orbit_eye((0.0, 0.0, 0.0), D, self.cam_ph, pitch)
        roll = 0.03 * math.sin(0.43 * self.cam_ph)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=40.0, roll=roll)

        # ---- environment
        style = float(p["env_style"])
        w0 = max(0.0, 1.0 - style)
        w1 = max(0.0, 1.0 - abs(style - 1.0))
        w2 = max(0.0, style - 1.0)
        g = [sum(w * sg[j] for w, sg in zip((w0, w1, w2), _STYLE_GLOBALS)) for j in range(3)]
        rough = float(p["roughness"])
        flash = float(p["flash"])
        hl = (0.75 + 0.55 * e) * (1.0 + 0.9 * flash * pulse)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"])
        Ph[28] = n
        Ph[29] = k
        Ph[30] = bound
        Ph[31:34] = (0.0, 0.0, 0.0)
        Ph[34] = rough
        Ph[35] = p["metal_tint"]
        Ph[36:39] = (w0, w1, w2)
        Ph[39] = 1.0
        Ph[40] = hl
        Ph[41] = pulse
        Ph[42] = g[1] * (0.6 + 0.6 * e) * (1.0 + 1.5 * flash * pulse)
        Ph[43] = g[0]
        Ph[44] = g[2]
        Ph[LOFF:BOFF] = self._lights(style, rough, 1.0 + 0.5 * flash * pulse).reshape(-1)
        Ph[BOFF:IOFF] = blobs.reshape(-1)
        Ph[IOFF:IOFF + MAXB] = self.pidx
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
