# === Core scheduler logic ===

from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from utils.logger import info, error, debug
import json
import os
import random
import asyncio
import threading
from routes.utils import dict_substitute, build_schema_subs, _load_json_once
from routes.scheduler_handlers import (
    handle_sleep, handle_wait, handle_unload, handle_device_media_sync,
    handle_device_wake, handle_device_sleep, handle_set_var, handle_terminate,
    handle_random_choice, handle_generate, handle_animate, handle_display,
    handle_import_var, handle_export_var, handle_reason, handle_log, handle_throw_event, handle_device_standby, handle_purge, handle_publish
)
from routes.scheduler_utils import (
    log_schedule, default_context, copy_context, 
    get_scheduler_storage_path, load_scheduler_state, save_scheduler_state, update_scheduler_state,
    get_context_stack, push_context, pop_context, get_current_context,
    extract_instructions, process_time_schedules,
    get_next_important_trigger, add_important_trigger, get_next_scheduled_action, log_next_scheduled_action,
    catch_up_on_important_actions, process_instruction_jinja,
    # Globals from scheduler_utils
    scheduler_logs, scheduler_schedule_stacks, scheduler_contexts_stacks, scheduler_states, running_schedulers
)
import time
from routes.scheduler_queue import get_instruction_queue, check_urgent_events, process_triggers, clear_instruction_queue
from config import SCHEDULER_TICK_INTERVAL, SCHEDULER_TICK_BUFFER

# === Event Loop Management ===
# Per-destination event loops and threads
_event_loops: Dict[str, asyncio.AbstractEventLoop] = {}
_loop_threads: Dict[str, threading.Thread] = {}

# Legacy single-loop placeholder kept for test-suite compatibility.
# Some integration tests monkey-patch `routes.scheduler._event_loop`
# expecting it to exist.  It is **not** used by the production code.
_event_loop: Optional[asyncio.AbstractEventLoop] = None

def get_event_loop(dest_id: str = "_default") -> asyncio.AbstractEventLoop:
    """Get or create an event loop for *dest_id*.

    The *dest_id* argument is optional so that tests which monkey-patch this
    function with a zero-argument stub continue to work.  If the caller omits
    the parameter we fall back to a single shared identifier "_default".
    """
    if dest_id in _event_loops:
        return _event_loops[dest_id]
    
    # Create a new loop for this destination
    loop = asyncio.new_event_loop()
    
    def run_event_loop():
        asyncio.set_event_loop(loop)
        _event_loops[dest_id] = loop
        loop.run_forever()
        
    # Create and start a thread for this destination
    thread = threading.Thread(
        target=run_event_loop, 
        name=f"sched-loop-{dest_id}", 
        daemon=True
    )
    thread.start()
    
    # Store the thread
    _loop_threads[dest_id] = thread
    
    # Wait for the loop to be stored
    while dest_id not in _event_loops:
        pass
    
    return _event_loops[dest_id]

