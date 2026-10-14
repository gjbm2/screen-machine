"""fMP4 boxes and media-socket framing (LIVE_V1 §1.4), without a GPU."""
from __future__ import annotations

import struct

from stream_service.encode.bitstream import StreamConfig
from stream_service.encode.fmp4 import TIMESCALE, init_segment, media_fragment
from stream_service.profiles import PROFILES
from stream_service.renderer import Engine, EngineListener


def boxes(data: bytes, start: int = 0, end: int | None = None):
    """Yield (type, payload_start, payload_end) for consecutive boxes."""
    end = len(data) if end is None else end
    i = start
    while i < end:
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        assert size >= 8 and i + size <= end, (kind, size)
        yield kind.decode(), i + 8, i + size
        i += size


def find(data: bytes, path: list[str], start: int = 0, end: int | None = None):
    for kind, a, b in boxes(data, start, end):
        if kind == path[0]:
            if len(path) == 1:
                return a, b
            # container boxes; stsd/sample entries need their header offsets skipped
            skip = {"stsd": 8, "hev1": 78, "avc1": 78, "av01": 78}.get(path[0], 0)
            return find(data, path[1:], a + skip, b)
    return None


def cfg(codec="hevc") -> StreamConfig:
    return StreamConfig(codec, {"hevc": "hev1", "h264": "avc1"}[codec], {"hevc": "hvcC", "h264": "avcC"}[codec],
                        b"\x01" * 23, "hev1.1.6.L153.B0", 3840, 2160, [])


def test_init_segment_is_low_delay_and_tagged():
    init = init_segment(cfg(), colr=PROFILES["hevc-2160p60"].colr())
    kinds = [k for k, _, _ in boxes(init)]
    assert kinds == ["ftyp", "moov"]
    a, b = find(init, ["moov", "mvhd"])
    version_flags, _, _, timescale, duration = struct.unpack(">IIIII", init[a:a + 20])
    assert version_flags >> 24 == 0 and duration == 0           # low-delay: duration 0 ...
    assert find(init, ["moov", "mvex", "mehd"]) is None          # ... and no mehd
    assert find(init, ["moov", "mvex", "trex"]) is not None
    colr = find(init, ["moov", "trak", "mdia", "minf", "stbl", "stsd", "hev1", "colr"])
    assert colr is not None
    a, b = colr
    assert init[a:a + 4] == b"nclx"
    prim, trc, matrix, full = struct.unpack(">HHHB", init[a + 4:b])
    assert (prim, trc, matrix, full) == (1, 13, 1, 0)            # bt709 / sRGB transfer / bt709 / limited
    assert find(init, ["moov", "trak", "mdia", "minf", "stbl", "stsd", "hev1", "hvcC"]) is not None


def test_bt709_trc_override():
    p = PROFILES["h264-720p30"].with_trc("bt709")
    assert p.colr() == (1, 1, 1, False)
    assert p.encoder_args()[:6] == ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def test_media_fragment_layout():
    sample = bytes(range(256)) * 10
    frag, moof_size = media_fragment(43, 42 * 1500, 1500, sample, keyframe=True)
    kinds = [(k, a, b) for k, a, b in boxes(frag)]
    assert [k for k, _, _ in kinds] == ["moof", "mdat"]
    assert kinds[0][2] == moof_size
    assert frag[kinds[1][1]:kinds[1][2]] == sample
    a, b = find(frag, ["moof", "traf", "tfdt"])
    vf, = struct.unpack(">I", frag[a:a + 4])
    assert vf >> 24 == 1 and struct.unpack(">Q", frag[a + 4:a + 12])[0] == 42 * 1500
    a, b = find(frag, ["moof", "traf", "trun"])
    _, count, data_offset, dur, size, flags = struct.unpack(">IIiIII", frag[a:a + 24])
    assert count == 1 and dur == 1500 and size == len(sample)
    assert data_offset == moof_size + 8                           # points at the mdat payload
    assert flags == 0x02000000                                    # sync sample


class Capture(EngineListener):
    def __init__(self):
        self.calls = []

    def on_encoder_config(self, cfg, config_msg, init_msg):
        self.calls.append(("config", config_msg, init_msg))

    def on_media(self, frame_id, key, msg):
        self.calls.append(("media", frame_id, key, msg))


def test_engine_media_messages():
    lis = Capture()
    e = Engine(profile=PROFILES["hevc-2160p60"], gpu=0, post=lambda fn, *a: fn(*a), listener=lis)
    e._on_config(cfg())
    kind, msg, init = lis.calls[0]
    assert msg == {"type": "config", "codec": "hevc", "codec_string": "hev1.1.6.L153.B0", "width": 3840,
                   "height": 2160, "fps": 60, "timescale": TIMESCALE, "frame_duration": 1500}
    assert init[0] == 0x01 and init[5:9] == b"ftyp"
    e._on_frame(300, True, b"\x00\x00\x00\x02\x26\x01", 0.0)
    _, fid, key, m = lis.calls[1]
    assert m[0] == 0x02 and m[1] == 1 and struct.unpack(">I", m[2:6])[0] == 300 == fid
    assert m[10:14] == b"moof"
    e._on_frame(301, False, b"\x00\x00\x00\x02\x02\x01", 0.0)
    assert lis.calls[2][3][1] == 0
