"""The picture formats the app takes, in one place (owner, 19 Nov: "we should be able to handle these sorts of images,
obviously", after an AVIF photo sent to the generator failed). Pillow 11.3 reads AVIF; pillow-heif, registered here,
reads iPhone HEIC/HEIF. A picture in a format a browser or a TV page can't be relied on to show is kept in a library as
a JPEG (bucketer._append_to_bucket); a screen's own file is always a JPEG (publisher._place_file)."""
from pathlib import Path

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:              # requirements.txt has it; without it HEIC can't be read, and says so on upload
    pass

STILLS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
VIDEOS = {".mp4", ".webm", ".mov"}
# kept in a library as JPEG: a browser can't show HEIC, and the rest are no help to the pages or the TVs as they are
TO_JPEG_IN_LIBRARY = {".heic", ".heif", ".avif", ".bmp", ".tif", ".tiff"}
CONTENT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
                 "image/avif": ".avif", "image/heic": ".heic", "image/heif": ".heif", "image/bmp": ".bmp",
                 "image/tiff": ".tiff"}


def is_still(name) -> bool:
    return Path(str(name)).suffix.lower() in STILLS


def save_as_jpeg(src: Path, dst: Path) -> Path:
    """The picture upright (EXIF orientation applied), as a JPEG (q95, 4:4:4) at dst."""
    from PIL import Image, ImageOps
    with Image.open(src) as img:
        ImageOps.exif_transpose(img).convert("RGB").save(dst, format="JPEG", quality=95, subsampling=0)
    return dst
