"""live_art.py: the scene pool from a mode's scene_tags, the director block and the look carry-over.

The art route (/api/v1/screens/{id}/art, the live art feed's frames with the TV's light mask) is tested in
test_live_art_feed.py; the old anchor fallback (serving ctl._shot mid-cut) is gone: the route never serves the
cut's anchor PNG, only the feed's own frames."""

import pytest

from room_service import live_art

CATALOG = {"presets": {"scenes": {
    "live-art": {"tags": {"energy": [0, 0.5], "modes": ["ambient"]}},
    "fractal-zoom": {"tags": {"energy": [0.1, 0.6]}},
    "odd-one": {"tags": {"modes": "party"}}}}}


class FakeStream:
    async def catalog(self):
        return CATALOG


async def test_auto_block():
    s = FakeStream()
    assert await live_art.auto_block({"scene": "fractal-zoom"}, None, s) is None          # no director asked for
    a = await live_art.auto_block({"scene": "live-art", "scene_tags": ["ambient"]}, None, s)
    assert a["enabled"] and a["pool"] == ["live-art"] and a["fx"]["enabled"]
    p = await live_art.auto_block({"scene": "warp-kaleido", "auto": True}, {"enabled": True, "fx": {"max": 3}}, s)
    assert p == {"enabled": True, "fx": {"max": 3}, "pool": "all"}     # explicit: a PATCH merges over a closed pool
    m = await live_art.auto_block({"scene": "x", "auto": {"fx": {"enabled": False}}}, live_art.DEFAULT_AUTO, s)
    assert m["fx"] == {"enabled": False, "max": 2, "pool": "all"}
    assert await live_art.auto_block({"scene": "x", "auto": False}, None, s) == {"enabled": False}
    e = await live_art.auto_block({"scene": "lonely", "scene_tags": ["nothing"]}, None, s)
    assert e["pool"] == ["lonely"]                                                        # never an empty pool
    assert live_art.tagged_scenes(CATALOG, ["party"]) == ["odd-one"]


async def test_display_default_applies_without_mode_keys():
    d = {"enabled": True, "fx": {"enabled": True, "max": 2, "pool": "all"}}
    assert await live_art.auto_block({"scene": "fractal-zoom"}, d, FakeStream()) == {**d, "pool": "all"}


def test_carried_look_drops_runtime_keys_and_auto_fx():
    sess = {"auto": {"enabled": True, "pool": ["live-art"], "armed": False, "eligible_at_pts": 3.0,
                     "dwell_s_effective": [40, 80], "fx": {"enabled": True, "pool": "all", "max": 2, "armed": True}},
            "direction": {"energy": 0.3}, "palette": None, "palette_transition_s": 4.0, "beat_follow": "extrapolate",
            "current_scene": "live-art",
            "fx": {"stack": [{"id": "fx-1", "preset": "bloom-soft", "level": 0.5, "origin": "manual", "label": "x"},
                             {"id": "fx-2", "effect": "chroma", "origin": "auto"}]}}
    c = live_art.carried_look(sess)
    assert c["auto"] == {"enabled": True, "pool": ["live-art"], "fx": {"enabled": True, "pool": "all", "max": 2}}
    assert c["look"] == {"direction": {"energy": 0.3}, "palette_transition_s": 4.0, "beat_follow": "extrapolate"}
    assert c["fx"] == [{"preset": "bloom-soft", "level": 0.5}] and c["scene"] == "live-art"


def test_normalize_desired_keeps_director_keys():
    from room_service.screens import normalize_desired
    d = normalize_desired({"live": {"scene": "live-art", "scene_tags": ["ambient"], "auto": {"fx": {"max": 1}}}})
    assert d["live"]["scene_tags"] == ["ambient"] and d["live"]["auto"] == {"fx": {"max": 1}}
    with pytest.raises(ValueError):
        normalize_desired({"live": {"scene": "x", "scene_tags": [3]}})


