"""Minimal fragmented-MP4 writer: one moof+mdat per frame.

Hand-written instead of ffmpeg's mov muxer so that a fragment can be emitted
the instant its frame is encoded: the sample duration is known a priori
(constant frame rate), so nothing waits for the next packet.

Layout (ISO/IEC 14496-12):
  init  = ftyp + moov(mvhd, trak(tkhd, mdia(mdhd, hdlr, minf(vmhd, dinf(dref), stbl(stsd, stts, stsc, stsz, stco)))), mvex(trex))
  frame = moof(mfhd, traf(tfhd[default-base-is-moof], tfdt[v1], trun[data-offset|duration|size|flags]))
          + mdat(sample)
"""
from __future__ import annotations

import struct

from .bitstream import StreamConfig

TIMESCALE = 90_000

SAMPLE_FLAGS_SYNC = 0x02000000      # sample_depends_on=2 (independent)
SAMPLE_FLAGS_NON_SYNC = 0x01010000  # sample_depends_on=1, sample_is_non_sync_sample=1

_UNITY_MATRIX = struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)


def box(kind: bytes, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", 8 + len(body)) + kind + body


def full_box(kind: bytes, version: int, flags: int, *payload: bytes) -> bytes:
    return box(kind, struct.pack(">I", (version << 24) | flags), *payload)


def _visual_sample_entry(cfg: StreamConfig) -> bytes:
    compressor = b"stream-latency-lab"[:31]
    compressor = bytes([len(compressor)]) + compressor + b"\0" * (31 - len(compressor))
    body = (
        b"\0" * 6 + struct.pack(">H", 1)            # reserved, data_reference_index
        + b"\0" * 16                                # pre_defined, reserved, pre_defined[3]
        + struct.pack(">HH", cfg.width, cfg.height)
        + struct.pack(">II", 0x00480000, 0x00480000)  # 72 dpi
        + struct.pack(">I", 0)                      # reserved
        + struct.pack(">H", 1)                      # frame_count
        + compressor
        + struct.pack(">Hh", 0x0018, -1)            # depth, pre_defined
        + box(cfg.config_box.encode(), cfg.record)
    )
    return box(cfg.sample_entry.encode(), body)


def init_segment(cfg: StreamConfig) -> bytes:
    brands = [b"isom", b"iso6", b"iso2", b"mp41"] + (
        [b"avc1"] if cfg.codec == "h264" else [b"av01"] if cfg.codec == "av1" else [])
    ftyp = box(b"ftyp", b"isom", struct.pack(">I", 0x200), *brands)
    mvhd = full_box(b"mvhd", 0, 0,
                    struct.pack(">IIII", 0, 0, 1000, 0),          # times, timescale, duration
                    struct.pack(">IH", 0x00010000, 0x0100),       # rate, volume
                    b"\0" * 10, _UNITY_MATRIX, b"\0" * 24,
                    struct.pack(">I", 2))                         # next_track_ID
    tkhd = full_box(b"tkhd", 0, 0x000007,
                    struct.pack(">IIIII", 0, 0, 1, 0, 0),         # times, track_ID, reserved, duration
                    b"\0" * 8, struct.pack(">hhH", 0, 0, 0), b"\0" * 2,
                    _UNITY_MATRIX,
                    struct.pack(">II", cfg.width << 16, cfg.height << 16))
    mdhd = full_box(b"mdhd", 0, 0, struct.pack(">IIIIHH", 0, 0, TIMESCALE, 0, 0x55C4, 0))
    hdlr = full_box(b"hdlr", 0, 0, struct.pack(">I", 0), b"vide", b"\0" * 12, b"VideoHandler\0")
    vmhd = full_box(b"vmhd", 0, 1, b"\0" * 8)
    dinf = box(b"dinf", full_box(b"dref", 0, 0, struct.pack(">I", 1), full_box(b"url ", 0, 1)))
    stbl = box(b"stbl",
               full_box(b"stsd", 0, 0, struct.pack(">I", 1), _visual_sample_entry(cfg)),
               full_box(b"stts", 0, 0, struct.pack(">I", 0)),
               full_box(b"stsc", 0, 0, struct.pack(">I", 0)),
               full_box(b"stsz", 0, 0, struct.pack(">II", 0, 0)),
               full_box(b"stco", 0, 0, struct.pack(">I", 0)))
    trak = box(b"trak", tkhd, box(b"mdia", mdhd, hdlr, box(b"minf", vmhd, dinf, stbl)))
    mvex = box(b"mvex", full_box(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, 0)))
    return ftyp + box(b"moov", mvhd, trak, mvex)


def media_fragment(sequence_number: int, decode_time: int, duration: int,
                   sample: bytes, keyframe: bool) -> tuple[bytes, int]:
    """Return (moof+mdat bytes, moof_size). decode_time/duration in TIMESCALE units."""
    flags = SAMPLE_FLAGS_SYNC if keyframe else SAMPLE_FLAGS_NON_SYNC

    def build(data_offset: int) -> bytes:
        mfhd = full_box(b"mfhd", 0, 0, struct.pack(">I", sequence_number))
        tfhd = full_box(b"tfhd", 0, 0x020000, struct.pack(">I", 1))          # default-base-is-moof
        tfdt = full_box(b"tfdt", 1, 0, struct.pack(">Q", decode_time))
        trun = full_box(b"trun", 0, 0x000701,                                 # offset|dur|size|flags
                        struct.pack(">IiIII", 1, data_offset, duration, len(sample), flags))
        return box(b"moof", mfhd, box(b"traf", tfhd, tfdt, trun))

    moof_size = len(build(0))
    moof = build(moof_size + 8)
    return moof + struct.pack(">I", 8 + len(sample)) + b"mdat" + sample, moof_size
