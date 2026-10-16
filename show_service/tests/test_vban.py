"""VBAN wire (show_service/vban.py) against the rev 13 spec and our TEXT contract (docs/SHOW_ENGINE.md §3.1, §3.5)."""

from __future__ import annotations

import json
import math
import random
import struct

import numpy as np
import pytest

from show_service import vban

FS = 48000


def _pcm(frames: int, channels: int, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).uniform(-0.999, 0.999, (frames, channels)).astype(np.float32)


# -- spec constants ---------------------------------------------------------------------------------------------------
def test_sample_rate_table_matches_spec():
    assert vban.SR_LIST == (6000, 12000, 24000, 48000, 96000, 192000, 384000,
                            8000, 16000, 32000, 64000, 128000, 256000, 512000,
                            11025, 22050, 44100, 88200, 176400, 352800, 705600)
    assert vban.sr_index(48000) == 3
    assert vban.sr_index(44100) == 16
    assert vban.sr_index(8000) == 7
    with pytest.raises(ValueError):
        vban.sr_index(47999)


def test_sizes_and_types():
    assert (vban.PORT, vban.HEADER_SIZE, vban.MAX_DATA, vban.MAX_PACKET) == (6980, 28, 1436, 1464)
    assert (vban.INT16, vban.INT24, vban.FLOAT32) == (1, 2, 4)
    assert (vban.PROTO_AUDIO, vban.PROTO_TEXT, vban.TXT_UTF8) == (0x00, 0x40, 0x10)


@pytest.mark.parametrize("channels, fmt, want", [
    (1, vban.INT16, 128), (1, vban.INT24, 128), (1, vban.FLOAT32, 128),
    (2, vban.INT16, 128), (2, vban.INT24, 128), (2, vban.FLOAT32, 128),
    (4, vban.INT16, 128), (4, vban.INT24, 96), (4, vban.FLOAT32, 64),
    (8, vban.INT16, 64), (64, vban.FLOAT32, 5),
])
def test_frames_per_packet(channels, fmt, want):
    got = vban.frames_per_packet(channels, fmt)
    assert got == want
    assert got * channels * vban.SAMPLE_BYTES[fmt] <= vban.MAX_DATA


def test_frames_per_packet_refuses_what_cannot_fit():
    with pytest.raises(ValueError):
        vban.frames_per_packet(256, vban.FLOAT64)
    with pytest.raises(ValueError):
        vban.frames_per_packet(0, vban.INT16)


# -- header bytes -----------------------------------------------------------------------------------------------------
def test_audio_header_bytes_exact():
    pkt = vban.pack_audio("room-mic", 0x01020304, np.zeros((128, 1), np.float32), 48000, vban.INT16)
    assert len(pkt) == 28 + 128 * 2
    assert pkt[0:4] == b"VBAN"
    assert pkt[4] == 0x03                       # AUDIO (0x00) | SR index 3 (48 kHz)
    assert pkt[5] == 127                        # 128 frames - 1
    assert pkt[6] == 0                          # 1 channel - 1
    assert pkt[7] == 0x01                       # INT16, PCM codec
    assert pkt[8:24] == b"room-mic" + b"\x00" * 8
    assert pkt[24:28] == bytes([0x04, 0x03, 0x02, 0x01])   # uint32 little-endian


def test_audio_header_other_fields():
    pkt = vban.pack_audio("pc22-loop", 9, np.zeros((96, 4), np.float32), 44100, vban.INT24)
    assert pkt[4] == 16 and pkt[5] == 95 and pkt[6] == 3 and pkt[7] == 0x02
    pkt = vban.pack_audio("a" * 16, 0, np.zeros((64, 2), np.float32), 96000, vban.FLOAT32)
    assert pkt[4] == 4 and pkt[7] == 0x04 and pkt[8:24] == b"a" * 16


