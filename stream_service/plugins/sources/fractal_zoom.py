"""Endless fractal zoom in float32: the Poincare function of a Julia map at a repelling fixed point.

Maths (design by screen-machine-2f; the Taylor start is an addition that keeps the identity exact):

* ``f(z) = z^2 + c`` has fixed points ``z* = (1 -/+ sqrt(1 - 4c)) / 2`` (alpha / beta) with multiplier
  ``lam = f'(z*) = 2 z*``.  We zoom into a repelling one (``|lam| > 1``).
* The Poincare function ``Psi`` (``Psi(0) = z*``, ``Psi'(0) = 1``) satisfies ``Psi(lam zeta) = f(Psi(zeta))``,
  so the escape count obeys ``n(Psi(zeta / lam)) = n(Psi(zeta)) + 1``: in zeta coordinates the picture is
  exactly self-similar under scaling by ``lam``, with a +1 iteration shift.
* Pixel ``w`` (y up, |Im w| <= 1 over the screen height) maps to ``zeta = S w`` with
  ``S = view_radius * lam^-u * exp(i theta)``, ``u = frac(phase)``.  The phase integrates
  ``dt * zoom_rate / ln|lam|``, so live parameter changes stay continuous.
* ``Psi(zeta) = f^N(Psi(eta))`` with ``eta = zeta lam^-N = D w``, ``D = S lam^-N`` (host, complex128).
  ``Psi(eta)`` near 0 comes from its Taylor series: comparing coefficients in ``Psi(lam z) = Psi(z)^2 + c``
  gives ``a_1 = 1``, ``a_k = sum_{i=1}^{k-1} a_i a_{k-i} / (lam^k - lam)``.  With 12 terms ``|eta|`` can be
  ~0.1-0.5 (instead of ~1e-5 for the first-order start ``Psi(eta) ~ z* + eta``), which cuts ``N`` 4-8x
  for the slow (|lam| ~ 1.07-1.12) fixed points.  N is chosen so ``|D| w_max <= eta_max``.
* Then iterate in offset form around ``z*``: ``delta <- lam delta + delta^2`` (exactly ``f``), escape-tested
  on ``z = z* + delta`` with ``|z|^2 > 256``.  The first ``M`` steps, while ``|delta|`` provably stays below
  0.25, skip the escape test.
* ``lam^-u`` also rotates the view by ``-arg(lam)`` per period (the natural spiral).  ``theta`` cancels
  ``1 - natural_spin`` of that and adds ``rotation`` deg/s; any continuous ``theta`` keeps the seam exact.
* Smooth count ``mu = k + 1 - log2(0.5 log2 |z|^2)``; ``depth = mu - N - u`` is invariant under the
  self-similarity; colour index ``depth / palette_period + drift`` feeds a cosine palette
  ``a + b cos(2 pi (c t + d))``.  The iteration limit is applied to ``depth`` too (with a short fade to
  black), so at the wrap (u: 1 -> 0, scale jumps back by ``lam``) nothing pops: the loop is seamless.
"""
from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ...palettes import NAMES as PALETTE_NAMES
from ...palettes import cosine
from ..base import Source

# Interesting parameters c, each with the fixed point that looks best.
C_PRESETS: dict[str, tuple[complex, str]] = {
    "dendrite": (complex(0.0, 1.0), "alpha"),           # c = i: three-armed dendrite, |lam| 1.39, arg 116 deg
    "seahorse": (complex(-0.8, 0.156), "alpha"),        # double spiral, |lam| 1.07, arg 172 deg
    "nebula": (complex(0.285, 0.01), "beta"),           # spiral of beads, |lam| 1.12, arg -20 deg
    "rabbit": (complex(-0.1226, 0.7449), "alpha"),      # Douady rabbit, three lobes meeting, |lam| 1.11
}

# Inigo Quilez cosine palettes a + b cos(2 pi (c t + d)), values sRGB-encoded (shared: stream_service/palettes.py).
PALETTES: dict[str, tuple[tuple[float, float, float], ...]] = {n: cosine(n) for n in PALETTE_NAMES}

SERIES_TERMS = 12    # Taylor terms of Psi used for the start point
SERIES_TOL = 1e-6    # relative truncation error allowed (a 4K pixel is ~5e-4 of the screen height)
ETA_MAX_CAP = 0.5
MIN_ABS_LAM = 1.02   # below this the fixed point is (nearly) not repelling: use the other one
MAX_N = 400
FADE_ITERS = 6.0     # depth fade to black just below the iteration limit

