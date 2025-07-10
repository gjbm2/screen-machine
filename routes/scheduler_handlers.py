# === Handler functions for scheduler ===

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from utils.logger import info, error, debug, warning
import random
import json
from routes.scheduler_utils import log_schedule, scheduler_contexts_stacks, get_next_scheduled_action as get_next_action, process_jinja_template
from routes.service_factory import get_generation_service, get_animation_service, get_display_service
from routes.utils import dict_substitute, build_schema_subs
from routes.samsung_utils import device_sleep, device_wake, device_sync, device_standby
from routes.bucketer import purge_bucket as bucketer_purge_bucket, _append_to_bucket
import routes.openai
from time import time
import os
from routes.publisher import publish_to_destination
import uuid
import traceback

# Maximum number of items to keep in history variables
MAX_HISTORY_SIZE = 20

def handle_random_choice(instruction, context, now, output, publish_destination):
    var = instruction["var"]
    choice = random.choice(instruction["choices"])
    context["vars"][var] = choice
    msg = f"Randomly chose '{choice}' for var '{var}'."
    log_schedule(msg, publish_destination, now, output)
    return False

def handle_generate(instruction, context, now, output, publish_destination):
    """Handle the generate instruction."""
    # The instruction has already been processed with Jinja templating
    prompt = ""
    # Extract the prompt from instruction
    if "input" in instruction:
        if isinstance(instruction["input"], dict):
            if "prompt" in instruction["input"]:
                prompt = instruction["input"]["prompt"]
    
    # Get fields - no need to process Jinja again since it was done at instruction level
    refiner = instruction.get("refiner")
    workflow = instruction.get("workflow")
    
    # Process images parameter if provided
    images = instruction.get("images", [])
    
    # If no prompt found and no images provided, log an error
    # For image-to-image generation (like upscaling), a prompt is not required
    if not prompt and not images:
        error_msg = "No prompt provided for generate instruction."
        log_schedule(error_msg, publish_destination, now, output)
        return
    if images:
        # Convert images to the expected format for the generation service
        # Each image should be a dict with both "name" and "image" keys
        formatted_images = []
        for img in images:
            if isinstance(img, str):
                # If it's a string path, load and encode the image
                try:
                    from PIL import Image
                    import base64
                    from io import BytesIO
                    import os
                    
                    # Load the image file
                    image_path = img
                    if not os.path.isabs(image_path):
                        # If relative path, make it absolute
                        image_path = os.path.abspath(image_path)
                    
                    if os.path.exists(image_path):
                        image = Image.open(image_path)
                        # Convert to RGB if needed for JPEG compatibility
                        if image.mode in ("RGBA", "P", "LA"):
                            image = image.convert("RGB")
                        
                        # Encode as base64 JPEG
                        buf = BytesIO()
                        image.save(buf, format="JPEG", quality=95)
                        base64_str = base64.b64encode(buf.getvalue()).decode("ascii")
                        
                        # Create the properly formatted object
                        formatted_images.append({
                            "name": os.path.basename(image_path),
                            "image": base64_str
                        })
                    else:
                        warning(f"Image file not found: {image_path}")
                except Exception as e:
                    warning(f"Failed to load image {img}: {e}")
            elif isinstance(img, dict) and "name" in img and "image" in img:
                # If it's already in the correct format, use it as is
                formatted_images.append(img)
            else:
                # Skip invalid formats
                warning(f"Invalid image format in scheduler instruction: {img}")
        images = formatted_images
    
    # Get new publish parameter (default True for backward compatibility)
    publish = instruction.get("publish", True)
    output_var = instruction.get("output_var")
    
    # ------------------------------------------------------------------
    # Determine requested batch size (if any) so we can log it clearly
    # ------------------------------------------------------------------
    requested_batch = None
    # Check additionalProperties string/dict – we already processed it into send_obj["data"] below, so we
    # will simply look it up afterwards.

    # Log and generate – mention batch size if >1
    log_msg = f"Generating from: '{prompt}'"
    requested_batch = instruction.get("additionalProperties", "")
    # If provided as key=value string (e.g. "batch=4") or JSON
    try:
        _ap = instruction.get("additionalProperties")
        batch_val = None
        if isinstance(_ap, str):
            # Look for batch in key=value pairs
            for prop in _ap.split(","):
                if "=" in prop:
                    k, v = prop.split("=", 1)
                    if k.strip() == "batch":
                        batch_val = int(v.strip())
                        break
        elif isinstance(_ap, dict):
            if "batch" in _ap:
                batch_val = int(_ap["batch"])
        if batch_val and batch_val > 1:
            log_msg += f" (batch of {batch_val})"
    except Exception:
        pass
    
    if refiner:
        log_msg += f" (using refiner: {refiner})"
    if workflow:
        log_msg += f" (using workflow: {workflow})"
    if not publish:
        log_msg += " (not publishing to display)"
    
    log_schedule(log_msg, publish_destination, now, output)
    
    try:
        if (not prompt or prompt.strip() == "") and not images:
            error_msg = "No prompt supplied for generation."
            log_schedule(error_msg, publish_destination, now, output)
            return 
        
        debug(f"Preparing generation with prompt: '{prompt}', refiner: {refiner}, publish: {publish}")

        # Build the send object
        # If publish is False, we send empty targets list to prevent display
        targets = [publish_destination] if publish else []
        
        # Load target config to get maxwidth/maxheight for downscaler (same as frontend)
        target_config = {}
        if publish_destination:
            try:
                from routes.utils import findfile
                from routes.generate import loosely_matches
                import json
                filepath = findfile("publish-destinations.json")
                if filepath:
                    with open(filepath, "r") as file:
                        publish_destinations = json.load(file)
                    
                    # Find the target config (same matching logic as frontend)
                    target_config = next(
                        (item for item in publish_destinations if
                         loosely_matches(item.get("id", ""), publish_destination) or
                         loosely_matches(item.get("name", ""), publish_destination)),
                        {}
                    )
            except Exception as e:
                debug(f"Failed to load target config: {e}")
        
        # Create base send object with required fields
        send_obj = {
            "data": {
                "prompt": prompt,
                "images": images,  # Use processed images from instruction
                "refiner": refiner,
                "workflow": workflow,
                "targets": targets,
                # Add target dimensions for downscaler (same as frontend)
                "maxwidth": target_config.get("maxwidth"),
                "maxheight": target_config.get("maxheight")
            }
        }

        # Handle additionalProperties - can be string containing JSON or key=value format
        if "additionalProperties" in instruction:
            props = instruction["additionalProperties"]
            if isinstance(props, str):
                # Try to parse as JSON first
                try:
                    props_dict = json.loads(props)
                    if isinstance(props_dict, dict):
                        # Successfully parsed JSON object
                        for key, value in props_dict.items():
                            send_obj["data"][key] = value
                    else:
                        # JSON parsed but not an object - treat as key=value string
                        raise ValueError("Not a JSON object")
                except (json.JSONDecodeError, ValueError):
                    # Not valid JSON, treat as key=value string
                    if props:
                        for prop in props.split(","):
                            if "=" in prop:
                                key, value = prop.split("=", 1)
                                key = key.strip()
                                value = value.strip()
                                # Try to convert to number if possible
                                try:
                                    if "." in value:
                                        value = float(value)
                                    else:
                                        value = int(value)
                                except ValueError:
                                    pass  # Keep as string if not a number
                                send_obj["data"][key] = value
            elif isinstance(props, dict):
                # Direct object format - add all properties
                for key, value in props.items():
                    send_obj["data"][key] = value

        # Now let's generate with prompt 
        # Include batch info (if previously detected)
        start_msg = f"Starting image generation with prompt: '{prompt}', refiner: {refiner}"
        try:
            if batch_val and batch_val > 1:
                start_msg += f", batch={batch_val}"
        except Exception:
            pass
        
        log_schedule(start_msg, publish_destination, now, output)
            
        # Get the generation service from our factory
        generation_service = get_generation_service()
        
        debug(f"Sending generation request: {json.dumps(send_obj)}")
        
        # Call the service
        if hasattr(generation_service, 'handle_image_generation'):
            # It's a mock service object - used in tests
            response = generation_service.handle_image_generation(
                input_obj=send_obj,
                wait=True
            )
        else:
            # It's a function - used in production
            response = generation_service(
                input_obj=send_obj,
                wait=True            
            )

        debug(f"Response from image generation: '{response}'")
        
        if not response:
            error_msg = "Image generation returned no results."
            log_schedule(error_msg, publish_destination, now, output)
            return    

        # Extract the image paths from the response
        image_paths = []
        if isinstance(response, list):
            for result in response:
                if isinstance(result, dict):
                    # Get the image path - prefer published_path, fallback to message
                    path = result.get("published_path") or result.get("message")
                    if path:
                        image_paths.append(path)
        
        # If publish is False, we need to manually save to bucket using publish_to_destination
        if not publish and image_paths:
            saved_paths = []
            
            # Generate a batch_id for this batch of images
            batch_id = f"batch_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            
            # Prepare metadata for the batch images
            batch_metadata = {
                "prompt": prompt,
                "workflow": workflow,
                "refiner": refiner,
                "batch_id": batch_id,
                "when_generated": now.isoformat(),
                "publish": False  # Mark that this was not published to display
            }
            
            # Add any additional metadata from the instruction
            if "metadata" in instruction:
                batch_metadata.update(instruction["metadata"])
            
            for idx, path in enumerate(image_paths):
                # Add batch index to metadata
                image_metadata = batch_metadata.copy()
                image_metadata["batch_index"] = idx
                
                # Call publish_to_destination with silent=True to save to bucket without display
                pub_result = publish_to_destination(
                    source=path,
                    publish_destination_id=publish_destination,
                    silent=True,  # This prevents overlays but still saves to bucket
                    metadata=image_metadata,
                    batch_id=batch_id,
                    update_published=False  # Don't update the published pointer for batch images
                )
                
                if pub_result.get("success"):
                    # Get the actual bucket path from the result
                    if "meta" in pub_result and "filename" in pub_result["meta"]:
                        # Store the full bucket path, not just the filename
                        from routes.bucketer import bucket_path
                        full_path = str(bucket_path(publish_destination) / pub_result["meta"]["filename"])
                        saved_paths.append(full_path)
                    else:
                        saved_paths.append(path)
                else:
                    warning(f"Failed to save {path} to bucket: {pub_result.get('error', 'Unknown error')}")
            
            if saved_paths:
                image_paths = saved_paths
                debug(f"Saved {len(saved_paths)} images to bucket (silent mode).")
        
        # Store in output variable(s) if specified
        if output_var and image_paths:
            # Single output_var - store single path or list
            if len(image_paths) == 1:
                context["vars"][output_var] = image_paths[0]
                debug(f"Stored image path in variable '{output_var}': {image_paths[0]}")
            else:
                context["vars"][output_var] = image_paths
                debug(f"Stored {len(image_paths)} image paths in variable '{output_var}'")
        
        # Keep backward compatibility - store first image in context["last_generated"]
        if image_paths:
            context["last_generated"] = image_paths[0]
            debug(f"Stored first image path in context['last_generated']: {image_paths[0]}")

        # Handle history if specified
        history_var = instruction.get("history_var")
        if history_var:
            if history_var not in context["vars"]:
                context["vars"][history_var] = []
            
            # Truncate prompt for history to prevent large entries
            stored_prompt = prompt
            if len(stored_prompt) > 50:
                stored_prompt = stored_prompt[:47] + "..."
                
            # Append entry to history with standardized fields
            context["vars"][history_var].append({
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "generation",
                "prompt": stored_prompt,
                "refiner": refiner,
                "workflow": workflow,
                "image_paths": image_paths,
                "published": publish
            })
            
            # Cap history size to prevent unlimited growth
            if len(context["vars"][history_var]) > MAX_HISTORY_SIZE:
                # Remove oldest entries first (keeping most recent MAX_HISTORY_SIZE entries)
                context["vars"][history_var] = context["vars"][history_var][-MAX_HISTORY_SIZE:]
                debug(f"Capped {history_var} at {MAX_HISTORY_SIZE} entries")
        
        success_msg = f"Generated {len(image_paths)} image(s) from: '{prompt}'"
        if not publish:
            success_msg += " (saved to bucket only, not displayed)"
                
        # Log each generated image individually for better visibility
        for i, pth in enumerate(image_paths, start=1):
            log_schedule(f" → Image {i}/{len(image_paths)}: {os.path.basename(pth)}", publish_destination, now, output)
        
        log_schedule(f"GENERATE SUCCESS: {success_msg}", publish_destination, now, output)
        
    except Exception as e:
        error_msg = f"Error in handle_generate: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())

