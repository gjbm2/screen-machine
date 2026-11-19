"""A finished job's picture, wherever the worker left it.

A worker hands a result back in one of two ways: a URL (it uploaded to its S3 bucket) or the image inside the reply
(it has no bucket set). After routes.generate.worker_output the rest of the app sees one thing, a *source*: an
http(s) URL, or a file under output/_inline/. Everything that uses a result reads it through here, so no caller has
to know which it got: the publisher, the generator page's copy into Recent, and the URL the browser is given.
"""
import os
import time
from io import BytesIO
from pathlib import Path

import requests

import routes.bucketer as bucketer
from utils.logger import warning

INLINE_DIR = "_inline"
INLINE_KEEP_S = 24 * 3600            # an inline result is only needed until it is published or copied to Recent
DOWNLOAD_TIMEOUT = (10, 120)         # connect, read: a stalled download must not hold a job (or its thread) for ever
from routes.image_formats import STILLS as _STILLS


def is_url(source) -> bool:
    return isinstance(source, str) and source.startswith(("http://", "https://"))


def inline_dir() -> Path:
    return Path(bucketer.BASE_OUTPUT) / INLINE_DIR


def save_inline(data: bytes, filename: str | None, job_id: str) -> str:
    """Keep an inline result as a file and return its path. A still is saved as a JPEG (q95, 4:4:4), as a result
    fetched from a URL is when it is published; anything else keeps its bytes and extension."""
    if not data:
        raise ValueError("the worker's inline result is empty")
    folder = inline_dir()
    folder.mkdir(parents=True, exist_ok=True)
    _prune(folder)
    ext = os.path.splitext(filename or "")[1].lower() or ".png"
    if ext in _STILLS:
        try:
            from PIL import Image
            path = folder / f"worker_{job_id}.jpg"
            with Image.open(BytesIO(data)) as img:
                img.convert("RGB").save(path, format="JPEG", quality=95, subsampling=0)
            return str(path)
        except Exception as e:
            warning(f"inline result: JPEG conversion failed ({e}); keeping the {ext}")
    path = folder / f"worker_{job_id}{ext}"
    path.write_bytes(data)
    return str(path)


def _prune(folder: Path) -> None:
    cutoff = time.time() - INLINE_KEEP_S
    for old in folder.glob("worker_*"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass


def read_bytes(source, timeout=DOWNLOAD_TIMEOUT) -> bytes:
    """The result's bytes: downloaded from a URL, or read from a file under output/."""
    if is_url(source):
        response = requests.get(source, timeout=timeout)
        response.raise_for_status()
        return response.content
    path = Path(str(source)).resolve()
    if not path.is_relative_to(Path(bucketer.BASE_OUTPUT).resolve()):
        raise ValueError(f"not a result the app made: {source}")
    return path.read_bytes()


def browser_url(source) -> str | None:
    """What a browser can load: the URL itself, or /output/... for a file under output/; None for anything else."""
    if not source:
        return None
    if is_url(source) or str(source).startswith(("/output/", "/api/")):
        return str(source)
    try:
        rel = Path(str(source)).resolve().relative_to(Path(bucketer.BASE_OUTPUT).resolve())
    except ValueError:
        return None
    return "/output/" + rel.as_posix()