_KERNEL = r"""
extern "C" __global__
void fractal_zoom(uchar4* __restrict__ out, const int W, const int H,
                  const float Dr, const float Di,          // eta = D * w
                  const float2* __restrict__ coef,         // Taylor coefficients a_1..a_K of Psi - z*
                  const int K,
                  const float zr0, const float zi0,        // fixed point z*
                  const float lr, const float li,          // multiplier lambda
                  const int m_unchecked, const int n_total,
                  const float n_shift,                     // N + u
                  const float max_depth, const float inv_fade,
                  const float inv_period, const float drift,
                  const float ar, const float ag, const float ab,     // palette a
                  const float br, const float bg, const float bb,     // palette b
                  const float cr, const float cg, const float cb,     // palette c
                  const float qr, const float qg, const float qb,     // palette d
                  const int aa, const float exposure)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x;
    const int y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float sc = 2.f / (float)H;
    const float inv_aa = 1.f / (float)aa;
    const float in_r = 0.015f, in_g = 0.015f, in_b = 0.02f;   // inside / undecided: near-black
    float accr = 0.f, accg = 0.f, accb = 0.f;
    for (int sy = 0; sy < aa; ++sy) {
        for (int sx = 0; sx < aa; ++sx) {
            const float wr = ((float)x + ((float)sx + 0.5f) * inv_aa - 0.5f * (float)W) * sc;
            const float wi = (0.5f * (float)H - (float)y - ((float)sy + 0.5f) * inv_aa) * sc;
            const float er = Dr * wr - Di * wi;
            const float ei = Dr * wi + Di * wr;
            // delta0 = Psi(eta) - z* = eta * (a_1 + a_2 eta + ... + a_K eta^(K-1))   (Horner)
            float pr = coef[K - 1].x, pi = coef[K - 1].y;
            for (int j = K - 2; j >= 0; --j) {
                const float t = fmaf(pr, er, fmaf(-pi, ei, coef[j].x));
                pi = fmaf(pr, ei, fmaf(pi, er, coef[j].y));
                pr = t;
            }
            float dr = pr * er - pi * ei;
            float di = pr * ei + pi * er;
            int k = 0;
            for (; k < m_unchecked; ++k) {           // |delta| < 0.25 here: no escape possible
                const float t = fmaf(lr, dr, fmaf(-li, di, fmaf(dr, dr, -di * di)));
                di = fmaf(lr, di, fmaf(li, dr, 2.f * dr * di));
                dr = t;
            }
            float m2 = 0.f;
            bool esc = false;
            // Brent-style periodicity check: an orbit that returns (to float precision) to a saved point has
            // converged onto an attracting cycle - an interior pixel - so stop instead of running the budget.
            float sr = dr, si = di;
            int save_at = k + 8;
            for (; k < n_total; ++k) {
                const float t = fmaf(lr, dr, fmaf(-li, di, fmaf(dr, dr, -di * di)));
                di = fmaf(lr, di, fmaf(li, dr, 2.f * dr * di));
                dr = t;
                const float zr = zr0 + dr, zi = zi0 + di;
                m2 = fmaf(zr, zr, zi * zi);
                if (m2 > 256.f) { esc = true; break; }
                const float ddr = dr - sr, ddi = di - si;
                if (fmaf(ddr, ddr, ddi * ddi) < 1e-12f) break;
                if (k == save_at) { sr = dr; si = di; save_at += save_at - m_unchecked; }
            }
            float wgt = 0.f, t = 0.f;
            if (esc) {
                const float depth = (float)(k + 1) + 1.f - __log2f(0.5f * __log2f(m2)) - n_shift;
                wgt = fminf(fmaxf((max_depth - depth) * inv_fade, 0.f), 1.f);
                t = depth * inv_period + drift;
            }
            accr += in_r + wgt * (ar + br * __cosf(6.2831853f * (cr * t + qr)) - in_r);
            accg += in_g + wgt * (ag + bg * __cosf(6.2831853f * (cg * t + qg)) - in_g);
            accb += in_b + wgt * (ab + bb * __cosf(6.2831853f * (cb * t + qb)) - in_b);
        }
    }
    const float s = 255.f * inv_aa * inv_aa * exposure;
    out[y * W + x] = make_uchar4((unsigned char)fminf(fmaxf(accr * s + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(accg * s + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(accb * s + 0.5f, 0.f), 255.f), 255);
}
"""