def handle_animate(instruction, context, now, output, publish_destination):
    # Extract the prompt - no need to process with Jinja again
    prompt = ""
    
    if "input" in instruction:
        if isinstance(instruction["input"], dict):
            if "prompt" in instruction["input"]:
                prompt = instruction["input"]["prompt"]
    
    # Make the request to the animate endpoint
    try:
        # Get the animation service from our factory
        animation_service = get_animation_service()
        
        # Get the source image path
        image_path = context.get("last_generated", "unknown file")
        success_msg = f"Started animation of {image_path}"
        
        # Get refiner - no need to process with Jinja again
        refiner = instruction.get("refiner", "animator")
        
        # Create an obj dictionary similar to what alexa.process provides
        obj = {
            "data": {
                "targets": [publish_destination],
                "refiner": refiner,
                "prompt": prompt if prompt else None,
                "image_path": image_path
            }
        }
        
        # Call with appropriate method depending on what we got
        if hasattr(animation_service, 'animate'):
            # It's a mock service object - used in tests
            result = animation_service.animate(
                targets=[publish_destination], 
                obj=obj
            )
        else:
            # It's a function - used in production
            result = animation_service(
                targets=[publish_destination], 
                obj=obj
            )
        
        # Handle history tracking if specified
        history_var = instruction.get("history_var")
        if history_var:
            if history_var not in context["vars"]:
                context["vars"][history_var] = []
            
            # Truncate prompt for history to prevent large entries
            stored_prompt = prompt
            if len(stored_prompt) > 50:
                stored_prompt = stored_prompt[:47] + "..."
                
            # Add animation details to history with standardized fields
            animation_id = result.get("animation_id", "unknown") if isinstance(result, dict) else "unknown"
            context["vars"][history_var].append({
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "animation",
                "prompt": stored_prompt,
                "image_path": image_path,
                "refiner": refiner,
                "animation_id": animation_id
            })
            
            # Cap history size to prevent unlimited growth
            if len(context["vars"][history_var]) > MAX_HISTORY_SIZE:
                # Remove oldest entries first (keeping most recent MAX_HISTORY_SIZE entries)
                context["vars"][history_var] = context["vars"][history_var][-MAX_HISTORY_SIZE:]
                debug(f"Capped {history_var} at {MAX_HISTORY_SIZE} entries")
        
        log_schedule(f"ANIMATE SUCCESS: {success_msg}", publish_destination, now, output)
        return result
    except Exception as e:
        error_msg = f"Error in handle_animate: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())
        return None

