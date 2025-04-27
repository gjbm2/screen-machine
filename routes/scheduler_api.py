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

# Global storage for scheduler state
scheduler_bp = Blueprint("scheduler_bp", __name__)
running_schedulers = {}
scheduler_logs: Dict[str, List[str]] = {}
scheduler_schedule_stacks = {}  # Store stacks of schedules by destination
scheduler_contexts_stacks = {}  # Store stacks of contexts by destination
scheduler_states = {}  # Store paused state by destination
important_triggers = {}  # Store important triggers by destination
active_events = {}  # Store active events by destination

# Load schedule schema
with open(os.path.join(os.path.dirname(__file__), "scheduler", "schedule_schema.json")) as f:
    SCHEDULE_SCHEMA = json.load(f)

# === Context Initialization ===
def default_context():
    return {
        "vars": {},
        "last_generated": None
    }

# === Instruction Execution ===
def run_instruction(instruction: Dict[str, Any], context: Dict[str, Any], now: datetime, output: List[str], publish_destination: str):
    action = instruction["action"]
    log_msg = f"[{now.strftime('%H:%M')}] Running {action}"
    output.append(log_msg)
    info(log_msg)

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
        "set_var": handle_set_var
    }

    if action in handler_map:
        try:
            # Run the handler which will modify the context
            handler_map[action](instruction, context, now, output)
            
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
                    if key != "vars":
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
        except Exception as e:
            error_msg = f"[{now.strftime('%H:%M')}] Error in {action}: {str(e)}"
            error(error_msg)
            output.append(error_msg)
    else:
        error_msg = f"[{now.strftime('%H:%M')}] Unknown action: {action}"
        error(error_msg)
        output.append(error_msg)


def handle_random_choice(instruction, context, now, output):
    var = instruction["var"]
    choice = random.choice(instruction["choices"])
    context["vars"][var] = choice
    output.append(f"[{now.strftime('%H:%M')}] Randomly chose '{choice}' for var '{var}'.")


def handle_devise_prompt(instruction, context, now, output):
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


def handle_generate(instruction, context, now, output):
    prompt = context["vars"].get(instruction["prompt_var"])
    context["last_generated"] = "[image_path]"
    
    if "history" in instruction:
        history_var = instruction["history"]
        if history_var not in context["vars"]:
            context["vars"][history_var] = []
        # Append timestamp and prompt to history
        context["vars"][history_var].append({
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "prompt": prompt
        })
    
    output.append(f"[{now.strftime('%H:%M')}] Generated image from: '{prompt}'.")


def handle_animate(instruction, context, now, output):
    # Prepare the animation request
    data = {
        "data": {
            "targets": [context.get("last_generated")] if context.get("last_generated") else []
        }
    }
    
    # Make the request to the animate endpoint
    try:
        response = requests.post("http://localhost:5000/api/animate", json=data)
        if response.status_code == 202:
            output.append(f"[{now.strftime('%H:%M')}] Started animation")
        else:
            output.append(f"[{now.strftime('%H:%M')}] Failed to start animation: {response.text}")
    except Exception as e:
        output.append(f"[{now.strftime('%H:%M')}] Error starting animation: {str(e)}")


def handle_display(instruction, context, now, output):
    mode = instruction["mode"]
    # This is where we would call display endpoint
    # ...
    # end of roundtrip
    img = context.get("last_generated")
    result = f"Displayed ({mode}) image."
    output.append(f"[{now.strftime('%H:%M')}] {result}")


def handle_sleep(instruction, context, now, output):
    duration = instruction["duration"]
    output.append(f"[{now.strftime('%H:%M')}] Sleeping display for {duration} minutes.")


def handle_wait(instruction, context, now, output):
    duration = instruction["duration"]
    # If we're not already waiting, start the wait
    if "wait_until" not in context:
        wait_until = now + timedelta(minutes=duration)
        context["wait_until"] = wait_until
        output.append(f"[{now.strftime('%H:%M')}] Started waiting for {duration} minutes (until {wait_until.strftime('%H:%M')})")
        return False  # Don't unload yet - we're just starting the wait
    
    # If we are waiting, check if it's complete
    if now >= context["wait_until"]:
        output.append(f"[{now.strftime('%H:%M')}] Wait period complete")
        del context["wait_until"]  # Clear the wait state
        return True  # Signal that we can unload now
    
    # Still waiting
    remaining = (context["wait_until"] - now).total_seconds() / 60
    output.append(f"[{now.strftime('%H:%M')}] Still waiting, {remaining:.1f} minutes remaining")
    return False  # Don't unload while still waiting


def handle_unload(instruction, context, now, output):
    output.append(f"[{now.strftime('%H:%M')}] Unloading temporary schedule.")
    return True  # Signal that we should unload the temporary schedule


