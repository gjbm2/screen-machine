"""4K60 plan change 2: the GPU encoder's bitstream plumbing (no GPU) and the renderer's encoder hand-off.

Golden test (shared plumbing): data/nvenc_320x180.hevc is 12 frames from the GPU encoder's exact options on the
P4000 (PyNvVideoCodec 2.2.3, an IDR forced at 7; packet sizes and GetSequenceParams() in the .json).  The references
are ffmpeg 6.1's FLV muxer on the same NAL units, i.e. today's path from the FLV on (iter_flv_video, SampleFixer):
  nvenc_320x180_ref.flv         ffmpeg -f hevc -r 30 -i <the NALs with 3-byte start codes> -c copy -f flv
  nvenc_320x180_ref_colour.flv  the same with -bsf:v hevc_metadata=colour_primaries=1:transfer_characteristics=13:
                                matrix_coefficients=1:video_full_range_flag=0 (the VUI today's ffmpeg path writes)
(3-byte start codes: ffmpeg's raw-HEVC demuxer otherwise keeps a 4-byte start code's leading zero at the end of the
NAL before it, which the live hevc_nvenc -> FLV path never sees.)  The hvcC, every sample, the keyframe flags, the
frame ids, the init segment and every fragment must be byte-identical.  The one intended difference from today's
stream is the encoder itself (PyNvVideoCodec's settings, its pic_timing SEI per frame): not the plumbing.
"""
from __future__ import annotations

import ctypes
import io
import json
import pathlib
import sys
import types

import numpy as np
import pytest

from stream_service.encode import bitstream as bs
from stream_service.encode.fmp4 import TIMESCALE, init_segment, media_fragment
from stream_service.media import MediaHub
from stream_service.profiles import PROFILES
from stream_service.renderer import Engine, EngineListener, _Slot

DATA = pathlib.Path(__file__).parent / "data"
COLR = PROFILES["hevc-2160p30"].colr()          # (1, 13, 1, False): bt709 / sRGB / bt709 / tv


def fixture():
    meta = json.loads((DATA / "nvenc_320x180.json").read_text())
    raw = (DATA / "nvenc_320x180.hevc").read_bytes()
    pkts, off = [], 0
    for p in meta["packets"]:
        pkts.append((p["ts"], p["picture_type"], raw[off:off + p["size"]]))
        off += p["size"]
    assert off == len(raw)
    return meta, bytes.fromhex(meta["seq_params"]), pkts


def flv(name):
    cfg, samples = None, []
    for p in bs.iter_flv_video(io.BytesIO((DATA / name).read_bytes())):
        if p.is_config:
            cfg = p.payload
        elif not p.is_end:
            samples.append((round(p.timestamp_ms * 30 / 1000), p.keyframe, p.payload))
    return cfg, samples


@pytest.mark.parametrize("ref,colr", [("nvenc_320x180_ref.flv", None), ("nvenc_320x180_ref_colour.flv", COLR)])
def test_golden_fmp4_matches_the_ffmpeg_flv_path(ref, colr):
    meta, seq, pkts = fixture()
    w, h, fps = meta["w"], meta["h"], meta["fps"]
    rcfg, rsamples = flv(ref)
    mux = bs.HevcAnnexB(w, h, colr)
    cfg = mux.on_param_sets(seq)
    assert cfg.record == rcfg                                         # hvcC
    theirs = bs.SampleFixer("hevc", w, h).on_config(rcfg)             # today's StreamConfig from the FLV
    assert (cfg.codec_string, cfg.sample_entry, cfg.config_box) == (theirs.codec_string, "hev1", "hvcC")
    assert init_segment(cfg, colr=COLR) == init_segment(theirs, colr=COLR)
    assert len(rsamples) == len(pkts) == 12
    dur = TIMESCALE // fps
    fixer = bs.SampleFixer("hevc", w, h)
    for (ts, ptype, data), (fid, rkey, rsample) in zip(pkts, rsamples):
        sample, key = mux.sample(data)
        assert (ts, key, sample) == (fid, rkey, fixer.fix_sample(rsample))
        assert key == (ptype == 3)                                    # NV_ENC_PIC_TYPE_IDR
        assert media_fragment(ts + 1, ts * dur, dur, sample, key) == media_fragment(fid + 1, fid * dur, dur,
                                                                                    rsample, rkey)
    assert [fid for fid, k, _ in rsamples if k] == [0, 7]             # the forced IDR
    assert mux.inband_param_sets == 2                                 # VPS/SPS/PPS on both IDRs (hev1)