def stop_event_loop(dest_id: str) -> None:
    """Stop the event loop for a specific destination."""
    # Get the loop and thread
    loop = _event_loops.pop(dest_id, None)
    thread = _loop_threads.pop(dest_id, None)
    
    # Stop the loop if it exists
    if loop:
        loop.call_soon_threadsafe(loop.stop)
    
    # Join the thread with timeout
    if thread:
        thread.join(timeout=2.0)

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
def resolve_schedule(schedule: Dict[str, Any], now: datetime, publish_destination: str, include_initial_actions: bool = False, context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    # Add rate-limiting for debug logging
    if not hasattr(resolve_schedule, '_last_debug_log_time'):
        resolve_schedule._last_debug_log_time = {}
    
    should_log = False
    current_time = time.time()
    if publish_destination not in resolve_schedule._last_debug_log_time or \
       (current_time - resolve_schedule._last_debug_log_time.get(publish_destination, 0)) > 30:
        should_log = True
        resolve_schedule._last_debug_log_time[publish_destination] = current_time
    
    # Get the context if not provided
    if context is None:
        context = get_current_context(publish_destination) or {"vars": {}}
    
    # Ensure the vars dict exists in context
    if "vars" not in context:
        context["vars"] = {}
    
    if should_log:
        debug(f"Resolving schedule at {now}")
        
    # First check for important triggers
    important_trigger = get_next_important_trigger(publish_destination)
    if important_trigger:
        info(f"Running important trigger from {important_trigger['triggered_at']}")
        log_schedule(f"Executing important trigger that was scheduled at {important_trigger['triggered_at'].strftime('%H:%M')}", publish_destination, now)
        return extract_instructions(important_trigger.get("trigger_actions", {}))
        
    # Accumulate all instructions to execute
    all_instructions = []
    
    # Only execute initial actions if requested (on first scheduler run) or if there are no triggers
    initial_instructions = extract_instructions(schedule.get("initial_actions", {}))
    if include_initial_actions and initial_instructions:
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
    
    if should_log:
        debug(f"Current date: {date_str}, day: {day_str}, time: {time_str}, minute of day: {minute_of_day}")
        
    # Track if we've matched any trigger
    matched_any_trigger = False
    found_actions_to_execute = False
    
    # Keep track of matched schedules to avoid duplicate processing
    processed_schedule_ids = set()
    
    # Keep track of all matched trigger/instruction data with urgency information
    all_trigger_data = []

    # First check all date triggers (these take precedence)
    for trigger in schedule.get("triggers", []):
        if trigger["type"] == "date" and "date" in trigger:
            if trigger["date"] == date_str:
                matched_any_trigger = True
                # Process time schedules for this date
                matched_schedules = process_time_schedules(
                    trigger.get("scheduled_actions", []),
                    now,
                    minute_of_day,
                    publish_destination,
                    apply_grace_period=include_initial_actions  # Only apply grace period for initial run
                )
                if matched_schedules:
                    found_actions_to_execute = True
                    message = f"Matched date trigger for {date_str} with actions to execute"
                    info(message)
                    log_schedule(message, publish_destination, now)

                    # Check if this trigger is marked as urgent or important
                    # First check trigger level, then fallback to global trigger level
                    trigger_actions = trigger.get("trigger_actions", {})
                    is_urgent = trigger_actions.get("urgent", trigger.get("urgent", False))
                    is_important = trigger_actions.get("important", trigger.get("important", False))
                    
                    if is_urgent:
                        debug(f"Date trigger for {date_str} is marked as URGENT - will interrupt wait states")
                    if is_important:
                        debug(f"Date trigger for {date_str} is marked as IMPORTANT - will not be removed by other urgent actions")

                    # Extract instructions from matched schedules
                    for matched_schedule in matched_schedules:
                        # Generate a unique ID for this schedule to avoid duplicates
                        schedule_id = id(matched_schedule)
                        if schedule_id not in processed_schedule_ids:
                            processed_schedule_ids.add(schedule_id)
                            instructions = extract_instructions(matched_schedule.get("trigger_actions", {}))
                            if should_log:
                                debug(f"Extracted {len(instructions)} instructions from date trigger schedule")
                                
                            # Also check if the individual schedule has urgency flags that override the parent trigger
                            schedule_urgent = matched_schedule.get("urgent", is_urgent)
                            schedule_important = matched_schedule.get("important", is_important)
                            
                            # Add to all_trigger_data with urgency/importance flags
                            all_trigger_data.append({
                                "block": instructions,
                                "urgent": schedule_urgent, 
                                "important": schedule_important,
                                "source": f"date:{date_str}"
                            })
                        else:
                            if should_log:
                                debug(f"Skipping duplicate schedule in date trigger")
    
    # === Day-of-week triggers (always evaluated, even if a date trigger matched) ===
    for trigger in schedule.get("triggers", []):
        if trigger["type"] == "day_of_week" and "days" in trigger:
            if day_str in trigger["days"]:
                matched_any_trigger = True  # We did match at least one trigger today
                # Process time schedules for this day
                matched_schedules = process_time_schedules(
                    trigger.get("scheduled_actions", []),
                    now,
                    minute_of_day,
                    publish_destination,
                    apply_grace_period=include_initial_actions  # Only apply grace period for initial run
                )
                if matched_schedules:
                    found_actions_to_execute = True
                    message = f"Matched day_of_week trigger for {day_str} with actions to execute"
                    info(message)
                    log_schedule(message, publish_destination, now)
                    
                    # Check if this trigger is marked as urgent or important
                    # First check trigger level, then fallback to global trigger level
                    trigger_actions = trigger.get("trigger_actions", {})
                    is_urgent = trigger_actions.get("urgent", trigger.get("urgent", False))
                    is_important = trigger_actions.get("important", trigger.get("important", False))
                    
                    if is_urgent:
                        debug(f"Day-of-week trigger for {day_str} is marked as URGENT - will interrupt wait states")
                    if is_important:
                        debug(f"Day-of-week trigger for {day_str} is marked as IMPORTANT - will not be removed by other urgent actions")
                    
                    # Extract instructions from matched schedules
                    for matched_schedule in matched_schedules:
                        # Generate a unique ID for this schedule to avoid duplicates
                        schedule_id = id(matched_schedule)
                        if schedule_id not in processed_schedule_ids:
                            processed_schedule_ids.add(schedule_id)
                            instructions = extract_instructions(matched_schedule.get("trigger_actions", {}))
                            if should_log:
                                debug(f"Extracted {len(instructions)} instructions from day_of_week trigger schedule")
                                
                            # Also check if the individual schedule has urgency flags that override the parent trigger
                            schedule_urgent = matched_schedule.get("urgent", is_urgent)
                            schedule_important = matched_schedule.get("important", is_important)
                            
                            # Add to all_trigger_data with urgency/importance flags
                            all_trigger_data.append({
                                "block": instructions,
                                "urgent": schedule_urgent, 
                                "important": schedule_important,
                                "source": f"day_of_week:{day_str}"
                            })
                        else:
                            if should_log:
                                debug(f"Skipping duplicate schedule in day_of_week trigger")
    
    # Check event triggers (these can match regardless of other triggers)
    from routes.scheduler_utils import pop_next_event, active_events
    
    # Debug: Print all available events for this destination - but only when should_log is true
    if should_log and publish_destination in active_events:
        for evt_key, evt_queue in active_events[publish_destination].items():
            if len(evt_queue) > 0:  # Only log if there are actual events
                debug(f"Available event key in {publish_destination}: {evt_key} with {len(evt_queue)} events")
    
    for trigger in schedule.get("triggers", []):
        # Only log trigger processing when it's an important trigger (not day_of_week)
        if trigger["type"] != "day_of_week" and should_log:
            debug(f"Processing trigger: {trigger.get('type')} - {trigger.get('value', 'no value')}")
            
        if trigger["type"] == "event" and "value" in trigger:
            event_key = trigger["value"]
            
            # Only log if should_log is true
            if should_log:
                debug(f"Looking for event trigger '{event_key}' in {publish_destination}")
                # Add detailed debugging for trigger definition
                debug(f"Event trigger definition: urgent={trigger.get('urgent', False)}, important={trigger.get('important', False)}")
            
            # Check if this event is active for this destination
            try:
                # Only log debug info when enabled
                if should_log and publish_destination in active_events and event_key in active_events[publish_destination]:
                    debug(f"Found event queue for '{event_key}' with {len(active_events[publish_destination][event_key])} entries")
                
                # Try getting an event - use event_trigger_mode=True to prioritize consumption
                event_entry = pop_next_event(publish_destination, event_key, now, event_trigger_mode=True)
                
                if event_entry:
                    matched_any_trigger = True
                    
                    # Check if this trigger is marked as urgent or important
                    # Determine urgency/importance from trigger_actions first, then trigger-level fallback
                    trigger_actions = trigger.get("trigger_actions", {})
                    is_urgent = trigger_actions.get("urgent", trigger.get("urgent", False))
                    is_important = trigger_actions.get("important", trigger.get("important", False))
                    
                    # Debug the actual trigger info directly from source
                    debug(f"*** EVENT MATCH: '{event_key}' - Extracted flags from trigger_actions or trigger: urgent={is_urgent}, important={is_important}")
                    debug(f"*** Source: urgent from trigger_actions={trigger_actions.get('urgent')}, from trigger={trigger.get('urgent')}")
                    debug(f"*** Source: important from trigger_actions={trigger_actions.get('important')}, from trigger={trigger.get('important')}")
                    
                    # Create message with importance/urgency flags
                    flags = []
                    if is_important:
                        flags.append("Important")
                    if is_urgent:
                        flags.append("Urgent")
                    
                    # Format message with flags if any
                    flags_str = f" {' '.join(flags)}" if flags else ""
                    message = f"Matched{flags_str} event trigger: {event_key}"
                    if event_entry.payload:
                        message += f" with payload {event_entry.payload}"
                    info(message)
                    log_schedule(message, publish_destination, now)
                    
                    # Store the event payload in the context as _event for use in jinja templates
                    context["vars"]["_event"] = {
                        "key": event_key,
                        "payload": event_entry.payload,
                        "unique_id": event_entry.unique_id,
                        "created_at": event_entry.created_at.isoformat() if event_entry.created_at else None,
                        "display_name": event_entry.display_name
                    }
                    # Also store at top level for process_jinja_template to find it
                    context["_event"] = context["vars"]["_event"]
                    debug(f"Added event payload to context as _event: {context['vars']['_event']}")
                    
                    # Get the trigger actions
                    trigger_actions = trigger.get("trigger_actions", {})
                    if should_log:
                        debug(f"Trigger actions: {trigger_actions}")
                    
                    # Extract instructions from trigger_actions
                    event_instructions = extract_instructions(trigger_actions)
                    debug(f"Event block contains {len(event_instructions)} instructions that will all have access to this event data")
                    if should_log:
                        debug(f"Extracted {len(event_instructions)} instructions from event trigger")
                        if not event_instructions:
                            debug(f"WARNING: No instructions found in event trigger actions: {trigger_actions}")
                    
                    # Log the final urgency/importance flags (computed above from trigger_actions or trigger)
                    debug(f"Event trigger '{event_key}' final flags: urgent={is_urgent}, important={is_important}")

                    if is_urgent:
                        debug(f"Event trigger '{event_key}' is marked as URGENT - will interrupt wait states")
                    if is_important:
                        debug(f"Event trigger '{event_key}' is marked as IMPORTANT - will not be removed by other urgent actions")
                    
                    # Add to all_trigger_data with urgency/importance flags
                    all_trigger_data.append({
                        "block": event_instructions,
                        "urgent": is_urgent, 
                        "important": is_important,
                        "source": f"event:{event_key}"
                    })
                    
                    found_actions_to_execute = True
                # Don't log "No active events" messages for normal event checking
            except Exception as e:
                error_msg = f"Error processing event trigger {event_key}: {str(e)}"
                error(error_msg)
                import traceback
                error(f"Traceback: {traceback.format_exc()}")

    # If no instructions were generated by ANY trigger, run final actions (if any)
    if not found_actions_to_execute:
        final_instructions = extract_instructions(schedule.get("final_actions", {}))
        if final_instructions:
            if should_log:
                debug("No trigger produced actions, running final actions")
            log_schedule("No triggers produced actions, running final actions", publish_destination, now)
            
            # Add to all_trigger_data - these are never urgent
            all_trigger_data.append({
                "block": final_instructions,
                "urgent": False,
                "important": False,
                "source": "final_actions"
            })
    
    if should_log:
        if not all_trigger_data:
            debug("No matching triggers or final actions found")
        else:
            total_instructions = sum(len(data.get("block", [])) for data in all_trigger_data)
            debug(f"Found {total_instructions} instructions to execute from {len(all_trigger_data)} trigger sources")
        
    return all_trigger_data

# === Instruction Execution ===
def run_instruction(instruction: Dict[str, Any], context: Dict[str, Any], now: datetime, output: List[str], publish_destination: str) -> bool:
    """Run a single instruction."""
    # Add rate-limiting for debug logging
    if not hasattr(run_instruction, '_last_debug_log_time'):
        run_instruction._last_debug_log_time = {}
    
    should_log_debug = False
    current_time = time.time()
    if publish_destination not in run_instruction._last_debug_log_time or \
       (current_time - run_instruction._last_debug_log_time.get(publish_destination, 0)) > 30:
        should_log_debug = True
        run_instruction._last_debug_log_time[publish_destination] = current_time
    
    # Ensure event data is accessible at the top level 
    # This is needed for proper Jinja processing of templates like {{ _event.payload.wait }}
    if "vars" in context and "_event" in context["vars"] and "_event" not in context:
        context["_event"] = context["vars"]["_event"]
        if should_log_debug:
            debug(f"Copied _event from vars to context root for Jinja processing")
    elif "_event" in context and should_log_debug:
        debug(f"Context already has _event at root level: {context['_event']}")
    
    # Process the instruction with Jinja templating at RUNTIME (just before execution)
    # This ensures variables set by previous instructions are available
    processed_instruction = process_instruction_jinja(instruction, context, publish_destination)
    
    action = processed_instruction.get("action")
    
    # Throttle logging for wait instructions
    # Use a static dictionary to track last log time per destination
    if not hasattr(run_instruction, '_last_wait_log_times'):
        run_instruction._last_wait_log_times = {}
    
    should_log_action = True
    
    # For wait actions specifically, only log at most once every 30 seconds per destination
    if action == "wait" and "wait_until" in context:
        current_time = time.time()
        last_log_time = run_instruction._last_wait_log_times.get(publish_destination, 0)
        if current_time - last_log_time < 30.0:  # Only log every 30 seconds
            should_log_action = False
        else:
            run_instruction._last_wait_log_times[publish_destination] = current_time
    
    # Don't add running message to output - will be added by handlers with the result
    if should_log_action:
        log_schedule(f"Running {action}", publish_destination, now, output)
        if should_log_debug:
            debug(f"[INSTRUCTION] Running {action} for {publish_destination}")
    
    try:
        # Store the result instead of immediately returning it
        result = False
        if action == "random_choice":
            result = handle_random_choice(processed_instruction, context, now, output, publish_destination)
        elif action == "generate":
            result = handle_generate(processed_instruction, context, now, output, publish_destination) or False  # Ensure boolean
        elif action == "animate":
            result = handle_animate(processed_instruction, context, now, output, publish_destination) or False  # Ensure boolean
        elif action == "display":
            result = handle_display(processed_instruction, context, now, output, publish_destination)
        elif action == "sleep":
            result = handle_sleep(processed_instruction, context, now, output, publish_destination)
        elif action == "wait":
            result = handle_wait(processed_instruction, context, now, output, publish_destination)
        elif action == "unload":
            result = handle_unload(processed_instruction, context, now, output, publish_destination)
        elif action == "device-media-sync":
            result = handle_device_media_sync(processed_instruction, context, now, output, publish_destination)
        elif action == "device-wake":
            result = handle_device_wake(processed_instruction, context, now, output, publish_destination)
        elif action == "device-sleep":
            result = handle_device_sleep(processed_instruction, context, now, output, publish_destination)
        elif action == "set_var":
            result = handle_set_var(processed_instruction, context, now, output, publish_destination)
        elif action == "terminate":
            result = handle_terminate(processed_instruction, context, now, output, publish_destination)
        elif action == "import_var":
            result = handle_import_var(processed_instruction, context, now, output, publish_destination) or False  # Ensure boolean
        elif action == "export_var":
            result = handle_export_var(processed_instruction, context, now, output, publish_destination) or False  # Ensure boolean
        elif action == "reason":
            result = handle_reason(processed_instruction, context, now, output, publish_destination)
        elif action == "log":
            result = handle_log(processed_instruction, context, now, output, publish_destination)
        elif action == "throw_event" or action == "throw":
            result = handle_throw_event(processed_instruction, context, now, output, publish_destination)
        elif action == "device_standby":
            result = handle_device_standby(processed_instruction, context, now, output, publish_destination)
        elif action == "purge":
            result = handle_purge(processed_instruction, context, now, output, publish_destination)
        elif action == "publish":
            result = handle_publish(processed_instruction, context, now, output, publish_destination)
        else:
            error_msg = f"Unknown action: {action}"
            output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
            log_schedule(error_msg, publish_destination, now, output)
            result = False
    except Exception as e:
        error_msg = f"Error in {action}: {str(e)}"
        output.append(f"[{now.strftime('%H:%M')}] {error_msg}")
        log_schedule(error_msg, publish_destination, now, output)
        error(f"Exception in run_instruction: {str(e)}")
        import traceback
        error(traceback.format_exc())
        result = False  # Ensure we return False on exceptions
    
    # Update global context after execution
    context_stack = get_context_stack(publish_destination)
    if context_stack and len(context_stack) > 0:
        # Store the updated context back in the context stack
        context_stack[-1] = context
        if should_log_debug:
            debug(f"[PERSISTENCE] After instruction {action}, saving context for {publish_destination}")
            if "vars" in context:
                debug(f"[PERSISTENCE] Context vars: {list(context.get('vars', {}).keys())}")
        update_scheduler_state(publish_destination, context_stack=context_stack)
    else:
        if should_log_debug:
            debug(f"[PERSISTENCE] Warning: No context stack found for {publish_destination} after {action}")
    
    # Propagate EXIT_BLOCK if returned
    if result == "EXIT_BLOCK":
        return "EXIT_BLOCK"
    return result  # Return the saved result

# === Scheduler Runtime ===
async def run_scheduler(schedule: Dict[str, Any], publish_destination: str, step_minutes: int = 1):
    try:
        # Initialize logs
        scheduler_logs[publish_destination] = []
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Starting scheduler")

        # Use the existing schedule stack, or initialize if missing
        if publish_destination not in scheduler_schedule_stacks:
            scheduler_schedule_stacks[publish_destination] = []
        schedule_stack = scheduler_schedule_stacks[publish_destination]
        
        # Ensure scheduler_schedule_stacks and scheduler_contexts_stacks are initialized
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

        # Get current schedule and context from top of stacks
        current_schedule = scheduler_schedule_stacks[publish_destination][-1]
        current_context = scheduler_contexts_stacks[publish_destination][-1]
        
        # Check if there are no triggers - this means we should run initial actions, then final actions, then stop
        has_no_triggers = "triggers" not in current_schedule or not current_schedule["triggers"]
        
        # Execute initial instructions
        initial_instructions = resolve_schedule(current_schedule, datetime.now(), publish_destination, include_initial_actions=True, context=current_context)
        if initial_instructions:
            info("Executing initial actions")
            # Flatten and process all instruction blocks
            for trigger_data in initial_instructions:
                block = trigger_data.get("block", [])
                is_urgent = trigger_data.get("urgent", False)
                is_important = trigger_data.get("important", False)
                source = trigger_data.get("source", "unknown")
                
                # Log what we're processing
                if is_urgent or is_important:
                    flags = []
                    if is_urgent:
                        flags.append("urgent")
                    if is_important:
                        flags.append("important")
                    flags_str = f" ({', '.join(flags)})" if flags else ""
                    debug(f"Processing instruction block from {source}{flags_str}")
                    
                for instr in block:
                    try:
                        should_unload = run_instruction(instr, current_context, datetime.now(), scheduler_logs[publish_destination], publish_destination)
                        if should_unload == "EXIT_BLOCK":
                            debug(f"EXIT_BLOCK signal received, breaking out of instruction block early")
                            break  # Exit the current instruction block
                    except Exception as e:
                        error_msg = f"Error running instruction: {str(e)}"
                        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        
        # If no triggers, run final actions and stop
        if has_no_triggers:
            # Run final actions immediately
            final_instructions = extract_instructions(current_schedule.get("final_actions", {}))
            if final_instructions:
                info("Executing final actions (no triggers defined)")
                for instr in final_instructions:
                    try:
                        should_unload = run_instruction(instr, current_context, datetime.now(), scheduler_logs[publish_destination], publish_destination)
                        if should_unload == "EXIT_BLOCK":
                            debug(f"EXIT_BLOCK signal received, breaking out of instruction block early")
                            break  # Exit the current instruction block
                        if should_unload:
                            break  # Skip remaining instructions if one requests unload
                    except Exception as e:
                        error_msg = f"Error running final instruction: {str(e)}"
                        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
            
            # No triggers means we're done after running initial and final actions
            scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] No triggers defined, stopping scheduler after running all actions")
            
            # Remove this scheduler from running schedulers
            if publish_destination in running_schedulers:
                running_schedulers.pop(publish_destination, None)
                scheduler_states[publish_destination] = "stopped"
                debug(f"Set {publish_destination} to stopped - no triggers or actions in schedule")
                update_scheduler_state(
                    publish_destination,
                    state="stopped"
                )
            return
        
        # Continue with the main scheduler loop
        await run_scheduler_loop(schedule, publish_destination, step_minutes)
            
    except asyncio.CancelledError:
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] Scheduler cancelled")
        raise
    except Exception as e:
        error_msg = f"Error in scheduler loop: {str(e)}"
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M')}] {error_msg}")
        raise

