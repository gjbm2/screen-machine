"""
publisher.py – one canonical place for every way we can publish an asset.

• publish_local_file(...)   – bucket  ➜  /output/<screen>.<ext>
• publish_remote_asset(...) – URL     ➜  local file or S3

─── New in this version ────────────────────────────────────────────────
1. Side-car handling  (media.ext.json) – created from EXIF if missing.
2. Touch() after copy so watchdogs always see fresh mtime.
3. Every publish ALSO appends the file to that screen’s bucket (not a favourite).
4. Generate 256×256 JPEG thumbnails for all media (first frame for videos), stored under output/thumbnails/<screen>/
"""

from __future__ import annotations

import json, shutil, subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
import uuid

import requests
from utils.logger import info, error, warning, debug
from routes.utils import (
    _load_json_once, 
    detect_file_type,
    save_jpeg_with_metadata, 
    save_video_with_metadata
)
from PIL import Image
import cv2


OUTPUT_ROOT = Path("output")         # centralised
ISO_NOW     = lambda: datetime.utcnow().isoformat() + "Z"

# ─────────────────────────────────────────────────────────────────────────────
# Thumbnail helper
# ─────────────────────────────────────────────────────────────────────────────
def generate_thumbnail(source: Path, thumb: Path = None) -> None:
    """
    Create a 256×256 JPEG thumbnail for an image or the first frame of a video.
    """
    if thumb:
        thumb.parent.mkdir(parents=True, exist_ok=True)
        
    ext = source.suffix.lower()
    try:
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            img = Image.open(source)
        else:
            # video: capture first frame
            cap = cv2.VideoCapture(str(source))
            success, frame = cap.read()
            cap.release()
            if not success:
                return
            img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        img.thumbnail((256, 256))
        info(f"Generated thumbnail: {thumb}")
        if thumb:
            img.save(thumb, "JPEG")
        else:
            return img
        
    except Exception as e:
        warning(f"Failed to generate thumbnail for {source}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Side-car helpers
# ─────────────────────────────────────────────────────────────────────────────
def sidecar_path(p: Path) -> Path:
    return p.with_suffix(p.suffix + ".json")

def _extract_exif_json(img_path: Path) -> dict[str, Any] | None:
    try:
        from PIL import Image as _Img, ExifTags
        img = _Img.open(img_path)
        exif = {ExifTags.TAGS.get(k, k): v for k, v in img.getexif().items()}
        if "UserComment" in exif:
            return json.loads(exif["UserComment"])
    except Exception:
        pass
    return None

def _extract_mp4_comment_json(mp4_path: Path) -> dict[str, Any] | None:
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format_tags=comment",
            "-of", "json", str(mp4_path)
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, check=False).stdout
        if not out:
            return None
        data = json.loads(out)
        return json.loads(data["format"]["tags"]["comment"])
    except Exception:
        return None

def ensure_sidecar_for(dest: Path) -> None:
    """
    Make sure <dest>.json exists, either by copying from source or
    extracting EXIF/comment.
    """
    sc = sidecar_path(dest)
    if sc.exists():
        return

    meta: dict[str, Any] | None = None
    ext = dest.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp"}:
        meta = _extract_exif_json(dest)
    elif ext == ".mp4":
        meta = _extract_mp4_comment_json(dest)

    if meta:
        sc.write_text(json.dumps(meta, indent=2), encoding="utf-8")

# ─── Name management ─────────────────────────────
def _unique_name(original: str) -> str:
    """timestamp-uuid + same extension, e.g. 20250423-230102-abcd1234.jpg"""
    stem  = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    uid   = uuid.uuid4().hex[:8]
    return f"{stem}-{uid}{Path(original).suffix.lower()}"

# ─── Destination ─────────────────────────────────────────────────────────────
def get_destination(dest_id: str) -> dict[str, Any]:
    """Lookup a publish-destination by id (raises KeyError if unknown)."""
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    for d in dests:
        if d["id"] == dest_id:
            return d
    raise KeyError(f"Unknown publish_destination_id '{dest_id}'")

