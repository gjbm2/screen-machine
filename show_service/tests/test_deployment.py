"""show_service on the deployments/ layer (deployments/README.md, smconfig): where every site file comes from, the
layered show.yaml (the shared base + this site's layer, re-read when either changes), what a site that leaves a site key
out gets (none, never the drawing room's), the hue and dmx capabilities, and missing optional files.

Everything is read only or on tmp files; nothing reaches a bridge, room_service or a port.
"""

from __future__ import annotations

import os
import time
import types

import pytest
import smconfig
import yaml

from show_service import artnet_out, config, contract as c, director, ensemble, fixtures as fx, flow, inputs
from show_service import library, outputs as om, situations, sources, space
from show_service.brain import Brain

DEP = smconfig.deployment()


# -- where the files are -----------------------------------------------------------------------------------------------
def test_site_files_come_from_the_deployment_and_shared_ones_stay_shared():
    lights = DEP.path("lights")
    for p in (c.PATCH_FILE, c.FIXTURES_FILE, c.FIXTURES_ADDED_FILE, c.FIXTURES_SETUP_FILE, c.SPACE_FILE,
              c.SPACE_ADDED_FILE):
        assert p.parent == lights, p
    assert c.LATENCY_FILE == DEP.path("calibration", "latency.json") == c.Config().latency_file
    assert c.AUDIO_CAL_FILE == DEP.path("calibration", "audio_cal.json") == config.Options(c.Config()).audio_cal_file
    assert c.SHOW_SITE_FILE == DEP.path("show.yaml")
    assert sources.SONOS_FILE == DEP.path("room", "sonos.json")
    assert artnet_out.PATCH_FILE == c.PATCH_FILE and artnet_out.OutConfig().patch_path == c.PATCH_FILE
    assert c.HUE_SECRETS_DIR == smconfig.secret("hue")
    for p in (c.VIBES_FILE, c.SHOW_FILE, c.FIXTURE_TYPES_DIR):           # shared behaviour, the same for every site
        assert p.parent == smconfig.shared("room"), p
    assert c.FIXTURES_FILE_WORDS == f"deployments/{DEP.id}/lights/fixtures.yaml"


def test_loopback_defaults_come_from_the_deployment_ports():
    assert c.ROOM_URL == smconfig.url("room") and artnet_out.ROOM_DMX_URL == smconfig.url("room") + "/api/v1/dmx"
    assert director.ART_SOURCES[0] == smconfig.url("flask") + "/output/{id}.jpg"
    assert c.BUS_PORT == smconfig.port("show") == c.Config().bus_bind[1]
    assert config.load_options([], {}).config.bus_bind == ("127.0.0.1", smconfig.port("show"))
    assert artnet_out.config_from_env({"SHOW_ROOM_DMX_URL": "http://h:1/x"}).room_dmx_url == "http://h:1/x"   # wins
    if DEP.id == "finsbury":                                     # the values the code had before deployments/
        assert c.ROOM_URL == "http://127.0.0.1:8700" and c.BUS_PORT == 8703
        assert director.ART_SOURCES[0] == "http://127.0.0.1:5000/output/{id}.jpg"


def test_the_app_written_headers_name_the_deployment_paths():
    for header, name in ((fx.ADDED_HEADER, "fixtures.added.yaml"), (fx.SETUP_HEADER, "fixtures.setup.yaml"),
                         (space.ADDED_HEADER, "space.added.yaml")):
        assert header.startswith(f"# deployments/<id>/lights/{name}:") and "config/room" not in header
        assert all(line.startswith("#") and len(line) <= 120 for line in header.splitlines())


# -- show.yaml: the base and the layer ---------------------------------------------------------------------------------
def _files(tmp_path, base: dict, site: dict | None):
    b = tmp_path / "show.yaml"
    b.write_text(yaml.safe_dump(base))
    s = tmp_path / "site.yaml"
    if site is not None:
        s.write_text(yaml.safe_dump(site))
    return b, s


def _touch(path, text: str) -> None:
    """Rewrite with a later mtime (some filesystems keep a coarse one)."""
    st = path.stat()
    path.write_text(text)
    os.utime(path, ns=(st.st_atime_ns, max(st.st_mtime_ns + 10_000_000, time.time_ns())))


