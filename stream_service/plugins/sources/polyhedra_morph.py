"""Polyhedra morph: every convex uniform polyhedron by Wythoff construction, flowing from one to the next.

Source: Original (inspired by https://www.shadertoy.com/view/ldXczX and
        https://github.com/neozhaoliang/pywonderland/tree/master/src/polytopes/glsl/wythoff)
Author: smfx (original; Wythoff construction maths; mattz's 'Wythoff construction demo' is CC-BY-NC-SA-3.0, idea only)
License: MIT (original)
license_class: permissive
Notes: written from scratch from the textbook Wythoff construction: fold the point into the fundamental cone of the
(p, 3, 2) reflection group (mirrors x = 0, y = 0 and a third plane whose angle sets p = 3/4/5), place the generator
inside the spherical fundamental triangle by barycentric weights, and the solid's faces are the three planes
normal to the triangle's corners through the generator; its edges run from the generator perpendicular to each
mirror.  No shader code from the inspiration links was copied.  SDF helpers from _lib/sdf3d.py (MIT).

Look: one to three nested, counter-rotating shells.  The inner shell is a glossy solid whose faces are coloured by
face type (the polygon around each of the three rotation-axis kinds), outlined by glowing neon tubes with white-hot
cores and vertex nodes; the outer shells are neon cages with faint glass faces.  Each shell's generator glides
round the fundamental triangle on its own path, so the solids flow through the regular, truncated, rectified and
omnitruncated forms (tetrahedron -> truncated -> octahedron, cube -> cuboctahedron, icosahedron ->
icosidodecahedron -> buckyball ...).  The renderer marches through the translucent faces and composites front to
back; edges glow by distance integrated along the ray.

Macros (es = energy smoothed over ~0.3 s for geometry):
  energy -> generator orbit speed (morph rate, e^1.3), shell spin rates, edge glow and halo, the outer-shell
            weights (outer cages thicken and brighten), camera orbit speed;
  pulse  -> a flash cascade by face type (type 0 at the beat, type 1 mid-decay, type 2 in the tail of the envelope),
            an edge-width bump, glow gain and a kick to the morph and spin rates (rates only, never positions);
  speed scales every rate; ``morph_speed`` / ``spin_speed`` scale the morph and the spin alone; drift integrates
  the palette; intensity is output gain.  ``nest_count`` (1-3) fades the outer shells in by weight.

Perf plan: render_scale 0.5, analytic fold (p iterations of abs + one reflection), bounding sphere per shell and for
the whole scene, <= 96 steps with a pixel-cone epsilon, no shadows or AO.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, rig_pack, rot_matrix, wrap
from stream_service.plugins._lib.upsample import internal_size, present

TAU = 2.0 * math.pi
SYMMETRY = {"tetra": 3, "octa": 4, "icosa": 5}
STEPS = 96
NSH = 3
SH0, SHS = 72, 24
NP = SH0 + SHS * NSH

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 pulse | 29 glow gain | 30 fold iterations (p)
#  31 active shells | 32 eps scale | 33..35 third mirror normal | 36..44 triangle corners (2-fold, p-fold, 3-fold)
#  45..47 flash per face type | 48 bound radius | 49 halo | 50..66 rig
#  72 + 24 k, shell k: 0..8 rotation (world -> local, row-major) | 9..11 generator | 12..14 unused | 15..17 mirror
#      offsets dot(v, m_j) | 18 radius | 19 edge radius (unit) | 20 node radius (unit) | 21 face alpha | 22 weight
#      | 23 colour offset
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define STEPS %(steps)d
#define SH0 %(sh0)d
#define SHS %(shs)d

// Fold into the fundamental cone of the (p, 3, 2) group: mirrors x = 0, y = 0 and nc.  p iterations suffice.
__device__ __forceinline__ float3 fold_w(float3 q, const float* __restrict__ P) {
    const int T = (int)P[30];
    const float3 nc = load3(P + 33);
    #pragma unroll
    for (int i = 0; i < 5; ++i) {
        if (i < T) {
            q.x = fabsf(q.x); q.y = fabsf(q.y);
            q = q - nc * (2.f * fminf(dot(q, nc), 0.f));
        }
    }
    return q;
}

// One shell: de = distance to its edge tubes / vertex nodes, df = signed distance to its faces (world units),
// fid = face type (0: around the 2-fold axis, 1: around the p-fold axis, 2: around the 3-fold axis).
__device__ __forceinline__ void shell_eval(float3 p, const float* __restrict__ P, const float* __restrict__ S,
                                           float& de, float& df, int& fid) {
    const float R = S[18], iR = 1.f / R;
    float3 q = f3(dot(load3(S), p), dot(load3(S + 3), p), dot(load3(S + 6), p)) * iR;
    q = fold_w(q, P);
    const float3 w = q - load3(S + 9);
    const float f0 = dot(w, load3(P + 36)), f1 = dot(w, load3(P + 39)), f2 = dot(w, load3(P + 42));
    float d = f0; int id = 0;
    if (f1 > d) { d = f1; id = 1; }
    if (f2 > d) { d = f2; id = 2; }
    const float3 nc = load3(P + 33);
    // half-edges: from the generator v towards each mirror (perpendicular to it), v - s m_j, s in [0, dot(v, m_j)]
    const float s0 = clamp(-w.x, 0.f, S[15]);
    const float s1 = clamp(-w.y, 0.f, S[16]);
    const float s2 = clamp(-dot(w, nc), 0.f, S[17]);
    const float e2 = fminf(fminf(dot2(f3(w.x + s0, w.y, w.z)), dot2(f3(w.x, w.y + s1, w.z))), dot2(w + nc * s2));
    const float e = fminf(sqrtf(e2) - S[19], length(w) - S[20]);
    de = e * R;
    df = d * R;
    fid = id;
}

// Nearest edge over the active shells (de, es) and nearest translucent face (df signed, fs shell, fid type).
__device__ __forceinline__ void scene(float3 p, const float* __restrict__ P, float& de, int& es, float& df,
                                      int& fs, int& fid) {
    de = 1e9f; df = 1e9f; es = 0; fs = -1; fid = 0;
    const int n = (int)P[31];
    const float r = length(p);
    for (int k = n - 1; k >= 0; --k) {                           // outer first: usually nearest from outside
        const float* S = P + SH0 + SHS * k;
        const float bound = r - S[18] * (1.f + S[20]);
        if (bound > fminf(de, fabsf(df))) continue;              // whole shell further than what we have
        float a, b; int id;
        shell_eval(p, P, S, a, b, id);
        if (a < de) { de = a; es = k; }
        if (S[21] > 0.004f && fabsf(b) < fabsf(df)) { df = b; fs = k; fid = id; }
    }
}

__device__ __forceinline__ float3 edge_col(int k, const float* __restrict__ P) {
    const float* S = P + SH0 + SHS * k;
    return srgb_to_linear(pal((0.55f + S[23]) * P[25] + P[26], P + 13));
}

SDF_KERNEL
void polyhedra_morph(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H) * P[32];
    const float* PAL = P + 13;
    const float period = P[25], drift = P[26], glowg = P[29], pulse = P[28];

    // background: dark palette-tinted gradient plus a soft halo around the solids
    const float RB = P[48];
    const float bd = dot(ro, rd);
    const float b2 = fmaxf(dot(ro, ro) - bd * bd, 0.f);           // squared closest approach to the centre
    const float3 bg = srgb_to_linear(pal(drift + 0.62f, PAL)) * (0.010f + 0.022f * (0.5f + 0.5f * rd.y))
                    + srgb_to_linear(pal(drift + 0.15f, PAL)) * (P[49] * __expf(-b2 / (RB * RB * 0.8f)));

    float3 acc = f3(0.f);
    float T = 1.f;
    float g0 = 0.f, g1 = 0.f, g2 = 0.f;
    const float2 bs = iSphere(ro, rd, RB);
    if (bs.x < bs.y && bs.y > 0.f) {
        float t = fmaxf(bs.x, 0.02f);
        const float tf = bs.y;
        bool armed = true, faces_on = true;
        int fhits = 0;
        for (int i = 0; i < STEPS; ++i) {
            const float3 p = ro + rd * t;
            float de, df; int es, fs, fid;
            scene(p, P, de, es, df, fs, fid);
            const float eps = pix * t;
            const float adf = (faces_on && fs >= 0) ? fabsf(df) : 1e9f;
            if (de < eps) {                                      // neon edge: opaque, white-hot core
                const float* S = P + SH0 + SHS * es;
                auto emap = [&](float3 q) { float a, b; int c; shell_eval(q, P, S, a, b, c); return a; };
                const float3 n = calc_normal(emap, p, fmaxf(0.5f * eps, 1e-4f));
                const float core = saturate(dot(n, -rd));
                const float c2 = core * core;
                const float3 ec = edge_col(es, P);
                const float wgt = 0.35f + 0.65f * S[22];
                const float3 c = ec * ((0.45f + 1.5f * c2) * (0.55f + glowg) * wgt)
                               + f3(1.f) * (c2 * c2 * c2 * (0.35f + 0.6f * glowg) * wgt);
                acc += c * T;
                T = 0.f;
                break;
            }
            if (armed && adf < 2.5f * eps) {                     // face: shade, composite, march on through it
                const float* S = P + SH0 + SHS * fs;
                auto fmap = [&](float3 q) { float a, b; int c; shell_eval(q, P, S, a, b, c); return b; };
                float3 n = calc_normal(fmap, p, fmaxf(eps, 1e-4f));
                if (dot(n, rd) > 0.f) n = -n;
                const float fre = 1.f - saturate(dot(n, -rd));
                const float3 fc = srgb_to_linear(pal((fid * 0.3f + S[23]) * period + drift, PAL));
                const Rig L = rig_load(P + 50);
                float3 c = shade(L, fc * 0.8f, n, rd, 1.f, 1.f);
                const float spill = __expf(-de / (0.16f * S[18]));      // neon light bleeding onto the face
                c += edge_col(fs, P) * (spill * (0.25f + 0.55f * glowg));
                c += fc * (0.05f + 0.10f * glowg + 1.9f * P[45 + fid]);  // body glow + beat cascade by face type
                const float a = saturate(S[21] * (0.8f + 0.6f * fre * fre));
                acc += c * (T * a);
                T *= 1.f - a;
                if (T < 0.02f) { T = 0.f; break; }
                armed = false;
                if (++fhits >= 6) faces_on = false;
            } else if (!armed && adf > 4.f * eps) {
                armed = true;
            }
            const float st = fminf(de, armed ? adf : fmaxf(adf, 2.5f * eps));
            // edge glow integrated along the ray (tight core + wide bloom), weighted by what is still visible
            const float gw = T * fminf(st, 0.3f) * (__expf(-de * 16.f) + 0.22f * __expf(-de * 3.2f));
            if (es == 0) g0 += gw; else if (es == 1) g1 += gw; else g2 += gw;
            t += st * 0.95f;
            if (t > tf) break;
        }
    }
    float3 col = acc + bg * T;
    const int n = (int)P[31];
    float3 glow = edge_col(0, P) * g0;
    if (n > 1) glow += edge_col(1, P) * g1;
    if (n > 2) glow += edge_col(2, P) * g2;
    col += glow * (glowg * (2.2f + 1.2f * pulse));
    col = vignette(col, x, y, W, H, 0.45f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"steps": STEPS, "sh0": SH0, "shs": SHS}


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _norm(v) -> np.ndarray:
    v = np.asarray(v, np.float64)
    return v / max(float(np.linalg.norm(v)), 1e-12)


def _group(p: int):
    """Mirror normal nc and the fundamental triangle's corners (2-fold, p-fold, 3-fold axes) for the (p, 3, 2) group."""
    c = math.cos(math.pi / p)
    s = math.sqrt(max(0.75 - c * c, 0.0))
    nc = np.array([-0.5, -c, s])
    corners = np.array([[0.0, 0.0, 1.0], _norm((s, 0.0, 0.5)), _norm((0.0, s, c))])
    return nc, corners