# ─── Public wrapper ─────────────────────────────────────────────────────
def publish_to_destination(
    url: str,
    publish_destination_id: str,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """
    High-level entry point: caller supplies the *destination id* only.
    We lookup the rest from publish-destinations.json and delegate.
    """
    dest = get_destination(publish_destination_id)
    dtype = dest["type"]              # "output_file" | "s3"
    dfile = dest["file"]              # basename or S3 key

    # pass through to the existing remote-asset engine
    result = publish_remote_asset(
        url=url,
        destination_type=dtype,
        destination_file=dfile,
        metadata=metadata or {},
    )
    
    if result.get("success"):
        _send_overlay_prompt(publish_destination_id, metadata)
        
    # Attach the destination record so callers can get .name, .icon, etc.
    result["destination"] = dest
    return result

# ─── overlay helper ────────────────────────────────────────────────────────
def _send_overlay_prompt(screen_id: str, metadata: dict[str, Any]) -> None:
    """
    Fire an overlay to the given screen with whatever fields we can find
    in the metadata dict.  Missing keys are simply rendered blank.
    """
    from routes.display import send_overlay           # late import = no circular deps
    dest = get_destination(screen_id)                 # already cached by _load_json_once

    substitutions = {
        "PROMPT_TEXT"   : metadata.get("prompt", ""),
        "WORKFLOW_TEXT" : metadata.get("workflow", ""),
        "SEED"          : metadata.get("seed", ""),
        "WIDTH"         : metadata.get("width", ""),
        "HEIGHT"        : metadata.get("height", ""),
        "DURATION"      : 30,
        "WHEN_GENERATED": metadata.get("when_generated", ""),
        "TIME_TO_GENERATE": metadata.get("time_to_generate", ""),
    }

    send_overlay(
        html="overlay_prompt.html.j2",
        screens=[screen_id],
        substitutions=substitutions,
        duration=30_000,        # 30 s
        clear=True
    )
    
# ─── publish records ────────────────────────────────────────────────────────
def _record_publish(bucket: str, filename: str, when: str):
    """Persist 'published_meta' to the bucket JSON."""
    from routes.bucket_api import load_meta, save_meta
    meta = load_meta(bucket)
    meta["published_meta"] = {"filename": filename, "published_at": when}
    save_meta(bucket, meta)    

# ─────────────────────────────────────────────────────────────────────────────
# Internal helper – bucket append
# ─────────────────────────────────────────────────────────────────────────────
def _append_to_bucket(screen: str, published_path: Path):
    """
    Copy *published_path* plus side-car into <bucket>/ preserving history:
    • If that exact filename already exists in the bucket, create a
      timestamp-uuid filename instead and store that.
    • Always append the name to bucket.json -> sequence[].
    """
    # delayed import to avoid circular dependency
    from routes.bucket_api import bucket_path, load_meta, save_meta, sidecar_path

    bucket_dir = bucket_path(screen)
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # choose filename (avoid collisions)
    target_path = bucket_dir / published_path.name
    if target_path.exists():
        target_path = bucket_dir / _unique_name(published_path.name)

    # copy media + side-car
    shutil.copy2(published_path, target_path)
    sc_src, sc_dst = sidecar_path(published_path), sidecar_path(target_path)
    if sc_src.exists():
        shutil.copy2(sc_src, sc_dst)

    # update bucket metadata
    meta = load_meta(screen)
    seq  = meta.setdefault("sequence", [])
    seq.append(target_path.name)
    save_meta(screen, meta)
    
    # now generate the thumbnail for *that* bucket copy
    from routes.publisher import generate_thumbnail, warning
    thumb_dir = bucket_dir / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{target_path.stem}.jpg"
    try:
        generate_thumbnail(target_path, thumb_path)
    except Exception as e:
        warning(f"Bucket thumbnail failed for {target_path.name}: {e}")
    return target_path    

# ─────────────────────────────────────────────────────────────────────────────
# 1. LOCAL FILE  ➜  /output/<screen>.<ext>
# ─────────────────────────────────────────────────────────────────────────────
def publish_local_file(
    source_path: Path,
    screen: str,
    meta: dict | None = None
) -> dict[str, Any]:
    """
    Copy (or overwrite) the screen's currently-published asset *and*
    guarantee a side-car json + bucket history.
    """
    if meta is None:
        try:
            meta = json.loads(sidecar_path(source_path).read_text("utf-8"))
        except Exception:
            meta = {}

    ext = source_path.suffix.lower()
    dest = OUTPUT_ROOT / f"{screen}{ext}"
    shutil.copy2(source_path, dest)
    dest.touch()                                    # bump mtime

    # keep side-car in sync
    sc_src, sc_dst = sidecar_path(source_path), sidecar_path(dest)
    if sc_src.exists():
        shutil.copy2(sc_src, sc_dst)
    else:
        ensure_sidecar_for(dest)

    # append media + side-car into bucket and generate thumbnail for the bucket copy
    new_pub = _append_to_bucket(screen, dest)
    

    # record that we've now published this file with the correct bucket filename
    published_at = ISO_NOW()
    _record_publish(screen, new_pub.name, published_at)

    # send overlay prompt to screen
    _send_overlay_prompt(screen, meta)

    return {
        "success": True,
        "path": str(dest),
        "meta": {
            **meta,
            "published_at": published_at,
            "filename": new_pub.name,
            "source": "bucket",
        },
    }

# ─────────────────────────────────────────────────────────────────────────────
# 2. REMOTE URL  ➜  local file or S3
# ─────────────────────────────────────────────────────────────────────────────
def publish_remote_asset(
    url: str,
    destination_type: str,
    destination_file: str,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """
    Publish a remote URL to either a local file (with bucket history & thumbnail)
    or simulate an S3 upload.
    """
    metadata = metadata or {}
    ftype = detect_file_type(url)
    if ftype == "unknown":
        return {"success": False, "error": "Unsupported file type"}

    # ensure correct extension
    ext = ".jpg" if ftype == "image" else ".mp4"
    if not destination_file.lower().endswith(ext):
        destination_file += ext

    # ─────────────── publish to local /output/<file> ────────────────
    if destination_type == "output_file":
        dest = OUTPUT_ROOT / destination_file

        # 1) download & save the media with embedded metadata
        if ftype == "image":
            save_jpeg_with_metadata(url=url, img_metadata=metadata, save_path=dest)
        else:
            save_video_with_metadata(url=url, img_metadata=metadata, save_path=dest)

        # 2) create / overwrite the side-car with the same metadata
        sidecar_path(dest).write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        # 3) fallback: if side-car missing and EXIF has JSON, extract it
        ensure_sidecar_for(dest)

        # 4) append the file (and side-car) to the appropriate bucket
        screen_id = Path(destination_file).stem  # strips directories + .ext
        new_pub = _append_to_bucket(screen_id, dest)

        # record published_meta using the actual bucket filename
        published_at = ISO_NOW()
        _record_publish(screen_id, new_pub.name, published_at)

        return {
            "success": True,
            "path": str(dest),
            "meta": {
                **metadata,
                "published_at": published_at,
                "source": url,
            },
        }

    # ──────────────── S3 (mock) ────────────────
    if destination_type == "s3":
        info(f"[PUBLISH] Mock S3 upload {url} → {destination_file}")
        return {
            "success": True,
            "path": f"s3://mock-bucket/{destination_file}",
            "meta": {**metadata, "published_at": ISO_NOW(), "source": url},
        }

    return {"success": False, "error": f"Unknown destination_type {destination_type}"}
