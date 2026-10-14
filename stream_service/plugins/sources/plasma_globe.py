"""Plasma globe: a Tesla plasma ball.  Jagged volumetric filaments arc from a hot core to the glass, seen through a
refracting, Fresnel-lit sphere shell whose reflections swirl.

Source: https://www.shadertoy.com/view/XsjXRm  (copy also at
        https://github.com/ArthurTent/ShaderAmp/blob/main/dist/shaders/PlasmaGlobe.frag)
Author: nimitz (@stormoid), 'Plasma Globe'
License: CC-BY-NC-SA-3.0  ('License Creative Commons Attribution-NonCommercial-ShareAlike 3.0 Unported License' in
         the shader header: "Plasma Globe by nimitz (twitter: @stormoid)")
license_class: noncommercial
Ported from GLSL by smfx (ideas/changes: the filament model is nimitz's -- a segment from the centre to the glass,
evaluated in a space squashed by ins*outs so it fattens at the core and at the glass contact, bent by his "sins"
travelling triangle waves, sphere-traced then volumetrically accumulated with a flickering 1D noise, depth
attenuation and max-combined across rays; the glass is his -log|flow - 0.01| reflection of a rotated value-noise
flow.  Changes: per-ray frames and rotations integrated on the host; every time term is a host phase wrapped on an
exact period (sins phases mod 1, flicker and flow noise on 1024/256-periodic lattices); each ray is bounded by an
analytic ray-vs-cone test (plus a core sphere) so the march only runs where its filament can be; a 4th fine sins
term; angular filament glow; explicit emissive core and gas haze; refraction at the glass, Schlick Fresnel rim,
specular highlight and outer halo; palette colour with drift; ACES tonemap.)

Macros: energy -> filament brightness, active ray count (fractional rays fade in, slewed), zigzag/flicker/crawl
rate (x(0.4+1.2e')), zigzag amplitude and fine detail, core glow, glow halo, gas haze, glass swirl rate, camera orbit
rate.  pulse -> filament flare (thickness + brightness, at most +35%), core flash and a kick into the crawl rate (all
local to the globe).  speed scales every rate; palette indexes the filaments (by position along the ray and heat),
the core, haze and halo; glass_tint blends the glass from cool blue/violet to palette colours.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

MAXF = 13          # nimitz's NUM_RAYS
FB = 64            # first per-filament slot in P
FS = 16            # floats per filament
NP = FB + FS * MAXF
SINS_W = (0.5, 0.435, 0.378)    # 1/z of nimitz's three sins terms
TAU = 2.0 * math.pi

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 core radius | 29 filament thickness
#  30 zigzag amplitude | 31 fine-term weight | 32..35 sins phases (mod 1) | 36 flicker phase (mod 1024)
#  37 cone cos | 38 glow sigma (rad) | 39 inside smoothstep width | 40 filament brightness | 41 core gain
#  42 refraction eta | 43 glass tint | 44 glass gain | 45..48 flow phases (mod 256) | 49 haze | 50 sins centre
#  51 glow gain | 52 ray count | 53 ins lo | 54 ins hi | 55 halo gain | 56 core test radius | 57 depth ref
#  58 inner mask start | 59..61 light dir
#  64 + 16 i: axis(3) u(3) v(3) fade o1 o2 flicker-offset colour-offset
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MAXF %(maxf)d
#define FB %(fb)d
#define FS %(fs)d
#define MSTEPS 30
#define VSTEPS 19
#define VSTEP 0.018f

__device__ __forceinline__ float tri(float x) { return fabsf(fract(x) - 0.5f); }

// nimitz's "sins": travelling triangle waves along the radius (phases are host-wrapped mod 1: exact)
__device__ __forceinline__ float sins(float x, const float* __restrict__ P) {
    return tri(1.4f * x - P[32]) * 0.5f + tri(1.82f * x - P[33]) * 0.435f
         + tri(2.366f * x - P[34]) * 0.378f + tri(4.1f * x - P[35]) * P[31] - P[50];
}
// 1D value noise, lattice periodic in 1024 (host wraps the phase there)
__device__ __forceinline__ float n1(float x, unsigned int seed) {
    const float i = floorf(x);
    float f = x - i;
    f = f * f * (3.f - 2.f * f);
    const int ii = (int)i;
    return mix(hash1((unsigned int)(ii & 1023), seed), hash1((unsigned int)((ii + 1) & 1023), seed), f);
}
// 3D value noise, lattice periodic in 256 on every axis
__device__ float vn3(float3 p, unsigned int s8) {
    const float3 i = floor(p);
    float3 f = p - i;
    f = f * f * (3.f - 2.f * f);
    const unsigned int x0 = ((int)i.x) & 255, y0 = ((int)i.y) & 255, z0 = ((int)i.z) & 255;
    const unsigned int x1 = (x0 + 1u) & 255u, y1 = (y0 + 1u) & 255u, z1 = ((z0 + 1u) & 255u) | s8;
    const unsigned int zz = z0 | s8;
    const float a = hash1(x0, y0, zz), b = hash1(x1, y0, zz), c = hash1(x0, y1, zz), d = hash1(x1, y1, zz);
    const float e = hash1(x0, y0, z1), g = hash1(x1, y0, z1), h = hash1(x0, y1, z1), k = hash1(x1, y1, z1);
    return mix(mix(mix(a, b, f.x), mix(c, d, f.x), f.y), mix(mix(e, g, f.x), mix(h, k, f.x), f.y), f.z);
}
// nimitz's flow (after XdfXRj): rotated octaves of sin(noise * 6); time enters as per-octave wrapped offsets
__device__ float flow(float3 p, const float* __restrict__ P, float sg, unsigned int s8) {
    float z = 2.f, rz = 0.f;
    const float3 bp = p;
    #pragma unroll
    for (int o = 0; o < 4; ++o) {
        rz += (sinf(vn3(p + f3(sg * P[45 + o]), s8) * 6.f) * 0.5f + 0.5f) / z;
        p = mix(bp, p, 0.6f) * 2.01f;
        z *= 2.f;
        p = f3(0.8f * p.y + 0.6f * p.z, -0.8f * p.x + 0.36f * p.y - 0.48f * p.z, -0.6f * p.x - 0.48f * p.y + 0.64f * p.z);
    }
    return rz;
}

struct Fil { float3 a, u, v; float o1, o2; };

// distance to filament F in nimitz's squashed space; k = ins*outs (his id), lp = |p|, chord = angular distance
__device__ __forceinline__ float fil_d(float3 p, const Fil& F, const float* __restrict__ P,
                                       float& k, float& lp, float& chord) {
    lp = length(p);
    const float ins = smoothstep(P[53], P[54], lp);
    const float outs = 0.15f + smoothstep(0.f, 0.15f, fabsf(lp - 1.f));
    k = ins * outs;
    const float w1 = P[30] * 0.30f * sins(lp + F.o1, P);
    const float w2 = P[30] * 0.28f * sins(lp + F.o2, P);
    const float3 en = normalize(F.a + F.u * w1 + F.v * w2);
    const float3 q = p * k;
    const float h = saturate(dot(q, en));
    chord = length(p * (1.f / fmaxf(lp, 1e-4f)) - en);
    return length(q - en * h) * 0.5f - P[29];
}

// [ta, tb] where the line o + d t lies inside the cone (apex 0, unit axis a, cos^2 = c2, half-angle < 90 deg)
__device__ __forceinline__ void cone_interval(float3 o, float3 d, float3 a, float c2, float& ta, float& tb) {
    const float da = dot(d, a), oa = dot(o, a);
    float A = da * da - c2;
    const float B = da * oa - c2 * dot(d, o);
    const float C = oa * oa - c2 * dot(o, o);
    if (fabsf(A) < 1e-6f) A = A < 0.f ? -1e-6f : 1e-6f;
    ta = 1e9f; tb = -1e9f;
    const float disc = B * B - A * C;
    if (disc < 0.f) return;
    const float sq = sqrtf(disc);
    float r1 = (-B - sq) / A, r2 = (-B + sq) / A;
    if (r1 > r2) { const float tmp = r1; r1 = r2; r2 = tmp; }
    if (A < 0.f) {                                   // inside between the roots: keep it if on the positive nappe
        if (oa + 0.5f * (r1 + r2) * da >= 0.f) { ta = r1; tb = r2; }
    } else if (da > 0.f) { ta = r2; tb = 1e9f; }     // line runs along the axis: the far piece is the + nappe
    else { ta = -1e9f; tb = r1; }
}

__device__ __forceinline__ float3 refr(float3 I, float3 N, float eta) {     // N faces against I
    const float ci = -dot(N, I);
    const float k = 1.f - eta * eta * (1.f - ci * ci);
    return k < 0.f ? reflect(I, N) : normalize(I * eta + N * (eta * ci - sqrtf(k)));
}

SDF_KERNEL
void plasma_globe(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                  const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 eye = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float* PAL = P + 13;
    const float period = P[25], drift = P[26];
    const unsigned int s8 = (seed & 0xffffu) << 8;

    const float3 hazec = srgb_to_linear(pal(drift + 0.42f * period, PAL));
    const float3 haloc = srgb_to_linear(pal(drift + 0.30f * period, PAL));

    // background: near-black violet, faint vertical gradient, light spill (halo) around the globe
    const float tc = -dot(eye, rd);
    const float b2 = fmaxf(dot2(eye) - tc * tc, 0.f);
    const float b = sqrtf(b2);
    float3 bg = f3(0.0040f, 0.0028f, 0.0085f) * (1.15f - 0.4f * rd.y) + hazec * 0.004f;
    if (tc > 0.f) bg += haloc * (P[55] * expf(-5.f * fmaxf(b - 1.f, 0.f)));
    float3 col = bg;

    const float Dc = length(eye);
    const float aw = pix * Dc * 1.25f;                               // ~1 pixel at the globe, world units
    if (tc > 0.f && b < 1.f + aw) {
        const float hh = sqrtf(fmaxf(1.f - b2, 0.f));
        const float3 pos = normalize(eye + rd * (tc - hh));         // entry point on the glass (unit sphere)
        const float3 n = pos;
        const float cosi = saturate(-dot(n, rd));
        const float3 rdi = refr(rd, n, P[42]);                      // refracted interior ray
        const float tex = fmaxf(-2.f * dot(pos, rdi), 0.f);         // to the far side of the glass

        // ---------------- filaments
        const float tcc = -dot(pos, rdi);
        const float bc2 = fmaxf(dot2(pos) - tcc * tcc, 0.f);
        const float Rct = P[56];
        float c0 = 1e9f, c1 = -1e9f;
        if (bc2 < Rct * Rct) { const float s = sqrtf(Rct * Rct - bc2); c0 = tcc - s; c1 = tcc + s; }
        const int nf = min((int)P[52], MAXF);
        const float c2 = P[37] * P[37];
        const float isig = 1.f / P[38];
        float3 fil = f3(0.f), glow = f3(0.f);
        for (int i = 0; i < nf; ++i) {
            const float* fp = P + FB + FS * i;
            const float fade = fp[9];
            if (fade <= 0.002f) continue;
            Fil F;
            F.a = load3(fp); F.u = load3(fp + 3); F.v = load3(fp + 6); F.o1 = fp[10]; F.o2 = fp[11];
            float ta, tb;
            cone_interval(pos, rdi, F.a, c2, ta, tb);
            ta = fmaxf(fminf(ta, c0), 0.f);
            tb = fminf(fmaxf(tb, c1), tex);
            if (tb <= ta) continue;
            // sphere-trace (nimitz: step 1.2 h), tracking the closest angular approach for the glow
            float t = ta, kk, lp, ch, cmin = 1e9f;
            float d = fil_d(pos + rdi * t, F, P, kk, lp, ch);
            bool hit = false;
            for (int s = 0; s < MSTEPS; ++s) {
                cmin = fminf(cmin, ch + 0.6f * (1.f - smoothstep(P[58], P[58] + 0.2f, lp)));
                if (d < 0.001f) { hit = true; break; }
                t += d * 1.2f;
                if (t > tb) break;
                d = fil_d(pos + rdi * t, F, P, kk, lp, ch);
            }
            if (!hit && t <= tb) hit = true;                         // ran out of steps creeping along it
            const float3 ph = pos + rdi * t;
            const float att = 1.f / (logf(fmaxf(length(ph - eye) - P[57], 1.25f)) + 0.75f);
            if (hit) {
                // volumetric pass through the filament (nimitz's vmarch), with an early out once through it
                float3 p = ph;
                float I = 0.f, Iid = 0.f;
                bool inside = false;
                for (int s = 0; s < VSTEPS; ++s) {
                    float k2, lp2, ch2;
                    const float dd = fil_d(p, F, P, k2, lp2, ch2);
                    p += rdi * VSTEP;
                    if (dd < 0.f) {
                        inside = true;
                        float w = smoothstep(0.f, P[39], -dd) * smoothstep(0.04f, 0.2f, fabsf(lp2 - 1.1f))
                                * smoothstep(P[58], P[58] + 0.24f, lp2);
                        w *= 1.2f - 1.1f * n1(lp2 * 2.f + fp[12] + P[36], 0x2545u);
                        I += w; Iid += w * k2;
                    } else if (inside || dd > 0.08f) break;
                }
                if (I > 0.f) {
                    const float idx = Iid / I;
                    const float heat = fminf(I * 3.f, 4.f);
                    const float3 c = srgb_to_linear(pal((idx * 0.8f + 0.06f * heat) * period + drift + fp[13], PAL));
                    fil = max(fil, c * (I * 3.f * P[40] * fade * att));
                }
            }
            const float g = expf(-square(cmin * isig));
            if (g > 0.002f)
                glow += srgb_to_linear(pal(0.55f * period + drift + fp[13], PAL)) * (g * fade * att);
        }

        // ---------------- core (emissive electrode + halo) and gas haze
        const float rc = P[28];
        float core = 0.f;
        if (tcc > 0.f && tcc < tex) {
            const float q = bc2 / (rc * rc);
            core = smoothstep(1.f, 0.8f, sqrtf(q)) * (0.55f + 0.9f * sqrtf(saturate(1.f - q)))
                 + 0.45f * powf(1.f / (q + 0.35f), 1.5f) * 0.25f;
        } else {
            const float q = (tcc <= 0.f ? dot2(pos) : dot2(pos + rdi * tex)) / (rc * rc);
            core = 0.11f * powf(1.f / (q + 0.35f), 1.5f);
        }
        const float3 corec = mix(srgb_to_linear(pal(drift + 0.12f * period, PAL)), f3(1.f, 0.95f, 1.f), 0.45f);
        float3 inner = fil + glow * P[51] + corec * (core * P[41]) + hazec * (tex * P[49]);

        // ---------------- glass: Fresnel, swirling reflections (nimitz), highlight, rim
        const float fre = 0.04f + 0.96f * powf(1.f - cosi, 5.f);
        const float3 rf = reflect(rd, n);
        const float3 pos2 = pos + rdi * tex;
        const float3 rf2 = reflect(rdi, pos2);
        const float nz = fminf(-logf(fabsf(flow(rf * 1.2f, P, 1.f, s8) - 0.01f) + 1e-3f), 4.f);
        const float nz2 = fminf(-logf(fabsf(flow(rf2 * 1.2f, P, -1.f, s8) - 0.01f) + 1e-3f), 4.f);
        const float tint = P[43];
        const float3 gA = mix(f3(0.10f, 0.12f, 0.55f), srgb_to_linear(pal(drift + 0.5f, PAL)) * 0.75f, tint);
        const float3 gB = mix(f3(0.50f, 0.16f, 0.55f), srgb_to_linear(pal(drift + 0.8f, PAL)) * 0.75f, tint);
        float3 glass = (gA * (0.1f * nz * nz) + gB * (0.05f * nz2 * nz2)) * P[44];
        const float sp = saturate(dot(rf, load3(P + 59)));
        glass += f3(1.f) * (powf(sp, 220.f) * 2.5f) + gA * (powf(sp, 14.f) * 0.12f);
        glass += (gA * 1.3f + haloc * 0.25f) * (fre * (0.6f + 0.8f * P[44]));
        const float3 globe = inner * (1.f - fre) + glass + bg * 0.35f;
        const float cov = smoothstep(1.f + 0.5f * aw, 1.f - 0.5f * aw, b);
        col = mix(bg, globe, cov);
    }
    col = vignette(col * P[27], x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col)));
}
""" % {"maxf": MAXF, "fb": FB, "fs": FS}


