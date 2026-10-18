"""fixtures.py: the registry (README S5.2, S5.4). The real fixtures.yaml loads clean and matches patch.yaml; tmp files
for the load rules (problems, left out vs kept, universes), OFL types through the registry, selectors and groups,
latency, hue_settings, the hot reload and the last good view. Files go to ``tmp_path`` only.

``-s`` prints the costs (the rig tick's budget is 3 ms p95, README S5.3; a no-change poll is part of it)."""

from __future__ import annotations

import ast
import json
import os
import pathlib
import statistics
import time

import pytest
import yaml

from show_service import contract
from show_service import fixtures as fx
from show_service.tests.test_ofl import PAR, SPOT

SHOW = yaml.safe_load(contract.SHOW_FILE.read_text())
LAMPS = SHOW.get("lamps")


def bump(path: pathlib.Path, text: str) -> None:
    """Rewrite a file so its mtime (ns) and size both move, whatever the filesystem's clock resolution."""
    st = path.stat() if path.exists() else None
    path.write_text(text)
    if st:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def settle(r, **kw) -> bool:
    """Registry.poll after a file edit: the first poll only notes the change (README S5.14's debounce), the next
    one, seeing the same files, applies it."""
    assert r.poll(**kw) is False
    return r.poll(**kw)


@pytest.fixture
def rigdir(tmp_path):
    types = tmp_path / "types"
    (types / "ofl" / "test").mkdir(parents=True)
    (types / "ofl" / "test" / "par.json").write_text(json.dumps(PAR))
    (types / "ofl" / "test" / "spot.json").write_text(json.dumps(SPOT))
    return tmp_path


DMX = """
version: 1
outputs:
  dmx-a: {driver: artnet, enabled: false, node: node-1, port_address: 1}
  lamps: {driver: hue, enabled: false}
nodes:
  node-1: {match: {short_name: "eDMX1"}, last_ip: 192.0.2.10, failsafe: scene}
types:
  sm-hue: {source: builtin}
  par: {source: ofl, file: ofl/test/par.json, mode: "Pro 7"}
  spot: {source: ofl, file: ofl/test/spot.json, mode: 6ch}
fixtures:
  l1: {type: sm-hue, output: lamps, light: aaa, placement: above, zone: n, order: 2}
  l2: {type: sm-hue, output: lamps, light: bbb, placement: opposite, zone: n, order: 1, tags: [warm]}
  p1: {type: par, output: dmx-a, address: 1, tags: [ball, front], pin: {Strobe: 5}, wb: {white: 0.8},
       house: {intensity: 0.3, hex: "#FFAA77"}, curve: "gamma:2.2"}
  p2: {type: par, output: dmx-a, address: 8, tags: [ball], role: key}
  s1: {type: spot, output: dmx-a, address: 15, zone: s}
groups:
  ring: {select: "kind:lamp", order: [p2, l1]}
  balls: {select: "ball"}
"""


def load(tmp, text: str, **kw) -> contract.RigView:
    p = tmp / "fixtures.yaml"
    p.write_text(text)
    kw.setdefault("types_dir", tmp / "types")
    return fx.load(p, **kw)


# -- the real files ----------------------------------------------------------------------------------------------------
def test_the_real_fixtures_yaml_loads_clean_and_matches_patch_yaml():
    v = fx.load(lamps=LAMPS, looks=SHOW.get("looks"))
    assert v["error"] is None and v["problems"] == [], v["problems"]
    patch = yaml.safe_load(contract.PATCH_FILE.read_text())
    devices = patch["devices"]
    assert set(v["fixtures"]) == set(devices)
    type_of = {"screen": "sm-screen", "hue": "sm-hue"}
    for fid, d in devices.items():
        f = v["fixtures"][fid]
        assert f["label"] == d["label"] and f["screen"] == d.get("screen")
        assert f["light"] == d.get("light") and f["placement"] == d.get("placement")
        assert f["type"] == type_of[d["profile"]] and f["role"] is None and f["zone"]
        assert f["latency"]["source"]["rise"] == "unset"                     # nothing measured for a lamp yet
    assert all(o["enabled"] is False for o in v["outputs"].values())
    assert v["groups"]["around"] == ["hue.ne", "hue.se", "hue.sw", "hue.nw"]
    assert v["nodes"] == {} and sorted(v["types"]) == ["sm-hue", "sm-screen"]
    assert all(fx._is_id(x) for k in ("outputs", "types", "fixtures", "groups") for x in v[k])
    hs = fx.hue_settings(v["outputs"]["hue"], patch)
    assert hs == {**{k: patch["hue"][k] for k in fx.HUE_KEYS}, "streamer": patch["hue"].get("streamer", "room"),
                  "host": patch["hue"].get("host")}


def test_the_real_view_has_the_contract_shapes():
    v = fx.load()
    assert set(v) == set(contract.RigView.__annotations__)
    for o in v["outputs"].values():
        assert set(o) == set(contract.OutputConfig.__annotations__)
    for t in v["types"].values():
        assert set(t) == set(contract.TypeInfo.__annotations__) and t["kind"] in contract.FIXTURE_KINDS
    for f in v["fixtures"].values():
        assert set(f) == set(contract.FixtureInfo.__annotations__)
        assert set(f["latency"]) == set(contract.LatencyView.__annotations__)
    json.dumps(v)                                                              # served by GET /api/v1/rig


def test_builtin_types():
    assert fx.BUILTIN_TYPES["sm-screen"]["kind"] == "screen" and fx.BUILTIN_TYPES["sm-screen"]["attrs"] == ["colour"]
    assert fx.BUILTIN_TYPES["sm-hue"]["kind"] == "lamp"
    assert fx.BUILTIN_TYPES["sm-hue"]["attrs"] == ["intensity", "colour"]
    assert all(fx.BUILTIN_TYPES[t]["footprint"] == 0 and fx.BUILTIN_TYPES[t]["channels"] == []
               for t in ("sm-screen", "sm-hue"))


# -- OFL types through the registry ------------------------------------------------------------------------------------
def test_a_dmx_rig_loads_with_ofl_types(rigdir):
    v = load(rigdir, DMX, lamps=LAMPS)
    assert v["error"] is None and v["problems"] == [], v["problems"]
    par, spot = v["types"]["par"], v["types"]["spot"]
    assert par["footprint"] == 7 and par["file"] == "ofl/test/par.json" and par["mode"] == "Pro 7"
    assert spot["footprint"] == 6 and spot["mode"] == "6-channel" and spot["wheel"]
    p1 = v["fixtures"]["p1"]
    assert p1["kind"] == "lamp" and p1["pin"] == {"Strobe": 5} and p1["wb"] == {"white": 0.8}
    assert p1["house"] == {"intensity": 0.3, "hex": "#ffaa77"} and p1["curve"] == "gamma:2.2"
    assert p1["tags"] == ["ball", "front"] and p1["address"] == 1
    n = v["nodes"]["node-1"]
    assert n == {"id": "node-1", "match": {"short_name": "eDMX1"}, "last_ip": "192.0.2.10", "port": 6454,
                 "full_512": False, "failsafe": "scene", "virtual": False}
    o = v["outputs"]["dmx-a"]
    assert (o["driver"], o["node"], o["port_address"], o["built"], o["enabled"]) == ("artnet", "node-1", 1, True,
                                                                                      False)
    assert v["groups"] == {"ring": ["p2", "l1", "l2", "p1", "s1"], "balls": ["p1", "p2"]}


def test_type_overrides_emitters_and_curve(rigdir):
    text = DMX.replace('par: {source: ofl, file: ofl/test/par.json, mode: "Pro 7"}',
                       'par: {source: ofl, file: ofl/test/par.json, mode: "Pro 7", curve: linear,\n'
                       '        emitters: {red: {xy: [0.69, 0.30], Y: 0.2}, amber: {xy: [0.57, 0.42], Y: 0.3},\n'
                       '                   green: {xy: [2, 0.5], Y: 1}, plaid: {xy: [0.3, 0.3], Y: 1}}}')
    v = load(rigdir, text)
    par = v["types"]["par"]
    assert par["curve"] == "linear"
    assert par["emitters"] == {"red": {"xy": [0.69, 0.30], "Y": 0.2, "source": "type"}}
    probs = "\n".join(v["problems"])
    assert "types.par.emitters.amber: this type has no amber channel" in probs
    assert "types.par.emitters.green: needs xy" in probs and "types.par.emitters.plaid: not an emitter" in probs


