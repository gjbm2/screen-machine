"""The cheap plug-in rescan (plugins/__init__.py DirWatch, server.rescan_now): a rescan when nothing in the plug-in
directories moved skips the files; a touched / edited / new / deleted module and an edited presets.yaml are all
picked up by the next one.  Throwaway plug-in trees only (never the real plug-in files)."""
from __future__ import annotations

import functools
import os
import pathlib
import pkgutil
import shutil
import sys
import textwrap
import time
import uuid

import pytest

from stream_service import presets as presets_mod
from stream_service import server
from stream_service.plugins import PACKAGES, DirWatch, Registry, _iter_modules, default_registry
from stream_service.presets import Presets

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
SOURCE = '''
from stream_service.plugins.base import Source
class {cls}(Source):
    name = "{name}"
    label = "{label}"
    params = {{"energy": {{"type": "number", "minimum": 0, "maximum": 1, "default": 0.5, "x-bindable": "level"}}}}
    def render(self, t, dt, out):
        pass
'''


@pytest.fixture
def pkgroot(tmp_path, monkeypatch):
    """A throwaway plug-in tree (<pkg>.sources, <pkg>.effects) with an existing __pycache__/ as in production;
    no bytecode writes (a first import would otherwise create __pycache__/ and move the directory once)."""
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    pkg = f"smfast_{uuid.uuid4().hex[:8]}"
    for sub in ("", "sources", "effects"):
        d = tmp_path / pkg / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("")
        (d / "__pycache__").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path / pkg, (f"{pkg}.sources", f"{pkg}.effects")
    for m in [m for m in sys.modules if m.startswith(pkg)]:
        del sys.modules[m]


def write(path: pathlib.Path, text: str, bump: float = 0.0) -> None:
    path.write_text(textwrap.dedent(text))
    if bump:
        t = time.time() + bump
        os.utime(path, (t, t))


def scanned(reg: Registry, pkgs, **kw) -> tuple[bool, dict]:
    """-> (a full pass over the files ran, the result)."""
    n = reg.scans
    res = reg.rescan(pkgs, **kw)
    return reg.scans != n, res


def test_no_change_skips_the_files(pkgroot):
    root, pkgs = pkgroot
    write(root / "sources" / "alpha.py", SOURCE.format(cls="Alpha", name="alpha", label="Alpha"))
    reg = Registry()
    full, res = scanned(reg, pkgs)
    assert full and res["added"] == ["alpha"]
    assert reg._watch[1].ok, "inotify should watch a local tree"
    rev, keys = reg.rev, set(res)
    for _ in range(3):
        full, res = scanned(reg, pkgs)
        assert not full and res["added"] == res["reloaded"] == res["removed"] == res["errors"] == []
        assert set(res) == keys and res["rev"] == rev and res["broken"] == 0
    assert reg.last_scan is res and reg.rescanned_at is not None
    # a read (an import, a reload's bytecode into an existing __pycache__/) is not a move
    (root / "sources" / "alpha.py").read_text()
    assert not scanned(reg, pkgs)[0]


def test_touch_edit_new_and_deleted_modules_are_picked_up(pkgroot):
    root, pkgs = pkgroot
    alpha = root / "sources" / "alpha.py"
    write(alpha, SOURCE.format(cls="Alpha", name="alpha", label="Alpha"))
    reg = Registry()
    reg.rescan(pkgs)
    # touched (same content, new mtime): reloaded
    t = time.time() + 2
    os.utime(alpha, (t, t))
    full, res = scanned(reg, pkgs)
    assert full and res["reloaded"] == ["alpha"]
    assert not scanned(reg, pkgs)[0]
    # edited in place (the directory's own mtime does not move)
    dir_mt = os.stat(alpha.parent).st_mtime_ns
    write(alpha, SOURCE.format(cls="Alpha", name="alpha", label="Alpha 2"), bump=4)
    assert os.stat(alpha.parent).st_mtime_ns == dir_mt
    full, res = scanned(reg, pkgs)
    assert full and res["reloaded"] == ["alpha"] and reg.sources["alpha"].label == "Alpha 2"
    # a new module
    write(root / "sources" / "beta.py", SOURCE.format(cls="Beta", name="beta", label="Beta"))
    full, res = scanned(reg, pkgs)
    assert full and res["added"] == ["beta"]
    # a broken edit, then its fix
    write(root / "sources" / "beta.py", "class Oops(:\n", bump=6)
    full, res = scanned(reg, pkgs)
    assert full and len(res["errors"]) == 1 and reg.broken_list()[0]["name"] == "beta"
    assert not scanned(reg, pkgs)[0]
    write(root / "sources" / "beta.py", SOURCE.format(cls="Beta", name="beta", label="Beta 2"), bump=8)
    assert scanned(reg, pkgs)[1]["reloaded"] == ["beta"] and reg.broken == {}
    # deleted
    (root / "sources" / "beta.py").unlink()
    full, res = scanned(reg, pkgs)
    assert full and res["removed"] == ["beta"] and "beta" not in reg.sources
    assert not scanned(reg, pkgs)[0]


