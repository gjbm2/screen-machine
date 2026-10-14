"""Palettes shared by the indexed sources (fractal_zoom, warp_stack) and the session-level palette axis.

Each palette is an Inigo Quilez cosine palette ``a + b cos(2 pi (c t + d))`` with sRGB-encoded rows a, b, c, d.
A session's ``palette`` (proposal LIVE_V1 §1.11, minimal slice) overrides the ``palette`` parameter of every
source that has one, so both sides of a scene change share it; the sources ease their coefficients, so a
palette change blends instead of switching.  The catalog's ``palettes`` block carries swatches and tags.

Not yet: palettes.yaml (hot reload), stop palettes, follow/drift modes and the shared palette pass.
"""
from __future__ import annotations

import math

Row = tuple[float, float, float]

PALETTES: dict[str, dict] = {
    "ember": {"label": "Ember",
        "cosine": ((0.50, 0.28, 0.12), (0.50, 0.30, 0.14), (1.00, 1.00, 1.00), (0.00, 0.06, 0.14)),
        "tags": {"family": "warm", "moods": ["mellow", "warm"]}},
    "gold": {"label": "Gold",
        "cosine": ((0.60, 0.45, 0.22), (0.40, 0.35, 0.22), (1.00, 1.00, 1.00), (0.00, 0.04, 0.12)),
        "tags": {"family": "warm", "moods": ["warm", "euphoric"]}},
    "dusk": {"label": "Dusk",
        "cosine": ((0.50, 0.30, 0.45), (0.45, 0.30, 0.35), (1.00, 0.80, 0.70), (0.00, 0.15, 0.30)),
        "tags": {"family": "warm", "moods": ["dreamy", "mellow"]}},
    "ice": {"label": "Ice",
        "cosine": ((0.38, 0.55, 0.72), (0.36, 0.38, 0.28), (1.00, 1.00, 1.00), (0.52, 0.50, 0.47)),
        "tags": {"family": "cool", "moods": ["calm", "cool"]}},
    "ocean": {"label": "Ocean",
        "cosine": ((0.20, 0.42, 0.58), (0.20, 0.32, 0.38), (1.00, 1.00, 1.00), (0.05, 0.12, 0.20)),
        "tags": {"family": "cool", "moods": ["calm", "dreamy"]}},
    "aurora": {"label": "Aurora",
        "cosine": ((0.30, 0.55, 0.45), (0.30, 0.40, 0.35), (1.00, 1.00, 1.00), (0.55, 0.00, 0.25)),
        "tags": {"family": "cool", "moods": ["dreamy", "hypnotic"]}},
    "neon": {"label": "Neon",
        "cosine": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (2.00, 1.00, 0.00), (0.50, 0.20, 0.25)),
        "tags": {"family": "vivid", "moods": ["bright", "euphoric"]}},
    "rainbow": {"label": "Rainbow",
        "cosine": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (1.00, 1.00, 1.00), (0.00, 0.33, 0.67)),
        "tags": {"family": "vivid", "moods": ["playful", "euphoric"]}},
    "acid": {"label": "Acid",
        "cosine": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (1.00, 1.00, 0.50), (0.80, 0.90, 0.30)),
        "tags": {"family": "vivid", "moods": ["tense", "playful"]}},
    "mono": {"label": "Mono",
        "cosine": ((0.50, 0.50, 0.50), (0.50, 0.50, 0.50), (1.00, 1.00, 1.00), (0.00, 0.00, 0.00)),
        "tags": {"family": "neutral", "moods": ["dark", "calm"]}},
}

NAMES = list(PALETTES)
FAMILIES = sorted({p["tags"]["family"] for p in PALETTES.values()})


def cosine(name: str) -> tuple[Row, Row, Row, Row]:
    return PALETTES[name]["cosine"]


def colour(name: str, t: float) -> Row:
    a, b, c, d = cosine(name)
    return tuple(min(1.0, max(0.0, a[i] + b[i] * math.cos(2 * math.pi * (c[i] * t + d[i])))) for i in range(3))


def swatch(name: str, n: int = 5) -> list[str]:
    """``n`` '#rrggbb' samples across one palette cycle (for the panel)."""
    return ["#%02x%02x%02x" % tuple(round(255 * v) for v in colour(name, i / n + 0.1)) for i in range(n)]


def catalog() -> dict:
    out = {}
    for name, p in PALETTES.items():
        a, b, c, d = p["cosine"]
        out[name] = {"label": p["label"], "kind": "cosine",
                     "cosine": {"a": list(a), "b": list(b), "c": list(c), "d": list(d)},
                     "swatch": swatch(name), "tags": p["tags"]}
    return out