@dataclass
class Geometry:
    c: complex
    point: str          # the fixed point actually used ("alpha" | "beta")
    zstar: complex
    lam: complex
    log_lam: complex    # principal log; Re = ln|lam| > 0
    coef: list[complex] = field(repr=False)   # a_1..a_K
    eta_max: float      # |eta| bound over the screen
    n: int              # N
    fallback: bool      # the requested point was not repelling enough


def poincare_coefficients(zstar: complex, lam: complex, terms: int = SERIES_TERMS) -> list[complex]:
    """Taylor coefficients a_1..a_K of Psi(eta) - z* from Psi(lam z) = Psi(z)^2 + c (a_0 = z*, a_1 = 1)."""
    a = [zstar, complex(1.0, 0.0)]
    for k in range(2, terms + 1):
        a.append(sum(a[i] * a[k - i] for i in range(1, k)) / (lam ** k - lam))
    return a[1:]


def series_radius(coef: list[complex], tol: float = SERIES_TOL) -> float:
    """|eta| up to which the truncated series is accurate to ~tol (relative), from the coefficient growth."""
    K = len(coef)
    rho = max((abs(coef[k]) ** (1.0 / k) for k in range(max(1, K - 4), K)), default=0.0)
    if rho <= 0:
        return ETA_MAX_CAP
    return min(ETA_MAX_CAP, tol ** (1.0 / K) / rho)


def fixed_point_geometry(c: complex, point: str, view_radius: float, w_max: float) -> Geometry:
    s = cmath.sqrt(1 - 4 * c)
    cand = {"alpha": (1 - s) / 2, "beta": (1 + s) / 2}
    other = "beta" if point == "alpha" else "alpha"
    use, fallback = point, False
    if abs(2 * cand[point]) < MIN_ABS_LAM and abs(2 * cand[other]) > abs(2 * cand[point]):
        use, fallback = other, True
    z = cand[use]
    lam = 2 * z
    if abs(lam) < MIN_ABS_LAM:  # only near the parabolic c = 1/4: push |lam| up (then only roughly self-similar)
        lam = lam / abs(lam) * MIN_ABS_LAM if lam != 0 else complex(MIN_ABS_LAM, 0)
    log_lam = cmath.log(lam)
    coef = poincare_coefficients(z, lam)
    eta_max = series_radius(coef)
    n = math.ceil(math.log(max(view_radius * w_max, 1e-12) / eta_max) / log_lam.real)
    return Geometry(c, use, z, lam, log_lam, coef, eta_max, max(0, min(MAX_N, n)), fallback)


def unchecked_steps(r0: float, abs_lam: float, n: int, limit: float = 0.25) -> int:
    """Largest M <= n such that the bound r_{k+1} = r_k (|lam| + r_k) stays <= limit for k < M."""
    r, m = r0, 0
    while m < n:
        nxt = r * (abs_lam + r)
        if nxt > limit:
            break
        r, m = nxt, m + 1
    return m


def frame_constants(geo: Geometry, view_radius: float, u: float, theta: float, w_max: float) -> tuple[complex, int]:
    """(D, M) for one frame: host-side complex128 maths."""
    S = view_radius * cmath.exp(-u * geo.log_lam) * cmath.exp(1j * theta)
    D = S * cmath.exp(-geo.n * geo.log_lam)
    eta = abs(D) * w_max
    r0 = sum(abs(a) * eta ** (k + 1) for k, a in enumerate(geo.coef))  # bound on |delta0|
    return D, unchecked_steps(r0, abs(geo.lam), geo.n)


