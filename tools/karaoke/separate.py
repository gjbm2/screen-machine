"""Split one song into its backing track and its lead vocal (docs/KARAOKE.md). Run by Flask's karaoke worker in the
karaoke venv (deployment host.karaoke_python: torch + audio-separator), never inside Flask.

    python tools/karaoke/separate.py --in source.m4a --out-dir <song dir> [--model ...] [--overlap 2] [--no-autocast]

Writes <out-dir>/instrumental.wav and <out-dir>/vocals.wav and prints one JSON line {ok, seconds, model}.

The default model removes only the LEAD vocal and keeps the backing vocals (fuller, and the singers keep their place).
Overlap 2 with autocast (fp16) measured 31 s for a 3:51 song on the laptop's RTX PRO 1000 with the 4K live render
running, and the render dropped no frames (25 Nov). Building the venv: docs/KARAOKE.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

DEFAULT_MODEL = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--overlap", type=int, default=2)
    ap.add_argument("--no-autocast", action="store_true")
    ap.add_argument("--model-dir", default=os.path.expanduser("~/.cache/screen-machine/karaoke-models"))
    a = ap.parse_args()

    from audio_separator.separator import Separator

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=a.out_dir) as tmp:
        sep = Separator(output_dir=tmp, output_format="wav", log_level=40, model_file_dir=a.model_dir,
                        use_autocast=not a.no_autocast,
                        mdxc_params={"segment_size": 256, "override_model_segment_size": False, "batch_size": 1,
                                     "overlap": a.overlap, "pitch_shift": 0})
        sep.load_model(model_filename=a.model)
        sep.separate(a.src, custom_output_names={"Instrumental": "instrumental", "Vocals": "vocals"})
        for name in ("instrumental", "vocals"):
            src = os.path.join(tmp, f"{name}.wav")
            if not os.path.exists(src):
                print(json.dumps({"ok": False, "error": f"no {name} stem (got {os.listdir(tmp)})"}))
                return 1
            shutil.move(src, os.path.join(a.out_dir, f"{name}.wav"))
    print(json.dumps({"ok": True, "seconds": round(time.perf_counter() - t0, 1), "model": a.model}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