def test_text_header_bytes_exact():
    pkt = vban.pack_text("sub", 5, {"v": 1})
    assert pkt[0:4] == b"VBAN"
    assert pkt[4] == 0x40                       # TEXT, bit-rate index 0
    assert pkt[5] == 0 and pkt[6] == 0
    assert pkt[7] == 0x10                       # BYTE8 | UTF-8
    assert pkt[8:24] == b"sub" + b"\x00" * 13
    assert struct.unpack_from("<I", pkt, 24)[0] == 5
    assert pkt[28:] == b'{"v":1}'


def test_parses_voicemeeter_style_text_header():
    # The spec's own example (p. 21): 'V','B','A','N', 0x52, 0x00, 0x00, 0x10, "Command1"
    body = json.dumps({"v": 1, "x": "é"}).encode("utf-8")
    pkt = b"VBAN" + bytes([0x52, 0x00, 0x00, 0x10]) + b"Command1".ljust(16, b"\x00") + struct.pack("<I", 7) + body
    p = vban.parse(pkt)
    assert isinstance(p, vban.TextPacket)
    assert (p.stream, p.counter, p.obj, p.text_fmt) == ("Command1", 7, {"v": 1, "x": "é"}, vban.TXT_UTF8)


def test_bad_stream_names_refused():
    for name in ("", "x" * 17, "piänø", "a\x00b"):
        with pytest.raises(ValueError):
            vban.pack_text(name, 0, {"v": 1})


# -- audio round trips ------------------------------------------------------------------------------------------------
TOL = {vban.INT16: 0.5 / 32768 + 1e-7, vban.INT24: 0.5 / 8388608 + 1e-7, vban.INT32: 1e-7, vban.FLOAT32: 0.0,
       vban.FLOAT64: 1e-7}


@pytest.mark.parametrize("fmt", [vban.INT16, vban.INT24, vban.FLOAT32, vban.INT32, vban.FLOAT64])
@pytest.mark.parametrize("channels", [1, 2, 4])
def test_audio_round_trip(fmt, channels):
    frames = vban.frames_per_packet(channels, fmt)
    x = _pcm(frames, channels, seed=channels * 10 + fmt)
    pkt = vban.pack_audio("room-mic", 42, x, FS, fmt)
    assert len(pkt) <= vban.MAX_PACKET
    p = vban.parse(pkt)
    assert isinstance(p, vban.AudioPacket)
    assert (p.stream, p.counter, p.sr, p.channels, p.fmt, p.frames) == ("room-mic", 42, FS, channels, fmt, frames)
    assert p.pcm.dtype == np.float32 and p.pcm.shape == (frames, channels)
    assert float(np.abs(p.pcm - x).max()) <= TOL[fmt]


@pytest.mark.parametrize("frames, channels, fmt", [
    (256, 1, vban.INT16), (256, 2, vban.INT16), (256, 1, vban.FLOAT32), (119, 4, vban.INT24),
    (179, 2, vban.FLOAT32), (1, 256, vban.INT16),
])
def test_audio_max_sizes(frames, channels, fmt):
    x = _pcm(frames, channels)
    p = vban.parse(vban.pack_audio("big", 1, x, FS, fmt))
    assert p is not None and p.pcm.shape == (frames, channels)
    assert float(np.abs(p.pcm - x).max()) <= TOL[fmt]


@pytest.mark.parametrize("frames, channels, fmt", [
    (257, 1, vban.INT16),        # more than 256 frames
    (120, 4, vban.INT24),        # 1440 B > 1436
    (256, 1, vban.FLOAT64),      # 2048 B
    (180, 2, vban.FLOAT32),      # 1440 B
])
def test_audio_over_limits_refused(frames, channels, fmt):
    with pytest.raises(ValueError):
        vban.pack_audio("big", 1, np.zeros((frames, channels), np.float32), FS, fmt)


