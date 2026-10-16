"""The dev console's library index: one item per plug-in (scene, transition, effect), broken ones included.

``GET /dev/library`` returns ``{rev, generated_at, stages, facets, counts, items}``; static/console/ (data/library.js) renders it.
An item gathers a plug-in's presets (config/live and its own ``<id>.presets.yaml``), their tags (family, dims,
technique, moods, style, energy) as filter facets, a thumbnail and preview from the fxtest harness output
(``~/.cache/sm-fxtest/out/<name>/sheet.png`` / ``video.mp4``), the module's mtime (``added_at``: "new" badges)
and the session runtime error count.  A module that failed to import is an item with ``load: "error"``; one whose
latest edit failed to reload keeps working (``load: "stale"``) and shows the error too.

Thumbnails: the sheet's first row holds the run; the tile shown is a scene's 2nd frame, a transition's middle
frame or an effect's 2nd output (effect rows alternate input / output).  Served as small JPEGs, cached by mtime.

Vetting data for the directors' auto pools (sessions.py ``auto_ok``): :func:`vetting` = the plug-ins with a sheet
(they rendered at least once) and the harness cost estimates (``est_ms_4k``), rescanned in the background at most
every ``VET_TTL_S``; :class:`Calibration` = the overnight per-GPU calibration (``~/.cache/sm-fxtest/
calibration.json``, tools/calibrate.py), re-read when its mtime changes.  An item's ``slow`` lists GPU guard trips
(SessionManager.slow) and calibration entries that are not ok.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import pathlib
import re
import sys
import threading
import time
from typing import Any, Optional

from .plugins.base import STAGES

log = logging.getLogger("stream_service.library")

FXTEST_OUT = pathlib.Path.home() / ".cache" / "sm-fxtest" / "out"
THUMB_W = 320
SHEET_TOP = 22                                # the sheet's title strip
TILE_W = {"scene": 480, "transition": 320, "effect": 320}
TILE_PICK = {"scene": 1, "transition": 2, "effect": 3}
FACETS = {
    "scene": ["modes", "dims", "family", "origin", "moods", "technique", "energy", "licence"],
    "transition": ["modes", "family", "origin", "style", "technique", "energy", "licence"],
    "effect": ["modes", "stage", "dims", "origin", "moods", "style", "technique", "energy", "licence"],
}
IMPORTED_PREFIXES = ("glt_", "isf_", "st_")         # imported GLSL (gl-transitions, ISF, Shadertoy)
INDEX_TTL_S = 5.0
VET_TTL_S = 30.0
CALIBRATION_FILE: Optional[pathlib.Path] = None     # None: FXTEST_OUT/../calibration.json
CAL_CHECK_S = 1.0


def origin_of(name: str) -> str:
    return "imported" if name.startswith(IMPORTED_PREFIXES) else "core"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_LIC_RE = re.compile(r"license_class:\s*([a-z]+)", re.I)


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _list(v: Any) -> list:
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])]


def _bands(e: Any) -> list[str]:
    if not (isinstance(e, (list, tuple)) and len(e) == 2):
        return []
    lo, hi = float(e[0]), float(e[1])
    return [n for n, a, b in (("low", 0.0, 0.35), ("mid", 0.35, 0.65), ("high", 0.65, 1.0)) if lo < b and hi > a]


def _licence(cls: type) -> str:
    doc = getattr(sys.modules.get(cls.__module__), "__doc__", None) or ""
    m = _LIC_RE.search(doc)
    return m.group(1).lower() if m else "unknown"


def _artifacts(name: str) -> dict:
    d = FXTEST_OUT / name
    out: dict = {}
    for key, fname in (("sheet", "sheet.png"), ("video", "video.mp4"), ("summary", "summary.json")):
        try:
            out[key] = (d / fname).stat().st_mtime
        except OSError:
            pass
    return out


BENCH_TTL_S = 30.0           # harness numbers change only when the harness reruns; the directors ask per candidate
_bench_cache: dict[str, tuple[float, Optional[float]]] = {}


def _bench_ms(name: str) -> Optional[float]:
    now = time.monotonic()
    hit = _bench_cache.get(name)
    if hit is not None and now - hit[0] < BENCH_TTL_S:
        return hit[1]
    try:
        v = _bench_of(json.loads((FXTEST_OUT / name / "summary.json").read_text()))
    except (OSError, ValueError):
        v = None
    _bench_cache[name] = (now, v)
    return v


def _bench_of(s: Any) -> Optional[float]:
    for k in ("bench_ms_4k", "ms_4k", "bench_ms"):
        v = s.get(k) if isinstance(s, dict) else None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict) and isinstance(v.get("p50"), (int, float)):
            return float(v["p50"])
    bench = s.get("bench") if isinstance(s, dict) else None
    if isinstance(bench, dict):
        for k in ("ms_p50", "p50_ms", "ms"):
            if isinstance(bench.get(k), (int, float)):
                return float(bench[k])
    return None


def harness_ms_4k(summary: Any) -> Optional[float]:
    """The harness's 4K cost in a summary.json: a bench figure, else the smoke test's estimate (``est_ms_4k``)."""
    v = _bench_of(summary)
    if v is not None:
        return v
    v = summary.get("est_ms_4k") if isinstance(summary, dict) else None
    return float(v) if isinstance(v, (int, float)) else None


_vet_lock = threading.Lock()
_vet: dict[str, tuple] = {}                  # str(FXTEST_OUT) -> (loaded at, frozenset(names with a sheet), {name: ms})
_vet_busy: set[str] = set()


def _load_vetting(root: pathlib.Path) -> tuple:
    sheets: set[str] = set()
    est: dict[str, float] = {}
    try:
        dirs = [d for d in os.scandir(root) if d.is_dir()]
    except OSError:
        dirs = []
    for d in dirs:
        if os.path.exists(os.path.join(d.path, "sheet.png")):
            sheets.add(d.name)
        try:
            with open(os.path.join(d.path, "summary.json")) as f:
                ms = harness_ms_4k(json.load(f))
        except (OSError, ValueError):
            ms = None
        if ms is not None:
            est[d.name] = ms
    snap = (time.monotonic(), frozenset(sheets), est)
    with _vet_lock:
        _vet[str(root)] = snap
        _vet_busy.discard(str(root))
    return snap


def vetting() -> tuple[frozenset, dict]:
    """(plug-ins with a thumbnail sheet, {plug-in: harness 4K ms}).  The first call loads synchronously; later
    ones return the snapshot and refresh it in the background when it is older than VET_TTL_S."""
    root = FXTEST_OUT
    key = str(root)
    with _vet_lock:
        snap = _vet.get(key)
        stale = snap is not None and time.monotonic() - snap[0] > VET_TTL_S and key not in _vet_busy
        if stale:
            _vet_busy.add(key)
    if snap is None:
        snap = _load_vetting(root)
    elif stale:
        threading.Thread(target=_load_vetting, args=(root,), name="library-vet", daemon=True).start()
    return snap[1], snap[2]


class Calibration:
    """tools/calibrate.py's results: ``{"gpus": {...}, "plugins": {name: {"kind", "sha1", "gpu0": {status, p50,
    p95, max, mem_mb, budget_ms, render_scale, fit_scale, fit_p95, fits}, ...}}}``.  Re-read when the file's mtime
    changes (stat at most once a second); a missing or unreadable file is no calibration."""

    def __init__(self, path: Optional[pathlib.Path] = None):
        self._path = path
        self._mtime: Optional[float] = None
        self._plugins: dict = {}
        self._checked = -1e9
        self._lock = threading.Lock()

    @property
    def path(self) -> pathlib.Path:
        return self._path or CALIBRATION_FILE or FXTEST_OUT.parent / "calibration.json"

    def plugins(self) -> dict:
        now = time.monotonic()
        if now - self._checked < CAL_CHECK_S:
            return self._plugins
        with self._lock:
            self._checked = now
            try:
                mt = self.path.stat().st_mtime
            except OSError:
                self._mtime, self._plugins = None, {}
                return self._plugins
            if mt != self._mtime:
                try:
                    data = json.loads(self.path.read_text())
                    plugins = data.get("plugins") if isinstance(data, dict) else None
                    self._plugins = plugins if isinstance(plugins, dict) else {}
                    self._mtime = mt
                except (OSError, ValueError) as e:      # being rewritten: keep the previous data, retry soon
                    log.warning("calibration %s unreadable: %s", self.path, e)
        return self._plugins

    def entry(self, name: str, gpu: Any) -> Optional[dict]:
        p = self.plugins().get(name)
        e = p.get(f"gpu{gpu}") if isinstance(p, dict) else None
        return e if isinstance(e, dict) else None

    @staticmethod
    def ok(e: dict) -> bool:
        return e.get("status") == "ok" and e.get("fits") is True

    def problems(self) -> list[dict]:
        """Calibration entries that are not ok, as catalog 'slow' rows (too_slow / fits false -> slow,
        hung / error -> broken)."""
        out = []
        for name, p in sorted(self.plugins().items()):
            if not isinstance(p, dict):
                continue
            for k, e in sorted(p.items()):
                if not (k.startswith("gpu") and isinstance(e, dict)) or self.ok(e):
                    continue
                st = e.get("status") or "unknown"
                out.append({"kind": p.get("kind"), "name": name, "source": "calibration", "gpu": k[3:],
                            "status": "broken" if st in ("hung", "error") else "slow", "calibration": st,
                            "ms": e.get("fit_p95") if e.get("fit_scale") else e.get("p95"),
                            "budget_ms": e.get("budget_ms"), "fits": e.get("fits"), "at": e.get("at"),
                            **({"error": str(e["error"])[:200]} if e.get("error") else {})})
        return out


class Library:
    def __init__(self, registry: Any, presets: Any, mgr: Any = None):
        self.registry = registry
        self.presets = presets
        self.mgr = mgr
        self._thumbs: dict[str, tuple[float, bytes]] = {}
        self._index: Optional[tuple] = None
        self._lock = threading.Lock()

    # -- index -------------------------------------------------------------------------
    def index(self) -> dict:
        """The library index; rebuilt when the registry / presets move, else at most every INDEX_TTL_S (the
        harness writes thumbnails in between)."""
        key = (self.registry.rev, self.presets.rev)
        with self._lock:
            hit = self._index
        if hit is not None and hit[0] == key and time.monotonic() - hit[1] < INDEX_TTL_S:
            return hit[2]
        out = self._build()
        with self._lock:
            self._index = (key, time.monotonic(), out)
        return out

    def _build(self) -> dict:
        reg, pre = self.registry, self.presets
        errs = getattr(self.mgr, "plugin_errors", {}) or {}
        items: list[dict] = []
        broken_by_mod = dict(reg.broken)
        kinds = (("scene", reg.sources, pre.scenes, "source"), ("transition", reg.transitions, pre.transitions,
                                                                "transition"),
                 ("effect", reg.effects, pre.effects, "effect"))
        for kind, table, presets, key in kinds:
            by_plugin: dict[str, list[tuple[str, dict]]] = {}
            for pname, p in presets.items():
                by_plugin.setdefault(p.get(key), []).append((pname, p))
            for name in sorted(table):
                cls = table[name]
                if getattr(cls, "internal", False):
                    continue
                plist = sorted(by_plugin.get(name, []), key=lambda x: (bool(x[1].get("auto")), x[0]))
                items.append(self._item(kind, name, cls, plist, errs, broken_by_mod.pop(cls.__module__, None)))
        for mod, b in broken_by_mod.items():        # never loaded: show it greyed with the error
            kind = b.get("kind", "scene" if ".sources." in mod else "transition" if ".transitions." in mod else "effect")
            kind = "scene" if kind == "source" else kind
            art = _artifacts(b["name"])
            items.append({
                "key": f"{kind}/{b['name']}", "kind": kind, "name": b["name"], "plugin": b["name"], "preset": None,
                "label": b["name"].replace("_", " "), "description": None, "spec": None, "presets": [],
                "facets": {**{f: [] for f in FACETS[kind]}, "origin": [origin_of(b["name"])]}, "load": "error",
                "error": b.get("error"),
                "thumb": self._thumb_url(b["name"], art), "preview": None, "added_at": _iso(b.get("mtime")),
                "runtime_errors": 0, "status": "broken"})
        counts = {k: {"total": 0, "broken": 0} for k in FACETS}
        for it in items:
            counts[it["kind"]]["total"] += 1
            if it["load"] == "error":
                counts[it["kind"]]["broken"] += 1
        return {"rev": f"{reg.rev}.{pre.rev}", "generated_at": _iso(time.time()),
                "rescanned_at": _iso(reg.rescanned_at), "stages": list(STAGES), "facets": FACETS,
                "counts": counts, "items": items}

    def _item(self, kind: str, name: str, cls: type, plist: list, errs: dict, broken: Optional[dict]) -> dict:
        tags: dict[str, list] = {"family": [], "dims": [], "technique": [], "moods": [], "style": [], "modes": []}
        lo, hi, weight = None, None, None
        for _, p in plist:
            t = p.get("tags") or {}
            for k in ("family", "dims", "technique", "moods", "modes"):
                for v in _list(t.get(k)):
                    if v not in tags[k]:
                        tags[k].append(v)
            for v in _list(t.get("style")) + _list(t.get("transition_style")):
                if v not in tags["style"]:
                    tags["style"].append(v)
            e = t.get("energy")
            if isinstance(e, (list, tuple)) and len(e) == 2:
                lo = e[0] if lo is None else min(lo, e[0])
                hi = e[1] if hi is None else max(hi, e[1])
            if weight is None and isinstance(t.get("weight"), (int, float)):
                weight = t["weight"]
        for k, v in (getattr(cls, "tags", None) or {}).items():   # plug-in level tags, if any
            if k in tags:
                for x in _list(v):
                    if x not in tags[k]:
                        tags[k].append(x)
        stage = getattr(cls, "stage", None) if kind == "effect" else None
        energy = [lo, hi] if lo is not None else None
        facets = {"family": tags["family"], "dims": [d.lower() for d in tags["dims"]], "technique": tags["technique"],
                  "moods": tags["moods"], "style": tags["style"], "energy": _bands(energy),
                  "modes": [str(m).lower() for m in tags["modes"]],
                  "stage": [stage] if stage else [], "licence": [_licence(cls)], "origin": [origin_of(name)]}
        facets = {f: facets.get(f, []) for f in FACETS[kind]}
        first = plist[0][0] if plist else None
        bare = {"scene": {"source": name}, "transition": {"transition": name}, "effect": {"effect": name}}[kind]
        info = self.registry.module_info(cls)
        art = _artifacts(name)
        rt = (errs.get(("source" if kind == "scene" else kind, name)) or {})
        slow_fn = getattr(self.mgr, "slow_for", None)
        slow = slow_fn("source" if kind == "scene" else kind, name) if slow_fn is not None else None
        item = {
            "key": f"{kind}/{name}", "kind": kind, "name": name, "plugin": name, "preset": first,
            "label": getattr(cls, "label", "") or name, "description": getattr(cls, "description", "") or None,
            "spec": first if first else bare, "bare": bare, "stage": stage,
            "presets": [{"name": n, "label": p.get("label") or n, "auto": bool(p.get("auto")),
                         "energy": (p.get("tags") or {}).get("energy")} for n, p in plist],
            "energy": energy, "weight": weight, "facets": facets,
            "thumb": self._thumb_url(name, art), "preview": f"/dev/preview/{name}.mp4?v={int(art['video'])}"
            if "video" in art else None,
            "bench_ms_4k": _bench_ms(name) if "summary" in art else None,
            "added_at": _iso(info.get("mtime")), "load": "ok", "error": None,
            "runtime_errors": int(rt.get("count", 0)), "runtime_error": rt.get("last_detail"),
            "status": "accepted", "slow": slow,
        }
        if kind == "transition":
            p0 = plist[0][1].get("params", {}) if plist else {}
            item["duration_s"] = p0.get("duration_s", getattr(cls, "default_duration_s", None))
        if broken is not None:
            item["load"], item["error"] = "stale", f"latest edit failed to load (running the previous version): " \
                                                   f"{broken.get('error')}"
        return item

    def cost_ms(self, kind: str, name: str) -> Optional[float]:
        """4K bench ms of a preset or plug-in from the harness summary, or None."""
        table = {"effect": self.presets.effects, "scene": self.presets.scenes,
                 "transition": self.presets.transitions}.get(kind, {})
        entry = table.get(name)
        if entry is not None:
            name = entry.get({"scene": "source"}.get(kind, kind), name)
        return _bench_ms(name) if NAME_RE.match(name) else None

    @staticmethod
    def _thumb_url(name: str, art: dict) -> Optional[str]:
        return f"/dev/thumb/{name}.png?v={int(art['sheet'])}" if "sheet" in art else None

    # -- thumbnails ---------------------------------------------------------------------
    def kind_of(self, name: str) -> str:
        if name in self.registry.sources:
            return "scene"
        if name in self.registry.transitions:
            return "transition"
        if name in self.registry.effects:
            return "effect"
        try:
            s = json.loads((FXTEST_OUT / name / "summary.json").read_text())
            k = s.get("kind")
            return "scene" if k in ("source", "scene") else k if k in TILE_W else "scene"
        except (OSError, ValueError, AttributeError):
            return "scene"

    def thumb(self, name: str) -> Optional[bytes]:
        """The JPEG thumbnail for a plug-in, or None (no sheet).  Cached by the sheet's mtime."""
        if not NAME_RE.match(name):
            return None
        path = FXTEST_OUT / name / "sheet.png"
        try:
            mt = path.stat().st_mtime
        except OSError:
            return None
        with self._lock:
            hit = self._thumbs.get(name)
        if hit is not None and hit[0] == mt:
            return hit[1]
        from PIL import Image

        kind = self.kind_of(name)
        with Image.open(path) as im:
            im = im.convert("RGB")
            tw = TILE_W.get(kind, 480)
            th = round(tw * 9 / 16)
            if im.width < tw or im.height < SHEET_TOP + th:      # an unexpected layout: the whole sheet
                tile = im
            else:
                cols = max(1, im.width // tw)
                i = min(TILE_PICK.get(kind, 0), cols - 1)
                tile = im.crop((i * tw, SHEET_TOP, (i + 1) * tw, SHEET_TOP + th))
            tile = tile.resize((THUMB_W, max(1, round(THUMB_W * tile.height / tile.width))), Image.Resampling.BILINEAR)
            buf = io.BytesIO()
            tile.save(buf, "JPEG", quality=82)
        data = buf.getvalue()
        with self._lock:
            self._thumbs[name] = (mt, data)
            if len(self._thumbs) > 600:
                self._thumbs.pop(next(iter(self._thumbs)))
        return data

    @staticmethod
    def preview_path(name: str) -> Optional[pathlib.Path]:
        if not NAME_RE.match(name):
            return None
        p = FXTEST_OUT / name / "video.mp4"
        return p if p.is_file() else None
