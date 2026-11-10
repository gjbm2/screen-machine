"""smconfig: deployment selection, the manifest, layering, the CLI and the shipped deployments' validity."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import smconfig
from smconfig import check as smcheck
from smconfig.legacy import MOVES, SHOW_SITE_KEYS

REPO = smconfig.REPO_ROOT


@pytest.fixture
def no_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr(smconfig, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.delenv("SM_DEPLOYMENT", raising=False)


@pytest.fixture
def fake_deployments(monkeypatch, tmp_path):
    root = tmp_path / "deployments"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "deployment.yaml").write_text(
        "id: alpha\nname: Alpha\ncapabilities: {live: true, generation: false}\nports: {room: 9700}\n"
        "location: {lat: 1.5, lon: 2.5}\n")
    (root / "alpha" / "publish-destinations.json").write_text(json.dumps([
        {"id": "vj", "name": "VJ", "live": {"enabled": True}}, {"id": "cloud", "headless": True}]))
    (root / "alpha" / "rooms.json").write_text(json.dumps({"_comment": "x", "studio": {"name": "Studio", "screens": ["vj"]}}))
    monkeypatch.setattr(smconfig, "DEPLOYMENTS_DIR", root)
    smconfig._cache.clear()
    yield root
    smconfig._cache.clear()


def test_env_reads_process_then_dotenv(monkeypatch, tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nA=one # trailing\nB='two words'\nexport C=three\nD=\nE=#x\n")
    monkeypatch.setattr(smconfig, "ENV_FILE", f)
    monkeypatch.delenv("A", raising=False)
    assert smconfig.env("A") == "one"
    assert smconfig.env("B") == "two words"
    assert smconfig.env("C") == "three"
    assert smconfig.env("D", "dflt") == "dflt"
    assert smconfig.env("E") == ""
    monkeypatch.setenv("A", "proc")
    assert smconfig.env("A") == "proc"


def test_unselected_deployment_is_an_error(no_env_file):
    with pytest.raises(smconfig.DeploymentError, match="SM_DEPLOYMENT is not set"):
        smconfig.deployment_id()


def test_unknown_deployment_is_an_error(no_env_file, monkeypatch):
    monkeypatch.setenv("SM_DEPLOYMENT", "no-such-place")
    with pytest.raises(smconfig.DeploymentError, match="does not exist"):
        smconfig.deployment_id()
    monkeypatch.setenv("SM_DEPLOYMENT", "../etc")
    with pytest.raises(smconfig.DeploymentError, match="not a valid"):
        smconfig.deployment_id()


def test_manifest_resolution(fake_deployments, monkeypatch):
    monkeypatch.setenv("SM_DEPLOYMENT", "alpha")
    dep = smconfig.deployment()
    assert dep.id == "alpha" and dep.name == "Alpha"
    assert dep.enabled("live") and not dep.enabled("generation") and not dep.enabled("dot")
    assert dep.capabilities["dot"] is False
    with pytest.raises(KeyError):
        dep.enabled("typo")
    assert dep.port("room") == 9700 and dep.port("flask") == 5000
    assert smconfig.url("room") == "http://127.0.0.1:9700"
    assert dep.url("overlay_ws", "ws") == "ws://127.0.0.1:8765"
    assert dep.location == {"lat": 1.5, "lon": 2.5}
    assert smconfig.live_screens() == ["vj"]
    assert list(smconfig.rooms()) == ["studio"]
    assert smconfig.default_room() == "studio"
    assert smconfig.path("room", "room.yaml") == fake_deployments / "alpha" / "room" / "room.yaml"
    with pytest.raises(smconfig.DeploymentError):
        smconfig.path("..", "..", "etc")


def test_manifest_reloads_when_changed(fake_deployments, monkeypatch):
    import os
    import time

    monkeypatch.setenv("SM_DEPLOYMENT", "alpha")
    assert not smconfig.enabled("dot")
    mf = fake_deployments / "alpha" / "deployment.yaml"
    mf.write_text(mf.read_text().replace("live: true", "live: true, dot: true"))
    st = mf.stat()
    os.utime(mf, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    time.sleep(0.01)
    assert smconfig.enabled("dot")


def test_manifest_id_must_match_directory(fake_deployments, monkeypatch):
    (fake_deployments / "beta").mkdir()
    (fake_deployments / "beta" / "deployment.yaml").write_text("id: alpha\n")
    with pytest.raises(smconfig.DeploymentError, match="does not match"):
        smconfig.load("beta")


def test_deep_merge():
    base = {"a": {"b": 1, "c": [1, 2]}, "d": 4}
    over = {"a": {"c": [3], "e": 5}, "f": None}
    assert smconfig.deep_merge(base, over) == {"a": {"b": 1, "c": [3], "e": 5}, "d": 4, "f": None}
    assert base == {"a": {"b": 1, "c": [1, 2]}, "d": 4}


def test_load_layered_yaml(tmp_path):
    b, o = tmp_path / "b.yaml", tmp_path / "o.yaml"
    b.write_text("x: {y: 1, z: 2}\nl: [1]\n")
    o.write_text("x: {z: 3}\n")
    assert smconfig.load_layered_yaml(b, o) == {"x": {"y": 1, "z": 3}, "l": [1]}
    assert smconfig.load_layered_yaml(b, tmp_path / "none.yaml") == {"x": {"y": 1, "z": 2}, "l": [1]}


@pytest.mark.parametrize("dep_id", smconfig.available())
def test_shipped_deployments_check_clean(dep_id):
    dep = smconfig.load(dep_id)
    problems, _ = smcheck.check(dep)
    assert problems == []


def test_finsbury_has_every_moved_file():
    dep = smconfig.load("finsbury")
    for new in MOVES.values():
        assert dep.path(*new.split("/")).is_file(), new


def test_finsbury_show_layer_holds_exactly_the_site_keys():
    import yaml

    site = yaml.safe_load(smconfig.load("finsbury").path("show.yaml").read_text())
    base = yaml.safe_load((smconfig.CONFIG_DIR / "room" / "show.yaml").read_text())

    def get(d, dotted):
        for k in dotted.split("."):
            d = d[k]
        return d

    def has(d, dotted):
        try:
            get(d, dotted)
            return True
        except (KeyError, TypeError):
            return False

    for key in SHOW_SITE_KEYS:
        assert has(site, key), f"finsbury show.yaml lacks {key}"
        assert not has(base, key), f"config/room/show.yaml still holds the site fact {key}"


def test_cli(monkeypatch):
    env = {"SM_DEPLOYMENT": "finsbury", "PATH": "/usr/bin:/bin"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "smconfig", *a], cwd=REPO, env=env,  # noqa: E731
                                    capture_output=True, text=True)
    assert run("get", "id").stdout.strip() == "finsbury"
    assert run("get", "ports.room").stdout.strip() == "8700"
    assert run("get", "capabilities.dot").stdout.strip() in ("true", "false")
    assert run("get", "path.lights/patch.yaml").stdout.strip().endswith("deployments/finsbury/lights/patch.yaml")
    assert run("enabled", "room").returncode == 0
    assert run("get", "nope.nope").returncode == 1
    assert json.loads(run("dump").stdout)["id"] == "finsbury"
    r = subprocess.run([sys.executable, "-m", "smconfig", "get", "id"], cwd=REPO, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "SM_DEPLOYMENT": "nope"})
    assert r.returncode == 2 and "does not exist" in r.stderr


def _alpha_problems(root, monkeypatch, manifest_extra: str = "", modes: dict | None = None):
    m = root / "alpha" / "deployment.yaml"
    m.write_text(m.read_text() + manifest_extra)
    if modes is not None:
        (root / "alpha" / "room").mkdir(exist_ok=True)
        (root / "alpha" / "room" / "modes.json").write_text(json.dumps(modes))
        (root / "alpha" / "room" / "room.yaml").write_text("{}\n")
    smconfig._cache.clear()
    monkeypatch.setenv("SM_DEPLOYMENT", "alpha")
    return smcheck.check(smconfig.deployment())


def test_check_wording_and_art_director(fake_deployments, monkeypatch):
    problems, _ = _alpha_problems(fake_deployments, monkeypatch,
                                  "wording: {art_space: a barn, art_spcae: typo, lights_menu: ''}\n"
                                  "art_director: {modes: [ambient], rooms: [studio, attic]}\n")
    assert any("unknown wording key 'art_spcae'" in p for p in problems)
    assert any("wording.lights_menu must be a non-empty string" in p for p in problems)
    assert any("art_director room 'attic'" in p for p in problems)
    assert not any("wording.art_space" in p or "key 'art_space'" in p for p in problems)


def test_check_kiosk_screens_must_be_a_mapping(fake_deployments, monkeypatch):
    problems, _ = _alpha_problems(fake_deployments, monkeypatch, "kiosk: {screens: [vj]}\n")
    assert any("kiosk.screens must be a mapping" in p for p in problems)


def test_check_catches_a_screen_typo_in_modes(fake_deployments, monkeypatch):
    m = fake_deployments / "alpha" / "deployment.yaml"
    m.write_text(m.read_text().replace("capabilities: {live: true", "capabilities: {room: true, live: true"))
    modes = {"default": "a", "modes": {"a": {"label": "A", "state": {"screens.jv": "art"}}},
             "transitions": [{"from": "*", "to": "*"}]}
    problems, _ = _alpha_problems(fake_deployments, monkeypatch, modes=modes)
    assert any("room/modes.json" in p and "jv" in p for p in problems), problems
    modes["modes"]["a"]["state"] = {"screens.vj": "art"}
    problems, _ = _alpha_problems(fake_deployments, monkeypatch, modes=modes)
    assert not any("room/modes.json" in p for p in problems), problems
