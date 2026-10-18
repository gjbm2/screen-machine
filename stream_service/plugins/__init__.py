"""Plug-in registry: discovers Source, Transition and Effect classes by module, live.

Adding a scene is one file in ``plugins/sources/`` (a ``Source`` subclass with a ``name``) plus, optionally,
``<name>.presets.yaml`` next to it or a preset in ``config/live/scenes.yaml``.  Transitions live in
``plugins/transitions/`` and effects in ``plugins/effects/`` (an ``Effect`` subclass: the engine's
:class:`base.Effect` or the toolkit's ``plugins._lib.effect_base.Effect``).

:meth:`Registry.rescan` (run at start and every ~20 s by the server) imports modules that are new or whose file
changed, unregisters plug-ins whose file was removed, and records a module that fails to import (or whose classes
fail registration) in ``broken`` with the error; its previously registered classes, if any, stay usable.  A
rescan builds new tables and swaps them in, so readers on other threads always see a consistent snapshot.
The plug-in directories are watched (inotify): a rescan when no entry in them was written, touched, added, renamed
or removed since the last one is a no-op (~0.01 ms of GIL time against ~15 ms for a pass over ~850 modules).
"""
from __future__ import annotations

import ctypes
import importlib
import importlib.machinery
import logging
import os
import pathlib
import sys
import threading
import time
import traceback
import weakref
from typing import Optional

from .base import (STAGES, Effect, ParamError, Source, Transition, check_props, is_bindable, iter_plugin_classes,
                   plugin_kind)

log = logging.getLogger("stream_service.plugins")

PACKAGES = ("stream_service.plugins.sources", "stream_service.plugins.transitions",
            "stream_service.plugins.effects")
KINDS = ("source", "transition", "effect")
PULSE_BEAT = {"bind": "beat", "range": [0.0, 1.0], "rest": 0.0}
# IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF |
# IN_MOVE_SELF: any write, touch, add, rename or removal in a watched directory (reads and __pycache__/ are not)
_IN_MASK = 0x2 | 0x4 | 0x8 | 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x800
_SUFFIXES = tuple(sorted(importlib.machinery.all_suffixes(), key=lambda x: (-len(x), x)))   # inspect.getmodulename's


def _short_error(exc: BaseException) -> str:
    tb = traceback.extract_tb(exc.__traceback__)
    where = ""
    for fr in reversed(tb):
        if "/plugins/" in fr.filename:
            where = f" ({pathlib.Path(fr.filename).name}:{fr.lineno})"
            break
    if isinstance(exc, SyntaxError) and exc.lineno:
        where = f" ({pathlib.Path(exc.filename or '?').name}:{exc.lineno})"
    return f"{type(exc).__name__}: {exc}{where}"[:600]


def _modname(fn: str) -> Optional[str]:
    for suf in _SUFFIXES:
        if fn.endswith(suf):
            return fn[:-len(suf)]
    return None


def _iter_modules(paths: list) -> list[tuple[str, bool]]:
    """pkgutil.iter_modules over plain directories, sorted: [(name, ispkg)] (a package wins over a same-named
    module, the first path over later ones), without its per-name suffix sort and __pycache__/ listing."""
    found: dict[str, bool] = {}
    for d in paths:
        try:
            with os.scandir(d) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            fn = e.name
            mod = _modname(fn)
            if mod == "__init__" or mod in found:
                continue
            ispkg = False
            if not mod and "." not in fn and e.is_dir():
                if not any(os.path.exists(os.path.join(e.path, "__init__" + suf)) for suf in _SUFFIXES):
                    continue
                mod, ispkg = fn, True
            if mod and "." not in mod:
                found[mod] = ispkg
    return sorted(found.items())


