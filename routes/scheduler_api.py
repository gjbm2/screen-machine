# TODO: handle async instructions with support for 'important' and 'urgent' instructions
# TODO: standard handling of await staete for long runnning executions

#
#TO DO
#
#Let's add an endpoint in _api to trigger an event (which will be picked up by the event trigger).
#
#Just put events in a bucket and at each tick of the scheduler loop:
#- if event is found, then (a) remove event from bucket and (b) run instruction.
#
#the endpoint should optionally take publish_destination.
#
#Finally, let's add a event trigger method for each publish_destination on the front end for "poke" which fires that evnt for the publish_destination.

from datetime import datetime, timedelta
import random
from typing import Dict, Any, List, Optional
import json
from utils.logger import log_to_console, info, error, warning, debug, console_logs
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from jsonschema import validate, ValidationError
import jsonschema
import os
from flask import Blueprint, request, jsonify
import requests
import copy
from routes.utils import dict_substitute, build_schema_subs

# Global storage for scheduler state
scheduler_bp = Blueprint("scheduler_bp", __name__)
running_schedulers = {}
scheduler_logs: Dict[str, List[str]] = {}
scheduler_schedule_stacks = {}  # Store stacks of schedules by destination
scheduler_contexts_stacks = {}  # Store stacks of contexts by destination
scheduler_states = {}  # Store paused state by destination
important_triggers = {}  # Store important triggers by destination
active_events = {}  # Store active events by destination

# Load schedule schema path
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "scheduler", "schedule.schema.json.j2")
# Don't parse the raw schema file at module load time - we'll process it on demand

# === Logging ===
def log_schedule(message: str, publish_destination: Optional[str] = None, now: Optional[datetime] = None):
    """
    Log a message to scheduler logs with timestamp.
    
    Args:
        message: The message to log
        publish_destination: Optional destination to log to. If None, logs to all active destinations.
        now: Optional datetime to use for timestamp. If None, uses current time.
    """
    if now is None:
        now = datetime.now()
    
    formatted_msg = f"[{now.strftime('%H:%M')}] {message}"
    
    if publish_destination is None:
        # Log to all available destinations
        for dest in scheduler_logs.keys():
            scheduler_logs[dest].append(formatted_msg)
    else:
        # Ensure the log list exists for this destination
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
        
        # Log to the specific destination
        scheduler_logs[publish_destination].append(formatted_msg)
    
    # Also log to console with INFO level for visibility
    info(message)

# === Context Initialization ===
def default_context():
    """Create a new default context."""
    return {
        "vars": {},
        "last_generated": None
    }

# === Schema Management ===
def get_current_schema() -> Dict[str, Any]:
    """Get the current schema with jinja substitutions as a JSON object."""
    try:
        with open(SCHEMA_PATH) as f:
            schema_text = f.read()
        
        # Apply jinja substitutions
        subs = build_schema_subs()  # Get substitutions from utils
        if subs:
            schema_text = dict_substitute(schema_text, subs)
        
        return json.loads(schema_text)
    except Exception as e:
        error(f"Error loading schema: {e}")
        # Return a minimal valid schema instead of SCHEDULE_SCHEMA
        return {"title": "Error", "description": f"Error loading schema: {str(e)}", "type": "object", "properties": {}}

@scheduler_bp.route("/api/scheduler/schema", methods=["GET"])
def api_get_schema():
    """Get the current schema with jinja substitutions as a string."""
    try:
        schema = get_current_schema()
        # debug(f"schema: {json.dumps(schema)}")
        return json.dumps(schema), 200, {'Content-Type': 'application/json; charset=utf-8'}
    except Exception as e:
        error(f"Error in api_get_schema: {e}")
        return jsonify({"error": str(e)}), 500

# === Instruction Execution ===
def run_instruction(instruction: Dict[str, Any], context: Dict[str, Any], now: datetime, output: List[str], publish_destination: str):
    action = instruction["action"]
    log_msg = f"Running {action}"
    output.append(f"[{now.strftime('%H:%M')}] {log_msg}")
    log_schedule(log_msg, publish_destination, now)

    handler_map = {
        "random_choice": handle_random_choice,
        "devise_prompt": handle_devise_prompt,
        "generate": handle_generate,
        "animate": handle_animate,
        "display": handle_display,
        "sleep": handle_sleep,
        "wait": handle_wait,
        "unload": handle_unload,
        "device-media-sync": handle_device_media_sync,
        "device-wake": handle_device_wake,
        "device-sleep": handle_device_sleep,
        "set_var": handle_set_var,
        "stop": handle_stop  # Add the new stop handler
    }

    if action in handler_map:
        try:
            # Run the handler which will modify the context
            should_unload = handler_map[action](instruction, context, now, output, publish_destination)
            
            # After running the instruction, update the context in the global stack
            stack = get_context_stack(publish_destination)
            if stack:
                # Get the existing context
                existing_context = stack[-1]
                
                # Create new context by starting with existing context
                new_context = dict(existing_context)
                
                # Update vars dictionary and copy everything else
                new_context["vars"] = {**existing_context.get("vars", {}), **context.get("vars", {})}
                for key, value in context.items():
                    if key != "vars":  # Only copy non-vars keys
                        new_context[key] = value
                
                # Update both the stack item and the global stack
                stack[-1] = new_context
                scheduler_contexts_stacks[publish_destination] = stack
                
                # Get the current schedule stack
                schedule_stack = scheduler_schedule_stacks.get(publish_destination, [])
                
                # Update both stacks in the state
                update_scheduler_state(
                    publish_destination,
                    schedule_stack=schedule_stack,
                    context_stack=stack
                )
            
            return should_unload
        except Exception as e:
            error_msg = f"Error in {action}: {str(e)}"
            output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
            log_schedule(error_msg, publish_destination, now)
    else:
        error_msg = f"Unknown action: {action}"
        output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
        log_schedule(error_msg, publish_destination, now)
    
    return False

def handle_random_choice(instruction, context, now, output, publish_destination):
    var = instruction["var"]
    choice = random.choice(instruction["choices"])
    context["vars"][var] = choice
    msg = f"Randomly chose '{choice}' for var '{var}'."
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)

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
    
    output.append(f"[{now.strftime('%H:%M')}] Devised prompt: {prompt}")
    log_schedule(f"Devised prompt: {prompt}", publish_destination, now)

# Image generation handler
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
            
        from routes.alexa import handle_image_generation
        
        debug(f"Sending generation request: {json.dumps(send_obj)}")
        
        response = handle_image_generation(
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
        from routes.alexa import async_amimate
        success_msg = f"Started animation of {context.get('last_generated', 'unknown file')}"
        
        # Create an obj dictionary similar to what alexa.process provides
        obj = {
            "data": {
                "targets": [publish_destination],
                "refiner": instruction.get("refiner", "animator"),
                "prompt": prompt if prompt else None
            }
        }
        
        # Call with both targets and obj parameters
        async_amimate(targets=[publish_destination], obj=obj)
        
        output.append(f"[{now.strftime('%H:%M')}] {success_msg}")
        log_schedule(f"ANIMATE SUCCESS: {success_msg}", publish_destination, now)
    except Exception as e:
        error_msg = f"Error starting animation: {str(e)}"
        output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
        log_schedule(error_msg, publish_destination, now)
        import traceback
        error(traceback.format_exc())

def handle_display(instruction, context, now, output, publish_destination):
    mode = instruction["mode"]
    # This is where we would call display endpoint
    # ...
    # end of roundtrip
    img = context.get("last_generated")
    result = f"Displayed ({mode}) image."
    output.append(f"[{now.strftime('%H:%M')}] {result}")
    log_schedule(result, publish_destination, now)

def handle_sleep(instruction, context, now, output, publish_destination):
    duration = instruction["duration"]
    msg = f"Sleeping display for {duration} minutes."
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)

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

def handle_device_wake(instruction, context, now, output, publish_destination):
    # This is where we would call the device wake endpoint
    # ...
    msg = "Waking device"
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)

def handle_device_sleep(instruction, context, now, output, publish_destination):
    # This is where we would call the device sleep endpoint
    # ...
    msg = "Putting device to sleep"
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)

