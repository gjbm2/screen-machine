"""Plug-in base classes: Source and Transition (LIVE_V1 §1.5).

A plug-in declares a ``name``, ``label``, ``description`` and a ``params`` mapping of JSON-Schema property
definitions (each with a ``default`` and optional ``x-ui`` hints).  The registry turns that into the catalog
schema the web app builds forms from, and :func:`validate_params` enforces it:

* unknown parameters are rejected (``ParamError("unknown_param")`` -> HTTP 400);
* wrong types / enum values are rejected (``ParamError("bad_param")``);
* out-of-range numbers are clamped to ``minimum``/``maximum``.

Live parameter updates on a running :class:`Source` ease numeric values towards their new targets with a
~0.8 s time constant (:class:`ParamEaser`); everything else switches at the next frame.

A numeric property may declare ``x-bindable: true`` (or, as lab plug-ins do, the kind of binding it expects:
``"level"``, ``"rate"`` or ``"trigger"``): it can then take a binding to a show signal instead of a value
(modulation.py).  A ``palette`` property marked ``x-palette: true`` lets the session palette replace it.
The session attaches a ``modulation`` to each scene source (never to the internal ``image`` source):
``step_params`` resolves its bindings each frame, ``session_palette`` hands over the session palette, and the
layer tree runs the source on the session's scene clock.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, ClassVar, Iterable, Optional

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

EASINGS = ("linear", "ease-in-out", "ease-in", "ease-out")
# Effect stages in render order (EFFECTS_LAYER_DESIGN A.1): a chain applies warps first and overlays last.
STAGES = ("warp", "feedback", "glitch", "stylise", "colour", "light", "projection", "motion", "overlay")
BINDABLE_KINDS = (True, "level", "rate", "trigger")   # x-bindable: true (core) or a kind string (lab)
LICENSE_CLASSES = ("permissive", "attribution", "noncommercial", "unknown")


class ParamError(ValueError):
    """A request named an unknown parameter or gave one a value of the wrong type."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def is_color_prop(prop: dict) -> bool:
    return prop.get("format") == "color" or (prop.get("x-ui") or {}).get("widget") == "color"


def color_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def coerce_value(name: str, prop: dict, value: Any) -> Any:
    """Validate one value against its schema property; clamp numbers.  Raises ParamError."""
    kind = prop.get("type")
    if kind in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParamError("bad_param", f"{name} must be a number, not {value!r}")
        x = float(value)
        if not math.isfinite(x):
            raise ParamError("bad_param", f"{name} must be finite")
        lo, hi = prop.get("minimum"), prop.get("maximum")
        if lo is not None and x < lo:
            x = float(lo)
        if hi is not None and x > hi:
            x = float(hi)
        if kind == "integer":
            x = int(round(x))
        if "enum" in prop and x not in prop["enum"]:
            raise ParamError("bad_param", f"{name} must be one of {prop['enum']}, not {value!r}")
        return x
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ParamError("bad_param", f"{name} must be true or false, not {value!r}")
        return value
    if kind == "string":
        if is_color_prop(prop):
            # accept "#RRGGBB" (the widget's form) and [r, g, b] (handy from scripts); store "#rrggbb"
            if isinstance(value, (list, tuple)) and len(value) == 3 and all(
                    isinstance(c, (int, float)) and not isinstance(c, bool) for c in value):
                r, g, b = (max(0, min(255, int(round(float(c))))) for c in value)
                return f"#{r:02x}{g:02x}{b:02x}"
            if not isinstance(value, str) or not _COLOR_RE.match(value):
                raise ParamError("bad_param", f"{name} must be a colour '#RRGGBB', not {value!r}")
            return value.lower()
        if not isinstance(value, str):
            raise ParamError("bad_param", f"{name} must be a string, not {value!r}")
        if "enum" in prop and value not in prop["enum"]:
            raise ParamError("bad_param", f"{name} must be one of {prop['enum']}, not {value!r}")
        return value
    raise ParamError("bad_param", f"{name}: unsupported schema type {kind!r}")


