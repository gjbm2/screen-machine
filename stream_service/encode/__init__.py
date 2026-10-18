"""NVENC encode + fMP4 mux, copied from experiments/stream-latency/lab (bd) on 2026-10-13. Keep in sync by hand.

Local changes vs the lab: encoder.py gained `extra_args` (VUI colour tags), `delay` (NVENC output delay,
the lab hard-coded 0) and a partial-write loop in submit(); fmp4.py gained an optional colr/nclx box in the init segment and the compressor name.

stream_service's own (4K60 plan change 2): nvenc.py, the GPU-resident encoder (PyNvVideoCodec), and bitstream.py's
"HEVC Annex B -> MP4" section (hvcC, samples and the SPS colour VUI, byte-identical to ffmpeg's FLV path)."""
