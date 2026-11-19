import argparse
import random
import requests
import json
import os
import re
import sys
import textwrap
from dotenv import load_dotenv, find_dotenv
from datetime import datetime
import routes.openai
from routes.utils import (
    findfile, 
    truncate_element, 
    _load_json_once, 
    detect_file_type,
    save_video_with_metadata,
    save_jpeg_with_metadata
)
from time import sleep, time
import uuid
from utils.alerts import alert
from utils.alerts import health as alerts_health
from routes.publisher import publish_to_destination
from routes.jobs import get_tracker, runpod_api
from routes.jobs.runpod_api import RunPodError
from routes.jobs.workflows import workflow_kind, target_name
from routes import reframe

from io import BytesIO

###########
# LOGGING #
###########

load_dotenv(find_dotenv())

def debug(message: str, **kwargs):
    """Log a debug message"""
    if app_logging:
        return utils.logger.debug(message, **kwargs)
    else:
        print(f"DEBUG: {message}", **kwargs)

def info(message: str, **kwargs):
    """Log an info message"""
    if app_logging:
        return utils.logger.info(message, **kwargs)
    else:
        print(f"INFO: {message}", **kwargs)

def warning(message: str, **kwargs):
    """Log a warning message"""
    if app_logging:
        return utils.logger.warning(message, **kwargs)
    else:
        print(f"WARNING: {message}", **kwargs)

def error(message: str, **kwargs):
    """Log an error message"""
    if app_logging:
        return utils.logger.error(message, **kwargs)
    else:
        print(f"ERROR: {message}", **kwargs)
        
try:
    from utils.logger import log_to_console, info, error, warning, debug, console_logs
except ImportError:
    app_logging = None
   
########
# MAIN #
########

def loosely_matches(a, b):
    # Normalize both strings to lowercase, strip spaces
    a = a.lower().strip()
    b = b.lower().strip()

    # Allow substring match or abbreviation match
    return a in b or b in a or re.sub(r'\s+', '', a) in re.sub(r'\s+', '', b)

# Argument parsing

def get_input_text(input_arg):
    # Check if the input is a file path
    if os.path.isfile(input_arg):
        with open(input_arg, 'r', encoding='utf-8') as file:
            file_contents = file.read()
            return file_contents
    else:
        # Otherwise, return the input argument directly as text
        return input_arg

# Dictionary replacer
INTERACTIVE_SOURCES = ("generator", "alexa", "reframe")   # reframe: a picture extended for a screen, shown at once


def worker_output(output, job_id):
    """The worker's output as {"status": "success", "message": <image URL or path>}. The old worker sends that
    already; worker-comfyui (worker/) sends {"images": [{"type": "s3_url" | "base64", "data": ...}]} (base64 when no
    bucket is set: kept as a file, routes/result_source.py, and its path used)."""
    if output is None:
        # RunPod reports COMPLETED and drops a result over its size limit (17 Nov: a 4K PNG sent inline)
        return {"status": "error", "message": "RunPod finished the job but sent no picture back (an inline result "
                                              "over RunPod's size limit is dropped: the endpoint needs its S3 bucket)"}
    if not isinstance(output, dict) or output.get("message"):
        return output
    if "images" not in output:
        return {"status": "error", "message": str(output.get("error") or "the worker sent no image")[:300]}
    images = output.get("images") or []
    if not images:
        return {"status": "error", "message": output.get("error") or "the worker sent no image"}
    first = images[0]
    if first.get("type") == "s3_url":
        return {"status": "success", "message": first.get("data")}
    import base64
    from routes.result_source import save_inline
    try:
        path = save_inline(base64.b64decode(first.get("data") or ""), first.get("filename"), job_id)
    except Exception as e:
        return {"status": "error", "message": f"the worker's inline picture could not be saved: {e}"[:300]}
    return {"status": "success", "message": path}


def _node_inputs(workflow_data, placeholder):
    """The inputs of the first node whose title carries placeholder (e.g. "{{FINISH}}"), else {}."""
    for node in workflow_data.values():
        if isinstance(node, dict) and placeholder in (node.get("_meta") or {}).get("title", ""):
            return node.get("inputs") or {}
    return {}


def image_size(image):
    """(width, height) of a request image ({"name", "image": base64}) as ComfyUI's LoadImage sees it (EXIF
    orientation applied), else None."""
    import base64
    from PIL import Image, ImageOps
    try:
        with Image.open(BytesIO(base64.b64decode(image.get("image") or ""))) as img:
            return ImageOps.exif_transpose(img).size
    except Exception:
        return None


def size_chosen(workflow_id, width, height):
    """True when the caller asked for a size other than the workflow's default (src/data/workflows.json params)."""
    if not width or not height:
        return False
    entry = next((w for w in _load_json_once("workflow", "workflows.json") if w.get("id") == workflow_id), {})
    defaults = {p.get("id"): p.get("default") for p in entry.get("params") or []}
    try:
        return (int(width), int(height)) != (int(defaults.get("width") or 0), int(defaults.get("height") or 0))
    except (TypeError, ValueError):
        return True


