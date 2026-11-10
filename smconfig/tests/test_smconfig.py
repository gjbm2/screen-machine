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


def test_readme_wording_table_lists_every_wording_key():
    """deployments/README.md's "Site wording" table and check.WORDING_KEYS name the same keys (a new one goes in both)."""
    import re
    text = (REPO / "deployments" / "README.md").read_text()
    section = text.split("### Site wording", 1)[1].split("\n#", 1)[0]
    assert set(re.findall(r"^\| `([a-z_]+)` \|", section, re.M)) == set(smcheck.WORDING_KEYS)


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


# -- rooms with their own modes ("own_modes") and rooms outside the show ("show": false) --------------------------------

MODES_OK = {"default": "a", "modes": {"a": {"label": "A", "state": {"screens": "art"}}},
            "transitions": [{"from": "*", "to": "*"}]}


def _two_rooms(root, monkeypatch, back: dict, front: dict | None = None, files: dict | None = None,
               dests: list | None = None):
    """alpha with a front room (the default) and a back room, the room capability on and a valid modes.json."""
    a = root / "alpha"
    (a / "publish-destinations.json").write_text(json.dumps(dests or [
        {"id": "fs", "live": {"enabled": True}}, {"id": "bs", "live": {"enabled": True}}, {"id": "cloud"}]))
    (a / "rooms.json").write_text(json.dumps({"_comment": "x", "front": front or {"name": "Front", "screens": ["fs"]},
                                              "back": {"name": "Back", **back}}))
    m = a / "deployment.yaml"
    m.write_text(m.read_text().replace("capabilities: {live: true", "capabilities: {room: true, live: true")
                 + "default_room: front\n")
    (a / "room").mkdir(exist_ok=True)
    (a / "room" / "room.yaml").write_text("{}\n")
    (a / "room" / "modes.json").write_text(json.dumps(MODES_OK))
    (a / "services").mkdir(exist_ok=True)
    (a / "services" / "sm-stream.env").write_text("")
    for rel, doc in (files or {}).items():
        (a / rel).write_text(doc if isinstance(doc, str) else json.dumps(doc))
    smconfig._cache.clear()
    monkeypatch.setenv("SM_DEPLOYMENT", "alpha")
    return smconfig.deployment()


def test_room_helpers(fake_deployments, monkeypatch):
    dep = _two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"], "own_modes": True, "show": False})
    assert smconfig.own_mode_rooms(dep) == {"back": {"name": "Back", "screens": ["bs"], "show": False}}
    assert smconfig.out_of_show_rooms(dep) == ["back"]
    assert smconfig.show_screens(dep) == {"fs", "cloud"}
    assert smconfig.room_of_screen(dep, "bs") == "back" and smconfig.room_of_screen(None, "fs") == "front"
    assert smconfig.room_of_screen(dep, "cloud") is None
    assert smconfig.room_modes_path(dep, "back") == fake_deployments / "alpha" / "room" / "modes.back.json"
    with pytest.raises(ValueError):
        smconfig.room_modes_path(dep, "../x")
    dep = _two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"], "own_modes": "yes"})
    assert smconfig.own_mode_rooms(dep) == {} and smconfig.out_of_show_rooms(dep) == []
    assert smconfig.show_screens(dep) is None                       # no room out of the show: every screen, as before


@pytest.mark.parametrize("dep_id", smconfig.available())
def test_shipped_deployments_set_neither_key(dep_id):
    dep = smconfig.load(dep_id)
    assert smconfig.own_mode_rooms(dep) == {} and smconfig.out_of_show_rooms(dep) == []
    assert smconfig.show_screens(dep) is None
    assert all("own_modes" not in r and "show" not in r for r in smcheck.dump(dep)["rooms"].values())


def test_finsbury_gets_only_the_shared_modes_note():
    _, notes = smcheck.check(smconfig.load("finsbury"))
    assert [n for n in notes if "rooms.json" in n] == [
        'rooms.json: drawing-room and lab share the site\'s modes; give one "own_modes": true to switch it alone']
    for d in ("laptop", "example"):
        assert not [n for n in smcheck.check(smconfig.load(d))[1] if "rooms.json" in n]


def test_a_zone_with_its_modes_file_is_clean(fake_deployments, monkeypatch):
    dep = _two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"], "own_modes": True},
                     files={"room/modes.back.json": MODES_OK})
    problems, notes = smcheck.check(dep)
    assert problems == [], problems
    assert not any("share the site's modes" in n for n in notes)      # back switches alone now
    assert "room/modes.back.json" in smcheck.known_files(dep)
    assert smcheck.dump(dep)["rooms"]["back"]["own_modes"] is True
    assert "own_modes" not in smcheck.dump(dep)["rooms"]["front"]
    assert smcheck.dump(dep)["files"]["room/modes.back.json"]["exists"] is True


