"""Hyper lattice: a glowing 4D lattice of bars, hyperspheres and thin walls, rotated through the XW/YW/ZW planes and
sphere-inverted into a blooming hyperdimensional flower that lurches forward on every beat.

Source: https://www.shadertoy.com/view/tfK3Dy , https://www.shadertoy.com/view/WfG3WV
        (the Shaderfuse DCTL https://github.com/nmbr73/Shaderfuse/blob/main/Shaders/ShaderOfTheWeek/D4DBeats.fuse
        is CC-BY-NC-SA-3.0 and was NOT used: only the original shader's logic is ported)
Author: Mårten Rånge (mrange), '4D Beats'; 4D inversion trick credited to mla
License: CC0-1.0 (shader header "// CC0: 4D Beats")
license_class: permissive
Ported from GLSL by smfx (ideas/changes: CUDA re-implementation of the logic; the floor+sqrt time warp is replaced by
        a host beat-phase integrator (phase += rate*speed*dt*(1+3k)) so beats lurch and ease, never jump; the 3D slice
        sits at a w offset so the inversion never hits its singularity; glow is integrated in lattice space with a
        pixel-footprint-widened kernel (thin lines dim instead of shimmering, sub-pixel cells fade out); outer bound
        sphere + near-camera fade keep it framed; colour through the shared cosine palette with drift.)

The scene: every 3D point p is lifted into 4D as (p, w0), rotated through the XW, YW and ZW planes, inverted in the
4D unit sphere (L = k p4 / |p4|^2), shifted along the lattice (z+w) by the flow phase, and folded into a unit 4D
cell.  In each cell: a hypersphere at the centre, rods along the three 2-planes (slice -> bars), and a thin wall pane
through the centre normal to w.  The march takes |d| steps (so rays pass through everything) for a fixed 80 steps,
accumulating a Lorentzian glow around each part; no normals.

Macros: energy -> 4D rotation rates, flow (dolly) rate, inversion wobble rate + depth, 4D slice wobble, glow gain,
bar thickness, wall/halo density (slewed), camera sway.  pulse -> a rate kick into the flow and rotation phases (the
lurch; lurch_kick), a glow flash <= +35% and a hypersphere swell (flash_kick).  speed scales every phase; palette
colours the glow by lattice radius, part and ray depth plus drift; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, wrap
from stream_service.plugins._lib.upsample import internal_size, present

NSTEPS = 80

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 28..39 M (4x3 row-major: p4 = M p3 + o) | 40..43 o
#  44 k (inversion) | 45 flow (lattice z and w offset, wrapped at 1) | 47 sphere r | 48 bar r | 49 wall thickness
#  50 wall amount | 51 glow width | 52 bound radius | 53 halo | 54 gain | 55 pulse k | 56 core tame | 57 core glow
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define NSTEPS @NSTEPS@

SDF_KERNEL
void hyper_lattice(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float* PAL = P + 13;
    const float period = P[25], drift = P[26];
    const float m00 = P[28], m01 = P[29], m02 = P[30];
    const float m10 = P[31], m11 = P[32], m12 = P[33];
    const float m20 = P[34], m21 = P[35], m22 = P[36];
    const float m30 = P[37], m31 = P[38], m32 = P[39];
    const float o0 = P[40], o1 = P[41], o2 = P[42], o3 = P[43];
    const float K = P[44], F = P[45];
    const float sr = P[47], br = P[48], wt = P[49], wamt = P[50], s0 = P[51], RB = P[52], halo = P[53];
    const float tame = P[56];

    float3 acc = f3(0.f);
    const float2 bs = iSphere(ro, rd, RB);
    if (bs.y > 0.f && bs.x < bs.y) {
        float t = fmaxf(bs.x, 0.05f);
        const float tend = bs.y;
        for (int i = 0; i < NSTEPS; ++i) {
            if (t >= tend) break;
            const float3 p = ro + rd * t;
            // lift into 4D (slice at w0), rotate through XW/YW/ZW: p4 = M p + o
            const float a0 = m00 * p.x + m01 * p.y + m02 * p.z + o0;
            const float a1 = m10 * p.x + m11 * p.y + m12 * p.z + o1;
            const float a2 = m20 * p.x + m21 * p.y + m22 * p.z + o2;
            const float a3 = m30 * p.x + m31 * p.y + m32 * p.z + o3;
            const float r2 = a0 * a0 + a1 * a1 + a2 * a2 + a3 * a3 + 1e-6f;
            const float inv = K / r2;                        // lattice length per world length (conformal)
            const float scale = r2 / K;                      // world length per lattice length
            // sphere inversion (mla) + flow along the lattice (z + w)
            const float L0 = a0 * inv, L1 = a1 * inv, L2 = a2 * inv + F, L3 = a3 * inv + F;
            const float c0 = rintf(L0), c1 = rintf(L1);
            const float q0 = L0 - c0, q1 = L1 - c1, q2 = L2 - rintf(L2), q3 = L3 - rintf(L3);
            const float x0 = q0 * q0, x1 = q1 * q1, x2 = q2 * q2, x3 = q3 * q3;
            const float ds = sqrtf(x0 + x1 + x2 + x3) - sr;                                   // hypersphere
            const float db = sqrtf(fminf(fminf(x0 + x1, x0 + x2), x1 + x2)) - br;           // rods -> bars
            const float dw = fmaxf(fabsf(q3) - wt, sqrtf(x0 + x1 + x2) - 0.42f);             // thin wall pane
            const float dl = fminf(fminf(ds, db), dw);
            // glow: Lorentzian around each part, widened to the pixel footprint (energy-conserving)
            const float fp = t * pix * inv;
            const float se = fmaxf(s0, 0.75f * fp);
            const float se2 = se * se;
            const float ls = se2 / (se2 + ds * ds), lb = se2 / (se2 + db * db), lw = se2 / (se2 + dw * dw);
            const float es = ls * ls + halo * ls;
            const float eb = 0.8f * (lb * lb + halo * lb);
            const float ew = wamt * (lw * lw + halo * lw);
            const float etot = es + eb + ew;
            const float stepw = fminf(fmaxf(fabsf(dl) * scale * 0.65f, 0.5f * se * scale + 0.0015f), 0.6f);
            const float r = sqrtf(dot(p, p));
            const float win = smoothstep(0.15f, 0.9f, t)                                  // nothing in the eye
                            * (1.f - smoothstep(0.62f * RB, RB, r))                       // soft outer bound
                            * (s0 / se) * (1.f - smoothstep(0.14f, 0.4f, fp))             // anti-alias fade
                            * (1.f - smoothstep(0.7f * NSTEPS, (float)NSTEPS, (float)i)); // soft step budget
            const float wgt = etot * win;
            if (wgt > 1e-5f) {
                const float latr = K * rsqrtf(r2);
                const float off = (eb * 0.22f + ew * 0.45f) / (etot + 1e-6f);
                const float hh = hash1((unsigned int)(int)c0 + 7919u, (unsigned int)(int)c1 + 104729u, 17u);
                const float idx = period * (0.055f * latr + off + 0.08f * hh + 0.04f * t) + drift;
                float3 c = pal(idx, PAL);
                c = c * c;                                                                // ~linear
                acc += c * (wgt * fminf(stepw * inv, 0.35f) / (1.f + tame * inv));
            }
            t += stepw;
        }
    }
    // background: near-black with a faint palette core glow at the flower's heart
    const float b = dot(ro, rd);
    const float dmin2 = b < 0.f ? fmaxf(dot(ro, ro) - b * b, 0.f) : dot(ro, ro);
    float3 bg = pal(drift + 0.5f * period + 0.3f, PAL);
    bg = bg * bg;
    float3 col = bg * (0.010f + P[57] * expf(-dmin2 * 1.3f)) + acc * P[54];
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col)));
}
""".replace("@NSTEPS@", str(NSTEPS))


