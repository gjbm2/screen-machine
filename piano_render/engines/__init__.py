"""Render engines, by the "engine" name in instruments.json.

Built in: "sfizz" (SFZ via libsfizz) and "fluidsynth" (SF2 via pyfluidsynth). "windows-gs" is the live PC22 GS
synth and is never rendered here. A new engine (Pianoteq's CLI, a VST3 host such as pedalboard) is a subclass of
base.Engine in its own module, added to _BUILTIN below or registered at run time with register(name, cls), and
imported only when used, so a missing optional dependency never breaks the others. An instrument with "layers"
(a blend of several files, e.g. mic positions) gets a base.LayeredEngine from create().
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Union

from .base import Engine, EngineError, LayeredEngine

_BUILTIN: dict[str, str] = {
    "sfizz": "piano_render.engines.sfizz:SfizzEngine",
    "fluidsynth": "piano_render.engines.fluidsynth:FluidSynthEngine",
}
_REGISTERED: dict[str, Union[str, type]] = {}

NEVER_RENDERED = {"windows-gs"}


def register(name: str, cls: "type[Engine] | str") -> None:
    """Add (or replace) an engine: a class, or "module:Class" imported on first use."""
    _REGISTERED[name] = cls


def unregister(name: str) -> None:
    _REGISTERED.pop(name, None)


def names() -> list[str]:
    return sorted(set(_BUILTIN) | set(_REGISTERED))


def engine_class(name: str) -> "type[Engine]":
    if name in NEVER_RENDERED:
        raise EngineError(f"engine {name!r} is the live PC22 synth; piano_render never renders it")
    ref = _REGISTERED.get(name, _BUILTIN.get(name))
    if ref is None:
        raise EngineError(f"unknown engine {name!r} (known: {', '.join(names())})")
    if isinstance(ref, str):
        mod, _, attr = ref.partition(":")
        try:
            return getattr(importlib.import_module(mod), attr)
        except ImportError as e:
            raise EngineError(f"engine {name!r} cannot be imported: {e}") from e
    return ref


def available(name: str, engines_root: "str | Path") -> bool:
    try:
        return bool(engine_class(name).available(engines_root))
    except Exception:
        return False


def version(name: str, engines_root: "str | Path") -> str:
    try:
        return engine_class(name).version(engines_root)
    except Exception:
        return f"{name}:missing"


def create(inst: dict, engines_root: "str | Path") -> Engine:
    """The instrument's engine; for an instrument with "layers", a base.LayeredEngine over one engine of its kind
    per layer file (base.py's docstring)."""
    if inst.get("layers"):
        engine_class(inst.get("engine", ""))          # an unknown engine fails here, before any layer loads
        return LayeredEngine(inst, engines_root, create)
    return engine_class(inst.get("engine", ""))(inst, engines_root)


__all__ = ["Engine", "EngineError", "LayeredEngine", "register", "unregister", "names", "engine_class", "available",
           "version", "create"]