def test_check_a_zone_without_its_modes_file(fake_deployments, monkeypatch):
    problems, _ = smcheck.check(_two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"], "own_modes": True}))
    assert any(p.startswith("room/modes.back.json: missing") for p in problems), problems


def test_check_a_zone_modes_file_room_service_would_refuse(fake_deployments, monkeypatch):
    bad = {"default": "nope", "modes": {"a": {"label": "A", "state": {"screens.zz": "art"}}}}
    problems, _ = smcheck.check(_two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"], "own_modes": True},
                                           files={"room/modes.back.json": bad}))
    assert any(p.startswith("room/modes.back.json") for p in problems), problems
    problems, _ = smcheck.check(_two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"], "own_modes": True},
                                           files={"room/modes.back.json": "{not json"}))
    assert any(p.startswith("room/modes.back.json") for p in problems), problems


@pytest.mark.parametrize("key", ["own_modes", "show"])
def test_check_the_default_room_keeps_the_site(fake_deployments, monkeypatch, key):
    front = {"name": "Front", "screens": ["fs"], key: key == "own_modes"}
    problems, _ = smcheck.check(_two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"]}, front=front,
                                           files={"room/modes.front.json": MODES_OK}))
    assert any("'front' is the default room" in p and key in p for p in problems), problems


@pytest.mark.parametrize("back,front,shared", [
    ({"screens": ["fs", "bs"]}, None, "fs"),
    ({"screens": ["bs"], "blinds": ["ch02"]}, {"screens": ["fs"], "blinds": ["ch02"]}, "ch02"),
    ({"screens": ["bs"], "lights": ["hue.ne"]}, {"screens": ["fs"], "lights": ["hue.ne"]}, "hue.ne"),
    ({"screens": ["bs"], "effects": [{"power": "ball.main"}]}, {"screens": ["fs"], "effects": [{"power": "ball.main"}]},
     "ball.main"),
    ({"screens": ["bs"], "lights": ["light.par1"]}, {"screens": ["fs"], "lights": ["light"]}, "light.par1"),
])
def test_check_a_zone_shares_nothing_with_another_room(fake_deployments, monkeypatch, back, front, shared):
    problems, _ = smcheck.check(_two_rooms(fake_deployments, monkeypatch, {**back, "own_modes": True}, front=front,
                                           files={"room/modes.back.json": MODES_OK}))
    assert any("'back' has its own modes but shares" in p and shared in p for p in problems), problems


def test_check_a_zone_lists_no_whole_subsystem(fake_deployments, monkeypatch):
    problems, _ = smcheck.check(_two_rooms(fake_deployments, monkeypatch,
                                           {"screens": ["bs"], "lights": ["light"], "own_modes": True},
                                           files={"room/modes.back.json": MODES_OK}))
    assert any("lists the whole \"light\" subsystem" in p for p in problems), problems


