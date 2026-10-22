#!/usr/bin/env python3
"""Offline validation of the FLV -> fMP4 path (no network, no browser).

For each codec: encode a short NVENC clip to FLV, remux it through
lab.bitstream + lab.fmp4, then check that
  1. ffprobe parses the fragmented MP4 and counts every frame,
  2. decoded frames are bit-identical to decoding the original FLV
     (so the SPS rewrite / TD stripping did not alter the picture data),
  3. (h264) the rewritten SPS carries bitstream_restriction with
     max_num_reorder_frames = 0.

Usage: python validate_mux.py [--gpu 0] [--codecs h264,hevc,av1] [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from lab.bitstream import SampleFixer, iter_flv_video  # noqa: E402
from lab.fmp4 import TIMESCALE, init_segment, media_fragment  # noqa: E402

ENCODERS = {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": "av1_nvenc"}


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, **kw)


def framemd5(path: pathlib.Path, fmt: str | None = None) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if fmt:
        cmd += ["-f", fmt]
    cmd += ["-i", str(path), "-map", "0:v", "-f", "framemd5", "-"]
    out = run(cmd).stdout.decode()
    return [line.split(",")[-1].strip() for line in out.splitlines() if line and not line.startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpu", default="0")
    ap.add_argument("--codecs", default="h264,hevc,av1")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--out", default="/tmp/stream-latency-validate")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    w, h = map(int, args.size.split("x"))
    ok_all = True
    for codec in args.codecs.split(","):
        flv = out / f"{codec}.flv"
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
               "-i", f"testsrc2=size={args.size}:rate={args.fps}", "-frames:v", str(args.frames),
               "-c:v", ENCODERS[codec], "-gpu", args.gpu, "-preset", "p1", "-tune", "ull",
               "-zerolatency", "1", "-delay", "0", "-rc", "cbr", "-b:v", "8M",
               "-g", str(args.fps), "-forced-idr", "1"]
        if codec != "av1":
            cmd += ["-bf", "0"]
        cmd += ["-fps_mode", "passthrough", "-f", "flv", str(flv)]
        run(cmd)

        fixer = SampleFixer(codec, w, h)
        mp4 = io.BytesIO()
        n = 0
        cts_nonzero = 0
        dur = TIMESCALE // args.fps
        with open(flv, "rb") as f:
            for pkt in iter_flv_video(f):
                if pkt.is_config:
                    cfg = fixer.on_config(pkt.payload)
                    mp4.write(init_segment(cfg))
                    continue
                if pkt.is_end:
                    continue
                if pkt.cts_ms:
                    cts_nonzero += 1
                frame_id = round(pkt.timestamp_ms * args.fps / 1000)
                frag, _ = media_fragment(n + 1, frame_id * dur, dur, fixer.fix_sample(pkt.payload), pkt.keyframe)
                mp4.write(frag)
                n += 1
        mp4_path = out / f"{codec}.mp4"
        mp4_path.write_bytes(mp4.getvalue())

        probe = run(["ffprobe", "-hide_banner", "-loglevel", "error", "-count_frames", "-select_streams", "v:0",
                     "-show_entries", "stream=codec_name,profile,width,height,nb_read_frames,has_b_frames",
                     "-of", "default=nw=1", str(mp4_path)]).stdout.decode().strip().replace("\n", " ")
        a, b = framemd5(flv), framemd5(mp4_path)
        identical = a == b and len(a) == args.frames
        line = (f"[{codec}] {cfg.codec_string} {cfg.sample_entry}/{cfg.config_box} frames={n} "
                f"inband_param_sets={fixer.inband_param_sets} cts_nonzero={cts_nonzero} | ffprobe: {probe} | "
                f"decoded-identical={identical} ({len(a)} vs {len(b)} frames, "
                f"sha={hashlib.sha1(''.join(b).encode()).hexdigest()[:10]})")
        for note in cfg.notes:
            line += f"\n      {note}"
        if codec == "h264":
            trace = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(mp4_path), "-frames:v", "1", "-c", "copy",
                                    "-bsf:v", "trace_headers", "-f", "null", "-"],
                                   capture_output=True).stderr.decode()
            fields = dict(re.findall(r"\s(bitstream_restriction_flag|max_num_reorder_frames|max_dec_frame_buffering)"
                                     r"\s+\S+ = (\d+)", trace))
            line += f"\n      trace_headers on muxed SPS: {fields}"
            identical = identical and fields.get("bitstream_restriction_flag") == "1" \
                and fields.get("max_num_reorder_frames") == "0"
        ok_all &= identical
        print(("PASS " if identical else "FAIL ") + line)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