def test_a_type_that_cant_be_read_is_left_out_with_its_fixtures(rigdir):
    text = DMX.replace('mode: "Pro 7"', 'mode: "Pro 9"')
    v = load(rigdir, text)
    assert "par" not in v["types"] and "p1" not in v["fixtures"] and "p2" not in v["fixtures"]
    probs = "\n".join(v["problems"])
    assert "types.par: mode 'Pro 9' isn't in the file" in probs and "types.par: can't be used: left out" in probs
    assert "fixtures.p1.type: 'par' isn't a usable type: left out" in probs
    assert "s1" in v["fixtures"] and v["groups"]["balls"] == []
    v2 = load(rigdir, DMX.replace("ofl/test/spot.json", "ofl/test/gone.json").replace(
        "ofl/test/par.json", "../../etc/passwd"))
    probs2 = "\n".join(v2["problems"])
    assert "types.spot: ofl/test/gone.json: file not found" in probs2
    assert "types.par.file: '../../etc/passwd' is outside" in probs2


def test_a_matrix_type_is_kept_inert(rigdir):
    (rigdir / "types" / "ofl" / "test" / "bar.json").write_text(json.dumps({
        "name": "Bar", "matrix": {"pixelCount": [2, 1, 1]}, "availableChannels": {},
        "templateChannels": {"Red $pixelKey": {"capability": {"type": "ColorIntensity", "color": "Red"}}},
        "modes": [{"name": "px", "channels": [{"insert": "matrixChannels", "repeatFor": "eachPixelABC",
                                               "channelOrder": "perPixel", "templateChannels": ["Red $pixelKey"]}]}]}))
    text = DMX.replace("fixtures:\n", "fixtures:\n  b1: {type: bar, output: dmx-a, address: 30}\n")
    v = load(rigdir, text.replace("types:\n", "types:\n  bar: {source: ofl, file: ofl/test/bar.json, mode: px}\n"))
    assert v["types"]["bar"]["footprint"] == 0 and v["types"]["bar"]["attrs"] == []
    assert "b1" in v["fixtures"]
    probs = "\n".join(v["problems"])
    assert "types.bar: mode 'px' uses pixel (matrix) channels" in probs
    assert "fixtures.b1: type bar has no DMX channels: it sends nothing" in probs


# -- problems: what is left out, what is kept --------------------------------------------------------------------------
def test_port_address_zero_is_refused(rigdir):
    v = load(rigdir, DMX.replace("port_address: 1}", "port_address: 0}"))
    assert v["outputs"]["dmx-a"]["port_address"] is None
    line = "outputs.dmx-a.port_address: 0 is room_service's virtual universe: refused (the output can't start)"
    assert line in v["problems"] and line in v["outputs"]["dmx-a"]["problems"]
    v2 = load(rigdir, DMX.replace("port_address: 1}", "port_address: 40000}"))
    assert v2["outputs"]["dmx-a"]["port_address"] is None and any("isn't 1..32767" in p for p in v2["problems"])


def test_a_footprint_past_512_is_left_out_and_overlaps_are_kept(rigdir):
    v = load(rigdir, DMX.replace("address: 15, zone: s", "address: 510, zone: s").replace("address: 8,",
                                                                                           "address: 5,"))
    assert "fixtures.s1: address 510 + footprint 6 runs past 512: left out" in v["problems"]
    assert "s1" not in v["fixtures"]
    assert ("fixtures.p2: channels 5-11 overlap fixtures.p1 (1-7): both kept; the later address wins on the shared "
            "channels") in v["problems"]
    assert {"p1", "p2"} <= set(v["fixtures"])


def test_overlaps_are_per_universe(rigdir):
    dmx_b = "\n  dmx-b: {driver: artnet, node: node-1, port_address: 2}"
    text = DMX.replace("  lamps: {driver: hue, enabled: false}", "  lamps: {driver: hue, enabled: false}" + dmx_b)
    text = text.replace("p2: {type: par, output: dmx-a, address: 8", "p2: {type: par, output: dmx-b, address: 1")
    v = load(rigdir, text)
    assert not any("overlap" in p for p in v["problems"])
    same = text.replace("port_address: 2}", "port_address: 1}")
    v2 = load(rigdir, same)
    assert any("outputs.dmx-b: node node-1 Port-Address 1 is also outputs.dmx-a's universe" in p
               for p in v2["problems"])
    assert any("fixtures.p2: channels 1-7 overlap fixtures.p1" in p for p in v2["problems"])


def test_unknown_type_output_node_and_streamer(rigdir):
    text = DMX.replace("node: node-1, port_address", "node: node-9, port_address").replace(
        "  lamps: {driver: hue, enabled: false}", "  lamps: {driver: hue, enabled: false, streamer: show, gamma: 0.5,"
                                                  " area: abc}").replace(
        "s1: {type: spot, output: dmx-a", "s1: {type: spot, output: nowhere").replace(
        "fixtures:\n", "fixtures:\n  z9: {type: mystery}\n  z8: {label: no type}\n")
    v = load(rigdir, text)
    probs = "\n".join(v["problems"])
    assert "outputs.dmx-a.node: 'node-9' isn't in nodes: unset (the output can't start)" in probs
    assert v["outputs"]["dmx-a"]["node"] is None
    assert "outputs.lamps.streamer: room_service's switch" in probs
    assert v["outputs"]["lamps"]["settings"] == {"gamma": 0.5, "area": "abc"}    # unknown keys -> the driver's
    assert "fixtures.s1.output: 'nowhere' isn't an output: unrouted" in probs and v["fixtures"]["s1"]["output"] is None
    assert "fixtures.z9.type: 'mystery' isn't a usable type: left out" in probs and "z9" not in v["fixtures"]
    assert "fixtures.z8.type: required: left out" in probs


def test_required_routing_keys_leave_a_fixture_out(rigdir):
    text = DMX.replace("p2: {type: par, output: dmx-a, address: 8,", "p2: {type: par, output: dmx-a,").replace(
        "light: bbb, ", "")
    v = load(rigdir, text)
    assert "p2" not in v["fixtures"] and "l2" not in v["fixtures"]
    probs = "\n".join(v["problems"])
    assert "fixtures.p2: on an artnet output it needs address (1..512): left out" in probs
    assert "fixtures.l2: on a hue output it needs light (the bridge's light id): left out" in probs


def test_bad_values_drop_to_unset_with_problem_lines(rigdir):
    text = DMX.replace("dmx-a: {driver: artnet, enabled: false,", "dmx-a: {driver: artnet, enabled: yes please,"
                       " hits: maybe, rate_hz: -1, min_attack_ms: fast, latency: {colour_ms: -3, wobble: 1},").replace(
        "  lamps: {driver: hue, enabled: false}", "  lamps: {driver: hue, enabled: false}\n  odd: {driver: laser}"
    ).replace("p2: {type: par, output: dmx-a, address: 8, tags: [ball], role: key}",
              "p2: {type: par, output: dmx-a, address: 8, tags: [ball, Big Ball], role: villain, pin: {Nope: 1,"
              " Dimmer: 300}, wb: {red: -1}, house: {hex: orange}, curve: wiggly, order: soon, colour: red}")
    v = load(rigdir, text, lamps=LAMPS)
    o = v["outputs"]["dmx-a"]
    assert o["enabled"] is False and o["hits"] is None and o["rate_hz"] is None and o["min_attack_ms"] is None
    assert o["latency"] == {} and "odd" not in v["outputs"]
    p2 = v["fixtures"]["p2"]
    assert p2["tags"] == ["ball"] and p2["role"] is None and p2["pin"] == {} and p2["wb"] == {}
    assert p2["house"] is None and p2["curve"] is None and p2["order"] is None
    probs = "\n".join(v["problems"])
    for frag in ("outputs.dmx-a.enabled: 'yes please' isn't true or false: false", "outputs.dmx-a.hits",
                 "outputs.dmx-a.rate_hz", "outputs.dmx-a.min_attack_ms", "outputs.dmx-a.latency.colour_ms",
                 "outputs.dmx-a.latency.wobble: unknown key", "outputs.odd.driver: 'laser' isn't one of",
                 "fixtures.p2.tags: 'Big Ball' isn't a tag", "fixtures.p2.role: 'villain' isn't a show.yaml",
                 "fixtures.p2.pin.Nope: type par has no channel 'Nope'", "fixtures.p2.pin.Dimmer: 300 isn't 0..255",
                 "fixtures.p2.wb.red", "fixtures.p2.house: needs hex", "fixtures.p2.curve: 'wiggly'",
                 "fixtures.p2.order: 'soon'", "fixtures.p2.colour: unknown key: ignored"):
        assert frag in probs, frag
    assert all(o["problems"][i] in v["problems"] for i in range(len(o["problems"])))