def test_read_show_deep_merges_the_layer_and_an_absent_layer_is_none(tmp_path):
    b, s = _files(tmp_path, {"a": {"x": 1, "y": 2}, "senders": {"test-piano": {"hosts": ["localhost"]}}},
                  {"a": {"y": 3}, "senders": {"kiosk": {"hosts": ["k"]}}})
    assert config.read_show(b, s) == {"a": {"x": 1, "y": 3}, "senders": {"test-piano": {"hosts": ["localhost"]},
                                                                          "kiosk": {"hosts": ["k"]}}}
    assert config.read_show(b, s) == smconfig.load_layered_yaml(b, s)
    assert config.read_show(b, None) == config.read_show(b, tmp_path / "missing.yaml") == config.read_show(b)
    s.write_text("a: [1\n")
    with pytest.raises(ValueError, match="site.yaml isn't valid YAML"):
        config.read_show(b, s)
    with pytest.raises(OSError):
        config.read_show(tmp_path / "no-base.yaml", None)


def test_the_real_layered_settings_are_the_shared_file_with_this_sites_layer():
    got = config.read_show(c.SHOW_FILE)                          # the default: the deployment's layer
    assert got == smconfig.load_layered_yaml(c.SHOW_FILE, c.SHOW_SITE_FILE)
    base = yaml.safe_load(c.SHOW_FILE.read_text())
    for key in ("kiosk", "pc22"):
        assert key not in base["senders"]                        # site facts are never in the shared file
    if DEP.id == "finsbury":
        assert got["senders"]["kiosk"]["streams"] == ["room-mic", "room-piano"]
        assert got["situations"]["sonos"]["now_playing"] == "sonos.drawing-room.now_playing"


def test_which_layer_an_options_takes(tmp_path):
    own = tmp_path / "show.yaml"
    assert config.Options(c.Config()).show_site_file == c.SHOW_SITE_FILE        # over the shared base: the site's
    assert config.Options(c.Config(), show_file=own).show_site_file is None     # a test's own file is read alone
    assert config.Options(c.Config(), show_file=own, show_site_file=own).show_site_file == own
    assert config.load_options([], {}).show_site_file == c.SHOW_SITE_FILE
    o = config.load_options(["--show-file", str(own)], {})                      # --show-file overrides the base only
    assert o.show_file == own and o.show_site_file == c.SHOW_SITE_FILE
    assert config.load_options([], {"SHOW_FILE": str(own)}).show_site_file == c.SHOW_SITE_FILE
    assert config.load_options([], {"SHOW_SITE_FILE": str(tmp_path / "s.yaml")}).show_site_file == tmp_path / "s.yaml"
    assert config.load_options(["--show-site-file", "x.yaml"], {"SHOW_SITE_FILE": "off"}).show_site_file \
        == c.REPO_ROOT / "x.yaml"                                               # the flag over the env
    for off in ("off", "none", "OFF"):
        assert config.load_options([], {"SHOW_SITE_FILE": off}).show_site_file is None
        assert config.load_options(["--show-site-file", off], {}).show_site_file is None


def test_the_brain_reloads_when_either_layer_changes(tmp_path):
    b, s = _files(tmp_path, {"version": 1, "listener": {"cadence_s": 30}}, {"situations": {"light": {"sensor": "s1"}}})
    br = Brain(c.BrainContext(publish=lambda m: None), show_file=b, state_file=tmp_path / "state.json",
               show_site_file=s)
    first = br._read_settings(first=True)
    assert first == {"version": 1, "listener": {"cadence_s": 30}, "situations": {"light": {"sensor": "s1"}}}
    rev = br.settings_revision
    assert br.snapshot()["settings"]["site_path"] == str(s) and br._read_settings() is None      # unchanged
    _touch(s, yaml.safe_dump({"situations": {"light": {"sensor": "s2"}}}))                      # the layer changed
    assert br._read_settings()["situations"]["light"]["sensor"] == "s2" and br.settings_revision != rev
    _touch(b, yaml.safe_dump({"version": 1, "listener": {"cadence_s": 60}}))                     # the base changed
    assert br._read_settings()["listener"]["cadence_s"] == 60
    _touch(s, "situations: [oops\n")                                                             # a bad layer
    assert br._read_settings() is None and "keeping the last good" in br.settings_error
    assert str(s) in br.settings_error
    s.unlink()                                                                                   # gone: no layer
    assert br._read_settings() == {"version": 1, "listener": {"cadence_s": 60}} and br.settings_error is None


