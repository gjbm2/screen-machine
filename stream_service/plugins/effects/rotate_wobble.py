"""Pendulum sway: the frame rocks like a pendulum.  A Dutch-tilt roll, plus optional 3D card pitch and yaw in
perspective, driven by a damped pendulum that the beats push alternately left and right.

Source: https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/3d%20Rotate.fs ,
        https://github.com/evanw/glfx.js/blob/master/src/filters/warp/perspective.js
Author: zoidberg (ISF 3d Rotate); Evan Wallace (glfx.js perspective); pendulum physics smfx
License: MIT
license_class: permissive
Ported by smfx (ideas/changes: the 3D card rotation of ISF "3d Rotate" and glfx.js's projective warp, re-derived as
one analytic homography; no shader code copied; pendulum dynamics, overscan solve, shadowed-card backdrop,
lighting and gloss are new).

The picture is a card in 3D, rotated by R = roll . pitch . yaw about a pivot (centre .. top edge) and seen by a
45 degree pinhole camera at the distance where the flat card fills the frame exactly.  The host builds the 3x3
homography screen -> card per frame; the kernel does one projective map and one bilinear tap per pixel (about
0.4 ms at 4K), plus cheap edge AA, a soft drop shadow, a palette backdrop, Lambert shading and a gloss band.

Backdrop morph: 0 = overscan (the host solves the smallest zoom that keeps the rotated card covering the screen,
so no edge is ever seen); 1 = a shadowed card, shrunk a little, swaying over a dark palette backdrop with a soft
ghost of the picture.  In between, the zoom and card size cross-fade in log space.

Physics (host, semi-implicit Euler, substeps <= 1/120 s, dt clamped to 0.1): three softened pendulums (roll,
pitch, yaw; pitch at 1.31x and yaw at 0.77x the roll frequency).  idle_sway is a resonant drive whose steady-state
amplitude is idle_sway x target (independent of damping), so it keeps a gentle integrated rock between beats.  A
beat rising edge (pulse jump > 0.15, 120 ms refractory) flips the push side; the pulse envelope then pushes the
roll (and yaw) toward that side and nods the pitch, i.e. kicks add force, never position.  Energy drives the push
gain (0.6 + 1.4 e^1.3), lower damping, the idle and lurch amplitudes (a slow off-resonant "drunken" lurch ~ e^2),
the angle scale and the 3D share: a gentle rock at low energy, a drunken lurch at high energy.

Realtime: amount scales every angle, the card shrink, the overscan, shadow, gloss and rim via m = smoothstep(amount)
(copy below 1e-4, so exact identity at 0).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

_TAU = 2.0 * math.pi
_FOV = 45.0
_D = 0.5 / math.tan(math.radians(_FOV) * 0.5)      # camera distance (height units): flat card fills the frame

# Q layout (floats):
#  0..8 screen->card homography (row-major; screen in height units, centred)   9 A (aspect)
#  10 shade (card Lambert gain)  11 shadow ox  12 shadow oy  13 shadow softness  14 shadow strength
#  15 gloss amp  16 gloss offset  17 gloss dir x  18 gloss dir y  19 drift  20 period  21 backdrop level
#  22 rim  23 ghost
#  24..35 palette
_NQ = 40

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float rw_fold(float v, float n) {          // mirror at the frame edges
    v = fabsf(v);
    v = fmodf(v, 2.f * n);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}

__device__ __forceinline__ float3 rw_map(const float* __restrict__ Q, float X, float Y) {
    return make_float3(Q[0] * X + Q[1] * Y + Q[2], Q[3] * X + Q[4] * Y + Q[5], Q[6] * X + Q[7] * Y + Q[8]);
}

// signed box distance of card point (u, v) to the card rectangle (height units; < 0 inside)
__device__ __forceinline__ float rw_box(float u, float v, float A) {
    return fmaxf(fabsf(u) - 0.5f * A, fabsf(v) - 0.5f);
}

extern "C" __global__
void rotate_wobble(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                   const float* __restrict__ Q)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fW = (float)W, fH = (float)H, iH = 1.f / fH;
    const float A = Q[9];
    const float Xs = (x + 0.5f) * iH - 0.5f * A, Ys = (y + 0.5f) * iH - 0.5f;

    // card coordinates of this pixel and of its neighbours (footprint for edge AA)
    const float3 h0 = rw_map(Q, Xs, Ys);
    float alpha = 0.f, u = 0.f, v = 0.f, dbox = 1.f, fp = 1e-3f;
    if (h0.z > 1e-5f) {
        const float iz = 1.f / h0.z;
        u = h0.x * iz; v = h0.y * iz;
        const float3 hx = rw_map(Q, Xs + iH, Ys), hy = rw_map(Q, Xs, Ys + iH);
        const float izx = 1.f / fmaxf(hx.z, 1e-5f), izy = 1.f / fmaxf(hy.z, 1e-5f);
        const float dux = hx.x * izx - u, dvx = hx.y * izx - v, duy = hy.x * izy - u, dvy = hy.y * izy - v;
        fp = fmaxf(sqrtf(fmaxf(dux * dux + dvx * dvx, duy * duy + dvy * dvy)), 1e-6f);
        dbox = rw_box(u, v, A);
        alpha = clamp01(0.5f - dbox / fp);
    }

    float4 col = make_float4(0.f, 0.f, 0.f, 255.f);
    if (alpha > 0.f) {
        col = bilin(src, rw_fold((u + 0.5f * A) * fH, fW), rw_fold((v + 0.5f) * fH, fH), W, H);
        // Lambert shading of the tilted card
        const float sh = Q[10];
        col.x *= sh; col.y *= sh; col.z *= sh;
        // gloss band sweeping across the card as it tilts (screen blend, palette-tinted white)
        const float ga = Q[15];
        if (ga > 1e-4f) {
            const float s = (u * Q[17] + v * Q[18] - Q[16]) * (1.f / 0.22f);
            const float g = ga * __expf(-s * s);
            const float3 pc = pal(Q[19] + Q[20] * (0.3f * u + 0.2f * v + 0.5f), Q + 24);
            const float hr = g * (0.55f + 0.45f * pc.x), hg = g * (0.55f + 0.45f * pc.y), hb = g * (0.55f + 0.45f * pc.z);
            col.x = 255.f - (255.f - fminf(col.x, 255.f)) * (1.f - hr);
            col.y = 255.f - (255.f - fminf(col.y, 255.f)) * (1.f - hg);
            col.z = 255.f - (255.f - fminf(col.z, 255.f)) * (1.f - hb);
        }
        // thin palette rim on the card edge (card mode)
        const float ra = Q[22];
        if (ra > 1e-4f) {
            const float dpx = fmaxf(-dbox / fp, 0.f);
            const float r = ra * __expf(-dpx * 0.45f);
            const float3 pc = pal(Q[19] + Q[20] * (0.4f * u - 0.3f * v), Q + 24);
            col.x = 255.f - (255.f - fminf(col.x, 255.f)) * (1.f - r * pc.x);
            col.y = 255.f - (255.f - fminf(col.y, 255.f)) * (1.f - r * pc.y);
            col.z = 255.f - (255.f - fminf(col.z, 255.f)) * (1.f - r * pc.z);
        }
    }
    if (alpha < 1.f) {
        // dark palette backdrop with a soft ghost of the picture
        const float rr = sqrtf(Xs * Xs / (0.25f * A * A + 1e-6f) + Ys * Ys * 4.f);
        const float3 pc = pal(Q[19] + Q[20] * (0.22f * Xs / fmaxf(A, 1e-3f) + 0.3f * Ys + 0.35f * rr), Q + 24);
        const float lum = Q[21] * (1.f - 0.55f * fminf(rr * rr, 1.6f));
        float4 bg = make_float4(255.f * pc.x * lum, 255.f * pc.y * lum, 255.f * pc.z * lum, 255.f);
        const float gh = Q[23];
        if (gh > 1e-4f) {
            const float gx = (Xs * 0.85f + 0.5f * A) * fH, gy = (Ys * 0.85f + 0.5f) * fH, o = 0.022f * fH;
            const float4 a = bilin(src, gx - o, gy - o, W, H), b = bilin(src, gx + o, gy - o, W, H);
            const float4 c = bilin(src, gx - o, gy + o, W, H), d = bilin(src, gx + o, gy + o, W, H);
            const float k = 0.25f * gh * (1.f - 0.5f * fminf(rr, 1.f));
            bg.x += k * (a.x + b.x + c.x + d.x) * (0.55f + 0.45f * pc.x);
            bg.y += k * (a.y + b.y + c.y + d.y) * (0.55f + 0.45f * pc.y);
            bg.z += k * (a.z + b.z + c.z + d.z) * (0.55f + 0.45f * pc.z);
        }
        // soft drop shadow: the card's footprint shifted down-right
        const float ss = Q[14];
        if (ss > 1e-4f) {
            const float3 hs = rw_map(Q, Xs - Q[11], Ys - Q[12]);
            if (hs.z > 1e-5f) {
                const float izs = 1.f / hs.z;
                const float ds = rw_box(hs.x * izs, hs.y * izs, A);
                const float sh = ss * (1.f - smooth(-Q[13], Q[13], ds));
                bg.x *= 1.f - sh; bg.y *= 1.f - sh; bg.z *= 1.f - sh;
            }
        }
        col = mix4(bg, col, alpha);
    }
    out[y * W + x] = pack(col);
}
"""


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smoothstep(v: float) -> float:
    t = _clamp01(v)
    return t * t * (3.0 - 2.0 * t)