def handle_display(instruction, context, now, output, publish_destination):
    show = instruction["show"]
    if show not in ["Next", "Random", "Previous", "Blank"]:
        error_msg = f"Invalid display mode: {show}. Must be 'Next', 'Random', 'Previous', or 'Blank'."
        log_schedule(error_msg, publish_destination, now, output)
        return False

    # Use the display service from the factory
    display_service = get_display_service()
    
    result = display_service(
        publish_destination_id=publish_destination,
        mode=show,
        silent=instruction.get("silent", False)
    )

    if not result.get("success"):
        error_msg = f"Failed to display {show.lower()} image: {result.get('error')}"
        log_schedule(error_msg, publish_destination, now, output)
        return False

    show_type = show.lower()
    message = f"Displayed {show_type} favorite"
    log_schedule(message, publish_destination, now, output)
    return False

def handle_sleep(instruction, context, now, output, publish_destination):
    duration = instruction["duration"]
    msg = f"Sleeping display for {duration} minutes."
    log_schedule(msg, publish_destination, now, output)
    return False

def handle_wait(instruction, context, now, output, publish_destination):
    """
    Process a wait instruction. This pauses execution for the specified duration,
    but allows urgent events to interrupt the wait.
    
    The wait is implemented as a non-blocking state rather than an actual delay,
    so the scheduler continues running and can process urgent events.
    
    Args:
        instruction: The wait instruction with a 'duration' property (can be string with units or number)
        context: The current context
        now: Current datetime
        output: List to append log messages to
        publish_destination: The current scheduler's publish destination ID
        
    Returns:
        bool: True if wait is complete, False if still waiting
    """
    from routes.scheduler_utils import parse_duration
    
    # Store the last time the wait status was checked, to avoid excessive logging
    # This is a static variable that persists between function calls
    if not hasattr(handle_wait, '_last_check_times'):
        handle_wait._last_check_times = {}
    
    # Get a unique key for this wait instance
    wait_instance_key = f"{publish_destination}"
    
    # Only perform detailed processing and logging at most once every 2 seconds for each destination
    current_time = time()
    last_check_time = handle_wait._last_check_times.get(wait_instance_key, 0)
    should_process_fully = (current_time - last_check_time) >= 2.0
    
    # Get duration - already processed by process_instruction_jinja
    duration_raw = instruction.get("duration")
    
    # Don't log anything for duration 0 - this is just a status check call
    is_status_check = duration_raw == 0 or duration_raw == "0"
    
    # Parse the duration into seconds using the utility function
    try:
        seconds = parse_duration(duration_raw, default_seconds=60)
        if should_process_fully and not is_status_check:
            debug_str = f"Parsed wait duration '{duration_raw}' to {seconds} seconds"
            log_schedule(debug_str, publish_destination, now, output)
            handle_wait._last_check_times[wait_instance_key] = current_time
    except (ValueError, TypeError) as e:
        if should_process_fully and not is_status_check:
            error_msg = f"Invalid wait duration: '{duration_raw}' - using default of 1 minute"
            log_schedule(error_msg, publish_destination, now, output)
            handle_wait._last_check_times[wait_instance_key] = current_time
        seconds = 60  # Default to 1 minute
    
    # Convert back to minutes for display
    duration_minutes = seconds / 60
    
    # Handle any errors in the wait state
    try:    
        # If we're not already waiting, start the wait
        if "wait_until" not in context:
            # Calculate end time
            wait_until = now + timedelta(seconds=seconds)
            context["wait_until"] = wait_until  # Store as datetime object
            context["last_wait_log"] = now  # Initialize last log time
            
            # Format duration for display
            if duration_minutes < 1:
                seconds_display = int(seconds)
                duration_str = f"{seconds_display} second{'s' if seconds_display != 1 else ''}"
            elif duration_minutes == int(duration_minutes):
                minutes_display = int(duration_minutes)
                duration_str = f"{minutes_display} minute{'s' if minutes_display != 1 else ''}"
            else:
                minutes_display = int(duration_minutes)
                seconds_display = int((duration_minutes - minutes_display) * 60)
                duration_str = f"{minutes_display} minute{'s' if minutes_display != 1 else ''}"
                if seconds_display > 0:
                    duration_str += f" and {seconds_display} second{'s' if seconds_display != 1 else ''}"
                
            msg = f"Started waiting for {duration_str} (until {wait_until.strftime('%H:%M:%S')})"
            log_schedule(msg, publish_destination, now, output)
            handle_wait._last_check_times[wait_instance_key] = current_time
            return False  # Don't unload yet - we're just starting the wait
        
        # Ensure wait_until is a datetime object
        wait_until = context["wait_until"]
        if isinstance(wait_until, str):
            # If it's a string (from JSON serialization), convert it back to datetime
            try:
                from dateutil import parser
                wait_until = parser.parse(wait_until)
                context["wait_until"] = wait_until  # Update with the proper datetime object
            except Exception as e:
                if should_process_fully:
                    error_msg = f"Error in wait: could not parse wait_until date: {e}"
                    log_schedule(error_msg, publish_destination, now, output)
                    handle_wait._last_check_times[wait_instance_key] = current_time
                # Reset the wait state to avoid getting stuck
                if "wait_until" in context:
                    del context["wait_until"]
                if "last_wait_log" in context:
                    del context["last_wait_log"]
                return True  # Signal that we can unload now
        
        # If we are waiting, check if it's complete
        if now >= wait_until:
            msg = "Wait period complete"
            log_schedule(msg, publish_destination, now, output)
            del context["wait_until"]  # Clear the wait state
            if "last_wait_log" in context:
                del context["last_wait_log"]  # Also clear the log timestamp
            handle_wait._last_check_times[wait_instance_key] = current_time
            return True  # Signal that we can unload now
        
        # Only log status update every 60 seconds (instead of 30)
        last_log = context.get("last_wait_log", now - timedelta(seconds=61))
        # Ensure last_log is a datetime object
        if isinstance(last_log, str):
            try:
                from dateutil import parser
                last_log = parser.parse(last_log)
            except Exception:
                last_log = now - timedelta(seconds=61)  # Default if parsing fails
        
        should_log = (now - last_log).total_seconds() >= 60
        
        if should_log and should_process_fully:
            # Still waiting
            remaining = (wait_until - now).total_seconds() / 60
            
            # Format remaining time for display
            if remaining < 1:
                seconds_remaining = int(remaining * 60)
                remaining_str = f"{seconds_remaining} second{'s' if seconds_remaining != 1 else ''}"
            elif remaining == int(remaining):
                minutes_remaining = int(remaining)
                remaining_str = f"{minutes_remaining} minute{'s' if minutes_remaining != 1 else ''}"
            else:
                minutes_remaining = int(remaining)
                seconds_remaining = int((remaining - minutes_remaining) * 60)
                remaining_str = f"{minutes_remaining} minute{'s' if minutes_remaining != 1 else ''}"
                if seconds_remaining > 0:
                    remaining_str += f" and {seconds_remaining} second{'s' if seconds_remaining != 1 else ''}"
            
            msg = f"Still waiting, {remaining_str} remaining"
            log_schedule(msg, publish_destination, now, output)
            context["last_wait_log"] = now  # Update the timestamp
            handle_wait._last_check_times[wait_instance_key] = current_time
    except Exception as e:
        if should_process_fully:
            error_msg = f"Error in wait: {str(e)}"
            log_schedule(error_msg, publish_destination, now, output)
            handle_wait._last_check_times[wait_instance_key] = current_time
        # Reset the wait state to avoid getting stuck
        if "wait_until" in context:
            del context["wait_until"]
        if "last_wait_log" in context:
            del context["last_wait_log"]
        return True  # Signal that we can unload now
    
    return False  # Don't unload while still waiting

