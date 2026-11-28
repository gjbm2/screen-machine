"""Sound files a Sonos fetches (docs/SOUND.md): written into piano_render's render cache, the folder show_service serves
at ``/api/v1/piano/audio/<key>.flac``, reached through room_service's LAN door (``http://<SM_LAN_HOST>:<room port>
/show/...``). Karaoke's mixes and the calibration's chirps use it, as the piano's renders do.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import wave
from pathlib import Path
from typing import Optional

import numpy as np

import smconfig

PIANO_RENDER_CACHE_ENV = "PIANO_RENDER_CACHE"
PIANO_RENDER_CACHE = Path("~/.cache/screen-machine/piano/renders")
AUDIO_PATH = "/show/api/v1/piano/audio/{key}.flac"


def cache_dir() -> Path:
    raw = os.environ.get(PIANO_RENDER_CACHE_ENV)
    return Path(raw).expanduser() if raw else PIANO_RENDER_CACHE.expanduser()


def lan_host() -> Optional[str]:
    """SM_LAN_HOST from the repo's .env (as show_sender reads it), else the environment: what the Sonos fetches from."""
    try:
        for line in (smconfig.REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("SM_LAN_HOST="):
                v = line.split("=", 1)[1].strip().strip("\"'")
                if v:
                    return v
    except OSError:
        pass
    return os.environ.get("SM_LAN_HOST") or None


def audio_url(key: str) -> str:
    host = lan_host()
    if not host:
        raise RuntimeError("no SM_LAN_HOST in .env (the address the Sonos fetches the sound from)")
    return f"http://{host}:{smconfig.deployment().port('room')}{AUDIO_PATH.format(key=key)}"


def write_flac(sig: np.ndarray, sr: int, folder: Path, tag: str) -> Path:
    """A mono float signal as ``<folder>/<key>.flac`` (stereo, 16-bit; key: 32 hex digits of the signal); made once."""
    folder.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(sig, -1.0, 1.0) * 32767).astype("<i2")
    key = hashlib.md5(tag.encode() + pcm.tobytes()).hexdigest()
    out = folder / f"{key}.flac"
    if out.is_file() and out.stat().st_size > 0:
        out.touch()
        return out
    tmp_wav = folder / f"{key}.part.wav"
    with wave.open(str(tmp_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(pcm[:, None], 2, axis=1).tobytes())
    tmp = folder / f"{key}.part.flac"
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(tmp_wav), "-c:a", "flac", "-compression_level", "0",
                        str(tmp)], check=True, timeout=60, capture_output=True)
        tmp.replace(out)
    finally:
        tmp_wav.unlink(missing_ok=True)
    return out
