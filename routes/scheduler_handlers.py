# === Handler functions for scheduler ===

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from utils.logger import info, error, debug
import random
import json
from routes.scheduler_utils import log_schedule, scheduler_contexts_stacks
from routes.service_factory import get_generation_service, get_animation_service, get_display_service

def handle_random_choice(instruction, context, now, output, publish_destination):
    var = instruction["var"]
    choice = random.choice(instruction["choices"])
    context["vars"][var] = choice
    msg = f"Randomly chose '{choice}' for var '{var}'."
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return False

def handle_devise_prompt(instruction, context, now, output, publish_destination):
    theme = instruction.get("theme")
    theme_var = instruction.get("theme_var")
    var = instruction["var"]
    
    if theme_var:
        theme = context["vars"].get(theme_var)
    
    prompt = f"Generated prompt for theme: {theme}"
    context["vars"][var] = prompt
    
    if "history" in instruction:
        history_var = instruction["history"]
        if history_var not in context["vars"]:
            context["vars"][history_var] = []
        # Append timestamp and prompt to history
        context["vars"][history_var].append({
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": prompt
        })
    
    msg = f"Devised prompt: {prompt}"
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return False

def handle_generate(instruction, context, now, output, publish_destination):
    try:
        # Extract the prompt from either direct input or variable
        prompt = ""
        if "input" in instruction:
            if isinstance(instruction["input"], dict):
                if "prompt" in instruction["input"]:
                    prompt = instruction["input"]["prompt"]
                elif "prompt_var" in instruction["input"]:
                    prompt_var = instruction["input"]["prompt_var"]
                    prompt = context["vars"].get(prompt_var, "")
        else:
            # Fallback for old format
            prompt = instruction.get("prompt", instruction.get("value", ""))
            prompt_var_value = context["vars"].get(instruction.get("prompt_var", ""), "")
            prompt = " ".join(filter(None, [prompt, prompt_var_value]))

        if not prompt or prompt.strip() == "":
            error_msg = "No prompt supplied for generation."
            output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
            log_schedule(error_msg, publish_destination, now)
            return 
        
        refiner = instruction.get("refiner", None)  

        debug(f"Preparing generation with prompt: '{prompt}', refiner: {refiner}")

        send_obj = {
            "data": {
                "prompt": prompt,
                "images": [],  # Add empty images array
                "refiner": instruction.get("refiner", None),
                "workflow": instruction.get("workflow", None),
                "targets": [publish_destination]
            }
        }
        #    # TODO: later add back: **call_args

        # Now let's generate with prompt 
        start_msg = f"Starting image generation with prompt: '{prompt}', refiner: {refiner}"
        output.append(f"[{now.strftime('%H:%M')}] {start_msg}")
        log_schedule(start_msg, publish_destination, now)
            
        # Get the generation service from our factory
        generation_service = get_generation_service()
        
        debug(f"Sending generation request: {json.dumps(send_obj)}")
        
        # Call the service
        response = generation_service(
            input_obj = send_obj,
            wait = True            
        )

        debug(f"Response from image generation: '{response}'")
        
        if not response:
            error_msg = "Image generation returned no results."
            output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
            log_schedule(error_msg, publish_destination, now)
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
        history_var = instruction.get("history_output_var")
        if history_var:
            if history_var not in context["vars"]:
                context["vars"][history_var] = []
            # Append timestamp and prompt to history
            context["vars"][history_var].append({
                "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                "prompt": prompt,
                "image_url": image_url  # Add the image URL to history as well
            })
        
        success_msg = f"Generated image from: '{prompt}'"
        # Add more detailed success logging
        if isinstance(response, list) and len(response) > 0:
            first_result = response[0]
            if isinstance(first_result, dict):
                result_details = first_result.get("file", image_url or "unknown")
                success_msg = f"Generated image from: '{prompt}' -> {result_details}"
                
        output.append(f"[{now.strftime('%H:%M')}] {success_msg}")
        log_schedule(f"GENERATE SUCCESS: {success_msg}", publish_destination, now)
        
    except Exception as e:
        error_msg = f"Exception in handle_generate: {str(e)}"
        output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
        log_schedule(error_msg, publish_destination, now)
        import traceback
        error(traceback.format_exc())