def latent_for_screen(size, box, step=32):
    """The latent (w, h) in the screen's shape at the same pixel count, in steps of 32; the size as it is when it is
    already within the finish's tolerance of the screen's shape."""
    import math
    w, h = int(size[0]), int(size[1])
    aspect = int(box[0]) / int(box[1])
    if abs((w / h) / aspect - 1) <= FINISH_SHAPE_TOLERANCE:
        return w, h
    area = w * h
    return (max(step, round(math.sqrt(area * aspect) / step) * step),
            max(step, round(math.sqrt(area / aspect) / step) * step))


def reshape_photo(images, prompt, aspect, fill, has_prompt):
    """The request's first image turned to the shape (aspect, w / h) before an edit: put on a grey canvas with an
    instruction to extend the scene into it (fill "extend", the default; a graph that takes no prompt is cropped
    instead), or cropped where the vision model finds the subject (fill "crop"). (images, prompt) as sent."""
    import base64
    from PIL import Image, ImageOps
    size = image_size(images[0])
    if not size or not reframe.off_shape(size, aspect):
        return images, prompt
    with Image.open(BytesIO(base64.b64decode(images[0]["image"]))) as img:
        photo = ImageOps.exif_transpose(img).convert("RGB")
    if fill != "crop" and has_prompt:
        photo, _ = reframe.pad(photo, aspect)
        prompt = reframe.EXTEND_EDIT + (prompt or "")
    else:
        photo = reframe.ai_crop(photo, aspect)
    info(f"Photo {size[0]}x{size[1]} -> {photo.width}x{photo.height} ({'crop' if fill == 'crop' or not has_prompt else 'extend'})")
    buf = BytesIO()
    photo.save(buf, format="JPEG", quality=95)
    return [{**images[0], "image": base64.b64encode(buf.getvalue()).decode()}] + list(images[1:]), prompt


# A picture this close to the screen's shape fills it (the crop trims a sliver); any further off, it keeps its shape
FINISH_SHAPE_TOLERANCE = 0.02


def finish_size(picture, box, turn=False):
    """The size the {{FINISH}} node scales the picture to: inside box, keeping the picture's shape (owner, 19 Nov: a
    portrait photo adapted came back cropped to a landscape screen). turn: the box is a size budget, not a screen, so
    it takes the picture's orientation. picture None: the box as it is."""
    box_w, box_h = int(box[0]), int(box[1])
    if not picture or not picture[0] or not picture[1]:
        return box_w, box_h
    w, h = int(picture[0]), int(picture[1])
    if turn and w != h and (w > h) != (box_w > box_h):
        box_w, box_h = box_h, box_w
    shape, box_shape = w / h, box_w / box_h
    if abs(shape / box_shape - 1) <= FINISH_SHAPE_TOLERANCE:
        return box_w, box_h
    if shape > box_shape:
        return box_w, max(1, round(box_w / shape))
    return max(1, round(box_h * shape)), box_h


def update_workflow(json_data, replacements):
    """
    Updates 'inputs' of matching nodes in a workflow JSON based on regex matches
    against the _meta.title field (case-insensitive).

    :param json_data: A Python dictionary loaded from a workflow JSON file.
    :param replacements: A dict where:
        - Keys are regex patterns (e.g., r"KSampler")
        - Values are dicts of input_key: new_value pairs to apply to matching nodes.
    :return: Updated JSON dictionary with replacements applied.
    """
    for node_id, node_def in json_data.items():
        node_title = node_def.get("_meta", {}).get("title", "")

        for pattern, input_changes in replacements.items():
            if re.search(pattern, node_title, flags=re.IGNORECASE):
                for input_key, new_value in input_changes.items():
                    if new_value is None:
                        continue
                    if input_key in node_def.get("inputs", {}):
                        node_def["inputs"][input_key] = new_value
                        if len(str(new_value)) > 60:
                            info(
                                f"  - Replaced {input_key} in '{node_title}' with:\n"
                                f"{textwrap.indent(textwrap.fill(str(new_value), width=80), '      ')}"
                            )
                        else:
                            info(f"  - Replaced {input_key} in '{node_title}' with: {new_value}")

    return json_data

