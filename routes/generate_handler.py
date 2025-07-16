"""
Generate handler - Utilities for generating content from other content
"""
import os
from pathlib import Path
import tempfile
import cv2
import logging
import json
import requests
import time
from PIL import Image
from io import BytesIO

from flask import send_file
from utils.logger import info, error, warning, debug
from routes.bucketer import _append_to_bucket
from routes.generate_utils import throw_user_interacting_event, process_generate_image_request, save_to_recent

# Cache for GPU pricing to avoid repeated API calls
_gpu_price_cache = {}
_gpu_cache_expiry = 3600  # Cache for 1 hour (in seconds)

def calculate_generation_cost(seconds_taken, workflow_id=None, runpod_id=None, default_cost_per_second=0.000584):
    """
    Calculate the cost of image generation based on time taken and the GPU used by RunPod.
    
    Args:
        seconds_taken: Number of seconds the generation took
        workflow_id: Optional workflow ID to look up specific cost rate
        runpod_id: RunPod endpoint ID to get actual GPU costs from API
        default_cost_per_second: Default cost per second if API calls fail
        
    Returns:
        Cost in GBP
    """
    try:
        # Static exchange rate: 1 GBP = 1.3 USD
        FX_RATE_USD_TO_GBP = 1 / 1.3
        
        # Try to find the workflow's specific cost_per_second setting first
        cost_per_second_usd = default_cost_per_second
        used_real_time_pricing = False
        
        # Check cache first if runpod_id is provided
        cache_key = f"endpoint_{runpod_id}" if runpod_id else None
        current_time = time.time()
        
        if cache_key and cache_key in _gpu_price_cache:
            cache_entry = _gpu_price_cache[cache_key]
            # Check if cache is still valid
            if current_time - cache_entry['timestamp'] < _gpu_cache_expiry:
                info(f"Using cached GPU pricing for {cache_entry['gpu_name']}: ${cache_entry['cost_per_second']:.6f}/second")
                cost_per_second_usd = cache_entry['cost_per_second']
                used_real_time_pricing = True
            else:
                info(f"GPU price cache expired for {runpod_id}, refreshing...")
        
        # If not in cache or cache expired, and runpod_id is provided, try to get from API
        if not used_real_time_pricing and runpod_id:
            try:
                # Check if runpod_id is valid
                if not runpod_id or runpod_id == "None" or runpod_id.lower() == "null":
                    warning(f"Invalid RunPod ID: '{runpod_id}', falling back to default cost")
                else:
                    # Get RunPod API key from environment
                    api_key = os.getenv("RUNPOD_API_KEY")
                    if not api_key:
                        warning("RUNPOD_API_KEY not found in environment, falling back to default cost")
                    else:
                        # Correct GraphQL endpoint
                        headers = {"Authorization": f"Bearer {api_key}"}
                        gql_endpoint = "https://api.runpod.io/graphql"
                        
                        info(f"Fetching GPU info for RunPod endpoint ID: {runpod_id}")
                        
                        try:
                            # 1. Get GPU type for this endpoint using GraphQL
                            endpoint_query = """
                            query($id:String!){
                              myself { endpoints(filter:{id:$id}){ gpuIds name } }
                            }"""
                            
                            info(f"Calling RunPod GraphQL for endpoint info: {gql_endpoint}")
                            endpoint_response = requests.post(
                                gql_endpoint,
                                json={"query": endpoint_query, "variables": {"id": runpod_id}},
                                headers=headers,
                                timeout=5
                            )
                            
                            info(f"Endpoint query response status: {endpoint_response.status_code}")
                            
                            if endpoint_response.status_code != 200:
                                warning(f"Failed to get endpoint data: HTTP {endpoint_response.status_code}")
                                warning(f"Response content: {endpoint_response.text[:200]}")
                            else:
                                try:
                                    endpoint_data = endpoint_response.json()
                                    # Log a shortened version to avoid flooding logs
                                    response_excerpt = json.dumps(endpoint_data)[:200] + "..." if len(json.dumps(endpoint_data)) > 200 else json.dumps(endpoint_data)
                                    info(f"Endpoint query response: {response_excerpt}")
                                    
                                    if not endpoint_data.get("data") or not endpoint_data["data"].get("myself") or \
                                       not endpoint_data["data"]["myself"].get("endpoints") or \
                                       len(endpoint_data["data"]["myself"]["endpoints"]) == 0:
                                        warning(f"No endpoint data found in response: {response_excerpt}")
                                        if "errors" in endpoint_data:
                                            warning(f"GraphQL errors: {json.dumps(endpoint_data['errors'])}")
                                    else:
                                        # Extract GPU ID and endpoint name
                                        endpoint_info = endpoint_data["data"]["myself"]["endpoints"][0]
                                        gpu_id = endpoint_info.get("gpuIds")
                                        endpoint_name = endpoint_info.get("name", f"Endpoint {runpod_id}")
                                        
                                        if not gpu_id:
                                            warning(f"No GPU ID found for endpoint {runpod_id}")
                                        else:
                                            info(f"Found GPU ID: {gpu_id} for endpoint: {endpoint_name}")
                                            
                                            # 2. Get price for this GPU type using GraphQL
                                            price_query = """
                                            query($id:String!){
                                              gpuTypes(input:{id:$id}){ securePrice communityPrice displayName }
                                            }"""
                                            
                                            info(f"Calling RunPod GraphQL for GPU pricing: {gql_endpoint}")
                                            price_response = requests.post(
                                                gql_endpoint,
                                                json={"query": price_query, "variables": {"id": gpu_id}},
                                                headers=headers,
                                                timeout=5
                                            )
                                            
                                            info(f"Price query response status: {price_response.status_code}")
                                            
                                            if price_response.status_code != 200:
                                                warning(f"Failed to get GPU price data: HTTP {price_response.status_code}")
                                                warning(f"Response content: {price_response.text[:200]}")
                                            else:
                                                try:
                                                    price_data = price_response.json()
                                                    # Log a shortened version to avoid flooding logs
                                                    response_excerpt = json.dumps(price_data)[:200] + "..." if len(json.dumps(price_data)) > 200 else json.dumps(price_data)
                                                    info(f"Price query response: {response_excerpt}")
                                                    
                                                    if not price_data.get("data") or not price_data["data"].get("gpuTypes") or \
                                                       len(price_data["data"]["gpuTypes"]) == 0:
                                                        warning(f"No GPU type data found in response: {response_excerpt}")
                                                        if "errors" in price_data:
                                                            warning(f"GraphQL errors: {json.dumps(price_data['errors'])}")
                                                    else:
                                                        # Extract price and GPU display name
                                                        gpu_data = price_data["data"]["gpuTypes"][0]
                                                        price_per_hour = gpu_data.get("securePrice") or gpu_data.get("communityPrice", 0)
                                                        gpu_display_name = gpu_data.get("displayName", gpu_id)
                                                        
                                                        if price_per_hour == 0:
                                                            warning(f"Price per hour is zero for GPU {gpu_id}")
                                                        
                                                        # Convert to price per second (USD)
                                                        cost_per_second_usd = price_per_hour / 3600
                                                        
                                                        # Save to cache
                                                        if cache_key:
                                                            _gpu_price_cache[cache_key] = {
                                                                'cost_per_second': cost_per_second_usd,
                                                                'gpu_name': gpu_display_name,
                                                                'timestamp': current_time
                                                            }
                                                        
                                                        info(f"Using real-time pricing for {gpu_display_name}: ${cost_per_second_usd:.6f}/second")
                                                        used_real_time_pricing = True
                                                except json.JSONDecodeError as jde:
                                                    warning(f"Failed to parse price query response: {jde}")
                                                    warning(f"Raw response: {price_response.text[:200]}")
                                except json.JSONDecodeError as jde:
                                    warning(f"Failed to parse endpoint query response: {jde}")
                                    warning(f"Raw response: {endpoint_response.text[:200]}")
                        except requests.exceptions.RequestException as re:
                            warning(f"RunPod GraphQL request failed: {type(re).__name__}: {str(re)}")
            except Exception as e:
                warning(f"Error getting GPU price from RunPod API: {type(e).__name__}: {str(e)}")
                import traceback
                warning(f"Traceback: {traceback.format_exc()}")
        
        # Check workflow config as fallback if we didn't get real-time pricing
        if not used_real_time_pricing and workflow_id:
            # Import locally to avoid circular imports
            from routes.utils import findfile, _load_json_once
            
            # Load the workflows config
            workflow_config = _load_json_once("workflow", "workflows.json")
            
            # Find the matching workflow
            for workflow in workflow_config:
                if workflow.get("id") == workflow_id:
                    # Use the workflow's cost_per_second if specified
                    workflow_cost = workflow.get("cost_per_second")
                    if workflow_cost:
                        cost_per_second_usd = workflow_cost
                        info(f"Using workflow-specific cost rate: ${cost_per_second_usd:.6f}/second")
                        break
        
        # Convert USD cost to GBP
        cost_per_second_gbp = cost_per_second_usd * FX_RATE_USD_TO_GBP
        
        # Calculate and return the final cost in GBP
        return round(seconds_taken * cost_per_second_gbp, 6)
    except Exception as e:
        error(f"Error calculating generation cost: {e}")
        # Return default calculation if there's an error
        return round(seconds_taken * default_cost_per_second * (1/1.3), 6)

