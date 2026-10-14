"""Mandelbox: an autopiloted flight through Mandelbox fractal architecture (3D raymarched).

Source: Original (inspired by https://github.com/Syntopia/FragmentariumWeb/blob/main/src/systems/fragmentarium/examples/Historical%203D%20Fractals/Mandelbox.frag ,
        http://www.fractalforums.com/3d-fractal-generation/a-mandelbox-distance-estimate-formula/15/ ,
        https://www.shadertoy.com/view/3lyXDm ); Mandelbox formula by Tom Lowe (2010); SDF helpers from
        stream_service/plugins/_lib/sdf3d.py (MIT, see that file).
Author: smfx (original; Mandelbox formula by Tom Lowe, 2010)
License: MIT
license_class: permissive
Ideas only (nothing copied): the Buddhi/Rrrola distance estimate and Mandelbulber2 (GPL) informed the general
approach (box fold + sphere fold + scale, |z|/|dr| estimate); the code here is written from scratch.

Look: cavernous cathedral halls and lattice vaults (negative scale, the default "cathedral" structure) or blocky
fractal cities (positive scale, "city"), coloured by an orbit trap through the shared palette, with edge glow,
travelling emissive veins, a headlight and coloured fog.

Camera: a host-side autopilot.  Each frame it evaluates the same distance estimate in numpy (float64, ~50 points)
at the eye and along a fan of look-ahead rays, steers towards the direction whose clearance is closest to a target
clearance (itself measured continuously from a random sample of the box, so it adapts as the director eases the
fold parameters), and flies at a speed proportional to the local clearance, so the apparent speed stays constant
in vast halls and tight lattices alike.  It slows near walls, never steps more than a fraction of the clearance per
frame, and is pulled back if it wanders out of the box.  As a last resort the kernel carves a small bubble around
the eye, so a wall that grows over the camera (breathing, eased params) is shown in cross-section rather than as a
solid frame.  No GPU readback.

Macros (e' = smoothstep(0.1, 0.85, energy), slewed for geometry):
  energy -> flight speed x(0.4 + 1.2 e'), scale-wobble and minR-breathing amplitude, glow and vein gain, fog
            thinning, camera sway; pulse (k = pulse * (0.3 + 0.7 e)) -> minR^2 swell (the sphere fold breathes,
            ``breathe_kick``), an emissive flash in the veins (``flash_kick``) and a dolly-rate lurch.
  speed scales every rate; palette: pal(sqrt(trap) * period + drift); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, rot_matrix, wrap
from stream_service.plugins._lib.upsample import internal_size, present

ITERS = 8          # fractal iterations (structural, never stepped)
STEPS = 48         # march steps
TWIST_AXIS = (0.31, 0.88, 0.36)
NP = 72            # P floats

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density (per world unit)
#  29 scale | 30 minR2 | 31 fixR2 | 32 fold limit | 33..41 twist Mat3 | 42 bubble radius | 43 tmax
#  44 glow gain | 45 vein base | 46 vein flash | 47 headlight gain | 48 headlight falloff | 49 box half-size
#  50 trap colour scale | 51 step scale | 52 pixel-eps factor | 53 fog brightness | 54 vein phase
#  55 vein frequency | 56 key light gain | 57 vignette
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MB_ITERS %(iters)d
#define MB_STEPS %(steps)d

// Mandelbox (Tom Lowe): z = scale * R * sphereFold(boxFold(z)) + p;  trap = min |boxFold(z)|^2 over the orbit.
__device__ __forceinline__ float mb_de(float3 p, const float* __restrict__ P, const Mat3& R, float& trap) {
    const float sc = P[29], minR2 = P[30], fixR2 = P[31], lim = P[32];
    const float asc = fabsf(sc);
    float3 z = p; float dr = 1.f; trap = 1e10f;
    #pragma unroll
    for (int i = 0; i < MB_ITERS; ++i) {
        z = clamp(z, -lim, lim) * 2.f - z;
        const float r2 = dot(z, z);
        trap = fminf(trap, r2);
        const float k = fmaxf(fixR2 / fmaxf(r2, minR2), 1.f);
        z = mul(R, z * k);
        dr = dr * k * asc + 1.f;
        z = z * sc + p;
    }
    return length(z) / fabsf(dr);
}

SDF_KERNEL
void mandelbox(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const Mat3 R = mat3_load(P + 33);
    const float pa = pixel_angle(cam, H) * P[52];
    const float bub = P[42];
    const float2 bt = iBox(ro, rd, f3(P[49]));
    const float tmax = fminf(P[43], bt.y);
    float t = fmaxf(bt.x, 0.f);
    bool hit = false, inside_box = !(bt.x > bt.y || bt.y < 0.f);
    int steps = 0;
    float trap = 4.f, glow = 0.f, gtrap = 4.f, best = 1e9f;
    if (inside_box) {
        for (int i = 0; i < MB_STEPS; ++i) {
            const float3 p = ro + rd * t;
            float tr;
            const float d = fmaxf(mb_de(p, P, R, tr), bub - t);    // bubble: carve the eye out of solids
            trap = tr; steps = i + 1;
            const float pe = pa * fmaxf(t, 1e-4f);
            const float ratio = d / pe;                               // distance in pixel footprints
            glow += saturate(1.f - ratio * 0.125f);
            if (ratio < best) { best = ratio; gtrap = tr; }
            if (d < pe) { hit = true; break; }
            t += d * P[51];
            if (t > tmax) break;
        }
    }
    const float drift = P[26];
    const float3 fogc = srgb_to_linear(pal(drift + 0.62f, P + 13)) * P[53];
    float3 col = fogc * (0.75f + 0.35f * rd.y);
    float tf = tmax;
    const bool surf = inside_box && (hit || t < tmax);               // step-limited rays: shade where they stopped
    if (surf) {
        tf = t;
        const float3 pos = ro + rd * t;
        auto map = [&](float3 q) { float tr2; return mb_de(q, P, R, tr2); };
        const float3 n = calc_normal(map, pos, 0.5f * pa * t + 1e-6f);
        const float ci = sqrtf(trap) * P[50];
        const float3 alb = srgb_to_linear(pal(ci * P[25] + drift, P + 13));
        float ao = 1.f - (float)steps / (float)MB_STEPS;
        ao = saturate(ao * ao * 1.3f + 0.08f);
        const float3 kd = normalize(f3(0.45f, 0.8f, -0.35f));
        const float dif = saturate(dot(n, kd));
        const float hd = saturate(dot(n, -rd));
        const float att = 1.f / (1.f + square(t / P[48]));
        const float3 hv = normalize(kd - rd);
        const float spe = powf(saturate(dot(n, hv)), 32.f) * dif;
        const float fre = 1.f - hd;
        const float3 key = f3(1.25f, 1.12f, 0.98f) * P[56];
        col = alb * (key * dif * 0.75f + f3(P[47]) * (0.35f + 0.65f * hd * hd) * att + fogc * 2.2f * (0.6f + 0.4f * n.y))
              * ao;
        col += (key * spe * 0.6f + fogc * 3.f * fre * fre * fre) * ao;
        // travelling emissive veins along orbit-trap level sets (localised; flashes on the beat)
        const float v = 0.5f + 0.5f * cosf(SDF_TAU * (ci * P[55] - P[54]));
        const float v2 = v * v, v4 = v2 * v2;
        const float vein = v4 * v4 * smoothstep(1.6f, 0.4f, ci);
        col += srgb_to_linear(pal(ci * P[25] + drift + 0.45f, P + 13)) * vein * (P[45] + P[46]) * (0.4f + 0.6f * ao);
    }
    col = fog_exp(col, fogc, tf, P[28]);
    // edge glow from near misses along the march (silhouettes against depth)
    const float g = saturate(glow * (1.f / 10.f));
    const float3 gcol = srgb_to_linear(pal(sqrtf(gtrap) * P[50] * P[25] + drift + 0.25f, P + 13));
    col += gcol * (g * g * P[44]) * expf(-0.35f * P[28] * tf);
    col = vignette(col, x, y, W, H, P[57]);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"iters": ITERS, "steps": STEPS}


# ----------------------------------------------------------------------------- host-side distance estimate
def _de_np(pts: np.ndarray, scale: float, min_r2: float, fix_r2: float, fold: float, R: np.ndarray) -> np.ndarray:
    """Same estimate as the kernel's mb_de (float64), for an (N, 3) array of points."""
    z = pts.copy()
    dr = np.ones(len(pts))
    asc = abs(scale)
    RT = R.T
    for _ in range(ITERS):
        z = np.minimum(np.maximum(z, -fold), fold) * 2.0 - z
        r2 = np.einsum("ij,ij->i", z, z)
        k = np.maximum(fix_r2 / np.maximum(r2, min_r2), 1.0)
        z = (z * k[:, None]) @ RT
        dr = dr * k * asc + 1.0
        z = z * scale + pts
    return np.sqrt(np.einsum("ij,ij->i", z, z)) / np.abs(dr)