def test_the_brain_takes_the_site_layer_only_over_the_shared_base(tmp_path):
    ctx = c.BrainContext(publish=lambda m: None)
    assert Brain(ctx, state_file=tmp_path / "s.json").show_site_file == c.SHOW_SITE_FILE
    assert Brain(ctx, show_file=tmp_path / "show.yaml", state_file=tmp_path / "s.json").show_site_file is None


def test_inputs_reread_the_labels_when_the_layer_changes(tmp_path):
    b, s = _files(tmp_path, {"senders": {}}, {"senders": {"kiosk": {"label": "Room"}}})
    i = inputs.Inputs(lambda: {}, show_file=b, state_file=tmp_path / "input.json", show_site_file=s)
    assert i._settings()["senders"]["kiosk"]["label"] == "Room"
    _touch(s, yaml.safe_dump({"senders": {"kiosk": {"label": "Hall"}}}))
    assert i._settings()["senders"]["kiosk"]["label"] == "Hall"


def test_library_reads_the_piano_folders_through_the_layer(tmp_path, monkeypatch):
    b, s = _files(tmp_path, {"tests": {"piano": {"pieces": str(tmp_path / "a")}}},
                  {"tests": {"piano": {"pieces": str(tmp_path / "b")}}})
    monkeypatch.setattr(library, "SHOW_FILE", b)
    monkeypatch.setattr(library, "SHOW_SITE_FILE", s)
    monkeypatch.setattr(library, "_show_cache", {"stamp": None, "checked": -1e18, "piano": {}})
    assert library.file_dir() == tmp_path / "b"
    _touch(s, yaml.safe_dump({"tests": {"piano": {"pieces": str(tmp_path / "c")}}}))
    library._show_cache["checked"] = -1e18
    assert library.file_dir() == tmp_path / "c"
    s.unlink()
    library._show_cache["checked"] = -1e18
    assert library.file_dir() == tmp_path / "a"
    monkeypatch.setattr(library, "SHOW_SITE_FILE", None)                       # unset: smconfig's
    assert library._show_site() == c.SHOW_SITE_FILE


# -- a site that leaves the site keys out has none of them -------------------------------------------------------------
def test_absent_site_keys_mean_this_site_has_none():
    shared = config.read_show(c.SHOW_FILE, None)                 # what a deployment with no show.yaml layer runs on
    names = [x.name for x in sources.parse_senders(shared)[0]]
    assert "kiosk" not in names and "pc22" not in names
    assert all(x.on_demand for x in sources.parse_senders(shared)[0])          # only the test sources, on demand
    sit = situations.Situations(c.BrainContext(publish=lambda m: None))
    sit.configure(shared)
    assert sit.params("sonos_playing")["now_playing"] is None and sit.params("sonos_playing")["volume"] is None
    assert sit.params("piano_playing")["streams"] is None        # any stream, not the drawing room's room-piano
    assert sit.light.sensor is None and sit.params("light_below")["sensor"] is None
    probs: list[str] = []
    mb = flow.parse_settings((shared.get("lights") or {}).get("flow"), probs)["mirror_ball"]
    assert mb["enabled"] is False and mb["power_member"] is None and mb["direction_target"] is None \
        and mb["speed_target"] is None
    ens = ensemble.parse_settings(shared.get("ensemble"), probs)
    assert ens["players"]["zones"] == {}
    assert not [p for p in probs if "mirror_ball" in p or "zones" in p], probs
    for key in ("now_playing", "volume"):
        assert key not in ((shared.get("situations") or {}).get("sonos") or {})


