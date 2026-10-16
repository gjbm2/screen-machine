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
