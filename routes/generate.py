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
import routes.display
from time import sleep, time 
import uuid
from overlay_ws_server import job_progress_listeners_latest
from routes.manage_jobs import add_job
from routes.publisher import publish_to_destination

# Runpod
import runpod
import requests

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
        cli_args=None,
        publish_destination: str | None = None,
        batch_id: str | None = None,
        silent: bool | None = None,
        **kwargs
        ):

    # Basic init
    info("Generator starting.")
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    missing_vars = [var for var in ["OPENAI_API_KEY", "RUNPOD_API_KEY", "RUNPOD_ID"] if not os.getenv(var)]
    if missing_vars:
        raise ValueError(f"X Missing environment variables: {', '.join(missing_vars)}. Fatal.")
    
    # Track generation start time
    generation_start_time = time()
        
    # Get params
    parser = get_parser() 
    if cli_args is not None:                    # called from command line
        args_namespace = parser.parse_args(cli_args)
    else:                                       # called as  a function
        defaults = vars(parser.parse_args([]))  # extract defaults
        
        provided_args = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": steps,
            "batch": batch,
            "pod": pod,
            "cfg": cfg,
            "scale": scale,
            "setwallpaper": setwallpaper,
            "refine": refine,
            "workflow": workflow,
            "seed": seed,
            "timeout": timeout,
            "negativeprompt": negativeprompt,
            "upscaler": upscaler,
            "lora": lora,
            "interpolate_frames": interpolate_frames,
            "lora_strength": lora_strength,
            "suppress": suppress,
            "video_length" : video_length,
            "metaprompt": metaprompt
        }
        provided_args["images"] = images        # include images if any

        # Merge defaults with provided arguments (use provided if not None, else default)
        final_args = {**defaults}
        final_args.update({k: v for k, v in provided_args.items() if v is not None})
        info(f"Kwags: {kwargs}")
        final_args.update(kwargs)
        
        #final_args = {k: v if v is not None else defaults.get(k, None) for k, v in provided_args.items()}
        args_namespace = argparse.Namespace(**final_args)
        
        #info(f"Args_namespace: {args_namespace}")

    # Validation steps
    if not args_namespace.prompt and not args_namespace.images: 
        raise ValueError("A prompt or image must be provided.")
    
    # Initialize publish_destinations variable to avoid scope issues
    publish_destinations = []
    
    # Get destinations data and see if we have a size constraint
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
        
        # Let the screen know we're generating
        routes.display.send_overlay(
            html = "overlay_generating.html.j2",
            screens = [publish_destination] if isinstance(publish_destination, str) else publish_destination, 
            substitutions = {'MESSAGE': 'Initialising...'},
            duration = 30000,
            position = "top-left"
        )
    
    #info("Image parameters: " + ", ".join(f"{k}={v}" for k, v in vars(args_namespace).items()))
    
    info(f"Trying to load: {args_namespace.workflow}")
    with open(findfile(args_namespace.workflow), "r") as file:
        workflow_data = json.load(file)
    info(f"Successfully loaded: {args_namespace.workflow}")
    
    unique_video_name = f"output_video{str(uuid.uuid4())[:8]}"    
    json_replacements = {
        r"{{LATENT-IMAGE}}": {  
            "width": args_namespace.width,
            "height": args_namespace.height,
            "batch_size": args_namespace.batch
        },
        r"{{POSITIVE-PROMPT}}": {  
            "text":  args_namespace.prompt
        },
        r"{{NEGATIVE-PROMPT}}": {  
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
            "width": locals().get("maxwidth", 6400),
            "height": locals().get("maxheight", 6400)
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
    with open(findfile("workflows.json")) as f:
        workflow_config_list = json.load(f)
    
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

    info(f"Workflow: {args_namespace.workflow}")
    info(f"RunPod ID: {runpod_id}")
    runpod.api_key = os.getenv("RUNPOD_API_KEY")
    endpoint = runpod.Endpoint(runpod_id)

    #debug(f"Workflow object: {json.dumps(truncate_element(input_payload, 5000), indent=2)}")
    #return 

    try:
        # Let's go...
        run_request = endpoint.run(input_payload)
        job_id = run_request.job_id
        add_job(job_id, endpoint.endpoint_id)  # track all jobs centrally

        status = run_request.status()
        info(f"Initial status: '{status}'")

        timeout = 100000  # TEMPORARY
        poll_interval = getattr(args_namespace, "poll_interval", 0.5)
        info(f"> Polling RunPod every {poll_interval}s for up to {timeout}s...")

        start_time = time()

        try:
            workflow_entry = next(item for item in workflow_config_list if item["id"] == args_namespace.workflow)
            raw_stages = workflow_entry.get("processing_stages", [{"name": "Rendering", "weight": 100}])
            if isinstance(raw_stages[0], str):
                stage_weight = 100 // len(raw_stages)
                processing_stages = [{"name": name, "weight": stage_weight} for name in raw_stages]
            else:
                processing_stages = raw_stages
        except Exception:
            processing_stages = [{"name": "Rendering", "weight": 100}]

        stage_weights = [s["weight"] for s in processing_stages]
        stage_names = [s["name"] for s in processing_stages]
        total_weight = sum(stage_weights)
        cumulative_weights = [sum(stage_weights[:i]) for i in range(len(stage_weights))]

        last_status = None
        last_update_message = ""
        last_update_percentage = ""
        last_max_val = None
        last_value = None
        stage_index = 1

        last_kind = None
        last_details_serialized = None

        while True:
            status = run_request.status()
            progress = job_progress_listeners_latest.get(job_id)

            value = None
            max_val = None
            new_message = last_update_message
            new_percentage = last_update_percentage
            new_stage_index = stage_index
            should_update_overlay = False

            if status != last_status:
                last_status = status
                should_update_overlay = True

            if status == "IN_QUEUE":
                new_message = "Queued..."
                new_percentage = ""
                should_update_overlay = (new_message != last_update_message)

            elif status == "IN_PROGRESS":
                if progress:
                    kind = progress.get("comfy", {}).get("type")
                    details = progress.get("comfy", {}).get("data", {})
                    current_details_serialized = json.dumps(details, sort_keys=True)

                    if kind != last_kind or current_details_serialized != last_details_serialized:
                        last_kind = kind
                        last_details_serialized = current_details_serialized

                    if kind == "progress":
                        max_val = int(details.get("max", 1))
                        value = int(details.get("value", 0))

                        if value != last_value or max_val != last_max_val:
                            if last_value is not None and (value < last_value or max_val != last_max_val):
                                new_stage_index = min(stage_index + 1, len(processing_stages))

                            # ✨ NEW: weighted global progress calculation
                            completed_weight = cumulative_weights[new_stage_index - 1]
                            stage_weight = stage_weights[new_stage_index - 1]
                            fraction_within_stage = value / max_val if max_val else 0
                            weighted_progress = completed_weight + (stage_weight * fraction_within_stage)
                            new_percentage = int(100 * weighted_progress / total_weight)

                            # ✨ NEW: bold current stage in status message
                            stage_status_parts = []
                            for i, name in enumerate(stage_names):
                                if i == new_stage_index - 1:
                                    stage_status_parts.append(f"<span class='pulsing-stage'>{name}</span>")
                                else:
                                    stage_status_parts.append(name)
                            stage_status_string = " › ".join(stage_status_parts)

                            new_message = f"{stage_status_string}"
                            should_update_overlay = True

                    elif kind == "status":
                        queue_remaining = details.get("status", {}).get("exec_info", {}).get("queue_remaining")
                        new_message = "Finalising..." if queue_remaining == 0 else "Queued..."
                        new_percentage = ""
                        should_update_overlay = True

                else:
                    new_message = "Working..."
                    new_percentage = ""
                    should_update_overlay = True

            elif status in {"FAILED", "CANCELLED"}:
                error_text = "Generation cancelled." if status == "CANCELLED" else "❌ Generation failed."
                routes.display.send_overlay(
                    html="overlay_generating.html.j2",
                    screens=["index"],
                    substitutions={'MESSAGE': error_text, 'BACKGROUND':'none', 'TYPE':'alert'},
                    duration=5000,
                    job_id=job_id
                )
                if publish_destination:
                    routes.display.send_overlay(
                        html="overlay_generating.html.j2",
                        screens=[publish_destination] if isinstance(publish_destination, str) else publish_destination,
                        substitutions={'ALERT_TEXT': error_text, 'TYPE': 'alert'},
                        duration=5000,
                        position="top-left",
                        clear=True,
                        job_id=job_id
                    )
                raise RuntimeError(error_text)

            elif status == "COMPLETED":
                output = run_request.output()
                break

            if should_update_overlay and (
                new_message != last_update_message or new_percentage != last_update_percentage
            ):
                display_index_message = (
                    f"{publish_destination}: "
                    f"{f'{new_percentage}% - ' if new_percentage != "" else ''}"
                    f"{new_message}"
                )
                routes.display.send_overlay(
                    html="overlay_generating.html.j2",
                    screens=["index"],
                    duration=120000,
                    substitutions={
                        'MESSAGE': display_index_message,
                        'PROGRESS_PERCENT': new_percentage,
                        'BACKGROUND':'none'
                    },
                    job_id=job_id
                )
                if publish_destination:
                    routes.display.send_overlay(
                        html="overlay_generating.html.j2",
                        screens=[publish_destination] if isinstance(publish_destination, str) else publish_destination,
                        duration=120000,
                        substitutions={
                            'MESSAGE': new_message,
                            'PROGRESS_PERCENT': new_percentage
                        },
                        position="top-left",
                        fadein=0,
                        clear=True,
                        job_id=job_id
                    )
                last_update_message = new_message
                last_update_percentage = new_percentage
                stage_index = new_stage_index
                last_max_val = max_val
                last_value = value

            if time() - start_time > timeout:
                raise TimeoutError(f"⏰ RunPod job timed out after {timeout} seconds.")

            sleep(poll_interval)

        
        #run_request = endpoint.run_sync(
        #    input_payload,
        #    timeout=args_namespace.timeout,  # Timeout in seconds.
        #)
                    
        # Success 
        if output["status"]=="success":
            info(f"> Successfully generated image")
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
            generation_cost = calculate_generation_cost(
                generation_time_seconds, 
                workflow_id=args_namespace.workflow,
                runpod_id=runpod_id
            )
            
            # Add time and cost to output metadata
            output["generation_time_seconds"] = generation_time_seconds
            output["generation_cost_gbp"] = generation_cost
            
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
        
            if publish_destination:
                # Clear any "Finalising..." overlay before publishing
                routes.display.send_overlay(
                    html="overlay_generating.html.j2",
                    screens=[publish_destination] if isinstance(publish_destination, str) else publish_destination,
                    duration=2000,
                    substitutions={'MESSAGE': 'Published'},
                    clear=True,
                    job_id=job_id
                )
                
                # Actually publish into your screen's bucket
                pub_res = publish_to_destination(
                    source = output["message"],
                    publish_destination_id = publish_destination,
                    metadata = vars(args_namespace),
                    batch_id = batch_id,
                    silent = silent,
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
           


            display_final_file = f'<a href="{output["message"]}" target="_blank">Done</a>'
            routes.display.send_overlay(
                html = "overlay_generating.html.j2",
                screens = ["index"],
                substitutions = {
                    'MESSAGE':  display_final_file,
                    'BACKGROUND':'none'                    
                },
                duration = 600000,
                job_id = job_id
            )
            
            return output
        
        else:
            # Let the screen know we're generating
            if publish_destinations:
                routes.display.send_overlay(
                    html = "overlay_generating.html.j2",
                    screens = [publish_destination] if isinstance(publish_destination, str) else publish_destination,
                    substitutions = {'ALERT_TEXT': '❌ Generation failed.', 'TYPE': 'alert'},
                    duration = 5000,
                    position = "top-left",
                    clear = True,
                    job_id = job_id
                )
            routes.display.send_overlay(
                html = "overlay_generating.html.j2",
                screens = ["index"],
                substitutions = {'ALERT_TEXT': '❌ Generation failed.', 'TYPE': 'alert'},
                duration = 30000,
                job_id = job_id
            )
            raise ValueError(output)
    
    except Exception as e:
        raise ValueError(str(e))


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
