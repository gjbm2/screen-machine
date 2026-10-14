"""Live plug-in discovery (Registry.rescan), the effect kind for toolkit / GLSL bases, plug-in presets for
effects, the dev library index and thumbnails, and the auto director skipping picks that fail to prepare.
Engine tests: the plug-ins here are tiny throwaway modules, never the real plug-in files."""
from __future__ import annotations

import os
import pathlib
import sys
import textwrap
import time
import uuid

import pytest

from stream_service.plugins import Registry, default_registry
from stream_service.plugins.base import Source, plugin_kind
from stream_service.plugins._lib.effect_base import Effect as ToolkitEffect
from stream_service.presets import Presets

from .test_cue import AUTO, cue, live
from .test_sessions import FakeEngine, client  # noqa: F401 (fixture)

SOURCE = '''
from stream_service.plugins.base import Source
class {cls}(Source):
    name = "{name}"
    label = "{label}"
    params = {{"energy": {{"type": "number", "minimum": 0, "maximum": 1, "default": 0.5, "x-bindable": "level"}}}}
    def render(self, t, dt, out):
        pass
'''
EFFECT = '''
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import standard_effect_params
class {cls}(Effect):
    name = "{name}"
    stage = "{stage}"
    params = {{**standard_effect_params()}}
    def render(self, t, dt, src, out):
        out[...] = src
'''


@pytest.fixture
def pkgroot(tmp_path, monkeypatch):
    """A throwaway plug-in tree: <pkg>.sources and <pkg>.effects on sys.path."""
    pkg = f"smtest_{uuid.uuid4().hex[:8]}"
    for sub in ("", "sources", "effects"):
        d = tmp_path / pkg / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path / pkg, (f"{pkg}.sources", f"{pkg}.effects")
    for m in [m for m in sys.modules if m.startswith(pkg)]:
        del sys.modules[m]


def write(path: pathlib.Path, text: str, bump: float = 0.0) -> None:
    path.write_text(textwrap.dedent(text))
    t = time.time() + bump
    os.utime(path, (t, t))


def test_rescan_adds_reloads_breaks_and_removes(pkgroot):
    root, pkgs = pkgroot
    reg = Registry()
    write(root / "sources" / "alpha.py", SOURCE.format(cls="Alpha", name="alpha", label="Alpha"))
    write(root / "effects" / "tint.py", EFFECT.format(cls="Tint", name="tint", stage="colour"))
    res = reg.rescan(pkgs)
    assert sorted(res["added"]) == ["alpha", "tint"] and reg.broken == {}
    assert reg.effects["tint"].stage == "colour" and reg.effects["tint"].amount_mode == "native"
    assert reg.effects["tint"].default_bindings == {"pulse": {"bind": "beat", "range": [0.0, 1.0], "rest": 0.0}}
    rev = reg.rev
    # nothing changed: nothing is imported again, the rev stays
    assert reg.rescan(pkgs)["added"] == [] and reg.rev == rev
    # a new module and a broken one
    write(root / "sources" / "beta.py", SOURCE.format(cls="Beta", name="beta", label="Beta"))
    write(root / "sources" / "gamma.py", "class Oops(:\n")
    res = reg.rescan(pkgs)
    assert res["added"] == ["beta"] and len(res["errors"]) == 1 and reg.rev > rev
    (b,) = reg.broken_list()
    assert b["name"] == "gamma" and b["kind"] == "source" and "SyntaxError" in b["error"]
    assert reg.catalog()["broken"][0]["name"] == "gamma"
    # an edit that fails to load keeps the previous class running (stale), and is retried only on a change
    old = reg.sources["alpha"]
    write(root / "sources" / "alpha.py", "raise RuntimeError('half written')\n", bump=2)
    reg.rescan(pkgs)
    assert reg.sources["alpha"] is old and "RuntimeError" in reg.broken[f"{pkgs[0]}.alpha"]["error"]
    assert reg.rescan(pkgs)["errors"] == []
    # the fix reloads it (a new class object, new label)
    write(root / "sources" / "alpha.py", SOURCE.format(cls="Alpha", name="alpha", label="Alpha 2"), bump=4)
    res = reg.rescan(pkgs)
    assert res["reloaded"] == ["alpha"] and reg.sources["alpha"].label == "Alpha 2" and f"{pkgs[0]}.alpha" not in reg.broken
    # removed files: unregistered / no longer broken
    (root / "sources" / "beta.py").unlink()
    (root / "sources" / "gamma.py").unlink()
    res = reg.rescan(pkgs)
    assert res["removed"] == ["beta"] and "beta" not in reg.sources and reg.broken == {}


def test_rescan_rejects_a_duplicate_name_from_another_module(pkgroot):
    root, pkgs = pkgroot
    reg = Registry()
    write(root / "sources" / "one.py", SOURCE.format(cls="One", name="same", label="One"))
    write(root / "sources" / "two.py", SOURCE.format(cls="Two", name="same", label="Two"))
    reg.rescan(pkgs)
    assert reg.sources["same"].label == "One"
    assert "duplicate" in reg.broken[f"{pkgs[0]}.two"]["error"]