def handle_unload(instruction, context, now, output, publish_destination):
    # First check if this schedule has prevent_unload=true
    from routes.scheduler_utils import scheduler_schedule_stacks
    
    # Get the current schedule (top of stack)
    if publish_destination in scheduler_schedule_stacks and scheduler_schedule_stacks[publish_destination]:
        current_schedule = scheduler_schedule_stacks[publish_destination][-1]
        if current_schedule.get("prevent_unload", False):
            msg = "Unload instruction ignored: schedule has 'prevent_unload' flag set to true"
            log_schedule(msg, publish_destination, now, output)
            return False  # Don't unload
    
    msg = "Unloading temporary schedule."
    log_schedule(msg, publish_destination, now, output)
    return True  # Signal that we should unload the temporary schedule

def handle_device_media_sync(instruction, context, now, output, publish_destination):
    """Handle device media sync instruction."""
    try:
        result = device_sync(publish_destination)
        if result == "fail":
            log_schedule("Device media sync failed", publish_destination, now, output)
        elif result == "no_action":
            log_schedule("Device media sync - no action needed", publish_destination, now, output)
        else:
            log_schedule("Device media sync started successfully", publish_destination, now, output)
    except Exception as e:
        error_msg = f"Error in handle_device_media_sync: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())

def handle_device_wake(instruction, context, now, output, publish_destination):
    """Handle device wake instruction."""
    try:
        # Use a longer timeout for wake operations to allow for TV response
        result = device_wake(publish_destination, timeout=30)
        if result == "fail":
            log_schedule("Device wake failed", publish_destination, now, output)
        elif result == "no_action":
            log_schedule("Device wake - no action needed", publish_destination, now, output)
        else:
            log_schedule("Device wake completed successfully", publish_destination, now, output)
    except Exception as e:
        error_msg = f"Error in handle_device_wake: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())

def handle_device_sleep(instruction, context, now, output, publish_destination):
    """Handle device sleep instruction."""
    try:
        result = device_sleep(publish_destination)
        if result == "fail":
            log_schedule("Device sleep failed", publish_destination, now, output)
        elif result == "no_action":
            log_schedule("Device sleep - no action needed", publish_destination, now, output)
        else:
            log_schedule("Device sleep completed successfully", publish_destination, now, output)
    except Exception as e:
        error_msg = f"Error in handle_device_sleep: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())

def handle_device_standby(instruction, context, now, output, publish_destination):
    """Handle device standby instruction."""
    debug(f"*************************************************handle_device_standby: {publish_destination}")
    try:
        device_standby(publish_destination)
        log_schedule("Device standby completed", publish_destination, now, output)
    except Exception as e:
        error_msg = f"Error in handle_device_standby: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())

def handle_set_var(instruction, context, now, output, publish_destination):
    # Process Jinja templates in the instruction at execution time
    from routes.scheduler_utils import process_jinja_template
    processed_instruction = process_jinja_template(instruction, context, publish_destination)
    
    # Get var_name - now processed with Jinja at execution time
    var_name = processed_instruction.get("var")
    
    # Special case: if var_name is null, reset all variables in the context
    if var_name is None:
        # If context doesn't have vars dict yet, nothing to do
        if "vars" not in context:
            context["vars"] = {}
            
        # Store the old variable count for logging
        old_var_count = len(context["vars"])
        
        # Clear all variables
        context["vars"].clear()
        
        msg = f"Reset context: cleared {old_var_count} variables."
        log_schedule(msg, publish_destination, now, output)
        return False
    
    # Regular set_var behavior for named variables
    # Support both direct value and nested input.value format
    value = None
    if "value" in processed_instruction:
        value = processed_instruction["value"]
    elif "input" in processed_instruction and isinstance(processed_instruction["input"], dict):
        if "value" in processed_instruction["input"]:
            value = processed_instruction["input"]["value"]
        elif "var_ref" in processed_instruction["input"]:
            ref_var = processed_instruction["input"]["var_ref"]
            if "vars" in context and ref_var in context["vars"]:
                value = context["vars"][ref_var]
            elif "default" in processed_instruction:
                value = processed_instruction["default"]
        elif len(processed_instruction["input"]) == 0:
            # If input is an empty object {}, interpret as explicitly setting to null
            value = None
    
    # Fix: Check for top-level default if no value was found through other methods
    if value is None and "default" in processed_instruction:
        value = processed_instruction["default"]
    
    if value is None and not ("input" in processed_instruction and isinstance(processed_instruction["input"], dict) and len(processed_instruction["input"]) == 0):
        error_msg = f"Error in set_var: could not determine value"
        log_schedule(error_msg, publish_destination, now, output)
        return False
    
    # If value is explicitly null, remove the variable from context if it exists
    if value is None and var_name in context["vars"]:
        del context["vars"][var_name]
        msg = f"Removed variable {var_name}."
        log_schedule(msg, publish_destination, now, output)
    else:
        # Attempt to convert string values to appropriate types
        if isinstance(value, str):
            # Try to convert to number (int or float) if it looks like one
            try:
                # Check if it's an integer
                if value.isdigit() or (value.startswith('-') and value[1:].isdigit()):
                    value = int(value)
                # Check if it's a float
                elif '.' in value and value.replace('.', '').replace('-', '').isdigit():
                    float_val = float(value)
                    # Keep as int if it's a whole number
                    if float_val == int(float_val):
                        value = int(float_val)
                    else:
                        value = float_val
                # Check if it's a boolean
                elif value.lower() in ['true', 'false']:
                    value = value.lower() == 'true'
            except (ValueError, AttributeError):
                # If conversion fails, keep as string
                pass
        
        context["vars"][var_name] = value
        msg = f"Set {var_name} to {value}."
        log_schedule(msg, publish_destination, now, output)
    
    # If this variable was set to null and was exported, remove its export registry entries
    if value is None:
        from routes.scheduler_utils import remove_exported_var
        removed = remove_exported_var(var_name, publish_destination)
        if removed:
            remove_msg = f"Removed export registry entries for {var_name} as it was set to null"
            log_schedule(remove_msg, publish_destination, now, output)
    
    return False

