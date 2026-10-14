"""Geodesic EQ globe: a geodesic sphere tiled in hexagons and pentagons whose bevelled cells extrude and retract
like a 3D equaliser, with emissive gaps between them; beats launch extrusion waves from the poles.

Source: Original (inspired by https://www.shadertoy.com/view/llVXRd and https://www.shadertoy.com/view/Mtc3RX)
Author: smfx (original; tdhooper's 'Geodesic tiling' and 'Icosahedron twist' are Shadertoy-default NC, ideas only;
        built on the toolkit's icosahedral folds)
License: MIT (original)
license_class: permissive

How it works: the point is folded into the positive octant (the icosahedron is symmetric under every axis flip),
the icosahedron face it projects into is the best of 4 face normals there, and its gnomonic barycentric coordinates
times the subdivision n give the small lattice triangle around it.  The nearest of that triangle's 3 vertices is the
cell centre (a Goldberg GP(n,0) tiling: pentagons at the 12 icosahedron vertices, hexagons elsewhere), and the two
bisector planes to the other vertices give the cell walls.  Each cell is a radial prism with a chamfered top whose
height comes from a per-cell pattern (noise/EQ bars .. polar bands .. spiral) plus travelling pole waves.  Cells
can be taller than their neighbours, so the SDF never steps further than the distance to the cell wall (plus the
gap) and marches with step_scale 0.8.  A glowing core sphere fills the gaps; the optional "spidroball" twist turns
the space around each icosahedron vertex before the tiling lookup.

Macros: energy -> extrusion amplitude, pattern / wave / spin rates, gap and cell emission, camera bob.  pulse ->
launches an extrusion wave from a pole (alternating; an integrated travelling envelope spawned on the host-detected
rising edge) and flashes the gaps.  speed scales every rate; palette indexes cells by latitude (+ drift), the gaps
take the complementary palette entry; intensity is output gain.
"""
from __future__ import annotations

import itertools
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, rig_pack, rot_matrix, wrap
from stream_service.plugins._lib.upsample import internal_size, present

MAX_WAVES = 4
MAX_STEPS = 64
R_BASE = 1.0
TWO_PI = 2.0 * math.pi


def _ico_tables() -> dict:
    """Icosahedron tables: the 4 faces whose normals lie in the positive octant (normal, vertices, inverse basis)
    and all 12 unit vertices."""
    phi = (1.0 + math.sqrt(5.0)) * 0.5
    vs = []
    for a in (-1.0, 1.0):
        for b in (-1.0, 1.0):
            vs += [(0.0, a, b * phi), (a, b * phi, 0.0), (b * phi, 0.0, a)]
    V = np.asarray(vs, np.float64)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    dist = np.linalg.norm(V[:, None] - V[None], axis=2)
    edge = np.min(dist[dist > 1e-6])
    faces = [f for f in itertools.combinations(range(12), 3)
             if all(abs(dist[i, j] - edge) < 1e-6 for i, j in itertools.combinations(f, 2))]
    assert len(faces) == 20
    fn, fa, fm = [], [], []
    for f in faces:
        A, B, C = V[f[0]], V[f[1]], V[f[2]]
        n = A + B + C
        if np.all(n >= -1e-9):
            fn += list(n / np.linalg.norm(n))
            fa += [*A, *B, *C]
            fm += list(np.linalg.inv(np.column_stack([A, B, C])).reshape(-1))
    assert len(fn) == 12

    def fmt(xs):
        return ", ".join(f"{x:.9f}f" for x in xs)
    return {"fn": fmt(fn), "fa": fmt(fa), "fm": fmt(fm), "iv": fmt(V.reshape(-1))}


# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 subdivision n | 29 base radius
#  30 core radius | 31 bound radius | 32 gap half-width | 33 bevel | 34 pattern (0 noise .. 1 bands .. 2 spiral)
#  35, 36 noise phases | 37 EQ phase | 38 band phase | 39 spiral phase | 40 extrusion amplitude | 41 min height
#  42 twist angle | 43 step scale | 44 gap emission | 45 cell emission | 46 material (0 chrome .. 1 matte .. 2 glow)
#  47 pulse | 48..56 world->globe rotation | 57..73 rig | 74..85 waves (amp, front, pole) x4 | 86 halo | 87 1/wave width
#  88 leak falloff
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
__constant__ float GP_FN[12] = { @FN@ };
__constant__ float GP_FA[36] = { @FA@ };
__constant__ float GP_FM[36] = { @FM@ };
__constant__ float GP_IV[36] = { @IV@ };

