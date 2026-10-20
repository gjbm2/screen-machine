"""Colour helpers: sRGB <-> CIE xy for Hue (r7 §14: sRGB gamma, the Wide RGB D65 matrix, gamut clamp), and a display
colour for a light's state so the Room page can show what each lamp is emitting."""

from __future__ import annotations

import math

GAMUT_C = ((0.692, 0.308), (0.17, 0.7), (0.153, 0.048))


def _expand(c: float) -> float:
    return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92


def _compress(c: float) -> float:
    c = max(0.0, c)
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _closest_on_segment(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return ax + t * dx, ay + t * dy


def _in_gamut(p, g) -> bool:
    (rx, ry), (gx, gy), (bx, by) = g

    def side(ax, ay, bx_, by_):
        return (p[0] - bx_) * (ay - by_) - (ax - bx_) * (p[1] - by_)
    d1, d2, d3 = side(rx, ry, gx, gy), side(gx, gy, bx, by), side(bx, by, rx, ry)
    neg, pos = d1 < 0 or d2 < 0 or d3 < 0, d1 > 0 or d2 > 0 or d3 > 0
    return not (neg and pos)


def clamp_gamut(x: float, y: float, gamut=GAMUT_C) -> tuple[float, float]:
    if _in_gamut((x, y), gamut):
        return x, y
    r, g, b = gamut
    cands = [_closest_on_segment((x, y), r, g), _closest_on_segment((x, y), g, b), _closest_on_segment((x, y), b, r)]
    return min(cands, key=lambda q: (q[0] - x) ** 2 + (q[1] - y) ** 2)


def rgb_to_xy(r: float, g: float, b: float, gamut=GAMUT_C) -> tuple[float, float]:
    """sRGB 0-1 -> gamut-clamped xy (rounded to 4 dp). Black maps to the white point."""
    r, g, b = _expand(r), _expand(g), _expand(b)
    X = 0.664511 * r + 0.154324 * g + 0.162028 * b
    Y = 0.283881 * r + 0.668433 * g + 0.047685 * b
    Z = 0.000088 * r + 0.072310 * g + 0.986039 * b
    s = X + Y + Z
    if s <= 0:
        return 0.3227, 0.329
    x, y = clamp_gamut(X / s, Y / s, gamut)
    return round(x, 4), round(y, 4)


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError(f"colour {h!r}: #rrggbb")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))      # type: ignore[return-value]


def rgb_to_hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in (r, g, b))


def xy_to_rgb(x: float, y: float) -> tuple[float, float, float]:
    """xy at full brightness -> sRGB 0-1 (normalised so the largest channel is 1)."""
    if y <= 0:
        return 1.0, 1.0, 1.0
    Y = 1.0
    X, Z = x / y * Y, (1 - x - y) / y * Y
    r = X * 1.656492 - Y * 0.354851 - Z * 0.255038
    g = -X * 0.707196 + Y * 1.655397 + Z * 0.036152
    b = X * 0.051713 - Y * 0.121364 + Z * 1.011530
    m = max(r, g, b, 1e-9)
    return tuple(_compress(c / m) for c in (r, g, b))           # type: ignore[return-value]


def mirek_to_rgb(mirek: int) -> tuple[float, float, float]:
    """Colour temperature -> an approximate sRGB (Tanner Helland's blackbody fit)."""
    t = 1e6 / max(1, mirek) / 100
    r = 255 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255 if t >= 66 else (0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    return tuple(max(0.0, min(255.0, c)) / 255 for c in (r, g, b))  # type: ignore[return-value]


def display_hex(value) -> str | None:
    """A swatch colour for a hue target value ({on, bri, xy|mirek} or 'off'); None when off/unknown."""
    if not isinstance(value, dict) or not value.get("on"):
        return None
    if "xy" in value:
        return rgb_to_hex(*xy_to_rgb(*value["xy"]))
    if "mirek" in value:
        return rgb_to_hex(*mirek_to_rgb(value["mirek"]))
    return "#fff4e0"


def _cct(x: float, y: float, max_dist: float = 0.02) -> float | None:
    """Colour temperature (K) when xy lies within max_dist of the Planckian locus, else None (a colour).
    McCamy's CCT estimate, checked against Kim et al.'s cubic fit of the locus (1667-25000 K)."""
    if abs(y - 0.1858) < 1e-6:
        return None
    n = (x - 0.3320) / (0.1858 - y)
    t = 449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33
    if not 1667 <= t <= 25000:
        return None
    xc = (-0.2661239e9 / t ** 3 - 0.2343589e6 / t ** 2 + 0.8776956e3 / t + 0.179910 if t <= 4000
          else -3.0258469e9 / t ** 3 + 2.1070379e6 / t ** 2 + 0.2226347e3 / t + 0.240390)
    if t <= 2222:
        yc = -1.1063814 * xc ** 3 - 1.34811020 * xc ** 2 + 2.18555832 * xc - 0.20219683
    elif t <= 4000:
        yc = -0.9549476 * xc ** 3 - 1.37418593 * xc ** 2 + 2.09137015 * xc - 0.16748867
    else:
        yc = 3.0817580 * xc ** 3 - 5.87338670 * xc ** 2 + 3.75112997 * xc - 0.37001483
    return t if ((x - xc) ** 2 + (y - yc) ** 2) ** 0.5 <= max_dist else None


def describe(value) -> str:
    """Plain words for a light state's colour: 'warm white', 'cool white', 'white', or a colour name ('blue')."""
    if not isinstance(value, dict):
        return ""
    if "mirek" in value:
        k = 1e6 / value["mirek"]
        return "warm white" if k < 3300 else "cool white" if k > 5000 else "white"
    if "xy" not in value:
        return ""
    x, y = value["xy"]
    cct = _cct(x, y)
    if cct is not None:                                  # near the white-light (Planckian) curve: a white
        return "warm white" if cct < 3300 else "cool white" if cct > 5000 else "white"
    r, g, b = xy_to_rgb(x, y)
    import colorsys
    h = colorsys.rgb_to_hsv(r, g, b)[0] * 360
    for limit, name in ((15, "red"), (45, "orange"), (70, "yellow"), (160, "green"), (200, "teal"),
                        (255, "blue"), (290, "purple"), (335, "pink"), (360, "red")):
        if h < limit:
            return name
    return "colour"
