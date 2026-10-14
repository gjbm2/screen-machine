"""KIFS crystal: a single rotating kaleidoscopic-IFS gem, faceted lattice with specular glints and an inner glow.

Source: https://www.shadertoy.com/view/7tj3DG ("Random friday fractal");
        http://www.fractalforums.com/ifs-iterated-function-systems/kaleidoscopic-(escape-time-ifs)/ (KIFS idea)
Author: Marten Range (mrange, 'Random friday fractal', 7tj3DG, CC0); KIFS method by Knighty (idea)
License: CC0-1.0
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the distance estimate (smooth abs fold, two smooth reflection planes,
        two rotations, scale, box-minus-sphere element carved into the previous levels) is mrange's, with the fold
        angles, scale, offset, fold smoothness and a fractional iteration count exposed as eased knobs, a
        box-minus-sphere <-> sphere-minus-box carve morph, bounding-sphere early out, step-count AO instead of
        soft shadows, orbit-trap palette colouring, emissive cores, a near-miss halo and the sdf3d light rig.)

Look: one crystal on black, orbited by the camera.  Each IFS level folds space, rotates it by two angles, scales
it by ``scale`` and shifts it by ``offset``; from level 3 on, each level adds its element (a hollow box with a core
sphere) and carves a gap around itself out of the coarser levels, giving a faceted lattice.  The fold angles swing
slowly around their base values, so the gem morphs continuously and never pops.

Macros (e' = energy slewed for geometry):
  energy -> fold-swing angular speed (integrated), camera orbit speed, specular / glint gain, core glow and halo
            gain, and sharper folds (smaller smoothing k) as it rises.
  pulse  -> a +0.035 scale bump (an amplitude, decays with the envelope), an inner emissive flash in the cores, a
            rim punch and a short kick to the swing and orbit rates (rates only: nothing jumps).
  speed scales every rate; colour: pal((level + orbit trap + depth) * period + drift); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, rig_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

MAX_IT = 7
STEPS = 64
NP = 72

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 zf (scale) | 29 offset / zf | 30 fold k
#  31 iterations (4..7, fractional) | 32 carve | 33,34 cos/sin fold angle a | 35,36 cos/sin fold angle b
#  37 bound radius | 38 glint gain | 39 core glow | 40 halo gain | 41 pulse | 42 camera distance | 43 glow_k
#  44 nominal radius | 45..61 rig
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define KC_MAXIT %(maxit)d
#define KC_STEPS %(steps)d

__device__ __forceinline__ float kc_pmin(float a, float b, float k) {
    const float h = saturate(0.5f + 0.5f * (b - a) / k);
    return b + (a - b) * h - k * h * (1.f - h);
}
__device__ __forceinline__ float kc_pmax(float a, float b, float k) { return -kc_pmin(-a, -b, k); }
__device__ __forceinline__ float kc_pabs1(float a, float k) { return -kc_pmin(a, -a, k); }

struct KcInfo { float lvl; float trap; float core; };

// mrange's element: a box with a sphere carved out of it, plus a core sphere.  carve morphs it to the dual
// (a sphere with a box carved out of it, plus a core box).  Linear SDF blends: Lipschitz <= 1, continuous.
__device__ __forceinline__ float kc_element(float3 p, float carve, float& core) {
    const float r = length(p);
    float shell = sdBox(p, f3(0.25f)) - 0.01f;
    float hole = r - 0.30f;
    core = r - 0.25f;
    if (carve > 1e-3f) {
        shell = mix(shell, r - 0.27f, carve);
        hole = mix(hole, sdBox(p, f3(0.20f)), carve);
        core = mix(core, sdBox(p, f3(0.165f)) - 0.01f, carve);
    }
    return fminf(kc_pmax(shell, -hole, 0.05f), core);
}

template <bool INFO>
__device__ __forceinline__ float kc_de(float3 p, const float* __restrict__ P, KcInfo& inf) {
    const float zf = P[28], off = P[29], k = P[30], iters = P[31], carve = P[32];
    const float ca = P[33], sa = P[34], cb = P[35], sb = P[36];
    const float3 nz = f3(0.70710678f, 0.f, -0.70710678f);
    const float3 ny = f3(0.70710678f, -0.70710678f, 0.f);
    const int n = min(max((int)ceilf(iters - 1e-3f), 4), KC_MAXIT);
    const float fr = saturate(iters - (float)(n - 1));        // weight of the last (fractional) level
    float d = 1e6f, z = 1.f, best = 1e6f;
    if (INFO) { inf.lvl = 3.f; inf.trap = 1e6f; inf.core = 0.f; }
    #pragma unroll 1
    for (int i = 0; i < KC_MAXIT; ++i) {
        if (i >= n) break;
        float dd = 1e6f, core = 1e6f;
        if (i > 2) {                                           // levels 0..2 only fold space (as in the original)
            dd = kc_element(p, carve, core) / z;
            core /= z;
        }
        if (INFO && i > 2) inf.trap = fminf(inf.trap, dot(p, p));     // trap over the visible levels only
        z *= zf;
        p = p * zf;
        { const float x = p.x * ca + p.y * sa; p.y = -p.x * sa + p.y * ca; p.x = x; }    // GLSL p.xy *= ROT(a)
        { const float y = p.y * cb + p.z * sb; p.z = -p.y * sb + p.z * cb; p.y = y; }    // GLSL p.yz *= ROT(b)
        p = f3(kc_pabs1(p.x, k), kc_pabs1(p.y, k), kc_pabs1(p.z, k));
        p = p - nz * (kc_pmin(0.f, dot(p, nz), k) * 2.f);
        p = p - ny * (kc_pmin(0.f, dot(p, ny), k) * 2.f);
        p.x -= off;
        if (i > 2) {
            float dn = kc_pmax(d, -(dd - 0.1f / z), 0.05f / z);
            dn = fminf(dn, dd);
            const float w = (i == n - 1) ? fr : 1.f;
            d = d + (dn - d) * w;
            if (INFO) {
                const float a = fabsf(dd);
                if (a < best) {
                    best = a;
                    inf.lvl = (float)i;
                    inf.core = (core <= dd) ? 1.f : 0.f;      // dd = min(shell, core): the core is the nearest part
                }
            }
        }
    }
    return d;
}

SDF_KERNEL
void kifs_crystal(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float* PAL = P + 13;
    const float period = P[25], drift = P[26], pulse = P[41];
    const float R = P[37], D = P[42], Rn = P[44];

    // black, with the faintest palette wash behind the gem so it never reads as a dead frame
    const float ex = (x + 0.5f) / W - 0.5f, ey = (y + 0.5f) / H - 0.5f;
    const float3 wash = srgb_to_linear(pal(drift + 0.62f, PAL));
    float3 col = wash * (0.010f * saturate(1.f - 2.2f * (ex * ex + ey * ey)));

    const float2 bs = iSphere(ro, rd, R);
    if (bs.x < bs.y && bs.y > 0.f) {
        KcInfo dummy;
        auto map = [&](float3 p) { return kc_de<false>(p, P, dummy); };
        const float t0 = fmaxf(bs.x, 0.005f), t1 = bs.y;
        const Hit h = march(map, ro, rd, t0, t1, KC_STEPS, 0.6f * pix, 0.9f, P[43]);
        const bool hit = h.hit || (h.steps >= KC_STEPS && h.t < t1);
        const float ii = (float)h.steps * (1.f / KC_STEPS);
        if (hit) {
            const float3 pos = ro + rd * h.t;
            const float3 n = calc_normal(map, pos, fmaxf(0.5f * pix * h.t, 1e-4f));
            KcInfo inf;
            kc_de<true>(pos, P, inf);
            const float depth = (h.t - D) / fmaxf(Rn, 1e-3f);
            const float idx = (inf.lvl - 3.f) * 0.21f + sqrtf(fminf(inf.trap, 4.f)) * 1.3f + depth * 0.3f;
            const float3 b0 = srgb_to_linear(pal(idx * period + drift, PAL));
            const float3 base = b0 * b0 * 1.6f + b0 * 0.15f;                 // deepen: keep the palette saturated
            const Rig L = rig_load(P + 45);
            const float ao = (1.f - smoothstep(0.06f, 0.75f, ii)) *          // step-count AO (as the original)
                             (0.25f + 0.75f * calc_ao(map, pos, n, 0.07f * Rn, 3));
            const float3 alb = base * mix(0.55f, 0.2f, inf.core);
            float3 c = shade(L, alb, n, rd, 1.f, ao);
            // gem glints: a sharp reflection of the key plus a soft top strip, fresnel weighted
            const float3 rf = reflect(rd, n);
            const float fre = 1.f - saturate(dot(n, -rd));
            const float g = powf(saturate(dot(rf, L.key_dir)), 90.f);
            const float strip = smoothstep(0.55f, 0.95f, rf.y) * (0.2f + 0.8f * fre * fre);
            c += (L.key_col * g * 1.6f + mix(f3(1.f), base, 0.5f) * strip * 0.35f) * (P[38] * ao);
            // inner glow: the cores are emissive (flash on the beat), seen through the shells' windows
            const float3 ec = srgb_to_linear(pal(idx * period + drift + 0.5f, PAL));
            c += ec * (inf.core * P[39] * (0.4f + 0.6f * ao));
            // rays that ran out of steps in deep crevices fade out (no speckle)
            c *= 1.f - 0.65f * smoothstep(0.35f, 1.f, ii);
            col = c;
        }
        // near-miss halo hugging the silhouette and the crevices
        const float3 hc = srgb_to_linear(pal(drift + 0.35f + 0.25f * pulse, PAL));
        col += hc * (h.glow * (1.f / KC_STEPS) * P[40] * (hit ? 0.08f : 1.f));
    }
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"maxit": MAX_IT, "steps": STEPS}


def _bound(zf: float, off_over_zf: float) -> float:
    """Radius (world) of a sphere holding the IFS attractor: |c| / (zf - 1), with margin for the smooth folds."""
    return 1.15 * off_over_zf / max(zf - 1.0, 0.25) + 0.05


class KifsCrystal(Source):
    name = "kifs_crystal"
    label = "KIFS crystal"
    description = ("A single rotating kaleidoscopic-IFS gem: a faceted crystal lattice of carved boxes and glowing "
                   "cores that morphs as its fold angles drift, with specular glints on black (3D raymarched).")
    tags = {"family": "fractal", "dims": "3d", "toolkit": "sdf3d", "technique": ["raymarch", "ifs", "fractal"],
            "moods": ["hypnotic", "icy", "elegant"], "style": "lively", "energy": [0.1, 0.9]}
    params = {
        **standard_scene_params(palette="ice", render_scale=0.45, period=1.0, drift=0.03),
        "fold_angle_a": num(0.0, math.pi, 2.586, "Fold angle A", unit="rad", tier="regional", basic=True),
        "fold_angle_b": num(0.2, 2.95, 2.725, "Fold angle B", unit="rad", tier="regional"),
        "scale": num(1.8, 2.4, 2.0, "IFS scale", tier="regional"),
        "offset": num(0.6, 1.0, 0.85, "IFS offset", tier="regional"),
        "fold_smoothness": num(0.0, 0.05, 0.031, "Fold smoothness", step=0.001, tier="regional"),
        "iterations": num(4.0, 7.0, 6.0, "Iterations (detail)", step=0.05, tier="regional"),
        "carve": num(0.0, 1.0, 0.0, "Carve (box - sphere -> sphere - box)", tier="regional", basic=True),
        "morph": num(0.0, 1.0, 0.5, "Fold drift (morph depth)", tier="regional"),
        "spin_speed": num(0.0, 4.0, 1.0, "Spin speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("kifs_crystal")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.ph0 = rng.uniform(0.0, 2.0 * math.pi, 4)
        self.fold_ph = 0.0                 # fold-angle swing phase (host float64, never wrapped: sin on host)
        self.yaw = float(rng.uniform(0.0, 2.0 * math.pi))
        self.cam_ph = 0.0
        self.drift = 0.0
        self.e_s = None
        self.r_s = None                    # smoothed nominal radius (camera framing ignores the pulse bump)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrated rates (pulse kicks rates only)
        self.fold_ph += spd * (0.07 + 0.38 * e + 0.25 * pulse) * dt
        self.yaw = wrap(self.yaw + float(p["spin_speed"]) * spd * (0.05 + 0.30 * e + 0.25 * pulse) * dt,
                        2.0 * math.pi)
        self.cam_ph += spd * (0.04 + 0.12 * e) * dt
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- IFS parameters: fold angles swing around their base values (continuous morph)
        mo = float(p["morph"])
        fp, ph = self.fold_ph, self.ph0
        ang_a = float(p["fold_angle_a"]) + mo * (0.45 * math.sin(fp + ph[0]) + 0.15 * math.sin(2.31 * fp + ph[1]))
        ang_b = float(p["fold_angle_b"]) + mo * (0.40 * math.sin(0.73 * fp + ph[2]) + 0.12 * math.sin(1.87 * fp + ph[3]))
        zf_nom = float(p["scale"])
        zf = zf_nom + 0.035 * pulse                              # beat: scale amplitude bump (decays)
        off = float(p["offset"]) * (1.0 + 0.03 * mo * math.sin(0.41 * fp + ph[1]))
        k = max(float(p["fold_smoothness"]) * (1.3 - 0.7 * es), 1e-4)  # sharper folds at high energy
        iters = min(max(float(p["iterations"]), 4.0), float(MAX_IT))

        r_nom = off / zf_nom / (zf_nom - 1.0)
        b = 1.0 - math.exp(-dt / 0.5) if dt > 0 else 1.0
        self.r_s = r_nom if self.r_s is None else self.r_s + (r_nom - self.r_s) * b
        bound = max(_bound(zf_nom, off / zf_nom), _bound(zf, off / zf)) + 0.01

        # ---- camera: orbit the gem (spin), gentle pitch / distance breathing
        D = 2.55 * self.r_s
        pitch = 0.42 + 0.22 * math.sin(self.cam_ph + ph[2])
        yaw = self.yaw
        cpch = math.cos(pitch)
        eye = (D * cpch * math.sin(yaw), D * math.sin(pitch), -D * cpch * math.cos(yaw))
        roll = 0.06 * math.sin(0.7 * self.cam_ph + ph[3])
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=42.0, roll=roll)

        # ---- light rig: key fixed in world (glints sweep as the camera orbits); rim punches on the beat
        spec = 0.55 + 0.9 * e
        rim = (0.35 + 0.25 * e) * (1.0 + 2.0 * pulse)
        rig = rig_pack(key_dir=(0.55, 0.8, -0.3), key_col=(1.35, 1.3, 1.25), fill_col=(0.06, 0.07, 0.10),
                       rim_col=(0.30 * rim, 0.40 * rim, 0.55 * rim), amb_col=(0.015, 0.015, 0.02),
                       spec=spec, shininess=70.0)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = zf
        Ph[29] = off / zf
        Ph[30] = k
        Ph[31] = iters
        Ph[32] = min(max(float(p["carve"]), 0.0), 1.0)
        Ph[33] = math.cos(ang_a)
        Ph[34] = math.sin(ang_a)
        Ph[35] = math.cos(ang_b)
        Ph[36] = math.sin(ang_b)
        Ph[37] = bound
        Ph[38] = (0.5 + 0.9 * e) * (1.0 + 0.6 * pulse)          # glint gain
        Ph[39] = (0.25 + 0.6 * e) * (1.0 + 3.0 * pulse)         # core glow (inner flash on the beat)
        Ph[40] = (0.35 + 0.65 * e) * (1.0 + 1.2 * pulse)        # halo gain
        Ph[41] = pulse
        Ph[42] = D
        Ph[43] = 1.0 / (0.008 * max(self.r_s, 0.05)) ** 2       # glow_k: halo within ~1% of the gem radius
        Ph[44] = self.r_s
        Ph[45:62] = rig
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
