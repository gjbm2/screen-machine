"""
Utility functions for image generation and processing
"""
import os
from pathlib import Path
import tempfile
import requests
import time
import uuid
import json
from PIL import Image
from io import BytesIO

from utils.logger import info, error, warning, debug
from routes.bucketer import _append_to_bucket
from routes.scheduler_utils import throw_event
from routes.utils import safe_cast

def throw_user_interacting_event(publish_destination, action_type="generate", wait_time=None):
    """
    Throw a 'user_interacting' event for the specified destination with an appropriate wait time
    based on the action type.

    Args:
        publish_destination (str): The destination ID to throw the event for.
        action_type (str): The type of user action ('generate', 'animate', etc.).
        wait_time (str, optional): Explicit wait time. If provided, it overrides the default for the action type.
    """
    if not publish_destination:
        warning("Cannot throw user_interacting event: no destination specified")
        return

    # Define default wait times for different action types
    wait_times = {
        "info": "90s",
        "generate": "10m",
        "animate": "30m",
        "default": "15m"
    }

    # Apply action-specific wait time if not explicitly given
    if wait_time is None:
        wait_time = wait_times.get(action_type, wait_times["default"])

    payload = {
        "wait": wait_time,
        "action": action_type
    }

    try:
        info(f"Throwing user_interacting event for {publish_destination} with {wait_time} wait")
        result = throw_event(
            scope=publish_destination,
            key="_user_interacting",
            ttl="3h",
            display_name="User Interaction",
            payload=payload,
            single_consumer=False
        )
        info(f"Event thrown: {result}")
    except Exception as e:
        error(f"Error throwing user_interacting event: {str(e)}")


def save_to_recent(img_url, batch_id, metadata=None):
    """
    Downloads the image from img_url, converts it to JPEG, and appends it to the _recent bucket.
    If *metadata* is provided, it is written to the side-car so that generation params are preserved.
    Returns the target path if successful, None otherwise.
    """
    try:
        response = requests.get(img_url)
        if response.status_code != 200:
            error(f"[save_to_recent] Failed to download image from {img_url}: {response.status_code}")
            return None
        info(f"[save_to_recent] Downloaded image from {img_url}, converting to JPEG")
        img = Image.open(BytesIO(response.content)).convert("RGB")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
            img.save(temp_file, format="JPEG", quality=90)
            temp_file.flush()
            temp_path = Path(temp_file.name)
        info(f"[save_to_recent] Calling _append_to_bucket with batch_id={batch_id}")
        target_path = _append_to_bucket("_recent", temp_path, batch_id=batch_id, metadata=metadata)
        temp_path.unlink()
        if target_path:
            info(f"[save_to_recent] Successfully saved to _recent: {target_path}")
            return target_path
        else:
            error(f"[save_to_recent] _append_to_bucket returned None for {img_url}")
            return None
    except Exception as e:
        error(f"[save_to_recent] Failed to save image to _recent: {e}")
        import traceback
        error(f"[save_to_recent] Traceback: {traceback.format_exc()}")
        return None

def process_generate_image_request(data, uploaded_images=None):
    """
    Process a web/API request for image generation.
    
    This is a high-level API handler that:
    1. Processes request data and parameters
    2. Calls the core generation function (handle_image_generation)
    3. Post-processes results and formats the API response
    
    Args:
        data: The JSON data containing prompt, workflow, params, etc.
        uploaded_images: List of encoded images from file uploads
        
    Returns:
        A dictionary containing generated images information and batch ID
    """
    # Generate a unique ID for this batch
    batch_id = data.get('batch_id') or str(uuid.uuid4())
    is_frontend_initiated = 'batch_id' in data  # Check if batch_id was provided in request
    
    prompt = data.get('prompt', '')
    workflow = data.get('workflow', 'text-to-image')
    params = data.get('params', {})
    global_params = data.get('global_params', {})

    # Only use refiner if this is the first image in this batch
    # We'll determine this by checking if there are any files in the _recent bucket
    refiner = "None"
    refiner_params = None
    if not is_frontend_initiated:
        refiner = data.get('refiner', 'none')
        refiner_params = data.get('refiner_params', {})

    batch_size = data.get('batch_size', 1)
    has_reference_image = data.get('has_reference_image', False)
    publish_destination = params.get('publish_destination', None)
    
    info(f"========================================================")
    info(f"Generating images with prompt: {prompt}")
    info(f"DEBUG: Workflow {workflow}, Params {params}")
    
    # Throw user_interacting event when generating from frontend
    if publish_destination:
        throw_user_interacting_event(publish_destination, action_type="generate")
    
    # Process any uploaded images
    images = uploaded_images or []
    
    # Generate
    send_obj = {
        "data": {
            "prompt": prompt,
            "images": images,
            "workflow": workflow,
            "refiner": refiner,
            "targets": publish_destination,
            "batch_id": batch_id
        }
    }
    
    # Extract only parameters that are valid for routes.generate.start
    import inspect
    import routes.generate
    
    # Get valid parameter names from generate()
    main_params = inspect.signature(routes.generate.start).parameters
    
    call_args = {
        k: safe_cast(v, main_params[k].annotation if main_params[k].annotation is not inspect._empty else str)
        for k, v in params.items()
        if k in main_params
    }
    
    # Call handler for image generation
    try:   
        from routes.generate_handler import handle_image_generation
        response = handle_image_generation(
            input_obj=send_obj,
            wait=True,
            **call_args
        )
    except Exception as e:
        error(f"Image generation failed: {e}")
        raise
        
    if not response:
        error("Image generation returned no results.")
        raise ValueError("Image generation returned no results.")
    
    # Generate the requested number of images (based on batch_size)
    result_images = []
    recent_files = []
    
    for i, r in enumerate(response):            
        # Generate a unique ID for this image
        image_id = str(uuid.uuid4())
        
        # Store the image metadata in our dictionary
        used_workflow = r["input"]["workflow"]
        
        image_data = {
            "id": image_id,
            "url": r.get("message", None),
            "seed": r["seed"],
            "original_prompt": prompt,
            "prompt": r["prompt"],
            "negative_prompt": r["negative_prompt"],
            "workflow": workflow,
            "full_workflow": r["input"],
            "timestamp": int(time.time() * 1000),
            "params": params,
            "global_params": global_params,
            "refiner_params": refiner_params,
            "used_reference_image": has_reference_image,
            "batch_id": batch_id,
            "batch_index": i  # Use the response index as batch index
        }
        
        # ------------------------------------------------------------------
        # Copy this image to _recent if this was a frontend batch
        # ------------------------------------------------------------------
        if is_frontend_initiated:
            try:
                target_path = save_to_recent(r.get("message"), batch_id, metadata=image_data)
                if target_path:
                    recent_files.append(target_path.name)
            except Exception as e:
                error(f"[generate_image] Failed to copy into _recent: {e}")
                import traceback
                error(f"[generate_image] Traceback: {traceback.format_exc()}")
        
        result_images.append(image_data)
        info(f"Generated image {i+1}/{batch_size} with ID: {image_id}")
    
    return {
        "success": True,
        "images": result_images,
        "batch_id": batch_id,
        "prompt": prompt,
        "workflow": workflow,
        "recent_files": recent_files
    } 