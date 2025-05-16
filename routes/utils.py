import os
import difflib
import json
from PIL import Image
import io
import base64
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
import requests
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from io import BytesIO
import re
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
import cv2
import qrcode
from urllib.parse import urlencode, urlparse
from pathlib import Path
from flask import url_for
from typing import Any

# image processing
from PIL import Image
import piexif
import piexif.helper

# Internal JSON cache
_json_cache = {}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SEARCH_PATHS = [
    os.path.join(os.getcwd(), "src", "data"),
    os.path.join(os.getcwd(), "routes", "overlays"),
    os.path.join(os.getcwd(), "routes", "data"),
    os.path.join(SCRIPT_DIR, "data"),
    os.path.join(SCRIPT_DIR, "workflows"),
    os.path.join(SCRIPT_DIR, "sysprompts"),
    os.path.join(SCRIPT_DIR, "src", "workflows"),
    os.path.join(SCRIPT_DIR, "src", "data", "workflows"),
]

# === Type-safe casting utilities ===
def unwrap_optional_type(tp):
    """
    If tp is an Optional (i.e., Union[X, None]), return X. Otherwise, return tp.
    """
    from typing import get_origin, get_args, Union
    import types
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        args = [t for t in get_args(tp) if t is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp

def safe_cast(val, target_type, default=None):
    """
    Attempts to cast val to the given target_type, handling common types and Optionals.
    Returns default if casting fails.
    """
    import ast
    target_type = unwrap_optional_type(target_type)
    if val is None:
        return default
    try:
        if target_type is bool:
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes", "on", "y")
            return bool(val)
        if target_type is int:
            return int(val)
        elif target_type is float:
            return float(val)
        elif target_type is str:
            return str(val)
        elif target_type in (list, dict):
            return ast.literal_eval(val)
        return target_type(val)
    except Exception as e:
        from utils.logger import error
        error(f"[safe_cast] Failed to cast {val!r} to {target_type}: {e}")
        return default

def findfile(file_param):
    """
    Check if a file exists with or without a path.
    Fully backwards-compatible with original behavior.
    """
    # 1. Absolute or relative path already valid
    if os.path.exists(file_param):
        return os.path.abspath(file_param)

    # 2. Check in known workflow directories (SEARCH_PATHS)
    for directory in SEARCH_PATHS:
        file_in_dir = os.path.join(directory, file_param)
        if os.path.exists(file_in_dir):
            return os.path.abspath(file_in_dir)

    # 3. Explicit fallback: cwd
    file_in_cwd = os.path.join(os.getcwd(), file_param)
    if os.path.exists(file_in_cwd):
        return os.path.abspath(file_in_cwd)

    # 4. Explicit fallback: script dir
    file_in_script_dir = os.path.join(SCRIPT_DIR, file_param)
    if os.path.exists(file_in_script_dir):
        return os.path.abspath(file_in_script_dir)

    # 5. Explicit fallback: user home directory
    home_dir = os.path.expanduser("~")
    file_in_home = os.path.join(home_dir, file_param)
    if os.path.exists(file_in_home):
        return os.path.abspath(file_in_home)

    return None

def detect_file_type(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith(".mp4"):
        return "video"
    elif path.endswith(".jpg") or path.endswith(".jpeg") or path.endswith(".png"):
        return "image"
    return "unknown"

def _load_json_once(cache_key: str, filename: str, force_reload: bool = False) -> Any:
    """
    Find and load a JSON file using findfile(), cache it by name,
    and automatically reload if the source file has changed on disk.
    """
    path = findfile(filename)
    if not path:
        raise FileNotFoundError(f"Could not locate file: {filename}")
    
    try:
        mtime = os.path.getmtime(path)
    except Exception as e:
        error(f"[_load_json_once] Failed to get mtime for {path}: {e}")
        mtime = None

    cache_entry = _json_cache.get(cache_key)

    if not cache_entry or cache_entry["timestamp"] != mtime:
        if mtime:
            debug(f"[_load_json_once] Reloading {cache_key} from disk (changed)")
        else:
            debug(f"[_load_json_once] Reloading {cache_key} from disk (no timestamp)")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _json_cache[cache_key] = {
            "data": data,
            "timestamp": mtime
        }

    return _json_cache[cache_key]["data"]

def fuzzy_match(name, candidates, key="name"):
    """Fuzzy match input name to item in candidate list by key."""
    if not name:
        return None
    names = [item[key] for item in candidates if key in item]
    match = difflib.get_close_matches(name.lower(), [n.lower() for n in names], n=1, cutoff=0.4)
    if match:
        for item in candidates:
            if key in item and item[key].lower() == match[0]:
                return item
    return None

def resolve_runtime_value(category, input_value, return_key="id", match_key=None):
    debug(f"category: {category}, input_value: {input_value}, return_key: {return_key}, match_key: {match_key}")
    """
    Resolve a single category (refiner, workflow, destination) to a specific field from the matched item.
    
    If match_key is not provided, tries all string fields and selects the best fuzzy match.
    """

    if not isinstance(input_value, str):
        return None

    mapping = {
        "refiner": "refiners.json",
        "workflow": "workflows.json",
        "destination": "publish-destinations.json"
    }

    if category not in mapping:
        raise ValueError(f"Unknown category: {category}")

    data = _load_json_once(category, mapping[category])

    best_match = None
    best_score = 0.0

    def score_match(value1, value2):
        return difflib.SequenceMatcher(None, value1.lower(), value2.lower()).ratio()

    if match_key:
        # Simple case: match on specified key
        return_value = None
        match = fuzzy_match(input_value, data, key=match_key)
        if match and return_key in match:
            return match[return_key]
        return None
    else:
        # Try all string fields in each item to find the highest score
        for item in data:
            for key, candidate_value in item.items():
                if isinstance(candidate_value, str):
                    score = score_match(input_value, candidate_value)
                    if score > best_score:
                        best_score = score
                        best_match = item

        if best_match and return_key in best_match:
            return best_match[return_key]

    return None

def reset_cache():
    """Clear the cached JSON data."""
    _json_cache.clear()


def encode_image_uploads(image_files, max_file_size_mb=5):
    """
    Compress and base64-encode uploaded images as JPEGs while preserving aspect ratio.

    Args:
        image_files (list): List of Werkzeug FileStorage objects.
        max_file_size_mb (int): Maximum size per image in megabytes.

    Returns:
        List of dicts with keys: "name" (filename), "image" (base64 string).
    """
    max_size_bytes = max_file_size_mb * 1024 * 1024
    result = []

    def compress_image(image, max_size_bytes):
        quality = 95
        step = 5
        buffer = io.BytesIO()

        # Ensure it's in RGB for JPEG compatibility
        if image.mode in ("RGBA", "P", "LA"):
            image = image.convert("RGB")

        while quality > 5:
            buffer.seek(0)
            buffer.truncate()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            if buffer.tell() <= max_size_bytes:
                break
            quality -= step

        buffer.seek(0)
        return buffer.read()

    for file in image_files:
        if file and file.filename:
            filename = secure_filename(file.filename)
            image = Image.open(file.stream)

            compressed_bytes = compress_image(image, max_size_bytes)
            image_base64 = base64.b64encode(compressed_bytes).decode('utf-8')

            result.append({
                "name": os.path.splitext(filename)[0] + ".jpg",
                "image": image_base64
            })

    return result


def encode_reference_urls(reference_urls, max_file_size_mb=5):
    """
    Downloads, compresses, and base64-encodes images from external URLs.
    Returns a list of dicts: {name, image}
    """
    result = []
    max_size_bytes = max_file_size_mb * 1024 * 1024

    # Use the original URLs to preserve query params for retrieval
    # but ensure we create proper filenames for OpenAI
    info(f"[encode_reference_urls] Called with {len(reference_urls)} raw URLs")

    def compress_image(image, max_size_bytes):
        info(f"[compress_image] Converting to JPEG. Original mode: {image.mode}, size: {image.size}")
        buffer = BytesIO()

        # Convert to RGB if needed (JPEG doesn't support alpha or palette)
        if image.mode in ("RGBA", "P", "LA"):
            image = image.convert("RGB")

        quality = 95
        step = 5

        while quality > 5:
            buffer.seek(0)
            buffer.truncate()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            if buffer.tell() <= max_size_bytes:
                break
            quality -= step

        buffer.seek(0)
        return buffer.read()

    for url in reference_urls:
        try:
            info(f"[encode_reference_urls] Fetching: {url}")
            
            # Handle local file paths (starting with /)
            if url.startswith('/'):
                info(f"[encode_reference_urls] Detected local file path")
                try:
                    # Remove leading slash if it's a path relative to site root
                    file_path = url[1:] if url.startswith('/') else url
                    
                    # Open the file directly from filesystem
                    with open(file_path, 'rb') as f:
                        image_data = f.read()
                    
                    image = Image.open(BytesIO(image_data)).convert("RGB")
                except FileNotFoundError:
                    # Try with the leading slash if not found
                    info(f"[encode_reference_urls] File not found, trying with original path")
                    with open(url, 'rb') as f:
                        image_data = f.read()
                    
                    image = Image.open(BytesIO(image_data)).convert("RGB")
            else:
                # For regular URLs, use requests
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                
                content_type = response.headers.get("Content-Type", "")
                info(f"[encode_reference_urls] Content-Type: {content_type}")
                
                image = Image.open(BytesIO(response.content)).convert("RGB")
            
            format = image.format or "PNG"
            compressed_bytes = compress_image(image, max_size_bytes)
            image_base64 = base64.b64encode(compressed_bytes).decode('utf-8')

            # Create a proper filename based on the URL
            # Special handling for jpg_from_mp4 endpoint
            if 'jpg_from_mp4' in url:
                # For the jpg_from_mp4 endpoint, create a filename with the correct extension
                filename = "frame_from_video.jpg"
            else:
                # For other URLs, use the basename but ensure it has a .jpg extension
                basename = os.path.basename(url.split("?")[0])
                if not basename:
                    basename = "image.jpg"
                elif not any(basename.lower().endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                    basename = f"{basename}.jpg"
                filename = basename.replace(".png", ".jpg")

            result.append({
                "name": filename,
                "image": image_base64
            })

            info(f"[encode_reference_urls] Encoded {url} as '{filename}' (length={len(image_base64)})")

        except Exception as e:
            error(f"[encode_reference_urls] Failed to process {url}: {str(e)}")

    return result


def dict_substitute(template_or_file: str, substitutions: dict = None) -> str:
    substitutions = substitutions or {}
    filepath = findfile(template_or_file)

    if filepath:
        template_name = os.path.basename(filepath)
        template_dir = os.path.dirname(filepath)

        env = Environment(loader=FileSystemLoader(template_dir))
        env.filters['tojson'] = lambda val: json.dumps(val)

        try:
            template = env.get_template(template_name)
            return template.render(substitutions)
        except TemplateNotFound:
            error(f"[dict_substitute] Template not found: {template_name}")
            return ""
        except Exception as e:
            error(f"[dict_substitute] Render error: {e}")
            return ""
    else:
        # NEW: Use a loader so {% include %} still works in string templates
        env = Environment(loader=FileSystemLoader(SEARCH_PATHS))
        env.filters['tojson'] = lambda val: json.dumps(val)
        try:
            template = env.from_string(template_or_file)
            return template.render(substitutions)
        except Exception as e:
            error(f"[dict_substitute] Inline render error: {e}")
            return template_or_file




# ------------------------------------------------------------------------------
# Dynamic schema substitutions from config
# ------------------------------------------------------------------------------
    
def build_schema_subs():
    """
    Dynamically construct a substitutions dictionary for Jinja2 templates
    from the contents of your core config JSON files. Includes enum values,
    enumDescriptions, and optional id-label-description structures.
    """
    subs = {}

    try:
        workflows = _load_json_once("workflow", "workflows.json")
        refiners = _load_json_once("refiner", "refiners.json")
        destinations = _load_json_once("destination", "publish-destinations.json")

        # --- WORKFLOWS ---

        subs["ALL_WORKFLOWS"] = [None] + [r["id"] for r in workflows]

        visible_workflows = [w for w in workflows if w.get("alexavisible")]

        subs["ALEXA_WORKFLOWS"] = [w["id"] for w in visible_workflows]
        subs["ALEXA_WORKFLOWS_DESCRIPTIONS"] = [w.get("description", "") for w in visible_workflows]
        subs["ALEXA_WORKFLOWS_LABELLED"] = [
            {
                "id": w["id"],
                "label": w.get("name", w["id"].replace("-", " ").title()),
                "description": w.get("description", ""),
                "style_guidance": w.get("style_guidance", ""),
                "style_descriptor": w.get("style_descriptor", "")
            }
            for w in visible_workflows
        ]

        default_workflows = [w["id"] for w in workflows if w.get("default")]
        subs["ALEXA_DEFAULT_WORKFLOW"] = default_workflows[0] if default_workflows else None

        # --- REFINERS ---
        visible_refiners = [r for r in refiners if r.get("alexavisible")]
        
        # Add ALL_REFINERS with null as first item
        subs["ALL_REFINERS"] = [None] + [r["id"] for r in refiners]
        
        subs["ALEXA_REFINERS"] = [r["id"] for r in visible_refiners]
        subs["ALEXA_REFINERS_DESCRIPTIONS"] = [r.get("description", "") for r in visible_refiners]
        subs["ALEXA_REFINERS_LABELLED"] = [
            {
                "id": r["id"],
                "label": r.get("name", r["id"].replace("-", " ").title()),
                "description": r.get("description", "")
            }
            for r in visible_refiners
        ]
        
        # --- REASONERS ---
        # Get a list of reasoner templates from the reasoners directory
        reasoners_dir = os.path.join(SCRIPT_DIR, "data", "reasoners")
        reasoners = []
        
        # Check if directory exists
        if os.path.exists(reasoners_dir):
            # Get all template files in the reasoners directory
            for file in os.listdir(reasoners_dir):
                if file.endswith(".txt.j2"):
                    reasoner_id = os.path.splitext(os.path.splitext(file)[0])[0]
                    reasoners.append(reasoner_id)
        
        # Add default if no reasoners found
        if not reasoners:
            reasoners = ["default"]
                
        subs["ALL_REASONERS"] = reasoners

        # --- DESTINATIONS (SCREENS) ---
        visible_screens = [d for d in destinations if d.get("alexavisible")]
        default_screens = [d for d in destinations if d.get("alexadefault")]

        # Add ALL_DESTINATIONS for variable importing/exporting
        subs["ALL_DESTINATIONS"] = [d["id"] for d in destinations]
        
        # Extract unique groups from destinations for DEST_GROUPS
        all_groups = set()
        for d in destinations:
            if "groups" in d and isinstance(d["groups"], list):
                for group in d["groups"]:
                    if isinstance(group, str) and group.strip():
                        all_groups.add(group.strip())
        
        subs["DEST_GROUPS"] = sorted(list(all_groups))
        
        subs["ALEXA_SCREENS"] = [d["id"] for d in visible_screens]
        subs["ALEXA_SCREENS_DESCRIPTIONS"] = [d.get("description", "") for d in visible_screens]
        subs["ALEXA_SCREENS_LABELLED"] = [
            {
                "id": d["id"],
                "label": d.get("name", d["id"].replace("-", " ").title()),
                "description": d.get("description", "")
            }
            for d in visible_screens
        ]

        subs["ALEXA_DEFAULT_SCREENS"] = [d["id"] for d in default_screens]

        # Default voice
        subs["ALEXA_VOICE"] = "Brian"

    except Exception as e:
        error(f"[build_schema_subs] Failed to build substitutions: {str(e)}")

    return subs
    
def resize_image_keep_aspect(image_path, max_dim=512):
    """
    Resize the image to fit within max_dim x max_dim while keeping aspect ratio.
    
    Returns:
        A resized PIL.Image object.
    """
    image = Image.open(image_path)
    image.thumbnail((max_dim, max_dim), Image.LANCZOS)
    return image

def encode_single_image(pil_image: Image.Image, name="image.jpg", max_file_size_mb=5) -> str:
    """
    Wraps a PIL Image as a FileStorage-like object and runs it through encode_image_uploads.
    Returns base64-encoded JPEG string.
    """
    buf = BytesIO()
    pil_image.save(buf, format="PNG")  # PNG avoids early compression artifacts
    buf.seek(0)
    fake_file = FileStorage(stream=buf, filename=name)
    encoded = encode_image_uploads([fake_file], max_file_size_mb=max_file_size_mb)
    return encoded[0]["image"] if encoded else None

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

def get_image_from_target(file_prefix: str, thumbnail: bool = False) -> dict | None:
    """
    Chooses the newer of filename.jpg or filename.mp4 in /output/,
    extracts and compresses the image, and returns a dict:
    { "name": "<filename>.jpg", "image": "<base64-encoded JPEG>" }

    If thumbnail=True, the image will be downscaled to 256×256 before encoding.
    """
    # Expanded search paths for more reliable content location
    possible_paths = [
        f"./output/{file_prefix}.jpg",
        f"./output/{file_prefix}.mp4",
        f"./output/{file_prefix}.png",
        # Additional direct-publish paths where files might be stored
        f"/output/{file_prefix}.jpg",
        f"/output/{file_prefix}.mp4",
        f"/output/{file_prefix}.png"
    ]
    
    # Track best candidate and its timestamp
    best_path = None
    best_mtime = 0
    
    # Find the newest file across all possible paths
    for path in possible_paths:
        if os.path.exists(path):
            current_mtime = os.path.getmtime(path)
            if current_mtime > best_mtime:
                best_mtime = current_mtime
                best_path = path
    
    if not best_path:
        debug(f"[get_image_from_target] No files found for prefix '{file_prefix}' in any expected location")
        return None  # No files found
    
    image_path = best_path
    raw_name = os.path.basename(best_path)
    
    try:
        # Process based on file type
        if image_path.lower().endswith(('.jpg', '.jpeg', '.png')):
            image = Image.open(image_path)
        elif image_path.lower().endswith('.mp4'):
            import cv2
            cap = cv2.VideoCapture(image_path)
            success, frame = cap.read()
            cap.release()
            if not success:
                error(f"[get_image_from_target] Failed to read video frame from {image_path}")
                return None
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        else:
            error(f"[get_image_from_target] Unsupported file format: {image_path}")
            return None

        if thumbnail:
            image.thumbnail((256, 256))

        buf = BytesIO()
        image.save(buf, format="JPEG")
        base64_str = base64.b64encode(buf.getvalue()).decode("ascii")
        
        VITE_URL = os.environ.get("VITE_URL", "").rstrip("/")
        
        # Construct URLs using the correct path structure
        raw_url = f"{VITE_URL}/output/{raw_name}" if VITE_URL else f"/output/{raw_name}"

        debug(f"[get_image_from_target] Successfully processed '{file_prefix}' from {image_path}")
        return {
            "name": f"{file_prefix}.jpg",
            "image": base64_str,
            "raw_name": raw_name,
            "raw_url": raw_url,
            "local_path": str(image_path)
        }

    except Exception as e:
        error(f"[get_image_from_target] Failed to process '{file_prefix}': {e}")
        return None
        
def truncate_element(value, max_len=5000):
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + f"...[truncated, total {len(value)} chars]"
    elif isinstance(value, list):
        return [truncate_element(v, max_len) for v in value]
    elif isinstance(value, dict):
        return {k: truncate_element(v, max_len) for k, v in value.items()}
    return value  # other types (int, float, None, etc.)
    
def find_destination_by_alexaclosest(alexa_id: str) -> str | None:
    """
    Find the destination `id` whose 'alexaclosest' field matches the given Alexa deviceId.
    """
    destinations = _load_json_once("destination", "publish-destinations.json")
    for dest in destinations:
        if dest.get("alexaclosest") == alexa_id:
            return dest["id"]
    return None

def get_qr(publish=None, run=False, refiner=None, prompt=None):
    base_url = os.getenv("VITE_URL")

    # Build query parameters
    params = {}
    if publish:
        if isinstance(publish, list):
            params["publish"] = ",".join(publish)
        else:
            params["publish"] = publish
    if prompt:
        params["prompt"] = prompt
    if refiner:
        params["refiner"] = refiner

    # Start with standard query string
    query_string = urlencode(params)

    # Add `run` as a flag (no value) if True
    if run:
        query_string += "&run" if query_string else "run"

    full_url = f"{base_url}?{query_string}" if query_string else base_url

    # Generate QR code image and encode as base64
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3
    )
    qr.add_data(full_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_bytes = buffered.getvalue()
    base64_str = base64.b64encode(img_bytes).decode("utf-8")

    return f"data:image/png;base64,{base64_str}"


def save_video_with_metadata(url: str, img_metadata: dict, save_path: Path):
    # Download the video
    response = requests.get(url, stream=True)
    response.raise_for_status()

    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    info(f"Saved MP4 to {save_path}")

    # Save metadata sidecar as .json
    from routes.publisher import sidecar_path
    metadata_path = sidecar_path(save_path)

    with open(metadata_path, "w", encoding="utf-8") as meta_file:
        json.dump(img_metadata, meta_file, ensure_ascii=False, indent=2)

    info(f"Saved metadata to {metadata_path}")

def save_jpeg_with_metadata(url: str, img_metadata: dict, save_path: Path):
    response = requests.get(url)
    response.raise_for_status()
    img = Image.open(BytesIO(response.content)).convert("RGB")

    # Build EXIF with JSON-formatted metadata
    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    comment = json.dumps(img_metadata, ensure_ascii=False, indent=None)
    user_comment = piexif.helper.UserComment.dump(comment, encoding="unicode")
    exif_dict["Exif"][piexif.ExifIFD.UserComment] = user_comment

    exif_bytes = piexif.dump(exif_dict)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(save_path, "JPEG", exif=exif_bytes, quality=95)
    info(f"Saved JPEG with metadata to {save_path}")

# THESE NEED TESTING


def _extract_exif_json(img_path: Path) -> dict[str, Any] | None:
    """Extract EXIF metadata from an image file."""
    try:
        img = Image.open(img_path)
        exif = img._getexif()
        if not exif:
            return None

        meta = {}
        for tag_id in exif:
            tag = ExifTags.TAGS.get(tag_id, tag_id)
            data = exif.get(tag_id)
            if isinstance(data, bytes):
                data = data.decode()
            meta[tag] = data

        return meta
    except Exception as e:
        warning(f"Failed to extract EXIF from {img_path}: {e}")
        return None

def _extract_mp4_comment_json(video_path: Path) -> dict[str, Any] | None:
    """Extract comment metadata from an MP4 file."""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        comment = data.get("format", {}).get("tags", {}).get("comment")
        if not comment:
            return None

        return json.loads(comment)
    except Exception as e:
        warning(f"Failed to extract comment from {video_path}: {e}")
        return None

def ensure_sidecar_for(file_path: Path) -> None:
    """Ensure a sidecar exists for the given file, creating it if needed."""
    sidecar = sidecar_path(file_path)
    if not sidecar.exists():
        # Extract metadata based on file type
        if file_path.suffix.lower() in ['.jpg', '.jpeg', '.png']:
            metadata = _extract_exif_json(file_path)
        elif file_path.suffix.lower() == '.mp4':
            metadata = _extract_mp4_comment_json(file_path)
        else:
            metadata = None

        # Write metadata to sidecar
        if metadata:
            with open(sidecar, 'w') as f:
                json.dump(metadata, f, indent=2)

def sidecar_path(file_path: Path) -> Path:
    """Return the path for a file's sidecar JSON."""
    return file_path.with_suffix(file_path.suffix + '.json')

def get_groups_for_destination(dest_id: str) -> list:
    """
    Get all groups that a destination belongs to.
    Args:
        dest_id: Destination identifier
    Returns:
        List of group names that the destination belongs to
    """
    try:
        from routes.utils import _load_json_once
        destinations = _load_json_once("destination", "publish-destinations.json")
        for dest in destinations:
            if dest["id"] == dest_id and "groups" in dest:
                return dest.get("groups", [])
        return []  # Destination not found or has no groups
    except Exception as e:
        from utils.logger import error
        error(f"Error getting groups for destination {dest_id}: {str(e)}")
        return []

def get_destinations_for_group(scope: str) -> list:
    """
    Given a scope (destination ID, group name, or 'global'), return a list of destination IDs.
    """
    from routes.utils import _load_json_once
    destinations = _load_json_once("destination", "publish-destinations.json")

    # If scope is 'global', return all destinations with a bucket
    if scope == "global":
        return [d["id"] for d in destinations if d.get("has_bucket", False)]

    # If scope matches a destination ID, return it if it has a bucket
    for d in destinations:
        if d["id"] == scope and d.get("has_bucket", False):
            return [scope]

    # Otherwise, treat as group name
    return [
        d["id"] for d in destinations
        if scope in d.get("groups", []) and d.get("has_bucket", False)
    ]