def test_inband_parameter_sets_equal_get_sequence_params():
    _, seq, pkts = fixture()
    ps = [n for n in bs.annexb_nals(pkts[0][2]) if bs.hevc_nal_type(n) in (32, 33, 34)]
    assert ps == bs.annexb_nals(seq)
    assert [bs.hevc_nal_type(n) for n in bs.annexb_nals(pkts[0][2])] == [32, 33, 34, 39, 19]
    assert [bs.hevc_nal_type(n) for n in bs.annexb_nals(pkts[1][2])] == [39, 1]


def test_codec_string_and_record_fields():
    meta, seq, _ = fixture()
    cfg = bs.HevcAnnexB(meta["w"], meta["h"], COLR).on_param_sets(seq)
    rec = cfg.record
    assert rec[0] == 1 and rec[21] & 3 == 3 and rec[22] == 3            # version, 4-byte lengths, 3 arrays
    assert cfg.codec_string.startswith("hev1.1.") and bs.hvcc_length_size(rec) == 4
    sets = bs.hvcc_parameter_sets(rec)
    assert sorted(sets) == [32, 33, 34] and all(len(v) == 1 for v in sets.values())


def _sps(seq):
    return next(n for n in bs.annexb_nals(seq) if bs.hevc_nal_type(n) == 33)


def _vui_signal(sps):
    pos = {}
    bs._hevc_sps(bs.BitReader(bs.unescape_rbsp(sps[2:])), bs._hvcc_rec(), pos)
    if "vst" not in pos:
        return None
    r = bs.BitReader(bs.unescape_rbsp(sps[2:]))
    r.pos = pos["vst"]
    present, fmt, full, desc = r.u(1), r.u(3), r.u(1), r.u(1)
    return present, fmt, full, desc, r.u(8), r.u(8), r.u(8)


def test_sps_colour_rewrite_sets_only_the_video_signal_type():
    _, seq, _ = fixture()
    sps = _sps(seq)
    assert _vui_signal(sps)[0] == 0                                   # NVENC writes a VUI without a video signal type
    out = bs.hevc_sps_with_colour(sps, (1, 13, 1, False))
    assert _vui_signal(out) == (1, 5, 0, 1, 1, 13, 1)
    assert bs.hevc_sps_with_colour(out, (1, 13, 1, False)) == out     # idempotent
    full = bs.hevc_sps_with_colour(sps, (1, 1, 1, True))
    assert _vui_signal(full) == (1, 5, 1, 1, 1, 1, 1)
    a, b = {}, {}
    ra, rb = bs._hvcc_rec(), bs._hvcc_rec()
    bs._hevc_sps(bs.BitReader(bs.unescape_rbsp(sps[2:])), ra, a)
    bs._hevc_sps(bs.BitReader(bs.unescape_rbsp(out[2:])), rb, b)
    assert ra == rb                                                   # profile, level, chroma, depth ... unchanged
    assert a["vst_end"] - a["vst"] == 1 and b["vui_end"] - a["vui_end"] == 3 + 1 + 1 + 24


def test_sps_without_vui_gets_one():
    _, seq, _ = fixture()
    sps = _sps(seq)
    rbsp = bs.unescape_rbsp(sps[2:])
    pos = {}
    bs._hevc_sps(bs.BitReader(rbsp), bs._hvcc_rec(), pos)
    w = bs.BitWriter()                                                # the same SPS with vui_parameters_present 0
    w.copy_bits(rbsp, 0, pos["vui_flag"])
    w.u(1, 0)
    w.copy_bits(rbsp, pos["vui_end"], bs._stop_bit(rbsp))
    bare = sps[:2] + bs.escape_rbsp(w.trailing())
    assert _vui_signal(bare) is None
    out = bs.hevc_sps_with_colour(bare, COLR)
    assert _vui_signal(out) == (1, 5, 0, 1, 1, 13, 1)
    p2 = {}
    r = bs.BitReader(bs.unescape_rbsp(out[2:]))
    bs._hevc_sps(r, bs._hvcc_rec(), p2)
    tail = bs.BitReader(bs.unescape_rbsp(out[2:]))                    # the bits after the VUI are the original's
    tail.pos = p2["vui_end"]
    orig = bs.BitReader(rbsp)
    orig.pos = pos["vui_end"]
    n = bs._stop_bit(rbsp) - pos["vui_end"]
    assert [tail.u(1) for _ in range(n)] == [orig.u(1) for _ in range(n)]