def handle_animate(instruction, context, now, output, publish_destination):
    # Extract the prompt from either direct input or variable (similar to handle_generate)
    prompt = ""
    if "input" in instruction:
        if isinstance(instruction["input"], dict):
            if "prompt" in instruction["input"]:
                prompt = instruction["input"]["prompt"]
            elif "prompt_var" in instruction["input"]:
                prompt_var = instruction["input"]["prompt_var"]
                prompt = context["vars"].get(prompt_var, "")
    else:
        # Fallback for old format
        prompt = instruction.get("prompt", "")
        prompt_var_value = context["vars"].get(instruction.get("prompt_var", ""), "")
        prompt = " ".join(filter(None, [prompt, prompt_var_value]))
    
    # Make the request to the animate endpoint
    try:
        # Get the animation service from our factory
        animation_service = get_animation_service()
        
        success_msg = f"Started animation of {context.get('last_generated', 'unknown file')}"
        
        # Create an obj dictionary similar to what alexa.process provides
        obj = {
            "data": {
                "targets": [publish_destination],
                "refiner": instruction.get("refiner", "animator"),
                "prompt": prompt if prompt else None,
                "image_path": context.get("last_generated")
            }
        }
        
        # Call with both targets and obj parameters
        animation_service(targets=[publish_destination], obj=obj)
        
        output.append(f"[{now.strftime('%H:%M')}] {success_msg}")
        log_schedule(f"ANIMATE SUCCESS: {success_msg}", publish_destination, now)
    except Exception as e:
        error_msg = f"Error starting animation: {str(e)}"
        output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
        log_schedule(error_msg, publish_destination, now)
        import traceback
        error(traceback.format_exc())

def handle_display(instruction, context, now, output, publish_destination):
    show = instruction["show"]
    if show not in ["Next", "Random", "Blank"]:
        error_msg = f"Invalid display mode: {show}. Must be 'Next', 'Random', or 'Blank'."
        log_schedule(error_msg, publish_destination, now, output)
        return False

    # Use the display service from the factory
    display_from_bucket = get_display_service()
    
    result = display_from_bucket(
        publish_destination_id=publish_destination,
        mode=show,
        silent=instruction.get("silent", False)
    )

    if not result.get("success"):
        error_msg = f"Failed to display {show.lower()} image: {result.get('error')}"
        log_schedule(error_msg, publish_destination, now, output)
        return False

    msg = f"Displayed {show.lower()} favorite" if show != "Blank" else "Displayed blank screen"
    log_schedule(msg, publish_destination, now, output)
    return False  # Don't unload the schedule

def handle_sleep(instruction, context, now, output, publish_destination):
    duration = instruction["duration"]
    msg = f"Sleeping display for {duration} minutes."
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return False

def handle_wait(instruction, context, now, output, publish_destination):
    duration = instruction["duration"]
    # If we're not already waiting, start the wait
    if "wait_until" not in context:
        wait_until = now + timedelta(minutes=duration)
        context["wait_until"] = wait_until
        msg = f"Started waiting for {duration} minutes (until {wait_until.strftime('%H:%M')})"
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
        return False  # Don't unload yet - we're just starting the wait
    
    # If we are waiting, check if it's complete
    if now >= context["wait_until"]:
        msg = "Wait period complete"
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
        del context["wait_until"]  # Clear the wait state
        return True  # Signal that we can unload now
    
    # Still waiting
    remaining = (context["wait_until"] - now).total_seconds() / 60
    msg = f"Still waiting, {remaining:.1f} minutes remaining"
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return False  # Don't unload while still waiting

def handle_unload(instruction, context, now, output, publish_destination):
    msg = "Unloading temporary schedule."
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return True  # Signal that we should unload the temporary schedule

def handle_device_media_sync(instruction, context, now, output, publish_destination):
    # This is where we would call the device media sync endpoint
    # ...
    msg = "Syncing media with device"
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return False

def handle_device_wake(instruction, context, now, output, publish_destination):
    # This is where we would call the device wake endpoint
    # ...
    msg = "Waking device"
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return False

def handle_device_sleep(instruction, context, now, output, publish_destination):
    # This is where we would call the device sleep endpoint
    # ...
    msg = "Putting device to sleep"
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    return False

def handle_set_var(instruction, context, now, output, publish_destination):
    var_name = instruction["var"]
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
    
    if value is None:
        error_msg = f"Error in set_var: could not determine value"
        output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
        log_schedule(error_msg, publish_destination, now)
        return False
    
    context["vars"][var_name] = value
    msg = f"Set {var_name} to {value}."
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)
    
    # If this variable was set to null and was exported, remove its export registry entries
    if value is None:
        from routes.scheduler_utils import remove_exported_var
        removed = remove_exported_var(var_name, publish_destination)
        if removed:
            remove_msg = f"Removed export registry entries for {var_name} as it was set to null"
            output.append(f"[{now.strftime('%H:%M')}] {remove_msg}")
            log_schedule(remove_msg, publish_destination, now)
    
    return False