def test_nodes_match_ip_and_the_loopback_rule(rigdir):
    text = DMX.replace('node-1: {match: {short_name: "eDMX1"}, last_ip: 192.0.2.10, failsafe: scene}',
                       'node-1: {match: {mac: "AA-BB-CC-DD-EE-01"}, last_ip: 127.0.0.1, full_512: true}\n'
                       '  node-2: {match: {serial: 1}, last_ip: not.an.ip, failsafe: smoke, port: 0}\n'
                       '  node-3: {last_ip: 127.0.0.1, port: 40001}\n'
                       '  BAD: {}')
    v = load(rigdir, text)
    n1, n2, n3 = (v["nodes"][k] for k in ("node-1", "node-2", "node-3"))
    assert n1["match"] == {"mac": "aa:bb:cc:dd:ee:01"} and n1["full_512"] is True
    assert n1["virtual"] is True                                    # 127.0.0.1:6454 is room's receiver: never sent to
    assert n2["match"] == {} and n2["last_ip"] is None and n2["failsafe"] is None and n2["port"] == 6454
    assert n3["virtual"] is False and n3["port"] == 40001           # a test's ephemeral sink is fine
    assert "BAD" not in v["nodes"]
    probs = "\n".join(v["problems"])
    assert "nodes.node-1: 127.0.0.1:6454 is room_service's own Art-Net receiver: never sent to" in probs
    for frag in ("nodes.node-2.match", "nodes.node-2.last_ip: 'not.an.ip'", "nodes.node-2.failsafe: 'smoke'",
                 "nodes.node-2.port: 0", "nodes.BAD: 'BAD' isn't an id"):
        assert frag in probs, frag


def test_shared_lights_version_and_unknown_top_keys(rigdir):
    text = DMX.replace("version: 1", "version: 2\nextras: {}").replace("light: bbb", "light: aaa")
    v = load(rigdir, text)
    probs = "\n".join(v["problems"])
    assert "version: 2 (this reader knows 1)" in probs and "extras: unknown key: ignored" in probs
    assert "fixtures.l2.light: also fixtures.l1's light on output lamps: both kept" in probs
    assert {"l1", "l2"} <= set(v["fixtures"])


def test_a_bad_file_gives_an_error_and_empty_maps(tmp_path):
    for text in ("fixtures: [unclosed", "- a list", ""):
        v = load(tmp_path, text)
        assert v["error"] and v["fixtures"] == {} and v["outputs"] == {}
    v = fx.load(tmp_path / "nope.yaml")
    assert "nope.yaml" in v["error"] and v["revision"] == ""


# -- selectors ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def view(rigdir):
    return load(rigdir, DMX, lamps=LAMPS)


def test_selector_terms(view):
    s = fx.select
    assert s("kind:lamp", view) == ["l2", "l1", "p1", "p2", "s1"]          # order first, then id
    assert s("*", view) == s("kind:lamp", view)
    assert s("output:dmx-a type:par", view) == ["p1", "p2"]                  # AND
    assert s("zone:n | zone:s", view) == ["l2", "l1", "s1"]                  # OR
    assert s("kind:lamp !tag:ball", view) == ["l2", "l1", "s1"]              # NOT
    assert s("ball !front", view) == ["p2"]                                  # bare words are tags
    assert s("id:p*", view) == ["p1", "p2"] and s("tag:fr*", view) == ["p1"]  # prefixes
    assert s("driver:hue", view) == ["l2", "l1"] and s("driver:artnet !type:par", view) == ["s1"]
    assert s("placement:above", view) == ["l1"] and s("screen:*", view) == []
    assert s("!*", view) == [] and s("kind:screen", view) == []


def test_roles_follow_placement_with_lamps(view):
    assert fx.select("role:key", view) == ["p2"]                              # own role only, without lamps
    with_lamps = fx.select("role:key", view, lamps=LAMPS)
    key_place = [p for p, r in LAMPS["placement_roles"].items() if r == "key"]
    expect = ["l2"] if "opposite" in key_place else []
    assert with_lamps == expect + ["p2"]
    others = fx.select("kind:lamp !role:key", view, lamps=LAMPS)
    assert "p2" not in others and set(others) | set(with_lamps) == set(fx.select("kind:lamp", view))


def test_group_terms_order_by_the_group(view):
    assert view["groups"]["ring"] == ["p2", "l1", "l2", "p1", "s1"]          # order lists first, the rest by order/id
    assert fx.select("group:ring", view) == ["p2", "l1", "l2", "p1", "s1"]
    assert fx.select("group:ring !type:spot", view) == ["p2", "l1", "l2", "p1"]
    assert fx.select("group:balls | group:ring", view) == ["p1", "p2", "l1", "l2", "s1"]
    assert fx.select("group:b*", view) == ["p1", "p2"]
    assert fx.select("group:nope", view) == []


def test_bad_selectors():
    for bad in ("", "   ", "a |", "| a", "a || b", "!", "!!a", "colour:red", "id:", "id:a*b", "kind:*x"):
        with pytest.raises(ValueError, match="bad_selector"):
            fx.parse_selector(bad)
    with pytest.raises(ValueError, match="bad_selector"):                    # looks.py writes the problem line
        fx.select("colour:red", {"fixtures": {"a": {}}, "groups": {}, "outputs": {}})
    assert fx.select("*", None) == []
    sel = fx.parse_selector("zone:n   kind:lamp|!ball")
    assert str(sel) == "zone:n kind:lamp | !tag:ball" and fx.parse_selector(str(sel)).clauses == sel.clauses
    assert hash(sel) == hash(fx.parse_selector("zone:n   kind:lamp|!ball"))


def test_groups_bad_selects_unknown_order_ids_and_cycles(rigdir):
    text = DMX.replace("  balls: {select: \"ball\"}",
                       "  balls: {select: \"ball\", order: [ghost, p2]}\n"
                       "  broken: {select: \"colour:red\"}\n  empty: {}\n"
                       "  a: {select: \"group:b | id:l1\"}\n  b: {select: \"group:a\"}\n"
                       "  front: {select: \"group:balls front\"}")
    v = load(rigdir, text)
    probs = "\n".join(v["problems"])
    assert v["groups"]["balls"] == ["p2", "p1"] and "groups.balls.order: 'ghost' isn't a fixture: skipped" in probs
    assert "broken" not in v["groups"] and "groups.broken.select: bad_selector" in probs
    assert "empty" not in v["groups"] and "groups.empty.select: required" in probs
    assert "refers to itself through group:" in probs
    assert v["groups"]["a"] == ["l1"] and v["groups"]["front"] == ["p1"]


# -- latency -----------------------------------------------------------------------------------------------------------
EDGE = {"rise": {"t10": 100.0, "t50": 180.0}, "fall": {"t10": 40.0, "t50": 80.0}, "trim_ms": 0.0}


def out(**kw) -> contract.OutputConfig:
    base = {"id": "o", "driver": "artnet", "enabled": False, "built": True, "node": None, "port_address": None,
            "latency_key": "o", "latency": {}, "hits": None, "min_attack_ms": None, "rate_hz": None, "settings": {},
            "problems": []}
    return {**base, **kw}