def test_presets_yaml_and_other_files_move_the_directory(pkgroot):
    root, pkgs = pkgroot
    write(root / "sources" / "alpha.py", SOURCE.format(cls="Alpha", name="alpha", label="Alpha"))
    reg = Registry()
    reg.rescan(pkgs)
    write(root / "sources" / "alpha.presets.yaml", "one:\n  label: One\n")
    assert scanned(reg, pkgs)[0]
    assert not scanned(reg, pkgs)[0]
    write(root / "sources" / "alpha.presets.yaml", "two:\n  label: Two\n")       # edited in place
    full, res = scanned(reg, pkgs)
    assert full and res["reloaded"] == [] and res["added"] == []
    (root / "effects" / "alpha.presets.yaml").write_text("x: {}\n")                 # the other directory
    assert scanned(reg, pkgs)[0]


def test_force_another_package_set_and_a_subpackage(pkgroot):
    root, pkgs = pkgroot
    write(root / "sources" / "alpha.py", SOURCE.format(cls="Alpha", name="alpha", label="Alpha"))
    sub = root / "sources" / "gamma"
    sub.mkdir()
    write(sub / "__init__.py", SOURCE.format(cls="Gamma", name="gamma", label="Gamma"))
    reg = Registry()
    assert sorted(reg.rescan(pkgs)["added"]) == ["alpha", "gamma"]
    assert scanned(reg, pkgs, force=True)[0]                          # manual: always looks
    assert not scanned(reg, pkgs)[0]
    assert scanned(reg, pkgs[:1])[0]                                  # another package set: its own watch
    assert scanned(reg, pkgs)[0]
    write(sub / "__init__.py", SOURCE.format(cls="Gamma", name="gamma", label="Gamma 2"), bump=2)
    full, res = scanned(reg, pkgs)                                    # the subpackage's directory is watched
    assert full and res["reloaded"] == ["gamma"] and reg.sources["gamma"].label == "Gamma 2"


def test_without_inotify_every_rescan_is_a_full_one(pkgroot, monkeypatch):
    monkeypatch.setattr(DirWatch, "add", lambda self, d: False)
    root, pkgs = pkgroot
    alpha = root / "sources" / "alpha.py"
    write(alpha, SOURCE.format(cls="Alpha", name="alpha", label="Alpha"))
    reg = Registry()
    reg.rescan(pkgs)
    assert scanned(reg, pkgs)[0] and scanned(reg, pkgs)[1]["reloaded"] == []
    write(alpha, SOURCE.format(cls="Alpha", name="alpha", label="Alpha 2"), bump=2)
    assert scanned(reg, pkgs)[1]["reloaded"] == ["alpha"]


