"""The light-sensor mask in live mode: the display page's art-mode mask, done in the renderer's final pass.

``PATCH /api/sessions/{sid} {"mask": {brightness, warm_hex, warm_alpha, transition_ms}}`` (also accepted on create;
``null`` clears it).  The page draws black at ``1 - brightness`` over the art, then ``warm_hex`` with multiply at
``warm_alpha``.  On sRGB-encoded values, which is what the browser composites, that is a per-channel gain

    gain_c = brightness * (1 - warm_alpha + warm_alpha * warm_c / 255)

folded into the RGBA -> NV12 conversion, so it costs nothing.  A new mask eases linearly from the current gains over
``transition_ms`` (room sends one about every 0.3 s while the light changes).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .plugins.base import ParamError

Gains = tuple[float, float, float]
IDENTITY: Gains = (1.0, 1.0, 1.0)
FIELDS = ("brightness", "warm_hex", "warm_alpha", "transition_ms")
TRANSITION_MS_DEFAULT = 200
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _unit(v: Any, name: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0:
        raise ParamError("bad_request", f"mask.{name} must be a number 0..1")
    return float(v)


def parse_mask(v: Any) -> tuple[Gains, float, Optional[dict]]:
    """A mask body -> (gains, transition_s, the normalised mask or None).  Raises ParamError (400)."""
    if v is None:
        return IDENTITY, TRANSITION_MS_DEFAULT / 1000.0, None
    if not isinstance(v, dict):
        raise ParamError("bad_request", "mask must be an object or null")
    unknown = set(v) - set(FIELDS)
    if unknown:
        raise ParamError("bad_request", f"unknown mask fields {sorted(unknown)} (known: {list(FIELDS)})")
    b = _unit(v.get("brightness", 1.0), "brightness")
    a = _unit(v.get("warm_alpha", 0.0), "warm_alpha")
    m = _HEX.match(str(v.get("warm_hex", "#ffffff")))
    if m is None:
        raise ParamError("bad_request", "mask.warm_hex must be a colour like #ffb070")
    ms = v.get("transition_ms", TRANSITION_MS_DEFAULT)
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or not 0 <= ms <= 60000:
        raise ParamError("bad_request", "mask.transition_ms must be a number 0..60000")
    hx = m.group(1).lower()
    warm = tuple(int(hx[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    gains = tuple(b * (1.0 - a + a * c) for c in warm)
    norm = {"brightness": b, "warm_hex": "#" + hx, "warm_alpha": a, "transition_ms": ms}
    return gains, float(ms) / 1000.0, norm   # type: ignore[return-value]


class MaskRamp:
    """The current gains, eased linearly to the newest target.  Set on the loop thread, read on the render thread:
    one tuple assignment each way, so no lock."""

    def __init__(self) -> None:
        self._seg: tuple[Gains, Gains, float, float] = (IDENTITY, IDENTITY, 0.0, 0.0)   # from, to, t0, duration

    def set(self, gains: Gains, duration_s: float, now: float) -> None:
        self._seg = (self.at(now), gains, now, max(0.0, duration_s))

    def at(self, now: float) -> Gains:
        a, b, t0, d = self._seg
        if d <= 0.0 or now >= t0 + d:
            return b
        p = max(0.0, (now - t0) / d)
        return (a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p, a[2] + (b[2] - a[2]) * p)
