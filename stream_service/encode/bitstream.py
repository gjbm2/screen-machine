"""Bitstream helpers for the streaming latency lab.

* FLV tag reader (ffmpeg writes one tag per encoded packet, length-prefixed,
  so each frame can be taken off the pipe the moment it is encoded -- unlike
  Annex-B output, where a frame's end is only known when the next start code
  arrives, i.e. one frame later).
* H.264 SPS rewriter that adds VUI ``bitstream_restriction`` with
  ``max_num_reorder_frames = 0``.  NVENC (driver 566 / ffmpeg 6.1) omits it,
  and a spec-following decoder must then assume the reorder depth equals the
  whole DPB (~5 frames at 4K L5.1, 16 at 1080p), i.e. hold frames back.
* Decoder configuration records (avcC / hvcC / av1C) and RFC 6381 codec
  strings for MSE / WebCodecs.
"""
from __future__ import annotations

import dataclasses
import struct
from typing import BinaryIO, Iterator, Optional

# --------------------------------------------------------------------------
# FLV
# --------------------------------------------------------------------------

FLV_TAG_VIDEO = 9

# Enhanced-RTMP packet types (ffmpeg >= 6.1 uses these for hevc / av1)
EX_SEQUENCE_START = 0
EX_CODED_FRAMES = 1
EX_SEQUENCE_END = 2
EX_CODED_FRAMES_X = 3


@dataclasses.dataclass
class FlvVideoPacket:
    timestamp_ms: int
    keyframe: bool
    is_config: bool        # True -> payload is a decoder config record
    is_end: bool           # end-of-sequence marker
    codec: str             # 'h264' | 'hevc' | 'av1'
    cts_ms: int            # composition time offset (should be 0 with no B-frames)
    payload: bytes         # config record, or length-prefixed NALs / OBUs


