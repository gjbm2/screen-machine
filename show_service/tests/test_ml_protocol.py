"""The ML worker's wire protocol (README S4.3): frames round-trip, the parser survives any chunking and junk, lines
are exactly contract's shapes. numpy only; runs under ~/.venvs/sm-show and ~/.venvs/sm-show-ml."""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

from show_service import contract as C
from show_service.ml import protocol as P


def test_header_constants():
    assert C.ML_HEADER_SIZE == 48
    assert C.ML_HEADER_SIZE + 4 * C.ML_FRAME_MAX_SAMPLES <= C.ML_PIPE_ATOMIC
    assert len(P.pack_audio("x", 1, 0, 0.0, 0, 0, np.zeros(C.ML_FRAME_MAX_SAMPLES, np.float32))) <= C.ML_PIPE_ATOMIC


@pytest.mark.parametrize("flags", [0, C.ML_FLAG_SEGMENT, C.ML_FLAG_GAP, C.ML_FLAG_AFTER_DROP,
                                   C.ML_FLAG_SEGMENT | C.ML_FLAG_GAP | C.ML_FLAG_AFTER_DROP])
def test_audio_round_trip_every_flag(flags):
    pcm = np.linspace(-1, 1, C.ML_FRAME_SAMPLES, dtype=np.float32)
    data = P.pack_audio("kiosk-room", 7, 123456789012, 8361234.123456789, 42, flags, pcm)
    assert len(data) == C.ML_HEADER_SIZE + 4 * C.ML_FRAME_SAMPLES
    frames = list(P.FrameParser().feed(data))
    assert len(frames) == 1
    f = frames[0]
    assert f.is_audio and f.flags == flags and f.n == C.ML_FRAME_SAMPLES
    assert (f.stream, f.seg, f.n0, f.dropped) == ("kiosk-room", 7, 123456789012, 42)
    assert f.t0 == 8361234.123456789
    assert np.array_equal(f.samples(), pcm)


def test_stream_name_truncated_and_padded():
    f = next(P.FrameParser().feed(P.pack_audio("a-very-long-stream-name-indeed", 1, 0, 0.0, 0, 0, b"\0" * 4)))
    assert f.stream == "a-very-long-stre"
    f = next(P.FrameParser().feed(P.pack_audio("", 1, 0, 0.0, 0, 0, b"\0" * 4)))
    assert f.stream == ""


def test_control_round_trip():
    ml = {"enabled": True, "tasks": {"ced": {"every_s": 1}}, "ced_groups": {"music": ["Music"]}}
    frames = list(P.FrameParser().feed(P.config_frame(ml) + P.stop_frame()))
    assert [f.ctrl["cmd"] for f in frames] == [C.ML_CMD_CONFIG, C.ML_CMD_STOP]
    assert frames[0].ctrl["ml"] == ml and frames[0].ctrl["v"] == C.ML_PROTOCOL_V
    assert not frames[0].is_audio and frames[0].flags == 0 and frames[0].stream == ""


def test_limits_refused():
    with pytest.raises(P.ProtocolError):
        P.pack_audio("x", 1, 0, 0.0, 0, 0, np.zeros(C.ML_FRAME_MAX_SAMPLES + 1, np.float32))
    with pytest.raises(P.ProtocolError):
        P.pack_audio("x", 1, 0, 0.0, 0, 0, b"")
    with pytest.raises(P.ProtocolError):
        P.pack_ctrl({"cmd": "config", "pad": "x" * C.ML_CTRL_MAX_BYTES})


def test_parser_any_chunking():
    rng = np.random.default_rng(1)
    blobs = [P.pack_audio("s", 1, i * 800, i * 0.05, 0, C.ML_FLAG_SEGMENT if i == 0 else 0,
                          rng.standard_normal(800).astype(np.float32)) for i in range(5)]
    blobs.insert(2, P.config_frame({"x": 1}))
    data = b"".join(blobs)
    for chunk in (1, 7, 48, 1000, len(data)):
        p = P.FrameParser()
        out = []
        for i in range(0, len(data), chunk):
            out += list(p.feed(data[i:i + chunk]))
        assert [f.is_audio for f in out] == [True, True, False, True, True, True]
        assert [f.n0 for f in out if f.is_audio] == [0, 800, 1600, 2400, 3200]
        assert p.bad == 0