def handle_set_var(instruction, context, now, output, publish_destination):
    var_name = instruction["var"]
    value = instruction["value"]
    context["vars"][var_name] = value
    msg = f"Set {var_name} to {value}."
    output.append(f"[{now.strftime('%H:%M')}] {msg}")
    log_schedule(msg, publish_destination, now)

# === Schedule Resolver ===
def resolve_schedule(schedule: Dict[str, Any], now: datetime, publish_destination: str) -> List[Dict[str, Any]]:
    debug(f"Resolving schedule at {now}")
    
    # First check for important triggers
    important_trigger = get_next_important_trigger(publish_destination)
    if important_trigger:
        info(f"Running important trigger from {important_trigger['triggered_at']}")
        log_schedule(f"Executing important trigger that was scheduled at {important_trigger['triggered_at'].strftime('%H:%M')}", publish_destination, now)
        return extract_instructions(important_trigger.get("trigger_actions", {}))
    
    # Accumulate all instructions to execute
    all_instructions = []
    
    # Always execute initial actions first
    initial_instructions = extract_instructions(schedule.get("initial_actions", {}))
    if initial_instructions:
        info("Executing initial actions")
        log_schedule("Executing initial actions", publish_destination, now)
        all_instructions.extend(initial_instructions)
    
    # If there are no triggers, return just the initial actions
    if "triggers" not in schedule or not schedule["triggers"]:
        return all_instructions
    
    # Format date and time strings
    date_str = now.strftime("%-d-%b")  # e.g., 25-Dec
    day_str = now.strftime("%A")       # e.g., Friday
    time_str = now.strftime("%H:%M")   # e.g., 08:00
    minute_of_day = now.hour * 60 + now.minute

    debug(f"Current date: {date_str}, day: {day_str}, time: {time_str}, minute of day: {minute_of_day}")

    # Track if we've matched any trigger
    matched_any_trigger = False
    found_actions_to_execute = False
    
    # Keep track of matched schedules to avoid duplicate processing
    processed_schedule_ids = set()
    
    # First check all date triggers (these take precedence)
    for trigger in schedule.get("triggers", []):
        if trigger["type"] == "date" and "date" in trigger:
            if trigger["date"] == date_str:
                matched_any_trigger = True
                # Process time schedules for this date
                matched_schedules = process_time_schedules(trigger.get("scheduled_actions", []), now, minute_of_day, publish_destination)
                if matched_schedules:
                    found_actions_to_execute = True
                    message = f"Matched date trigger for {date_str} with actions to execute"
                    info(message)
                    log_schedule(message, publish_destination, now)
                    
                    # Extract instructions from matched schedules
                    for matched_schedule in matched_schedules:
                        # Generate a unique ID for this schedule to avoid duplicates
                        schedule_id = id(matched_schedule)
                        if schedule_id not in processed_schedule_ids:
                            processed_schedule_ids.add(schedule_id)
                            instructions = extract_instructions(matched_schedule.get("trigger_actions", {}))
                            debug(f"Extracted {len(instructions)} instructions from date trigger schedule")
                            all_instructions.extend(instructions)
                        else:
                            debug(f"Skipping duplicate schedule in date trigger")
    
    # If a date trigger matched, skip day of week triggers
    if not matched_any_trigger:
        # Then check day of week triggers
        for trigger in schedule.get("triggers", []):
            if trigger["type"] == "day_of_week" and "days" in trigger:
                if day_str in trigger["days"]:
                    matched_any_trigger = True
                    # Process time schedules for this day
                    matched_schedules = process_time_schedules(trigger.get("scheduled_actions", []), now, minute_of_day, publish_destination)
                    if matched_schedules:
                        found_actions_to_execute = True
                        message = f"Matched day_of_week trigger for {day_str} with actions to execute"
                        info(message)
                        log_schedule(message, publish_destination, now)
                        
                        # Extract instructions from matched schedules (only once per schedule)
                        for matched_schedule in matched_schedules:
                            # Generate a unique ID for this schedule to avoid duplicates
                            schedule_id = id(matched_schedule)
                            if schedule_id not in processed_schedule_ids:
                                processed_schedule_ids.add(schedule_id)
                                instructions = extract_instructions(matched_schedule.get("trigger_actions", {}))
                                debug(f"Extracted {len(instructions)} instructions from day_of_week trigger schedule")
                                all_instructions.extend(instructions)
                            else:
                                debug(f"Skipping duplicate schedule in day_of_week trigger")
    
    # Check event triggers (these can match regardless of other triggers)
    for trigger in schedule.get("triggers", []):
        if trigger["type"] == "event" and "value" in trigger:
            event_value = trigger["value"]
            # Check if this event is active for this destination
            if (publish_destination in active_events and 
                event_value in active_events[publish_destination]):
                matched_any_trigger = True
                event_time = active_events[publish_destination][event_value]
                # Clear the event after it's been handled
                del active_events[publish_destination][event_value]
                message = f"Matched event trigger: {event_value}"
                info(message)
                log_schedule(message, publish_destination, now)
                # Add event trigger actions
                event_instructions = extract_instructions(trigger.get("trigger_actions", {}))
                debug(f"Extracted {len(event_instructions)} instructions from event trigger")
                all_instructions.extend(event_instructions)
                found_actions_to_execute = True

    # If we've gone through all triggers and found nothing, add any final actions
    if not matched_any_trigger:
        final_instructions = extract_instructions(schedule.get("final_actions", {}))
        if final_instructions:
            debug("No trigger matched, running final actions")
            log_schedule("No triggers matched, running final actions", publish_destination, now)
            all_instructions.extend(final_instructions)
    
    if not all_instructions:
        debug("No matching triggers or final actions found")
    else:
        debug(f"Found {len(all_instructions)} instructions to execute")
        
    return all_instructions

def process_time_schedules(time_schedules: List[Dict[str, Any]], now: datetime, minute_of_day: int, publish_destination: str = None) -> List[Dict[str, Any]]:
    """Process a list of time schedules and return matched schedules without extracting instructions yet."""
    if not time_schedules:
        return []
        
    current_time_str = now.strftime("%H:%M")
    matched_schedules = []
    
    for schedule in time_schedules:
        # Get the scheduled time
        if "time" not in schedule:
            continue
            
        time_str = schedule["time"]
        
        # Convert time to minutes for comparison
        try:
            scheduled_time = datetime.strptime(time_str, "%H:%M").time()
            scheduled_minutes = scheduled_time.hour * 60 + scheduled_time.minute
        except ValueError:
            error(f"Invalid time format: {time_str}")
            continue
        
        # Check if we have a repeating schedule
        repeat_schedule = schedule.get("repeat_schedule", None)
        
        if repeat_schedule:
            # Handle repeating time window
            try:
                repeat_interval = repeat_schedule.get("every", 0)
                if repeat_interval <= 0:
                    continue
                    
                until_str = repeat_schedule.get("until", "23:59")
                until_time = datetime.strptime(until_str, "%H:%M").time()
                until_minutes = until_time.hour * 60 + until_time.minute
                
                # Handle case where end time is on the next day
                if until_minutes < scheduled_minutes:
                    until_minutes += 24 * 60
                    if minute_of_day < scheduled_minutes:
                        current_minutes = minute_of_day + 24 * 60
                    else:
                        current_minutes = minute_of_day
                else:
                    current_minutes = minute_of_day
                
                # Check if we're in the scheduled window
                if scheduled_minutes <= current_minutes <= until_minutes:
                    # Check if we're at a repeat interval
                    minutes_since_start = current_minutes - scheduled_minutes
                    if minutes_since_start % repeat_interval == 0:
                        message = f"Matched repeating time schedule at {current_time_str} (every {repeat_interval} minutes until {until_str})"
                        info(message)
                        if publish_destination:
                            log_schedule(message, publish_destination, now)
                        matched_schedules.append(schedule)
            except (ValueError, TypeError) as e:
                error(f"Error processing repeat schedule: {e}")
                continue
        else:
            # Handle single time point
            # Only execute if current time matches the scheduled time
            if current_time_str == time_str:
                message = f"Matched time schedule at {time_str}"
                info(message)
                if publish_destination:
                    log_schedule(message, publish_destination, now)
                matched_schedules.append(schedule)
    
    return matched_schedules