class DirWatch:
    """inotify on the plug-in directories: :meth:`moved` says whether an entry in one was written, touched, added,
    renamed or removed since the last call.  Without inotify, or a directory it could not watch, it always says
    moved (every rescan is a full one)."""

    def __init__(self) -> None:
        self.fd, self.ok = -1, False
        try:
            self._libc = ctypes.CDLL(None, use_errno=True)
            self.fd = self._libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except Exception:                    # not Linux
            self.fd = -1
        if self.fd >= 0:
            weakref.finalize(self, os.close, self.fd)

    def add(self, d: str) -> bool:
        """Watch a directory (again: idempotent).  -> watched."""
        return self.fd >= 0 and self._libc.inotify_add_watch(self.fd, os.fsencode(d), _IN_MASK) >= 0

    def moved(self) -> bool:
        """Drain the events.  -> any since the last call (always True when not watching everything)."""
        got = not self.ok
        while self.fd >= 0:
            try:
                if not os.read(self.fd, 65536):
                    break
            except BlockingIOError:
                break
            except OSError:
                self.ok = False
                return True
            got = True
        return got


class Registry:
    def __init__(self) -> None:
        self.sources: dict[str, type[Source]] = {}
        self.transitions: dict[str, type[Transition]] = {}
        self.effects: dict[str, type] = {}
        self.modules: dict[str, dict] = {}   # module -> {kind, file, mtime, names: [(kind, name)]}
        self.broken: dict[str, dict] = {}    # module -> {kind, module, name, file, error, mtime}
        self.rev = 0                         # bumped on every change (presets / library rebuild on it)
        self.rescanned_at: Optional[float] = None
        self.last_scan: dict = {}
        self.scans = 0                       # full passes over the files (presets re-read theirs on one)
        self._watch: Optional[tuple[tuple, DirWatch]] = None     # (packages, their directories' watch)
        self._lock = threading.Lock()

    def table(self, kind: str) -> dict[str, type]:
        return {"source": self.sources, "transition": self.transitions, "effect": self.effects}[kind]

    # -- registration --------------------------------------------------------------
    @staticmethod
    def check(cls: type) -> str:
        """Validate a plug-in class (raises TypeError); fills effect defaults.  -> its kind."""
        kind = plugin_kind(cls)
        if kind is None:
            raise TypeError(f"{cls!r} is not a Source, Transition or Effect")
        if not cls.name:
            raise TypeError(f"{cls.__name__} has no name")
        check_props(f"{kind} {cls.name}", cls.param_props())
        if kind == "effect":
            if getattr(cls, "stage", None) not in STAGES:
                cls.stage = "stylise"
            cls.amount_mode = "native" if "amount" in cls.param_props() else "mix"
            if not getattr(cls, "default_bindings", None):
                pulse = cls.param_props().get("pulse")
                if pulse is not None and is_bindable(pulse):
                    cls.default_bindings = {"pulse": dict(PULSE_BEAT)}
        return kind

    def register(self, cls: type) -> None:
        """Register one class now (tests, tools).  Raises TypeError for a bad class or a duplicate name."""
        kind = self.check(cls)
        table = self.table(kind)
        old = table.get(cls.name)
        if old is not None and old is not cls and old.__module__ != cls.__module__:
            raise TypeError(f"duplicate {kind} name {cls.name!r}: {old.__module__} and {cls.__module__}")
        new = dict(table)
        new[cls.name] = cls
        self._set_table(kind, new)
        self.rev += 1

    def unregister(self, kind: str, name: str) -> None:
        table = self.table(kind)
        if name in table:
            new = dict(table)
            del new[name]
            self._set_table(kind, new)
            self.rev += 1

    def _set_table(self, kind: str, table: dict) -> None:
        if kind == "source":
            self.sources = table
        elif kind == "transition":
            self.transitions = table
        else:
            self.effects = table

    # -- discovery -----------------------------------------------------------------
    def discover(self, packages: tuple[str, ...] = PACKAGES) -> "Registry":
        self.rescan(packages)
        return self

    def rescan(self, packages: tuple[str, ...] = PACKAGES, force: bool = False) -> dict:
        """Import new / changed plug-in modules, drop removed ones.  Thread-safe (one scan at a time); the tables
        are replaced, never mutated in place.  Skips the files when nothing in the directories moved (``force``:
        look anyway).  -> {added, reloaded, removed, broken, took_ms}."""
        with self._lock:
            t0 = time.monotonic()
            w = self._watch[1] if self._watch is not None and self._watch[0] == packages else None
            if w is not None and not w.moved() and not force:
                self.rescanned_at = time.time()
                self.last_scan = out = {"added": [], "reloaded": [], "removed": [], "broken": len(self.broken),
                                        "errors": [], "took_ms": round((time.monotonic() - t0) * 1000, 1),
                                        "rev": self.rev}
                return out
            if w is None:
                w = DirWatch()
                self._watch = (packages, w)
            return self._rescan(packages, w, t0)

    def _rescan(self, packages: tuple[str, ...], watch: DirWatch, t0: float) -> dict:
        self.scans += 1
        watch.ok = False                     # until this pass completes: a pass that raises is redone next time
        importlib.invalidate_caches()
        tables = {k: dict(self.table(k)) for k in KINDS}
        modules = dict(self.modules)
        broken = dict(self.broken)
        added, reloaded, removed, errors = [], [], [], []
        seen: set[str] = set()
        watched = True                       # every directory watched before its files are read
        for pkg_name in packages:
            try:
                pkg = importlib.import_module(pkg_name)
            except Exception:
                log.exception("plug-in package %s failed to import; skipped", pkg_name)
                watched = False
                continue
            kind_dir = pkg_name.rsplit(".", 1)[-1]
            watched = all([watch.add(d) for d in pkg.__path__]) and watched
            base = str(pathlib.Path(pkg.__path__[0]))
            for name, ispkg in _iter_modules(list(pkg.__path__)):
                if name.startswith("_"):
                    continue
                mod_name = f"{pkg_name}.{name}"
                seen.add(mod_name)
                if ispkg:
                    watched = watch.add(os.path.join(base, name)) and watched
                path = os.path.join(base, name, "__init__.py") if ispkg else os.path.join(base, name + ".py")
                try:
                    mtime = os.stat(path).st_mtime
                except OSError:
                    continue
                known = broken.get(mod_name) or modules.get(mod_name)   # a failed reload: wait for a change
                if known is not None and known.get("mtime") == mtime:
                    continue
                first = mod_name not in modules
                try:
                    if mod_name in sys.modules:
                        mod = importlib.reload(sys.modules[mod_name])
                    else:
                        mod = importlib.import_module(mod_name)
                    classes = list(iter_plugin_classes(vars(mod).values(), mod_name))
                    if not classes:
                        raise TypeError("no plug-in class found (a Source / Transition / Effect subclass with a name)")
                    checked = []
                    for cls in classes:
                        kind = self.check(cls)
                        old = tables[kind].get(cls.name)
                        if old is not None and old.__module__ != mod_name:
                            raise TypeError(f"duplicate {kind} name {cls.name!r}: already {old.__module__}")
                        checked.append((kind, cls))
                except Exception as e:   # one broken plug-in must not take the service down
                    err = _short_error(e)
                    log.warning("plug-in module %s %s: %s", mod_name, "failed to load" if first else "reload failed "
                                "(keeping the previous version)", err)
                    broken[mod_name] = {"kind": {"sources": "source", "transitions": "transition"}.get(kind_dir, "effect"),
                                        "module": mod_name, "name": name, "file": path, "error": err,
                                        "mtime": mtime}
                    errors.append({"module": mod_name, "error": err})
                    if first:
                        sys.modules.pop(mod_name, None)
                    continue
                for k, n in (modules.get(mod_name) or {}).get("names", []):   # the module's previous classes
                    if tables[k].get(n) is not None and tables[k][n].__module__ == mod_name:
                        del tables[k][n]
                for kind, cls in checked:
                    tables[kind][cls.name] = cls
                broken.pop(mod_name, None)
                modules[mod_name] = {"kind": checked[0][0], "file": path, "mtime": mtime,
                                     "names": [(k, c.name) for k, c in checked]}
                (added if first else reloaded).extend(c.name for _, c in checked)
        for mod_name in [m for m in list(modules) + list(broken) if m not in seen]:
            if not any(mod_name.startswith(p + ".") for p in packages):
                continue
            info = modules.pop(mod_name, None)
            broken.pop(mod_name, None)
            for k, n in (info or {}).get("names", []):
                if tables[k].get(n) is not None and tables[k][n].__module__ == mod_name:
                    del tables[k][n]
                    removed.append(n)
            sys.modules.pop(mod_name, None)
        changed = bool(added or reloaded or removed) or broken.keys() != self.broken.keys() or \
            any(broken[m]["error"] != self.broken.get(m, {}).get("error") for m in broken)
        watch.ok = watched
        self.modules, self.broken = modules, broken
        for k in KINDS:
            self._set_table(k, tables[k])
        if changed:
            self.rev += 1
        self.rescanned_at = time.time()
        out = {"added": added, "reloaded": reloaded, "removed": removed, "broken": len(broken),
               "errors": errors, "took_ms": round((time.monotonic() - t0) * 1000, 1), "rev": self.rev}
        self.last_scan = out
        if added or reloaded or removed or errors:
            log.info("plug-in scan: %d added, %d reloaded, %d removed, %d broken (%.0f ms)", len(added),
                     len(reloaded), len(removed), len(broken), out["took_ms"])
        return out

    # -- lookups ---------------------------------------------------------------------
    def source(self, name: str, *, allow_internal: bool = False) -> type[Source]:
        cls = self.sources.get(name)
        if cls is None or (cls.internal and not allow_internal):
            raise ParamError("unknown_source", f"unknown source {name!r} (known: {self.public_sources()})")
        return cls

    def transition(self, name: str) -> type[Transition]:
        cls = self.transitions.get(name)
        if cls is None:
            raise ParamError("unknown_transition",
                             f"unknown transition {name!r} (known: {sorted(self.transitions)})")
        return cls

    def effect(self, name: str, *, allow_internal: bool = False) -> type:
        cls = self.effects.get(name)
        if cls is None or (getattr(cls, "internal", False) and not allow_internal):
            raise ParamError("unknown_effect", f"unknown effect {name!r} (known: {self.public_effects()})")
        return cls

    def public_sources(self) -> list[str]:
        return sorted(n for n, c in self.sources.items() if not c.internal)

    def public_effects(self) -> list[str]:
        return sorted(n for n, c in self.effects.items() if not getattr(c, "internal", False))

    def module_info(self, cls: type) -> dict:
        return self.modules.get(cls.__module__) or {}

    @staticmethod
    def describe_effect(cls: type) -> dict:
        if issubclass(cls, Effect):
            d = cls.describe()
        else:
            d = {"name": cls.name, "label": getattr(cls, "label", "") or cls.name,
                 "description": getattr(cls, "description", ""), "stage": getattr(cls, "stage", "stylise"),
                 "internal": bool(getattr(cls, "internal", False)), "schema": cls.schema(),
                 "origin": getattr(cls, "origin", "core"), "tags": dict(getattr(cls, "tags", {}) or {}),
                 "license_class": getattr(cls, "license_class", "unknown")}
        d["amount_mode"] = getattr(cls, "amount_mode", "native")
        return d

    def broken_list(self) -> list[dict]:
        return [{k: v for k, v in b.items() if k != "mtime"} for _, b in sorted(self.broken.items())]

    def catalog(self) -> dict:
        sources, transitions, effects = self.sources, self.transitions, self.effects
        return {
            "sources": [sources[n].describe() for n in sorted(sources)],
            "transitions": [transitions[n].describe() for n in sorted(transitions)],
            "effects": [self.describe_effect(effects[n]) for n in sorted(effects)],
            "broken": self.broken_list(),
            "filters": [],
        }


_default: Optional[Registry] = None


def default_registry() -> Registry:
    global _default
    if _default is None:
        _default = Registry().discover()
    return _default