def _rotate(v: np.ndarray, k: np.ndarray, th: np.ndarray) -> np.ndarray:
    """Rodrigues: rotate rows of v about unit rows of k by th (radians)."""
    c = np.cos(th)[:, None]
    s = np.sin(th)[:, None]
    return v * c + np.cross(k, v) * s + k * np.sum(k * v, axis=1, keepdims=True) * (1.0 - c)


def _norm(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-9)


def _smooth(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class PlasmaGlobe(Source):
    name = "plasma_globe"
    label = "Plasma globe"
    description = "A Tesla plasma ball: jagged volumetric filaments arc from a hot core to a refracting, Fresnel-lit glass shell (3D)."
    params = {
        **standard_scene_params(palette="candy", render_scale=0.5, period=1.0, drift=0.02),
        "filament_density": num(4.0, 13.0, 9.0, "Filaments", step=0.1, tier="regional", basic=True),
        "filament_thickness": num(0.5, 2.0, 1.0, "Filament thickness", tier="regional"),
        "globe_size": num(0.0, 1.0, 0.6, "Globe size", tier="regional"),
        "glass_tint": num(0.0, 1.0, 0.35, "Glass tint", tier="regional", basic=True),
        "core_size": num(0.0, 1.0, 0.5, "Core size", tier="regional"),
        "refraction": num(0.0, 1.0, 0.4, "Refraction", tier="regional"),
        "flare_kick": num(0.0, 2.0, 1.0, "Beat flare", tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("plasma_globe")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        self.seed = int(params["seed"]) & 0xFFFFFFFF
        rng = np.random.default_rng(self.seed)
        # ray base directions: a jittered Fibonacci sphere, randomly ordered (so fading rays are spread out)
        i = np.arange(MAXF) + 0.5
        zz = 1.0 - 2.0 * i / MAXF
        rr = np.sqrt(np.maximum(1.0 - zz * zz, 0.0))
        az = math.pi * (3.0 - math.sqrt(5.0)) * i
        a0 = np.stack([rr * np.cos(az), zz, rr * np.sin(az)], axis=1)
        a0 = _norm(a0 + rng.normal(0.0, 0.18, a0.shape))
        a0 = a0[rng.permutation(MAXF)]
        ref = _norm(rng.normal(size=(MAXF, 3)))
        u0 = _norm(np.cross(a0, ref))
        v0 = np.cross(a0, u0)
        self.frame0 = (a0, u0, v0)
        self.rot_k = _norm(rng.normal(size=(MAXF, 3)))
        self.rot_w = rng.uniform(0.05, 0.24, MAXF) * rng.choice([-1.0, 1.0], MAXF)
        self.rot_th = rng.uniform(0.0, TAU, MAXF)
        self.o1 = rng.uniform(0.0, 8.0, MAXF)
        self.o2 = rng.uniform(0.0, 8.0, MAXF)
        self.flick_off = rng.uniform(0.0, 600.0, MAXF)
        self.col_off = rng.uniform(-0.07, 0.07, MAXF)
        self.sph = [0.0, 0.0, 0.0, 0.0]      # sins phases (mod 1)
        self.flick = 0.0                     # flicker noise phase (mod 1024)
        self.flow = [0.0, 0.0, 0.0, 0.0]     # glass flow phases (mod 256)
        self.yaw = float(rng.uniform(0.0, TAU))
        self.pitch_ph = float(rng.uniform(0.0, TAU))
        self.drift = 0.0
        self.e_fast = None
        self.e_slow = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        ep = _smooth((e - 0.1) / 0.75)                       # e' = smoothstep(0.1, 0.85, e)
        af = 1.0 - math.exp(-dt / 0.25) if dt > 0 else 1.0
        asl = 1.0 - math.exp(-dt / 1.6) if dt > 0 else 1.0
        self.e_fast = ep if self.e_fast is None else self.e_fast + (ep - self.e_fast) * af
        self.e_slow = ep if self.e_slow is None else self.e_slow + (ep - self.e_slow) * asl
        ef, es = self.e_fast, self.e_slow
        fk = float(p["flare_kick"])
        kp = pulse * (0.3 + 0.7 * e) * fk                   # beat envelope, energy-scaled, with its gain

        # ---- rates (kicks only ever add to rates)
        jr = (0.4 + 1.2 * ep) * spd * (1.0 + 1.5 * kp)
        for j, r in enumerate((0.3, 2.3, 5.65, 8.5)):
            self.sph[j] = wrap(self.sph[j] + r * jr * dt, 1.0)
        self.flick = wrap(self.flick + 5.5 * jr * dt, 1024.0)
        fr = (0.45 + 0.6 * ep) * spd
        for o in range(4):
            self.flow[o] = wrap(self.flow[o] + 0.85 * (1.0 + 0.3 * o) * fr * dt, 256.0)
        self.rot_th = np.mod(self.rot_th + self.rot_w * (0.45 + 0.8 * ef) * spd * dt, TAU)
        self.yaw = wrap(self.yaw + (0.10 + 0.14 * ef) * spd * dt, TAU)
        self.pitch_ph = wrap(self.pitch_ph + 0.07 * spd * dt, TAU)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- camera: orbit the globe (globe_size = camera distance)
        D = 7.5 + (3.4 - 7.5) * float(p["globe_size"])
        pitch = 0.08 + 0.32 * math.sin(self.pitch_ph)
        eye = (D * math.cos(pitch) * math.sin(self.yaw), D * math.sin(pitch), D * math.cos(pitch) * math.cos(self.yaw))
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=28.0, roll=0.04 * math.sin(0.6 * self.pitch_ph))

        # ---- rays: frames, fades
        a0, u0, v0 = self.frame0
        a = _rotate(a0, self.rot_k, self.rot_th)
        u = _rotate(u0, self.rot_k, self.rot_th)
        v = _rotate(v0, self.rot_k, self.rot_th)
        n_eff = max(3.0, float(p["filament_density"]) * (0.72 + 0.28 * es ** 1.5))
        fades = np.clip(n_eff - np.arange(MAXF), 0.0, 1.0)
        fades = fades * fades * (3.0 - 2.0 * fades)
        nf = int(min(MAXF, math.ceil(n_eff - 1e-4)))

        # ---- shape / light levels
        tm = float(p["filament_thickness"])
        thick = 0.011 * tm * (1.0 + 0.6 * kp)
        A = 0.7 + 0.8 * _smooth(ef)                         # zigzag amplitude
        fine = 0.06 + 0.32 * ef ** 1.5                      # 4th (fine) sins term
        centre = 0.25 * (sum(SINS_W) + fine)
        maxw = 0.30 * A * centre                            # max deviation per axis (tan)
        sigma = 0.03 + 0.4 * thick
        alpha = min(1.35, math.atan(1.4143 * maxw) + 11.5 * thick + 3.0 * sigma + 0.03)
        rc = 0.05 + 0.17 * float(p["core_size"])
        ins_lo = rc * 0.85
        bright = (0.55 + 0.65 * ef) * (1.0 + min(0.35, 0.35 * kp))
        core_g = (0.6 + 0.9 * ef) * (1.0 + 1.1 * kp)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.3
        Ph[28] = rc
        Ph[29] = thick
        Ph[30] = A
        Ph[31] = fine
        Ph[32:36] = self.sph
        Ph[36] = self.flick
        Ph[37] = math.cos(alpha)
        Ph[38] = sigma
        Ph[39] = 0.015 * tm
        Ph[40] = bright
        Ph[41] = core_g
        Ph[42] = 1.0 / (1.0 + 0.5 * float(p["refraction"]))
        Ph[43] = p["glass_tint"]
        Ph[44] = 0.8 + 0.2 * ef
        Ph[45:49] = self.flow
        Ph[49] = 0.03 * (0.6 + 0.8 * ef)
        Ph[50] = centre
        Ph[51] = 0.32 * (0.6 + 0.6 * ef) * (1.0 + 0.5 * kp)
        Ph[52] = nf
        Ph[53] = ins_lo
        Ph[54] = ins_lo + 0.35
        Ph[55] = 0.07 * (0.6 + 0.6 * ef) * (1.0 + 0.25 * kp)
        Ph[56] = ins_lo + 0.1 + 6.0 * thick
        Ph[57] = D - 3.0
        Ph[58] = rc * 0.75
        Ph[59:62] = (-0.42, 0.78, 0.46)
        blk = Ph[FB:FB + FS * MAXF].reshape(MAXF, FS)
        blk[:, 0:3] = a
        blk[:, 3:6] = u
        blk[:, 6:9] = v
        blk[:, 9] = fades
        blk[:, 10] = self.o1
        blk[:, 11] = self.o2
        blk[:, 12] = self.flick_off
        blk[:, 13] = self.col_off
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
