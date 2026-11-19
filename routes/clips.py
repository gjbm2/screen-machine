"""A still's clips: one logical object (owner, 19 Nov: "video files are shown adjacent to the image files, but
essentially they are adjunct ... if a user republishes a still which happens to have a sidecar video enrichment, it
would naturally be included as part of a single logical object").

A clip made from a still names it: `still` (the still's file name in the same bucket) in the clip's metadata, which
is its sidecar and, for an eased clip, its own MP4 comment tag too (routes/clip_ease.py). From that:
  * a bucket's listing shows the still once, with its clips under `clips` (the one that plays first, then newest
    first), not as items of their own; a clip whose still has gone is listed as itself;
  * publishing the still publishes the clip that plays (its newest, or the one chosen: `chosen_at`) after it;
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


def _rank(mtime, meta: dict | None) -> float:
    """A clip's place among its still's clips: when it was chosen to play, or else when it was made."""
    return max(float(mtime or 0), float((meta or {}).get("chosen_at") or 0))


def clips_of(folder: Path, still: str) -> list[Path]:
    """The clips in `folder` made from `still`, the one that plays first."""
    found = [(p, _meta(p)) for p in folder.iterdir() if p.suffix.lower() in VIDEOS and p.is_file()]
    found = [(p, m) for p, m in found if still_of(m) == still]
    return [p for p, m in sorted(found, key=lambda pm: _rank(pm[0].stat().st_mtime, pm[1]), reverse=True)]


def choose(folder: Path, clip: str) -> None:
    """Make `clip` the one its still plays (it goes first among its still's clips)."""
    import time
    sc = (folder / clip).with_suffix((folder / clip).suffix + ".json")
    meta = _meta(folder / clip)
    meta["chosen_at"] = time.time()
    sc.write_text(json.dumps(meta, indent=2, ensure_ascii=False), "utf-8")


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
            f["clips"].sort(key=lambda c: _rank(c.get("modified"), c.get("metadata")), reverse=True)
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