def test_mono_1d_and_rate_checks():
    p = vban.parse(vban.pack_audio("m", 0, np.zeros(128, np.float32), FS, vban.INT16))
    assert p.pcm.shape == (128, 1)
    with pytest.raises(ValueError):
        vban.pack_audio("m", 0, np.zeros(128, np.float32), 47000, vban.INT16)


def test_int_scaling_and_clipping():
    x = np.array([[1.0], [-1.0], [0.5], [-1 / 8388608], [2.0], [-3.0]], np.float32)
    p16 = vban.parse(vban.pack_audio("s", 0, x, FS, vban.INT16)).pcm[:, 0]
    assert p16[0] == np.float32(32767 / 32768) and p16[1] == -1.0 and p16[2] == 0.5
    assert p16[4] == np.float32(32767 / 32768) and p16[5] == -1.0
    pkt = vban.pack_audio("s", 0, x, FS, vban.INT24)
    data = pkt[28:]
    assert data[6:9] == bytes([0x00, 0x00, 0x40])          # +0.5 = 0x400000, little-endian
    assert data[9:12] == bytes([0xFF, 0xFF, 0xFF])         # -1 LSB, two's complement
    assert data[3:6] == bytes([0x00, 0x00, 0x80])          # -1.0 = -8388608
    p24 = vban.parse(pkt).pcm[:, 0]
    assert p24[1] == -1.0 and p24[2] == 0.5 and p24[3] == np.float32(-1 / 8388608)


def test_float_non_finite_sanitised():
    x = np.array([[np.nan], [np.inf], [-np.inf], [0.25]], np.float32)
    p = vban.parse(vban.pack_audio("s", 0, x, FS, vban.FLOAT32))
    assert p.pcm[:, 0].tolist() == [0.0, 1.0, -1.0, 0.25]
    p = vban.parse(vban.pack_audio("s", 0, x, FS, vban.INT16))      # the sender side too
    assert p.pcm[:, 0].tolist() == [0.0, 32767 / 32768, -1.0, 0.25]


@pytest.mark.parametrize("fmt, dtype", [(vban.FLOAT32, "<f4"), (vban.FLOAT64, "<f8")])
def test_float_encode_never_puts_non_finite_on_the_wire(fmt, dtype):
    """A third-party receiver (PipeWire, Voicemeeter) doesn't sanitise: the bytes themselves must be finite."""
    x = np.array([[np.nan], [np.inf], [-np.inf], [0.25]], np.float64)
    before = x.copy()
    raw = np.frombuffer(vban.pack_audio("s", 0, x, FS, fmt)[vban.HEADER_SIZE:], dtype)
    assert raw.tolist() == [0.0, 1.0, -1.0, 0.25]
    assert np.array_equal(x, before, equal_nan=True)                    # the caller's array untouched
    big = np.array([[1e300]], np.float64)                               # finite, but not in float32
    with np.errstate(over="ignore"):
        assert np.frombuffer(vban.pack_audio("s", 0, big, FS, vban.FLOAT32)[28:], "<f4").tolist() == [1.0]


def test_parse_copies_out_of_the_buffer():
    buf = bytearray(vban.pack_audio("s", 0, np.full((4, 1), 0.5, np.float32), FS, vban.FLOAT32))
    p = vban.parse(buf)
    buf[28:] = b"\x00" * (len(buf) - 28)
    assert p.pcm[:, 0].tolist() == [0.5] * 4


def test_byte8_decoded():
    pkt = b"VBAN" + bytes([0x03, 3, 0, vban.BYTE8]) + b"u8".ljust(16, b"\x00") + struct.pack("<I", 1) + bytes(
        [0, 128, 192, 255])
    p = vban.parse(pkt)
    assert p.pcm[:, 0].tolist() == [-1.0, 0.0, 0.5, 127 / 128]