# Stops scheduler but doesn't unload
def handle_stop(instruction, context, now, output, publish_destination):
    from routes.scheduler_api import running_schedulers, scheduler_states, update_scheduler_state
    
    # Get the stop mode - 'normal' (default) or 'immediate'
    stop_mode = instruction.get("mode", "normal")
    
    # Set a stopping flag in the context to indicate normal stop in progress
    if stop_mode == "normal":
        context["stopping"] = True
        msg = "Stop instruction received (normal mode) - will run final_instructions before stopping."
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
        return False  # Don't unload yet, let final_instructions run
    else:  # immediate mode
        msg = "Stop instruction received (immediate mode) - stopping scheduler immediately without running final_instructions."
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
        
        # Explicitly remove from running_schedulers without unloading
        if publish_destination in running_schedulers:
            future = running_schedulers.pop(publish_destination, None)
            if future:
                future.cancel()
            
            # Update state to stopped, but preserve schedule and context
            scheduler_states[publish_destination] = "stopped"
            update_scheduler_state(
                publish_destination,
                state="stopped"
            )
        
        return True  # Unload the schedule immediately

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
            output.append(f"[{now.strftime('%H:%M')}] {msg}")
            log_schedule(msg, publish_destination, now)
        else:
            msg = f"No import entry found for variable '{var_name}' to remove"
            output.append(f"[{now.strftime('%H:%M')}] {msg}")
            log_schedule(msg, publish_destination, now)
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
                
                # Add to available vars with value
                available_vars[var] = {
                    **var_info,
                    "value": value
                }
        source_type = "group"
        source_id = source_group
    elif source_scope == "global":
        # Import from global scope - get all global variables
        registry = load_vars_registry()
        available_vars = {}
        for var, var_info in registry.get("global", {}).items():
            owner_id = var_info["owner"]
            # Get the actual value from the owner's context
            value = None
            if owner_id in scheduler_contexts_stacks and scheduler_contexts_stacks[owner_id]:
                owner_context = scheduler_contexts_stacks[owner_id][-1]
                if "vars" in owner_context and var in owner_context["vars"]:
                    value = owner_context["vars"][var]
            
            # Add to available vars with value
            available_vars[var] = {
                **var_info,
                "value": value
            }
        source_type = "global scope"
        source_id = "global"
    else:
        # No valid source specified
        msg = f"Failed to import variable '{var_name}': No valid source (dest_id, group, or scope) specified"
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
        return False
    
    # Check if the variable exists in the available variables
    if var_name in available_vars and available_vars[var_name]["value"] is not None:
        # Import the variable
        value = available_vars[var_name]["value"]
        context["vars"][imported_as] = value
        
        # Get the owner for registration
        owner_id = available_vars[var_name]["owner"]
        
        # Register the import in the registry
        register_imported_var(
            var_name=var_name,
            imported_as=imported_as,
            source_dest_id=owner_id,  # Always register with the actual owner
            importing_dest_id=publish_destination,
            timestamp=now.isoformat()
        )
        
        # Log success
        value_desc = str(value)
        if isinstance(value, dict) or isinstance(value, list):
            value_desc = f"{type(value).__name__} with {len(value)} items"
            
        friendly_name = available_vars[var_name]["friendly_name"]
        
        msg = f"Imported variable '{var_name}' ('{friendly_name}') from {source_type} '{source_id}' as '{imported_as}' with value: {value_desc}"
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
    else:
        # Log failure
        msg = f"Failed to import variable '{var_name}' from {source_type} '{source_id}': Variable not found or has no value"
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
    
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
                output.append(f"[{now.strftime('%H:%M')}] {msg}")
                log_schedule(msg, publish_destination, now)
            else:
                msg = f"No export entry found for variable '{var_name}' to remove"
                output.append(f"[{now.strftime('%H:%M')}] {msg}")
                log_schedule(msg, publish_destination, now)
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
            output.append(f"[{now.strftime('%H:%M')}] {msg}")
            log_schedule(msg, publish_destination, now)
    else:
        # Log failure
        msg = f"Failed to export variable '{var_name}': Variable not found in current context"
        output.append(f"[{now.strftime('%H:%M')}] {msg}")
        log_schedule(msg, publish_destination, now)
    
    return False  # Don't unload the schedule 