def _soft(th: float) -> float:
    """Soft limit of a pendulum state (max angle units) so the shown angle stays bounded."""
    return 1.25 * math.tanh(th / 1.25)


def _rot(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr, sr = math.cos(roll), math.sin(roll)
    cp_, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rz = np.array([[cr, -sr, 0.0], [sr, cr, 0.0], [0.0, 0.0, 1.0]])
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cp_, -sp], [0.0, sp, cp_]])
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    return rz @ rx @ ry


def _card_to_screen(R: np.ndarray, s: float, pivot_y: float) -> np.ndarray:
    """Homography card (u, v, 1) -> screen (X k, Y k, k), height units, camera at z = -D looking +z."""
    r0, r1 = R[:, 0], R[:, 1]
    t = -pivot_y * r1 + np.array([0.0, pivot_y, 0.0])
    return np.array([[_D * s * r0[0], _D * s * r1[0], _D * t[0]],
                     [_D * s * r0[1], _D * s * r1[1], _D * t[1]],
                     [s * r0[2], s * r1[2], t[2] + _D]])


def _covers(Minv: np.ndarray, A: float, Z: float) -> bool:
    for cx in (-0.5 * A, 0.5 * A):
        for cy in (-0.5, 0.5):
            hx, hy, hz = Minv @ np.array([cx / Z, cy / Z, 1.0])
            if hz <= 1e-6:
                return False
            if abs(hx / hz) > 0.5 * A + 1e-6 or abs(hy / hz) > 0.5 + 1e-6:
                return False
    return True


