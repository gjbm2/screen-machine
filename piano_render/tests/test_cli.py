"""python -m piano_render: list, render (into a named cache, or to a file with no cache at all), and the lazy fetch /
play-pc22 imports. There is no prerender any more (owner, 22 Oct: the test piano renders as it plays)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from piano_render import __main__ as cli

REPO = Path(__file__).resolve().parents[2]


def run(*args, env_extra=None, cwd=None):
    env = dict(os.environ, PYTHONPATH=str(REPO))
    env.update(env_extra or {})
    for k, v in list(env.items()):
        if v is None:
            del env[k]
    p = subprocess.run([sys.executable, "-m", "piano_render", *args], cwd=str(cwd or REPO), env=env,
                       capture_output=True, text=True, timeout=300)
    return p.returncode, p.stdout, p.stderr


def test_list_json(sfz_registry, cache):
    rc, out, err = run("list", "--json")
    assert rc == 0, err
    d = json.loads(out)
    ids = {r["id"]: r for r in d["instruments"]}
    assert ids["synth"]["available"] and ids["synth"]["default"] and ids["basic"]["available"]
    assert d["config"] == os.environ["PIANO_RENDER_CONFIG"]


def test_list_text_with_config_flag(sfz_registry, cache, tmp_path):
    cfg = os.environ["PIANO_RENDER_CONFIG"]
    rc, out, err = run("--config", cfg, "list", env_extra={"PIANO_RENDER_CONFIG": ""})
    assert rc == 0, err
    assert "synth" in out and "Test sine piano" in out and "[ok]" in out


def test_render_json_then_cached(sfz_registry, midi, cache):
    m = midi([(0.5, "on", 69, 100), (0.8, "off", 69, 64)])
    rc, out, err = run("--no-idle", "render", str(m), "--instrument", "synth", "--json")
    assert rc == 0, err
    d = json.loads(out)                     # stdout is pure JSON (libsfizz's chatter goes to stderr)
    assert not d["cached"] and Path(d["path"]).exists() and d["instrument"] == "synth"
    assert d["sample_rate"] == 48000 and d["realism"] is True
    rc, out, err = run("render", str(m), "--json")          # default instrument, cache hit
    assert rc == 0, err
    d2 = json.loads(out)
    assert d2["cached"] and d2["cache_key"] == d["cache_key"]
    rc, out, err = run("render", str(m), "--no-realism")
    assert rc == 0, err
    assert "rendered in" in out


def test_render_errors_are_one_line(sfz_registry, midi, cache):
    m = midi([(0.5, "on", 69, 100)])
    rc, out, err = run("render", str(m), "--instrument", "nope")
    assert rc == 1 and "unknown instrument" in err
    rc, out, err = run("render", str(m), "--instrument", "basic")
    assert rc == 1 and "PC22" in err


def test_with_no_cache_render_writes_its_file_and_keeps_nothing_else(sfz_registry, midi, tmp_path, monkeypatch):
    monkeypatch.delenv("PIANO_RENDER_CACHE", raising=False)
    m = midi([(0.5, "on", 69, 100), (0.8, "off", 69, 64)], name="tune.mid")
    work = tmp_path / "here"
    work.mkdir()
    rc, out, err = run("--no-idle", "render", str(m), "--instrument", "synth", "--json", cwd=work,
                       env_extra={"PIANO_RENDER_CACHE": None})
    assert rc == 0, err
    d = json.loads(out)
    assert d["path"] == str(work / "tune - synth.flac") and not d["cached"]
    assert sorted(p.name for p in work.iterdir()) == ["tune - synth.flac"]            # no sidecar, lock or progress
    rc, out, err = run("render", str(m), "--out", str(tmp_path / "x" / "y.flac"), env_extra={"PIANO_RENDER_CACHE":
                                                                                         None})
    assert rc == 0, err
    assert (tmp_path / "x" / "y.flac").exists()
    from piano_render import render
    with pytest.raises(ValueError, match="nothing is kept unless asked"):
        render.render(m, "synth", registry=sfz_registry)


def test_there_is_no_prerender_any_more():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["prerender"])


def test_optional_modules_missing_message(capsys):
    assert cli.optional_module("no_such_module_xyz", "fetch") is None
    assert "not installed yet" in capsys.readouterr().err


@pytest.fixture
def env_guard(monkeypatch):
    # cli.main() writes these into os.environ; monkeypatch puts the originals back afterwards (setenv first: a
    # delenv of an unset variable records nothing, and the value main() set would leak into later tests)
    for var in ("PIANO_RENDER_CONFIG", "PIANO_RENDER_CACHE"):
        monkeypatch.setenv(var, "")
        monkeypatch.delenv(var)


def test_fetch_passes_registry_and_args(monkeypatch, tmp_path, env_guard):
    import types
    seen = {}
    fake = types.ModuleType("piano_render.fetch")
    fake.main = lambda argv: seen.setdefault("argv", argv) and 0
    monkeypatch.setitem(sys.modules, "piano_render.fetch", fake)
    cfg = tmp_path / "i.json"
    cfg.write_text("{}")
    assert cli.main(["--config", str(cfg), "fetch", "salamander", "--status"]) == 0
    assert seen["argv"] == ["--registry", str(cfg.resolve()), "salamander", "--status"]
    # an option first (argparse.REMAINDER used to reject "fetch --status"), and fetch's own --registry
    seen.clear()
    assert cli.main(["fetch", "--status"]) == 0
    assert seen["argv"][-1] == "--status" and "salamander" not in seen["argv"]
    seen.clear()
    other = tmp_path / "other.json"
    assert cli.main(["--config", str(cfg), "fetch", "--registry", str(other), "a", "b"]) == 0
    assert seen["argv"] == ["--registry", str(other), "a", "b"]


def test_fetch_status_from_the_command_line():
    # the real module, end to end: no network, reports what is installed
    rc, out, err = run("fetch", "--status")
    assert rc == 0, err
    assert out.split()[0] == "sfizz"


def test_play_pc22_uses_the_module(monkeypatch, tmp_path, capsys, env_guard):
    import types
    calls = {}
    fake = types.ModuleType("piano_render.pc22")

    def play(path, **kw):
        calls["play"] = (path, kw)
        return {"path": path, "t_audio0": 1.0, "error": None}

    fake.play = play
    monkeypatch.setitem(sys.modules, "piano_render.pc22", fake)
    f = tmp_path / "x.flac"
    f.write_bytes(b"")
    assert cli.main(["play-pc22", str(f), "--instrument", "synth"]) == 0
    assert calls["play"][0] == str(f) and calls["play"][1]["instrument"] == "synth"
    assert json.loads(capsys.readouterr().out)["t_audio0"] == 1.0