def test_parser_resyncs_after_junk():
    good = P.pack_audio("s", 1, 0, 0.0, 0, 0, np.ones(10, np.float32))
    bad_version = bytearray(good)
    bad_version[4] = 99
    p = P.FrameParser()
    out = list(p.feed(b"garbage!" * 10 + bytes(bad_version) + good))
    assert len(out) == 1 and out[0].n == 10
    assert p.bad >= 1


def test_non_object_control_counted():
    import struct
    payload = b"[1,2]"                               # JSON, but not an object
    h = struct.pack(C.ML_HEADER_FMT, C.ML_CTRL_MAGIC, C.ML_PROTOCOL_V, 0, len(payload), b"\0" * 16, 0, 0, 0.0, 0)
    p = P.FrameParser()
    out = list(p.feed(h + payload + P.stop_frame()))
    assert [f.ctrl for f in out] == [{"cmd": C.ML_CMD_STOP}]
    assert p.bad == 1


def test_atomic_write_through_a_real_pipe():
    r, w = os.pipe()
    try:
        os.set_blocking(w, False)
        frame = P.pack_audio("s", 1, 0, 0.0, 0, 0, np.zeros(C.ML_FRAME_MAX_SAMPLES, np.float32))
        written = 0
        while True:
            try:
                assert os.write(w, frame) == len(frame)        # all or nothing
                written += 1
            except BlockingIOError:
                break
        p = P.FrameParser()
        got = []
        while len(got) < written:
            got += list(p.feed(os.read(r, 65536)))
        assert len(got) == written and p.bad == 0
    finally:
        os.close(r)
        os.close(w)


# -- lines -------------------------------------------------------------------------------------------------------------
def _ced(**kw):
    d = {"type": "ced", "t": 1.0, "window_s": 2.0, "stream": "s", "seg": 1, "cost_ms": 1.0}
    d.update({f"{g}_p": 0.0 for g in C.CED_GROUPS})
    d.update(kw)
    return d


def test_check_line_shapes():
    assert P.check_line(_ced()) is None
    assert P.check_line(_ced(skipped="silence")) is None          # NotRequired
    bad = _ced()
    del bad["music_p"]
    assert "missing" in P.check_line(bad)
    assert "unexpected" in P.check_line(_ced(extra=1))
    assert "unknown type" in P.check_line({"type": "nope"})
    assert P.check_line([1]) == "not an object"
    err = {"type": "error", "t": 1.0, "task": "ced", "error": "x"}
    assert P.check_line(err) is None


def test_encode_line_no_nan_and_bounded():
    line = P.encode_line({"type": "x", "a": float("nan"), "b": [float("inf"), 1.23456789], "c": np.float32(0.5),
                          "d": (1, 2)})
    assert line.endswith(b"\n") and b"NaN" not in line and b"Infinity" not in line
    obj = json.loads(line)
    assert obj == {"type": "x", "a": None, "b": [None, 1.234568], "c": 0.5, "d": [1, 2]}
    with pytest.raises(P.ProtocolError):
        P.encode_line({"type": "x", "big": "y" * C.ML_LINE_MAX})


def test_embedding_round_trip():
    v = np.random.default_rng(3).standard_normal(C.EMB_DIM).astype(np.float32)
    v /= np.linalg.norm(v)
    b64 = P.encode_emb(v)
    back = P.decode_emb(b64, C.EMB_DIM)
    assert back.dtype == np.float32 and back.shape == (C.EMB_DIM,)
    assert float(np.abs(back - v).max()) < 1e-3
    assert math.isclose(float(np.linalg.norm(back)), 1.0, abs_tol=1e-3)
    with pytest.raises(ValueError):
        P.decode_emb(b64, C.EMB_DIM - 1)