def extract_instructions(instruction_container: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract instructions from the new schema's instruction container structure."""
    if not instruction_container:
        return []
        
    # Check if this is an instruction array object with an instructions_block
    if "instructions_block" in instruction_container:
        return instruction_container.get("instructions_block", [])
        
    # Fallback for old format or direct instruction arrays
    return instruction_container.get("instructions", [])

# === Run scheduler in real time ===
@scheduler_bp.route("/api/schedulers", methods=["GET"])
def api_list_schedulers():
    return jsonify({"running": list_running_schedulers()})

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["GET"])
def api_get_scheduler_log(publish_destination):
    return jsonify({"log": get_scheduler_log(publish_destination)})

# Global event loop for background tasks
_event_loop = None
_loop_thread = None
_event_loop_lock = threading.Lock()

def get_event_loop():
    global _event_loop, _loop_thread
    
    with _event_loop_lock:
        if _event_loop is None:
            def run_event_loop():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                global _event_loop
                _event_loop = loop
                loop.run_forever()
                
            _loop_thread = threading.Thread(target=run_event_loop, daemon=True)
            _loop_thread.start()
            
            # Wait for event loop to be created
            while _event_loop is None:
                pass
            
    return _event_loop

def stop_event_loop():
    global _event_loop, _loop_thread
    
    with _event_loop_lock:
        if _event_loop is not None:
            _event_loop.stop()
            _event_loop = None
            _loop_thread = None

def get_next_scheduled_action(publish_destination: str, schedule: Dict[str, Any]) -> Dict[str, Any]:
    """Predict the next scheduled action for a destination and return the prediction data."""
    now = datetime.now()
    current_minute = now.hour * 60 + now.minute
    day_str = now.strftime("%A")
    date_str = now.strftime("%-d-%b")
    
    # Initialize variables to track the next action
    next_action_time = None
    next_action_description = None
    minutes_until_next = float('inf')
    result = {
        "has_next_action": False,
        "next_time": None,
        "description": None,
        "minutes_until_next": None,
        "timestamp": now.isoformat()
    }
    
    # Check all triggers
    for trigger in schedule.get("triggers", []):
        # Day of week trigger
        if trigger["type"] == "day_of_week" and day_str in trigger.get("days", []):
            for time_schedule in trigger.get("scheduled_actions", []):
                if "time" not in time_schedule:
                    continue
                    
                time_str = time_schedule.get("time")
                try:
                    scheduled_time = datetime.strptime(time_str, "%H:%M").time()
                    scheduled_minutes = scheduled_time.hour * 60 + scheduled_time.minute
                except ValueError:
                    continue
                
                # Check for repeating schedule
                repeat_schedule = time_schedule.get("repeat_schedule")
                if repeat_schedule:
                    repeat_interval = repeat_schedule.get("every", 0)
                    if repeat_interval <= 0:
                        continue
                        
                    until_str = repeat_schedule.get("until", "23:59")
                    try:
                        until_time = datetime.strptime(until_str, "%H:%M").time()
                        until_minutes = until_time.hour * 60 + until_time.minute
                    except ValueError:
                        continue
                    
                    # Handle case where end time is on the next day
                    if until_minutes < scheduled_minutes:
                        until_minutes += 24 * 60
                    
                    # If current time is before start time today
                    if current_minute < scheduled_minutes:
                        time_until_next = scheduled_minutes - current_minute
                        if time_until_next < minutes_until_next:
                            minutes_until_next = time_until_next
                            next_action_time = f"{scheduled_time.hour:02d}:{scheduled_time.minute:02d}"
                            next_action_description = f"Repeating '{trigger['type']}' trigger (every {repeat_interval} min until {until_str})"
                    # If current time is within the repeat window
                    elif scheduled_minutes <= current_minute <= until_minutes:
                        # Find next repeat interval
                        minutes_since_start = current_minute - scheduled_minutes
                        next_interval = repeat_interval - (minutes_since_start % repeat_interval)
                        if next_interval == 0:
                            next_interval = repeat_interval
                        
                        if next_interval < minutes_until_next:
                            minutes_until_next = next_interval
                            next_time_minute = (current_minute + next_interval) % (24 * 60)
                            next_action_time = f"{next_time_minute // 60:02d}:{next_time_minute % 60:02d}"
                            next_action_description = f"Repeating '{trigger['type']}' trigger (every {repeat_interval} min until {until_str})"
                else:
                    # Single time point - check if it's in the future today
                    if scheduled_minutes > current_minute:
                        time_until_next = scheduled_minutes - current_minute
                        if time_until_next < minutes_until_next:
                            minutes_until_next = time_until_next
                            next_action_time = f"{scheduled_time.hour:02d}:{scheduled_time.minute:02d}"
                            next_action_description = f"'{trigger['type']}' trigger at specific time"
        
        # Date trigger
        elif trigger["type"] == "date" and date_str == trigger.get("date"):
            # Use same logic as day_of_week for finding next action
            for time_schedule in trigger.get("scheduled_actions", []):
                if "time" not in time_schedule:
                    continue
                    
                time_str = time_schedule.get("time")
                try:
                    scheduled_time = datetime.strptime(time_str, "%H:%M").time()
                    scheduled_minutes = scheduled_time.hour * 60 + scheduled_time.minute
                except ValueError:
                    continue
                
                # Check if this time is in the future today
                if scheduled_minutes > current_minute:
                    time_until_next = scheduled_minutes - current_minute
                    if time_until_next < minutes_until_next:
                        minutes_until_next = time_until_next
                        next_action_time = f"{scheduled_time.hour:02d}:{scheduled_time.minute:02d}"
                        next_action_description = f"'{trigger['type']}' trigger for today ({date_str})"
    
    # Prepare result if next action found
    if next_action_time and next_action_description:
        result["has_next_action"] = True
        result["next_time"] = next_action_time
        result["description"] = next_action_description
        result["minutes_until_next"] = minutes_until_next
        return result
    
    # If no immediate action found, check for future date triggers
    future_date_found = False
    for trigger in schedule.get("triggers", []):
        if trigger["type"] == "date" and "date" in trigger:
            try:
                trigger_date = datetime.strptime(trigger["date"], "%-d-%b").replace(year=now.year)
                # If date is in the past, it might be for next year
                if trigger_date.month < now.month or (trigger_date.month == now.month and trigger_date.day < now.day):
                    trigger_date = trigger_date.replace(year=now.year + 1)
                
                if trigger_date > now:
                    future_date_found = True
                    days_until = (trigger_date - now).days
                    result["has_next_action"] = True
                    result["next_time"] = trigger["date"]
                    result["description"] = f"Date trigger {days_until} days from now"
                    result["minutes_until_next"] = days_until * 24 * 60
                    break
            except ValueError:
                continue
    
    return result

def log_next_scheduled_action(publish_destination: str, schedule: Dict[str, Any]) -> None:
    """Log the next scheduled action for a destination."""
    next_action = get_next_scheduled_action(publish_destination, schedule)
    
    if next_action["has_next_action"]:
        minutes_until_next = next_action["minutes_until_next"]
        next_action_time = next_action["next_time"]
        next_action_description = next_action["description"]
        
        if minutes_until_next < 60:
            log_schedule(f"Next scheduled action at {next_action_time} ({minutes_until_next} minutes from now): {next_action_description}", publish_destination)
        else:
            hours = minutes_until_next // 60
            mins = minutes_until_next % 60
            log_schedule(f"Next scheduled action at {next_action_time} ({hours}h {mins}m from now): {next_action_description}", publish_destination)
    else:
        log_schedule("No upcoming scheduled actions found", publish_destination)

@scheduler_bp.route("/api/schedulers/<publish_destination>/next_action", methods=["GET"])
def api_get_next_action(publish_destination: str):
    """Get the next scheduled action for a destination."""
    try:
        # Check if scheduler exists and has a schedule
        if (publish_destination not in scheduler_schedule_stacks or 
            not scheduler_schedule_stacks.get(publish_destination)):
            return jsonify({"error": "No schedule found for destination"}), 404
        
        # Get the current schedule
        schedule = scheduler_schedule_stacks[publish_destination][-1]
        
        # Get the next action
        next_action = get_next_scheduled_action(publish_destination, schedule)
        
        # Return the next action
        return jsonify({
            "status": "success",
            "destination": publish_destination,
            "next_action": next_action
        })
    except Exception as e:
        error_msg = f"Error getting next action: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