def test_latency_order_per_fixture_then_output_then_hand_set_then_unset():
    doc = {"outputs": {"f": {"intensity": EDGE}, "o": {"intensity": {"rise": {"t10": 10.0, "t50": 50.0},
                                                                     "fall": {"t10": 5.0, "t50": 9.0}}}}}
    hand = out(latency={"intensity": {"rise_ms": 7.0, "fall_ms": 3.0}, "colour_ms": 11.0, "position_ms": 13.0})
    lv = fx.resolve_latency("f", hand, doc, align_pct=25, min_visible_ms=40)
    assert lv["rise_ms"] == pytest.approx(100 + 80 * 15 / 40)
    assert lv["source"]["rise"] == "latency.json outputs.f.intensity"
    assert lv["fall_ms"] == pytest.approx(40 + 40 * 15 / 40)
    assert (lv["colour_ms"], lv["source"]["colour"]) == (11.0, "fixtures.yaml")
    assert (lv["position_ms"], lv["source"]["position"]) == (13.0, "fixtures.yaml")
    lv2 = fx.resolve_latency("g", hand, doc, align_pct=25, min_visible_ms=40)       # no per-lamp entry
    assert lv2["rise_ms"] == pytest.approx(25.0) and lv2["source"]["rise"] == "latency.json outputs.o.intensity"
    lv3 = fx.resolve_latency("g", hand, {}, align_pct=25, min_visible_ms=40)
    assert (lv3["rise_ms"], lv3["fall_ms"], lv3["source"]["rise"]) == (7.0, 3.0, "fixtures.yaml")
    lv4 = fx.resolve_latency("g", None, None)
    assert lv4["rise_ms"] == 0 and set(lv4["source"].values()) == {"unset"} and lv4["min_pulse_ms"] == fx.MIN_VISIBLE_MS


def test_latency_align_pct_trim_brightness_and_the_colour_fallback():
    doc = {"outputs": {"tv": {"brightness": {**EDGE, "trim_ms": -5.0}}}}
    at10 = fx.resolve_latency("x", out(latency_key="tv"), doc, align_pct=10, min_visible_ms=40)
    at50 = fx.resolve_latency("x", out(latency_key="tv"), doc, align_pct=50, min_visible_ms=40)
    assert at10["rise_ms"] == pytest.approx(95.0) and at50["rise_ms"] == pytest.approx(175.0)   # trim applies
    assert at10["source"]["rise"] == "latency.json outputs.tv.brightness"
    assert at10["colour_ms"] == at10["rise_ms"] and "(the intensity rise)" in at10["source"]["colour"]
    doc["outputs"]["tv"]["colour"] = {"rise": {"t10": 20.0, "t50": 60.0}}
    c = fx.resolve_latency("x", out(latency_key="tv"), doc, align_pct=30, min_visible_ms=40)
    assert c["colour_ms"] == pytest.approx(40.0) and c["source"]["colour"] == "latency.json outputs.tv.colour"
    only50 = {"outputs": {"x": {"intensity": {"rise": {"t50": 70.0}}}}}
    assert fx.resolve_latency("x", None, only50)["rise_ms"] == 70.0


def test_min_pulse_ms():
    doc = {"outputs": {"f": {"intensity": {"rise": {"t10": 300.0, "t50": 300.0}, "fall": {"t10": 100.0,
                                                                                         "t50": 100.0}}}}}
    lv = fx.resolve_latency("f", None, doc, min_visible_ms=40)
    assert lv["min_pulse_ms"] == pytest.approx(300 - 100 + 40)
    fast_rise = {"outputs": {"f": {"intensity": {"rise": {"t10": 10.0, "t50": 10.0}, "fall": {"t10": 90.0,
                                                                                              "t50": 90.0}}}}}
    assert fx.resolve_latency("f", None, fast_rise, min_visible_ms=40)["min_pulse_ms"] == 40


def test_latency_reaches_the_view(rigdir):
    lat = {"outputs": {"l1": {"intensity": EDGE}, "dmx-a": {"intensity": {"rise": {"t10": 2.0, "t50": 6.0}}}}}
    v = load(rigdir, DMX, latency=lat, looks={"align_pct": 50, "min_visible_ms": 10})
    assert v["fixtures"]["l1"]["latency"]["rise_ms"] == 180.0
    assert v["fixtures"]["p1"]["latency"]["rise_ms"] == 6.0
    assert v["fixtures"]["l2"]["latency"]["source"]["rise"] == "unset"
    bad = load(rigdir, DMX, looks={"align_pct": 400, "min_visible_ms": "x"})
    assert any("looks.align_pct" in p for p in bad["problems"]) and any("looks.min_visible_ms" in p
                                                                         for p in bad["problems"])


# -- hue_settings ------------------------------------------------------------------------------------------------------
def test_hue_settings_over_a_fake_patch():
    patch = {"hue": {"bridge": "b1", "area": "a1", "gamma": 0.6, "stop_after_s": 10, "host": "h"}}
    assert fx.hue_settings(out(driver="hue"), patch) == {"bridge": "b1", "area": "a1", "gamma": 0.6,
                                                         "stop_after_s": 10, "streamer": "room", "host": "h"}
    own = out(driver="hue", settings={"area": "a2", "gamma": 0.8, "stop_after_s": "soon"})
    s = fx.hue_settings(own, {"hue": {**patch["hue"], "streamer": "show"}})
    assert s == {"bridge": "b1", "area": "a2", "gamma": 0.8, "stop_after_s": 10, "streamer": "show", "host": "h"}
    # room's BridgeSecrets.load(hue.bridge, hue.host): patch.yaml's host only for patch.yaml's own bridge
    assert fx.hue_settings(out(driver="hue", settings={"bridge": "b2"}), patch)["host"] is None
    assert fx.hue_settings(None, None) == {"bridge": None, "area": None, "gamma": None, "stop_after_s": None,
                                           "streamer": "room", "host": None}


# -- the registry: hot reload and the last good view -------------------------------------------------------------------
@pytest.fixture
def reg(rigdir):
    (rigdir / "fixtures.yaml").write_text(DMX)
    (rigdir / "latency.json").write_text(json.dumps({"version": 2, "outputs": {}}))
    (rigdir / "patch.yaml").write_text("hue: {bridge: b1, area: a1, gamma: 0.6, stop_after_s: 10}\n")
    r = fx.Registry(rigdir / "fixtures.yaml", types_dir=rigdir / "types", latency_path=rigdir / "latency.json",
                    patch_path=rigdir / "patch.yaml")
    assert r.view() is None
    assert r.poll() is True
    return r, rigdir


def test_poll_reloads_only_on_change(reg):
    r, d = reg
    v = r.view()
    assert r.poll() is False and r.view() is v
    assert r.poll(force=True) is True and r.view() is not v
    assert r.hue_settings(r.view()["outputs"]["lamps"])["streamer"] == "room"
    bump(d / "patch.yaml", "hue: {bridge: b1, area: a1, gamma: 0.6, stop_after_s: 10, streamer: show}\n")
    assert settle(r) is True and r.hue_settings(r.view()["outputs"]["lamps"])["streamer"] == "show"


def test_a_bad_edit_keeps_the_last_good_view_with_the_error(reg):
    r, d = reg
    good = r.view()
    bump(d / "fixtures.yaml", "fixtures: [oops")
    assert settle(r) is True
    bad = r.view()
    assert bad["fixtures"] is good["fixtures"] and bad["revision"] == good["revision"]
    assert "the last good registry stays in use" in bad["error"] and good["error"] is None   # never mutated
    assert r.poll() is False and r.poll(force=True) is False                 # the same error: nothing new
    bump(d / "fixtures.yaml", DMX.replace("address: 15", "address: 20"))
    assert settle(r) is True and r.view()["error"] is None
    assert r.view()["fixtures"]["s1"]["address"] == 20


def test_a_half_written_file_is_never_loaded(reg):
    """README S5.14: a poll that lands in the middle of an in-place save can read a truncated file that is still valid
    YAML (here: an output switched on, its fixtures cut off). A change is applied only when the next poll sees the
    same files, so the half-written one never becomes the view."""
    r, d = reg
    good = r.view()
    full = DMX.replace("lamps: {driver: hue, enabled: false}", "lamps: {driver: hue, enabled: true}")
    half = full[:full.index("fixtures:")]
    (d / "half.yaml").write_text(half)
    assert fx.load(d / "half.yaml", types_dir=d / "types")["error"] is None   # on its own it loads: ok, 0 fixtures
    bump(d / "fixtures.yaml", half)                                          # the poll sees the save half done
    assert r.poll() is False and r.view() is good
    bump(d / "fixtures.yaml", full)                                          # the save finished
    assert r.poll() is False and r.view() is good                            # a new change: seen once more
    assert r.poll() is True
    v = r.view()
    assert v["outputs"]["lamps"]["enabled"] is True and set(v["fixtures"]) == set(good["fixtures"])
    # show.yaml's inputs alone (not a file) still apply at once
    assert r.poll(looks={"align_pct": 50}) is True