_TETRA = np.array([(1, -1, -1), (-1, -1, 1), (-1, 1, -1), (1, 1, 1)], np.float64) / math.sqrt(3.0)


def _box_half(scale: float) -> float:
    s = abs(scale)
    return 2.05 if scale < 0 else min(2.0 * (s + 1.0) / (s - 1.0) + 0.1, 8.0)


def _fan() -> np.ndarray:
    """Look-ahead directions in the camera frame (x right, y up, z forward)."""
    dirs = [(0.0, 0.0, 1.0)]
    for ang, n, off in ((0.36, 8, 0.0), (0.75, 8, 0.39), (1.2, 6, 0.0)):
        for i in range(n):
            a = 2 * math.pi * i / n + off
            dirs.append((math.sin(ang) * math.cos(a), math.sin(ang) * math.sin(a), math.cos(ang)))
    return np.asarray(dirs, np.float64)


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _norm(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else np.array([0.0, 0.0, 1.0])


class Mandelbox(Source):
    name = "mandelbox"
    label = "Mandelbox"
    description = ("Fly through Mandelbox fractal architecture: cathedral halls and lattice vaults, or blocky "
                   "fractal cities, with orbit-trap colour, glowing veins and fog (3D raymarched).")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.35, period=1.0, drift=0.03),
        "structure": enum(("cathedral", "city"), "cathedral", "Structure (scale sign)"),
        "box_scale": num(1.5, 3.0, 2.1, "Box scale", tier="regional", basic=True),
        "min_radius": num(0.1, 0.7, 0.5, "Sphere-fold radius", tier="regional"),
        "fold_limit": num(0.6, 1.4, 1.0, "Box-fold limit", tier="regional"),
        "twist": num(0.0, 0.3, 0.06, "Twist per iteration", unit="rad", tier="regional", basic=True),
        "path_curvature": num(0.0, 1.0, 0.45, "Flight curvature", tier="regional"),
        "glow": num(0.0, 2.0, 0.9, "Glow", tier="regional"),
        "breathe_kick": num(0.0, 2.0, 1.0, "Beat breathing", tier="regional"),
        "flash_kick": num(0.0, 2.0, 1.0, "Beat vein flash", tier="regional"),
        "flight_speed": num(0.0, 3.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
    }

    # ------------------------------------------------------------------ lifecycle
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("mandelbox")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        self.rng = np.random.default_rng(int(params["seed"]))
        self.fan = _fan()
        self.cloud = self.rng.uniform(-1.0, 1.0, (4096, 3))          # unit-box sample cloud for the clearance probe
        self.cloud_i = 0
        self.wander_ph = self.rng.uniform(0.0, 100.0, 3)
        # phases (host-integrated)
        self.drift = 0.0
        self.wob = 0.0
        self.breath = 0.0
        self.vein = 0.0
        self.sway = 0.0
        self.e_s = None
        self.v_s = 0.0
        self.stuck = 0.0
        # initial geometry + clearance target, then a starting pose at ~target clearance
        geo = self._geometry(params, 0.5, 0.0, 0.0)
        self.c_t = self._probe_clearance(geo, n=2048)
        B = geo["B"]
        pts = self.cloud[:2048] * (0.8 * B)
        d = _de_np(pts, geo["scale"], geo["minR2"], 1.0, geo["fold"], geo["R"])
        score = -np.abs(np.log((d + 1e-9) / self.c_t)) - 0.3 * np.abs(pts).max(1) / B
        self.eye = pts[int(np.argmax(score))].copy()
        self.head = self._initial_heading(geo)
        self.look = self.head.copy()
        self.roll = 0.0
        self.d_eye = float(self._de(self.eye[None, :], geo)[0])
        self.vel = np.zeros(3)
        self.ap_acc = 0.0
        self.ap_tick = 0

    def close(self) -> None:
        self.buf = None
        self.P = None

    # ------------------------------------------------------------------ host geometry helpers
    def _geometry(self, p: dict, es: float, k: float, wob_ph: float) -> dict:
        sign = 1.0 if p["structure"] == "city" else -1.0
        mag = float(p["box_scale"])
        if sign > 0:
            mag = max(mag, 1.9)                                         # positive scale < ~1.9 is a solid blob
        wob_amp = 0.03 + 0.05 * es
        scale = sign * (mag + wob_amp * math.sin(wob_ph))
        mr = float(p["min_radius"])
        breathe = (0.04 + 0.06 * es) * math.sin(self.breath) + 0.35 * float(p["breathe_kick"]) * k
        minR2 = max(mr * mr * (1.0 + breathe), 0.004)
        Rl = rot_matrix(TWIST_AXIS, float(p["twist"]))
        return {"scale": scale, "minR2": minR2, "fold": float(p["fold_limit"]), "R": np.asarray(Rl).reshape(3, 3),
                "Rl": Rl, "B": _box_half(scale)}

    def _de(self, pts: np.ndarray, geo: dict) -> np.ndarray:
        return _de_np(pts, geo["scale"], geo["minR2"], 1.0, geo["fold"], geo["R"])

    def _probe_clearance(self, geo: dict, n: int = 48) -> float:
        """Clearance target: a high percentile of the estimate over random points in the box."""
        i0 = self.cloud_i
        idx = (np.arange(n) + i0) % len(self.cloud)
        self.cloud_i = (i0 + n) % len(self.cloud)
        d = self._de(self.cloud[idx] * (0.9 * geo["B"]), geo)
        return float(max(np.partition(d, int(0.85 * (len(d) - 1)))[int(0.85 * (len(d) - 1))], 1e-3))

    def _frame(self, fwd: np.ndarray):
        up = np.array([0.0, 1.0, 0.0])
        r = np.cross(up, fwd)
        if np.dot(r, r) < 1e-8:
            r = np.array([1.0, 0.0, 0.0])
        r = _norm(r)
        u = np.cross(fwd, r)
        return r, u

    def _initial_heading(self, geo: dict) -> np.ndarray:
        best, bd = np.array([0.0, 0.0, 1.0]), -1.0
        for v in self.fan[[0, 1, 3, 5, 7, 9, 11, 13, 15]]:
            for f in (np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])):
                r, u = self._frame(f)
                dvec = _norm(v[0] * r + v[1] * u + v[2] * f)
                dvec[1] *= 0.5
                dvec = _norm(dvec)
                d = float(self._de((self.eye + dvec * 2.0 * self.c_t)[None, :], geo)[0])
                if d > bd:
                    best, bd = dvec, d
        return best

    # ------------------------------------------------------------------ autopilot
    def _autopilot(self, p: dict, geo: dict, dt: float, e1: float, k: float, spd: float) -> None:
        """One batched host DE call: eye, 4 gradient taps, the look-ahead fan at L/2 and L, and the clearance probe."""
        c_t = self.c_t
        B = geo["B"]
        r, u = self._frame(self.head)
        dirs = self.fan[:, 0:1] * r + self.fan[:, 1:2] * u + self.fan[:, 2:3] * self.head
        n = len(dirs)
        L = min(max(3.0 * self.d_eye, 1.2 * c_t), 4.0 * c_t)
        hg = 0.12 * c_t
        nprobe = 24
        idx = (np.arange(nprobe) + self.cloud_i) % len(self.cloud)
        self.cloud_i = (self.cloud_i + nprobe) % len(self.cloud)
        pts = np.concatenate([self.eye[None, :], self.eye + _TETRA * hg, self.eye + dirs * (0.5 * L),
                              self.eye + dirs * L, self.cloud[idx] * (0.9 * B)])
        d = self._de(pts, geo)
        d_eye = float(d[0])
        self.d_eye = d_eye
        grad = _norm((_TETRA * d[1:5, None]).sum(0))
        dmin = np.minimum(d[5:5 + n], d[5 + n:5 + 2 * n])
        # clearance target: EMA of a high percentile over a rolling random sample of the box
        probe = np.partition(d[5 + 2 * n:], nprobe - 4)
        c_new = float(max(probe[nprobe - 4], 1e-3))                     # ~85th percentile
        self.c_t += (c_new - c_t) * (1.0 - math.exp(-dt / 3.0))

        lr = np.log((dmin + 1e-9) / c_t)
        s = np.where(lr < 0.0, 1.6 * lr, -0.3 * lr)                     # tight is bad, very open is mildly bad
        s += 0.9 * (dirs @ self.head)                                   # keep going
        curv = float(p["path_curvature"])
        wd = _norm(np.sin(self.wander_ph * np.array([1.0, 0.77, 1.31]) + np.array([0.0, 2.1, 4.2])))
        s += (0.15 + 0.9 * curv) * (dirs @ wd)                          # meander
        tight = 1.0 - _ss(0.25 * c_t, 0.9 * c_t, d_eye)
        s += 1.2 * tight * (dirs @ grad)                                # turn away from a close wall
        out = _ss(0.7 * B, 1.0 * B, float(np.abs(self.eye).max()))
        if out > 0.0:
            s += 3.0 * out * (dirs @ _norm(-self.eye))                  # come back into the box
        s -= 5.0 * np.maximum(np.abs(dirs[:, 1]) - 0.62, 0.0)           # keep pitch moderate
        w = np.exp((s - s.max()) / 0.22)
        desired = _norm((w[:, None] * dirs).sum(0))
        turn = (0.9 + 1.4 * curv) * (1.0 + 2.0 * tight) * max(spd, 0.25)
        a = 1.0 - math.exp(-turn * dt)
        new_head = _norm(self.head + (desired - self.head) * a)
        turn_rate = float(np.dot(np.cross(self.head, new_head), u)) / max(dt, 1e-3)
        self.head = new_head
        roll_t = -0.35 * (0.3 + curv) * max(min(turn_rate, 1.5), -1.5)   # bank into turns
        self.roll += (roll_t - self.roll) * (1.0 - math.exp(-1.5 * dt))
        # clearance-proportional speed; slows near walls; the beat lurches the rate (never the position)
        clear = min(max(min(d_eye, float(dmin[0])), 0.25 * c_t), 3.0 * c_t)
        fs = float(p["flight_speed"]) * spd
        v_t = 0.9 * clear * (0.4 + 1.2 * e1) * fs
        self.v_s += (v_t - self.v_s) * (1.0 - math.exp(-dt / 0.5))
        step = self.v_s * (1.0 + 1.4 * k) * dt
        self.stuck = self.stuck + dt if d_eye < 0.05 * c_t else 0.0
        cap = max(0.5 * d_eye, 0.12 * c_t * fs * dt, (0.6 * c_t * dt) if self.stuck > 0.5 else 0.0)
        move = self.head * min(step, cap)
        if tight > 0.0:                                                 # ease out of a wall's way
            move = move + grad * min((0.6 * c_t - min(d_eye, 0.6 * c_t)) * 1.5 * dt, 0.4 * max(d_eye, 0.02 * c_t))
        self.vel = move / dt                                            # applied per frame in render()
        self.wander_ph += dt * spd * (0.05 + 0.2 * curv)

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        e1 = _ss(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        self.e_s = e1 if self.e_s is None else self.e_s + (e1 - self.e_s) * a
        es = self.e_s
        k = pulse * (0.3 + 0.7 * e)

        # phases (rates only; kicks add to rates)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)
        self.wob += spd * (0.07 + 0.08 * e1) * dt
        self.breath += spd * (0.35 + 0.5 * e1) * dt
        self.vein = wrap(self.vein + spd * (0.12 + 0.35 * e1) * (1.0 + 2.0 * k) * dt, 1.0)
        self.sway += spd * (0.15 + 0.25 * e1) * dt

        geo = self._geometry(p, es, k, self.wob)
        # steering runs at half the frame rate (host numpy DE); the eye integrates its velocity every frame
        self.ap_acc += dt
        self.ap_tick ^= 1
        if self.ap_acc > 0 and (self.ap_tick == 0 or self.ap_acc > 0.05):
            self._autopilot(p, geo, self.ap_acc, e1, k, spd)
            self.ap_acc = 0.0
        self.eye = self.eye + self.vel * dt
        c_t = self.c_t

        # camera: eased look direction with an energy-scaled sway
        self.look = _norm(self.look + (self.head - self.look) * ((1.0 - math.exp(-2.2 * dt)) if dt > 0 else 1.0))
        r, u = self._frame(self.look)
        sw = 0.025 + 0.06 * es
        look = _norm(self.look + r * (sw * math.sin(self.sway)) + u * (0.6 * sw * math.sin(0.71 * self.sway + 1.3)))
        roll = self.roll + 0.5 * sw * math.sin(0.53 * self.sway + 0.4)
        fov = 72.0 + 3.0 * es
        cam = camera_pack(tuple(self.eye), tuple(self.eye + look), fov_deg=fov, roll=roll)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * (0.95 + 0.25 * e1)
        Ph[28] = (0.075 / max(c_t, 1e-4)) * (1.25 - 0.55 * es)          # fog thins with energy
        Ph[29] = geo["scale"]
        Ph[30] = geo["minR2"]
        Ph[31] = 1.0
        Ph[32] = geo["fold"]
        Ph[33:42] = geo["Rl"]
        Ph[42] = 0.22 * c_t
        Ph[43] = 60.0 * c_t + 2.0 * geo["B"]
        Ph[44] = float(p["glow"]) * (0.5 + 0.9 * e1) * 0.55
        Ph[45] = 0.12 + 0.4 * e1
        Ph[46] = float(p["flash_kick"]) * 2.2 * k
        Ph[47] = 1.1
        Ph[48] = 5.0 * c_t
        Ph[49] = geo["B"]
        Ph[50] = 0.42
        Ph[51] = 0.9
        Ph[52] = 1.0
        Ph[53] = 0.11 + 0.05 * e1
        Ph[54] = self.vein
        Ph[55] = 2.6
        Ph[56] = 0.8 + 0.25 * e1
        Ph[57] = 0.35
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