def start_scheduler(publish_destination: str, schedule: Dict[str, Any], *args, **kwargs) -> None:
    """
    Start a scheduler with a given schedule.
    
    :param publish_destination: The destination ID to start the scheduler for
    :param schedule: The schedule dictionary containing triggers, actions, etc.
    """
    try:
        info(f"Starting scheduler for destination '{publish_destination}'")
        
        # Check if already running
        if publish_destination in running_schedulers:
            future = running_schedulers[publish_destination]["future"]
            if not future.done() and not future.cancelled():
                error(f"Scheduler already running for {publish_destination}")
                raise ValueError(f"Scheduler already running for {publish_destination}")
        
        # Validate schedule has required structure
        if not isinstance(schedule, dict):
            raise ValueError("Schedule must be a dictionary")

        # ------------------------------------------------------------------
        # HISTORIC-CORRECT STACK HANDLING
        # ------------------------------------------------------------------
        # We only create a new stack if none exists (fresh destination) or
        # if the existing stack is empty.  Otherwise we leave it untouched.
        # *NO* append, *NO* replace – just use what is there.
        if publish_destination not in scheduler_schedule_stacks:
            scheduler_schedule_stacks[publish_destination] = []
        if not scheduler_schedule_stacks[publish_destination]:
            # Fresh destination – seed stack with the provided schedule
            scheduler_schedule_stacks[publish_destination].append(schedule)
        schedule_stack = scheduler_schedule_stacks[publish_destination]
        
        # Get the context stack - ensure it's not empty
        context_stack = scheduler_contexts_stacks.get(publish_destination, [])
        if not context_stack:
            info(f"No context found for {publish_destination}, creating default context")
            default_ctx = default_context()
            default_ctx["publish_destination"] = publish_destination
            context_stack = [default_ctx]
            scheduler_contexts_stacks[publish_destination] = context_stack
        
        # Update the global stacks
        scheduler_schedule_stacks[publish_destination] = schedule_stack
        
        # Update scheduler state
        scheduler_states[publish_destination] = "running"
        update_scheduler_state(
            publish_destination, 
            schedule_stack=schedule_stack,
            context_stack=context_stack,
            state="running"
        )
        
        # Get (or create) the event loop for this destination.  Some unit tests
        # monkey-patch `get_event_loop` with a zero-argument stub, so we fall
        # back to calling it without arguments if the first call raises a
        # *TypeError*.
        try:
            loop = get_event_loop(publish_destination)
        except TypeError:
            loop = get_event_loop()
        except Exception as e:
            error(f"Failed to get event loop for {publish_destination}: {str(e)}")
            raise
        
        # CRITICAL: Set placeholder in running_schedulers BEFORE starting coroutine
        # This prevents the race condition where run_scheduler_loop() checks running_schedulers
        # before the entry exists, causing immediate exit
        running_schedulers[publish_destination] = {
            "future": None,  # Will be updated below
            "loop": loop
        }
        
        # Schedule the coroutine to run in the background
        future = asyncio.run_coroutine_threadsafe(
            run_scheduler(schedule_stack[-1], publish_destination, *args, **kwargs),
            loop
        )
        
        # Update the placeholder with the actual future
        running_schedulers[publish_destination]["future"] = future
        
        info(f"Scheduler for {publish_destination} started successfully")
    except Exception as e:
        error(f"Error starting scheduler for {publish_destination}: {str(e)}")
        # Reset state to stopped on error
        scheduler_states[publish_destination] = "stopped"
        import traceback
        error(traceback.format_exc())
        raise  # Re-raise to be caught by API handler