# -- counter ----------------------------------------------------------------------------------------------------------
def test_counter_wraps():
    x = np.zeros((128, 1), np.float32)
    assert vban.parse(vban.pack_audio("s", 2**32 + 5, x, FS)).counter == 5
    assert vban.parse(vban.pack_audio("s", 2**32 - 1, x, FS)).counter == 2**32 - 1
    assert vban.parse(vban.pack_audio("s", -1, x, FS)).counter == 2**32 - 1
    assert vban.counter_delta(2**32 - 1, 0) == 1
    assert vban.counter_delta(2**32 - 2, 3) == 5
    assert vban.counter_delta(7, 7) == 0
    assert vban.counter_delta(7, 5) > 2**31                # backwards


# -- junk -------------------------------------------------------------------------------------------------------------
def _audio_hdr(sr_byte=0x03, nbs=0, nbc=0, bit=0x01, name=b"s"):
    return b"VBAN" + bytes([sr_byte, nbs, nbc, bit]) + name.ljust(16, b"\x00") + struct.pack("<I", 0)


@pytest.mark.parametrize("buf", [
    b"", b"VBAN", b"VBAX" + b"\x00" * 30, _audio_hdr()[:27],
    _audio_hdr(sr_byte=21) + b"\x00\x00",               # SR index 21 is undefined
    _audio_hdr(sr_byte=0x1F) + b"\x00\x00",
    _audio_hdr(bit=0x11) + b"\x00\x00",                 # VBCA codec
    _audio_hdr(bit=0x06) + b"\x00\x00",                 # 12-bit
    _audio_hdr(bit=0x07) + b"\x00\x00",                 # 10-bit
    _audio_hdr() + b"\x00",                             # 1 byte short of one INT16 frame
    _audio_hdr() + b"\x00\x00\x00",                     # one byte too many
    _audio_hdr(sr_byte=0x20) + b"\x00\x00",             # SERIAL
    _audio_hdr(sr_byte=0x60) + b"\x00\x00",             # SERVICE
    _audio_hdr(name=b"") + b"\x00\x00",                 # no stream name
    _audio_hdr(name=b"\xff\xfe") + b"\x00\x00",         # not ASCII
    _audio_hdr(nbs=255, bit=0x04) + b"\x00" * 1436,     # over 1464 B in all
    _audio_hdr(sr_byte=0x40, bit=0x10) + b"not json",
    _audio_hdr(sr_byte=0x40, bit=0x10) + b"[1, 2]",     # JSON, but not an object
    _audio_hdr(sr_byte=0x40, bit=0x10) + b"\xff\xfe{}",  # not UTF-8
    _audio_hdr(sr_byte=0x40, bit=0x20) + b"{}",         # WCHAR text
    _audio_hdr(sr_byte=0x40, bit=0x11) + b"{}",         # TEXT with a data type other than BYTE8
    _audio_hdr(sr_byte=0x40, bit=0x10) + b"[" * 5000,   # nesting bomb (and oversize)
    _audio_hdr(sr_byte=0x40, bit=0x10) + b"[" * 1400,   # nesting bomb within the size limit
])
def test_junk_is_none(buf):
    assert vban.parse(buf) is None


def test_fuzz_never_raises():
    rng = random.Random(1)
    good = [vban.pack_audio("room-mic", 1, _pcm(96, 4), FS, vban.INT24),
            vban.pack_text("room-piano", 2, vban.midi_events_obj(3, [[1, 0x90, 60, 64]], sess="k-1"))]
    for _ in range(3000):
        kind = rng.random()
        if kind < 0.3:
            buf = bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 80)))
        elif kind < 0.6:
            buf = b"VBAN" + bytes(rng.getrandbits(8) for _ in range(rng.randrange(0, 200)))
        else:
            b = bytearray(rng.choice(good))
            for _ in range(rng.randrange(1, 6)):
                b[rng.randrange(len(b))] = rng.getrandbits(8)
            buf = bytes(b[:rng.randrange(len(b) + 1)]) if rng.random() < 0.3 else bytes(b)
        out = vban.parse(buf)
        assert out is None or isinstance(out, (vban.AudioPacket, vban.TextPacket))