def test_annexb_nals_cut_like_ffmpeg():
    a, b, c = b"\x40\x01\xaa", b"\x42\x01\xbb\x00", b"\x26\x01\xcc"
    assert bs.annexb_nals(b"\x00\x00\x00\x01" + a + b"\x00\x00\x00\x01" + b[:-1] + b"\x00\x00\x01" + c) == [a, b[:-1], c]
    # one zero before a start code is the 4-byte start code's; a second stays with the NAL (nal_parse_units)
    assert bs.annexb_nals(b"\x00\x00\x01" + a + b"\x00\x00\x00\x00\x01" + c) == [a + b"\x00", c]
    assert bs.annexb_nals(b"\x00\x00\x01" + c + b"\x00") == [c + b"\x00"]       # the last runs to the end
    assert bs.annexb_nals(b"junk") == []


# -- GpuEncoder through a fake PyNvVideoCodec (replays the fixture, 3 frames late like the real one) -----------------
class FakeNvc(types.ModuleType):
    def __init__(self, seq, pkts, fail_at=None):
        super().__init__("PyNvVideoCodec")
        self.made, outer = [], self

        class Flag:
            def __init__(self, v):
                self.value = v

        class Flags:
            FORCEIDR, OUTPUT_SPSPPS = Flag(4), Flag(8)

        class PicParams:
            inputTimeStamp = 0
            encodePicFlags = 0

        class Enc:
            def __init__(self, kw):
                self.kw, self.queue, self.flags = kw, [], []

            def GetNumBFrames(self):
                return 0

            def GetSequenceParams(self):
                return seq

            def Encode(self, frame, pp):
                if pp.inputTimeStamp == fail_at:
                    raise RuntimeError("encoder exploded")
                self.flags.append((pp.inputTimeStamp, pp.encodePicFlags))
                self.queue.append(pp.inputTimeStamp)
                if len(self.queue) <= 3:
                    return []
                ts = self.queue.pop(0)
                return [{"data": pkts[ts][2], "timestamp": ts, "picture_type": pkts[ts][1]}]

            def EndEncode(self):
                return []

        def create(w, h, fmt, cpu, **kw):
            e = Enc({"w": w, "h": h, "fmt": fmt, "cpu": cpu, **kw})
            outer.made.append(e)
            return e

        self.CreateEncoder, self.NV_ENC_PIC_FLAGS, self.NV_ENC_PIC_PARAMS = create, Flags, PicParams


class FakeCp:
    """Just enough of CuPy for the encoder and the fallback: 'device' memory is host memory here."""

    def __init__(self):
        cp = self

        class Device:
            id = 1                          # a GPU 1 context: GpuEncoder passes it as gpu_id

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def use(self):
                pass

        class Stream:
            ptr = 7

            def __init__(self, non_blocking=False):
                pass

            def synchronize(self):
                pass

        class runtime:
            memcpyDeviceToHost = 2

            @staticmethod
            def memcpyAsync(dst, src, n, kind, stream):
                ctypes.memmove(dst, src, n)

        class driver:
            @staticmethod
            def ctxGetCurrent():
                return 1234

        self.cuda = types.SimpleNamespace(Device=Device, Stream=Stream, runtime=runtime, driver=driver,
                                          alloc_pinned_memory=lambda n: bytearray(n))
        del cp


class Dev:
    def __init__(self, arr):
        self.arr = arr
        self.data = types.SimpleNamespace(ptr=arr.ctypes.data)