def initialize_schedulers_from_disk():
    """Initialize scheduler state from disk.
    
    This includes loading all saved scheduler states and restoring any running schedulers.
    """
    info("Initializing schedulers from disk")
    schedulers_dir = os.path.join(os.getcwd(), "data", "schedulers")
    
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
            debug(f"Processing destination: {publish_destination}")
            state_path = os.path.join(storage_dir, f"{publish_destination}.json")
            
            if os.path.exists(state_path):
                try:
                    debug(f"State file exists for {publish_destination}, loading")
                    info(f"Loading scheduler state for ID '{publish_destination}'")
                    state = load_scheduler_state(publish_destination)
                    
                    debug(f"Loaded state for {publish_destination}: state='{state.get('state', 'unknown')}'")
                    
                    # Initialize the schedule stack
                    if "schedule_stack" in state and state["schedule_stack"]:
                        debug(f"Schedule stack exists for {publish_destination}")
                        scheduler_schedule_stacks[publish_destination] = state["schedule_stack"]
                        scheduler_contexts_stacks[publish_destination] = state.get("context_stack", [])
                        saved_state = state.get("state", "stopped")
                        debug(f"Got state '{saved_state}' from file for {publish_destination}")
                        
                        scheduler_states[publish_destination] = saved_state
                        if saved_state == "stopped":
                            debug(f"Using default state 'stopped' for {publish_destination}")
                        debug(f"Set in-memory state to '{scheduler_states[publish_destination]}' for {publish_destination}")
                        
                        # Handle each state appropriately
                        if saved_state == "running":
                            debug(f"State is 'running' for {publish_destination}, will resume")
                            # Check if a scheduler is already running for this destination
                            if publish_destination in running_schedulers:
                                future = running_schedulers[publish_destination]
                                if not future.done() and not future.cancelled():
                                    info(f"Scheduler for {publish_destination} already running, not auto-resuming")
                                    continue
                                else:
                                    # Clean up stale entry
                                    running_schedulers.pop(publish_destination, None)
                            
                            info(f"Auto-resuming scheduler for {publish_destination} (state is 'running')")
                            schedule = state["schedule_stack"][-1]
                            
                            # Resume without re-running initial instructions
                            debug(f"Calling resume_scheduler for {publish_destination}")
                            resume_scheduler(publish_destination, schedule)
                        elif saved_state == "paused":
                            debug(f"State is 'paused' for {publish_destination}, NOT resuming")
                            info(f"Preserving paused state for {publish_destination} (not starting scheduler)")
                            # DON'T CALL resume_scheduler for paused schedulers - this was causing the bug
                            # Just make sure the state is correctly set
                            scheduler_states[publish_destination] = "paused"
                            debug(f"Re-set state to 'paused' for {publish_destination}")
                            update_scheduler_state(
                                publish_destination,
                                state="paused"
                            )
                            debug(f"After update_scheduler_state, state is '{scheduler_states[publish_destination]}' for {publish_destination}")
                        else:  # "stopped" or any other state
                            debug(f"State is '{saved_state}' for {publish_destination}, not starting")
                            info(f"Not auto-starting {publish_destination} (state is '{saved_state}')")
                    else:
                        debug(f"No schedule stack for {publish_destination}")
                        # Initialize empty stacks if they don't exist
                        scheduler_schedule_stacks[publish_destination] = []
                        scheduler_contexts_stacks[publish_destination] = []
                        scheduler_states[publish_destination] = "stopped"
                        debug(f"Setting to stopped state for {publish_destination} - no schedule stack")
                        info(f"No schedule stack found for {publish_destination}")
                except Exception as e:
                    debug(f"Failed to initialize {publish_destination}: {str(e)}")
                    error(f"Error initializing scheduler for {publish_destination}: {str(e)}")
            else:
                debug(f"No state file found for {publish_destination}")
                info(f"No state file found for {publish_destination}")
        
        debug(f"Final states after initialization: {scheduler_states}")
    except Exception as e:
        debug(f"Global exception during initialization: {str(e)}")
        error(f"Error in initialize_schedulers_from_disk: {str(e)}")
        import traceback
        error(traceback.format_exc())

