import os
import difflib
import json

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