# -- text -------------------------------------------------------------------------------------------------------------
def test_text_round_trip_and_limits():
    obj = {"v": 1, "a": [1, 2.5, None, True], "s": "ünïcode"}
    p = vban.parse(vban.pack_text("x", 9, obj))
    assert isinstance(p, vban.TextPacket) and p.obj == obj and p.counter == 9
    with pytest.raises(ValueError):
        vban.pack_text("x", 0, {"pad": "x" * 1500})
    with pytest.raises(ValueError):
        vban.pack_text("x", 0, {"bad": math.nan})
    with pytest.raises(ValueError):
        vban.pack_text("x", 0, [1, 2])  # type: ignore[arg-type]
    assert vban.parse(vban.pack_text("x", 0, {"pad": "x" * (1436 - 10)})) is not None


def test_trailing_nuls_tolerated():
    pkt = vban.pack_text("x", 0, {"v": 1}) + b"\x00\x00"
    assert vban.parse(pkt).obj == {"v": 1}


# -- SUBSCRIBE --------------------------------------------------------------------------------------------------------
def test_subscribe_round_trip():
    pkt = vban.subscribe_packet(["room-mic", "room-piano"], 3)
    p = vban.parse(pkt)
    assert p.stream == "sub" and p.counter == 3
    assert p.obj == {"v": 1, "subscribe": ["room-mic", "room-piano"], "from": "show_rt"}
    assert pkt[28:] == b'{"v":1,"subscribe":["room-mic","room-piano"],"from":"show_rt"}'
    assert vban.is_subscribe(p)
    assert vban.subscribed_streams(p) == ["room-mic", "room-piano"]
    assert vban.classify(p) == "subscribe"


def test_subscribe_rejections():
    assert not vban.is_subscribe(vban.parse(vban.pack_text("room-mic", 0, vban.subscribe_obj(["a"]))))  # wrong stream
    assert not vban.is_subscribe({"v": 1, "subscribe": "room-mic"})
    assert not vban.is_subscribe({"v": 1, "subscribe": ["x" * 17]})
    assert not vban.is_subscribe({"subscribe": ["a"]})                                               # no v
    assert not vban.is_subscribe(vban.parse(vban.pack_text("hb", 0, _hb())))


# -- heartbeat --------------------------------------------------------------------------------------------------------
def _hb(**kw) -> dict:
    args = dict(sess="k-1", frame=1000, n=128000, t_cap_ns=12_345_678_901_234, in_lat_ms=5.33,
                dev="ATR4697-USB", state="ok", xruns=2, peak_dbfs=[-3.5], rms_dbfs=[-24.0], clips=1)
    args.update(kw)
    return vban.make_heartbeat(**args)


def test_heartbeat_round_trip():
    obj = _hb(peak_dbfs=[-math.inf, -3.0], rms_dbfs=[math.nan, -200.0])
    assert set(obj) == {"v", "sess", "frame", "n", "t_cap_ns", "in_lat_ms", "dev", "state", "xruns", "peak_dbfs",
                        "rms_dbfs", "clips"}
    assert obj["peak_dbfs"] == [-120.0, -3.0] and obj["rms_dbfs"] == [-120.0, -120.0]
    p = vban.parse(vban.pack_text("room-mic", 4, obj))
    hb = vban.parse_heartbeat(p)
    assert hb == vban.Heartbeat(1, "k-1", 1000, 128000, 12_345_678_901_234, 5.33, "ATR4697-USB", "ok", 2,
                                [-120.0, -3.0], [-120.0, -120.0], 1)
    assert vban.is_heartbeat(p) and vban.classify(p) == "heartbeat"