def _cover_zoom(Minv: np.ndarray, A: float, zmax: float = 3.0) -> float:
    if _covers(Minv, A, 1.0):
        return 1.0
    if not _covers(Minv, A, zmax):
        return zmax
    lo, hi = 1.0, zmax
    for _ in range(16):
        mid = 0.5 * (lo + hi)
        if _covers(Minv, A, mid):
            hi = mid
        else:
            lo = mid
    return hi


class RotateWobble(Effect):
    name = "rotate_wobble"
    stage = "motion"
    label = "Pendulum sway"
    description = ("The frame rocks like a pendulum: Dutch-tilt roll plus optional 3D card pitch and yaw in "
                   "perspective, pushed alternately left and right by the beats; overscanned or a shadowed card.")
    tags = {"family": ["motion"], "dims": ["2d"], "technique": ["projection", "displacement"],
            "moods": ["playful", "dreamy", "hypnotic"], "style": "lively", "energy": [0.2, 0.9]}
    params = {
        **standard_effect_params(),
        # the beat envelope drives rising-edge detection and the push force directly: keep it raw
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "energy": num(0.0, 1.0, 0.5, "Energy", tier="realtime", bind="level"),
        "roll_max": num(0.0, 25.0, 7.0, "Max roll", unit="°", step=0.1, tier="regional", basic=True),
        "tilt3d": num(0.0, 1.0, 0.45, "3D tilt (flat roll .. full 3D)", tier="regional"),
        "period": num(0.6, 6.0, 1.7, "Pendulum period", unit="s", step=0.05, tier="regional"),
        "damping": num(0.02, 0.8, 0.16, "Damping", step=0.01, tier="regional"),
        "idle_sway": num(0.0, 1.0, 0.45, "Idle sway", tier="regional"),
        "kick": num(0.0, 3.0, 1.0, "Beat push", step=0.05, tier="regional"),
        "backdrop": num(0.0, 1.0, 0.0, "Backdrop (overscan .. shadowed card)", tier="regional"),
        "pivot": num(0.0, 1.0, 0.25, "Pivot (centre .. top edge)", tier="regional"),
        "gloss": num(0.0, 1.0, 0.3, "Gloss", tier="regional"),
        **palette_params("dusk", 1.0, 0.03),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("rotate_wobble")
        self.A = ctx.width / ctx.height
        self._Q = cp.zeros(_NQ, cp.float32)
        self._Qh = np.zeros(_NQ, np.float32)
        seed = 1
        if isinstance(params, dict):
            try:
                seed = int(params.get("seed", 1))
            except (TypeError, ValueError):
                seed = 1
        rng = np.random.default_rng(seed)
        side = 1.0 if rng.uniform() < 0.5 else -1.0
        # start mid-swing (state is in max-angle units; the shown angle is scaled by m, so amount 0 is still exact)
        self.th = np.array([0.65 * side, 0.45 * -side, 0.5 * side])     # roll, pitch, yaw
        self.om = np.zeros(3)
        self.ph = rng.uniform(0.0, _TAU, 3)                              # idle drive phases
        self.lph = rng.uniform(0.0, _TAU)                                # drunken lurch phase
        self.wph = rng.uniform(0.0, _TAU)                                # drive-frequency wander phase
        self.side = side
        self.prev_pulse = 0.0
        self.since_edge = 1.0
        self.drift = 0.0
        self._pal = np.asarray(palette_coeffs(str(self.easer.values().get("palette", "dusk"))), np.float64)

    def _physics(self, p: dict, dt: float) -> None:
        e = _clamp01(p["energy"])
        pulse = _clamp01(p["pulse"])
        # rising edge -> the push changes side (alternate left / right each beat)
        self.since_edge += dt
        if pulse - self.prev_pulse > 0.15 and self.since_edge > 0.12:
            self.side = -self.side
            self.since_edge = 0.0
        self.prev_pulse = pulse
        if dt <= 0.0:
            return
        dt = min(dt, 0.1)
        w = _TAU / max(0.3, float(p["period"]))
        wv = np.array([w, 1.31 * w, 0.77 * w])
        zeta = max(0.01, float(p["damping"])) * (1.3 - 0.6 * e)
        idle = _clamp01(p["idle_sway"])
        target = (0.5 + 0.5 * e) * np.array([1.0, 0.55, 0.65])
        lurch = (0.3 + 0.7 * idle) * 0.55 * e * e
        push = max(0.0, float(p["kick"])) * (0.6 + 1.4 * e ** 1.3) * pulse
        kdir = np.array([self.side, 0.6, 0.6 * self.side])        # roll and yaw alternate; pitch nods
        n = max(1, int(math.ceil(dt * 120.0)))
        h = dt / n
        for _ in range(n):
            self.wph = math.fmod(self.wph + 0.11 * h, _TAU)
            wander = 1.0 + 0.04 * math.sin(self.wph)
            self.ph = np.fmod(self.ph + wv * wander * h, _TAU)
            self.lph = math.fmod(self.lph + 0.37 * w * h, _TAU)
            w2 = wv * wv
            drive = idle * target * 2.0 * zeta * w2 * np.sin(self.ph)
            drive[0] += lurch * w2[0] * math.sin(self.lph)
            drive[2] += 0.5 * lurch * w2[2] * math.sin(self.lph + 1.9)
            drive += push * w2 * kdir
            acc = -w2 * np.sin(1.2 * self.th) / 1.2 - 2.0 * zeta * wv * self.om + drive
            self.om = self.om + acc * h
            self.th = np.clip(self.th + self.om * h, -2.0, 2.0)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(0.0, float(dt)), 0.5)
        self._physics(p, dt)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 1000.0)
        if dt > 0.0:
            a_pal = 1.0 - math.exp(-dt / 0.9)
            self._pal += (np.asarray(palette_coeffs(str(p["palette"])), np.float64) - self._pal) * a_pal
        amount = _clamp01(p["amount"])
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = _smoothstep(amount)
        e = _clamp01(p["energy"])
        pulse = _clamp01(p["pulse"])
        A = self.A
        W, H = self.ctx.width, self.ctx.height

        # angles: amount scales everything (identity at 0); energy widens the swing and the 3D share
        base = math.radians(max(0.0, float(p["roll_max"]))) * (0.7 + 0.6 * e) * m
        t3 = _clamp01(p["tilt3d"]) * (0.75 + 0.5 * e)
        cap = math.radians(38.0)
        roll = max(-cap, min(cap, base * _soft(self.th[0])))
        pitch = max(-cap, min(cap, base * 1.7 * t3 * _soft(self.th[1])))
        yaw = max(-cap, min(cap, base * 2.1 * t3 * _soft(self.th[2])))
        R = _rot(roll, pitch, yaw)
        piv = -0.5 * _clamp01(p["pivot"])                     # y down: the top edge is -0.5

        card = _clamp01(p["backdrop"])
        # overscan solve on the unshrunk card, then morph (log space) toward the shrunk, unzoomed card
        Minv1 = np.linalg.inv(_card_to_screen(R, 1.0, piv))
        zc = _cover_zoom(Minv1, A) * (1.0 + 0.003 * m)
        Z = zc ** (1.0 - card)
        s = 1.0 - 0.22 * m * card
        Minv = np.linalg.inv(_card_to_screen(R, s, piv)) @ np.diag([1.0 / Z, 1.0 / Z, 1.0])
        Minv = Minv / max(abs(Minv[2, 2]), 1e-9)

        # Lambert gain of the card normal vs the flat pose (light from upper left, in front)
        L = np.array([-0.35, -0.55, -1.0])
        L = L / np.linalg.norm(L)
        nrm = R @ np.array([0.0, 0.0, -1.0])
        shade = 1.0 + 0.9 * (float(nrm @ L) - float(np.array([0.0, 0.0, -1.0]) @ L))

        # gloss band: drifts across the card with the pitch / yaw (and a little with the roll); the beat brightens it
        gloss = _clamp01(p["gloss"]) * m * (0.35 + 0.65 * _clamp01(p["tilt3d"])) * 0.32 * (1.0 + 0.8 * pulse)
        goff = 2.2 * yaw + 1.6 * pitch + 0.8 * roll
        gdir = (0.8575, 0.5145)

        Q = self._Qh
        Q[0:9] = Minv.reshape(-1)
        Q[9] = A
        Q[10] = shade
        Q[11], Q[12] = 0.018 * m, 0.034 * m
        Q[13] = 0.035
        Q[14] = 0.78 * m * card
        Q[15] = gloss
        Q[16] = goff
        Q[17], Q[18] = gdir
        Q[19] = self.drift
        Q[20] = float(p["palette_period"])
        Q[21] = 0.17 + 0.05 * e
        Q[22] = 0.45 * card * m * (1.0 + 0.6 * pulse)
        Q[23] = 0.28
        Q[24:36] = self._pal
        self._Q.set(Q)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._Q))