def schema_defaults(props: dict[str, dict]) -> dict[str, Any]:
    return {k: copy.deepcopy(p["default"]) for k, p in props.items()}


def validate_params(props: dict[str, dict], params: Optional[dict], base: Optional[dict] = None) -> dict:
    """Return ``base`` (the defaults when None) updated with ``params``, validated and clamped."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ParamError("bad_param", "params must be an object")
    out = schema_defaults(props) if base is None else dict(base)
    for key, value in params.items():
        prop = props.get(key)
        if prop is None:
            raise ParamError("unknown_param", f"unknown parameter {key!r} (known: {sorted(props)})")
        out[key] = coerce_value(key, prop, value)
    return out


def check_props(owner: str, props: dict[str, dict]) -> None:
    """Registration-time sanity check of a plug-in's declared params (programming errors, not user input)."""
    for key, prop in props.items():
        if "default" not in prop:
            raise TypeError(f"{owner}.{key}: every parameter needs a default")
        if prop.get("type") not in ("number", "integer", "boolean", "string"):
            raise TypeError(f"{owner}.{key}: unsupported type {prop.get('type')!r}")
        coerce_value(f"{owner}.{key}", prop, prop["default"])  # the default itself must validate


# ---------------------------------------------------------------------------
# Plug-in classes
# ---------------------------------------------------------------------------

def is_bindable(prop: dict) -> bool:
    v = prop.get("x-bindable")
    return any(v is k or (isinstance(v, str) and v == k) for k in BINDABLE_KINDS) and \
        prop.get("type") in ("number", "integer")


class Plugin:
    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    params: ClassVar[dict[str, dict]] = {}
    # filled by the registry / lab loader (EFFECTS_LAYER_DESIGN A.1)
    tags: ClassVar[dict] = {}                 # plug-in level default tags (family, dims, technique, ...)
    origin: ClassVar[str] = "core"            # "core" | "lab:<root>"
    license_class: ClassVar[str] = "permissive"
    default_bindings: ClassVar[dict] = {}     # engine-form bindings used when a spec names the bare plug-in

    @classmethod
    def meta(cls) -> dict:
        return {"origin": cls.origin, "tags": copy.deepcopy(dict(cls.tags or {})),
                "license_class": cls.license_class}

    @classmethod
    def param_props(cls) -> dict[str, dict]:
        """All parameter properties (subclasses may add common ones)."""
        return cls.params

    @classmethod
    def schema(cls) -> dict:
        return {"type": "object", "additionalProperties": False,
                "properties": copy.deepcopy(cls.param_props())}

    @classmethod
    def defaults(cls) -> dict:
        return schema_defaults(cls.param_props())

    @classmethod
    def validate(cls, params: Optional[dict], base: Optional[dict] = None) -> dict:
        return validate_params(cls.param_props(), params, base)


class ParamEaser:
    """Eases numeric parameters towards their targets: x += (target - x) * (1 - exp(-dt / tau)).

    Non-numeric values (enums, booleans, colours) and properties marked ``"x-ease": false`` switch at once.
    Integers are eased as floats and rounded when read."""

    def __init__(self, props: dict[str, dict], values: dict, tau_s: float = 0.8):
        self.props = props
        self.tau = tau_s
        self.target = dict(values)
        self.current: dict[str, Any] = dict(values)

    def easable(self, key: str) -> bool:
        p = self.props.get(key, {})
        return p.get("type") in ("number", "integer") and p.get("x-ease", True)

    def set_targets(self, values: dict) -> None:
        for k, v in values.items():
            self.target[k] = v
            if not self.easable(k):
                self.current[k] = v

    def step(self, dt: float) -> None:
        if dt <= 0:
            return
        a = 1.0 - math.exp(-dt / self.tau)
        for k, tgt in self.target.items():
            if not self.easable(k):
                continue
            cur = float(self.current[k])
            if cur == tgt:
                continue
            nxt = cur + (float(tgt) - cur) * a
            if abs(nxt - tgt) <= 1e-6 * max(1.0, abs(tgt)):
                nxt = float(tgt)
            self.current[k] = nxt

    def values(self) -> dict[str, Any]:
        out = {}
        for k, v in self.current.items():
            if self.props.get(k, {}).get("type") == "integer":
                out[k] = int(round(v))
            else:
                out[k] = v
        return out

    @property
    def settled(self) -> bool:
        return all(self.current[k] == self.target[k] for k in self.target)