def handle_device_media_sync(instruction, context, now, output):
    # This is where we would call the device media sync endpoint
    # ...
    output.append(f"[{now.strftime('%H:%M')}] Syncing media with device")


def handle_device_wake(instruction, context, now, output):
    # This is where we would call the device wake endpoint
    # ...
    output.append(f"[{now.strftime('%H:%M')}] Waking device")


def handle_device_sleep(instruction, context, now, output):
    # This is where we would call the device sleep endpoint
    # ...
    output.append(f"[{now.strftime('%H:%M')}] Putting device to sleep")


def handle_set_var(instruction, context, now, output):
    var_name = instruction["var"]
    value = instruction["value"]
    context["vars"][var_name] = value
    output.append(f"[{now.strftime('%H:%M')}] Set {var_name} to {value}.")


# === Schedule Resolver ===
def resolve_schedule(schedule: Dict[str, Any], now: datetime, publish_destination: str) -> List[Dict[str, Any]]:
    debug(f"Resolving schedule at {now}")
    
    # First check for important triggers
    important_trigger = get_next_important_trigger(publish_destination)
    if important_trigger:
        info(f"Running important trigger from {important_trigger['triggered_at']}")
        return important_trigger.get("instructions", [])
    
    # If there are no triggers, return the root instructions
    if "triggers" not in schedule or not schedule["triggers"]:
        return schedule.get("instructions", [])
    
    date_str = now.strftime("%-d-%b")  # e.g., 25-Dec
    day_str = now.strftime("%A")       # e.g., Friday
    time_str = now.strftime("%H:%M")   # e.g., 08:00
    minute_of_day = now.hour * 60 + now.minute

    debug(f"Current date: {date_str}, day: {day_str}, time: {time_str}, minute of day: {minute_of_day}")

    # Check each trigger
    for trigger in schedule["triggers"]:
        trigger_type = trigger["type"]
        trigger_value = trigger["value"]
        
        if trigger_type == "day_of_year" and trigger_value == date_str:
            info(f"Matched day_of_year trigger for {date_str}")
            if trigger.get("urgent", False):
                info("Running urgent trigger immediately")
                return trigger.get("instructions", [])
            if trigger.get("important", False):
                add_important_trigger(publish_destination, trigger, now)
                continue
            return trigger.get("instructions", [])
            
        elif trigger_type == "day_of_week" and trigger_value == day_str:
            info(f"Matched day_of_week trigger for {day_str}")
            if trigger.get("urgent", False):
                info("Running urgent trigger immediately")
                return trigger.get("instructions", [])
            if trigger.get("important", False):
                add_important_trigger(publish_destination, trigger, now)
                continue
            return trigger.get("instructions", [])
            
        elif trigger_type == "time":
            if "window" in trigger:
                # Handle time window with repeat
                start = datetime.strptime(trigger["window"][0], "%H:%M").time()
                end = datetime.strptime(trigger["window"][1], "%H:%M").time()
                
                # Convert all times to minutes since midnight
                start_minutes = start.hour * 60 + start.minute
                end_minutes = end.hour * 60 + end.minute
                current_minutes = minute_of_day
                
                # Handle case where end time is on the next day
                if end_minutes < start_minutes:
                    end_minutes += 24 * 60
                    if current_minutes < start_minutes:
                        current_minutes += 24 * 60
                
                in_window = start_minutes <= current_minutes <= end_minutes
                
                if in_window and "repeat" in trigger:
                    repeat_interval = trigger["repeat"]
                    minutes_since_start = current_minutes - start_minutes
                    is_interval = minutes_since_start % repeat_interval == 0
                    
                    if is_interval:
                        info(f"Matched repeating time window at {time_str}")
                        if trigger.get("urgent", False):
                            info("Running urgent trigger immediately")
                            return trigger.get("instructions", [])
                        if trigger.get("important", False):
                            add_important_trigger(publish_destination, trigger, now)
                            continue
                        return trigger.get("instructions", [])
            else:
                # Handle single time point
                scheduled_time = datetime.strptime(trigger_value, "%H:%M").time()
                scheduled_minutes = scheduled_time.hour * 60 + scheduled_time.minute
                
                # Only execute if the scheduled time is in the future
                if scheduled_minutes > minute_of_day:
                    info(f"Found future trigger at {trigger_value}")
                    if trigger.get("urgent", False):
                        info("Running urgent trigger immediately")
                        return trigger.get("instructions", [])
                    if trigger.get("important", False):
                        add_important_trigger(publish_destination, trigger, now)
                        continue
                    return trigger.get("instructions", [])
                    
        elif trigger_type == "event":
            # Check if this event is active for this destination
            if (publish_destination in active_events and 
                trigger_value in active_events[publish_destination]):
                event_time = active_events[publish_destination][trigger_value]
                # Clear the event after it's been handled
                del active_events[publish_destination][trigger_value]
                info(f"Matched event trigger {trigger_value}")
                if trigger.get("urgent", False):
                    info("Running urgent trigger immediately")
                    return trigger.get("instructions", [])
                if trigger.get("important", False):
                    add_important_trigger(publish_destination, trigger, now)
                    continue
                return trigger.get("instructions", [])

    debug("No matching triggers found")
    return []


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