def test_gpu_encoder_options_frames_and_forced_idr(monkeypatch):
    meta, seq, pkts = fixture()
    nvc = FakeNvc(seq, pkts)
    monkeypatch.setitem(sys.modules, "PyNvVideoCodec", nvc)
    from stream_service.encode.nvenc import GpuEncoder

    got, cfgs = [], []
    enc = GpuEncoder("hevc", meta["w"], meta["h"], 30, cp=FakeCp(), stream=types.SimpleNamespace(ptr=99),
                     bitrate="30M", gop_seconds=1.0, colr=COLR, clock=lambda: 5.0, on_config=cfgs.append,
                     on_frame=lambda *a: got.append(a))
    kw = nvc.made[0].kw
    assert kw["cudacontext"] == 1234 and kw["cudastream"] == 99 and kw["fmt"] == "NV12" and kw["cpu"] is False
    assert kw["gpu_id"] == "1"                  # the context's own device, not PyNvVideoCodec's default 0
    assert {k: kw[k] for k in ("codec", "preset", "tuning_info", "rc", "fps", "gop", "idrperiod", "bf", "bitrate",
                               "maxbitrate", "vbvbufsize", "repeatspspps", "aq")} == {
        "codec": "hevc", "preset": "P4", "tuning_info": "ultra_low_latency", "rc": "cbr", "fps": "30", "gop": "30",
        "idrperiod": "30", "bf": "0", "bitrate": "30000000", "maxbitrate": "30000000", "vbvbufsize": "60000000",
        "repeatspspps": "1", "aq": "12"}
    assert len(cfgs) == 1 and cfgs[0].record == flv("nvenc_320x180_ref_colour.flv")[0]
    for i in range(12):
        assert enc.submit(object(), i, idr=(i == 5)) == 5.0
    assert [g[0] for g in got] == list(range(9)) and got[0][1] and not got[1][1]
    assert dict(nvc.made[0].flags)[5] == 4 | 8 and dict(nvc.made[0].flags)[4] == 0
    enc.close()
    enc.close()
    with pytest.raises(RuntimeError):
        enc.submit(object(), 12)


def test_gpu_encoder_refuses_other_codecs(monkeypatch):
    monkeypatch.setitem(sys.modules, "PyNvVideoCodec", FakeNvc(b"", []))
    from stream_service.encode.nvenc import GpuEncoder

    with pytest.raises(ValueError):
        GpuEncoder("h264", 64, 64, 30, cp=FakeCp(), stream=None, bitrate="1M", clock=float, on_config=print,
                   on_frame=print)


# -- the renderer's hand-off --------------------------------------------------------------------------------------
class Capture(EngineListener):
    def __init__(self):
        self.configs, self.media, self.failed = [], [], []

    def on_encoder_config(self, cfg, config_msg, init_msg):
        self.configs.append((cfg, config_msg, init_msg))

    def on_media(self, frame_id, key, msg):
        self.media.append((frame_id, key, msg))

    def on_failed(self, detail):
        self.failed.append(detail)


def engine(profile="hevc-2160p30"):
    lis = Capture()
    e = Engine(profile=PROFILES[profile], gpu=0, post=lambda fn, *a: fn(*a), listener=lis, name="s-t")
    return e, lis


def test_engine_reports_the_encoder_and_defaults_to_ffmpeg():
    e, _ = engine()
    s = e.summary()
    assert s["encoder"] == "ffmpeg" and s["encoder_fallback"] is None and s["forced_idrs"] == 0
    assert s["encode_cpu_ms"]["n"] == 0 and "slot_to_packet_ms" in s
    e.encoder = "nvenc"
    assert e.summary()["encoder"] == "nvenc"                            # wanted, before start
    assert "enc_cpu_ms" in e.window_stats(2.0) and e.window_stats(2.0)["nvenc_delay_frames"] == 1   # ffmpeg's


def test_engine_falls_back_at_start_when_nvenc_cannot_be_made(monkeypatch):
    monkeypatch.setitem(sys.modules, "PyNvVideoCodec", None)          # import fails
    e, _ = engine()
    e.ctx = types.SimpleNamespace(cp=FakeCp(), stream=types.SimpleNamespace(ptr=1))
    assert e._make_gpu_encoder() is None
    assert e.encoder_fallback.startswith("NVENC unavailable: ") and "PyNvVideoCodec" in e.encoder_fallback


def test_idr_grid_hold_frames_and_joins():
    e, _ = engine()                                                   # 30 fps, gop 1 s
    assert [n for n in range(0, 95) if e._idr_due(n)] == [0, 30, 60, 90]
    e.request_keyframe()                                              # before the first packet: nothing to do
    assert not e._idr_due(95)
    e.frames_encoded = 10
    e.request_keyframe()
    e.request_keyframe()                                              # two joins in one frame: one IDR
    e._idr_frames.add(101)                                            # the hold frame
    e._idr_frames.add(120)                                            # on the grid anyway: not counted as forced
    assert [n for n in range(96, 125) if e._idr_due(n)] == [96, 101, 120]
    assert e.forced_idrs == 2 and not e._idr_frames


