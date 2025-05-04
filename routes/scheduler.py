# === Core scheduler logic ===

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from utils.logger import info, error, debug
import json
import os
import random
import asyncio
import threading
from routes.utils import dict_substitute, build_schema_subs
from routes.scheduler_handlers import (
    handle_sleep, handle_wait, handle_unload, handle_device_media_sync,
    handle_device_wake, handle_device_sleep, handle_set_var, handle_stop,
    handle_random_choice, handle_devise_prompt, handle_generate, handle_animate, handle_display,
    handle_import_var, handle_export_var
)
from routes.scheduler_utils import (
    log_schedule, default_context, copy_context, 
    get_scheduler_storage_path, load_scheduler_state, save_scheduler_state, update_scheduler_state,
    get_context_stack, push_context, pop_context, get_current_context,
    extract_instructions, process_time_schedules,
    get_next_important_trigger, add_important_trigger, get_next_scheduled_action, log_next_scheduled_action,
    catch_up_on_important_actions,
    # Globals from scheduler_utils
    scheduler_logs, scheduler_schedule_stacks, scheduler_contexts_stacks, scheduler_states, running_schedulers
)

# === Event Loop Management ===
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

# Load schedule schema path
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "scheduler", "schedule.schema.json.j2")

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
    from routes.scheduler_utils import active_events
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

# === Instruction Execution ===
def run_instruction(instruction: Dict[str, Any], context: Dict[str, Any], now: datetime, output: List[str], publish_destination: str) -> bool:
    """Run a single instruction."""
    action = instruction.get("action")
    # Don't add running message to output - will be added by handlers with the result
    log_schedule(f"Running {action}", publish_destination, now)
    
    try:
        if action == "random_choice":
            return handle_random_choice(instruction, context, now, output, publish_destination)
        elif action == "devise_prompt":
            return handle_devise_prompt(instruction, context, now, output, publish_destination)
        elif action == "generate":
            return handle_generate(instruction, context, now, output, publish_destination) or False  # Ensure boolean
        elif action == "animate":
            return handle_animate(instruction, context, now, output, publish_destination) or False  # Ensure boolean
        elif action == "display":
            return handle_display(instruction, context, now, output, publish_destination)
        elif action == "sleep":
            return handle_sleep(instruction, context, now, output, publish_destination)
        elif action == "wait":
            return handle_wait(instruction, context, now, output, publish_destination)
        elif action == "unload":
            return handle_unload(instruction, context, now, output, publish_destination)
        elif action == "device-media-sync":
            return handle_device_media_sync(instruction, context, now, output, publish_destination)
        elif action == "device-wake":
            return handle_device_wake(instruction, context, now, output, publish_destination)
        elif action == "device-sleep":
            return handle_device_sleep(instruction, context, now, output, publish_destination)
        elif action == "set_var":
            return handle_set_var(instruction, context, now, output, publish_destination)
        elif action == "stop":
            return handle_stop(instruction, context, now, output, publish_destination)
        elif action == "import_var":
            return handle_import_var(instruction, context, now, output, publish_destination) or False  # Ensure boolean
        elif action == "export_var":
            return handle_export_var(instruction, context, now, output, publish_destination) or False  # Ensure boolean
        else:
            error_msg = f"Unknown action: {action}"
            output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
            log_schedule(error_msg, publish_destination, now)
            return False
    except Exception as e:
        error_msg = f"Error in {action}: {str(e)}"
        output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
        log_schedule(error_msg, publish_destination, now)
        error(f"Exception in run_instruction: {str(e)}")
        import traceback
        error(traceback.format_exc())
        return False  # Ensure we return False on exceptions

    # Update global context after execution
    context_stack = get_context_stack(publish_destination)
    if context_stack and len(context_stack) > 0:
        context_stack[-1] = context
        update_scheduler_state(publish_destination, context_stack=context_stack)
    
    return False  # Default return if the handler doesn't return a value

# === Scheduler Runtime ===
async def run_scheduler(schedule: Dict[str, Any], publish_destination: str, step_minutes: int = 1):
    try:
        # Initialize logs
        scheduler_logs[publish_destination] = []
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Starting scheduler")

        # Ensure scheduler_schedule_stacks and scheduler_contexts_stacks are initialized
        if publish_destination not in scheduler_schedule_stacks:
            scheduler_schedule_stacks[publish_destination] = []
        
        if publish_destination not in scheduler_contexts_stacks:
            scheduler_contexts_stacks[publish_destination] = []
            
        # Initialize stacks if they're empty
        if not scheduler_schedule_stacks[publish_destination]:
            scheduler_schedule_stacks[publish_destination] = [schedule]
            
        if not scheduler_contexts_stacks[publish_destination]:
            scheduler_contexts_stacks[publish_destination] = [{
                "vars": {},
                "publish_destination": publish_destination
            }]

        # Run initial check immediately
        now = datetime.now()
        current_minutes = now.hour * 60 + now.minute

        # Get current schedule and context from top of stacks
        current_schedule = scheduler_schedule_stacks[publish_destination][-1]
        current_context = scheduler_contexts_stacks[publish_destination][-1]
        
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
            
            # Get current context to check for stopping flag
            current_context = get_current_context(publish_destination)
            
            # Check if stopping flag is set (for normal mode stopping)
            if current_context.get("stopping") == True:
                # Clear the stopping flag to avoid recursive execution
                current_context["stopping"] = False
                
                # Execute final_instructions before stopping
                current_schedule = scheduler_schedule_stacks[publish_destination][-1]
                final_instructions = extract_instructions(current_schedule.get("final_actions", {}))
                
                if final_instructions:
                    info("Executing final actions before stopping scheduler")
                    log_msg = "Executing final instructions before stopping (normal mode)"
                    scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {log_msg}")
                    
                    for instr in final_instructions:
                        try:
                            should_unload = run_instruction(instr, current_context, now, scheduler_logs[publish_destination], publish_destination)
                            if should_unload:
                                break  # Skip remaining instructions if one requests unload
                        except Exception as e:
                            error_msg = f"Error running final instruction: {str(e)}"
                            scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {error_msg}")
                
                # Now stop the scheduler
                if publish_destination in running_schedulers:
                    log_msg = "Stopping scheduler after executing final instructions"
                    scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M')}] {log_msg}")
                    running_schedulers.pop(publish_destination, None)
                    scheduler_states[publish_destination] = "stopped"
                    update_scheduler_state(
                        publish_destination,
                        state="stopped"
                    )
                break  # Exit the scheduler loop
            
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