def jpg_from_mp4_handler(mp4_path):
    """
    Extract the first frame from an MP4 file and return it as a JPG.
    
    Args:
        mp4_path: Path to the MP4 file
        
    Returns:
        Flask response with the JPG image
    """
    temp_path = None
    try:
        debug(f"Extracting first frame from: {mp4_path}")
        
        # Convert string path to Path object if needed
        if isinstance(mp4_path, str):
            mp4_path = Path(mp4_path)
            
        # Check if the file exists
        if not mp4_path.exists():
            error(f"MP4 file not found: {mp4_path}")
            return "File not found", 404, {"Content-Type": "text/plain"}
            
        # Open the video file
        video = cv2.VideoCapture(str(mp4_path))
        
        # Check if video opened successfully
        if not video.isOpened():
            error(f"Failed to open MP4 file: {mp4_path}")
            return "Failed to open video file", 500, {"Content-Type": "text/plain"}
        
        # Read the first frame
        success, frame = video.read()
        if not success:
            error(f"Failed to read frame from MP4: {mp4_path}")
            return "Failed to read frame from video", 500, {"Content-Type": "text/plain"}
        
        # Release the video file
        video.release()
        
        # No need to convert BGR to RGB since cv2.imwrite expects BGR
        # We'll just use the frame as is
        
        # Create a temporary file for the JPG
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
            temp_path = temp_file.name
            
        # Save the frame as a JPG
        cv2.imwrite(temp_path, frame)
        
        # Return the JPG file
        debug(f"Successfully extracted frame to: {temp_path}")
        return send_file(
            temp_path, 
            mimetype='image/jpeg', 
            as_attachment=False,
            # Add custom cleanup callback to delete the temp file 
            # after response is sent
            download_name=f"frame_{os.path.basename(mp4_path)}.jpg"
        )
        
    except Exception as e:
        # Clean up the temp file if there was an error
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass  # Ignore errors in cleanup
        error(f"Error extracting frame from MP4: {e}")
        return f"Error extracting frame: {e}", 500, {"Content-Type": "text/plain"}
    
    # The temp file cleanup is handled by the endpoint function 