def test_a_mode_names_tags_not_a_scene():
    """Owner, 15 Oct: modes carry tags; the art director picks the scene."""
    from room_service.screens import desired_label, normalize_desired
    d = normalize_desired({"live": {"scene_tags": ["euphoric", "playful"], "transitions": {"in": "xfade"}}})
    assert d == {"live": {"scene_tags": ["euphoric", "playful"], "transitions": {"in": "xfade"}}}
    assert desired_label(d) == "live:#euphoric+playful"
    assert desired_label(normalize_desired({"live": {"auto": True}})) == "live:director"
    party = normalize_desired({"live": {"direction": {"energy": 0.8, "moods": ["euphoric"]}}})
    assert party == {"live": {"direction": {"energy": 0.8, "moods": ["euphoric"]}}}
    with pytest.raises(ValueError):
        normalize_desired({"live": {"transitions": {"in": "xfade"}}})       # nothing for the director to go on
    with pytest.raises(ValueError):
        normalize_desired({"live": {"auto": False}})


def test_one_generic_tag_rule():
    cat = {"presets": {"scenes": {
        "warp-kaleido": {"tags": {"moods": ["euphoric", "playful", "bright"]}},
        "fractal-zoom": {"tags": {"moods": ["dreamy"], "family": "fractal"}},
        "live-art": {"tags": {"moods": ["calm"], "modes": ["ambient"]}},
        "untagged": {}}}}
    assert live_art.tagged_scenes(cat, ["euphoric"]) == ["warp-kaleido"]              # a word: any facet
    assert live_art.tagged_scenes(cat, ["fractal", "playful"]) == ["fractal-zoom", "warp-kaleido"]
    assert live_art.tagged_scenes(cat, ["modes:ambient"]) == ["live-art"]             # facet:word: that facet only
    assert live_art.tagged_scenes(cat, ["moods:ambient"]) == []
    tl = live_art.scene_tag_list(cat)
    assert {"tag": "euphoric", "facet": "moods", "count": 1} in tl and {"tag": "ambient", "facet": "modes", "count": 1} in tl


async def test_a_tag_only_go_live_starts_on_the_directors_pick(make_harness):
    h = await make_harness()
    await h.page()
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": {"live": {"auto": True, "profile": "h264-720p30"}}})
    assert st == 202
    await h.wait(lambda: h.screen().layer == "live", 5, "live")
    body = [b for m, p, b in h.stream.calls if m == "POST" and p == "/api/sessions"][-1]
    assert body["scene"] == "auto" and body["auto"]["enabled"] is True       # the renderer's director picks


async def test_a_modes_direction_reaches_the_session(make_harness):
    h = await make_harness()
    await h.page()
    party = {"live": {"direction": {"energy": 0.9, "moods": ["euphoric"]}, "profile": "h264-720p30"}}
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": party})
    assert st == 202
    await h.wait(lambda: h.screen().layer == "live", 5, "live")
    body = [b for m, p, b in h.stream.calls if m == "POST" and p == "/api/sessions"][-1]
    assert body["scene"] == "auto" and body["direction"] == {"energy": 0.9, "moods": ["euphoric"]}
    disco = {"live": {"direction": {"energy": 1.0, "moods": ["geometric"]}, "profile": "h264-720p30"}}
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": disco})
    await h.wait(lambda: any(m == "PATCH" and (b or {}).get("direction") == disco["live"]["direction"]
                             for m, p, b in h.stream.calls), 5, "direction PATCHed")


async def test_live_to_live_resets_pool_and_direction(make_harness):
    """2b's audit (17 Oct): Ambient -> Party must open the pool; Party -> a mode without a direction goes neutral."""
    h = await make_harness()
    await h.page()
    ambient = {"live": {"scene_tags": ["modes:ambient"], "direction": {"energy": 0.2}, "profile": "h264-720p30"}}
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": ambient})
    await h.wait(lambda: h.screen().layer == "live", 5, "live")
    sid = h.screen().session["id"]
    party = {"live": {"auto": True, "profile": "h264-720p30"}}
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": party})
    def patched():
        bodies = [b for m, p, b in h.stream.calls if m == "PATCH" and p == f"/api/sessions/{sid}"]
        return bodies[-1] if bodies else None
    await h.wait(lambda: patched() is not None and "auto" in patched(), 5, "PATCH")
    body = patched()
    assert body["auto"]["pool"] == "all"
    assert "fx" not in body["auto"], "a mode change never touches effect picking or How many"
    assert body["direction"] == live_art.NEUTRAL_DIRECTION
    await h.wait(lambda: any(m == "POST" and p == f"/api/sessions/{sid}/cue" and (b or {}).get("to") == "auto"
                             for m, p, b in h.stream.calls), 5, "the director picks from the opened pool")
