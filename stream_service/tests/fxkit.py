"""Test-only effect plug-ins and an app registry / config built from them, so the effects-layer tests exercise
the engine and the API, never a particular plug-in file (owner ruling: test the system, not plug-ins)."""
from __future__ import annotations

import pathlib
import shutil

from stream_service.plugins import Registry, default_registry
from stream_service.plugins.base import Effect, effect_params

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"


def _num(lo, hi, default, bind=None):
    d = {"type": "number", "minimum": lo, "maximum": hi, "default": default, "x-ui": {"label": "x"}}
    if bind:
        d["x-bindable"] = bind
    return d


class _Fake(Effect):
    """Records renders; output = input (the API tests never render)."""

    def render(self, t_local, dt, src, out):
        self.step_params(dt)
        out[...] = src


class Chroma(_Fake):
    name, label, stage = "chroma", "Chroma split", "glitch"
    params = {**effect_params(0.5), "spread": _num(0.0, 0.05, 0.008), "punch": _num(0.0, 4.0, 1.5)}


class Grade(_Fake):
    name, label, stage = "grade", "Colour grade", "colour"
    params = {**effect_params(1.0), "saturation": _num(0.0, 2.5, 1.2), "contrast": _num(0.5, 2.0, 1.1),
              "warmth": _num(-1.0, 1.0, 0.0), "lift": _num(0.0, 0.3, 0.0), "flash": _num(0.0, 1.0, 0.0)}


class Shake(_Fake):
    name, label, stage = "shake", "Shake", "motion"
    params = {**effect_params(0.6), "max_shift": _num(0.0, 0.06, 0.012), "max_rotate": _num(0.0, 6.0, 0.8),
              "sway": _num(0.0, 1.0, 0.2), "punch": _num(0.0, 3.0, 1.0), "frequency": _num(0.2, 12.0, 3.0)}


class HueDrift(_Fake):
    name, label, stage = "hue_drift", "Hue drift", "colour"
    params = {**effect_params(0.6), "rate": _num(-1.0, 1.0, 0.05, "rate"), "offset": _num(0.0, 1.0, 0.25),
              "saturation": _num(0.0, 2.0, 1.15), "punch": _num(0.0, 2.0, 0.5)}


class Flash(_Fake):
    name, label, stage = "flash", "Flash", "light"
    params = {**effect_params(0.6), "strength": _num(0.0, 1.0, 0.45), "glow": _num(0.0, 0.5, 0.0),
              "tint": {"type": "string", "format": "color", "default": "#ffffff"}, "screen": _num(0.0, 1.0, 0.8)}


class Passthrough(_Fake):
    name, label, stage, internal = "passthrough", "Passthrough", "overlay", True
    params = {**effect_params(1.0)}


TEST_EFFECTS = (Chroma, Grade, Shake, HueDrift, Flash, Passthrough)

EFFECTS_YAML = """
chroma-soft:
  label: Chroma (soft)
  effect: chroma
  params: {amount: 0.5, spread: 0.006, punch: 1.2}
  tags: {energy: [0.2, 0.8], moods: [dreamy, playful, bright], style: [gentle, lively], weight: 1.0}
chroma-punch:
  label: Chroma (beat punch)
  effect: chroma
  params: {amount: 0.8, spread: 0.012, punch: 3.0}
  tags: {energy: [0.55, 1.0], moods: [euphoric, tense, aggressive], style: [lively, wild], weight: 0.8}
shake-beat:
  label: Shake (on the beat)
  effect: shake
  params: {amount: 0.7, max_shift: 0.012, sway: 0.1, punch: 1.2}
  tags: {energy: [0.5, 1.0], moods: [euphoric, aggressive, tense, playful], style: [lively, wild], weight: 1.0}
hue-drift-slow:
  label: Hue drift (slow)
  effect: hue_drift
  params: {amount: 0.6, rate: 0.03, saturation: 1.1, punch: 0.2}
  tags: {energy: [0.0, 0.6], moods: [dreamy, hypnotic, calm, playful], style: [gentle], weight: 1.0}
grade-warm:
  label: Grade (warm)
  effect: grade
  params: {amount: 0.8, saturation: 1.15, contrast: 1.05, warmth: 0.55}
  tags: {energy: [0.0, 0.7], moods: [warm, mellow, dreamy], style: [gentle], weight: 1.0}
flash-kick:
  label: Flash (on the kick)
  effect: flash
  params: {amount: 0.7, strength: 0.5, screen: 0.85}
  tags: {energy: [0.55, 1.0], moods: [euphoric, aggressive, bright], style: [lively, wild], weight: 0.9}
"""


def fx_registry() -> Registry:
    """The real sources and transitions plus the test effects (no plug-in effect files)."""
    base = default_registry()
    r = Registry()
    r.sources, r.transitions = dict(base.sources), dict(base.transitions)
    for cls in TEST_EFFECTS:
        r.register(cls)
    return r


def fx_config(tmp: pathlib.Path) -> pathlib.Path:
    """config/live's scenes / transitions / vocab plus the test effects.yaml."""
    for f in ("scenes.yaml", "transitions.yaml", "vocab.yaml"):
        if (REPO_CONFIG / f).exists():
            shutil.copy(REPO_CONFIG / f, tmp / f)
    (tmp / "effects.yaml").write_text(EFFECTS_YAML)
    return tmp