def test_heartbeat_before_any_audio_and_states():
    hb = vban.parse_heartbeat(_hb(frame=None, n=None, t_cap_ns=None, state="no_device"))
    assert hb is not None and hb.frame is None and hb.n is None and hb.state == "no_device"
    for st in vban.HEARTBEAT_STATES:
        assert vban.is_heartbeat(_hb(state=st))
    with pytest.raises(ValueError):
        _hb(state="sleeping")
    assert vban.make_heartbeat(sess="s", frame=2**32 + 1, n=0, t_cap_ns=0)["frame"] == 1


@pytest.mark.parametrize("patch", [
    {"state": "sleeping"}, {"sess": ""}, {"sess": 5}, {"v": 0}, {"v": True}, {"frame": True}, {"frame": -1},
    {"frame": 2**32}, {"n": 1.5}, {"t_cap_ns": "1"}, {"peak_dbfs": "loud"}, {"rms_dbfs": [None]},
    {"in_lat_ms": "5"}, {"dev": 3}, {"xruns": -1},
])
def test_heartbeat_invalid(patch):
    obj = _hb()
    obj.update(patch)
    assert vban.parse_heartbeat(obj) is None


def test_heartbeat_ignores_unknown_keys():
    obj = _hb()
    obj["future"] = {"x": 1}
    assert vban.is_heartbeat(obj)


# -- MIDI -------------------------------------------------------------------------------------------------------------
T = 98_765_432_100_000


def test_midi_events_round_trip():
    obj = vban.midi_events_obj(1234, [[T, 0x90, 60, 100], [T + 5, 0xB0, 64, 127]],
                               prev=[(1233, [[T - 9, 0x80, 59, 40]]), (1232, [])], sess="k-1")
    assert obj == {"v": 1, "seq": 1234, "ev": [[T, 0x90, 60, 100], [T + 5, 0xB0, 64, 127]],
                   "prev": [[1233, [[T - 9, 0x80, 59, 40]]], [1232, []]], "sess": "k-1"}
    p = vban.parse(vban.pack_midi_events("room-piano", 77, obj))
    assert p.stream == "room-piano" and vban.classify(p) == "midi_events"
    b = vban.parse_midi_events(p)
    assert b == vban.MidiBatch(1234, [(T, 0x90, 60, 100), (T + 5, 0xB0, 64, 127)],
                               [(1233, [(T - 9, 0x80, 59, 40)]), (1232, [])], "k-1", 1)


def test_midi_events_without_prev_or_sess():
    """Readers accept MIDI without sess (third parties); our builders never send it without."""
    b = vban.parse_midi_events({"v": 1, "seq": 1, "ev": [[0, 0x90, 21, 1]]})
    assert b.prev == [] and b.sess is None
    st = vban.parse_midi_state({"v": 1, "seq": 1, "state": {"keys": [0] * 88}, "t_ns": 5})
    assert st is not None and st.sess is None and st.cc64 == 0
    assert vban.parse_midi_events(vban.midi_events_obj(1, [[0, 0x90, 21, 1]], sess="k-1")).prev == []


def test_midi_builders_require_sess():
    for bad in (None, "", 7):
        with pytest.raises(ValueError):
            vban.midi_events_obj(1, [[0, 0x90, 21, 1]], sess=bad)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            vban.midi_state_obj(1, [0] * 88, 0, 0, 0, T, sess=bad)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        vban.midi_events_obj(1, [[0, 0x90, 21, 1]])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        vban.midi_state_obj(1, [0] * 88, 0, 0, 0, T)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        vban.midi_events_obj(1, [[0, 0x90, 21, 1]], sess="x" * (vban.MIDI_SESS_MAX + 1))


@pytest.mark.parametrize("ev", [
    [[T, 0x70, 60, 100]],            # not a status byte
    [[T, 0x90, 128, 100]],           # data byte out of range
    [[T, 0x90, 60]],                 # three fields
    [[-1, 0x90, 60, 100]],           # negative time
    [[T, 0x90, 60.0, 100]],          # float
    [[T, 0x90, True, 100]],          # bool
])
def test_midi_bad_events(ev):
    assert vban.parse_midi_events({"v": 1, "seq": 5, "ev": ev}) is None
    with pytest.raises(ValueError):
        vban.midi_events_obj(5, ev, sess="k-1")