def test_toolkit_and_glsl_bases_count_as_plugin_kinds():
    class T(ToolkitEffect):
        name = "t"

    class S(Source):
        name = "s"

    assert plugin_kind(T) == "effect" and plugin_kind(S) == "source" and plugin_kind(ToolkitEffect) is None
    try:
        from stream_service.plugins._lib.glsl.plugins import GlslEffect, GlslSource, GlslTransition
    except ImportError:
        pytest.skip("no GLSL toolkit in this tree")
    for base in (GlslEffect, GlslSource, GlslTransition):
        assert plugin_kind(base) is None                     # the bases themselves have no name
        sub = type("X", (base,), {"name": "x", "__module__": "m"})
        assert plugin_kind(sub) == {GlslEffect: "effect", GlslSource: "source", GlslTransition: "transition"}[base]


def test_effect_plugins_get_presets_and_resolve_with_the_beat(tmp_path):
    reg = default_registry()
    name = next(n for n, c in sorted(reg.effects.items()) if not getattr(c, "internal", False))
    p = Presets(tmp_path, reg)                          # no config/live files: plug-in presets only
    mine = {k: v for k, v in p.effects.items() if v["effect"] == name}
    assert mine and all(v["stage"] == reg.effects[name].stage for v in mine.values())
    spec = p.resolve_effect({"effect": name})
    assert spec.effect == name and spec.bindings["pulse"]["bind"] == "beat"


def test_library_index_counts_broken_origin_and_thumb(tmp_path, monkeypatch):
    from PIL import Image

    from stream_service import library as libmod

    reg = default_registry()
    p = Presets(tmp_path, reg)
    scene = sorted(n for n, c in reg.sources.items() if not c.internal)[0]
    out = tmp_path / "fxout"
    (out / scene).mkdir(parents=True)
    sheet = Image.new("RGB", (480 * 3, 22 + 270 + 16), (0, 0, 0))
    sheet.paste(Image.new("RGB", (480, 270), (255, 0, 0)), (480, 22))     # the 2nd tile: the one shown
    sheet.save(out / scene / "sheet.png")
    monkeypatch.setattr(libmod, "FXTEST_OUT", out)
    fake = Registry()
    fake.sources, fake.transitions, fake.effects = reg.sources, reg.transitions, reg.effects
    fake.modules = reg.modules
    fake.broken = {"x.sources.st_bad": {"kind": "source", "module": "x.sources.st_bad", "name": "st_bad",
                                        "file": "/x", "error": "SyntaxError: nope", "mtime": time.time()}}
    lib = libmod.Library(fake, p)
    idx = lib.index()
    items = {i["key"]: i for i in idx["items"]}
    assert idx["counts"]["scene"]["total"] == sum(1 for c in reg.sources.values() if not c.internal) + 1
    assert idx["counts"]["scene"]["broken"] == 1 and items["scene/st_bad"]["load"] == "error"
    assert items["scene/st_bad"]["facets"]["origin"] == ["imported"]
    it = items[f"scene/{scene}"]
    assert it["load"] == "ok" and it["thumb"].startswith(f"/dev/thumb/{scene}.png") and it["added_at"]
    assert it["facets"]["origin"] == ["core"] and it["presets"]
    jpg = lib.thumb(scene)
    import io

    im = Image.open(io.BytesIO(jpg)).convert("RGB")
    r, g, b = im.getpixel((im.width // 2, im.height // 2))
    assert im.width == libmod.THUMB_W and r > 200 and g < 60 and b < 60
    assert lib.thumb("../etc") is None and lib.thumb("nothing_here") is None


async def test_auto_pick_that_fails_to_prepare_is_skipped_for_the_session(client, monkeypatch):
    pool = ["warp-cells", "warp-smoke", "warp-liquid", "fractal-dendrite", "fractal-rabbit", "fractal-seahorse"]
    sid, s, eng = await live(client, auto={**AUTO, "pool": pool, "avoid_repeat": 0})
    orig = FakeEngine.prepare

    def prepare(self, source, params):
        if getattr(source, "name", None) == "warp_stack":        # every warp-* preset fails to compile
            raise RuntimeError("nvrtc: compile error")
        return orig(self, source, params)

    monkeypatch.setattr(FakeEngine, "prepare", prepare)
    ok = 0
    for _ in range(8):
        r = await cue(client, sid, to="auto")
        assert r.status in (202, 500), await r.text()
        if r.status == 202:
            ok += 1
            assert s.scene_spec.source == "fractal_zoom"
    bad = {n for n, t in s.director.quarantine.items() if t == float("inf")}
    assert ok >= 6 and bad and bad <= {"warp-cells", "warp-smoke", "warp-liquid"}
    assert s.state == "live"
    # once blacklisted, a warp preset is never picked again
    for _ in range(4):
        assert s.director.pick_scene(s.mgr.presets.scenes, None, time.monotonic())[0] not in bad