def handle_terminate(instruction, context, now, output, publish_destination):
    """
    Process a terminate instruction, which can end script execution in various ways.
    
    Args:
        instruction: The terminate instruction containing:
            - mode: 'normal' (default) runs final_instructions before terminating
                   'immediate' terminates without final_instructions
                   'block' exits current instruction block only
            - test: Optional Jinja expression that must evaluate to true
        context: The current context
        now: Current datetime
        output: List to append log messages to
        publish_destination: The current scheduler's publish destination ID
    
    Returns:
        str or bool: "EXIT_BLOCK" to exit current block, True to unload schedule, False otherwise
    """
    from routes.scheduler import running_schedulers
    from routes.scheduler_utils import scheduler_states, update_scheduler_state
    from routes.scheduler_utils import process_jinja_template
    from routes.scheduler_utils import throw_event, scheduler_schedule_stacks
    
    def jinja_string_to_bool(value):
        """
        Convert a Jinja template result (often a string) to a boolean in a robust way.
        Rules:
        1. If value is already bool -> return it.
        2. If value is int/float -> return value != 0
        3. If value is a string:
           - Strip whitespace, lower-case for comparison.
           - Explicit **false** strings: '', 'false', 'no', 'n', '0', 'off', 'none', 'null'.
           - Explicit **true** strings: 'true', 'yes', 'y', '1', 'on', 't'.
           - If string is numeric (e.g. '23', '0.0') -> convert and evaluate != 0
           - Anything else (non-empty) is treated as True.
        """
        # Case 1: Already boolean
        if isinstance(value, bool):
            return value
        
        # Case 2: Numeric types
        if isinstance(value, (int, float)):
            return value != 0
        
        # Case 3: Everything else – treat as string
        if value is None:
            return False
        
        if not isinstance(value, str):
            # Fallback to truthiness of the object
            return bool(value)
        
        v = value.strip()
        if not v:
            return False
        
        v_lower = v.lower()
        
        false_strings = {'false', 'no', 'n', '0', 'off', 'none', 'null'}
        true_strings = {'true', 'yes', 'y', '1', 'on', 't'}
        
        if v_lower in false_strings:
            return False
        if v_lower in true_strings:
            return True
        
        # Try numeric conversion
        try:
            num = float(v)
            return num != 0
        except ValueError:
            pass
        
        # Default: any other non-empty string is True
        return True
    
    # Get the terminate mode - 'normal' (default), 'immediate', or 'block'
    terminate_mode = instruction.get("mode", "normal")
    
    # Check test condition if provided
    test_expr = instruction.get("test")
    if test_expr:
        # DEBUG: Show exactly what we received after Jinja processing
        msg = f"DEBUG TERMINATE: test_expr = '{test_expr}', conf_int in context = {context.get('vars', {}).get('conf_int', 'NOT_FOUND')}"
        log_schedule(msg, publish_destination, now, output)
        
        # The test expression is already processed by process_instruction_jinja at runtime
        # Convert to boolean using our robust function
        test_result = jinja_string_to_bool(test_expr)
        
        # DEBUG: Show boolean conversion result
        msg = f"DEBUG TERMINATE: Boolean result = {test_result}"
        log_schedule(msg, publish_destination, now, output)
            
        if not test_result:
            msg = f"Terminate instruction test condition evaluated to false: '{test_expr}'"
            log_schedule(msg, publish_destination, now, output)
            return False  # Don't terminate if test is false
    else:
        # DEBUG: Show when no test condition
        msg = f"DEBUG TERMINATE: NO test condition found in instruction"
        log_schedule(msg, publish_destination, now, output)
    
    # Get current schedule to check prevent_unload
    prevent_unload = False
    try:
        if publish_destination in scheduler_schedule_stacks and scheduler_schedule_stacks[publish_destination]:
            current_schedule = scheduler_schedule_stacks[publish_destination][-1]
            prevent_unload = current_schedule.get("prevent_unload", False)
    except Exception as e:
        error_msg = f"Error accessing current schedule: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
    
    # Check if this is already from an event to prevent infinite loop
    from_event = instruction.get("from_event", False)
    
    # For terminating, we'll throw a special event that will be picked up urgently
    if terminate_mode == "normal":
        msg = "Terminate instruction received (normal mode) - will run final_instructions before terminating."
        log_schedule(msg, publish_destination, now, output)
        
        # If this is from an event, don't throw another event (prevents infinite loop)
        if from_event:
            debug(f"Not throwing __terminate__ event as this instruction is already from an event")
            return False
            
        # Throw a __terminate__ event which will be picked up urgently
        try:
            throw_event(
                scope=publish_destination,
                key="__terminate__", 
                ttl="60s",
                payload={
                    "mode": "normal",
                    "prevent_unload": prevent_unload
                }
            )
        except Exception as e:
            error_msg = f"Error throwing terminate event: {str(e)}"
            log_schedule(error_msg, publish_destination, now, output)
        
        return False  # Don't unload directly from here
        
    elif terminate_mode == "immediate":
        msg = "Terminate instruction received (immediate mode) - terminating immediately without running final_instructions."
        log_schedule(msg, publish_destination, now, output)
        
        # If this is from an event, don't throw another event (prevents infinite loop)
        if from_event:
            debug(f"Not throwing __terminate_immediate__ event as this instruction is already from an event")
            # If immediate termination from an event, signal that we should unload
            if prevent_unload:
                debug(f"Script has prevent_unload=true, stopping scheduler loop instead")
                from routes.scheduler import stop_scheduler
                stop_scheduler(publish_destination)
                return False
            return True  # Signal that we should unload
        
        # Throw a __terminate_immediate__ event which will be picked up urgently
        try:
            throw_event(
                scope=publish_destination,
                key="__terminate_immediate__", 
                ttl="60s",
                payload={
                    "mode": "immediate",
                    "prevent_unload": prevent_unload
                }
            )
        except Exception as e:
            error_msg = f"Error throwing immediate terminate event: {str(e)}"
            log_schedule(error_msg, publish_destination, now, output)
            
        return False  # Don't unload directly from here
        
    elif terminate_mode == "block":
        msg = "Terminate instruction received (block mode) - exiting current instruction block."
        log_schedule(msg, publish_destination, now, output)
        
        # If this is from an event, don't throw another event
        if from_event:
            debug(f"Not throwing __exit_block__ event as this instruction is already from an event")
            return "EXIT_BLOCK"  # Signal to exit the current block
        
        # Throw a __exit_block__ event which will be picked up urgently
        try:
            throw_event(
                scope=publish_destination,
                key="__exit_block__", 
                ttl="60s",
                payload={"mode": "block"}
            )
        except Exception as e:
            error_msg = f"Error throwing exit block event: {str(e)}"
            log_schedule(error_msg, publish_destination, now, output)
            
        return "EXIT_BLOCK"  # Signal to exit the current block
        
    else:
        error_msg = f"Invalid terminate mode: {terminate_mode}"
        log_schedule(error_msg, publish_destination, now, output)
        return False