def start_scheduler(publish_destination: str, schedule: Dict[str, Any]):
    """Start a scheduler for the given destination with the provided schedule."""
    try:
        # Check if scheduler was previously running/paused (resuming) or is new/stopped (starting fresh)
        is_resuming = (publish_destination in scheduler_states and 
                      scheduler_states[publish_destination] in ["running", "paused"])
        
        # Stop any existing scheduler for this destination
        if publish_destination in running_schedulers:
            stop_scheduler(publish_destination)
            log_schedule("Stopped existing scheduler", publish_destination)
            
        # Load existing state from disk
        state = load_scheduler_state(publish_destination)
        
        # Initialize in-memory state
        scheduler_states[publish_destination] = "running"
        scheduler_schedule_stacks[publish_destination] = [schedule]
        
        # If we're starting fresh (not resuming), clear context
        # If resuming, keep existing context intact
        if not is_resuming:
            # Create fresh context when starting new
            new_context = default_context()
            new_context["publish_destination"] = publish_destination
            scheduler_contexts_stacks[publish_destination] = [new_context]
            log_schedule("Starting with fresh context", publish_destination)
            
            # Check for important actions in current time window
            catch_up_on_important_actions(publish_destination, schedule)
        else:
            # Use existing context stack when resuming
            if state.get("context_stack"):
                scheduler_contexts_stacks[publish_destination] = state["context_stack"]
            else:
                scheduler_contexts_stacks[publish_destination] = [default_context()]
            log_schedule("Resuming with existing context", publish_destination)
        
        # Start the scheduler
        loop = get_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            run_scheduler(schedule, publish_destination),
            loop
        )
        running_schedulers[publish_destination] = future
        
        # Update persisted state after successful start
        update_scheduler_state(
            publish_destination,
            schedule_stack=scheduler_schedule_stacks[publish_destination],
            context_stack=scheduler_contexts_stacks[publish_destination],
            state="running"
        )
        
        log_schedule("Started scheduler", publish_destination)
        
        # Log the next scheduled action
        log_next_scheduled_action(publish_destination, schedule)
        
    except Exception as e:
        error_msg = f"Error starting scheduler: {str(e)}"
        log_schedule(error_msg, publish_destination)
        # Ensure state is cleaned up on error
        update_scheduler_state(
            publish_destination,
            schedule_stack=[],
            context_stack=[],
            state="stopped"
        )
        raise

def catch_up_on_important_actions(publish_destination: str, schedule: Dict[str, Any]):
    """Check for and execute important actions in the current cycle that may have been missed."""
    now = datetime.now()
    current_minute = now.hour * 60 + now.minute
    day_str = now.strftime("%A")
    date_str = now.strftime("%-d-%b")
    
    info(f"[catch_up] Checking for important actions to catch up on at {now.strftime('%H:%M')}")
    context = get_current_context(publish_destination)
    
    # Check all triggers
    for trigger in schedule.get("triggers", []):
        # Day of week trigger
        if trigger["type"] == "day_of_week" and day_str in trigger.get("days", []):
            info(f"[catch_up] Found matching day-of-week trigger for {day_str}")
            
            for time_schedule in trigger.get("scheduled_actions", []):
                # Check if within an active time window
                time_str = time_schedule.get("time", "00:00")
                try:
                    scheduled_time = datetime.strptime(time_str, "%H:%M").time()
                    scheduled_minutes = scheduled_time.hour * 60 + scheduled_time.minute
                except ValueError:
                    error(f"[catch_up] Invalid time format: {time_str}")
                    continue
                
                # Check for repeating schedule
                repeat_schedule = time_schedule.get("repeat_schedule")
                if repeat_schedule:
                    repeat_interval = repeat_schedule.get("every", 0)
                    if repeat_interval <= 0:
                        continue
                        
                    until_str = repeat_schedule.get("until", "23:59")
                    try:
                        until_time = datetime.strptime(until_str, "%H:%M").time()
                        until_minutes = until_time.hour * 60 + until_time.minute
                    except ValueError:
                        error(f"[catch_up] Invalid until time format: {until_str}")
                        continue
                    
                    # Handle case where end time is on the next day
                    if until_minutes < scheduled_minutes:
                        until_minutes += 24 * 60
                        if current_minute < scheduled_minutes:
                            current_minute_adjusted = current_minute + 24 * 60
                        else:
                            current_minute_adjusted = current_minute
                    else:
                        current_minute_adjusted = current_minute
                    
                    # If we're in an active window
                    if scheduled_minutes <= current_minute_adjusted <= until_minutes:
                        # Find the last interval that should have happened
                        minutes_since_start = current_minute_adjusted - scheduled_minutes
                        intervals_passed = minutes_since_start // repeat_interval
                        
                        if intervals_passed > 0:
                            last_interval_minute = scheduled_minutes + (intervals_passed * repeat_interval)
                            last_interval_hour = last_interval_minute // 60
                            last_interval_min = last_interval_minute % 60
                            
                            # If actions are marked as important, execute them
                            trigger_actions = time_schedule.get("trigger_actions", {})
                            if trigger_actions.get("important", False):
                                info(f"[catch_up] Executing important actions from {last_interval_hour:02d}:{last_interval_min:02d}")
                                scheduler_logs[publish_destination].append(
                                    f"[{now.strftime('%H:%M')}] Catching up on important actions from {last_interval_hour:02d}:{last_interval_min:02d}"
                                )
                                
                                instructions = extract_instructions(trigger_actions)
                                for instr in instructions:
                                    try:
                                        run_instruction(instr, context, now, scheduler_logs[publish_destination], publish_destination)
                                    except Exception as e:
                                        error_msg = f"Error running catch-up instruction: {str(e)}"
                                        scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {error_msg}")
                            else:
                                info(f"[catch_up] Found non-important actions at {last_interval_hour:02d}:{last_interval_min:02d} - skipping")
                        else:
                            info(f"[catch_up] No intervals have passed yet in the current window")
                    else:
                        info(f"[catch_up] Current time {current_minute//60:02d}:{current_minute%60:02d} not in window {scheduled_minutes//60:02d}:{scheduled_minutes%60:02d} - {until_minutes//60:02d}:{until_minutes%60:02d}")
                else:
                    # Non-repeating schedule - only execute if marked important and exactly matching the start time
                    if time_schedule.get("trigger_actions", {}).get("important", False) and scheduled_minutes == current_minute:
                        info(f"[catch_up] Executing important non-repeating actions scheduled for exactly now")
                        instructions = extract_instructions(time_schedule.get("trigger_actions", {}))
                        for instr in instructions:
                            try:
                                run_instruction(instr, context, now, scheduler_logs[publish_destination], publish_destination)
                            except Exception as e:
                                error_msg = f"Error running catch-up instruction: {str(e)}"
                                scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {error_msg}")
        
        # Date trigger
        elif trigger["type"] == "date" and date_str == trigger.get("date"):
            info(f"[catch_up] Found matching date trigger for {date_str}")
            # Use the same logic as for day_of_week trigger for the time_schedules
            # [Same code as above, but for date triggers]

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["POST"])
def api_start_scheduler(publish_destination):
    """Start a scheduler with the provided publish_destination ID."""
    try:
        # IMPORTANT: publish_destination should always be the ID, not the display name
        # If this is receiving display names instead of IDs, the frontend needs fixing
        schedule = request.json
        debug(f"Received schedule for ID '{publish_destination}': {json.dumps(schedule, indent=2)}")
        
        # Check if an empty schedule was provided but we have an existing one
        if not schedule or (isinstance(schedule, dict) and not schedule):
            debug(f"Empty schedule received for '{publish_destination}', checking for existing schedule")
            
            # Check if we have an existing schedule in disk state
            state = load_scheduler_state(publish_destination)
            if state and "schedule_stack" in state and state["schedule_stack"]:
                # Use the topmost schedule from the stack
                schedule = state["schedule_stack"][-1]
                debug(f"Using existing schedule from disk: {json.dumps(schedule, indent=2)}")
            else:
                error_msg = "No existing schedule found and empty schedule provided"
                error(error_msg)
                return jsonify({"error": error_msg}), 400
        
        # Validate schedule structure
        if not isinstance(schedule, dict):
            error_msg = "Schedule must be a JSON object"
            error(error_msg)
            return jsonify({"error": error_msg}), 400
            
        # Check for new schema structure (triggers, initial_actions, final_actions)
        if "triggers" not in schedule and "initial_actions" not in schedule and "final_actions" not in schedule:
            error_msg = "Schedule must contain at least one of: triggers, initial_actions, final_actions"
            error(error_msg)
            return jsonify({"error": error_msg}), 400

        # Validate against schema
        try:
            jsonschema.validate(instance=schedule, schema=get_current_schema())
        except jsonschema.exceptions.ValidationError as e:
            error_msg = f"Invalid schedule format: {str(e)}"
            error(error_msg)
            return jsonify({"error": error_msg}), 400

        start_scheduler(publish_destination, schedule)
        info(f"Successfully started scheduler for ID '{publish_destination}'")
        return jsonify({"status": "started", "destination": publish_destination})
    except Exception as e:
        error_msg = f"Error starting scheduler: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        return jsonify({"error": error_msg}), 400

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["DELETE"])
def api_stop_scheduler(publish_destination):
    stop_scheduler(publish_destination)
    return jsonify({"status": "stopped", "context_reset": True, "destination": publish_destination})