def mutate_workflow_for_node_skip(workflow_data, workflow_config, args_namespace):
    """
    Mutate workflow at runtime to skip nodes based on mutate parameters.
    
    Args:
        workflow_data: Loaded workflow JSON
        workflow_config: Workflow configuration from workflows.json
        args_namespace: Parsed arguments containing parameter values
    
    Returns:
        Modified workflow_data with mutations applied
    """
    from copy import deepcopy
    
    # Get parameters from workflow config
    parameters = workflow_config.get("params", {})
    
    # Find parameters that should trigger node skipping
    nodes_to_skip = []
    inputs_to_remove = []
    
    for param in parameters:
        if param.get("mutate") == "skip_nodes":
            param_id = param.get("id")
            param_pattern = param.get("pattern")
            
            # Get parameter value from args_namespace
            # Python argparse converts hyphens to underscores, so check both versions
            param_value = None
            if hasattr(args_namespace, param_id):
                param_value = getattr(args_namespace, param_id)
            elif hasattr(args_namespace, param_id.replace("-", "_")):
                param_value = getattr(args_namespace, param_id.replace("-", "_"))
            
            info(f"Checking parameter '{param_id}' (pattern: {param_pattern}): {param_value}")
            
            # Only skip nodes if parameter is True
            if param_value is True:
                info(f"Parameter '{param_id}' is True - will skip nodes with pattern '{param_pattern}'")
                
                # Find all nodes with this pattern in their titles
                for node_id, node_data in workflow_data.items():
                    if isinstance(node_data, dict) and "_meta" in node_data:
                        title = node_data["_meta"].get("title", "")
                        if f"{{{{{param_pattern}}}}}" in title:
                            info(f"Found node to skip: {node_id} - {title}")
                            nodes_to_skip.append(node_id)
            else:
                info(f"Parameter '{param_id}' is {param_value} - keeping nodes with pattern '{param_pattern}'")
    
    # Apply node removals
    if nodes_to_skip:
        info(f"Will remove {len(nodes_to_skip)} nodes: {nodes_to_skip}")
        
        # Build complete dependency graph
        dependencies = {}  # {node_id: {input_name: (source_node_id, source_output_index)}}
        dependents = {}    # {node_id: [(dependent_node_id, input_name)]}
        
        for node_id in workflow_data:
            dependencies[node_id] = {}
            dependents[node_id] = []
        
        # Map all connections
        for node_id, node_def in workflow_data.items():
            inputs = node_def.get("inputs", {})
            for input_name, input_value in inputs.items():
                if isinstance(input_value, list) and len(input_value) >= 2:
                    source_node_id = str(input_value[0])
                    source_output_idx = input_value[1]
                    if source_node_id in workflow_data:
                        dependencies[node_id][input_name] = (source_node_id, source_output_idx)
                        dependents[source_node_id].append((node_id, input_name))
        
        # For each node being removed, find replacement sources
        replacement_map = {}  # {removed_node_id: replacement_node_id}
        
        for skip_node_id in nodes_to_skip:
            if skip_node_id not in workflow_data:
                continue
                
            # Find the best replacement by tracing back through dependencies
            # Look for the first non-removed node in the dependency chain
            replacement = None
            
            # Check direct inputs first
            for input_name, (source_node_id, source_output_idx) in dependencies[skip_node_id].items():
                if source_node_id not in nodes_to_skip:
                    # Found a direct input that's not being removed
                    replacement = source_node_id
                    break
            
            # If no direct replacement, trace back through the chain
            if not replacement:
                # BFS to find the nearest non-removed node
                visited = set()
                queue = [skip_node_id]
                
                while queue and not replacement:
                    current = queue.pop(0)
                    if current in visited:
                        continue
                    visited.add(current)
                    
                    for input_name, (source_node_id, source_output_idx) in dependencies.get(current, {}).items():
                        if source_node_id not in nodes_to_skip:
                            replacement = source_node_id
                            break
                        else:
                            queue.append(source_node_id)
            
            if replacement:
                replacement_map[skip_node_id] = replacement
                info(f"Node {skip_node_id} will be replaced by {replacement}")
            else:
                info(f"No replacement found for node {skip_node_id}")
        
        # Apply rerouting before removing nodes
        for skip_node_id in nodes_to_skip:
            if skip_node_id not in workflow_data:
                continue
                
            replacement_node_id = replacement_map.get(skip_node_id)
            if not replacement_node_id:
                continue
                
            # Reroute all dependents of this node to the replacement
            for dependent_node_id, input_name in dependents[skip_node_id]:
                if dependent_node_id in workflow_data and dependent_node_id not in nodes_to_skip:
                    inputs = workflow_data[dependent_node_id].get("inputs", {})
                    if input_name in inputs:
                        old_connection = inputs[input_name]
                        if isinstance(old_connection, list) and len(old_connection) >= 2:
                            # Keep the same output index, just change the source node
                            inputs[input_name][0] = replacement_node_id
                            info(f"Rerouted {dependent_node_id}.{input_name} from {skip_node_id} to {replacement_node_id}")
        
        # Remove the nodes
        for skip_node_id in nodes_to_skip:
            if skip_node_id in workflow_data:
                del workflow_data[skip_node_id]
                info(f"Removed node {skip_node_id} from workflow")
    
    return workflow_data

def apply_workflow_mutations(workflow_data, workflow_config, args_namespace):
    """
    Apply all workflow mutations based on parameters.
    
    Args:
        workflow_data: Loaded workflow JSON
        workflow_config: Workflow configuration from workflows.json  
        args_namespace: Parsed arguments containing parameter values
    
    Returns:
        Modified workflow_data with all mutations applied
    """
    # Apply node skip mutations
    workflow_data = mutate_workflow_for_node_skip(workflow_data, workflow_config, args_namespace)
    
    # Future: Add other mutation types here
    
    return workflow_data

