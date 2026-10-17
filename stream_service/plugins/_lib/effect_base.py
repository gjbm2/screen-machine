"""Effect: a post-process plug-in kind (new; the engine will chain effects after a scene in a later step).

``render(t_local, dt, src, out)``: ``src`` and ``out`` are (H, W, 4) uint8 sRGB-encoded RGBA at the output size
(never the same buffer).  Parameters ease like a Source's (numeric values with ~0.3 s time constant).
Expose :func:`stream_service.plugins._lib.params.standard_effect_params` (amount, pulse) plus your own.
"""
from __future__ import annotations

from typing import Any, ClassVar, Optional

from stream_service.plugins.base import ParamEaser, Plugin


class Effect(Plugin):
    ease_tau_s: ClassVar[float] = 0.3
    beat_clock: Any = None     # the session's beat clock this frame (plugins/_lib/beat.py), set by the EffectSlot

    def __init__(self) -> None:
        self.ctx = None
        self.easer: Optional[ParamEaser] = None

    def setup(self, ctx, params: dict) -> None:
        self.ctx = ctx
        self.easer = ParamEaser(self.param_props(), params, self.ease_tau_s)

    def update(self, params: dict) -> None:
        if self.easer is not None:
            self.easer.set_targets(params)

    def step_params(self, dt: float) -> dict:
        assert self.easer is not None, "setup() not called"
        self.easer.step(dt)
        return self.easer.values()

    def render(self, t_local: float, dt: float, src, out) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "label": cls.label or cls.name, "description": cls.description,
                "schema": cls.schema()}
