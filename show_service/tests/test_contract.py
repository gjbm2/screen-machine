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