def test_hot_reload_on_a_type_file_latency_and_looks(reg):
    r, d = reg
    assert r.view()["types"]["par"]["footprint"] == 7
    par = json.loads(json.dumps(PAR))
    par["modes"][1]["channels"] = par["modes"][1]["channels"][:6]             # Pro 7 loses its Strobe
    bump(d / "types" / "ofl" / "test" / "par.json", json.dumps(par))
    assert settle(r) is True and r.view()["types"]["par"]["footprint"] == 6
    bump(d / "latency.json", json.dumps({"version": 2, "outputs": {"l1": {"intensity": EDGE}}}))
    assert settle(r) is True and r.view()["fixtures"]["l1"]["latency"]["rise_ms"] == pytest.approx(130.0)
    assert r.poll(looks={"align_pct": 50}) is True
    assert r.view()["fixtures"]["l1"]["latency"]["rise_ms"] == pytest.approx(180.0)
    assert r.poll(looks={"align_pct": 50}) is False
    assert r.poll(looks={"align_pct": 50}, lamps=LAMPS) is True


def test_unreadable_side_files_are_problems_not_errors(reg):
    r, d = reg
    bump(d / "latency.json", "{not json")
    bump(d / "patch.yaml", "hue: [")
    assert settle(r) is True
    v = r.view()
    assert v["error"] is None and r.patch is None and r.latency is None
    assert any(p.startswith("latency.json") for p in v["problems"]) and any(p.startswith("patch.yaml")
                                                                            for p in v["problems"])


def test_no_good_view_yet_gives_the_error_view(tmp_path):
    r = fx.Registry(tmp_path / "fixtures.yaml", types_dir=tmp_path, latency_path=tmp_path / "l.json",
                    patch_path=tmp_path / "p.yaml")
    assert r.poll() is True
    assert r.view()["error"] and r.view()["fixtures"] == {}
    assert fx.summary(r.view())["counts"] == {"outputs": 0, "nodes": 0, "types": 0, "fixtures": 0, "groups": 0}
    bump(tmp_path / "fixtures.yaml", "version: 1\n")
    assert settle(r) is True and r.view()["error"] is None
    assert fx.summary(None)["error"] == "not loaded yet"


def test_poll_costs(reg):
    r, d = reg
    for _ in range(20):
        r.poll()
    t = []
    for _ in range(200):
        t0 = time.perf_counter()
        r.poll()
        t.append((time.perf_counter() - t0) * 1e6)
    reload_ = []
    for _ in range(20):
        t0 = time.perf_counter()
        r.poll(force=True)
        reload_.append((time.perf_counter() - t0) * 1e3)
    p95 = statistics.quantiles(t, n=20)[18]
    print(f"\nfixtures: poll, no change p95 {p95:.0f} us; a reload (5 fixtures, 2 OFL types) "
          f"p50 {statistics.median(reload_):.1f} ms")
    assert p95 < 3000                                                         # the rig tick's whole budget


# -- the owner's rule: config names live in config ---------------------------------------------------------------------
def _fixed_vocabulary() -> set[str]:
    words: set[str] = set(fx.BUILTIN_TYPES)                                   # builtin type ids are fixed words
    for v in vars(contract).values():
        if isinstance(v, tuple) and all(isinstance(x, str) for x in v):
            words |= set(v)
        ann = getattr(v, "__annotations__", None)
        if isinstance(v, type) and isinstance(ann, dict):
            words |= set(ann)
    return words


def _config_names() -> set[str]:
    names: set[str] = set()
    f = yaml.safe_load(contract.FIXTURES_FILE.read_text())
    for sec in ("outputs", "nodes", "types", "fixtures", "groups"):
        names |= set((f.get(sec) or {}).keys())
    for d in (f.get("fixtures") or {}).values():
        names |= {d[k] for k in ("zone", "screen", "placement", "role") if isinstance(d, dict) and d.get(k)}
    patch = yaml.safe_load(contract.PATCH_FILE.read_text())
    names |= set((patch.get("devices") or {}).keys())
    names |= set(((SHOW.get("lamps") or {}).get("roles") or {}).keys())
    names |= set(((SHOW.get("lamps") or {}).get("placement_roles") or {}).keys())
    return names - _fixed_vocabulary()