def handle_image_generation(input_obj, wait=False, **kwargs):
    """
    Core image generation logic that performs actual generation.
    
    This is a lower-level function that:
    1. Handles prompt refinement via OpenAI
    2. Manages image generation workflows
    3. Creates and manages generation threads
    
    Called by both API handlers and internal components.
    
    Args:
        input_obj: Dictionary containing data for generation
        wait: Whether to wait for generation threads to complete
        **kwargs: Additional keyword arguments to pass to routes.generate.start
        
    Returns:
        List of results if wait=True, otherwise None
    """
    # Import necessary modules
    import threading
    import uuid
    import json
    from routes.utils import build_schema_subs, resolve_runtime_value, dict_substitute, _load_json_once
    import routes.openai
    import routes.generate
    import utils.logger
    from utils.logger import info, error, warning, debug
    
    subs = build_schema_subs()
       
    # ------------------------------------------------------------------
    # Batch handling (how many separate images to generate?)
    # ------------------------------------------------------------------
    # Batch can be supplied either through the incoming *data* payload or
    # via keyword arguments (e.g. params from the API).  We take whichever
    # is provided **first** – kwargs overrides the data payload – and fall
    # back to 1 if nothing specified.
    #
    # We pop it out of *kwargs* so that it is NOT forwarded to the lower
    # layers (routes.generate.start).  This ensures we create <batch>
    # separate jobs here rather than relying on the workflow to batch.
    # ------------------------------------------------------------------
    batch_size = 1  # default

    # Pull from kwargs if present – this takes priority
    if "batch" in kwargs and kwargs["batch"] is not None:
        try:
            batch_size = int(kwargs.pop("batch"))
        except (TypeError, ValueError):
            batch_size = 1

    # Otherwise fall back to data["batch"] if supplied
    elif isinstance(input_obj.get("data", {}).get("batch"), (int, str)):
        try:
            batch_size = int(input_obj.get("data", {}).get("batch", 1))
        except (TypeError, ValueError):
            batch_size = 1

    # Sanity-check
    if batch_size < 1:
        batch_size = 1

    utils.logger.info(f"[handle_image_generation] Requested batch size = {batch_size}")

    data = input_obj.get("data", {})
    prompt = data.get("prompt", None)
    user_refiner = data.get("refiner")  # User-specified refiner (may be None)
    user_workflow = data.get("workflow")  # User-specified workflow (may be None)
    images = data.get("images", [])
    batch_id = data.get("batch_id") or str(uuid.uuid4())  # Generate a batch_id if not provided

    # ------------------------------------------------------------------
    # Implement proper default resolution hierarchy:
    # 1. User preferences always take priority
    # 2. If user specifies refiner but no workflow, use refiner's default-workflow
    # 3. If user specifies workflow but no refiner, use workflow's default-refiner
    # 4. If user specifies neither, use system defaults at the last moment
    # ------------------------------------------------------------------
    
    refiner = user_refiner
    workflow = user_workflow
    
    # If user specified a refiner but no workflow, try to get the refiner's default workflow
    if user_refiner and not user_workflow:
        try:
            refiners_data = _load_json_once("refiner", "refiners.json")
            refiner_config = next((r for r in refiners_data if r.get("id") == user_refiner), None)
            if refiner_config and refiner_config.get("default-workflow"):
                workflow = refiner_config["default-workflow"]
                utils.logger.info(f"Using refiner's default workflow: {workflow}")
        except Exception as e:
            utils.logger.warning(f"Failed to load refiner default workflow: {e}")
    
    # If user specified a workflow but no refiner, try to get the workflow's default refiner
    if user_workflow and not user_refiner:
        try:
            workflows_data = _load_json_once("workflow", "workflows.json")
            workflow_config = next((w for w in workflows_data if w.get("id") == user_workflow), None)
            if workflow_config and workflow_config.get("default-refiner"):
                refiner = workflow_config["default-refiner"]
                utils.logger.info(f"Using workflow's default refiner: {refiner}")
        except Exception as e:
            utils.logger.warning(f"Failed to load workflow default refiner: {e}")

    # NOTE: We still don't apply system defaults here. We want the refiner to be
    # free to suggest an appropriate workflow first. System defaults will be added
    # only after the refiner stage if none has been chosen.

    # Get "targets" from input data
    targets = data.get("targets", [])

    # If it's a string (possibly comma-separated), split and clean it
    if isinstance(targets, str):
        targets = [t.strip() for t in targets.split(",") if t.strip()]

    # Ensure it's a list at this point
    targets = targets if isinstance(targets, list) else [targets]

    # Final fallback if empty
    publish_targets = targets if targets else [None]

    if not prompt and not images:
        return None
    
    # Build the dictionary that will be passed into the refiner.  Only include
    # the workflow key if the caller explicitly provided one – otherwise leave
    # it out so that the refiner can decide which workflow to apply.
    input_dict = {"prompt": prompt}
    if workflow:
        input_dict["workflow"] = workflow

    utils.logger.debug(f"****input_dict {input_dict}")
    utils.logger.info(f"Resolved refiner: {refiner}, workflow: {workflow}")
    
    # AUTO-REFINER RESOLUTION: If no refiner specified, apply intelligent auto-selection
    if not refiner:
        try:
            refiners_data = _load_json_once("refiner", "refiners.json")
            
            # Check if we have images to determine auto-selection strategy
            has_images = images and len(images) > 0
            
            if has_images:
                # Images provided: look for image-capable refiners (with uploadimages > 0)
                image_refiners = [r for r in refiners_data if r.get("uploadimages", 0) > 0 and r.get("alexavisible", False)]
                if image_refiners:
                    # Prefer 'adapt' refiner for image editing if available
                    adapt_refiner = next((r for r in image_refiners if r.get("id") == "adapt"), None)
                    if adapt_refiner:
                        refiner = adapt_refiner["id"]
                        utils.logger.info(f"Auto-selected 'adapt' refiner for image editing: {refiner}")
                    else:
                        # Otherwise use first available image refiner
                        refiner = image_refiners[0]["id"]
                        utils.logger.info(f"Auto-selected image-capable refiner: {refiner}")
                else:
                    # No image refiners found, use default refiner
                    default_refiner = next((r for r in refiners_data if r.get("default", False)), None)
                    if default_refiner:
                        refiner = default_refiner["id"]
                        utils.logger.info(f"Auto-selected default refiner (no image refiners available): {refiner}")
            else:
                # No images: use default text refiner
                default_refiner = next((r for r in refiners_data if r.get("default", False)), None)
                if default_refiner:
                    refiner = default_refiner["id"]
                    utils.logger.info(f"Auto-selected default refiner (no images): {refiner}")
                    
        except Exception as e:
            utils.logger.warning(f"Failed to auto-select refiner: {e}")

    # Resolve the refiner to its system prompt (if any)
    corrected_refiner = None
    if refiner:
        corrected_refiner = resolve_runtime_value("refiner", refiner, return_key="system_prompt")
    
    # Get images required by the refiner
    images_required = 0
    if refiner:
        images_required = resolve_runtime_value("refiner", refiner, return_key="uploadimages") or 0
    
    # Normalize input images list
    images = images or []
    prepared_images = []

    if images_required == 1:
        if len(images) >= 1:
            prepared_images = [images[0]]
    elif images_required == 2:
        if len(images) >= 2:
            prepared_images = [images[0], images[1]]
        elif len(images) == 1:
            # Don't duplicate the same image - treat as single image case
            prepared_images = [images[0]]
            # Override images_required to reflect actual usage
            images_required = 1
    else:
        prepared_images = []  # none required
       
    utils.logger.info(f"> Using refiner: '{refiner}' -> '{corrected_refiner}'")
    
    # Refine if needed
    if corrected_refiner is not None:
        openai_args = {
            "user_prompt": json.dumps(input_dict),
            "system_prompt": dict_substitute(corrected_refiner, subs),
            "schema": json.loads(dict_substitute("refiner-enrich.schema.json.j2", subs))
        }
        
        if images_required:
            openai_args["images"] = prepared_images
            utils.logger.debug(f"[handle_image_generation] {len(prepared_images)} image(s) prepared for OpenAI prompt.")

        utils.logger.debug(f"[handle_image_generation] Calling openai_prompt with args:")
        
        # Create a sanitized version of openai_args for logging (remove base64 image data)
        log_args = openai_args.copy()
        if "images" in log_args:
            log_args["images"] = [
                {
                    "name": img.get("name", "unknown"),
                    "image": f"<base64_data_{len(img.get('image', ''))}chars>" if isinstance(img, dict) and "image" in img else "<image_data>"
                }
                for img in log_args["images"]
            ]
        
        utils.logger.debug(json.dumps(log_args, indent=2)[:1000])  # Now safe to log without base64 overflow

        
        refined_output = routes.openai.openai_prompt(**openai_args)
        refined_prompt = refined_output.get("full_prompt", prompt)
    else:
        refined_output = {}
        refined_prompt = prompt
        
    # Determine final workflow
    if corrected_refiner is not None:
        # Refiner was used - check if it suggested a workflow
        corrected_workflow = resolve_runtime_value(
            category="workflow", 
            input_value=refined_output.get("workflow", workflow),
            return_key="id", 
            match_key="id"
        )
    else:
        # No refiner used - use the workflow we determined earlier
        corrected_workflow = resolve_runtime_value(
            category="workflow", 
            input_value=workflow,
            return_key="id", 
            match_key="id"
        )
    
    # SOPHISTICATED AUTO-RESOLUTION: If everything above still left us without a workflow,
    # apply intelligent auto-selection based on whether images are provided
    if not corrected_workflow:
        try:
            workflows = _load_json_once("workflow", "workflows.json")
            
            # Check if we have images to determine auto-selection strategy
            has_images = images and len(images) > 0
            
            if has_images:
                # Images provided: select first workflow that supports image input
                image_workflow = next((w for w in workflows if w.get("input") and "image" in w.get("input", [])), None)
                if image_workflow:
                    corrected_workflow = image_workflow["id"]
                    utils.logger.info(
                        f"Auto-selected image-capable workflow (images provided): {corrected_workflow}"
                    )
                else:
                    # Fallback to default if no image workflows found
                    default_workflow = next((w for w in workflows if w.get("default", False)), None)
                    if default_workflow:
                        corrected_workflow = default_workflow["id"]
                        utils.logger.warning(
                            f"No image-capable workflows found – falling back to default: {corrected_workflow}"
                        )
            else:
                # No images: use default text-to-image workflow
                default_workflow = next((w for w in workflows if w.get("default", False)), None)
                if default_workflow:
                    corrected_workflow = default_workflow["id"]
                    utils.logger.info(
                        f"Auto-selected default workflow (no images): {corrected_workflow}"
                    )
            
            # Final safety check
            if not corrected_workflow:
                utils.logger.error("No suitable workflow found for auto-selection – generation may fail.")
                
        except Exception as e:
            utils.logger.error(f"Failed to load workflows for auto-selection: {e}")

    # Translate prompt into Chinese if required (for WAN)
    translate = data.get("translate") if "data" in locals() else False
    if translate: 
        openai_args = {
            "user_prompt": refined_prompt,
            "system_prompt": dict_substitute("prompt-translate.txt", subs)
        }
        final_prompt = routes.openai.openai_prompt(**openai_args)
        utils.logger.info(f"Translated: {refined_prompt} to {final_prompt}")
    else:
        final_prompt = refined_prompt

    # We don't want to force publication if no target was specified
    no_targets = publish_targets == [None]

    # ------------------------------------------------------------------
    # Extract workflow-specific parameters from data and add to base_kwargs
    # ------------------------------------------------------------------
    # Extract skip-upscaling parameter if present
    skip_upscaling = data.get("skip-upscaling")
    if skip_upscaling is not None:
        utils.logger.info(f"Found skip-upscaling parameter: {skip_upscaling}")

    # ------------------------------------------------------------------
    # Prepare common kwargs that will be sent to each generator invocation
    # ------------------------------------------------------------------
    base_kwargs = kwargs.copy()
    base_kwargs.pop("publish_destination", None)  # Do not allow callers to override this here
    base_kwargs["images"] = images                    # ensure images list is passed downstream
    base_kwargs.pop("batch_id", None)                 # Already handled higher up
    
    # Add workflow-specific parameters
    if skip_upscaling is not None:
        base_kwargs["skip-upscaling"] = skip_upscaling

    # Add maxwidth/maxheight from data if present (for DOWNSCALER template replacement)
    if "maxwidth" in data:
        base_kwargs["maxwidth"] = data["maxwidth"]
    if "maxheight" in data:
        base_kwargs["maxheight"] = data["maxheight"]

    # Add silent parameter from data if present
    if "silent" in data:
        base_kwargs["silent"] = data["silent"]

    # ✨ Remove the seed if batch_size > 1 so that each run gets its own
    #     random seed (generate.start will assign a random default).
    if batch_size > 1:
        base_kwargs.pop("seed", None)

    # One thread per *image* (publish_target × batch)
    total_jobs = len(publish_targets) * batch_size
    threads = []
    results = [None] * total_jobs  # Pre-size so we can keep ordering stable

    job_index = 0  # Global index for results list

    for publish_destination in publish_targets:
        if no_targets:
            corrected_publish_destination = None
        else:
            utils.logger.info(f"> For destination: {publish_destination}")
            corrected_publish_destination = resolve_runtime_value("destination", publish_destination)

        for _ in range(batch_size):
            current_index = job_index  # Capture for closure

            def thread_fn(index=current_index, destination=corrected_publish_destination):
                try:
                    result = routes.generate.start(
                        prompt=final_prompt,
                        workflow=corrected_workflow,
                        publish_destination=destination,
                        **base_kwargs
                    )
                    
                    # Add the resolved refiner information to the result
                    if result and isinstance(result, dict):
                        result["actual_refiner"] = refiner  # Original refiner request
                        result["actual_corrected_refiner"] = corrected_refiner  # Resolved refiner
                        result["actual_refined_prompt"] = refined_prompt  # The refined prompt
                        result["actual_final_prompt"] = final_prompt  # The final prompt used
                    
                    results[index] = result  # Store result in shared list
                except Exception as e:
                    utils.logger.error(f"Generation job failed: {e}")
                    results[index] = None

            thread = threading.Thread(target=thread_fn)
            thread.start()
            threads.append(thread)

            job_index += 1

    utils.logger.info(f" * Spawned {len(threads)} generator threads.")

    # if we need to wait until the end, do so
    if wait:
        for t in threads:
            t.join()
        utils.logger.info(" * All generator threads completed.")
        # Filter out any failed runs (None) just in case
        return [r for r in results if r is not None]
    else:
        return None 