def test_iter_modules_matches_pkgutil(tmp_path):
    d = tmp_path / "pk"
    d.mkdir()
    for f in ("a.py", "b.py", "_c.py", "x.presets.yaml", "notes.md", "d.pyc", "e.cpython-312-x86_64-linux-gnu.so",
              "f.g.py", "h.py"):
        (d / f).write_text("")
    for sub, init in (("pkg", "__init__.py"), ("h", "__init__.py"), ("nopkg", None), ("__pycache__", None),
                      ("dot.ted", "__init__.py")):
        (d / sub).mkdir()
        if init:
            (d / sub / init).write_text("")
    (d / "__pycache__" / "__init__.cpython-312.pyc").write_text("")
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.py").write_text("")
    (other / "z.py").write_text("")
    paths = [str(d), str(other)]
    want = sorted((m.name, m.ispkg) for m in pkgutil.iter_modules(paths))
    assert _iter_modules(paths) == want
    assert ("h", True) in want and ("nopkg", False) not in want                    # a package beats h.py
    import importlib

    for pkg in PACKAGES:                                                              # the real tree
        path = list(importlib.import_module(pkg).__path__)
        assert _iter_modules(path) == sorted((m.name, m.ispkg) for m in pkgutil.iter_modules(path))


async def test_rescan_now_rereads_plugin_presets_only_after_a_full_pass(pkgroot):
    root, pkgs = pkgroot
    write(root / "sources" / "alpha.py", SOURCE.format(cls="Alpha", name="alpha", label="Alpha"))
    reg = Registry()
    reg.rescan(pkgs)
    reg.rescan = functools.partial(reg.rescan, pkgs)          # rescan_now scans the default packages

    class FakePresets:
        rev = 0

        def __init__(self):
            self.calls: list = []

        def reload_if_changed(self, **kw):
            self.calls.append(kw)
            return False

    events: list = []

    class Events:
        def emit(self, ev):
            events.append(ev)

    pre = FakePresets()
    app = {server.REGISTRY: reg, server.PRESETS: pre, server.EVENTS: Events()}
    res = await server.rescan_now(app, "rescan")
    assert res["added"] == [] and pre.calls == [] and events == []
    write(root / "sources" / "beta.py", SOURCE.format(cls="Beta", name="beta", label="Beta"))
    await server.rescan_now(app, "rescan")
    assert pre.calls == [{"plugin_files": True, "wait": True}]
    assert [(e["type"], e["added"]) for e in events] == [("catalog.changed", ["beta"])]
    await server.rescan_now(app, "manual")                    # forced: the presets look too, nothing moved
    assert len(pre.calls) == 2 and len(events) == 1


def test_an_edited_presets_yaml_reaches_the_presets(pkgroot, tmp_path, monkeypatch):
    root, pkgs = pkgroot
    monkeypatch.setattr(presets_mod, "PLUGIN_ROOT", root)
    name = f"fast_{uuid.uuid4().hex[:8]}"                     # Presets' plug-in cache is shared by instances
    write(root / "sources" / f"{name}.py", SOURCE.format(cls="Alpha", name=name, label="Alpha"))
    yml = root / "sources" / f"{name}.presets.yaml"
    write(yml, "fast-one:\n  label: One\n  params: {energy: 0.2}\n")
    reg = Registry()
    reg.rescan(pkgs)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    p = Presets(cfg, reg)
    assert "fast-one" in p.scenes

    def rescan_now() -> bool:
        n = reg.scans
        reg.rescan(pkgs)
        if reg.scans != n:
            p.reload_if_changed(plugin_files=True, wait=True)
        return reg.scans != n

    assert not rescan_now()
    rev = p.rev
    write(yml, "fast-two:\n  label: Two\n  params: {energy: 0.7}\n", bump=2)     # in place
    assert rescan_now() and p.rev > rev
    assert "fast-two" in p.scenes and "fast-one" not in p.scenes and p.scenes["fast-two"]["params"]["energy"] == 0.7


def test_overlay_cache_gives_the_same_tables(tmp_path):
    for f in ("scenes.yaml", "transitions.yaml", "effects.yaml", "vocab.yaml", "tags.generated.json"):
        if (REPO_CONFIG / f).exists():
            shutil.copy(REPO_CONFIG / f, tmp_path / f)
    reg = default_registry()
    p = Presets(tmp_path, reg)
    assert any(p.overlay.presets.values()) or not (REPO_CONFIG / "tags.generated.json").exists()
    before = (p.scenes, p.transitions, p.effects, p.catalog_errors())
    p._rebuild()                                              # every entry reused
    assert (p.scenes, p.transitions, p.effects, p.catalog_errors()) == before
    p._ocache = {}
    p._rebuild()                                              # every entry computed again
    assert (p.scenes, p.transitions, p.effects, p.catalog_errors()) == before