def handle_import_var(instruction, context, now, output, publish_destination):
    """
    Import a variable from another scheduler's context.
    
    Args:
        instruction: The import_var instruction object containing var_name and one of:
          - dest_id: The specific destination to import from
          - group: The group to import from
          - scope: 'global' to import from global scope
        context: The current context to modify
        now: Current datetime
        output: List to append log messages to
        publish_destination: The current scheduler's publish destination ID
    
    Returns:
        bool: False (don't unload the schedule)
    """
    from routes.scheduler_utils import get_exported_variables_with_values, register_imported_var, load_vars_registry, remove_imported_var
    # Explicitly import scheduler_contexts_stacks again to be safe
    from routes.scheduler_utils import scheduler_contexts_stacks
    
    var_name = instruction["var_name"]
    # The var name to import as - use the same name if not specified
    imported_as = instruction.get("as", var_name)
    
    # If imported_as is null, it's a request to remove the import
    if imported_as is None:
        removed = remove_imported_var(var_name, publish_destination)
        if removed:
            msg = f"Removed import for variable '{var_name}'"
            log_schedule(msg, publish_destination, now, output)
        else:
            msg = f"No import entry found for variable '{var_name}' to remove"
            log_schedule(msg, publish_destination, now, output)
        return False
    
    # Determine the source: can be a specific destination, a group, or global
    source_dest_id = instruction.get("dest_id", None)
    source_group = instruction.get("group", None)
    source_scope = instruction.get("scope", None)
    
    # Initialize vars if it doesn't exist
    if "vars" not in context:
        context["vars"] = {}
    
    # Handle different source types
    if source_dest_id:
        # Import from specific destination - original behavior
        available_vars = get_exported_variables_with_values(source_dest_id)
        source_type = "destination"
        source_id = source_dest_id
    elif source_group:
        # Import from a group - get all variables in this group
        registry = load_vars_registry()
        available_vars = {}
        if source_group in registry.get("groups", {}):
            # For each variable in the group
            for var, var_info in registry["groups"][source_group].items():
                owner_id = var_info["owner"]
                # Get the actual value from the owner's context
                value = None
                if owner_id in scheduler_contexts_stacks and scheduler_contexts_stacks[owner_id]:
                    owner_context = scheduler_contexts_stacks[owner_id][-1]
                    if "vars" in owner_context and var in owner_context["vars"]:
                        value = owner_context["vars"][var]
                
                # Add to available vars with value - even if value is None
                available_vars[var] = {
                    **var_info,
                    "value": value
                }
        source_type = "group"
        source_id = source_group
    elif source_scope:
        # The source_scope parameter can be used directly - no need to check for specific values
        # Import from scope - get all variables from that scope
        registry = load_vars_registry()
        available_vars = {}
        
        if source_scope == "global":
            # Check global scope
            scope_vars = registry.get("global", {})
            source_type = "global scope"
        else:
            # Check group scope
            scope_vars = registry.get("groups", {}).get(source_scope, {})
            source_type = "group scope"
        
        # Get values for all variables in the scope
        for var, var_info in scope_vars.items():
            owner_id = var_info["owner"]
            # Get the actual value from the owner's context
            value = None
            if owner_id in scheduler_contexts_stacks and scheduler_contexts_stacks[owner_id]:
                owner_context = scheduler_contexts_stacks[owner_id][-1]
                if "vars" in owner_context and var in owner_context["vars"]:
                    value = owner_context["vars"][var]
            
            # Add to available vars with value - even if value is None
            available_vars[var] = {
                **var_info,
                "value": value
            }
        
        source_id = source_scope
    else:
        # No valid source specified
        msg = f"Failed to import variable '{var_name}': No valid source (dest_id, group, or scope) specified"
        log_schedule(msg, publish_destination, now, output)
        return False
    
    # Check if the variable exists in the available variables
    if var_name in available_vars:
        # CHANGED: Import the variable even if value is None
        value = available_vars[var_name]["value"]
        context["vars"][imported_as] = value
        
        # Get the owner for registration
        owner_id = available_vars[var_name]["owner"]
        
        # Register the import in the registry - use different source_dest_id based on how we're importing
        register_source = None
        
        if source_dest_id:
            # Direct import from a specific destination - use owner_id
            register_source = owner_id
        elif source_group:
            # Import from a group - use the group name with special prefix
            register_source = f"group:{source_group}"
        elif source_scope:
            # Import from a scope - use the scope name with special prefix
            register_source = f"scope:{source_scope}"
        else:
            # Fallback - shouldn't happen with earlier validation
            register_source = owner_id
        
        register_imported_var(
            var_name=var_name,
            imported_as=imported_as,
            source_dest_id=register_source,  # Use the appropriate source based on import type
            importing_dest_id=publish_destination,
            timestamp=now.isoformat()
        )
        
        # Log success
        if value is None:
            msg = f"Imported variable '{var_name}' from {source_type} '{source_id}' as '{imported_as}' with null value"
        else:
            value_desc = str(value)
            if isinstance(value, dict) or isinstance(value, list):
                value_desc = f"{type(value).__name__} with {len(value)} items"
                
            friendly_name = available_vars[var_name].get("friendly_name", var_name)
            
            msg = f"Imported variable '{var_name}' ('{friendly_name}') from {source_type} '{source_id}' as '{imported_as}' with value: {value_desc}"
        
        log_schedule(msg, publish_destination, now, output)
    else:
        # Log failure
        msg = f"Failed to import variable '{var_name}' from {source_type} '{source_id}': Variable not found"
        log_schedule(msg, publish_destination, now, output)
    
    return False  # Don't unload the schedule

def handle_export_var(instruction, context, now, output, publish_destination):
    """
    Export a variable to make it available to other schedulers.
    
    Args:
        instruction: The export_var instruction containing var_name, friendly_name, and scope
        context: The current context
        now: Current datetime
        output: List to append log messages to
        publish_destination: The current scheduler's publish destination ID
    
    Returns:
        bool: False (don't unload the schedule)
    """
    from routes.scheduler_utils import register_exported_var, remove_exported_var
    
    var_name = instruction["var_name"]
    friendly_name = instruction.get("friendly_name", var_name)
    scope = instruction["scope"]
    
    # Check if the variable exists in the current context
    if "vars" in context and var_name in context["vars"]:
        value = context["vars"][var_name]
        
        # If value is None, this is a delete operation
        if value is None:
            removed = remove_exported_var(var_name, publish_destination)
            if removed:
                msg = f"Removed export for variable '{var_name}' as it has null value"
                log_schedule(msg, publish_destination, now, output)
            else:
                msg = f"No export entry found for variable '{var_name}' to remove"
                log_schedule(msg, publish_destination, now, output)
        else:
            # Register the exported variable in the central registry
            register_exported_var(
                var_name=var_name,
                friendly_name=friendly_name,
                scope=scope,
                publish_destination=publish_destination,
                timestamp=now.isoformat()
            )
            
            # Log success
            value_desc = str(value)
            if isinstance(value, dict) or isinstance(value, list):
                value_desc = f"{type(value).__name__} with {len(value)} items"
                
            msg = f"Exported variable '{var_name}' as '{friendly_name}' to {scope} scope with value: {value_desc}"
            log_schedule(msg, publish_destination, now, output)
    else:
        # Log failure
        msg = f"Failed to export variable '{var_name}': Variable not found in current context"
        log_schedule(msg, publish_destination, now, output)
    
    return False  # Don't unload the schedule