@pytest.mark.parametrize("module", ["fixtures.py", "ofl.py"])
def test_no_config_names_in_code(module):
    src = pathlib.Path(fx.__file__).parent / module
    names = _config_names()
    hits = [n.value for n in ast.walk(ast.parse(src.read_text()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in names]
    assert hits == [], f"{module} names config things in code: {sorted(set(hits))}"


# -- README S5.23: the generic types, the added file and the Registrar -------------------------------------------------
def test_generic_types_have_channels_labels_and_encode():
    from show_service import colour_mix
    for tid, (label, chans) in fx.GENERIC_TYPES.items():
        t = fx.BUILTIN_TYPES[tid]
        assert t["source"] == "builtin" and t["kind"] == "lamp" and fx.TYPE_LABELS[tid] == label
        assert t["footprint"] == len(chans) == len(t["channels"]) and f"({len(chans)} channel" in label
        assert [c["name"] for c in t["channels"]] == list(chans) and all(c["default"] == fx.GENERIC_DEFAULTS.get(c["name"], 0) for c in t["channels"])
        assert set(t) == set(contract.TypeInfo.__annotations__)
    assert fx.BUILTIN_TYPES["generic-dimmer"]["attrs"] == ["intensity"]
    assert fx.BUILTIN_TYPES["generic-mover"]["attrs"] == ["intensity", "colour", "pan", "tilt"]   # driven now
    assert [c["attr"] for c in fx.BUILTIN_TYPES["generic-mover"]["channels"]] == ["pan", "tilt", "intensity",
                                                                                "emitter", "emitter", "emitter"]
    f = {"id": "x", "wb": {}, "pin": {}, "curve": None}
    assert colour_mix.encode(f, fx.BUILTIN_TYPES["generic-rgbw"], 1.0, [1.0, 0.0, 0.0]) == bytes([255, 0, 0, 0])
    assert colour_mix.encode(f, fx.BUILTIN_TYPES["generic-rgbw"], 1.0, [1.0, 1.0, 1.0]) == bytes([0, 0, 0, 255])
    assert colour_mix.encode(f, fx.BUILTIN_TYPES["generic-drgbs"], 0.5, [0.0, 0.0, 1.0])[:4] == bytes([128, 0, 0, 255])
    assert colour_mix.encode(f, fx.BUILTIN_TYPES["generic-drgbs"], 1.0, [0.0, 0.0, 1.0])[4] == 0     # no strobe
    uv = colour_mix.encode(f, fx.BUILTIN_TYPES["generic-rgbwauv"], 1.0, [1.0, 0.5, 0.0])
    assert len(uv) == 6 and uv[5] == 0 and uv[0] == 255                                     # uv never takes part


HAND = """
version: 1
outputs:
  lamps: {driver: hue, enabled: false}
  dmx-a: {driver: artnet, enabled: false, node: node-1, port_address: 1}
nodes:
  node-1: {last_ip: 192.0.2.10}
types:
  sm-hue: {source: builtin}
  par: {source: ofl, file: ofl/test/par.json, mode: "Pro 7"}
fixtures:
  l1: {type: sm-hue, output: lamps, light: aaa, placement: above}
"""


@pytest.fixture
def hand(rigdir):
    (rigdir / "fixtures.yaml").write_text(HAND)
    return rigdir


def registrar(d, **kw):
    kw.setdefault("lamps", LAMPS)
    return fx.Registrar(d / "fixtures.yaml", types_dir=d / "types", **kw)


def added(d) -> dict:
    return yaml.safe_load((d / fx.ADDED_NAME).read_text())


def test_merge_added_hand_wins_and_reports():
    probs: list[str] = []
    h = {"version": 1, "fixtures": {"a": {"type": "t"}}, "outputs": {"o": {}}}
    a = {"version": 2, "fixtures": {"a": {"type": "other"}, "b": {"type": "t"}}, "nodes": {"n": {}}, "groups": {}}
    raw, ids = fx.merge_added(h, a, probs)
    assert raw["fixtures"] == {"a": {"type": "t"}, "b": {"type": "t"}} and raw["nodes"] == {"n": {}}
    assert ids == {"b"} and h["fixtures"] == {"a": {"type": "t"}}                    # never changes its inputs
    assert "fixtures.added.yaml: fixtures.a: also in fixtures.yaml: the hand file's is used" in probs
    assert any("version: 2" in p for p in probs) and any(p.startswith("fixtures.added.yaml: groups:") for p in probs)


def test_the_registry_merges_the_added_file_and_reloads_on_it(hand):
    r = fx.Registry(hand / "fixtures.yaml", types_dir=hand / "types", latency_path=hand / "l.json",
                    patch_path=hand / "p.yaml")
    assert r.added_path == hand / fx.ADDED_NAME and r.poll() is True and r.added_ids == frozenset()
    rev0 = r.view()["revision"]
    fid, probs, _ = registrar(hand).add({"label": "Ball spot", "type": "generic-rgbw", "node_ip": "192.0.2.10",
                                         "universe": 1, "address": 1})
    assert settle(r) is True
    v = r.view()
    assert v["error"] is None and v["problems"] == [] and v["revision"] != rev0
    assert v["fixtures"][fid]["output"] == "dmx-a" and r.added_ids == {fid}
    assert fx.load(hand / "fixtures.yaml", types_dir=hand / "types")["fixtures"].keys() == {"l1"}   # hand alone
    bump(hand / fx.ADDED_NAME, "fixtures: [oops")                       # a broken added file: the hand file alone
    assert settle(r) is True
    v = r.view()
    assert v["error"] is None and set(v["fixtures"]) == {"l1"} and r.added_ids == frozenset()
    assert any(p.startswith("fixtures.added.yaml: ") and "left out" in p for p in v["problems"])


def test_add_makes_the_node_output_and_type_once_and_never_touches_the_hand_file(hand):
    before = (hand / "fixtures.yaml").read_bytes()
    r = registrar(hand)
    fid, probs, view = r.add({"label": "Ball spot", "type": "generic-rgbw", "node_ip": " 192.168.1.50 ",
                              "universe": "2", "address": 1, "placement": "above", "zone": "north", "role": ""})
    assert fid == "dmx.ball-spot" and probs == []
    doc = added(hand)
    assert list(doc) == ["version", "nodes", "outputs", "types", "fixtures"] and doc["version"] == 1
    assert doc["nodes"] == {"node-192-168-1-50": {"last_ip": "192.168.1.50"}}
    assert doc["outputs"] == {"dmx-192-168-1-50-u2": {"driver": "artnet", "enabled": True,
                                                      "node": "node-192-168-1-50", "port_address": 2}}
    assert doc["types"] == {"generic-rgbw": {"source": "builtin"}}
    assert doc["fixtures"][fid] == {"label": "Ball spot", "type": "generic-rgbw", "output": "dmx-192-168-1-50-u2",
                                    "address": 1, "placement": "above", "zone": "north"}
    assert (hand / fx.ADDED_NAME).read_text().startswith(fx.ADDED_HEADER)
    assert view["fixtures"][fid]["kind"] == "lamp" and view["outputs"]["dmx-192-168-1-50-u2"]["enabled"] is True
    # a second light on the same node and universe reuses both; the same label gets a new id; an overlap is a line
    fid2, probs2, _ = r.add({"label": "Ball spot", "type": "par", "node_ip": "192.168.1.50", "universe": 2,
                             "address": 3})
    assert fid2 == "dmx.ball-spot-2" and len(added(hand)["outputs"]) == 1 and len(added(hand)["nodes"]) == 1
    assert probs2 == ["fixtures.dmx.ball-spot-2: channels 3-9 overlap fixtures.dmx.ball-spot (1-4): both kept; the "
                      "later address wins on the shared channels"]
    assert "par" not in added(hand)["types"]                              # the hand file's OFL type: used as is
    # the hand file's node and output (switched off there) are reused, with a line saying where to switch it on
    fid3, probs3, _ = r.add({"label": "Hand node", "type": "generic-rgb", "node_ip": "192.0.2.10", "universe": 1,
                             "address": 100})
    assert added(hand)["fixtures"][fid3]["output"] == "dmx-a" and "node-1" not in added(hand)["nodes"]
    assert probs3 == ["outputs.dmx-a: switched off in config/room/fixtures.yaml (enabled: false): switch it on there "
                      "and this light sends"]
    assert (hand / "fixtures.yaml").read_bytes() == before
    assert not [p for p in hand.iterdir() if p.name.endswith(".tmp")]
    v = fx.load(hand / "fixtures.yaml", types_dir=hand / "types", added_path=hand / fx.ADDED_NAME)
    assert {fid, fid2, fid3} <= set(v["fixtures"])


@pytest.mark.parametrize("body,code,where", [
    ("not a mapping", "bad_request", "The body"),
    ({"type": "generic-rgb", "node_ip": "192.0.2.9", "universe": 1, "address": 1}, "bad_request", "label"),
    ({"label": "x", "type": "sm-hue", "node_ip": "192.0.2.9", "universe": 1, "address": 1}, "unknown_type", "type"),
    ({"label": "x", "type": "nope", "node_ip": "192.0.2.9", "universe": 1, "address": 1}, "unknown_type", "type"),
    ({"label": "x", "type": "generic-rgb", "node_ip": "127.0.0.1", "universe": 1, "address": 1}, "bad_request",
     "node_ip"),
    ({"label": "x", "type": "generic-rgb", "node_ip": "lamp.local", "universe": 1, "address": 1}, "bad_request",
     "node_ip"),
    ({"label": "x", "type": "generic-rgb", "node_ip": "192.0.2.9", "universe": 0, "address": 1}, "bad_request",
     "universe"),
    ({"label": "x", "type": "generic-rgb", "node_ip": "192.0.2.9", "universe": 1, "address": 513}, "bad_request",
     "address"),
    ({"label": "x", "type": "generic-rgb", "node_ip": "192.0.2.9", "universe": 1, "address": 1, "zone": 5},
     "bad_request", "zone"),
    ({"label": "x", "type": "par", "node_ip": "192.0.2.9", "universe": 1, "address": 510}, "not_added",
     "That light can't be added as asked: fixtures.dmx.x: address 510 + footprint 7 runs past 512: left out"),
])
def test_add_refuses_and_writes_nothing(hand, body, code, where):
    with pytest.raises(fx.Refused) as e:
        registrar(hand).add(body)
    assert e.value.code == code and where in e.value.detail, e.value.detail
    assert str(e.value).startswith(f"{code}: ")
    assert not (hand / fx.ADDED_NAME).exists()
    if code == "unknown_type":
        assert [c["id"] for c in e.value.extra["choices"]][:2] == ["generic-dimmer", "generic-rgb"]
        assert "par" in [c["id"] for c in e.value.extra["choices"]]
    if code == "not_added":
        assert e.value.extra["problems"] == [where.split("asked: ")[1]]


def test_update_moves_clears_and_prunes(hand):
    r = registrar(hand)
    fid, _, _ = r.add({"label": "Spot", "type": "generic-drgb", "node_ip": "192.0.2.50", "universe": 1,
                       "address": 1, "zone": "south"})
    fid2, probs, view = r.update(fid, {"universe": 4, "zone": None, "label": "Ball spot", "colour": "red"})
    assert fid2 == fid and probs == ["colour: not something a light takes here: ignored"]
    doc = added(hand)
    assert doc["fixtures"][fid] == {"label": "Ball spot", "type": "generic-drgb", "output": "dmx-192-0-2-50-u4",
                                    "address": 1}
    assert list(doc["outputs"]) == ["dmx-192-0-2-50-u4"] and list(doc["nodes"]) == ["node-192-0-2-50"]  # u1 went
    assert view["fixtures"][fid]["label"] == "Ball spot" and view["fixtures"][fid]["zone"] is None
    r.update(fid, {"type": "generic-rgbw", "node_ip": "192.0.2.51"})
    doc = added(hand)
    assert list(doc["types"]) == ["generic-rgbw"] and list(doc["nodes"]) == ["node-192-0-2-51"]
    assert doc["outputs"]["dmx-192-0-2-51-u4"]["node"] == "node-192-0-2-51"
    with pytest.raises(fx.Refused) as e:
        r.update("l1", {"label": "x"})
    assert e.value.code == "in_hand_file"
    with pytest.raises(fx.Refused) as e:
        r.update("dmx.nope", {"label": "x"})
    assert e.value.code == "unknown_fixture" and e.value.extra["choices"] == [fid]
    before = (hand / fx.ADDED_NAME).read_bytes()
    with pytest.raises(fx.Refused):
        r.update(fid, {"address": 511})                                   # 4 channels from 511: past 512
    assert (hand / fx.ADDED_NAME).read_bytes() == before


def test_remove_prunes_what_only_it_used(hand):
    r = registrar(hand)
    a, _, _ = r.add({"label": "A", "type": "generic-rgb", "node_ip": "192.0.2.60", "universe": 1, "address": 1})
    b, _, _ = r.add({"label": "B", "type": "generic-rgb", "node_ip": "192.0.2.60", "universe": 2, "address": 1})
    c, _, _ = r.add({"label": "C", "type": "generic-rgb", "node_ip": "192.0.2.10", "universe": 1, "address": 9})
    assert r.remove(a) == {"removed": a, "outputs": ["dmx-192-0-2-60-u1"], "nodes": [], "types": []}
    assert r.remove(b) == {"removed": b, "outputs": ["dmx-192-0-2-60-u2"], "nodes": ["node-192-0-2-60"],
                           "types": []}
    assert r.remove(c) == {"removed": c, "outputs": [], "nodes": [], "types": ["generic-rgb"]}   # dmx-a is the hand's
    doc = added(hand)
    assert doc == {"version": 1, "nodes": {}, "outputs": {}, "types": {}, "fixtures": {}}
    with pytest.raises(fx.Refused) as e:
        r.remove(a)
    assert e.value.code == "unknown_fixture"
    with pytest.raises(fx.Refused) as e:
        r.remove("l1")
    assert e.value.code == "in_hand_file"


def test_a_broken_hand_or_added_file_refuses_writes(hand):
    (hand / fx.ADDED_NAME).write_text("fixtures: [oops")
    with pytest.raises(fx.Refused) as e:
        registrar(hand).add({"label": "x", "type": "generic-rgb", "node_ip": "192.0.2.9", "universe": 1, "address": 1})
    assert e.value.code == "added_unreadable" and (hand / fx.ADDED_NAME).read_text() == "fixtures: [oops"
    (hand / fx.ADDED_NAME).unlink()
    (hand / "fixtures.yaml").write_text("outputs: [oops")
    with pytest.raises(fx.Refused) as e:
        registrar(hand).add({"label": "x", "type": "generic-rgb", "node_ip": "192.0.2.9", "universe": 1, "address": 1})
    assert e.value.code == "fixtures_unreadable" and not (hand / fx.ADDED_NAME).exists()


def test_type_rows_and_labels(rigdir):
    rows = fx.type_rows(None)
    assert [r["id"] for r in rows] == list(fx.GENERIC_TYPES)
    assert {k: v for k, v in rows[2].items() if k != "caps"} == {
        "id": "generic-rgbw", "label": "RGBW light (4 channels)", "source": "builtin", "footprint": 4,
        "channels": ["Red", "Green", "Blue", "White"], "kind": "lamp"}
    assert rows[2]["caps"]["colour"] == "mix" and rows[2]["caps"]["moves"] is False
    assert next(r for r in rows if r["id"] == "generic-mover-full")["caps"]["moves"] is True
    assert all(set(r) == set(contract.RigTypeRow.__annotations__) for r in rows)
    v = load(rigdir, DMX)
    ids = [r["id"] for r in fx.type_rows(v)]
    assert ids[-2:] == ["par", "spot"] and "sm-hue" not in ids
    par = next(r for r in fx.type_rows(v) if r["id"] == "par")
    assert par["label"] == "par (Pro 7, 7 channels)" and par["source"] == "ofl" and len(par["channels"]) == 7
    assert fx.type_label(fx.BUILTIN_TYPES["sm-hue"]) == "Hue colour lamp" and fx.type_label(None) is None
    assert fx.role_of({"kind": "lamp", "role": "key"}) == "key" and fx.role_of({"kind": "screen"}) is None


def test_the_real_added_file_reads():
    """The repo's config/room/fixtures.added.yaml: the header, version 1, and the real registry with it merged."""
    p = contract.FIXTURES_ADDED_FILE
    assert p == fx.added_path_for(contract.FIXTURES_FILE)
    assert p.read_text().startswith(fx.ADDED_HEADER)
    doc = yaml.safe_load(p.read_text())
    assert doc["version"] == 1 and set(doc) <= {"version", *fx.ADDED_SECTIONS}
    v = fx.load(lamps=LAMPS, looks=SHOW.get("looks"), added_path=p)
    assert v["error"] is None and not [x for x in v["problems"] if x.startswith(fx.ADDED_NAME)], v["problems"]


def test_generic_mover_rests_at_centre():
    """Owner, 18 Oct: Pan and Tilt aren't driven yet, so a registered mover rests at centre, not aimed at 0, 0."""
    t = fx._generic("generic-mover", fx.GENERIC_TYPES["generic-mover"][1])
    d = {c["name"]: c["default"] for c in t["channels"]}
    assert d["Pan"] == 128 and d["Tilt"] == 128 and d["Dimmer"] == 0 and d["Red"] == 0


# -- docs/LIGHTS.md §2: the movers, capability selectors and the setup file --------------------------------------------
def test_generic_mover_full_is_sixteen_bit_and_rests_centred_and_open():
    t = fx.BUILTIN_TYPES["generic-mover-full"]
    ch = {c["name"]: c for c in t["channels"]}
    assert t["footprint"] == 13 and set(t) == set(contract.TypeInfo.__annotations__)
    assert (ch["Pan"]["fine"], ch["Tilt"]["fine"], ch["Pan"]["res"]) == (1, 3, 2)
    assert ch["Pan"]["ranges"][0]["dmx"] == [0, 65535] and ch["Pan fine"]["attr"] == "other"
    assert (ch["Pan"]["default"], ch["Tilt"]["default"], ch["Shutter"]["default"]) == (128, 128, 8)
    assert [c["attr"] for c in t["channels"]] == ["pan", "other", "tilt", "other", "pt_speed", "intensity", "shutter",
                                                  "wheel", "gobo", "gobo_spin", "prism", "zoom", "focus"]
    assert set(t["wheels"]) == {"Colour wheel", "Gobo"} and len(t["wheels"]["Gobo"]["slots"]) == fx.GENERIC_SLOTS
    assert all(set(a for r in c.get("ranges") or () for a in [r["attr"]]) <= set(contract.ATTRS)
               for c in t["channels"])
    assert all(c["attr"] in contract.CHANNEL_ATTRS for tid in fx.GENERIC_TYPES
               for c in fx.BUILTIN_TYPES[tid]["channels"])
    assert fx.generic_type("generic-mover-full") is t and fx.generic_type("generic-mover-full", {"gobo": 8}) is t
    six = fx.generic_type("generic-mover-full", {"gobo": 6, "colour": 3, "nope": 2})
    assert six is fx.generic_type("generic-mover-full", {"colour": 3, "gobo": 6}) and six is not t
    assert len(six["wheels"]["Gobo"]["slots"]) == 6 and len(six["wheels"]["Colour wheel"]["slots"]) == 3
    gob = {c["name"]: c for c in six["channels"]}["Gobo"]["ranges"]
    assert [r["dmx"] for r in gob if r["attr"] == "gobo"] == [[0, 9], [10, 20], [21, 31], [32, 41], [42, 52],
                                                             [53, 63]]
    assert fx.generic_type("generic-rgbw", {"gobo": 4}) is fx.BUILTIN_TYPES["generic-rgbw"]
    assert fx.generic_type("par") is None


def test_cap_side_and_height_selectors(view):
    s = fx.select
    assert s("cap:pan", view) == ["s1"] and s("cap:gobo", view) == ["s1"] and s("cap:gob*", view) == ["s1"]
    assert s("cap:shutter", view) == ["p1", "p2", "s1"] and s("kind:lamp !cap:pan", view) == ["l2", "l1", "p1", "p2"]
    assert s("cap:colour", view) == s("kind:lamp", view)
    north = {"l1", "s1"}

    def spatial(fid, key, value):
        return (key, value) == ("side", "north") and fid in north or (key, value) == ("height", "high") and fid == "p1"
    assert s("side:north", view) == [] and s("height:high", view) == []        # unplaced: on no side
    assert s("side:north", view, spatial=spatial) == ["l1", "s1"]
    assert s("cap:pan side:north", view, spatial=spatial) == ["s1"]
    assert s("height:high | side:north", view, spatial=spatial) == ["l1", "p1", "s1"]
    assert s("!side:north", view, spatial=spatial) == ["l2", "p1", "p2"]

    def broken(fid, key, value):
        raise RuntimeError("no room")
    assert s("side:north", view, spatial=broken) == []
    for bad in ("cap:wings", "height:loft", "cap:"):
        with pytest.raises(ValueError, match="bad_selector"):
            fx.parse_selector(bad)


def test_a_fixtures_yaml_group_can_select_by_capability(rigdir):
    groups = '  balls: {select: "ball"}'
    v = load(rigdir, DMX.replace(groups, groups + '\n  movers: {select: "cap:pan"}'))
    assert v["groups"]["movers"] == ["s1"] and v["problems"] == [], v["problems"]


@pytest.fixture
def store(tmp_path):
    return fx.SetupStore(tmp_path / fx.SETUP_NAME), tmp_path / fx.SETUP_NAME


def test_setup_store_patches_merges_and_removes(store):
    st, path = store
    assert st.read() == {} and not path.exists()
    got = st.patch("dmx.spot", {"home": {"pan": 10, "tilt": -20}, "beam": {"zoom": 18},
                                "slots": {"gobo": {"3": "Stars", 4: "Leaves"}, "count": {"gobo": 6}},
                                "positions": {"piano": {"pan": 40, "tilt": -60, "label": "The piano"}}})
    assert got == {"home": {"pan": 10.0, "tilt": -20.0}, "beam": {"zoom": 18.0},
                   "slots": {"gobo": {"3": "Stars", "4": "Leaves"}, "count": {"gobo": 6}},
                   "positions": {"piano": {"pan": 40.0, "tilt": -60.0, "label": "The piano"}}}
    text = path.read_text()
    assert text.startswith(fx.SETUP_HEADER) and yaml.safe_load(text) == {"version": 1, "fixtures": {"dmx.spot": got}}
    got = st.patch("dmx.spot", {"home": {"tilt": None}, "beam": {"focus": 0.3},
                                "slots": {"gobo": {"3": None, "5": "Dots"}},
                                "positions": {"door": {"pan": 0, "tilt": 0}}, "limits": {"pan": [-90, 90]}})
    assert got["home"] == {"pan": 10.0} and got["beam"] == {"zoom": 18.0, "focus": 0.3}
    assert got["slots"] == {"gobo": {"4": "Leaves", "5": "Dots"}, "count": {"gobo": 6}}
    assert set(got["positions"]) == {"piano", "door"} and got["limits"] == {"pan": [-90.0, 90.0]}
    got = st.patch("dmx.spot", {"positions": {"piano": None}, "limits": None, "slots": {"colour": {"2": {
        "name": "Deep red", "hex": "#CC0000"}}}, "strobe_hz_max": 8, "jog_flip": {"pan": True}})
    assert set(got["positions"]) == {"door"} and "limits" not in got and got["strobe_hz_max"] == 8.0
    assert got["slots"]["colour"] == {"2": {"name": "Deep red", "hex": "#cc0000"}} and got["jog_flip"] == {"pan": True}
    assert st.read()["dmx.spot"] == got and st.get("dmx.spot") == got and st.get("nope") == {}
    st.patch("other", {"range": {"pan": 630}})
    assert st.patch("dmx.spot", {k: None for k in fx.SETUP_KEYS}) == {}
    assert set(st.read()) == {"other"}                                        # nothing left: the id goes
    assert st.remove("other") is True and st.remove("other") is False and st.read() == {}


@pytest.mark.parametrize("patch, where", [
    ({"home": {"pan": "left"}}, "home.pan"), ({"limits": {"pan": [90, -90]}}, "limits.pan"),
    ({"limits": {"roll": [0, 1]}}, "limits.roll"), ({"max_speed": {"pan": 0}}, "max_speed.pan"),
    ({"beam": {"iris": 2}}, "beam.iris"), ({"beam": {"zoom": -1}}, "beam.zoom"),
    ({"slots": {"wheel9": {"1": "x"}}}, "slots.wheel9"), ({"slots": {"gobo": {"x": "y"}}}, "slots.gobo"),
    ({"slots": {"count": {"gobo": 99}}}, "slots.count.gobo"), ({"slots": {"colour": {"2": {"hex": "red"}}}}, "hex"),
    ({"positions": {"Over the sofa": {"pan": 1, "tilt": 2}}}, "positions"),
    ({"positions": {"sofa": {"pan": 1}}}, "positions.sofa"), ({"strobe_hz_max": -3}, "strobe_hz_max"),
    ({"jog_flip": {"pan": "yes"}}, "jog_flip.pan"), ({"wings": 2}, "wings"),
    ({"range": {"pan": float("nan")}}, "range.pan")])
def test_setup_store_refuses_and_writes_nothing(store, patch, where):
    st, path = store
    st.patch("dmx.spot", {"home": {"pan": 1}})
    before = path.read_bytes()
    with pytest.raises(fx.Refused) as e:
        st.patch("dmx.spot", patch)
    assert e.value.code == "bad_request" and where in e.value.detail and e.value.extra["problems"]
    assert path.read_bytes() == before


def test_setup_store_refuses_bad_ids_bodies_and_an_unreadable_file(store):
    st, path = store
    for fid, body in (("Bad Id", {}), ("ok", ["home"])):
        with pytest.raises(fx.Refused, match="bad_request"):
            st.patch(fid, body)
    path.write_text("fixtures: [oops")
    with pytest.raises(fx.Refused) as e:
        st.patch("dmx.spot", {"home": {"pan": 1}})
    assert e.value.code == "setup_unreadable" and path.read_text() == "fixtures: [oops"
    assert fx.slug_id("Over the Sofa!") == "over-the-sofa" and fx.slug_id("  ") == "place"
    assert fx.slug_id("Mirror ball") == "mirror-ball" and fx._is_id(fx.slug_id("x" * 99))


def test_the_registry_merges_the_setup_file_and_reloads_on_it(reg):
    r, d = reg
    path = d / fx.SETUP_NAME
    assert r.setup_path == path and all(f["setup"] == {} for f in r.view()["fixtures"].values())
    st = fx.SetupStore(path)
    st.patch("s1", {"home": {"pan": 15}, "slots": {"gobo": {"2": "Stars"}}})
    st.patch("ghost", {"home": {"pan": 1}})
    rev = r.view()["revision"]
    assert settle(r) is True
    v = r.view()
    assert v["fixtures"]["s1"]["setup"] == {"home": {"pan": 15.0}, "slots": {"gobo": {"2": "Stars"}}}
    assert v["fixtures"]["p1"]["setup"] == {} and v["revision"] != rev
    assert f"{fx.SETUP_NAME}: setup for ghost: no such light (kept)" in v["problems"]
    assert fx.caps_of(v["types"]["spot"], v["fixtures"]["s1"]["setup"])["gobo_slots"][1]["name"] == "Stars"
    bump(path, path.read_text().replace("pan: 15.0", "pan: left") + "")          # a hand edit: that key dropped
    assert settle(r) is True
    v = r.view()
    assert v["fixtures"]["s1"]["setup"] == {"slots": {"gobo": {"2": "Stars"}}}
    assert any(p.startswith(f"{fx.SETUP_NAME}: s1.home.pan") and p.endswith("ignored") for p in v["problems"])
    bump(path, "fixtures: [broken")                                              # unreadable: the last good stays
    assert settle(r) is True
    v = r.view()
    assert v["error"] is None and v["fixtures"]["s1"]["setup"] == {"slots": {"gobo": {"2": "Stars"}}}
    assert f"{fx.SETUP_NAME}: the lights' last good settings stay in use" in v["problems"]
    one = fx.load(d / "fixtures.yaml", types_dir=d / "types", setup_path=d / "nope.yaml")
    assert one["error"] is None and all(f["setup"] == {} for f in one["fixtures"].values())


def test_the_real_setup_file_reads():
    """The repo's config/room/fixtures.setup.yaml: the header, version 1, every id a light in the real registry."""
    p = contract.FIXTURES_SETUP_FILE
    assert p == fx.setup_path_for(contract.FIXTURES_FILE) and p.read_text().startswith(fx.SETUP_HEADER)
    doc = yaml.safe_load(p.read_text())
    assert doc["version"] == 1 and isinstance(doc["fixtures"], dict)
    v = fx.load(lamps=LAMPS, looks=SHOW.get("looks"), added_path=contract.FIXTURES_ADDED_FILE, setup_path=p)
    assert v["error"] is None and not [x for x in v["problems"] if x.startswith(fx.SETUP_NAME)], v["problems"]