def test_midi_bad_prev_and_seq():
    good = [[T, 0x90, 60, 100]]
    assert vban.parse_midi_events({"v": 1, "seq": 5, "ev": good, "prev": [[5, good]]}) is None      # not older
    assert vban.parse_midi_events({"v": 1, "seq": 5, "ev": good, "prev": [[4]]}) is None
    assert vban.parse_midi_events({"v": 1, "seq": -1, "ev": good}) is None
    assert vban.parse_midi_events({"v": 1, "seq": 5, "ev": good, "sess": ""}) is None
    with pytest.raises(ValueError):
        vban.midi_events_obj(5, [], sess="k-1")
    with pytest.raises(ValueError):
        vban.midi_events_obj(5, [[T, 0x90, 60, 100]] * (vban.MIDI_BATCH_MAX + 1), sess="k-1")


def test_midi_full_batch_fits_and_prev_is_trimmed():
    worst = [[2**63 - 1, 0xFF, 127, 127]] * vban.MIDI_BATCH_MAX
    longest = "s" * vban.MIDI_SESS_MAX
    obj = vban.midi_events_obj(2**63 - 1, worst, prev=[(2**63 - 2, worst), (2**63 - 3, worst)], sess=longest)
    pkt = vban.pack_midi_events("room-piano", 0, obj)
    assert len(pkt) <= vban.MAX_PACKET
    b = vban.parse_midi_events(vban.parse(pkt))
    assert len(b.ev) == vban.MIDI_BATCH_MAX and b.prev == []
    small = [[T, 0x90, 60, 100]]
    obj = vban.midi_events_obj(10, worst[:20], prev=[(9, small), (8, worst[:30])], sess="k-1")
    b = vban.parse_midi_events(vban.parse(vban.pack_midi_events("room-piano", 0, obj)))
    assert [s for s, _ in b.prev] == [9]           # the oldest batch went first


@pytest.mark.parametrize("seq, sess, last_seq, last_sess, state, want", [
    (1, "a", None, None, False, "restart"),        # the first packet
    (5, "a", None, None, True, "restart"),
    (41, "a", 40, "a", False, "new"),
    (45, "a", 40, "a", False, "new"),              # ahead: prev first
    (40, "a", 40, "a", False, "old"),              # a duplicate
    (36, "a", 40, "a", False, "old"),              # a straggler within the reorder window
    (40, "a", 40, "a", True, "new"),               # a state at the last batch heals
    (39, "a", 40, "a", True, "old"),               # a stale state
    (1, "b", 40, "a", False, "restart"),           # sess changed
    (41, "b", 40, "a", True, "restart"),
    (41, "a", 40, None, False, "restart"),         # sess appeared
    (1, None, 40, None, False, "restart"),         # no sess: back by more than MIDI_REORDER (restarted at 1)
    (1, None, 40, None, True, "restart"),          # ... a state too, so stuck notes heal
    (32, None, 40, None, False, "old"),            # exactly MIDI_REORDER back: still a straggler
    (31, None, 40, None, False, "restart"),
])
def test_midi_seq_verdict(seq, sess, last_seq, last_sess, state, want):
    assert vban.midi_seq_verdict(seq, sess, last_seq, last_sess, state=state) == want
    assert vban.MIDI_REORDER > vban.MIDI_PREV