class Source(Plugin):
    """A frame generator.  Lifecycle: ``setup(ctx, params)`` (worker thread), then ``update`` / ``render`` on
    the session's render thread, then ``close()``.

    ``render(t_local, dt, out)`` draws into ``out``: a CuPy uint8 array of shape (H, W, 4) holding
    sRGB-encoded RGBA (alpha is ignored).  ``t_local`` is stream time since the source was put on screen;
    ``dt`` is the stream time since this source last rendered (1/fps normally, 0 on its first frame, a
    multiple of 1/fps after a skipped frame), so integrating state with ``dt`` stays on the timeline."""

    internal: ClassVar[bool] = False
    ease_tau_s: ClassVar[float] = 0.8

    listens: ClassVar[tuple] = ()      # what it reads from self.sound: "notes", "spectrum", "chroma" (sound.py)

    def __init__(self) -> None:
        self.ctx = None
        self.easer: Optional[ParamEaser] = None
        self.modulation = None   # modulation.Modulation, set by the session for scene sources (None: plain)
        self.sound = None        # sound.Sound: the piano and room mic (classes that declare ``listens``)

    # -- lifecycle ----------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        """``params`` is fully validated (defaults filled in).  Subclasses call super().setup() first."""
        self.ctx = ctx
        self.easer = ParamEaser(self.param_props(), params, self.ease_tau_s)

    def update(self, params: dict) -> None:
        """New full parameter set (validated).  Numeric values are eased, the rest switch now."""
        if self.easer is not None:
            self.easer.set_targets(params)

    def render(self, t_local: float, dt: float, out) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # -- helpers for subclasses ------------------------------------------------
    def step_params(self, dt: float) -> dict:
        """Advance parameter easing by ``dt`` and return the current values: eased, then with bindings resolved
        when the source has a modulation (bound values bypass the easer)."""
        assert self.easer is not None, "setup() not called"
        self.easer.step(dt)
        values = self.easer.values()
        if self.modulation is not None:
            values = self.modulation.apply(values)
        return values

    def session_palette(self):
        """The session palette's current cosine rows (4 x 3 array), or None to use the source's own."""
        return None if self.modulation is None else self.modulation.palette_rows()

    @property
    def static(self) -> bool:
        """True if successive frames are identical while parameters are settled (a still)."""
        return False

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "label": cls.label or cls.name, "description": cls.description,
                "internal": cls.internal, "schema": cls.schema(), **cls.meta()}


class Transition(Plugin):
    """Blends two frames.  ``render(progress_eased, a, b, out)`` with progress in [0, 1] already eased.

    ``duration_s`` and ``easing`` are common to every transition and are added to the schema here."""

    default_duration_s: ClassVar[float] = 1.0
    max_duration_s: ClassVar[float] = 60.0
    # True if progress 0 shows exactly `a` and progress 1 exactly `b`: the layer tree then skips rendering
    # the unused side at the ends.
    pure_endpoints: ClassVar[bool] = True

    @classmethod
    def param_props(cls) -> dict[str, dict]:
        common = {
            "duration_s": {"type": "number", "minimum": 0.0, "maximum": cls.max_duration_s,
                           "default": cls.default_duration_s,
                           "x-ui": {"label": "Duration", "step": 0.1, "unit": "s", "widget": "slider"}},
            "easing": {"type": "string", "enum": list(EASINGS), "default": "ease-in-out",
                       "x-ui": {"label": "Easing", "widget": "select"}},
        }
        return {**common, **cls.params}

    def __init__(self, params: dict) -> None:
        self.params = params
        self.ctx = None

    def setup(self, ctx) -> None:
        self.ctx = ctx

    @property
    def duration_s(self) -> float:
        return float(self.params.get("duration_s", self.default_duration_s))

    @property
    def easing(self) -> str:
        return str(self.params.get("easing", "linear"))

    def needs(self, progress: float) -> tuple[bool, bool]:
        """Which inputs the frame at this (linear) progress needs: (a, b).  progress 1 never gets here."""
        if self.pure_endpoints and progress <= 0.0:
            return True, False
        return True, True

    def render(self, progress: float, a, b, out) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @classmethod
    def describe(cls) -> dict:
        return {"name": cls.name, "label": cls.label or cls.name, "description": cls.description,
                "schema": cls.schema(), **cls.meta()}