@scheduler_bp.route("/api/schedulers/<publish_destination>/schedule", methods=["GET"])
def api_get_scheduler_schedule(publish_destination):
    try:
        # Get the current schedule from the top of the stack
        if publish_destination in scheduler_schedule_stacks and scheduler_schedule_stacks[publish_destination]:
            schedule = scheduler_schedule_stacks[publish_destination][-1]
            return jsonify({
                "schedule": schedule,
                "destination": publish_destination,
                "stack_size": len(scheduler_schedule_stacks[publish_destination])
            })
        else:
            # Return empty schedule if none exists
            return jsonify({
                "schedule": {},
                "destination": publish_destination,
                "stack_size": 0
            })
    except Exception as e:
        error_msg = f"Error getting scheduler schedule: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

# === Scheduler status ===
def list_running_schedulers() -> List[str]:
    return list(running_schedulers.keys())

import asyncio

scheduler_schedules: Dict[str, Dict[str, Any]] = {}  # Store schedules by destination

# Global storage for contexts and schedule stacks
# scheduler_contexts = {}  # Store contexts by scheduler instance (old)

# Global storage for active events
active_events = {}

def add_important_trigger(publish_destination: str, trigger: Dict[str, Any], now: datetime):
    if publish_destination not in important_triggers:
        important_triggers[publish_destination] = []
    
    # Add timestamp for ordering
    trigger["triggered_at"] = now
    
    important_triggers[publish_destination].append(trigger)

@scheduler_bp.route("/api/schedulers/<publish_destination>/pause", methods=["POST"])
def api_pause_scheduler(publish_destination):
    try:
        scheduler_states[publish_destination] = "paused"
        # Persist state to disk
        update_scheduler_state(publish_destination, state="paused")
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Scheduler paused")
        return jsonify({"status": "paused", "destination": publish_destination})
    except Exception as e:
        error_msg = f"Error pausing scheduler: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/unpause", methods=["POST"])
def api_unpause_scheduler(publish_destination):
    try:
        scheduler_states[publish_destination] = "running"
        # Persist state to disk
        update_scheduler_state(publish_destination, state="running")
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Scheduler unpaused")
        return jsonify({"status": "running", "destination": publish_destination})
    except Exception as e:
        error_msg = f"Error unpausing scheduler: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/status", methods=["GET"])
def api_get_scheduler_status(publish_destination):
    try:
        # Get the current state, defaulting to 'stopped' if not found
        state = scheduler_states.get(publish_destination, 'stopped')
        return jsonify({
            "status": state,
            "destination": publish_destination,
            "is_running": state == "running",
            "is_paused": state == "paused"
        })
    except Exception as e:
        error_msg = f"Error getting scheduler status: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

# Modify run_scheduler to check paused state
async def run_scheduler(schedule: Dict[str, Any], publish_destination: str, step_minutes: int = 1):
    try:
        # Initialize logs
        scheduler_logs[publish_destination] = []
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Starting scheduler")
        
        # Run initial check immediately
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute
        
        # Get current schedule and context from top of stacks
        current_schedule = scheduler_schedule_stacks[publish_destination][-1]
        current_context = get_current_context(publish_destination)
        
        # Check if there are no triggers - this means we should run initial actions, then final actions, then stop
        has_no_triggers = "triggers" not in current_schedule or not current_schedule["triggers"]
        
        # Execute initial instructions
        initial_instructions = extract_instructions(current_schedule.get("initial_actions", {}))
        if initial_instructions:
            info("Executing initial actions")
            for instr in initial_instructions:
                try:
                    should_unload = run_instruction(instr, current_context, now, scheduler_logs[publish_destination], publish_destination)
                    if should_unload:
                        scheduler_schedule_stacks[publish_destination].pop()
                        pop_context(publish_destination)  # Pop the context too
                        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Unloaded schedule")
                        
                        # Sync state after unload
                        update_scheduler_state(
                            publish_destination,
                            schedule_stack=scheduler_schedule_stacks[publish_destination],
                            context_stack=scheduler_contexts_stacks[publish_destination]
                        )
                        
                        if not scheduler_schedule_stacks[publish_destination]:
                            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] No schedules left in stack, stopping scheduler")
                            return
                except Exception as e:
                    error_msg = f"Error running instruction: {str(e)}"
                    scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {error_msg}")
        
        # If no triggers, run final actions and stop
        if has_no_triggers:
            # Run final actions immediately
            final_instructions = extract_instructions(current_schedule.get("final_actions", {}))
            if final_instructions:
                info("Executing final actions (no triggers defined)")
                for instr in final_instructions:
                    try:
                        should_unload = run_instruction(instr, current_context, now, scheduler_logs[publish_destination], publish_destination)
                        if should_unload:
                            break  # Stop running more final instructions if one requests unload
                    except Exception as e:
                        error_msg = f"Error running final instruction: {str(e)}"
                        scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {error_msg}")
            
            # No triggers means we're done after running initial and final actions
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] No triggers defined, stopping scheduler after running all actions")
            
            # Remove this scheduler from running schedulers
            if publish_destination in running_schedulers:
                running_schedulers.pop(publish_destination, None)
                scheduler_states[publish_destination] = "stopped"
                update_scheduler_state(
                    publish_destination,
                    state="stopped"
                )
            return
        
        # Sleep until start of next minute to align all future checks
        seconds_to_next_minute = 60 - now.second
        await asyncio.sleep(seconds_to_next_minute)
        
        last_check_minute = None
        while True:
            # Check if scheduler is stopped
            if publish_destination not in running_schedulers:
                scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Scheduler stopped")
                break
                
            # Check if scheduler is paused
            if scheduler_states.get(publish_destination) == "paused":
                await asyncio.sleep(1)  # Sleep briefly and check again
                continue
                
            now = datetime.now()
            current_minute = now.hour * 60 + now.minute
            
            # Only run if we haven't checked this minute yet
            if current_minute != last_check_minute:
                # Get current schedule and context from top of stacks
                current_schedule = scheduler_schedule_stacks[publish_destination][-1]
                current_context = get_current_context(publish_destination)
                
                # Execute instructions
                instructions = resolve_schedule(current_schedule, now, publish_destination)
                if instructions:
                    for instr in instructions:
                        try:
                            should_unload = run_instruction(instr, current_context, now, scheduler_logs[publish_destination], publish_destination)
                            if should_unload:
                                scheduler_schedule_stacks[publish_destination].pop()
                                pop_context(publish_destination)  # Pop the context too
                                scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Unloaded schedule")
                                
                                # Sync state after unload
                                update_scheduler_state(
                                    publish_destination,
                                    schedule_stack=scheduler_schedule_stacks[publish_destination],
                                    context_stack=scheduler_contexts_stacks[publish_destination]
                                )
                                
                                if not scheduler_schedule_stacks[publish_destination]:
                                    scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] No schedules left in stack, stopping scheduler")
                                    return
                        except Exception as e:
                            error_msg = f"Error running instruction: {str(e)}"
                            scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {error_msg}")
                
                last_check_minute = current_minute
            
            # Sleep a short time before next check
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Scheduler cancelled")
        raise
    except Exception as e:
        error_msg = f"Error in scheduler loop: {str(e)}"
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        raise
    finally:
        # Only update state if we're actually stopping
        if publish_destination not in running_schedulers:
            # Get current state before updating
            current_state = scheduler_states.get(publish_destination)
            # Only set to stopped if we're not paused
            if current_state != "paused":
                scheduler_states[publish_destination] = "stopped"
                update_scheduler_state(
                    publish_destination,
                    schedule_stack=scheduler_schedule_stacks.get(publish_destination, []),
                    context_stack=scheduler_contexts_stacks.get(publish_destination, []),
                    state="stopped"
                )