def async_combine(targets, obj = {}):
    """
    Process combination for exactly two targets.
    This function combines images from two targets by:
    1. Taking the first two targets (A and B)
    2. Combining A+B -> publish to A
    3. Combining B+A -> publish to B
    
    Args:
        targets: List of target IDs (should contain exactly 2 targets)
        obj: Optional dictionary with additional configuration
        
    Returns:
        None
    """
    # Import necessary modules
    from utils.logger import info, error, warning, debug
    from routes.utils import get_image_from_target, resolve_runtime_value
    import threading
    
    result = obj
    result.setdefault("data", {}).setdefault(
        "targets",
        targets if isinstance(targets, list) else []
    )

    # Ensure we have exactly 2 targets
    if not targets or len(targets) < 2:
        warning("Combine: requires exactly 2 targets, insufficient targets provided")
        return None
    
    # Take only the first two targets
    target_a = targets[0]
    target_b = targets[1]
    
    info(f"Combine: processing targets {target_a} and {target_b}")
    
    # Get images from both targets
    image_a = get_image_from_target(target_a)
    image_b = get_image_from_target(target_b)
    
    if not image_a:
        warning(f"Combine: no current image found for target A ({target_a})")
    if not image_b:
        warning(f"Combine: no current image found for target B ({target_b})")
        
    # If we don't have both images, we can't combine
    if not image_a or not image_b:
        missing_targets = []
        if not image_a:
            missing_targets.append(target_a)
        if not image_b:
            missing_targets.append(target_b)
        warning(f"Combine: cannot proceed without images from both targets. Missing images from: {', '.join(missing_targets)}")
        return None
    
    # Prepare the combination prompt
    combination_prompt = result.get("data", {}).get("prompt", "")
    
    # Create combination A+B -> publish to A
    info(f"Combine: creating A+B combination for target {target_a}")
    target_a_result = {
        "intent": "combine",
        "data": {
            "prompt": combination_prompt,
            "refiner": "adapt",  # Use adapt refiner for combination
            "targets": [target_a],  # Publish to target A
            "workflow": result.get("data", {}).get("workflow"),
            "images": [image_a, image_b]  # A first, then B
        }
    }
    
    # Create combination B+A -> publish to B  
    info(f"Combine: creating B+A combination for target {target_b}")
    target_b_result = {
        "intent": "combine", 
        "data": {
            "prompt": combination_prompt,
            "refiner": "adapt",  # Use adapt refiner for combination
            "targets": [target_b],  # Publish to target B
            "workflow": result.get("data", {}).get("workflow"),
            "images": [image_b, image_a]  # B first, then A
        }
    }
    
    # Run both combinations in parallel
    threading.Thread(
        target=handle_image_generation,
        kwargs={
            "input_obj": target_a_result
        }
    ).start()
    
    threading.Thread(
        target=handle_image_generation,
        kwargs={
            "input_obj": target_b_result
        }
    ).start()
    
    info(f"Combine: started parallel combination threads for {target_a} and {target_b}")
    return None

