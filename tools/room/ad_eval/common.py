"""Shared bits for tools/room/ad_eval: paths, the song set, decoding a song as the room hears it, the code's identity."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys
from typing import Any, Optional

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
CACHE = pathlib.Path(os.environ.get("SM_AD_EVAL_CACHE", pathlib.Path.home() / ".cache" / "sm-ad-eval"))
KARAOKE = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "karaoke"
SONGS_YAML = HERE / "songs.yaml"
WEIGHTS_YAML = HERE / "weights.yaml"
SHOW_PY = pathlib.Path("~/.venvs/sm-show/bin/python").expanduser()
ML_PY = pathlib.Path("~/.venvs/sm-show-ml/bin/python").expanduser()
FS = 48000

#: the code a run's result depends on (hashed into the run's identity, beside git HEAD: other sessions edit in place)
STACK_GLOBS = ("show_service/*.py", "show_service/ml/*.py", "utils/song_shape.py", "config/room/show.yaml",
               "config/room/vibes.yaml", "config/live/light_*.yaml", "config/live/moves.yaml", "deployments/*/show.yaml",
               "tools/room/ad_eval/*.py", "tools/room/ad_eval/*.yaml")


def song_set(path: pathlib.Path = SONGS_YAML) -> list[dict]:
    import yaml
    return list((yaml.safe_load(path.read_text()) or {}).get("songs") or [])


def library() -> dict:
    return json.loads((KARAOKE / "state.json").read_text()).get("songs") or {}


def song_dir(sid: str) -> pathlib.Path:
    return KARAOKE / "songs" / sid


def song_meta(sid: str, lib: Optional[dict] = None) -> dict:
    s = (lib if lib is not None else library()).get(sid) or {}
    return {"id": sid, "title": s.get("title"), "artist": s.get("artist"),
            "duration_s": s.get("audio_s") or s.get("duration"), "genre": s.get("genre"), "year": s.get("year")}


def _decode(path: pathlib.Path, fs: int) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(fs),
                        "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype="<f4").astype(np.float32)


def room_mix(sid: str, fs: int = FS, guide: float = 1.0) -> np.ndarray:
    """Mono float32 at ``fs``: the backing track plus the lead vocal at the guide gain (karaoke settings.guide; 1.0
    = the original mix), as the karaoke player plays it into the room. Cached as .npy."""
    out = CACHE / "audio" / f"{sid}_{fs}_{guide:g}.npy"
    if out.is_file():
        return np.load(out)
    d = song_dir(sid)
    x = _decode(d / "instrumental.m4a", fs)
    v = d / "vocals.m4a"
    if guide > 0 and v.is_file():
        y = _decode(v, fs)
        n = min(len(x), len(y))
        x = x[:n] + guide * y[:n]
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, x.astype(np.float32))
    return x.astype(np.float32)


def git_sha() -> str:
    if os.environ.get("SM_AD_EVAL_SHA"):                # a clean copy of HEAD (scripts/ad-nightly): no .git in it
        return os.environ["SM_AD_EVAL_SHA"]
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip() or "nogit"
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def stack_id() -> str:
    """A short hash of every file the result depends on, as it is on disk now (uncommitted edits included)."""
    h = hashlib.sha1()
    for g in STACK_GLOBS:
        for p in sorted(REPO.glob(g)):
            if p.is_file():
                h.update(str(p.relative_to(REPO)).encode())
                h.update(p.read_bytes())
    return h.hexdigest()[:10]


def dirty_files() -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--", "show_service", "config",
                              "deployments", "utils", "tools/room/ad_eval"], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [ln[3:] for ln in out.splitlines() if ln.strip()]


def write_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)


def add_repo_path() -> None:
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
