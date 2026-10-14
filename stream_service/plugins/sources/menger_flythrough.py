"""Menger Cathedral: a glide through an infinite, domain-repeated Menger sponge (3D raymarched).

Source: https://www.shadertoy.com/view/WtcfDB ("Menger sponge space base", mrange);
        https://www.shadertoy.com/view/MscyW4 ("Inverse Menger", chronosis);
        https://iquilezles.org/articles/menger/ ; SDF/raymarch helpers from stream_service/plugins/_lib/sdf3d.py
        (MIT, see that file).
Author: Marten Range (mrange, 'Menger sponge space base', CC0); chronosis (MscyW4, MIT); DE from the toolkit's deMenger
License: CC0-1.0 + MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: rewritten for CUDA from the ideas, not line by line.  The sponge is the
        infinite "carve" form (iq/chronosis: every recursion level cuts a lattice of cross-shaped holes, the field
        is the max over levels), but each level's lattice is mirror-repeated (triangle wave) instead of mod-repeated,
        so mrange's per-iteration rotation and offset can be applied about each hole's own centre and the field
        stays continuous and 1-Lipschitz; the coarse "nave" and "corridor" levels stay axis-aligned so a host-side
        B-spline path along the corridor lattice is always clear; fractional last level (holes grow from their
        axes); materials and emissive seams from the two strongest levels; no shadows, 3-tap AO, exp fog.)

Look: cathedral corridors of square holes (period corridor_cell), every third junction opening into a nave three
times larger; finer recursion levels perforate the walls.  Each surface takes its material from the recursion
level that cut it (palette index = level trap x period + drift); the rims where one level's holes pierce another
level's faces glow as emissive seams in pal(drift + 0.5).  Twist rotates (and slowly spins) the fine levels about
their own hole centres and offsets them, turning the clean architecture into an alien "space base".

Camera: a uniform cubic B-spline through the corridor lattice (control points every half cell, junctions every
cell), choosing straight on or a left/right turn at junctions from the seed; constant-speed arc-length stepping,
eased look-ahead, banking into turns.  The whole field is periodic with 12 lattice units, so the path is silently
re-centred to keep float32 coordinates small.

Macros (es = energy slewed for geometry):
  energy -> forward speed 0.2..2.5 world u/s (integrated), fine-level spin rate (the twist's angular velocity),
            seam-glow gain, fog thinning, and a fractional recursion count iterations-1 -> iterations+0.5
            (default 3 -> 4.5; the last level's carve weight fades, never a hard step).
  pulse  -> seam emissive flash (flash_kick), a FOV kick (low-passed), a palette-drift spurt and a small bump
            of the fine levels' offset amplitude (all decay with the envelope).
  speed scales flight, spin and bob rates; palette: pal(level trap x period + drift); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, rot_matrix, wrap
from stream_service.plugins._lib.upsample import internal_size, present

MAXL = 5           # recursion levels: 0 nave (period 12, holes every 6), 1 corridors (holes every 2), 2..4 fine
STEPS = 80         # march steps
NP = 104           # P floats
PERIOD = 12.0      # the whole field repeats every 12 lattice units (mirror period of the nave level)
FINE_AXES = ((0.0, 0.0, 1.0), (0.0, 0.0, 1.0),
             (0.22, 1.0, 0.35), (1.0, 0.28, -0.32), (-0.35, 0.42, 1.0))
FINE_OFFS = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0),
             (0.62, -0.35, 0.48), (-0.41, 0.57, 0.33), (0.29, 0.44, -0.61))

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density (per lattice unit)
#  29 hole offset | 30 effective iterations | 31 seam gain | 32 seam flash | 33 surface weight
#  34 near-miss glow gain | 35 headlight falloff | 36 tmax | 37 vignette | 38 fog brightness
#  39 spatial colour frequency | 40.. per level i: 40 + 12 i: Mat3 (9, row-major) + offset (3, cell units)
#  100 seam width (lattice units at level 0) | 101 fog hue offset
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MF_MAXL %(maxl)d
#define MF_STEPS %(steps)d

// Mirror repetition: a continuous triangle wave in [-1, 1] with period 4, zero (a hole centre) at u = 0 mod 2.
__device__ __forceinline__ float mf_tri(float u) { return fabsf(mod(u + 1.f, 4.f) - 2.f) - 1.f; }

// Carve value of level i (positive inside its holes): the cross of three square tubes of half-width h * w,
// rotated about the hole centre for the fine levels.  1-Lipschitz (triangle wave slope 1, rotation, median).
__device__ __forceinline__ float mf_level(float3 p, const float* __restrict__ P, const int i, const float S,
                                          const float h, const float w) {
    const float* __restrict__ L = P + 40 + 12 * i;
    const float3 u = p * S + load3(L + 9);
    float3 q = f3(mf_tri(u.x), mf_tri(u.y), mf_tri(u.z));
    if (i >= 2) q = mul(mat3_load(L), q);
    q = abs(q);
    const float med = fmaxf(fminf(q.x, q.y), fminf(fmaxf(q.x, q.y), q.z));     // middle of the three
    return (h * w - med) / S;
}

__device__ __forceinline__ float mf_de(float3 p, const float* __restrict__ P) {
    const float h = (2.f - P[29]) * (1.f / 3.f), it = P[30];
    float d = -1e9f, S = 1.f / 3.f;
    #pragma unroll
    for (int i = 0; i < MF_MAXL; ++i) {
        const float w = saturate(it - (float)i);
        if (w <= 0.f) break;
        if (d >= h / S) break;                        // no level from here down can raise the max
        d = fmaxf(d, mf_level(p, P, i, S, h, w));
        S *= 3.f;
    }
    return d;
}

// The two strongest levels at a point: the surface's level (material) and the runner-up (seams).
__device__ __forceinline__ void mf_mat(float3 p, const float* __restrict__ P, float& c1, int& l1, float& c2, int& l2) {
    const float h = (2.f - P[29]) * (1.f / 3.f), it = P[30];
    c1 = -1e9f; c2 = -1e9f; l1 = 0; l2 = 0;
    float S = 1.f / 3.f;
    #pragma unroll
    for (int i = 0; i < MF_MAXL; ++i) {
        const float w = saturate(it - (float)i);
        if (w <= 0.f) break;
        const float c = mf_level(p, P, i, S, h, w);
        if (c > c1) { c2 = c1; l2 = l1; c1 = c; l1 = i; }
        else if (c > c2) { c2 = c; l2 = i; }
        S *= 3.f;
    }
}

SDF_KERNEL
void menger_flythrough(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pa = pixel_angle(cam, H);
    const float tmax = P[36];
    float t = 0.01f, glow = 0.f;
    bool hit = false;
    int steps = 0;
    for (int i = 0; i < MF_STEPS; ++i) {
        const float d = mf_de(ro + rd * t, P);
        steps = i + 1;
        const float pe = pa * t;
        glow += saturate(1.f - d / (pe * 10.f + 1e-4f));
        if (d < pe) { hit = true; break; }
        t += d * 0.92f;
        if (t > tmax) break;
    }
    const float drift = P[26];
    const float3 fogc = srgb_to_linear(pal(drift + P[101], P + 13)) * P[38];
    const float3 scol = srgb_to_linear(pal(drift + 0.5f, P + 13));
    float3 col = fogc;
    float3 emis = f3(0.f);
    float tf = tmax;
    if (hit || t < tmax) {                                         // step-limited rays: shade where they stopped
        tf = t;
        const float3 pos = ro + rd * t;
        const float pe = pa * t;
        auto map = [&](float3 q) { return mf_de(q, P); };
        const float3 n = calc_normal(map, pos, 0.5f * pe + 2e-5f);
        float c1, c2; int l1, l2;
        mf_mat(pos, P, c1, l1, c2, l2);
        // material by recursion level, with a slow spatial hue wander so long corridors are not flat
        const float lv = (float)l1;
        const float fq = P[39];
        const float sp = 0.5f + 0.25f * (sinf(fq * pos.x + 0.9f) + sinf(fq * 0.73f * pos.z + 0.61f * fq * pos.y + 2.1f));
        const float idx = lv * 0.2f + 0.24f * sp;
        float3 alb = srgb_to_linear(pal(idx * P[25] + drift, P + 13));
        alb = alb * (0.8f + 0.12f * lv);
        // occlusion: 3 taps + step count
        float ao = calc_ao(map, pos, n, 0.16f, 3);
        const float sao = 1.f - (float)steps / (float)MF_STEPS;
        ao *= saturate(0.3f + 0.7f * sao);
        // headlight at the eye, a soft key from above, sky fill tinted by the fog
        const float hd = saturate(dot(n, -rd));
        const float att = 1.f / (1.f + square(t / P[35]));
        const float3 kd = normalize(f3(0.3f, 0.9f, 0.2f));
        const float3 key = f3(1.f, 0.94f, 0.86f);
        float3 lit = alb * (key * (0.2f + 0.8f * hd * hd) * att * 1.35f + key * 0.28f * saturate(dot(n, kd))
                            + fogc * (2.2f + 1.6f * n.y));
        lit += key * powf(hd, 40.f) * att * 0.3f;
        col = lit * ao * P[33];
        // emissive seams: rims where the runner-up level's faces meet the surface's level
        const int lf = max(l1, l2);
        const float wsz = P[100] * exp2f(-1.5849625f * (float)lf);          // P[100] / 3^lf
        const float wpx = fmaxf(wsz, 1.4f * pe);
        const float cons = sqrtf(wsz / wpx);                                 // thin far lines dim, not fatten
        const float e = c1 - c2;
        const float core = 1.f - smoothstep(0.f, wpx, e);
        const float halo = expf(-e / (4.f * wpx)) * 0.3f;
        const float3 sc = srgb_to_linear(pal(drift + 0.5f + 0.06f * (float)lf, P + 13));
        emis = sc * ((core + halo) * cons * (P[31] + P[32]) * (0.45f + 0.55f * ao));
    }
    col = fog_exp(col, fogc, tf, P[28]);
    col += emis * expf(-0.5f * P[28] * tf);                                  // neon reads further than surfaces
    const float g = saturate(glow * 0.12f);
    col += scol * (g * g * P[34]) * expf(-0.6f * P[28] * tf);              // near-miss edge glow
    col = vignette(col, x, y, W, H, P[37]);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"maxl": MAXL, "steps": STEPS}


# ----------------------------------------------------------------------------- host helpers
def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _norm(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else np.array([0.0, 0.0, 1.0])


def _bspline(P0, P1, P2, P3, u: float):
    """Uniform cubic B-spline position and derivative on one segment."""
    u2, u3 = u * u, u * u * u
    m = 1.0 - u
    pos = (m * m * m * P0 + (3 * u3 - 6 * u2 + 4) * P1 + (-3 * u3 + 3 * u2 + 3 * u + 1) * P2 + u3 * P3) / 6.0
    der = (-3 * m * m * P0 + (9 * u2 - 12 * u) * P1 + (-9 * u2 + 6 * u + 3) * P2 + 3 * u2 * P3) / 6.0
    return pos, der


class MengerFlythrough(Source):
    name = "menger_flythrough"
    label = "Menger Cathedral"
    description = ("Glide through an infinite Menger sponge: cathedral corridors of square holes, materials by "
                   "recursion level, glowing seams between levels and fog; twist turns it into an alien space base "
                   "(3D raymarched).")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.45, period=1.0, drift=0.03),
        "twist_per_iter": num(0.0, 0.4, 0.12, "Twist per level", unit="rad", tier="regional", basic=True),
        "hole_offset": num(0.9, 1.15, 1.0, "Hole tightness", tier="regional"),
        "corridor_cell": num(2.0, 6.0, 3.0, "Corridor cell", unit="u", tier="regional"),
        "iterations": num(3.0, 5.0, 4.0, "Recursion levels", step=0.1, tier="regional"),
        "glow_mix": num(0.0, 1.0, 0.4, "Surface / neon edges", tier="regional"),
        "fog_density": num(0.02, 0.4, 0.1, "Fog density", unit="/u", tier="regional"),
        "flash_kick": num(0.0, 2.0, 1.0, "Beat seam flash", tier="regional"),
    }

    # ------------------------------------------------------------------ lifecycle
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("menger_flythrough")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        self.rng = np.random.default_rng(int(params["seed"]))
        sp = self.session_palette()
        self.pal = np.asarray(PALETTES[params["palette"]] if sp is None else sp, np.float64).reshape(-1).copy()
        # path: start on a corridor junction (x = 0 is a nave), heading along +z or +x
        x0 = 2.0 * float(self.rng.integers(0, 3))
        z0 = 2.0 * float(self.rng.integers(0, 6))
        self.dir = np.array([0.0, 0.0, 1.0]) if self.rng.random() < 0.7 else np.array([1.0, 0.0, 0.0])
        j = np.array([x0, 0.0, z0])
        self.ctrl = [j - 2.0 * self.dir, j - self.dir, j.copy()]
        self.junc = j.copy()
        self.since_turn = 0
        self.U = 1.0                      # global spline parameter (segment index + fraction)
        while len(self.ctrl) < 12:
            self._extend()
        self.look = None
        self.roll = 0.0
        # host-integrated phases
        self.drift = 0.0
        self.spin = np.zeros(MAXL)
        self.osc = float(self.rng.uniform(0.0, 2.0 * math.pi))
        self.bob = 0.0
        self.sway = 0.0
        self.e_s = None
        self.fovk = 0.0

    def close(self) -> None:
        self.buf = None
        self.P = None

    # ------------------------------------------------------------------ path
    def _extend(self) -> None:
        d = self.dir
        self.ctrl.append(self.junc + d)
        self.junc = self.junc + 2.0 * d
        self.ctrl.append(self.junc.copy())
        if self.since_turn >= 1 and self.rng.random() < 0.32:
            left = np.array([d[2], 0.0, -d[0]])
            self.dir = left if self.rng.random() < 0.5 else -left
            self.since_turn = 0
        else:
            self.since_turn += 1

    def _eval(self, U: float):
        s = int(math.floor(U))
        u = U - s
        c = self.ctrl
        return _bspline(c[s], c[s + 1], c[s + 2], c[s + 3], u)

    def _advance(self, ds: float) -> None:
        # constant-speed stepping in a few substeps (|B'| is ~0.7..1 per unit of U)
        n = max(1, int(math.ceil(ds / 0.05)))
        for _ in range(n):
            _, der = self._eval(self.U)
            self.U += (ds / n) / max(float(np.linalg.norm(der)), 0.3)
        while self.U > 2.0:                               # drop control points behind us
            self.ctrl.pop(0)
            self.U -= 1.0
        while len(self.ctrl) < int(self.U) + 10:
            self._extend()
        # the field repeats every PERIOD units: re-centre to keep float32 coordinates small (invisible)
        head = self.ctrl[int(self.U) + 1]
        if abs(head[0]) > 4 * PERIOD or abs(head[2]) > 4 * PERIOD:
            shift = np.array([PERIOD * round(head[0] / PERIOD), 0.0, PERIOD * round(head[2] / PERIOD)])
            self.ctrl = [c - shift for c in self.ctrl]
            self.junc = self.junc - shift

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a_e = 1.0 - math.exp(-dt / 1.2) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a_e
        es = self.e_s
        k = pulse * (0.35 + 0.65 * e)

        # palette (session palette wins; changes blend)
        sp = self.session_palette()
        tp = np.asarray(PALETTES[p["palette"]] if sp is None else sp, np.float64).reshape(-1)
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / 0.8))

        # rates (kicks add to rates/amplitudes only)
        cell = float(p["corridor_cell"])
        v_world = 0.2 + 2.3 * e ** 1.25
        self._advance(v_world * (2.0 / cell) * spd * dt)
        self.drift = wrap(self.drift + (float(p["drift_speed"]) * spd + 0.15 * k) * dt, 1000.0)
        twist = float(p["twist_per_iter"])
        spin_rate = (twist / 0.4) * (0.06 + 0.5 * e) * spd
        for i in range(2, MAXL):
            self.spin[i] += spin_rate * (1.0 + 0.35 * (i - 2)) * (1.0 if i % 2 == 0 else -1.0) * dt
        self.osc += (0.1 + 0.3 * e) * spd * dt
        self.bob += (0.25 + 0.35 * e) * spd * dt
        self.sway += (0.12 + 0.25 * e) * spd * dt
        self.fovk += (7.0 * k - self.fovk) * ((1.0 - math.exp(-dt / 0.07)) if dt > 0 else 0.0)

        # camera on the spline, looking ahead, banking into turns
        eye, _ = self._eval(self.U)
        ahead, _ = self._eval(self.U + 0.9)
        eye = eye + np.array([0.0, 0.035 * math.sin(self.bob), 0.0])
        want = _norm(ahead - eye)
        if self.look is None:
            self.look = want
        prev = self.look
        self.look = _norm(self.look + (want - self.look) * ((1.0 - math.exp(-dt / 0.35)) if dt > 0 else 1.0))
        yaw_rate = float(np.cross(prev, self.look)[1]) / dt if dt > 0 else 0.0
        roll_t = -0.3 * max(min(yaw_rate, 1.5), -1.5)
        self.roll += (roll_t - self.roll) * ((1.0 - math.exp(-dt / 0.4)) if dt > 0 else 0.0)
        right = _norm(np.cross(self.look, np.array([0.0, 1.0, 0.0])))
        sw = 0.03 + 0.05 * es
        look = _norm(self.look + right * (sw * math.sin(self.sway)) + np.array([0.0, 0.6 * sw * math.sin(0.7 * self.sway + 1.3), 0.0]))
        roll = self.roll + 0.4 * sw * math.sin(0.53 * self.sway + 0.4)
        fov = 74.0 + 4.0 * es + self.fovk
        cam = camera_pack(tuple(eye), tuple(eye + look), fov_deg=fov, roll=roll)

        # geometry
        it_eff = min(max(float(p["iterations"]) - 1.0 + 1.5 * es, 2.0), float(MAXL))
        glow_mix = float(p["glow_mix"])
        fog = float(p["fog_density"]) * (cell * 0.5) * (1.25 - 0.6 * es)          # per lattice unit, thins with energy
        off_amp = 0.55 * twist + 0.12 * k

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = self.pal
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * (0.92 + 0.22 * es)
        Ph[28] = fog
        Ph[29] = p["hole_offset"]
        Ph[30] = it_eff
        seam_base = 0.3 + 2.4 * glow_mix
        Ph[31] = seam_base * (0.35 + 0.9 * e)
        Ph[32] = float(p["flash_kick"]) * 2.4 * k * (0.5 + 0.5 * seam_base)
        Ph[33] = 1.0 - 0.8 * glow_mix
        Ph[34] = (0.12 + 0.9 * glow_mix) * (0.5 + 0.7 * e)
        Ph[35] = 2.6
        Ph[36] = min(4.6 / max(fog, 1e-3), 40.0)
        Ph[37] = 0.35
        Ph[38] = 0.09 + 0.05 * es
        Ph[39] = 0.31
        for i in range(MAXL):
            base = 40 + 12 * i
            if i < 2:
                Ph[base:base + 9] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
                Ph[base + 9:base + 12] = 0.0
            else:
                ang = (i - 1) * twist * (1.0 + 0.4 * math.sin(self.osc + 1.9 * i)) + self.spin[i]
                Ph[base:base + 9] = rot_matrix(FINE_AXES[i], ang)
                Ph[base + 9:base + 12] = [off_amp * c for c in FINE_OFFS[i]]
        Ph[100] = 0.07 + 0.05 * glow_mix
        Ph[101] = 0.27
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