def async_amimate(targets, obj = {}):
    """
    Process animation for the specified targets.
    For animation, each target needs its own image retrieved from that specific target.
    
    Args:
        targets: List of target IDs or single target ID
        obj: Optional dictionary with additional configuration
        
    Returns:
        None
    """
    # Import necessary modules
    from utils.logger import info, error, warning, debug
    from routes.utils import get_image_from_target, resolve_runtime_value
    import threading
    
    result = obj
    result.setdefault("data", {}).setdefault(
        "targets",
        targets if isinstance(targets, list) else []
    )

    # Set the animation refiner
    result.setdefault("data", {})["refiner"] = resolve_runtime_value("refiner", "animate")
    
    # Handle multiple targets - each needs its own image
    if targets:
        info(f"Animate: processing {len(targets)} targets: {targets}")
        
        # Process each target separately with its own image retrieval and generation
        for target_destination in targets:
            info(f"Animate: retrieving current image from {target_destination}")
            image_payload = get_image_from_target(target_destination)
            
            # Create a separate request for each target
            target_result = {
                "intent": "animate",
                "data": {
                    "prompt": result.get("data", {}).get("prompt", ""),
                    "refiner": resolve_runtime_value("refiner", "animate"),
                    "targets": [target_destination],  # Single target for this request
                    "workflow": result.get("data", {}).get("workflow")
                }
            }
            
            if image_payload:
                # Include the current published image as input for animation
                target_result["data"]["images"] = [image_payload]
                info(f"Animate: found image for {target_destination}, length: {len(image_payload.get('image', ''))}")
            else:
                warning(f"Animate: no current image found for {target_destination}")
                # Continue anyway - the animator refiner can handle this case
            
            # Run the refinement + generation flow in background for this target
            threading.Thread(
                target=handle_image_generation,
                kwargs={
                    "input_obj": target_result
                }
            ).start()
    else:
        warning("Animate: no targets specified")
    
    return None