def get_scheduler_log(publish_destination: str) -> List[str]:
    return scheduler_logs.get(publish_destination, [])

def stop_scheduler(publish_destination: str):
    """Stop the scheduler for a destination while preserving its state."""
    try:
        # Cancel the running future if it exists
        future = running_schedulers.pop(publish_destination, None)
        if future:
            future.cancel()
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Cancelled scheduler future")
        
        # Update in-memory state
        scheduler_states[publish_destination] = "stopped"
        
        # Get current schedule stack
        current_schedule_stack = scheduler_schedule_stacks.get(publish_destination, [])
        
        # Reset context stack to default values while preserving stack structure
        current_context_stack = scheduler_contexts_stacks.get(publish_destination, [])
        new_context_stack = []
        
        # Create new default contexts for each item in the stack
        for _ in range(len(current_context_stack)):
            new_context = default_context()
            new_context["publish_destination"] = publish_destination
            new_context_stack.append(new_context)
        
        # Update context stack in memory
        scheduler_contexts_stacks[publish_destination] = new_context_stack
        
        # Update persisted state to stopped and reset contexts while preserving schedule
        update_scheduler_state(
            publish_destination,
            state="stopped",
            schedule_stack=current_schedule_stack,
            context_stack=new_context_stack
        )
        
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Stopped scheduler while preserving schedule and resetting context")
        
        # If no schedulers are running, stop the event loop
        if not running_schedulers:
            stop_event_loop()
        
    except Exception as e:
        error_msg = f"Error stopping scheduler: {str(e)}"
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        # Ensure state is marked as stopped even on error
        update_scheduler_state(publish_destination, state="stopped")
        raise

# === Simulate schedule with context ===
def simulate_schedule(schedule: Dict[str, Any], start_time: str, end_time: str, step_minutes: int, context: Dict[str, Any]) -> List[str]:
    now = datetime.strptime(start_time, "%H:%M")
    end = datetime.strptime(end_time, "%H:%M")
    output = []

    while now <= end:
        instructions = resolve_schedule(schedule, now, "")
        for instr in instructions:
            run_instruction(instr, context, now, output, "")
        now += timedelta(minutes=step_minutes)

    return output

# Initialize the event loop when the module loads
get_event_loop()

# Add new endpoints for context management
@scheduler_bp.route("/api/schedulers/<publish_destination>/context", methods=["GET"])
def api_get_scheduler_context(publish_destination):
    try:
        # Get the context from the top of the stack
        context = get_current_context(publish_destination)
        if context:
            return jsonify({
                "vars": context.get("vars", {}),
                "last_generated": context.get("last_generated", None)
            })
        return jsonify({"error": "No context found for scheduler"}), 404
    except Exception as e:
        error_msg = f"Error getting scheduler context: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/context", methods=["POST"])
def api_set_scheduler_context(publish_destination):
    try:
        data = request.json
        if not isinstance(data, dict) or "var_name" not in data or "var_value" not in data:
            return jsonify({"error": "Request must include var_name and var_value"}), 400

        var_name = data["var_name"]
        var_value = data["var_value"]

        # Get the context from the top of the stack
        context = get_current_context(publish_destination)
        if not context:
            return jsonify({"error": "No scheduler context found"}), 404

        if "vars" not in context:
            context["vars"] = {}
        
        # If var_value is null, delete the variable instead of setting it to null
        now = datetime.now()
        if var_value is None:
            if var_name in context["vars"]:
                del context["vars"][var_name]
                log_msg = f"Deleted context variable '{var_name}'"
                log_schedule(log_msg, publish_destination, now)
                info(f"Deleted context variable {var_name} from scheduler {publish_destination}")
            else:
                log_msg = f"Attempted to delete non-existent context variable '{var_name}'"
                log_schedule(log_msg, publish_destination, now)
                info(f"Attempted to delete non-existent variable {var_name} from scheduler {publish_destination}")
        else:
            # Set the variable value - can be any type (string, number, boolean, object, array)
            context["vars"][var_name] = var_value
            
            # Add log entry
            value_desc = str(var_value)
            if isinstance(var_value, dict) or isinstance(var_value, list):
                value_desc = f"{type(var_value).__name__} with {len(var_value)} items"
            
            log_msg = f"Set context variable '{var_name}' to {value_desc}"
            log_schedule(log_msg, publish_destination, now)
            info(f"Set context variable {var_name}={var_value} for scheduler {publish_destination}")
        
        # Update the context stack in memory
        scheduler_contexts_stacks[publish_destination][-1] = context
        
        # Persist changes to disk
        update_scheduler_state(
            publish_destination,
            context_stack=scheduler_contexts_stacks[publish_destination]
        )
        
        return jsonify({
            "status": "success", 
            "var_name": var_name, 
            "var_value": var_value,
            "vars": context["vars"],  # Return the updated vars object for convenience
            "deleted": var_value is None
        })
    except Exception as e:
        error_msg = f"Error setting scheduler context: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/schedule", methods=["POST"])
def api_load_schedule(publish_destination):
    """Load a schedule for a destination. If no scheduler exists, create one."""
    try:
        schedule = request.get_json()
        if not schedule:
            return jsonify({"error": "No schedule provided"}), 400

        # Validate against schema
        try:
            jsonschema.validate(instance=schedule, schema=get_current_schema())
        except jsonschema.exceptions.ValidationError as e:
            error_msg = f"Invalid schedule format: {str(e)}"
            error(error_msg)
            return jsonify({"error": error_msg}), 400

        # Initialize stacks if they don't exist
        if publish_destination not in scheduler_schedule_stacks:
            scheduler_schedule_stacks[publish_destination] = []
            scheduler_contexts_stacks[publish_destination] = []
            scheduler_states[publish_destination] = "stopped"
            scheduler_logs[publish_destination] = []
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Initialized new scheduler")

        # Add schedule to stack
        scheduler_schedule_stacks[publish_destination].append(schedule)
        
        # Create new context, inheriting from previous context if it exists
        if len(scheduler_schedule_stacks[publish_destination]) > 1:
            # Get the previous context if it exists
            previous_context = get_current_context(publish_destination)
            if previous_context:
                # Create a new context that inherits from the previous one
                new_context = copy_context(previous_context)
            else:
                new_context = default_context()
        else:
            new_context = default_context()
            
        # Ensure publish_destination is set in the context
        new_context["publish_destination"] = publish_destination
        debug(f"Setting publish_destination in context: {new_context}")
            
        push_context(publish_destination, new_context)
        
        # Update persisted state
        update_scheduler_state(
            publish_destination,
            schedule_stack=scheduler_schedule_stacks[publish_destination],
            context_stack=get_context_stack(publish_destination)
        )
        
        stack_size = len(scheduler_schedule_stacks[publish_destination])
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Loaded new schedule (stack size: {stack_size})")

        # Don't automatically start - wait for user to click start
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Schedule loaded but not started (waiting for user)")
            
        return jsonify({
            "message": f"Schedule loaded. Stack size: {stack_size}",
            "position": stack_size - 1,
            "is_active": True
        }), 200

    except Exception as e:
        error_msg = f"Error loading schedule: {str(e)}"
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        return jsonify({"error": str(e)}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/schedule", methods=["DELETE"])
def api_unload_schedule(publish_destination):
    try:
        if publish_destination in scheduler_schedule_stacks:
            if scheduler_schedule_stacks[publish_destination]:  # If stack is not empty
                # Pop both the schedule and its context
                scheduler_schedule_stacks[publish_destination].pop()
                if scheduler_contexts_stacks.get(publish_destination):
                    scheduler_contexts_stacks[publish_destination].pop()
                
                # Update persisted state
                update_scheduler_state(
                    publish_destination,
                    schedule_stack=scheduler_schedule_stacks[publish_destination],
                    context_stack=get_context_stack(publish_destination)
                )
                
                stack_size = len(scheduler_schedule_stacks[publish_destination])
                scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Unloaded top schedule (stack size: {stack_size})")
                
                # If this was the last schedule, stop the scheduler
                if stack_size == 0:
                    stop_scheduler(publish_destination)
                    scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Stopped scheduler - no schedules remaining")
                
                return jsonify({
                    "status": "unloaded", 
                    "destination": publish_destination,
                    "stack_size": stack_size
                })
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] No schedules to unload")
            return jsonify({"error": "No schedules to unload"}), 400
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] No schedules found")
        return jsonify({"error": "No schedules found"}), 404
    except Exception as e:
        error_msg = f"Error unloading schedule: {str(e)}"
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/schedule/stack", methods=["GET"])
def api_get_schedule_stack(publish_destination):
    try:
        if publish_destination in scheduler_schedule_stacks:
            return jsonify({
                "status": "success",
                "stack": scheduler_schedule_stacks[publish_destination],
                "stack_size": len(scheduler_schedule_stacks[publish_destination])
            })
        return jsonify({"error": "No schedules found"}), 404
    except Exception as e:
        error_msg = f"Error getting schedule stack: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

