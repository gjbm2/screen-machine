"""Shared cosine palettes (Inigo Quilez form ``a + b cos(2 pi (c t + d))``, sRGB-encoded, MIT-style common
knowledge) and the CUDA helper every palette-indexed scene uses, so a later shared palette pass can take over.

A scene computes a scalar index ``t`` per pixel (its "field") and colours it with ``pal(t + drift, P)`` where
``P`` is the 12 floats from :func:`palette_coeffs`.  Expose ``palette``, ``palette_period`` and ``drift_speed``
via :func:`stream_service.plugins._lib.params.palette_params`.
"""
from __future__ import annotations

PALETTES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "ember": ((0.50, 0.28, 0.12), (0.50, 0.30, 0.14), (1.00, 1.00, 1.00), (0.00, 0.06, 0.14)),
    "ice": ((0.38, 0.55, 0.72), (0.36, 0.38, 0.28), (1.00, 1.00, 1.00), (0.52, 0.50, 0.47)),
    "neon": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (2.00, 1.00, 0.00), (0.50, 0.20, 0.25)),
    "mono": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (1.00, 1.00, 1.00), (0.00, 0.00, 0.00)),
    "rainbow": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (1.00, 1.00, 1.00), (0.00, 0.33, 0.67)),
    "dusk": ((0.50, 0.30, 0.45), (0.45, 0.30, 0.35), (1.00, 0.80, 0.70), (0.00, 0.15, 0.30)),
    "acid": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (1.00, 1.00, 0.50), (0.80, 0.90, 0.30)),
    "ocean": ((0.20, 0.40, 0.55), (0.20, 0.30, 0.35), (1.00, 1.00, 1.00), (0.60, 0.55, 0.50)),
    "forest": ((0.30, 0.45, 0.25), (0.25, 0.30, 0.20), (1.00, 1.00, 1.00), (0.10, 0.20, 0.30)),
    "candy": ((0.80, 0.50, 0.70), (0.20, 0.40, 0.30), (2.00, 1.00, 1.00), (0.00, 0.25, 0.25)),
}
FAMILIES = {"warm": ["ember", "dusk"], "cool": ["ice", "ocean"], "vivid": ["neon", "rainbow", "acid", "candy"],
            "natural": ["forest", "ocean"], "mono": ["mono"]}


def palette_coeffs(name: str) -> list[float]:
    a, b, c, d = PALETTES[name]
    return [*a, *b, *c, *d]


# float3 pal(t, P): P = 12 floats a.xyz b.xyz c.xyz d.xyz -> sRGB 0..1 (clamped)
PALETTE_CUDA = r"""
__device__ __forceinline__ float3 pal(float t, const float* __restrict__ P) {
    float3 c;
    c.x = fminf(fmaxf(P[0] + P[3] * cosf(6.2831853f * (P[6] * t + P[9])), 0.f), 1.f);
    c.y = fminf(fmaxf(P[1] + P[4] * cosf(6.2831853f * (P[7] * t + P[10])), 0.f), 1.f);
    c.z = fminf(fmaxf(P[2] + P[5] * cosf(6.2831853f * (P[8] * t + P[11])), 0.f), 1.f);
    return c;
}
"""