def async_adapt(targets, obj = {}):
    """
    Process adaptation for the specified targets.
    For adapt, each target needs its own image retrieved from that specific target.
    
    Args:
        targets: List of target IDs or single target ID
        obj: Optional dictionary with additional configuration
        
    Returns:
        None
    """
    # Import necessary modules
    from utils.logger import info, error, warning, debug
    from routes.utils import get_image_from_target, resolve_runtime_value
    import threading
    
    result = obj
    result.setdefault("data", {}).setdefault(
        "targets",
        targets if isinstance(targets, list) else []
    )

    # Set the adapt refiner
    result.setdefault("data", {})["refiner"] = "adapt"
    
    # Handle multiple targets - each needs its own image
    if targets:
        info(f"Adapt: processing {len(targets)} targets: {targets}")
        
        # Process each target separately with its own image retrieval and generation
        for target_destination in targets:
            info(f"Adapt: retrieving current image from {target_destination}")
            image_payload = get_image_from_target(target_destination)
            
            # Create a separate request for each target
            target_result = {
                "intent": "adapt",
                "data": {
                    "prompt": result.get("data", {}).get("prompt", ""),
                    "refiner": "adapt",
                    "targets": [target_destination],  # Single target for this request
                    "workflow": result.get("data", {}).get("workflow"),
                    "skip-upscaling": True  # Fast adapt - skip upscale initially
                }
            }
            
            if image_payload:
                # Include the current published image as input for adaptation
                target_result["data"]["images"] = [image_payload]
                info(f"Adapt: found image for {target_destination}, length: {len(image_payload.get('image', ''))}")
            else:
                warning(f"Adapt: no current image found for {target_destination}")
                # Continue anyway - the adapt refiner can handle this case
            
            # Run the refinement + generation flow in background for this target
            threading.Thread(
                target=handle_image_generation,
                kwargs={
                    "input_obj": target_result
                }
            ).start()
            
            # Check if an auto-upscale event already exists - only throw a new one if none exists
            from routes.scheduler_utils import get_events_for_destination, throw_event
            existing_events = get_events_for_destination(target_destination)
            
            # Check if there's already an _auto_upscale event in the queue
            has_auto_upscale = any(event["key"] == "_auto_upscale" for event in existing_events["queue"])
            
            if not has_auto_upscale:
                # Throw auto-upscale event with 5s delay to ensure _user_interacting is consumed first
                throw_event(
                    scope=target_destination,
                    key="_auto_upscale",
                    delay="5s",
                    ttl="5m",
                    display_name="Auto-upscale after fast adapt",
                    payload={
                        "trigger_type": "auto_upscale",
                        "original_prompt": result.get("data", {}).get("prompt", ""),
                        "workflow": result.get("data", {}).get("workflow")
                    },
                    single_consumer=True
                )
                info(f"Adapt: Threw auto-upscale event for {target_destination} (delayed by 5s)")
            else:
                info(f"Adapt: Auto-upscale event already exists for {target_destination}, skipping")
    else:
        warning("Adapt: no targets specified")
    
    return None 