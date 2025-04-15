import os
import difflib
import json
from PIL import Image
import io
import base64
from werkzeug.utils import secure_filename
import requests
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from io import BytesIO

# Internal JSON cache
_json_cache = {}

# Find file
def findfile(file_param):
    """
    Check if a file exists with or without a path.
    If only a filename is given, check multiple predefined directories.
    """
    # 1. Check if the provided path is already valid
    if os.path.exists(file_param):
        return file_param  # Use as-is

    # 2. Set script location
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 3. Check in predefined workflow directories
    workflow_dirs = [
        os.path.join(os.getcwd(), "src", "data"),                 # ./ssrc/data
        os.path.join(os.getcwd(), "routes", "overlays"),             # ./routes/overlays
        os.path.join(script_dir, "data"),                         # script/data
        os.path.join(script_dir, "workflows"),                    # script/workflows
        os.path.join(script_dir, "sysprompts"),                   # script/sysprompts
        os.path.join(script_dir, "src", "workflows"),             # script/src/workflows
        os.path.join(script_dir, "src", "data", "workflows")      # script/src/data/workflows
    ]

    for directory in workflow_dirs:
        file_in_dir = os.path.join(directory, file_param)
        if os.path.exists(file_in_dir):
            return file_in_dir

    # 4. Check in current working directory
    file_in_cwd = os.path.join(os.getcwd(), file_param)
    if os.path.exists(file_in_cwd):
        return file_in_cwd

    # 5. Check in script directory directly
    file_in_script_dir = os.path.join(script_dir, file_param)
    if os.path.exists(file_in_script_dir):
        return file_in_script_dir

    # 6. Check in user's home directory
    home_dir = os.path.expanduser("~")
    file_in_home = os.path.join(home_dir, file_param)
    if os.path.exists(file_in_home):
        return file_in_home

    print(f"*** FAILED TO FIND *** {file_param}")
    return None

def _load_json_once(name, filename):
    """Find and load a JSON file using findfile(), cache it by name."""
    if name not in _json_cache:
        path = findfile(filename)
        if not path:
            raise FileNotFoundError(f"Could not locate file: {filename}")
        with open(path, "r", encoding="utf-8") as f:
            _json_cache[name] = json.load(f)
    return _json_cache[name]

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
    """
    Resolve a single category (refiner, workflow, destination) to a specific field from the matched item.
    
    If match_key is not provided, tries all string fields and selects the best fuzzy match.
    """
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
    
import base64, os, requests
from PIL import Image
from io import BytesIO
from utils.logger import info, error

def encode_reference_urls(reference_urls, max_file_size_mb=5):
    """
    Downloads, compresses, and base64-encodes images from external URLs.
    Returns a list of dicts: {name, image}
    """
    result = []
    max_size_bytes = max_file_size_mb * 1024 * 1024

    # Normalize and dedupe: remove query params
    normalized = list({url.split("?")[0]: url for url in reference_urls}.values())
    info(f"[encode_reference_urls] Called with {len(reference_urls)} raw, {len(normalized)} unique URLs")

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

    for url in normalized:
        try:
            info(f"[encode_reference_urls] Fetching: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            info(f"[encode_reference_urls] Content-Type: {content_type}")

            image = Image.open(BytesIO(response.content)).convert("RGB")
            format = image.format or "PNG"
            compressed_bytes = compress_image(image, max_size_bytes)
            image_base64 = base64.b64encode(compressed_bytes).decode('utf-8')

            result.append({
                "name": os.path.basename(url.split("?")[0]).replace(".png", ".jpg"),
                "image": image_base64
            })

            info(f"[encode_reference_urls] Encoded {os.path.basename(url)} (length={len(image_base64)})")

        except Exception as e:
            error(f"[encode_reference_urls] Failed to process {url}: {str(e)}")

    return result