# Global storage for active events
active_events = {}

@scheduler_bp.route("/api/schedulers/<publish_destination>/events", methods=["POST"])
def api_trigger_event(publish_destination):
    try:
        data = request.json
        if not isinstance(data, dict) or "event" not in data:
            return jsonify({"error": "Request must include event type"}), 400

        event_type = data["event"]
        if event_type not in ["user-started-generation"]:
            return jsonify({"error": f"Unknown event type: {event_type}"}), 400

        # Store the event with timestamp
        if publish_destination not in active_events:
            active_events[publish_destination] = {}
        active_events[publish_destination][event_type] = datetime.now()

        info(f"Triggered event {event_type} for {publish_destination}")
        return jsonify({"status": "event_triggered", "event": event_type})
    except Exception as e:
        error_msg = f"Error triggering event: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

def get_next_important_trigger(publish_destination: str) -> Optional[Dict[str, Any]]:
    if (publish_destination in important_triggers and 
        important_triggers[publish_destination]):
        return important_triggers[publish_destination].pop(0)
    return None

# === Context Stack Management ===
def get_context_stack(publish_destination: str) -> List[Dict[str, Any]]:
    """Get or create the context stack for a destination."""
    if publish_destination not in scheduler_contexts_stacks:
        # Try to load from disk first
        state = load_scheduler_state(publish_destination)
        scheduler_contexts_stacks[publish_destination] = state.get("context_stack", [])
    return scheduler_contexts_stacks[publish_destination]

def push_context(publish_destination: str, context: Dict[str, Any]) -> None:
    """Push a new context onto the stack for a destination."""
    stack = get_context_stack(publish_destination)
    stack.append(context)
    update_scheduler_state(
        publish_destination,
        context_stack=stack
    )

def pop_context(publish_destination: str) -> Dict[str, Any]:
    """Pop the top context from the stack for a destination."""
    stack = get_context_stack(publish_destination)
    context = stack.pop()
    update_scheduler_state(
        publish_destination,
        context_stack=stack
    )
    return context

def get_current_context(publish_destination: str) -> Optional[Dict[str, Any]]:
    """Get the current context from the top of the stack."""
    stack = get_context_stack(publish_destination)
    return stack[-1] if stack else None

def copy_context(context):
    """Create a deep copy of a context."""
    new_context = {
        "vars": copy.deepcopy(context.get("vars", {})),
        "last_generated": context.get("last_generated")
    }
    # Handle lists in vars
    if "vars" in context:
        for key, value in context["vars"].items():
            if isinstance(value, list):
                new_context["vars"][key] = value.copy()
    return new_context

# === Persistence Functions ===
def get_scheduler_storage_path(publish_destination: str) -> str:
    """Get the path to store scheduler data for a destination."""
    # IMPORTANT: Use the exact publish_destination as the filename
    # This is critical because the filename is used to determine which 
    # destinations have schedules during initialization
    storage_dir = os.path.join(os.path.dirname(__file__), "scheduler")
    os.makedirs(storage_dir, exist_ok=True)
    return os.path.join(storage_dir, f"{publish_destination}.json")

def load_scheduler_state(publish_destination: str) -> Dict[str, Any]:
    """Load scheduler state from disk."""
    # Initialize global variables if they don't exist
    if publish_destination not in scheduler_schedule_stacks:
        scheduler_schedule_stacks[publish_destination] = []
    if publish_destination not in scheduler_contexts_stacks:
        scheduler_contexts_stacks[publish_destination] = []
    if publish_destination not in scheduler_states:
        scheduler_states[publish_destination] = "stopped"
    if publish_destination not in scheduler_logs:
        scheduler_logs[publish_destination] = []
    
    path = get_scheduler_storage_path(publish_destination)
    
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                state = json.load(f)
                # Ensure state key exists
                if 'state' not in state:
                    state['state'] = 'stopped'
                # Ensure context_stack exists and is properly initialized
                if 'context_stack' not in state:
                    state['context_stack'] = []
                return state
        except Exception as e:
            error(f"Error loading scheduler state for {publish_destination}: {str(e)}")
    
    # Create a new state with empty stacks
    state = {
        "schedule_stack": [],
        "context_stack": [],
        "state": "stopped",
        "last_updated": datetime.now().isoformat()
    }
    
    # Save the initial state to disk
    save_scheduler_state(publish_destination, state)
    
    return state

def save_scheduler_state(publish_destination: str, state: Dict[str, Any]) -> None:
    """Save scheduler state to disk."""
    path = get_scheduler_storage_path(publish_destination)
    try:
        # Get the current context stack from global state if not in state
        context_stack = state.get("context_stack", scheduler_contexts_stacks.get(publish_destination, []))
        
        debug(f"*** context_stack: {context_stack}")

        # Create a deep copy of the state to avoid modifying the original
        state_to_save = {
            "schedule_stack": state.get("schedule_stack", []),
            "context_stack": [],  # Will be populated below
            "state": state.get("state", "stopped"),
            "last_updated": datetime.now().isoformat()
        }
        
        debug(f"*** state_to_Save: {state_to_save}")
            
        # Deep copy each context in the stack
        for context in context_stack:
            # Start with a copy of the entire context
            context_copy = dict(context)
            
            # Ensure vars is a deep copy
            if "vars" in context_copy:
                context_copy["vars"] = dict(context_copy["vars"])
                
            # Ensure all list values (including history arrays) are deep copied
            for key, value in context_copy.items():
                if isinstance(value, list):
                    context_copy[key] = list(value)
                    
            state_to_save["context_stack"].append(context_copy)
            
        # Log the final state_to_save for debugging
        debug(f"*** Final state_to_save: {state_to_save}")
                    
        with open(path, 'w') as f:
            # Use default=str so datetime and other non-serializable objects are converted to strings
            json.dump(state_to_save, f, indent=2, default=str)
            
    except Exception as e:
        error(f"Error saving scheduler state for {publish_destination}: {str(e)}")
        raise

