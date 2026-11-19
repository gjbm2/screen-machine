"""A still's clips: one logical object (owner, 19 Nov: "video files are shown adjacent to the image files, but
essentially they are adjunct ... if a user republishes a still which happens to have a sidecar video enrichment, it
would naturally be included as part of a single logical object").

A clip made from a still names it: `still` (the still's file name in the same bucket) in the clip's metadata, which
is its sidecar and, for an eased clip, its own MP4 comment tag too (routes/clip_ease.py). From that:
  * a bucket's listing shows the still once, with its clips under `clips` (newest first), not as items of their own;
    a clip whose still has gone is listed as itself;
  * publishing the still publishes its newest clip after it (the TV shows the picture, then it comes to life);
  * deleting, purging or copying the still takes its clips with it.
"""
import json
from pathlib import Path

from routes.image_formats import VIDEOS


def still_of(meta: dict | None) -> str | None:
    meta = meta or {}
    return meta.get("still") or (meta.get("clip") or {}).get("still")


def _meta(path: Path) -> dict:
    try:
        return json.loads(path.with_suffix(path.suffix + ".json").read_text("utf-8"))
    except Exception:
        return {}


def clips_of(folder: Path, still: str) -> list[Path]:
    """The clips in `folder` made from `still`, newest first."""
    found = [p for p in folder.iterdir()
             if p.suffix.lower() in VIDEOS and p.is_file() and still_of(_meta(p)) == still]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def group(files: list[dict]) -> list[dict]:
    """A bucket listing (dicts with "filename" and "metadata") with each clip moved under its still's `clips`."""
    by_name = {f["filename"]: f for f in files}
    out = []
    for f in files:
        still = still_of(f.get("metadata"))
        if still and still != f["filename"] and still in by_name:
            by_name[still].setdefault("clips", []).append(f)
        else:
            out.append(f)
    for f in out:
        if f.get("clips"):
            f["clips"].sort(key=lambda c: c.get("modified") or 0, reverse=True)
    return out


def published_still(bucket: str) -> str | None:
    """The still the screen is showing, as its file name in the screen's bucket (a published clip stands for its still)."""
    from routes.bucketer import bucket_path, load_meta
    name = (load_meta(bucket).get("published_meta") or {}).get("filename")
    if not name:
        return None
    path = bucket_path(bucket) / name
    if path.suffix.lower() in VIDEOS:
        name = still_of(_meta(path))
        path = bucket_path(bucket) / name if name else None
    return name if path and path.exists() else None


def snapshot_screen_still(screen: str) -> str | None:
    """A copy of the screen's still as it is now (output/<screen>.jpg, the picture fitted to the screen): the clip
    is laid onto it when it lands (routes/clip_ease.py). Kept with the job's other files (output/_inline, pruned)."""
    import shutil
    import uuid
    from routes.result_source import inline_dir
    src = Path("output") / f"{screen}.jpg"
    if not src.exists():
        return None
    folder = inline_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"still-{screen}-{uuid.uuid4().hex[:8]}.jpg"
    shutil.copy2(src, dst)
    return str(dst)