def handle_reason(instruction, context, now, output, publish_destination):
    """
    Handle the 'reason' instruction which processes text and/or images using a reasoner.
    This replaces the older 'devise_prompt' instruction with more advanced capabilities.
    """
    # Get inputs - already processed with Jinja at instruction level
    text_input = instruction.get("text_input", "")
    image_inputs = instruction.get("image_inputs", [])
    reasoner_id = instruction.get("reasoner", "default")
    output_vars = instruction.get("output_vars", [])

    # text_input should already be processed with Jinja at instruction level
    # No need for additional processing here

    # If image_inputs is a string, it could be either a variable name or a string representation of a list
    if isinstance(image_inputs, str):
        # First check if it looks like a string representation of a list
        if image_inputs.strip().startswith('[') and image_inputs.strip().endswith(']'):
            # Try to parse it as a list
            try:
                import ast
                parsed_list = ast.literal_eval(image_inputs)
                if isinstance(parsed_list, list):
                    image_inputs = parsed_list
                else:
                    # If it didn't parse to a list, treat as variable name
                    var_value = context["vars"].get(image_inputs, [])
                    if isinstance(var_value, list):
                        image_inputs = var_value
                    elif isinstance(var_value, str) and var_value:
                        image_inputs = [var_value]
                    else:
                        image_inputs = []
            except (ValueError, SyntaxError):
                # If parsing failed, treat as variable name
                var_value = context["vars"].get(image_inputs, [])
                if isinstance(var_value, list):
                    image_inputs = var_value
                elif isinstance(var_value, str) and var_value:
                    image_inputs = [var_value]
                else:
                    image_inputs = []
        else:
            # Doesn't look like a list, treat as variable name
            var_value = context["vars"].get(image_inputs, [])
            if isinstance(var_value, list):
                image_inputs = var_value
            elif isinstance(var_value, str) and var_value:
                image_inputs = [var_value]
            else:
                image_inputs = []
    # If image_inputs is already a list (from Jinja expression), use it directly
    elif isinstance(image_inputs, list):
        # Filter out any None or empty string values
        image_inputs = [img for img in image_inputs if img]
    else:
        # For any other type, convert to empty list
        image_inputs = []

    # Validate that we have at least one output variable
    if not output_vars:
        error_msg = "No output variables specified in reason instruction."
        log_schedule(error_msg, publish_destination, now, output)
        return False
    
    # Log the reasoning request
    log_msg = f"Reasoning with '{reasoner_id}' reasoner"
    if text_input:
        # If text input is too long, truncate it for the log
        if len(text_input) > 100:
            log_msg += f", text input: '{text_input[:100]}...'"
        else:
            log_msg += f", text input: '{text_input}'"
    if image_inputs:
        log_msg += f", with {len(image_inputs)} image inputs"
    log_schedule(log_msg, publish_destination, now, output)
    
    try:
        # Get the system prompt from the reasoner template file
        reasoner_template = f"data/reasoners/{reasoner_id}.txt.j2"
        
        # Build substitutions for templates
        subs = build_schema_subs()
        
        # Process the system prompt template with substitutions
        system_prompt = dict_substitute(reasoner_template, subs)
        
        # Get the schema template and process it with substitutions
        schema_template = "data/reasoner.schema.json.j2"
        schema_json = dict_substitute(schema_template, subs)
        
        try:
            schema = json.loads(schema_json)
        except Exception as e:
            error(f"Failed to parse schema template: {e}")
            schema = {
                "type": "object",
                "properties": {
                    "outputs": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        }
                    },
                    "explanation": {
                        "type": "string"
                    }
                },
                "required": ["outputs"]
            }
        
        # Use OpenAI service to process the reasoning
        result = routes.openai.openai_prompt(
            user_prompt=text_input,
            system_prompt=system_prompt,
            schema=schema,
            images=image_inputs if image_inputs else None
        )
        
        # If the reasoner failed to produce valid outputs, create a fallback structure
        if not result or "outputs" not in result or not isinstance(result["outputs"], list):
            error_msg = f"Reasoning with '{reasoner_id}' failed to return valid outputs array."
            log_schedule(error_msg, publish_destination, now, output)

            # Fallback: choose the first image (if any) or NONE so execution can continue
            fallback_output = image_inputs[0] if image_inputs else "NONE"
            result = {
                "outputs": [fallback_output, "50%", "fallback selection"],
                "explanation": "OpenAI reasoning failed – fallback applied"
            }
        
        # Log the explanation if present
        if "explanation" in result and result["explanation"]:
            explanation = result["explanation"]
            # Truncate long explanations for the log
            log_explanation = explanation
            if len(explanation) > 300:
                log_explanation = explanation[:297] + "..."
            explanation_msg = f"Reasoner explanation: {log_explanation}"
            log_schedule(explanation_msg, publish_destination, now, output)
            
        # (Result validation/mapping should be handled by each specific reasoner or by downstream logic.)
        
        # Store each output variable in the context, mapping by position
        # Store only as many variables as we have outputs, up to the number requested
        for i, var_name in enumerate(output_vars):
            if i < len(result["outputs"]):
                context["vars"][var_name] = result["outputs"][i]
                var_log = f"Set {var_name} to result from '{reasoner_id}' reasoning (position {i+1})."
                log_schedule(var_log, publish_destination, now, output)
            else:
                var_error = f"Reasoner didn't return enough values for output variable '{var_name}' (position {i+1})."
                log_schedule(var_error, publish_destination, now, output)
        
        # Handle history if specified
        history_var = instruction.get("history_var")
        if history_var:
            if history_var not in context["vars"]:
                context["vars"][history_var] = []
            
            # Add results to history with standardized fields
            history_entry = {
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "reason",
                "reasoner": reasoner_id,
                "outputs": {}
            }
            
            # Add a truncated/sanitized version of text_input if provided
            if text_input:
                # Check if text contains a history_var reference
                if history_var in text_input:
                    # Sanitize - remove any history references which cause recursion
                    import re
                    # This regex matches {{ history_var[...] }} patterns
                    pattern = re.compile(r'{{[^}]*' + re.escape(history_var) + r'\[[^}]*}}')
                    sanitized_input = pattern.sub("[history reference]", text_input)
                else:
                    sanitized_input = text_input
                
                # Truncate to reasonable length
                if len(sanitized_input) > 50:
                    sanitized_input = sanitized_input[:47] + "..."
                
                history_entry["text_input"] = sanitized_input
                
            # Add image inputs (references only, not the actual data)
            if image_inputs:
                history_entry["image_inputs"] = image_inputs
                
            # Add the outputs in a position-indexed format
            history_entry["outputs"] = {}
            for i, value in enumerate(result["outputs"]):
                if i < len(output_vars):
                    history_entry["outputs"][output_vars[i]] = value
                else:
                    history_entry["outputs"][f"output_{i+1}"] = value
            
            # Add explanation if provided
            if "explanation" in result:
                # Also truncate explanation to reasonable length
                explanation = result["explanation"]
                if len(explanation) > 100:
                    explanation = explanation[:97] + "..."
                history_entry["explanation"] = explanation
                
            # Add the entry to history
            context["vars"][history_var].append(history_entry)
            
            # Cap history size to prevent unlimited growth
            if len(context["vars"][history_var]) > MAX_HISTORY_SIZE:
                # Remove oldest entries first (keeping most recent MAX_HISTORY_SIZE entries)
                context["vars"][history_var] = context["vars"][history_var][-MAX_HISTORY_SIZE:]
                debug(f"Capped {history_var} at {MAX_HISTORY_SIZE} entries")
        
        success_msg = f"Completed reasoning with '{reasoner_id}' – outputs: {', '.join(result['outputs'])}"
        log_schedule(success_msg, publish_destination, now, output)
        
        return False
        
    except Exception as e:
        error_msg = f"Error in handle_reason: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())
        # Final fallback to keep scheduler alive
        if image_inputs:
            context["vars"][output_vars[0]] = image_inputs[0]
            log_schedule(f"Fallback reasoner: set {output_vars[0]} to first image due to error", publish_destination, now, output)
        return False