def resume_scheduler(publish_destination: str, schedule: Dict[str, Any]) -> None:
    """
    Resume a scheduler without running initial instructions.
    This is used when restoring a scheduler that was already in the running state.
    """
    try:
        info(f"Resuming scheduler for {publish_destination} without running initial instructions")
        
        # Check if a scheduler is already running for this destination
        if publish_destination in running_schedulers:
            scheduler_info = running_schedulers[publish_destination]
            future = scheduler_info["future"] if isinstance(scheduler_info, dict) else scheduler_info
            if not future.done() and not future.cancelled():
                info(f"Scheduler for {publish_destination} already running, not resuming")
                return
            else:
                # Clean up stale entry
                info(f"Found stale scheduler entry for {publish_destination}, cleaning up")
                running_schedulers.pop(publish_destination, None)
        
        # Ensure the in-memory state reflects what we loaded from disk
        scheduler_states[publish_destination] = "running"
        update_scheduler_state(
            publish_destination, 
            state="running"
        )
        
        # Get (or create) the event loop for this destination.  Some unit tests
        # monkey-patch `get_event_loop` with a zero-argument stub, so we fall
        # back to calling it without arguments if the first call raises a
        # *TypeError*.
        try:
            loop = get_event_loop(publish_destination)
        except TypeError:
            loop = get_event_loop()
        
        # Create a special scheduler coroutine that doesn't run initial instructions
        async def resume_scheduler_without_initial():
            # Skip running initial instructions - start the main scheduler loop directly
            try:
                await run_scheduler_loop(schedule, publish_destination)
            except Exception as e:
                error(f"Error in resumed scheduler: {str(e)}")
                import traceback
                error(traceback.format_exc())
        
        # CRITICAL: Set placeholder in running_schedulers BEFORE starting coroutine
        # This prevents the race condition where run_scheduler_loop() checks running_schedulers
        # before the entry exists, causing immediate exit
        running_schedulers[publish_destination] = {
            "future": None,  # Will be updated below
            "loop": loop
        }
        
        # Schedule the coroutine to run in the background
        future = asyncio.run_coroutine_threadsafe(
            resume_scheduler_without_initial(),
            loop
        )
        
        # Update the placeholder with the actual future
        running_schedulers[publish_destination]["future"] = future
        
        info(f"Scheduler for {publish_destination} resumed successfully")
    except Exception as e:
        error(f"Error resuming scheduler: {str(e)}")
        import traceback
        error(traceback.format_exc())

