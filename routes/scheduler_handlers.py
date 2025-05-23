# === Handler functions for scheduler ===

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from utils.logger import info, error, debug
import random
import json
from routes.scheduler_utils import log_schedule, scheduler_contexts_stacks, get_next_scheduled_action as get_next_action, process_jinja_template
from routes.service_factory import get_generation_service, get_animation_service, get_display_service
from routes.utils import dict_substitute, build_schema_subs
from routes.samsung_utils import device_sleep, device_wake, device_sync, device_standby
from routes.bucketer import purge_bucket as bucketer_purge_bucket  # Added import for purge_bucket
import routes.openai
from time import time

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
    
    # If no prompt found, log an error
    if not prompt:
        error_msg = "No prompt provided for generate instruction."
        log_schedule(error_msg, publish_destination, now, output)
        return

    # Get fields - no need to process Jinja again since it was done at instruction level
    refiner = instruction.get("refiner")
    workflow = instruction.get("workflow")
    
    # Log and generate
    log_msg = f"Generating from: '{prompt}'"
    if refiner:
        log_msg += f" (using refiner: {refiner})"
    if workflow:
        log_msg += f" (using workflow: {workflow})"
    
    log_schedule(log_msg, publish_destination, now, output)
    
    try:
        if not prompt or prompt.strip() == "":
            error_msg = "No prompt supplied for generation."
            log_schedule(error_msg, publish_destination, now, output)
            return 
        
        debug(f"Preparing generation with prompt: '{prompt}', refiner: {refiner}")

        send_obj = {
            "data": {
                "prompt": prompt,
                "images": [],  # Add empty images array
                "refiner": refiner,
                "workflow": workflow,
                "targets": [publish_destination]
            }
        }
        #    # TODO: later add back: **call_args

        # Now let's generate with prompt 
        start_msg = f"Starting image generation with prompt: '{prompt}', refiner: {refiner}"
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

        # Extract the image URL from the response
        image_url = None
        if isinstance(response, list) and len(response) > 0:
            first_result = response[0]
            if isinstance(first_result, dict):
                # Get the image URL from the message field, similar to app.py
                image_url = first_result.get("message", None)
                if image_url:
                    # Record generation result with the actual image URL
                    context["last_generated"] = image_url
                    debug(f"Stored image URL in context: {image_url}")
                else:
                    debug("No image URL found in response message field")
        
        # Default if no URL was found in the response
        if not image_url:
            context["last_generated"] = "[image_path]"
            debug("Using default image path placeholder")

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
                "image_url": image_url
            })
            
            # Cap history size to prevent unlimited growth
            if len(context["vars"][history_var]) > MAX_HISTORY_SIZE:
                # Remove oldest entries first (keeping most recent MAX_HISTORY_SIZE entries)
                context["vars"][history_var] = context["vars"][history_var][-MAX_HISTORY_SIZE:]
                debug(f"Capped {history_var} at {MAX_HISTORY_SIZE} entries")
        
        success_msg = f"Generated image from: '{prompt}'"
        # Add more detailed success logging
        if isinstance(response, list) and len(response) > 0:
            first_result = response[0]
            if isinstance(first_result, dict):
                result_details = first_result.get("file", image_url or "unknown")
                success_msg = f"Generated image from: '{prompt}' -> {result_details}"
                
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
        device_sync(publish_destination)
        log_schedule("Device media sync completed", publish_destination, now, output)
    except Exception as e:
        error_msg = f"Error in handle_device_media_sync: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())

def handle_device_wake(instruction, context, now, output, publish_destination):
    """Handle device wake instruction."""
    try:
        device_wake(publish_destination)
        log_schedule("Device wake completed", publish_destination, now, output)
    except Exception as e:
        error_msg = f"Error in handle_device_wake: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())

def handle_device_sleep(instruction, context, now, output, publish_destination):
    """Handle device sleep instruction."""
    try:
        device_sleep(publish_destination)
        log_schedule("Device sleep completed", publish_destination, now, output)
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
    # Get var_name - already processed with Jinja at instruction level
    var_name = instruction["var"]
    
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
    if "value" in instruction:
        value = instruction["value"]
    elif "input" in instruction and isinstance(instruction["input"], dict):
        if "value" in instruction["input"]:
            value = instruction["input"]["value"]
        elif "var_ref" in instruction["input"]:
            ref_var = instruction["input"]["var_ref"]
            if "vars" in context and ref_var in context["vars"]:
                value = context["vars"][ref_var]
            elif "default" in instruction:
                value = instruction["default"]
        elif len(instruction["input"]) == 0:
            # If input is an empty object {}, interpret as explicitly setting to null
            value = None
    
    # Fix: Check for top-level default if no value was found through other methods
    if value is None and "default" in instruction:
        value = instruction["default"]
    
    if value is None and not ("input" in instruction and isinstance(instruction["input"], dict) and len(instruction["input"]) == 0):
        error_msg = f"Error in set_var: could not determine value"
        log_schedule(error_msg, publish_destination, now, output)
        return False
    
    # If value is explicitly null, remove the variable from context if it exists
    if value is None and var_name in context["vars"]:
        del context["vars"][var_name]
        msg = f"Removed variable {var_name}."
        log_schedule(msg, publish_destination, now, output)
    else:
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
    
    # Get the terminate mode - 'normal' (default), 'immediate', or 'block'
    terminate_mode = instruction.get("mode", "normal")
    
    # Check test condition if provided
    test_expr = instruction.get("test")
    if test_expr:
        try:
            # Process the test expression with Jinja
            test_result = process_jinja_template(test_expr, context)
            # Convert to boolean - empty string, 0, false, none are all False
            if not test_result or test_result.lower() in ('false', '0', 'none', ''):
                msg = f"Terminate instruction test condition evaluated to false: '{test_expr}'"
                log_schedule(msg, publish_destination, now, output)
                return False  # Don't terminate if test is false
        except Exception as e:
            error_msg = f"Error evaluating terminate test condition: {str(e)}"
            log_schedule(error_msg, publish_destination, now, output)
            return False  # Don't terminate if test evaluation fails
    
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
        
        if not result or "outputs" not in result or not isinstance(result["outputs"], list):
            error_msg = f"Reasoning with '{reasoner_id}' failed to return valid outputs array."
            log_schedule(error_msg, publish_destination, now, output)
            return False
        
        # Log the explanation if present
        if "explanation" in result and result["explanation"]:
            explanation = result["explanation"]
            # Truncate long explanations for the log
            log_explanation = explanation
            if len(explanation) > 300:
                log_explanation = explanation[:297] + "..."
            explanation_msg = f"Reasoner explanation: {log_explanation}"
            log_schedule(explanation_msg, publish_destination, now, output)
            
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
        
        success_msg = f"Completed reasoning with '{reasoner_id}'"
        log_schedule(success_msg, publish_destination, now, output)
        
        return False
        
    except Exception as e:
        error_msg = f"Error in handle_reason: {str(e)}"
        log_schedule(error_msg, publish_destination, now, output)
        import traceback
        error(traceback.format_exc())
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

# Delete the duplicate process_time_schedules function that was copied here 