class FractalZoom(Source):
    name = "fractal_zoom"
    label = "Fractal zoom"
    description = ("An endless zoom into a Julia set at a repelling fixed point (exactly self-similar, so it "
                   "loops seamlessly in float32).")
    params = {
        "preset": {"type": "string", "enum": [*C_PRESETS, "custom"], "default": "nebula",
                   "x-ui": {"label": "Fractal", "widget": "select"}},
        "c_re": {"type": "number", "minimum": -2.0, "maximum": 2.0, "default": 0.0, "x-ease": False,
                 "x-ui": {"label": "c real (custom)", "step": 0.001, "widget": "slider"}},
        "c_im": {"type": "number", "minimum": -2.0, "maximum": 2.0, "default": 1.0, "x-ease": False,
                 "x-ui": {"label": "c imaginary (custom)", "step": 0.001, "widget": "slider"}},
        "point": {"type": "string", "enum": ["auto", "alpha", "beta"], "default": "auto",
                  "x-ui": {"label": "Fixed point", "widget": "select"}},
        "zoom_rate": {"type": "number", "minimum": -2.0, "maximum": 2.0, "default": 0.25,
                      "x-bindable": True,
                      "x-ui": {"label": "Zoom speed", "step": 0.01, "unit": "e-folds/s", "widget": "slider",
                             "basic": True}},
        "natural_spin": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0,
                         "x-bindable": True,
                         "x-ui": {"label": "Natural spin", "step": 0.01, "widget": "slider"}},
        "rotation": {"type": "number", "minimum": -90.0, "maximum": 90.0, "default": 3.0,
                     "x-bindable": True,
                     "x-ui": {"label": "Extra rotation", "step": 0.5, "unit": "deg/s", "widget": "slider"}},
        "view_radius": {"type": "number", "minimum": 0.05, "maximum": 4.0, "default": 1.0,
                        "x-bindable": True,
                        "x-ui": {"label": "View radius", "step": 0.01, "widget": "slider"}},
        "iterations": {"type": "integer", "minimum": 16, "maximum": 2000, "default": 200,
                       "x-ui": {"label": "Iterations", "step": 1, "widget": "slider"}},
        "palette": {"type": "string", "enum": list(PALETTES), "default": "ember", "x-palette": True,
                    "x-ui": {"label": "Palette", "widget": "select", "basic": True}},
        "palette_period": {"type": "number", "minimum": 0.5, "maximum": 200.0, "default": 6.0,
                           "x-bindable": True,
                           "x-ui": {"label": "Palette period", "step": 0.5, "unit": "iterations",
                                    "widget": "slider"}},
        "palette_drift": {"type": "number", "minimum": -2.0, "maximum": 2.0, "default": 0.02,
                          "x-bindable": True,
                          "x-ui": {"label": "Palette drift", "step": 0.005, "unit": "cycles/s",
                                   "widget": "slider"}},
        "palette_shift": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0, "x-bindable": True,
                          "x-ui": {"label": "Colour-cycle offset", "step": 0.01, "unit": "cycles",
                                   "widget": "slider"}},
        "exposure": {"type": "number", "minimum": 0.0, "maximum": 3.0, "default": 1.0, "x-bindable": True,
                     "x-ui": {"label": "Exposure", "step": 0.01, "unit": "×", "widget": "slider"}},
        "aa": {"type": "integer", "enum": [1, 2], "minimum": 1, "maximum": 2, "default": 1, "x-ease": False,
               "x-ui": {"label": "Supersampling", "widget": "select"}},
    }

    def __init__(self) -> None:
        super().__init__()
        self.kernel = None
        self.phase = 0.0          # zoom phase in periods of the self-similarity
        self.theta = 0.0          # extra rotation (rad)
        self.drift = 0.0          # palette phase (cycles)
        self.c: Optional[complex] = None
        self.palette: Optional[np.ndarray] = None   # (4, 3) eased palette coefficients
        self.geometry: Optional[Geometry] = None
        self._coef_dev = None                       # device float2[K]
        self._coef_key: Optional[tuple] = None
        self._p: Optional[dict] = None              # this frame's resolved params (bindings applied)

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        from ...gpu import raw_module

        mod = raw_module(_KERNEL, options=("--std=c++14", "--use_fast_math"))
        self.kernel = mod.get_function("fractal_zoom")
        p = self.easer.values()
        self.c = self._target_c(p)
        sp = self.session_palette()
        self.palette = np.array(PALETTES[p["palette"]] if sp is None else sp, dtype=np.float64)
        w, h = ctx.width, ctx.height
        self.block = (16, 16, 1)
        self.grid = ((w + 15) // 16, (h + 15) // 16, 1)
        # |w| bound over the screen (plus half a pixel for supersampling offsets)
        self.w_max = math.hypot(w / h, 1.0) * (1 + 2.0 / h)
        self._coef_dev = ctx.cp.zeros(2 * SERIES_TERMS, dtype=ctx.cp.float32)
        self._update_geometry(p)
        # warm-up: load the module on this device now, not on the render thread's first frame
        tmp = ctx.new_frame()
        self.draw(tmp, u=0.0)
        ctx.cp.cuda.get_current_stream().synchronize()

    @staticmethod
    def _target_c(p: dict) -> complex:
        if p["preset"] in C_PRESETS:
            return C_PRESETS[p["preset"]][0]
        return complex(p["c_re"], p["c_im"])

    @staticmethod
    def _point(p: dict) -> str:
        if p["point"] != "auto":
            return p["point"]
        return C_PRESETS.get(p["preset"], (None, "alpha"))[1]

    def _update_geometry(self, p: dict) -> None:
        self.geometry = fixed_point_geometry(self.c, self._point(p), p["view_radius"], self.w_max)
        key = (self.geometry.zstar, self.geometry.lam)
        if key != self._coef_key:  # only when c / the point changed: a 96-byte upload on the render stream
            host = np.array([[a.real, a.imag] for a in self.geometry.coef], dtype=np.float32).reshape(-1)
            self._coef_dev.set(host)
            self._coef_key = key

    # -- per frame -----------------------------------------------------------
    def advance(self, dt: float) -> dict:
        """Integrate parameter easing and the zoom/rotation/palette phases by dt; return current params."""
        p = self.step_params(dt)
        a = 1.0 - math.exp(-dt / self.ease_tau_s) if dt > 0 else 0.0
        tc = self._target_c(p)
        self.c = tc if abs(tc - self.c) < 1e-9 else self.c + (tc - self.c) * a
        sp = self.session_palette()
        tp = np.array(PALETTES[p["palette"]] if sp is None else sp, dtype=np.float64)
        self.palette = tp if np.allclose(tp, self.palette, atol=1e-6) else self.palette + (tp - self.palette) * a
        self._update_geometry(p)
        du = dt * p["zoom_rate"] / self.geometry.log_lam.real
        self.phase += du
        # S carries lam^-u, i.e. a rotation of -arg(lam) per period (88 deg/s for dendrite at 0.25 e-folds/s,
        # ~290 deg/s for rabbit).  Cancel (1 - natural_spin) of it.  Any continuous theta keeps the wrap
        # seamless: only u jumps there.
        self.theta += (1.0 - p["natural_spin"]) * self.geometry.log_lam.imag * du + dt * math.radians(p["rotation"])
        self.drift += dt * p["palette_drift"]
        self._p = p
        return p

    def render(self, t_local: float, dt: float, out) -> None:
        self.advance(dt)
        self.draw(out, u=self.phase - math.floor(self.phase))

    def draw(self, out, *, u: float, theta: Optional[float] = None, drift: Optional[float] = None) -> None:
        """Draw the current parameters at zoom phase u in [0, 1) (exposed for the seamlessness check)."""
        p = self._p if self._p is not None else self.easer.values()
        geo = self.geometry
        theta = self.theta if theta is None else theta
        drift = self.drift if drift is None else drift
        D, m = frame_constants(geo, p["view_radius"], u, theta, self.w_max)
        iters = p["iterations"]
        pal = [np.float32(v) for v in self.palette.reshape(-1)]  # a, b, c, d rows (r, g, b each)
        self.kernel(self.grid, self.block, (
            out, np.int32(self.ctx.width), np.int32(self.ctx.height),
            np.float32(D.real), np.float32(D.imag),
            self._coef_dev, np.int32(SERIES_TERMS),
            np.float32(geo.zstar.real), np.float32(geo.zstar.imag),
            np.float32(geo.lam.real), np.float32(geo.lam.imag),
            np.int32(m), np.int32(geo.n + iters + 2),
            np.float32(geo.n + u),
            np.float32(iters), np.float32(1.0 / FADE_ITERS),
            np.float32(1.0 / p["palette_period"]),
            np.float32((drift + p["palette_shift"]) - math.floor(drift + p["palette_shift"])),
            *pal,
            np.int32(p["aa"]), np.float32(p["exposure"]),
        ))

    def close(self) -> None:
        self.kernel = None
        self._coef_dev = None