async def run_scheduler_loop(schedule: Dict[str, Any], publish_destination: str, step_minutes: int = 1):
    """
    Main scheduler loop that processes instructions and handles events.
    """
    # Initialize debug logging control
    should_log_debug = True  # Default to True to maintain existing debug output
    
    try:
        # Initialize instruction queue for this destination if it doesn't exist
        instruction_queue = get_instruction_queue(publish_destination)
        
        # Initialize logs if not already initialized
        if publish_destination not in scheduler_logs:
            scheduler_logs[publish_destination] = []
        
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M:%S')}] Resuming scheduler (skipping initial instructions)")
        
        # We no longer need to wait for start of next minute - this allows for sub-minute scheduling 
        
        last_check_time = None
        last_debug_log_time = 0  # Track when we last did debug logging
        last_expiration_check_time = 0  # Track when we last checked for expired events
        
        # Track if the scheduler is in a waiting state
        is_in_wait_state = False
        
        while True:
            # Check if scheduler is stopped
            if publish_destination not in running_schedulers:
                scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M:%S')}] Scheduler stopped")
                break
                
            # Check if scheduler is paused
            if scheduler_states.get(publish_destination) == "paused":
                # Rate-limit debug logging
                current_time = time.time()
                if current_time - last_debug_log_time > 30:
                    debug(f"Preserving context while scheduler is paused: {len(scheduler_contexts_stacks.get(publish_destination, []))} context(s)")
                    last_debug_log_time = current_time
                
                await asyncio.sleep(2.0)  # Sleep during pause state
                continue
                
            now = datetime.now()
            # Use epoch seconds to avoid negative deltas when the day rolls over
            current_epoch_second = time.time()
            
            # Get current context
            current_context = get_current_context(publish_destination)
            
            # Check if we're in waiting state (wait instruction is being executed)
            is_in_wait_state = current_context and "wait_until" in current_context
            
            # Rate-limit debug logging
            should_log_debug = False
            current_time = time.time()
            if current_time - last_debug_log_time > 30:
                should_log_debug = True
                last_debug_log_time = current_time
            
            # Periodically check for expired events (every 30 seconds)
            # This ensures expired events are properly moved to history even if not explicitly requested
            if current_time - last_expiration_check_time > 30:
                try:
                    from routes.scheduler_utils import check_all_expired_events
                    expired_counts = check_all_expired_events()
                    if expired_counts and should_log_debug:
                        debug(f"Checked for expired events: {expired_counts}")
                    last_expiration_check_time = current_time
                except Exception as e:
                    error(f"Error checking expired events: {str(e)}")
            
            # If this is our first loop or at least one tick interval has elapsed since the
            # previous schedule evaluation, process triggers and update the queue
            if last_check_time is None or (current_epoch_second - last_check_time) >= SCHEDULER_TICK_INTERVAL:
                # Get current schedule and context from top of stacks
                current_schedule = scheduler_schedule_stacks[publish_destination][-1]
                current_context = get_current_context(publish_destination)
                
                try:
                    # 1. Process triggers and add to queue
                    triggers = process_triggers(current_schedule, now, publish_destination, current_context)
                    for trigger in triggers:
                        block = trigger.get("block", [])
                        is_urgent = trigger.get("urgent", False)
                        is_important = trigger.get("important", False)
                        source = trigger.get("source", "unknown")

                        # During wait state:
                        # 1. URGENT triggers queue immediately and interrupt the wait
                        # 2. IMPORTANT (but non-urgent) triggers queue for execution after wait completes
                        # 3. Normal triggers (neither urgent nor important) are skipped during wait
                        #
                        # This applies to all trigger types: time-based, events, etc.
                        if is_in_wait_state and not is_urgent and not is_important:
                            if should_log_debug:
                                debug(
                                    f"WAIT STATE: Skipping normal trigger from {source} (will reevaluate after wait)"
                                )
                            # Add to scheduler logs (visible in UI)
                            log_schedule(
                                f"Skipping normal trigger from {source} during wait (will check again after wait finishes)",
                                publish_destination, now
                            )
                            continue
                        elif is_in_wait_state and is_important and not is_urgent:
                            if should_log_debug:
                                debug(
                                    f"WAIT STATE: Queuing important (non-urgent) trigger from {source} (will execute after wait)"
                                )
                            # Add to scheduler logs (visible in UI)
                            log_schedule(
                                f"Queued important trigger from {source} (will execute after wait completes)",
                                publish_destination, now
                            )
                        
                        # Debug log exactly what we're pushing to the queue
                        flags = []
                        if is_urgent:
                            flags.append("URGENT")
                        if is_important:
                            flags.append("IMPORTANT")
                        flags_str = f" [{', '.join(flags)}]" if flags else ""
                        debug(f"Adding instruction block from {source}{flags_str} to the queue with {len(block)} instructions")

                        instruction_queue.push_block(
                            block,
                            important=is_important,
                            urgent=is_urgent
                        )

                    # 2. Check for urgent events
                    urgent_event = check_urgent_events(publish_destination)
                    if urgent_event:
                        debug(f"Found special urgent event: {urgent_event['key']}")
                        instruction_queue.push_block(
                            urgent_event["block"],
                            important=True,
                            urgent=True
                        )
                except Exception as e:
                    error_msg = f"Error processing triggers: {str(e)}"
                    scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M:%S')}] {error_msg}")
                    import traceback
                    error(traceback.format_exc())
                
                # Store the epoch second so that the next trigger check isn't too soon
                last_check_time = current_epoch_second
            
            # 3. Execute instructions with special handling for wait state
            # If we're in waiting state, only process urgent instructions
            entry = None
            
            if is_in_wait_state:
                # Rate limit debug logs during wait states
                if not hasattr(run_scheduler_loop, '_wait_debug_logs'):
                    run_scheduler_loop._wait_debug_logs = {}
                
                wait_debug_key = f"wait_state_{publish_destination}"
                current_time = time.time()
                should_log_wait_state = False
                
                if wait_debug_key not in run_scheduler_loop._wait_debug_logs or \
                   (current_time - run_scheduler_loop._wait_debug_logs.get(wait_debug_key, 0)) > 30.0:
                    should_log_wait_state = True
                    run_scheduler_loop._wait_debug_logs[wait_debug_key] = current_time
                    
                    # When logging wait state, tell update_scheduler_state to also log (throttled)
                    if not hasattr(update_scheduler_state, '_last_wait_log_times'):
                        update_scheduler_state._last_wait_log_times = {}
                    update_scheduler_state._last_wait_log_times[publish_destination] = current_time
                    
                    # And also silence run_instruction debug logs except at intervals
                    if not hasattr(run_instruction, '_wait_debug_logs'):
                        run_instruction._wait_debug_logs = {}
                    run_instruction._wait_debug_logs[publish_destination] = current_time
                
                if should_log_wait_state:
                    debug(f"Scheduler in wait state, checking for urgent instructions that could interrupt")
                
                # First, update the wait to reflect passage of time
                if should_log_wait_state:
                    debug(f"Updating wait state status")
                should_unload = run_instruction({"action": "wait", "duration": 0}, current_context, now, 
                                              scheduler_logs[publish_destination], publish_destination)

                # Check if the wait has completed normally
                if should_unload:
                    debug(f"Wait completed normally")
                    is_in_wait_state = False
                    
                    # Now we can process next instruction as normal
                    entry = instruction_queue.pop_next()
                else:
                    # We're still in wait state - only check for urgent instructions
                    urgent_entry = instruction_queue.peek_next_urgent()
                    
                    # Log wait interrupt status for debugging
                    if urgent_entry:
                        # Found an urgent instruction - interrupt the wait
                        action_name = urgent_entry['instruction'].get('action', 'unknown')
                        # Always log wait interruptions regardless of rate limiting
                        debug(f"WAIT INTERRUPT: Found urgent instruction {action_name} that will interrupt wait state")
                        
                        # Clear the wait state
                        if "wait_until" in current_context:
                            previous_wait_until = current_context["wait_until"]
                            del current_context["wait_until"]
                            log_message = f"Wait interrupted by urgent {action_name} instruction (was waiting until {previous_wait_until.strftime('%H:%M:%S')})"
                            log_schedule(log_message, publish_destination, now, scheduler_logs[publish_destination])
                            
                            # Also remove last_wait_log if it exists
                            if "last_wait_log" in current_context:
                                del current_context["last_wait_log"]
                            
                            # Save context changes immediately
                            debug(f"Saving context after clearing wait state")
                            update_scheduler_state(publish_destination, context_stack=scheduler_contexts_stacks[publish_destination])
                            
                        # Get the entry that will be processed - ONLY get urgent instruction
                        entry = instruction_queue.pop_next(urgent_only=True)
                        if entry:
                            debug(f"Popped urgent instruction for immediate execution: {action_name}")
                            is_in_wait_state = False
                        else:
                            if should_log_wait_state:
                                debug(f"WARNING: Failed to pop urgent instruction that was previously found - something went wrong")
                            # We'll keep the wait state in this case since we couldn't pop the urgent entry
                    else:
                        if should_log_wait_state:
                            debug(f"No urgent instructions found that could interrupt the wait")
                        # Important: Don't process ANY instructions when in wait state, not even if queue appears empty
                        # This prevents normal instructions from being processed during wait
            else:
                # Normal operation - process next instruction
                entry = instruction_queue.pop_next()
            
            # Execute the instruction if we have one
            if entry:
                try:
                    instr = entry["instruction"]
                    important = entry["important"]
                    urgent = entry["urgent"]
                    
                    # Log what we're about to run
                    if should_log_debug:
                        flags = []
                        if important:
                            flags.append("important")
                        if urgent:
                            flags.append("urgent")
                        flags_str = f" ({', '.join(flags)})" if flags else ""
                        debug(f"Running instruction {instr.get('action', 'unknown')}{flags_str} from queue")
                    
                    # Run the instruction
                    should_unload = run_instruction(instr, current_context, now, scheduler_logs[publish_destination], publish_destination)
                    
                    # Check if we're entering wait state
                    if instr.get("action") == "wait" and not should_unload:
                        is_in_wait_state = "wait_until" in current_context
                    
                    # Handle stop/unload logic based on the return value
                    if should_unload == "EXIT_BLOCK":
                        # For EXIT_BLOCK, we remove all remaining non-important instructions from the queue
                        debug(f"EXIT_BLOCK signal received, breaking out of current instruction block")
                        instruction_queue.remove_non_important()
                    elif should_unload:
                        # For unload, clear the queue and pop the schedule
                        debug(f"Unload signal received, clearing queue and popping schedule")
                        clear_instruction_queue(publish_destination)
                        scheduler_schedule_stacks[publish_destination].pop()
                        pop_context(publish_destination)  # Pop the context too
                        scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M:%S')}] Unloaded schedule")
                        
                        # Sync state after unload
                        update_scheduler_state(
                            publish_destination,
                            schedule_stack=scheduler_schedule_stacks[publish_destination],
                            context_stack=scheduler_contexts_stacks[publish_destination]
                        )
                        
                        if not scheduler_schedule_stacks[publish_destination]:
                            scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M:%S')}] No schedules left in stack, stopping scheduler")
                            return
                except Exception as e:
                    error_msg = f"Error running instruction: {str(e)}"
                    scheduler_logs[publish_destination].append(f"[{now.strftime('%H:%M:%S')}] {error_msg}")
                    import traceback
                    error(traceback.format_exc())
            
                # After running an instruction, clean up any _event variables
                # Only remove _event data if:
                # 1. We're not in a wait state (already handled) AND
                # 2. There are no more instructions in the queue from this event
                # This ensures event data persists throughout the entire event-triggered instruction block
                if ("vars" in current_context and "_event" in current_context["vars"] and
                    not (instr and instr.get("action") == "wait" and not should_unload) and
                    instruction_queue.is_empty()):
                    payload_debug = "unknown"
                    if "_event" in current_context and isinstance(current_context["_event"], dict):
                        payload_debug = current_context["_event"].get("payload", "no payload")
                    debug(f"Removing temporary _event from context - instruction block complete. Payload was: {payload_debug}")
                    del current_context["vars"]["_event"]
                    if "_event" in current_context:
                        del current_context["_event"]
                    update_scheduler_state(publish_destination, context_stack=scheduler_contexts_stacks[publish_destination])
                
            # Sleep after processing (either running an instruction or finding none to run)
            # Wait a small amount of time to allow for urgent events to be processed quickly
            # Use a shorter sleep when in wait state to be more responsive to urgent interruptions
            sleep_time = 0.05 if is_in_wait_state else 0.1
            await asyncio.sleep(sleep_time)
            
            # Add wait state debug logging rate limiting
            if not hasattr(run_scheduler_loop, '_wait_debug_logs'):
                run_scheduler_loop._wait_debug_logs = {}

            # Use the publish_destination as the key for rate limiting
            wait_debug_key = f"wait_state_{publish_destination}"
            
            # Throttle debug logs during wait state - during wait, we silence most debugging
            # except at 30 second intervals or when actual events occur
            if is_in_wait_state:
                # Reset counter when wait state completes
                if wait_debug_key in run_scheduler_loop._wait_debug_logs:
                    del run_scheduler_loop._wait_debug_logs[wait_debug_key]
            else:
                # Set logging level back to normal when not in wait state
                if wait_debug_key in run_scheduler_loop._wait_debug_logs:
                    del run_scheduler_loop._wait_debug_logs[wait_debug_key]
                    debug(f"Normal debug logging resumed after wait state ended")
            
    except asyncio.CancelledError:
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M:%S')}] Scheduler cancelled")
        raise
    except Exception as e:
        error_msg = f"Error in scheduler loop: {str(e)}"
        scheduler_logs[publish_destination].append(f"[{datetime.now().strftime('%H:%M:%S')}] {error_msg}")
        import traceback
        error(traceback.format_exc())
        raise
    finally:
        # Only update state if we're actually stopping
        if publish_destination not in running_schedulers:
            # Get current state before updating
            current_state = scheduler_states.get(publish_destination)
            
            # Also check the state from disk to handle server restart cases
            try:
                loaded_state = load_scheduler_state(publish_destination)
                disk_state = loaded_state.get("state", "stopped")
                if should_log_debug:
                    debug(f"Checking disk state for {publish_destination}: {disk_state}")
                
                # If disk state is paused but memory state is not, use disk state
                # This handles cases where scheduler was paused but server restarted
                if disk_state == "paused" and current_state != "paused":
                    if should_log_debug:
                        debug(f"Found paused state on disk for {publish_destination}, preserving it")
                    current_state = "paused"
                    scheduler_states[publish_destination] = "paused"
            except Exception as e:
                debug(f"Could not read disk state: {e}")
            
            # Only set to stopped if we're not paused (either in memory or disk)
            if current_state != "paused":
                # Preserve the context when stopping
                current_context_stack = scheduler_contexts_stacks.get(publish_destination, [])
                scheduler_states[publish_destination] = "stopped"
                if should_log_debug:
                    debug(f"Set {publish_destination} to stopped in finally block of run_scheduler_loop")
                update_scheduler_state(
                    publish_destination,
                    schedule_stack=scheduler_schedule_stacks.get(publish_destination, []),
                    context_stack=current_context_stack,
                    state="stopped"
                )
            else:
                if should_log_debug:
                    debug(f"KEPT PAUSED for {publish_destination} - in finally block of run_scheduler_loop")
                    debug(f"Not changing state because current_state is '{current_state}'")
                # Explicitly update with stopped state to ensure it's persisted
                current_context_stack = scheduler_contexts_stacks.get(publish_destination, [])
                update_scheduler_state(
                    publish_destination,
                    schedule_stack=scheduler_schedule_stacks.get(publish_destination, []),
                    context_stack=current_context_stack,
                    state="stopped"
                )
                
            # Note: We don't need to manage the event loop here, as that's handled in stop_scheduler
            # which will be called to clean up properly when needed