@pytest.mark.parametrize("key,value", [("own_modes", "yes"), ("show", 0), ("show", None)])
def test_check_the_keys_are_booleans(fake_deployments, monkeypatch, key, value):
    problems, _ = smcheck.check(_two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"], key: value}))
    assert any(f"'back': \"{key}\" must be true or false" in p for p in problems), problems


def test_check_notes_rooms_sharing_modes_and_lights_outside_the_show(fake_deployments, monkeypatch):
    _, notes = smcheck.check(_two_rooms(fake_deployments, monkeypatch, {"screens": ["bs"]}))
    assert 'rooms.json: front and back share the site\'s modes; give one "own_modes": true to switch it alone' \
        in notes
    problems, notes = smcheck.check(_two_rooms(fake_deployments, monkeypatch,
                                               {"screens": ["bs"], "show": False, "lights": ["hue.x"]}))
    assert problems == [], problems
    assert any("'back' is outside the show" in n and "still play the show's look" in n for n in notes), notes
    assert smcheck.dump(smconfig.deployment())["rooms"]["back"]["show"] is False


# -- notes about this machine: files at old paths, and the frontend's build-time addresses ------------------------------

def _old_path_notes(notes):
    return [n for n in notes if n.startswith("files at old paths")]


def test_check_notes_files_at_old_paths(fake_deployments, monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / "config" / "room" / "rf" / "types").mkdir(parents=True)
    (root / "config" / "room" / "modes.json").write_text("{}")                  # a session followed an old note
    (root / "config" / "room" / "rf" / "lamp.json").write_text("{}")            # an app-written device, old dir
    (root / "config" / "room" / "rf" / "notes.txt").write_text("x")             # not JSON: not a device file
    (root / "config" / "room" / "rf" / "types" / "lamp.json").write_text("{}")  # shared types stay there
    (root / "routes" / "data").mkdir(parents=True)
    (root / "routes" / "data" / "rooms.json").symlink_to(root / "config" / "room" / "modes.json")   # a link: skipped
    monkeypatch.setenv("SM_DEPLOYMENT", "alpha")
    before, _ = smcheck.check(smconfig.deployment())
    monkeypatch.setattr(smconfig, "REPO_ROOT", root)
    problems, notes = smcheck.check(smconfig.deployment())
    assert problems == before, problems                                         # a note, never a problem
    assert _old_path_notes(notes) == [
        "files at old paths: config/room/modes.json (now deployments/alpha/room/modes.json); config/room/rf/lamp.json "
        "(now deployments/alpha/room/rf/lamp.json). Nothing reads them any more, so an edit there never takes effect. "
        "`.venv/bin/python scripts/migrate_deployment.py --deployment alpha` lists them, and --apply moves each one "
        "(or removes it when the deployment's copy is the same)"]
    for p in ((root / "config" / "room" / "modes.json"), (root / "config" / "room" / "rf" / "lamp.json")):
        p.unlink()
    assert _old_path_notes(smcheck.check(smconfig.deployment())[1]) == []


def test_this_checkout_has_no_files_at_old_paths():
    from smconfig.legacy import leftovers

    assert leftovers(REPO) == []


def _frontend(fake_deployments, monkeypatch, tmp_path, env_text: str, room: bool = True, selected: str = "alpha"):
    m = fake_deployments / "alpha" / "deployment.yaml"
    m.write_text(m.read_text().replace("capabilities: {room: true, ", "capabilities: {")
                 .replace("capabilities: {", "capabilities: {room: true, " if room else "capabilities: {"))
    smconfig._cache.clear()
    f = tmp_path / "frontend.env"
    f.write_text(env_text)
    monkeypatch.setattr(smconfig, "ENV_FILE", f)
    for k in ("VITE_API_URL", "VITE_WS_HOST", "VITE_ROOM_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SM_DEPLOYMENT", selected)
    dep = smconfig.load("alpha")
    assert dep.enabled("room") is room
    problems, notes = smcheck.check(dep)
    assert not [p for p in problems if "VITE_" in p], problems                 # notes only, never a problem
    return [n for n in notes if "VITE_" in n]


GOOD = ("VITE_API_URL=http://203.0.113.5:5000/api   # Backend API URL\nVITE_WS_HOST=ws://203.0.113.5:8765\n"
        "VITE_ROOM_URL=http://192.0.2.10:9700\n")                                # alpha's room port is 9700


def test_frontend_addresses_that_agree_say_nothing(fake_deployments, monkeypatch, tmp_path):
    assert _frontend(fake_deployments, monkeypatch, tmp_path, GOOD) == []
    no_port = GOOD.replace("http://203.0.113.5:5000/api", "https://screen.example/api")   # a proxy's default port
    assert _frontend(fake_deployments, monkeypatch, tmp_path, no_port) == []


def test_frontend_address_on_another_port(fake_deployments, monkeypatch, tmp_path):
    notes = _frontend(fake_deployments, monkeypatch, tmp_path, GOOD.replace(":9700", ":8700"))
    assert notes == [".env: VITE_ROOM_URL uses port 8700, but deployment.yaml's ports.room is 9700. The app is built "
                     "with it (npx vite build): make them agree and rebuild, unless something forwards 8700 to 9700"]
    notes = _frontend(fake_deployments, monkeypatch, tmp_path, GOOD.replace(":8765", ":x"))
    assert notes == [".env: VITE_WS_HOST has a port that isn't a number; the app is built with it (npx vite build)"]


def test_frontend_addresses_missing(fake_deployments, monkeypatch, tmp_path):
    notes = _frontend(fake_deployments, monkeypatch, tmp_path, "VITE_API_URL=http://h:5000/api\n")
    assert len(notes) == 1 and notes[0].startswith(".env lacks VITE_WS_HOST and VITE_ROOM_URL. The app is built "
                                                   "with them (npx vite build)")
    assert "Setting up a new deployment" in notes[0]
    notes = _frontend(fake_deployments, monkeypatch, tmp_path, "VITE_API_URL=http://h:5000/api\n"
                      "VITE_WS_HOST=ws://h:8765\n", room=False)                 # room off: no VITE_ROOM_URL needed
    assert notes == []


def test_frontend_addresses_of_another_deployments_machine_are_not_checked(fake_deployments, monkeypatch, tmp_path):
    (fake_deployments / "beta").mkdir()
    (fake_deployments / "beta" / "deployment.yaml").write_text("id: beta\n")
    assert _frontend(fake_deployments, monkeypatch, tmp_path, "", selected="beta") == []
    assert _frontend(fake_deployments, monkeypatch, tmp_path, "", selected="alpha") != []