def effect_params(amount: float = 0.5) -> dict:
    """The standard effect macros: ``amount`` (0 = identity, a level) and ``pulse`` (a beat trigger)."""
    return {
        "amount": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": amount, "x-bindable": "level",
                   "x-tier": "realtime", "x-ui": {"label": "Amount", "step": 0.01, "widget": "slider", "basic": True}},
        "pulse": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0, "x-bindable": "trigger",
                  "x-tier": "realtime", "x-ui": {"label": "Pulse (beat envelope)", "step": 0.01, "widget": "slider"}},
    }


class Effect(Plugin):
    """A post-process: ``render(t_local, dt, src, out)``; ``src`` and ``out`` are (H, W, 4) uint8 sRGB frames at the
    output size, never the same buffer.  ``amount = 0`` must give ``out == src`` within 1/255, and 0 -> 1 must be a
    smooth morph (the engine fades effects in and out through ``amount``).  ``stage`` sets the order in a chain
    (:data:`STAGES`).  Same interface as the lab's ``smfx._lib.effect_base.Effect``; the engine's EffectSlot wraps
    ``step_params`` to apply bindings and the fade level, so ``render`` just calls ``self.step_params(dt)``."""

    stage: ClassVar[str] = "stylise"
    ease_tau_s: ClassVar[float] = 0.3
    internal: ClassVar[bool] = False
    default_bindings: ClassVar[dict] = {"pulse": {"bind": "beat", "range": [0.0, 1.0], "rest": 0.0}}
    listens: ClassVar[tuple] = ()      # what it reads from self.sound: "notes", "spectrum", "chroma" (sound.py)

    def __init__(self) -> None:
        self.ctx = None
        self.easer: Optional[ParamEaser] = None
        self.sound = None

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
                "stage": cls.stage, "internal": cls.internal, "schema": cls.schema(), **cls.meta()}


def plugin_kind(cls: Any) -> Optional[str]:
    """'source' | 'transition' | 'effect' | None.  An effect is an engine :class:`Effect` subclass, or any named
    :class:`Plugin` subclass (not a Source / Transition) with a ``render`` whose module path contains
    ``.effects.`` (the lab's ``smfx`` Effect and imported packages that follow the same interface)."""
    if not isinstance(cls, type) or not getattr(cls, "name", ""):
        return None
    if issubclass(cls, Source):
        return None if cls is Source else "source"
    if issubclass(cls, Transition):
        return None if cls is Transition else "transition"
    if issubclass(cls, Effect):
        return None if cls is Effect else "effect"
    # plug-ins subclass the toolkit's Effect (plugins/_lib/effect_base.py): same interface, counts as an Effect
    if any(c.__name__ == "Effect" and c.__module__.endswith("effect_base") for c in cls.__mro__):
        return None if cls.__module__.endswith("effect_base") else "effect"
    if issubclass(cls, Plugin) and callable(getattr(cls, "render", None)) and ".effects." in f".{cls.__module__}.":
        return "effect"
    return None


def iter_plugin_classes(namespace: Iterable[Any], module_name: str) -> Iterable[type]:
    """Plug-in classes (sources, transitions, effects) defined (not merely imported) in a module."""
    for obj in namespace:
        if isinstance(obj, type) and getattr(obj, "__module__", None) == module_name and plugin_kind(obj):
            yield obj