def start_scheduler(publish_destination: str, schedule: Dict[str, Any]):
    """Start a scheduler for the given destination with the provided schedule."""
    try:
        # Stop any existing scheduler for this destination
        if publish_destination in running_schedulers:
            stop_scheduler(publish_destination)
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Stopped existing scheduler")
            
        # Load existing state from disk
        state = load_scheduler_state(publish_destination)
        
        # Initialize in-memory state
        scheduler_states[publish_destination] = "running"
        scheduler_schedule_stacks[publish_destination] = [schedule]
        
        # Use existing context stack from disk if available, otherwise use default
        if state.get("context_stack"):
            scheduler_contexts_stacks[publish_destination] = state["context_stack"]
        else:
            scheduler_contexts_stacks[publish_destination] = [default_context()]
        
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
        
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Started new scheduler")
        
    except Exception as e:
        error_msg = f"Error starting scheduler: {str(e)}"
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        # Ensure state is cleaned up on error
        update_scheduler_state(
            publish_destination,
            schedule_stack=[],
            context_stack=[],
            state="stopped"
        )
        raise

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["POST"])
def api_start_scheduler(publish_destination):
    try:
        schedule = request.json
        debug(f"Received schedule for {publish_destination}: {json.dumps(schedule, indent=2)}")
        
        # Validate schedule structure
        if not isinstance(schedule, dict):
            error_msg = "Schedule must be a JSON object"
            error(error_msg)
            return jsonify({"error": error_msg}), 400
            
        valid_keys = ["time_of_day", "day_of_week", "day_of_year"]
        if not any(key in schedule for key in valid_keys):
            error_msg = f"Schedule must contain at least one of: {', '.join(valid_keys)}"
            error(error_msg)
            return jsonify({"error": error_msg}), 400

        start_scheduler(publish_destination, schedule)
        info(f"Successfully started scheduler for {publish_destination}")
        return jsonify({"status": "started", "destination": publish_destination})
    except Exception as e:
        error_msg = f"Error starting scheduler: {str(e)}"
        error(error_msg)
        return jsonify({"error": error_msg}), 400

@scheduler_bp.route("/api/schedulers/<publish_destination>", methods=["DELETE"])
def api_stop_scheduler(publish_destination):
    stop_scheduler(publish_destination)
    return jsonify({"status": "stopped", "destination": publish_destination})

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
        
        # Execute initial instructions
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
        
        # Update persisted state to stopped but preserve stacks
        current_schedule_stack = scheduler_schedule_stacks.get(publish_destination, [])
        current_context_stack = scheduler_contexts_stacks.get(publish_destination, [])
        
        update_scheduler_state(
            publish_destination,
            state="stopped",
            schedule_stack=current_schedule_stack,
            context_stack=current_context_stack
        )
        
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Stopped scheduler while preserving state")
        
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
        context["vars"][var_name] = var_value
        info(f"Set context variable {var_name}={var_value} for scheduler {publish_destination}")
        return jsonify({"status": "success", "var_name": var_name, "var_value": var_value})
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
            jsonschema.validate(instance=schedule, schema=SCHEDULE_SCHEMA)
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

        # Only start a new scheduler if one doesn't exist
        if publish_destination not in running_schedulers:
            start_scheduler(publish_destination, schedule)
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Started scheduler")
        else:
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Using existing scheduler")
            
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

def copy_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Create a deep copy of a context."""
    new_context = {
        "vars": {},
        "last_generated": context.get("last_generated")
    }
    
    # Deep copy the vars dictionary
    for key, value in context.get("vars", {}).items():
        if isinstance(value, list):
            # For lists (like history), make a deep copy
            new_context["vars"][key] = [dict(item) if isinstance(item, dict) else item for item in value]
        else:
            new_context["vars"][key] = value
            
    return new_context

# === Persistence Functions ===
def get_scheduler_storage_path(publish_destination: str) -> str:
    """Get the path to store scheduler data for a destination."""
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
    # try:
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
        
        # Ensure publish_destination is preserved
        if "publish_destination" in context:
            context_copy["publish_destination"] = context["publish_destination"]
                
        state_to_save["context_stack"].append(context_copy)
                
    with open(path, 'w') as f:
        json.dump(state_to_save, f, indent=2)
            
    #except Exception as e:
    #    error(f"Error saving scheduler state for {publish_destination}: {str(e)}")

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
            publish_destination = filename[:-5]  # Remove .json extension
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
            jsonschema.validate(instance=schedule, schema=SCHEDULE_SCHEMA)
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