class PolyhedraMorph(Source):
    name = "polyhedra_morph"
    label = "Polyhedra Morph"
    description = ("Every convex uniform polyhedron by Wythoff construction: nested, counter-rotating solids flow "
                   "from tetrahedron through truncated forms to a buckyball, faces coloured by type with neon edges "
                   "(3D raymarched).")
    tags = {"family": "geometric", "dims": "3d", "toolkit": "sdf3d", "technique": ["raymarch", "analytic", "kaleido"],
            "moods": ["geometric", "hypnotic", "bright"], "style": "lively", "energy": [0.2, 0.9]}
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.03),
        "symmetry": enum(list(SYMMETRY), "icosa", "Symmetry (tetra / octa / icosa)"),
        "generator_u": num(0.0, 1.0, 0.667, "Form A (corner -> edge)", tier="regional", basic=True),
        "generator_v": num(0.0, 1.0, 0.5, "Form B (along the edge)", tier="regional", basic=True),
        "morph_range": num(0.0, 1.0, 0.85, "Morph range", tier="regional"),
        "edge_width": num(0.008, 0.08, 0.028, "Edge width", step=0.001, tier="regional"),
        "face_opacity": num(0.0, 1.0, 0.85, "Face opacity", tier="regional"),
        "nest_count": num(1.0, 3.0, 3.0, "Nested shells", step=0.1, tier="regional"),
        "shell_spread": num(1.2, 2.0, 1.45, "Shell spacing", tier="regional"),
        "glow": num(0.0, 2.0, 1.0, "Edge glow", tier="regional"),
        "morph_speed": num(0.0, 4.0, 1.0, "Morph speed", unit="×", tier="realtime", bind="rate"),
        "spin_speed": num(0.0, 4.0, 1.0, "Spin speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("polyhedra_morph")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.morph = float(rng.uniform(0.0, TAU))       # generator tour phase (host only, float64, never wrapped)
        self.amp_ph = float(rng.uniform(0.0, TAU))      # tour amplitude phase (corners <-> interior)
        self.spin = rng.uniform(0.0, TAU, NSH)          # per-shell spin angles
        self.tumble = float(rng.uniform(0.0, TAU))      # slow precession of the spin axes
        self.cam_ph = float(rng.uniform(0.0, TAU))      # camera orbit
        self.drift = 0.0
        self.e_s = None
        self._geo_p = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrate rates (pulse kicks rates only)
        self.morph += float(p["morph_speed"]) * spd * (0.06 + 0.42 * e ** 1.3 + 0.35 * pulse) * dt
        self.amp_ph += float(p["morph_speed"]) * spd * (0.035 + 0.12 * e) * dt
        spin_rate = float(p["spin_speed"]) * spd * (0.10 + 0.50 * e + 0.6 * pulse) * dt
        self.spin += spin_rate * np.array([1.0, -1.35, 0.8])
        self.tumble += spd * (0.03 + 0.08 * e) * dt
        self.cam_ph += spd * (0.04 + 0.10 * e) * dt
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- symmetry group (structural)
        pg = SYMMETRY.get(str(p["symmetry"]), 5)
        if self._geo_p != pg:
            self._geo_p = pg
            self.nc, self.corners = _group(pg)
        nc, corners = self.nc, self.corners

        # ---- shells: weights, radii, generators, rotations
        nest = min(max(float(p["nest_count"]), 1.0), 3.0)
        weights = [1.0, _ss(0.0, 1.0, nest - 1.0) * (0.45 + 0.55 * es), _ss(0.0, 1.0, nest - 2.0) * (0.30 + 0.70 * es)]
        n_act = 1 + int(weights[1] > 0.003) + int(weights[1] > 0.003 and weights[2] > 0.003)
        spread = float(p["shell_spread"])
        R0 = 1.15
        u, v = float(p["generator_u"]), float(p["generator_v"])
        base = np.array([1.0 - u, u * (1.0 - v), u * v])
        rng_m = float(p["morph_range"])
        ew = float(p["edge_width"]) * (1.0 + 0.5 * pulse)
        fo = float(p["face_opacity"])
        Ph = self.Ph
        bound = 0.0
        for k in range(NSH):
            off = SH0 + SHS * k
            wk = weights[k]
            R = R0 * spread ** k
            # generator: glides round the fundamental triangle (corners = regular solids, edges = truncations)
            th = self.morph * (1.0, -0.8, 0.65)[k] + k * TAU / 3.0
            amp = 0.55 + 0.45 * math.sin(self.amp_ph * (1.0, 0.8, 1.2)[k] + 1.3 * k)
            tour = (2.0 / 3.0) * amp * np.cos(th - TAU * np.arange(3) / 3.0)
            b = np.maximum(base + rng_m * tour, 0.0)
            b /= max(float(b.sum()), 1e-9)
            gv = _norm(b @ corners)
            mo = np.array([max(gv[0], 0.0), max(gv[1], 0.0), max(float(gv @ nc), 0.0)])
            axis = (0.45 * math.sin(self.tumble * 0.7 + 1.9 * k), 1.0, 0.4 * math.cos(self.tumble * 0.5 + 2.3 * k))
            tilt = rot_matrix((1.0, 0.0, 0.3 * k), 0.35 + 0.5 * k)
            M = np.asarray(rot_matrix(axis, float(self.spin[k]) % TAU), np.float64).reshape(3, 3) @ \
                np.asarray(tilt, np.float64).reshape(3, 3)
            er = ew * (1.0, 0.8, 0.65)[k] * (0.25 + 0.75 * wk) / R
            Ph[off:off + 9] = M.reshape(-1)
            Ph[off + 9:off + 12] = gv
            Ph[off + 12:off + 15] = 0.0
            Ph[off + 15:off + 18] = mo
            Ph[off + 18] = R
            Ph[off + 19] = er
            Ph[off + 20] = er * 1.45
            Ph[off + 21] = fo if k == 0 else fo * 0.25 * wk
            Ph[off + 22] = wk
            Ph[off + 23] = 0.17 * k
            if k < n_act:
                bound = max(bound, R * (1.0 + er * 1.45))

        # ---- camera: orbit the nest, framing adapts to the shell count
        r_out = R0 * spread ** (nest - 1.0)
        D = (2.55 * r_out + 2.4) * (1.0 + 0.03 * math.sin(1.7 * self.cam_ph) * (0.3 + 0.7 * es))
        yaw = self.cam_ph
        pitch = 0.22 + 0.2 * math.sin(0.61 * self.cam_ph + 0.8)
        eye = orbit_eye((0.0, 0.0, 0.0), D, yaw, pitch)
        roll = 0.05 * math.sin(0.43 * self.cam_ph + 0.3) * (0.4 + 0.6 * es)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=46.0 - 2.0 * pulse, roll=roll)

        # ---- beat: flash cascade by face type as the envelope decays (type 0 first, type 2 last)
        on = _ss(0.0, 0.12, pulse)
        flash = [max(0.0, 1.0 - ((pulse - c) / 0.32) ** 2) * on for c in (0.82, 0.52, 0.22)]

        rig = rig_pack(key_dir=(0.55, 0.8, -0.45), key_col=(1.5, 1.4, 1.3), fill_col=(0.10, 0.12, 0.17),
                       rim_col=(0.35, 0.38, 0.5), amb_col=(0.03, 0.03, 0.035), spec=0.9, shininess=60.0)

        pal_rows = self.session_palette()
        Ph[0:13] = cam
        Ph[13:25] = (np.asarray(pal_rows, np.float32).reshape(12) if pal_rows is not None
                     else palette_coeffs(p["palette"]))
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = pulse
        Ph[29] = float(p["glow"]) * (0.4 + 0.9 * e) * (1.0 + 0.9 * pulse)
        Ph[30] = pg
        Ph[31] = n_act
        Ph[32] = 1.0
        Ph[33:36] = nc
        Ph[36:45] = corners.reshape(-1)
        Ph[45:48] = flash
        Ph[48] = bound + 0.05
        Ph[49] = (0.04 + 0.08 * e + 0.12 * pulse) * min(1.0, 0.3 + 0.7 * float(p["glow"]))
        Ph[50:67] = rig
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