def test_midi_state_round_trip():
    keys = [0] * 88
    keys[39] = 80                                   # middle C (MIDI 60)
    obj = vban.midi_state_obj(1240, keys, 127, 0, 30, T, sess="k-1")
    assert obj == {"v": 1, "seq": 1240, "state": {"keys": keys, "cc64": 127, "cc66": 0, "cc67": 30}, "t_ns": T,
                   "sess": "k-1"}
    p = vban.parse(vban.pack_text("room-piano", 5, obj))
    assert vban.classify(p) == "midi_state"
    st = vban.parse_midi_state(p)
    assert st == vban.MidiState(1240, keys, 127, 0, 30, T, "k-1", 1)
    assert vban.PIANO_LOW + 39 == 60
    assert not vban.is_heartbeat(p)


@pytest.mark.parametrize("patch", [
    {"state": {"keys": [0] * 87, "cc64": 0, "cc66": 0, "cc67": 0}},
    {"state": {"keys": [0] * 87 + [128], "cc64": 0, "cc66": 0, "cc67": 0}},
    {"state": {"keys": [0] * 88, "cc64": 200, "cc66": 0, "cc67": 0}},
    {"t_ns": -5}, {"t_ns": None}, {"seq": "1"},
])
def test_midi_state_invalid(patch):
    obj = vban.midi_state_obj(1, [0] * 88, 0, 0, 0, T, sess="k-1")
    obj.update(patch)
    assert vban.parse_midi_state(obj) is None
    assert vban.classify(obj) != "midi_state"


def test_midi_state_arg_checks():
    with pytest.raises(ValueError):
        vban.midi_state_obj(1, [0] * 87, 0, 0, 0, T, sess="k-1")
    with pytest.raises(ValueError):
        vban.midi_state_obj(1, [0] * 88, 128, 0, 0, T, sess="k-1")


def test_classify_unknown():
    assert vban.classify({"v": 1, "hello": 1}) is None
    assert vban.classify([1]) is None  # type: ignore[arg-type]


# -- device choices (the Room page's Mic select) ----------------------------------------------------------------------
def test_heartbeat_devices_round_trip_and_fit_one_packet():
    hb = vban.make_heartbeat(sess="s", frame=0, n=0, t_cap_ns=0, dev="Dell", devices=["Dell", "Insta360 Link"])
    got = vban.parse_heartbeat(vban.parse(vban.pack_text("pc22-mic", 0, hb)))
    assert got is not None and got.dev == "Dell" and got.devices == ["Dell", "Insta360 Link"]
    big = vban.make_heartbeat(sess="s", frame=0, n=0, t_cap_ns=0, devices=[f"{i:03d}" + "x" * 200 for i in range(40)])
    assert 0 < len(big["devices"]) < vban.DEVICES_MAX and all(len(d) == vban.DEVICE_NAME_MAX for d in big["devices"])
    assert len(vban.pack_text("pc22-mic", 0, big)) <= vban.MAX_PACKET            # trimmed from the end until it fits
    assert vban.parse_heartbeat(vban.make_heartbeat(sess="s", frame=0, n=0, t_cap_ns=0)).devices is None


def test_heartbeat_with_bad_devices_still_parses():
    hb = vban.make_heartbeat(sess="s", frame=0, n=0, t_cap_ns=0)
    assert vban.parse_heartbeat({**hb, "devices": "Dell"}).devices is None
    assert vban.parse_heartbeat({**hb, "devices": ["Dell", 3, "", "y" * 129]}).devices == ["Dell"]


def test_subscribe_device_hints():
    pkt = vban.parse(vban.subscribe_packet(["pc22-mic"], 0, devices={"pc22-mic": "Dell"}))
    assert vban.subscribed_streams(pkt) == ["pc22-mic"] and vban.subscribed_devices(pkt) == {"pc22-mic": "Dell"}
    assert vban.subscribed_devices(vban.parse(vban.subscribe_packet(["pc22-mic"], 1))) == {}
    assert "devices" not in vban.subscribe_obj(["pc22-mic"], devices={})
    assert vban.subscribed_devices({"v": 1, "subscribe": ["a"], "devices": {"a": 3, "b": "B"}}) == {"b": "B"}
    assert vban.subscribed_devices({"v": 1, "sess": "x"}) == {}