__device__ __forceinline__ float3 gp_lattice(int f, float a, float b, float c) {
    const float* F = GP_FA + 9 * f;
    return normalize(f3(F[0] * a + F[3] * b + F[6] * c, F[1] * a + F[4] * b + F[7] * c, F[2] * a + F[5] * b + F[8] * c));
}
// q = |p| (positive octant).  Returns the nearest Goldberg cell centre (unit, octant space) and the distance from q
// to the nearest wall plane of that cell (bisector planes through the origin, so this is a Euclidean distance).
__device__ __forceinline__ void gp_cell(float3 q, float n, float3& c0, float& e) {
    int f = 0;
    float best = q.x * GP_FN[0] + q.y * GP_FN[1] + q.z * GP_FN[2];
    #pragma unroll
    for (int i = 1; i < 4; ++i) {
        const float d = q.x * GP_FN[3 * i] + q.y * GP_FN[3 * i + 1] + q.z * GP_FN[3 * i + 2];
        if (d > best) { best = d; f = i; }
    }
    const float* M = GP_FM + 9 * f;
    float u = M[0] * q.x + M[1] * q.y + M[2] * q.z;
    float v = M[3] * q.x + M[4] * q.y + M[5] * q.z;
    float w = M[6] * q.x + M[7] * q.y + M[8] * q.z;
    const float k = n / fmaxf(u + v + w, 1e-6f);
    u *= k; v *= k; w *= k;
    const float iu = floorf(u), iv = floorf(v), iw = floorf(w);
    float3 a, b, c;
    if (iu + iv + iw > n - 1.5f) {           // "up" lattice triangle
        a = gp_lattice(f, iu + 1.f, iv, iw); b = gp_lattice(f, iu, iv + 1.f, iw); c = gp_lattice(f, iu, iv, iw + 1.f);
    } else {                                 // "down" lattice triangle
        a = gp_lattice(f, iu, iv + 1.f, iw + 1.f); b = gp_lattice(f, iu + 1.f, iv, iw + 1.f); c = gp_lattice(f, iu + 1.f, iv + 1.f, iw);
    }
    const float da = dot(q, a), db = dot(q, b), dc = dot(q, c);
    if (db > da && db >= dc) { const float3 t = a; a = b; b = t; }
    else if (dc > da && dc > db) { const float3 t = a; a = c; c = t; }
    c0 = a;
    e = fminf(dot(q, normalize(a - b)), dot(q, normalize(a - c)));
}
// "Spidroball": turn space around the nearest icosahedron vertex, fading to zero at the vertex-region boundary.
__device__ __forceinline__ float3 gp_twist(float3 p, float ang) {
    const float3 u = p / (length(p) + 1e-6f);
    float b1 = -2.f, b2 = -2.f; int i1 = 0;
    #pragma unroll
    for (int i = 0; i < 12; ++i) {
        const float d = u.x * GP_IV[3 * i] + u.y * GP_IV[3 * i + 1] + u.z * GP_IV[3 * i + 2];
        if (d > b1) { b2 = b1; b1 = d; i1 = i; } else if (d > b2) { b2 = d; }
    }
    const float f = sqrtf(fmaxf(1.f - b1, 0.f) / fmaxf(1.f - b2, 1e-6f));     // 0 at the vertex .. 1 at the border
    const float wgt = 1.f - smoothstep(0.f, 1.f, f);
    const float a = ang * wgt * wgt;
    const float3 v = f3(GP_IV[3 * i1], GP_IV[3 * i1 + 1], GP_IV[3 * i1 + 2]);
    const float ca = cosf(a), sa = sinf(a);
    return p * ca + cross(v, p) * sa + v * (dot(v, p) * (1.f - ca));
}
// Per-cell extrusion (world-space unit centre c).  hn: 0..1 "level" for colour and emission.
__device__ __forceinline__ float cell_height(float3 c, const float* __restrict__ P, float& hn) {
    const float pat = P[34];
    const float wn = fmaxf(1.f - pat, 0.f), wb = fmaxf(1.f - fabsf(pat - 1.f), 0.f), ws = fmaxf(pat - 1.f, 0.f);
    float v = 0.f;
    if (wn > 0.f) {                                  // smooth noise mixed with per-cell EQ bars
        const unsigned int hx = (unsigned int)(int)floorf(c.x * 512.f + 1024.5f);
        const unsigned int hy = (unsigned int)(int)floorf(c.y * 512.f + 1024.5f);
        const unsigned int hz = (unsigned int)(int)floorf(c.z * 512.f + 1024.5f);
        const float r1 = hash1(hx, hy, hz), r2 = hash1(hx + 7u, hy + 3u, hz);
        const float n1 = sinf(2.3f * c.x + 1.7f * c.z + P[35]) * sinf(2.1f * c.y - 1.3f * c.x + P[36]);
        const float n2 = sinf(3.1f * c.z + 2.6f * c.y + 1.9f * c.x - P[35]);
        const float sn = 0.5f + 0.5f * (0.62f * n1 + 0.38f * n2);
        const float m = 1.f + floorf(r1 * 2.99f);                       // integer multiples: the wrap is invisible
        const float eq = 0.5f + 0.5f * sinf(m * P[37] + 6.2831853f * r2);
        v += wn * mix(sn, eq * (0.3f + 0.7f * sn), 0.5f);
    }
    if (wb > 0.f) {                                  // bands sweeping from both poles to the equator
        const float dp = acosf(fminf(fabsf(c.y), 1.f));
        v += wb * (0.5f + 0.5f * cosf(dp * 5.f - P[38]));
    }
    if (ws > 0.f) {                                  // three-armed spiral
        const float th = acosf(clamp(c.y, -1.f, 1.f));
        v += ws * (0.5f + 0.5f * cosf(th * 4.f + atan2f(c.z, c.x) * 3.f - P[39]));
    }
    v = saturate(v);
    v = v * v * (3.f - 2.f * v);
    float h = P[40] * v, wv = 0.f;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {                    // pole waves (travelling amplitude envelopes)
        const float A = P[74 + 3 * i];
        if (A > 1e-4f) {
            const float th = acosf(clamp(c.y * P[76 + 3 * i], -1.f, 1.f));
            const float g = (th - P[75 + 3 * i]) * P[87];
            wv += A * expf(-g * g);
        }
    }
    hn = saturate(v + 3.f * wv);
    return P[41] + h + wv;
}
// Globe SDF in world space.  id: 0 outside the bound, 1 cell, 2 glowing core.  cw: cell centre (globe frame).
__device__ __forceinline__ float geo_map(float3 pw, const float* __restrict__ P, int& id, float3& cw, float& hn) {
    const Mat3 R = mat3_load(P + 48);
    float3 p = mul(R, pw);
    const float r = length(p);
    const float dbound = r - P[31];
    id = 0; cw = f3(0.f, 1.f, 0.f); hn = 0.f;
    if (dbound > 0.05f) return dbound;
    const float dcore = r - P[30];
    if (fabsf(P[42]) > 1e-4f) p = gp_twist(p, P[42]);
    const float3 s = f3(sgn(p.x), sgn(p.y), sgn(p.z));
    float3 c; float e;
    gp_cell(abs(p), P[28], c, e);
    cw = c * s;
    const float top = cell_height(cw, P, hn);
    const float lat = P[32] - e;
    const float rad = r - (P[29] + top);
    float dc = fmaxf(fmaxf(lat, rad), (lat + rad + P[33]) * 0.70710678f);   // prism with a chamfered rim
    dc = fminf(dc, fmaxf(e, 0.f) + P[32]);          // neighbours may be taller: never step past the wall
    dc = fmaxf(dc, dbound);
    id = dc < dcore ? 1 : 2;
    return fminf(dc, dcore);
}
// Studio environment for reflections (world space): palette sky, horizon ring, two softboxes.
__device__ __forceinline__ float3 envmap(float3 d, const float* __restrict__ P, float boxes) {
    const float* PAL = P + 13;
    const float up = saturate(d.y * 0.5f + 0.5f);
    float3 c = srgb_to_linear(pal(P[26] + 0.2f + 0.25f * d.y, PAL)) * (0.02f + 0.30f * up * up);
    const float az = atan2f(d.z, d.x) * (1.f / SDF_TAU);
    c += srgb_to_linear(pal(P[26] + 2.f * az, PAL)) * (0.9f * expf(-d.y * d.y * 22.f));
    if (boxes > 0.f) {
        c += f3(5.0f, 4.7f, 4.4f) * (boxes * smoothstep(0.955f, 0.985f, dot(d, f3(0.42f, 0.72f, -0.55f))));
        c += f3(1.4f, 1.6f, 2.0f) * (boxes * smoothstep(0.90f, 0.96f, dot(d, f3(-0.85f, 0.25f, -0.46f))));
    }
    return c;
}