def _rot4(i: int, j: int, a: float) -> np.ndarray:
    m = np.eye(4)
    c, s = math.cos(a), math.sin(a)
    m[i, i] = c
    m[i, j] = -s
    m[j, i] = s
    m[j, j] = c
    return m


# fixed 3D tilt so the lattice never sits axis-aligned to the camera
_TILT = _rot4(0, 1, 0.35) @ _rot4(1, 2, 0.27)


class HyperLattice(Source):
    name = "hyper_lattice"
    label = "Hyper lattice"
    description = ("A glowing 4D lattice of bars, hyperspheres and thin walls, rotated through the fourth dimension "
                   "and sphere-inverted into a blooming flower that lurches forward on every beat (3D raymarched).")
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.04),
        "inversion_strength": num(3.0, 12.0, 6.0, "Inversion (fold depth)", tier="regional", basic=True),
        "rotation_plane_mix": num(0.0, 1.0, 0.35, "4D rotation plane (XW -> YW -> ZW)", tier="regional",
                                  basic=True),
        "wall_thickness": num(0.0, 0.08, 0.015, "Wall thickness", step=0.001, tier="regional"),
        "sphere_radius": num(0.05, 0.4, 0.2, "Hypersphere radius", tier="regional"),
        "slice_depth": num(0.35, 1.2, 0.7, "4D slice depth", tier="regional"),
        "camera_distance": num(1.5, 5.0, 2.8, "Camera distance", tier="regional"),
        "lurch_kick": num(0.0, 2.0, 1.0, "Beat lurch", tier="regional"),
        "flash_kick": num(0.0, 1.0, 0.6, "Beat flash / swell", tier="regional"),
        "rotation_speed": num(0.0, 4.0, 1.0, "4D rotation speed", unit="×", tier="realtime", bind="rate"),
        "flow_speed": num(0.0, 4.0, 1.0, "Flow speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("hyper_lattice")
        self.P = cp.zeros(64, cp.float32)
        self.Ph = np.zeros(64, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.rot = rng.uniform(0.4, 2.0, 3)          # XW, YW, ZW angles (host float64, never wrapped)
        self.flow = float(rng.uniform(0.0, 1.0))     # lattice flow phase (the beat-lurched dolly)
        self.wob = float(rng.uniform(0.0, 2.0 * math.pi))
        self.cam_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.drift = 0.0
        self.e_fast = None
        self.e_slow = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        ep = _sstep(0.1, 0.85, e)
        if self.e_fast is None:
            self.e_fast = self.e_slow = ep
        elif dt > 0:
            self.e_fast += (ep - self.e_fast) * (1.0 - math.exp(-dt / 0.3))
            self.e_slow += (ep - self.e_slow) * (1.0 - math.exp(-dt / 1.8))
        ef, es = self.e_fast, self.e_slow
        es15 = es ** 1.5
        kk = pulse * (0.3 + 0.7 * e)
        rate = 0.4 + 1.2 * ep
        lurch = float(p["lurch_kick"])
        flash = float(p["flash_kick"])

        # ---- phases (kicks add to rates only)
        m = float(p["rotation_plane_mix"])
        wts = [max(0.0, 1.0 - abs(m - c) * 2.0) for c in (0.0, 0.5, 1.0)]
        rspd = float(p["rotation_speed"]) * spd * dt * rate * (1.0 + 0.6 * lurch * kk)
        for i, mult in enumerate((1.0, 0.87, 0.74)):
            self.rot[i] += rspd * 0.17 * mult * (0.3 + 0.7 * wts[i])
        self.flow += float(p["flow_speed"]) * spd * dt * 0.16 * rate * (1.0 + 3.0 * lurch * kk)
        self.wob += spd * dt * 0.45 * rate
        self.cam_ph += spd * dt * 0.09 * rate
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 100.0)

        # ---- 4D frame: rotate through XW, YW, ZW (+ static emphasis tilt from the plane mix)
        ang = [self.rot[i] + 0.45 * wts[i] for i in range(3)]
        R = _rot4(0, 3, ang[0]) @ _rot4(1, 3, ang[1]) @ _rot4(2, 3, ang[2]) @ _TILT
        w0 = float(p["slice_depth"]) * (1.0 + (0.06 + 0.06 * es) * math.sin(0.53 * self.wob + 1.0))
        kinv = float(p["inversion_strength"]) * (1.0 + (0.07 + 0.07 * es) * math.sin(self.wob))

        # ---- camera: orbit sway around the flower's heart
        D = float(p["camera_distance"]) * (1.0 + 0.04 * math.sin(0.5 * self.cam_ph))
        sway = 0.5 + 0.5 * es
        yaw = 0.32 * sway * math.sin(self.cam_ph)
        pitch = 0.18 * sway * math.sin(0.71 * self.cam_ph + 1.0)
        eye = orbit_eye((0.0, 0.0, 0.0), D, yaw, pitch)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=62.0, roll=0.04 * sway * math.sin(0.43 * self.cam_ph + 0.7))

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[28:40] = R[:, :3].reshape(-1)
        Ph[40:44] = R[:, 3] * w0
        Ph[44] = kinv
        Ph[45] = wrap(self.flow, 1.0)
        Ph[47] = min(float(p["sphere_radius"]) * (1.0 + 0.5 * flash * kk), 0.46)
        Ph[48] = 0.022 + 0.014 * es
        Ph[49] = p["wall_thickness"]
        Ph[50] = 0.6 * (0.55 + 0.45 * es15)
        Ph[51] = 0.032
        Ph[52] = D + 2.2
        Ph[53] = 0.05 + 0.09 * es15
        Ph[54] = 2.6 * float(p["intensity"]) * (0.7 + 0.6 * ef) * (1.0 + 0.35 * flash * kk)
        Ph[55] = kk
        Ph[56] = 0.06
        Ph[57] = 0.05 * (0.6 + 0.4 * es) * (1.0 + 0.5 * flash * kk)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None


def _sstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)