def test_engine_messages_from_the_gpu_encoder_match_the_ffmpeg_path(monkeypatch):
    meta, seq, pkts = fixture()
    monkeypatch.setitem(sys.modules, "PyNvVideoCodec", FakeNvc(seq, pkts))
    e, lis = engine()
    e.w, e.h = meta["w"], meta["h"]
    e.ctx = types.SimpleNamespace(cp=FakeCp(), stream=types.SimpleNamespace(ptr=1))
    gpu = e._gpu_enc = e._make_gpu_encoder()
    assert e.window_stats(2.0)["nvenc_delay_frames"] == 3            # the diagnostics' encoder budget: NVENC's delay
    for i in range(12):
        gpu.submit(object(), i, e._idr_due(i))
    ref, _ = engine()                                                 # the ffmpeg path: SampleFixer on the FLV
    rcfg, rs = flv("nvenc_320x180_ref_colour.flv")
    ref.w, ref.h = meta["w"], meta["h"]
    ref._on_config(bs.SampleFixer("hevc", meta["w"], meta["h"]).on_config(rcfg))
    for fid, key, sample in rs[:9]:
        ref._on_frame(fid, key, sample, 0.0)
    rl = ref.listener
    assert lis.configs[0][1:] == rl.configs[0][1:]                    # config message and init segment
    assert lis.media == rl.media and e._next_id == 9


def test_mid_session_failure_falls_back_to_ffmpeg_and_keeps_the_timeline(monkeypatch):
    meta, seq, pkts = fixture()
    monkeypatch.setitem(sys.modules, "PyNvVideoCodec", FakeNvc(seq, pkts, fail_at=8))
    e, lis = engine()
    e.ctx = types.SimpleNamespace(cp=FakeCp(), stream=types.SimpleNamespace(ptr=1))
    e.nv12_bytes = 64
    gpu = e._gpu_enc = e.pipeline = e._make_gpu_encoder()
    e.encoder_active = "nvenc"

    class Pipe:
        def __init__(self, id_base):
            self.id_base, self.frames, self.closed = id_base, [], False

        def submit(self, frame, frame_id=-1, idr=False):
            self.frames.append(bytes(frame))

        def close(self):
            self.closed = True
    pipes = []
    monkeypatch.setattr(e, "_ffmpeg_pipeline", lambda id_base=0: pipes.append(Pipe(id_base)) or pipes[-1])
    slots = []
    for i in range(10):
        arr = np.full(64, i, np.uint8)
        slots.append(_Slot(i, None, None, dev=Dev(arr), surface=object()))
    for i in range(10):
        e._submit(i, slots[i])
    assert e.encoder_active == "ffmpeg" and "NVENC failed mid-session: RuntimeError: encoder exploded" in \
        e.encoder_fallback and e._gpu_enc is None and gpu.enc is None
    p = pipes[0]
    assert p.id_base == 5 and e.pipeline is p                         # frames 0-4 came out of NVENC; 5-7 were held
    assert [f[0] for f in p.frames] == [8, 8, 8, 8, 9]                # 5, 6, 7 re-sent as frame 8, then 8, 9
    assert e.summary()["encoder"] == "ffmpeg"


def test_media_hub_reannounces_a_new_encoder_and_asks_for_keys():
    hub = MediaHub("s", 30)
    asks = []
    hub.want_key = lambda: asks.append(1)

    class C:
        def __init__(self):
            self.announced, self.needs_key, self.dropped, self.control, self.media = False, True, 0, [], []
            self.streaming = False

        def push_control(self, m):
            self.control.append(m)
    a = C()
    hub.clients[1] = a
    hub.set_config({"type": "config", "codec_string": "x"}, b"\x01init1")
    assert a.announced and a.control[1] == b"\x01init1" and len(asks) == 1
    a.needs_key, a.media = False, [b"old", b"older"]
    hub.set_config({"type": "config", "codec_string": "x"}, b"\x01init2")
    assert a.control[-1] == b"\x01init2" and a.needs_key and a.media == [] and a.dropped == 2 and len(asks) == 2