def stop_scheduler(publish_destination: str):
    """
    Signal a scheduler to stop completely, immediately stopping the loop.
    This is like "pulling the plug" on the scheduler but preserves its state.
    """
    try:
        # Add more detailed logging about the reason for stopping
        caller_info = ""
        import traceback
        stack_frames = traceback.extract_stack()
        if len(stack_frames) > 1:
            caller = stack_frames[-2]  # The caller of this function
            caller_info = f" (called from {caller.name} in {caller.filename}:{caller.lineno})"
        
        info(f"STOPPING SCHEDULER: {publish_destination}{caller_info}")
        debug(f"Current schedulers BEFORE stopping {publish_destination}:")
        debug(f"  Running: {list(running_schedulers.keys())}")
        debug(f"  States: {scheduler_states}")
        
        # Clear the instruction queue for this destination
        clear_instruction_queue(publish_destination)
        
        # Preserve context before stopping
        context_stack = scheduler_contexts_stacks.get(publish_destination, [])
        
        # Check if scheduler is running
        if publish_destination in running_schedulers:
            # Get the scheduler info
            scheduler_info = running_schedulers[publish_destination]
            
            # Handle both old (just future) and new (dict with future and loop) formats
            if isinstance(scheduler_info, dict):
                future = scheduler_info.get("future")
            else:
                future = scheduler_info
                
            # Cancel the future if it's not already done
            if future and not future.done() and not future.cancelled():
                future.cancel()
                info(f"Cancelled running scheduler for {publish_destination}")
                
            # Remove from the running schedulers
            running_schedulers.pop(publish_destination, None)
        
        # Update in-memory state - unless paused
        if scheduler_states.get(publish_destination) != "paused":
            scheduler_states[publish_destination] = "stopped"
            debug(f"Set {publish_destination} to stopped in stop_scheduler")
        else:
            debug(f"KEPT PAUSED for {publish_destination} - in stop_scheduler")
            
        # Update the scheduler state on disk, preserving context
        update_scheduler_state(
            publish_destination,
            state=scheduler_states.get(publish_destination, "stopped"),
            context_stack=context_stack
        )
        
        # Also stop the event loop for this destination only if no other schedulers are
        # active *or* paused.  This preserves the shared loop in the test
        # suite where multiple destinations share the same mock loop.
        any_active_schedulers = False
        any_paused_schedulers = False
        for dest, state in scheduler_states.items():
            if state == "running" and dest in running_schedulers:
                any_active_schedulers = True
            if state == "paused":
                any_paused_schedulers = True
                
        if not any_active_schedulers and not any_paused_schedulers:
            try:
                stop_event_loop(publish_destination)
            except TypeError:
                stop_event_loop()
        
        # Log the state after stopping
        debug(f"Scheduler states AFTER stopping {publish_destination}:")
        debug(f"  Running: {list(running_schedulers.keys())}")
        debug(f"  States: {scheduler_states}")
        
        info(f"Scheduler for {publish_destination} set to stopped")
    except Exception as e:
        error(f"Error stopping scheduler: {str(e)}")
        import traceback
        error(traceback.format_exc())

def simulate_schedule(schedule: Dict[str, Any], start_time: str, end_time: str, step_minutes: int, context: Dict[str, Any]) -> List[str]:
    now = datetime.strptime(start_time, "%H:%M")
    end = datetime.strptime(end_time, "%H:%M")
    output = []

    while now <= end:
        instructions = resolve_schedule(schedule, now, "", True)
        for instr in instructions:
            run_instruction(instr, context, now, output, "")
        now += timedelta(minutes=step_minutes)

    return output
