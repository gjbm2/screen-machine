"""Splat demo: a palette-coloured spiral galaxy of up to a million stars around a glowing wireframe polyhedron.

Source: Original (reference scene for smfx/_lib/splat3d.py)
Author: smfx
License: MIT
license_class: permissive

The splat toolkit's reference scene, deliberately written with NO custom kernel: everything is Splat3D draw calls
on device geometry uploaded once in setup, moved per frame only by model matrices and the camera.
  * galaxy: galaxy_disc() stars (weight in w), shuffled so drawing the first n is a uniform subsample; per-star
    palette index from radius (+ jitter); rigid spin by a model matrix; tilted disc; far fade.
  * star field: a big sphere of faint background stars for parallax as the camera orbits.
  * core: a wireframe polyhedron (+ a smaller counter-rotating copy) with bright vertex nodes, palette graded per
    vertex; optional geodesic cage around it.
  * bursts: on each beat a shell of points and an equatorial shock ring expand from the core and fade (2 slots).
  * resolve(): bloom + wide halo, ACES, palette-tinted deep-space background, written straight to the 4K output.

Macros: energy -> galaxy spin, camera orbit, wire tumble, star count and brightness, wire glow, bloom, camera bob.
pulse -> burst spawn (rising edge), galaxy/wire flash, wire swell (amplitude) and a spin-rate kick.  speed scales
every rate; palette colours everything (session palette honoured); intensity is exposure.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.plugins.base import Source

from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.splat3d import (POLYHEDRA, Splat3D, fibonacci_sphere, galaxy_disc, geodesic, look_at,
                                                 mat_euler, mat_mul, mat_rot, mat_scale, mat_trs, orbit, polyhedron,
                                                 ring)
from stream_service.plugins._lib.upsample import internal_size

N_GALAXY = 900_000
N_FIELD = 24_000
N_SHELL = 6_000
GALAXY_R = 2.4


def _pal_host(coeffs, t: float) -> tuple[float, float, float]:
    """Cosine palette on the host (sRGB 0..1), for the background tint."""
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return tuple(min(max(a[i] + b[i] * math.cos(2 * math.pi * (c[i] * t + d[i])), 0.0), 1.0) for i in range(3))


class SplatDemo(Source):
    name = "splat_demo"
    label = "Galaxy core (splat demo)"
    description = "A million-star spiral galaxy wheeling around a glowing wireframe polyhedron; beats fire shockwaves."
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.02),
        "density": num(0.1, 1.0, 0.65, "Star density", tier="regional", basic=True),
        "tilt": num(0.05, 1.3, 0.45, "View elevation", unit="rad", tier="regional"),
        "camera_distance": num(3.5, 12.0, 7.0, "Camera distance", tier="regional"),
        "glow": num(0.0, 2.0, 0.8, "Bloom", tier="regional"),
        "wire_size": num(0.2, 1.6, 0.75, "Polyhedron size", tier="regional"),
        "cage": num(0.0, 1.0, 0.35, "Geodesic cage", tier="regional", basic=True),
        "stars": num(0.0, 2.0, 0.8, "Background stars", tier="regional"),
        "polyhedron": enum(POLYHEDRA, "icosahedron", "Polyhedron", tier="structural"),
        "arms": num(1, 6, 2, "Spiral arms", tier="structural", integer=True),
        "spin_speed": num(0.0, 4.0, 1.0, "Galaxy spin", unit="×", tier="realtime", bind="rate"),
        "orbit_speed": num(0.0, 4.0, 1.0, "Camera orbit", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        seed = int(params["seed"])
        rng = np.random.default_rng(seed)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=N_GALAXY, max_lines=4096)

        # galaxy: shuffled so drawing the first n stars is a uniform subsample (density / energy change n)
        g = galaxy_disc(N_GALAXY, arms=int(params["arms"]), seed=seed, radius=GALAXY_R, thickness=0.05)
        g = g[rng.permutation(N_GALAXY)]
        r = np.linalg.norm(g[:, [0, 2]], axis=1) / GALAXY_R
        gidx = (0.62 * np.sqrt(r) + rng.normal(0.0, 0.06, N_GALAXY)).astype(np.float32)
        self.gal = cp.asarray(np.ascontiguousarray(g, np.float32))
        self.gidx = cp.asarray(gidx)

        # background star field on a big sphere (weights skewed: a few bright, many faint)
        f = np.ones((N_FIELD, 4), np.float32)
        f[:, :3] = fibonacci_sphere(N_FIELD) * rng.uniform(28.0, 40.0, (N_FIELD, 1))
        f[:, 3] = rng.uniform(0.0, 1.0, N_FIELD) ** 6
        self.field = cp.asarray(f)
        self.fidx = cp.asarray(rng.uniform(0.0, 1.0, N_FIELD).astype(np.float32))

        # every polyhedron uploaded once (switchable live); per-vertex palette index from height
        self.poly = {}
        for nm in POLYHEDRA:
            V, E = polyhedron(nm)
            self.poly[nm] = (cp.asarray(V), cp.asarray(E), cp.asarray((0.5 + 0.25 * V[:, 1]).astype(np.float32)))
        gV, gE = geodesic(3)
        self.cageV, self.cageE = cp.asarray(gV), cp.asarray(gE)

        # burst shell (unit sphere with a little radial thickness) and shock ring
        sh = np.ones((N_SHELL, 4), np.float32)
        sh[:, :3] = fibonacci_sphere(N_SHELL) * rng.uniform(0.94, 1.06, (N_SHELL, 1))
        sh[:, 3] = rng.uniform(0.3, 1.0, N_SHELL)
        self.shell = cp.asarray(sh)
        self.ring = cp.asarray(ring(255, 1.0))

        self.spin = 0.0          # galaxy spin angle (rad, host float64, never wrapped)
        self.yaw = 0.0           # camera orbit
        self.tumble = 0.0        # wire tumble phase
        self.bob = 0.0           # camera bob phase
        self.drift = 0.0
        self.e_s = None          # smoothed energy (counts / camera)
        self.prev_pulse = 0.0
        self.bursts = [[0.0, 0.0], [0.0, 0.0]]   # [radius, brightness] per slot
        self.slot = 0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- rates (pulse kicks rates only)
        self.spin += p["spin_speed"] * spd * (0.04 + 0.32 * e + 0.5 * pulse) * dt
        self.yaw += p["orbit_speed"] * spd * (0.03 + 0.12 * e) * dt
        self.tumble += spd * (0.15 + 0.9 * e + 0.8 * pulse) * dt
        self.bob += spd * (0.2 + 0.3 * e) * dt
        self.drift = math.fmod(self.drift + p["drift_speed"] * spd * dt, 1000.0)

        # ---- bursts: spawn on the pulse's rising edge into the dimmer slot; expand + fade
        if pulse > 0.25 and pulse - self.prev_pulse > 0.12:
            self.slot = 0 if self.bursts[0][1] < self.bursts[1][1] else 1
            self.bursts[self.slot] = [0.25, pulse]
        self.prev_pulse = pulse
        for b in self.bursts:
            if b[1] > 1e-3:
                b[0] += spd * (2.2 + 2.5 * es) * dt / (1.0 + 0.25 * b[0])
                b[1] *= math.exp(-dt / 0.55)

        # ---- camera
        D = float(p["camera_distance"])
        pitch = float(p["tilt"]) + (0.03 + 0.07 * es) * math.sin(self.bob)
        cam = look_at(orbit((0.0, 0.0, 0.0), D, self.yaw, pitch), (0.0, 0.0, 0.0), fov_deg=50.0)

        sp = self.sp
        sp.clear()
        sess = self.session_palette()                      # session palette (4 x 3 rows) wins over our own
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        pal = tuple(coeffs.tolist())                       # Splat3D takes a name or 12 coefficients
        period, drift = float(p["palette_period"]), self.drift
        far = (D + 0.6 * GALAXY_R, D + 2.2 * GALAXY_R)

        # background stars (no perspective sizing: they are far)
        if p["stars"] > 0.0:
            sp.points(self.field, cam=cam, intensity=0.9 * p["stars"], size_px=1.1, depth_ref=0.0,
                      weight_in_w=True, palette=pal, index=self.fidx, period=0.3, drift=drift + 0.4)

        # galaxy
        n = int(N_GALAXY * float(p["density"]) * (0.55 + 0.45 * es))
        gain = (0.55 + 0.75 * e) * (1.0 + 0.7 * pulse) * (300_000.0 / max(n, 1)) ** 0.6
        gm = mat_rot((0.0, 1.0, 0.0), self.spin)
        sp.points(self.gal, cam=cam, n=n, model=gm, intensity=0.22 * gain, size_px=1.25, weight_in_w=True,
                  palette=pal, index=self.gidx, period=period, drift=drift, far_fade=far)

        # core: polyhedron + counter-rotating inner copy, vertex nodes, cage
        V, E, vidx = self.poly.get(p["polyhedron"], self.poly["icosahedron"])
        size = float(p["wire_size"]) * (1.0 + 0.14 * pulse) * (0.9 + 0.15 * es)
        rot = mat_euler(0.37 * self.tumble, self.tumble, 0.21 * self.tumble)
        wire_e = (0.9 + 1.4 * e) * (1.0 + 1.8 * pulse)
        wm = mat_trs(r=rot, s=size)
        sp.wire(V, E, cam=cam, model=wm, intensity=wire_e, width_px=2.2, palette=pal, index=vidx, period=1.0,
                drift=drift + 0.5, colour=(1.6, 1.6, 1.6))
        im = mat_trs(r=mat_euler(-0.6 * self.tumble, -0.8 * self.tumble, 0.3 * self.tumble), s=0.48 * size)
        sp.wire(V, E, cam=cam, model=im, intensity=0.7 * wire_e, width_px=1.6, palette=pal, index=vidx,
                period=1.0, drift=drift + 0.15)
        sp.points(V, cam=cam, model=wm, intensity=6.0 * wire_e, size_px=5.0, palette=pal, drift=drift + 0.55,
                  colour=(1.3, 1.3, 1.3))
        if p["cage"] > 0.0:
            cm = mat_trs(r=mat_rot((0.2, 1.0, 0.1), -0.35 * self.tumble), s=1.75 * size)
            sp.wire(self.cageV, self.cageE, cam=cam, model=cm, intensity=0.45 * p["cage"] * (0.7 + 0.6 * e),
                    width_px=1.0, palette=pal, drift=drift + 0.3)

        # bursts: shell of points + equatorial shock ring in the galaxy plane
        for rad, bright in self.bursts:
            if bright <= 1e-3:
                continue
            fade = bright / (1.0 + 0.4 * rad)
            sm = mat_mul(gm, mat_scale(rad))
            sp.points(self.shell, cam=cam, model=sm, intensity=0.35 * fade, size_px=1.6, weight_in_w=True,
                      palette=pal, drift=drift + 0.75, far_fade=far)
            sp.polylines(self.ring, k=256, cam=cam, model=mat_scale((1.15 * rad, 1.0, 1.15 * rad)),
                         intensity=1.6 * fade, width_px=2.0, palette=pal, drift=drift + 0.8)

        # resolve straight to the output, over a dim palette-tinted space background
        bg = _pal_host(coeffs, drift + 0.6)
        bgk = 0.06
        sp.resolve(out, exposure=1.1 * float(p["intensity"]), tonemap="aces", saturation=1.1,
                   glow=float(p["glow"]) * (0.7 + 0.5 * e) * (1.0 + 0.6 * pulse), glow_radius_px=22.0,
                   glow_wide=0.55, background=(bg[0] * bgk, bg[1] * bgk, bg[2] * bgk + 0.01))

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = self.gal = self.gidx = self.field = self.fidx = self.shell = self.ring = None
        self.poly = {}
        self.cageV = self.cageE = None

