"""Shared constants (show_service/contract.py) against docs/SHOW_ENGINE.md §3.3 and §11.1."""

from __future__ import annotations

from show_service import contract as c


def test_spectrum32_edges_match_the_spec():
    edges = [round(e) for e in c.SPEC_EDGES_HZ]
    assert len(edges) == 33
    assert edges[:8] == [30, 37, 44, 54, 66, 80, 97, 118]
    assert edges[-4:] == [8881, 10806, 13149, 16000]
    lo, hi = c.SPEC_EDGES_HZ[0], c.SPEC_EDGES_HZ[1]
    assert abs(c.SPEC_CENTRES_HZ[0] - (lo * hi) ** 0.5) < 1e-9


def test_stream_priority():
    ranks = [c.stream_rank(s) for s in ("room-line", "room-mic", "pc22-loop", "pc22-mic", "test-piano", "whatever")]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert c.stream_rank("phone-test") == c.stream_rank("test-piano")
    pri = ("pc22-loop", "room-mic")                     # Config.stream_priority: those first, in that order
    ranks = [c.stream_rank(s, pri) for s in ("pc22-loop", "room-mic", "room-line", "pc22-mic", "whatever")]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


def test_clock_body_lives_here():
    assert {"bpm", "anchor_ms", "beat_at_anchor", "beats_per_bar", "bar_anchor_beat", "phrase_bars", "conf",
            "bar_conf", "phrase_conf", "state"} <= set(c.ClockData.__annotations__)        # §5.3's fields


def test_flags_are_distinct_bits():
    flags = [c.AUDIO_GAP, c.AUDIO_RESUME, c.AUDIO_SEGMENT]
    assert all(f & (f - 1) == 0 for f in flags) and len(set(flags)) == 3


def test_pedal_ccs_and_bands():
    assert c.PEDAL_CC == {64: "damper", 66: "sostenuto", 67: "soft"}
    assert tuple(c.BAND_EDGES_HZ) == c.BAND_NAMES
    assert len(c.KEY_NAMES) == 12


def test_config_fields_live_here():
    """Sender and Config are contract types (config.py fills them; the receiver takes them)."""
    import dataclasses

    cfg = c.Config(senders=(c.Sender("kiosk", "screen-machine-drawingroom", streams=("room-mic", "room-piano")),))
    assert cfg.vban_bind == ("0.0.0.0", 6980) and cfg.bus_bind == ("127.0.0.1", c.BUS_PORT)
    assert cfg.latency_file.parts[-3:] == ("config", "room", "latency.json")
    assert cfg.senders[0].port == 6980
    try:
        cfg.log_level = "DEBUG"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("Config must be frozen")


def test_features_carry_punch_and_drive():
    assert {"punch", "drive", "energy", "spectrum32"} <= set(c.FeaturesData.__annotations__)


def test_song_end_kinds_and_shapes():
    """README S5.17 (the room.event shape agreed with 04, 17 Oct)."""
    assert set(c.TRACK_EVENT_KINDS) == {"track_end_armed", "track_end", "track_change"}
    assert set(c.TRACK_EVENT_KINDS) <= set(c.MUSIC_EVENT_KINDS)
    assert {"track_end_armed", "track_end"} <= set(c.EVENT_KINDS_STAGE4)
    assert not set(c.EVENT_KINDS_STAGE4) & set(c.EVENT_KINDS)
    assert "id" in c.EventData.__annotations__
    assert {"transition", "held", "prev_track_id", "eta_s", "conf", "track_id", "via"} <= set(
        c.MusicEventData.__annotations__)
    assert set(c.TrackTransition.__annotations__) == {"style", "out_s", "in_s", "seconds", "on", "change",
                                                      "dark_max_s"}
    assert c.TRANSITION_STYLES == ("fade-dark", "crossfade", "cut") and c.TRANSITION_ON == ("downbeat", "now")
    assert c.CHANGE_SIZES == ("big", "small") and c.TRACK_VIA == ("sonos", "dsp", "player")
    assert c.BrainContext(publish=lambda m: None).track_transition is None