RUNPOD_TIMEOUT_S = 1200  # 20 minutes: enough for WAN2 video jobs


class JobCancelled(Exception):
    """The job was cancelled through the tracker; it is already marked cancelled."""


def _plain_error(e):
    """An exception -> the one-line reason shown on the job."""
    if isinstance(e, (RunPodError, JobCancelled)):
        return str(e)
    text = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return text[:300] or type(e).__name__


def start(
        prompt: str | None = None, 
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        cfg: float | None = None,
        batch: int | None = None,
        pod: str | None = None,
        scale: int | None = None,
        setwallpaper: int | None = None,
        refine: str | None = None,
        workflow: str | None = None,
        seed: int | None = None,
        timeout: int | None = None,
        suppress: bool | None = None,
        metaprompt: bool | None = None,
        images: list[dict[str, str]] | None = None,
        negativeprompt: str | None = None,
        interpolate_frames: int | None = None,
        upscaler: str | None = None,
        lora: str | None = None,
        video_length: int | None = None,
        lora_strength: float | None = None,
        shape: str | None = None,
        fill: str | None = None,
        cli_args=None,
        publish_destination: str | None = None,
        batch_id: str | None = None,
        silent: bool | None = None,
        job_id: str | None = None,
        **kwargs
        ):
    """Run one generation job on RunPod and publish it. Every step is reported to the job tracker
    (docs/JOBS.md); job_id is the tracker job made by handle_image_generation (a new one when called
    directly). Any failure ends the job as failed and raises ValueError."""

    # Basic init
    info("Generator starting.")
    tracker = get_tracker()
    if not job_id:
        job_id = tracker.create(prompt=prompt or "", source="api", target=publish_destination,
                                target_name=target_name(publish_destination), batch_id=batch_id)["id"]
    try:
        return _run(tracker, job_id, provided_args=dict(
            prompt=prompt, width=width, height=height, steps=steps, cfg=cfg, batch=batch, pod=pod,
            scale=scale, setwallpaper=setwallpaper, refine=refine, workflow=workflow, seed=seed,
            timeout=timeout, suppress=suppress, metaprompt=metaprompt, images=images,
            negativeprompt=negativeprompt, interpolate_frames=interpolate_frames, upscaler=upscaler,
            lora=lora, video_length=video_length, lora_strength=lora_strength, shape=shape, fill=fill),
            cli_args=cli_args, publish_destination=publish_destination, batch_id=batch_id,
            silent=silent, kwargs=kwargs)
    except JobCancelled as e:
        raise ValueError(str(e))
    except Exception as e:
        tracker.fail(job_id, _plain_error(e))
        raise ValueError(str(e))