def start_scheduler(publish_destination: str, schedule: Dict[str, Any], *args, **kwargs) -> None:
    """
    Start a scheduler for a destination. This will first stop any existing scheduler.
    """
    try:
        info(f"Starting scheduler for {publish_destination}")
        # First stop any existing scheduler
        stop_scheduler(publish_destination)
        
        # Load existing context and schedule from state
        loaded_state = load_scheduler_state(publish_destination)
        
        # Initialize context and schedule stacks
        context_stack = loaded_state.get("context_stack", [])
        schedule_stack = loaded_state.get("schedule_stack", [])
        
        # Create a fresh context if none exists
        if not context_stack:
            context_stack = [{
                "vars": {},
                "publish_destination": publish_destination
            }]
        
        # Add the new schedule to the stack (or initialize it)
        if schedule_stack:
            schedule_stack.append(schedule)
        else:
            schedule_stack = [schedule]
        
        # Update global stacks
        scheduler_contexts_stacks[publish_destination] = context_stack
        scheduler_schedule_stacks[publish_destination] = schedule_stack
        scheduler_states[publish_destination] = "running"
        
        # Create coroutine for the scheduler
        coro = run_scheduler(schedule, publish_destination, *args, **kwargs)
        
        # Add to running schedulers
        loop = get_event_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        running_schedulers[publish_destination] = future
        
        # Update state
        update_scheduler_state(
            publish_destination,
            context_stack=context_stack,
            schedule_stack=schedule_stack,
            state="running"
        )
    except Exception as e:
        error_msg = f"Error starting scheduler: {str(e)}"
        info(error_msg)
        log_schedule(error_msg, publish_destination, datetime.now())

def initialize_schedulers_from_disk():
    """Initialize scheduler states from disk on startup."""
    from routes.utils import _load_json_once
    
    try:
        # Get the list of publish destinations
        dest_data = _load_json_once("publish_destinations", "publish-destinations.json")
        publish_destinations = []
        
        # Extract destination IDs
        if isinstance(dest_data, list):
            publish_destinations = [d["id"] for d in dest_data if isinstance(d, dict) and "id" in d]
        elif isinstance(dest_data, dict):
            publish_destinations = list(dest_data.keys())
            
        info(f"Found {len(publish_destinations)} publish destinations: {publish_destinations}")
        
        # Check for state files for each destination
        storage_dir = os.path.join(os.path.dirname(__file__), "scheduler")
        if not os.path.exists(storage_dir):
            info(f"Scheduler directory does not exist, creating: {storage_dir}")
            os.makedirs(storage_dir, exist_ok=True)
            return
            
        for publish_destination in publish_destinations:
            state_path = os.path.join(storage_dir, f"{publish_destination}.json")
            
            if os.path.exists(state_path):
                try:
                    info(f"Loading scheduler state for ID '{publish_destination}'")
                    state = load_scheduler_state(publish_destination)
                    
                    # Initialize the schedule stack
                    if "schedule_stack" in state and state["schedule_stack"]:
                        scheduler_schedule_stacks[publish_destination] = state["schedule_stack"]
                        scheduler_contexts_stacks[publish_destination] = state.get("context_stack", [])
                        scheduler_states[publish_destination] = state.get("state", "stopped")
                        
                        # Start the scheduler if it was running
                        if state["state"] == "running":
                            info(f"Auto-starting scheduler for {publish_destination} (state is 'running')")
                            start_scheduler(publish_destination, state["schedule_stack"][-1])
                        else:
                            info(f"Not auto-starting {publish_destination} (state is '{state['state']}')")
                    else:
                        # Initialize empty stacks if they don't exist
                        scheduler_schedule_stacks[publish_destination] = []
                        scheduler_contexts_stacks[publish_destination] = []
                        scheduler_states[publish_destination] = "stopped"
                        info(f"No schedule stack found for {publish_destination}")
                except Exception as e:
                    error(f"Error initializing scheduler for {publish_destination}: {str(e)}")
            else:
                info(f"No state file found for {publish_destination}")
    except Exception as e:
        error(f"Error in initialize_schedulers_from_disk: {str(e)}")

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