# -- the art wish (owner, 28 Oct) ----------------------------------------------------------------------------------------
ART_YAML = """facets:
  subject: {landscape: x, still-life: x, abstract: x, urban: x, people: x, animals: x, sky: x}
  medium: {painting: x, photograph: x, render: x}
  moods: {calm: x, dark: x, elegant: x, warm: x}
music_art:
  jazz: {subjects: [abstract, urban, people, landscape, sky, still-life], moods: [dark, elegant, warm],
         media: [painting], avoid: [animals], theme: "late-night city, smoky rooms, painterly abstraction"}
"""


def test_art_group_words_are_tagpass_group_words():
    """A copy, read by file (tagpass sits in stream_service's tools and changes sys.path on import): they must agree."""
    import ast
    tree = ast.parse((c.REPO_ROOT / "stream_service" / "tools" / "tagpass.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "GROUP_WORDS")
    theirs = ast.literal_eval(node.value)
    assert {g: tuple(ws) for g, ws in theirs.items()} == c.ART_GROUP_WORDS


def test_art_group_takes_the_genre_then_the_top_share():
    assert c.art_group("blues") == "jazz" and c.art_group("electronic") == "house"   # first group naming it
    assert c.art_group(None, {"piano": 0.3, "pop": 0.7}) == "pop"
    assert c.art_group("bebop", {"jazz": 1.0}) == "jazz"                            # unknown word: the shares
    assert c.art_group(None) is None and c.art_group(None, {}) is None


def test_clean_art_keeps_only_lexicon_words_within_the_limits():
    facets = {"subject": ("landscape", "still-life", "abstract", "urban", "people"), "medium": ("painting",),
              "moods": ("calm", "dark", "elegant", "warm")}
    a = c.clean_art({"subjects": ["Still life", "abstract", "unicorns", "abstract", "urban", "people"],
                     "moods": ["DARK", "sparkly", 3], "media": ["painting", "fresco"],
                     "avoid": ["abstract", "people"],
                     "theme": " late night   city smoky rooms painterly abstraction and some more words here "},
                    facets, "listener")
    assert a == {"subjects": ["still-life", "abstract", "urban"], "moods": ["dark"], "media": ["painting"],
                 "avoid": ["people"], "theme": "late night city smoky rooms painterly abstraction and some more",
                 "via": "listener"}
    assert set(a) == set(c.ArtWish.__annotations__)
    for bad in (None, "abstract", ["abstract"], {}, {"subjects": "abstract"}, {"subjects": ["nothing"]}):
        assert c.clean_art(bad, facets, "listener") is None


def test_art_tags_reads_the_lexicon_and_music_art(tmp_path):
    p = tmp_path / "art_tags.yaml"
    p.write_text(ART_YAML)
    t = c.ArtTags(p)
    t.poll(0.0)
    assert t.error is None and t.words("medium") == ["painting", "photograph", "render"]
    j = t.for_group("jazz")
    assert j["via"] == "genre" and j["subjects"] == ["abstract", "urban", "people", "landscape", "sky"]   # <= 5
    assert t.for_group("pop") is None and t.for_group(None) is None                   # no row: nothing invented
    p.write_text("facets: [")                                                         # broken: the last good stays
    t.poll(10.0)
    assert t.error and t.for_group("jazz") == j
    missing = c.ArtTags(tmp_path / "nope.yaml")
    missing.poll(0.0)
    assert missing.error and missing.for_group("jazz") is None


def test_the_real_music_art_rows_are_all_lexicon_words():
    t = c.ArtTags()
    t.poll(0.0)
    assert t.error is None
    for g in c.ART_GROUP_WORDS:
        row = t.rows.get(g)
        if row is None:
            continue
        a = t.for_group(g)
        assert a is not None
        for k in ("subjects", "moods", "media", "avoid"):
            assert a[k] == list(row.get(k) or [])[:len(a[k])] and len(a[k]) == len(row.get(k) or []), (g, k)
