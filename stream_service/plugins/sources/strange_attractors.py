"""Strange attractors: 1.5 million particles riding a chaotic ODE on the GPU, a silky long-exposure density cloud
coloured by speed, with comet tracers and a camera that orbits and dives through the wings.

Source: Original (inspired by https://en.wikipedia.org/wiki/Lorenz_system and
        https://en.wikipedia.org/wiki/Thomas%27_cyclically_symmetric_attractor)
Author: smfx (original; toolkit ParticleSim.attractor; the published ODEs)
License: MIT (original)
license_class: permissive

Built on smfx/_lib/splat3d.py (device ODEs sp_attr_step/sp_attr_f, curl noise sp_curl, Splat3D splatting):
  * sim: one fused kernel (sa_step) advances every particle by RK2 on the chosen attractor (Lorenz, Aizawa, Thomas,
    Halvorsen, Dadras, Wang-Sun four-wing, Rossler), adds a smooth curl-noise perturbation (four particle groups
    see four different fields, so the cloud spreads without any per-frame white noise), respawns diverged states
    by copying a random live particle (already on the attractor; invisible, then fades in), and writes each
    particle's speed |dp/dt| / median as a 0..1 palette index.
  * attractor_param morphs one published coefficient (Lorenz rho 26-28-40, Aizawa a, Thomas b 0.18-0.21,
    Halvorsen a, Dadras e, four-wing a, Rossler c) between surveyed chaotic values; the native centre, radius and
    median speed measured at each end are interpolated so the cloud stays framed and the colours stay put.
    Live particles simply re-converge onto the new shape, so the knob eases without a jump.
  * render: Splat3D.fade(persistence^(60 dt)) keeps a decaying HDR accumulator (long-exposure trails); deposits
    are scaled by (1 - k) so brightness is independent of persistence.  Points at 0.5 scale, palette index =
    speed * period + drift.  1024 tracers carry ring-buffer polyline tails (one vertex per fixed slice of native
    time) and bright heads in a complementary palette slot.  resolve(): exp (log-like) tonemap + bloom + halo
    over a dim palette-tinted background.
  * camera: orbits the attractor (stood up along its symmetry axis), bobs in pitch and periodically dives to
    `camera_distance * (1 - 0.72 * flythrough)`, i.e. through the wings; near fade hides points at the lens.

Macros: energy -> integration rate (0.35x .. 1.75x), brightness, particle count, camera orbit/dive rate, spread
noise, bloom.  pulse -> a curl-noise kick that knocks the cloud off the attractor (it re-converges by itself),
an integration-rate surge, exposure flash (<= 30%) and bloom; each rising edge picks a new kick field.  speed
scales every rate; drift_speed moves the speed->colour palette; intensity is exposure.  A structural change of
`attractor` dips to black, reseeds and warms up on the GPU over a few frames, then fades back in.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.splat3d import (ATTRACTORS, SPLAT3D_OPTIONS, SPLAT3D_SRC, ParticleSim, Splat3D,
                                                 look_at, mat_euler, mat_mul, mat_rot, mat_scale, mat_translate,
                                                 orbit)
from stream_service.plugins._lib.upsample import internal_size

N_PTS = 1_500_000
M_TR = 1024          # comet tracers
K_TR = 40            # tracer tail vertices
WARM_STEPS = 1600    # setup warm-up (stable steps) onto the attractor
WARM_FRAMES = 10     # runtime reseed: 64 steps per frame while dark

# Coefficient varied by attractor_param (index into the 6 params) at (lo, mid, hi) = knob (0, 0.5, 1), with the
# native centre, radius (99.5th pct) and median speed measured at each value (host RK2 survey: 3000 states, 2500
# warm + 2500 sampled stable steps; all bounded and chaotic).  up: native symmetry axis stood up along +y.
# gain: density normalisation from the projected density of 1.5M states (p75 per lit 1080p pixel).
_ATTR = {
    "lorenz": dict(ki=1, vals=(26.0, 28.0, 40.0), centre=((0.0, 0.0, 22.4), (0.0, 0.0, 24.3), (0.0, 0.0, 35.8)),
                   radius=(27.0, 28.5, 36.5), vmed=(71.4, 77.3, 104.3), up=(0.0, 0.0, 1.0), gain=1.0),
    "aizawa": dict(ki=0, vals=(0.8, 0.95, 0.99), centre=((0.0, 0.0, 0.72), (0.0, 0.0, 0.74), (0.0, 0.0, 0.73)),
                   radius=(1.35, 1.54, 1.55), vmed=(2.6, 1.98, 1.4), up=(0.0, 0.0, 1.0), gain=1.6),
    "thomas": dict(ki=0, vals=(0.18, 0.195, 0.21), centre=((0.0, 0.0, 0.0),) * 3, radius=(4.65, 4.55, 4.47),
                   vmed=(0.754, 0.619, 0.676), up=(1.0, 1.0, 1.0), gain=0.6),
    "halvorsen": dict(ki=0, vals=(1.4, 1.89, 2.0),
                      centre=((-3.29, -3.28, -3.31), (-2.91, -2.91, -2.91), (-2.82, -2.82, -2.82)),
                      radius=(12.89, 12.65, 12.63), vmed=(32.9, 30.7, 24.8), up=(1.0, 1.0, 1.0), gain=0.35),
    "dadras": dict(ki=4, vals=(7.0, 9.0, 10.0), centre=((1.03, -0.31, 0.09), (0.64, -0.79, 0.28), (0.48, -0.53, 0.21)),
                   radius=(11.54, 13.43, 11.87), vmed=(4.94, 5.69, 5.96), up=(0.0, 1.0, 0.0), gain=1.0),
    "four_wing": dict(ki=0, vals=(0.15, 0.2, 0.3), centre=((0.0, 0.0, -0.15), (0.0, 0.0, -0.15), (0.0, 0.0, -0.07)),
                      radius=(2.07, 2.6, 3.41), vmed=(0.105, 0.136, 0.213), up=(0.0, 0.0, 1.0), gain=1.1),
    "rossler": dict(ki=2, vals=(4.5, 5.7, 9.0), centre=((0.91, -1.15, 6.21), (1.11, -1.45, 9.64), (1.42, -2.26, 19.88)),
                    radius=(10.46, 14.33, 25.82), vmed=(6.47, 7.16, 6.31), up=(0.0, 0.0, 1.0), gain=1.0),
}
ATTRACTOR_NAMES = list(_ATTR)

_SRC = SPLAT3D_SRC + r"""
struct SaPrm {
    float k[6];
    float dt, limit, cx, cy, cz, R, ivmed, namp, nfreq, ox, oy, oz, scx, scy, scz, sex, sey, sez, fade_in;
    int n, kind, steps, nseed, frame, mode;
};
// mode 0: advance + perturb + respawn; mode 1: reset into the seed box (a warm-up must follow)
extern "C" __global__ void __launch_bounds__(256)
sa_step(float4* __restrict__ pos, float* __restrict__ spd, const SaPrm s)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= s.n) return;
    float k[6];
    #pragma unroll
    for (int j = 0; j < 6; ++j) k[j] = s.k[j];
    const float4 q = pos[i];
    float3 p = make_float3(q.x, q.y, q.z);
    float w = q.w;
    if (s.mode == 1) {
        const float3 h = sp_hash3((unsigned int)i, 0x51ed27u + (unsigned int)s.frame);
        p = make_float3(s.scx + s.sex * (2.f * h.x - 1.f), s.scy + s.sey * (2.f * h.y - 1.f),
                        s.scz + s.sez * (2.f * h.z - 1.f));
        w = 1.f;
    } else {
        bool bad = !(fabsf(p.x) + fabsf(p.y) + fabsf(p.z) < s.limit);
        if (!bad) {
            for (int st = 0; st < s.steps && st < 64; ++st) p = sp_attr_step(s.kind, p, k, s.dt, 1);
            if (s.namp > 0.f) {                       // smooth curl-noise push; 4 groups see 4 fields -> spread
                const float g = (float)(i & 3);
                const float f = s.nfreq / s.R;
                const float3 u = make_float3((p.x - s.cx) * f + s.ox + 17.3f * g, (p.y - s.cy) * f + s.oy - 9.1f * g,
                                             (p.z - s.cz) * f + s.oz + 5.7f * g);
                p = sp_add3(p, sp_mul3(sp_curl(u, (unsigned int)s.nseed, 2), s.namp));
            }
            bad = !(fabsf(p.x) + fabsf(p.y) + fabsf(p.z) < s.limit);
        }
        if (bad) {                                   // respawn on a random live particle (already on the attractor)
            const unsigned int j = sp_hash((unsigned int)i * 0x9E3779B1u ^ ((unsigned int)s.frame * 0x85EBCA77u + 7u))
                                   % (unsigned int)s.n;
            const float4 r = pos[j];
            const float3 h = sp_hash3((unsigned int)i, (unsigned int)s.frame * 31u + 3u);
            if (fabsf(r.x) + fabsf(r.y) + fabsf(r.z) < s.limit) {
                const float jr = 2e-3f * s.R;
                p = make_float3(r.x + jr * (2.f * h.x - 1.f), r.y + jr * (2.f * h.y - 1.f), r.z + jr * (2.f * h.z - 1.f));
            } else {
                p = make_float3(s.scx + s.sex * (2.f * h.x - 1.f), s.scy + s.sey * (2.f * h.y - 1.f),
                                s.scz + s.sez * (2.f * h.z - 1.f));
            }
            w = 0.f;
        } else {
            w = fminf(w + s.fade_in, 1.f);
        }
    }
    pos[i] = make_float4(p.x, p.y, p.z, w);
    const float3 v = sp_attr_f(s.kind, p, k);
    const float u = sqrtf(sp_dot3(v, v)) * s.ivmed;
    spd[i] = u / (1.f + u);                           // 0.5 at the median speed
}
"""

_PRM_DTYPE = np.dtype([("k", np.float32, 6)]
                      + [(f, np.float32) for f in ("dt", "limit", "cx", "cy", "cz", "R", "ivmed", "namp", "nfreq",
                                                   "ox", "oy", "oz", "scx", "scy", "scz", "sex", "sey", "sez",
                                                   "fade_in")]
                      + [(f, np.int32) for f in ("n", "kind", "steps", "nseed", "frame", "mode")])


def _pal_host(coeffs, t: float) -> tuple[float, float, float]:
    """Cosine palette on the host (sRGB 0..1), for the background tint."""
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return tuple(min(max(a[i] + b[i] * math.cos(2 * math.pi * (c[i] * t + d[i])), 0.0), 1.0) for i in range(3))


def _interp3(tri, t: float):
    """Piecewise-linear lo -> mid -> hi at t in [0, 1] (scalars or tuples)."""
    lo, mid, hi = tri
    a, b, u = (lo, mid, t * 2.0) if t < 0.5 else (mid, hi, t * 2.0 - 1.0)
    if isinstance(a, tuple):
        return tuple(x + (y - x) * u for x, y in zip(a, b))
    return a + (b - a) * u


def _align_up(d) -> np.ndarray:
    """3x4 rotation taking direction d to +y."""
    d = np.asarray(d, np.float64)
    d = d / np.linalg.norm(d)
    c = float(d[1])
    if c > 0.9999:
        return mat_rot((0.0, 1.0, 0.0), 0.0)
    axis = (-d[2], 0.0, d[0])                          # d x (0, 1, 0)
    if abs(axis[0]) + abs(axis[2]) < 1e-9:
        axis = (1.0, 0.0, 0.0)
    return mat_rot(axis, math.acos(max(-1.0, min(1.0, c))))


def _smooth01(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class StrangeAttractors(Source):
    name = "strange_attractors"
    label = "Strange attractors"
    description = ("1.5 million particles riding a chaotic attractor: a silky long-exposure cloud coloured by "
                   "speed, with comet tracers and a camera that dives through the wings.")
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=0.8, drift=0.02),
        "attractor": enum(ATTRACTOR_NAMES, "lorenz", "Attractor", tier="structural"),
        "attractor_param": num(0.0, 1.0, 0.5, "Shape (attractor coefficient)", tier="regional", basic=True),
        "trail_persistence": num(0.0, 0.97, 0.88, "Trail persistence", tier="regional"),
        "point_size": num(1.0, 6.0, 1.8, "Point size", unit="px", tier="regional"),
        "camera_distance": num(1.4, 5.0, 2.7, "Camera distance", tier="regional"),
        "flythrough": num(0.0, 1.0, 0.45, "Fly-through depth", tier="regional"),
        "tracers": num(0.0, 1.0, 0.5, "Comet tracers", tier="regional"),
        "spread": num(0.0, 2.0, 1.0, "Cloud spread", tier="regional"),
        "glow": num(0.0, 2.0, 0.6, "Bloom", tier="regional"),
        "flow": num(0.0, 3.0, 1.0, "Flow rate", unit="×", tier="realtime", bind="rate"),
        "orbit_speed": num(0.0, 4.0, 1.0, "Camera orbit", unit="×", tier="realtime", bind="rate"),
    }

    # ------------------------------------------------------------------------------------------------ setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.seed = int(params["seed"])
        self.rng = np.random.default_rng(self.seed)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=N_PTS, max_lines=M_TR * K_TR, pool=N_PTS + 1_200_000)
        self.sim = ParticleSim(ctx)
        self.k_step = raw_module(_SRC, SPLAT3D_OPTIONS).get_function("sa_step")
        self._prm = np.zeros(1, _PRM_DTYPE)

        self.pos = cp.zeros((N_PTS, 4), cp.float32)
        self.spd = cp.zeros(N_PTS, cp.float32)
        self.trc = cp.zeros((M_TR, 4), cp.float32)
        self.tspd = cp.zeros(M_TR, cp.float32)
        self.trails = cp.zeros((M_TR, K_TR, 4), cp.float32)
        hue = self.rng.uniform(0.0, 1.0, M_TR).astype(np.float32)
        self.hidx = cp.asarray(hue)                                        # per-tracer palette offset (heads)
        self.tidx = cp.asarray(np.repeat(hue, K_TR).astype(np.float32))   # same, per tail vertex

        self.frame = 0
        self.nseed = int(self.rng.integers(1, 1 << 30))
        self.ups = {nm: _align_up(_ATTR[nm]["up"]) for nm in _ATTR}
        self.cur = params["attractor"] if params["attractor"] in _ATTR else "lorenz"
        self.tparam = float(params["attractor_param"])
        self._reseed(self.cur, self.tparam, WARM_STEPS)
        self.sw_state = 0        # 0 live, 1 fading out, 2 warming (dark), 3 fading in
        self.sw_gain = 1.0
        self.sw_warm = 0

        r = self.rng.uniform(0.0, 1.0, 6)
        self.yaw = 2 * math.pi * r[0]
        self.bob = 2 * math.pi * r[1]
        self.dive = 0.1 + 0.25 * r[2]          # dive phase (cycles)
        self.breath = 2 * math.pi * r[3]
        self.wob = 2 * math.pi * r[4]
        self.drift = 0.0
        self.noff = np.array([0.0, 0.0, 0.0])
        self.kick_off = self.rng.normal(0.0, 3.0, 3)
        self.e_s = None
        self.prev_pulse = 0.0
        self.since_edge = 1.0
        self.slot = 0
        self.trail_acc = 0.0

    # ------------------------------------------------------------------------------------------------ sim
    def _coeffs(self, name: str, t: float):
        A, S = _ATTR[name], ATTRACTORS[name]
        k = list(S["k"])
        k[A["ki"]] = _interp3(A["vals"], t)
        return k, _interp3(A["centre"], t), _interp3(A["radius"], t), _interp3(A["vmed"], t)

    def _launch(self, arr, spd, name: str, t: float, *, steps: int, dt: float, namp: float = 0.0,
                nfreq: float = 1.0, off=(0.0, 0.0, 0.0), mode: int = 0, fade_in: float = 0.05) -> None:
        S = ATTRACTORS[name]
        k, c, R, vmed = self._coeffs(name, t)
        s = self._prm[0]
        s["k"] = k
        s["dt"], s["limit"] = dt, S["limit"]
        s["cx"], s["cy"], s["cz"] = c
        s["R"], s["ivmed"] = R, 1.0 / max(vmed, 1e-6)
        s["namp"], s["nfreq"] = namp, nfreq
        s["ox"], s["oy"], s["oz"] = off
        (s["scx"], s["scy"], s["scz"]), (s["sex"], s["sey"], s["sez"]) = S["spawn"][0], S["spawn"][1]
        s["fade_in"] = fade_in
        n = int(arr.shape[0])
        s["n"], s["kind"], s["steps"] = n, S["kind"], max(0, min(int(steps), 64))
        s["nseed"], s["frame"], s["mode"] = self.nseed, self.frame & 0x7FFFFFFF, mode
        self.k_step(((n + 255) // 256,), (256,), (arr, spd, s))

    def _reseed(self, name: str, t: float, warm_steps: int) -> None:
        """Seed-box reset + full warm-up onto the attractor (setup), then tracers copied from live particles."""
        dt = ATTRACTORS[name]["dt"]
        self._launch(self.pos, self.spd, name, t, steps=0, dt=dt, mode=1)
        done = 0
        while done < warm_steps:
            k = min(64, warm_steps - done)
            self._launch(self.pos, self.spd, name, t, steps=k, dt=dt, fade_in=1.0)
            done += k
        self._tracers_from_cloud()

    def _tracers_from_cloud(self) -> None:
        self.ctx.cp.copyto(self.trc, self.pos[:M_TR])
        self.ctx.cp.copyto(self.tspd, self.spd[:M_TR])
        self.sim.trail_fill(self.trails, self.trc)
        self.slot = 0
        self.trail_acc = 0.0

    # ------------------------------------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self.frame += 1
        dtc = min(max(float(dt), 0.0), 0.1)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dtc / 0.4) if dtc > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- structural attractor change: dip, reseed + warm while dark, fade back in
        want = p["attractor"] if p["attractor"] in _ATTR else self.cur
        if self.sw_state == 0 and want != self.cur:
            self.sw_state = 1
        if self.sw_state == 1:
            self.sw_gain = max(0.0, self.sw_gain - dtc / 0.35) if dtc > 0 else self.sw_gain
            if self.sw_gain <= 0.0:
                self.cur = want
                self._launch(self.pos, self.spd, self.cur, self.tparam, steps=0, dt=ATTRACTORS[self.cur]["dt"],
                             mode=1)
                self.sw_state, self.sw_warm = 2, 0
        elif self.sw_state == 2:
            self._launch(self.pos, self.spd, self.cur, self.tparam, steps=64, dt=ATTRACTORS[self.cur]["dt"],
                         fade_in=1.0)
            self.sw_warm += 1
            if self.sw_warm >= WARM_FRAMES:
                self._tracers_from_cloud()
                self.sw_state = 3
        elif self.sw_state == 3:
            self.sw_gain = min(1.0, self.sw_gain + dtc / 0.6)
            if self.sw_gain >= 1.0:
                self.sw_state = 0
        name = self.cur
        A, S = _ATTR[name], ATTRACTORS[name]

        # ---- beat: rising edge picks a new kick field (velocity change only, never a position jump)
        self.since_edge += dtc
        if pulse - self.prev_pulse > 0.15 and self.since_edge > 0.12:
            self.kick_off = self.kick_off + self.rng.normal(0.0, 3.0, 3)
            self.since_edge = 0.0
        self.prev_pulse = pulse

        # ---- host-integrated phases
        flow = max(float(p["flow"]), 0.0)
        rate_mult = spd * flow * (0.35 + 1.4 * e ** 1.2 + 0.6 * pulse)
        native = S["rate"] * rate_mult * dtc                                   # native time this frame
        self.yaw += float(p["orbit_speed"]) * spd * (0.04 + 0.16 * es + 0.05 * pulse) * dtc
        self.bob += spd * (0.13 + 0.12 * es) * dtc
        self.dive = math.fmod(self.dive + spd * (0.022 + 0.03 * es) * dtc, 1000.0)
        self.breath += spd * (0.10 + 0.12 * es) * dtc
        self.wob += spd * (0.05 + 0.06 * es) * dtc
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dtc, 1000.0)
        self.noff = np.fmod(self.noff + np.array([0.6, 0.35, -0.7]) * spd * (0.05 + 0.12 * es) * dtc, 500.0)

        # attractor coefficient: the eased knob plus a slow, energy-scaled breathing
        tp = float(p["attractor_param"]) + 0.06 * (0.3 + es) * math.sin(self.breath)
        self.tparam = min(max(tp, 0.0), 1.0)

        # ---- simulate (cloud + tracers)
        if self.sw_state != 2:
            steps = int(math.ceil(native / S["dt"])) if native > 0 else 0
            steps = min(steps, 64)
            sdt = native / steps if steps > 0 else S["dt"]
            R = _interp3(A["radius"], self.tparam)
            # spread (energy) + beat kick, in cloud radii per second -> native displacement this frame
            amp = float(p["spread"]) * (0.05 + 0.25 * es ** 1.5) + 2.0 * pulse
            namp = amp * R * dtc * min(max(spd, 0.25), 2.0)
            off = tuple(self.noff + self.kick_off)
            self._launch(self.pos, self.spd, name, self.tparam, steps=steps, dt=sdt, namp=namp, nfreq=1.6, off=off)
            self._launch(self.trc, self.tspd, name, self.tparam, steps=steps, dt=sdt, namp=namp, nfreq=1.6, off=off)
            # tracer tails: the head slot follows the tracer; advance a slot per fixed slice of native time
            self.sim.trail_push(self.trails, self.trc, self.slot)
            self.trail_acc += native
            interval = S["rate"] * 2.6 / (K_TR - 1)
            if self.trail_acc >= interval:
                self.slot = (self.slot + 1) % K_TR
                self.sim.trail_push(self.trails, self.trc, self.slot)
                self.trail_acc = 0.0 if self.trail_acc > 2 * interval else self.trail_acc - interval

        # ---- camera: orbit + pitch bob + periodic dive through the wings
        D = float(p["camera_distance"])
        bump = (0.5 - 0.5 * math.cos(2 * math.pi * self.dive)) ** 1.6
        Dc = D * (1.0 - 0.72 * float(p["flythrough"]) * bump)
        pitch = 0.28 + (0.22 + 0.1 * es) * math.sin(self.bob)
        cam = look_at(orbit((0.0, 0.0, 0.0), Dc, self.yaw, pitch), (0.0, 0.0, 0.0), fov_deg=52.0, near=0.03)

        k_, c_, R_, _ = self._coeffs(name, self.tparam)
        model = mat_mul(mat_euler(0.16 * math.sin(self.wob), 0.0, 0.11 * math.sin(0.7 * self.wob + 1.0)),
                        mat_mul(self.ups[name], mat_mul(mat_scale(1.0 / R_), mat_translate([-v for v in c_]))))

        # ---- accumulate: decaying long exposure (deposits scaled so brightness ignores persistence)
        sp = self.sp
        if dtc <= 0.0 or dt > 0.5:
            sp.clear()
            dep = 1.0
        else:
            kf = float(p["trail_persistence"]) ** (dtc * 60.0)
            sp.fade(kf)
            dep = 1.0 - kf
        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        pal = tuple(coeffs.tolist())
        period, drift = float(p["palette_period"]), self.drift
        near = (0.04, 0.35)
        far = (Dc - 0.2, Dc + 2.4)

        if self.sw_state != 2:
            n = int(N_PTS * (0.5 + 0.5 * es))
            bright = (0.55 + 0.65 * e) * A["gain"]
            psz = float(p["point_size"])
            ipt = 0.09 * bright * (N_PTS / max(n, 1)) ** 0.6 * (psz / 1.8) ** 0.7 * dep   # bigger grains glow more
            sp.points(self.pos, cam=cam, n=n, model=model, intensity=ipt, size_px=float(p["point_size"]),
                      size_range=(0.6, 7.0), weight_in_w=True, palette=pal, index=self.spd, period=period,
                      drift=drift, near_fade=near, far_fade=far)
            tr = float(p["tracers"])
            if tr > 0.01:
                m = max(1, int(M_TR * tr))
                tb = (0.6 + 0.6 * e) * dep
                sp.polylines(self.trails, n_strips=m, head=(self.slot + 1) % K_TR, tail_fade=True,
                             weight_in_w=True, cam=cam, model=model, intensity=0.7 * tb, width_px=1.4,
                             size_range=(0.6, 4.0), palette=pal, index=self.tidx, period=0.3,
                             drift=drift + 0.5, near_fade=near, far_fade=far)
                sp.points(self.trc, cam=cam, n=m, model=model, intensity=2.6 * tb, size_px=1.7 * psz,
                          size_range=(0.6, 7.0), weight_in_w=True, palette=pal, index=self.hidx, period=0.3,
                          drift=drift + 0.5, colour=(1.5, 1.5, 1.5), near_fade=near, far_fade=far)

        bg = _pal_host(coeffs, drift + 0.55)
        bgk = 0.035
        sp.resolve(out, exposure=float(p["intensity"]) * self.sw_gain * (1.0 + 0.3 * pulse), tonemap="exp",
                   saturation=1.15, glow=float(p["glow"]) * (0.55 + 0.6 * e) * (1.0 + 0.5 * pulse),
                   glow_radius_px=20.0, glow_wide=0.6,
                   background=(bg[0] * bgk, bg[1] * bgk, bg[2] * bgk + 0.008))

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = self.sim = None
        self.pos = self.spd = self.trc = self.tspd = self.trails = self.hidx = self.tidx = None