# -- capabilities ------------------------------------------------------------------------------------------------------
class _HueDriver:
    caps = {"hits": False, "min_attack_ms": 0.0, "grid": "fixed", "rate_hz": 50.0, "keepalive_s": None}
    built: list = []

    def __init__(self, oid, **kw):
        _HueDriver.built.append(oid)
        self.output_id = oid

    def configure(self, o, rig):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class _Registry:
    def __init__(self, v):
        self.v = v

    def poll(self, looks=None, force=False, lamps=None):
        return True

    def view(self):
        return self.v


class _Engine:
    def configure(self, looks, rig, *, lamps=None):
        pass

    def set_look(self, **kw):
        pass


def test_hue_off_never_builds_a_hue_driver(monkeypatch):
    monkeypatch.setattr(om, "capability_on", lambda cap: cap != "hue")
    _HueDriver.built = []
    o = {"id": "o-hue", "driver": "hue", "enabled": True, "built": True, "node": None, "port_address": None,
         "latency_key": "o-hue", "latency": {}, "hits": None, "min_attack_ms": None, "rate_hz": None, "settings": {},
         "problems": []}
    v = {"revision": "r1", "path": "test", "error": None, "problems": [], "outputs": {"o-hue": o}, "nodes": {},
         "types": {}, "fixtures": {}, "groups": {}}
    rig = om.attach(c.BrainContext(publish=lambda m: None), registry=_Registry(v), engine=_Engine(),
                    driver_factories={"hue": lambda oid, **k: _HueDriver(oid, **k)})
    rig.configure({"rig": {"enabled": True}, "looks": {}, "lamps": {"roles": {}}})
    rig.start()
    try:
        assert _HueDriver.built == [] and rig.drivers_of("hue") == []
        st = rig.hue_status()
        assert st["state"] == "off" and st["detail"] == om.capability_off_words("hue")
        assert "hue capability is off" in st["detail"] and not rig.hue_running()
        assert rig.snapshot()["outputs"]["o-hue"]["state"] == "off"
    finally:
        rig.stop()


def test_dmx_off_sends_no_art_net_to_room(monkeypatch):
    from show_service import __main__ as main_mod
    made = []
    amod = types.SimpleNamespace(VirtualMaestro=lambda **kw: made.append(kw) or kw)
    me = types.SimpleNamespace(opts=types.SimpleNamespace(artnet_target=("127.0.0.1", 6454)), clock=None,
                               features=None, director=None, modules={})
    monkeypatch.setattr(main_mod, "dmx_enabled", lambda: False)
    assert main_mod.Show._virtual_maestro(me, amod, {})["target"] is None and "dmx" in me.modules["artnet_out"]
    monkeypatch.setattr(main_mod, "dmx_enabled", lambda: True)
    assert main_mod.Show._virtual_maestro(me, amod, {})["target"] == ("127.0.0.1", 6454)
    monkeypatch.undo()
    assert main_mod.dmx_enabled() == DEP.enabled("dmx")


# -- missing optional site files: none, never a crash ------------------------------------------------------------------
def test_missing_optional_site_files_mean_none(tmp_path):
    gone = tmp_path / "nothing-here"
    assert sources.load_sonos_outputs(gone / "sonos.json") == ([], []) and sources.sonos_rooms(gone / "x") == {}
    assert config.acoustic_offsets(gone / "latency.json") == {}
    assert config.GateOffsetWatch(gone / "audio_cal.json").poll(force=True) in ({}, None)
    st = space.Store(gone / "space.yaml")
    st.poll()
    assert st.view() is not None
    vm = artnet_out.VirtualMaestro(patch_path=gone / "patch.yaml", room_dmx_url=None)
    vm._check_patch(force=True)                                  # logged, kept as an error, no layout: no crash
    assert vm.patch_error is not None and vm.layout is None
    br = Brain(c.BrainContext(publish=lambda m: None), show_file=c.SHOW_FILE, state_file=tmp_path / "s.json",
               show_site_file=gone / "show.yaml")
    assert br._read_settings(first=True) == yaml.safe_load(c.SHOW_FILE.read_text()) and br.settings_error is None
