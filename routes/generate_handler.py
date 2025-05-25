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
       
    data = input_obj.get("data", {})
    prompt = data.get("prompt", None)
    refiner = data.get("refiner", "none")  
    workflow = data.get("workflow", None)
    images = data.get("images", [])
    batch_id = data.get("batch_id") or str(uuid.uuid4())  # Generate a batch_id if not provided

    # NOTE: **Do NOT** apply a default workflow here.  We want the refiner to be
    # free to suggest an appropriate workflow first.  A default will be added
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
    
    corrected_refiner = resolve_runtime_value("refiner", refiner, return_key="system_prompt")
    
    # TODO: should be done with a proper lookup NOT runtimevalue -- which should ONLY be used to resolve informal strings
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
            prepared_images = [images[0], images[0]]  # reuse first image
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
        utils.logger.debug(json.dumps(openai_args, indent=2)[:1000])  # print first 1000 chars to avoid base64 overflow

        
        refined_output = routes.openai.openai_prompt(**openai_args)
        refined_prompt = refined_output.get("full_prompt", prompt)
    else:
        refined_output = {}
        refined_prompt = prompt
        
    corrected_workflow = resolve_runtime_value(
        category="workflow", 
        input_value=refined_output.get("workflow", workflow),
        return_key="id", 
        match_key="id"
    )
    
    # FINAL FALLBACK: If everything above still left us without a workflow,
    # select the default *now* ("last moment") so that the downstream
    # generation call always has a valid workflow to load.
    if not corrected_workflow:
        try:
            workflows = _load_json_once("workflow", "workflows.json")
            default_workflow = next((w for w in workflows if w.get("default", False)), None)
            if default_workflow:
                corrected_workflow = default_workflow["id"]
                utils.logger.debug(
                    f"No workflow specified/chosen by refiner – using default workflow: {corrected_workflow}"
                )
            else:
                utils.logger.error("No default workflow found in workflows.json – generation may fail.")
        except Exception as e:
            utils.logger.error(f"Failed to load default workflow list: {e}")

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

    # One thread per image
    threads=[]
    results = [None] * len(publish_targets)  # Pre-size results list
    safe_kwargs = kwargs.copy()
    safe_kwargs.pop("publish_destination", None)  # remove if exists, else no-op
    safe_kwargs["images"] = images
    # Remove batch_id from safe_kwargs since it's already in the data dict
    safe_kwargs.pop("batch_id", None)
    
    for idx, publish_destination in enumerate(publish_targets):
        if no_targets:
            corrected_publish_destination = None
        else:
            utils.logger.info(f"> For destination: {publish_destination}")
            corrected_publish_destination = resolve_runtime_value("destination", publish_destination)
            
        def thread_fn(index=idx, destination=corrected_publish_destination):
            result = routes.generate.start(
                prompt=final_prompt,
                workflow=corrected_workflow,
                publish_destination=destination,
                **safe_kwargs
            )
            results[index] = result  # Store result in shared list

        thread = threading.Thread(target=thread_fn)
        thread.start()
        threads.append(thread)

    utils.logger.info(f" * Spawned {len(threads)} generator threads.")

    # if we need to wait until the end, do so
    if wait:
        for t in threads:
            t.join()
        utils.logger.info(" * All generator threads completed.")
        return results
    else:
        return None 

def async_amimate(targets, obj = {}):
    """
    Process animation for the specified targets.
    
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
    
    # TODO: make this smarter; handle multiple targets, accept input prompts, refiners, etc.

    result = obj
    result.setdefault("data", {}).setdefault(
        "targets",
        targets if isinstance(targets, list) else []
    )

    # Get target ID directly (no need for resolve_runtime_value to fuzzy match)
    target_image_file = targets[0] if targets and len(targets) > 0 else None

    # Create a result dictionary
    if target_image_file:
        # Inject base64 image into result["data"]["images"]
        image_payload = get_image_from_target(target_image_file)
        
        if image_payload:
            result.setdefault("data", {})["images"] = [image_payload]
            
            info(
                f"Will address: {target_image_file}, "
                f"image present: True, "
                f"image length: {len(image_payload.get('image'))}"
            )
        else:
            warning(f"No image found at ./output/{target_image_file}.jpg or ./output/{target_image_file}.mp4")
    else:
        warning("No target specified for animation")
    
    # Ensure we're using the currently selected refiner
    result.setdefault("data", {})["refiner"] = resolve_runtime_value("refiner", "animate")
    
    # Run the refinement + generation flow in background
    threading.Thread(
        target=handle_image_generation,
        kwargs={
            "input_obj": result
        }
    ).start()
    
    return None 