SDF_KERNEL
void geodesic_pulse(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float* PAL = P + 13;
    const float drift = P[26], period = P[25];
    auto map = [&](float3 p) { int i; float3 c; float h; return geo_map(p, P, i, c, h); };

    // background: dark palette gradient + faint horizon ring
    const float3 gapc0 = srgb_to_linear(pal(drift + 0.5f, PAL));
    float3 col = envmap(rd, P, 0.f) * 0.10f + gapc0 * 0.012f;
    bool hit = false;
    const float2 bs = iSphere(ro, rd, P[31] + 0.02f);
    if (bs.y > 0.f && bs.x < bs.y) {
        const float t0 = fmaxf(bs.x, 0.01f), t1 = bs.y;
        const Hit h = march(map, ro, rd, t0, t1, MAX_STEPS, 0.75f * pix, P[43]);
        hit = h.hit || (h.steps >= MAX_STEPS && h.t < t1);
        if (hit) {
            const float t = h.t;
            const float3 pos = ro + rd * t;
            int id; float3 cw; float hn;
            geo_map(pos, P, id, cw, hn);
            const float3 n = calc_normal(map, pos, fmaxf(0.5f * pix * t, 5e-4f));
            const float r = length(pos);
            const float3 radial = pos / fmaxf(r, 1e-6f);
            const float lat01 = acosf(clamp(cw.y, -1.f, 1.f)) * (1.f / SDF_PI);
            const float3 gapc = srgb_to_linear(pal(lat01 * period + drift + 0.5f, PAL));
            if (id == 2) {
                col = gapc * (P[44] * 1.6f);
            } else {
                const unsigned int hx = (unsigned int)(int)floorf(cw.x * 512.f + 1024.5f);
                const unsigned int hz = (unsigned int)(int)floorf(cw.z * 512.f + 1024.5f);
                const float rnd = hash1(hx, hz, 77u);
                const float3 cellc = srgb_to_linear(pal(lat01 * period + drift + 0.08f * rnd + 0.22f * hn, PAL));
                const float ao = calc_ao(map, pos, n, 0.14f, 2);
                const float top = saturate(dot(n, radial));
                const float fre = 1.f - saturate(dot(n, -rd));
                const Rig L = rig_load(P + 57);
                const float mat = P[46];
                const float wc = fmaxf(1.f - mat, 0.f), wm = fmaxf(1.f - fabsf(mat - 1.f), 0.f), wg = fmaxf(mat - 1.f, 0.f);
                float3 c = f3(0.f);
                if (wc > 0.f) {                       // chrome: tinted studio reflections
                    const float3 rf = reflect(rd, n);
                    const float k = 0.55f + 0.45f * fre * fre;
                    c += (envmap(rf, P, 1.f) * mix(f3(0.85f), cellc * 1.4f + f3(0.08f), 0.55f) * k * (0.35f + 0.65f * ao)
                          + cellc * 0.04f) * wc;
                }
                if (wm > 0.f) {                       // matte: palette albedo, key/fill/rim rig
                    c += shade(L, cellc * 0.85f, n, rd, 1.f, ao) * wm;
                }
                if (wg > 0.f) {                       // glow: emissive tops, dim sides
                    c += (cellc * (P[45] * (0.35f + 1.4f * hn) * (0.35f + 0.65f * top))
                          + shade(L, cellc * 0.12f, n, rd, 1.f, ao)) * wg;
                }
                c += cellc * (P[45] * 0.25f * hn * top);                          // EQ level glow on every material
                c += gapc * (P[44] * expf(-(r - P[30]) * P[88]));                 // light leaking up the gap walls
                col = c;
            }
        }
    }
    // atmospheric halo around the limb (rays that miss)
    if (!hit) {
        const float tc = -dot(ro, rd);
        const float b = length(ro + rd * fmaxf(tc, 0.f));
        const float dd = fmaxf(b - P[29], 0.f);
        col += gapc0 * (P[86] * (0.75f * expf(-dd * 5.f) + 0.18f * expf(-dd * 1.3f)));
    }
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
"""
_T = _ico_tables()
_SRC = (_SRC.replace("@FN@", _T["fn"]).replace("@FA@", _T["fa"]).replace("@FM@", _T["fm"]).replace("@IV@", _T["iv"])
        .replace("MAX_STEPS", str(MAX_STEPS)))


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class GeodesicPulse(Source):
    name = "geodesic_pulse"
    label = "Geodesic EQ globe"
    description = ("A geodesic globe of bevelled hexagon and pentagon cells that pump like a 3D equaliser over glowing "
                   "gaps, with beat waves rolling out from the poles (3D raymarched).")
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=0.8, drift=0.03),
        "subdivision": num(2, 6, 4, "Subdivision", tier="structural", integer=True),
        "extrusion_pattern": num(0.0, 2.0, 0.35, "Pattern (noise .. bands .. spiral)", tier="regional", basic=True),
        "material": num(0.0, 2.0, 0.7, "Material (chrome .. matte .. glow)", tier="regional", basic=True),
        "cell_gap": num(0.0, 1.0, 0.35, "Cell gap", tier="regional"),
        "bevel": num(0.0, 1.0, 0.5, "Bevel", tier="regional"),
        "extrusion": num(0.2, 2.0, 1.0, "Extrusion depth", unit="×", tier="regional"),
        "twist": num(0.0, 1.0, 0.0, "Spidroball twist", tier="regional"),
        "camera_distance": num(3.4, 7.5, 4.8, "Camera distance", tier="regional"),
        "spin_speed": num(0.0, 4.0, 1.0, "Spin speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("geodesic_pulse")
        self.P = cp.zeros(96, cp.float32)
        self.Ph = np.zeros(96, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.ph = rng.uniform(0.0, TWO_PI, 6)      # noise0, noise1, eq, band, spiral, twist (wrapped at 2 pi)
        self.spin = float(rng.uniform(0.0, TWO_PI))
        self.cam_ph = float(rng.uniform(0.0, TWO_PI))
        self.drift = 0.0
        self.e_s = None
        self.waves: list[list[float]] = []         # [age, front, pole, peak]
        self.prev_pulse = 0.0
        self.since_spawn = 1.0
        self.next_pole = 1.0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.25) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrated phases (pulse adds to rates only)
        rates = (0.25 + 0.9 * e + 0.8 * pulse, 0.21 + 0.7 * e + 0.6 * pulse, 0.8 + 3.2 * e + 2.5 * pulse,
                 0.5 + 2.0 * e + 1.5 * pulse, 0.4 + 1.6 * e + 1.2 * pulse, 0.3 + 0.8 * e)
        for i, r in enumerate(rates):
            self.ph[i] = wrap(self.ph[i] + r * spd * dt, TWO_PI)
        self.spin += float(p["spin_speed"]) * spd * (0.06 + 0.30 * e + 0.15 * pulse) * dt
        self.cam_ph += spd * (0.08 + 0.18 * e) * dt
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- pole waves: spawn on the beat's rising edge, travel pole -> pole, decay
        self.since_spawn += dt
        if dt > 0 and pulse - self.prev_pulse > 0.15 and self.since_spawn > 0.12:
            peak = (0.10 + 0.20 * e) * float(p["extrusion"]) * (0.6 + 0.4 * pulse)
            if len(self.waves) >= MAX_WAVES:
                self.waves.sort(key=lambda w: w[0])
                self.waves.pop()                   # drop the oldest
            self.waves.append([0.0, 0.0, self.next_pole, peak])
            self.next_pole = -self.next_pole
            self.since_spawn = 0.0
        self.prev_pulse = pulse
        front_rate = (1.1 + 1.6 * e) * spd
        alive = []
        for w in self.waves:
            w[0] += dt
            w[1] += front_rate * dt
            if w[1] < math.pi + 0.6 and w[0] < 6.0:
                alive.append(w)
        self.waves = alive
        wave_vals = np.zeros(3 * MAX_WAVES, np.float32)
        wave_sum = 0.0
        for i, (age, front, pole, peak) in enumerate(self.waves[:MAX_WAVES]):
            amp = peak * (1.0 - math.exp(-age / 0.06)) * math.exp(-age / 1.8) * (1.0 - _ss(math.pi - 0.4, math.pi + 0.5, front))
            wave_vals[3 * i:3 * i + 3] = (amp, front, pole)
            wave_sum += amp

        # ---- geometry
        n = int(round(float(p["subdivision"])))
        n = min(max(n, 2), 6)
        cell = 4.0 / n                              # cell size relative to n = 4
        amp = float(p["extrusion"]) * (0.05 + 0.30 * es ** 1.2)
        hmin = 0.03
        r_core = R_BASE - 0.035
        r_max = R_BASE + hmin + amp + wave_sum + 0.03
        gap = (0.004 + 0.034 * float(p["cell_gap"])) * cell
        bevel = float(p["bevel"]) * 0.075 * cell
        twist = float(p["twist"])
        tw_ang = twist * 1.1 * math.sin(self.ph[5])
        step_scale = 0.8 / (1.0 + 0.9 * twist)

        # ---- globe orientation (tilted axis so a pole faces the camera) and camera orbit
        tilt = 0.42
        O = np.asarray(rot_matrix((1.0, 0.0, 0.0), tilt)).reshape(3, 3) @ \
            np.asarray(rot_matrix((0.0, 1.0, 0.0), self.spin)).reshape(3, 3)
        Rm = O.T                                    # world -> globe
        D = float(p["camera_distance"])
        yaw = 0.35 * math.sin(self.cam_ph)
        pitch = 0.18 + (0.07 + 0.10 * es) * math.sin(0.7 * self.cam_ph + 1.0)
        eye = orbit_eye((0.0, 0.0, 0.0), D, yaw, pitch)
        roll = (0.01 + 0.03 * es) * math.sin(0.53 * self.cam_ph + 0.4)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=38.0, roll=roll)

        rig = rig_pack(key_dir=(0.5, 0.75, -0.55), key_col=(1.6, 1.5, 1.4), fill_col=(0.12, 0.14, 0.2),
                       rim_col=(0.3, 0.34, 0.45), amb_col=(0.03, 0.03, 0.035), spec=0.9, shininess=56.0)

        pal_rows = self.session_palette()
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = (np.asarray(pal_rows, np.float32).reshape(12) if pal_rows is not None
                     else palette_coeffs(p["palette"]))
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = n
        Ph[29] = R_BASE
        Ph[30] = r_core
        Ph[31] = r_max
        Ph[32] = gap
        Ph[33] = bevel
        Ph[34] = p["extrusion_pattern"]
        Ph[35:40] = self.ph[0:5]
        Ph[40] = amp
        Ph[41] = hmin
        Ph[42] = tw_ang
        Ph[43] = step_scale
        Ph[44] = (0.35 + 0.9 * e) * (1.0 + 2.2 * pulse)
        Ph[45] = (0.45 + 0.8 * e) * (1.0 + 0.6 * pulse)
        Ph[46] = p["material"]
        Ph[47] = pulse
        Ph[48:57] = Rm.reshape(-1)
        Ph[57:74] = rig
        Ph[74:86] = wave_vals
        Ph[86] = (0.25 + 0.45 * e) * (1.0 + 1.2 * pulse)
        Ph[87] = 1.0 / 0.32
        Ph[88] = 22.0 / max(cell, 0.5)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