def _run(tracker, job_id, provided_args, cli_args, publish_destination, batch_id, silent, kwargs):
    missing_vars = [var for var in ["OPENAI_API_KEY", "RUNPOD_API_KEY", "RUNPOD_ID"] if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"Missing environment variables: {', '.join(missing_vars)}")
    
    # Track generation start time
    generation_start_time = time()
        
    # Get params
    parser = get_parser() 
    if cli_args is not None:                    # called from command line
        args_namespace = parser.parse_args(cli_args)
    else:                                       # called as  a function
        defaults = vars(parser.parse_args([]))  # extract defaults

        # Merge defaults with provided arguments (use provided if not None, else default)
        final_args = {**defaults}
        final_args.update({k: v for k, v in provided_args.items() if v is not None})
        info(f"Kwags: {kwargs}")
        final_args.update(kwargs)
        args_namespace = argparse.Namespace(**final_args)
    vars(args_namespace).setdefault("images", None)  # absent when the caller passed none

    # Validation steps
    if not args_namespace.prompt and not args_namespace.images: 
        raise ValueError("A prompt or image must be provided.")
    
    # Get destinations data and see if we have a size constraint
    maxwidth = maxheight = None
    if publish_destination:
        info(f"publish_destination: {publish_destination}")
        with open(findfile("publish-destinations.json"), "r") as file:
            publish_destinations = json.load(file)
        
        target_config = next(
            (item for item in publish_destinations if
             loosely_matches(item.get("id", ""), publish_destination) or
             loosely_matches(item.get("name", ""), publish_destination)),
            {}
        )
        
        maxwidth = target_config.get("maxwidth", None)
        maxheight = target_config.get("maxheight", None)
        targetfile=target_config.get('file')
        info(f"Target config: {targetfile}")
    
    # A retired workflow named by an old schedule or caller runs on its replacement (workflows.json "replaced_by")
    retired = next((w for w in _load_json_once("workflow", "workflows.json")
                    if w.get("id") == args_namespace.workflow and w.get("replaced_by")), None)
    if retired:
        info(f"Workflow {args_namespace.workflow} is retired; using {retired['replaced_by']}")
        args_namespace.workflow = retired["replaced_by"]
    tracker.update(job_id, workflow=args_namespace.workflow, refined_prompt=args_namespace.prompt,
                   message="Loading the workflow")
    info(f"Trying to load: {args_namespace.workflow}")
    with open(findfile(args_namespace.workflow), "r") as file:
        workflow_data = json.load(file)
    info(f"Successfully loaded: {args_namespace.workflow}")
    
    # A photo to be edited into another shape (Adapt/Combine's "shape" and "fill"): turned before the edit, so the
    # edit encoder's latent takes the new shape; one job (routes/reframe.py)
    latent = _node_inputs(workflow_data, "{{LATENT-IMAGE}}")
    if args_namespace.images and not latent and vars(args_namespace).get("shape") in reframe.SHAPES:
        args_namespace.images, args_namespace.prompt = reshape_photo(
            args_namespace.images, args_namespace.prompt, reframe.SHAPES[args_namespace.shape],
            vars(args_namespace).get("fill"), bool(_node_inputs(workflow_data, "{{POSITIVE-PROMPT}}")))

    screen_box = (maxwidth, maxheight) if maxwidth and maxheight else (
        (args_namespace.maxwidth, args_namespace.maxheight)
        if vars(args_namespace).get("maxwidth") and vars(args_namespace).get("maxheight") else None)

    # A picture drawn from text for a screen of another shape (a phone, 1080x2400) is drawn in the screen's shape, at
    # the workflow's own pixel count, unless the caller chose its size (owner, 19 Nov: a portrait screen got a
    # landscape picture letterboxed). The page always sends the workflow's default size, so that counts as not chosen.
    if latent and screen_box:
        size = (args_namespace.width or latent.get("width"), args_namespace.height or latent.get("height"))
        if not size_chosen(args_namespace.workflow, args_namespace.width, args_namespace.height):
            drawn = latent_for_screen(size, screen_box)
            if drawn != tuple(int(v) for v in size):
                info(f"Drawn {drawn[0]}x{drawn[1]} for a {screen_box[0]}x{screen_box[1]} screen (was {size[0]}x{size[1]})")
                args_namespace.width, args_namespace.height = drawn

    # The finish: the picture's own shape inside the screen's box. The picture is the latent's shape, or for a graph
    # with no {{LATENT-IMAGE}} (edit, combine, upscale) the first reference image's: Qwen 2.1's edit encoder makes
    # its latent on that image's shape. With no screen, the box (the caller's size, else the graph's) turns to suit.
    if latent:
        picture = (args_namespace.width or latent.get("width"), args_namespace.height or latent.get("height"))
    else:
        picture = image_size(args_namespace.images[0]) if args_namespace.images else None
    finish_node = _node_inputs(workflow_data, "{{FINISH}}")
    finish = None
    if finish_node:
        box = screen_box or ((args_namespace.width, args_namespace.height)
                             if args_namespace.width and args_namespace.height
                             else (finish_node.get("width"), finish_node.get("height")))
        finish = finish_size(picture, box, turn=screen_box is None)
        info(f"Finish: picture {picture} -> {finish[0]}x{finish[1]} (box {box})")

    unique_video_name = f"output_video{str(uuid.uuid4())[:8]}"
    json_replacements = {
        r"{{LATENT-IMAGE}}": {  
            "width": args_namespace.width,
            "height": args_namespace.height,
            "batch_size": args_namespace.batch
        },
        r"{{POSITIVE-PROMPT}}": {  
            "text":  args_namespace.prompt,
            "prompt": args_namespace.prompt,    # Qwen-Image's own text encoder node
            "value": args_namespace.prompt      # a string node (PrimitiveStringMultiline) that feeds several encoders
        },
        r"{{NEGATIVE[-_]PROMPT}}": {  
            "text": vars(args_namespace).get("negativeprompt", None)
        },
        r"{{UPSCALER}}": {
            "scale_by": args_namespace.scale,
            "model_name": vars(args_namespace).get("upscaler", None)
        },
        r"{{SAMPLER}}": {
            "seed": args_namespace.seed,
            "noise_seed": args_namespace.seed,            
            "steps": args_namespace.steps,
            "scheduler": vars(args_namespace).get("scheduler", None),
            "sampler":vars(args_namespace).get("sampler", None),
            "sampler_name": vars(args_namespace).get("sampler", None),
            "width": args_namespace.width,
            "height": args_namespace.height,
            "cfg": vars(args_namespace).get("cfg", None)
        },
        r"{{LORA}}": {
            "lora_name": vars(args_namespace).get("lora", None),
            "strength_model": vars(args_namespace).get("lora_strength", None),
            "strength_clip": vars(args_namespace).get("lora_strength", None)
        },
        r"{{DOWNSCALER}}": {
            # The destination's size, else the caller's maxwidth/maxheight (the scheduler sends the screen's when it
            # saves to the bucket without publishing), else no limit
            "width": maxwidth or vars(args_namespace).get("maxwidth") or 6400,
            "height": maxheight or vars(args_namespace).get("maxheight") or 6400
        },
        # The new worker's graphs (worker/): render at the model's own size, then one lanczos scale to the finish
        # above (its centre crop trims at most a sliver), and the sampling shift for the latent's size
        r"{{FINISH}}": {
            "width": finish[0] if finish else None,
            "height": finish[1] if finish else None
        },
        r"{{SHIFT}}": {
            "width": args_namespace.width,
            "height": args_namespace.height
        },
        r"{{INTERPOLATOR}}": {
            "multiplier": vars(args_namespace).get("interpolate_frames", None)
        },        
        r"{{IMAGE-TO-VIDEO}}": {
            "length": vars(args_namespace).get("video_length", None),
            "width": args_namespace.width,
            "height": args_namespace.height
        },
        r"{{SAVE-WEBM}}": {
            "filename_prefix": unique_video_name
        }
    }
    
    # Reference images as appropriate
    
    # Load workflows and find matching workflow metadata
    master_workflow_data = _load_json_once("workflow", "workflows.json")
    workflow_id = args_namespace.workflow  # adjust if needed
    master_workflow_meta = next((w for w in master_workflow_data if w["id"] == workflow_id), {})

    # Check whether this workflow expects 2 images
    uses_two_images = master_workflow_meta.get("uses_images", 1) == 2
    
    debug("!!! uses_two_images !!!")

    # Always set the first image if any images are provided
    if args_namespace.images and len(args_namespace.images) > 0:
        json_replacements[r"{{LOAD-IMAGE}}"] = {
            "image": args_namespace.images[0].get("name")
        }

        # Only add LOAD-IMAGE-2 if the workflow explicitly expects 2 images
        if uses_two_images:
            second_image = (
                args_namespace.images[1].get("name")
                if len(args_namespace.images) > 1
                else args_namespace.images[0].get("name")
            )
            json_replacements[r"{{LOAD-IMAGE-2}}"] = {
                "image": second_image
            }


    # Use the right runpod container id and get workflow config
    workflow_config_list = _load_json_once("workflow", "workflows.json")
    
    # Find the specific workflow configuration
    workflow_config = next(
        (item for item in workflow_config_list if item["id"] == args_namespace.workflow),
        {}
    )
    
    # Apply runtime mutations based on parameters
    workflow_data = apply_workflow_mutations(workflow_data, workflow_config, args_namespace)
    
    # Compile the final object to submit
    input_payload = {
      "input": {
        "workflow": update_workflow(workflow_data, json_replacements),
      }
    }
    if args_namespace.images:
        input_payload["input"]["images"] = args_namespace.images

    runpod_id = next(
        (item.get("runpod_id") for item in workflow_config_list if item["id"] == args_namespace.workflow and item.get("runpod_id")),
        getattr(args_namespace, "pod", None) or os.getenv("RUNPOD_ID")
    )
    # Someone waiting at the generator page or Alexa runs on the workflow's interactive endpoint (warm for 2 minutes
    # after a job or a warm-up); schedules keep the batch endpoint, which goes idle at once (owner, 18 Nov)
    if tracker.get(job_id).get("source") in INTERACTIVE_SOURCES and workflow_config.get("runpod_id_live"):
        runpod_id = workflow_config["runpod_id_live"]

    info(f"Workflow: {args_namespace.workflow}")
    info(f"RunPod ID: {runpod_id}")
    tracker.set_stages(job_id, workflow_config.get("processing_stages"))
    tracker.update(job_id, kind=workflow_kind(workflow_config), endpoint_id=runpod_id,
                   message="Sending to RunPod")

    if tracker.is_cancelled(job_id):
        raise JobCancelled("Cancelled before it was sent to RunPod")

    # Let's go...
    runpod_job_id = runpod_api.submit(runpod_id, input_payload)
    submitted_at = time()
    info(f"RunPod job {runpod_job_id} submitted for {job_id}")
    tracker.update(job_id, status="queued", runpod_job_id=runpod_job_id, endpoint_id=runpod_id,
                   message="Waiting for a GPU")

    poll_interval = getattr(args_namespace, "poll_interval", 0.5)
    info(f"> Polling RunPod every {poll_interval}s for up to {RUNPOD_TIMEOUT_S}s...")
    alert_context = {"job_id": runpod_job_id, "endpoint": runpod_id,
                     "workflow": args_namespace.workflow, "destination": publish_destination}
    start_time = time()
    output = None
    while True:
        if tracker.is_cancelled(job_id):
            # The tracker cancelled the RunPod job when it was cancelled; cancel again in case the
            # cancel landed between submit and the queued update above
            runpod_api.cancel(runpod_id, runpod_job_id, timeout=10)
            raise JobCancelled("Cancelled")

        try:
            state = runpod_api.status(runpod_id, runpod_job_id)
        except RunPodError as e:
            # A missed poll isn't fatal; the local timeout below still bounds the job
            warning(f"RunPod status for {runpod_job_id} failed: {e}")
            state = {}
        status = state.get("status")

        if status == "IN_QUEUE":
            tracker.update(job_id, status="queued", message="Waiting for a GPU")

        elif status == "IN_PROGRESS":
            if tracker.get(job_id)["status"] == "queued":
                tracker.update(job_id, status="running", message="Starting the workflow")
            # Step progress: the new worker (worker/) reports it in the job's status ({"comfy": frame}); the old
            # worker's ComfyUI relay sends the same frames through overlay_ws_server
            frame = (state.get("output") or {}).get("comfy") if isinstance(state.get("output"), dict) else None
            if frame:
                tracker.on_worker_frame(runpod_job_id, frame)

        elif status == "COMPLETED":
            output = worker_output(state.get("output"), job_id)
            final_state = state
            break

        elif status in ("FAILED", "TIMED_OUT"):
            alert_key = "runpod.job_failed" if status == "FAILED" else "runpod.timeout"
            alert(alert_key,
                  f"RunPod job {status}: workflow '{args_namespace.workflow}' on endpoint {runpod_id}",
                  context=alert_context, dedup_key=f"{alert_key}:{args_namespace.workflow}")
            if status == "TIMED_OUT":
                raise RunPodError("RunPod timed the job out")
            detail = state.get("error")
            if isinstance(detail, (dict, list)):
                detail = json.dumps(detail)
            detail = str(detail or "").strip().splitlines()[0][:200] if detail else ""
            raise RunPodError("The job failed on RunPod" + (f": {detail}" if detail else ""))

        elif status == "CANCELLED":
            tracker.update(job_id, status="cancelled", message="Cancelled on RunPod")
            raise JobCancelled("Cancelled on RunPod")

        if time() - start_time > RUNPOD_TIMEOUT_S:
            alert("runpod.timeout",
                  f"RunPod job timed out after {RUNPOD_TIMEOUT_S}s — if it never left "
                  f"IN_QUEUE, check RunPod credit",
                  context=alert_context, dedup_key=f"runpod.timeout:{args_namespace.workflow}")
            runpod_api.cancel(runpod_id, runpod_job_id, timeout=10)
            raise TimeoutError(f"Timed out after {RUNPOD_TIMEOUT_S // 60} minutes; the RunPod job was cancelled")

        # A wait=True generation blocks its scheduler loop thread in
        # join() for up to 20 min; tick from here so a busy destination
        # doesn't read as wedged (HEARTBEAT_STALE_S is only 10 min)
        for _dest in ([publish_destination]
                      if isinstance(publish_destination, str)
                      else (publish_destination or [])):
            alerts_health.tick(_dest)
        sleep(poll_interval)

    if not isinstance(output, dict) or output.get("status") != "success":
        detail = output.get("message") if isinstance(output, dict) else output
        detail = str(detail or "").strip()[:200]
        raise RuntimeError("The worker sent no result" + (f": {detail}" if detail else ""))

    info(f"> Successfully generated image")
    tracker.update(job_id, status="publishing", message="Publishing" if publish_destination else "Finishing")
    output["seed"] = args_namespace.seed
    output["prompt"] = args_namespace.prompt
    output["negative_prompt"] = vars(args_namespace).get("negativeprompt", None)
    
    # Store the actual resolved parameters that were used during generation
    output["actual_workflow_id"] = args_namespace.workflow
    output["actual_params"] = {
        "width": args_namespace.width,
        "height": args_namespace.height,
        "steps": args_namespace.steps,
        "cfg": getattr(args_namespace, 'cfg', None),
        "scale": args_namespace.scale,
        "upscaler": getattr(args_namespace, 'upscaler', None),
        "lora": getattr(args_namespace, 'lora', None),
        "lora_strength": getattr(args_namespace, 'lora_strength', None),
        "negativeprompt": getattr(args_namespace, 'negativeprompt', None),
        "interpolate_frames": getattr(args_namespace, 'interpolate_frames', None),
        "video_length": getattr(args_namespace, 'video_length', None),
        "publish_destination": publish_destination,
    }
    output["actual_global_params"] = {
        "batch_size": args_namespace.batch or 1,
    }
    
    # Calculate generation time and cost
    generation_end_time = time()
    generation_time_seconds = round(generation_end_time - generation_start_time, 2)
    
    # Import the function from generate_handler
    from routes.generate_handler import calculate_generation_cost
    billing = runpod_api.billing(runpod_id, final_state or {}, submitted_at, generation_end_time)
    generation_cost = calculate_generation_cost(
        generation_time_seconds, 
        workflow_id=args_namespace.workflow,
        runpod_id=runpod_id,
        billing=billing,
    )
    
    # Add time and cost to output metadata
    output["generation_time_seconds"] = generation_time_seconds
    output["generation_cost_gbp"] = generation_cost
    output["job_id"] = job_id
    
    info(f"Generation completed in {generation_time_seconds} seconds, estimated cost: £{generation_cost}")
    info("\n".join(f"  - {key}: {value}" for key, value in output.items()))
    
    output.update(input_payload)
    
    # We're done with the base-64 image data now--discard it so we aren't carrying it around
    if "images" in input_payload["input"]:
        input_payload["input"]["images"] = [
            {"name": img.get("name")} for img in input_payload["input"]["images"]
        ]
    if hasattr(args_namespace, "images") and args_namespace.images:
        args_namespace.images = [
            {"name": img.get("name")} for img in args_namespace.images
        ]

    # Add generation time and cost to args_namespace for metadata
    vars(args_namespace)["generation_time_seconds"] = generation_time_seconds
    vars(args_namespace)["generation_cost_gbp"] = generation_cost

    # Include batch_id in metadata if provided
    if batch_id is not None:
        vars(args_namespace)["batch_id"] = batch_id

    # A screen clip (workflows.json "loop") gets its slow ease rendered in, on the full-size still it was made from
    loop = next((w.get("loop") for w in _load_json_once("workflow", "workflows.json")
                 if w.get("id") == args_namespace.workflow), None)
    still_image = vars(args_namespace).pop("still_image", None)
    if loop:
        pace = vars(args_namespace).get("pace") or loop.get("pace", "slow")
        loop = {**loop, **(loop.get("paces") or {}).get(pace, {}), "pace": pace}
        from routes.clip_ease import ease_result
        tracker.update(job_id, message="Easing the clip")
        output["message"], clip = ease_result(output["message"], loop, job_id,
                                              still=still_image)
        info(f"Eased clip: {clip}")
        vars(args_namespace)["clip"] = clip   # a lot of movement only lengthens its rests; a clip is never thrown away

    if publish_destination:
        if tracker.is_cancelled(job_id):
            raise JobCancelled("Cancelled before it was published")

        # A clip of a still goes on the screen only if the screen still shows that still; otherwise it is just
        # filed with it (routes/clips.py)
        on_screen = True
        if vars(args_namespace).get("still") and isinstance(publish_destination, str):
            from routes.clips import published_still
            showing = published_still(publish_destination)
            on_screen = showing in (None, vars(args_namespace)["still"])
            if not on_screen:
                info(f"{publish_destination} now shows {showing}: the clip is filed with its still, not shown")

        # Actually publish into your screen's bucket
        pub_res = publish_to_destination(
            source = output["message"],
            publish_destination_id = publish_destination,
            metadata = vars(args_namespace),
            batch_id = batch_id,
            silent = silent,
            update_published = on_screen,
        )
        if not pub_res["success"]:
            raise RuntimeError(f"Publish failed: {pub_res.get('error')}")

        # Only access meta data if publish was successful
        if "meta" in pub_res and "filename" in pub_res["meta"]:
            info(f"✅ Published to {pub_res['meta']['filename']}")
            dest_info = pub_res["meta"]
            output["published_path"] = pub_res["meta"]["filename"]
            output["published_meta"] = pub_res.get("meta", {})
            output["destination"] = dest_info
        else:
            info("✅ Published successfully")
            output["published_path"] = output["message"]
            output["published_meta"] = {}
            output["destination"] = {}

    tracker.complete(job_id, result_url=output.get("message"), seed=args_namespace.seed,
                     cost_gbp=generation_cost, billing=billing, cost_state="provisional" if billing else None,
                     published_path=output.get("published_path"),
                     refined_prompt=args_namespace.prompt)
    return output

