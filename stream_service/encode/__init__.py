"""NVENC encode + fMP4 mux, copied from experiments/stream-latency/lab (bd) on 2026-10-13. Keep in sync by hand.

Local changes vs the lab: encoder.py gained `extra_args` (VUI colour tags), `delay` (NVENC output delay,
the lab hard-coded 0) and a partial-write loop in submit(); fmp4.py gained an optional colr/nclx box in the init segment and the compressor name."""