def _read_exact(f: BinaryIO, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = f.read(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return bytes(buf)


def _si24(b: bytes) -> int:
    v = int.from_bytes(b, "big")
    return v - (1 << 24) if v & 0x800000 else v


def iter_flv_video(f: BinaryIO) -> Iterator[FlvVideoPacket]:
    """Yield video packets from an FLV byte stream as soon as each tag is complete."""
    header = _read_exact(f, 9)
    if header[:3] != b"FLV":
        raise ValueError(f"not an FLV stream: {header[:3]!r}")
    data_offset = int.from_bytes(header[5:9], "big")
    if data_offset > 9:
        _read_exact(f, data_offset - 9)
    _read_exact(f, 4)  # PreviousTagSize0
    while True:
        try:
            th = _read_exact(f, 11)
        except EOFError:
            return
        tag_type = th[0] & 0x1F
        size = int.from_bytes(th[1:4], "big")
        ts = int.from_bytes(th[4:7], "big") | (th[7] << 24)
        body = _read_exact(f, size)
        _read_exact(f, 4)  # PreviousTagSize
        if tag_type != FLV_TAG_VIDEO or not body:
            continue
        b0 = body[0]
        if b0 & 0x80:  # enhanced RTMP
            frame_type = (b0 >> 4) & 0x07
            ptype = b0 & 0x0F
            fourcc = body[1:5]
            codec = {b"hvc1": "hevc", b"av01": "av1", b"avc1": "h264"}.get(fourcc)
            if codec is None:
                raise ValueError(f"unsupported enhanced FLV fourcc {fourcc!r}")
            rest = body[5:]
            cts = 0
            if ptype == EX_CODED_FRAMES and codec in ("hevc", "h264"):
                cts, rest = _si24(rest[:3]), rest[3:]
            yield FlvVideoPacket(ts, frame_type == 1, ptype == EX_SEQUENCE_START,
                                 ptype == EX_SEQUENCE_END, codec, cts, bytes(rest))
        else:  # legacy: codec id 7 = AVC
            frame_type = b0 >> 4
            codec_id = b0 & 0x0F
            if codec_id != 7:
                raise ValueError(f"unsupported legacy FLV codec id {codec_id}")
            ptype = body[1]
            cts = _si24(body[2:5])
            yield FlvVideoPacket(ts, frame_type == 1, ptype == 0, ptype == 2,
                                 "h264", cts, bytes(body[5:]))


# --------------------------------------------------------------------------
# NAL / RBSP utilities
# --------------------------------------------------------------------------

def unescape_rbsp(nal_payload: bytes) -> bytes:
    """Remove emulation-prevention bytes (00 00 03 -> 00 00)."""
    out = bytearray()
    zeros = 0
    for byte in nal_payload:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def escape_rbsp(rbsp: bytes) -> bytes:
    """Insert emulation-prevention bytes where needed."""
    out = bytearray()
    zeros = 0
    for byte in rbsp:
        if zeros >= 2 and byte <= 3:
            out.append(3)
            zeros = 0
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def split_length_prefixed(data: bytes, length_size: int = 4) -> list[bytes]:
    nals, i = [], 0
    while i + length_size <= len(data):
        n = int.from_bytes(data[i:i + length_size], "big")
        i += length_size
        if n <= 0 or i + n > len(data):
            raise ValueError(f"bad NAL length {n} at {i - length_size}/{len(data)}")
        nals.append(data[i:i + n])
        i += n
    if i != len(data):
        raise ValueError("trailing bytes after length-prefixed NAL units")
    return nals


def join_length_prefixed(nals: list[bytes], length_size: int = 4) -> bytes:
    return b"".join(len(n).to_bytes(length_size, "big") + n for n in nals)


class BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0  # bit position

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            byte = self.data[self.pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("exp-golomb overflow")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


class BitWriter:
    def __init__(self):
        self.bits: list[int] = []

    def u(self, n: int, v: int) -> None:
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def ue(self, v: int) -> None:
        v += 1
        n = v.bit_length()
        self.u(n - 1, 0)
        self.u(n, v)

    def copy_bits(self, data: bytes, start: int, end: int) -> None:
        for p in range(start, end):
            self.bits.append((data[p >> 3] >> (7 - (p & 7))) & 1)

    def trailing(self) -> bytes:
        self.bits.append(1)
        while len(self.bits) % 8:
            self.bits.append(0)
        out = bytearray()
        for i in range(0, len(self.bits), 8):
            byte = 0
            for b in self.bits[i:i + 8]:
                byte = (byte << 1) | b
            out.append(byte)
        return bytes(out)


# --------------------------------------------------------------------------
# H.264 SPS parsing / rewriting
# --------------------------------------------------------------------------

_HIGH_PROFILES = {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}


@dataclasses.dataclass
class H264SpsInfo:
    profile_idc: int
    constraint_flags: int
    level_idc: int
    width: int
    height: int
    poc_type: int
    max_num_ref_frames: int
    vui_present: bool
    bitstream_restriction: bool
    max_num_reorder_frames: Optional[int]
    max_dec_frame_buffering: Optional[int]
    # bit offsets in the RBSP (header byte excluded) used by the rewriter
    _pos_vui_flag: int = 0
    _pos_restriction_flag: int = 0


def _skip_scaling_list(r: BitReader, size: int) -> None:
    last, nxt = 8, 8
    for _ in range(size):
        if nxt != 0:
            nxt = (last + r.se() + 256) % 256
        last = nxt if nxt != 0 else last


def _skip_hrd(r: BitReader) -> None:
    cpb_cnt = r.ue() + 1
    r.u(4); r.u(4)
    for _ in range(cpb_cnt):
        r.ue(); r.ue(); r.u(1)
    r.u(5); r.u(5); r.u(5); r.u(5)


def parse_h264_sps(nal: bytes) -> H264SpsInfo:
    """Parse an SPS NAL unit (with its 1-byte header, emulation bytes allowed)."""
    if nal[0] & 0x1F != 7:
        raise ValueError("not an SPS NAL")
    rbsp = unescape_rbsp(nal[1:])
    r = BitReader(rbsp)
    profile_idc = r.u(8); constraint = r.u(8); level_idc = r.u(8)
    r.ue()  # seq_parameter_set_id
    chroma_format_idc = 1
    if profile_idc in _HIGH_PROFILES:
        chroma_format_idc = r.ue()
        if chroma_format_idc == 3:
            r.u(1)
        r.ue(); r.ue(); r.u(1)
        if r.u(1):  # seq_scaling_matrix_present_flag
            for i in range(8 if chroma_format_idc != 3 else 12):
                if r.u(1):
                    _skip_scaling_list(r, 16 if i < 6 else 64)
    r.ue()  # log2_max_frame_num_minus4
    poc_type = r.ue()
    if poc_type == 0:
        r.ue()
    elif poc_type == 1:
        r.u(1); r.se(); r.se()
        for _ in range(r.ue()):
            r.se()
    max_num_ref_frames = r.ue()
    r.u(1)
    w_mbs = r.ue() + 1
    h_map = r.ue() + 1
    frame_mbs_only = r.u(1)
    if not frame_mbs_only:
        r.u(1)
    r.u(1)  # direct_8x8_inference_flag
    crop = [0, 0, 0, 0]
    if r.u(1):
        crop = [r.ue(), r.ue(), r.ue(), r.ue()]
    sub_w = 1 if chroma_format_idc == 3 else 2
    sub_h = 2 if chroma_format_idc == 1 else 1
    if chroma_format_idc == 0:
        sub_w = sub_h = 1
    height_mul = 2 - frame_mbs_only
    width = w_mbs * 16 - sub_w * (crop[0] + crop[1])
    height = h_map * 16 * height_mul - sub_h * height_mul * (crop[2] + crop[3])
    pos_vui = r.pos
    vui = r.u(1)
    info = H264SpsInfo(profile_idc, constraint, level_idc, width, height, poc_type,
                       max_num_ref_frames, bool(vui), False, None, None, _pos_vui_flag=pos_vui)
    if not vui:
        return info
    if r.u(1):  # aspect_ratio_info_present_flag
        if r.u(8) == 255:
            r.u(16); r.u(16)
    if r.u(1):  # overscan_info_present_flag
        r.u(1)
    if r.u(1):  # video_signal_type_present_flag
        r.u(3); r.u(1)
        if r.u(1):
            r.u(8); r.u(8); r.u(8)
    if r.u(1):  # chroma_loc_info_present_flag
        r.ue(); r.ue()
    if r.u(1):  # timing_info_present_flag
        r.u(32); r.u(32); r.u(1)
    nal_hrd = r.u(1)
    if nal_hrd:
        _skip_hrd(r)
    vcl_hrd = r.u(1)
    if vcl_hrd:
        _skip_hrd(r)
    if nal_hrd or vcl_hrd:
        r.u(1)  # low_delay_hrd_flag
    r.u(1)  # pic_struct_present_flag
    info._pos_restriction_flag = r.pos
    if r.u(1):
        info.bitstream_restriction = True
        r.u(1); r.ue(); r.ue(); r.ue(); r.ue()
        info.max_num_reorder_frames = r.ue()
        info.max_dec_frame_buffering = r.ue()
    return info


def _write_restriction(w: BitWriter, max_dec_frame_buffering: int) -> None:
    w.u(1, 1)    # bitstream_restriction_flag
    w.u(1, 1)    # motion_vectors_over_pic_boundaries_flag
    w.ue(0)      # max_bytes_per_pic_denom (0 = unconstrained)
    w.ue(0)      # max_bits_per_mb_denom (0 = unconstrained)
    w.ue(15)     # log2_max_mv_length_horizontal (generous)
    w.ue(15)     # log2_max_mv_length_vertical
    w.ue(0)      # max_num_reorder_frames  <-- the point of the exercise
    w.ue(max_dec_frame_buffering)


def rewrite_h264_sps_zero_reorder(nal: bytes, max_dec_frame_buffering: Optional[int] = None) -> bytes:
    """Return an SPS NAL that signals max_num_reorder_frames=0.

    Everything before the VUI bitstream_restriction position is copied
    bit-for-bit; only the tail of the VUI is (re)written.  If the SPS has no
    VUI at all, a minimal VUI carrying only bitstream_restriction is added.
    """
    info = parse_h264_sps(nal)
    if info.bitstream_restriction and info.max_num_reorder_frames == 0:
        return nal
    mdfb = info.max_num_ref_frames if max_dec_frame_buffering is None else max_dec_frame_buffering
    mdfb = max(mdfb, info.max_num_ref_frames)  # spec: >= max_num_ref_frames
    rbsp = unescape_rbsp(nal[1:])
    w = BitWriter()
    if info.vui_present:
        w.copy_bits(rbsp, 0, info._pos_restriction_flag)
    else:
        w.copy_bits(rbsp, 0, info._pos_vui_flag)
        w.u(1, 1)  # vui_parameters_present_flag
        # aspect, overscan, video_signal, chroma_loc, timing, nal_hrd, vcl_hrd, pic_struct = 0
        w.u(8, 0)
    _write_restriction(w, mdfb)
    return nal[:1] + escape_rbsp(w.trailing())


# --------------------------------------------------------------------------
# Decoder configuration records & codec strings
# --------------------------------------------------------------------------

def parse_avcc(avcc: bytes) -> tuple[list[bytes], list[bytes], int, bytes]:
    """-> (sps_list, pps_list, length_size, trailing_extension_bytes)"""
    if avcc[0] != 1:
        raise ValueError("bad avcC version")
    length_size = (avcc[4] & 3) + 1
    n_sps = avcc[5] & 0x1F
    i = 6
    sps = []
    for _ in range(n_sps):
        ln = int.from_bytes(avcc[i:i + 2], "big"); i += 2
        sps.append(avcc[i:i + ln]); i += ln
    n_pps = avcc[i]; i += 1
    pps = []
    for _ in range(n_pps):
        ln = int.from_bytes(avcc[i:i + 2], "big"); i += 2
        pps.append(avcc[i:i + ln]); i += ln
    return sps, pps, length_size, avcc[i:]


def build_avcc(sps: list[bytes], pps: list[bytes], length_size: int = 4, ext: bytes = b"") -> bytes:
    s0 = sps[0]
    out = bytearray([1, s0[1], s0[2], s0[3], 0xFC | (length_size - 1), 0xE0 | len(sps)])
    for s in sps:
        out += len(s).to_bytes(2, "big") + s
    out.append(len(pps))
    for p in pps:
        out += len(p).to_bytes(2, "big") + p
    return bytes(out) + ext


def avc_codec_string(sps_nal: bytes) -> str:
    return "avc1.%02X%02X%02X" % (sps_nal[1], sps_nal[2], sps_nal[3])


def hevc_codec_string(hvcc: bytes, sample_entry: str = "hvc1") -> str:
    """RFC 6381 / ISO 14496-15 Annex E codec string from an hvcC record."""
    b1 = hvcc[1]
    profile_space = (b1 >> 6) & 3
    tier = (b1 >> 5) & 1
    profile_idc = b1 & 0x1F
    compat = int.from_bytes(hvcc[2:6], "big")
    constraints = hvcc[6:12]
    level_idc = hvcc[12]
    rev = int(f"{compat:032b}"[::-1], 2)  # bit-reversed per spec
    ps = {0: "", 1: "A", 2: "B", 3: "C"}[profile_space]
    cons = list(constraints)
    while cons and cons[-1] == 0:
        cons.pop()
    s = f"{sample_entry}.{ps}{profile_idc}.{rev:X}.{'H' if tier else 'L'}{level_idc}"
    if cons:
        s += "." + ".".join(f"{c:X}" for c in cons)
    return s


def av1_codec_string(av1c: bytes) -> str:
    """av01.P.LLT.DD from an av1C record."""
    b1, b2 = av1c[1], av1c[2]
    profile = b1 >> 5
    level = b1 & 0x1F
    tier = "H" if b2 & 0x80 else "M"
    high_bd, twelve = (b2 >> 6) & 1, (b2 >> 5) & 1
    depth = 12 if (high_bd and twelve) else 10 if high_bd else 8
    return f"av01.{profile}.{level:02d}{tier}.{depth:02d}"


def hvcc_parameter_sets(hvcc: bytes) -> dict[int, list[bytes]]:
    num_arrays = hvcc[22]
    i = 23
    out: dict[int, list[bytes]] = {}
    for _ in range(num_arrays):
        nal_type = hvcc[i] & 0x3F; i += 1
        n = int.from_bytes(hvcc[i:i + 2], "big"); i += 2
        for _ in range(n):
            ln = int.from_bytes(hvcc[i:i + 2], "big"); i += 2
            out.setdefault(nal_type, []).append(hvcc[i:i + ln]); i += ln
    return out


def hvcc_length_size(hvcc: bytes) -> int:
    return (hvcc[21] & 3) + 1


# --------------------------------------------------------------------------
# AV1 OBU helpers
# --------------------------------------------------------------------------

OBU_TEMPORAL_DELIMITER = 2


def _leb128(data: bytes, i: int) -> tuple[int, int]:
    v = 0
    for k in range(8):
        b = data[i + k]
        v |= (b & 0x7F) << (7 * k)
        if not b & 0x80:
            return v, i + k + 1
    raise ValueError("leb128 too long")


def strip_av1_temporal_delimiters(tu: bytes) -> bytes:
    """ISO-BMFF AV1 samples must not contain temporal-delimiter OBUs."""
    out = bytearray()
    i = 0
    while i < len(tu):
        start = i
        header = tu[i]
        obu_type = (header >> 3) & 0x0F
        has_ext = (header >> 2) & 1
        has_size = (header >> 1) & 1
        i += 1 + has_ext
        if not has_size:
            # size runs to end of TU; keep as-is
            if obu_type != OBU_TEMPORAL_DELIMITER:
                out += tu[start:]
            break
        size, i = _leb128(tu, i)
        end = i + size
        if obu_type != OBU_TEMPORAL_DELIMITER:
            out += tu[start:end]
        i = end
    return bytes(out)


# --------------------------------------------------------------------------
# Per-codec stream state: config record + per-sample fix-ups
# --------------------------------------------------------------------------

@dataclasses.dataclass
class StreamConfig:
    codec: str            # h264 | hevc | av1
    sample_entry: str     # avc1 | hev1 | hvc1 | av01
    config_box: str       # avcC | hvcC | av1C
    record: bytes         # decoder config record (box payload; also WebCodecs description)
    codec_string: str
    width: int
    height: int
    notes: list[str]


class SampleFixer:
    """Turns FLV packets into MP4/WebCodecs-ready samples + config."""

    def __init__(self, codec: str, width: int, height: int, fix_reorder: bool = True):
        self.codec = codec
        self.width, self.height = width, height
        self.fix_reorder = fix_reorder
        self.config: Optional[StreamConfig] = None
        self._sps_cache: dict[bytes, bytes] = {}
        self.inband_param_sets = 0

    def _fix_sps(self, sps: bytes) -> bytes:
        if not self.fix_reorder:
            return sps
        out = self._sps_cache.get(sps)
        if out is None:
            out = rewrite_h264_sps_zero_reorder(sps)
            self._sps_cache[sps] = out
        return out

    def on_config(self, record: bytes) -> StreamConfig:
        notes: list[str] = []
        if self.codec == "h264":
            sps, pps, ls, ext = parse_avcc(record)
            if ls != 4:
                raise ValueError(f"expected 4-byte NAL lengths, got {ls}")
            before = parse_h264_sps(sps[0])
            sps = [self._fix_sps(s) for s in sps]
            after = parse_h264_sps(sps[0])
            notes.append(
                f"SPS profile={before.profile_idc} level={before.level_idc} poc_type={before.poc_type} "
                f"refs={before.max_num_ref_frames} restriction={before.bitstream_restriction}"
                f"->{after.bitstream_restriction} reorder={before.max_num_reorder_frames}->{after.max_num_reorder_frames} "
                f"dec_buf={after.max_dec_frame_buffering}")
            rec = build_avcc(sps, pps, 4, ext)
            self.config = StreamConfig("h264", "avc1", "avcC", rec, avc_codec_string(sps[0]),
                                       before.width, before.height, notes)
        elif self.codec == "hevc":
            if hvcc_length_size(record) != 4:
                raise ValueError("expected 4-byte NAL lengths in hvcC")
            # 'hev1' allows in-band parameter sets (NVENC may repeat them on IDRs)
            self.config = StreamConfig("hevc", "hev1", "hvcC", record,
                                       hevc_codec_string(record, "hev1"), self.width, self.height, notes)
        elif self.codec == "av1":
            self.config = StreamConfig("av1", "av01", "av1C", record, av1_codec_string(record),
                                       self.width, self.height, notes)
        else:
            raise ValueError(self.codec)
        return self.config

    def fix_sample(self, payload: bytes) -> bytes:
        if self.codec == "h264":
            nals = split_length_prefixed(payload)
            changed = False
            for k, n in enumerate(nals):
                t = n[0] & 0x1F
                if t == 7:
                    self.inband_param_sets += 1
                    fixed = self._fix_sps(n)
                    if fixed is not n:
                        nals[k] = fixed
                        changed = True
            return join_length_prefixed(nals) if changed else payload
        if self.codec == "hevc":
            for n in split_length_prefixed(payload):
                if ((n[0] >> 1) & 0x3F) in (32, 33, 34):
                    self.inband_param_sets += 1
                    break
            return payload
        if self.codec == "av1":
            return strip_av1_temporal_delimiters(payload)
        raise ValueError(self.codec)


def nal_types(sample: bytes, codec: str) -> list[int]:
    out = []
    for n in split_length_prefixed(sample):
        out.append(n[0] & 0x1F if codec == "h264" else (n[0] >> 1) & 0x3F)
    return out


def u32(v: int) -> bytes:
    return struct.pack(">I", v)