def handle_log(instruction, context, now, output, publish_destination):
    """
    Simple handler to output a log message.
    Makes script debugging easier.
    
    Args:
        instruction: The log instruction containing the message
        context: The current context
        now: Current datetime
        output: List to append log messages to
        publish_destination: The current scheduler's publish destination ID
        
    Returns:
        bool: False (don't unload the schedule)
    """
    # Get the message - support both "message" key and "text" key
    message = instruction.get("message", instruction.get("text", ""))
    
    # If no message, use a default
    if not message:
        message = "[Empty log message]"
        
    # Output the message to the logs
    log_schedule(message, publish_destination, now, output)
    
    return False

def handle_throw_event(instruction, context, now, output, publish_destination):
    """
    Throw an event based on instruction parameters.
    Args:
        instruction: The throw_event instruction
        context: The current context
        now: Current datetime
        output: List to append log messages to
        publish_destination: The current scheduler's publish destination ID
    Returns:
        bool: False (don't unload the schedule)
    """
    from routes.scheduler_utils import throw_event

    # Get parameters from the instruction
    event_key = instruction["event"]
    scope = instruction.get("scope", publish_destination)  # Default to current destination 
    display_name = instruction.get("display_name")
    ttl = instruction.get("ttl", "60s")
    delay = instruction.get("delay")
    future_time = instruction.get("future_time")
    single_consumer = instruction.get("single_consumer", False)
    payload = instruction.get("payload")

    # Throw the event (let throw_event handle all scope logic)
    result = throw_event(
        scope=scope,
        key=event_key,
        ttl=ttl,
        delay=delay,
        future_time=future_time,
        display_name=display_name,
        payload=payload,
        single_consumer=single_consumer
    )

    # Log the action
    if result.get("status") == "queued":
        msg = f"Threw event '{event_key}' to scope '{scope}'"
        if display_name:
            msg += f" ({display_name})"
        if future_time:
            msg += f", active from {result.get('active_from', '')}"
        elif delay:
            msg += f", delayed by {delay}"
        log_schedule(msg, publish_destination, now, output)
    else:
        error_msg = f"Failed to throw event: {result.get('message', 'unknown error')}"
        log_schedule(error_msg, publish_destination, now, output)

    return False  # Don't unload the schedule

def handle_purge(instruction, context, now, output, publish_destination):
    """
    Handle the purge instruction to clean up a bucket.
    
    Args:
        instruction: The purge instruction, which may include:
            - days: Optional number of days for age-based filtering
            - include_favorites: Whether to include favorite files in purge
        context: The current context
        now: Current datetime
        output: List to append log messages to
        publish_destination: The bucket ID to purge
        
    Returns:
        bool: False (don't unload the schedule)
    """
    # Get parameters from the instruction
    days = instruction.get("days")
    include_favorites = instruction.get("include_favorites", False)
    
    # Log what we're about to do
    msg = f"Purging bucket '{publish_destination}'"
    if days is not None:
        msg += f" for files older than {days} days"
    if not include_favorites:
        msg += " (keeping favorites)"
    else:
        msg += " (including favorites)"
    
    log_schedule(msg, publish_destination, now, output)
    
    try:
        # Call the actual purge_bucket function from bucketer
        result = bucketer_purge_bucket(
            publish_destination_id=publish_destination,
            include_favorites=include_favorites,
            days=days
        )
        
        # Log the results
        deleted_count = len(result.get("removed", []))
        msg = f"Purged {deleted_count} files from bucket '{publish_destination}'"
        log_schedule(msg, publish_destination, now, output)
        
        return False  # Don't unload the schedule
        
    except Exception as e:
        error_msg = f"Error in handle_purge: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())
        return False

def handle_publish(instruction, context, now, output, publish_destination):
    """
    Handle the publish instruction to display images on output devices.
    
    Args:
        instruction: The publish instruction containing:
            - source: Image path(s) or Jinja expression to publish
            - targets: List of destination IDs (default: current destination)
            - silent: Whether to suppress overlays (default: False)
        context: The current context
        now: Current datetime
        output: List to append log messages to
        publish_destination: The current scheduler's publish destination ID
        
    Returns:
        bool: False (don't unload the schedule)
    """
    from routes.publisher import publish_to_destination
    
    # Get images from source
    source = instruction.get("source")
    
    # Check for None or empty string
    if source is None or source == "":
        error_msg = "No 'source' specified for publish instruction"
        log_schedule(error_msg, publish_destination, now, output)
        return False
    
    # Handle both single image and list of images
    images = []
    if isinstance(source, list):
        # Filter out empty strings from list
        images = [img for img in source if img]
    elif source:
        images = [source]
    
    # Get targets (default to current destination)
    targets = instruction.get("targets", [publish_destination])
    if isinstance(targets, str):
        targets = [targets]
    
    # Get silent flag
    silent = instruction.get("silent", False)
    
    # Validate we have images to publish
    if not images:
        # Empty source - this is allowed for conditional publishing
        msg = "No images to publish (empty source) - skipping"
        log_schedule(msg, publish_destination, now, output)
        return False
    
    # Log what we're about to do
    msg = f"Publishing {len(images)} image(s) to {len(targets)} target(s)"
    if silent:
        msg += " (silent mode)"
    log_schedule(msg, publish_destination, now, output)
    
    # Publish each image to each target
    published_count = 0
    for target in targets:
        for image_path in images:
            try:
                # Call publish_to_destination
                result = publish_to_destination(
                    source=image_path,
                    publish_destination_id=target,
                    silent=silent
                )
                
                if result.get("success"):
                    published_count += 1
                    debug(f"Published {image_path} to {target}")
                else:
                    warning(f"Failed to publish {image_path} to {target}: {result.get('error', 'Unknown error')}")
                    
            except Exception as e:
                error_msg = f"Error publishing {image_path} to {target}: {str(e)}"
                log_schedule(error_msg, publish_destination, now, output)
                error(f"Traceback: {traceback.format_exc()}")
    
    # Log final result
    success_msg = f"Successfully published {published_count} image(s)"
    log_schedule(success_msg, publish_destination, now, output)
    
    return False  # Don't unload the schedule

# Delete the duplicate process_time_schedules function that was copied here 