# Handle if called from command line
def get_parser():
    parser = argparse.ArgumentParser(description="Example script with parameters")
    parser.add_argument("prompt", nargs="?", help="Enter prompt text (e.g. 'cat on a sofa')")
    parser.add_argument("--width", type=int, help="Width of image")
    parser.add_argument("--height", type=int, help="Height of image")
    parser.add_argument("--steps", type=int, help="Number of rendering steps")
    parser.add_argument("--scale", default=1, type=int, help="Output scale factor")
    parser.add_argument("--pod", default=os.getenv("RUNPOD_ID"), type=str, help="Runpod ID")
    parser.add_argument(
        "--setwallpaper", 
        nargs="?",               				# Allows zero or one argument
        const=1,             	                        # Used when --refine is present but no value given
        default=None,
        help="Set your desktop wallpaper to this image?"
    )
    parser.add_argument(
        "--refine",
        nargs="?",               				# Allows zero or one argument
        const="refiner-system-prompt.txt",             	# Used when --refine is present but no value given
        default=None,           				# Used when --refine is not provided at all
        help="Optional refinement argument"
    )
    parser.add_argument("--workflow", default="flux1-dev-scale-l.json", type=str, help="Specify the workflow json (with params inserted)")
    parser.add_argument("--seed", default=random.randint(0,99999999), type=int, help="Seed (default = random)")
    parser.add_argument("--timeout", default=300, help="How many seconds to wait.")
    parser.add_argument("--suppress", action="store_true", help="Don't deliver the image to console.")
    parser.add_argument("--batch", default=1, type=int, help="Generate multiple images")
    parser.add_argument("--metaprompt", action="store_true", help="Take the prompt as an instruction to OpenAI to generate a prompt")

    return parser
    
# This was called from command line, so handle accoringly
if __name__ == "__main__":
    try:
        info("Called as CLI.")
        start(cli_args=sys.argv[1:])  # Use CLI args automatically
    except ValueError as e:
        error(e)