def update_scheduler_state(publish_destination: str, 
                         schedule_stack: List[Dict[str, Any]] = None,
                         context_stack: List[Dict[str, Any]] = None,
                         state: str = None) -> None:
    """Update and persist scheduler state."""
    debug(f"*** update_scheduler_state called from {__name__}")
    import traceback
    debug(f"*** Stack trace: {traceback.format_stack()}")
    
    current_state = load_scheduler_state(publish_destination)
    
    if schedule_stack is not None:
        current_state["schedule_stack"] = schedule_stack
    if context_stack is not None:
        # Create a new context stack to store the merged contexts
        new_context_stack = []
        
        # Process each context in the new stack
        for i, context in enumerate(context_stack):
            # Start with a new context
            new_context = {}
            
            # If there's an existing context at this position, use it as base
            if i < len(current_state.get("context_stack", [])):
                existing_context = current_state["context_stack"][i]
                # Copy existing context as base
                new_context = dict(existing_context)
                # Update vars dictionary
                new_context["vars"] = {**existing_context.get("vars", {}), **context.get("vars", {})}
            else:
                # For new contexts, just copy the entire context
                new_context = dict(context)
                new_context["vars"] = dict(context.get("vars", {}))
            
            # Copy non-vars fields from the new context
            for key, value in context.items():
                if key != "vars":
                    new_context[key] = value
            
            # Ensure publish_destination is preserved/set
            if "publish_destination" in context:
                new_context["publish_destination"] = context["publish_destination"]
            elif i < len(current_state.get("context_stack", [])) and "publish_destination" in current_state["context_stack"][i]:
                new_context["publish_destination"] = current_state["context_stack"][i]["publish_destination"]
            
            new_context_stack.append(new_context)
        
        current_state["context_stack"] = new_context_stack
        
    if state is not None:
        current_state["state"] = state
        
    # Save to disk immediately
    save_scheduler_state(publish_destination, current_state)
    
    # Update in-memory state
    if schedule_stack is not None:
        scheduler_schedule_stacks[publish_destination] = schedule_stack
    if context_stack is not None:
        scheduler_contexts_stacks[publish_destination] = context_stack
    if state is not None:
        scheduler_states[publish_destination] = state

def initialize_schedulers_from_disk():
    """Initialize scheduler states from disk on startup."""
    storage_dir = os.path.join(os.path.dirname(__file__), "scheduler")
    if not os.path.exists(storage_dir):
        return
        
    for filename in os.listdir(storage_dir):
        if filename.endswith('.json'):
            # CRITICAL: This assumes the filename (minus .json) is the publish_destination ID
            # If display names are being used for filenames, schedulers won't load correctly
            publish_destination = filename[:-5]  # Remove .json extension
            info(f"Loading scheduler state for ID '{publish_destination}'")
            state = load_scheduler_state(publish_destination)
            
            # Initialize the schedule stack
            if "schedule_stack" in state and state["schedule_stack"]:
                scheduler_schedule_stacks[publish_destination] = state["schedule_stack"]
                scheduler_contexts_stacks[publish_destination] = state.get("context_stack", [])
                scheduler_states[publish_destination] = state.get("state", "stopped")
                
                # Start the scheduler if it was running
                if state["state"] == "running":
                    start_scheduler(publish_destination, state["schedule_stack"][-1])
            else:
                # Initialize empty stacks if they don't exist
                scheduler_schedule_stacks[publish_destination] = []
                scheduler_contexts_stacks[publish_destination] = []
                scheduler_states[publish_destination] = "stopped"

# Initialize schedulers from disk when the module loads
initialize_schedulers_from_disk()

# === Schedule Stack Management ===
@scheduler_bp.route("/api/schedulers/<publish_destination>/schedule/<int:position>", methods=["GET"])
def api_get_schedule_at_position(publish_destination: str, position: int):
    """Get the schedule at a specific position in the stack."""
    try:
        if publish_destination not in scheduler_schedule_stacks:
            return jsonify({"error": "No schedules found for destination"}), 404
            
        stack = scheduler_schedule_stacks[publish_destination]
        if position < 0 or position >= len(stack):
            return jsonify({"error": f"Invalid position {position}. Stack size: {len(stack)}"}), 400
            
        return jsonify({
            "schedule": stack[position],
            "position": position,
            "is_active": position == len(stack) - 1
        })
    except Exception as e:
        error_msg = f"Error getting schedule at position {position}: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/schedule/<int:position>", methods=["PUT"])
def api_set_schedule_at_position(publish_destination: str, position: int):
    """Set the schedule at a specific position in the stack."""
    try:
        schedule = request.get_json()
        if not schedule:
            return jsonify({"error": "No schedule provided"}), 400
            
        # Validate against schema
        try:
            jsonschema.validate(instance=schedule, schema=get_current_schema())
        except jsonschema.exceptions.ValidationError as e:
            error_msg = f"Invalid schedule format: {str(e)}"
            error(error_msg)
            return jsonify({"error": error_msg}), 400
            
        if publish_destination not in scheduler_schedule_stacks:
            scheduler_schedule_stacks[publish_destination] = []
            
        stack = scheduler_schedule_stacks[publish_destination]
        
        # If position is beyond current stack size, pad with empty schedules
        while position >= len(stack):
            stack.append({})
            
        # Update the schedule at the specified position
        stack[position] = schedule
        
        # Update persisted state
        update_scheduler_state(
            publish_destination,
            schedule_stack=stack
        )
        
        # Log the update
        scheduler_logs[publish_destination].append(
            f"[{datetime.now().strftime('%H:%M')}] Updated schedule at position {position}"
        )
        
        # If this is the active schedule and the scheduler is running, warn about potential state issues
        if position == len(stack) - 1 and publish_destination in running_schedulers:
            scheduler_logs[publish_destination].append(
                f"[{datetime.now().strftime('%H:%M')}] WARNING: Active schedule updated while running. State variables may be stale."
            )
            
        return jsonify({
            "status": "updated",
            "position": position,
            "is_active": position == len(stack) - 1,
            "warning": "State variables may be stale" if position == len(stack) - 1 else None
        })
        
    except Exception as e:
        error_msg = f"Error setting schedule at position {position}: {str(e)}"
        error(error_msg)
        import traceback
        error(f"Error traceback: {traceback.format_exc()}")
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/schedule/<int:position>", methods=["DELETE"])
def api_remove_schedule_at_position(publish_destination: str, position: int):
    """Remove the schedule at a specific position in the stack."""
    try:
        if publish_destination not in scheduler_schedule_stacks:
            return jsonify({"error": "No schedules found for destination"}), 404
            
        stack = scheduler_schedule_stacks[publish_destination]
        if position < 0 or position >= len(stack):
            return jsonify({"error": f"Invalid position {position}. Stack size: {len(stack)}"}), 400
            
        # Remove the schedule and its context
        stack.pop(position)
        if scheduler_contexts_stacks.get(publish_destination) and len(scheduler_contexts_stacks[publish_destination]) > position:
            scheduler_contexts_stacks[publish_destination].pop(position)
            
        # Update persisted state
        update_scheduler_state(
            publish_destination,
            schedule_stack=stack,
            context_stack=get_context_stack(publish_destination)
        )
        
        # If this was the active schedule and the scheduler is running, stop it
        if position == len(stack) and publish_destination in running_schedulers:
            stop_scheduler(publish_destination)
            scheduler_logs[publish_destination].append(
                f"[{datetime.now().strftime('%H:%M')}] Stopped scheduler - active schedule removed"
            )
            
        return jsonify({
            "status": "removed",
            "position": position,
            "stack_size": len(stack)
        })
        
    except Exception as e:
        error_msg = f"Error removing schedule at position {position}: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

@scheduler_bp.route("/api/schedulers/<publish_destination>/context/clear", methods=["POST"])
def api_clear_scheduler_context(publish_destination):
    try:
        # Get the context stack for the destination
        if publish_destination not in scheduler_contexts_stacks or not scheduler_contexts_stacks[publish_destination]:
            return jsonify({"error": "No context found for scheduler"}), 404
        # Reset the top context to default_context, but preserve publish_destination
        new_context = default_context()
        new_context["publish_destination"] = publish_destination
        scheduler_contexts_stacks[publish_destination][-1] = new_context
        # Persist the change
        update_scheduler_state(
            publish_destination,
            schedule_stack=scheduler_schedule_stacks.get(publish_destination, []),
            context_stack=scheduler_contexts_stacks[publish_destination]
        )
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Cleared context for scheduler")
        return jsonify({"status": "context_cleared", "destination": publish_destination})
    except Exception as e:
        error_msg = f"Error clearing scheduler context: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 500

# Correct the stop handler function - stops scheduler but doesn't unload
def handle_stop(instruction, context, now, output, publish_destination):
    msg = "Stop instruction received - stopping scheduler without unloading."
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
    
    # Return False because we don't want to unload - just stop
    return False  
